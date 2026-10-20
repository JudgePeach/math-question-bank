"""Read-only C source review binds each field, never an unlocated page risk."""
from copy import deepcopy
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from pdf_feature_contract import experimental_pdf_only
import requests

from mathbank import pdf_source_verify as verify
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_figures import apply_pdf_layout_reviews, refresh_pdf_review_items
from mathbank.pdf_fragment_draft import prepare_pdf_fragment_draft
from mathbank.pdf_fragment_sources import prepare_pdf_fragment_sources
from mathbank.pdf_page_fragments import SCHEMA, validate_pdf_page_fragments


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
        lambda *_a, **_kw: pytest.fail("No model/network is allowed"))
    raw = []
    numbers = (1, 10, 14, 16)
    for page_index in range(2):
        role = "stem" if page_index == 0 else "answer"
        blocks = []
        for number in numbers:
            text = f"{number}. " + ("原题$x=1$。" if role == "stem" else "原版解答$x+1=2$。")
            if (page_index, number) in {(0, 10), (0, 16), (1, 14)}:
                text += f"[插图待补: 图{number}]"
            blocks.append({"role": role, "number": number, "edge": "none", "text": text + "\n\n"})
        packet = {"schema": SCHEMA, "blocks": blocks, "figures": [], "ignored_candidates": [],
                  "page_complete": False if page_index == 0 else True, "warnings": []}
        page = validate_pdf_page_fragments(packet, {"page_index": page_index, "candidates": []})
        raw.append({"page_number": page_index + 1, "markdown": page["markdown"], "fragments": page["fragments"]})
    bundle = prepare_pdf_fragment_sources(raw)
    pages = [{**page, "origin": "joint_vision", "figures": []} for page in bundle["source_pages"]]
    plan = prepare_pdf_fragment_draft(bundle["source"], pages)
    questions = deepcopy(plan["questions"])
    for question in questions:
        # Historical page excerpts remain audit evidence, not unique field
        # evidence and not data to insert into the visual candidate.
        question["source_review"]["source_excerpt"] = pages[0]["markdown"]
    diagnostics = {"source_matches": deepcopy(plan["source_matches"]),
        "pdf_local_draft": {"source_sha256": plan["source_sha256"]},
        "pdf_fragment_draft": {"schema": SCHEMA}}
    layout = {"pages": [{"page_index": i, "figures": []} for i in range(2)],
        "page_texts": [page["markdown"] for page in pages], "issues": [{"page_index": 0,
            "page_wide": True, "figure_ids": [], "reason": "配图检查未完成，请核对整页。", "source_excerpt": pages[0]["markdown"]}]}
    apply_pdf_layout_reviews(questions, layout, diagnostics)
    root = tmp_path / "uploads"
    (root / "tmp").mkdir(parents=True)
    urls = []
    for page in range(1, 3):
        name = f"pdf_page_test_{page}.png"
        Image.new("RGB", (20, 20), "white").save(root / "tmp" / name)
        urls.append("/static/uploads/tmp/" + name)
    monkeypatch.setattr(verify, "UPLOADS_DIR", root)
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "test-only-unused",
        "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3-VL-8B-Instruct"})
    monkeypatch.setattr(verify, "resolve_ocr_provider", lambda _engine: provider)
    value = SimpleNamespace(questions=questions, diagnostics=diagnostics, pages=pages, layout=layout,
        urls=urls, calls=[], decisions="equivalent", mutate=None)
    def post(_provider, payload, **kwargs):
        value.calls.append(deepcopy(payload))
        # Only q1 has complete safe field evidence; no diagram candidate may
        # enter the request merely because the original page is available.
        prompt = payload["messages"][0]["content"][0]["text"]
        assert '"source_number": 1,' in prompt
        assert '"source_number": 10,' not in prompt
        if value.mutate:
            value.mutate()
        verdict = {"items": [{"id": "item_001", "source_number": 1, "source_pages": [1, 2],
            "decision": value.decisions, "checks": {key: value.decisions == "equivalent" for key in verify._CHECKS},
            "evidence": "test-only: bounded original stem and answer compared"}]}
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(verdict)}}]})
    monkeypatch.setattr(verify, "post_chat_completion", post)
    return value


def run(state, **kwargs):
    return verify.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, [1, 2],
        source_pages=state.pages, **kwargs)


@experimental_pdf_only
def test_fragment_no_figure_peer_is_reviewable_without_page_wide_broadcast(state):
    original = deepcopy(state.questions)
    report = run(state)
    assert report["calls"] == report["checked"] == report["confirmed"] == 1
    assert report["repair_enabled"] is False and report["pending"] == report["skipped"] == 3
    assert {item["question_index"]: item["code"] for item in report["skipped_reasons"]} == {
        1: "figure_risk", 2: "figure_risk", 3: "figure_risk"}
    assert state.questions[0]["source_review"]["required"] is False
    for question, old in zip(state.questions, original):
        assert question["content"] == old["content"] and question["answer_markdown"] == old["answer_markdown"]
    assert state.questions[1:] == original[1:]
    assert state.layout["issues"][0]["scope"] == "page_unassigned"
    assert state.layout["issues"][0]["affected_questions"] == []


@pytest.mark.parametrize("change", ["source_hash", "range", "excerpt", "sidecar", "duplicate", "wrong_page"])
def test_fragment_field_evidence_must_be_exact_and_hash_bound(state, change):
    match = state.diagnostics["source_matches"][0]
    if change == "source_hash": state.diagnostics["pdf_local_draft"]["source_sha256"] = "0" * 64
    elif change == "range": match["source_ranges"][0][0] += 1
    elif change == "excerpt": match["source_excerpt"] = state.pages[0]["markdown"]
    elif change == "sidecar": state.pages[0]["fragments"]["blocks"][0]["text_sha256"] = "0" * 64
    elif change == "duplicate": state.diagnostics["source_matches"].append({**deepcopy(match), "question_index": 99})
    elif change == "wrong_page": state.diagnostics["pdf_review_items"][0]["source_pages"] = [1]
    original = deepcopy(state.questions)
    report = run(state)
    assert not state.calls and report["calls"] == report["checked"] == 0
    assert state.questions == original


@experimental_pdf_only
def test_fragment_sidecar_mutation_during_request_invalidates_verdict(state):
    original = deepcopy(state.questions)
    state.mutate = lambda: state.pages[0]["fragments"]["blocks"][0]["pending_reasons"].append("late-change")
    report = run(state)
    assert len(state.calls) == 1 and report["status"] == "failed"
    assert report["checked"] == report["confirmed"] == 0
    assert state.questions == original


@experimental_pdf_only
def test_fragment_readonly_visual_different_never_requests_rewrite(state):
    original = deepcopy(state.questions)
    state.decisions = "different"
    report = run(state, allow_repair=True)
    assert len(state.calls) == report["calls"] == report["checked"] == 1
    assert report["repair_enabled"] is False and report["confirmed"] == 0
    assert state.questions[0]["source_review"]["required"] is True
    assert all(new["content"] == old["content"] and new["answer_markdown"] == old["answer_markdown"]
               for new, old in zip(state.questions, original))


@experimental_pdf_only
def test_fragment_explicit_incomplete_answer_is_not_cleared_as_page_warning(state):
    state.questions[0]["source_review"]["reasons"].append("fragment_continuation_unclosed")
    refresh_pdf_review_items(state.questions, state.layout, state.diagnostics)
    original = deepcopy(state.questions)
    report = run(state)
    assert not state.calls and report["calls"] == 0 and state.questions == original
    assert report["skipped_reasons"][0]["code"] == "other_review_reason"
