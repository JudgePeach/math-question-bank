"""OOXML numbering semantics through DOCX extraction, no model or service."""
import io
import zipfile

import pytest

from mathbank.docx_helper import extract_docx_markdown

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def paragraph(level, text, num_id=10):
    return f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'


def package(body, levels, nums):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/numbering.xml", f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="5">{levels}</w:abstractNum>{nums}</w:numbering>')
    return output.getvalue()


def level(index, fmt="decimal", text=None, restart=None, start=1):
    return f'<w:lvl w:ilvl="{index}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text or "%" + str(index+1) + "."}"/>' + (f'<w:lvlRestart w:val="{restart}"/>' if restart is not None else '') + '</w:lvl>'


def num(num_id=10, override=""):
    return f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="5"/>{override}</w:num>'


def extract(body, levels, nums, tmp_path):
    result = extract_docx_markdown(package(body, levels, nums), output_dir=tmp_path, url_prefix="/static/test_uploads/docx-numbering")
    assert result["success"]
    return result


@pytest.mark.parametrize("restart,last_number", [(None,1),(0,2),(1,1),(9,1),("invalid",1)])
def test_nested_numbering_restart_and_never_restart(restart, last_number, tmp_path):
    body = ''.join(paragraph(l, t) for l,t in [(0,"Parent one"),(1,"Child one"),(0,"Parent two"),(1,"Child two")])
    result = extract(body, level(0) + level(1,restart=restart), num(), tmp_path)
    assert result["markdown"].split("\n\n") == ["1. Parent one", "1. Child one", "2. Parent two", f"{last_number}. Child two"]
    assert result["diagnostics"]["review_required"] == 0


def test_explicit_restart_waits_for_named_level(tmp_path):
    body = ''.join(paragraph(l,t) for l,t in [(0,"Parent one"),(1,"Middle one"),(2,"Leaf one"),(1,"Middle two"),(2,"Leaf two"),(0,"Parent two"),(2,"Leaf three")])
    result = extract(body,level(0)+level(1)+level(2,restart=1),num(),tmp_path)
    assert result["markdown"].split("\n\n") == ["1. Parent one","1. Middle one","1. Leaf one","2. Middle two","2. Leaf two","2. Parent two","1. Leaf three"]


@pytest.mark.parametrize("override,expected", [
    ('<w:lvlOverride w:ilvl="0"><w:startOverride w:val="7"/></w:lvlOverride>', ["7. First","8. Next"]),
    ('<w:lvlOverride w:ilvl="0">' + level(0,"upperLetter",start=7) + '</w:lvlOverride>', ["G. First","H. Next"]),
    ('<w:lvlOverride w:ilvl="0"><w:startOverride w:val="7"/>' + level(0,"upperLetter",start=3) + '</w:lvlOverride>', ["G. First","H. Next"]),
])
def test_start_and_complete_level_overrides_have_instance_precedence(override, expected, tmp_path):
    result = extract(paragraph(0,"First")+paragraph(0,"Next"),level(0),num(override=override),tmp_path)
    assert result["markdown"].split("\n\n") == expected
    assert result["diagnostics"]["review_required"] == 0


def test_full_override_does_not_mutate_other_numbering_instance(tmp_path):
    override = '<w:lvlOverride w:ilvl="0">' + level(0,"upperLetter",start=3) + '</w:lvlOverride>'
    body = paragraph(0,"Letter",10)+paragraph(0,"Decimal",11)+paragraph(0,"Letter next",10)+paragraph(0,"Decimal next",11)
    result = extract(body,level(0),num(10,override)+num(11),tmp_path)
    assert result["markdown"].split("\n\n") == ["C. Letter","1. Decimal","D. Letter next","2. Decimal next"]


def test_overridden_parent_format_used_in_nested_placeholders(tmp_path):
    override = '<w:lvlOverride w:ilvl="0">' + level(0,"upperRoman",start=4) + '</w:lvlOverride>'
    result = extract(paragraph(0,"Parent")+paragraph(1,"Child"),level(0)+level(1,text="%1.%2)"),num(override=override),tmp_path)
    assert result["markdown"].split("\n\n") == ["IV. Parent","IV.1) Child"]


def test_complete_override_can_set_never_restart_for_one_instance(tmp_path):
    override = '<w:lvlOverride w:ilvl="1">' + level(1,"lowerRoman",restart=0,start=3) + '</w:lvlOverride>'
    body = ''.join(paragraph(l,t) for l,t in [(0,"Parent one"),(1,"Child one"),(0,"Parent two"),(1,"Child two")])
    result = extract(body,level(0)+level(1),num(override=override),tmp_path)
    assert result["markdown"].split("\n\n") == ["1. Parent one","iii. Child one","2. Parent two","iv. Child two"]


def test_missing_numbering_definition_still_admits_uncertainty(tmp_path):
    result = extract(paragraph(0,"Unknown",99),level(0),num(),tmp_path)
    assert result["markdown"] == "[自动编号待核对] Unknown"
    assert result["diagnostics"]["numbering_unavailable"] == 1
    assert result["diagnostics"]["review_required"] == 1
