"""Metadata contracts reject ambiguous envelopes before adopting any fields."""

from types import SimpleNamespace
import json

import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import prepare_word_source_metadata
from mathbank.source_metadata_request import request_word_source_metadata
from mathbank.task_manager import TaskCancelled


CURRICULUM = {"必修一": {"函数": []}}


def setup(monkeypatch):
    import mathbank.source_metadata_request as module
    monkeypatch.setattr(module, "apply_model_thinking_policy", lambda payload, **_: payload)
    plan = prepare_word_source_metadata("1. 已知二次函数$f(x)=x^2$，求$f(3)$。", _new_diagnostics())
    assert plan["eligible"]
    row = {"id": plan["questions"][0]["id"], "question_type": "detailed_answer", "category_compulsory": "必修一",
           "category_chapter": "函数", "difficulty": "normal"}
    provider = SimpleNamespace(api_key="isolated-dummy", chat_completions_url="https://invalid.local",
                               model_name="isolated-model")
    return plan, row, provider


def run(monkeypatch, raw, *, finish="stop"):
    plan, row, provider = setup(monkeypatch)
    if callable(raw): raw = raw(row)
    calls = []; diagnostics = {}
    def post(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": finish, "message": {"content": raw}}]})
    def request():
        return request_word_source_metadata(plan, CURRICULUM, provider=provider, post=post, diagnostics=diagnostics,
                                            normalize_fillin=lambda value: value)
    return request, calls, diagnostics, plan


@pytest.mark.parametrize("transform", [
    lambda row: json.dumps({"items": [row]}, ensure_ascii=False),
    lambda row: "```json\n" + json.dumps({"items": [row]}, ensure_ascii=False) + "\n```",
    lambda row: "```JSON\r\n" + json.dumps({"items": [row]}, ensure_ascii=False) + "\r\n```",
])
def test_complete_json_or_complete_fence_keeps_body_local_and_uses_one_post(monkeypatch, transform):
    request, calls, diagnostics, plan = run(monkeypatch, transform)
    questions = request()
    assert questions[0]["content"] == plan["questions"][0]["content"]
    assert len(calls) == 1 and diagnostics["word_source_metadata"]["calls"] == 1
    assert diagnostics["word_source_metadata"]["status"] == "used"


@pytest.mark.parametrize("transform", [
    lambda row: '{"items":[],"items":' + json.dumps([row]) + '}',
    lambda row: '{"items":[{"id":"UNKNOWN",' + json.dumps(row)[1:] + ']}',
    lambda row: '{"items":[' + json.dumps(row)[:-1] + ',"difficulty":"normal"}]}',
    lambda row: json.dumps({"items": [row]})[:-1],
    lambda row: "```json\n" + json.dumps({"items": [row]}),
    lambda row: "前缀文字\n" + json.dumps({"items": [row]}),
    lambda row: json.dumps({"items": [row]}) + "\n后缀文字",
    lambda row: '{"items":[' + json.dumps(row)[:-1] + ',"unexpected":NaN}]}',
    lambda row: '{"items":[' + json.dumps(row)[:-1] + ',"unexpected":Infinity}]}',
    lambda row: '{"items":[' + json.dumps(row)[:-1] + ',"unexpected":-Infinity}]}',
    lambda row: json.dumps({"items": []}),
    lambda row: json.dumps({"items": [{**row, "content": "injected"}]}),
])
def test_ambiguous_nonfinite_truncated_or_wrong_shape_never_adopts_metadata(monkeypatch, transform):
    request, calls, diagnostics, _ = run(monkeypatch, transform)
    with pytest.raises((ValueError, TypeError)):
        request()
    assert len(calls) == 1 and diagnostics["word_source_metadata"]["calls"] == 1
    assert diagnostics["word_source_metadata"]["status"] == "fallback"


@pytest.mark.parametrize("finish", ["length", None, "content_filter"])
def test_bad_finish_reason_rejects_even_complete_json_and_never_retries(monkeypatch, finish):
    request, calls, diagnostics, _ = run(monkeypatch, lambda row: json.dumps({"items": [row]}), finish=finish)
    with pytest.raises(ValueError): request()
    assert len(calls) == 1 and diagnostics["word_source_metadata"]["status"] == "fallback"


def test_cancelled_network_error_is_not_reclassified_as_retryable_contract_error(monkeypatch):
    plan, _, provider = setup(monkeypatch)
    calls = []; cancelled = False; diagnostics = {}
    def check_cancelled():
        if cancelled: raise TaskCancelled("isolated cancellation")
    def post(*args, **kwargs):
        nonlocal cancelled
        calls.append(kwargs); cancelled = True
        raise ConnectionError("isolated network failure")
    with pytest.raises(TaskCancelled):
        request_word_source_metadata(plan, CURRICULUM, provider=provider, post=post, diagnostics=diagnostics,
                                     normalize_fillin=lambda value: value, check_cancelled=check_cancelled)
    assert len(calls) == 1 and diagnostics["word_source_metadata"]["status"] == "cancelled"
