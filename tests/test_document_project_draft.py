"""Persist the editable local source before optional metadata or source review."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
import requests

from pdf_feature_contract import experimental_pdf_only
from mathbank.ai_providers import resolve_text_provider
from mathbank.document_project_draft import execute_pdf_local_draft
from mathbank.document_projects import DocumentProjectError, DocumentProjectStore
from mathbank.task_manager import TaskCancelled


TREE = {"必修一": {"3. 函数": []}}
PROVIDER = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY": "fake-offline-only"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("No real provider HTTP"))
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)


@pytest.fixture
def local(tmp_path):
    source = "一、解答题\n1. 原题甲$x+1$。\n2. 原题乙$y^2$。\n# 参考答案\n1. 原解甲$x=2$。\n2. 原解乙$y=3$。"
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-isolated-local-draft", "draft.pdf", {}, TREE)
    return {"source": source, "source_pages": [{"page_number": 1, "markdown": source}],
        "layout_result": None, "curriculum": TREE, "store": store, "project_id": project["project_id"],
        "source_document_sha256": project["source_sha256"], "provider": PROVIDER}


def stages(local):
    store, pid = local["store"], local["project_id"]
    manifest = json.loads((store.root / pid / "manifest.json").read_text())
    return {name: store.load_stage(pid, name, item["input_fingerprint"]) for name, item in manifest["stages"].items()}


def reply(payload, *, status=200, extra=False):
    rows = json.loads(payload["messages"][1]["content"])["items"]
    data = {"items": [{"id": row["id"], "question_type": "detailed_answer", "difficulty": "normal",
        "category_compulsory": "必修一", "category_chapter": "3. 函数", "category_knowledge": ""} for row in rows]}
    if extra:
        for row in data["items"]: row["content"] = "forbidden generated body"
    class Reply:
        status_code = status
        headers = {}
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data, ensure_ascii=False)}}]}
    return Reply()


def test_both_immutable_source_plan_and_prepared_preview_are_saved_before_first_metadata_http(local):
    published, calls = [], []
    def prepare(rows):
        for row in rows: row["project_card_id"] = "card-" + str(row["source_number"])
        return rows
    def post(config, payload, **kwargs):
        saved = stages(local)
        raw = [packet for name, packet in saved.items() if name.startswith("local-draft-")]
        preview = [packet for name, packet in saved.items() if name.startswith("local-preview-")]
        assert len(raw) == len(preview) == len(published) == 1
        assert raw[0]["source"] == local["source"]
        assert all(q.get("project_card_id") for q in preview[0]["questions"])
        assert preview[0]["questions"] == published[0]["questions"]
        assert "questions" not in json.loads(payload["messages"][1]["content"]), "AI supplies metadata only"
        calls.append(1)
        return reply(payload)
    result = execute_pdf_local_draft(**local, post=post, prepare_questions=prepare, publish_draft=published.append)
    assert len(calls) == 1 and result["workflow"] == "local-draft.v1" and not result["partial"]
    assert [q["content"] for q in result["questions"]] == ["原题甲$x+1$。", "原题乙$y^2$。"]
    assert [q["answer_markdown"] for q in result["questions"]] == ["原解甲$x=2$。", "原解乙$y=3$。"]
    assert result["diagnostics"]["pdf_legacy_requests"]["body_generation_disabled"] is True


@pytest.mark.parametrize("prefix", ["local-draft-", "local-preview-"])
def test_required_local_save_failure_prevents_every_paid_metadata_call(local, monkeypatch, prefix):
    original = local["store"].save_stage
    def save(pid, stage, *args, **kwargs):
        if stage.startswith(prefix): raise OSError("isolated write failure")
        return original(pid, stage, *args, **kwargs)
    monkeypatch.setattr(local["store"], "save_stage", save)
    with pytest.raises(OSError):
        execute_pdf_local_draft(**local, post=lambda *a, **kw: pytest.fail("No API before durable local draft"))


def test_cancellation_after_local_preview_keeps_saved_pages_and_never_starts_metadata(local):
    cancelled = [False]
    def check():
        if cancelled[0]: raise TaskCancelled("user cancelled")
    def publish(plan): cancelled[0] = True
    with pytest.raises(TaskCancelled):
        execute_pdf_local_draft(**local, post=lambda *a, **kw: pytest.fail("No API after cancellation"),
                                publish_draft=publish, check_cancelled=check)
    assert len([key for key in stages(local) if key.startswith("local-draft-")]) == 1
    assert len([key for key in stages(local) if key.startswith("local-preview-")]) == 1


def test_failed_optional_metadata_returns_every_original_card_with_visible_classification_review(local):
    calls = []
    def post(config, payload, **kwargs): calls.append(1); return reply(payload, status=503)
    result = execute_pdf_local_draft(**local, post=post)
    assert len(calls) == 2 and len(result["questions"]) == 2 and not result["partial"]
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert result["diagnostics"]["pdf_draft_metadata"]["metadata_failed"]
    assert [q["content"] for q in result["questions"]] == ["原题甲$x+1$。", "原题乙$y^2$。"]
    assert not any("forbidden" in q["content"] for q in result["questions"])


def test_illegal_model_body_fields_never_replace_local_preview_or_trigger_body_fallback(local):
    calls = []
    def post(config, payload, **kwargs): calls.append(1); return reply(payload, extra=True)
    result = execute_pdf_local_draft(**local, post=post)
    assert len(calls) == 2 and len(result["questions"]) == 2
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert all("forbidden" not in q["content"] for q in result["questions"])


@pytest.mark.parametrize("field", ["content", "answer_markdown"])
def test_preparation_callback_cannot_silently_replace_the_first_local_body(local, field):
    def prepare(rows): rows[0][field] = "rewritten before preview"; return rows
    with pytest.raises(DocumentProjectError):
        execute_pdf_local_draft(**local, prepare_questions=prepare,
            post=lambda *a, **kw: pytest.fail("No API for modified original local body"))
    saved = stages(local)
    original = next(packet for key, packet in saved.items() if key.startswith("local-draft-"))
    assert original["questions"][0][field] != "rewritten before preview"


def test_published_preview_is_a_copy_and_metadata_cache_reuses_without_another_post(local):
    calls = []
    def post(config, payload, **kwargs): calls.append(1); return reply(payload)
    def publish(snapshot): snapshot["questions"][0]["content"] = "UI changed its own snapshot"
    first = execute_pdf_local_draft(**local, post=post, publish_draft=publish)
    second = execute_pdf_local_draft(**local, post=lambda *a, **kw: pytest.fail("Per-card cache must survive rerun"))
    assert len(calls) == 1 and first["questions"] == second["questions"]
    assert second["diagnostics"]["pdf_local_draft"]["reused_local_plan"]


@pytest.mark.parametrize("equivalent", [True, False])
@experimental_pdf_only
def test_original_page_verification_can_confirm_but_cannot_repair_a_local_body(tmp_path, monkeypatch, equivalent):
    path = Path(__file__).with_name("test_pdf_source_verify.py")
    spec = importlib.util.spec_from_file_location("read_only_source_verify_fixture", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    setup = module.setup.__wrapped__(tmp_path, monkeypatch)
    original = deepcopy(setup.questions)
    data = module.all_equivalent(2, setup)
    if not equivalent:
        for row in data["items"]:
            row["decision"] = "different"; row["checks"]["math_and_conditions"] = False
            row["evidence"] = "原页一个明确系数与候选不同。"
    report = module.call(setup, data, allow_repair=False)
    assert report["repair_enabled"] is False
    assert len(setup.calls) == report["calls"] == 1
    assert not report.get("repair_calls", 0) and not report.get("repaired", 0)
    for old, new in zip(original, setup.questions):
        assert old["content"] == new["content"] and old["answer_markdown"] == new["answer_markdown"]
        assert new["source_review"]["required"] is (not equivalent)
