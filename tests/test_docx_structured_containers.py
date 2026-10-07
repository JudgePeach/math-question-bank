"""Visible Word container boundaries, exercised through complete DOCX ZIPs."""
import io
import zipfile

import pytest
from PIL import Image

from mathbank.docx_helper import extract_docx_markdown

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_image_bytes = io.BytesIO()
Image.new("RGB", (2, 2), "red").save(_image_bytes, format="PNG")
PNG = _image_bytes.getvalue()
FORMULA = "<m:oMath><m:f><m:num><m:r><m:t>x+1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath>"
IMAGE = '<w:r><w:drawing><a:blip r:embed="img"/></w:drawing></w:r>'
CONTENT = '<w:p><w:r><w:t>VISIBLE A</w:t></w:r>' + FORMULA + IMAGE + '<w:r><w:t>VISIBLE B</w:t></w:r></w:p>'


def package(body):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="doc" Type="' + R + '/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:a="{A}" xmlns:mc="{MC}" xmlns:v="urn:schemas-microsoft-com:vml"><w:body>{body}</w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="img" Type="' + R + '/image" Target="media/image.png"/></Relationships>')
        z.writestr("word/media/image.png", PNG)
    return output.getvalue()


def sdt(content):
    return '<w:sdt><w:sdtPr><w:alias w:val="METADATA ONLY"/></w:sdtPr><w:sdtContent>' + content + '</w:sdtContent></w:sdt>'


def extract(body, tmp_path):
    return extract_docx_markdown(package(body), output_dir=tmp_path, url_prefix="/static/test_uploads/docx-containers")


def assert_visible_once(result):
    assert result["success"]
    for value in ["VISIBLE A", r"\dfrac{x+1}{2}", "VISIBLE B"]:
        assert result["markdown"].count(value) == 1
    assert result["image_count"] == 1
    assert result["markdown"].count("![](") == 1
    assert result["diagnostics"]["review_required"] == 0


@pytest.mark.parametrize("context", ["cell_block_sdt", "cell_custom_xml", "cell_wrapper", "row_wrapper", "nested_wrappers", "body_wrapper", "inline_wrapper"])
def test_structured_containers_keep_visible_text_formula_and_image(context, tmp_path):
    cell = "<w:tc>" + CONTENT + "</w:tc>"
    if context == "cell_block_sdt":
        body = "<w:tbl><w:tr><w:tc>" + sdt(CONTENT) + "<w:p/></w:tc></w:tr></w:tbl>"
    elif context == "cell_custom_xml":
        body = '<w:tbl><w:tr><w:tc><w:customXml w:element="payload">' + CONTENT + '</w:customXml><w:p/></w:tc></w:tr></w:tbl>'
    elif context == "cell_wrapper":
        body = "<w:tbl><w:tr>" + sdt(cell) + "</w:tr></w:tbl>"
    elif context == "row_wrapper":
        body = "<w:tbl>" + sdt("<w:tr>" + cell + "</w:tr>") + "</w:tbl>"
    elif context == "nested_wrappers":
        body = "<w:tbl>" + sdt('<w:customXml w:element="rows"><w:tr>' + sdt('<w:customXml w:element="cells">' + cell + '</w:customXml>') + '</w:tr></w:customXml>') + "</w:tbl>"
    elif context == "body_wrapper":
        body = sdt(CONTENT)
    else:
        body = "<w:p>" + sdt(CONTENT.removeprefix("<w:p>").removesuffix("</w:p>")) + "</w:p>"
    assert_visible_once(extract(body, tmp_path))


def test_container_unwrapping_keeps_combined_merge_topology(tmp_path):
    first = '<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>' + sdt(CONTENT) + '<w:p/></w:tc>'
    continuation = '<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>'
    body = '<w:tbl>' + sdt('<w:tr>' + sdt(first) + '<w:tc><w:p><w:r><w:t>ROW ONE</w:t></w:r></w:p></w:tc></w:tr>') + sdt('<w:tr>' + sdt(continuation) + '<w:tc><w:p><w:r><w:t>ROW TWO</w:t></w:r></w:p></w:tc></w:tr>') + '</w:tbl>'
    result = extract(body, tmp_path)
    assert_visible_once(result)
    assert r"\multicolumn{2}{|c|}{\multirow{2}{*}{VISIBLE A" in result["markdown"]
    assert r"\cline{3-3}" in result["markdown"]
    assert result["markdown"].count("ROW ONE") == 1
    assert result["markdown"].count("ROW TWO") == 1


def test_wrappers_exclude_deleted_rows_cells_and_field_instructions(tmp_path):
    hidden = '<w:tr><w:tc>' + CONTENT.replace("VISIBLE", "DELETED") + '</w:tc></w:tr>'
    visible = '<w:tr><w:tc>' + sdt('<w:p><w:del><w:r><w:delText>DELETED INLINE</w:delText></w:r></w:del><w:r><w:instrText>HIDDEN INSTRUCTION</w:instrText></w:r><w:fldSimple w:instr="HIDDEN FIELD"><w:r><w:t>FIELD RESULT</w:t></w:r></w:fldSimple><w:moveFrom><w:r><w:t>DELETED MOVE</w:t></w:r></w:moveFrom></w:p>') + '</w:tc></w:tr>'
    result = extract('<w:tbl><w:del>' + hidden + '</w:del><w:moveFrom>' + hidden + '</w:moveFrom><w:ins>' + visible + '</w:ins></w:tbl>', tmp_path)
    assert result["success"]
    assert result["markdown"].count("FIELD RESULT") == 1
    for value in ["DELETED", "HIDDEN", "METADATA ONLY"]:
        assert value not in result["markdown"]
    assert result["image_count"] == 0


def test_container_does_not_promote_nested_table_rows(tmp_path):
    nested = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>NESTED TEXT</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    body = '<w:tbl><w:tr><w:tc>' + sdt(nested + '<w:p/>') + '</w:tc><w:tc><w:p><w:r><w:t>RIGHT CELL</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    result = extract(body, tmp_path)
    assert result["success"]
    assert result["markdown"].count(r"\begin{tabular}") == 1
    assert result["markdown"].count("NESTED TEXT") == 1
    assert "NESTED TEXT & RIGHT CELL" in result["markdown"]
    assert result["diagnostics"]["tables_review_required"] == 1


def test_structured_cell_alternate_image_is_selected_once(tmp_path):
    alternate = '<mc:AlternateContent><mc:Choice Requires="a">' + IMAGE + '</mc:Choice><mc:Fallback>' + IMAGE + '</mc:Fallback></mc:AlternateContent>'
    body = '<w:tbl><w:tr><w:tc>' + sdt(CONTENT.replace(IMAGE, alternate)) + '<w:p/></w:tc></w:tr></w:tbl>'
    assert_visible_once(extract(body, tmp_path))


def test_direct_native_math_in_structured_cell_is_not_dropped(tmp_path):
    result = extract('<w:tbl><w:tr><w:tc>' + sdt(FORMULA) + '<w:p/></w:tc></w:tr></w:tbl>', tmp_path)
    assert result["success"]
    assert result["markdown"].count(r"\dfrac{x+1}{2}") == 1
    assert result["diagnostics"]["omml_converted"] == 1


@pytest.mark.parametrize("wrapper", ["sdt", "customXml"])
def test_textbox_structured_blocks_keep_two_question_boundaries(wrapper, tmp_path):
    paragraphs = '<w:p><w:r><w:t>1. First question</w:t></w:r></w:p><w:p><w:r><w:t>2. Second question</w:t></w:r></w:p>'
    wrapped = sdt(paragraphs) if wrapper == "sdt" else '<w:customXml w:element="questions">' + paragraphs + '</w:customXml>'
    body = '<w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>' + wrapped + '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
    result = extract(body, tmp_path)
    assert result["markdown"] == "1. First question\n\n2. Second question"
    assert result["diagnostics"]["review_required"] == 0


def test_textbox_wrapper_ignores_deleted_text_and_metadata(tmp_path):
    hidden_paragraph = '<w:p><w:del><w:r><w:delText>DELETED QUESTION</w:delText></w:r></w:del><w:r><w:instrText>HIDDEN INSTRUCTION</w:instrText></w:r></w:p>'
    paragraphs = '<w:p><w:r><w:t>1. First question</w:t></w:r></w:p>' + hidden_paragraph + '<w:p><w:r><w:t>2. Second question</w:t></w:r></w:p>'
    body = '<w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>' + sdt('<w:customXml w:element="questions">' + paragraphs + '</w:customXml>') + '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
    result = extract(body, tmp_path)
    assert result["markdown"] == "1. First question\n\n2. Second question"
    assert result["image_count"] == 0
