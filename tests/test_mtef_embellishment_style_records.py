"""Known MTEF SIZE/COLOR records do not turn an over-arrow into a failed CHAR.

Reference: https://docs.wiris.com/en_US/mathtype-mtef-v5-mathtype-40-and-later
The two formula-only real fragments below preserve their original byte order;
no paper text, answers or model-generated transcription is used as a fixture.
"""
import struct

import pytest

from mathbank.mtef_helper import MAX_DEPTH, MAX_RECORDS, decode_mtef_formula
from mathbank.omml_helper import normalize_word_formula_latex

HEADER = b"\x05\x01\x00\x07\x00DSMT7\x00\x00"
BEIJING_COLOR_ARROW_PERP = bytes.fromhex(
    "050100060944534d543600011357696e416c6c4261736963436f6465506167657300110554696d6573204e657720526f6d616e00110353796d626f6c001105436f7572696572204e65770011044d54204578747261001357696e416c6c436f64655061676573001106cbcecce500120008212f27f25f218f212f475f4150f21f1e4150f4150f4100f445f425f48f425f4100f4100f435f4100f21f20a5f20a25f48f21f4100f4100f40f48f417f48f4100f21a5f445f45f45f45f45f410f0c0100010001020202020002000101010003000100040005000a010010000000000000000f0102018361000f0006000b000f01020484a5225e02018362000f0006000b000000"
)
BEIJING_COLOR_ARROW_MAGNITUDE = bytes.fromhex(
    "050100060944534d543600011357696e416c6c4261736963436f6465506167657300110554696d6573204e657720526f6d616e00110353796d626f6c001105436f7572696572204e65770011044d54204578747261001357696e416c6c436f64655061676573001106cbcecce500120008212f27f25f218f212f475f4150f21f1e4150f4150f4100f445f425f48f425f4100f4100f435f4100f21f20a5f20a25f48f21f4100f4100f40f48f417f48f4100f21a5f445f45f45f45f45f410f0c0100010001020202020002000101010003000100040005000a010010000000000000000f0103000403000f0001000f0102018363000f0006000b00000f0102009607ec02009608ec000204863d003d0000"
)


def _char(value="a", children=b"\x06\x00\x0b"):
    return b"\x02\x01\x83" + struct.pack("<H", ord(value)) + children + b"\x00"


def _stream(children):
    return HEADER + b"\x0a\x01\x00" + _char(children=children) + b"\x00\x00"


@pytest.mark.parametrize("style", [
    b"\x09\x00\x80",                  # SIZE szFULL, delta 0
    b"\x09\x65\x80\xfe",            # explicit point size
    b"\x09\x64\x01\x80\x00",        # large delta for szSUB
    *(bytes([value]) for value in range(10, 15)),
    b"\x0f\x00",                      # COLOR default black
    b"\x0f\xff\x01\x01",            # COLOR 257, unsigned index
])
@pytest.mark.parametrize("arrow,expected", [(11, r"\overrightarrow{a}"), (12, r"\overleftarrow{a}"), (13, r"\overleftrightarrow{a}")])
def test_known_style_records_can_precede_a_supported_arrow(style, arrow, expected):
    result = decode_mtef_formula(_stream(style + bytes([6, 0, arrow])))
    assert result.success and result.confidence == "structural", result
    assert result.latex == expected
    assert result.warning == ""


def test_style_records_between_multiple_embellishments_keep_both_modifiers():
    result = decode_mtef_formula(_stream(b"\x06\x00\x0b\x0f\x00\x0a\x06\x00\x05"))
    assert result.success and result.latex == r"\overrightarrow{a}'", result


@pytest.mark.parametrize("payload,expected", [
    (BEIJING_COLOR_ARROW_PERP, r"\overrightarrow{a}\perp\overrightarrow{b}"),
    (BEIJING_COLOR_ARROW_MAGNITUDE, r"\left|\overrightarrow{c}\right|="),
])
def test_real_beijing_formula_bytes_keep_their_exact_mathematical_structure(payload, expected):
    result = decode_mtef_formula(payload)
    assert result.success and result.confidence == "structural", result
    diagnostics = {}
    # Existing Word normalization maps the recorded MTCode vertical-bar
    # glyphs; the template selector 4 independently specifies vertical bars.
    assert normalize_word_formula_latex(result.latex, diagnostics) == expected
    assert not diagnostics.get("unsupported_math_tokens")


@pytest.mark.parametrize("child", [
    b"\x02\x00\x83b\x00",            # visible CHAR b
    b"\x01\x00\x00",                # LINE, even empty
    b"\x03\x00\x01\x03\x00\x00",  # fence template
    b"\x04\x00\x00\x00\x00",      # PILE
    b"\x07\x00",                    # RULER
    b"\x08\x01\x00",                # FONT_STYLE_DEF
    b"\x10\x00\x00\x00\x00\x00\x00\x00",  # COLOR_DEF
    b"\x11\x05Times New Roman\x00",  # FONT_DEF
    b"\x13WinAllCodePages\x00",      # ENCODING_DEF
    b"\x64\x01X",                    # future record cannot be invisible by assumption
    b"\x14",                          # unknown record
])
def test_other_records_in_a_char_embellishment_list_still_fail_closed(child):
    result = decode_mtef_formula(_stream(b"\x0f\x00" + child + b"\x06\x00\x0b"))
    assert not result.success and result.latex == "", result


@pytest.mark.parametrize("child", [
    b"\x06\x00\x07",                  # backwards prime unsupported by renderer
    b"\x06\x00\xff",                  # unknown embellishment type
    b"\x06\x08\x80",                  # truncated nudge
])
def test_unsupported_or_truncated_embellishments_do_not_gain_trust(child):
    result = decode_mtef_formula(_stream(b"\x0f\x00" + child))
    assert not result.success and result.latex == "", result


@pytest.mark.parametrize("tail", [b"\x0f", b"\x0f\xff", b"\x0f\xff\x01", b"\x09", b"\x09\x65\x80", b"\x06\x00"])
def test_truncated_style_or_embellishment_cannot_consume_a_missing_list_end(tail):
    payload = HEADER + b"\x0a\x01\x00\x02\x01\x83a\x00" + tail
    result = decode_mtef_formula(payload)
    assert not result.success and result.latex == "", result


def test_nested_char_cannot_hide_source_content_even_if_it_has_a_valid_arrow():
    result = decode_mtef_formula(_stream(b"\x0f\x00" + _char("b") + b"\x06\x00\x0b"))
    assert not result.success and result.latex == "", result


def test_large_known_style_list_remains_bounded_by_record_limit():
    result = decode_mtef_formula(_stream(b"\x0a" * MAX_RECORDS + b"\x06\x00\x0b"))
    assert not result.success and result.latex == "", result
    assert "记录数量异常" in result.warning


def test_deep_nested_lists_remain_bounded_by_depth_limit():
    payload = HEADER + (b"\x01\x00" * (MAX_DEPTH + 2)) + b"\x00" * (MAX_DEPTH + 3)
    result = decode_mtef_formula(payload)
    assert not result.success and result.latex == "", result
    assert "嵌套层级过深" in result.warning


def test_unexplained_nonzero_trailing_data_still_fails():
    result = decode_mtef_formula(_stream(b"\x0f\x00\x06\x00\x0b") + b"\x01")
    assert not result.success and result.latex == "", result
