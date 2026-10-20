# -*- coding: utf-8 -*-
"""PDF 本地切块的纯离线单测（只 import mathbank.pdf_local_split，绝不触发 main 抢锁）。"""

import pytest

from mathbank import pdf_local_split as P

GOOD = """一、选择题
1. 已知集合 $A=\\{x|x^2<4\\}$，则
A．$(-2,2)$
B．$(0,2)$
C．$(-2,0)$
D．$(2,4)$
【答案】A
【解析】由 $x^2<4$ 得 $-2<x<2$。
2. 若 $z=1+i$，则 $|z|=$
A．$1$  B．$\\sqrt{2}$  C．$2$  D．$2\\sqrt{2}$
【答案】B
3. 下列函数与 $y=x$ 同一的是
A．$y=\\sqrt{x^2}$  B．$y=(\\sqrt{x})^2$  C．$y=\\dfrac{x^2}{x}$  D．$y=\\sqrt[3]{x^3}$
【答案】D
二、填空题
4. 已知 $\\sin\\alpha=\\dfrac35$，则 $\\tan\\alpha=$ ____。
【答案】$-\\dfrac34$
三、解答题
5. 已知函数 $f(x)=\\sin x+\\cos x$。
（1）求最小正周期；
（2）求最大值。
【答案】（1）$2\\pi$；（2）$\\sqrt2$。
"""


def test_basic_good_paper_trusted():
    plan = P.plan_pdf_local_split(GOOD)
    assert plan.trustworthy is True
    assert plan.cover_ok is True
    assert len(plan.all_blocks) == 5
    assert plan.clean_blocks and not plan.suspicious_blocks


def test_subquestion_parens_and_circled_are_not_anchors():
    plan = P.plan_pdf_local_split(GOOD)
    assert [b.paper_question_no for b in plan.all_blocks] == ["1", "2", "3", "4", "5"]
    q5 = plan.all_blocks[-1]
    assert "（1）" in q5.content and "（2）" in q5.content


def test_leading_answer_summary_dropped():
    src = "参考答案\n\n13.充要条件 14.-1 15.2 16.(2,4)\n\n【参考详解】\n\n" + GOOD
    plan = P.plan_pdf_local_split(src)
    assert plan.trustworthy is True
    assert plan.judge_report.get("dropped_leading_anchors") == ["13"]
    assert [b.paper_question_no for b in plan.all_blocks] == ["1", "2", "3", "4", "5"]


def test_generate_answers_disables_fast_path():
    plan = P.plan_pdf_local_split(GOOD, generate_answers=True)
    assert plan.trustworthy is False
    assert plan.judge_report.get("skipped") == "generate_answers_on"


def test_odd_dollar_flagged_suspicious():
    src = GOOD.replace("则", "则 $", 1)  # 往题1塞一个奇数 $
    plan = P.plan_pdf_local_split(src)
    assert any("G6" in f for b in plan.all_blocks for f in b.risk_flags)


def test_monotonic_break_without_section_header_not_trusted():
    src = "1. 这是第一题\n2. 这是第二题\n3. 这是第三题\n4. 这是第四题\n2. 又出现第二题\n"
    plan = P.plan_pdf_local_split(src)
    assert plan.trustworthy is False
    assert plan.judge_report.get("monotonic_ok") is False


def test_section_header_allows_number_restart():
    src = "一、选择题\n1. 甲甲甲甲 $a$\n2. 乙乙乙乙 $b$\n二、填空题\n1. 丙丙丙丙 $c$\n2. 丁丁丁丁 $d$\n3. 戊戊戊戊 $e$\n"
    plan = P.plan_pdf_local_split(src)
    assert plan.judge_report.get("monotonic_ok") is True


def test_empty_source_is_safe_not_trusted():
    plan = P.plan_pdf_local_split("")
    assert plan.trustworthy is False


def test_lossy_options_flagged_via_gate5():
    src = "1. 求值 A. B. C. D.\n2. 题二正文 $x+1$\n3. 题三正文 $x+2$\n4. 题四正文 $x+3$\n5. 题五正文 $x+4$\n"
    plan = P.plan_pdf_local_split(src)
    assert any("G5" in f for b in plan.all_blocks for f in b.risk_flags)


def test_to_parsed_question_keeps_content_verbatim_and_marks_refine():
    plan = P.plan_pdf_local_split(GOOD)
    b = plan.clean_blocks[0]
    q = b.to_parsed_question(None)
    assert q["content"] == b.content
    assert b.content in GOOD
    assert q["category_chapter"] == ""
    assert "待refine" in q.get("review", "")


@pytest.mark.parametrize("bad", [None, 123, ["x"], ""])
def test_plan_never_raises_on_garbage(bad):
    plan = P.plan_pdf_local_split(bad)
    assert isinstance(plan, P.SplitPlan)
    assert plan.trustworthy is False


# ======================= 回归：原作者反例 1 —— 多选被识别为单选 =======================

MULTI_PAPER = """一、单选题：本题共2小题，每小题5分，共10分。在每小题给出的四个选项中，只有一项是符合题目要求的。
1. 已知 $a=1$，则 $a+1=$
A．$1$  B．$2$  C．$3$  D．$4$
【答案】B
2. 若 $x>0$，则 $x^0=$
A．$0$  B．$1$  C．$x$  D．不存在
【答案】B
二、多选题：本题共5小题，每小题6分，共30分。在每小题给出的选项中，有多项符合题目要求。
3. 下列函数中为奇函数的是
A．$y=x$  B．$y=x^2$  C．$y=x^3$  D．$y=|x|$
【答案】AC
4. 下列不等式成立的是
A．$2>1$  B．$1>2$  C．$3>1$  D．$0>1$
【答案】AC
5. 下列数为正数的是
A．$1$  B．$-1$  C．$2$  D．$-2$
【答案】AC
6. 下列集合为空集的是
A．$\\varnothing$  B．$\\{0\\}$  C．$\\{x|x^2<0\\}$  D．$\\{1\\}$
【答案】AC
7. 下列说法正确的是
A．甲说法正确  B．乙说法正确  C．丙说法正确  D．丁说法正确
【答案】ABD
"""


def test_multi_section_forces_multi_choice_for_all_five():
    plan = P.plan_pdf_local_split(MULTI_PAPER)
    by_no = {b.paper_question_no: b for b in plan.all_blocks}
    assert len(plan.all_blocks) == 7
    for no in ("3", "4", "5", "6", "7"):
        assert by_no[no].forced_type == "multi_choice", no
    assert by_no["1"].forced_type == "single_choice"
    assert plan.judge_report["multi_choice_blocks"] == ["3", "4", "5", "6", "7"]


def test_forced_multi_overrides_model_single_choice():
    plan = P.plan_pdf_local_split(MULTI_PAPER)
    q3 = next(b for b in plan.all_blocks if b.paper_question_no == "3")
    assert q3.to_parsed_question({"question_type": "single_choice"})["question_type"] == "multi_choice"
    assert q3.to_parsed_question(None)["question_type"] == "multi_choice"


@pytest.mark.parametrize("answer", ["【答案】BC", "【解析】由题意，故选：AD", "【答案】A、C", "【解析】略，故选：$\\mathrm{BD}$"])
def test_multi_letter_answer_without_section_declares_multi(answer):
    src = (
        "1. 下列命题正确的是\nA．甲甲  B．乙乙  C．丙丙  D．丁丁\n" + answer + "\n"
        "2. 题二正文 $x+1$\n3. 题三正文 $x+2$\n4. 题四正文 $x+3$\n5. 题五正文 $x+4$\n"
    )
    plan = P.plan_pdf_local_split(src)
    assert plan.all_blocks[0].forced_type == "multi_choice"


def test_multi_stem_hint_declares_multi():
    src = (
        "1. （多选）下列命题正确的是\nA．甲甲  B．乙乙  C．丙丙  D．丁丁\n【答案】A\n"
        "2. 题二正文 $x+1$\n3. 题三正文 $x+2$\n4. 题四正文 $x+3$\n5. 题五正文 $x+4$\n"
    )
    plan = P.plan_pdf_local_split(src)
    assert plan.all_blocks[0].forced_type == "multi_choice"


def test_single_section_with_multi_answer_is_conflict():
    src = MULTI_PAPER.replace("【答案】B\n2.", "【答案】BD\n2.", 1)
    plan = P.plan_pdf_local_split(src)
    q1 = plan.all_blocks[0]
    assert "G10_题型冲突" in q1.risk_flags
    assert q1.forced_type == ""


@pytest.mark.parametrize("answer", ["故选：$AB=2$", "答案为 $A_1$", "【答案】A"])
def test_answer_letters_do_not_misread_formulas_or_single(answer):
    assert len(P._answer_choice_letters(answer)) < 2


# ======================= 回归：原作者反例 2 —— 6 道题被切成 5 块 =======================

def _six(q6_line: str) -> str:
    return (
        "1. 题一正文已知 $x=1$\n2. 题二正文已知 $x=2$\n3. 题三正文已知 $x=3$\n"
        "4. 题四正文已知 $x=4$\n5. 题五正文已知 $x=5$\n" + q6_line + "\n"
    )


@pytest.mark.parametrize("q6", [
    "**6.** 题六正文已知 $x=6$",
    "# 6．题六正文已知 $x=6$",
    "\\textbf{6.} 题六正文已知 $x=6$",
    "６．题六正文已知 $x=6$",
    "6\\. 题六正文已知 $x=6$",
    "6．5名同学站成一排，求排法 $n$",
])
def test_six_questions_are_six_blocks(q6):
    plan = P.plan_pdf_local_split(_six(q6))
    assert [b.paper_question_no for b in plan.all_blocks] == ["1", "2", "3", "4", "5", "6"]
    assert "题五正文" in plan.all_blocks[4].content
    assert "题六" not in plan.all_blocks[4].content and "5名" not in plan.all_blocks[4].content
    assert plan.trustworthy is True


def test_bold_first_question_is_detected():
    src = _six("6. 题六正文已知 $x=6$").replace("1. 题一", "**1.** 题一", 1)
    plan = P.plan_pdf_local_split(src)
    assert [b.paper_question_no for b in plan.all_blocks][0] == "1"
    assert len(plan.all_blocks) == 6


def test_missed_first_question_is_not_trusted():
    src = _six("6. 题六正文已知 $x=6$").replace("1. 题一", "1 题一", 1)
    plan = P.plan_pdf_local_split(src)
    assert plan.trustworthy is False
    assert plan.cover_ok is False


def test_inline_merged_last_question_is_flagged():
    src = _six("6. 题六正文已知 $x=6$").replace("\n6. 题六", " 6．题六", 1)
    plan = P.plan_pdf_local_split(src)
    assert len(plan.all_blocks) == 5
    assert "G3_疑似并题" in plan.all_blocks[-1].risk_flags
    assert plan.trustworthy is False
    assert "merged_question_suspected" in plan.judge_report["untrusted_reasons"]


def test_inline_merged_middle_question_is_flagged():
    src = _six("6. 题六正文已知 $x=6$").replace("\n4. 题四", " 4．题四", 1)
    plan = P.plan_pdf_local_split(src)
    q3 = next(b for b in plan.all_blocks if b.paper_question_no == "3")
    assert "G3_疑似并题" in q3.risk_flags
    assert plan.trustworthy is False


def test_declared_count_mismatch_is_not_trusted():
    src = "一、选择题：本题共6小题，每小题5分。\n" + _six("").rstrip("\n") + "\n"
    plan = P.plan_pdf_local_split(src)
    assert len(plan.all_blocks) == 5
    assert plan.trustworthy is False
    assert plan.judge_report["section_count_mismatch"] == [{"declared": 6, "actual": 5}]


def test_declared_count_match_stays_trusted():
    src = "一、选择题：本题共6小题，每小题5分。\n" + _six("6. 题六正文已知 $x=6$")
    plan = P.plan_pdf_local_split(src)
    assert plan.trustworthy is True
    assert "section_count_mismatch" not in plan.judge_report


def test_halfwidth_decimal_at_line_start_is_not_anchor():
    src = _six("6. 题六正文已知 $x=6$").replace("题二正文已知 $x=2$", "题二正文已知\n2.5 倍于 $x$", 1)
    plan = P.plan_pdf_local_split(src)
    assert [b.paper_question_no for b in plan.all_blocks] == ["1", "2", "3", "4", "5", "6"]
    assert "2.5 倍" in plan.all_blocks[1].content


# ======================= 回归：原作者反例 3 —— 共享条件或公式丢失 =======================

def test_shared_material_between_questions_is_not_trusted():
    src = _six("6. 题六正文已知 $x=6$").replace(
        "\n4. 题四", "\n根据以下材料，完成第4～5题。某校共有 $n=100$ 名学生。\n4. 题四", 1)
    plan = P.plan_pdf_local_split(src)
    assert plan.trustworthy is False
    assert "shared_context" in plan.judge_report["untrusted_reasons"]
    q3 = next(b for b in plan.all_blocks if b.paper_question_no == "3")
    assert "G9_共享条件" in q3.risk_flags


def test_section_preamble_with_shared_formula_is_cut_and_not_trusted():
    src = (
        "一、选择题\n1. 题一正文已知 $x=1$\n2. 题二正文已知 $x=2$\n3. 题三正文已知 $x=3$\n"
        "二、填空题\n阅读下面材料，回答第4～5题：设 $f(x)=x^2$。\n"
        "4. 求 $f(2)$ 的值\n5. 求 $f(3)$ 的值\n"
    )
    plan = P.plan_pdf_local_split(src)
    q3 = next(b for b in plan.all_blocks if b.paper_question_no == "3")
    assert "填空题" not in q3.content and "f(x)" not in q3.content
    assert plan.trustworthy is False
    assert any(h["where"] == "section_preamble" for h in plan.judge_report["shared_context"])


def test_cross_reference_to_other_question_is_flagged():
    src = _six("6. 题六正文已知 $x=6$").replace("题四正文已知 $x=4$", "在第3题的条件下，求 $x$", 1)
    plan = P.plan_pdf_local_split(src)
    q4 = next(b for b in plan.all_blocks if b.paper_question_no == "4")
    assert "G9_跨题引用" in q4.risk_flags
    assert plan.trustworthy is False


def test_figure_caption_is_not_cross_reference():
    assert P._CROSS_REF_RE.search("如图（第7题图）所示") is None
    src = _six("6. 题六正文已知 $x=6$").replace("题五正文已知 $x=5$", "如图（第5题图），求 $x$", 1)
    plan = P.plan_pdf_local_split(src)
    q5 = next(b for b in plan.all_blocks if b.paper_question_no == "5")
    assert "G9_跨题引用" not in q5.risk_flags
    assert plan.trustworthy is True


def test_shared_formula_before_first_question_is_not_trusted():
    src = "已知函数 $f(x)=\\ln x$，解答下列各题。\n" + _six("6. 题六正文已知 $x=6$")
    plan = P.plan_pdf_local_split(src)
    assert plan.trustworthy is False
    assert "leading_context" in plan.judge_report["untrusted_reasons"]


def test_notice_zone_items_are_not_questions():
    src = (
        "注意事项：\n1. 答卷前，考生务必将自己的姓名填写在答题卡上。\n"
        "2. 回答选择题时，选出每小题答案后用铅笔涂黑。\n3. 考试结束后，将本试卷交回。\n" + GOOD
    )
    plan = P.plan_pdf_local_split(src)
    assert plan.judge_report.get("notice_zone_skipped") is True
    assert [b.paper_question_no for b in plan.all_blocks] == ["1", "2", "3", "4", "5"]
    assert plan.trustworthy is True
