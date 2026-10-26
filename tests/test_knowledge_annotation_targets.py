"""No database, network, or model: literal targets and coverage-only diagnostics."""
from copy import deepcopy

import pytest

from mathbank.knowledge_annotation_targets import (
    diagnose_knowledge_target_coverage,
    extract_knowledge_annotation_targets,
    knowledge_annotation_targets_for_prompt,
    validate_target_evidence,
)


@pytest.mark.parametrize("first,second", [
    ("(1)", "(2)"), ("（1）", "（2）"), ("(I)", "(II)"),
    ("（I）", "（II）"), ("(i)", "(ii)"), ("(Ⅰ)", "(Ⅱ)"), ("(1)", "（2）"),
])
def test_explicit_flat_questions_keep_original_quotes_and_offsets(first, second):
    content = f"已知函数$f(x)$。\r\n  {first}求函数的定义域；\r\n{second}证明函数为奇函数。  \r\n"
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions"
    assert [item["label"] for item in plan["targets"]] == [first, second]
    assert [item["quote"] for item in plan["targets"]] == [f"{first}求函数的定义域；", f"{second}证明函数为奇函数。"]
    for item in plan["targets"]:
        assert content[item["start"]:item["end"]] == item["quote"]
    assert extract_knowledge_annotation_targets(content) == plan
    assert len({item["id"] for item in plan["targets"]}) == 2
    public = knowledge_annotation_targets_for_prompt(plan)
    assert all(set(item) == {"id", "label", "quote", "goal_quotes"} for item in public)
    assert public[0]["quote"] == plan["targets"][0]["quote"]
    assert public[0]["goal_quotes"] == ["求函数的定义域"]


@pytest.mark.parametrize("content", [
    "题设：(1)求函数值；(2)判断函数的奇偶性。",
    "15.(1)求函数值；(2)判断函数的奇偶性。",
    "题设 (1)求函数值 (2)判断函数的奇偶性。",
    "(1)若函数满足条件，求函数值；(2)利用前问结果证明函数的单调性。",
    "(1)请确定函数的极值；(2)函数是否有最大值，说明理由。",
])
def test_inline_safe_boundaries_and_outer_question_number(content):
    assert extract_knowledge_annotation_targets(content)["mode"] == "subquestions"


@pytest.mark.parametrize("protected", [
    r"$(1)求伪目标；(2)证明伪命题$",
    r"\((1)求伪目标；(2)证明伪命题\)",
    "$$\n(1)求伪目标；\n(2)证明伪命题\n$$",
    "```text\n(1)求伪目标；\n(2)证明伪命题\n```",
    "    (1)求伪目标；\n    (2)证明伪命题",
    "`(1)求伪目标；(2)证明伪命题`",
    r"\verb|(1)求伪目标；(2)证明伪命题|",
    r"\detokenize{(1)求伪目标；(2)证明伪命题}",
    "<!--\n(1)求伪目标；\n(2)证明伪命题\n-->",
    "% (1)求伪目标；(2)证明伪命题",
    "“(1)求伪目标；(2)证明伪命题”",
    "> (1)求伪目标；\n> (2)证明伪命题",
    r"\begin{choices}\item (1)求伪目标；\item (2)证明伪命题\end{choices}",
    "\\begin{tabular}{l}\n(1)求伪目标；\\\\\n(2)证明伪命题\n\\end{tabular}",
    "| 题号 | 说明 |\n| --- | --- |\n| (1) | 求伪目标 |\n| (2) | 证明伪命题 |",
    "<table><tr><td>(1)求伪目标；</td><td>(2)证明伪命题</td></tr></table>",
    "![图](</static/uploads/(1)求伪目标；(2)证明伪命题.png>)",
    "https://example.test/(1)求伪目标；(2)证明伪命题",
])
def test_math_literals_choices_and_tables_never_supply_question_markers(protected):
    content = protected + "\n\n(1)求真实定义域；\n(2)求真实值域。"
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions"
    assert len(plan["targets"]) == 2
    assert all("真实" in item["quote"] and "伪" not in item["quote"] for item in plan["targets"])


@pytest.mark.parametrize("content", [
    "(1)求单个目标。", "求函数的定义域。", "(2)求函数值；(3)求导数。",
    "(1)求函数值；(3)求导数。", "(1)求函数值；(1)求导数。",
    "(1）求函数值；（2)求导数。",
    "已知两个条件：\n(1)函数为奇函数；\n(2)函数的周期为2。求函数值。",
    "已知两个条件：\n(1)满足要求P；\n(2)满足要求Q。求函数值。",
    "(1)求和公式为公式甲；(2)求和公式为公式乙。求函数值。",
    "$\n(1)求函数值；\n(2)求导数。",
    "\\begin{choices}\n(1)求函数值；\n(2)求导数。",
    "\\begin{tabular}{l}\n(1)求函数值；\n(2)求导数。",
    "```text\n(1)求函数值；\n(2)求导数。",
    "\n".join(f"({index})求参数{index}的取值范围。" for index in range(1, 50)),
])
def test_uncertain_or_malformed_structures_stay_whole_and_never_warn_missing_parts(content):
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "whole_question"
    assert len(plan["targets"]) == 1
    assert plan["targets"][0]["quote"] == content.strip()
    assert diagnose_knowledge_target_coverage(content, [])["warnings"] == []


def test_empty_input_has_no_invented_target():
    for content in ("", " \n ", None):
        assert extract_knowledge_annotation_targets(content)["targets"] == []
        assert diagnose_knowledge_target_coverage(content, [])["warnings"] == []


def test_target_quote_validation_is_literal_bounded_and_non_mutating():
    content = "(1)求$a$的值；\n(2)证明函数为奇函数。"
    evidence = ["求$a$的值", "证明函数为奇函数", "求$a$的值"]
    before = deepcopy(evidence)
    assert validate_target_evidence(content, evidence) == ["求$a$的值", "证明函数为奇函数"]
    assert evidence == before
    assert validate_target_evidence(content, "求$a$的值") == ["求$a$的值"]
    for invalid in (None, 1, True, {}, [], [1], [""], ["\x00"], ["求 a 的值"],
                    ["求$a$的值"] * 13, ("求$a$的值",), "x" * 2001):
        assert validate_target_evidence(content, invalid) is None
    assert validate_target_evidence("x" * 2001, "x" * 2001) is None


CONTENT = "已知函数$f(x)$满足条件P。\n(1)求函数的定义域；\n(2)证明函数的单调性；\n(3)求参数的取值范围。"


def main(code, **fields):
    return {"code": code, "role": "main", **fields}


def test_main_can_cover_multiple_parts_and_auxiliary_does_not_cover_missing_part():
    points = [main("x", target_evidence=["求函数的定义域", "证明函数的单调性"]),
              {"code": "y", "role": "auxiliary", "target_evidence": "求参数的取值范围"}]
    before = deepcopy(points)
    plan = extract_knowledge_annotation_targets(CONTENT)
    result = diagnose_knowledge_target_coverage(CONTENT, points, targets=plan)
    assert result["covered_target_ids"] == [item["id"] for item in plan["targets"][:2]]
    assert result["uncovered_target_ids"] == [plan["targets"][2]["id"]]
    assert len(result["warnings"]) == 1 and "(3)" in result["warnings"][0]
    assert result["point_targets"][0]["evidence_kind"] == "target_evidence"
    assert result["targets_recomputed"] is False
    assert points == before


def test_legacy_content_evidence_supported_but_scope_and_answer_quotes_cannot_claim_parts():
    result = diagnose_knowledge_target_coverage(CONTENT, [
        main("a", evidence_source="content", evidence="求函数的定义域", scope="第(2)问、全题"),
        main("b", evidence_source="answer_markdown", evidence="证明函数的单调性", scope="(2)"),
        main("c", scope="第(3)问"),
    ])
    assert len(result["covered_target_ids"]) == 1
    assert len(result["uncovered_target_ids"]) == 2
    assert result["point_targets"][0]["evidence_kind"] == "legacy_content_evidence"
    assert result["point_targets"][1]["target_ids"] == []


def test_invalid_new_field_cannot_fall_back_to_good_legacy_quote():
    result = diagnose_knowledge_target_coverage(CONTENT, [main(
        "a", target_evidence="不存在的原文", evidence_source="content", evidence="求函数的定义域")])
    assert result["covered_target_ids"] == []
    assert result["point_targets"][0]["invalid_target_evidence"] is True


def test_repeated_quote_is_ambiguous_until_literal_subquestion_labels_disambiguate():
    content = "(1)求函数的单调区间。\n(2)求函数的单调区间。"
    result = diagnose_knowledge_target_coverage(content, [main("a", target_evidence="求函数的单调区间", scope="(1)")])
    assert result["covered_target_ids"] == []
    assert result["point_targets"][0]["ambiguous_quote_count"] == 1
    result = diagnose_knowledge_target_coverage(content, [main("a", target_evidence=[
        "(1)求函数的单调区间", "(2)求函数的单调区间"])])
    assert len(result["covered_target_ids"]) == 2
    assert result["uncovered_target_ids"] == []
    assert result["warnings"] == []


@pytest.mark.parametrize("quote", ["条件P", "定义域；\n(2)证明", "(1)", "求", "证明", "第(1)问"])
def test_shared_conditions_cross_part_quotes_and_generic_markers_do_not_claim_coverage(quote):
    result = diagnose_knowledge_target_coverage(CONTENT, [main("a", target_evidence=quote)])
    assert result["covered_target_ids"] == []


def test_literal_and_formula_only_quotes_inside_a_real_part_do_not_claim_target_coverage():
    content = "(1)求函数的定义域。例子`目标为单调性`，公式$目标为周期性$。\n(2)证明函数为奇函数。"
    assert extract_knowledge_annotation_targets(content)["mode"] == "subquestions"
    result = diagnose_knowledge_target_coverage(content, [
        main("a", target_evidence="目标为单调性"), main("b", target_evidence="目标为周期性")])
    assert result["covered_target_ids"] == []


def test_stale_or_forged_plan_is_recomputed_from_current_content():
    stale = extract_knowledge_annotation_targets("(1)求旧目标一；(2)求旧目标二。")
    before = deepcopy(stale)
    result = diagnose_knowledge_target_coverage(CONTENT, [main("a", target_evidence="求函数的定义域")], targets=stale)
    assert result["targets_recomputed"] is True
    assert result["target_count"] == 3
    assert stale == before
    forged = extract_knowledge_annotation_targets(CONTENT)
    forged["targets"][0]["end"] = len(CONTENT)
    assert diagnose_knowledge_target_coverage(CONTENT, [], targets=forged)["targets_recomputed"] is True


@pytest.mark.parametrize("labels", [("(1)", "(2)"), ("(I)", "(II)"), ("（1）", "（2）")])
def test_subquestion_conditions_do_not_count_as_covered_targets(labels):
    content = (f"{labels[0]}已知参数取正数，求函数的定义域；\n"
               f"{labels[1]}已知参数取负数，求函数的值域。")
    plan = extract_knowledge_annotation_targets(content)
    assert [item["goal_quotes"] for item in knowledge_annotation_targets_for_prompt(plan)] == [
        ["求函数的定义域"], ["求函数的值域"]]
    result = diagnose_knowledge_target_coverage(content, [
        main("a", target_evidence="参数取正数"), main("b", target_evidence="参数取负数")])
    assert result["covered_target_ids"] == []
    assert len(result["uncovered_target_ids"]) == 2
    assert result["warnings"]
    # Actual target objects need not repeat the action verb.
    result = diagnose_knowledge_target_coverage(content, [
        main("a", target_evidence="函数的定义域"), main("b", target_evidence="函数的值域")])
    assert len(result["covered_target_ids"]) == 2
    assert result["warnings"] == []


def test_condition_plus_only_action_verb_does_not_falsely_cover_object():
    content = "(1)已知参数取正数，求函数的定义域；\n(2)已知参数取负数，求函数的值域。"
    result = diagnose_knowledge_target_coverage(content, [main("a", target_evidence="已知参数取正数，求")])
    assert result["covered_target_ids"] == []
    result = diagnose_knowledge_target_coverage(content, [main("a", target_evidence="求函数的定义域")])
    assert len(result["covered_target_ids"]) == 1


def test_requested_mathematical_objects_and_internal_punctuation_are_covered():
    content = "(I)已知条件P，求函数$f(x,y)$的定义域；\n(II)已知条件Q，求$\\frac{f(1)}{f(2)}$的值。"
    plan = extract_knowledge_annotation_targets(content)
    assert [item["goal_quotes"] for item in knowledge_annotation_targets_for_prompt(plan)] == [
        ["求函数$f(x,y)$的定义域"], [r"求$\frac{f(1)}{f(2)}$的值"]]
    result = diagnose_knowledge_target_coverage(content, [
        main("a", target_evidence="$f(x,y)$"), main("b", target_evidence=r"$\frac{f(1)}{f(2)}$")])
    assert len(result["covered_target_ids"]) == 2
    assert result["warnings"] == []


def test_conditions_after_a_visible_comma_do_not_expand_the_goal_clause():
    content = "(1)求函数的定义域，其中参数取正数；\n(2)求函数的值域，其中参数取负数。"
    result = diagnose_knowledge_target_coverage(content, [
        main("a", target_evidence="参数取正数"), main("b", target_evidence="参数取负数")])
    assert result["covered_target_ids"] == []


def test_requested_multiline_math_is_not_cut_at_protected_newlines():
    content = "(1)计算$$\\begin{aligned}x+y\\\\\nx-y\\end{aligned}$$的值；\n(2)判断函数的奇偶性。"
    target = extract_knowledge_annotation_targets(content)["targets"][0]
    assert "\nx-y" in target["goal_ranges"][0]["quote"]
    result = diagnose_knowledge_target_coverage(content, [main("a", target_evidence="x-y")])
    assert len(result["covered_target_ids"]) == 1
