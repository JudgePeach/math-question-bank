"""Real-world declaration/answer roles must retain data or fail closed."""
import json
import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import prepare_word_source_metadata, build_source_metadata_messages, normalize_source_fillin


def plan(source):
    return prepare_word_source_metadata(source, _new_diagnostics())


@pytest.mark.parametrize("header,expected", [
    ("一、选择题：本题共8小题，每小题5分，共40分。在每小题给出的四个选项中，只有一项是符合题目要求的。", "single_choice"),
    ("一、单选题：本题共8小题，每小题5分，共40分. 在每小题给出的选项中，只有一项是符合题目要求的.", "single_choice"),
    ("二、多项选择题：本题共3小题，每小题6分，共18分。在每小题给出的选项中，有多个选项是符合题目要求的，全部选对的得6分，部分选对得部分分，有选错的得0分。", "multi_choice"),
])
def test_full_colon_score_declaration_constrains_source_type(header, expected):
    p = plan(header + "\n1. 比较$x=2$。\nA. $1$\nB. $2$\nC. $3$\nD. $4$")
    assert p["eligible"], p["fallback_reasons"]
    assert p["questions"][0]["explicit_question_type"] == expected
    assert header in p["source"]


@pytest.mark.parametrize("extra", ["设各题均取$a=2$", "各题共用下图", "给出的选项中以下条件共用", "存在未知计分条件"])
def test_score_declaration_cannot_absorb_shared_or_unknown_conditions(extra):
    source = "一、选择题：本题共8小题，每小题5分。" + extra + "\n1. 比较$x$。"
    p = plan(source)
    assert not p["eligible"]
    assert p["source"] == source


@pytest.mark.parametrize("heading", [
    "宁波市2025学年第二学期高考模拟考试\n高三数学试题卷 全解析",
    "金华十校2026年4月高三模拟考试\n数学试题卷",
    "浙江省高考科目考试绍兴市适应性试卷（2026年4月）\n数 学 试 题",
    "台州市2026届高三第二次教学质量评估试题\n数 学",
    "嘉兴市2026年高三教学测试\n数学 试题卷",
])
def test_complete_multi_line_exam_title_context_is_preserved(heading):
    p = plan(heading + "\n1. 求$x$。")
    assert p["eligible"], p["fallback_reasons"]
    assert p["source"].startswith(heading)


def test_publisher_paragraph_is_retained_after_choices_not_inside_d():
    notice = r"\textbf{公众号：（示范高中数学）}"
    source = "1. 已知$x=3$。\nA. $1$\nB. $2$\nC. $3$\nD. $4$\n\n" + notice + "\n\n" + r"\textbf{【答案】}\textbf{C}" + "\n" + r"\textbf{【解析】}代入$x=3$，故选C。"
    p = plan(source)
    assert p["eligible"], p["fallback_reasons"]
    q = p["questions"][0]
    assert q["content"].index(notice) > q["content"].index(r"\end{choices}")
    assert r"\textbf{C}" in q["answer_markdown"]
    assert "$x=3$" in q["answer_markdown"]
    assert notice not in q["answer_markdown"]


@pytest.mark.parametrize("header", [
    "本卷所有参数取正实数的高三数学试卷",
    "本卷所有变量属于实数的高三数学试卷",
    "所有变量均为正数的某中学2026学年期中考试\n高三数学试题",
])
def test_math_assumptions_in_title_shaped_text_are_not_administration(header):
    assert not plan(header + "\n1. 给定参数$a$，写出其取值范围。")["eligible"]


def test_publisher_cannot_move_a_continuation_of_option_d_outside_choices():
    source = "1. 判断。\nA. $1$\nB. $2$\nC. $3$\nD. 条件如下。\n公众号：（示范高中数学）\n还必须满足$x>5$。\n【答案】D。"
    p = plan(source)
    assert not p["eligible"]
    assert "publisher_with_option_continuation" in p["fallback_reasons"]


@pytest.mark.parametrize("env", ["tabular", "tabular*", "tabularx", "longtable", "tblr", "longtblr", "talltblr"])
def test_cross_question_answer_summary_table_is_never_owned_by_first_question(env):
    source = "1. 求$x$。\n2. 求$y$。\n参考答案：\n1. " + f"\\begin{{{env}}}{{cc}}题号&答案\\\\1&$9$\\\\2&$3$\\\\\\end{{{env}}}"
    assert not plan(source)["eligible"]


def test_long_shared_administration_is_sent_once_without_clipping():
    context = "一、解答题\n" + "\n".join("某校九年级数学试卷" for _ in range(220))
    source = context + "\n" + "\n".join(f"{i}. 已知第{i}个编号参数$a={i}$，写出参数数值。" for i in range(1,31))
    p = plan(source)
    assert p["eligible"], p["fallback_reasons"]
    messages = build_source_metadata_messages(p, {"必修一": {"集合": []}})
    data = json.loads(messages[1]["content"])
    assert len(data["items"]) == 30
    assert context in data["document_context"][0]
    assert all(len(row["section_context"]) < 100 for row in data["items"])
    assert len(messages[1]["content"]) < len(source) * 6


@pytest.mark.parametrize("body", [
    "该函数的最大值为1．若$x=2$，求值。",
    "长与宽之比为3：2. 求长。",
    "可简化为图2：半径为$r$。",
    "一组样本数据依次为0，2，4，5. 关于这组数据求均值。",
])
def test_explicit_mathematical_numeric_roles_do_not_add_fake_question_heads(body):
    p = plan("1. " + body + "\n2. 求$y$。")
    assert p["eligible"], p["fallback_reasons"]
    assert len(p["questions"]) == 2


def test_solution_decimal_and_complex_component_sentences_are_not_question_heads():
    source = "1. 求$z$的虚部。\n解析：所以虚部为1.\n2. 求这组评分均值。\n详解：B组的平均值为9.1."
    p = plan(source)
    assert p["eligible"], p["fallback_reasons"]
    assert all(q["answer_markdown"] for q in p["questions"])


@pytest.mark.parametrize("body", [
    "前题文字2．求$y$。",
    "题号为1．已知$x=2$。",
    "前题叙述图2．新题说明。",
    "前题文字，5. 关于未知新题。",
])
def test_unproven_inline_numbers_still_veto_combined_questions(body):
    assert not plan("1. " + body)["eligible"]


@pytest.mark.parametrize("native", [
    r"\fillin\underline{▲}\fillin",
    r"\underline{不能删除的原文字}",
    r"\underline{数值\textbf{21}与\{可见括号\}}",
    r"\underline{尚未闭合的原文字",
])
def test_source_underline_contents_survive_a_destructive_legacy_fillin_formatter(native):
    import re
    def legacy(value):
        return re.sub(r"\\underline\s*\{[^}]*?\}", lambda _: r"\fillin", value).replace("____", r"\fillin")
    source = "保留 " + native + "，另填 ____。"
    result = normalize_source_fillin(source, legacy)
    assert native in result
    assert result == normalize_source_fillin(result, legacy)
    if native.endswith("}"):
        assert "另填 " + r"\fillin" in result


@pytest.mark.parametrize("native", [
    r"\fillin[x>0]", r"\fillin[2cm][21]", r"\fillin[{x[1]}]{21}",
    r"\textbf{\fillin}", r"\\underline{AB}", r"\\fillin[x>0]",
    r"\verb|x___|", r"\detokenize{x___}", r"\path{q___x}",
    r"\fillin[x>0", r"\fillin[{a]}",
])
def test_source_macro_arguments_and_tex_literals_are_never_erased(native):
    import re
    def legacy(value):
        value = re.sub(r"_{3,}", lambda _: r"\fillin", value)
        value = re.sub(r"\\fillin\s*\[[^\]]*?\](?:\[[^\]]*?\])?", lambda _: r"\fillin", value)
        value = re.sub(r"\\underline\s*\{[^}]*?\}", lambda _: r"\fillin", value)
        return value.replace(r"\fillin}", r"\fillin")
    result = normalize_source_fillin("保留 " + native + "。另填 ____。", legacy)
    assert native in result
    assert result == normalize_source_fillin(result, legacy)


def test_converted_plain_blank_keeps_original_presentation_group_closing():
    def legacy(value):
        return value.replace("____", r"\fillin").replace(r"\fillin}", r"\fillin")
    result = normalize_source_fillin(r"\textbf{____}", legacy)
    assert result == r"\textbf{\fillin}"
