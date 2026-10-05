"""Isolated source-backed repair rounds; no model, network or user document."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import requests

from mathbank import pdf_source_verify, source_review_repair as repair
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.source_review_results import parse_review_response
from mathbank.task_manager import TaskCancelled


CHECKS = {"same_question", "complete_content", "math_and_conditions", "options_and_subquestions", "figures", "answer"}


def candidate(index=1, content="已知 $x-1=2$，求值。", answer="", original_answer=""):
    return {"id": f"item_{index:03d}", "question_index": index - 1, "source_number": index, "source_pages": [1],
            "original": {"content": f"{index}. 已知 $x+1=2$，求值。", "answer_markdown": original_answer},
            "output": {"content": content, "answer_markdown": answer}}


def verdict(item, decision="different", **checks):
    defaults = {key: True for key in CHECKS}
    if decision != "equivalent":
        defaults["math_and_conditions"] = False
    return {"id": item["id"], "source_number": item["source_number"], "source_pages": item["source_pages"],
            "decision": decision, "checks": {**defaults, **checks}, "evidence": "原页同题中的正负号与候选题文不同。"}


def proposal(item, before="$x-1=2$", after="$x+1=2$", field="content", **updates):
    return {"id": item["id"], "source_number": item["source_number"], "source_pages": item["source_pages"],
            "patches": [{"field": field, "before": before, "after": after}],
            "evidence": "原页第1题清晰可见为加号，修正提取的负号。", **updates}


def response(entries, *, finish="stop", status=200, raw=None):
    body = {"choices": [{"finish_reason": finish, "message": {"content": raw if raw is not None
                                                               else json.dumps({"items": entries}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
    return SimpleNamespace(status_code=status, json=lambda: deepcopy(body))


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *args, **kwargs: pytest.fail("Live network is forbidden in repair unit tests"))
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused-test-key",
                                                 "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3-VL-8B-Instruct"})
    state = SimpleNamespace(provider=provider, items=[candidate()], responses=[], calls=[], verified=[], validated=[],
                            progress=[], evidence_version=0, evidence_checks=0, on_request=lambda: None,
                            on_evidence=lambda: None, cancelled=False)

    def request(config, payload, **kwargs):
        state.calls.append((config, deepcopy(payload), kwargs))
        state.on_request()
        assert state.responses, "Unexpected extra repair/referee request"
        reply = state.responses.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def attachments(items):
        return [{"type": "text", "text": "原始文档第1页"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,dGVzdA=="}}]

    def verification_content(items):
        state.verified.append(deepcopy(items))
        return [{"type": "text", "text": "独立原页核验，仅查看完整候选：" + json.dumps(items, ensure_ascii=False)},
                *attachments(items)]

    def validate_output(item, output):
        state.validated.append((deepcopy(item), deepcopy(output)))

    def check_evidence():
        state.evidence_checks += 1
        state.on_evidence()
        if state.evidence_version:
            raise repair.RepairStopped("原页或原题文证据已发生变化")

    def check_cancelled():
        if state.cancelled:
            raise TaskCancelled("test cancelled")

    state.arguments = dict(provider=provider, request=request, attachments=attachments,
                           verification_content=verification_content,
                           parse_verdicts=lambda raw, items: pdf_source_verify._valid_decisions(parse_review_response(raw), items)[0],
                           validate_output=validate_output, check_evidence=check_evidence,
                           check_cancelled=check_cancelled, progress=state.progress.append)
    return state


def run(state, *, decisions=None, budget=None, **kwargs):
    if decisions is None:
        decisions = {item["id"]: verdict(item) for item in state.items}
    return repair.repair_verified_differences(state.items, decisions,
                                              budget=budget if budget is not None else {"remaining": repair.MAX_EXTRA_CALLS},
                                              **{**state.arguments, **kwargs})


def test_exact_patch_needs_fresh_independent_complete_verdict_before_returning_output(state):
    item = state.items[0]
    patch = proposal(item, evidence="REPAIRER_CONCLUSION_NOT_REFEREE_EVIDENCE")
    state.responses = [response([patch]), response([verdict(item, "equivalent")])]
    original = deepcopy(state.items)
    budget = {"remaining": repair.MAX_EXTRA_CALLS}
    result = run(state, budget=budget)
    assert state.items == original
    assert result["outputs"] == {item["id"]: {"content": "已知 $x+1=2$，求值。", "answer_markdown": ""}}
    assert result["decisions"][item["id"]]["decision"] == "equivalent"
    assert result["calls"] == 2 and result["repair_calls"] == result["recheck_calls"] == 1
    assert all(kwargs["timeout"] == 120 for _provider, _payload, kwargs in state.calls)
    assert result["usage"] == {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240}
    assert budget["remaining"] == repair.MAX_EXTRA_CALLS - 2
    audit = result["repairs"][item["id"]]
    assert audit["status"] == "confirmed" and audit["attempts"] == 1
    assert audit["history"][0]["decision"] == "equivalent"
    assert audit["history"][0]["output_sha256"] == repair.output_hash(result["outputs"][item["id"]])
    referee_text = json.dumps(state.calls[1][1], ensure_ascii=False)
    assert "REPAIRER_CONCLUSION_NOT_REFEREE_EVIDENCE" not in referee_text
    assert "$x+1=2$" in referee_text and "原始文档第1页" in referee_text
    assert all(kwargs["retry_connection"] is False for _, _, kwargs in state.calls)
    assert all(payload["max_tokens"] == 4096 for _, payload, _ in state.calls)


def three_round_responses(item):
    values = ["$x-1=2$", "$x+1=2$", "$x+2=2$", "$x+3=2$"]
    return [reply for before, after in zip(values, values[1:])
            for reply in (response([proposal(item, before, after)]), response([verdict(item)]))]


def test_three_round_exhaustion_never_commits_any_unconfirmed_draft(state):
    state.responses = three_round_responses(state.items[0])
    original = deepcopy(state.items)
    result = run(state)
    assert result["outputs"] == result["decisions"] == {} and state.items == original
    assert result["calls"] == 6 and result["repair_calls"] == result["recheck_calls"] == 3
    audit = result["repairs"][state.items[0]["id"]]
    assert audit["status"] == "exhausted" and audit["attempts"] == 3
    assert [entry["round"] for entry in audit["history"]] == [1, 2, 3]
    assert all(entry["decision"] == "different" for entry in audit["history"])


@pytest.mark.parametrize("mode", ["no_op", "cycle"])
def test_no_op_or_repeated_draft_stops_without_repeating_referee(state, mode):
    item = state.items[0]
    if mode == "no_op":
        state.responses = [response([proposal(item, after="$x-1=2$")])]
    else:
        state.responses = [response([proposal(item)]), response([verdict(item)]),
                           response([proposal(item, "$x+1=2$", "$x-1=2$")])]
    result = run(state)
    assert not result["outputs"] and result["calls"] == (1 if mode == "no_op" else 3)
    assert result["recheck_calls"] == (0 if mode == "no_op" else 1)
    assert result["repairs"][item["id"]]["status"] == "stopped"


@pytest.mark.parametrize("mode", ["uncertain", "equivalent", "wrong_identity", "contradictory", "local_block"])
def test_unusable_initial_verdict_never_starts_correction(state, mode):
    item = state.items[0]
    decision = verdict(item, mode if mode in {"uncertain", "equivalent"} else "different")
    if mode == "wrong_identity": decision["checks"]["same_question"] = False
    elif mode == "contradictory": decision["checks"] = {key: True for key in CHECKS}
    elif mode == "local_block": item["repair_block_reason"] = "原版答案来源不唯一，无法修正。"
    result = run(state, decisions={item["id"]: decision})
    assert not state.calls and result["calls"] == 0 and result["outputs"] == {}
    if mode in {"uncertain", "equivalent"}:
        assert result["repairs"] == {}
    else:
        assert result["repairs"][item["id"]]["reason"]


@pytest.mark.parametrize("kind", ["missing", "duplicate", "missing_fields", "bad_patch"])
def test_malformed_proposal_only_stops_its_identified_question(state, kind):
    state.items.append(candidate(2))
    good, bad = map(proposal, state.items)
    proposals = [good, bad]
    if kind == "missing": proposals.pop()
    elif kind == "duplicate": proposals.append(deepcopy(bad))
    elif kind == "missing_fields": bad.pop("source_pages")
    elif kind == "bad_patch": bad["patches"][0]["field"] = "question_type"
    state.responses = [response(proposals), response([verdict(state.items[0], "equivalent")])]
    result = run(state)
    assert set(result["outputs"]) == {"item_001"} and result["calls"] == 2
    assert result["repairs"]["item_002"]["status"] == "stopped"
    assert [item["id"] for item in state.verified[0]] == ["item_001"]


def test_incomplete_referee_result_preserves_valid_peer_and_original_failed_item(state):
    state.items.append(candidate(2))
    original = deepcopy(state.items)
    state.responses = [response([proposal(item) for item in state.items]),
                       response([verdict(state.items[0], "equivalent")])]
    result = run(state)
    assert set(result["outputs"]) == {"item_001"} and result["calls"] == 2
    assert result["repairs"]["item_002"]["status"] == "failed" and state.items == original


@pytest.mark.parametrize("phase", ["repair", "recheck"])
@pytest.mark.parametrize("failure", ["unknown_id", "truncated", "transport", "invalid_json", "http"])
def test_envelope_or_request_failure_never_retries_or_commits(state, phase, failure):
    item = state.items[0]
    entry = proposal(item) if phase == "repair" else verdict(item, "equivalent")
    if failure == "unknown_id":
        entry["id"] = "unknown_item"
        failed = response([entry])
    elif failure == "truncated": failed = response([entry], finish="length")
    elif failure == "transport": failed = requests.Timeout("SECRET_PROVIDER_TOKEN")
    elif failure == "invalid_json": failed = response([], raw='{"items":[')
    else: failed = response([entry], status=503)
    state.responses = ([response([proposal(item)])] if phase == "recheck" else []) + [failed]
    original = deepcopy(state.items)
    result = run(state)
    assert result["outputs"] == {} and result["decisions"] == {} and state.items == original
    assert result["calls"] == len(state.calls) == (1 if phase == "repair" else 2)
    assert result["repairs"][item["id"]]["status"] == "failed"
    assert "SECRET_PROVIDER_TOKEN" not in json.dumps(result, ensure_ascii=False)
    assert all(kwargs["retry_connection"] is False for _, _, kwargs in state.calls)


def test_twelve_call_budget_is_shared_across_batches_and_reserves_a_referee_call(state):
    budget = {"remaining": repair.MAX_EXTRA_CALLS}
    results = []
    for _ in range(3):
        state.responses = three_round_responses(state.items[0])
        results.append(run(state, budget=budget))
    assert [result["calls"] for result in results] == [6, 6, 0]
    assert len(state.calls) == repair.MAX_EXTRA_CALLS == 12 and budget["remaining"] == 0
    assert "调用额度" in results[-1]["repairs"]["item_001"]["reason"]
    assert run(state, budget={"remaining": 1})["calls"] == 0


@pytest.mark.parametrize("phase", ["before", "repair", "recheck"])
def test_cancellation_propagates_without_committing_or_retrying(state, phase):
    state.responses = [response([proposal(state.items[0])]), response([verdict(state.items[0], "equivalent")])]
    original = deepcopy(state.items)
    if phase == "before": state.cancelled = True
    else:
        def cancel():
            if len(state.calls) == (1 if phase == "repair" else 2): state.cancelled = True
        state.on_request = cancel
    with pytest.raises(TaskCancelled):
        run(state)
    assert state.items == original and len(state.calls) == {"before": 0, "repair": 1, "recheck": 2}[phase]


@pytest.mark.parametrize("phase", ["repair", "recheck", "final"])
def test_source_or_page_mutation_invalidates_even_an_equivalent_draft(state, phase):
    item = state.items[0]
    state.responses = [response([proposal(item)]), response([verdict(item, "equivalent")])]
    original = deepcopy(state.items)
    if phase == "final":
        def mutate():
            if state.evidence_checks == 5: state.evidence_version += 1
        state.on_evidence = mutate
    else:
        def mutate():
            if len(state.calls) == (1 if phase == "repair" else 2): state.evidence_version += 1
        state.on_request = mutate
    result = run(state)
    assert result["invalidated"] is True and result["outputs"] == result["decisions"] == {}
    assert state.items == original and result["repairs"][item["id"]]["status"] == "failed"


def test_later_mutation_discards_earlier_confirmed_peer_in_same_repair_batch(state):
    state.items.append(candidate(2))
    first, second = state.items
    state.responses = [response([proposal(first), proposal(second)]),
                       response([verdict(first, "equivalent"), verdict(second)]),
                       response([proposal(second, "$x+1=2$", "$x+2=2$")])]
    def mutate():
        if len(state.calls) == 3: state.evidence_version += 1
    state.on_request = mutate
    result = run(state)
    assert result["invalidated"] is True and result["outputs"] == {}
    assert all(audit["status"] == "failed" for audit in result["repairs"].values())


@pytest.mark.parametrize("change", ["add", "drop", "replace", "literal"])
def test_patch_cannot_change_visible_image_identity_or_occurrence_count(state, change):
    reference = "![图](/static/uploads/tmp/figure.png)"
    item = state.items[0]
    item["output"]["content"] += reference
    before = reference
    replacements = {"add": reference * 2, "drop": "", "replace": "![图](/static/uploads/tmp/other.png)",
                    "literal": "`" + reference + "`"}
    state.responses = [response([proposal(item, before, replacements[change])])]
    result = run(state)
    assert result["outputs"] == {} and result["calls"] == 1 and result["recheck_calls"] == 0
    assert "图片" in result["repairs"][item["id"]]["reason"]


def test_patch_cannot_generate_or_rewrite_an_answer_without_original_answer(state):
    item = state.items[0]
    item["output"]["answer_markdown"] = "模型猜出的答案"
    state.responses = [response([proposal(item, "模型猜出的答案", "$x=1$", "answer_markdown")])]
    result = run(state)
    assert result["calls"] == 1 and result["recheck_calls"] == 0 and not result["outputs"]
    assert "原文没有" in result["repairs"][item["id"]]["reason"]


@pytest.mark.parametrize("mode", ["duplicate_before", "overlapping_before", "missing_before", "overlap"])
def test_replacement_requires_exact_unique_nonoverlapping_input_spans(state, mode):
    item = state.items[0]
    patch = proposal(item)
    if mode == "duplicate_before": item["output"]["content"] += "另有 $x-1=2$。"
    elif mode == "overlapping_before":
        item["output"]["content"] = "已知 $x=111$，求值。"
        patch["patches"][0].update(before="11", after="12")
    elif mode == "missing_before": patch["patches"][0]["before"] = "$x-100=2$"
    else: patch["patches"].append({"field": "content", "before": "x-1", "after": "x+2"})
    original = deepcopy(state.items)
    state.responses = [response([patch])]
    result = run(state)
    assert result["calls"] == 1 and result["recheck_calls"] == 0 and result["outputs"] == {}
    assert state.items == original and result["repairs"][item["id"]]["status"] == "stopped"


def test_local_candidate_guard_rejects_proposed_output_before_referee(state):
    state.responses = [response([proposal(state.items[0])])]
    def reject(item, output):
        raise repair.RepairStopped("修正后来源范围无法唯一对应。")
    result = run(state, validate_output=reject)
    assert result["calls"] == 1 and result["recheck_calls"] == 0 and not result["outputs"]
    assert "来源范围" in result["repairs"]["item_001"]["reason"]


def test_full_repair_prompt_and_attachment_text_are_bounded_before_request(state):
    result = run(state, max_chars=10)
    assert result["calls"] == 0 and not state.calls and not result["outputs"]
    assert "文字额度" in result["repairs"]["item_001"]["reason"]


@pytest.mark.parametrize("decision", ["uncertain", "wrong_identity"])
def test_uncertain_referee_stops_further_correction_rounds(state, decision):
    item = state.items[0]
    check = verdict(item, "uncertain" if decision == "uncertain" else "different")
    if decision == "wrong_identity": check["checks"]["same_question"] = False
    state.responses = [response([proposal(item)]), response([check])]
    result = run(state)
    assert result["calls"] == 2 and not result["outputs"]
    assert result["repairs"][item["id"]]["status"] == "stopped"


@pytest.mark.parametrize("glyph", ["\ue000", "\U000f0000", "\U00100000"])
def test_patch_cannot_introduce_undecoded_private_use_glyphs_even_if_referee_would_approve(state, glyph):
    item = state.items[0]
    state.responses = [response([proposal(item, after=f"$x={glyph}$")]), response([verdict(item, "equivalent")])]
    result = run(state)
    assert not result["outputs"] and result["calls"] == 1 and result["recheck_calls"] == 0
    assert "缺字" in result["repairs"][item["id"]]["reason"]
