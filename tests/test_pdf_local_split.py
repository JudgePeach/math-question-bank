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
