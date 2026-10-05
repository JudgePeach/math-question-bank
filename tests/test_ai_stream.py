"""SSE boundaries, caller deadlines and resource ownership without networking."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from mathbank import ai_stream
from mathbank.task_manager import TaskCancelled


def event(value):
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"


def wire(text='{"questions":[{"content":"完整题干","answer_markdown":""}]}', finish="stop", done=True):
    result = event({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}})
    result += event({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    result += event({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14}})
    return result + (b"data: [DONE]\n\n" if done else b"")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(ai_stream, "_SLOTS", threading.BoundedSemaphore(4))
    monkeypatch.setattr(ai_stream, "_RESERVATION_LOCK", threading.Lock())
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("No live requests"))


class Reply:
    status_code = 200
    headers = {}

    def __init__(self, data=None, *, read_gate=None, close_gate=None, close_error=False):
        self.data = wire() if data is None else data
        self.read_gate, self.close_gate = read_gate, close_gate
        self.close_error = close_error
        self.closed = threading.Event()
        self.read_started = threading.Event()
        self.close_started = threading.Event()

    def iter_content(self, chunk_size):
        self.read_started.set()
        if self.read_gate: self.read_gate.wait(2)
        for index in range(0, len(self.data), 3):
            yield self.data[index:index + 3]

    def close(self):
        self.close_started.set()
        if self.close_gate: self.close_gate.wait(2)
        self.closed.set()
        if self.close_error: raise ValueError("private key must not be logged")


def receive(reply, *, timeout=.5, cancel=lambda: None, progress=lambda _stats: None):
    with ai_stream.reserve_stream_attempts(1, check_cancelled=cancel, timeout_seconds=timeout) as permits:
        permit = permits[0]
        result = ai_stream.receive_completion(SimpleNamespace(), {"stream": True},
            post=lambda *_a, **_kw: reply, permit=permit, timeout_seconds=timeout,
            check_cancelled=cancel, progress=progress)
        return result, permit


def available(semaphore):
    held = []
    while semaphore.acquire(blocking=False): held.append(True)
    for _ in held: semaphore.release()
    return len(held)


def await_free(semaphore, count=4):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if available(semaphore) == count: return
        time.sleep(.005)
    assert available(semaphore) == count


def test_stop_done_and_last_usage_are_complete():
    decoder = ai_stream.SSECompletion()
    data = b": heartbeat\n\n" + wire()
    for byte in data: decoder.feed(bytes([byte]))
    body = decoder.response_body()
    assert body["_stream_complete"] is True
    assert json.loads(body["choices"][0]["message"]["content"])["questions"][0]["content"] == "完整题干"
    assert body["usage"]["total_tokens"] == 14  # Last value; not 10 + 14.


@pytest.mark.parametrize("finish,done", [("length", True), ("content_filter", True), (None, True), ("stop", False)])
def test_partial_json_never_counts_as_complete(finish, done):
    reply, permit = receive(Reply(wire(finish=finish, done=done)))
    assert reply.json()["_stream_complete"] is False
    await_free(permit.semaphore)


@pytest.mark.parametrize("late", ["finish", "content"])
def test_terminal_state_cannot_be_overwritten(late):
    data = wire(finish="length", done=False)
    data += event({"choices": [{"delta": {"content": "late"} if late == "content" else {}, "finish_reason": "stop"}]})
    data += b"data: [DONE]\n\n"
    reply, _permit = receive(Reply(data))
    assert reply.json()["_stream_complete"] is False
    assert reply.json()["choices"][0]["finish_reason"] == "length"


def test_invalid_sse_is_a_safe_incomplete_response():
    reply, _permit = receive(Reply(b"data: not-json-secret\n\n"))
    body = reply.json()
    assert body["_stream_complete"] is False
    assert "secret" not in str(body)


def test_missing_usage_stays_unknown():
    data = event({"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"
    reply, _permit = receive(Reply(data))
    assert "usage" not in reply.json()


def test_explicit_unknown_last_usage_does_not_reuse_an_earlier_count():
    data = wire(done=False) + event({"choices": [], "usage": None}) + b"data: [DONE]\n\n"
    reply, _permit = receive(Reply(data))
    assert reply.json()["_stream_complete"] is True
    assert "usage" not in reply.json()


def test_non200_preserves_only_retry_after_and_closes():
    reply = Reply()
    reply.status_code = 503
    reply.headers = {"Retry-After": "30", "secret": "private"}
    result, permit = receive(reply)
    assert result.status_code == 503 and result.headers == {"Retry-After": "30"}
    assert reply.closed.wait(1)
    await_free(permit.semaphore)


def test_slow_close_does_not_delay_complete_result_or_free_slot_early():
    gate = threading.Event()
    reply = Reply(close_gate=gate)
    start = time.monotonic()
    result, permit = receive(reply)
    assert result.json()["_stream_complete"] is True and time.monotonic() - start < .4
    assert reply.close_started.wait(1) and available(permit.semaphore) == 3
    gate.set()
    await_free(permit.semaphore)


@pytest.mark.parametrize("cancelled", [False, True])
def test_stalled_read_deadline_or_cancel_drops_late_data(cancelled):
    gate = threading.Event()
    reply, progress = Reply(read_gate=gate), []
    stop = threading.Event()
    def cancel():
        if stop.is_set(): raise TaskCancelled("cancelled")
    if cancelled: threading.Timer(.04, stop.set).start()
    start = time.monotonic()
    with pytest.raises(TaskCancelled if cancelled else requests.ReadTimeout):
        receive(reply, timeout=.09, cancel=cancel, progress=progress.append)
    assert time.monotonic() - start < .4
    assert available(ai_stream._SLOTS) == 3
    observations_before_late_read = len(progress)
    gate.set()
    assert reply.closed.wait(1)
    await_free(ai_stream._SLOTS)
    assert len(progress) == observations_before_late_read
    assert all(item["stream_content_characters"] == 0 and not item["stream_complete"] for item in progress)


def test_late_headers_are_closed_without_starting_read():
    gate, reply = threading.Event(), Reply()
    with ai_stream.reserve_stream_attempts(1, check_cancelled=lambda: None, timeout_seconds=.1) as permits:
        def post(*_a, **_kw):
            gate.wait(2)
            return reply
        with pytest.raises(requests.ReadTimeout):
            ai_stream.receive_completion(SimpleNamespace(), {}, post=post, permit=permits[0],
                timeout_seconds=.05, check_cancelled=lambda: None)
        gate.set()
        assert reply.closed.wait(1) and not reply.read_started.is_set()
        await_free(permits[0].semaphore)


def test_close_error_is_observed_without_secret_logs(capsys):
    reply, permit = receive(Reply(close_error=True))
    await_free(permit.semaphore)
    assert permit.cleanup_error_type == "ValueError"
    assert "private" not in capsys.readouterr().err


def test_reservation_is_atomic_and_all_four_callers_progress():
    barrier, complete = threading.Barrier(4), []
    def caller():
        barrier.wait()
        with ai_stream.reserve_stream_attempts(2, check_cancelled=lambda: None, timeout_seconds=.5):
            time.sleep(.02)
            complete.append(True)
    threads = [threading.Thread(target=caller) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(1)
    assert len(complete) == 4 and available(ai_stream._SLOTS) == 4


def test_cancelled_partial_reservation_restores_every_unused_slot():
    assert ai_stream._SLOTS.acquire()
    assert ai_stream._SLOTS.acquire()
    assert ai_stream._SLOTS.acquire()
    deadline = time.monotonic() + .03
    def cancel():
        if time.monotonic() >= deadline: raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        with ai_stream.reserve_stream_attempts(2, check_cancelled=cancel, timeout_seconds=.5):
            pytest.fail("Must not start a POST")
    assert available(ai_stream._SLOTS) == 1
    for _ in range(3): ai_stream._SLOTS.release()
    assert available(ai_stream._SLOTS) == 4


def test_event_and_content_buffers_are_bounded(monkeypatch):
    monkeypatch.setattr(ai_stream, "MAX_EVENT_BYTES", 16)
    with pytest.raises(ValueError): ai_stream.SSECompletion().feed(b"x" * 17)
    monkeypatch.setattr(ai_stream, "MAX_EVENT_BYTES", 10000)
    monkeypatch.setattr(ai_stream, "MAX_CONTENT_CHARACTERS", 3)
    reply, _permit = receive(Reply(wire("four")))
    assert reply.json()["_stream_complete"] is False


def test_thread_start_failure_releases_transferred_permit(monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda _self: (_ for _ in ()).throw(RuntimeError("cannot start")))
    with pytest.raises(RuntimeError): receive(Reply())
    assert available(ai_stream._SLOTS) == 4


def test_transport_failure_returns_slot_and_no_fake_usage():
    with ai_stream.reserve_stream_attempts(1, check_cancelled=lambda: None, timeout_seconds=.2) as permits:
        def post(*_a, **_kw): raise requests.ConnectionError("private")
        with pytest.raises(requests.ConnectionError):
            ai_stream.receive_completion(SimpleNamespace(), {}, post=post, permit=permits[0],
                                        timeout_seconds=.2, check_cancelled=lambda: None)
        await_free(permits[0].semaphore)


def test_tiny_heartbeats_do_not_extend_caller_deadline():
    reply = Reply()
    def fragments(chunk_size):
        while True:
            time.sleep(.002)
            yield b":"
    reply.iter_content = fragments
    start = time.monotonic()
    with pytest.raises(requests.ReadTimeout): receive(reply, timeout=.04)
    assert time.monotonic() - start < .3
    assert reply.closed.wait(1)
    await_free(ai_stream._SLOTS)


def test_stop_then_late_content_is_rejected_even_with_done():
    data = event({"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}]})
    data += event({"choices": [{"delta": {"content": "late"}, "finish_reason": None}]})
    data += b"data: [DONE]\n\n"
    response, _permit = receive(Reply(data))
    assert response.json()["_stream_complete"] is False
