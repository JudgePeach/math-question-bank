"""Optional source verification dispatch; no live model requests or user data."""

from io import BytesIO
import uuid

import pymupdf as fitz
import pytest


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("No live AI in flow tests"))


def pdf_bytes():
    with fitz.open() as document:
        document.new_page(width=595, height=842)
        return document.tobytes()


@pytest.mark.parametrize("strategy,field,expected", [
    ("layout_aware", None, False), ("layout_aware", "true", True),
    ("layout_aware", "false", False), ("native_preferred", "true", False),
    ("force_ocr", "true", False),
])
def test_pdf_upload_requires_explicit_smart_mode_verification_flag(client, monkeypatch, strategy, field, expected):
    import main
    submitted = []
    monkeypatch.setattr(main.DOCUMENT_TASKS, "submit", lambda *a, **k: submitted.append(a))
    fields = {"pdf_strategy": strategy}
    if field is not None:
        fields["pdf_verify_suspicions"] = field
    response = client.post("/api/upload/pdf-task", data=fields,
                           files={"file": ("test.pdf", BytesIO(pdf_bytes()), "application/pdf")},
                           headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    try:
        assert len(submitted) == 1
        assert submitted[0][-2:] == (strategy, expected)
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


@pytest.mark.parametrize("enabled,pending", [(False, 1), (True, 0), (True, 1)])
def test_source_verifier_only_runs_when_enabled_and_pending_after_final_text(monkeypatch, enabled, pending):
    import main
    from mathbank import pdf_source_verify
    source = "1. 已知函数的表达式，求对应结果。"
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pdf_type": "text_based", "pages": [{"page_index": 0, "markdown": source, "needs_ocr": False}]})
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *a, **k: [{"content": source, "answer_markdown": ""}])
    def reconcile(questions, locks, source):
        if pending:
            questions[0]["source_review"] = {"required": True,
                "reasons": ["题干文字或公式位置与原文未能完整对应，请对照原文核对。"], "source_excerpt": source}
        return {"source_review_count": pending, "source_matches": [], "unmatched_source": []}
    monkeypatch.setattr(main, "reconcile_visible_math", reconcile)
    real_postprocess = main.post_process_pdf_parsed_questions
    def finalize(*args, **kwargs):
        result = real_postprocess(*args, **kwargs)
        result[0]["finalized_for_display"] = True
        return result
    monkeypatch.setattr(main, "post_process_pdf_parsed_questions", finalize)
    calls = []
    def verify(questions, diagnostics, urls, numbers, **kwargs):
        kwargs["check_cancelled"]()
        assert questions[0]["finalized_for_display"] is True
        assert numbers == [1] and len(urls) == 1
        assert kwargs["source_pages"][0]["page_number"] == 1
        assert kwargs["source_pages"][0]["origin"] == "native"
        assert kwargs["source_pages"][0]["markdown"].endswith(source)
        calls.append(True)
        # A failed/uncertain review must not make the import task fail or approve a question.
        return {"status": "failed", "calls": 1, "checked": 0, "confirmed": 0,
                "pending": 1, "skipped": 0, "usage": {}, "note": "核验未完成，保留原核对提示。"}
    monkeypatch.setattr(pdf_source_verify, "verify_pdf_source_suspicions", verify)
    task_id = "source-verification-flow-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(), "test.pdf", pdf_strategy="layout_aware",
                                 pdf_verify_suspicions=enabled)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        report = task["diagnostics"]["pdf_source_verification"]
        if enabled and pending:
            assert calls == [True] and report["status"] == "failed"
        else:
            assert calls == [] and report["calls"] == 0
            assert report["status"] == ("no_candidates" if enabled else "disabled")
        if pending:
            assert task["data"][0]["source_review"]["required"] is True
    finally:
        main.DOCUMENT_TASKS.remove(task_id)
