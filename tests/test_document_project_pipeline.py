"""Fake-only local-structure persistence/cache tests; no DB or real model."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import requests

import mathbank.document_project_pipeline as module
from mathbank.ai_providers import resolve_text_provider
from mathbank.document_project_pipeline import PLAN_STAGE, RESULT_STAGE, parse_project_pdf
from mathbank.document_projects import DocumentProjectStore, ProjectCorrupt
from mathbank.pdf_local_structure import PdfLocalStructureError
from mathbank.task_manager import TaskCancelled


TREE = {"必修一": {"3. 函数": []}}
PROVIDER = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY": "fake-offline-key"})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("Real model requests forbidden"))
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)


@pytest.fixture
def project(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    metadata = store.create(b"%PDF-immutable-test-source", "测试.pdf", {}, TREE)
    source = "1. 甲题$x=1$。\n2. 乙题$x=2$。"
    return SimpleNamespace(store=store, project_id=metadata["project_id"], sha=metadata["source_sha256"],
                           source=source, pages=[{"page_number": 1, "markdown": source, "revision": 1}])


class Response:
    headers = {}
    def __init__(self, value=None, status=200):
        self.value, self.status_code = value, status
    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.value, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def reply(payload, bad=None, unknown=False):
    data = json.loads(payload["messages"][1]["content"])
    book = next(iter(data["curriculum"])); chapter = next(iter(data["curriculum"][book]))
    questions = []
    for block in data["blocks"]:
        text = block["text"].strip()
        if not text or not text[0].isdigit() or ". " not in text:
            continue
        number = int(text.split(".", 1)[0])
        questions.append({"id": f"q{number}", "source_number": number,
            "stem": [block["id"]], "answer": [], "answer_cells": [],
            "question_type": "detailed_answer", "difficulty": "bad" if number == bad else "normal",
            "category_compulsory": book, "category_chapter": chapter, "category_knowledge": ""})
    return {"input_sha256": data["input_sha256"], "questions": [] if unknown else questions,
            "roles": [{"role": "unknown", "blocks": [block["id"] for block in data["blocks"]]}] if unknown else []}


def run(project, post, **kwargs):
    diagnostics = kwargs.pop("diagnostics", {})
    result = parse_project_pdf(project.store, project.project_id,
        kwargs.pop("source", project.source), kwargs.pop("source_pages", project.pages), kwargs.pop("curriculum", TREE),
        provider=kwargs.pop("provider", PROVIDER), post=post, diagnostics=diagnostics,
        source_document_sha256=kwargs.pop("source_document_sha256", project.sha), **kwargs)
    return result, diagnostics


def good_post(calls):
    def post(_provider, payload, **kwargs):
        calls.append(deepcopy(payload))
        return Response(reply(payload))
    return post


def test_complete_result_cache_reuses_with_zero_posts_after_restart(project):
    calls = []
    first, first_diagnostics = run(project, good_post(calls))
    assert len(calls) == 1 and not first["partial"]
    first_snapshot = deepcopy(first)
    assert first_diagnostics["pdf_project_structure"]["plan_saved"] is True
    assert first_diagnostics["pdf_project_structure"]["result_saved"] is True
    project.store = DocumentProjectStore(project.store.root)
    result, diagnostics = run(project, lambda *_a, **_k: pytest.fail("cached result must make zero POSTs"))
    assert result == first_snapshot
    assert diagnostics["pdf_project_structure"]["reused"] is True
    assert diagnostics["pdf_local_structure"]["calls"] == 0
    assert diagnostics["pdf_local_structure"]["usage"] == {}
    assert result["source_matches"] == first_snapshot["source_matches"]


def test_callbacks_persist_plan_before_post_and_valid_partial_before_repair(project):
    calls = []; diagnostics = {}
    def post(_provider, payload, **kwargs):
        calls.append(deepcopy(payload))
        fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
        assert project.store.load_stage(project.project_id, PLAN_STAGE, fingerprint)["plan"]["source"] == project.source
        if len(calls) == 2:
            cached = project.store.load_stage(project.project_id, RESULT_STAGE, fingerprint)["result"]
            assert cached["question_ids"] == ["q2"] and cached["partial"]
        return Response(reply(payload, bad=1 if len(calls) == 1 else None))
    result, _ = run(project, post, diagnostics=diagnostics)
    assert len(calls) == 2 and result["question_ids"] == ["q1", "q2"]


def test_cached_partial_only_resends_failed_group_and_retains_success_exactly(project):
    calls = []
    def first_post(_provider, payload, **kwargs):
        calls.append(deepcopy(payload))
        return Response(reply(payload, bad=1)) if len(calls) == 1 else Response(status=403)
    partial, _ = run(project, first_post)
    assert partial["question_ids"] == ["q2"] and partial["retry_question_ids"] == ["q1"]
    successful = deepcopy(partial["questions"][0]); repair_calls = []
    complete, diagnostics = run(project, good_post(repair_calls))
    assert len(repair_calls) == 1
    data = json.loads(repair_calls[0]["messages"][1]["content"])
    assert data["retry_question_ids"] == ["q1"]
    assert "甲题" in repair_calls[0]["messages"][1]["content"] and "乙题" not in repair_calls[0]["messages"][1]["content"]
    assert complete["question_ids"] == ["q1", "q2"]
    assert complete["questions"][1] == successful
    assert diagnostics["pdf_project_structure"]["retry_from_cached_partial"] is True


def test_cached_unscoped_zero_question_partial_is_manual_and_preserves_exact_source(project):
    calls = []
    def post(_provider, payload, **kwargs):
        calls.append(payload); return Response(reply(payload, unknown=True))
    result, diagnostics = run(project, post)
    assert len(calls) == 1 and result["questions"] == [] and result["partial"]
    fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
    saved = project.store.load_stage(project.project_id, PLAN_STAGE, fingerprint)
    assert saved["plan"]["source"] == project.source and saved["source_pages"] == project.pages
    reused, second = run(project, lambda *_a, **_k: pytest.fail("unknown ownership must not prompt a full retry"))
    assert reused == result
    assert second["pdf_local_structure"]["status"] == "partial_manual_review"


def test_unusable_plan_retains_sources_and_never_posts(project):
    result, diagnostics = run(project, lambda *_a, **_k: pytest.fail("unusable local source must not POST"),
                              source="无法与原页对应的编辑文本")
    assert result["questions"] == [] and result["partial"]
    fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
    saved = project.store.load_stage(project.project_id, PLAN_STAGE, fingerprint)
    assert saved["source_pages"] == project.pages
    assert saved["plan"]["source"] == "无法与原页对应的编辑文本"
    assert diagnostics["pdf_project_structure"]["result_saved"] is True


@pytest.mark.parametrize("change", ["source", "page_revision", "page_version", "page_number", "curriculum", "curriculum_order"])
def test_source_page_version_and_curriculum_changes_never_borrow_old_result(project, change):
    calls = []
    _, initial = run(project, good_post(calls))
    kwargs = {}; pages = deepcopy(project.pages)
    if change == "source":
        new_source = project.source.replace("甲题", "新甲题")
        pages[0]["markdown"] = new_source
        kwargs.update(source=new_source, source_pages=pages)
    elif change == "page_revision":
        pages[0]["revision"] = 2; kwargs["source_pages"] = pages
    elif change == "page_version":
        pages[0]["version"] = 2; kwargs["source_pages"] = pages
    elif change == "page_number":
        pages[0]["page_number"] = 2; kwargs["source_pages"] = pages
    elif change == "curriculum":
        kwargs["curriculum"] = {"必修一": {"4. 函数": []}}
    else:
        kwargs["curriculum"] = {"选修一": {"1. 圆锥曲线": []}, **TREE}
    result, diagnostics = run(project, good_post(calls), **kwargs)
    assert len(calls) == 2 and result["questions"]
    assert diagnostics["pdf_project_structure"]["fingerprint"] != initial["pdf_project_structure"]["fingerprint"]
    assert diagnostics["pdf_project_structure"]["reused"] is False


def test_model_configuration_is_not_an_implicit_cache_invalidation(project):
    run(project, good_post([]))
    result, diagnostics = run(project, lambda *_a, **_k: pytest.fail("changing global model is not explicit redo"),
                              provider=SimpleNamespace(model_name="a-different-model"))
    assert result["questions"] and diagnostics["pdf_project_structure"]["reused"]


def test_explicit_force_can_redo_same_input(project):
    calls = []
    run(project, good_post(calls))
    _, diagnostics = run(project, good_post(calls), force=True)
    assert len(calls) == 2 and diagnostics["pdf_project_structure"]["force"]
    assert diagnostics["pdf_project_structure"]["reused"] is False


@pytest.mark.parametrize("stage", [PLAN_STAGE, RESULT_STAGE])
def test_private_write_failure_is_truthfully_reported_without_another_paid_post(project, monkeypatch, stage):
    original = project.store.save_stage
    def fail(project_id, name, *args, **kwargs):
        if name == stage:
            raise OSError("private disk path and account data")
        return original(project_id, name, *args, **kwargs)
    monkeypatch.setattr(project.store, "save_stage", fail)
    calls = []
    result, diagnostics = run(project, good_post(calls))
    report = diagnostics["pdf_project_structure"]
    assert len(calls) == 1 and len(result["questions"]) == 2
    assert report["plan_saved" if stage == PLAN_STAGE else "result_saved"] is False
    assert report["cache_errors"] and "private disk" not in json.dumps(report)


def test_cancellation_after_primary_keeps_immediately_saved_partial_for_next_resume(project):
    calls = []
    def post(_provider, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 2:
            raise TaskCancelled("cancel during scoped repair")
        return Response(reply(payload, bad=1))
    with pytest.raises(TaskCancelled):
        run(project, post)
    repaired = []
    result, diagnostics = run(project, good_post(repaired))
    assert len(repaired) == 1 and result["question_ids"] == ["q1", "q2"]
    assert diagnostics["pdf_project_structure"]["retry_from_cached_partial"]


def test_corrupt_cache_never_becomes_reason_to_make_an_automatic_paid_post(project):
    _, diagnostics = run(project, good_post([]))
    fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
    manifest = json.loads((project.store.root / project.project_id / "manifest.json").read_text())
    result_file = manifest["stages"][RESULT_STAGE]["file"]
    (project.store.root / project.project_id / result_file).write_bytes(b"damaged")
    with pytest.raises(ProjectCorrupt):
        run(project, lambda *_a, **_k: pytest.fail("corrupt cache must fail locally"))


@pytest.mark.parametrize("field", ["content", "source_matches"])
def test_cache_reassembles_source_and_does_not_trust_a_changed_body_or_evidence(project, field):
    _, diagnostics = run(project, good_post([]))
    fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
    packet = project.store.load_stage(project.project_id, RESULT_STAGE, fingerprint)
    if field == "content":
        packet["result"]["questions"][0]["content"] = "错误覆盖的正文"
    else:
        packet["result"]["source_matches"][0]["source_start"] += 1
    # A valid disk receipt alone is not a source-reconstruction certificate.
    project.store.save_stage(project.project_id, RESULT_STAGE, packet, fingerprint, complete=True)
    with pytest.raises(PdfLocalStructureError):
        run(project, lambda *_a, **_k: pytest.fail("changed cache evidence must not POST"))


def test_valid_cache_keeps_additional_owned_question_and_evidence_fields(project):
    _, diagnostics = run(project, good_post([]))
    fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
    packet = project.store.load_stage(project.project_id, RESULT_STAGE, fingerprint)
    packet["result"]["questions"][0]["local_audit"] = {"retained": True}
    packet["result"]["source_matches"][0]["local_audit"] = "retained"
    project.store.save_stage(project.project_id, RESULT_STAGE, packet, fingerprint, complete=True)
    reused, _ = run(project, lambda *_a, **_k: pytest.fail("cache must not POST"))
    assert reused["questions"][0]["local_audit"] == {"retained": True}
    assert reused["source_matches"][0]["local_audit"] == "retained"


def test_legacy_generic_first_pass_review_is_upgraded_locally_without_model_calls(project):
    _, diagnostics = run(project, good_post([]))
    fingerprint = diagnostics["pdf_project_structure"]["fingerprint"]
    packet = project.store.load_stage(project.project_id, RESULT_STAGE, fingerprint)
    row = packet["result"]["questions"][0]
    row["source_review"] = {"required": True, "reasons": ["首次OCR稿已按来源块保留；尚未证明与原PDF逐字一致。"]}
    row["pdf_local_source"].pop("original_verified", None)
    original_classification = deepcopy(row.get("classification_review"))
    project.store.save_stage(project.project_id, RESULT_STAGE, packet, fingerprint, complete=True)
    reused, _ = run(project, lambda *_a, **_k: pytest.fail("legacy status upgrade must stay local"))
    assert reused["questions"][0]["source_review"]["required"] is False
    assert reused["questions"][0]["source_review"]["status"] == "not_verified"
    assert reused["questions"][0]["pdf_local_source"]["original_verified"] is False
    assert reused["questions"][0].get("classification_review") == original_classification
    saved = project.store.load_stage(project.project_id, RESULT_STAGE, fingerprint)
    assert saved["result"]["questions"][0]["source_review"]["required"] is False


def test_cache_packet_cannot_claim_another_source_or_project_document(project):
    with pytest.raises(PdfLocalStructureError, match="document_changed"):
        run(project, lambda *_a, **_k: pytest.fail("wrong PDF identity must not POST"), source_document_sha256="0" * 64)


def test_pipeline_does_not_enumerate_other_projects(project, monkeypatch):
    monkeypatch.setattr(project.store, "list", lambda *_a, **_k: pytest.fail("must not enumerate projects"))
    result, _ = run(project, good_post([]))
    assert result["questions"]
