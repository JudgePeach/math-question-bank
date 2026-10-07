"""Font identities, legacy EQ fields and raw MathML must not silently lose math."""

from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from mathbank.docx_helper import extract_docx_markdown, _SYMBOL_FONT_MAP
from mathbank.docx_source_scopes import verify_source_review_evidence
from mathbank.mathml_helper import MathMLUnsupported, mathml_element_to_latex
from mathbank.source_metadata import prepare_word_source_metadata

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
ML = "http://www.w3.org/1998/Math/MathML"


def package(body):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:ml="{ML}"><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


def extract(body, tmp_path):
    blob = package(body)
    result = extract_docx_markdown(blob, output_dir=tmp_path, include_source_review_evidence=True)
    assert result["success"]
    proof = verify_source_review_evidence(result["markdown"], result["diagnostics"],
        result["_source_review_evidence"], source_document_sha256=sha256(blob).hexdigest())
    assert proof["status"] == "ready"
    return result, proof


def assert_clean_but_uncertified(result, proof):
    """A clean draft is still incomplete when its native evidence says so."""
    assert "待核对" not in result["markdown"]
    assert "?" not in result["markdown"] and "\ufffd" not in result["markdown"]
    assert result["diagnostics"]["review_required"] > 0
    assert result["diagnostics"]["native_missing_glyphs"] > 0
    assert proof["blocks"][0]["has_risk"]
    assert not prepare_word_source_metadata(result["markdown"], result["diagnostics"])["eligible"]


@pytest.mark.parametrize("font", ["Arial", "Wingdings", "Unknown", "Cambria Math", "NotSymbol", ""])
def test_private_font_position_never_guesses_symbol_alpha(font, tmp_path):
    result, proof = extract(f'<w:p><w:r><w:t>1. 已知</w:t><w:sym w:font="{font}" w:char="F061"/></w:r></w:p>', tmp_path)
    assert r"\alpha" not in result["markdown"]
    assert result["markdown"] == "1. 已知"
    assert result["diagnostics"]["symbols_converted"] == 0
    assert result["diagnostics"]["symbols_unavailable"] == result["diagnostics"]["review_required"] == 1
    assert_clean_but_uncertified(result, proof)


@pytest.mark.parametrize("code", ["0061", "F061"])
def test_explicit_symbol_font_preserves_known_greek_slots(code, tmp_path):
    result, proof = extract(f'<w:p><w:r><w:sym w:font="Symbol" w:char="{code}"/></w:r></w:p>', tmp_path)
    assert result["markdown"] == r"$\alpha$"
    assert not result["diagnostics"]["review_required"]
    assert not proof["blocks"][0]["has_risk"]


@pytest.mark.parametrize("font,code,expected", [("Arial", "03B1", "α"), ("Cambria Math", "2211", "∑"), ("Times New Roman", "0061", "a")])
def test_explicit_unicode_fonts_keep_actual_unicode(font, code, expected, tmp_path):
    result, _ = extract(f'<w:p><w:r><w:sym w:font="{font}" w:char="{code}"/></w:r></w:p>', tmp_path)
    assert result["markdown"] == expected
    assert not result["diagnostics"]["review_required"]


@pytest.mark.parametrize("font,code", [("Symbol", "F123"), ("Symbol", "0060"), ("Wingdings", "0061"), ("Arial", "D800"), ("Arial", "F0061"), ("", "03B1")])
def test_unknown_font_or_unmapped_font_glyph_is_reviewed(font, code, tmp_path):
    result, proof = extract(f'<w:p><w:r><w:t>1. 原式</w:t><w:sym w:font="{font}" w:char="{code}"/></w:r></w:p>', tmp_path)
    assert result["diagnostics"]["review_required"] == 1
    assert result["markdown"] == "1. 原式"
    assert_clean_but_uncertified(result, proof)
    evidence = result["diagnostics"]["unsupported_formula_sources"][0]
    symbol = ET.fromstring(evidence["source"])
    assert symbol.attrib["{" + W + "}font"] == font
    assert symbol.attrib["{" + W + "}char"] == code


@pytest.mark.parametrize("code,expected", [("F0D8", r"\neg"), ("F0C6", r"\varnothing"),
    ("F022", r"\forall"), ("F024", r"\exists"), ("F047", r"\Gamma"),
    ("F0D7", r"\cdot"), ("F0E5", r"\sum"), ("F0F2", r"\int")])
def test_verified_adobe_symbol_encoding_preserves_operator_identity(code, expected, tmp_path):
    result, _ = extract(f'<w:p><w:r><w:sym w:font="Symbol" w:char="{code}"/></w:r></w:p>', tmp_path)
    assert result["markdown"] == "$" + expected + "$"
    assert not result["diagnostics"]["review_required"]
    if code == "F0D8":
        assert r"\varnothing" not in result["markdown"]


@pytest.mark.parametrize("code", ["F060", "F0BD", "F0BE", "F0E6", "F0F3", "F0F4", "F0F5"])
def test_partial_adobe_symbol_glyph_pieces_are_never_guessed(code, tmp_path):
    result, proof = extract(f'<w:p><w:r><w:t>1. 原式</w:t><w:sym w:font="Symbol" w:char="{code}"/></w:r></w:p>', tmp_path)
    assert result["diagnostics"]["review_required"] == 1
    assert result["diagnostics"]["unsupported_formula_sources"][0]["type"] == "Word font symbol"
    assert_clean_but_uncertified(result, proof)


def test_simple_eq_field_keeps_cached_value_and_exact_instruction(tmp_path):
    result, proof = extract('<w:p><w:r><w:t>1. 求值</w:t></w:r><w:fldSimple w:instr=" EQ \\f(1,2) "><w:r><w:t>缓存结果</w:t></w:r></w:fldSimple></w:p>', tmp_path)
    assert "缓存结果" in result["markdown"]
    assert result["markdown"] == "1. 求值缓存结果"
    assert result["diagnostics"]["equation_fields_unsupported"] == 1
    source = result["diagnostics"]["unsupported_formula_sources"][0]
    assert source["source"] == r" EQ \f(1,2) "
    assert source["sha256"] == sha256(source["source"].encode()).hexdigest()
    assert_clean_but_uncertified(result, proof)


def test_complex_eq_instruction_is_assembled_across_runs(tmp_path):
    body = '<w:p><w:r><w:t>1. 值为</w:t><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> E</w:instrText></w:r><w:r><w:instrText>Q \\r(3,x)</w:instrText><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>√x</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    result, proof = extract(body, tmp_path)
    assert "√x" in result["markdown"] and "\\r" not in result["markdown"]
    assert result["diagnostics"]["equation_fields_unsupported"] == 1
    assert result["diagnostics"]["unsupported_formula_sources"][0]["source"] == r" EQ \r(3,x)"
    assert_clean_but_uncertified(result, proof)


@pytest.mark.parametrize("instruction", ["PAGE", "NUMPAGES", "SEQ Equation", 'IF 1 = 1 "EQ" "no"', "EQUATION", "HIDDEN FIELD"])
def test_ordinary_word_fields_keep_cached_results_without_formula_risk(instruction, tmp_path):
    result, _ = extract(f'<w:p><w:fldSimple w:instr="{instruction.replace(chr(34), "&quot;")}"><w:r><w:t>3</w:t></w:r></w:fldSimple></w:p>', tmp_path)
    assert result["markdown"] == "3"
    assert not result["diagnostics"]["review_required"]


def test_deleted_eq_and_nested_textbox_are_not_counted_twice(tmp_path):
    body = '<w:p><w:del><w:fldSimple w:instr="EQ \\f(a,b)"><w:r><w:t>deleted</w:t></w:r></w:fldSimple></w:del><w:r><w:pict><w:txbxContent><w:p><w:fldSimple w:instr="EQ \\f(c,d)"><w:r><w:t>visible</w:t></w:r></w:fldSimple></w:p></w:txbxContent></w:pict></w:r></w:p>'
    result, proof = extract(body, tmp_path)
    assert "deleted" not in result["markdown"]
    assert "visible" in result["markdown"]
    assert result["diagnostics"]["equation_fields_unsupported"] == 1
    assert_clean_but_uncertified(result, proof)


@pytest.mark.parametrize("xml,expected", [
    ('<mfrac><mi>x</mi><mn>2</mn></mfrac>', r"\dfrac{\mathit{x}}{2}"),
    ('<mfrac linethickness="0"><mi>x</mi><mn>2</mn></mfrac>', r"\genfrac{}{}{0pt}{}{ \mathit{x} }{ 2 }"),
    ('<mroot><mi>x</mi><mn>3</mn></mroot>', r"\sqrt[3]{\mathit{x}}"),
    ('<msubsup><mi>a</mi><mn>1</mn><mn>2</mn></msubsup>', r"{\mathit{a}}_{1}^{2}"),
    ('<mrow><mi mathvariant="double-struck">R</mi><mo>∩</mo><mi>A</mi></mrow>', r"\mathbb{R} \cap  \mathit{A}"),
    ('<mtable><mtr><mtd><mn>1</mn></mtd><mtd><mn>2</mn></mtd></mtr><mtr><mtd><mn>3</mn></mtd><mtd><mn>4</mn></mtd></mtr></mtable>', r"\begin{matrix}1 & 2 \\ 3 & 4\end{matrix}"),
])
def test_basic_mathml_keeps_structure(xml, expected):
    root = ET.fromstring(f'<math xmlns="{ML}">{xml}</math>')
    assert mathml_element_to_latex(root) == expected


def test_inline_mathml_is_not_flattened_or_lost_in_word(tmp_path):
    result, proof = extract('<w:p><w:r><w:t>1. 值为</w:t></w:r><ml:math><ml:mfrac><ml:mi>x</ml:mi><ml:mn>2</ml:mn></ml:mfrac></ml:math><w:r><w:t>。</w:t></w:r></w:p>', tmp_path)
    assert r"\dfrac{\mathit{x}}{2}" in result["markdown"]
    assert result["diagnostics"]["mathml_converted"] == 1
    assert not result["diagnostics"]["review_required"]
    assert not proof["blocks"][0]["has_risk"]


def test_barless_mathml_fraction_adds_no_fences_in_final_word_extraction(tmp_path):
    result, _ = extract('<w:p><w:r><w:t>1. 原式</w:t></w:r><ml:math><ml:mfrac linethickness="0"><ml:mn>1</ml:mn><ml:mn>2</ml:mn></ml:mfrac></ml:math></w:p>', tmp_path)
    assert r"\genfrac{}{}{0pt}{}{ 1 }{ 2 }" in result["markdown"]
    assert r"\binom" not in result["markdown"] and r"\left" not in result["markdown"]
    assert not result["diagnostics"]["review_required"]


@pytest.mark.parametrize("xml", [
    '<math><apply><plus/><ci>x</ci><cn>1</cn></apply></math>',
    '<math><mfrac><mi>x</mi></mfrac></math>',
    '<math><mi>\uefff</mi></math>',
    '<math><mover><mi>x</mi><mo>→</mo></mover></math>',
    '<math><mtable><mtr><mtd><mn>1</mn></mtd></mtr><mtr><mtd><mn>2</mn></mtd><mtd><mn>3</mn></mtd></mtr></mtable></math>',
    '<math><mi mathvariant="unknown">x</mi></math>',
    '<math><mi mathvariant="bold">α</mi></math>',
    '<math><mn>2^3</mn></math>',
    '<math><mi>x</mi><mo>\u2062</mo><mi>y</mi></math>',
    '<math><mo>\u22f0</mo></math>',
    '<math><mfrac linethickness="1px"><mi>x</mi><mn>2</mn></mfrac></math>',
])
def test_unknown_mathml_is_atomic_with_original_xml_evidence(xml, tmp_path):
    xml = xml.replace('<math>', f'<math xmlns="{ML}">')
    result, proof = extract('<w:p><w:r><w:t>1. 原式</w:t></w:r>' + xml + '</w:p>', tmp_path)
    assert result["markdown"] == "1. 原式"
    assert result["diagnostics"]["mathml_unsupported"] == result["diagnostics"]["review_required"] == 1
    evidence = result["diagnostics"]["unsupported_formula_sources"][0]
    original = ET.fromstring(evidence["source"])
    assert original.tag == "{" + ML + "}math"
    assert sha256(evidence["source"].encode()).hexdigest() == evidence["sha256"]
    assert_clean_but_uncertified(result, proof)


def test_mathml_depth_limit_rejects_complete_formula():
    root = ET.fromstring(f'<math xmlns="{ML}">' + '<mrow>' * 66 + '<mn>1</mn>' + '</mrow>' * 66 + '</math>')
    with pytest.raises(MathMLUnsupported, match="安全读取限额"):
        mathml_element_to_latex(root)


def test_omml_normalizer_unknown_tokens_also_force_review(monkeypatch, tmp_path):
    def unknown(latex, diagnostics):
        diagnostics["unsupported_math_tokens"] = ["privateUse:U+F123"]
        return latex
    monkeypatch.setattr("mathbank.docx_helper.normalize_word_formula_latex", unknown)
    result, proof = extract('<w:p><w:r><w:t>1. 原式</w:t></w:r><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>', tmp_path)
    assert result["diagnostics"]["omml_unsupported"] == result["diagnostics"]["review_required"] == 1
    assert result["diagnostics"]["unsupported_omml_tags"] == ["privateUse:U+F123"]
    assert "$x$" in result["markdown"]
    assert result["diagnostics"]["unsupported_formula_sources"][0]["type"] == "OMML"
    assert_clean_but_uncertified(result, proof)


def test_entirely_unknown_symbol_has_no_placeholder_or_fabricated_source_scope(tmp_path):
    blob = package('<w:p><w:r><w:sym w:font="Wingdings" w:char="F061"/></w:r></w:p>')
    result = extract_docx_markdown(blob, output_dir=tmp_path, include_source_review_evidence=True)
    assert result["success"] and result["markdown"] == ""
    assert result["diagnostics"]["symbols_unavailable"] == result["diagnostics"]["native_missing_glyphs"] == 1
    assert result["diagnostics"]["unsupported_formula_sources"][0]["type"] == "Word font symbol"
    proof = verify_source_review_evidence(result["markdown"], result["diagnostics"], result["_source_review_evidence"])
    assert proof["status"] == "uncertain"
    assert "empty_output_review_origin_unlocated" in proof["unlocated_reasons"]
    assert not prepare_word_source_metadata(result["markdown"], result["diagnostics"])["eligible"]
