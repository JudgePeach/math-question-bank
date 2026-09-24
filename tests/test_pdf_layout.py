"""Real PDF geometry/rendering tests; source anchors require no model calls."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
import pymupdf as fitz
import pytest

from mathbank.pdf_layout import (
    MAX_CANDIDATES,
    MAX_CROP_PIXELS,
    MAX_CROP_SIDE,
    apply_figure_anchors,
    crop_pdf_figure,
    inspect_pdf_page,
    normalize_model_bbox,
    refine_figure_bbox,
)


def test_model_named_rectangle_is_xyxy_independent_of_key_order_and_legacy_arrays_are_unchanged():
    value = {"top": 798, "bottom": 932, "right": 913, "left": 700}
    assert normalize_model_bbox(value) == [700, 798, 913, 932]
    assert value == {"top": 798, "bottom": 932, "right": 913, "left": 700}
    old = [801, 698, 928, 915]
    assert normalize_model_bbox(old) == old  # Never guess that a legacy array was YXYX.
    assert normalize_model_bbox(old) is not old


@pytest.mark.parametrize("value", [
    {"left": 1, "top": 2, "right": 3}, {"left": 1, "top": 2, "right": 3, "bottom": 4, "score": 1},
    {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}, {"left": True, "top": 2, "right": 3, "bottom": 4},
    {"left": "1", "top": 2, "right": 3, "bottom": 4}, [1, 2, 3], [1, 2, 3, False], (1, 2, 3, 4), None,
])
def test_model_rectangle_normalization_rejects_ambiguous_shape_and_nonnumeric_coordinates(value):
    with pytest.raises(ValueError):
        normalize_model_bbox(value)


def _png(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(output, format="PNG")
    return output.getvalue()


def _figure(identifier="fig1", before="", after="", path=None):
    return {
        "id": identifier,
        "image_path": path or f"/static/uploads/tmp/{identifier}.png",
        "bbox": [100, 100, 400, 400],
        "anchor_before": before,
        "anchor_after": after,
        "review_required": False,
        "review_reason": "",
    }


def test_real_pdf_extracts_raster_occurrences_vector_and_text_without_page_frame():
    with fitz.open() as source:
        page = source.new_page(width=300, height=300)
        xref = page.insert_image(fitz.Rect(30, 60, 70, 100), stream=_png())
        page.insert_image(fitz.Rect(120, 60, 160, 100), xref=xref)
        page.draw_polyline([(50, 180), (90, 180), (70, 220), (50, 180)])
        page.draw_rect(fitz.Rect(1, 1, 299, 299))
        page.draw_line((10, 30), (11, 30))
        page.insert_text((30, 40), "1. Choose A or B.")
        pdf_bytes = source.tobytes()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        result = inspect_pdf_page(document[0], 4)
        assert result == inspect_pdf_page(document[0], 4)
    assert result["width"] == result["height"] == 300
    assert result["page_index"] == 4
    assert result["needs_visual"]
    assert [item["type"] for item in result["candidates"]] == ["raster", "raster", "vector"]
    assert [item["id"] for item in result["candidates"]] == ["p5_raster_001", "p5_raster_002", "p5_vector_001"]
    assert result["candidates"][0]["bbox"] == pytest.approx([100, 200, 233.3333, 333.3333])
    assert "Choose A" in result["text_blocks"][0]["text"]


def test_real_pdf_full_page_scan_is_not_a_figure():
    with fitz.open() as document:
        page = document.new_page(width=200, height=200)
        page.insert_image(page.rect, stream=_png())
        result = inspect_pdf_page(page, 0)
    assert result["candidates"] == []
    assert result["needs_visual"] is True
    assert any("整页" in warning for warning in result["warnings"])


def test_plain_native_page_does_not_request_visual_layout():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 40), "1. Find the value of x.")
        result = inspect_pdf_page(page, 0)
    assert result["candidates"] == []
    assert result["needs_visual"] is False
    assert result["text_blocks"]


@pytest.mark.parametrize(("rotation", "expected"), [
    (0, [125, 200, 458.3333, 500]),
    (90, [500, 125, 800, 458.3333]),
    (180, [541.6667, 500, 875, 800]),
    (270, [200, 541.6667, 500, 875]),
])
def test_real_pdf_cropbox_rotation_inspection_and_crop_agree(tmp_path, rotation, expected):
    with fitz.open() as source:
        page = source.new_page(width=300, height=240)
        page.insert_image(fitz.Rect(60, 60, 140, 120), stream=_png(), keep_proportion=False)
        page.set_cropbox(fitz.Rect(30, 20, 270, 220))
        page.set_rotation(rotation)
        pdf_bytes = source.tobytes()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        page = document[0]
        result = inspect_pdf_page(page, 0)
        bbox = result["candidates"][0]["bbox"]
        assert bbox == pytest.approx(expected, abs=0.001)
        url = crop_pdf_figure(page, bbox, tmp_path, "/static/uploads/tmp", "rotated")
    with Image.open(tmp_path / Path(url).name) as image:
        assert image.format == "PNG"
        pixels = list(image.convert("RGB").getdata())
        red = sum(r > 240 and g < 20 and b < 20 for r, g, b in pixels)
        assert red / len(pixels) > 0.97
        expected_ratio = 80 / 60 if rotation in (0, 180) else 60 / 80
        assert image.width / image.height == pytest.approx(expected_ratio, abs=0.02)


def test_real_pdf_cropped_figure_keeps_vector_lines_and_native_label(tmp_path):
    with fitz.open() as document:
        page = document.new_page(width=300, height=200)
        page.insert_text((95, 68), "A", fontsize=14)
        page.draw_polyline([(80, 80), (120, 80), (100, 120), (80, 80)])
        url = crop_pdf_figure(page, [250, 200, 450, 650], tmp_path, "/static/uploads/tmp", "label")
    with Image.open(tmp_path / Path(url).name) as image:
        pixels = image.convert("RGB")
        top = list(pixels.crop((0, 0, image.width, int(image.height / 3))).getdata())
        lower = list(pixels.crop((0, int(image.height / 3), image.width, image.height)).getdata())
        assert sum(max(value) < 100 for value in top) > 30  # native text A
        assert sum(max(value) < 100 for value in lower) > 100  # vector triangle


def test_refine_repairs_observed_a_top_and_bc_bottom_without_nearby_text():
    page_info = {
        "width": 595, "height": 842, "candidates": [],
        "text_blocks": [
            {"bbox": [268.9076, 120.0713, 282.3597, 139.6532], "text": "A"},
            {"bbox": [144.5378, 257.8385, 426.3261, 277.4204], "text": "B C"},
            {"bbox": [144, 80, 428, 100], "text": "这段不相交的题干绝对不能被自动加入插图区域。"},
            {"bbox": [440, 130, 453, 150], "text": "D"},
        ],
    }
    original = [144, 126, 428, 276]
    result = refine_figure_bbox(page_info, original, [])
    assert result["bbox"] == pytest.approx([144 - 1500 / 595, 120.0713 - 1500 / 842, 428 + 1500 / 595, 277.4204 + 1500 / 842], abs=0.0001)
    assert result["bbox"][1] > 100
    assert result["bbox"][2] < 440
    assert any("2 处" in warning for warning in result["warnings"])
    assert original == [144, 126, 428, 276]


def test_refine_restores_small_raster_margin_but_not_an_unreferenced_candidate():
    page_info = {
        "width": 600, "height": 800,
        "candidates": [
            {"id": "image", "bbox": [100, 100, 300, 300], "type": "raster"},
            {"id": "nearby", "bbox": [300, 100, 350, 300], "type": "raster"},
        ], "text_blocks": [],
    }
    result = refine_figure_bbox(page_info, [110, 110, 290, 290], ["image"])
    assert result["bbox"] == pytest.approx([97.5, 98.125, 302.5, 301.875])
    assert result["bbox"][2] < 350
    assert any("原生候选 image" in warning for warning in result["warnings"])


@pytest.mark.parametrize(("original", "ids"), [
    ([100, 100, 200, 300], ["image"]),
    ([100, 100, 200, 300], ["missing"]),
    ([100, 100, 300, 300], ["image", "missing"]),
])
def test_refine_half_figure_or_unknown_id_remains_unmodified(original, ids):
    page_info = {"width": 600, "height": 800,
                 "candidates": [{"id": "image", "bbox": [100, 100, 300, 300]}],
                 "text_blocks": [{"bbox": [110, 95, 120, 110], "text": "A"}]}
    result = refine_figure_bbox(page_info, original, ids)
    assert result["bbox"] == original
    assert result["warnings"]


def test_refine_intersecting_prose_is_a_boundary_not_a_label():
    page_info = {
        "width": 600, "height": 800, "candidates": [],
        "text_blocks": [{"bbox": [150, 95, 700, 115], "text": "这段正文虽然与裁框相交但不能按图内标签处理并自动扩入整段正文。"}],
    }
    original = [100, 100, 300, 300]
    result = refine_figure_bbox(page_info, original, [])
    assert result["bbox"] == original  # padding would also increase prose overlap
    assert any("原生正文相交" in warning for warning in result["warnings"])


def test_refine_intersecting_short_instruction_is_not_a_diagram_label():
    page_info = {"width": 600, "height": 800, "candidates": [],
                 "text_blocks": [{"bbox": [110, 95, 250, 115], "text": "1. 求三角形面积"}]}
    original = [100, 100, 300, 300]
    result = refine_figure_bbox(page_info, original, [])
    assert result["bbox"] == original
    assert any("原生正文相交" in warning for warning in result["warnings"])


def test_refine_short_but_far_extending_label_is_not_expanded():
    page_info = {"width": 600, "height": 800, "candidates": [],
                 "text_blocks": [{"bbox": [290, 150, 700, 170], "text": "A B C D"}]}
    result = refine_figure_bbox(page_info, [100, 100, 300, 300], [])
    assert result["bbox"][2] < 310
    assert any("短标签超出" in warning for warning in result["warnings"])


def test_refine_does_not_recruit_nonintersecting_labels_through_padding():
    page_info = {"width": 600, "height": 800, "candidates": [],
                 "text_blocks": [{"bbox": [150, 98, 160, 99.9], "text": "A"}]}
    original = [100, 100, 300, 300]
    result = refine_figure_bbox(page_info, original, [])
    assert result["bbox"] == original
    assert any("碰到其他文字" in warning for warning in result["warnings"])


def test_refine_overlapping_candidates_cannot_chain_past_total_growth_limit():
    page_info = {"width": 600, "height": 800, "text_blocks": [], "candidates": [
        {"id": "left", "bbox": [75, 100, 190, 300]},
        {"id": "right", "bbox": [210, 100, 325, 300]},
        {"id": "top", "bbox": [100, 75, 300, 190]},
        {"id": "bottom", "bbox": [100, 210, 300, 325]},
    ]}
    result = refine_figure_bbox(page_info, [100, 100, 300, 300], ["left", "right", "top", "bottom"])
    x0, y0, x1, y1 = result["bbox"]
    assert (x1 - x0) * (y1 - y0) <= 200 * 200 * 1.5
    assert any("超出有限扩张" in warning for warning in result["warnings"])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_real_pdf_refinement_uses_rotated_cropbox_coordinates(tmp_path, rotation):
    with fitz.open() as document:
        page = document.new_page(width=300, height=240)
        page.insert_image(fitz.Rect(60, 60, 140, 120), stream=_png(), keep_proportion=False)
        page.set_cropbox(fitz.Rect(30, 20, 270, 220))
        page.set_rotation(rotation)
        info = inspect_pdf_page(page, 0)
        candidate = info["candidates"][0]
        full_box = candidate["bbox"]
        cropped = [full_box[0] + 5, full_box[1] + 5, full_box[2] - 5, full_box[3] - 5]
        refined = refine_figure_bbox(info, cropped, [candidate["id"]])
        assert refined["bbox"][0] < full_box[0]
        assert refined["bbox"][1] < full_box[1]
        assert refined["bbox"][2] > full_box[2]
        assert refined["bbox"][3] > full_box[3]
        url = crop_pdf_figure(page, refined["bbox"], tmp_path, "/static/uploads/tmp", "refined")
    with Image.open(tmp_path / Path(url).name) as image:
        assert image.format == "PNG"
        assert image.getpixel((image.width // 2, image.height // 2)) == (255, 0, 0)
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_real_pdf_large_crop_has_bounded_png_dimensions(tmp_path):
    with fitz.open() as document:
        page = document.new_page(width=12000, height=9000)
        page.draw_rect(fitz.Rect(0, 0, 6000, 6000), fill=(0, 0, 1))
        url = crop_pdf_figure(page, [0, 0, 500, 500], tmp_path, "/static/test_uploads/tmp", "bounded")
    with Image.open(tmp_path / Path(url).name) as image:
        assert image.format == "PNG"
        assert max(image.size) <= MAX_CROP_SIDE
        assert image.width * image.height <= MAX_CROP_PIXELS


@pytest.mark.parametrize("bbox", [
    None, [], [1, 2, 3], [0, 0, 1001, 500], [-1, 0, 100, 100],
    [0, 0, float("nan"), 200], [0, 0, float("inf"), 200],
    [0, 0, True, 100], ["0", 0, 100, 100], [50, 20, 40, 100],
    [10, 10, 10.1, 100], [0, 0, 1000, 1000],
])
def test_crop_rejects_invalid_or_unbounded_bbox_without_writing(tmp_path, bbox):
    with fitz.open() as document:
        page = document.new_page()
        with pytest.raises(ValueError):
            crop_pdf_figure(page, bbox, tmp_path, "/static/uploads/tmp", "invalid")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("prefix", "asset_prefix"), [
    ("https://example.com/files", "safe"), ("/static/uploads/../tmp", "safe"),
    ("/static/uploads/tmp", "../../bad"), ("/static/uploads/tmp", "x.png"),
])
def test_crop_rejects_path_input_outside_server_contract(tmp_path, prefix, asset_prefix):
    with fitz.open() as document:
        page = document.new_page()
        with pytest.raises(ValueError):
            crop_pdf_figure(page, [100, 100, 400, 400], tmp_path, prefix, asset_prefix)
    assert list(tmp_path.iterdir()) == []


def test_real_pdf_many_raster_occurrences_are_limited_with_explicit_warning():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        xref = 0
        for index in range(MAX_CANDIDATES + 5):
            x, y = 20 + (index % 10) * 50, 20 + (index // 10) * 60
            xref = page.insert_image(fitz.Rect(x, y, x + 10, y + 10), xref=xref, stream=_png() if not xref else None)
        result = inspect_pdf_page(page, 0)
    assert len(result["candidates"]) == MAX_CANDIDATES
    assert result["needs_visual"]
    assert any("候选超过" in warning for warning in result["warnings"])


def test_crop_never_overwrites_or_deletes_a_preexisting_destination(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mathbank import pdf_layout

    monkeypatch.setattr(pdf_layout.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    existing = tmp_path / "collision_fixed.png"
    existing.write_bytes(b"existing asset")
    with fitz.open() as document:
        page = document.new_page()
        with pytest.raises(FileExistsError):
            crop_pdf_figure(page, [100, 100, 300, 300], tmp_path, "/static/uploads/tmp", "collision")
    assert existing.read_bytes() == b"existing asset"


def test_exact_anchor_inserts_without_changing_other_source_and_keeps_shared_order():
    source = "1. First sentence.\n\nNext sentence."
    figures = [
        _figure("fig2", before="First sentence.", after="Next sentence."),
        _figure("fig1", before="First sentence.", after="Next sentence.", path="/static/uploads/tmp/fig2.png"),
    ]
    result = apply_figure_anchors(source, figures)
    assert result["attached"] == ["fig2", "fig1"]
    assert result["unmatched"] == []
    markup = "\n![插图](/static/uploads/tmp/fig2.png)\n"
    assert result["markdown"] == source.replace("First sentence.", "First sentence." + markup * 2)
    assert figures[0]["anchor_before"] == "First sentence."


def test_multiple_anchors_resolve_against_original_source():
    source = "First.\nNext.\nLast."
    result = apply_figure_anchors(source, [
        _figure("last", before="Last."),
        _figure("first", after="First."),
        _figure("middle", before="First.", after="Next."),
    ])
    assert result["attached"] == ["last", "first", "middle"]
    assert result["markdown"].index("first.png") < result["markdown"].index("First.")
    assert result["markdown"].index("middle.png") < result["markdown"].index("Next.")
    assert result["markdown"].index("Last.") < result["markdown"].index("last.png")


def test_markdown_table_anchor_keeps_the_original_row_and_cell():
    source = "| A | B |\n|---|---|\n| x | y |\n| second | row |"
    result = apply_figure_anchors(source, [_figure(before="| x ", after="| y |")])
    assert result["attached"] == ["fig1"]
    assert len(result["markdown"].splitlines()) == 4
    assert result["markdown"].splitlines()[2] == "| x  ![插图](/static/uploads/tmp/fig1.png) | y |"


@pytest.mark.parametrize(("source", "before", "after"), [
    (r"\begin{choices}\item A: \item B: \end{choices}", r"\item A:", r"\item B:"),
    (r"\begin{tabular}{cc}Left & Right \\ \end{tabular}", "Left", "& Right"),
    (r"\begin{tabular}{cc}\multicolumn{2}{c}{Graph here} \\ \end{tabular}", "Graph here", "}"),
    ("See $x+1$ then continue.", "$x+1$", "then continue."),
    (r"Show \(x\) here.", r"\(x\)", "here."),
    ("<span>Visible text</span>", "Visible text", "</span>"),
])
def test_exact_anchor_allows_option_cell_and_protected_span_boundary(source, before, after):
    result = apply_figure_anchors(source, [_figure(before=before, after=after)])
    assert result["attached"] == ["fig1"], result
    assert "![插图]" in result["markdown"]


@pytest.mark.parametrize(("source", "before", "after"), [
    ("Repeated.\nRepeated.", "Repeated.", ""),
    ("Left intervening words Right", "Left", "Right"),
    ("Text", "", ""), ("Text", "  ", ""),
    ("Exact spelling", "exact spelling", ""),
    ("Math $x+1$ end", "$x", "+1$"),
    ("Math $$x+1$$ end", "$$x", "+1$$"),
    (r"Math \(x+1\) end", r"\(x", r"+1\)"),
    (r"\begin{alignat}{2}x&=1\end{alignat}", "x&", "=1"),
    (r"\begin{cases}x&=1\end{cases}", "x&", "=1"),
    ("Unclosed $x end", "end", ""),
    ("`code content`", "code", "content"),
    ("```tex\ncode content\n```", "code", "content"),
    ("~~~\ncode content\n~~~", "code", "content"),
    (r"\verb|code content|", "code", "content"),
    (r"\begin{verbatim}code content\end{verbatim}", "code", "content"),
    ('<img src="image.png">', '<img src="image', '.png">'),
    ("<!-- page marker -->", "page", "marker"),
    (r"\includegraphics{image.png}", "image", ".png"),
    ("![label](image.png)", "![lab", "el]"),
    ("![label](image.png)", "image", ".png"),
    (r"\begin{tabular}{cc}Left & Right\end{tabular}", "{c", "c}"),
    (r"\dfrac{x}{y}", r"\df", "rac"),
    (r"Bare \frac{1}{2} formula", r"\frac{", "1}"),
    (r"Bare \dfrac{1+a}{2+b} formula", "2+", "b}"),
    (r"Bare \tfrac{1}{2} formula", r"\tfrac{1}", "{2}"),
    (r"Bare \cfrac[l]{1+a}{2} formula", "1+", "a}"),
    (r"Bare \sqrt[3]{x+1} formula", "[", "3]"),
    (r"Bare \sqrt[3]{x+1} formula", "{x+", "1}"),
    (r"Bare \frac {a} {\sqrt{b}} formula", r"\sqrt{", "b}"),
    (r"Bare \vec{AB} formula", "{A", "B}"),
    (r"Bare \overline{AB} formula", "{A", "B}"),
    (r"Bare \frac{1}{unfinished", "unfinished", ""),
])
def test_unsafe_or_ambiguous_anchor_is_retained_for_review(source, before, after):
    figure = _figure(before=before, after=after)
    figure["confidence"] = 1.0
    result = apply_figure_anchors(source, [figure])
    assert result["markdown"] == source
    assert result["attached"] == []
    assert result["unmatched"][0]["image_path"] == figure["image_path"]
    assert result["unmatched"][0]["reason"]


@pytest.mark.parametrize("path", ["https://example.com/a.png", "../a.png", "/static/uploads/tmp/a.png)evil(", "/static/uploads/tmp/../a.png"])
def test_anchor_never_inserts_untrusted_image_path(path):
    result = apply_figure_anchors("Source", [_figure(before="Source", path=path)])
    assert result["markdown"] == "Source"
    assert result["unmatched"]


def test_native_option_markers_are_not_mistaken_for_clipped_vertex_labels():
    # Wuhan paper: PyMuPDF merges A./B. across both image columns. Only the
    # actual embedded image belongs in the crop; the choices grid renders A.
    info = {"width": 595, "height": 842,
            "candidates": [{"id": "imageA", "bbox": [206.7227, 397.6247, 400.9412, 532.1615]}],
            "text_blocks": [{"bbox": [176.4706, 457.3115, 529.8319, 473.8001], "text": "A．\nB．\n"}]}
    result = refine_figure_bbox(info, [207, 398, 401, 532], ["imageA"])
    assert not any("短标签" in message for message in result["warnings"])
    assert result["bbox"][0] > 200
