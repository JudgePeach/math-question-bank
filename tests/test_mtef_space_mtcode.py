"""Confirmed fnSPACE MTCode records do not authorize generic PUA replacement."""
import struct
from xml.etree import ElementTree as ET

import pytest

from mathbank.mtef_helper import decode_mtef_formula
from mathbank.omml_helper import normalize_word_formula_latex, omml_element_to_latex

# Formula-only MTEF from the original two objects, verified against their
# embedded PNG previews. No document text, file names or model results.
SOURCE_SET = bytes.fromhex("050100060944534d543600011357696e416c6c4261736963436f6465506167657300110554696d6573204e657720526f6d616e00110353796d626f6c001105436f7572696572204e65770011044d54204578747261001357696e416c6c436f64655061676573001106cbcecce500120008212f27f25f218f212f475f4150f21f1e4150f4150f4100f445f425f48f425f4100f4100f435f4100f21f20a5f20a25f48f21f4100f4100f40f48f417f48f4100f21a5f445f45f45f45f45f410f0c0100010001020202020002000101010003000100040005000a010010000000000000000f0102008355000204863d003d03000203000f0001000f01020083780003000401000f0001000f0102008830000204863c003c02008378000204863c003c02008836000002009607ec000200822c0002009802ef02008378000204860822ce0200875a00000200967b000200967d00000000")
SOURCE_ELEMENTS = bytes.fromhex("050100070844534d543700001357696e416c6c4261736963436f6465506167657300110554696d6573204e657720526f6d616e00110353796d626f6c001105436f7572696572204e65770011044d54204578747261001357696e416c6c436f64655061676573001106cbcecce500120008212f458f442f4150f4100f475f4150f21f1e4150f4150f4100f445f425f48f425f4100f4100f435f4100f48f45f42a5f48f48f4100f4100f40f48f417f48f4100f412a5f445f45f45f45f45f410f0c0100010001020202020002000100010003000100040005000a010002008341000204863d003d0300020300010002008831000200822c0002009804ef02008832000200822c0002009804ef0200883300000200967b000200967d00000000")


def char(code, style=24, *, options=0, suffix=b""):
    return bytes((2, options, style+128)) + struct.pack("<H", code) + suffix


def equation(body):
    return b"\x05\x01\x00\x07\x00DSMT7\x00\x00\x01\x00" + body + b"\x00\x00"


@pytest.mark.parametrize("code,space", [(0xEF02, r"\,"), (0xEF04, r"{\ }")])
def test_confirmed_fnspace_between_operands_keeps_commas_and_values(code, space):
    result = decode_mtef_formula(equation(char(ord("1"), 8) + char(ord(","), 2)
                                         + char(code) + char(ord("2"), 8)))
    assert result.success and result.confidence == "structural"
    assert result.latex == "1," + space + "2"
    diag = {}
    assert normalize_word_formula_latex(result.latex, diag) == result.latex
    assert not diag.get("unsupported_math_tokens")


@pytest.mark.parametrize("code,space", [(0xEF02, r"\,"), (0xEF04, r"{\ }")])
@pytest.mark.parametrize("position", ["first", "last", "only", "repeated"])
def test_spaces_at_formula_edges_keep_complete_tex_tokens(code, space, position):
    body, expected = {
        "first": (char(code) + char(ord("x"), 3), space + "x"),
        "last": (char(ord("x"), 3) + char(code), "x" + space),
        "only": (char(code), space),
        "repeated": (char(ord("x"), 3) + char(code) + char(code), "x" + space + space),
    }[position]
    result = decode_mtef_formula(equation(body))
    assert result.success and result.confidence == "structural"
    assert result.latex == expected
    assert not result.latex.endswith("\\")


@pytest.mark.parametrize("payload,expected", [
    (SOURCE_SET, r"U=\left\{x\left|0<x<6\right.,\,x\in Z\right\}"),
    (SOURCE_ELEMENTS, r"A=\left\{1,{\ }2,{\ }3\right\}"),
])
def test_original_mathtype_set_formulas_match_their_source_previews(payload, expected):
    result = decode_mtef_formula(payload)
    assert result.success and result.confidence == "structural"
    diag = {}
    assert normalize_word_formula_latex(result.latex, diag) == expected
    assert not diag.get("unsupported_math_tokens")
    assert not result.warning


@pytest.mark.parametrize("code", [0xEF02, 0xEF04])
@pytest.mark.parametrize("style", [3, 6, 11, 22, -1])
def test_confirmed_code_in_an_unproved_typeface_remains_a_review_glyph(code, style):
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(code, style) + char(ord("b"), 3)))
    assert result.success and chr(code) in result.latex
    diag = {}
    normalize_word_formula_latex(result.latex, diag)
    assert diag["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]


@pytest.mark.parametrize("code", [0xE990])
def test_neighbour_or_unknown_fnspace_codes_are_not_silently_discarded(code):
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(code) + char(ord("b"), 3)))
    assert result.success and chr(code) in result.latex
    diag = {}
    normalize_word_formula_latex(result.latex, diag)
    assert diag["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]


@pytest.mark.parametrize("code", [0xEF00, 0xEF01, 0xEF07, 0xEF09, 0xEF0A])
def test_unmapped_but_explicit_fnspace_uses_natural_layout_and_records_its_code(code):
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(code) + char(ord("b"), 3)))
    assert result.success and result.confidence == "structural" and result.latex == "ab"
    assert result.ignored_spacing_codes == (f"U+{code:04X}",) and not result.warning


@pytest.mark.parametrize("code", [0xEF02, 0xEF04])
@pytest.mark.parametrize("options,suffix", [(4, b"x"), (16, b"\x78\x00"), (2, b""), (1, b"\x06\x00\x02\x00")])
def test_unproved_font_position_function_or_embellished_space_keeps_review(code, options, suffix):
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(code, options=options, suffix=suffix)))
    assert result.success and chr(code) in result.latex
    diag = {}
    normalize_word_formula_latex(result.latex, diag)
    assert diag["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]


@pytest.mark.parametrize("code", [0xEF02, 0xEF04])
def test_font_position_without_explicit_mtcode_does_not_authorize_space(code):
    encoded_only = bytes((2, 0x20 | 0x10, 24+128)) + struct.pack("<H", code)
    result = decode_mtef_formula(equation(char(ord("a"), 3) + encoded_only))
    assert not result.success and not result.latex


@pytest.mark.parametrize("code", [0xEF02, 0xEF04])
def test_same_private_code_in_word_formula_or_omml_stays_unsupported(code):
    diag = {}
    normalized = normalize_word_formula_latex("a" + chr(code) + "b", diag)
    assert chr(code) not in normalized
    assert diag["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]
    ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    math = ET.fromstring(f'<m:oMath xmlns:m="{ns}"><m:r><m:t>a{chr(code)}b</m:t></m:r></m:oMath>')
    omml_diag = {}
    assert chr(code) not in omml_element_to_latex(math, omml_diag)
    assert omml_diag["unsupported_omml_tags"] == [f"privateUse:U+{code:04X}"]


def test_ascii_character_with_fnspace_style_is_not_eaten_as_whitespace():
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(ord("X")) + char(ord("b"), 3)))
    assert result.success and result.latex == "aXb"


@pytest.mark.parametrize("tail", [b"\x30", b"\x64\x01\x00", b"\x02\x00\x98\x04"])
def test_unknown_or_truncated_records_after_known_space_still_fail(tail):
    result = decode_mtef_formula(equation(char(ord("x"), 3) + char(0xEF02) + tail))
    assert not result.success and not result.latex
