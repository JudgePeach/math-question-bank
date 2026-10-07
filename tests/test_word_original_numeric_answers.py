"""A source's own scalar answer must not become a discarded model summary."""

import pytest

from mathbank.content_locks import _comparable_answer, lock_visible_math, reconcile_visible_math
from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import prepare_word_source_metadata


@pytest.mark.parametrize("summary", ["21", "1", "-2", "+2", "9.1", "C", "AC"])
def test_original_standalone_summary_is_kept_with_exact_formula_order(summary):
    explanation = f"代入得$f(3)={summary}$。故答案为：{summary}。"
    source = "1. 已知二次函数$f(x)=x^2$，写出$f(3)$。\n\\textbf{【答案】}" + summary + "\n\n\\textbf{【解析】}\n" + explanation
    answer = "[EXTRACTED_ORIGINAL]" + summary + "\n\n\\textbf{【解析】}\n" + explanation
    _, locks = lock_visible_math(source, "source-summary")
    questions = [{"content": "已知二次函数$f(x)=x^2$，写出$f(3)$。", "answer_markdown": answer}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0 and report["unmatched_source"] == []
    assert report["math_locks_missing"] == 0
    assert questions[0]["answer_markdown"] == answer
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"], plan["fallback_reasons"]
    assert plan["questions"][0]["answer_markdown"].startswith(summary + "\n")


@pytest.mark.parametrize("summary", ["21", "1"])
def test_source_numbered_original_answer_keeps_its_summary(summary):
    body = f"由$x={summary}$得到结果。故答案为：{summary}。"
    source_answer = "12. " + summary + "\n\n【解析】" + body
    candidate = "[EXTRACTED_ORIGINAL]" + summary + "\n\n【解析】" + body
    assert _comparable_answer(candidate, source_answer) == candidate


@pytest.mark.parametrize("wrapper", [r"\textbf{%s}", r"\textit{%s}", r"\textrm{%s}", "**%s**", "__%s__"])
def test_source_summary_known_presentation_wrapper_is_not_absence_of_summary(wrapper):
    source = "【答案】" + wrapper % "21" + "\n\n【解析】由$x=21$得到结果。故答案为：21。"
    candidate = "21\n\n【解析】由$x=21$得到结果。故答案为：21。"
    assert _comparable_answer(candidate, source) == candidate


@pytest.mark.parametrize("candidate", ["22", "-21", "+21", "2.1"])
def test_changed_summary_survives_comparison_and_fails_source_check(candidate):
    source = "1. 求$x$。\n【答案】21\n\n【解析】由$x=21$得到结果。故答案为：21。"
    answer = candidate + "\n\n【解析】由$x=21$得到结果。故答案为：21。"
    _, locks = lock_visible_math(source, "different-summary")
    questions = [{"content": "求$x$。", "answer_markdown": answer}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert questions[0]["answer_markdown"] == answer


def test_even_source_conflicting_tail_does_not_authorize_hiding_changed_leading_summary():
    source = "【答案】21\n\n【解析】由$x=21$得到结果。故答案为：1。"
    candidate = "1\n\n【解析】由$x=21$得到结果。故答案为：1。"
    assert _comparable_answer(candidate, source) == candidate


def test_existing_added_model_summary_compatibility_remains_when_source_has_no_summary():
    source = "1. 求$x$。\n【解析】当$x=1$时得到结果。故答案为：1。"
    candidate = "1\n\n当$x=1$时得到结果。故答案为：1。"
    _, locks = lock_visible_math(source, "added-summary")
    questions = [{"content": "求$x$。", "answer_markdown": candidate}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0 and report["math_locks_missing"] == 0


@pytest.mark.parametrize("literal", [
    "`故答案为：21。`", "```text\n故答案为：21。\n```", r"\verb|故答案为：21。|",
    r"\detokenize{故答案为：21。}", r"$\text{故答案为：21。}$",
])
def test_code_or_mathematical_text_cannot_authorize_discarding_model_summary(literal):
    source = "【解析】阅读下面的字面数据：\n" + literal
    candidate = "21\n\n阅读下面的字面数据：\n" + literal
    assert _comparable_answer(candidate, source) == candidate


def test_simple_numeric_math_result_after_visible_conclusion_keeps_old_compatibility():
    source = "【解析】由$x=21$得到结果。故答案为：$21$。"
    candidate = "21\n\n由$x=21$得到结果。故答案为：$21$。"
    compared = _comparable_answer(candidate, source)
    assert len(compared) == len(candidate) and compared != candidate
    assert "$21$" in compared


def test_original_math_summary_stays_locked_and_cannot_be_replaced_by_plain_number():
    source = "1. 求$x$。\n【答案】$21$\n\n【解析】由$x=21$得到结果。故答案为：21。"
    _, locks = lock_visible_math(source, "math-summary")
    good = [{"content": "求$x$。", "answer_markdown": "[EXTRACTED_ORIGINAL]$21$\n\n【解析】由$x=21$得到结果。故答案为：21。"}]
    assert reconcile_visible_math(good, locks, source)["source_review_count"] == 0
    changed = [{"content": "求$x$。", "answer_markdown": "[EXTRACTED_ORIGINAL]21\n\n【解析】由$x=21$得到结果。故答案为：21。"}]
    report = reconcile_visible_math(changed, locks, source)
    assert report["source_review_count"] == 1 and report["math_locks_missing"] >= 1
