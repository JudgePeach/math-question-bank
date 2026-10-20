"""Rejected first-pass packets survive locally; never contact a provider."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from pdf_feature_contract import experimental_pdf_only
import requests

from mathbank.document_projects import DocumentProjectStore
from mathbank.pdf_ocr_archive import retain_pdf_ocr_response
from mathbank import pdf_vision_request as vision
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(vision, "PDF_VISION_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("No paid API"))


@pytest.fixture
def archive(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    pdf = b"%PDF original fixture"
    project = store.create(pdf, "exam.pdf", {}, {})["project_id"]
    png = tmp_path / "page.png"
    Image.new("RGB", (8, 8), "white").save(png)
    metadata = {"stage": "joint_page", "page_index": 0, "attempt": 1, "max_attempts": 2,
                "http_status": 200, "model": "test-vision", "request_payload_sha256": "a" * 64,
                "navigation_image_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                "api_key": "NEVER-SAVE", "payload": {"base64": "NEVER-SAVE"}}
    def save(body, meta=None, **kwargs):
        return retain_pdf_ocr_response(store, project, hashlib.sha256(pdf).hexdigest(), body,
            metadata if meta is None else meta, page_number=1, image_path=png, **kwargs)
    return SimpleNamespace(store=store, project=project, png=png, meta=metadata, save=save, root=tmp_path)


def packet(text="complete", finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": text}}]}


def test_truncated_inner_json_is_exactly_retained_private_and_survives_restart(archive):
    body = packet('{"markdown":"$x=1$未闭合', "length")
    before = deepcopy(body)
    receipt = archive.save(body)
    store = DocumentProjectStore(archive.root / "projects")
    saved = store.load_stage(archive.project, receipt["stage"], receipt["input_fingerprint"])
    assert saved["body"] == before == body
    assert saved["accepted_source"] is saved["original_verified"] is False
    assert saved["request_payload_sha256"] == "a" * 64
    assert "NEVER-SAVE" not in json.dumps(saved)
    assert store.get(archive.project)["has_draft"] is False


def test_same_binding_different_response_cannot_overwrite_original_and_identical_is_idempotent(archive):
    first = archive.save(packet("first"))
    assert archive.save(packet("first")) == first
    second = archive.save(packet("second"))
    assert first["stage"] != second["stage"]
    for receipt, text in ((first, "first"), (second, "second")):
        row = archive.store.load_stage(archive.project, receipt["stage"], receipt["input_fingerprint"])
        assert row["body"] == packet(text)


def test_archive_rejects_wrong_document_and_changed_navigation(archive):
    with pytest.raises(ValueError):
        retain_pdf_ocr_response(archive.store, archive.project, "b" * 64, packet(), archive.meta,
            page_number=1, image_path=archive.png)
    Image.new("RGB", (8, 8), "black").save(archive.png)
    with pytest.raises(ValueError):
        archive.save(packet())


def test_archive_rejects_symlink_navigation(archive):
    target = archive.png.with_name("target.png")
    archive.png.rename(target)
    try:
        archive.png.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Platform does not permit test symlinks")
    with pytest.raises(ValueError):
        archive.save(packet())


def request(post, on_response, validate=None, **kwargs):
    return vision.request_pdf_vision(SimpleNamespace(provider_code="fake"),
        {"model": "configured", "messages": [{"role": "user", "content": "data:image/png;base64,PRIVATE"}]},
        post=post, validate=validate or (lambda body: {"markdown": vision.completion_content(body, "PDF", max_chars=1000)}),
        on_response=on_response, label="PDF", stage="joint_page", page_index=0, **kwargs)


@experimental_pdf_only
def test_length_packet_is_archived_before_finish_validation_and_same_retry_budget(archive):
    bodies = [packet('{"markdown":"truncated', "length"), packet("complete")]
    calls = []
    receipts = []
    def post(*a, **k):
        body = bodies[len(calls)]; calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(body))
    def retain(body, meta):
        receipts.append(archive.save(body, meta))
    result = request(post, retain)
    assert len(calls) == len(receipts) == result["visual_calls"] == 2
    assert result["attempts"][0]["finish_reason"] == "length"
    assert all(row["response_retained"] for row in result["attempts"])
    saved = archive.store.load_stage(archive.project, receipts[0]["stage"], receipts[0]["input_fingerprint"])
    assert saved["body"] == bodies[0]
    assert "PRIVATE" not in json.dumps(saved["metadata"])


@experimental_pdf_only
def test_malformed_inner_json_retained_before_parser_rejects_and_callback_cannot_mutate_response(archive):
    body = packet('{"markdown":')
    calls = []; receipts = []
    def post(*a, **k):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(body))
    def retain(value, meta):
        receipts.append(archive.save(value, meta)); value.clear()
    def validate(value):
        json.loads(vision.completion_content(value, "PDF", max_chars=1000))
    with pytest.raises(vision.PDFVisionRequestError):
        request(post, retain, validate)
    assert len(calls) == len(receipts) == 2
    assert all(archive.store.load_stage(archive.project,r["stage"],r["input_fingerprint"])["body"] == body for r in receipts)


@experimental_pdf_only
def test_disk_error_does_not_buy_another_response_and_records_only_error_type():
    calls = []
    def post(*a, **k):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: packet())
    def fail(*a):
        raise OSError("private path / secret")
    result = request(post, fail)
    assert len(calls) == result["visual_calls"] == 1
    assert result["attempts"][0]["response_archive_error"] == "OSError"
    assert "private" not in str(result) and "secret" not in str(result)


@experimental_pdf_only
def test_request_digest_binds_actual_image_input_and_model_controls_without_copying_payload():
    captured = []
    payload = {"model": "vision", "max_tokens": 99, "enable_thinking": False,
        "messages": [{"role": "user", "content": [{"type": "image_url",
            "image_url": {"url": "data:image/png;base64,EXACT-INPUT"}}]}]}
    def post(provider, actual, **kwargs):
        assert actual == payload
        return SimpleNamespace(status_code=200, json=lambda: packet())
    vision.request_pdf_vision(SimpleNamespace(), payload, post=post,
        validate=lambda body: {"markdown": "ok"}, label="PDF", stage="joint_page", page_index=0,
        on_response=lambda body, meta: captured.append(meta))
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False,sort_keys=True,
        separators=(",", ":"),allow_nan=False).encode()).hexdigest()
    assert captured[0]["request_payload_sha256"] == expected
    assert captured[0]["max_tokens"] == 99 and captured[0]["enable_thinking"] is False
    assert "EXACT-INPUT" not in json.dumps(captured)


@experimental_pdf_only
def test_callback_cancellation_propagates_without_retry():
    calls = []
    def post(*a, **k):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: packet())
    def cancel(*a):
        raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        request(post, cancel)
    assert calls == [1]
