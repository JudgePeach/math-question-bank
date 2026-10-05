"""Real PDF crops and stubbed model responses: no network or production data."""

import io
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from PIL import Image

from mathbank import pdf_figures
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)
    def fail(*args, **kwargs):
        raise AssertionError("Tests must not contact a model")
    monkeypatch.setattr("requests.sessions.Session.request", fail)


def make_pdf():
    """A real graphic between two paragraphs, plus a plain-text second page."""
    stream = io.BytesIO()
    image = Image.new("RGB", (100, 100), "white")
    for x in range(10, 90):
        image.putpixel((x, x), (0, 0, 0))
    image.save(stream, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "1. Refer to the diagram.")
        page.insert_image(fitz.Rect(50, 70, 150, 170), stream=stream.getvalue())
        page.insert_text((30, 210), "Find the answer.")
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "2. This page contains only ordinary text.")
        return document.tobytes()


def fake_layout(info, **changes):
    figures = [{"bbox": [125, 140, 375, 340],
                "candidate_ids": [item["id"] for item in info.get("candidates", [])],
                "anchor_before": "Refer to the diagram.", "anchor_after": "Find the answer.",
                "review_required": False}]
    return {"page_complete": True, "figures": figures, "ignored_candidates": [], **changes}


def run_enrichment(tmp_path, monkeypatch, *, layout=None, request=None):
    data = make_pdf()
    images = []
    with fitz.open(stream=data, filetype="pdf") as document:
        for index, page in enumerate(document):
            target = tmp_path / f"page{index}.png"
            page.get_pixmap().save(target)
            images.append(str(target))
    registered = []
    calls = []

    def model(image_path, source, info, **kwargs):
        calls.append(info["page_index"])
        kwargs["diagnostics"]["visual_calls"] += 1
        if request:
            return request(image_path, source, info, **kwargs)
        return fake_layout(info) if layout is None else layout(info)

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", model)
    result = pdf_figures.enrich_pdf_with_figures(
        data, [0, 1], images, ["/static/uploads/tmp/page0.png", "/static/uploads/tmp/page1.png"],
        ["1. Refer to the diagram.\nFind the answer.", "2. Plain text."], set(),
        output_dir=tmp_path, url_prefix="/static/uploads/tmp", task_id="layout-test",
        check_cancelled=lambda: None, register_asset=registered.append,
        report_progress=lambda *_args: None,
    )
    return result, registered, calls


def test_native_image_anchored_and_plain_page_has_no_paid_call(tmp_path, monkeypatch):
    result, registered, calls = run_enrichment(tmp_path, monkeypatch)
    assert calls == [0]
    assert len(registered) == 1
    assert registered[0] in result["page_texts"][0]
    assert result["diagnostics"]["figures_attached"] == 1
    assert result["diagnostics"]["skipped_pages"] == 1
    assert result["issues"] == []
    with Image.open(tmp_path / Path(registered[0]).name) as crop:
        assert crop.format == "PNG"
        assert crop.width == crop.height


def test_unmatched_image_is_kept_with_original_page_evidence(tmp_path, monkeypatch):
    def layout(info):
        result = fake_layout(info)
        result["figures"][0]["anchor_before"] = "not present in source"
        return result
    result, registered, _ = run_enrichment(tmp_path, monkeypatch, layout=layout)
    assert result["diagnostics"]["unmatched_figures"] == 1
    assert registered[0] in result["issues"][0]["source_excerpt"]
    assert "page0.png" in result["issues"][0]["source_excerpt"]
    assert registered[0] not in result["page_texts"][0]
    questions = [{"content": "question", "answer_markdown": ""}]
    report = {}
    pdf_figures.apply_pdf_layout_reviews(questions, result, report)
    assert not questions[0].get("source_review", {}).get("required")
    assert report["source_review_count"] == 0
    assert len(report["unmatched_source"]) == 1


def test_only_filled_adjacent_ocr_placeholders_are_removed():
    path = "/static/uploads/tmp/figure.png"
    image = "![插图](" + path + ")"
    source = r"\begin{choices}\item [插图待补: 图1]" + "\n" + image + r"\item [插图待补: 图2]\end{choices}"
    figure = {"image_path": path, "anchor_before": "[插图待补: 图1]", "anchor_after": r"\item"}
    cleaned = pdf_figures.clear_resolved_figure_placeholders(source, [figure])
    assert "图1" not in cleaned and "[插图待补: 图2]" in cleaned
    assert image in cleaned and r"\item" in cleaned
    after = {"image_path": path, "anchor_before": "", "anchor_after": "[插图待补: 图1]"}
    assert pdf_figures.clear_resolved_figure_placeholders(image + " [插图待补: 图1]", [after]) == image + " "
    assert pdf_figures.clear_resolved_figure_placeholders(source, []) == source
    assert pdf_figures.clear_resolved_figure_placeholders("[插图待补: 图1]\n" + image, [{**figure, "image_path": "/different.png"}]).startswith("[插图待补")
    between_slots = "[插图待补: 图1]\n" + image + "\n[插图待补: 图2]"
    ambiguous = {**figure, "anchor_after": "[插图待补: 图2]"}
    assert pdf_figures.clear_resolved_figure_placeholders(between_slots, [ambiguous]) == between_slots


def test_missing_candidates_are_not_certified_by_model_confidence(tmp_path, monkeypatch):
    result, registered, _ = run_enrichment(tmp_path, monkeypatch, layout=lambda info: {
        "figures": [], "page_complete": True, "confidence": 1.0,
    })
    assert not registered
    assert result["diagnostics"]["review_pages"] == 1
    assert "未被解释" in result["issues"][0]["reason"]


def test_half_crop_ignored_large_image_and_duplicate_region_require_review():
    info = {"page_index": 0, "width": 400, "height": 500,
            "candidates": [{"id": "image1", "bbox": [100, 100, 500, 500], "type": "raster"}]}
    figure = {"bbox": [100, 100, 300, 500], "candidate_ids": ["image1"],
              "anchor_before": "anchor", "anchor_after": "", "review_required": False}
    _, warnings = pdf_figures._validate_layout({"page_complete": True, "figures": [figure]}, info)
    assert any("未完整覆盖" in warning for warning in warnings)
    notes = []
    _, warnings = pdf_figures._validate_layout({"page_complete": True, "figures": [],
        "ignored_candidates": [{"id": "image1", "reason": "formula"}]}, info, notes=notes)
    assert any("位图" in message for message in warnings)
    vector_info = {**info, "candidates": [{**info["candidates"][0], "type": "vector"}]}
    _, warnings = pdf_figures._validate_layout({"page_complete": True, "figures": [],
        "ignored_candidates": [{"id": "image1", "reason": "decoration"}]}, vector_info, notes=notes)
    assert not warnings and notes
    figure["bbox"] = [100, 100, 500, 500]
    figures, warnings = pdf_figures._validate_layout({"page_complete": True, "figures": [figure, dict(figure)]}, info)
    assert any("重叠" in warning for warning in warnings)
    assert all(any("重叠" in reason for reason in item["review_reasons"]) for item in figures)


def test_q27_group_and_repeated_right_subfigure_are_retained_with_containment_review():
    # Geometry from the audited Q27 group/right-child case, with no uploaded
    # files or provider response needed. Its IoU is about 0.5, not 0.8.
    group = [87.9358, 270.5839, 569.1897, 430.9316]
    right = [327.4803, 270.2184, 569.5197, 430.7816]
    info = {"page_index": 6, "width": 595.32, "height": 841.92,
            "candidates": [{"id": "p7_raster_001", "type": "raster", "bbox": list(group)}]}
    result = {"page_complete": True, "figures": [
        {"bbox": list(group), "native_box": True, "candidate_ids": ["p7_raster_001"],
         "anchor_before": "图1", "anchor_after": "", "review_required": False},
        {"bbox": list(right), "candidate_ids": [], "anchor_before": "图2", "anchor_after": "",
         "review_required": False},
    ]}
    figures, warnings = pdf_figures._validate_layout(result, info)
    assert len(figures) == 2
    assert [figure["id"] for figure in figures] == ["p7-f1", "p7-f2"]
    assert any("整组配图及其局部重复插入" in message for message in warnings)
    assert all(figure["review_required"] for figure in figures)
    assert all(any("完整包含" in message for message in figure["review_reasons"]) for figure in figures)
    assert result["figures"][0]["bbox"] == group and result["figures"][1]["bbox"] == right


@pytest.mark.parametrize("model_box", [[620, 740, 860, 860], None])
def test_precomputed_scan_strip_native_claim_uses_only_retained_model_frame(model_box):
    boxes = [[0.5308, 0, 309.564, 999.7505], [309.564, 0, 654.6812, 999.7505],
             [654.6812, 0, 999.7984, 999.7505]]
    info = {"page_index": 0, "width": 595.276, "height": 841.89, "full_page_image": True,
            "candidates": [{"id": f"p1_raster_{index + 1:03d}", "type": "raster", "bbox": box}
                           for index, box in enumerate(boxes)]}
    result = {"page_complete": True, "figures": [{"bbox": list(boxes[2]), "model_bbox": model_box,
              "native_box": True, "candidate_ids": ["p1_raster_003"], "review_required": False}],
              "ignored_candidates": [{"id": "p1_raster_001", "reason": "page_background"},
                                     {"id": "p1_raster_002", "reason": "page_background"}]}
    figures, warnings = pdf_figures._validate_layout(result, info)
    assert any("扫描背景切片" in message for message in warnings)
    assert result["figures"][0]["bbox"] == boxes[2]
    if model_box is None:
        assert figures == []
        # Old crop padding is still server-generated tile geometry, not a
        # model rectangle. It must not invent a missing saved estimate.
        result["figures"][0]["bbox"] = [652.1, 0, 1000, 1000]
        assert pdf_figures._validate_layout(result, info)[0] == []
    else:
        assert len(figures) == 1 and figures[0]["model_bbox"] == model_box
        assert figures[0]["bbox"][1] > 700 and figures[0]["bbox"][3] < 900
        assert figures[0]["native_box"] is False and figures[0]["review_required"] is True


@pytest.mark.parametrize("first,second,contained", [
    ([100, 100, 500, 500], [300, 200, 500, 400], True),
    ([300, 200, 500, 400], [100, 100, 500, 500], True),
    ([100, 100, 500, 500], [480, 200, 680, 400], False),
    ([100, 100, 500, 500], [510, 200, 710, 400], False),
    ([100, 100, 500, 500], [120, 100, 520, 500], False),
])
def test_containment_requires_nearly_all_of_meaningfully_smaller_crop(first, second, contained):
    message = pdf_figures._figure_overlap_warning(first, second)
    assert ("完整包含" in message) is contained


def test_local_guard_created_group_child_overlap_keeps_both_assets_and_source(tmp_path, monkeypatch):
    from mathbank import pdf_raster_guard
    original_inspection = pdf_figures.inspect_pdf_page
    def inspection(page, index):
        return {**original_inspection(page, index), "full_page_image": True}
    monkeypatch.setattr(pdf_figures, "inspect_pdf_page", inspection)
    def layout(info):
        return {"page_complete": True, "figures": [
            {"bbox": [100, 100, 500, 500], "candidate_ids": [], "anchor_before": "Refer to the diagram.",
             "anchor_after": "Find the answer.", "review_required": False},
            {"bbox": [650, 150, 850, 350], "candidate_ids": [], "anchor_before": "Refer to the diagram.",
             "anchor_after": "Find the answer.", "review_required": False},
        ]}
    def guard(_path, box):
        return {"bbox": [100, 100, 900, 500] if box[0] == 100 else box,
                "changed": box[0] == 100, "warnings": [], "notes": []}
    monkeypatch.setattr(pdf_raster_guard, "guard_raster_figure_bbox", guard)
    result, assets, _ = run_enrichment(tmp_path, monkeypatch, layout=layout)
    page = result["pages"][0]
    assert page["status"] == "checked" and len(page["figures"]) == len(assets) == 2
    assert all(figure["review_required"] for figure in page["figures"])
    assert any("图框校验后" in message and "完整包含" in message for message in page["warnings"])
    assert all((tmp_path / Path(path).name).exists() for path in assets)
    assert "Refer to the diagram." in result["page_texts"][0] and "Find the answer." in result["page_texts"][0]


@pytest.mark.parametrize("completed_left", [90, 100])
def test_component_completion_expands_only_its_proven_side_and_keeps_review(tmp_path, monkeypatch, completed_left):
    from mathbank import pdf_raster_guard
    original_inspection = pdf_figures.inspect_pdf_page
    def inspection(page, index):
        return {**original_inspection(page, index), "full_page_image": True,
                "candidates": [], "text_blocks": []}
    monkeypatch.setattr(pdf_figures, "inspect_pdf_page", inspection)
    def layout(info):
        return {"page_complete": True, "figures": [
            {"bbox": [100, 100, 500, 500], "candidate_ids": [], "anchor_before": "Refer to the diagram.",
             "anchor_after": "Find the answer.", "review_required": True, "review_reason": "Check source label."},
        ]}
    raw = layout({})
    info = {"candidates": [], "page_index": 0, "width": 400, "height": 500, "full_page_image": True}
    padded = pdf_figures._validate_layout(raw, info)[0][0]["bbox"]
    def guard(_path, box):
        assert box == [100, 100, 500, 500]
        return {"bbox": [completed_left, 100, 500, 500], "changed": True,
                "method": "bounded_component_completion", "component_completion": {"side": "left"},
                "warnings": [], "notes": ["Bounded same-component padding."]}
    monkeypatch.setattr(pdf_raster_guard, "guard_raster_figure_bbox", guard)
    result, assets, _ = run_enrichment(tmp_path, monkeypatch, layout=layout)
    figure = result["pages"][0]["figures"][0]
    assert figure["bbox"] == [min(completed_left, padded[0]), *padded[1:]]
    assert figure["model_bbox"] == [100, 100, 500, 500]
    assert figure["review_required"] and "Check source label." in figure["review_reasons"]
    assert len(assets) == 1 and (tmp_path / Path(assets[0]).name).exists()




@pytest.mark.parametrize("bbox", [[-1, 0, 200, 200], [0, 0, 1001, 200],
                                   [300, 200, 100, 50], [False, 0, 200, 200],
                                   [0, 0, float("nan"), 20], [0, 0, 1000, 1000]])
def test_invalid_model_geometry_does_not_crop_or_hide_failure(tmp_path, monkeypatch, bbox):
    def layout(info):
        result = fake_layout(info)
        result["figures"][0]["bbox"] = bbox
        return result
    result, registered, _ = run_enrichment(tmp_path, monkeypatch, layout=layout)
    assert registered == []
    assert result["diagnostics"]["review_pages"] == 1
    assert "page0.png" in result["issues"][0]["source_excerpt"]


def test_timeout_is_single_request_and_keeps_text(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        import requests
        raise requests.ReadTimeout("private request details must not be displayed")
    result, registered, calls = run_enrichment(tmp_path, monkeypatch, request=fail)
    assert calls == [0]
    assert result["page_texts"][0] == "1. Refer to the diagram.\nFind the answer."
    assert "private" not in str(result["diagnostics"])
    assert result["diagnostics"]["review_pages"] == 1


def test_cancel_after_vision_stops_before_crop(tmp_path, monkeypatch):
    def cancel(*args, **kwargs):
        raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        run_enrichment(tmp_path, monkeypatch, request=cancel)
    assert not list(tmp_path.glob("pdf_figure*"))


def test_vision_request_uses_selected_provider_and_tracks_usage(tmp_path, monkeypatch):
    from mathbank.ai_providers import resolve_ocr_provider
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused-test-key",
        "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3-VL-8B-Instruct"})
    monkeypatch.setattr(pdf_figures, "resolve_ocr_provider", lambda _: provider)
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 10), "white").save(path)
    sent = []
    def completion(_provider, payload, **kwargs):
        sent.append(payload)
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "page_complete": True, "figures": [], "ignored_candidates": []})}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 90, "total_tokens": 1290},
        })
    monkeypatch.setattr(pdf_figures, "post_chat_completion", completion)
    report = {"visual_calls": 0, "usage": {}}
    result = pdf_figures.request_pdf_layout(str(path), "1. text", {"page_index": 0},
                                           diagnostics=report, check_cancelled=lambda: None)
    assert result["figures"] == []
    assert len(sent) == report["visual_calls"] == 1
    assert report["usage"]["total_tokens"] == 1290
    assert sent[0]["messages"][0]["content"][1]["type"] == "image_url"
    assert "enable_thinking" not in sent[0]


def test_verified_qwen38_layout_uses_json_mode_and_no_implicit_reasoning(tmp_path, monkeypatch):
    from mathbank.ai_providers import resolve_ocr_provider
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused",
                                                  "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3.8-27B"})
    monkeypatch.setattr(pdf_figures, "resolve_ocr_provider", lambda _: provider)
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 10), "white").save(path)
    sent = []
    def post(_provider, payload, **kwargs):
        sent.append(payload)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"page_complete":true,"figures":[],"ignored_candidates":[]}'}}]})
    monkeypatch.setattr(pdf_figures, "post_chat_completion", post)
    pdf_figures.request_pdf_layout(str(path), "text", {"page_index": 0},
                                  diagnostics={"visual_calls": 0, "usage": {}}, check_cancelled=lambda: None)
    assert sent[0]["enable_thinking"] is False
    assert sent[0]["response_format"] == {"type": "json_object"}


def test_truncated_layout_response_is_rejected_even_if_json_valid(tmp_path, monkeypatch):
    from mathbank.ai_providers import resolve_ocr_provider
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused"})
    monkeypatch.setattr(pdf_figures, "resolve_ocr_provider", lambda _: provider)
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 10)).save(path)
    monkeypatch.setattr(pdf_figures, "post_chat_completion", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {"choices": [{"finish_reason": "length",
        "message": {"content": '{"figures":[]}'}}]}))
    with pytest.raises(ValueError, match="截断"):
        pdf_figures.request_pdf_layout(str(path), "source", {"page_index": 0},
                                      diagnostics={"visual_calls": 0, "usage": {}},
                                      check_cancelled=lambda: None)


def test_layout_timeout_retries_once_with_exact_http_budget(tmp_path, monkeypatch):
    from mathbank.ai_providers import resolve_ocr_provider
    import requests
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused"})
    monkeypatch.setattr(pdf_figures, "resolve_ocr_provider", lambda _: provider)
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 10)).save(path)
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.ReadTimeout("private")
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"figures":[],"page_complete":true,"ignored_candidates":[]}'}}]})
    monkeypatch.setattr(pdf_figures, "post_chat_completion", post)
    report = {"visual_calls": 0, "usage": {}}
    result = pdf_figures.request_pdf_layout(str(path), "source", {"page_index": 0},
                                         diagnostics=report, check_cancelled=lambda: None)
    assert result["figures"] == [] and result["visual_calls"] == report["visual_calls"] == 2
    assert all(kw["timeout"] == 600 and kw["retry_connection"] is False for kw in calls)
    assert report["attempts"][0]["status"] == "failed"


def test_native_layout_bad_geometry_does_not_repeat_paid_call(tmp_path, monkeypatch):
    from mathbank.ai_providers import resolve_ocr_provider
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused"})
    monkeypatch.setattr(pdf_figures, "resolve_ocr_provider", lambda _: provider)
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 10)).save(path)
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"figures":[{"bbox":[-1,0,200,200]}],"page_complete":true,"ignored_candidates":[]}'}}]})
    monkeypatch.setattr(pdf_figures, "post_chat_completion", post)
    report = {"visual_calls": 0, "usage": {}}
    result = pdf_figures.request_pdf_layout(str(path), "usable native source", {"page_index": 0},
                                         diagnostics=report, check_cancelled=lambda: None)
    with pytest.raises(ValueError, match="越界"):
        pdf_figures._validate_layout(result, {"page_index": 0})
    assert len(calls) == result["visual_calls"] == report["visual_calls"] == 1


def test_pdf_task_integrates_source_locks_and_figure_assets(monkeypatch):
    import main
    data = make_pdf()
    task_id = "test-layout-flow-" + uuid.uuid4().hex
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pages": [{"page_index": 0, "markdown": "1. Refer to the diagram.\nFind the answer.", "needs_ocr": False}],
        "pdf_type": "text_based"})
    monkeypatch.setattr(pdf_figures, "request_pdf_layout", lambda image, source, info, **k: fake_layout(info))
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(main, "PDF_OCR_SEMAPHORE", gate)
    original_enrichment = main.enrich_pdf_with_figures
    def enrichment(*args, **kwargs):
        assert kwargs["vision_semaphore"] is gate
        # The caller must not occupy a permit around local processing: an
        # outer acquisition would deadlock the worker with a one-slot gate.
        assert gate.acquire(blocking=False)
        gate.release()
        assert set(kwargs["precomputed_page_infos"]) == {0}
        return original_enrichment(*args, **kwargs)
    monkeypatch.setattr(main, "enrich_pdf_with_figures", enrichment)
    requests = []
    def split(source, solve, **kwargs):
        requests.append((source, solve, kwargs))
        return [{"content": source.strip().removeprefix("1. "), "answer_markdown": ""}]
    monkeypatch.setattr(main, "parse_paper_text_internal", split)
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, data, "layout.pdf", page_range="1", pdf_strategy="layout_aware")
        state = main.DOCUMENT_TASKS.snapshot(task_id)
        assert state["status"] == "completed", state
        assert state["page_numbers"] == [1]
        assert state["pdf_layout"]["schema"] == pdf_figures.LAYOUT_SCHEMA
        assert state["diagnostics"]["pdf_layout"]["figures_attached"] == 1
        assert len(state["data"][0]["image_paths"]) == 1
        path = state["data"][0]["image_paths"][0]
        assert path in state["temp_assets"]
        assert path in state["data"][0]["content"]
        assert requests[0][1] is False
        assert requests[0][2]["preserve_source_answers"] is True
        assert not state["data"][0].get("source_review", {}).get("required")
    finally:
        state = main.DOCUMENT_TASKS.remove(task_id)
        if state:
            main._delete_task_temp_assets(state.get("temp_assets", []))


def test_invalid_pdf_strategy_rejected_before_task_creation(client):
    import main
    response = client.post("/api/upload/pdf-task", files={"file": ("paper.pdf", make_pdf(), "application/pdf")},
                           data={"pdf_strategy": "invented"}, headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 400


def test_parallel_image_list_cannot_add_another_questions_figure(tmp_path):
    import main
    path = "/static/uploads/tmp/pdf_figure_original.png"
    source = "1. First question.\n![figure](" + path + ")\n\n2. Second question."
    questions = [{"content": "First question.\n![figure](" + path + ")", "answer_markdown": ""},
                 {"content": "Second question.", "answer_markdown": "", "referenced_images": [path]}]
    from mathbank.content_locks import reconcile_visible_math
    assert reconcile_visible_math(questions, [], source)["source_review_count"] == 0
    result = {"pages": [{"figures": [{"image_path": path}]}]}
    pdf_figures.isolate_shared_pdf_figures(questions, result, output_dir=tmp_path,
        url_prefix="/static/uploads/tmp", register_asset=lambda _: None, check_cancelled=lambda: None)
    main.post_process_pdf_parsed_questions(questions, "test")
    assert questions[0]["image_paths"] == [path]
    assert questions[1]["image_paths"] == []


@pytest.mark.parametrize("template", ["![figure]({})", "![figure]( {} )", "![figure](<{}>)", '![figure]({} "caption")'])
def test_shared_figure_cards_receive_independent_equal_files(tmp_path, template):
    original = tmp_path / "pdf_figure_original.png"
    Image.new("RGB", (5, 5), "blue").save(original)
    path = "/static/uploads/tmp/" + original.name
    questions = [{"content": template.format(path), "answer_markdown": ""} for _ in range(2)]
    result = {"pages": [{"figures": [{"image_path": path}]}]}
    registered = []
    pdf_figures.isolate_shared_pdf_figures(questions, result, output_dir=tmp_path,
        url_prefix="/static/uploads/tmp", register_asset=registered.append, check_cancelled=lambda: None)
    assert len(registered) == 1
    assert registered[0] in questions[1]["content"]
    assert path in questions[0]["content"]
    assert original.read_bytes() == (tmp_path / Path(registered[0]).name).read_bytes()


def test_cancel_after_crop_cleans_registered_files_and_never_splits(monkeypatch, tmp_path, db_session):
    import main
    from mathbank import asset_lifecycle
    uploads, retained = tmp_path / 'uploads', tmp_path / 'retained'
    (uploads / 'tmp').mkdir(parents=True)
    monkeypatch.setattr(main, 'UPLOAD_DIR', str(uploads))
    monkeypatch.setattr(main, 'TMP_UPLOAD_DIR', str(uploads / 'tmp'))
    monkeypatch.setattr(asset_lifecycle, '_STORES', dict(asset_lifecycle._STORES))
    asset_lifecycle.register_asset_store(uploads, retained)
    # Cancellation retirement consults real stored references before removing
    # public paths; a missing schema must retain files rather than pretending
    # the reference check succeeded.
    assert db_session.query(main.Question).count() == 0
    task_id = "test-layout-cancel-" + uuid.uuid4().hex
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pages": [{"page_index": 0, "markdown": "1. Refer to the diagram.\nFind the answer.", "needs_ocr": False}]})
    monkeypatch.setattr(pdf_figures, "request_pdf_layout", lambda image, source, info, **k: fake_layout(info))
    original_register = main.DOCUMENT_TASKS.add_temp_asset
    recorded = []
    def cancel_after_register(identifier, path):
        recorded.append(path)
        value = original_register(identifier, path)
        main.DOCUMENT_TASKS.cancel(identifier)
        return value
    monkeypatch.setattr(main.DOCUMENT_TASKS, "add_temp_asset", cancel_after_register)
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *a, **k: pytest.fail("split after cancel"))
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, make_pdf(), "cancel.pdf", page_range="1", pdf_strategy="layout_aware")
        state = main.DOCUMENT_TASKS.snapshot(task_id)
        assert state["status"] == "cancelled"
        assert recorded
        assert all(not (Path(main.TMP_UPLOAD_DIR) / Path(path).name).exists() for path in state["temp_assets"])
        for path in state['temp_assets']:
            kept = retained / 'files' / 'tmp' / Path(path).name
            assert kept.is_file(), 'Cancelled images remain recoverable after public-path cleanup'
            with Image.open(kept) as picture:
                picture.verify()
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_manual_crop_uses_selected_original_page_above_eighty():
    import main
    task_id = str(uuid.uuid4())
    image = Path(main.TMP_UPLOAD_DIR) / f"pdf_page_{task_id}_89.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), "blue").save(image)
    image_url = f"/{main.UPLOAD_DIR_REL}/tmp/{image.name}"
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", page_numbers=[90], temp_assets=[image_url])
    main.DOCUMENT_TASKS.complete(task_id)
    try:
        payload = {"task_id": task_id, "page_index": 89, "xmin": 0, "ymin": 0, "xmax": 50, "ymax": 50}
        result = main.manual_crop_pdf(payload)
        assert result["status"] == "success"
        assert Path(main.TMP_UPLOAD_DIR, Path(result["image_path"]).name).is_file()
        payload["page_index"] = 0
        assert main.manual_crop_pdf(payload).status_code == 400
    finally:
        state = main.DOCUMENT_TASKS.remove(task_id)
        main._delete_task_temp_assets(state["temp_assets"])


def test_layout_review_only_targets_proven_page_or_figure_owner():
    first, second = "1. First page question.", "2. Second page question."
    question1 = {"content": "First page question.", "answer_markdown": ""}
    question2 = {"content": "Second page question.", "answer_markdown": ""}
    issue = {"page_index": 0, "page_wide": True, "figure_ids": [],
             "reason": "配图检查未完成", "source_excerpt": first}
    result = {"pages": [{"page_index": 0, "figures": []}, {"page_index": 1, "figures": []}],
              "page_texts": [first, second], "issues": [issue]}
    report = {"source_matches": [{"question_index": 0, "source_start": 0, "source_end": len(first)},
                                  {"question_index": 1, "source_start": len(first)+2, "source_end": len(first)+2+len(second)}]}
    pdf_figures.apply_pdf_layout_reviews([question1, question2], result, report)
    assert question1["source_review"]["required"]
    assert not question2.get("source_review")
    assert report["source_review_count"] == 1
    assert report["pdf_review_items"][0]["source_number"] is None
    assert report["pdf_review_items"][0]["source_pages"] == [1]
    assert report["unmatched_source"][0]["scope"] == "possible_questions"
    assert report["unmatched_source"][0]["affected_questions"] == [{"question_index": 0, "source_number": None}]


def test_unresolved_placeholder_still_requires_its_own_question_review():
    questions = [{"content": "1. text [插图待补: 图1]"}, {"content": "2. ordinary text"}]
    report = {}
    pdf_figures.apply_pdf_layout_reviews(questions, {"pages": [], "issues": []}, report)
    assert questions[0]["source_review"]["required"]
    assert not questions[1].get("source_review")


def test_ocr_slots_have_distinct_identity_even_with_repeated_labels():
    text = "3. Choose\n\\begin{choices}\n\\item [插图待补: 图1]\n\\item [插图待补: 图1]\n\\end{choices}"
    slots = pdf_figures.describe_figure_slots(text, 0)
    assert [slot["id"] for slot in slots] == ["p1-s1", "p1-s2"]
    assert slots[0]["start"] < slots[1]["start"]
    info = {"page_index": 0, "width": 500, "height": 800, "figure_slots": slots}
    figures, warnings = pdf_figures._validate_layout({"page_complete": True, "figures": [{
        "bbox": [100, 100, 300, 300], "slot_id": "p1-s2", "review_required": False}]}, info)
    assert figures[0]["slot_id"] == "p1-s2" and not warnings


def test_review_report_identifies_question_and_hides_internal_geometry():
    path = "/static/uploads/tmp/diagram.png"
    questions = [{"content": "question 13"}, {"content": "question 21 ![](\"x\") " + path}]
    questions[0]["source_review"] = {"required": True, "reasons": ["文字与原文不同"]}
    report = {"source_matches": [
        {"question_index": 0, "source_number": 13, "source_start": 0, "source_end": 2},
        {"question_index": 1, "source_number": 21, "source_start": 4, "source_end": 6}]}
    result = {"pages": [{"page_index": 2, "figures": []}, {"page_index": 5, "figures": [
        {"id": "fig", "page_index": 5, "bbox": [1, 2, 3, 4], "image_path": path}]}],
        "page_texts": ["aa", "bb"], "issues": [{"page_index": 5, "figure_ids": ["fig"], "page_wide": False,
        "reason": "裁框未覆盖候选 p6_raster_001 的 75%，不能自动补成完整插图。", "source_excerpt": "原页"}]}
    pdf_figures.apply_pdf_layout_reviews(questions, result, report)
    assert [item["source_number"] for item in report["pdf_review_items"]] == [13, 21]
    assert report["pdf_review_items"][1]["source_pages"] == [6]
    assert "75%" not in str(report["pdf_review_items"])
    assert "p6_raster" not in str(report["pdf_review_items"])
    assert report["unmatched_source"][0]["affected_questions"] == [{"question_index": 1, "source_number": 21}]


def test_layout_aware_ocr_reuses_joint_layout_without_second_vision_request(monkeypatch):
    import main
    from mathbank import pdf_page_vision
    task_id = str(uuid.uuid4())
    source = "1. Refer to the diagram.\n[插图待补: 图1]\nFind the answer."
    calls = []
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pages": [{"page_index": 0, "markdown": "damaged", "needs_ocr": True}]})
    def joint(image_path, info, **kwargs):
        calls.append(info["page_index"])
        return {"markdown": source, "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180},
                "model": "test", "layout": {"page_complete": True, "ignored_candidates": [], "notes": [],
                "figures": [{"bbox": [125, 140, 375, 340], "candidate_ids": [info['candidates'][0]['id']],
                             "slot_id": "p1-s1", "anchor_before": "", "anchor_after": "", "review_required": False}]}}
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", joint)
    monkeypatch.setattr(pdf_figures, "request_pdf_layout", lambda *a, **k: pytest.fail("second paid layout request"))
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda *a, **k: pytest.fail("duplicate OCR request"))
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda source, *a, **k: [
        {"content": source.strip().removeprefix("1. "), "answer_markdown": ""}])
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, make_pdf(), "joint.pdf", page_range="1", pdf_strategy="layout_aware")
        state = main.DOCUMENT_TASKS.snapshot(task_id)
        assert state["status"] == "completed", state
        assert calls == [0]
        assert state["diagnostics"]["pdf_layout"]["joint_visual_calls"] == 1
        assert state["diagnostics"]["pdf_layout"]["visual_calls"] == 0
        assert state["diagnostics"]["pdf_layout"]["figures_attached"] == 1
        assert state["diagnostics"]["pdf_joint_usage"]["total_tokens"] == 180
        assert "插图待补" not in state["data"][0]["content"]
    finally:
        state = main.DOCUMENT_TASKS.remove(task_id)
        main._delete_task_temp_assets(state['temp_assets'])
