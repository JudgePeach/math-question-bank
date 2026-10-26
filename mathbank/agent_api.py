"""Additive, bounded HTTP access to the same read-only reader used by the CLI."""

import sqlite3
from pathlib import Path
from typing import Callable, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from mathbank.agent_reads import AgentReadError, AgentReader


Profile = Literal["candidate", "teaching", "evidence"]


def _ids(value: str) -> list[int]:
    parts = value.split(",")
    if (not value or len(value) > 2200 or len(parts) > 100
            or any(len(part.strip()) > 19 or not part.strip().isascii() or not part.strip().isdigit()
                        or int(part.strip()) < 1 for part in parts)):
        raise HTTPException(status_code=400, detail="ids 必须为逗号分隔的正整数 ID")
    return [int(part.strip()) for part in parts]


def create_agent_router(
    *, database_path: Callable, uploads_dir: Callable,
    metadata_path: Callable, active_version_code: Callable,
) -> APIRouter:
    """Resolve application paths at request time; never initialize a database."""
    router = APIRouter(prefix="/api/agent", tags=["Agent read-only"])

    def read(method, **kwargs):
        try:
            with AgentReader(
                db_path=Path(database_path()), uploads_dir=Path(uploads_dir()),
                metadata_path=Path(metadata_path()),
                active_version_code=active_version_code(), include_local_paths=False,
            ) as reader:
                return getattr(reader, method)(**kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AgentReadError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (sqlite3.Error, OSError) as exc:
            # Driver and filesystem messages can contain private absolute paths.
            raise HTTPException(status_code=503, detail="题库读取暂不可用，请检查服务就绪状态后重试") from exc

    @router.get("/capabilities")
    def capabilities():
        return read("capabilities")

    @router.get("/knowledge-points")
    def knowledge_points(
        q: str = "", version_code: Optional[str] = None,
        compulsory: str = "", chapter: str = "", knowledge: str = "",
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    ):
        return read("knowledge_points", query=q, version_code=version_code,
                    compulsory=compulsory, chapter=chapter, knowledge=knowledge,
                    limit=limit, offset=offset)

    @router.get("/questions")
    def search(
        q: str = "", source: str = "", compulsory: str = "", chapter: str = "",
        knowledge: str = "", knowledge_point_codes: Optional[List[str]] = Query(None),
        knowledge_query: str = "", knowledge_point_mode: Literal["any", "all"] = "any",
        knowledge_point_role: Literal["main", "all"] = "main",
        knowledge_point_status: Literal["all", "confirmed"] = "all",
        qtype: str = "", difficulty: str = "", page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        related_to: Optional[int] = Query(None, ge=1), profile: Profile = "candidate",
        include_answers: Optional[bool] = None, sort: Literal["asc", "desc"] = "desc",
        version_code: Optional[str] = None,
    ):
        return read(
            "search", query=q, source=source, compulsory=compulsory, chapter=chapter,
            knowledge=knowledge, knowledge_point_codes=knowledge_point_codes,
            knowledge_query=knowledge_query, knowledge_point_mode=knowledge_point_mode,
            knowledge_point_role=knowledge_point_role,
            knowledge_point_status=knowledge_point_status, qtype=qtype,
            difficulty=difficulty, page=page, page_size=page_size, related_to=related_to,
            profile=profile, include_answers=(profile != "candidate" if include_answers is None
                                              else include_answers), sort=sort,
            version_code=version_code,
        )

    @router.get("/questions/batch")
    def batch(ids: str, profile: Profile = "teaching", include_answers: bool = True,
              version_code: Optional[str] = None):
        return read("get_questions", ids=_ids(ids), profile=profile,
                    include_answers=include_answers, version_code=version_code)

    @router.get("/assets")
    def assets(ids: str):
        return read("assets", ids=_ids(ids))

    @router.get("/questions/{question_id}")
    def question(question_id: int, profile: Profile = "teaching", include_answers: bool = True,
                 version_code: Optional[str] = None):
        result = read("get_questions", ids=[question_id], profile=profile,
                      include_answers=include_answers, version_code=version_code)
        if result["missing_ids"]:
            raise HTTPException(status_code=404, detail="未找到对应的题目")
        return result["items"][0]

    @router.get("/questions/{question_id}/assets")
    def question_assets(question_id: int):
        result = read("assets", ids=[question_id])
        if result["missing_ids"]:
            raise HTTPException(status_code=404, detail="未找到对应的题目")
        return result

    return router
