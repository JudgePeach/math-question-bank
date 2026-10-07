"""Visible braces in an OMML text run cannot be certified as TeX grouping."""

from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.omml_helper import omml_element_to_latex
from mathbank.source_metadata import prepare_word_source_metadata


M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def root_text(*runs):
    root = ET.Element("{" + M + "}oMath")
    for value in runs:
        r = ET.SubElement(root, "{" + M + "}r")
        ET.SubElement(r, "{" + M + "}t").text = value
    return root


@pytest.mark.parametrize("text", [
    "A={x|-2≤x<3}", "B={1,2,4}", "{AB}", "x+{1}",
    "A={x|x>0", "x>0}", r"\alpha+A={1,2}",
    r"\frac{1}{2}+{3}", r"\unknown{1}", r"\\frac{1}{2}",
])
def test_unproven_visible_braces_preserve_output_and_require_review(text):
    elem = root_text(text)
    diagnostics = {}
    output = omml_element_to_latex(elem, diagnostics)
    assert output == omml_element_to_latex(elem)
    assert "literalVisibleBraces" in diagnostics["unsupported_omml_tags"]
    assert diagnostics["literal_brace_review_count"] >= 1
    assert "{" in output or "}" in output


@pytest.mark.parametrize("runs", [
    [r"A=\left\{x|-2\le x<3\right\}"], [r"\{1,2\}"],
    [r"\frac{1}{2}"], [r"\dfrac{x+{1}}{2}"], [r"\sqrt[3]{x+1}"],
    [r"\mathbf{a}+x^{2}+a_{n+1}"], [r"\text{R}+\mathbb{R}"],
    [r"\begin{cases}x&x>0\\-x&x<0\end{cases}"],
    [r"\frac", "{1}", "{2}"], [r"\left", r"\{", "x|x>0", r"\right", r"\}"],
    [r"\verb|{x}|"], [r"\detokenize{[EXTRACTED_ORIGINAL]}"],
])
def test_explicit_linear_tex_controls_and_scripts_remain_supported(runs):
    diagnostics = {}
    output = omml_element_to_latex(root_text(*runs), diagnostics)
    assert output == omml_element_to_latex(root_text(*runs))
    assert "literalVisibleBraces" not in diagnostics.get("unsupported_omml_tags", [])


def test_structured_set_delimiters_are_not_literal_text_braces():
    root = root_text("A=")
    delimiter = ET.SubElement(root, "{" + M + "}d")
    properties = ET.SubElement(delimiter, "{" + M + "}dPr")
    ET.SubElement(properties, "{" + M + "}begChr", {"{" + M + "}val": "{"})
    ET.SubElement(properties, "{" + M + "}endChr", {"{" + M + "}val": "}"})
    inner = ET.SubElement(delimiter, "{" + M + "}e")
    inner.append(root_text("x|-2≤x<3")[0])
    diagnostics = {}
    output = omml_element_to_latex(root, diagnostics)
    assert r"\left\{" in output and r"\right\}" in output
    assert not diagnostics.get("unsupported_omml_tags")


def test_a_structured_node_is_a_control_context_barrier():
    root = root_text(r"\frac")
    structured = ET.SubElement(root, "{" + M + "}sSup")
    base = ET.SubElement(structured, "{" + M + "}e")
    base.append(root_text("x")[0])
    power = ET.SubElement(structured, "{" + M + "}sup")
    power.append(root_text("2")[0])
    root.extend(root_text("{1}{2}"))
    diagnostics = {}
    omml_element_to_latex(root, diagnostics)
    assert "literalVisibleBraces" in diagnostics["unsupported_omml_tags"]


def test_word_import_keeps_ambiguous_source_formula_and_cannot_issue_metadata_certificate(tmp_path):
    document = ET.Element("{" + W + "}document")
    body = ET.SubElement(document, "{" + W + "}body")
    paragraph = ET.SubElement(body, "{" + W + "}p")
    run = ET.SubElement(paragraph, "{" + W + "}r")
    ET.SubElement(run, "{" + W + "}t").text = "1. 集合"
    paragraph.append(root_text("A={1,2}"))
    run = ET.SubElement(paragraph, "{" + W + "}r")
    ET.SubElement(run, "{" + W + "}t").text = "，求其元素个数。"
    archive = BytesIO()
    with ZipFile(archive, "w") as z:
        z.writestr("word/document.xml", ET.tostring(document))
    result = extract_docx_markdown(archive.getvalue(), output_dir=tmp_path / "assets")
    assert result["success"]
    assert "A={1,2}" in result["markdown"]
    assert "[公式结构待核对]" not in result["markdown"]
    assert result["diagnostics"]["native_missing_glyphs"] == 1
    assert result["diagnostics"]["omml_unsupported"] == 1
    assert result["diagnostics"]["review_required"] >= 1
    plan = prepare_word_source_metadata(result["markdown"], result["diagnostics"])
    assert not plan["eligible"]


@pytest.mark.parametrize("text", [r"\frac{" + "x" * 20_000 + "}{2}", "x" + "{1}" * 200])
def test_oversized_unescaped_groups_are_reviewed_quickly_without_source_changes(text):
    diagnostics = {}
    output = omml_element_to_latex(root_text(text), diagnostics)
    assert output == text
    assert "literalVisibleBraces" in diagnostics["unsupported_omml_tags"]


def test_oversized_explicitly_escaped_visible_delimiters_need_no_group_parser():
    text = r"\{x\}" * 2000
    diagnostics = {}
    assert omml_element_to_latex(root_text(text), diagnostics) == text
    assert "literalVisibleBraces" not in diagnostics.get("unsupported_omml_tags", [])
