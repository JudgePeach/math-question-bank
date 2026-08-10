# tests/test_docx_import.py
# -*- coding: utf-8 -*-

import io
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from main import app, LOCAL_TOKEN, post_process_pdf_parsed_questions
from mathbank.omml_helper import (
    omml_element_to_latex,
    omml_to_latex,
    normalize_known_word_math_private_characters,
    normalize_word_formula_latex,
    normalize_word_linear_latex_boundaries,
)
from mathbank.docx_helper import extract_docx_markdown

from mathbank.mtef_helper import decode_mtef_formula, mtef_to_latex

client = TestClient(app)


def _mtef_header() -> bytes:
    return b"\x05\x01\x00\x07\x00DSMT7\x00\x00"


def _mtef_char(value: str, *, typeface: int = 3, mtcode: int | None = None) -> bytes:
    code = ord(value) if mtcode is None else mtcode
    return b"\x02\x00" + bytes([typeface + 128]) + struct.pack("<H", code)


def _mtef_line(*records: bytes, null: bool = False) -> bytes:
    if null:
        return b"\x01\x01"
    return b"\x01\x00" + b"".join(records) + b"\x00"


def _mtef_template(selector: int, variation: int, *children: bytes) -> bytes:
    assert 0 <= variation < 128
    return b"\x03\x00" + bytes([selector, variation, 0]) + b"".join(children) + b"\x00"


def _mtef_stream(*records: bytes) -> bytes:
    return _mtef_header() + b"".join(records) + b"\x00"


def test_mtef_decoder_fails_closed_instead_of_guessing():
    """未知 MTEF 二进制不得猜成 i、z 或特定题目公式。"""
    unknown = b"\x05\x01\x00\x07" + bytes(32)
    assert mtef_to_latex(unknown) == ""
    result = decode_mtef_formula(unknown)
    assert result.success is False
    assert result.warning


def test_mtef_decoder_accepts_explicit_latex_annotation():
    payload = b"\x05\x01\x00\x07 MathType LaTeX: \\dfrac{x+1}{2}\x00"
    result = decode_mtef_formula(payload)
    assert result.success is True
    assert result.confidence == "high"
    assert result.latex == "\\dfrac{x+1}{2}"


def test_mtef_decoder_restores_established_compatibility_rules():
    payload = _mtef_header() + b"WinAllCodePages cos x + 3 sin x\x00"
    result = decode_mtef_formula(payload)
    assert result.success is True
    assert result.confidence == "compatibility"
    assert result.latex == "\\cos x + 3\\sin x"


def test_mtef_structural_fraction_and_radical_are_not_guessed_from_32():
    radical = _mtef_template(
        10,
        0,
        _mtef_line(_mtef_char("3", typeface=8)),
        _mtef_line(null=True),
    )
    fraction = _mtef_template(
        11,
        0,
        _mtef_line(radical),
        _mtef_line(_mtef_char("2", typeface=8)),
    )
    result = decode_mtef_formula(_mtef_stream(_mtef_line(fraction)))
    assert result.success is True
    assert result.confidence == "structural"
    assert result.latex == r"\dfrac{\sqrt{3}}{2}"


def test_mtef_structural_plain_32_stays_plain_32():
    payload = _mtef_stream(
        _mtef_line(_mtef_char("3", typeface=8), _mtef_char("2", typeface=8))
    )
    result = decode_mtef_formula(payload)
    assert result.success is True
    assert result.confidence == "structural"
    assert result.latex == "32"


def test_mtef_character_encoding_distinguishes_latin_p_from_pi():
    latin = decode_mtef_formula(_mtef_stream(_mtef_line(_mtef_char("p"))))
    greek = decode_mtef_formula(
        _mtef_stream(_mtef_line(_mtef_char("p", typeface=4, mtcode=0x03C0)))
    )
    assert latin.latex == "p"
    assert greek.latex == r"\pi"


def test_mtef_sequence_generically_separates_control_words_from_variables():
    cases = (
        (0x2229, "B", r"\cap B"),
        (0x2225, "n", r"\parallel n"),
        (0x2200, "z", r"\forall z"),
        (0x2203, "w", r"\exists w"),
    )
    for operator_code, variable, expected in cases:
        result = decode_mtef_formula(_mtef_stream(_mtef_line(
            _mtef_char("x", mtcode=operator_code),
            _mtef_char(variable),
        )))
        assert result.success is True
        assert result.latex == expected


def test_mtef_structural_superscript_uses_template_slots():
    superscript = _mtef_template(
        28,
        0,
        _mtef_line(null=True),
        _mtef_line(_mtef_char("2", typeface=8)),
    )
    result = decode_mtef_formula(
        _mtef_stream(_mtef_line(_mtef_char("x"), superscript))
    )
    assert result.latex == "x^{2}"


def test_mtef_equation_native_header_is_unwrapped_before_parsing():
    payload = _mtef_stream(_mtef_line(_mtef_char("x")))
    wrapper = struct.pack("<HIHI4I", 28, 0x00020000, 0, len(payload), 0, 0, 0, 0)
    result = decode_mtef_formula(wrapper + payload)
    assert result.success is True
    assert result.confidence == "structural"
    assert result.latex == "x"


def test_mtef_structural_parser_skips_realistic_definition_records():
    definitions = (
        b"\x13WinAllBasicCodePages\x00"
        b"\x11\x05Times New Roman\x00"
        b"\x08\x01\x00"
        b"\x0a"
    )
    payload = _mtef_header() + definitions + _mtef_line(_mtef_char("x")) + b"\x00"
    result = decode_mtef_formula(payload)
    assert result.success is True
    assert result.confidence == "structural"
    assert result.latex == "x"


def test_mtef_structural_matrix_fence_and_big_operator():
    matrix = (
        b"\x05\x00\x00\x01\x00\x02\x02\x00\x00"
        + _mtef_line(_mtef_char("1", typeface=8))
        + _mtef_line(_mtef_char("2", typeface=8))
        + _mtef_line(_mtef_char("3", typeface=8))
        + _mtef_line(_mtef_char("4", typeface=8))
        + b"\x00"
    )
    fenced_matrix = _mtef_template(1, 3, _mtef_line(matrix))
    lower = _mtef_line(
        _mtef_char("i"),
        _mtef_char("="),
        _mtef_char("1", typeface=8),
    )
    summation = _mtef_template(
        16,
        3,
        _mtef_line(fenced_matrix),
        _mtef_line(_mtef_char("n")),
        lower,
        _mtef_char("S", typeface=6, mtcode=0x2211),
    )
    result = decode_mtef_formula(_mtef_stream(_mtef_line(summation)))
    assert result.success is True
    assert result.confidence == "structural"
    assert result.latex == (
        r"\sum_{i=1}^{n}{\left(\begin{matrix}1 & 2 \\ 3 & 4\end{matrix}\right)}"
    )


def test_omml_to_latex_conversions():
    """测试 OMML 常见数学结构转换至 LaTeX 语法"""
    # 1. 分数
    frac_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:f><m:num><m:r><m:t>a+b</m:t></m:r></m:num><m:den><m:r><m:t>c+d</m:t></m:r></m:den></m:f></m:oMath>"""
    assert "\\dfrac{a+b}{c+d}" in omml_to_latex(frac_xml)

    # 2. 根号
    rad_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:rad><m:deg/><m:e><m:r><m:t>x^2+1</m:t></m:r></m:e></m:rad></m:oMath>"""
    assert "\\sqrt{x^2+1}" in omml_to_latex(rad_xml)

    # 3. 矢量箭头
    acc_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:acc><m:accPr><m:chr m:val="→"/></m:accPr><m:e><m:r><m:t>AB</m:t></m:r></m:e></m:acc></m:oMath>"""
    assert "\\vec{AB}" in omml_to_latex(acc_xml)

    # 4. 方程组
    cases_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:eqArr><m:e><m:r><m:t>x+y=3</m:t></m:r></m:e><m:e><m:r><m:t>x-y=1</m:t></m:r></m:e></m:eqArr></m:oMath>"""
    assert "\\begin{cases}" in omml_to_latex(cases_xml)
    assert "x+y=3 \\\\ x-y=1" in omml_to_latex(cases_xml)

    # 5. 上划线与下极限不得压平成普通文字
    bar_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:bar><m:e><m:r><m:t>x</m:t></m:r></m:e></m:bar></m:oMath>"""
    assert omml_to_latex(bar_xml) == "\\overline{x}"
    limit_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:limLow><m:e><m:r><m:t>lim</m:t></m:r></m:e><m:lim><m:r><m:t>x→0</m:t></m:r></m:lim></m:limLow></m:oMath>"""
    assert omml_to_latex(limit_xml) == "\\lim_{x\\to 0}"


def test_omml_normalizes_verified_word_private_use_vertical_bar():
    private_bar_xml = """
    <m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
      <m:r><m:t>B=\\left\\{x\\left&#xEC07;</m:t></m:r>
      <m:f>
        <m:num><m:r><m:t>x-3</m:t></m:r></m:num>
        <m:den><m:r><m:t>x</m:t></m:r></m:den>
      </m:f>
      <m:r><m:t>&lt;0\\right.\\right\\}，则 A\\capB=</m:t></m:r>
    </m:oMath>
    """
    assert omml_to_latex(private_bar_xml) == (
        r"B=\left\{x\left|\dfrac{x-3}{x}\lt 0\right.\right\}，则 A\cap B="
    )
    assert normalize_known_word_math_private_characters("\uec07x\uec08") == "|x|"


def test_omml_repairs_greedy_linear_control_words_without_touching_real_commands():
    diagnostics = {}
    normalized = normalize_word_linear_latex_boundaries(
        r"A\capB,\cupC,\sinx,\paralleln,\forallz,\existsw,\caption,\infty,\sinh",
        diagnostics,
    )
    assert normalized == (
        r"A\cap B,\cup C,\sin x,\parallel n,\forall z,\exists w,"
        r"\caption,\infty,\sinh"
    )
    assert diagnostics["control_word_boundaries_repaired"] == 6


def test_shared_word_formula_normalizer_covers_mathtype_output_too():
    diagnostics = {}
    normalized = normalize_word_formula_latex(
        "B=\\left\\{x\\left\uec07A\\capB=", diagnostics
    )
    assert normalized == r"B=\left\{x\left|A\cap B="
    assert diagnostics["private_use_symbols_converted"] == 1
    assert diagnostics["control_word_boundaries_repaired"] == 1


def test_shared_word_formula_normalizer_restores_mathtype_absolute_value_pair():
    diagnostics = {}
    normalized = normalize_word_formula_latex(
        "\\left\uec07z-z_{2025}\\right\uec08=1", diagnostics
    )
    assert normalized == r"\left|z-z_{2025}\right|=1"
    assert diagnostics["private_use_symbols_converted"] == 2
    assert "unsupported_math_tokens" not in diagnostics


def test_omml_unknown_private_use_character_is_visible_and_requires_review():
    unknown_xml = """
    <m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
      <m:r><m:t>x&#xEC09;y</m:t></m:r>
    </m:oMath>
    """
    diagnostics = {}
    latex = omml_element_to_latex(ET.fromstring(unknown_xml), diagnostics)
    assert latex == r"x\text{?}y"
    assert diagnostics["unsupported_omml_tags"] == ["privateUse:U+EC09"]


def _create_mock_docx(paragraphs, tables=None) -> bytes:
    """在内存中生成一个用于测试的标准 .docx 字节流"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. [Content_Types].xml
        ct = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
        z.writestr("[Content_Types].xml", ct)

        # 2. word/document.xml
        p_xmls = []
        for p in paragraphs:
            p_xmls.append(f"""<w:p><w:r><w:t>{p}</w:t></w:r></w:p>""")

        tbl_xmls = []
        if tables:
            for tbl in tables:
                rows_xml = []
                for row in tbl:
                    cells_xml = "".join([f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row])
                    rows_xml.append(f"<w:tr>{cells_xml}</w:tr>")
                tbl_xmls.append(f"<w:tbl>{''.join(rows_xml)}</w:tbl>")

        doc_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    {"".join(p_xmls)}
    {"".join(tbl_xmls)}
  </w:body>
</w:document>"""
        z.writestr("word/document.xml", doc_xml)
        z.writestr("word/_rels/document.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""")

    return buf.getvalue()


def _create_docx_package(body_xml: str, rels_xml: str = "", assets=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>""")
        z.writestr("word/document.xml", f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"><w:body>{body_xml}</w:body></w:document>""")
        z.writestr("word/_rels/document.xml.rels", f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels_xml}</Relationships>""")
        for name, content in (assets or {}).items():
            z.writestr(name, content)
    return buf.getvalue()


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_docx_extract_markdown_with_tables():
    """测试 docx 提取段落与表格转换为 Markdown 和 LaTeX tabular"""
    docx_bytes = _create_mock_docx(
        paragraphs=["高中数学模拟试卷", "1. 已知集合 $A = {1, 2}$，求子集个数。"],
        tables=[
            [["类别", "数量"], ["甲", "10"], ["乙", "20"]]
        ]
    )

    res = extract_docx_markdown(docx_bytes)
    assert res["success"] is True
    assert "高中数学模拟试卷" in res["markdown"]
    assert "\\begin{tabular}" in res["markdown"]
    assert "类别 & 数量 \\\\" in res["markdown"]


def test_docx_recursively_keeps_hyperlinks_revisions_and_symbols():
    body = """
    <w:sdt><w:sdtContent><w:p>
        <w:r><w:t>普通文字</w:t></w:r>
        <w:hyperlink><w:r><w:t>链接文字</w:t></w:r></w:hyperlink>
        <w:ins><w:r><w:t>修订新增</w:t></w:r></w:ins>
        <w:del><w:r><w:delText>已删除</w:delText></w:r></w:del>
        <w:r><w:sym w:font="Symbol" w:char="F0B1"/></w:r>
    </w:p></w:sdtContent></w:sdt>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert result["success"] is True
    assert "普通文字链接文字修订新增" in result["markdown"]
    assert "已删除" not in result["markdown"]
    assert "$\\pm$" in result["markdown"]
    assert result["diagnostics"]["symbols_converted"] == 1


def test_docx_reports_verified_private_math_character_conversion():
    body = """
    <w:p><m:oMath>
      <m:r><m:t>B=\\left\\{x\\left&#xEC07;</m:t></m:r>
      <m:f>
        <m:num><m:r><m:t>x-3</m:t></m:r></m:num>
        <m:den><m:r><m:t>x</m:t></m:r></m:den>
      </m:f>
      <m:r><m:t>&lt;0\\right.\\right\\}，则 A\\capB=</m:t></m:r>
    </m:oMath></w:p>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert (
        r"$B=\left\{x\left|\dfrac{x-3}{x}\lt 0\right.\right\}，则 A\cap B=$"
        in result["markdown"]
    )
    assert result["diagnostics"]["omml_private_chars_converted"] == 1
    assert result["diagnostics"]["omml_control_words_repaired"] == 1
    assert result["diagnostics"]["review_required"] == 0


def test_docx_unknown_private_math_character_is_never_silently_preserved():
    body = """
    <w:p><m:oMath><m:r><m:t>x&#xEC09;y</m:t></m:r></m:oMath></w:p>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert r"$x\text{?}y$ [公式结构待核对]" in result["markdown"]
    assert "privateUse:U+EC09" in result["diagnostics"]["unsupported_omml_tags"]
    assert result["diagnostics"]["review_required"] == 1


def test_docx_restores_automatic_question_and_choice_numbering():
    body = """
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="11"/></w:numPr></w:pPr><w:r><w:t>第一道题</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="11"/></w:numPr></w:pPr><w:r><w:t>第二道题</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="12"/></w:numPr></w:pPr><w:r><w:t>选项甲</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="12"/></w:numPr></w:pPr><w:r><w:t>选项乙</w:t></w:r></w:p>
    """
    numbering = """
    <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>
      <w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperLetter"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>
      <w:num w:numId="11"><w:abstractNumId w:val="1"/></w:num>
      <w:num w:numId="12"><w:abstractNumId w:val="2"/></w:num>
    </w:numbering>
    """
    result = extract_docx_markdown(_create_docx_package(
        body, assets={"word/numbering.xml": numbering.encode("utf-8")}
    ))
    assert "1. 第一道题" in result["markdown"]
    assert "2. 第二道题" in result["markdown"]
    assert "A. 选项甲" in result["markdown"]
    assert "B. 选项乙" in result["markdown"]
    assert result["diagnostics"]["numbering_converted"] == 4
    assert result["diagnostics"]["review_required"] == 0


def test_docx_preserves_run_scripts_underlined_blank_and_emphasis():
    body = """
    <w:p>
      <w:r><w:t>x</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>
      <w:r><w:t>+a</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>1</w:t></w:r>
      <w:r><w:t>，答案：</w:t></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve">      </w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:rPr><w:b/></w:rPr><w:t>重点</w:t></w:r>
      <w:r><w:rPr><w:i/></w:rPr><w:t>条件</w:t></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>AB</w:t></w:r>
    </w:p>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert "x$^{2}$+a$_{1}$，答案：\\fillin" in result["markdown"]
    assert r"\textbf{重点}" in result["markdown"]
    assert r"\textit{条件}" in result["markdown"]
    assert r"\underline{AB}" in result["markdown"]
    assert result["diagnostics"]["superscripts_converted"] == 1
    assert result["diagnostics"]["subscripts_converted"] == 1
    assert result["diagnostics"]["underlines_converted"] == 2


def test_docx_keeps_text_inside_word_text_box():
    body = """
    <w:p><w:r><w:pict><w:txbxContent>
      <w:p><w:r><w:t>文本框中的题目条件</w:t></w:r></w:p>
    </w:txbxContent></w:pict></w:r></w:p>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert "文本框中的题目条件" in result["markdown"]


def test_mathtype_failure_keeps_preview_image_and_review_marker(tmp_path):
    body = """
    <w:p><w:r><w:object>
      <v:shape><v:imagedata r:id="rIdPreview"/></v:shape>
      <o:OLEObject ProgID="Equation.DSMT4" r:id="rIdOle"/>
    </w:object></w:r></w:p>
    """
    rels = """
    <Relationship Id="rIdPreview" Target="media/equation.png"/>
    <Relationship Id="rIdOle" Target="embeddings/equation1.bin"/>
    """
    package = _create_docx_package(
        body,
        rels,
        {
            "word/media/equation.png": _tiny_png(),
            "word/embeddings/equation1.bin": b"\x05\x01\x00\x07" + bytes(32),
        },
    )
    result = extract_docx_markdown(
        package,
        output_dir=tmp_path,
        url_prefix="/static/test_uploads/tmp",
        asset_prefix="word_test",
    )
    assert result["success"] is True
    assert "[公式待核对]" in result["markdown"]
    assert "![MathType 公式待核对]" in result["markdown"]
    assert result["diagnostics"]["mtef_fallback_images"] == 1
    assert result["diagnostics"]["review_required"] == 1
    assert len(list(tmp_path.glob("word_test_*.png"))) == 1


def test_docx_mathtype_compatibility_formula_is_converted_without_review_marker():
    body = """
    <w:p><w:r><w:object>
      <o:OLEObject ProgID="Equation.DSMT4" r:id="rIdOle"/>
    </w:object></w:r></w:p>
    """
    rels = '<Relationship Id="rIdOle" Target="embeddings/equation1.bin"/>'
    package = _create_docx_package(
        body,
        rels,
        {
            "word/embeddings/equation1.bin": (
                _mtef_header() + b"WinAllCodePages cos x + 3 sin x\x00"
            ),
        },
    )
    result = extract_docx_markdown(package)
    assert result["success"] is True
    assert "$\\cos x + 3\\sin x$" in result["markdown"]
    assert "[公式待核对]" not in result["markdown"]
    assert result["diagnostics"]["mtef_converted"] == 1
    assert result["diagnostics"]["mtef_compatibility_converted"] == 1
    assert result["diagnostics"]["mtef_structural_converted"] == 0


def test_docx_mathtype_normalizes_private_bar_and_structurally_separates_cap():
    payload = _mtef_stream(_mtef_line(
        _mtef_char("x"),
        _mtef_char("\uec07"),
        _mtef_char("A"),
        _mtef_char("x", mtcode=0x2229),
        _mtef_char("B"),
    ))
    body = """
    <w:p><w:r><w:object>
      <o:OLEObject ProgID="Equation.DSMT4" r:id="rIdOle"/>
    </w:object></w:r></w:p>
    """
    rels = '<Relationship Id="rIdOle" Target="embeddings/equation1.bin"/>'
    package = _create_docx_package(
        body,
        rels,
        {"word/embeddings/equation1.bin": payload},
    )
    result = extract_docx_markdown(package)
    assert result["success"] is True
    assert r"$x|A\cap B$" in result["markdown"]
    assert "\uec07" not in result["markdown"]
    assert result["diagnostics"]["mtef_private_chars_converted"] == 1
    # The MTEF renderer now emits the boundary correctly at source, so the
    # defensive post-normalizer has nothing left to repair.
    assert result["diagnostics"]["mtef_control_words_repaired"] == 0


def test_docx_keeps_plain_word_32_and_structurally_converts_mathtype_fraction():
    radical = _mtef_template(
        10,
        0,
        _mtef_line(_mtef_char("3", typeface=8)),
        _mtef_line(null=True),
    )
    fraction = _mtef_template(
        11,
        0,
        _mtef_line(radical),
        _mtef_line(_mtef_char("2", typeface=8)),
    )
    payload = _mtef_stream(_mtef_line(fraction))
    native_header = struct.pack("<HIHI4I", 28, 0x00020000, 0, len(payload), 0, 0, 0, 0)
    body = """
    <w:p>
      <w:r><w:t>普通 Word 数字 32；MathType 公式：</w:t></w:r>
      <w:r><w:object><o:OLEObject ProgID="Equation.DSMT4" r:id="rIdOle"/></w:object></w:r>
    </w:p>
    """
    rels = '<Relationship Id="rIdOle" Target="embeddings/equation1.bin"/>'
    package = _create_docx_package(
        body,
        rels,
        {"word/embeddings/equation1.bin": native_header + payload},
    )
    result = extract_docx_markdown(package)
    assert "普通 Word 数字 32" in result["markdown"]
    assert r"$\dfrac{\sqrt{3}}{2}$" in result["markdown"]
    assert result["diagnostics"]["mtef_structural_converted"] == 1
    assert result["diagnostics"]["mtef_compatibility_converted"] == 0
    assert result["diagnostics"]["review_required"] == 0


def test_table_grid_span_and_special_characters_are_preserved():
    body = """
    <w:tbl><w:tr>
      <w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>A &amp; B，占 50%</w:t></w:r></w:p></w:tc>
    </w:tr></w:tbl>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert "\\multicolumn{2}" in result["markdown"]
    assert r"A \& B，占 50\%" in result["markdown"]


def test_table_vertical_merge_generates_multirow_without_review_warning():
    body = """
    <w:tbl>
      <w:tr>
        <w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>类别</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>10</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    assert r"\multirow{2}{*}{类别} & 10 \\" in result["markdown"]
    assert r"\cline{2-2}" in result["markdown"]
    assert "\n & 20 \\\\" in result["markdown"]
    assert result["diagnostics"]["tables_review_required"] == 0
    assert result["diagnostics"]["review_required"] == 0


def test_table_plain_text_escapes_all_latex_special_characters_without_breaking_styles():
    body = r"""
    <w:tbl><w:tr><w:tc><w:p>
      <w:r><w:rPr><w:b/></w:rPr><w:t>编号_A</w:t></w:r>
      <w:r><w:t xml:space="preserve"> &amp; 50% $5 {x} 2^3 a~b C:\tmp</w:t></w:r>
    </w:p></w:tc></w:tr></w:tbl>
    """
    result = extract_docx_markdown(_create_docx_package(body))
    markdown = result["markdown"]
    assert r"\textbf{编号\_A}" in markdown
    assert r"\& 50\% \textdollar{}5 \{x\} 2\textasciicircum{}3" in markdown
    assert r"a\textasciitilde{}b C:\textbackslash{}tmp" in markdown


def test_docx_rejects_abnormal_decompression_ratio():
    package = _create_docx_package(
        "<w:p><w:r><w:t>测试</w:t></w:r></w:p>",
        assets={"word/media/suspicious.bin": bytes(11 * 1024 * 1024)},
    )
    result = extract_docx_markdown(package)
    assert result["success"] is False
    assert "压缩比异常" in result["error"]


def test_word_temp_preview_is_attached_to_question_without_duplicate_markup():
    url = "/static/test_uploads/tmp/word_task_formula.png"
    questions = [{
        "content": f"已知 [公式待核对]\n![MathType 公式待核对]({url})",
        "answer_markdown": "",
        "referenced_images": [url],
    }]
    result = post_process_pdf_parsed_questions(questions, "Word 试卷")
    assert result[0]["image_paths"] == [url]
    assert "[公式待核对]" in result[0]["content"]
    assert "![MathType" not in result[0]["content"]


def test_docx_upload_task_api_validation():
    """测试 docx-task 接口对格式与任务排队生命周期的校验"""
    headers = {"X-Local-Token": LOCAL_TOKEN}

    # 1. 拒绝非 .docx 后缀
    bad_res = client.post(
        "/api/upload/docx-task",
        headers=headers,
        files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert bad_res.status_code == 400
    assert "必须为 .docx" in bad_res.json()["message"]

    # 2. 拒绝仅修改后缀、但内容不是 ZIP/DOCX 的伪文件
    fake_docx_res = client.post(
        "/api/upload/docx-task",
        headers=headers,
        files={"file": ("fake.docx", b"not-a-zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert fake_docx_res.status_code == 400
    assert "不是有效的 Word DOCX" in fake_docx_res.json()["message"]

    # 3. 正常接收 .docx 并返回 task_id
    docx_bytes = _create_mock_docx(["2026年浙江名校联考数学试题"])
    ok_res = client.post(
        "/api/upload/docx-task",
        headers=headers,
        files={"file": ("2026_exam.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    )
    assert ok_res.status_code == 200
    task_id = ok_res.json()["task_id"]
    assert task_id is not None

    # 4. 轮询状态接口
    status_res = client.get(f"/api/tasks/{task_id}/status")
    assert status_res.status_code == 200
    assert "status" in status_res.json()
