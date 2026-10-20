"""Real DOCX paragraph-style numbering, without service/database/model access."""
from io import BytesIO
import zipfile

import pytest

from mathbank.docx_helper import extract_docx_markdown

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def package(body, styles, numbering=None):
    data = BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
        archive.writestr("word/numbering.xml", numbering or f'''<w:numbering xmlns:w="{W}">
          <w:abstractNum w:abstractNumId="5"><w:lvl w:ilvl="0"><w:start w:val="1"/>
          <w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
          <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/>
          <w:lvlText w:val="(%2)"/><w:pStyle w:val="Subquestion"/></w:lvl></w:abstractNum>
          <w:num w:numId="10"><w:abstractNumId w:val="5"/></w:num>
          <w:num w:numId="11"><w:abstractNumId w:val="5"/></w:num></w:numbering>''')
    return data.getvalue()


def style(identifier, properties="", parent="", default=False):
    return (f'<w:style w:type="paragraph" w:styleId="{identifier}" w:default="{int(default)}">'
            + (f'<w:basedOn w:val="{parent}"/>' if parent else "")
            + f'<w:pPr>{properties}</w:pPr></w:style>')


def paragraph(text, style_id="Question", properties=""):
    return (f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/>{properties}</w:pPr>'
            f'<w:r><w:t>{text}</w:t></w:r></w:p>')


def numprops(num_id=None, level=None):
    return '<w:numPr>' + (f'<w:numId w:val="{num_id}"/>' if num_id is not None else '') + (
        f'<w:ilvl w:val="{level}"/>' if level is not None else '') + '</w:numPr>'


def extract(tmp_path, body, styles):
    result = extract_docx_markdown(package(body, styles), output_dir=tmp_path,
                                   include_source_review_evidence=True)
    assert result["success"], result["error"]
    return result


def test_question_numbers_in_paragraph_style_are_not_lost(tmp_path):
    result = extract(tmp_path, paragraph("已知x=1，求值。") + paragraph("已知y=2，求值。"),
                     style("Question", numprops(10, 0)))
    assert result["markdown"] == "1. 已知x=1，求值。\n\n2. 已知y=2，求值。"
    assert result["diagnostics"]["numbering_converted"] == 2
    assert result["diagnostics"]["review_required"] == 0


def test_numbering_properties_merge_based_on_and_direct_overrides(tmp_path):
    styles = style("Base", numprops(10, 0)) + style("Subquestion", parent="Base")
    body = (paragraph("第一问", "Subquestion") + paragraph("第二问", "Subquestion")
            + paragraph("独立题", properties=numprops(num_id=11, level=0))
            + paragraph("停用", properties=numprops(num_id=0))
            + paragraph("第三问", "Subquestion"))
    result = extract(tmp_path, body, styles)
    assert result["markdown"].split("\n\n") == ["(a) 第一问", "(b) 第二问", "1. 独立题", "停用", "(c) 第三问"]
    assert result["diagnostics"]["review_required"] == 0


def test_abstract_pstyle_selects_level_and_style_ilvl_is_not_mistaken_for_direct_format(tmp_path):
    result = extract(tmp_path, paragraph("按关联级别编号", "Subquestion"),
                     style("Subquestion", numprops(10, 0)))
    assert result["markdown"] == "(a) 按关联级别编号"
    assert result["diagnostics"]["review_required"] == 0


def test_default_paragraph_style_and_unrelated_plain_style(tmp_path):
    styles = style("Question", numprops(10, 0), default=True) + style("Plain")
    body = '<w:p><w:r><w:t>默认题</w:t></w:r></w:p>' + paragraph("普通说明", "Plain")
    result = extract(tmp_path, body, styles)
    assert result["markdown"] == "1. 默认题\n\n普通说明"
    assert result["diagnostics"]["review_required"] == 0


@pytest.mark.parametrize("styles", [style("Question", parent="Missing"),
    style("Question", parent="Base") + style("Base", parent="Question")])
def test_missing_or_cyclic_inheritance_keeps_clean_text_and_source_diagnostic(tmp_path, styles):
    result = extract(tmp_path, paragraph("已知x=1，求值。"), styles)
    assert result["markdown"] == "已知x=1，求值。"
    assert "待核对" not in result["markdown"]
    assert result["diagnostics"]["numbering_unavailable"] == 1
    assert result["diagnostics"]["numbering_review_details"]


def test_unused_bad_style_does_not_add_review_to_plain_text(tmp_path):
    result = extract(tmp_path, paragraph("普通说明", "Plain"),
                     style("Plain") + style("Unused", parent="Missing"))
    assert result["diagnostics"]["review_required"] == 0
    assert result["diagnostics"]["warnings"] == []


def test_direct_complete_numbering_does_not_depend_on_missing_style(tmp_path):
    result = extract(tmp_path, paragraph("明确直接编号", "Missing", numprops(10, 0)), style("Plain"))
    assert result["markdown"] == "1. 明确直接编号"
    assert result["diagnostics"]["review_required"] == 0
