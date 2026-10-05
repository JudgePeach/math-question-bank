"""PDF retry routing in an isolated source copy, with no provider networking."""

import json
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import uuid

import pymupdf as fitz
import pytest
import requests


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("No live AI calls"))


def document(count=2):
    with fitz.open() as pdf:
        for _ in range(count):
            pdf.new_page(width=595, height=842)
        return pdf.tobytes()


def test_failed_page_keeps_parallel_success_source_images_and_attempts(monkeypatch):
    import main
    from mathbank import pdf_page_vision, pdf_native_regions
    from mathbank.ai_providers import resolve_ocr_provider

    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *_a, **_kw: {
        "pages": [{"page_index": 0, "markdown": "", "needs_ocr": True},
                  {"page_index": 1, "markdown": "", "needs_ocr": True}]})
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", lambda *_a: None)
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused"})
    monkeypatch.setattr(pdf_page_vision, "resolve_ocr_provider", lambda _engine: provider)
    monkeypatch.setattr(pdf_page_vision.prompts, "build_pdf_page_vision_prompt", lambda info: str(info["page_index"]))
    calls = {0: [], 1: []}
    def post(_provider, payload, **kwargs):
        index = int(payload["messages"][0]["content"][0]["text"])
        calls[index].append(kwargs)
        if index == 0:
            raise requests.ReadTimeout("private key and url")
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "markdown": "2. 完整成功页 $x=2$。", "figures": [], "ignored_candidates": [],
                "page_complete": True, "warnings": []})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr(pdf_page_vision, "post_chat_completion", post)
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_kw: pytest.fail("A missing page cannot be split"))
    task_id = "pdf-retry-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, document(), "retry.pdf", pdf_strategy="layout_aware")
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "error"
        assert "第 1 页" in task["error"] and "共尝试 2 次" in task["error"]
        assert len(calls[0]) == 2 and len(calls[1]) == 1
        attempts = task["diagnostics"]["pdf_vision"]
        assert attempts["calls"] == 3 and attempts["retries"] == 1
        assert attempts["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        assert attempts["usage_complete"] is False
        assert "private" not in str(attempts)
        assert task["pdf_source_pages"][0] == {"page_number": 1, "markdown": "", "status": "failed"}
        assert "完整成功页" in task["pdf_source_pages"][1]["markdown"]
        assert task["pdf_source_pages"][1]["status"] == "completed"
        assert len(task["page_images"]) == 2
        assert all((main.PROJECT_ROOT / path.lstrip("/")).is_file() for path in task["page_images"])
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_ordinary_pdf_ocr_uses_same_provider_and_one_retry(monkeypatch, tmp_path):
    import main
    from mathbank.ai_providers import resolve_ocr_provider
    from PIL import Image

    image = tmp_path / "page.png"
    Image.new("RGB", (16, 16), "white").save(image)
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused"})
    monkeypatch.setattr(main, "resolve_ocr_provider", lambda _engine: provider)
    monkeypatch.setattr(main, "resolve_ocr_fallbacks", lambda *_a: pytest.fail("No extra provider fallback"))
    calls = []
    def post(config, payload, **kwargs):
        calls.append((config, kwargs))
        if len(calls) == 1:
            raise requests.ReadTimeout("private")
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [
            {"finish_reason": "stop", "message": {"content": "1. 正文"}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    assert main.ocr_pdf_page_image(str(image)) == "1. 正文"
    assert len(calls) == 2 and all(config is provider for config, _kw in calls)
    assert all(kw["timeout"] == 600 and kw["retry_connection"] is False for _config, kw in calls)


def test_removed_task_stops_before_any_paid_page_call(monkeypatch):
    import main
    from mathbank import pdf_page_vision, pdf_native_regions

    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *_a, **_kw: {
        "pages": [{"page_index": 0, "markdown": "", "needs_ocr": True}]})
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", lambda *_a: None)
    task_id = "pdf-remove-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    original_update = main.DOCUMENT_TASKS.update
    def update(identifier, **changes):
        result = original_update(identifier, **changes)
        if changes.get("status") == "ocr_extraction":
            main.DOCUMENT_TASKS.remove(identifier)
        return result
    monkeypatch.setattr(main.DOCUMENT_TASKS, "update", update)
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", lambda *_a, **_kw: pytest.fail("Removed task must not call AI"))
    main.run_pdf_parsing_task(task_id, document(1), "removed.pdf", pdf_strategy="layout_aware")
    assert main.DOCUMENT_TASKS.snapshot(task_id) is None


@pytest.mark.parametrize("outcome", ["recovered", "failed", "cancelled", "removed", "postprocess_failure"])
def test_text_split_retry_preserves_prepared_input_without_repeating_page_vision(monkeypatch, outcome):
    import main
    from mathbank import pdf_page_vision, pdf_native_regions
    from mathbank.ai_providers import resolve_ocr_provider, resolve_text_provider

    source = "1. 计算 $x+1$。"
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *_a, **_kw: {
        "pages": [{"page_index": 0, "markdown": "", "needs_ocr": True}]})
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", lambda *_a: None)
    image_provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused"})
    text_provider = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY": "unused"})
    monkeypatch.setattr(pdf_page_vision, "resolve_ocr_provider", lambda _engine: image_provider)
    monkeypatch.setattr(main, "resolve_text_provider", lambda _model: text_provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {})
    page_calls, split_calls = [], []
    def page_post(_provider, payload, **kwargs):
        page_calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "markdown": source, "figures": [], "ignored_candidates": [],
                "page_complete": True, "warnings": []})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr(pdf_page_vision, "post_chat_completion", page_post)
    task_id = "pdf-split-retry-" + uuid.uuid4().hex
    def text_post(_provider, payload, **kwargs):
        split_calls.append((deepcopy(payload), kwargs))
        if outcome == "failed" or (len(split_calls) == 1 and outcome != "postprocess_failure"):
            if outcome == "cancelled": main.DOCUMENT_TASKS.cancel(task_id)
            elif outcome == "removed": main.DOCUMENT_TASKS.remove(task_id)
            raise requests.ReadTimeout("private text provider detail")
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"questions": [
                {"content": source, "answer_markdown": "", "question_type": "detailed_answer"}]})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
    monkeypatch.setattr(main, "post_chat_completion", text_post)
    if outcome == "postprocess_failure":
        monkeypatch.setattr(main, "post_process_pdf_parsed_questions", lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("local processing error")))
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, document(1), "split.pdf", pdf_strategy="layout_aware",
                                  pdf_verify_suspicions=False)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert len(page_calls) == 1
        assert len(split_calls) == (2 if outcome in ("recovered", "failed") else 1)
        assert all(kw == {"timeout": 600, "check_status": False, "retry_connection": False,
                          "allow_redirects": False} for _payload, kw in split_calls)
        if len(split_calls) == 2:
            assert split_calls[0][0] == split_calls[1][0]
        if outcome == "removed":
            assert task is None
        elif outcome == "cancelled":
            assert task["status"] == "cancelled"
        else:
            assert task["status"] == ("completed" if outcome == "recovered" else "error"), task.get("error")
            assert task["diagnostics"]["pdf_vision"]["calls"] == 1
            assert task["diagnostics"]["pdf_vision"]["timeout_seconds"] == 600
            report = task["diagnostics"]["pdf_splitting"]
            assert report["calls"] == len(split_calls) and report["retries"] == len(split_calls) - 1
            assert report["timeout_seconds"] == 600
            assert "private" not in str(report)
            assert task["pdf_source_pages"][0]["origin"] == "joint_vision"
            assert task["pdf_source_pages"][0]["figures"] == []
            assert source in task["pdf_source_pages"][0]["markdown"]
            cache = task["pdf_source_cache"]
            assert cache["status"] == "complete" and source in cache["source_markdown"]
            assert cache["model_source"] == split_calls[0][0]["messages"][1]["content"]
            assert '<mathbank-math id="' in cache["model_source"]
            assert len(cache["source_sha256"]) == len(cache["model_source_sha256"]) == 64
            assert all((main.PROJECT_ROOT / path.lstrip("/")).is_file() for path in task["page_images"])
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_regular_word_internal_split_has_600_seconds_without_added_retry(monkeypatch):
    import main
    from mathbank.ai_providers import resolve_text_provider
    provider = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY": "unused"})
    monkeypatch.setattr(main, "resolve_text_provider", lambda _model: provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {})
    calls = []
    def post(_provider, _payload, **kwargs):
        calls.append(kwargs)
        raise requests.ReadTimeout("private")
    monkeypatch.setattr(main, "post_chat_completion", post)
    with pytest.raises(requests.ReadTimeout):
        main.parse_paper_text_internal("1. 题干", False, preserve_source_answers=True)
    assert len(calls) == 1 and calls[0]["timeout"] == 600
    assert "retry_connection" not in calls[0] and "allow_redirects" not in calls[0]
