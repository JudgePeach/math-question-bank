"""Bounded retries for PDF page recognition, without changing other AI flows."""

from __future__ import annotations

from copy import deepcopy
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import time
from typing import Callable

import requests

from mathbank.task_manager import TaskCancelled
from mathbank.document_requests import DOCUMENT_AI_TIMEOUT_SECONDS


PDF_VISION_TIMEOUT_SECONDS = DOCUMENT_AI_TIMEOUT_SECONDS
PDF_VISION_MAX_ATTEMPTS = 2
PDF_VISION_RETRY_DELAY_SECONDS = 1
PDF_VISION_MAX_RETRY_DELAY_SECONDS = 30
_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _request_metadata(provider, payload: dict) -> dict:
    """Describe only sent model controls; never copy prompts or credentials."""
    metadata = {}
    code = getattr(provider, "provider_code", None)
    model = payload.get("model")
    for key, value, limit in (("provider_code", code, 64), ("model", model, 256)):
        if isinstance(value, str) and value.strip():
            metadata[key] = value[:limit]
    for key in ("enable_thinking", "stream"):
        if isinstance(payload.get(key), bool):
            metadata[key] = payload[key]
    for key in ("thinking_budget", "max_tokens", "max_completion_tokens"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            metadata[key] = value
    effort = payload.get("reasoning_effort")
    if isinstance(effort, str) and effort in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "default"}:
        metadata["reasoning_effort"] = effort
    formatting = payload.get("response_format")
    if (isinstance(formatting, dict) and isinstance(formatting.get("type"), str)
            and formatting["type"] in {"text", "json_object", "json_schema"}):
        metadata["response_format_type"] = formatting["type"]
    return metadata


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _json_failure_metadata(error: BaseException) -> dict:
    """Record parser coordinates and hashes, never response text or excerpts."""
    reasons = {
        "Expecting value": "expected_value",
        "Extra data": "extra_data",
        "Unterminated string starting at": "unterminated_string",
        "Invalid control character at": "control_character",
        "Invalid \\escape": "invalid_escape",
        "Expecting property name enclosed in double quotes": "property_quotes",
        "Expecting ',' delimiter": "comma_delimiter",
        "Expecting ':' delimiter": "colon_delimiter",
        "Invalid \\uXXXX escape": "unicode_escape",
    }
    current = error
    for _ in range(5):
        if isinstance(current, json.JSONDecodeError):
            return {"code": reasons.get(current.msg, "invalid_json"),
                    "position": current.pos, "line": current.lineno, "column": current.colno,
                    "candidate_characters": len(current.doc), "candidate_sha256": _text_digest(current.doc)}
        current = current.__cause__ or current.__context__
        if current is None:
            break
    return {}


class PDFVisionRequestError(ValueError):
    """Safe failure details plus evidence from every actual HTTP attempt."""

    def __init__(self, message: str, attempts: list[dict], *, timeout_seconds: float = PDF_VISION_TIMEOUT_SECONDS):
        self.attempts = deepcopy(attempts)
        self.usage = collected_usage(attempts)
        details = "；".join(f"第 {item['attempt']} 次：{item.get('error', '失败')}" for item in attempts)
        super().__init__(f"{message}（共尝试 {len(attempts)} 次，单次限时 {timeout_seconds:g} 秒）。{details}")


class PDFVisionResponseError(ValueError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def collected_usage(attempts: list[dict]) -> dict:
    """Sum only supplied counts; missing/failed responses remain unknown."""
    return {key: sum(item["usage"][key] for item in attempts if key in item.get("usage", {}))
            for key in _USAGE_FIELDS if any(key in item.get("usage", {}) for item in attempts)}


def _retry_delay(response) -> float:
    """Respect throttling guidance without allowing an unbounded wait."""
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers and response.status_code in (429, 503) else None
    if raw is None:
        return PDF_VISION_RETRY_DELAY_SECONDS
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            stamp = parsedate_to_datetime(raw)
            if stamp.tzinfo is None:
                return PDF_VISION_RETRY_DELAY_SECONDS
            delay = stamp.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return PDF_VISION_RETRY_DELAY_SECONDS
    if not math.isfinite(delay):
        return PDF_VISION_RETRY_DELAY_SECONDS
    return min(PDF_VISION_MAX_RETRY_DELAY_SECONDS, max(0, delay))


def completion_content(body, label: str, *, max_chars: int, allow_missing_finish: bool = False) -> str:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise PDFVisionResponseError(f"{label}未返回有效 Choices 结果。")
    finish = choices[0].get("finish_reason")
    if finish == "content_filter":
        raise PDFVisionResponseError(f"{label}被服务方过滤，未接受结果。", retryable=False)
    if finish != "stop" and not (allow_missing_finish and finish is None):
        raise PDFVisionResponseError(f"{label}未正常结束或输出被截断，未接受不完整结果。")
    message = choices[0].get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > max_chars:
        raise PDFVisionResponseError(f"{label}内容为空、格式无效或过长。")
    return raw


def request_pdf_vision(
    provider, payload: dict, *, post: Callable, validate: Callable,
    label: str, stage: str, page_index: int | None = None,
    timeout_seconds: float = PDF_VISION_TIMEOUT_SECONDS,
    check_cancelled: Callable[[], None] = lambda: None,
    report_attempt: Callable[[dict], None] = lambda _event: None,
) -> dict:
    """At most two physical POSTs, using the same configured provider.

    Callers validate inputs before entering this helper. A usable transcript
    with uncertain figure geometry is a successful response needing review;
    its validator must return that transcript instead of raising for a retry.
    """
    attempts: list[dict] = []
    for number in range(1, PDF_VISION_MAX_ATTEMPTS + 1):
        check_cancelled()
        item = {"stage": stage, "page_index": page_index, "attempt": number,
                "max_attempts": PDF_VISION_MAX_ATTEMPTS, "timeout_seconds": timeout_seconds,
                "status": "running", "usage": {}, **_request_metadata(provider, payload)}
        attempts.append(item)
        report_attempt(deepcopy(item))
        started = time.monotonic()
        retryable = False
        retry_delay = PDF_VISION_RETRY_DELAY_SECONDS
        try:
            try:
                response = post(provider, payload, timeout=timeout_seconds,
                                check_status=False, retry_connection=False, allow_redirects=False)
            except TaskCancelled:
                raise
            except requests.exceptions.RequestException as exc:
                retryable = not isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.InvalidURL,
                                                 requests.exceptions.InvalidSchema, requests.exceptions.MissingSchema,
                                                 requests.exceptions.InvalidHeader, requests.exceptions.URLRequired,
                                                 requests.exceptions.TooManyRedirects))
                raise PDFVisionResponseError(f"{label}请求失败（{type(exc).__name__}）。",
                                             retryable=retryable) from exc
            except Exception as exc:
                # Configuration and programming errors must not cause another
                # paid request, and arbitrary provider exception text is private.
                raise PDFVisionResponseError(f"{label}请求失败（{type(exc).__name__}）。",
                                             retryable=False) from exc
            check_cancelled()
            item["http_status"] = response.status_code
            if response.status_code != 200:
                retry_delay = _retry_delay(response)
                raise PDFVisionResponseError(f"{label}请求失败（HTTP {response.status_code}）。",
                                             retryable=response.status_code in (408, 425, 429) or 500 <= response.status_code < 600)
            try:
                body = response.json()
            except Exception as exc:
                raise PDFVisionResponseError(f"{label}服务未返回有效 JSON。") from exc
            usage = body.get("usage") if isinstance(body, dict) else None
            item["usage"] = {key: usage[key] for key in _USAGE_FIELDS
                             if isinstance(usage, dict) and isinstance(usage.get(key), int)
                             and not isinstance(usage[key], bool) and usage[key] >= 0}
            choices = body.get("choices") if isinstance(body, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                finish = choices[0].get("finish_reason")
                if finish in ("stop", "length", "content_filter", "tool_calls", "function_call", None):
                    item["finish_reason"] = finish
                message = choices[0].get("message")
                raw = message.get("content") if isinstance(message, dict) else None
                reasoning = message.get("reasoning_content") if isinstance(message, dict) else None
                if isinstance(reasoning, str):
                    item["reasoning_characters"] = len(reasoning)
                if isinstance(raw, str):
                    stripped = raw.lstrip()
                    item["response_characters"] = len(raw)
                    item["response_sha256"] = _text_digest(raw)
                    item["response_start"] = ("fenced" if stripped.startswith("```") else
                                              "object" if stripped.startswith("{") else
                                              "array" if stripped.startswith("[") else
                                              "text" if stripped else "empty")
            result = validate(body)
            check_cancelled()
            item.update(status="succeeded", elapsed_seconds=round(time.monotonic() - started, 3))
            report_attempt(deepcopy(item))
            result.update(attempts=deepcopy(attempts), visual_calls=len(attempts),
                          usage=collected_usage(attempts),
                          usage_complete=all(all(key in attempt["usage"] for key in _USAGE_FIELDS) for attempt in attempts))
            return result
        except TaskCancelled:
            item.update(status="cancelled", elapsed_seconds=round(time.monotonic() - started, 3))
            report_attempt(deepcopy(item))
            raise
        except ValueError as exc:
            retryable = getattr(exc, "retryable", True)
            json_failure = _json_failure_metadata(exc)
            if json_failure:
                item["json_failure"] = json_failure
            item.update(status="failed", error=str(exc), elapsed_seconds=round(time.monotonic() - started, 3),
                        retrying=retryable and number < PDF_VISION_MAX_ATTEMPTS)
            if item["retrying"]:
                item["retry_delay_seconds"] = retry_delay
            report_attempt(deepcopy(item))
            # A cancellation requested during a failed request takes priority.
            check_cancelled()
            if not item["retrying"]:
                raise PDFVisionRequestError(f"{label}失败", attempts, timeout_seconds=timeout_seconds) from exc
        deadline = time.monotonic() + retry_delay
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise AssertionError("PDF request attempts must reach a terminal state")
