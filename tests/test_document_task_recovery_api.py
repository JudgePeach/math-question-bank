"""Local document recovery API contracts in an isolated application copy."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest
import requests

from mathbank.document_task_store import DocumentTaskStore, DurableDocumentTasks


@pytest.fixture
def recovery_api(client, monkeypatch, tmp_path):
    import main
    store = DocumentTaskStore(tmp_path / "tasks")
    manager = DurableDocumentTasks(store=store, max_workers=2, max_queue=2,
        transient_document_types={"pdf"} if main.PDF_CLOUD_ONLY else ())
    monkeypatch.setattr(main, "DOCUMENT_TASKS", manager)
    monkeypatch.setattr(main, "DOCUMENT_TASK_STORE", store)
    monkeypatch.setattr(main, "DOCUMENT_TASK_INPUTS", {})
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"必修一": {"集合": [], "函数": []}})
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_k: pytest.fail("No live AI"))
    calls = []
    monkeypatch.setattr(manager, "submit", lambda *args, **kwargs: calls.append((args, kwargs)))
    yield main, client, manager, store, calls
    manager.shutdown()


def upload(api, *, request_id="request-identity-001", content=b"%PDF-1.4 fixture", kind="pdf", **data):
    main, client, *_ = api
    payload = {"client_request_id": request_id, **data}
    return client.post(f"/api/upload/{kind}-task", data=payload,
        files={"file": ("数学原卷." + kind, content, "application/octet-stream")},
        headers={"X-Local-Token": main.LOCAL_TOKEN})


@pytest.mark.parametrize("kind,content", [("pdf", b"%PDF-1.4 fixture"), ("docx", b"PK\x03\x04fixture")])
def test_repeated_upload_request_is_idempotent_and_other_source_is_rejected(recovery_api, kind, content):
    main, client, manager, store, calls = recovery_api
    first = upload(recovery_api, kind=kind, content=content)
    second = upload(recovery_api, kind=kind, content=content)
    assert first.status_code == second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"] and len(calls) == 1
    changed = upload(recovery_api, kind=kind, content=content + b"changed")
    assert changed.status_code == 409 and len(calls) == 1
    persistent = kind != "pdf" or not main.PDF_CLOUD_ONLY
    assert len(list(store.root.glob("*.source"))) == int(persistent)
    if not persistent:
        assert main.DOCUMENT_TASK_INPUTS[first.json()["task_id"]] == content


@pytest.mark.parametrize("kind,content", [("pdf", b"%PDF-1.4 fixture"), ("docx", b"PK\x03\x04fixture")])
def test_invalid_request_identity_stays_400(recovery_api, kind, content):
    response = upload(recovery_api, kind=kind, content=content, request_id="../invalid")
    assert response.status_code == 400 and recovery_api[-1] == []


def test_parallel_uploads_cannot_dispatch_two_tasks(recovery_api):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: upload(recovery_api), range(2)))
    assert all(result.status_code == 200 for result in results)
    assert len({result.json()["task_id"] for result in results}) == 1
    assert len(recovery_api[-1]) == 1


@pytest.mark.parametrize("kind,content", [("pdf", b"%PDF-1.4 fixture"), ("docx", b"PK\x03\x04fixture")])
def test_cancel_before_upload_has_no_worker_and_obeys_document_persistence(recovery_api, kind, content):
    main, client, manager, store, calls = recovery_api
    response = client.post("/api/tasks/cancel-by-request", json={"client_request_id": "request-identity-001", "document_type": kind},
                           headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200
    task_id = upload(recovery_api, kind=kind, content=content).json()["task_id"]
    assert manager.snapshot(task_id)["status"] == "cancelled" and calls == []
    reloaded = DurableDocumentTasks(store=store, max_workers=1, max_queue=0,
        transient_document_types={"pdf"} if main.PDF_CLOUD_ONLY else ())
    try:
        persistent = kind != "pdf" or not main.PDF_CLOUD_ONLY
        assert reloaded.request_cancelled("request-identity-001") is persistent
        restored = reloaded.snapshot(task_id)
        if persistent:
            assert restored["status"] == "cancelled"
        else:
            assert restored is None
    finally:
        reloaded.shutdown()


def test_cancellation_between_creation_and_submit_returns_task_instead_of_500(recovery_api, monkeypatch):
    main, _, manager, _, calls = recovery_api
    def cancelled_submit(task_id, *_a, **_k):
        manager.cancel(task_id)
        raise ValueError("terminal task")
    monkeypatch.setattr(manager, "submit", cancelled_submit)
    response = upload(recovery_api)
    assert response.status_code == 200
    assert manager.snapshot(response.json()["task_id"])["status"] == "cancelled" and calls == []


@pytest.mark.parametrize("kind,content", [("pdf", b"%PDF-1.4 fixture"), ("docx", b"PK\x03\x04fixture")])
def test_status_hides_private_checkpoints_and_original_download_is_verified(recovery_api, kind, content):
    main, client, manager, store, calls = recovery_api
    task_id = upload(recovery_api, kind=kind, content=content).json()["task_id"]
    manager.update(task_id, pdf_resume_state={"private": "pdf"}, docx_resume_state={"private": "word"})
    manager.fail(task_id, "retry explicitly")
    response = client.get(f"/api/tasks/{task_id}/status")
    body = response.json()
    assert body["source_available"] is True
    assert body["can_resume"] is (kind != "pdf" or not main.PDF_CLOUD_ONLY)
    assert not {"pdf_resume_state", "docx_resume_state", "document_input"}.intersection(body)
    original = client.get(f"/api/tasks/{task_id}/source")
    assert original.status_code == 200 and original.content == content
    assert "attachment; filename*=UTF-8''" in original.headers["content-disposition"]
    assert len(calls) == 1
    if kind == "pdf" and main.PDF_CLOUD_ONLY:
        main.DOCUMENT_TASK_INPUTS[task_id] = b"changed bytes"
    else:
        store.source(task_id, b"changed bytes")
    assert client.get(f"/api/tasks/{task_id}/source").status_code == 409
    assert client.post(f"/api/tasks/{task_id}/resume", headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 409
    assert len(calls) == 1


def test_resume_checks_curriculum_and_missing_source_before_dispatch(recovery_api, monkeypatch):
    main, client, manager, store, calls = recovery_api
    task_id = upload(recovery_api, kind="docx", content=b"PK\x03\x04fixture").json()["task_id"]
    manager.fail(task_id, "explicit retry")
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"必修一": {"函数": [], "集合": []}})
    response = client.post(f"/api/tasks/{task_id}/resume", headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 409 and "教材" in response.json()["detail"]
    assert manager.snapshot(task_id)["status"] == "error" and len(calls) == 1
    store._path(task_id, ".source").unlink()
    assert client.get(f"/api/tasks/{task_id}/status").json()["source_available"] is False
    assert client.post(f"/api/tasks/{task_id}/resume", headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 409
    assert len(calls) == 1


def test_parallel_resume_dispatches_once_and_uses_private_accepted_checkpoint(recovery_api):
    main, client, manager, _, calls = recovery_api
    task_id = upload(recovery_api, kind="docx", content=b"PK\x03\x04fixture").json()["task_id"]
    checkpoint = {"accepted_split": {"questions": [{"content": "accepted"}]}}
    manager.update(task_id, docx_resume_state=checkpoint)
    manager.fail(task_id, "explicit retry")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _value: client.post(f"/api/tasks/{task_id}/resume",
            headers={"X-Local-Token": main.LOCAL_TOKEN}), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(calls) == 2 and calls[-1][1]["resume_state"] == checkpoint


def test_resume_settings_corruption_does_not_dispatch(recovery_api):
    main, client, manager, _, calls = recovery_api
    task_id = upload(recovery_api, kind="docx", content=b"PK\x03\x04fixture").json()["task_id"]
    source = deepcopy(manager.snapshot(task_id)["document_input"])
    source["options"]["generate_answers"] = "corrupted"
    manager.update(task_id, document_input=source)
    manager.fail(task_id, "explicit retry")
    response = client.post(f"/api/tasks/{task_id}/resume", headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 409 and len(calls) == 1


def test_current_cloud_pdf_resume_requires_reupload_without_dispatch(recovery_api):
    main, client, manager, store, calls = recovery_api
    assert main.PDF_CLOUD_ONLY is True
    task_id = upload(recovery_api).json()["task_id"]
    manager.fail(task_id, "explicit retry")
    response = client.post(f"/api/tasks/{task_id}/resume", headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 409 and "重新上传" in response.json()["detail"]
    assert len(calls) == 1 and list(store.root.iterdir()) == []
