"""Deletion responses and cleanup ordering, using only temporary projects."""
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mathbank.document_project_api import register_document_project_routes
from mathbank.document_project_service import DocumentProjectService
from mathbank.document_projects import DocumentProjectStore
from test_document_project_api import FakeTasks, api  # noqa: F401


def test_http_delete_waits_for_an_overlapping_open(api, monkeypatch):
    entered, finish, deleting = threading.Event(), threading.Event(), threading.Event()
    detail, delete = api.service.detail, api.store.delete

    def held_detail(identifier):
        entered.set()
        assert finish.wait(3)
        return detail(identifier)

    def observed_delete(*args, **kwargs):
        deleting.set()
        return delete(*args, **kwargs)

    monkeypatch.setattr(api.service, "detail", held_detail)
    monkeypatch.setattr(api.store, "delete", observed_delete)
    url = "/api/document-projects/" + api.project_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        opening = pool.submit(api.client.post, url + "/open")
        assert entered.wait(3)
        deletion = pool.submit(api.client.delete, url)
        try:
            assert deleting.wait(3)
            assert not deletion.done()
        finally:
            finish.set()
        assert opening.result(3).status_code == 200
        assert deletion.result(3).status_code == 200
    assert not (api.store.root / api.project_id).exists()


def test_terminal_worker_finishing_can_settle_before_http_deletion(api, monkeypatch):
    entered, finish, deleting = threading.Event(), threading.Event(), threading.Event()
    future = Future()
    api.store.bind_task(api.project_id, "finishing-task")
    api.tasks._records["finishing-task"] = SimpleNamespace(
        state={"status": "completed"}, future=future)
    original_delete = api.store.delete

    def observed_delete(*args, **kwargs):
        deleting.set()
        return original_delete(*args, **kwargs)

    monkeypatch.setattr(api.store, "delete", observed_delete)

    def flush_source():
        with api.store.operation(api.project_id):
            entered.set()
            assert finish.wait(3)
            api.store.touch(api.project_id)
            future.set_result(None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(flush_source)
        assert entered.wait(3)
        deletion = pool.submit(api.client.delete, "/api/document-projects/" + api.project_id)
        try:
            assert deleting.wait(3), "terminal flushing must wait rather than reject as running"
            assert not deletion.done()
        finally:
            finish.set()
        writing.result(3)
        assert deletion.result(3).status_code == 200


def test_http_success_body_is_sent_before_slow_safe_asset_cleanup(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    identifier = store.create(b"%PDF-private-test", "test.pdf", {}, {})["project_id"]
    image = "/static/uploads/tmp/private-test.png"
    store.register_assets(identifier, [image])
    entered, finish = threading.Event(), threading.Event()
    released = []

    def release_assets(urls):
        entered.set()
        assert finish.wait(3)
        released.extend(urls)

    app = FastAPI()
    register_document_project_routes(app, get_service=lambda: DocumentProjectService(store, FakeTasks()),
        dispatch_project=lambda *_: None, release_assets=release_assets)

    async def run():
        body_sent = asyncio.Event()
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body":
                body_sent.set()

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "DELETE", "scheme": "http", "path": "/api/document-projects/" + identifier,
            "raw_path": ("/api/document-projects/" + identifier).encode(), "query_string": b"",
            "root_path": "", "headers": [], "client": ("127.0.0.1", 1), "server": ("test", 80)}
        pending = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(body_sent.wait(), 1)
            assert messages[0]["status"] == 200
            assert not (store.root / identifier).exists()
            assert await asyncio.to_thread(entered.wait, 1)
            assert not pending.done() and released == []
        finally:
            finish.set()
            await pending
        assert released == [image]

    asyncio.run(run())
