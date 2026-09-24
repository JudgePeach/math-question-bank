"""Scan-crop guards integrate before writing and affect only their source figure."""

from io import BytesIO

from PIL import Image
import pymupdf as fitz
import pytest

from mathbank import pdf_figures as figures


def scan():
    with fitz.open() as document:
        page = document.new_page(width=400, height=600)
        buffer = BytesIO()
        Image.new("RGB", (400, 600), "white").save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())
        return document.tobytes()


def test_direct_layout_named_coordinates_are_normalized_and_auditable():
    raw = {"left": 100, "top": 200, "right": 300, "bottom": 450}
    info = {"page_index": 0, "width": 400, "height": 600, "candidates": [], "text_blocks": []}
    result = {"page_complete": True, "ignored_candidates": [], "figures": [{
        "bbox": raw, "candidate_ids": [], "anchor_before": "题干", "anchor_after": "",
        "review_required": False, "review_reason": ""}]}
    checked, warnings = figures._validate_layout(result, info)
    assert warnings == []
    assert checked[0]["model_bbox"] == [100, 200, 300, 450]
    assert checked[0]["model_bbox_raw"] == raw
    assert checked[0]["model_bbox_space"] == "page"


@pytest.mark.parametrize("guard_mode", ["corrected", "suspect", "not_applicable"])
@pytest.mark.parametrize("model_warning", [False, True])
def test_scanned_box_is_checked_before_crop_and_warning_stays_on_own_question(tmp_path, monkeypatch, guard_mode, model_warning):
    from mathbank import pdf_raster_guard
    from mathbank.content_locks import lock_visible_math, reconcile_visible_math

    source = "1. 计算相应结果。\n\n2. 如图判断结论。\n[插图待补: 图1]"
    raw_bbox, corrected = [801, 698, 928, 915], [690, 790, 930, 940]
    guard_warning = guard_mode == "suspect"
    calls, cropped, assets = [], [], []
    def guard(path, bbox):
        calls.append((path, bbox))
        return {"bbox": corrected, "changed": guard_mode == "corrected", "status": guard_mode,
                "warnings": ["图形边界仍有歧义，请核对裁剪。"] if guard_warning else [],
                "notes": [] if guard_warning else ["本地检查说明。"]}
    monkeypatch.setattr(pdf_raster_guard, "guard_raster_figure_bbox", guard)
    def crop(page, bbox, *args):
        cropped.append(bbox)
        return "/static/test_uploads/tmp/checked_crop.png"
    monkeypatch.setattr(figures, "crop_pdf_figure", crop)
    monkeypatch.setattr(figures, "request_pdf_layout", lambda *a, **k: pytest.fail("Precomputed layout must not call a model"))
    payload = {"page_complete": True, "ignored_candidates": [], "figures": [{
        "bbox": raw_bbox, "slot_id": "p1-s1", "candidate_ids": [],
        "review_required": model_warning, "review_reason": "模型仍无法确定配图是否完整。" if model_warning else ""}]}
    result = figures.enrich_pdf_with_figures(scan(), [0], ["local-page.png"],
        ["/static/test_uploads/tmp/pdf_page_fixture.png"], [source], {0},
        output_dir=tmp_path, url_prefix="/static/test_uploads/tmp", task_id="guard-flow",
        check_cancelled=lambda: None, register_asset=assets.append, report_progress=lambda *a: None,
        precomputed_layouts={0: payload})
    figure = result["pages"][0]["figures"][0]
    assert calls == [("local-page.png", raw_bbox)]
    assert figure["model_bbox"] == raw_bbox and figure["model_bbox_raw"] == raw_bbox
    assert figure["attached"] and len(assets) == 1
    if guard_mode == "corrected":
        assert cropped == [corrected]
    if not guard_warning and not model_warning:
        assert result["diagnostics"]["review_pages"] == 0
    else:
        assert figure["review_required"]
        assert result["issues"][0]["page_wide"] is False
        full_source = result["page_texts"][0]
        questions = [{"content": "计算相应结果。"},
                     {"content": "如图判断结论。\n\n![插图](/static/test_uploads/tmp/checked_crop.png)"}]
        _, locks = lock_visible_math(full_source, "guard-review")
        diagnostic = reconcile_visible_math(questions, locks, full_source)
        figures.apply_pdf_layout_reviews(questions, result, diagnostic)
        assert not questions[0].get("source_review", {}).get("required")
        assert questions[1]["source_review"]["required"]
        assert "配图" in str(questions[1]["source_review"]["reasons"]) or "裁剪" in str(questions[1]["source_review"]["reasons"])
