"""PDF-only retry budgets and evidence; every provider boundary is mocked."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from mathbank import pdf_vision_request as vision
from mathbank.ai_http import post_chat_completion
from mathbank.task_manager import TaskCancelled

_SESSION_REQUEST = requests.sessions.Session.request


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    monkeypatch.setattr(vision, "PDF_VISION_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("No live AI calls"))


def response(*, status=200, body=None):
    value = body or {"choices": [{"finish_reason": "stop", "message": {"content": "complete text"}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 17}}
    return SimpleNamespace(status_code=status, json=lambda: deepcopy(value))


def recognize(post, **kwargs):
    return vision.request_pdf_vision(SimpleNamespace(), {"model": "configured-model"}, post=post,
        validate=lambda body: {"markdown": vision.completion_content(body, "PDF", max_chars=100)},
        label="PDF", stage="test", page_index=2, **kwargs)


@pytest.mark.parametrize("failure", [requests.ReadTimeout, requests.ConnectTimeout, requests.ConnectionError,
                                    requests.exceptions.ProxyError, requests.exceptions.ChunkedEncodingError])
def test_transport_failure_retries_once_with_600_seconds(failure):
    calls, events = [], []
    def post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise failure("private url and api key")
        return response()
    result = recognize(post, report_attempt=events.append)
    assert len(calls) == result["visual_calls"] == 2
    assert all(item == {"timeout": 600, "check_status": False, "retry_connection": False,
                        "allow_redirects": False} for item in calls)
    assert [item["status"] for item in result["attempts"]] == ["failed", "succeeded"]
    assert all(item["timeout_seconds"] == 600 for item in result["attempts"])
    assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 17}
    assert result["usage_complete"] is False
    assert "private" not in str(events)


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_transient_http_failure_retries_and_does_not_read_failed_body(status):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=status, json=lambda: pytest.fail("Do not read failed HTTP body")) if len(calls) == 1 else response()
    assert recognize(post)["markdown"] == "complete text" and len(calls) == 2


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 404, 413, 422])
def test_explicit_http_configuration_or_input_errors_never_retry(status):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=status, json=lambda: pytest.fail("Do not read failed HTTP body"))
    with pytest.raises(vision.PDFVisionRequestError, match=f"HTTP {status}") as caught:
        recognize(post)
    assert len(calls) == len(caught.value.attempts) == 1


@pytest.mark.parametrize("failure", [ValueError, TypeError, requests.exceptions.InvalidURL,
                                    requests.exceptions.SSLError, requests.exceptions.InvalidHeader])
def test_configuration_programming_and_tls_errors_never_retry(failure):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        raise failure("private details")
    with pytest.raises(vision.PDFVisionRequestError) as caught:
        recognize(post)
    assert calls == [1] and "private" not in str(caught.value)


@pytest.mark.parametrize("bad", ["json", "length", "missing_choices", "empty", "bad_content"])
def test_unusable_response_retries_and_counts_failed_response_usage(bad):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            return response()
        if bad == "json":
            return SimpleNamespace(status_code=200, json=lambda: (_ for _ in ()).throw(ValueError("private response")))
        body = response().json()
        if bad == "length": body["choices"][0]["finish_reason"] = "length"
        elif bad == "missing_choices": body["choices"] = []
        elif bad == "empty": body["choices"][0]["message"]["content"] = ""
        else: body["choices"][0]["message"]["content"] = {"private": "data"}
        return response(body=body)
    result = recognize(post)
    assert len(calls) == 2
    assert result["usage"]["total_tokens"] == (17 if bad == "json" else 34)
    assert result["usage_complete"] is (bad != "json")
    assert "private" not in str(result["attempts"])


def test_two_failures_keep_every_attempt_and_safe_error_without_third_post():
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        raise requests.ReadTimeout("private detail")
    with pytest.raises(vision.PDFVisionRequestError, match="共尝试 2 次") as caught:
        recognize(post)
    assert len(calls) == len(caught.value.attempts) == 2
    assert caught.value.usage == {} and "private" not in str(caught.value)


def test_json_failure_records_coordinates_and_hash_without_response_text():
    raw = '{"markdown":"private question and private credential", bad}'
    body = {"choices": [{"finish_reason": "stop", "message": {"content": raw}}]}
    events = []

    def validate(value):
        try:
            json.loads(value["choices"][0]["message"]["content"])
        except json.JSONDecodeError as exc:
            raise ValueError("PDF 结构化结果无法解析。") from exc

    with pytest.raises(vision.PDFVisionRequestError) as caught:
        vision.request_pdf_vision(SimpleNamespace(), {}, post=lambda *_a, **_kw: response(body=body),
            validate=validate, label="PDF", stage="joint_page", page_index=5, report_attempt=events.append)
    assert len(caught.value.attempts) == 2
    for attempt in caught.value.attempts:
        assert attempt["response_characters"] == len(raw)
        assert attempt["response_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
        assert attempt["response_start"] == "object"
        details = attempt["json_failure"]
        assert details["code"] == "property_quotes"
        assert details["candidate_characters"] == len(raw)
        assert details["line"] == 1 and details["column"] > 1
        assert set(details) == {"code", "position", "line", "column", "candidate_characters", "candidate_sha256"}
    assert "private" not in json.dumps(events) + str(caught.value)


def test_response_metadata_accepts_unpaired_surrogates_without_reissuing_request():
    raw = "text\ud800"
    body = {"choices": [{"finish_reason": "stop", "message": {"content": raw}}]}
    calls = []
    def post(*_a, **_kw):
        calls.append(1)
        return response(body=body)
    result = recognize(post)
    assert result["markdown"] == raw and len(calls) == 1
    assert len(result["attempts"][0]["response_sha256"]) == 64


def test_request_metadata_only_records_bounded_sent_controls_and_no_prompts_or_credentials():
    provider = SimpleNamespace(provider_code="siliconflow" + "X" * 100, model_name="unused-provider-model",
                               api_key="private credential", api_base="private gateway")
    payload = {"model": "configured-model" + "M" * 300, "enable_thinking": False,
               "thinking_budget": 4096, "reasoning_effort": "high", "max_tokens": 16384,
               "max_completion_tokens": 32768, "stream": False,
               "response_format": {"type": "json_schema", "json_schema": {"private": "private schema"}},
               "messages": [{"role": "user", "content": "private exam source"}],
               "headers": {"Authorization": "private credential"}, "api_key": "private credential"}
    original = deepcopy(payload)
    seen = []

    def post(actual_provider, actual_payload, **kwargs):
        assert actual_provider is provider and actual_payload == original
        seen.append(actual_payload)
        return response(body={"choices": [{"finish_reason": "stop", "message": {
            "content": "complete text", "reasoning_content": "private reasoning source"}}]})

    result = vision.request_pdf_vision(provider, payload, post=post,
        validate=lambda _body: {"markdown": "complete text"}, label="PDF", stage="paper_split")
    attempt = result["attempts"][0]
    assert len(attempt["provider_code"]) == 64 and len(attempt["model"]) == 256
    assert attempt["model"].startswith("configured-model")
    assert {key: attempt[key] for key in ("enable_thinking", "thinking_budget", "reasoning_effort", "max_tokens",
            "max_completion_tokens", "stream", "response_format_type")} == {
        "enable_thinking": False, "thinking_budget": 4096, "reasoning_effort": "high",
        "max_tokens": 16384, "max_completion_tokens": 32768, "stream": False, "response_format_type": "json_schema"}
    assert attempt["reasoning_characters"] == len("private reasoning source")
    assert payload == original and len(seen) == 1
    assert "private" not in json.dumps(result["attempts"])
    assert all(key not in attempt for key in ("headers", "api_key", "api_base", "messages", "response_format"))


@pytest.mark.parametrize("payload", [
    {},
    {"model": None, "enable_thinking": None, "thinking_budget": None, "stream": None},
    {"model": {"private": "secret"}, "enable_thinking": "false", "thinking_budget": True,
     "max_tokens": -1, "max_completion_tokens": 0, "reasoning_effort": {"private": "secret"},
     "stream": "false", "response_format": {"type": ["private"]}},
])
def test_missing_or_malformed_controls_remain_unknown_without_changing_request(payload):
    result = vision.request_pdf_vision(SimpleNamespace(), payload, post=lambda *_a, **_kw: response(),
        validate=lambda _body: {}, label="PDF", stage="test")
    attempt = result["attempts"][0]
    assert all(key not in attempt for key in ("provider_code", "model", "enable_thinking", "thinking_budget",
        "max_tokens", "max_completion_tokens", "reasoning_effort", "stream", "response_format_type", "reasoning_characters"))


def test_sent_controls_are_recorded_for_each_retry_without_inventing_failed_response_reasoning():
    payload = {"model": "Qwen/Qwen3.8-27B", "enable_thinking": False, "max_tokens": 8192,
               "response_format": {"type": "json_object"}}
    events, calls = [], []

    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ReadTimeout("private")
        return response(body={"choices": [{"finish_reason": "stop", "message": {
            "content": "complete text", "reasoning_content": ""}}]})

    result = vision.request_pdf_vision(SimpleNamespace(provider_code="siliconflow"), payload, post=post,
        validate=lambda _body: {}, label="PDF", stage="paper_split", report_attempt=events.append)
    assert len(calls) == 2
    assert all(attempt["model"] == payload["model"] and attempt["enable_thinking"] is False
               and attempt["max_tokens"] == 8192 for attempt in result["attempts"])
    assert "reasoning_characters" not in result["attempts"][0]
    assert result["attempts"][1]["reasoning_characters"] == 0
    assert "private" not in json.dumps(events)


@pytest.mark.parametrize("phase", ["before", "failed", "after"])
def test_cancellation_has_priority_over_result_and_retry(phase):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        if phase == "failed": raise requests.ReadTimeout("private")
        return response()
    def cancel():
        if phase == "before" or calls:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        recognize(post, check_cancelled=cancel)
    assert len(calls) == (0 if phase == "before" else 1)


def test_transport_proxy_retry_does_not_multiply_pdf_attempt_budget(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        raise requests.exceptions.ProxyError("private")
    monkeypatch.setattr(requests, "post", post)
    provider = SimpleNamespace(api_key="test", credential_label="test", provider_label="test",
                               chat_completions_url="https://test.invalid/chat/completions")
    with pytest.raises(vision.PDFVisionRequestError):
        vision.request_pdf_vision(provider, {}, post=post_chat_completion, validate=lambda body: {},
                                  label="PDF", stage="test")
    assert len(calls) == 2 and all(item["timeout"] == 600 for item in calls)
    assert all(item["allow_redirects"] is False for item in calls)


@pytest.mark.parametrize("value,expected", [("3600", 30), ("3", 3), ("0", 0), ("nonsense", 0), ("NaN", 0)])
def test_retry_after_is_bounded_and_wait_checks_cancel(monkeypatch, value, expected):
    clock, sleeps, checks, calls = [0.0], [], [], []
    monkeypatch.setattr(vision.time, "monotonic", lambda: clock[0])
    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
    monkeypatch.setattr(vision.time, "sleep", sleep)
    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return SimpleNamespace(status_code=429, headers={"Retry-After": value})
        return response()
    result = recognize(post, check_cancelled=lambda: checks.append(clock[0]))
    assert sum(sleeps) == pytest.approx(expected)
    assert all(delay <= 0.1 for delay in sleeps)
    assert result["attempts"][0]["retry_delay_seconds"] == expected
    assert len(checks) >= len(sleeps) and len(calls) == 2


def test_cancellation_during_retry_after_avoids_second_post(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(vision.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(vision.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=503, headers={"Retry-After": "30"})
    def cancelled():
        if clock[0] >= .3:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        recognize(post, check_cancelled=cancelled)
    assert calls == [1]


@pytest.mark.parametrize("redirect", [307, 308])
def test_requests_redirect_cannot_create_an_untracked_pdf_post(monkeypatch, redirect):
    # Exercise requests' real redirect processing while the adapter itself is
    # mocked. No socket, localhost server, or provider connection is needed.
    calls = []
    monkeypatch.setattr(requests.sessions.Session, "request", _SESSION_REQUEST)
    def send(_adapter, request, **kwargs):
        calls.append(request.url)
        reply = requests.Response()
        reply.status_code = redirect if request.url.endswith("/configured") else 200
        reply.headers = requests.structures.CaseInsensitiveDict(
            {"Location": "/completion"} if reply.status_code == redirect else {})
        reply._content = b'{}'
        reply.url, reply.request = request.url, request
        return reply
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    provider = SimpleNamespace(api_key="test", credential_label="test", provider_label="test",
                               chat_completions_url="https://test.invalid/configured")
    with pytest.raises(vision.PDFVisionRequestError, match=f"HTTP {redirect}") as caught:
        vision.request_pdf_vision(provider, {}, post=post_chat_completion, validate=lambda body: {},
                                  label="PDF", stage="test")
    assert calls == ["https://test.invalid/configured"]
    assert len(caught.value.attempts) == 1
    calls.clear()
    post_chat_completion(provider, {}, timeout=10)
    assert calls == ["https://test.invalid/configured", "https://test.invalid/completion"]
