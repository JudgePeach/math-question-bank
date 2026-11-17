import hashlib
from io import BytesIO
import json
from zipfile import ZipFile

import pymupdf as fitz
import pytest

from mathbank.pdf_figures import _mineru_graph_crop, enrich_pdf_with_figures
from mathbank.pdf_mineru_source import load_mineru_pdf_source


def pdf(word):
    with fitz.open() as d:
        page = d.new_page(width=300, height=400)
        page.insert_text((30, 45), word)
        return d.tobytes()


def make_bundle(rows, original):
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("trial_origin.pdf", original)
        archive.writestr("trial_content_list.json", json.dumps(rows))
    return stream.getvalue()


def test_bound_blocks_are_source_text_and_original_image_slots_only():
    original = pdf("Real page")
    rows = [
        {"page_idx": 0, "type": "text", "text": r"1. $a_{n+1}=a_n+\frac1{a_n}$"},
        {"page_idx": 0, "type": "image", "bbox": [200, 300, 500, 600],
         "img_path": "ignored.png", "image_caption": ["图注"]},
        {"page_idx": 0, "type": "page_number", "text": "page 1"},
    ]
    src = load_mineru_pdf_source(original, make_bundle(rows, original))
    assert src.source_sha256 == hashlib.sha256(original).hexdigest()
    page = src.page(0)
    assert r"\frac1{a_n}" in page["markdown"] and "[插图待补: 图1]" in page["markdown"]
    assert "图注" in page["markdown"] and "ignored.png" not in page["markdown"] and "page 1" not in page["markdown"]
    assert page["figures"][0]["bbox"] == {"left": 200, "top": 300, "right": 500, "bottom": 600}
    assert src.single_image_box(0) == (200, 300, 500, 600)


def test_mismatched_source_pixels_are_rejected():
    with pytest.raises(ValueError, match="像素"):
        load_mineru_pdf_source(pdf("Real page"), make_bundle(
            [{"page_idx": 0, "type": "text", "text": "different"}], pdf("Different page")))


@pytest.mark.parametrize("row", [
    {"type": "text", "text": "![](https://example.invalid/p.png)"},
    {"type": "image", "bbox": [800, 20, 40, 50]},
    {"type": "equation", "text": ""},
    {"type": "unknown", "text": "Maybe material"},
])
def test_unsupported_or_unsafe_page_stays_available_to_existing_recognizer(row):
    original = pdf("Real page")
    source = load_mineru_pdf_source(original, make_bundle([{"page_idx": 0, **row}], original))
    assert source.page(0) is None


def test_crop_candidate_is_independent_of_transcription_but_requires_one_image():
    original = pdf("Real page")
    image = {"page_idx": 0, "type": "image", "bbox": [259, 265, 579, 503]}
    unknown_text = {"page_idx": 0, "type": "unknown"}
    source = load_mineru_pdf_source(original, make_bundle([image, unknown_text], original))
    assert source.page(0) is None
    assert source.single_image_box(0) == (259, 265, 579, 503)
    second = {**image, "bbox": [600, 200, 750, 550]}
    ambiguous = load_mineru_pdf_source(original, make_bundle([image, second], original))
    assert ambiguous.single_image_box(0) is None


@pytest.mark.parametrize(("original_status", "proposed", "confirmed", "expected"), [
    ("suspect", [259, 265, 579, 503], [260, 260, 585, 505], True),
    ("verified", [259, 265, 579, 503], [260, 260, 585, 505], True),
    ("corrected", [259, 265, 579, 503], [260, 260, 585, 505], True),
    ("verified", [610, 300, 950, 540], [260, 260, 585, 505], False),
    ("suspect", [610, 300, 950, 540], [260, 260, 585, 505], False),
    ("suspect", [259, 265, 579, 503], [275, 260, 585, 505], False),
])
def test_mineru_competing_box_must_recover_the_same_graph(
        monkeypatch, original_status, proposed, confirmed, expected):
    original = pdf("Real page")
    row = {"page_idx": 0, "type": "image", "bbox": proposed}
    source = load_mineru_pdf_source(original, make_bundle([row], original))
    initial = {"status": original_status,
               "method": "isolated_graph" if original_status == "corrected" else "unchanged",
               "figure_candidates": 0 if original_status == "corrected" else 1,
               "model_bbox": [80, 290, 290, 530] if original_status == "corrected" else [210, 290, 540, 530],
               "bbox": [260, 260, 585, 505], "evidence_bbox": [260, 260, 585, 505]}

    def validate(path, box, *, improve_local_frame):
        assert improve_local_frame is True
        assert tuple(box) == source.single_image_box(0)
        return {"method": "unchanged" if original_status == "verified" else "isolated_graph",
                "status": "verified" if original_status == "verified" else "corrected",
                "figure_candidates": initial["figure_candidates"], "warnings": [], "bbox": confirmed}

    monkeypatch.setattr("mathbank.pdf_raster_guard.guard_raster_figure_bbox", validate)
    assert (_mineru_graph_crop(source, 0, "owned.png", initial) is not None) == expected


def test_enrichment_refuses_figure_bundle_from_another_pdf(tmp_path):
    original = pdf("Real page")
    row = {"page_idx": 0, "type": "image", "bbox": [200, 300, 500, 600]}
    source = load_mineru_pdf_source(original, make_bundle([row], original))
    with pytest.raises(ValueError, match="未绑定"):
        enrich_pdf_with_figures(
            pdf("Another page"), [], [], [], [], set(),
            output_dir=tmp_path, url_prefix="/static/uploads/tmp", task_id="wrong-source",
            check_cancelled=lambda: None, register_asset=lambda _path: None, report_progress=lambda *_args: None,
            mineru_figure_source=source,
        )


@pytest.mark.parametrize("method,expected", [("isolated_graph", True), ("unchanged", False)])
def test_truncated_original_can_use_independently_proven_mineru_frame(monkeypatch, method, expected):
    original = pdf("Real page")
    row = {"page_idx": 0, "type": "image", "bbox": [126, 251, 439, 473]}
    source = load_mineru_pdf_source(original, make_bundle([row], original))
    initial = {"status": "suspect", "figure_candidates": 0, "model_bbox": [115, 245, 350, 475]}
    candidate = {"method": method, "status": "corrected" if expected else "verified",
                 "figure_candidates": 0 if expected else 1, "warnings": [],
                 "bbox": [127.2158, 253.8462, 446.2982, 476.9231]}
    monkeypatch.setattr("mathbank.pdf_raster_guard.guard_raster_figure_bbox", lambda *a, **k: candidate)
    assert (_mineru_graph_crop(source, 0, "owned.png", initial) is not None) == expected


@pytest.mark.parametrize("count,method,expected", [
    (0, "isolated_graph", True), (0, "unchanged", False), (2, "isolated_graph", False),
])
def test_misplaced_frame_can_only_be_replaced_by_a_unique_isolated_graph(
        monkeypatch, count, method, expected):
    original = pdf("Real page")
    row = {"page_idx": 0, "type": "image", "bbox": [126, 251, 439, 473]}
    source = load_mineru_pdf_source(original, make_bundle([row], original))
    initial = {"status": "not_applicable", "figure_candidates": count, "model_bbox": [119, 545, 431, 891]}
    candidate = {"method": method, "status": "corrected", "figure_candidates": 0,
                 "warnings": [], "bbox": [127.2158, 253.8462, 446.2982, 476.9231]}
    monkeypatch.setattr("mathbank.pdf_raster_guard.guard_raster_figure_bbox", lambda *a, **k: candidate)
    assert (_mineru_graph_crop(source, 0, "owned.png", initial) is not None) == expected
