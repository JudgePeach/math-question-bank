"""Classification returns usable tags independently of textbook placement."""

from mathbank.ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS
from copy import deepcopy
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from mathbank.ai_classification import (
    CLASSIFICATION_TIMEOUT_SECONDS, build_classification_user_message, parse_classification_response,
)
from mathbank.ai_knowledge_points import annotate_ai_knowledge_points, bind_ai_knowledge_catalog
from mathbank.ai_providers import resolve_text_provider


CATALOG = [{"code": "demo:function", "name": "函数", "original_name": "函数", "aliases": []}]


def point(**changes):
    return {"code": "demo:function", "role": "main", "evidence_source": "content",
            "evidence": "函数$f(x)$", "reason": "求函数值，直接考查函数。", **changes}


@pytest.mark.parametrize("packet,outcome", [({}, "missing_field"),
    ({"knowledge_points": []}, "abstained"), ({"knowledge_points": None}, "invalid_format"),
    ({"knowledge_points": [point(reason=" ")]}, "rejected")])
def test_missing_abstained_and_invalid_are_distinct_and_survive_normalization(packet, outcome):
    q = {"content": "研究函数$f(x)$。", **packet}
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_point_diagnostics"]["outcome"] == outcome
    before = deepcopy(q["knowledge_point_diagnostics"])
    annotate_ai_knowledge_points(q, [])
    assert q["knowledge_point_diagnostics"] == before


def test_auxiliary_requires_provided_solution_and_scope_is_preserved():
    q = {"content": "研究函数$f(x)$。", "knowledge_points": [point(role="auxiliary")]}
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"] == []
    q = {"content": "研究函数$f(x)$。", "answer_markdown": "原解使用函数定义，求得函数值。",
         "knowledge_points": [point(role="auxiliary", scope="第(2)问",
             evidence_source="answer_markdown", evidence="使用函数定义")]}
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"][0]["scope"] == "第(2)问"


def test_bound_request_catalog_is_used_after_current_catalog_changes():
    frozen = deepcopy(CATALOG)
    q = {"content": "研究函数$f(x)$。", "knowledge_points": [point()]}
    bind_ai_knowledge_catalog(q, frozen)
    annotate_ai_knowledge_points(q, [])
    assert q["knowledge_points"][0]["code"] == "demo:function"
    assert q["knowledge_point_diagnostics"]["catalog_size"] == 1


@pytest.mark.parametrize("raw,finish", [('{"a":1,"a":2}', "stop"),
    ('{"knowledge_points":NaN}', "stop"), ('[]', "stop"), ('{}', "length")])
def test_ambiguous_or_truncated_responses_are_not_adopted(raw, finish):
    with pytest.raises(ValueError):
        parse_classification_response({"choices": [{"finish_reason": finish, "message": {"content": raw}}]})


def test_usage_unknown_remains_unknown_and_fenced_complete_json_is_accepted():
    body = {"choices": [{"finish_reason": "stop", "message": {"content": '```json\n{}\n```'}}]}
    assert parse_classification_response(body) == ({}, None)
    body["usage"] = {"prompt_tokens": 20, "completion_tokens": True, "total_tokens": -1}
    assert parse_classification_response(body)[1] == {"prompt_tokens": 20}


def test_images_use_existing_model_identity_and_never_fetch_external_paths(tmp_path):
    image = Image.new("RGB", (20, 20), "white")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    (tmp_path / "figure.png").write_bytes(buffer.getvalue())
    provider = resolve_text_provider("SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash", {})
    question = {"content": "看图 ![](/static/uploads/figure.png)", "answer_markdown": "", "review_markdown": ""}
    before = deepcopy(question)
    message, context, warnings = build_classification_user_message(question, provider, uploads_dir=tmp_path)
    assert question == before and context["visible_image_count"] == 1 and warnings == []
    assert message["content"][2]["image_url"]["url"].startswith("data:image/png;base64,")
    text_provider = resolve_text_provider("DEEPSEEK/deepseek-v4-pro", {})
    message, context, warnings = build_classification_user_message(question, text_provider, uploads_dir=tmp_path)
    assert isinstance(message["content"], str) and context["image_state"] == "unavailable" and warnings
    (tmp_path / "link.png").symlink_to(tmp_path / "figure.png")
    question["content"] = "![](/static/uploads/link.png) ![](/static/uploads/../private.png)"
    _, context, warnings = build_classification_user_message(question, provider, uploads_dir=tmp_path)
    assert context["visible_image_count"] == 0 and warnings


def test_verified_named_relay_receives_the_same_safe_images_as_the_comparison_model(tmp_path):
    buffer = io.BytesIO(); Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    (tmp_path / "figure.png").write_bytes(buffer.getvalue())
    question = {"content": "看图 ![](/static/uploads/figure.png)", "answer_markdown": "", "review_markdown": ""}
    ds = resolve_text_provider("SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash", {})
    relay = resolve_text_provider("ZHONGZHAN_CLAUDE/gemini-3.8-flash-high", {})
    assert build_classification_user_message(question, ds, uploads_dir=tmp_path) == build_classification_user_message(
        question, relay, uploads_dir=tmp_path)


@pytest.mark.parametrize("model", ["BAILIAN/custom-text", "ZHONGZHAN_GPT/custom-text",
    "ZHONGZHAN_CLAUDE/custom-text", "SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash-unknown"])
def test_unknown_text_models_do_not_inherit_drawing_image_capability(model, tmp_path):
    provider = resolve_text_provider(model, {})
    question = {"content": "看图 ![](/static/uploads/figure.png)", "answer_markdown": "", "review_markdown": ""}
    message, context, warnings = build_classification_user_message(question, provider, uploads_dir=tmp_path)
    assert isinstance(message["content"], str) and context["image_state"] == "unavailable" and warnings


def test_single_request_returns_tags_when_placement_invalid_and_has_no_database_writes(monkeypatch, db_session):
    import main
    calls = []
    monkeypatch.setattr(main, "resolve_text_provider", lambda _: SimpleNamespace(
        api_key="isolated", api_base="https://example.invalid/v1", model_name="test",
        credential_label="test", reasoning_effort=None, provider_code="test"))
    monkeypatch.setattr(main, "get_active_version_code", lambda: "A")
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"必修一": {"函数": ["概念"]}})
    monkeypatch.setattr(main, "load_ai_knowledge_catalog", lambda *_: deepcopy(CATALOG))
    monkeypatch.setattr(db_session, "commit", lambda: pytest.fail("classification must not write"))
    def post(_provider, payload, **_kwargs):
        assert _kwargs["timeout"] == CLASSIFICATION_TIMEOUT_SECONDS == AI_REQUEST_TIMEOUT_SECONDS
        calls.append(payload)
        return SimpleNamespace(json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "compulsory": "未知册", "chapter": "未知章", "knowledge": "", "question_form": "choice",
            "knowledge_points": [point()]}, ensure_ascii=False)}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    result = main.ai_classify("研究函数$f(x)$。", "", "", db_session, "A")
    assert result["status"] == "success" and result["placement_valid"] is False
    assert result["knowledge_points"][0]["code"] == "demo:function" and len(calls) == 1
    assert result["classification_review"]["required"] is True
    assert result["classification_diagnostics"]["usage"] is None
