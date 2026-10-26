"""Bounded candidate validation keeps valid goals without mutating source data."""
from copy import deepcopy

import pytest

from mathbank.ai_knowledge_points import (
    MAX_AI_CANDIDATES, MAX_AI_POINTS, annotate_ai_knowledge_points,
    restore_ai_knowledge_evidence,
)


CATALOG = [{"code": f"demo:{index}", "name": f"能力{index}"} for index in range(60)]
CONTENT = "(1) 求函数$f$的导数。\n(2) 求函数$g$的导数。"


def point(code="demo:0", quote="求函数$f$的导数", **changes):
    return {"code": code, "role": "main", "evidence_source": "content", "evidence": quote,
            "target_evidence": quote, "reason": "直接考查求导目标。", **changes}


def annotate(points, *, content=CONTENT, **fields):
    question = {"content": content, "answer_markdown": "原解使用函数定义，求得函数值。",
                "review_markdown": "原解利用函数单调性，求得结果。", "knowledge_points": points, **fields}
    annotate_ai_knowledge_points(question, CATALOG)
    return question


def assert_counts(question, *, requested, accepted, merged=0, invalid=0, over_budget=0):
    diagnostics = question["knowledge_point_diagnostics"]
    assert {key: diagnostics[key] for key in ("requested", "accepted", "merged", "invalid", "over_budget")} == {
        "requested": requested, "accepted": accepted, "merged": merged,
        "invalid": invalid, "over_budget": over_budget}
    assert diagnostics["rejected"] == invalid + over_budget
    assert requested == accepted + merged + invalid + over_budget
    assert diagnostics["considered"] == min(requested, MAX_AI_CANDIDATES)


def test_invalid_prefix_does_not_consume_the_effective_point_limit():
    question = annotate([None] * MAX_AI_POINTS + [point()])
    assert [item["code"] for item in question["knowledge_points"]] == ["demo:0"]
    assert_counts(question, requested=13, accepted=1, invalid=12)


def test_valid_duplicate_goals_merge_after_both_items_are_validated():
    raw = [point(scope="第(1)问"), point(quote="求函数$g$的导数", reason="第二问求导。")]
    frozen = deepcopy(raw)
    question = annotate(raw)
    accepted = question["knowledge_points"]
    assert len(accepted) == 1
    assert accepted[0]["target_evidence"] == ["求函数$f$的导数", "求函数$g$的导数"]
    assert accepted[0]["evidence"] == raw[0]["evidence"]
    assert accepted[0]["reason"] == raw[0]["reason"] and accepted[0]["scope"] == "第(1)问"
    assert not question["knowledge_point_diagnostics"]["coverage"]["uncovered_target_ids"]
    assert raw == frozen
    assert_counts(question, requested=2, accepted=1, merged=1)


def test_duplicate_without_new_target_contract_keeps_the_legacy_refusal():
    first, second = point(), point()
    first.pop("target_evidence"); second.pop("target_evidence")
    question = annotate([first, second])
    assert len(question["knowledge_points"]) == 1
    assert any("编码重复" in warning for warning in question["knowledge_point_warnings"])
    assert_counts(question, requested=2, accepted=1, invalid=1)


@pytest.mark.parametrize("changes", [
    {"role": "auxiliary", "evidence_source": "answer_markdown", "evidence": "使用函数定义"},
    {"evidence_source": "review_markdown", "evidence": "利用函数单调性"},
])
def test_duplicate_role_or_source_conflict_does_not_guess_a_winner(changes):
    question = annotate([point(), point(quote="求函数$g$的导数", **changes)])
    assert len(question["knowledge_points"]) == 1
    assert question["knowledge_points"][0]["role"] == "main"
    assert question["knowledge_points"][0]["evidence_source"] == "content"
    assert any("冲突" in warning for warning in question["knowledge_point_warnings"])
    assert_counts(question, requested=2, accepted=1, invalid=1)


def test_same_source_auxiliary_targets_merge_only_after_actual_step_checks():
    raw = [point(role="auxiliary", evidence_source="answer_markdown", evidence="使用函数定义"),
           point(quote="求函数$g$的导数", role="auxiliary", evidence_source="answer_markdown",
                 evidence="求得函数值")]
    question = annotate(raw)
    assert len(question["knowledge_points"]) == 1
    assert question["knowledge_points"][0]["target_evidence"] == ["求函数$f$的导数", "求函数$g$的导数"]
    assert_counts(question, requested=2, accepted=1, merged=1)


@pytest.mark.parametrize("changes", [
    {"target_evidence": "另一题的目标"}, {"evidence": "题中没有的公式"},
    {"reason": ""}, {"role": []}, {"evidence_source": []},
])
def test_bad_duplicate_cannot_acquire_trust_from_the_first_point(changes):
    question = annotate([point(), point(quote="求函数$g$的导数", **changes)])
    assert len(question["knowledge_points"]) == 1
    assert question["knowledge_points"][0]["target_evidence"] == "求函数$f$的导数"
    assert_counts(question, requested=2, accepted=1, invalid=1)


def test_new_unique_identity_over_budget_is_separate_from_invalid_items():
    raw = [point(code=f"demo:{index}") for index in range(MAX_AI_POINTS + 1)]
    question = annotate(raw)
    assert len(question["knowledge_points"]) == MAX_AI_POINTS
    assert_counts(question, requested=13, accepted=12, over_budget=1)


def test_duplicate_of_an_existing_point_can_merge_when_unique_limit_is_full():
    raw = [point(code=f"demo:{index}") for index in range(MAX_AI_POINTS)]
    raw.append(point(quote="求函数$g$的导数"))
    question = annotate(raw)
    assert len(question["knowledge_points"]) == MAX_AI_POINTS
    assert question["knowledge_points"][0]["target_evidence"] == ["求函数$f$的导数", "求函数$g$的导数"]
    assert_counts(question, requested=13, accepted=12, merged=1)


def test_target_union_over_budget_does_not_truncate_or_partially_merge():
    quotes = [f"目标{index:02d}" for index in range(13)]
    first = point(quote=quotes[0], target_evidence=quotes[:12])
    second = point(quote=quotes[12])
    question = annotate([first, second], content="、".join(quotes))
    assert question["knowledge_points"][0]["target_evidence"] == quotes[:12]
    assert_counts(question, requested=2, accepted=1, over_budget=1)


def test_candidate_scan_has_its_own_bounded_budget():
    question = annotate([None] * MAX_AI_CANDIDATES + [point()])
    assert not question["knowledge_points"]
    assert_counts(question, requested=49, accepted=0, invalid=48, over_budget=1)


def test_counts_and_warnings_are_stable_on_repeated_local_normalization():
    raw = [point(), point(quote="求函数$g$的导数"), *([None] * 46), point(code="demo:1")]
    question = annotate(raw)
    before = deepcopy(question)
    annotate_ai_knowledge_points(question, [])
    assert question == before
    assert_counts(question, requested=49, accepted=1, merged=1, invalid=46, over_budget=1)


def test_failed_revalidation_accounts_for_all_candidates_supporting_a_merged_point():
    question = annotate([point(), point(quote="求函数$g$的导数")])
    question["content"] = "求函数$f$的导数。"
    annotate_ai_knowledge_points(question, CATALOG)
    assert not question["knowledge_points"]
    assert_counts(question, requested=2, accepted=0, invalid=2)
    assert question["knowledge_point_diagnostics"]["rejected_items"][-1]["candidate_count"] == 2


def test_formula_restoration_reaches_late_candidates_without_mutating_raw_items():
    points = [None] * 12 + [point(evidence="[[owned]]", target_evidence="[[owned]]")]
    raw = deepcopy(points)
    question = {"content": "求函数$f$的导数。", "knowledge_points": points}
    restore_ai_knowledge_evidence(question, lambda value, _field: value.replace("[[owned]]", "求函数$f$的导数"))
    annotate_ai_knowledge_points(question, CATALOG)
    assert question["knowledge_points"][0]["target_evidence"] == "求函数$f$的导数"
    assert points == raw
    assert_counts(question, requested=13, accepted=1, invalid=12)


def test_formula_restoration_never_scans_beyond_the_candidate_budget():
    points = [None] * MAX_AI_CANDIDATES + [point(evidence="[[owned]]", target_evidence="[[owned]]")]
    question = {"knowledge_points": points}
    restore_ai_knowledge_evidence(question, lambda value, _field: value.replace("[[owned]]", "changed"))
    assert question["knowledge_points"] is points
    assert points[-1]["evidence"] == "[[owned]]"
