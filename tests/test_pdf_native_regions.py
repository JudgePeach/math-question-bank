"""Local region planning with real PDF geometry; no model or network calls."""
from copy import deepcopy
import io

import pymupdf as fitz
from PIL import Image
import pytest
import requests

from mathbank import pdf_native_regions as planner
from mathbank.pdf_layout import inspect_pdf_page

PROSE = "这是一段能够从原生页面直接保留的中文说明文字用于解释题目的背景和作答要求。"
MORE_PROSE = "请认真阅读题目的条件并按照要求完成解答书写过程中应当说明推理的依据。"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("No network in local planner"))


def text(page, value, x, y):
    page.insert_text((x, y), value, fontname="china-s", fontsize=10)


def picture(page, box):
    image = Image.new("RGB", (60, 60), "white")
    for value in range(5, 55):
        image.putpixel((value, value), (255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    page.insert_image(fitz.Rect(box), stream=buffer.getvalue())


def native_text(plan):
    return "\n".join(piece["text"] for piece in plan["pieces"] if "text" in piece)


def contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def assert_integrity(info, plan):
    regions = {region["id"]: region for region in plan["regions"]}
    assert sorted(piece["region_id"] for piece in plan["pieces"] if "region_id" in piece) == sorted(regions)
    used = []
    for region in regions.values():
        box = region["bbox"]
        assert 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000
        for candidate in region["page_info"]["candidates"]:
            used.append(candidate["id"])
            original = next(value for value in info["candidates"] if value["id"] == candidate["id"])
            x0, y0, x1, y1 = candidate["bbox"]
            recovered = [box[0] + x0 * (box[2] - box[0]) / 1000,
                         box[1] + y0 * (box[3] - box[1]) / 1000,
                         box[0] + x1 * (box[2] - box[0]) / 1000,
                         box[1] + y1 * (box[3] - box[1]) / 1000]
            assert recovered == pytest.approx(original["bbox"], abs=0.001)
            assert contains(box, original["bbox"])
    assert sorted(used) == sorted(candidate["id"] for candidate in info["candidates"])
    boxes = [region["bbox"] for region in regions.values()]
    assert all(not planner._overlap(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert plan["area_ratio"] == pytest.approx(sum(planner._area(box) for box in boxes) / 1_000_000)


def test_safe_prose_and_math_image_band_are_kept_separate():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, PROSE, 40, 70)
        text(page, "x = 2", 40, 160)
        picture(page, [50, 180, 150, 260])
        text(page, MORE_PROSE, 40, 350)
        info = inspect_pdf_page(page, 0)
        plan = planner.plan_pdf_regions(page, info)
        assert plan and plan["kind"] == "mixed"
        assert PROSE in native_text(plan) and MORE_PROSE in native_text(plan)
        assert "x" not in native_text(plan) and "2" not in native_text(plan)
        assert [set(piece) for piece in plan["pieces"]] == [{"text"}, {"region_id"}, {"text"}]
        assert_integrity(info, plan)


def test_chinese_fragment_sharing_a_math_baseline_is_not_retained():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, PROSE, 40, 65)
        fragment = "根据上述条件可得到等式"
        text(page, fragment, 40, 160)
        text(page, "x = 2", 200, 160)
        text(page, MORE_PROSE, 40, 350)
        info = inspect_pdf_page(page, 0)
        plan = planner.plan_pdf_regions(page, info)
        assert plan and fragment not in native_text(plan)
        row = next(row for row in planner._text_rows(page, page.rect) if fragment in row["text"])
        assert any(contains(region["bbox"], row["bbox"]) for region in plan["regions"])


def test_native_text_inside_graph_stays_with_vision():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, PROSE, 40, 65)
        picture(page, [40, 130, 400, 260])
        caption = "这段文字属于图内标注"
        text(page, caption, 170, 190)
        text(page, MORE_PROSE, 40, 350)
        info = inspect_pdf_page(page, 0)
        plan = planner.plan_pdf_regions(page, info)
        assert plan and caption not in native_text(plan)
        assert_integrity(info, plan)


def test_fraction_bar_and_tiny_unlisted_visual_mark_cannot_be_dropped():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, PROSE, 40, 65)
        text(page, "1", 100, 145)
        text(page, "2", 100, 166)
        page.draw_line((97, 150), (125, 150), width=0.6)
        page.draw_line((430, 155), (430.2, 155.2), width=0.3)
        text(page, MORE_PROSE, 40, 350)
        plan = planner.plan_pdf_regions(page, inspect_pdf_page(page, 0))
        assert plan
        for box in planner._visual_boxes(page, page.rect):
            assert any(contains(region["bbox"], box) for region in plan["regions"])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_image_only_rotation_and_cropbox_preserve_candidate_and_pixels(rotation):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        picture(page, [150, 210, 270, 360])
        page.set_cropbox(fitz.Rect(50, 100, 550, 700))
        page.set_rotation(rotation)
        info = inspect_pdf_page(page, 3)
        plan = planner.plan_pdf_regions(page, info)
        assert plan and plan["kind"] == "image_only"
        assert plan["native_characters"] == 0 and len(plan["regions"]) == 1
        assert plan["regions"][0]["page_info"]["page_index"] == 3
        assert_integrity(info, plan)
        box = plan["regions"][0]["bbox"]
        rect = fitz.Rect(box[0] * page.rect.width / 1000, box[1] * page.rect.height / 1000,
                         box[2] * page.rect.width / 1000, box[3] * page.rect.height / 1000)
        pixmap = page.get_pixmap(clip=rect, colorspace=fitz.csRGB)
        pixels = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        assert sum(r > 150 and g < 100 and b < 100 for r, g, b in pixels.getdata()) > 20


def test_image_only_cannot_omit_an_unlisted_outside_drawing():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        picture(page, [50, 80, 150, 180])
        page.draw_line((400, 500), (402, 500), width=0.2)
        info = inspect_pdf_page(page, 0)
        assert len(info["candidates"]) == 1
        assert planner.plan_pdf_regions(page, info) is None


@pytest.mark.parametrize("stagger", [0, 12])
def test_multicolumn_prose_does_not_assume_top_to_bottom_order(stagger):
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        left = "左栏完整叙述应当按照左栏自身顺序阅读"
        right = "右栏完整叙述应当按照右栏自身顺序阅读"
        for y in (80, 120):
            text(page, left, 30, y)
            text(page, right, 280, y + stagger)
        text(page, "x=2", 30, 250)
        assert planner.plan_pdf_regions(page, inspect_pdf_page(page, 0)) is None


def test_little_reuse_and_too_many_regions_fall_back():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, "请认真阅读题目并解答", 40, 65)
        text(page, "x=2", 40, 160)
        assert planner.plan_pdf_regions(page, inspect_pdf_page(page, 0)) is None
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        for index, y in enumerate((80, 180, 280, 380)):
            text(page, "x=2", 40, y)
            if index < 3:
                text(page, PROSE, 40, y + 50)
        assert planner.plan_pdf_regions(page, inspect_pdf_page(page, 0)) is None


def test_large_risk_area_falls_back_even_with_reliable_prose():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, PROSE, 40, 30)
        picture(page, [20, 55, 480, 575])
        assert planner.plan_pdf_regions(page, inspect_pdf_page(page, 0)) is None


@pytest.mark.parametrize("mutation", ["warning", "duplicate", "nan", "wrong_size", "unknown_type"])
def test_invalid_or_incomplete_evidence_falls_back(mutation):
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        picture(page, [50, 80, 150, 180])
        info = deepcopy(inspect_pdf_page(page, 0))
        if mutation == "warning": info["warnings"] = ["candidate list truncated"]
        elif mutation == "duplicate": info["candidates"].append(deepcopy(info["candidates"][0]))
        elif mutation == "nan": info["candidates"][0]["bbox"][0] = float("nan")
        elif mutation == "wrong_size": info["width"] += 2
        else: info["candidates"][0]["type"] = "unknown"
        assert planner.plan_pdf_regions(page, info) is None


@pytest.mark.parametrize("value", ["其中有二十个元素 x", "已知原生数值为２", "其中未知字形为\uef04", "含有上标字符²的条件", "存在关系符号≤的说明"])
def test_risky_characters_never_enter_native_pieces(value):
    assert planner._safe_prose(value, False) is False


def test_superscript_prose_is_not_certified():
    assert planner._safe_prose(PROSE, True) is False


def test_sideways_text_keeps_visual_fallback():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        text(page, PROSE, 40, 70)
        text(page, "x=2", 40, 160)
        page.set_rotation(90)
        assert planner.plan_pdf_regions(page, inspect_pdf_page(page, 0)) is None


def test_large_whitespace_is_removed_when_three_region_budget_allows():
    with fitz.open() as document:
        page = document.new_page(width=500, height=800)
        text(page, "x=2", 40, 65)
        text(page, "y=3", 40, 330)
        text(page, PROSE, 40, 400)
        text(page, "z=4", 40, 500)
        plan = planner.plan_pdf_regions(page, inspect_pdf_page(page, 0))
        assert plan and len(plan["regions"]) == 3
        assert plan["area_ratio"] < 0.02


def test_many_blank_separated_math_rows_use_one_complete_band_when_needed():
    with fitz.open() as document:
        page = document.new_page(width=500, height=800)
        text(page, PROSE, 40, 60)
        for y in (200, 350, 500, 650):
            text(page, "x=2", 40, y)
        plan = planner.plan_pdf_regions(page, inspect_pdf_page(page, 0))
        assert plan and len(plan["regions"]) == 1
        assert PROSE in native_text(plan)
        for row in planner._text_rows(page, page.rect):
            if "x=2" in row["text"]:
                assert contains(plan["regions"][0]["bbox"], row["bbox"])
