"""PDF metadata adds catalogue points in its existing request, preserving source."""
from copy import deepcopy
import hashlib
import json

import pytest

from mathbank.pdf_draft_metadata import (
    build_pdf_draft_metadata_messages, request_pdf_local_draft_metadata,
)
from test_pdf_draft_metadata import Cache, NORMAL, Response, TREE, plan, response_rows


CATALOG = [{"code": "1015", "name": "函数概念", "original_name": "函数概念",
            "aliases": ["函数的概念"]}]


@pytest.fixture(autouse=True)
def offline_catalog_and_transport(monkeypatch):
    monkeypatch.setattr("mathbank.pdf_draft_metadata.load_ai_knowledge_catalog", lambda: deepcopy(CATALOG))
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *_args, **_kwargs: pytest.fail("No live model or database fixture"))


def suggestion(evidence="原题1", **changes):
    return {"code": "1015", "role": "main", "evidence_source": "content",
            "evidence": evidence, "reason": "直接考查函数概念", **changes}


def run(post, *, draft=None, catalog=None, **options):
    return request_pdf_local_draft_metadata(draft or plan(1), TREE, provider=NORMAL,
        post=post, knowledge_catalog=CATALOG if catalog is None else catalog, **options)


def body_fields(questions):
    optional = {"question_type", "difficulty", "category_compulsory", "category_chapter",
                "category_knowledge", "classification_review", "knowledge_points", "knowledge_point_warnings",
                "knowledge_point_diagnostics"}
    return [{key: value for key, value in question.items() if key not in optional} for question in questions]


def source_bound_plan(*answers):
    """Build real byte ranges, unlike the generic metadata-only fixture."""
    draft = plan(len(answers))
    chunks = []
    for index, answer in enumerate(answers):
        question, item = draft["questions"][index], draft["items"][index]
        question["answer_markdown"] = answer
        item["answer_markdown"] = answer
        item["question"] = deepcopy(question)
        chunks.extend([question["content"], "\n", answer, "\n"])
    draft["source"] = "".join(chunks)
    draft["input_sha256"] = draft["source_sha256"] = hashlib.sha256(draft["source"].encode()).hexdigest()
    cursor = 0
    for item, answer in zip(draft["items"], answers):
        start = draft["source"].index(answer, cursor)
        item["answer_ranges"] = [[start, start + len(answer)]]
        item["raw_answer"] = answer
        cursor = start + len(answer)
    return draft


@pytest.mark.parametrize("compact", [False, True])
def test_source_bound_answer_is_sent_as_unverified_transcription_in_both_protocols(compact):
    draft = source_bound_plan("原解先用函数的定义，再求定义域。")
    original = deepcopy(draft)
    messages = build_pdf_draft_metadata_messages(draft, TREE, compact=compact, knowledge_catalog=CATALOG)
    item = json.loads(messages[1]["content"])["items"][0]
    assert item["answer_markdown"] == draft["questions"][0]["answer_markdown"]
    assert item["answer_context"] == {"source_basis": "first_pass_transcription", "original_verified": False}
    assert "未提供解析时不得猜测" in messages[0]["content"]
    assert "没有提供图片像素" in messages[0]["content"]
    assert draft == original


def test_source_bound_answer_evidence_uses_the_existing_post_and_preserves_saved_fields():
    answer = "原解先使用函数的定义，再求定义域。"
    draft = source_bound_plan(answer); frozen = deepcopy(draft); calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        item = json.loads(payload["messages"][1]["content"])["items"][0]
        assert item["answer_markdown"] == answer
        value = response_rows(payload)
        value["items"][0]["knowledge_points"] = [suggestion(evidence_source="answer_markdown",
            evidence="使用函数的定义", role="auxiliary")]
        return Response(value)
    result = run(post, draft=draft)
    assert len(calls) == 1
    point = result["questions"][0]["knowledge_points"][0]
    assert point["evidence_source"] == "answer_markdown" and point["evidence"] == "使用函数的定义"
    assert result["questions"][0]["knowledge_point_warnings"] == []
    assert result["questions"][0]["knowledge_point_diagnostics"]["outcome"] == "accepted"
    assert body_fields(result["questions"]) == body_fields(frozen["questions"])
    assert draft == frozen


@pytest.mark.parametrize("change", ["generated_answer", "marker_only", "claimed_verified", "invalid_ranges"])
def test_unbound_answers_and_public_source_claims_never_become_original_solution_context(change):
    draft = source_bound_plan("原解确实使用函数的定义。")
    item, question = draft["items"][0], draft["questions"][0]
    if change == "generated_answer":
        question["answer_markdown"] = "AI新解使用导数。"
    elif change == "marker_only":
        question["answer_markdown"] = "[EXTRACTED_ORIGINAL]AI新解使用导数。"
    elif change == "claimed_verified":
        question["source_review"] = {"original_verified": True, "source_basis": "teacher_confirmed"}
        item["answer_ranges"] = []
    else:
        item["answer_ranges"] = [[0, len(draft["source"]) + 1]]
    item["answer_markdown"] = question["answer_markdown"]
    item["question"] = deepcopy(question)
    messages = build_pdf_draft_metadata_messages(draft, TREE, knowledge_catalog=CATALOG)
    sent = json.loads(messages[1]["content"])["items"][0]
    assert "answer_markdown" not in sent and "answer_context" not in sent


def test_source_bound_historical_prefix_and_multiple_ranges_preserve_exact_answer():
    answer = "先用函数定义。\n\n再求定义域。"
    draft = source_bound_plan(answer)
    item, question = draft["items"][0], draft["questions"][0]
    start, end = item["answer_ranges"][0]
    split = start + len("先用函数定义。")
    item["answer_ranges"] = [[start, split], [split, split + 2], [split + 2, end]]
    question["answer_markdown"] = "[EXTRACTED_ORIGINAL]" + answer
    item["answer_markdown"] = question["answer_markdown"]
    item["question"] = deepcopy(question)
    messages = build_pdf_draft_metadata_messages(draft, TREE, knowledge_catalog=CATALOG)
    sent = json.loads(messages[1]["content"])["items"][0]
    assert sent["answer_markdown"] == question["answer_markdown"]
    assert sent["answer_context"]["original_verified"] is False


@pytest.mark.parametrize("workflow", ["local_v1", "page_fragments_v2"])
def test_actual_local_builders_supply_cross_page_source_bound_answers(workflow):
    if workflow == "local_v1":
        from test_pdf_local_draft import draft as make_draft
        _, _, draft = make_draft(["一、解答题\n1. 求函数定义域。\n2. 第二题。",
            "# 参考答案\n1. 使用函数定义，\n数学 参考答案 第1页 共2页",
            "续原解确定定义域。\n2. 第二题原解。\n数学 参考答案 第2页 共2页"])
    else:
        from test_pdf_fragment_draft import block, build, page
        _, draft = build([page(1, [block("stem", 1, "1. 求函数定义域。\n"),
            block("stem", 2, "2. 第二题。\n"),
            block("answer", 1, "1. 使用函数定义，\n", "to_next"),
            block("furniture", None, "参考答案第1页\n")]),
            page(2, [block("furniture", None, "参考答案第2页\n"),
                block("answer", None, "续原解确定定义域。\n", "from_previous"),
                block("answer", 2, "2. 第二题原解。\n")])])
    frozen = deepcopy(draft)
    messages = build_pdf_draft_metadata_messages(draft, TREE, compact=True, knowledge_catalog=CATALOG)
    sent = json.loads(messages[1]["content"])["items"]
    assert len(sent) == 2
    assert "续原解确定定义域。" in sent[0]["answer_markdown"]
    assert "参考答案第" not in sent[0]["answer_markdown"]
    assert [item["answer_markdown"] for item in sent] == [q["answer_markdown"] for q in draft["questions"]]
    assert draft == frozen


def test_answer_evidence_cannot_cross_cards_or_read_unprovided_review():
    draft = source_bound_plan("第一题原解只用函数的定义。", "第二题原解改用导数。")
    draft["questions"][0]["review_markdown"] = "仅供本地留存的评析。"
    draft["items"][0]["question"] = deepcopy(draft["questions"][0])
    calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        value = response_rows(payload)
        for row in value["items"]:
            if row["id"] == "PLD_card_1":
                row["knowledge_points"] = [suggestion(evidence_source="answer_markdown", evidence="第二题原解改用导数。"),
                    suggestion(evidence_source="review_markdown", evidence="仅供本地留存的评析。")]
        return Response(value)
    result = run(post, draft=draft)
    assert len(calls) == 1
    assert not result["questions"][0]["knowledge_points"]
    assert len(result["questions"][0]["knowledge_point_warnings"]) == 2
    assert body_fields(result["questions"]) == body_fields(draft["questions"])


def test_answer_context_changes_invalidate_only_affected_card_cache():
    cache = Cache(); calls = []
    def post(_provider, payload, **_options):
        calls.append(deepcopy(payload))
        return Response(response_rows(payload))
    draft = source_bound_plan("第一题原解使用函数定义。", "第二题原解使用函数定义。")
    run(post, draft=draft, cache=cache)
    run(lambda *_a, **_kw: pytest.fail("Same answer context must replay"), draft=draft, cache=cache)
    changed = source_bound_plan("第一题原解改用单调性的定义。", "第二题原解使用函数定义。")
    result = run(post, draft=changed, cache=cache)
    assert len(calls) == 2
    assert [item["id"] for item in json.loads(calls[-1]["messages"][1]["content"])["items"]] == ["PLD_card_1"]
    assert result["diagnostics"]["pdf_draft_metadata"]["reused_cards"] == ["PLD_card_2"]
    assert body_fields(result["questions"]) == body_fields(changed["questions"])


def test_old_stem_only_cache_is_not_reused_when_source_answer_becomes_available():
    from test_pdf_draft_metadata import seed_legacy_card_cache
    cache = Cache(); draft = source_bound_plan("原解使用函数的定义。")
    seed_legacy_card_cache(cache, draft)
    calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        return Response(response_rows(payload))
    result = run(post, draft=draft, cache=cache)
    assert len(calls) == 1
    assert not result["diagnostics"]["pdf_draft_metadata"]["reused_cards"]
    assert not result["diagnostics"]["pdf_draft_metadata"]["migrated_cards"]


def test_compact_points_share_one_post_and_preserve_all_source_fields():
    draft = plan(1); frozen = deepcopy(draft); calls = []
    def post(_provider, payload, **_options):
        calls.append(deepcopy(payload))
        item = json.loads(payload["messages"][1]["content"])["items"][0]
        assert "knowledge_points数组" in payload["messages"][0]["content"]
        assert "1015" in payload["messages"][0]["content"]
        assert "原版答案" not in payload["messages"][1]["content"]
        return Response({"items": [[item["key"], 0, 2, 2, [suggestion()]]]})
    result = run(post, draft=draft)
    assert len(calls) == 1
    point = result["questions"][0]["knowledge_points"][0]
    assert point["code"] == "1015" and point["status"] == "confirmed"
    assert point["origin"] == "ai_classification" and point["evidence"] == "原题1"
    assert body_fields(result["questions"]) == body_fields(frozen["questions"])
    assert result["source_matches"] == frozen["source_matches"] and draft == frozen


@pytest.mark.parametrize("points", [None, {}, [suggestion(code="unknown")],
    [suggestion(), suggestion()], [suggestion(evidence="不存在的题干")],
    [suggestion(evidence_source="answer_markdown", evidence="原版答案1")]])
def test_invalid_optional_points_never_repeat_post_or_discard_saved_body(points):
    calls = []; draft = plan(1)
    def post(_provider, payload, **_options):
        calls.append(payload)
        value = response_rows(payload)
        value["items"][0]["knowledge_points"] = points
        return Response(value)
    result = run(post, draft=draft)
    assert len(calls) == 1
    question = result["questions"][0]
    assert question["knowledge_point_warnings"]
    assert question["category_chapter"] == "3. 函数"
    assert body_fields(result["questions"]) == body_fields(draft["questions"])
    assert not result["diagnostics"]["pdf_draft_metadata"]["metadata_failed"]


def test_invalid_fifth_compact_column_is_optional_and_does_not_retry():
    calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        item = json.loads(payload["messages"][1]["content"])["items"][0]
        return Response({"items": [[item["key"], 0, 2, 2, "bad knowledge array"]]})
    result = run(post)
    assert len(calls) == 1
    assert result["questions"][0]["knowledge_points"] == []
    assert result["questions"][0]["knowledge_point_warnings"]


def test_old_four_column_reply_preserves_existing_selected_points():
    draft = plan(1)
    draft["questions"][0]["knowledge_points"] = [{"code": "1015", "status": "confirmed", "role": "auxiliary"}]
    draft["items"][0]["question"] = deepcopy(draft["questions"][0])
    def post(_provider, payload, **_options):
        item = json.loads(payload["messages"][1]["content"])["items"][0]
        return Response({"items": [[item["key"], 0, 2, 2]]})
    result = run(post, draft=draft)
    assert result["questions"][0]["knowledge_points"] == draft["questions"][0]["knowledge_points"]
    assert result["diagnostics"]["pdf_draft_metadata"]["knowledge_point_outcomes"] == {"missing_field": 1}
    assert "knowledge_point_diagnostics" not in result["questions"][0]


def test_explicit_empty_points_are_distinguished_from_missing_legacy_column():
    def post(_provider, payload, **_options):
        item = json.loads(payload["messages"][1]["content"])["items"][0]
        return Response({"items": [[item["key"], 0, 2, 2, []]]})
    result = run(post)
    assert result["diagnostics"]["pdf_draft_metadata"]["knowledge_point_outcomes"] == {"abstained": 1}
    assert result["questions"][0]["knowledge_point_diagnostics"]["outcome"] == "abstained"


def test_valid_points_and_warnings_replay_without_another_post_and_catalog_changes_invalidate_cache():
    cache = Cache(); calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        value = response_rows(payload)
        value["items"][0]["knowledge_points"] = [suggestion(), suggestion(code="unknown")]
        return Response(value)
    first = run(post, cache=cache)
    replay = run(lambda *_args, **_kwargs: pytest.fail("Exact point cache must be free"), cache=cache)
    assert first["questions"] == replay["questions"]
    assert len(calls) == 1 and replay["questions"][0]["knowledge_point_warnings"]
    assert replay["questions"][0]["knowledge_point_diagnostics"]["accepted"] == 1
    assert replay["questions"][0]["knowledge_point_diagnostics"]["rejected"] == 1
    changed_catalog = [{**CATALOG[0], "name": "新的函数概念名称"}]
    changed = run(post, cache=cache, catalog=changed_catalog)
    assert len(calls) == 2
    assert changed["questions"][0]["knowledge_points"][0]["name"] == "新的函数概念名称"
    assert not changed["diagnostics"]["pdf_draft_metadata"]["reused_cards"]


def test_catalog_snapshot_is_frozen_for_prompt_and_response_validation():
    catalog = deepcopy(CATALOG)
    def post(_provider, payload, **_options):
        assert "函数概念" in payload["messages"][0]["content"]
        catalog.clear()
        value = response_rows(payload)
        value["items"][0]["knowledge_points"] = [suggestion()]
        return Response(value)
    result = run(post, catalog=catalog)
    assert result["questions"][0]["knowledge_points"][0]["code"] == "1015"


def test_annotation_policy_change_cannot_replay_old_point_cache(monkeypatch):
    from mathbank import pdf_draft_metadata as module
    original = module.build_ai_knowledge_prompt
    policy = ["policy-A"]
    monkeypatch.setattr(module, "build_ai_knowledge_prompt", lambda catalog: original(catalog) + policy[0])
    cache = Cache(); calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        value = response_rows(payload)
        value["items"][0]["knowledge_points"] = [suggestion()]
        return Response(value)
    run(post, cache=cache)
    run(lambda *_a, **_k: pytest.fail("Same policy should reuse completed labels"), cache=cache)
    policy[0] = "policy-B"
    changed = run(post, cache=cache)
    assert len(calls) == 2
    assert not changed["diagnostics"]["pdf_draft_metadata"]["reused_cards"]


def test_explicit_prompt_and_output_budget_include_point_evidence():
    prompt = build_pdf_draft_metadata_messages(plan(1), TREE, knowledge_catalog=CATALOG)[0]["content"]
    assert '"knowledge_points":[]' in prompt and '"1015"' in prompt
    calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        return Response(response_rows(payload))
    run(post, draft=plan(19))
    assert len(calls) == 1 and calls[0]["max_tokens"] >= 256 + 384 * 19
