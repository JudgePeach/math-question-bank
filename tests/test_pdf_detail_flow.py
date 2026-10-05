"""Auxiliary views preserve the one-page OCR and task asset contracts."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pymupdf as fitz
import pytest


@pytest.mark.parametrize("mode", ["joint", "native", "regional", "force"])
def test_details_are_registered_only_for_full_joint_pages(monkeypatch, mode):
    import main
    from mathbank import pdf_page_vision, pdf_region_vision, pdf_vision_details, pdf_native_regions
    with fitz.open() as document:
        document.new_page(width=595, height=842)
        raw_pdf = document.tobytes()
    task_id = "details-flow-" + uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    native = mode == "native"
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *_a, **_k: {
        "pdf_type": "scanned", "pages": [{"page_index": 0, "needs_ocr": not native,
                                           "markdown": "1. 测试 $B$。" if native else ""}],
    })
    plan = {"native_characters": 25, "area_ratio": 0.25, "regions": [{}]} if mode == "regional" else None
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", lambda *_a, **_k: plan)
    rendered, request_inputs = [], []
    render = pdf_vision_details.render_pdf_detail_views
    def render_spy(page, **kwargs):
        assert mode == "joint"
        result = render(page, **kwargs)
        rendered.append(deepcopy(result))
        return result
    monkeypatch.setattr(pdf_vision_details, "render_pdf_detail_views", render_spy)
    def joint(_image, _info, **kwargs):
        assert mode == "joint"
        request_inputs.append(deepcopy(kwargs["detail_views"]))
        _, metadata = pdf_vision_details.prepare_detail_messages(kwargs["detail_views"], page_index=0)
        return {"markdown": "1. 测试 $B$。", "layout": {"figures": [], "page_complete": True,
                "ignored_candidates": [], "notes": []}, "detail_input": {"status": "included", **metadata},
                "usage": {}, "visual_calls": 1}
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", joint)
    monkeypatch.setattr(pdf_region_vision, "request_pdf_regions", lambda *_a, **_k: {
        "markdown": "1. 测试 $B$。", "layout": {"figures": [], "page_complete": True,
            "ignored_candidates": [], "notes": []}, "usage": {}, "visual_calls": 1})
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda *_a, **_k: "1. 测试 $B$。")
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: [{
        "content": "测试 $B$。", "answer_markdown": "", "image_paths": []}])
    try:
        main.run_pdf_parsing_task(task_id, raw_pdf, "fixture.pdf",
            pdf_strategy="force_ocr" if mode == "force" else "layout_aware", pdf_verify_suspicions=False)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        assert len(task["page_images"]) == 1
        assert task["data"][0]["image_paths"] == []
        if mode == "joint":
            assert len(rendered) == len(request_inputs) == 1
            assert len(request_inputs[0]) == 2
            assert all(view["url"] in task["temp_assets"] for view in rendered[0]["views"])
            assert task["diagnostics"]["pdf_detail_views"]["successful_input_images"] == 2
            assert task["diagnostics"]["pdf_detail_views"]["separate_detail_requests"] == 0
            assert task["diagnostics"]["pdf_detail_views"]["independent_verification"] is False
        else:
            assert rendered == request_inputs == []
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


@pytest.mark.parametrize("verify", [False, True])
def test_first_ocr_symbol_risk_respects_existing_verification_switch(monkeypatch, verify):
    import main
    from mathbank import pdf_page_vision, pdf_native_regions, pdf_source_verify
    with fitz.open() as document:
        document.new_page(width=595, height=842)
        raw_pdf = document.tobytes()
    task_id = "symbol-flow-" + uuid4().hex
    source = "1. 圆弧由若干段组成，求$A_3B_3$的长度。"
    output = source[3:]
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *_a, **_k: {
        "pdf_type": "scanned", "pages": [{"page_index": 0, "needs_ocr": True, "markdown": ""}],
    })
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", lambda *_a, **_k: None)
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", lambda *_a, **_k: {
        "markdown": source, "layout": {"figures": [], "page_complete": True,
            "ignored_candidates": [], "notes": []}, "usage": {}, "visual_calls": 1})
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: [{
        "content": output, "answer_markdown": "", "image_paths": []}])
    calls = []
    def check(questions, diagnostics, *args, **kwargs):
        calls.append(1)
        assert questions[0]["content"] == output
        assert diagnostics["pdf_symbol_risks"]["risk_count"] == 1
        assert pdf_source_verify._candidate(0, questions[0], diagnostics) is not None
        return {"status": "pending", "calls": 1, "pending": 1, "items": []}
    monkeypatch.setattr(pdf_source_verify, "verify_pdf_source_suspicions", check)
    try:
        main.run_pdf_parsing_task(task_id, raw_pdf, "fixture.pdf",
            pdf_strategy="layout_aware", pdf_verify_suspicions=verify)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        assert calls == ([1] if verify else [])
        assert task["data"][0]["content"] == output
        assert task["data"][0]["source_review"]["blocking"] is False
        assert task["diagnostics"]["pdf_review_items"][0]["source_number"] == 1
        assert task["diagnostics"]["pdf_review_items"][0]["source_pages"] == [1]
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_review_refresh_preserves_cloned_image_and_never_invents_source_number():
    from mathbank.pdf_figures import refresh_pdf_review_items
    questions = [{"content": "圆弧$AB$的长度。", "source_review": {"required": True,
                  "reasons": ["圆弧语境中的点名可能缺顶线，请按原图核对。"]},
                  "pdf_source_figures": [{"page_index": 1, "image_path": "/static/test_uploads/tmp/cloned.png"}]}]
    original = deepcopy(questions)
    diagnostics = {"source_matches": []}
    layout = {"pages": [{"page_index": 1}], "page_texts": ["原页索引"]}
    refresh_pdf_review_items(questions, layout, diagnostics)
    assert questions == original
    assert diagnostics["pdf_review_items"][0]["source_number"] is None
    assert diagnostics["pdf_review_items"][0]["source_pages"] == [2]
