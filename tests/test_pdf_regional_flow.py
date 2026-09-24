"""Regional PDF routing with mocked paid boundaries and the real task lifecycle."""

import uuid

import pymupdf as fitz
import pytest


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Regional flow tests must not contact a model")
    monkeypatch.setattr("requests.sessions.Session.request", reject)


def pdf_bytes(count=3):
    with fitz.open() as document:
        for _ in range(count):
            document.new_page(width=595, height=842)
        return document.tobytes()


def joint(markdown):
    return {"markdown": markdown, "layout": {"figures": [], "ignored_candidates": [],
            "page_complete": True, "notes": []},
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 150}}


@pytest.mark.parametrize("strategy", ["layout_aware", "native_preferred"])
def test_native_regional_and_full_pages_share_one_split_without_duplicate_vision(monkeypatch, strategy):
    import main
    from mathbank import pdf_native_regions, pdf_region_vision, pdf_page_vision

    native = "1. 原生正文保持不变，计算结果并说明理由。"
    regional = "2. 局部页保留原文。识别公式 $x=2$。"
    full = "3. 复杂页面完成识别。"
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pdf_type": "mixed", "pages": [
            {"page_index": 0, "markdown": native, "needs_ocr": False},
            {"page_index": 1, "markdown": "BAD_NATIVE", "needs_ocr": True},
            {"page_index": 2, "markdown": "BAD_TABLE", "needs_ocr": True},
        ],
    })
    planned, regional_calls, full_calls = [], [], []
    def plan(page, info):
        planned.append(info["page_index"])
        if info["page_index"] != 1:
            return None
        return {"pieces": [{"text": "局部页保留原文。"}, {"region_id": "r1"}],
                "regions": [{"id": "r1", "bbox": [0, 0, 1000, 200], "page_info": info}],
                "native_characters": 9, "area_ratio": .2, "kind": "mixed"}
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", plan)
    def region_request(image, info, plan, **kwargs):
        kwargs["check_cancelled"]()
        regional_calls.append((info["page_index"], kwargs["include_figures"]))
        return joint(regional)
    monkeypatch.setattr(pdf_region_vision, "request_pdf_regions", region_request)
    def page_request(image, info, **kwargs):
        full_calls.append(info["page_index"])
        return joint(full)
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", page_request)
    def legacy(image):
        full_calls.append("legacy")
        return full
    monkeypatch.setattr(main, "ocr_pdf_page_image", legacy)
    split_inputs = []
    def split(source, *args, **kwargs):
        split_inputs.append(source)
        return [{"content": text, "answer_markdown": ""} for text in (native, regional, full)]
    monkeypatch.setattr(main, "parse_paper_text_internal", split)
    task_id = "regional-flow-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(), "regional.pdf", pdf_strategy=strategy)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("log")
        assert planned == [1, 2]
        assert regional_calls == [(1, strategy == "layout_aware")]
        assert full_calls == ([2] if strategy == "layout_aware" else ["legacy"])
        assert len(split_inputs) == 1 and native in split_inputs[0]
        assert "局部页保留原文" in split_inputs[0]
        assert "BAD_NATIVE" not in split_inputs[0] and "BAD_TABLE" not in split_inputs[0]
        diagnostic = task["diagnostics"]
        page_evidence = task["pdf_source_pages"]
        assert [item["page_number"] for item in page_evidence] == [1, 2, 3]
        assert [item["origin"] for item in page_evidence] == [
            "native", "regional_vision", "joint_vision" if strategy == "layout_aware" else "ocr"]
        assert all(item["markdown"].endswith(text) for item, text in zip(page_evidence, (native, regional, full)))
        assert all(isinstance(item["figures"], list) for item in page_evidence)
        assert diagnostic["pdf_extraction"]["native_pages"] == 1
        assert diagnostic["pdf_extraction"]["regional_pages"] == 1
        assert diagnostic["pdf_extraction"]["full_vision_pages"] == 1
        assert diagnostic["pdf_extraction"]["native_characters_reused"] == 9
        assert [p["mode"] for p in diagnostic["pdf_extraction"]["pages"]] == ["native", "regional", "full_vision"]
        assert diagnostic["pdf_regional_usage"]["total_tokens"] == 150
        if strategy == "layout_aware":
            assert diagnostic["pdf_layout"]["visual_calls"] == 0
            assert diagnostic["pdf_layout"]["joint_visual_calls"] == 2
            assert diagnostic["pdf_layout"]["regional_visual_calls"] == 1
            assert diagnostic["pdf_joint_usage"]["total_tokens"] == 300
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_failed_regional_call_never_retries_full_page_or_splits(monkeypatch):
    import main
    from mathbank import pdf_native_regions, pdf_region_vision, pdf_page_vision

    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pdf_type": "mixed", "pages": [{"page_index": 0, "markdown": "BAD", "needs_ocr": True}]})
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", lambda *a: {
        "native_characters": 50, "regions": [{}], "area_ratio": .3})
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise ValueError("局部识别缺少区域，未自动重试")
    def forbidden(*args, **kwargs):
        raise AssertionError("No second paid request or split is allowed")
    monkeypatch.setattr(pdf_region_vision, "request_pdf_regions", fail)
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", forbidden)
    monkeypatch.setattr(main, "ocr_pdf_page_image", forbidden)
    monkeypatch.setattr(main, "parse_paper_text_internal", forbidden)
    task_id = "regional-failure-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(1), "failure.pdf", pdf_strategy="layout_aware")
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "error" and "局部识别缺少区域" in task["error"]
        assert calls == [True]
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_force_ocr_never_uses_regional_planning(monkeypatch):
    import main
    from mathbank import pdf_native_regions, pdf_region_vision

    def forbidden(*args, **kwargs):
        raise AssertionError("Force OCR must not use native or regional routing")
    monkeypatch.setattr(main, "inspect_and_extract_pdf", forbidden)
    monkeypatch.setattr(pdf_native_regions, "plan_pdf_regions", forbidden)
    monkeypatch.setattr(pdf_region_vision, "request_pdf_regions", forbidden)
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda *a: "1. 直接识别整页。")
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *a, **k: [{"content": "直接识别整页。"}])
    task_id = "regional-force-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(1), "force.pdf", pdf_strategy="force_ocr")
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed"
        assert task["diagnostics"]["pdf_extraction"]["full_vision_pages"] == 1
        assert task["diagnostics"]["pdf_extraction"]["regional_pages"] == 0
    finally:
        main.DOCUMENT_TASKS.remove(task_id)
