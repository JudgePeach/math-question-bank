"""MTCode identities do not authorize matching arbitrary Word private glyphs."""
import struct

import pytest

from mathbank.mtef_helper import decode_mtef_formula
from mathbank.omml_helper import normalize_word_formula_latex


def char(code,style=3,font_position=None):
    return bytes((2,4 if font_position is not None else 0,style+128))+struct.pack('<H',code)+(bytes((font_position,)) if font_position is not None else b'')


def equation(body):
    return b'\x05\x01\x00\x07\x00DSMT7\x00\x00\x01\x00'+body+b'\x00\x00'


@pytest.mark.parametrize('style,font_position',[(11,103),(11,None),(6,None),(3,None)])
def test_explicit_mtef_medium_dot_preserves_the_operator_and_its_token_boundary(style,font_position):
    result=decode_mtef_formula(equation(char(ord('a'))+char(0xE98F,style,font_position)+char(ord('b'))))
    assert result.success and result.confidence=='structural'
    assert result.latex==r'a\centerdot b'
    diag={};assert normalize_word_formula_latex(result.latex,diag)==result.latex
    assert not diag.get('unsupported_math_tokens')


@pytest.mark.parametrize('code',[0xE98E,0xE990,0xEF0A])
def test_unproved_neighbor_or_spacing_mtcode_remains_a_review_glyph(code):
    result=decode_mtef_formula(equation(char(ord('a'))+char(code,11)+char(ord('b'))))
    assert result.success and chr(code) in result.latex
    diag={};normalize_word_formula_latex(result.latex,diag)
    assert diag['unsupported_math_tokens']==[f'privateUse:U+{code:04X}']


def test_same_private_code_in_arbitrary_word_math_is_not_globally_mapped():
    diag={};latex=normalize_word_formula_latex('a\ue98fb',diag)
    assert latex == 'ab' and '\\centerdot' not in latex
    assert diag['unsupported_math_tokens']==['privateUse:U+E98F']
