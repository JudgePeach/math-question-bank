"""Result-only and hypothetical text cannot license an auxiliary method."""
import pytest

from mathbank.knowledge_solution_context import evidence_is_reasoning, solution_context


@pytest.mark.parametrize("answer", ["5", "【答案】5", "【答案】AB", "答案：A、B、D", "【答案】$x=5$",
    r"【答案】$\frac{\sqrt{10}}{5}$", r"【答案】$a=-4,d=3$", "[EXTRACTED_ORIGINAL]【答案】5",
    "![](/static/uploads/solution.png)", '<img src="/static/uploads/solution.png">',
    r"\includegraphics{static/uploads/solution.png}"])
def test_explicit_results_and_image_only_answers_are_not_derivations(answer):
    states = solution_context({"answer_markdown": answer})
    assert states == {"answer_markdown": "answer_only", "review_markdown": "absent", "has_reasoning": False}
    assert not evidence_is_reasoning(answer, answer)


@pytest.mark.parametrize("answer,quote", [
    ("原解使用导数。", "使用导数"),
    ("原解使用函数定义", "使用函数定义"),
    ("代入$x=1$，解得$m=3$。", "代入$x=1$"),
    (r"【答案】5。 【解析】利用等差数列求和公式，得$S=5$。", "利用等差数列求和公式"),
    (r"$S=1+4=5$", r"$S=1+4=5$"),
    (r"设平面$PCD$的法向量为$n=(x,y,z)$。", r"设平面$PCD$的法向量为$n=(x,y,z)$"),
    (r"抽样比为$25/300=1/12$，所以各层人数为6、9、10。", r"抽样比为$25/300=1/12$"),
    (r"函数的导数为$y'=2x+b$，据此判断单调性。", r"导数为$y'=2x+b$"),
])
def test_short_real_steps_remain_eligible(answer, quote):
    assert solution_context({"answer_markdown": answer})["has_reasoning"]
    assert evidence_is_reasoning(answer, quote)


def test_protocol_marker_alone_is_not_reasoning_and_cannot_be_evidence():
    assert solution_context({"answer_markdown": "[EXTRACTED_ORIGINAL]"})["answer_markdown"] == "absent"
    text = "[EXTRACTED_ORIGINAL]原解使用导数。"
    assert evidence_is_reasoning(text, "使用导数")
    assert not evidence_is_reasoning(text, "[EXTRACTED_ORIGINAL]")


@pytest.mark.parametrize("text", [
    "【注】若改问通项，则可使用累加法。",
    "【注】如果本题第(I)问不是证明数列，而是直接让我们求出通项，会怎样处理呢？\n\n利用累加法求出通项。",
    "这是未采用的解法：使用导数。",
    "`原解使用导数。`",
    "```text\n原解使用导数。\n```",
    r"\verb|原解使用导数。|",
    "<!-- 原解使用导数。 -->",
])
def test_hypothetical_or_literal_method_mentions_do_not_count_as_actual_steps(text):
    assert not solution_context({"review_markdown": text})["has_reasoning"]
    assert not evidence_is_reasoning(text, "使用导数")
    assert not evidence_is_reasoning(text, "利用累加法求出通项")


def test_real_solution_does_not_license_a_quote_from_later_hypothetical_note():
    text = "原解使用函数定义。\n\n【注】若改问导数值，则可使用导数。"
    assert solution_context({"review_markdown": text})["has_reasoning"]
    assert evidence_is_reasoning(text, "使用函数定义")
    assert not evidence_is_reasoning(text, "使用导数")


def test_explicit_return_to_actual_solution_ends_hypothetical_note():
    text = "【注】若改问通项，则可利用累加法。\n\n【原解】使用等差数列的定义。"
    assert evidence_is_reasoning(text, "使用等差数列的定义")
    assert not evidence_is_reasoning(text, "利用累加法")


def test_result_quote_does_not_inherit_reasoning_from_another_step():
    text = "【答案】5\n【解析】使用等差数列的求和公式。"
    assert solution_context({"answer_markdown": text})["has_reasoning"]
    assert not evidence_is_reasoning(text, "5")
    assert evidence_is_reasoning(text, "使用等差数列的求和公式")


def test_unknown_prose_and_partial_quotes_do_not_claim_a_proof():
    text = "这是一道有关函数的题。"
    assert solution_context({"review_markdown": text})["review_markdown"] == "uncertain"
    assert not evidence_is_reasoning(text, text)
    assert not evidence_is_reasoning("原解使用导数。", "使用不存在的方法")
    assert solution_context({"answer_markdown": "This is a comment"})["answer_markdown"] == "uncertain"


def test_formula_step_can_be_quoted_inside_an_actual_solution_but_not_as_a_bare_answer():
    formula = r"$f^{\prime}(x)=a_1+2a_2x$"
    text = "使用幂函数的求导公式，可得" + formula + "。"
    assert evidence_is_reasoning(text, formula)
    assert not evidence_is_reasoning("【答案】" + formula, formula)
    coordinates = r"故$\vec{AB}=(1,0,1),\vec{AC}=(2,0,-2)$"
    worked = "利用点坐标作差求向量，" + coordinates + "。"
    assert not evidence_is_reasoning(worked, coordinates)
    assert evidence_is_reasoning(worked, worked)
    assert not evidence_is_reasoning(coordinates, coordinates)


def test_same_quote_in_literal_and_actual_step_uses_only_actual_occurrence():
    text = "代码示例：`使用导数`。\n\n原解使用导数。"
    assert evidence_is_reasoning(text, "使用导数")
    assert not evidence_is_reasoning(text, "`使用导数`")


def test_fields_are_classified_independently():
    result = solution_context({"answer_markdown": "【答案】5", "review_markdown": "原解使用导数。"})
    assert result == {"answer_markdown": "answer_only", "review_markdown": "reasoning", "has_reasoning": True}


@pytest.mark.parametrize("negative", ["未使用", "未曾使用", "并未使用", "尚未使用", "没有使用", "无需使用",
    "不必使用", "不需要使用", "不用使用", "不能使用", "不是使用", "并非使用", "不使用"])
def test_negated_method_cannot_become_a_step_by_removing_its_negation(negative):
    text = "原解" + negative + "导数。"
    assert not solution_context({"answer_markdown": text})["has_reasoning"]
    assert not evidence_is_reasoning(text, "使用导数")


@pytest.mark.parametrize("separator", ["，而是", ",而是", "而是", "；但是", "。不过"])
def test_positive_method_after_denied_method_remains_eligible(separator):
    text = "原解没有采用导数" + separator + "利用函数单调性。"
    assert solution_context({"review_markdown": text})["has_reasoning"]
    assert not evidence_is_reasoning(text, "采用导数")
    assert evidence_is_reasoning(text, "利用函数单调性")


def test_negated_formula_cannot_inherit_a_positive_step_elsewhere():
    text = r"无需利用导数，原解使用几何法。也未使用坐标法$f(x,y)=x+y$，而是利用函数定义。"
    assert not evidence_is_reasoning(text, "利用导数")
    assert not evidence_is_reasoning(text, r"$f(x,y)=x+y$")
    assert evidence_is_reasoning(text, "使用几何法")
    assert evidence_is_reasoning(text, "利用函数定义")


@pytest.mark.parametrize("formula,conclusion", [
    (r"$x^2-1=(x-1)(x+1)$", r"，故方程的根为$-1,1$。"),
    (r"\(x^2-2x+1=(x-1)^2\)", r"，因此最小值为$0$。"),
    (r"\[(a+b)^2=a^2+2ab+b^2\]", r"；所以所求式等于$4$。"),
    (r"$$x(x+1)=x^2+x$$", r"，从而可比较两式的系数。"),
])
def test_single_structural_rewrite_with_a_following_conclusion_is_a_real_step(formula, conclusion):
    text = "【解析】" + formula + conclusion
    assert solution_context({"answer_markdown": text})["answer_markdown"] == "reasoning"
    assert evidence_is_reasoning(text, formula)
    assert evidence_is_reasoning(text, text)


@pytest.mark.parametrize("text", [
    r"【答案】$(x-1)(x+1)$",
    r"【答案】$x^2-1=(x-1)(x+1)$",
    r"【解析】$(x-1)(x+1)$",
    r"【解析】$x^2-1=(x-1)(x+1)$",
    r"【解析】最终因式分解结果为$(x-1)(x+1)$。",
    r"【答案】$x=-1,1$",
    r"【解析】$x=-1$；$x=1$。",
    r"【解析】$x^2-1=0$，故答案如上。",
    r"【解析】$x=2-1$，故答案为$1$。",
    r"【解析】$x^2-1=y^2+1$，故答案如上。",
])
def test_final_factorization_and_equation_results_do_not_claim_an_actual_method(text):
    assert not solution_context({"answer_markdown": text})["has_reasoning"]
    assert not evidence_is_reasoning(text, text)


@pytest.mark.parametrize("prefix,suffix", [
    ("【注】若改问因式分解，则", ""),
    ("这是未采用的解法：", ""),
    ("这是可替代的方法：", ""),
    ("原解没有使用因式分解", ""),
    ("`", "`"),
    ("```text\n", "\n```"),
])
def test_short_rewrites_in_hypotheticals_negations_or_literals_remain_ineligible(prefix, suffix):
    formula = r"$x^2-1=(x-1)(x+1)$"
    text = prefix + formula + r"，故方程的根为$-1,1$。" + suffix
    assert not solution_context({"review_markdown": text})["has_reasoning"]
    assert not evidence_is_reasoning(text, formula)


def test_following_masked_hypothetical_text_cannot_supply_a_conclusion():
    text = r"【解析】$x^2-1=(x-1)(x+1)$，故若改问因式分解，就可以给出结果。"
    assert not solution_context({"answer_markdown": text})["has_reasoning"]


@pytest.mark.parametrize("result", ["解得$x=5$。", "可得$x=5$。", "【答案】解得$x=5$。",
    "【解析】求得$x=5$。", "计算得$x=5$。", "所以$x=5$。", "解得参数为$5$。"])
def test_result_introducing_words_do_not_create_a_derivation(result):
    assert solution_context({"answer_markdown": result})["answer_markdown"] == "answer_only"
    assert not evidence_is_reasoning(result, result)
    real = "原解使用函数定义。" + result
    assert solution_context({"answer_markdown": real})["has_reasoning"]
    assert not evidence_is_reasoning(real, result)


def test_result_introduction_with_a_visible_calculation_still_shows_work():
    text = r"计算得$S=1+4=5$。"
    assert solution_context({"answer_markdown": text})["has_reasoning"]
    assert evidence_is_reasoning(text, text)
