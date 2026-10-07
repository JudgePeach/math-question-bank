"""Bare sentence-ending zero is distinct from merged question headings."""
import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import prepare_word_source_metadata, _visible_heading_census


@pytest.mark.parametrize("body", [
    "设数列公差均不为0．若$n=3$，求结果。",
    "最大值与最小值之和为0．",
    "表达式的值为0．\n请说明理由。",
])
def test_explicit_zero_value_does_not_advertise_question_zero(body):
    source = "1. " + body + "\n2. 求$x$。"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"], plan["fallback_reasons"]
    assert len(plan["questions"]) == 2
    assert body in plan["questions"][0]["content"]


def test_actual_zero_heading_is_not_ignored_as_a_sentence_value():
    plan = prepare_word_source_metadata("0．求$x$。\n1．求$y$。", _new_diagnostics())
    assert not plan["eligible"]
    assert _visible_heading_census(plan["source"], []) == 2
    assert plan["source"].startswith("0．")


@pytest.mark.parametrize("source", [
    "1. 前一题文字2．已知$x=2$，求值。",
    "1. 文本框题目文字0．已知$x=0$，求值。",
    "1. 设数列第2．求$x$。",
])
def test_merged_or_unknown_inline_heading_evidence_remains_rejected(source):
    assert not prepare_word_source_metadata(source, _new_diagnostics())["eligible"]
