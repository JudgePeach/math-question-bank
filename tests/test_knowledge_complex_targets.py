"""Mixed numbering and separate products remain literal diagnostic anchors."""
import pytest

from mathbank.knowledge_annotation_targets import (
    MAX_SCAN_MARKERS, MAX_DIAGNOSTIC_TARGETS, MAX_TARGET_QUOTES,
    extract_knowledge_annotation_targets, diagnose_knowledge_target_coverage, validate_target_evidence,
)


def main(quote):
    return {"code": "demo:goal", "role": "main", "target_evidence": quote}


@pytest.mark.parametrize("labels", [("(I)", "(2)"), ("( 1 )", "（ 2 ）"),
    ("（一）", "（二）"), ("①", "②"), ("1. ", "2. "), ("一、", "二、")])
def test_common_mixed_and_spaced_labels_are_kept_verbatim(labels):
    content = labels[0] + "求函数值。\n" + labels[1] + "证明函数有界。"
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions" and len(plan["targets"]) == len(plan["goals"]) == 2
    assert all(content[item["start"]:item["end"]] == item["quote"] for item in plan["targets"])


@pytest.mark.parametrize("outer,inner", [(("(I)", "(II)"), ("(1)", "(2)")),
    (("(1)", "(2)"), ("①", "②")), (("一、", "二、"), ("(1)", "(2)"))])
def test_nested_shared_conditions_do_not_become_extra_targets(outer, inner):
    content = (f"{outer[0]} 已知两个函数满足相同条件：\n"
               f"  {inner[0]} 若参数为正数，则函数有定义，求函数值；\n"
               f"  {inner[1]} 已知其定义域，证明函数有界；\n{outer[1]} 求参数范围。")
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions" and len(plan["targets"]) == 3
    assert plan["targets"][0]["label"].endswith(inner[0])
    result = diagnose_knowledge_target_coverage(content, [main("参数为正数"), main("其定义域")])
    assert result["covered_goal_ids"] == []
    assert len(result["uncovered_goal_ids"]) == 3


@pytest.mark.parametrize("connector", ["并", "并且", "再", "然后", "同时", "，并"])
def test_one_subquestion_has_separate_value_and_proof_products(connector):
    content = f"(1)求函数值{connector}证明函数有界。\n(2)求参数范围。"
    plan = extract_knowledge_annotation_targets(content)
    assert len(plan["targets"]) == 2 and len(plan["goals"]) == 3
    first = diagnose_knowledge_target_coverage(content, [main("求函数值")])
    assert len(first["covered_goal_ids"]) == 1
    assert first["covered_target_ids"] == []
    assert any("证明函数有界" in warning for warning in first["warnings"])
    complete = diagnose_knowledge_target_coverage(content, [main(["求函数值", "证明函数有界", "求参数范围"])])
    assert len(complete["covered_goal_ids"]) == 3 and complete["warnings"] == []


def test_whole_question_with_two_explicit_products_is_also_checked_separately():
    content = "求函数值，并证明函数有界。"
    result = diagnose_knowledge_target_coverage(content, [main("求函数值")])
    assert result["mode"] == "whole_question" and result["goal_count"] == 2
    assert len(result["uncovered_goal_ids"]) == 1 and result["warnings"]


def test_a_relationship_of_objects_is_not_fabricated_as_two_outputs():
    content = "(1)求$a$与$b$的和。\n(2)证明函数有界。"
    assert len(extract_knowledge_annotation_targets(content)["goals"]) == 2


def test_repeated_object_inside_one_part_needs_an_unambiguous_quote():
    content = "(1)求函数的单调性，并证明函数的单调性。\n(2)求参数范围。"
    result = diagnose_knowledge_target_coverage(content, [main("函数的单调性")])
    assert result["covered_goal_ids"] == []
    assert result["point_targets"][0]["ambiguous_quote_count"] == 1


@pytest.mark.parametrize("prefix", ["已知条件：①函数为奇函数；②函数周期为2。",
    "已知条件：(1)若求参数的值，则条件成立；(2)若证明结论，则条件成立。"])
def test_condition_lists_with_conditional_task_words_do_not_turn_into_subquestions(prefix):
    plan = extract_knowledge_annotation_targets(prefix + "求函数值。")
    assert plan["mode"] == "whole_question"


def test_diagnostic_limits_are_separate_from_the_response_quote_limit():
    assert MAX_SCAN_MARKERS == MAX_DIAGNOSTIC_TARGETS == 48 and MAX_TARGET_QUOTES == 12
    content = "\n".join(f"({index})求参数{index}。" for index in range(1, 14))
    assert len(extract_knowledge_annotation_targets(content)["targets"]) == 13
    assert validate_target_evidence(content, ["求参数1"] * 13) is None


def test_more_than_48_goals_cannot_claim_complete_coverage_from_one_anchor():
    content = "\n".join(f"({index})求函数值，再证明性质{index}。" for index in range(1, 26))
    result = diagnose_knowledge_target_coverage(content, [main("求函数值")])
    assert result["goal_diagnostics_limited"] and result["detected_goal_count"] == 50
    assert result["covered_target_ids"] == [] and result["warnings"]


def test_coordinated_products_require_an_anchor_including_the_whole_group():
    content = "（1）求函数的零点和单调区间；（2）求函数的最大值。"
    partial = diagnose_knowledge_target_coverage(content, [main("零点"), main("最大值")])
    assert len(partial["uncovered_target_ids"]) == 1
    assert any("零点和单调区间" in warning for warning in partial["warnings"])
    complete = diagnose_knowledge_target_coverage(content, [main("零点和单调区间"), main("最大值")])
    assert complete["uncovered_target_ids"] == []


def test_derivation_description_inside_condition_list_is_not_a_requested_task():
    content = r"已知函数满足以下条件：①对任意$x$，求导后$f'(x)>0$；②函数有零点。求函数最大值。"
    assert extract_knowledge_annotation_targets(content)["mode"] == "whole_question"


def test_single_relation_product_does_not_split_into_two_goals_or_claim_operand_only_coverage():
    content = "(1)求$a$与$b$的和。\n(2)求参数范围。"
    assert len(extract_knowledge_annotation_targets(content)["goals"]) == 2
    assert diagnose_knowledge_target_coverage(content, [main("$a$")])["covered_goal_ids"] == []


def test_query_word_inside_a_requested_judgment_does_not_create_an_extra_product():
    content = "(1)判断函数是否为奇函数？\n(2)求参数范围。"
    plan = extract_knowledge_annotation_targets(content)
    assert len(plan["goals"]) == 2
    result = diagnose_knowledge_target_coverage(content, [main("奇函数"), main("参数范围")])
    assert result["uncovered_goal_ids"] == []


@pytest.mark.parametrize("number", [1, 15, 19, 24])
def test_one_preserved_paper_number_wraps_the_same_literal_subquestions(number):
    content = f"{number}. 已知函数$f(x)=x^2$。\n（1）求$f(2)$；\n（2）证明$f(x)\\ge0$。"
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions" and len(plan["targets"]) == 2
    assert len(plan["goals"]) == 2
    for target in plan["targets"]:
        assert content[target["start"]:target["end"]] == target["quote"]
        assert target["label"].startswith(f"{number}./")
    complete = diagnose_knowledge_target_coverage(content, [main(["求$f(2)$", r"证明$f(x)\ge0$"])])
    assert complete["uncovered_target_ids"] == []


@pytest.mark.parametrize("number", [1, 15, 19, 24])
def test_a_container_head_with_its_own_task_is_not_discarded(number):
    content = f"{number}. 求函数的定义域。\n(1)求函数值；\n(2)证明函数有界。"
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions" and len(plan["targets"]) == len(plan["goals"]) == 3
    result = diagnose_knowledge_target_coverage(content, [main(["求函数值", "证明函数有界"])])
    assert len(result["uncovered_goal_ids"]) == 1 and "定义域" in plan["goals"][0]["quote"]


@pytest.mark.parametrize("first,second", [(1, 2), (15, 16), (19, 20), (24, 30)])
def test_multiple_original_questions_with_subquestions_are_not_merged(first, second):
    content = (f"{first}. 已知函数。\n(1)求函数值；\n(2)证明函数有界。\n"
               f"{second}. 已知数列。\n(1)求数列通项；\n(2)证明数列递增。")
    assert extract_knowledge_annotation_targets(content)["mode"] == "whole_question"


@pytest.mark.parametrize("number", [1, 15, 19, 24])
def test_a_paper_number_does_not_turn_numbered_conditions_into_tasks(number):
    content = f"{number}. 已知函数满足以下条件：\n(1)函数为奇函数；\n(2)函数周期为2。求函数值。"
    assert extract_knowledge_annotation_targets(content)["mode"] == "whole_question"


def test_a_protected_outer_number_does_not_interfere_with_the_real_subquestions():
    content = "```text\n19. 伪原题号\n```\n(1)求函数值；\n(2)证明函数有界。"
    plan = extract_knowledge_annotation_targets(content)
    assert plan["mode"] == "subquestions" and len(plan["targets"]) == 2
    assert all("伪" not in target["quote"] for target in plan["targets"])
