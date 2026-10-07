"""Bounded ISO OMML examples preserve meaning; unknown semantics keep review.

These constructed XML cases are not reported as errors in user DOCX files.
No provider requests are used. The marked native Word roundtrip requires local Pandoc.
"""
from io import BytesIO
import json
import shutil
import zipfile
import xml.etree.ElementTree as ET

import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.docx_source_scopes import verify_source_review_evidence
from mathbank.omml_helper import omml_element_to_latex, normalize_word_formula_latex
from mathbank.source_metadata import prepare_word_source_metadata
from test_math_preview_regressions import run_preview_script

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def run(text):
    return '<m:r><m:t>' + text + '</m:t></m:r>'


def convert(body):
    diagnostics = {}
    element = ET.fromstring(f'<m:oMath xmlns:m="{M}">{body}</m:oMath>')
    output = omml_element_to_latex(element, diagnostics)
    return normalize_word_formula_latex(output, diagnostics), diagnostics


def accent(char=None, props=''):
    if char is not None:
        props = f'<m:chr m:val="{char}"/>' + props
    properties = '<m:accPr>' + props + '</m:accPr>' if props else ''
    return '<m:acc>' + properties + '<m:e>' + run('AB') + '</m:e></m:acc>'


def fraction(kind=None, props=''):
    if kind is not None:
        props = f'<m:type m:val="{kind}"/>' + props
    return '<m:f><m:fPr>' + props + '</m:fPr><m:num>' + run('a+b') + '</m:num><m:den>' + run('c+d') + '</m:den></m:f>'


def delimiter(begin='(', end=')', inner=None, props=''):
    return '<m:d><m:dPr>' + f'<m:begChr m:val="{begin}"/><m:endChr m:val="{end}"/>' + props + '</m:dPr><m:e>' + (inner or run('x')) + '</m:e></m:d>'


def array(*rows):
    return '<m:eqArr>' + ''.join('<m:e>' + run(row) + '</m:e>' for row in rows) + '</m:eqArr>'


@pytest.mark.parametrize('char,command', [(None, 'hat'), ('̂', 'hat'), ('^', 'hat'),
    ('→', 'vec'), ('⃗', 'vec'), ('←', 'overleftarrow'), ('↔', 'overleftrightarrow'),
    ('̃', 'widetilde'), ('̈', 'ddot'), ('̇', 'dot'), ('̄', 'overline'),
    ('́', 'acute'), ('̀', 'grave'), ('̌', 'check'), ('̆', 'breve'), ('̊', 'mathring'),
    ('⏜', 'wideparen'), ('̑', 'wideparen')])
def test_known_accents_and_default_keep_the_original_mark(char, command):
    output, diagnostics = convert(accent(char))
    assert output == '\\' + command + '{AB}'
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('char', ['★', '⏟', '\uef04', '', 'future'])
def test_unknown_accents_never_default_to_a_dot_or_vector(char):
    output, diagnostics = convert(accent(char))
    assert output == '{AB}' and '待核对' not in output
    assert r'\dot' not in output and r'\vec' not in output
    assert any(token.startswith('accent:') for token in diagnostics['unsupported_omml_tags'])


@pytest.mark.parametrize('kind,expected', [(None, r'\dfrac{a+b}{c+d}'),
    ('bar', r'\dfrac{a+b}{c+d}'), ('noBar', r'\genfrac{}{}{0pt}{}{a+b}{c+d}'),
    ('skw', r'\sfrac{a+b}{c+d}'), ('lin', r'{a+b}/{c+d}')])
def test_fraction_type_changes_no_other_operand(kind, expected):
    output, diagnostics = convert(fraction(kind))
    assert output == expected
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('kind', ['future', '', 'NoBar'])
def test_unknown_fraction_type_cannot_become_division(kind):
    output, diagnostics = convert(fraction(kind))
    assert output == '' and r'\frac' not in output and r'\dfrac' not in output
    assert diagnostics['unsupported_omml_tags'] == ['fractionType:' + kind]


@pytest.mark.parametrize('begin,end,left,right', [
    ('⟨', '⟩', r'\langle', r'\rangle'), ('〈', '〉', r'\langle', r'\rangle'),
    ('‖', '‖', r'\Vert', r'\Vert'), ('∥', '∥', r'\Vert', r'\Vert'),
    ('⌊', '⌋', r'\lfloor', r'\rfloor'), ('⌈', '⌉', r'\lceil', r'\rceil'),
    ('{', '}', r'\{', r'\}'), ('(', ']', '(', ']'), (']', '[', ']', '['),
    ('', '', '.', '.'), ('(', '', '(', '.')])
def test_known_and_explicitly_invisible_delimiters_keep_their_identity(begin, end, left, right):
    output, diagnostics = convert(delimiter(begin, end))
    assert output == (r'\left' + left + (' ' if left[-1:].isalpha() else '')
                      + 'x' + r'\right' + right + (' ' if right[-1:].isalpha() else ''))
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('begin,end', [('★', ')'), ('(', 'future'), ('⟦', '⟧')])
def test_unknown_delimiters_remain_pending_only_in_diagnostics(begin, end):
    output, diagnostics = convert(delimiter(begin, end))
    assert output == '{x}' and '待核对' not in output
    assert any(token.startswith('delimiter:') for token in diagnostics['unsupported_omml_tags'])


def test_array_has_no_brace_unless_the_source_has_an_outer_delimiter():
    bare, diagnostics = convert(array('x+y=3', 'x-y=1'))
    assert bare == r'\begin{aligned} x+y=3 \\ x-y=1 \end{aligned}'
    assert not diagnostics.get('unsupported_omml_tags')
    braced, diagnostics = convert(delimiter('{', '', array('x+y=3', 'x-y=1')))
    assert braced == r'\left\{' + bare + r'\right.'
    assert r'\begin{cases}' not in braced
    assert not diagnostics.get('unsupported_omml_tags')


def test_array_multi_point_spacing_is_not_certified_by_simple_alignment():
    _, diagnostics = convert(array('x&amp;&amp;y=1', 'x&amp;y=2'))
    assert 'equationArray:multipleAlignmentPoints' in diagnostics['unsupported_omml_tags']


def phantom(props=''):
    return '<m:phant><m:phantPr>' + props + '</m:phantPr><m:e>' + run('x') + '</m:e></m:phant>'


@pytest.mark.parametrize('props,expected', [('', 'x'), ('<m:show/>', 'x'),
    ('<m:show m:val="1"/>', 'x'), ('<m:show m:val="false"/>', r'\phantom{x}'),
    ('<m:show m:val="0"/><m:zeroWid/>', r'\vphantom{x}'),
    ('<m:show m:val="0"/><m:zeroAsc/><m:zeroDesc/>', r'\hphantom{x}'),
    ('<m:show m:val="0"/><m:zeroWid/><m:zeroAsc/><m:zeroDesc/>', r'\phantom{}')])
def test_phantom_show_and_complete_supported_dimensions(props, expected):
    output, diagnostics = convert(phantom(props))
    assert output == expected
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('props', ['<m:show m:val="future"/>', '<m:show m:val="0"/><m:zeroAsc/>',
                                  '<m:zeroWid/>', '<m:futurePr/>'])
def test_unhandled_phantom_visibility_or_dimensions_keep_review(props):
    _, diagnostics = convert(phantom(props))
    assert diagnostics['unsupported_omml_tags']


def style(script=None, sty=None, extras=''):
    props = (f'<m:scr m:val="{script}"/>' if script is not None else '')
    props += (f'<m:sty m:val="{sty}"/>' if sty is not None else '') + extras
    return '<m:r><m:rPr>' + props + '</m:rPr><m:t>R</m:t></m:r>'


@pytest.mark.parametrize('script,sty,expected', [(None, None, 'R'), (None, 'p', r'\mathrm{R}'),
    (None, 'i', r'\mathit{R}'), (None, 'b', r'\mathbf{R}'), (None, 'bi', r'\boldsymbol{R}'),
    ('double-struck', 'p', r'\mathbb{R}'), ('fraktur', 'p', r'\mathfrak{R}'),
    ('script', 'p', r'\mathcal{R}'), ('sans-serif', 'p', r'\mathsf{R}'),
    ('monospace', 'p', r'\mathtt{R}')])
def test_explicit_supported_math_alphabet_and_style(script, sty, expected):
    output, diagnostics = convert(style(script, sty))
    assert output == expected
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('props', [('future', 'p', ''), ('roman', 'future', ''),
    ('double-struck', 'b', ''), (None, None, '<m:nor/>'), (None, None, '<m:lit/>'),
    (None, None, '<m:futurePr/>'), (None, None, '<m:nor m:val="future"/>')])
def test_unproved_alphabet_or_text_properties_remain_pending(props):
    _, diagnostics = convert(style(*props))
    assert diagnostics['unsupported_omml_tags']


@pytest.mark.parametrize('body', [accent('→', '<m:futurePr/>'), fraction('bar', '<m:futurePr/>'),
                                delimiter('(', ')', props='<m:futurePr/>')])
def test_new_properties_do_not_bypass_the_review_boundary(body):
    _, diagnostics = convert(body)
    assert diagnostics['unsupported_omml_tags']


def test_unknown_private_math_character_stays_unknown_with_a_known_style():
    _, diagnostics = convert(style('double-struck', 'p').replace('>R<', '>\uef04<'))
    assert 'privateUse:U+EF04' in diagnostics['unsupported_omml_tags']


def docx(body):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:m="{M}"><w:body><w:p><w:r><w:t>1. 已知</w:t></w:r><m:oMath>{body}</m:oMath><w:r><w:t>，求值。</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


@pytest.mark.parametrize('body', [accent('★'), fraction('future'), delimiter('★', ')'),
                                 phantom('<m:show m:val="future"/>'), style('future', 'p')])
def test_unknown_semantics_are_recorded_at_the_real_docx_block_and_cannot_certify(tmp_path, body, monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *_a, **_k: pytest.fail('No provider requests'))
    result = extract_docx_markdown(docx(body), output_dir=tmp_path, url_prefix='/isolated/omml', include_source_review_evidence=True)
    assert result['success'] and result['diagnostics']['review_required'] == 1
    assert result['diagnostics']['omml_unsupported'] == 1
    assert result['diagnostics']['native_missing_glyphs'] == 1
    assert '待核对' not in result['markdown'] and '?' not in result['markdown']
    original = result['diagnostics']['unsupported_formula_sources'][0]
    assert original['type'] == 'OMML'
    assert ET.tostring(ET.fromstring(original['source']), encoding='unicode') == original['source']
    proof = verify_source_review_evidence(result['markdown'], result['diagnostics'], result['_source_review_evidence'])
    assert proof['status'] == 'ready' and proof['blocks'][0]['has_risk']
    plan = prepare_word_source_metadata(result['markdown'], result['diagnostics'])
    assert not plan['eligible'] and 'word_extraction_requires_review' in plan['fallback_reasons']


def test_new_supported_shapes_render_with_bundled_preview_without_error_or_fallback():
    bodies = [accent(char) for char in [None, '←', '↔', '̃', '̈', '́', '̀', '̌', '̆', '̊', '⏜']]
    bodies += [fraction(kind) for kind in ['bar', 'noBar', 'skw', 'lin']]
    bodies += [delimiter(a, b) for a, b in [('⟨', '⟩'), ('‖', '‖'), ('⌊', '⌋'), ('⌈', '⌉')]]
    bodies += [array('x+y=3', 'x-y=1'), delimiter('{', '', array('x+y=3', 'x-y=1')),
               phantom('<m:show m:val="0"/>'), phantom('<m:show m:val="0"/><m:zeroWid/>')]
    bodies += [style(script, 'p') for script in ['double-struck', 'fraktur', 'script', 'sans-serif', 'monospace']]
    formulas = [convert(body)[0] for body in bodies]
    run_preview_script('const formulas = ' + json.dumps(formulas, ensure_ascii=False) + r''';
for (const formula of formulas) {
    const html = katex.renderToString(formula, {throwOnError: true, strict: 'error', trust: false});
    assert(!html.includes('katex-error') && !html.includes('_fallback'), formula);
}
''')


UNICODE_OPERATORS = [
    ('∁', r'\complement'), ('∋', r'\ni'), ('∓', r'\mp'), ('∖', r'\setminus'),
    ('∗', r'\ast'), ('∘', r'\circ'), ('∣', r'\mid'), ('∤', r'\nmid'),
    ('∦', r'\nparallel'), ('∬', r'\iint'), ('∭', r'\iiint'), ('∮', r'\oint'),
    ('⊄', r'\not\subset'), ('⊅', r'\not\supset'), ('⊈', r'\nsubseteq'), ('⊉', r'\nsupseteq'),
    ('⊕', r'\oplus'), ('⊖', r'\ominus'), ('⊗', r'\otimes'), ('⊘', r'\oslash'),
    ('⊙', r'\odot'), ('⋅', r'\cdot'), ('⋮', r'\vdots'), ('⋯', r'\cdots'), ('⋱', r'\ddots'),
]


@pytest.mark.parametrize('unicode,command', UNICODE_OPERATORS)
def test_semantic_unicode_operator_is_correct_through_the_shared_finalizer(unicode, command):
    output, diagnostics = convert(run('A' + unicode + 'B'))
    assert output == 'A' + command + ' B'
    assert normalize_word_formula_latex(output) == output
    assert not diagnostics.get('unsupported_omml_tags')
    assert not diagnostics.get('control_word_boundaries_repaired')
    # The same complete command is used by the other MathType backend.
    assert normalize_word_formula_latex(command) == command


def test_ascending_dots_never_become_descending_dots():
    output, diagnostics = convert(run('A⋰B'))
    assert r'\ddots' not in output
    assert output == 'AB' and '待核对' not in output
    assert 'symbol:U+22F0' in diagnostics['unsupported_omml_tags']


@pytest.mark.parametrize('command', ['left', 'right', 'middle', 'dfrac', 'genfrac', 'sfrac',
    'vec', 'overleftarrow', 'overleftrightarrow', 'hat', 'widetilde', 'dot', 'ddot',
    'acute', 'grave', 'breve', 'check', 'mathring', 'wideparen', 'mathrm', 'mathit',
    'mathbf', 'boldsymbol', 'mathbb', 'mathcal', 'mathfrak', 'mathsf', 'mathtt',
    'phantom', 'hphantom', 'vphantom', 'langle', 'rangle', 'Vert', 'lfloor',
    'rfloor', 'lceil', 'rceil', 'backslash', 'begin', 'end', 'centerdot', 'cdots'])
def test_complete_generated_commands_are_not_prefix_repaired(command):
    token = '\\' + command
    diagnostics = {}
    assert normalize_word_formula_latex(token, diagnostics) == token
    assert not diagnostics.get('control_word_boundaries_repaired')


def test_complete_command_protection_keeps_the_verified_variable_boundary_repairs():
    diagnostics = {}
    assert normalize_word_formula_latex(r'\cdots+\capB+\cdotA', diagnostics) == r'\cdots+\cap B+\cdot A'
    assert diagnostics['control_word_boundaries_repaired'] == 2


def test_upright_standard_limit_keeps_operator_limit_behavior():
    base = '<m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>lim</m:t></m:r>'
    output, diagnostics = convert('<m:limLow><m:e>' + base + '</m:e><m:lim>' + run('x→0') + '</m:lim></m:limLow>')
    assert output == r'\lim_{x\to 0}'
    assert not diagnostics.get('unsupported_omml_tags')


def test_zero_size_hidden_formula_does_not_become_an_unknown_docx_placeholder(tmp_path):
    body = phantom('<m:show m:val="0"/><m:zeroWid/><m:zeroAsc/><m:zeroDesc/>')
    result = extract_docx_markdown(docx(body), output_dir=tmp_path, url_prefix='/isolated/omml')
    assert result['success'] and r'\phantom{}' in result['markdown']
    assert '待核对' not in result['markdown']
    assert result['diagnostics']['review_required'] == 0


def numeric_run(value='12', extras='', font=''):
    word_props = f'<w:rPr><w:rFonts w:ascii="{font}"/></w:rPr>' if font else ''
    return '<m:r><m:rPr><m:sty m:val="p"/>' + extras + '</m:rPr>' + word_props + '<m:t>' + value + '</m:t></m:r>'


def convert_word_props(body):
    diagnostics = {}
    element = ET.fromstring(f'<m:oMath xmlns:m="{M}" xmlns:w="{W}">{body}</m:oMath>')
    return omml_element_to_latex(element, diagnostics), diagnostics


@pytest.mark.parametrize('value', ['12', '12.5', '+1', '≤', '∈', '３'])
@pytest.mark.parametrize('normal', ['', '<m:nor/>'])
def test_upright_numerals_and_known_operators_need_no_redundant_font_wrapper(value, normal):
    output, diagnostics = convert_word_props(numeric_run(value, normal))
    assert r'\mathrm' not in output
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('value,extras,font', [('12', '<m:nor/>', 'Wingdings'),
    ('R', '<m:nor/>', ''), ('1x', '<m:nor/>', ''), ('12', '<m:lit/>', ''),
    ('12', '<m:nor m:val="future"/>', '')])
def test_unproved_normal_font_letter_or_literal_keeps_review(value, extras, font):
    _, diagnostics = convert_word_props(numeric_run(value, extras, font))
    assert diagnostics['unsupported_omml_tags']


def proved_variable():
    return ('<m:r><m:rPr><m:nor/><m:sty m:val="i"/></m:rPr>'
        '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
        '<w:i/><w:iCs/></w:rPr><m:t>m</m:t></m:r>')


def test_native_export_proved_single_variable_preserves_italic_without_spurious_review():
    output, diagnostics = convert_word_props(proved_variable())
    assert output == r'\mathit{m}'
    assert not diagnostics.get('unsupported_omml_tags')


@pytest.mark.parametrize('before,after', [
    ('w:ascii="Times New Roman"', ''), ('w:hAnsi="Times New Roman"', ''),
    ('w:cs="Times New Roman"', ''), ('w:ascii="Times New Roman"', 'w:ascii="Arial"'),
    ('<w:i/>', ''), ('<w:iCs/>', ''), ('<w:i/>', '<w:i w:val="0"/>'),
    ('<m:sty m:val="i"/>', ''), ('<m:sty m:val="i"/>', '<m:sty m:val="p"/>'),
    ('<m:t>m</m:t>', '<m:t>mm</m:t>'), ('<m:t>m</m:t>', '<m:t>α</m:t>'),
    ('<m:nor/>', '<m:nor/><m:lit/>'), ('<w:i/>', '<w:i/><w:b/>'),
])
def test_missing_any_native_variable_proof_or_mixed_literal_keeps_review(before, after):
    _, diagnostics = convert_word_props(proved_variable().replace(before, after))
    assert diagnostics['unsupported_omml_tags']


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_actual_native_word_fraction_roundtrip_keeps_digits_and_proved_variable(tmp_path):
    from mathbank.word_export_helper import build_word_document
    content = r'$\dfrac{1}{2}\le m<\dfrac{3}{4}$'
    data, export = build_word_document('正体数字回读', '', 'exam', [{'question': {
        'id': 1, 'question_type': 'fill_in_blank', 'content': content, 'image_paths': []}, 'score': 5}],
        show_secret=False, show_notice=False)
    assert export['native_formulas'] == 1 and export['fallback_formulas'] == 0
    imported = extract_docx_markdown(data, output_dir=tmp_path, url_prefix='/isolated/roundtrip')
    assert imported['success'] and r'\dfrac{1}{2}' in imported['markdown']
    assert r'\dfrac{3}{4}' in imported['markdown'] and r'\mathit{m}' in imported['markdown']
    assert imported['diagnostics']['review_required'] == 0
