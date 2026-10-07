"""Reject incomplete extraction blocks before declaring native text complete.

The first fixture owns an independent glyph/answer oracle. The second is a real
PyMuPDF mixed page. Faults are injected only into the extraction dictionary;
the underlying visible PDF, layout evidence and expected text remain intact.
No application, database, configuration, network or model is accessed.
"""
from copy import deepcopy
from io import BytesIO

from PIL import Image
import pymupdf as fitz
import pytest

from mathbank.pdf_layout import inspect_pdf_page
from mathbank.pdf_native_math import repair_native_page
from mathbank.pdf_native_regions import plan_pdf_regions


PROSE = "这是一段能够从原生页面直接保留的中文说明文字用于解释题目的背景和作答要求。"
MORE_PROSE = "请认真阅读题目的条件并按照要求完成解答书写过程中应当说明推理的依据。"


def glyph(char, x, y, font="CMMI10", size=10.5):
    width = size * (0.85 if "\u3400" <= char <= "\u9fff" else 0.5)
    box = (x, y - size * 0.8, x + width, y + size * 0.2)
    return {"size": size, "flags": 6 if font.startswith("CMMI") else 4,
            "font": font, "color": 0, "ascender": 0.8, "descender": -0.2,
            "origin": (x, y), "bbox": box,
            "chars": [{"origin": (x, y), "bbox": box, "c": char}]}


def line(spans):
    box = (min(span["bbox"][0] for span in spans), min(span["bbox"][1] for span in spans),
           max(span["bbox"][2] for span in spans), max(span["bbox"][3] for span in spans))
    return {"wmode": 0, "dir": (1, 0), "bbox": box, "spans": spans}


class GlyphPage:
    def __init__(self):
        self.rect = fitz.Rect(0, 0, 600, 800)
        self.cropbox = self.rect
        self.mediabox = self.rect
        self.rotation = 0
        self.number = 0
        self.lines = [
            line([glyph(char, 40 + index * 10, 60, "STSong") for index, char in enumerate("题目1：")]),
            line([glyph("x", 80, 100), glyph("2", 86, 96, "CMR7", 7),
                  glyph("+", 94, 100, "CMR10"), glyph("a", 104, 100),
                  glyph("1", 110, 103, "CMR7", 7), glyph("=", 118, 100, "CMR10"),
                  glyph("3", 129, 100, "CMR10")]),
            line([glyph(char, 40 + index * 10, 140, "STSong") for index, char in enumerate("答案：C")]),
        ]

    def get_text(self, kind="text", **_kwargs):
        assert kind == "rawdict"
        return {"width": 600, "height": 800,
                "blocks": [{"type": 0, "number": index, "bbox": value["bbox"], "lines": [deepcopy(value)]}
                           for index, value in enumerate(self.lines)]}

    def get_images(self, *_args, **_kwargs):
        return []

    def get_drawings(self, **_kwargs):
        return []


class ExtractionFault:
    def __init__(self, page, mode, mutation, target=0):
        self.page = page
        self.mode = mode
        self.mutation = mutation
        self.target = target

    def __getattr__(self, name):
        return getattr(self.page, name)

    def get_text(self, mode="text", **kwargs):
        data = self.page.get_text(mode, **kwargs)
        if mode != self.mode:
            return data
        blocks = data["blocks"]
        block = blocks[self.target]
        if self.mutation == "missing_lines":
            block.pop("lines")
        elif self.mutation == "empty_lines":
            block["lines"] = []
        elif self.mutation == "none_lines":
            block["lines"] = None
        elif self.mutation == "not_dict_block":
            blocks[self.target] = "malformed text block"
        elif self.mutation == "not_dict_line":
            block["lines"] = ["malformed text line"]
        elif self.mutation == "empty_line":
            block["lines"] = [{}]
        elif self.mutation == "image_block":
            blocks.append({"type": 1, "bbox": (50, 180, 150, 260), "image": b"image bytes"})
        else:
            raise AssertionError("Unknown fault")
        return data


def test_complete_supported_native_page_preserves_question_math_and_plain_answer():
    result = repair_native_page(GlyphPage())
    assert result["status"] == "repaired"
    compact = "".join(result["markdown"].split())
    assert "题目1：" in compact and "答案：C" in compact
    assert "x^{2}+a_{1}=3" in compact
    assert result["stats"]["glyphs_consumed"] == result["stats"]["glyphs_total"]


@pytest.mark.parametrize("mutation", ["missing_lines", "empty_lines", "none_lines", "not_dict_block", "not_dict_line", "empty_line"])
def test_native_math_cannot_report_complete_after_losing_a_text_block(mutation):
    # The original answer oracle remains visible but disappears from the
    # extraction dictionary. Counting only the surviving glyphs is insufficient.
    result = repair_native_page(ExtractionFault(GlyphPage(), "rawdict", mutation, target=2))
    assert result["status"] == "unsupported"
    assert result["markdown"] == ""
    assert result["reason"]


def test_native_math_image_block_keeps_the_existing_nontext_fallback():
    # A legal image block has no lines by design. It must not be interpreted as
    # an incomplete text block or silently adopted by pure-glyph math recovery.
    result = repair_native_page(ExtractionFault(GlyphPage(), "rawdict", "image_block"))
    assert result["status"] == "unsupported"
    assert result["markdown"] == ""
    assert "非文字" in result["reason"] or "位图" in result["reason"]


def add_text(page, value, y):
    page.insert_text((40, y), value, fontname="china-s", fontsize=10)


def add_picture(page):
    buffer = BytesIO()
    Image.new("RGB", (60, 60), "red").save(buffer, format="PNG")
    page.insert_image(fitz.Rect(50, 180, 150, 260), stream=buffer.getvalue())


def mixed_page(document):
    page = document.new_page(width=500, height=600)
    add_text(page, PROSE, 70)
    add_text(page, "x = 2", 160)
    add_picture(page)
    add_text(page, MORE_PROSE, 350)
    return page


def native_text(plan):
    return "\n".join(piece["text"] for piece in plan["pieces"] if "text" in piece)


def test_complete_dictionary_keeps_both_prose_blocks_and_the_risky_band():
    with fitz.open() as document:
        page = mixed_page(document)
        plan = plan_pdf_regions(page, inspect_pdf_page(page, 0))
        assert plan is not None and plan["kind"] == "mixed"
        assert PROSE in native_text(plan) and MORE_PROSE in native_text(plan)
        assert "x" not in native_text(plan)
        assert len(plan["regions"]) == 1


@pytest.mark.parametrize("mutation", ["missing_lines", "empty_lines", "none_lines", "not_dict_block", "not_dict_line", "empty_line"])
def test_regional_plan_falls_back_when_a_visible_text_block_has_no_line_evidence(mutation):
    with fitz.open() as document:
        page = mixed_page(document)
        original_layout = inspect_pdf_page(page, 0)
        damaged = ExtractionFault(page, "dict", mutation, target=0)
        assert plan_pdf_regions(damaged, original_layout) is None


def test_legal_image_block_does_not_prevent_an_otherwise_complete_mixed_plan():
    with fitz.open() as document:
        page = mixed_page(document)
        layout = inspect_pdf_page(page, 0)
        expected = plan_pdf_regions(page, layout)
        actual = plan_pdf_regions(ExtractionFault(page, "dict", "image_block"), layout)
        assert actual == expected


def test_image_only_pdf_remains_eligible_for_the_existing_single_image_region():
    with fitz.open() as document:
        page = document.new_page(width=500, height=600)
        add_picture(page)
        layout = inspect_pdf_page(page, 0)
        expected = plan_pdf_regions(page, layout)
        assert expected is not None and expected["kind"] == "image_only"
        # Real raw image blocks have no lines. Use the default dict form here
        # to expose it, while production normally strips image dictionaries.
        class ImageDictionary:
            def __getattr__(self, name):
                return getattr(page, name)
            def get_text(self, mode="text", **kwargs):
                if mode == "dict":
                    return page.get_text("dict")
                return page.get_text(mode, **kwargs)
        assert plan_pdf_regions(ImageDictionary(), layout) == expected
