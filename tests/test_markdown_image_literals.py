"""Markdown images use explicit filenames and never rewrite literal examples."""

import pytest

from mathbank.markdown_helper import (
    map_markdown_question_images,
    markdown_image_references,
    prepare_markdown_source,
    prepare_markdown_question_for_storage,
)
from mathbank.question_assets import (
    embedded_question_assets, image_reference_spans, markdown_literal_ranges,
    rewrite_question_asset_paths,
)
from mathbank.content_locks import lock_visible_math, reconcile_visible_math


PNG = "/static/uploads/review-png.png"
JPG = "/static/uploads/review-jpg.jpg"


def mapped(content, mapping, *, answer=""):
    question = {"content": content, "answer_markdown": answer}
    diagnostics = {}
    map_markdown_question_images(question, mapping, diagnostics)
    return question, diagnostics


def test_exact_extension_and_path_win_before_stem_fallback():
    mapping = {"fig.png": PNG, "fig.jpg": JPG, "another/fig.png": "/static/uploads/other.png"}
    question, diagnostics = mapped("![甲](./fig.png)", mapping, answer="![乙](fig.jpg)")
    assert question["content"] == f"![甲]({PNG})"
    assert question["answer_markdown"] == f"![乙]({JPG})"
    assert question["image_paths"] == [PNG, JPG]
    assert not diagnostics
    question, _ = mapped("![图](another/fig.png)", mapping)
    assert question["content"] == "![图](/static/uploads/other.png)"


def test_encoded_relative_filename_and_unique_stem_compatibility():
    question, diagnostics = mapped("![图](images/%E5%9B%BE%20%E4%B8%80.png)", {"图 一.png": PNG})
    assert question["content"] == f"![图]({PNG})" and not diagnostics
    question, diagnostics = mapped("![图](images/fig)", {"fig.png": PNG})
    assert question["image_paths"] == [PNG] and not diagnostics


def test_ambiguous_filename_and_stem_are_not_guessed():
    source = "![图](images/fig.png)"
    question, diagnostics = mapped(source, {"one/fig.png": PNG, "two/fig.png": JPG})
    assert question["content"] == source and not question["image_paths"]
    assert diagnostics["unmapped_images"] == ["images/fig.png"]
    question, diagnostics = mapped("![图](fig)", {"fig.png": PNG, "fig.jpg": JPG})
    assert not question["image_paths"] and diagnostics["unmapped_images"] == ["fig"]


@pytest.mark.parametrize("literal", [
    "    ![示例](fig.png)",
    "\t![示例](fig.png)",
    "```markdown\n![示例](fig.png)\n```",
    "~~~~markdown\n![示例](fig.png)\n~~~~",
    "`![示例](fig.png)`",
    "``示例 ` ![示例](fig.png)``",
    "<pre>![示例](fig.png)</pre>",
    "<CODE class='sample'>![示例](fig.png)</CODE>",
    "<!-- ![示例](fig.png) -->",
])
def test_literal_images_are_neither_rewritten_nor_registered(literal):
    source = literal + "\n\n![真图](fig.png)"
    question, diagnostics = mapped(source, {"fig.png": PNG})
    assert question["content"] == literal + f"\n\n![真图]({PNG})"
    assert question["image_paths"] == [PNG] and not diagnostics
    assert markdown_image_references(literal) == []


@pytest.mark.parametrize("source", [
    "1. 如图\n    ![图](fig.png)",
    "- 如图\n\n    ![图](fig.png)",
    "1. 如图\n    - 第二层\n        ![图](fig.png)",
    "正文紧接下一行\n    ![图](fig.png)",
])
def test_list_or_paragraph_continuation_images_stay_visible(source):
    question, diagnostics = mapped(source, {"fig.png": PNG})
    assert question["content"] == source.replace("fig.png", PNG)
    assert question["image_paths"] == [PNG] and not diagnostics


def test_indented_code_under_a_list_is_protected():
    source = "1. 示例\n\n        - ![示例](fig.png)\n\n2. 如图\n    ![真图](fig.png)"
    question, diagnostics = mapped(source, {"fig.png": PNG})
    assert question["content"] == source.replace("![真图](fig.png)", f"![真图]({PNG})")
    assert question["image_paths"] == [PNG] and not diagnostics


def test_bare_parentheses_never_supply_a_partial_stem_match():
    source = "![图](plot.v1(1).png)"
    mapping = {"plot.v1(1).png": PNG, "plot.png": JPG}
    question, diagnostics = mapped(source, mapping)
    assert question["content"] == source and not question["image_paths"]
    assert diagnostics["unsupported_image_references"]
    assert any("未自动匹配配图" in warning for warning in diagnostics["warnings"])
    prepared = prepare_markdown_source(source)
    assert prepared["diagnostics"]["unsupported_image_references"]
    question, diagnostics = mapped("![图](<plot.v1(1).png>)", mapping)
    assert question["content"] == f"![图](<{PNG}>)" and not diagnostics


def test_unsupported_example_does_not_produce_an_image_warning():
    result = prepare_markdown_source("```markdown\n![图](plot.v1(1).png)\n```")
    assert result["diagnostics"]["referenced_graphics"] == []
    assert "unsupported_image_references" not in result["diagnostics"]


def test_markdown_literal_ranges_cover_formula_examples_and_destinations():
    source = "`$a$`\n\n    $b$\n\n<pre>$c$</pre>\n\n![图](<图(1)$d$.png>)\n\n$x$"
    ranges = markdown_literal_ranges(source)
    for formula in ("$a$", "$b$", "$c$", "$d$"):
        start = source.index(formula)
        assert any(left <= start and start + len(formula) <= right for left, right in ranges)
    start = source.index("$x$")
    assert not any(left <= start < right for left, right in ranges)


def test_existing_lifecycle_defaults_keep_their_comment_and_indent_contract():
    source = f"    ![图]({PNG})\n50% ![注释图]({JPG})"
    assert [path for _, _, path in image_reference_spans(source)] == [PNG]
    assert [path for _, _, path in image_reference_spans(
        source, tex_comments=False, markdown_mode=True,
    )] == [JPG]


@pytest.mark.parametrize("source", ["# **高中数学试卷**", "**高中数学试卷**\n====="])
def test_title_does_not_restore_bold_markers(source):
    assert prepare_markdown_source(source)["title"] == "高中数学试卷"


@pytest.mark.parametrize("example", [
    "`$x$`", "![说明](<$x$.png>)", "<code>$x$</code>",
    "\n\n        $x$\n\n", "\n\n~~~\n$x$\n~~~\n\n",
])
def test_unpaired_dollar_cannot_wrap_or_cut_a_literal_example(example):
    source = "1. 售价 $50，示例 " + example + "，求 $y$。"
    locked, locks = lock_visible_math(source, "MD_BOUNDARY", literal_ranges=markdown_literal_ranges(source))
    assert example in locked
    assert [lock.original for lock in locks] == ["$y$"]
    assert source == "1. 售价 $50，示例 " + example + "，求 $y$。"


@pytest.mark.parametrize("example", ["`$`", r"`\(`", r"<code>\[</code>"])
def test_unclosed_literal_delimiters_cannot_hide_later_real_math(example):
    content = "源码 " + example + r"，求 \(x\)。"
    source = "1. " + content
    locked, locks = lock_visible_math(source, "MD_UNCLOSED", literal_ranges=markdown_literal_ranges(source))
    assert example in locked
    assert [lock.original for lock in locks] == [r"\(x\)"]
    questions = [{"content": content.replace(r"\(x\)", "[[" + locks[0].lock_id + "]]"), "answer_markdown": ""}]
    report = reconcile_visible_math(questions, locks, source, tex_comments=False)
    assert questions[0]["content"] == content and report["math_locks_restored"] == 1


@pytest.mark.parametrize("example", [
    "```txt\n【答案】不是本题答案\n```", "<pre>【答案】不是本题答案</pre>",
    "`【答案】不是本题答案`", "        【答案】不是本题答案",
])
def test_literal_answer_marker_does_not_reclassify_actual_question_formula(example):
    content = "阅读代码：\n\n" + example + "\n\n求 $x$。"
    source = "1. " + content
    _, locks = lock_visible_math(source, "MD_ANSWER", literal_ranges=markdown_literal_ranges(source))
    questions = [{"content": content.replace("$x$", "[[" + locks[0].lock_id + "]]"), "answer_markdown": ""}]
    report = reconcile_visible_math(questions, locks, source, tex_comments=False)
    assert questions[0]["content"] == content
    assert report["math_locks_restored"] == 1
    assert not questions[0].get("source_review", {}).get("required")


def test_storage_percent_escape_protects_math_urls_and_existing_literals():
    protected = (
        r"$50%$ \(20%\) \[30%\] \begin{align}a=40%\end{align} "
        r"`50%` \verb|50%| \detokenize{50%} \url{https://example.invalid/50%} "
        r"\begin{tikzpicture}% literal ![图](/static/uploads/tmp/literal.png)\end{tikzpicture} "
        r"![图](<images/50%图.png>) [链接](https://example.invalid/%25) https://example.invalid/%25 "
        r"已有 50\%"
    )
    source = "比例50%，见图。 " + protected
    question, diagnostics = {"content": source, "answer_markdown": "收益20%。"}, {}
    prepare_markdown_question_for_storage(question, diagnostics)
    assert question["content"] == r"比例50\%，见图。 " + protected
    assert question["answer_markdown"] == r"收益20\%。"
    assert diagnostics["markdown_storage_compatibility"] == {
        "percent_escapes": 2, "code_literals_wrapped": 0, "math_percent_requires_review": 4,
    }
    assert prepare_markdown_source(source)["source"] == source


@pytest.mark.parametrize("formula", [
    r"$x+\detokenize{abc}%$", r"$x+\text{`abc`}%$", r"\(x+\verb|$|%\)",
    "$\n\n    x+50%\n\n$",
    r"$a=50%+\text{https://example.test/r}$",
    r"\(a=50%+\text{https://example.test/r}\)",
    r"\begin{align}a=50%+\text{https://example.test/r}\end{align}",
])
def test_storage_preserves_math_containing_literal_macros_or_text(formula):
    question, diagnostics = {"content": "比例50%，公式 " + formula, "answer_markdown": ""}, {}
    prepare_markdown_question_for_storage(question, diagnostics)
    assert question["content"] == r"比例50\%，公式 " + formula
    assert diagnostics["markdown_storage_compatibility"]["math_percent_requires_review"] == 1


@pytest.mark.parametrize("example", [
    "    ![例图](/static/uploads/tmp/literal.png) 50%\n    ~~~~ $x$",
    "<pre>![例图](/static/uploads/tmp/literal.png) 50%\n~~~~ $x$</pre>",
    "<code>![例图](/static/uploads/tmp/literal.png) 50% `x`</code>",
    "<code>`![例图](/static/uploads/tmp/literal.png)`</code>",
])
def test_storage_literal_examples_remain_inert_during_asset_rewrite(example):
    old, new = "/static/uploads/tmp/visible.png", "/static/uploads/visible.png"
    content = example + f"\n\n比例50%，见 ![真图]({old})。"
    question, diagnostics = {"content": content, "answer_markdown": ""}, {}
    prepare_markdown_question_for_storage(question, diagnostics)
    stored = question["content"]
    assert "/static/uploads/tmp/literal.png" in stored
    if "50%" in example:
        assert "50%" in stored
    assert embedded_question_assets(stored) == [old]
    rewritten = rewrite_question_asset_paths(stored, {old: new, "/static/uploads/tmp/literal.png": "/static/uploads/literal.png"})
    assert "/static/uploads/tmp/literal.png" in rewritten
    assert f"![真图]({new})" in rewritten and "比例50\\%" in rewritten
    assert diagnostics["markdown_storage_compatibility"]["code_literals_wrapped"] == 1
    repeated = dict(question)
    prepare_markdown_question_for_storage(repeated, {})
    assert repeated == question


def test_storage_empty_inline_code_does_not_mask_following_image():
    question, diagnostics = {"content": f"<code></code> ![真图]({PNG})", "answer_markdown": ""}, {}
    prepare_markdown_question_for_storage(question, diagnostics)
    assert embedded_question_assets(question["content"]) == [PNG]
