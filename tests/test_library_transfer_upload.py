"""Real multipart parsing boundaries without importing main or a database."""

import asyncio
import errno
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.testclient import TestClient
import pytest

from mathbank import library_transfer_upload as upload
from mathbank.storage_access import StorageAccessError


PATHS = ("/api/library-transfer/import", "/api/library-transfer/tasks/import")


def _app(tmp_path, **options):
    root = tmp_path / "private-sharing-uploads"
    app, entered = FastAPI(), []
    router = upload.create_library_transfer_upload_router(lambda: root, **options)

    @router.post(PATHS[0])
    def sync_import(file: UploadFile = File(...)):
        entered.append(file.filename)
        return {"filename": file.filename, "bytes": len(file.file.read()), "rolled": file.file._rolled}

    @router.post(PATHS[1])
    def task_import(file: UploadFile = File(...), client_request_id: str = Form(...)):
        entered.append(file.filename)
        return {"filename": file.filename, "bytes": len(file.file.read()), "request_id": client_request_id}

    app.include_router(router)
    return app, root, entered


def _multipart(data=b"archive", *, filename="MathBank.zip", request_id="intent-123"):
    boundary = "mathbank-upload-boundary"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            'Content-Type: application/zip\r\n\r\n').encode() + data
    body += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="client_request_id"\r\n\r\n'
             f'{request_id}\r\n--{boundary}--\r\n').encode()
    return boundary, body


async def _asgi(app, path, boundary, chunks, *, content_length=None, receive_hook=None, send_hook=None):
    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "headers": headers,
             "server": ("testserver", 80), "client": ("127.0.0.1", 1234)}
    sent, reads = [], []
    iterator = iter(chunks)

    async def receive():
        reads.append(True)
        if receive_hook:
            await receive_hook(len(reads))
        chunk = next(iterator, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        sent.append(message)
        if send_hook:
            await send_hook(message)

    await app(scope, receive, send)
    return {"status": sent[0]["status"],
            "body": b"".join(item.get("body", b"") for item in sent), "reads": len(reads)}


def _assert_clean(root):
    assert not root.exists() or not list(root.iterdir())


@pytest.mark.parametrize("path", PATHS)
def test_real_multipart_keeps_contract_and_uses_only_application_temp(tmp_path, monkeypatch, path):
    app, root, entered = _app(tmp_path)
    original = upload.tempfile
    created = []

    def tracked_spool(*args, **kwargs):
        created.append(Path(kwargs["dir"]))
        return original.SpooledTemporaryFile(*args, **kwargs)

    monkeypatch.setattr(upload, "tempfile", SimpleNamespace(
        mkdtemp=original.mkdtemp, SpooledTemporaryFile=tracked_spool,
    ))
    data = b"x" * (upload.UPLOAD_SPOOL_BYTES + 13)
    with TestClient(app) as client:
        response = client.post(path, files={"file": ("中文题库.zip", data, "application/zip")},
                               data={"client_request_id": "intent-123"})
    assert response.status_code == 200, response.text
    assert response.json()["bytes"] == len(data)
    assert entered == ["中文题库.zip"]
    assert created and all(path.parent == root for path in created)
    _assert_clean(root)


@pytest.mark.parametrize("length", [2048, "999999999999999999999999999999999999", "-1", "invalid"])
def test_declared_oversize_or_invalid_length_never_reads_body_or_enters_handler(tmp_path, length):
    app, root, entered = _app(tmp_path, file_limit=128, overhead_limit=512)
    boundary, body = _multipart()
    result = asyncio.run(_asgi(app, PATHS[1], boundary, [body], content_length=length))
    assert result["status"] == (400 if length in {"-1", "invalid"} else 413)
    assert result["reads"] == 0 and not entered
    _assert_clean(root)


@pytest.mark.parametrize("overflow", ["file", "multipart"])
def test_chunked_upload_without_content_length_is_bounded_before_handler(tmp_path, overflow):
    app, root, entered = _app(tmp_path, file_limit=128, overhead_limit=512)
    if overflow == "file":
        boundary, body = _multipart(b"x" * 129)
    else:
        boundary, body = _multipart(b"x", filename="x" * 800 + ".zip")
    result = asyncio.run(_asgi(app, PATHS[1], boundary, [body[:100], body[100:]]))
    assert result["status"] == 413 and not entered
    _assert_clean(root)


def test_three_receiving_slots_reject_fourth_before_read_and_are_released(tmp_path):
    app, root, entered = _app(tmp_path)
    boundary, body = _multipart()

    async def exercise():
        release, receiving = asyncio.Event(), asyncio.Event()
        count = 0

        async def pause(reads):
            nonlocal count
            if reads == 1:
                count += 1
                if count == 3:
                    receiving.set()
                await release.wait()

        pending = [asyncio.create_task(_asgi(app, PATHS[1], boundary, [body], receive_hook=pause)) for _ in range(3)]
        await asyncio.wait_for(receiving.wait(), 3)
        try:
            rejected = await _asgi(app, PATHS[0], boundary, [body])
            assert rejected["status"] == 503 and rejected["reads"] == 0 and not entered
        finally:
            release.set()
        accepted = await asyncio.gather(*pending)
        assert all(result["status"] == 200 for result in accepted)
        assert (await _asgi(app, PATHS[1], boundary, [body]))["status"] == 200

    asyncio.run(exercise())
    assert len(entered) == 4
    _assert_clean(root)


def test_early_rejection_keeps_slot_until_response_sent(tmp_path):
    app, root, entered = _app(tmp_path, file_limit=128, overhead_limit=512, max_uploads=1)
    boundary, body = _multipart()

    async def exercise():
        sending, release = asyncio.Event(), asyncio.Event()

        async def pause_send(message):
            if message["type"] == "http.response.body":
                sending.set()
                await release.wait()

        pending = asyncio.create_task(_asgi(app, PATHS[1], boundary, [body], content_length=2048, send_hook=pause_send))
        await asyncio.wait_for(sending.wait(), 3)
        try:
            rejected = await _asgi(app, PATHS[1], boundary, [body])
            assert rejected["status"] == 503 and rejected["reads"] == 0
        finally:
            release.set()
        assert (await pending)["status"] == 413
        assert (await _asgi(app, PATHS[1], boundary, [body]))["status"] == 200

    asyncio.run(exercise())
    assert len(entered) == 1
    _assert_clean(root)


def test_temp_disk_failure_is_actionable_and_does_not_enter_handler(tmp_path, monkeypatch):
    app, root, entered = _app(tmp_path, max_uploads=1)
    original = upload.tempfile

    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "/Users/private-user/secret-path")

    with monkeypatch.context() as patch:
        patch.setattr(upload, "tempfile", SimpleNamespace(mkdtemp=original.mkdtemp, SpooledTemporaryFile=disk_full))
        with TestClient(app) as client:
            response = client.post(PATHS[1], files={"file": ("library.zip", b"x")}, data={"client_request_id": "id"})
        assert response.status_code == 507, response.text
        assert response.json()["code"] == "upload_rejected"
        assert "程序所在磁盘" in response.json()["detail"] and "可用空间" in response.json()["detail"]
        assert "secret-path" not in response.text and "private-user" not in response.text and not entered
    _assert_clean(root)
    boundary, body = _multipart()
    assert asyncio.run(_asgi(app, PATHS[1], boundary, [body]))["status"] == 200


@pytest.mark.parametrize("errno_value,winerror,status,phrase", [
    (errno.ENOSPC, 112, 507, "可用空间"),
    (errno.EACCES, 5, 500, "权限"),
    (errno.EACCES, 32, 500, "占用"),
    (errno.EACCES, 33, 500, "占用"),
    (errno.ENAMETOOLONG, 206, 500, "路径过长"),
    (None, None, 500, "内存"),
])
def test_rollover_windows_storage_or_memory_failure_closes_spool_and_reports_before_handler(
    tmp_path, monkeypatch, errno_value, winerror, status, phrase,
):
    app, root, entered = _app(tmp_path, max_uploads=1)
    original = upload.tempfile
    files = []

    class FailedRollover(original.SpooledTemporaryFile):
        def rollover(self):
            if errno_value is None:
                raise MemoryError("private question data must never appear")
            exc = OSError(errno_value, "/Users/private-user/secret-path")
            exc.winerror = winerror
            raise exc

    def failed_spool(*args, **kwargs):
        stream = FailedRollover(*args, **kwargs)
        files.append(stream)
        return stream

    with monkeypatch.context() as patch:
        patch.setattr(upload, "tempfile", SimpleNamespace(mkdtemp=original.mkdtemp, SpooledTemporaryFile=failed_spool))
        boundary, body = _multipart(b"x" * (upload.UPLOAD_SPOOL_BYTES + 13))
        result = asyncio.run(_asgi(app, PATHS[1], boundary, [body]))
        assert result["status"] == status
        message = result["body"].decode()
        assert phrase in message and "upload_rejected" in message
        assert "private-user" not in message and "secret-path" not in message and "private question" not in message
        assert files and all(stream.closed for stream in files) and not entered
    _assert_clean(root)
    boundary, body = _multipart()
    assert asyncio.run(_asgi(app, PATHS[1], boundary, [body]))["status"] == 200


def test_malformed_multipart_header_closes_prior_file_and_returns_safe_400(tmp_path, monkeypatch):
    app, root, entered = _app(tmp_path, max_uploads=1)
    original = upload.tempfile
    files = []

    def tracked_spool(*args, **kwargs):
        stream = original.SpooledTemporaryFile(*args, **kwargs)
        files.append(stream)
        return stream

    monkeypatch.setattr(upload, "tempfile", SimpleNamespace(mkdtemp=original.mkdtemp, SpooledTemporaryFile=tracked_spool))
    boundary = "broken-header-boundary"
    first = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="MathBank.zip"\r\n\r\n').encode()
    first += b"x" * (upload.UPLOAD_SPOOL_BYTES + 13)
    broken = f'\r\n--{boundary}\r\ninvalid header without colon\r\n\r\nprivate secret\r\n--{boundary}--\r\n'.encode()
    result = asyncio.run(_asgi(app, PATHS[1], boundary, [first, broken]))
    assert result["status"] == 400 and "上传表单无效" in result["body"].decode()
    assert b"private secret" not in result["body"] and not entered
    assert files and all(stream.closed for stream in files)
    _assert_clean(root)


def test_storage_exception_after_handler_started_is_not_marked_as_unaccepted(tmp_path):
    root = tmp_path / "private-sharing-uploads"
    app, entered = FastAPI(), []
    router = upload.create_library_transfer_upload_router(lambda: root)

    @router.post(PATHS[0])
    def uncertain_handler(file: UploadFile = File(...)):
        entered.append(True)
        raise OSError(errno.ENOSPC, "handler may already have committed")

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(PATHS[0], files={"file": ("MathBank.zip", b"archive")})
    assert response.status_code == 500 and entered == [True]
    assert "upload_rejected" not in response.text and "committed" not in response.text
    _assert_clean(root)


@pytest.mark.parametrize("failure", [asyncio.CancelledError, "disconnect"])
def test_cancel_or_disconnect_closes_spooled_files_and_releases_slot(tmp_path, monkeypatch, failure):
    from starlette.requests import ClientDisconnect

    app, root, entered = _app(tmp_path, max_uploads=1)
    original = upload.tempfile
    files = []

    def tracked_spool(*args, **kwargs):
        stream = original.SpooledTemporaryFile(*args, **kwargs)
        files.append(stream)
        return stream

    monkeypatch.setattr(upload, "tempfile", SimpleNamespace(mkdtemp=original.mkdtemp, SpooledTemporaryFile=tracked_spool))
    boundary, body = _multipart(b"x" * (upload.UPLOAD_SPOOL_BYTES + 13))

    async def interrupt(reads):
        if reads > 1:
            raise ClientDisconnect() if failure == "disconnect" else failure()

    with pytest.raises(ClientDisconnect if failure == "disconnect" else failure):
        asyncio.run(_asgi(app, PATHS[1], boundary, [body[:-50]], receive_hook=interrupt))
    assert files and all(stream.closed for stream in files) and not entered
    _assert_clean(root)
    retry_boundary, retry_body = _multipart()
    assert asyncio.run(_asgi(app, PATHS[1], retry_boundary, [retry_body]))["status"] == 200
    _assert_clean(root)


@pytest.mark.parametrize("path", PATHS)
def test_upload_directory_probe_denial_is_safe_and_precedes_body_read(tmp_path, monkeypatch, path):
    app, root, entered = _app(tmp_path, max_uploads=1)
    label = "分享上传暂存目录（.system_generated/library_transfer_uploads）"
    cause = PermissionError(errno.EACCES, "private OS diagnostic", "/Users/private-user/secret-path")
    cause.winerror = 5
    failure = StorageAccessError(root, label=label, stage="create", cause=cause)
    probes = []

    def deny(directory, **options):
        probes.append((Path(directory), options))
        raise failure

    boundary, body = _multipart()
    with monkeypatch.context() as patch:
        patch.setattr(upload, "check_directory_access", deny)
        result = asyncio.run(_asgi(app, path, boundary, [body]))
    message = result["body"].decode()
    assert result["status"] == 500 and result["reads"] == 0 and not entered
    assert probes == [(root, {"label": label, "require_subdirectory": True})]
    assert "upload_rejected" in message and failure.safe_message in message
    assert label in message and "private-user" not in message and "secret-path" not in message
    assert "private OS diagnostic" not in message
    _assert_clean(root)
    # The receiving slot is released even when the body was never consumed.
    assert asyncio.run(_asgi(app, path, boundary, [body]))["status"] == 200
    assert entered == ["MathBank.zip"]
    _assert_clean(root)
