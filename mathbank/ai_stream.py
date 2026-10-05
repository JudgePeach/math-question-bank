"""Bounded SSE reception for PDF text splitting, using public requests APIs."""

from contextlib import contextmanager
import json
import queue
import threading
import time
from types import SimpleNamespace

import requests


MAX_READERS = 4
MAX_CONTENT_CHARACTERS = 500_000
MAX_EVENT_BYTES = 1_048_576
_SLOTS = threading.BoundedSemaphore(MAX_READERS)
_RESERVATION_LOCK = threading.Lock()


class _Permit:
    def __init__(self, semaphore):
        self.semaphore = semaphore
        self.transferred = False
        self.released = False
        self.cleanup_error_type = None
        self.lock = threading.Lock()

    def release(self):
        with self.lock:
            if not self.released:
                self.released = True
                self.semaphore.release()


@contextmanager
def reserve_stream_attempts(count, *, check_cancelled, timeout_seconds):
    """Reserve before attempt/POST accounting; unused permits always return."""
    permits = []
    semaphore = _SLOTS
    deadline = time.monotonic() + timeout_seconds
    owns_reservation = False
    try:
        while not owns_reservation:
            check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("流式接收资源仍在清理，请稍后重试；未发送模型请求。")
            owns_reservation = _RESERVATION_LOCK.acquire(timeout=min(0.25, remaining))
        for _ in range(count):
            while True:
                check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("流式接收资源仍在清理，请稍后重试；未发送模型请求。")
                if semaphore.acquire(timeout=min(0.25, remaining)):
                    permits.append(_Permit(semaphore))
                    break
        _RESERVATION_LOCK.release()
        owns_reservation = False
        yield permits
    finally:
        if owns_reservation:
            _RESERVATION_LOCK.release()
        for permit in permits:
            if not permit.transferred:
                permit.release()


class SSECompletion:
    """Incrementally assemble one choice; usage is the last supplied value."""
    def __init__(self):
        self.buffer = bytearray()
        self.data_lines = []
        self.event_bytes = 0
        self.content = []
        self.content_characters = 0
        self.reasoning_characters = 0
        self.events = 0
        self.usage = None
        self.usage_seen = False
        self.finish_reason = None
        self.done = False
        self.error = None

    def feed(self, chunk):
        self.buffer.extend(chunk)
        if len(self.buffer) + self.event_bytes > MAX_EVENT_BYTES:
            raise ValueError("流式响应单帧过长。")
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            line = bytes(line).rstrip(b"\r")
            if not line:
                self._event()
            elif line.startswith(b"data:"):
                value = line[5:].lstrip(b" ")
                self.data_lines.append(value)
                self.event_bytes += len(value)

    def _event(self):
        if not self.data_lines:
            return
        raw = b"\n".join(self.data_lines)
        self.data_lines = []
        self.event_bytes = 0
        if raw == b"[DONE]":
            self.done = True
            return
        if self.done:
            return
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict) or "error" in body:
            raise ValueError("流式响应包含错误或无效事件。")
        self.events += 1
        if "usage" in body:
            self.usage_seen = True
            self.usage = body["usage"] if isinstance(body["usage"], dict) else None
        choices = body.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("流式响应选择项无效。")
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                raise ValueError("流式响应选择项无效。")
            finish = choice.get("finish_reason")
            if finish not in (None, "stop", "length", "max_tokens", "content_filter", "tool_calls", "function_call"):
                raise ValueError("流式响应结束状态无效。")
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ValueError("流式响应内容格式无效。")
            text, reason = delta.get("content"), delta.get("reasoning_content")
            if self.finish_reason is not None:
                if finish is not None and finish != self.finish_reason:
                    raise ValueError("流式响应结束状态发生冲突。")
                if text or reason:
                    raise ValueError("流式响应结束后继续返回正文。")
            elif finish is not None:
                self.finish_reason = finish
            if text is not None and not isinstance(text, str):
                raise ValueError("流式响应内容格式无效。")
            if text:
                self.content_characters += len(text)
                if self.content_characters > MAX_CONTENT_CHARACTERS:
                    raise ValueError("流式响应正文超出安全长度。")
                self.content.append(text)
            if isinstance(reason, str):
                self.reasoning_characters += len(reason)

    def stats(self):
        result = {"stream_content_characters": self.content_characters,
                "stream_reasoning_characters": self.reasoning_characters,
                "stream_events": self.events,
                "stream_complete": self.done and self.finish_reason == "stop" and self.error is None}
        if self.usage_seen:
            usage = self.usage or {}
            result["stream_usage"] = {key: usage[key] for key in
                                      ("prompt_tokens", "completion_tokens", "total_tokens")
                                      if type(usage.get(key)) is int and usage[key] >= 0}
        return result

    def response_body(self):
        result = {"choices": [{"finish_reason": self.finish_reason,
                               "message": {"content": "".join(self.content)}}],
                  "_stream_complete": self.stats()["stream_complete"],
                  "_stream_error": self.error or (None if self.done else "流式响应在DONE之前结束。")}
        if self.usage is not None:
            result["usage"] = self.usage
        return result


def receive_completion(provider, payload, *, post, permit, timeout_seconds,
                       check_cancelled, progress=lambda _stats: None, observation=None):
    """The caller's deadline/cancellation never waits for a slow public close.

    A daemon owns its permit through POST, reading and close. Cleanup can wait
    for the public HTTP timeout after cancellation, but at most four such
    lifecycles exist. Queue, event and assembled-content sizes are bounded.
    """
    check_cancelled()
    messages = queue.Queue(maxsize=8)
    stopped = threading.Event()
    start_lock = threading.Lock()
    observation = observation if observation is not None else {}
    observation["http_started"] = False
    deadline = time.monotonic() + timeout_seconds
    decoder = SSECompletion()

    def publish(kind, value):
        while not stopped.is_set():
            try:
                messages.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                pass

    def reader():
        response = None
        try:
            with start_lock:
                if stopped.is_set():
                    return
                check_cancelled()
                observation["http_started"] = True
            publish("started", None)
            response = post(provider, payload, timeout=timeout_seconds, stream=True,
                            check_status=False, retry_connection=False, allow_redirects=False)
            if stopped.is_set():
                return
            if response.status_code != 200:
                headers = getattr(response, "headers", None) or {}
                publish("response", (response.status_code, {}, decoder.stats(),
                                     {"Retry-After": headers["Retry-After"]} if "Retry-After" in headers else {}))
                return
            last_chars = 0
            last_progress = time.monotonic()
            # A one-byte public iterator cannot hide indefinitely behind a
            # succession of tiny heartbeat fragments while filling a chunk.
            for chunk in response.iter_content(chunk_size=1):
                if stopped.is_set():
                    return
                try:
                    decoder.feed(chunk)
                except (ValueError, UnicodeError):
                    decoder.error = "流式响应事件无效，未采用不完整结果。"
                    publish("response", (200, decoder.response_body(), decoder.stats(), {}))
                    return
                if decoder.done:
                    publish("response", (200, decoder.response_body(), decoder.stats(), {}))
                    return
                now = time.monotonic()
                if decoder.content_characters > last_chars and now - last_progress >= 2:
                    publish("progress", decoder.stats())
                    last_chars, last_progress = decoder.content_characters, now
            publish("response", (200, decoder.response_body(), decoder.stats(), {}))
        except Exception as exc:
            publish("error", exc)
        finally:
            try:
                if response is not None:
                    response.close()
            except Exception as exc:
                permit.cleanup_error_type = type(exc).__name__
            finally:
                permit.release()

    permit.transferred = True
    try:
        threading.Thread(target=reader, name="mathbank-pdf-sse", daemon=True).start()
    except BaseException:
        permit.release()
        raise
    try:
        while True:
            check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.exceptions.ReadTimeout("PDF stream attempt deadline reached")
            try:
                kind, value = messages.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            check_cancelled()
            if time.monotonic() >= deadline:
                raise requests.exceptions.ReadTimeout("PDF stream attempt deadline reached")
            if kind == "progress":
                progress(value)
            elif kind == "started":
                progress({"stream_content_characters": 0, "stream_reasoning_characters": 0,
                          "stream_events": 0, "stream_complete": False})
            elif kind == "error":
                raise value
            else:
                status, body, stats, headers = value
                progress(stats)
                return SimpleNamespace(status_code=status, json=lambda: body, headers=headers)
    finally:
        with start_lock:
            stopped.set()
