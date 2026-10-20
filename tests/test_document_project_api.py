"""HTTP shell tests with isolated stores and fake dispatch; never run AI/DB."""
from copy import deepcopy
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from mathbank.document_project_api import register_document_project_routes
from mathbank.document_project_service import DocumentProjectService
from mathbank.document_projects import DocumentProjectStore


class FakeTasks:
    def __init__(self):
        self._lock = threading.RLock()
        self._records = {}

    def snapshot(self, task_id):
        record = self._records.get(task_id)
        return deepcopy(record.state) if record else None


def draft(text="人工校对", **extra):
    return {"questions": [{"content": text, "answer_markdown": "D"}],
            "paperTitle": "试卷校对", "pageImages": [], "pageNumbers": [1],
            "diagnostics": {}, **extra}


@pytest.fixture
def api(tmp_path):
    now = [1000.0]
    store = DocumentProjectStore(tmp_path / "projects", clock=lambda: now[0])
    project = store.create(b"%PDF-local-source", "数学 试卷(1).pdf", {}, {"必修二": {"9. 统计": []}})
    tasks = FakeTasks()
    service = DocumentProjectService(store, tasks)
    current = [service]
    dispatched = []; released = []
    def dispatch(project_id, analyze=False):
        dispatched.append((project_id, analyze))
        return {"status": "success", "task_id": "fake-task", "project_id": project_id}
    app = FastAPI()
    register_document_project_routes(app, get_service=lambda: current[0], dispatch_project=dispatch,
                                     release_assets=lambda urls: released.extend(urls))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, store=store, service=service, current=current,
                              tasks=tasks, project_id=project["project_id"], now=now,
                              dispatched=dispatched, released=released)


def url(api, suffix=""):
    return f"/api/document-projects/{api.project_id}{suffix}"


def test_project_list_is_metadata_only_and_does_not_load_drafts_or_checkpoints(api, monkeypatch):
    monkeypatch.setattr(api.store, "load_task", lambda *_: (_ for _ in ()).throw(AssertionError("large checkpoint read")))
    monkeypatch.setattr(api.store, "get_draft", lambda *_: (_ for _ in ()).throw(AssertionError("draft read")))
    original = api.store.get(api.project_id)
    api.now[0] += 10
    response = api.client.get("/api/document-projects")
    assert response.status_code == 200
    projects = response.json()["projects"]
    assert len(projects) == 1 and projects[0]["project_id"] == api.project_id
    assert "draft" not in projects[0] and "pdf_resume_state" not in projects[0]
    assert api.store.get(api.project_id)["last_used"] == original["last_used"]
    assert api.dispatched == []


def test_detail_is_read_only_but_open_extends_retention_without_dispatch(api):
    original = api.store.get(api.project_id)
    api.now[0] += 60
    response = api.client.get(url(api))
    assert response.status_code == 200 and response.json()["draft"]["questions"] == []
    assert api.store.get(api.project_id)["last_used"] == original["last_used"]
    response = api.client.post(url(api, "/open"))
    assert response.status_code == 200
    assert response.json()["last_used"] == api.now[0]
    assert response.json()["expires_at"] == api.now[0] + 7 * 86400
    assert api.dispatched == []


def test_draft_save_uses_cas_and_returns_current_revision_on_conflict(api):
    payload = draft()
    saved = api.client.put(url(api, "/draft"), json={"expected_revision": 0, "draft": payload})
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    row = saved.json()["draft"]["questions"][0]
    assert row["project_card_id"] and row["import_project_id"] == api.project_id
    conflict = api.client.put(url(api, "/draft"), json={"expected_revision": 0, "draft": draft("迟到覆盖")})
    assert conflict.status_code == 409 and conflict.json()["current_revision"] == 1
    assert api.store.get_draft(api.project_id)["payload"]["questions"][0]["content"] == "人工校对"


def test_same_text_fork_does_not_inherit_saved_card_or_baseline_status(api):
    card = {"content": "完全相同题文", "answer_markdown": "D", "saved": True,
            "savedSnapshot": {"content": "完全相同题文"}, "saved_id": 123,
            "savedQuestionId": 123, "modifiedAfterSave": False,
            "project_card_id": "old-card", "import_project_id": api.project_id}
    payload = draft(baselineQuestions=[deepcopy(card)])
    payload["questions"] = [deepcopy(card)]
    api.service.save_draft(api.project_id, payload, 0)
    forked = api.client.post(url(api, "/fork"), json={})
    assert forked.status_code == 200
    result = forked.json(); new_id = result["project_id"]
    assert new_id != api.project_id
    assert api.store.load_source(new_id) == api.store.load_source(api.project_id)
    for key in ("questions", "baselineQuestions"):
        row = result["draft"][key][0]
        assert row["content"] == "完全相同题文"
        assert not any(name in row for name in ("saved", "savedSnapshot", "saved_id", "savedQuestionId"))
        assert row["import_project_id"] == new_id and row["project_card_id"] != "old-card"
    assert api.store.get_draft(api.project_id)["payload"]["questions"][0]["saved"] is True
    assert api.dispatched == []


def test_only_explicit_resume_and_analyze_dispatch_work(api):
    for suffix, expected in (("/resume", False), ("/analyze", True)):
        response = api.client.post(url(api, suffix))
        assert response.status_code == 200 and response.json()["task_id"] == "fake-task"
        assert api.dispatched[-1] == (api.project_id, expected)


def test_delete_releases_registered_assets_after_project_deletion(api):
    image = "/static/uploads/tmp/image.png"
    api.store.register_assets(api.project_id, [image])
    response = api.client.delete(url(api))
    assert response.status_code == 200 and response.json()["status"] == "success"
    assert api.released == [image]
    assert api.client.get(url(api)).status_code == 404


def test_active_task_blocks_delete_and_asset_release(api):
    api.store.bind_task(api.project_id, "live-task")
    api.tasks._records["live-task"] = SimpleNamespace(state={"status": "running"}, future=object())
    response = api.client.delete(url(api))
    assert response.status_code == 409
    assert api.store.get(api.project_id)["task_id"] == "live-task"
    assert api.released == []


def test_source_download_is_verified_pdf_attachment_without_private_path(api):
    before = api.store.get(api.project_id)
    api.now[0] += 10
    response = api.client.get(url(api, "/source"))
    assert response.status_code == 200 and response.content == b"%PDF-local-source"
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "filename*=UTF-8''" in disposition
    assert "%E6%95%B0" in disposition
    assert str(api.store.root) not in str(response.headers)
    assert api.store.get(api.project_id)["last_used"] == before["last_used"]
    assert api.dispatched == []


@pytest.mark.parametrize("method,suffix,body", [
    ("get", "", None), ("post", "/open", None), ("put", "/draft", {}),
    ("post", "/fork", {}), ("post", "/resume", None), ("post", "/analyze", None),
    ("delete", "", None), ("get", "/source", None),
])
def test_service_can_be_disabled_dynamically(api, method, suffix, body):
    api.current[0] = None
    if body is None:
        response = getattr(api.client, method)(url(api, suffix))
    else:
        response = getattr(api.client, method)(url(api, suffix), json=body)
    assert response.status_code == 404
    assert api.client.get("/api/document-projects").json() == {"projects": []}


@pytest.mark.parametrize("body", [
    [], {}, {"draft": draft()}, {"expected_revision": True, "draft": draft()},
    {"expected_revision": -1, "draft": draft()}, {"expected_revision": 0, "draft": []},
    {"expected_revision": 0, "draft": draft(), "extra": True},
    {"expected_revision": 0, "draft": draft(api_key="must-not-persist")},
])
def test_invalid_draft_payloads_are_400_without_mutation(api, body):
    response = api.client.put(url(api, "/draft"), json=body)
    assert response.status_code == 400
    assert api.store.get(api.project_id)["revision"] == 0


@pytest.mark.parametrize("raw", [b"{broken", b'{"expected_revision":0,"expected_revision":1,"draft":{}}', b'{"value":NaN}'])
def test_malformed_ambiguous_json_is_rejected(api, raw):
    response = api.client.put(url(api, "/draft"), content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_request_body_has_a_bounded_total_size(api, monkeypatch):
    monkeypatch.setattr(api.store, "MAX_DRAFT_BYTES", 10)
    response = api.client.put(url(api, "/draft"), content=b"x" * 5000)
    assert response.status_code == 400


def test_invalid_fork_payload_does_not_create_half_project(api):
    before = len(api.store.list())
    response = api.client.post(url(api, "/fork"), json={"draft": {"questions": "bad"}})
    assert response.status_code == 400
    assert len(api.store.list()) == before


def test_bad_identifier_is_400_and_unknown_valid_identifier_is_404(api):
    assert api.client.get("/api/document-projects/bad-project-id").status_code == 400
    assert api.client.get("/api/document-projects/" + str(uuid.uuid4())).status_code == 404


def test_corrupt_project_is_not_deleted_and_error_does_not_leak_paths(api):
    path = api.store.root / api.project_id / "manifest.json"
    path.write_text("broken")
    response = api.client.get(url(api))
    assert response.status_code == 409
    assert str(path) not in response.text
    assert api.client.delete(url(api)).status_code == 409
    assert path.exists() and api.released == []


def test_unexpected_filesystem_error_is_sanitized(api, monkeypatch):
    monkeypatch.setattr(api.service, "detail", lambda *_: (_ for _ in ()).throw(OSError("private /Users/secret/folder")))
    response = api.client.get(url(api))
    assert response.status_code == 500 and "secret" not in response.text


def test_async_dispatch_callback_is_supported_without_calling_it_on_open(tmp_path):
    store = DocumentProjectStore(tmp_path / "async-projects")
    project_id = store.create(b"source", "test.pdf", {}, {})["project_id"]
    service = DocumentProjectService(store, FakeTasks())
    dispatched = []
    async def dispatch(project_id, analyze=False):
        dispatched.append(analyze)
        return {"status": "success", "project_id": project_id}
    app = FastAPI()
    register_document_project_routes(app, get_service=lambda: service, dispatch_project=dispatch, release_assets=lambda _: None)
    with TestClient(app) as client:
        assert client.post(f"/api/document-projects/{project_id}/open").status_code == 200
        assert dispatched == []
        assert client.post(f"/api/document-projects/{project_id}/analyze").status_code == 200
        assert dispatched == [True]


def test_asset_release_failure_does_not_report_committed_delete_as_failed(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project_id = store.create(b"source", "test.pdf", {}, {})["project_id"]
    store.register_assets(project_id, ["/static/uploads/tmp/image.png"])
    service = DocumentProjectService(store, FakeTasks())
    def fail_release(_):
        raise OSError("private cleanup details")
    app = FastAPI()
    register_document_project_routes(app, get_service=lambda: service, dispatch_project=lambda *_: None, release_assets=fail_release)
    with TestClient(app) as client:
        result = client.delete(f"/api/document-projects/{project_id}")
        assert result.status_code == 200 and result.json()["cleanup_pending"] is True
        assert "private cleanup details" not in result.text
        assert client.get(f"/api/document-projects/{project_id}").status_code == 404


def test_dispatcher_http_queue_errors_keep_their_original_status(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project_id = store.create(b"source", "test.pdf", {}, {})["project_id"]
    service = DocumentProjectService(store, FakeTasks())
    def dispatch(*_, **__):
        raise HTTPException(status_code=429, detail="后台队列已满")
    app = FastAPI()
    register_document_project_routes(app, get_service=lambda: service, dispatch_project=dispatch, release_assets=lambda _: None)
    with TestClient(app) as client:
        result = client.post(f"/api/document-projects/{project_id}/resume")
        assert result.status_code == 429 and result.json()["detail"] == "后台队列已满"
