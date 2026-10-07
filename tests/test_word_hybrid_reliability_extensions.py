"""Source declarations/values can be recognized without weakening boundaries."""
import pytest
from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import prepare_word_source_metadata

def plan(source):
    return prepare_word_source_metadata(source,_new_diagnostics())

@pytest.mark.parametrize("title", [
    "2025学年第二学期杭州市高三年级教学质量检测\n数学试卷 全解析",
    "温州市普通高中2026届高三第二次适应性考试\n数学试题卷",
])
def test_complete_declared_quality_exam_headers_keep_source_context(title):
    result = plan(title + "\n1. 求$x$。")
    assert result["eligible"], result["fallback_reasons"]
    assert title in result["document_metadata"][0]["text"]

@pytest.mark.parametrize("text", [
    "故第70百分位数为第6个数字7.", "当$x=2$时取得最大值4．", "该函数取得最小值2．",
])
def test_explicit_value_roles_do_not_introduce_new_question_numbers(text):
    result = plan("1. 求值。\n解析：" + text + "\n2. 求$y$。")
    assert result["eligible"], result["fallback_reasons"]
    assert len(result["questions"]) == 2

def test_exact_three_cases_are_internal_only_with_the_complete_local_declaration():
    text="1. 证明。\n（2）以下三种情况：情况1：$x>0$；情况2：$x=0$；情况3：$x<0$。\n2. 求$y$。"
    result=plan(text)
    assert result["eligible"], result["fallback_reasons"]
    assert len(result["questions"])==2

@pytest.mark.parametrize("body", [
    "情况1：$x>0$；情况2：$x=0$；情况3：$x<0$。",
    "以下三种情况：情况1：$x>0$；情况3：$x<0$。",
    "以下三种情况：情况1：$x>0$；情况2：$x=0$；情况4：$x<0$。",
])
def test_incomplete_or_unproven_cases_remain_ambiguous(body):
    assert not plan("1. " + body)["eligible"]

def test_case_declaration_in_previous_question_cannot_hide_next_question_numbers():
    assert not plan("1. 以下三种情况待说明。\n2. 情况1：$x>0$；情况2：$x=0$；情况3：$x<0$。")["eligible"]

def test_new_exam_pattern_does_not_consume_math_assumptions():
    title="温州市普通高中2026届高三第二次适应性考试\n数学试题卷\n本卷所有参数为正实数"
    assert not plan(title+"\n1. 求$a$。")["eligible"]

def test_explicit_fillin_administration_keeps_type_and_complete_text():
    header="三、填空题（本大题共3小题，每小题5分，共15分。把答案填在题中的横线上.）"
    result=plan(header+"\n12. 求$x$。")
    assert result["eligible"],result["fallback_reasons"]
    assert result["questions"][0]["explicit_question_type"]=="fill_in_blank"
