"""Local first-OCR risk hints and existing-verifier gates; no paid requests."""
from copy import deepcopy
import json

import pytest
import requests

from mathbank import pdf_symbol_risks as risks
from mathbank import pdf_source_verify as verify
from mathbank import prompts
from test_pdf_source_verify import setup as verification_setup, checks

TICK = chr(96)

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No real model requests")))


def annotate(content, *, answer="", diagnostics=None):
    questions = [{"content": content, "answer_markdown": answer, "image_paths": ["/static/uploads/test.png"]}]
    original = deepcopy(questions[0])
    diagnostics = diagnostics if diagnostics is not None else {"source_matches": []}
    report = risks.annotate_pdf_symbol_risks(questions, diagnostics)
    assert all(questions[0][key] == value for key, value in original.items())
    return questions[0], diagnostics, report


@pytest.mark.parametrize("content", [
    r"已知圆弧，$AB$是以点$O$为圆心、以$OA$为半径的圆弧。",
    r"已知圆弧$\wideparen{CD}$，则$A_3B_3$的长度为\fillin。",
    r"已知圆弧，$A_{12}B_{12}$长为\fillin。",
    r"已知圆弧，\(AB\)的弧长为\fillin。",
    r"已知圆弧，$$AB$$的长度为\fillin。",
])
def test_arc_context_marks_only_current_bare_formula_without_adding_arc(content):
    question, diagnostics, report = annotate(content)
    assert report["risk_count"] == 1
    item = question["source_review"]["symbol_risks"]["items"][0]
    assert item["kind"] == "arc_mark" and item["field"] == "content"
    start, end = item["span"]
    assert content[start:end] == item["exactformula"]
    assert question["source_review"]["advisory"] is True
    assert risks.ARC_MARK_REASON in question["source_review"]["reasons"]
    assert "source_excerpt" not in question["source_review"] and diagnostics["source_matches"] == []
    assert "expected" not in item and "after_formula" not in item


@pytest.mark.parametrize("content", [
    r"线段$AB$的长度为2。",
    r"已知圆弧，线段$AB$的长度为2。",
    r"已知圆弧，弦$AB$的长度为2。",
    r"已知圆弧，$\wideparen{AB}$的长度为2。",
    r"已知圆弧，$AB+CD$的长度为2。",
    r"已知圆弧，$\mathrm{AB}$的长度为2。",
    r"已知圆弧，$A_{\alpha}B_{\alpha}$的长度为2。",
    "已知圆弧，$A₃B₃$的长度为2。",
    r"已知圆弧，\$AB\$的长度为2。",
    r"直径$AB$，点$d$在图中。",
    r"直径$A_NC_N$，点$n$在图中。",
])
def test_clear_segments_composites_unknown_scripts_and_no_case_conflict_are_unchanged(content):
    question, _diagnostics, report = annotate(content)
    assert report["risk_count"] == 0 and "source_review" not in question


def test_lowercase_point_conflict_is_only_a_visual_risk():
    content = r"圆中直径$AB$，点$b$位于圆上。"
    question, _diagnostics, report = annotate(content)
    assert report["risk_count"] == 1
    assert question["source_review"]["symbol_risks"]["items"][0]["exactformula"] == "$b$"
    assert risks.POINT_CASE_REASON in question["source_review"]["reasons"]
    assert question["content"] == content


@pytest.mark.parametrize("literal", [
    TICK + "圆弧 $AB$ 的长度，点 $B$" + TICK,
    TICK * 3 + "tex\n圆弧 $AB$ 的长度，点 $B$\n" + TICK * 3,
    "~~~tex\n圆弧 $AB$ 的长度，点 $B$\n~~~",
    r"\begin{tikzpicture}\node{圆弧 $AB$ 的长度，点 $B$};\end{tikzpicture}",
    r"\verb|圆弧 $AB$ 的长度，点 $B$|",
    r"\detokenize{圆弧 $AB$ 的长度，点 $B$}",
    r"\url{https://example.test/圆弧$AB$}",
    "https://example.test/圆弧$AB$",
    "[圆弧点B](https://example.test/$AB$)",
    "![](https://example.test/圆弧$AB$.png)",
    '<code>圆弧 $AB$ 的长度，点 $B$</code>',
    '<mathbank-math id="MBM_scope_0001">圆弧 $AB$ 的长度，点 $B$</mathbank-math>',
    "% 圆弧 $AB$ 的长度，点 $B$\n",
])
def test_literal_formula_and_geometry_context_cannot_trigger_real_body(literal):
    question, _diagnostics, report = annotate(literal + r" 之后 $CD$ 的长度为2，直径$AC$，点$b$在图中。")
    assert report["risk_count"] == 0 and "source_review" not in question


def test_risks_do_not_scan_answers():
    question, _diagnostics, report = annotate("普通题目。", answer=r"圆弧$AB$的长度为2。")
    assert report["risk_count"] == 0 and "source_review" not in question


@pytest.mark.parametrize("content", [
    r"圆弧中$AB$$CD$的长度为2。",
    r"直径$AB$，点$B$$b$在图中。",
    r"直径$AB$，https://example.test/点 $b$在图中。",
])
def test_adjacent_math_or_literal_point_cue_cannot_be_skipped(content):
    question, _diagnostics, report = annotate(content)
    items = question.get("source_review", {}).get("symbol_risks", {}).get("items", [])
    if content.startswith("圆弧"):
        assert len(items) == 1 and items[0]["exactformula"] == "$CD$"
    else:
        assert report["risk_count"] == 0


def state_for_risk(state, content=r"14. 已知圆弧，$A_3B_3$的长度为\fillin。"):
    state.questions = [{"content": content, "answer_markdown": ""}]
    state.diagnostics = {"source_matches": [{"question_index": 0, "field": "content",
        "source_number": 14, "source_start": 0, "source_end": len(content), "source_excerpt": content}],
        "pdf_review_items": []}
    risks.annotate_pdf_symbol_risks(state.questions, state.diagnostics)
    state.diagnostics["pdf_review_items"] = [{"question_index": 0, "source_number": 14,
        "source_pages": [1], "reasons": list(state.questions[0]["source_review"]["reasons"])}]
    state.response["choices"][0]["message"]["content"] = json.dumps({"items": [{
        "id": "item_001", "source_number": 14, "source_pages": [1], "decision": "equivalent",
        "checks": checks(), "evidence": "原页当前记号与输出一致，没有据词义补线。"}]})
    return state


def cache_for(state):
    return [{"page_number": number, "origin": "joint_vision", "markdown": state.questions[0]["content"] if number == 1 else "",
             "figures": []} for number in state.numbers]


def test_same_first_ocr_and_final_text_still_enters_existing_one_batch(verification_setup):
    state = state_for_risk(verification_setup)
    original = state.questions[0]["content"]
    source_matches = deepcopy(state.diagnostics["source_matches"])
    candidate = verify._candidate(0, state.questions[0], state.diagnostics)
    assert candidate["source_excerpt"] == original
    assert candidate["visual_symbol_risks"]["items"][0]["exactformula"] == "$A_3B_3$"
    report = verify.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, state.numbers,
                                               source_pages=cache_for(state))
    assert report["calls"] == report["checked"] == report["confirmed"] == 1
    assert state.questions[0]["content"] == original and state.diagnostics["source_matches"] == source_matches
    sent = state.prompt_items[0][0]
    assert sent["visual_symbol_risks"]["items"] and sent["source_excerpt"] == sent["output"]
    assert state.calls[0][2]["retry_connection"] is False
    assert state.calls[0][1]["max_tokens"] == 4096


def test_legal_lowercase_point_can_be_equivalent_without_case_rewrite(verification_setup):
    state = state_for_risk(verification_setup, r"14. 直径$AB$，点$b$是另外一个点。")
    original = state.questions[0]["content"]
    report = verify.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, state.numbers)
    assert report["confirmed"] == 1 and len(state.calls) == 1
    assert state.questions[0]["content"] == original and "$b$" in state.questions[0]["content"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown_number", "bad_range", "shared_range"])
def test_no_unique_source_match_creates_no_excerpt_or_number(verification_setup, change):
    state = verification_setup
    content = r"14. 圆弧$AB$的长度为\fillin。"
    match = {"question_index": 0, "field": "content", "source_number": 14,
             "source_start": 0, "source_end": len(content), "source_excerpt": content}
    matches = [match]
    if change == "missing": matches = []
    elif change == "duplicate": matches.append(deepcopy(match))
    elif change == "unknown_number": match["source_number"] = None
    elif change == "bad_range": match["source_end"] += 1
    else: matches.append({**match, "question_index": 1})
    state.questions = [{"content": content, "answer_markdown": ""}]
    state.diagnostics = {"source_matches": matches, "pdf_review_items": []}
    before = deepcopy(matches)
    risks.annotate_pdf_symbol_risks(state.questions, state.diagnostics)
    assert "source_excerpt" not in state.questions[0]["source_review"]
    assert state.diagnostics["source_matches"] == before
    state.diagnostics["pdf_review_items"] = [{"question_index": 0, "source_number": None,
        "source_pages": [1], "reasons": state.questions[0]["source_review"]["reasons"]}]
    report = verify.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, state.numbers)
    assert report["calls"] == 0 and not state.calls and state.questions[0]["source_review"]["advisory"]


def test_wrong_page_or_bracket_heading_does_not_bypass_original_gates(verification_setup):
    state = state_for_risk(verification_setup)
    state.diagnostics["pdf_review_items"][0]["source_pages"] = [2]
    report = verify.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, state.numbers,
                                               source_pages=cache_for(state))
    assert report["calls"] == 0 and not state.calls
    state = state_for_risk(state, r"(14) 圆弧$AB$的长度为\fillin。")
    assert verify._candidate(0, state.questions[0], state.diagnostics) is None


@pytest.mark.parametrize("change", ["content", "span", "formula", "source", "unknown_reason", "forged_reason"])
def test_stale_or_forged_risks_never_make_candidate_eligible(verification_setup, change):
    state = state_for_risk(verification_setup)
    question = state.questions[0]
    if change == "content": question["content"] += " "
    elif change == "span": question["source_review"]["symbol_risks"]["items"][0]["span"][0] += 1
    elif change == "formula": question["source_review"]["symbol_risks"]["items"][0]["exactformula"] = "$CD$"
    elif change == "source": state.diagnostics["source_matches"][0]["source_excerpt"] += " "
    elif change == "unknown_reason": question["source_review"]["reasons"].append("仍有未知公式编号，不能确认。")
    else: question["source_review"].pop("symbol_risks")
    assert verify._candidate(0, question, state.diagnostics) is None
    report = verify.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, state.numbers)
    assert report["calls"] == 0 and not state.calls


def test_prompt_keeps_risk_even_when_difference_focus_empty_and_drops_edited_draft_hints(verification_setup):
    state = state_for_risk(verification_setup)
    candidate = verify._candidate(0, state.questions[0], state.diagnostics)
    prompt = prompts.build_pdf_source_verification_prompt([candidate])
    sent = json.loads(prompt.split("\n")[-1])["items"][0]
    assert sent["review_focus"]["fields"]["content"]["items"] == []
    assert sent["visual_symbol_risks"]["items"]
    assert "不能凭圆弧词义补顶线" in prompt and "按答案倒推" in prompt
    candidate["output"] += " "
    changed = prompts.build_pdf_source_verification_prompt([candidate])
    assert "visual_symbol_risks" not in json.loads(changed.split("\n")[-1])["items"][0]


def test_annotation_is_idempotent_bounded_and_preserves_unknown_review_evidence():
    content = "圆弧：" + "；".join(f"$A_{{{n}}}B_{{{n}}}$的长度为2" for n in range(1, 12))
    question, diagnostics, report = annotate(content)
    assert report["risk_count"] == risks.MAX_RISKS and report["omitted"] == 3
    snapshot = deepcopy(question)
    risks.annotate_pdf_symbol_risks([question], diagnostics)
    assert question == snapshot
    question["source_review"]["reasons"].append("未知来源问题不能消除。")
    risks.annotate_pdf_symbol_risks([question], diagnostics)
    assert "未知来源问题不能消除。" in question["source_review"]["reasons"]
    assert not verify._reviewable_reasons(question["source_review"]["reasons"])


@pytest.mark.parametrize("review", ["invalid review", {"reasons": "unknown"}, {"reasons": [{"unknown": True}]}])
def test_malformed_existing_review_cannot_be_replaced_with_eligible_risk(review):
    question = {"content": r"圆弧$AB$的长度为2。", "source_review": deepcopy(review)}
    original = deepcopy(question)
    risks.annotate_pdf_symbol_risks([question], {"source_matches": []})
    assert question == original


@pytest.mark.parametrize("content", [
    r"圆弧[[MBM_scope_0001]]，$AB$的长度为2。",
    r'圆弧<mathbank-math id="MBM_scope_0001">$AB$的长度为2。',
])
def test_unrestored_complete_or_partial_lock_protocol_is_not_scanned(content):
    question, _diagnostics, report = annotate(content)
    assert report["risk_count"] == 0 and "source_review" not in question
    assert report["unavailable"][0]["reason"] == "reference_protocol_pending"
