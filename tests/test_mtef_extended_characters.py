"""Finite, source-specific MathType character support never guesses PUA text."""
import struct

import pytest

from mathbank.mtef_helper import decode_mtef_formula
from mathbank.omml_helper import normalize_word_formula_latex


def char(code, typeface=6, *, options=0, encoded=b""):
    return bytes((2, options, typeface + 128)) + struct.pack("<H", code) + encoded


def equation(body):
    return b"\x05\x01\x00\x07\x00DSMT7\x00\x00\x01\x00" + body + b"\x00\x00"


SPACES = [(0xEF03, r"\;"), (0xEF05, r"\quad"),
          (0xEF06, r"\qquad"), (0xEF22, r"\!")]


@pytest.mark.parametrize("code,latex", SPACES)
def test_explicit_fnspace_preserves_values_and_control_word_boundaries(code, latex):
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(code, 24) + char(ord("b"), 3)))
    expected = "a" + latex + (" " if latex in (r"\quad", r"\qquad") else "") + "b"
    assert result.success and result.confidence == "structural" and result.latex == expected
    diagnostics = {}
    assert normalize_word_formula_latex(result.latex, diagnostics) == expected
    assert not diagnostics.get("unsupported_math_tokens")


@pytest.mark.parametrize("code,latex", SPACES)
@pytest.mark.parametrize("position", ["first", "last", "only"])
def test_fnspace_at_edges_is_a_complete_tex_token(code, latex, position):
    body = {"first": char(code, 24) + char(ord("x"), 3),
            "last": char(ord("x"), 3) + char(code, 24), "only": char(code, 24)}[position]
    result = decode_mtef_formula(equation(body))
    expected = {"first": latex + (" " if latex in (r"\quad", r"\qquad") else "") + "x",
                "last": "x" + latex, "only": latex}[position]
    assert result.success and result.latex == expected and not result.latex.endswith("\\")


@pytest.mark.parametrize("code,_", SPACES)
@pytest.mark.parametrize("typeface", [3, 6, 11, 22, -1])
def test_space_code_in_another_typeface_retains_unknown_character_review(code, _, typeface):
    result = decode_mtef_formula(equation(char(code, typeface)))
    assert chr(code) in result.latex
    diagnostics = {}
    normalize_word_formula_latex(result.latex, diagnostics)
    assert diagnostics["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]


@pytest.mark.parametrize("code,_", SPACES)
@pytest.mark.parametrize("options,encoded", [(4, b"x"), (16, b"x\x00"), (2, b""), (1, b"\x06\x00\x02\x00")])
def test_conflicting_font_position_function_or_embellishment_is_not_certified(code, _, options, encoded):
    result = decode_mtef_formula(equation(char(code, 24, options=options, encoded=encoded)))
    assert chr(code) in result.latex
    diagnostics = {}
    normalize_word_formula_latex(result.latex, diagnostics)
    assert diagnostics["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]


@pytest.mark.parametrize("code,_", SPACES)
def test_new_space_does_not_hide_a_future_extension(code, _):
    result = decode_mtef_formula(equation(char(ord("x"), 3) + char(code, 24) + b"\x66\x01\x00"))
    assert not result.success and not result.latex


@pytest.mark.parametrize("code", [0xEF00, 0xEF01, 0xEF07, 0xEF09, 0xEF0A, 0xEF21, 0xEF23])
def test_proven_spacing_category_needs_no_guessed_width_or_inline_placeholder(code):
    result = decode_mtef_formula(equation(char(ord("x"), 3) + char(code, 24)))
    assert result.success and result.latex == "x"
    assert result.ignored_spacing_codes == (f"U+{code:04X}",) and not result.warning


@pytest.mark.parametrize("typeface", [3, 6, 11, 22, -1])
def test_unknown_spacing_code_in_another_typeface_is_never_ignored(typeface):
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(0xEF0A, typeface)))
    assert chr(0xEF0A) in result.latex and not result.ignored_spacing_codes


@pytest.mark.parametrize("options,encoded", [(4, b"x"), (16, b"x\x00"), (2, b""), (1, b"\x06\x00\x02\x00")])
def test_unknown_spacing_conflicts_are_never_ignored(options, encoded):
    result = decode_mtef_formula(equation(char(0xEF0A, 24, options=options, encoded=encoded)))
    assert chr(0xEF0A) in result.latex and not result.ignored_spacing_codes


def test_ignored_spacing_cannot_hide_a_future_record():
    result = decode_mtef_formula(equation(char(ord("x"), 3) + char(0xEF0A, 24) + b"\x66\x01\x00"))
    assert not result.success and not result.latex and not result.ignored_spacing_codes


def test_ignored_spacing_codes_are_unique_and_control_words_keep_operand_boundaries():
    result = decode_mtef_formula(equation(char(0x2200) + char(0xEF0A, 24) + char(0xEF0A, 24)
                                         + char(ord("z"), 3) + char(0xEF09, 24)))
    assert result.success and result.latex == r"\forall z"
    assert result.ignored_spacing_codes == ("U+EF0A", "U+EF09")


@pytest.mark.parametrize("code,_", SPACES)
def test_mapping_is_not_applied_to_arbitrary_word_private_use_text(code, _):
    diagnostics = {}
    assert chr(code) not in normalize_word_formula_latex("a" + chr(code) + "b", diagnostics)
    assert diagnostics["unsupported_math_tokens"] == [f"privateUse:U+{code:04X}"]


UNICODE_OPERATORS = [
    (0x2201, r"\complement"), (0x220B, r"\ni"), (0x2213, r"\mp"),
    (0x2216, r"\setminus"), (0x2217, r"\ast"), (0x2218, r"\circ"),
    (0x2223, r"\mid"), (0x2224, r"\nmid"), (0x2226, r"\nparallel"),
    (0x222C, r"\iint"), (0x222D, r"\iiint"), (0x222E, r"\oint"),
    (0x2284, r"\not\subset"), (0x2285, r"\not\supset"),
    (0x2288, r"\nsubseteq"), (0x2289, r"\nsupseteq"),
    (0x2295, r"\oplus"), (0x2296, r"\ominus"), (0x2297, r"\otimes"),
    (0x2298, r"\oslash"), (0x2299, r"\odot"), (0x22C5, r"\cdot"),
    (0x22EE, r"\vdots"), (0x22EF, r"\cdots"), (0x22F1, r"\ddots"),
]


@pytest.mark.parametrize("code,latex", UNICODE_OPERATORS)
def test_unicode_identity_controls_the_operator_despite_a_font_position(code, latex):
    # The explicit MTCode is a Unicode semantic identity; the font-position
    # field is for drawing it and cannot replace it with a different symbol.
    result = decode_mtef_formula(equation(char(ord("a"), 3) + char(code, options=4, encoded=b"x") + char(ord("b"), 3)))
    assert result.success and result.latex == "a" + latex + " b"
    assert not result.warning
    diagnostics = {}
    assert normalize_word_formula_latex(result.latex, diagnostics) == result.latex
    assert not diagnostics.get("unsupported_math_tokens")


def test_ascending_ellipsis_retains_preview_review_without_an_unavailable_iddots_macro():
    result = decode_mtef_formula(equation(char(0x22F0)))
    assert not result.success and not result.latex
    assert "升序对角省略号" in result.warning and "原公式预览" in result.warning


def test_asterisk_does_not_become_a_five_point_star_or_a_bullet():
    result = decode_mtef_formula(equation(char(0x2217) + char(0x22C6) + char(0x2219)))
    assert result.success and result.latex == r"\ast⋆∙"


# Original equation-only MTEF streams; no Word text, file names or API result.
@pytest.mark.parametrize("payload,expected", [
    (bytes.fromhex('050100070844534d543700011357696e416c6c4261736963436f6465506167657300110554696d6573204e657720526f6d616e00110353796d626f6c001105436f7572696572204e65770011044d54204578747261001357696e416c6c436f64655061676573001106cbcecce500120008210a5f27f25f218f212f475f4150f21f1e4150f4150f4100f445f425f48f425f4100f4100f435f4100f21f20a5f20a25f48f21f4100f4100f40f48f417f48f4100f21a5f445f45f45f45f45f410f0c0100010001020202020002000101010003000100040005000a010010000000000000000f0102008367000f0003001b00000b01000f010200836b00000f000101000a0f010204863d003d03000b00000f0001000f010200883100000f0001000f010200836e000000020486d700b40200836b000204862b002b03000b00000f0001000f010200883100000f0001000f010200836e00000003000103000f0001000f01020088310002048612222d02008367000f0003001b00000b01000f010200836b000204862b002b0200883100000f00010100000a0f0102009628000200962900000204862b002b03000b00000f0001000f010200883100000f0001000f010200836e00000003000103000f0001000f01020088310002048612222d02008367000f0003001b00000b01000f010200836b000204862b002b0200883200000f00010100000a0f0102009628000200962900000204862b002b02048bef224c0204862b002b03000b00000f0001000f010200883100000f0001000f010200836e00000003000103000f0001000f01020088310002048612222d02008367000f0003001b00000b01000f010200836e00000f00010100000a0f0102009628000200962900000204863d003d020088310002048612222d03000b00000f0001000f010200883100000f0001000f010200836e0000000200980aef0300107000010002008367000f0003001b00000b01000f010200836d00000f00010100000f0101000200836d000204863d003d0200836b000204862b002b02008831000001000200836e00000d0204861122e5000000'), 'g_{k}=\\dfrac{1}{n}\\times k+\\dfrac{1}{n}\\left(1-g_{k+1}\\right)+\\dfrac{1}{n}\\left(1-g_{k+2}\\right)+\\cdots+\\dfrac{1}{n}\\left(1-g_{n}\\right)=1-\\dfrac{1}{n}\\sum_{m=k+1}^{n}{g_{m}}'),
    (bytes.fromhex('050100070844534d543700011357696e416c6c4261736963436f6465506167657300110554696d6573204e657720526f6d616e00110353796d626f6c001105436f7572696572204e65770011044d54204578747261001357696e416c6c436f64655061676573001106cbcecce500120008210a5f27f25f218f212f475f4150f21f1e4150f4150f4100f445f425f48f425f4100f4100f435f4100f21f20a5f20a25f48f21f4100f4100f40f48f417f48f4100f21a5f445f45f45f45f45f410f0c0100010001020202020002000101010003000100040005000a010010000000000000000f01030016700001010b0f0001000f010200836b000204863d003d0200883100000f0001000f010200836e00000d0f0001000f010204861122e500000a03000103000f0001000f0103000a00000f0001000f01020081740002008161000200816e0003000b00000100020081c003000f0001000f0102008834000f0003001c00000b010101000f010200836b0000000000000101000a0204862b002b03000a00000f0001000f01020081730002008169000200816e0003000b00000100020081c003000f0001000f0102008834000f0003001c00000b010101000f010200836b000000000000010100000a02009628000200962900000204863e003e0200883200030016700001010b0f0001000f010200836b000204863d003d0200883100000f0001000f010200836e00000d0f0001000f010204861122e500000a0200980aef03000a00000f0001000f0103000b00000100020081c003000f0001000f0102008834000f0003001c00000b010101000f010200836b0000000000000101000a0204863d003d030016700001010b0f0001000f010200836b000204863d003d0200883100000f0001000f010200836e00000d0f0001000f010204861122e500000a03000b00000f0001000f01020088320003000a00000100020081c003000b010100000a0f0001000f0102008832000f0003001c00000b010101000f010200836b00000000000a0204863d003d020088320003000a00000100020081c003000b0101000a03000103000f0001000f01020088310002048612222d03000b00000f0001000f010200883100000f0001000f0102008832000f0003001c00000b010101000f010200836e0000000000000a02009628000200962900000000'), '\\sum_{k=1}^{n}\\left(\\sqrt{tan\\dfrac{\\pi}{4^{k}}}+\\sqrt{sin\\dfrac{\\pi}{4^{k}}}\\right)>2\\sum_{k=1}^{n}\\sqrt{\\dfrac{\\pi}{4^{k}}}=\\sum_{k=1}^{n}\\dfrac{2\\sqrt{\\pi}}{2^{k}}=2\\sqrt{\\pi}\\left(1-\\dfrac{1}{2^{n}}\\right)'),
])
def test_real_spacing_formulas_keep_source_with_verified_0x70_limits(payload, expected):
    result = decode_mtef_formula(payload)
    assert result.success and result.confidence == "structural"
    assert result.latex == expected and not result.warning
    assert result.ignored_spacing_codes == ("U+EF0A",)
    diagnostics = {}
    assert normalize_word_formula_latex(result.latex, diagnostics) == expected
    assert not diagnostics.get("unsupported_math_tokens")
