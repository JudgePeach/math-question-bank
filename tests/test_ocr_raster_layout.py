"""Raster hints never certify figure ownership or select delivered crops."""

from PIL import Image, ImageDraw

from mathbank.ocr_raster_layout import raster_ink_components, compatible_ink_hint, context_bbox, source_pixel_bbox
from mathbank.ocr_figure_review import _safe_box


def ultrawide_image():
    image = Image.new("RGB", (1009, 157), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((320, 70, 473, 137), outline="black", width=3)
    draw.line([(590, 136), (712, 65), (712, 136), (590, 136)], fill="black", width=3)
    return image


def test_complete_rectangle_and_triangle_each_have_one_candidate_hint():
    image = ultrawide_image()
    components = raster_ink_components(image)
    initial = [{"left": 325, "top": 376, "right": 495, "bottom": 784},
               {"left": 602, "top": 331, "right": 753, "bottom": 784}]
    hints = [compatible_ink_hint(box, components, image.size) for box in initial]
    assert all(len(items) == 1 for items in hints)
    assert hints[0][0]["bbox_pixels"] == {"left": 320., "top": 70., "right": 474., "bottom": 138.}
    assert hints[1][0]["bbox_pixels"]["right"] >= 713
    assert all(hint[0]["kind"] == "raster_ink_component" for hint in hints)
    assert all("native_box" not in hint[0] for hint in hints)


def test_ultrawide_source_context_keeps_full_short_height_and_pixel_mapping():
    box = {"left": 325, "top": 376, "right": 495, "bottom": 784}
    context = context_bbox(box, (1009, 157))
    assert context["top"] == 0 and context["bottom"] == 157
    assert context["left"] < 320 and context["right"] > 474
    assert source_pixel_bbox(box, (1009, 157))["top"] == 59


def test_full_graph_plus_small_white_margin_is_allowed_but_far_and_mixed_boxes_are_rejected():
    initial_b = {"left": 325, "top": 376, "right": 495, "bottom": 784}
    initial_c = {"left": 602, "top": 331, "right": 753, "bottom": 784}
    full_b = {"left": 311.199207, "top": 407.643312, "right": 475.718533, "bottom": 917.197452}
    full_c = {"left": 578.790882, "top": 375.796178, "right": 712.58672, "bottom": 910.828025}
    others = {"B": initial_b, "C": initial_c}
    assert _safe_box(full_b, initial_b, "B", others, (1009, 157))
    assert _safe_box(full_c, initial_c, "C", others, (1009, 157))
    assert not _safe_box(full_c, initial_b, "B", others, (1009, 157))
    assert not _safe_box({"left": 200, "top": 200, "right": 800, "bottom": 990}, initial_b, "B", others, (1009, 157))
    assert not _safe_box({**initial_b, "bottom": 990, "top": 100}, initial_b, "B", {}, (1009, 157))


def test_diagonal_pixels_connect_as_eight_neighbors_without_an_unbounded_fill():
    image = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.line((10, 10, 40, 40), fill="black", width=1)
    assert len(raster_ink_components(image)) == 1


def test_no_ink_or_multiple_compatible_components_provides_no_unique_hint():
    blank = Image.new("RGB", (100, 100), "white")
    assert raster_ink_components(blank) == []
    box = {"left": 100, "top": 100, "right": 500, "bottom": 700}
    duplicate = {"id": "candidate", "bbox": dict(box), "bbox_pixels": {}}
    assert compatible_ink_hint(box, [duplicate, dict(duplicate, id="other")], (1009, 157)) == []
