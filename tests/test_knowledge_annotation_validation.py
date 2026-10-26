"""New model annotations require goals and actual solution-step evidence."""
from copy import deepcopy

import pytest

from mathbank.ai_knowledge_points import annotate_ai_knowledge_points, restore_ai_knowledge_evidence


CATALOG = [{"code": "demo:goal", "name": "研究目标"}, {"code": "demo:method", "name": "实际方法"}]


def point(**overrides):
    return {"code": "demo:goal", "role": "main", "evidence_source": "content",
            "evidence": "求函数值", "target_evidence": "求函数值", "reason": "题干直接要求求函数值。", **overrides}


@pytest.mark.parametrize("answer", ["【答案】5", "AB", "[EXTRACTED_ORIGINAL]$x=5$",
                                  "![](/static/uploads/answer.png)"])
def test_result_only_material_does_not_license_auxiliary_points(answer):
    question = {"content": "已知函数，求函数值。", "answer_markdown": answer,
                "knowledge_points": [point(code="demo:method", role="auxiliary",
                    evidence_source="answer_markdown", evidence=answer)]}
    original = deepcopy(question)
    annotate_ai_knowledge_points(question, CATALOG)
    assert question["knowledge_points"] == []
    assert question["knowledge_point_diagnostics"]["solution_context"]["has_reasoning"] is False
    assert question["content"] == original["content"] and question["answer_markdown"] == answer


def test_auxiliary_requires_a_real_step_from_its_own_solution_field():
    question = {"content": "已知函数，求函数值。", "answer_markdown": "原解使用函数定义，求得值为5。"}
    for source, quote in (("content", "求函数值"), ("answer_markdown", "5"),
                          ("review_markdown", "使用函数定义")):
        q = {**question, "knowledge_points": [point(code="demo:method", role="auxiliary",
                                                  evidence_source=source, evidence=quote)]}
        annotate_ai_knowledge_points(q, CATALOG)
        assert q["knowledge_points"] == []
    q = {**question, "knowledge_points": [point(code="demo:method", role="auxiliary",
                                              evidence_source="answer_markdown", evidence="使用函数定义")]}
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"][0]["role"] == "auxiliary"


@pytest.mark.parametrize("source", [{}, [], None, 1])
def test_malformed_auxiliary_source_is_an_optional_rejection_not_a_flow_error(source):
    q = {"content": "已知函数，求函数值。", "answer_markdown": "原解使用函数定义。",
         "knowledge_points": [point(code="demo:method", role="auxiliary",
             evidence_source=source, evidence="使用函数定义"), point()]}
    annotate_ai_knowledge_points(q, CATALOG)
    assert [p["code"] for p in q["knowledge_points"]] == ["demo:goal"]
    assert q["knowledge_point_diagnostics"]["rejected"] == 1


def test_solution_core_method_cannot_become_a_main_goal_without_a_stem_anchor():
    q = {"content": "已知函数，求函数值。", "answer_markdown": "原解使用函数定义。",
         "knowledge_points": [point(evidence_source="answer_markdown", evidence="使用函数定义")]}
    del q["knowledge_points"][0]["target_evidence"]
    annotate_ai_knowledge_points(q, CATALOG)
    assert not q["knowledge_points"]


@pytest.mark.parametrize("target", ["另一题的目标", ["求函数值", "其他字段才有"], [], 1])
def test_goal_anchors_cannot_be_invented_or_borrowed_from_other_fields(target):
    q = {"content": "已知函数，求函数值。", "answer_markdown": "其他字段才有",
         "knowledge_points": [point(target_evidence=target)]}
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"] == [] and q["knowledge_point_diagnostics"]["rejected_items"]


def test_missing_second_main_goal_is_diagnosed_and_auxiliary_does_not_fill_it():
    content = "已知函数与数列。\n(1) 求函数值。\n(2) 证明数列单调递增。"
    q = {"content": content, "answer_markdown": "原解使用函数定义。",
         "knowledge_points": [point(), point(code="demo:method", role="auxiliary",
             evidence_source="answer_markdown", evidence="使用函数定义", target_evidence="证明数列单调递增")]}
    annotate_ai_knowledge_points(q, CATALOG)
    coverage = q["knowledge_point_diagnostics"]["coverage"]
    assert coverage["mode"] == "subquestions" and len(coverage["uncovered_target_ids"]) == 1
    assert "(2)" in "".join(q["knowledge_point_warnings"])
    warnings = list(q["knowledge_point_warnings"])
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_point_warnings"] == warnings


def test_one_point_can_cover_multiple_questions_without_duplicating_its_identity():
    q = {"content": "(1) 求函数值。\n(2) 证明函数有界。", "knowledge_points": [point(
        target_evidence=["求函数值", "证明函数有界"], scope="第(1)(2)问")]}
    annotate_ai_knowledge_points(q, CATALOG)
    assert len(q["knowledge_points"]) == 1
    assert not q["knowledge_point_diagnostics"]["coverage"]["uncovered_target_ids"]


def test_target_formula_restoration_is_copy_on_write_and_uses_the_content_owner():
    points = [point(target_evidence=["求[[owned]]的值", "证明函数有界"])]
    q = {"knowledge_points": points}
    restore_ai_knowledge_evidence(q, lambda value, source: value.replace("[[owned]]", "$f(1)$")
                                  if source == "content" else value)
    assert q["knowledge_points"][0]["target_evidence"][0] == "求$f(1)$的值"
    assert points[0]["target_evidence"][0] == "求[[owned]]的值"


def test_overlong_target_array_is_not_silently_truncated_before_validation():
    points = [point(target_evidence=["求函数值"] * 13)]
    q = {"content": "求函数值。", "knowledge_points": points}
    restore_ai_knowledge_evidence(q, lambda value, _source: value)
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"] == []
