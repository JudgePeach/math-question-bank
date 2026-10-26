"""Tolerate explanatory field mistakes while preserving grounded hard gates."""
from copy import deepcopy
import hashlib

import pytest

from mathbank.ai_knowledge_points import annotate_ai_knowledge_points


CATALOG = [{"code": "demo:goal", "name": "直接目标"},
           {"code": "demo:method", "name": "实际方法"}]
CONTENT = "(1) 求函数值。\n(2) 求方程的根。"


def point(**changes):
    return {"code": "demo:goal", "role": "main", "evidence_source": "content",
            "evidence": "求函数值", "target_evidence": "求函数值",
            "reason": "题干直接要求求函数值。", **changes}


def annotate(raw, **source):
    question = {"content": CONTENT, **source, "knowledge_points": raw}
    return annotate_ai_knowledge_points(question, CATALOG)


@pytest.mark.parametrize("scope", [None, [], {}, 7, True, "", " \n", "x" * 201, "第\x00一问"])
def test_invalid_optional_scope_is_ignored_with_a_warning_without_losing_the_point(scope):
    raw = [point(scope=scope)]
    frozen = deepcopy(raw)
    q = annotate(raw)
    assert len(q["knowledge_points"]) == 1
    accepted = q["knowledge_points"][0]
    assert "scope" not in accepted
    assert accepted["role"] == "main" and accepted["evidence"] == "求函数值"
    assert any("可选考查范围格式无效" in warning for warning in q["knowledge_point_warnings"])
    d = q["knowledge_point_diagnostics"]
    assert (d["accepted"], d["invalid"], d["over_budget"], d["merged"]) == (1, 0, 0, 0)
    assert d["adjusted_items"] == [{"index": 1, "code": "demo:goal", "field": "scope", "action": "ignored"}]
    assert raw == frozen


@pytest.mark.parametrize("scope", ["第(1)问", "x" * 200])
def test_valid_scope_remains_exact(scope):
    q = annotate([point(scope=scope)])
    assert q["knowledge_points"][0]["scope"] == scope
    assert q["knowledge_point_diagnostics"]["adjusted_items"] == []


@pytest.mark.parametrize("reason", ["原文理由" * 500, " " * 1000 + "理由", "x" * 1001])
def test_overlong_reason_retains_literal_bounded_text_and_a_verifiable_digest(reason):
    raw = [point(reason=reason)]
    q = annotate(raw)
    retained = q["knowledge_points"][0]["reason"]
    assert retained == reason.lstrip()[:1000] and retained.strip()
    assert retained in reason and len(retained) <= 1000
    adjustment = q["knowledge_point_diagnostics"]["adjusted_items"][0]
    assert adjustment == {"index": 1, "code": "demo:goal", "field": "reason", "action": "truncated",
        "original_length": len(reason), "retained_length": len(retained),
        "original_sha256": hashlib.sha256(reason.encode()).hexdigest()}
    assert any("分类理由过长" in warning for warning in q["knowledge_point_warnings"])
    assert q["knowledge_point_diagnostics"]["rejected"] == 0
    assert raw[0]["reason"] == reason


def test_reason_at_the_limit_is_preserved_without_adjustment():
    q = annotate([point(reason="x" * 1000)])
    assert q["knowledge_points"][0]["reason"] == "x" * 1000
    assert q["knowledge_point_diagnostics"]["adjusted_items"] == []


@pytest.mark.parametrize("reason", [None, [], {}, 4, True, "", " " * 1001, "有效说明\x00", "x" * 1001 + "\x00"])
def test_missing_or_untrusted_reason_still_rejects_the_point(reason):
    q = annotate([point(reason=reason, scope=[])])
    assert q["knowledge_points"] == []
    assert q["knowledge_point_diagnostics"]["invalid"] == 1
    assert q["knowledge_point_diagnostics"]["adjusted_items"] == []


def test_missing_reason_cannot_be_replaced_by_a_model_claimed_diagnostic():
    raw = point()
    del raw["reason"]
    q = annotate([raw], knowledge_point_diagnostics={"accepted": 1, "adjusted_items": ["trusted"]})
    assert q["knowledge_points"] == []
    assert q["knowledge_point_diagnostics"]["accepted"] == 0
    assert q["knowledge_point_diagnostics"]["adjusted_items"] == []


@pytest.mark.parametrize("changes", [
    {"code": "invented"}, {"role": "primary"}, {"evidence": "另一题的原文"},
    {"target_evidence": "另一题的目标"},
    {"role": "auxiliary", "evidence_source": "content"},
    {"role": "auxiliary", "evidence_source": "answer_markdown", "evidence": "【答案】5"},
])
def test_tolerant_explanatory_fields_do_not_bypass_grounding_or_role_checks(changes):
    q = annotate([point(reason="理由" * 1001, scope=[], **changes)], answer_markdown="【答案】5")
    assert q["knowledge_points"] == []
    assert q["knowledge_point_diagnostics"]["invalid"] == 1
    assert q["knowledge_point_diagnostics"]["adjusted_items"] == []


def test_diagnostics_warnings_and_candidate_counts_remain_stable_on_revalidation():
    q = annotate([point(reason="理由" * 1001, scope=[]),
                  point(evidence="求方程的根", target_evidence="求方程的根", scope=None)])
    before = deepcopy(q)
    annotate_ai_knowledge_points(q, [])
    assert q == before
    d = q["knowledge_point_diagnostics"]
    assert (d["requested"], d["accepted"], d["merged"], d["invalid"], d["over_budget"]) == (2, 1, 1, 0, 0)
    assert q["knowledge_points"][0]["target_evidence"] == ["求函数值", "求方程的根"]
    assert "scope" not in q["knowledge_points"][0]


def test_short_real_formula_step_can_support_an_auxiliary_with_unchanged_literal_evidence():
    formula = r"$x^2-1=(x-1)(x+1)$"
    answer = "【解析】" + formula + r"，故方程的根为$-1,1$。"
    q = annotate([point(code="demo:method", role="auxiliary", evidence_source="answer_markdown",
                        evidence=formula, target_evidence="求方程的根")], answer_markdown=answer)
    assert q["knowledge_points"][0]["role"] == "auxiliary"
    assert q["knowledge_points"][0]["evidence"] == formula
    assert q["answer_markdown"] == answer


@pytest.mark.parametrize("answer", [r"【答案】$(x-1)(x+1)$", r"【答案】$x^2-1=(x-1)(x+1)$",
    r"【解析】$x^2-1=(x-1)(x+1)$", r"【答案】$x=-1,1$",
    r"若改问因式分解，则$x^2-1=(x-1)(x+1)$，故方程的根为$-1,1$。"])
def test_factorization_results_and_hypothetical_steps_still_cannot_support_auxiliary(answer):
    q = annotate([point(code="demo:method", role="auxiliary", evidence_source="answer_markdown",
                        evidence=answer, target_evidence="求方程的根")], answer_markdown=answer)
    assert q["knowledge_points"] == []
    assert not q["knowledge_point_diagnostics"]["solution_context"]["has_reasoning"]
