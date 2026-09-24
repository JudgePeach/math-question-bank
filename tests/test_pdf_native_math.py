"""Local reconstruction contracts independent of model responses or user files."""

from copy import deepcopy
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from mathbank.pdf_native_math import repair_native_page


def glyph(c, x, y, font="CMMI10", size=10.5):
    box = (x, y-size*.8, x+size*.5, y+size*.2)
    return {"font": font, "size": size, "origin": (x,y), "bbox": box,
            "chars": [{"c": c, "origin": (x,y), "bbox": box}]}


def line(spans):
    return {"dir": (1,0), "spans": spans}


def text(value, x, y, font="STSong"):
    return [glyph(c, x+i*6, y, font) for i,c in enumerate(value)]


def page(lines):
    data = {"blocks": [{"type": 0, "lines": lines}]}
    return SimpleNamespace(rotation=0, rect=fitz.Rect(0,0,600,800),
        get_text=lambda kind: deepcopy(data), get_images=lambda **kw: [], get_fonts=lambda **kw: [], get_drawings=lambda: [])


def expression(y=100):
    return [glyph("x",80,y),glyph("2",86,y-4,"CMR7",7),glyph("=",96,y,"CMR10"),glyph("1",106,y,"CMR10")]


def test_choice_answers_are_emitted_and_only_true_page_number_is_metadata():
    original = page([line([glyph("1",540,35,"CMR10")]),line(text("题目1：",40,60)),line(expression()),
                     line(text("答案：C",40,130)),line(text("答案：BCD",40,150)),line(text("答案：AC",40,170)),
                     line(text("评析：保留完整说明。",40,190))])
    result = repair_native_page(original)
    assert result["status"] == "repaired", result["reason"]
    for answer in ("C", "BCD", "AC"):
        assert "答案：" + answer in result["markdown"]
    assert "评析：保留完整说明。" in result["markdown"]
    assert not result["markdown"].startswith("1")
    stats = result["stats"]
    assert stats["glyphs_consumed"] == stats["glyphs_total"]
    assert stats["metadata_glyphs"] == 1
    assert stats["emitted_glyphs"] + stats["metadata_glyphs"] + stats["whitespace_glyphs"] == stats["glyphs_total"]


def test_all_physical_option_labels_remain_text_even_in_cmr_font():
    original = page([line(expression()), line(text("A.1",40,140,"CMR10") + text("B.2",90,140,"CMR10")
                                            + text("C.3",140,140,"CMR10") + text("D.4",190,140,"CMR10"))])
    result = repair_native_page(original)
    assert result["status"] == "repaired", result["reason"]
    for letter in "ABCD":
        assert letter + "." in result["markdown"]
        assert r"\mathrm{" + letter + "}" not in result["markdown"]


@pytest.mark.parametrize("char", ["$", "\\", "%", "&", "_", "{", "`"])
def test_unknown_literal_tex_control_cannot_become_an_export_instruction(char):
    result = repair_native_page(page([line(expression()),line(text("文字"+char+"内容",40,140))]))
    assert result["status"] == "unsupported" and result["markdown"] == ""


def test_unverified_text_font_subscript_is_not_flattened_as_ordinary_text():
    spans = expression()
    spans[1] = glyph("2",86,96,"TimesNewRoman",7)
    result = repair_native_page(page([line(spans),line(expression(150))]))
    assert result["status"] == "unsupported" and "混合字体" in result["reason"]


def test_separate_spaced_numbers_cannot_silently_become_one_number():
    result = repair_native_page(page([line(expression()),line([glyph("1",80,150,"CMR10"),glyph("2",100,150,"CMR10")])]))
    assert result["status"] == "unsupported" and result["markdown"] == ""


def test_parallel_math_columns_cannot_be_concatenated_into_an_equation_product():
    left = expression()
    right = [glyph("y",350,100),glyph("2",356,96,"CMR7",7),glyph("=",366,100,"CMR10"),glyph("2",376,100,"CMR10")]
    result = repair_native_page(page([line(left + right)]))
    assert result["status"] == "unsupported" and result["markdown"] == ""


def test_plain_page_is_not_rewritten_merely_because_repair_is_available():
    result = repair_native_page(page([line(text("普通正文无需数学结构恢复",40,100))]))
    assert result["status"] == "unsupported" and not result["stats"]["has_supported_math"]


def test_overlay_true_origin_can_come_from_physical_trace_without_widening_target_guess():
    spans = [glyph("B",80,100),glyph("\u0338",87,100,"CMSY10"),glyph("⊂",90,100,"CMSY10"),glyph("A",105,100)]
    spans[1]["bbox"] = (87,91.6,87,102.1)
    spans[1]["chars"][0]["bbox"] = spans[1]["bbox"]
    original = page([line(spans)])
    original.get_texttrace = lambda: [{"font":"CMSY10","size":10.5,"chars":[(0x338, 1, (90,100), (90,91.6,90,102.1))]}]
    result = repair_native_page(original)
    assert result["status"] == "repaired", result["reason"]
    assert r"B\not\subset A" in result["markdown"]
    assert result["stats"]["negations"] == 1


def test_ambiguous_trace_cannot_certify_a_zero_width_overlay():
    original = page([line([glyph("B",80,100),glyph("\u0338",87,100,"CMSY10"),glyph("⊂",90,100,"CMSY10")])])
    original.get_texttrace = lambda: [{"font":"CMSY10","size":10.5,"chars":[
        (0x338, 1, (90,100),(90,91,90,102)),(0x338, 2, (91,100),(91,91,91,102))]}]
    result = repair_native_page(original)
    assert result["status"] == "unsupported" and result["markdown"] == ""
