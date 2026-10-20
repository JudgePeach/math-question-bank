"""Thin local-project routes; application middleware owns request permissions."""
from __future__ import annotations

import inspect
import json
import logging
from urllib.parse import quote
import uuid

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from mathbank.document_projects import (
    DocumentProjectError, ProjectConflict, ProjectCorrupt, ProjectNotFound,
    ProjectUnsafePath,
)

_LOGGER = logging.getLogger(__name__)


async def _call(function, *args, **kwargs):
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    result = await run_in_threadpool(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _project_id(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical project id")
    except (ValueError, TypeError, AttributeError) as exc:
        raise DocumentProjectError("文档工程标识无效。") from exc
    return value


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object field")
        result[key] = value
    return result


async def _body(request, limit, *, empty_allowed=False):
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise DocumentProjectError("文档工程请求超过保存上限。")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw and empty_allowed:
        return {}
    try:
        body = json.loads(raw, object_pairs_hook=_strict_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise DocumentProjectError("文档工程请求必须为完整有效 JSON。") from exc
    if not isinstance(body, dict):
        raise DocumentProjectError("文档工程请求必须为 JSON 对象。")
    return body


async def _respond(action):
    try:
        return await action()
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    except ProjectNotFound:
        return JSONResponse({"detail": "本地文档工程不存在。"}, status_code=404)
    except ProjectConflict as exc:
        result = {"detail": str(exc)}
        if exc.current_revision is not None:
            result["current_revision"] = exc.current_revision
        return JSONResponse(result, status_code=409)
    except (ProjectCorrupt, ProjectUnsafePath):
        return JSONResponse({"detail": "本地文档工程无法安全访问，已保留原有文件。"}, status_code=409)
    except DocumentProjectError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception:
        # Never expose raw filesystem/provider exception strings to HTTP callers.
        return JSONResponse({"detail": "本地文档工程操作未完成，请保留当前编辑后重试。"}, status_code=500)


def register_document_project_routes(app, *, get_service, dispatch_project, release_assets):
    """Bind routes to the current service without creating jobs or stores here."""

    async def release_deleted_assets(urls):
        # The committed project deletion can be acknowledged before scanning
        # other projects and the library for shared image references.
        try:
            await _call(release_assets, urls)
        except Exception:
            _LOGGER.warning("Deleted project image cleanup is pending; files remain protected.")

    async def service(required=True):
        current = await _call(get_service)
        if current is None and required:
            raise ProjectNotFound("Document projects unavailable")
        return current

    @app.get("/api/document-projects")
    async def list_projects():
        async def action():
            current = await service(False)
            if current is None:
                return {"projects": []}
            rows = await _call(current.store.list)
            return {"projects": [await _call(current.metadata, row) for row in rows]}
        return await _respond(action)

    @app.get("/api/document-projects/{project_id}")
    async def get_project(project_id: str):
        async def action():
            current = await service()
            return await _call(current.detail, _project_id(project_id))
        return await _respond(action)

    @app.post("/api/document-projects/{project_id}/open")
    async def open_project(project_id: str):
        async def action():
            current = await service()
            identifier = _project_id(project_id)
            # A lease protects the gap between renewal and loading the draft.
            with current.store.operation(identifier):
                await _call(current.store.touch, identifier)
                return await _call(current.detail, identifier)
        return await _respond(action)

    @app.put("/api/document-projects/{project_id}/draft")
    async def save_project_draft(project_id: str, request: Request):
        async def action():
            current = await service()
            identifier = _project_id(project_id)
            body = await _body(request, current.store.MAX_DRAFT_BYTES + 4096)
            if (set(body) != {"expected_revision", "draft"}
                    or type(body["expected_revision"]) is not int or body["expected_revision"] < 0
                    or not isinstance(body["draft"], dict)):
                raise DocumentProjectError("工作稿及预期修订号格式无效。")
            return await _call(current.save_draft, identifier, body["draft"], body["expected_revision"])
        return await _respond(action)

    @app.post("/api/document-projects/{project_id}/fork")
    async def fork_project(project_id: str, request: Request):
        async def action():
            current = await service()
            identifier = _project_id(project_id)
            body = await _body(request, current.store.MAX_DRAFT_BYTES + 4096, empty_allowed=True)
            if set(body) - {"draft"} or (body.get("draft") is not None and not isinstance(body["draft"], dict)):
                raise DocumentProjectError("工程副本工作稿格式无效。")
            if isinstance(body.get("draft"), dict):
                # Validate before creating the new project, avoiding a half-fork
                # when a malformed request cannot become a valid working draft.
                await _call(current.store._validate_draft, body["draft"])
            with current.store.operation(identifier):
                return await _call(current.fork, identifier, body.get("draft"))
        return await _respond(action)

    async def dispatch(project_id, analyze):
        async def action():
            current = await service()
            identifier = _project_id(project_id)
            with current.store.operation(identifier):
                await _call(current.store.get, identifier)
                return await _call(dispatch_project, identifier, analyze=analyze)
        return await _respond(action)

    @app.post("/api/document-projects/{project_id}/resume")
    async def resume_project(project_id: str):
        return await dispatch(project_id, False)

    @app.post("/api/document-projects/{project_id}/analyze")
    async def analyze_project(project_id: str):
        return await dispatch(project_id, True)

    @app.delete("/api/document-projects/{project_id}")
    async def delete_project(project_id: str, background_tasks: BackgroundTasks):
        async def action():
            current = await service()
            identifier = _project_id(project_id)
            active = await _call(current.active_task_ids)
            metadata = await _call(current.store.get, identifier)
            if metadata.get("task_id") in active:
                task = await _call(current.tasks.snapshot, metadata["task_id"])
                if (task or {}).get("status") not in {"completed", "error", "cancelled", "interrupted"}:
                    raise ProjectConflict("工程仍有活跃解析任务，不能删除。")
            receipt = await _call(current.store.delete, identifier,
                                  active_task_ids=current.active_task_ids, wait=True)
            result = {"status": "success", "project_id": identifier, **receipt}
            if receipt.get("temp_assets"):
                background_tasks.add_task(release_deleted_assets, receipt["temp_assets"])
                result.update(cleanup_pending=True, message="工程已删除，图片正在后台安全清理。")
            return result
        return await _respond(action)

    @app.get("/api/document-projects/{project_id}/source")
    async def download_project_source(project_id: str):
        async def action():
            current = await service()
            identifier = _project_id(project_id)
            with current.store.operation(identifier):
                metadata = await _call(current.store.get, identifier)
                content = await _call(current.store.load_source, identifier)
            name = metadata.get("filename") or "document.pdf"
            return Response(content, media_type="application/pdf", headers={
                "Content-Disposition": "attachment; filename=\"document.pdf\"; filename*=UTF-8''" + quote(name, safe=""),
                "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
            })
        return await _respond(action)
