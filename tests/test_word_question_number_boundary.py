"""Keep a Word outer question number when separating its first subquestion."""
import pytest

from mathbank.ai_json import normalize_subquestions_double_newlines
from mathbank.content_locks import _source_parts, lock_visible_math


@pytest.mark.parametrize("number,marker", [(15, "(1)"), (1, "（1）"), (998, "①")])
def test_outer_heading_dot_keeps_exact_source_question_identity(number, marker):
    original = f"{number}.{marker}计算 $x^2-2x$；(2)求值。\n\n{number + 1}. 已知 $y=3$，求值。"
    result = normalize_subquestions_double_newlines(original)
    assert result.startswith(f"{number}.\n\n{marker} ")
    _, locks = lock_visible_math(result, "wordheading")
    questions = [p for p in _source_parts(result, locks) if p.field == "content"]
    assert [p.number for p in questions] == [number, number + 1]
    assert "$x^2-2x$" in questions[0].text
    assert "$y=3$" in questions[1].text


def test_plain_paragraph_separators_still_separate_subquestions():
    assert normalize_subquestions_double_newlines("先求值.(1)计算") == "先求值\n\n(1) 计算"


def test_existing_heading_and_decimal_are_not_reclassified_as_a_heading():
    assert normalize_subquestions_double_newlines("15.\n\n(1) 计算").startswith("15.\n\n(1)")
    assert normalize_subquestions_double_newlines("1.5.(1)计算") == "1.5\n\n(1) 计算"
