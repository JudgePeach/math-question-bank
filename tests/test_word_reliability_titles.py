"""Declared multi-line exam headings cannot absorb mathematical conditions."""
import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import prepare_word_source_metadata, apply_source_metadata_response, SourceMetadataContractError, build_source_metadata_messages
import json


@pytest.mark.parametrize("heading", [
    "淮南二中2024-2025学年第一学期期中考试\n高一数学试卷",
    r"\textbf{淮南二中}\textbf{2024-2025}\textbf{学年第一学期期中考试}" + "\n" + r"\textbf{高一数学试卷}",
    "2026北京东城高三一模\n数    学",
    "示范中学2025-2026学年上学期期末考试\n高二数学试题",
])
def test_complete_declared_title_is_preserved_as_document_context(heading):
    source = heading + "\n一、解答题\n1. 已知$x=1$，求$x+1$。"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"], plan["fallback_reasons"]
    assert plan["source"] == source
    assert heading in plan["document_metadata"][0]["text"]
    assert plan["source_ranges"][0]["start"] == 0


@pytest.mark.parametrize("heading", [
    "所有题共用参数a=2\n高一数学试卷",
    "本卷各题共用下图\n数    学",
    "所有变量都是正数\n高一数学试卷",
    "已知函数f(x)=x²\n高一数学试卷",
    "淮南二中2024-2025学年第一学期期中考试\n高一数学试卷\n各题均取a=2",
    "淮南二中2024-2025学年第一学期期中考试\n高一数学试卷\n![](/static/uploads/unknown.png)",
    "这里不是完整的考试声明\n高一数学试卷",
])
def test_unknown_or_shared_mathematical_preamble_is_not_a_title(heading):
    source = heading + "\n1. 求$x$。"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert not plan["eligible"]
    assert plan["source"] == source


def test_multi_line_title_rules_do_not_absorb_mid_paper_material():
    source = "1. 求$x$。\n二、解答题\n淮南二中2024-2025学年第一学期期中考试\n高一数学试卷\n2. 求$y$。"
    assert not prepare_word_source_metadata(source, _new_diagnostics())["eligible"]


@pytest.mark.parametrize("label,expected,opposite", [
    ("单选题", "single_choice", "multi_choice"),
    ("多选题", "multi_choice", "single_choice"),
    ("单项选择题", "single_choice", "multi_choice"),
    ("多项选择题", "multi_choice", "single_choice"),
])
def test_explicit_short_choice_heading_constrains_returned_type(label, expected, opposite):
    source = f"一、{label}\n1. 比较$x=1$。\nA. $1$\nB. $2$\nC. $3$\nD. $4$"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"]
    assert plan["questions"][0]["explicit_question_type"] == expected
    row = {"id": plan["questions"][0]["id"], "question_type": expected,
           "category_compulsory": "必修一", "category_chapter": "集合", "difficulty": "normal"}
    assert apply_source_metadata_response({"items": [row]}, plan, {"必修一": {"集合": []}})[0]["question_type"] == expected
    row["question_type"] = opposite
    with pytest.raises(SourceMetadataContractError):
        apply_source_metadata_response({"items": [row]}, plan, {"必修一": {"集合": []}})


def test_metadata_classification_retains_declared_grade_context_and_allows_analysis():
    heading = "淮南二中2024-2025学年第一学期期中考试\n高一数学试卷"
    source = heading + "\n一、解答题\n1. 已知$f(x)=2^x$，分析函数性质。"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    messages = build_source_metadata_messages(plan, {"必修一": {"指数函数": []}})
    data = json.loads(messages[1]["content"])
    assert heading in data["document_context"][0]
    assert "$f(x)=2^x$" in data["items"][0]["content"]
    assert "允许内部推导与解法分析" in messages[0]["content"]
    assert "不输出新的解答" in messages[0]["content"]
    assert "不得返回 content、answer_markdown" in messages[0]["content"]
