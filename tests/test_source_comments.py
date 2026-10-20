"""Bounded source-attribution hiding without changing stored question text."""

from copy import deepcopy
import io
import json
import zipfile

from lxml import etree
from PIL import Image
import pytest

from mathbank.paper_helper import clean_content_for_latex
from mathbank.question_duplicates import QuestionDuplicateInput, build_question_fingerprint
from mathbank.source_comments import (
    SOURCE_ATTRIBUTION_PREFIX,
    SOURCE_ATTRIBUTION_SUFFIX,
    strip_source_attribution_comment,
)
from mathbank import word_export_helper as word
from test_math_preview_regressions import run_preview_script


STEM = (
    "$x^2+1$ 首公式之后的正文。\n"
    "![正文图](/static/uploads/visible.png)\n图后正文。\n"
    r"\begin{choices}\item $1$\item $2$\item $3$\item $4$\end{choices}"
)
ATTRIBUTION_BODY = (
    "来源 https://example.invalid/a_b?x=1&y=2；许可 50% & < >。\n"
    r"$unclosed \frac{1}{2} ![隐藏图](/static/uploads/hidden.png) "
    r"\begin{choices}\item 隐藏选项\end{choices}"
)
BLOCK = SOURCE_ATTRIBUTION_PREFIX + ATTRIBUTION_BODY + SOURCE_ATTRIBUTION_SUFFIX


NON_PROTOCOL = (
    "<!-- 普通注释 -->\n\n" + STEM,
    "正文。" + BLOCK + STEM,
    " " + BLOCK + STEM,
    "\n" + BLOCK + STEM,
    "\ufeff" + BLOCK + STEM,
    "```html\n" + BLOCK + "```\n" + STEM,
    "`" + BLOCK + "`\n" + STEM,
    BLOCK.replace("MathBank", "Mathbank", 1) + STEM,
    BLOCK.replace("attribution:\n", "attribution: ", 1) + STEM,
    BLOCK.replace("\n", "\r\n") + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源未闭合\n" + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源-->\n\n" + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源\n-->\n" + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源\n-->" + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源\n-->\n \n" + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源--> 中间\n-->\n\n" + STEM,
    SOURCE_ATTRIBUTION_PREFIX + "来源 <!-- 嵌套\n-->\n\n" + STEM,
)


@pytest.mark.parametrize("content", NON_PROTOCOL)
def test_non_protocol_comments_and_literals_are_byte_preserved(content):
    assert strip_source_attribution_comment(content) == content


def test_exact_head_block_removes_only_one_block_and_its_own_separator():
    assert strip_source_attribution_comment(BLOCK + STEM) == STEM
    assert strip_source_attribution_comment(BLOCK + "\n" + STEM) == "\n" + STEM
    assert strip_source_attribution_comment(BLOCK + BLOCK + STEM) == BLOCK + STEM
    assert strip_source_attribution_comment(BLOCK) == ""
    assert strip_source_attribution_comment("") == ""


def test_latex_export_hides_before_formula_choice_and_image_scanning():
    original = BLOCK + STEM
    decorated = clean_content_for_latex(
        original, q_type="single_choice", preserve_image_positions=True,
    )
    baseline = clean_content_for_latex(
        STEM, q_type="single_choice", preserve_image_positions=True,
    )
    assert decorated == baseline
    assert "$x^2+1$" in decorated
    assert decorated.count(r"\item") == 4
    assert decorated.count(r"\includegraphics") == 1
    assert "hidden.png" not in decorated
    assert original == BLOCK + STEM


def test_word_preparation_uses_a_copy_before_choices_images_and_math(tmp_path):
    Image.new("RGB", (30, 20), "blue").save(tmp_path / "visible.png")
    Image.new("RGB", (30, 20), "red").save(tmp_path / "hidden.png")
    item = {"question": {
        "content": BLOCK + STEM, "question_type": "single_choice",
        "answer_markdown": "答案为 $1$。", "image_paths": [],
    }, "score": 5}
    original = deepcopy(item)
    prepared = word._prepare_question(item, tmp_path)
    assert item == original
    assert prepared.question is not item["question"]
    assert prepared.question["content"] == STEM
    assert prepared.stem.startswith("$x^2+1$")
    assert prepared.choices == ["$1$", "$2$", "$3$", "$4$"]
    assert [path.name for path in prepared.images] == ["visible.png"]
    assert [token.latex for token in word.tokenize_mixed_content(prepared.stem)
            if isinstance(token, word.MathToken)] == ["x^2+1"]
    data, diagnostics = word.build_word_document(
        "来源注释核验", "", "exam", [item], uploads_dir=tmp_path,
        show_secret=False, show_notice=False,
    )
    assert item == original
    assert diagnostics["missing_images"] == diagnostics["failed_formulas"] == 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = etree.fromstring(xml)
    namespace = {"w": word.WORD_NS, "m": word.MATH_NS}
    visible = "".join(root.xpath(".//w:t/text()", namespaces=namespace))
    assert "example.invalid" not in visible
    assert "隐藏" not in visible
    assert len(root.xpath(".//w:drawing", namespaces=namespace)) == 1
    assert "x" in root.xpath(".//m:t/text()", namespaces=namespace)


def test_duplicate_semantics_ignore_block_but_raw_hash_keeps_it():
    baseline = build_question_fingerprint(QuestionDuplicateInput(STEM))
    decorated = build_question_fingerprint(QuestionDuplicateInput(BLOCK + STEM))
    for field in ("canonical_text", "canonical_stem", "ordered_choices", "exact_hash",
                  "critical_math_hash", "content_revision_hash", "figure_count",
                  "choice_count", "simhash_hex", "bands", "text_bands"):
        assert getattr(decorated, field) == getattr(baseline, field), field
    assert decorated.raw_content_hash != baseline.raw_content_hash
    assert decorated.figure_count == 1
    assert decorated.choice_count == 4


def test_js_protocol_matches_python_and_keeps_first_formula_choices_and_anchor():
    cases = [(BLOCK + STEM, STEM), (BLOCK + "\n" + STEM, "\n" + STEM),
             (BLOCK + BLOCK + STEM, BLOCK + STEM), (BLOCK, ""), ("", "")]
    cases.extend((source, source) for source in NON_PROTOCOL)
    run_preview_script(
        "const cases=" + json.dumps(cases, ensure_ascii=False) + ";\n"
        "for (const [source, expected] of cases) {"
        " assert.equal(stripSourceAttributionComment(source), expected); }\n"
        "const stem=" + json.dumps(STEM, ensure_ascii=False) + ";\n"
        "const decorated=" + json.dumps(BLOCK + STEM, ensure_ascii=False) + ";\n"
        "const html=preprocessFormulaForKaTeX(decorated);\n"
        "assert.equal(html,preprocessFormulaForKaTeX(stem));\n"
        "assert.deepEqual(parsedFormulas(html), ['x^2+1','1','2','3','4']);\n"
        "assert.equal((html.match(/class=\"choices-label/g)||[]).length,4);\n"
        "assert.equal((html.match(/<img\\b/g)||[]).length,1);\n"
        "assert(html.includes('/static/uploads/visible.png'));\n"
        "assert(!html.includes('hidden.png'));\n"
        "assert(html.indexOf('<img') < html.indexOf('图后正文'));\n"
        "assert.equal(decorated," + json.dumps(BLOCK + STEM, ensure_ascii=False) + ");\n"
    )
