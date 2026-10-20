"""PDF metadata adds catalogue points in its existing request, preserving source."""
from copy import deepcopy
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
                "category_knowledge", "classification_review", "knowledge_points", "knowledge_point_warnings"}
    return [{key: value for key, value in question.items() if key not in optional} for question in questions]


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


def test_explicit_prompt_and_output_budget_include_point_evidence():
    prompt = build_pdf_draft_metadata_messages(plan(1), TREE, knowledge_catalog=CATALOG)[0]["content"]
    assert '"knowledge_points":[]' in prompt and '"1015"' in prompt
    calls = []
    def post(_provider, payload, **_options):
        calls.append(payload)
        return Response(response_rows(payload))
    run(post, draft=plan(19))
    assert len(calls) == 1 and calls[0]["max_tokens"] >= 256 + 384 * 19
