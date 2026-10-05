"""PDF text splitting retries have a separate budget from page recognition."""

from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import pytest
import requests

from mathbank import paper_parse
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("No live model calls"))


def response(*, finish="stop", questions=None, usage=True):
    body = {"choices": [{"finish_reason": finish, "message": {"content": json.dumps({
        "questions": questions if questions is not None else [{"content": "完整题干 $x=1$。", "answer_markdown": ""}]})}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 155}
    return SimpleNamespace(status_code=200, json=lambda: deepcopy(body))


def split(post, **kwargs):
    provider = SimpleNamespace(api_key="test-key", chat_completions_url="https://model.invalid")
    return paper_parse.request_pdf_paper_completion(provider, {
        "model": "same-model", "messages": [{"role": "user", "content": "same locked source"}]},
        post=post, **kwargs)


def test_timeout_then_success_reuses_identical_input_and_preserves_page_diagnostics():
    calls, events = [], []
    diagnostics = {"pdf_vision": {"calls": 7, "retries": 0}, "pdf_joint_usage": {"total_tokens": 300}}
    def post(provider, payload, **kwargs):
        calls.append((provider, deepcopy(payload), kwargs))
        if len(calls) == 1:
            raise requests.ReadTimeout("private url and api key")
        return response()
    result = split(post, diagnostics=diagnostics, report_attempt=events.append)
    assert result[0]["content"] == "完整题干 $x=1$。"
    assert len(calls) == 2 and calls[0][0] is calls[1][0] and calls[0][1] == calls[1][1]
    assert all(kw == {"timeout": 600, "check_status": False, "retry_connection": False,
                      "allow_redirects": False} for _provider, _payload, kw in calls)
    assert diagnostics["pdf_vision"] == {"calls": 7, "retries": 0}
    assert diagnostics["pdf_joint_usage"] == {"total_tokens": 300}
    report = diagnostics["pdf_splitting"]
    assert report["calls"] == 2 and report["retries"] == 1 and report["usage_complete"] is False
    assert report["timeout_seconds"] == 600
    assert report["usage"] == diagnostics["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 155}
    assert "private" not in str(report) and all(event["stage"] == "paper_split" for event in events)
    assert diagnostics["finish_reason"] == "stop"


def test_invalid_first_result_usage_stays_known_but_metadata_is_from_success():
    calls, diagnostics = [], {"finish_reason": "before"}
    def post(*args, **kwargs):
        calls.append(1)
        return response(finish="length") if len(calls) == 1 else response()
    split(post, diagnostics=diagnostics)
    report = diagnostics["pdf_splitting"]
    assert report["usage"]["total_tokens"] == 310 and report["usage_complete"] is True
    assert [item["finish_reason"] for item in report["attempts"]] == ["length", "stop"]
    assert all(item["output_characters"] > 0 for item in report["attempts"])
    assert diagnostics["finish_reason"] == "stop"


@pytest.mark.parametrize("status", [307, 308, 400, 401, 403, 404])
def test_permanent_http_error_does_not_retry_or_read_body(status):
    calls, diagnostics = [], {}
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=status, json=lambda: pytest.fail("No failed HTTP body"))
    with pytest.raises(ValueError, match=f"HTTP {status}"):
        split(post, diagnostics=diagnostics)
    assert calls == [1] and diagnostics["pdf_splitting"]["calls"] == 1


def test_content_filter_never_retries():
    calls, diagnostics = [], {}
    def post(*args, **kwargs):
        calls.append(1)
        return response(finish="content_filter")
    with pytest.raises(ValueError, match="过滤"):
        split(post, diagnostics=diagnostics)
    assert calls == [1] and diagnostics["pdf_splitting"]["usage"]["total_tokens"] == 155


@pytest.mark.parametrize("failure", ["timeout", "truncated", "empty_questions", "bad_question"])
def test_two_invalid_results_are_not_accepted_or_retried_again(failure):
    calls, diagnostics = [], {"finish_reason": "unchanged"}
    def post(*args, **kwargs):
        calls.append(1)
        if failure == "timeout": raise requests.ReadTimeout("private")
        if failure == "truncated": return response(finish="length")
        if failure == "empty_questions": return response(questions=[])
        return response(questions=[{"content": None}])
    with pytest.raises(ValueError, match="共尝试 2 次"):
        split(post, diagnostics=diagnostics)
    assert calls == [1, 1]
    assert diagnostics["finish_reason"] == "unchanged"
    assert diagnostics["pdf_splitting"]["usage"] == ({} if failure == "timeout" else {
        "prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 310})


@pytest.mark.parametrize("phase", ["before", "failed", "returned"])
def test_cancelled_or_removed_task_does_not_retry_or_accept(phase):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        if phase == "failed": raise requests.ReadTimeout("private")
        return response()
    def cancel():
        if phase == "before" or calls:
            raise TaskCancelled("cancelled or removed")
    with pytest.raises(TaskCancelled):
        split(post, check_cancelled=cancel)
    assert len(calls) == (0 if phase == "before" else 1)


def test_legacy_missing_finish_reason_remains_compatible():
    assert split(lambda *_a, **_kw: response(finish=None))[0]["content"] == "完整题干 $x=1$。"


def stream_provider():
    return SimpleNamespace(provider_code="siliconflow", model_name="Qwen/Qwen3.8-27B",
                           api_key="unused", chat_completions_url="https://model.invalid")


def sse_response(*, done=True, finish="stop", content=None):
    content = content or json.dumps({"questions": [{"content": "完整题干", "answer_markdown": ""}]})
    events = [{"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]},
              {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}]
    raw = b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in events)
    if done: raw += b"data: [DONE]\n\n"
    return SimpleNamespace(status_code=200, iter_content=lambda chunk_size: iter([raw]), close=lambda: None)


def test_pdf_stream_retries_incomplete_eof_once_and_records_real_stream_post():
    calls, diagnostics = [], {}
    def post(provider, payload, **kwargs):
        calls.append((payload, kwargs))
        return sse_response(done=len(calls) > 1)
    result = paper_parse.request_pdf_paper_completion(stream_provider(), {"model": "Qwen/Qwen3.8-27B"},
                                                     post=post, diagnostics=diagnostics)
    assert result[0]["content"] == "完整题干" and len(calls) == 2
    assert all(payload["stream"] is True and kw["stream"] is True and not kw["retry_connection"]
               and not kw["allow_redirects"] for payload, kw in calls)
    report = diagnostics["pdf_splitting"]
    assert report["calls"] == 2 and report["attempts_started"] == 2 and report["retries"] == 1
    assert all(item["http_started"] for item in report["attempts"])
    assert report["usage"]["total_tokens"] == 60 and report["usage_complete"] is False


@pytest.mark.parametrize("finish,expected_calls", [("length", 2), ("content_filter", 1)])
def test_pdf_stream_never_parses_partial_or_retries_filter(finish, expected_calls, monkeypatch):
    calls, diagnostics = [], {}
    monkeypatch.setattr(paper_parse, "parse_paper_completion", lambda *_a, **_kw: pytest.fail("No partial parse"))
    def post(*_a, **_kw):
        calls.append(1)
        return sse_response(finish=finish)
    with pytest.raises(ValueError):
        paper_parse.request_pdf_paper_completion(stream_provider(), {}, post=post, diagnostics=diagnostics)
    assert len(calls) == expected_calls
    assert diagnostics["pdf_splitting"]["usage"]["total_tokens"] == expected_calls * 30


def test_cancel_before_stream_post_does_not_count_a_paid_request():
    checks, calls, diagnostics = [], [], {}
    def cancel():
        checks.append(1)
        if len(checks) >= 5: raise TaskCancelled("cancelled before HTTP")
    def post(*_a, **_kw):
        calls.append(1)
        return sse_response()
    with pytest.raises(TaskCancelled):
        paper_parse.request_pdf_paper_completion(stream_provider(), {}, post=post, diagnostics=diagnostics,
                                                 check_cancelled=cancel)
    assert calls == [] and diagnostics["pdf_splitting"]["calls"] == 0


def test_pdf_stream_valid_transport_but_invalid_json_retries_without_accepting(monkeypatch):
    calls = []
    def post(*_a, **_kw):
        calls.append(1)
        return sse_response(content="not valid JSON")
    with pytest.raises(ValueError):
        paper_parse.request_pdf_paper_completion(stream_provider(), {}, post=post)
    assert len(calls) == 2


def test_stream_resource_wait_precedes_http_attempt_accounting(monkeypatch):
    from mathbank import ai_stream
    semaphore = threading.BoundedSemaphore(4)
    monkeypatch.setattr(ai_stream, "_SLOTS", semaphore)
    for _ in range(4): assert semaphore.acquire()
    monkeypatch.setattr(paper_parse, "PAPER_SPLIT_TIMEOUT_SECONDS", .02)
    calls, diagnostics = [], {}
    def post(*_a, **_kw):
        calls.append(1)
        return sse_response()
    with pytest.raises(ValueError, match="未发送模型请求"):
        paper_parse.request_pdf_paper_completion(stream_provider(), {}, post=post, diagnostics=diagnostics)
    assert calls == [] and diagnostics["pdf_splitting"]["calls"] == 0
    assert diagnostics["pdf_splitting"]["attempts"] == []
    for _ in range(4): semaphore.release()
