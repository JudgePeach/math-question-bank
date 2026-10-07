"""A Word tabbed A.①/B.②/C.③/D.④ group must not lose its label dots."""
import io
import zipfile

import pytest

from mathbank.ai_json import (
    _complete_choice_circle_ranges, _subquestion_literal_ranges,
    normalize_subquestions_double_newlines,
)
from mathbank.docx_helper import extract_docx_markdown
from mathbank.source_metadata import _canonical_body


@pytest.mark.parametrize("separator", ["\t", "    ", "\n", "\n\n"])
@pytest.mark.parametrize("point", [".", "．", "、"])
def test_four_explicit_circled_options_keep_every_dot_and_reference(separator, point):
    options = separator.join(f"{label}{point} {circle}" for label, circle in zip("ABCD", "①②③④"))
    original = "9. 其中真命题是（ ）\n\n" + options
    result = normalize_subquestions_double_newlines(original)
    assert result == original
    body, answer, reasons, has_choices = _canonical_body(result)
    assert reasons == [] and has_choices and answer == ""
    assert [f"\\item {circle}" in body for circle in "①②③④"] == [True] * 4


def test_circle_protection_is_local_to_labels_not_other_question_substeps():
    original = "9. 判断（ ）\nA. ①成立    B. 普通文字    C. ③成立    D. 普通文字\n\n10.(1)计算；(2)求值"
    result = normalize_subquestions_double_newlines(original)
    assert "A. ①成立    B. 普通文字    C. ③成立    D. 普通文字" in result
    assert "10.\n\n(1) 计算\n\n(2) 求值" in result


@pytest.mark.parametrize("options", [
    "A. ①    B. ②    C. ③",
    "A. ①    B. ②    C. ③    C. ④",
    "A. ①    B. ②    C. ③    D. ④    E. ⑤",
    "A.     B. ②    C. ③    D. ④",
    "提及 A. ①    B. ②    C. ③    D. ④",
    "1. 第一题\nA. ①\n2. 第二题\nB. ②    C. ③    D. ④",
])
def test_incomplete_duplicate_prose_and_cross_question_labels_are_not_proven_options(options):
    assert _complete_choice_circle_ranges(options, _subquestion_literal_ranges(options)) == []


@pytest.mark.parametrize("literal", [
    "$A. ① B. ② C. ③ D. ④$",
    r"\(A. ① B. ② C. ③ D. ④\)",
    "`A. ① B. ② C. ③ D. ④`",
    "```text\nA. ① B. ② C. ③ D. ④\n```",
    "```text\nA. ① B. ②\n\n\nC. ③ D. ④\n```",
    "“A. ① B. ② C. ③ D. ④”",
    "“引用的选项\nA. ①\nB. ②\nC. ③\nD. ④”",
    '"A. ① B. ② C. ③ D. ④"',
    "> A. ① B. ② C. ③ D. ④",
])
def test_math_code_and_quoted_options_remain_literal_and_do_not_certify_options(literal):
    assert _complete_choice_circle_ranges(literal, _subquestion_literal_ranges(literal)) == []
    assert normalize_subquestions_double_newlines(literal) == literal


def test_real_docx_tabbed_choice_paragraph_keeps_plain_xml_labels(tmp_path):
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    options = '<w:r><w:t xml:space="preserve">A. </w:t></w:r><w:r><w:t>①</w:t></w:r>'
    for letter, circle in zip("BCD", "②③④"):
        options += f'<w:r><w:tab/><w:t xml:space="preserve">{letter}. </w:t></w:r><w:r><w:t>{circle}</w:t></w:r>'
    document = f'<w:document xmlns:w="{namespace}"><w:body><w:p><w:r><w:t>9. 下列命题中真命题是（ ）</w:t></w:r></w:p><w:p>{options}</w:p></w:body></w:document>'
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("word/document.xml", document)
    result = extract_docx_markdown(package.getvalue(), output_dir=tmp_path, url_prefix="/static/test_uploads/circle-options")
    assert result["success"] and result["diagnostics"]["review_required"] == 0
    assert result["markdown"].endswith("A. ①    B. ②    C. ③    D. ④")
    assert _canonical_body(result["markdown"])[2:] == ([], True)
