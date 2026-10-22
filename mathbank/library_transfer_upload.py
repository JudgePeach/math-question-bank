"""Bound sharing multipart uploads before FastAPI binds File/Form arguments.

The two sharing import routes use a private parser with an application-local
temporary directory. Other uploads keep their normal Starlette behaviour.
"""

from __future__ import annotations

import errno
from pathlib import Path
import shutil
import tempfile
import threading

from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser, _user_safe_decode, parse_options_header
from starlette.responses import JSONResponse

from mathbank.asset_security import harden_private_path
from mathbank.library_transfer_tasks import transfer_error
from mathbank.storage_access import StorageAccessError, check_directory_access


MAX_SHARING_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
UPLOAD_SPOOL_BYTES = 1024 * 1024
_SIZE_MESSAGE = "题库分享包不能超过 512 MiB，请按来源分批导出。"


def _storage_error(exc):
    """Keep local paths and the operating system's exception text private."""
    error = transfer_error(exc, stage="upload")
    if isinstance(exc, StorageAccessError):
        message = exc.safe_message
    elif error["code"] == "disk":
        message = "分享包上传暂存失败，请检查程序所在磁盘的可用空间和读写状态后重试。"
    elif error["code"] in {"permission", "file_in_use"}:
        message = "分享包上传暂存失败：" + error["message"]
    elif error["code"] == "memory":
        message = error["message"]
    elif getattr(exc, "winerror", None) == 206 or getattr(exc, "errno", None) == errno.ENAMETOOLONG:
        message = "分享包上传暂存路径过长，请将程序移到较短的可写目录后重试。"
    else:
        message = "分享包上传暂存失败，请检查程序目录权限、可用空间和磁盘状态后重试。"
    return HTTPException(status_code=507 if error["code"] == "disk" else 500, detail=message)


class _SharingMultipartParser(MultiPartParser):
    """Pinned Starlette parser with scoped temporary-file creation and caps."""

    def __init__(self, headers, stream, *, directory, file_limit):
        super().__init__(headers, stream, max_files=1, max_fields=1, max_part_size=4096)
        self.directory = directory
        self.file_limit = file_limit
        self.file_bytes = 0

    def on_headers_finished(self):
        # Match Starlette's callback, changing only the file factory. A global
        # tempfile override would also redirect unrelated OCR/Word requests.
        disposition, options = parse_options_header(self._current_part.content_disposition)
        try:
            self._current_part.field_name = _user_safe_decode(options[b"name"], self._charset)
        except KeyError:
            raise MultiPartException("Missing multipart field name.") from None
        if b"filename" in options:
            self._current_files += 1
            if self._current_files > self.max_files:
                raise MultiPartException("Too many sharing files.")
            filename = _user_safe_decode(options[b"filename"], self._charset)
            stream = tempfile.SpooledTemporaryFile(max_size=UPLOAD_SPOOL_BYTES, dir=self.directory)
            self._files_to_close_on_error.append(stream)
            self._current_part.file = UploadFile(
                file=stream, size=0, filename=filename,
                headers=Headers(raw=self._current_part.item_headers),
            )
        else:
            self._current_fields += 1
            if self._current_fields > self.max_fields:
                raise MultiPartException("Too many sharing fields.")
            self._current_part.file = None

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.file_limit:
                raise HTTPException(status_code=413, detail=_SIZE_MESSAGE)
        super().on_part_data(data, start, end)

    async def parse(self):
        try:
            return await super().parse()
        except BaseException:
            # Older installed Starlette versions only close these streams for
            # MultiPartException. Disconnect, cancellation and our HTTP size
            # rejection must release Windows file handles too.
            for stream in self._files_to_close_on_error:
                try:
                    stream.close()
                except OSError:
                    pass
            raise


def create_library_transfer_upload_router(root_getter, *, file_limit=MAX_SHARING_UPLOAD_BYTES,
                                         overhead_limit=MAX_MULTIPART_OVERHEAD_BYTES, max_uploads=3):
    """Create a route-local receiving gate; authentication stays in middleware.

    The gate covers form parsing through the import handler. Its acquisition
    never waits, so the existing copy-stage gate cannot form a wait cycle.
    ``root_getter`` lets isolated app tests replace the anchored storage root.
    """
    slots = threading.BoundedSemaphore(max_uploads)
    request_limit = file_limit + overhead_limit

    class SharingUploadRoute(APIRoute):
        async def handle(self, scope, receive, send):
            if not slots.acquire(blocking=False):
                response = JSONResponse(
                    {"detail": "题库分享上传队列已满，请稍后重试。"}, status_code=503,
                    headers={"Retry-After": "2"},
                )
                await response(scope, receive, send)
                return
            try:
                # Keep the slot until the response has been sent, including
                # rejected requests whose body deliberately remains unread.
                await super().handle(scope, receive, send)
            finally:
                slots.release()

        def get_route_handler(self):
            original = super().get_route_handler()

            async def receive_sharing_form(request):
                length = request.headers.get("content-length")
                if length is not None:
                    if not length.isascii() or not length.isdecimal():
                        raise HTTPException(status_code=400, detail="分享包上传长度无效，请重新选择文件后重试。")
                    if len(length) > 20 or int(length) > request_limit:
                        raise HTTPException(status_code=413, detail=_SIZE_MESSAGE)
                directory, form = None, None
                try:
                    content_type, _ = parse_options_header(request.headers.get("content-type", ""))
                    if content_type != b"multipart/form-data":
                        raise HTTPException(status_code=400, detail="请使用文件上传方式选择 ZIP 分享包。")
                    root = Path(root_getter())
                    if root.is_symlink():
                        raise OSError(errno.EACCES, "Invalid sharing upload root")
                    check_directory_access(
                        root, label="分享上传暂存目录（.system_generated/library_transfer_uploads）",
                        require_subdirectory=True,
                    )
                    harden_private_path(root, directory=True)
                    directory = Path(tempfile.mkdtemp(prefix="receive-", dir=root))
                    harden_private_path(directory, directory=True)

                    async def limited_stream():
                        size = 0
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > request_limit:
                                raise HTTPException(status_code=413, detail=_SIZE_MESSAGE)
                            yield chunk

                    parser = _SharingMultipartParser(request.headers, limited_stream(),
                                                     directory=directory, file_limit=file_limit)
                    form = await parser.parse()
                except (MultiPartException, MultipartParseError) as exc:
                    raise HTTPException(status_code=400, detail="上传表单无效，请重新选择 ZIP 分享包后重试。") from exc
                except (OSError, MemoryError) as exc:
                    error = _storage_error(exc)
                    # Only parsing failures carry this marker: the handler
                    # has not run, so the browser can safely end this intent.
                    return JSONResponse({"detail": error.detail, "code": "upload_rejected"},
                                        status_code=error.status_code, headers={"Cache-Control": "no-store"})
                else:
                    # FastAPI's existing File/Form binding reuses this parsed
                    # form, preserving the public multipart contract. Errors
                    # after this point retain the handler's uncertainty rules.
                    request._form = form
                    return await original(request)
                finally:
                    try:
                        if form is not None:
                            try:
                                await form.close()
                            except OSError:
                                pass
                    finally:
                        if directory is not None:
                            # Temporary-file cleanup cannot change an accepted
                            # import response or hide its durable task receipt.
                            shutil.rmtree(directory, ignore_errors=True)

            return receive_sharing_form

    return APIRouter(route_class=SharingUploadRoute)
