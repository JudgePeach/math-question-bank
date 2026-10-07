"""Source answer events keep complete formatting and exact source ownership."""

import pytest

from mathbank.content_locks import (
    _source_parts, find_source_answer_start, lock_visible_math,
    reconcile_visible_math, strip_source_answer_label,
)


def parts(source):
    _, locks = lock_visible_math(source, "answer-label-test")
    return _source_parts(source, locks)


@pytest.mark.parametrize("label", ["答案：", "解答：", "解：", "解析：", "详解：", "分析："])
def test_plain_role_is_a_line_start_event_and_preserves_all_source_ranges(label):
    source = "1. 已知$f(x)=x^2$，求$f(3)$。\n\n" + label + "$9$。\n2. 给定$y=2$，写出$y$。"
    split = parts(source)
    assert [item.field for item in split] == ["content", "answer_markdown", "content"]
    assert "解析" not in split[0].text and split[1].text.startswith("\n")
    assert strip_source_answer_label(split[1].text) == "$9$。"
    assert "".join(item.text for item in split) == source
    assert all(item.text == source[item.source_start:item.source_end] for item in split)


def test_reference_answer_heading_retains_its_existing_global_section_role():
    source = "1. 已知$x=1$，求$x$。\n参考答案：\n1. $1$。"
    split = parts(source)
    assert [item.field for item in split] == ["content", "extra", "answer_markdown"]
    assert strip_source_answer_label("参考答案：$1$。") == "$1$。"


@pytest.mark.parametrize("wrapper", [r"\textbf{%s}", r"\textit{%s}", r"\textrm{%s}", "**%s**", "__%s__"])
@pytest.mark.parametrize("label", ["【答案】", "【解析】", "解析：", "详解："])
def test_complete_label_only_wrapper_belongs_to_answer_not_stem(wrapper, label):
    formatted = wrapper % label
    source = "1. 已知$f(x)=x^2$，求$f(3)$。\n\n" + formatted + "$9$。"
    split = parts(source)
    assert len(split) == 2 and split[1].field == "answer_markdown"
    assert not split[0].text.endswith((r"\textbf{", r"\textit{", r"\textrm{", "**", "__"))
    assert split[1].text.lstrip().startswith(formatted)
    assert strip_source_answer_label(split[1].text) == "$9$。"
    boundary = find_source_answer_start(source)
    assert boundary is not None and source[boundary.start:boundary.end].strip() == formatted
    assert "".join(item.text for item in split) == source


@pytest.mark.parametrize(("value", "expected"), [
    (r"\textbf{【答案】$9$。}", r"\textbf{$9$。}"),
    (r"\textit{解析：$9$。}", r"\textit{$9$。}"),
    ("**【解析】$9$。**", "**$9$。**"),
    (r"\textbf{\textit{【答案】}}$9$。", "$9$。"),
    (r"\textbf{\textit{【答案】}$9$。}", r"\textbf{$9$。}"),
    (r"\textbf{【答案】}\textit{保留真实强调}$9$。", r"\textit{保留真实强调}$9$。"),
])
def test_only_owned_label_formatting_is_consumed_real_answer_emphasis_survives(value, expected):
    assert strip_source_answer_label(value) == expected


def test_complete_multiline_wrapper_keeps_its_opening_with_the_answer():
    source = "1. 求$f(3)$。\n\\textbf{\n【答案】\n}$9$。"
    split = parts(source)
    assert split[1].text.lstrip().startswith("\\textbf{")
    assert strip_source_answer_label(split[1].text) == "$9$。"
    assert "".join(item.text for item in split) == source


def test_answer_emphasis_keeps_nested_math_groups_and_visible_escaped_braces():
    value = r"\textbf{【答案】$\dfrac{1}{2}$，$\{1,2\}$。}"
    assert strip_source_answer_label(value) == r"\textbf{$\dfrac{1}{2}$，$\{1,2\}$。}"


@pytest.mark.parametrize("label", ["解析：", "详解：", "分析："])
def test_plain_role_never_steals_inline_question_instructions(label):
    source = "1. 请" + label + "已知条件并求$f(3)$。"
    assert find_source_answer_start(source) is None
    assert [item.field for item in parts(source)] == ["content"]
    assert strip_source_answer_label(source) == source


@pytest.mark.parametrize("value", [
    r"$\text{解析：}$", r"\(\text{【答案】}\)", "`【答案】$9$`",
    "```text\n解析：$9$\n```", r"\url{https://example/解析：}",
    r"\verb|【答案】|", r"\detokenize{【答案】}", r"\path{【解析】}",
    "![【答案】](/static/uploads/解析：.png)", "<!--解析：$9$-->",
])
def test_formula_code_url_and_image_label_literals_do_not_create_source_answer(value):
    source = "1. 阅读字面数据：" + value + "，求$f(3)$。"
    assert find_source_answer_start(source) is None
    assert [item.field for item in parts(source)] == ["content"]


@pytest.mark.parametrize("label", ["解析：", "详解：", "分析："])
def test_new_plain_labels_cannot_split_a_table_cell(label):
    source = "1. 按表中给出的说明完成任务。\n\\begin{tabular}{cc}\n甲 & 乙\\\\\n" + label + "数据 & $9$\\\\\n\\end{tabular}"
    assert find_source_answer_start(source) is None
    assert [item.field for item in parts(source)] == ["content"]


@pytest.mark.parametrize("value", [r"\unknown{解析：}$9$", r"\textbf{解析：$9$", "**解析：$9$", "__详解：$9$"])
def test_unknown_or_incomplete_plain_wrapper_does_not_widen_role_recognition(value):
    assert find_source_answer_start(value) is None
    assert strip_source_answer_label(value) == value


def test_explicit_inline_bracket_label_retains_boundary_for_existing_inline_guard():
    source = "1. 求$f(3)$。\\textbf{【答案】}$9$。"
    split = parts(source)
    assert len(split) == 2
    assert split[1].text.startswith(r"\textbf{【答案】}")
    assert source[:split[1].source_start].rsplit("\n", 1)[-1].strip()


def test_plain_analysis_is_never_absorbed_by_option_D():
    source = "1. 已知$f(x)=x^2$，则$f(3)$为（ ）\nA. $3$\nB. $6$\nC. $9$\nD. $12$\n解析：代入得$9$，故选C。"
    split = parts(source)
    assert len(split) == 2 and split[1].field == "answer_markdown"
    assert split[0].text.endswith("D. $12$")
    assert strip_source_answer_label(split[1].text) == "代入得$9$，故选C。"


def test_plain_source_answer_matches_without_discarding_math_or_original_solution():
    source = "1. 已知二次函数$f(x)=x^2$，求$f(3)$。\n解析：把$x=3$代入可得$f(3)=9$。"
    _, locks = lock_visible_math(source, "plain-analysis")
    questions = [{"content": "已知二次函数$f(x)=x^2$，求$f(3)$。",
                  "answer_markdown": "[EXTRACTED_ORIGINAL]把$x=3$代入可得$f(3)=9$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0 and report["unmatched_source"] == []
    assert report["math_locks_missing"] == 0


def test_source_original_marker_inside_body_is_not_consumed_as_role():
    value = r"解析：`[EXTRACTED_ORIGINAL]` 与 \textbf{强调正文}。"
    assert strip_source_answer_label(value) == r"`[EXTRACTED_ORIGINAL]` 与 \textbf{强调正文}。"
