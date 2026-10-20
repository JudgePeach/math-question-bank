"""Reduce output size with two original-parser scopes sharing the entire PDF.

The local census only restricts which original question numbers a request may
return. It never cuts source text, assembles question bodies, or claims that a
model-generated question has been verified against original PDF pixels.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import hashlib
import json
import re
import threading
import time

import requests

from mathbank.content_locks import _formulas
from mathbank.document_projects import DocumentProjectError
from mathbank.document_requests import is_retryable_document_http_status
from mathbank.pdf_split_parallel import PdfParallelResult, pdf_split_request_slot
from mathbank.pdf_vision_request import _retry_delay
from mathbank.question_assets import _mask_literals, markdown_literal_ranges
from mathbank.task_manager import TaskCancelled


SCHEMA = "mathbank.pdf-full-context-split.v1"
_ANSWER_TITLES = {"答案", "参考答案", "参考答案与解析", "参考答案及解析", "答案与解析", "答案及解析"}
_SECTION = re.compile(r"^(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十]+[）)])"
                      r"(?:单项选择题|多项选择题|选择题|填空题|解答题|证明题)(?:[（(:：].*)?$")
_NUMBER = re.compile(r"^[ \t]{0,3}(?:#{1,6}[ \t]+)?(?:\*\*|__)?"
                     r"([1-9][0-9]{0,2})(?:\\?[.．、])(?:\*\*|__)?(?![0-9])", re.MULTILINE)
_ENV = re.compile(r"\\(begin|end)\s*\{([^{}\n]+)\}")
_ATOMIC = {"choices", "choices*", "tabular", "tabular*", "tabularx", "longtable", "tblr", "longtblr", "talltblr",
           "array", "cases", "matrix", "pmatrix", "bmatrix", "align", "align*", "aligned", "gather", "gather*",
           "equation", "equation*", "multline", "multline*", "tikzpicture", "enumerate", "itemize"}
_LOCK = re.compile(r'<mathbank-math id="MBM_[A-Za-z0-9_-]+">([\s\S]*?)</mathbank-math>')


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _blank(chars, start, end):
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def _heading(line):
    return re.sub(r"\s+", "", line.strip(" #*_\t\r\n"))


def full_context_pdf_split_eligibility(source):
    """Return a conservative explicit 1..N census, otherwise no paid route."""
    if not isinstance(source, str) or not 8000 <= len(source) <= 500000:
        return None
    literal_view = _mask_literals(source, tex_comments=False)
    chars = list(literal_view)
    for start, end in markdown_literal_ranges(source):
        _blank(chars, start, end)
    initial = "".join(chars)
    # Mask balanced atomic structures before looking at line headings. An
    # unknown or broken environment cannot certify a safe question census.
    stack, protected = [], None
    for token in _ENV.finditer(initial):
        action, env = token[1], token[2].strip()
        if action == "begin":
            stack.append((env, token.start()))
            if protected is None and env in _ATOMIC:
                protected = token.start()
        else:
            if not stack or stack[-1][0] != env:
                return None
            stack.pop()
            if protected is not None and not any(name in _ATOMIC for name, _ in stack):
                _blank(chars, protected, token.end())
                protected = None
    if stack:
        return None
    for formula in _formulas("".join(chars)):
        _blank(chars, formula.start, formula.end)
    view = "".join(chars)
    lines = []
    offset = 0
    for line in view.splitlines(keepends=True):
        lines.append((offset, _heading(line)))
        offset += len(line)
    answer_heads = [start for start, title in lines if title in _ANSWER_TITLES]
    if len(answer_heads) != 1:
        return None
    end = answer_heads[0]
    sections = [start for start, title in lines if start < end and _SECTION.fullmatch(title)]
    # Require the actual exam section: administrative notes alone can also
    # form a superficially contiguous numbered list.
    if not sections:
        return None
    start = sections[0]
    matches = list(_NUMBER.finditer(view, start, end))
    numbers = [int(match[1]) for match in matches]
    if not 10 <= len(numbers) <= 80 or numbers != list(range(1, len(numbers)+1)):
        return None
    for match in matches:
        line_end = source.find("\n", match.end(), end)
        line_end = end if line_end < 0 else line_end
        if not source[match.end():line_end].strip():
            return None
    # These lengths only balance requested number lists. They are not source
    # ownership proofs and never affect eligibility or copy any source range.
    weights = {number: 200 + (matches[index+1].start() if index+1 < len(matches) else end) - match.start()
               for index, (number, match) in enumerate(zip(numbers, matches))}
    answer_numbers = list(_NUMBER.finditer(view, end, len(view)))
    for index, match in enumerate(answer_numbers):
        number = int(match[1])
        if number in weights:
            stop = answer_numbers[index+1].start() if index+1 < len(answer_numbers) else len(source)
            weights[number] += stop-match.start()
    return {"schema": SCHEMA, "source_numbers": numbers, "question_count": len(numbers),
            "stem_range": [start, end], "answer_heading_start": end, "source_sha256": _sha(source),
            "estimated_output_characters": weights, "estimate_is_token_measurement": False}


def _validate(rows, numbers):
    if (not isinstance(rows, list) or len(rows) != len(numbers)
            or any(not isinstance(row, dict) or not isinstance(row.get("content"), str)
                   or not row["content"].strip() or type(row.get("source_number")) is not int for row in rows)):
        raise ValueError("拆题结果未完整覆盖指定原题号范围。")
    supplied = [row["source_number"] for row in rows]
    if sorted(supplied) != numbers:
        raise ValueError("拆题结果原题号重复、遗漏或超出指定范围。")
    return sorted(rows, key=lambda row: row["source_number"])


def _retryable(error):
    if getattr(error, "retryable", None) is False or isinstance(error, DocumentProjectError):
        return False
    if isinstance(error, requests.exceptions.HTTPError):
        response = error.response
        return response is not None and is_retryable_document_http_status(response.status_code)
    if isinstance(error, (requests.exceptions.SSLError, requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema,
                          requests.exceptions.MissingSchema, requests.exceptions.InvalidHeader,
                          requests.exceptions.TooManyRedirects)):
        return False
    cause = error.__cause__
    if cause is not None and getattr(cause, "retryable", None) is False:
        return False
    return isinstance(error, (ValueError, requests.exceptions.RequestException))


def request_full_context_pdf_split(source, *, locked_source, provider, parse_full, request_post,
        cache=None, diagnostics, check_cancelled=lambda: None, report_attempt=lambda event: None):
    """Two entire-context requests, one shared failed-scope repair, <=3 POSTs.

    ``parse_full`` is the existing full-card parser with an added system-only
    ``requested_source_numbers`` constraint. It must use the supplied POST hook
    without connection retries and validate historical raw scope results too.
    Scoped output is provisional until the caller runs its existing whole-PDF
    source reconciliation, answer protection and asset checks.
    """
    check_cancelled()
    plan = full_context_pdf_split_eligibility(source)
    if (plan is None or not isinstance(locked_source, str)
            or _LOCK.sub(lambda match: match[1], locked_source) != source):
        return None
    weights = plan["estimated_output_characters"]
    total = sum(weights.values())
    midpoint = min(range(1, plan["question_count"]),
                   key=lambda boundary: abs(2*sum(weights[n] for n in plan["source_numbers"][:boundary])-total))
    scopes = [plan["source_numbers"][:midpoint], plan["source_numbers"][midpoint:]]
    profile = {"provider": getattr(provider, "provider_code", None), "model": getattr(provider, "model_name", None),
        "effort": getattr(provider, "reasoning_effort", None),
        "endpoint_sha256": _sha(str(getattr(provider, "chat_completions_url", "")))}
    common = {"schema": SCHEMA, "source_sha256": plan["source_sha256"],
              "locked_source_sha256": _sha(locked_source), "provider_profile": profile}
    bindings = [{**common, "requested_source_numbers": numbers} for numbers in scopes]
    identities = ["full_context_"+_sha(_json(binding))[:24] for binding in bindings]
    report = {"schema": SCHEMA, "mode": "full_context", "used_parallel": True, "status": "preparing",
        "concurrency": 2, "max_posts": 3, "calls": 0, "group_count": 2, "completed_groups": 0,
        "reused_groups": 0, "ordered_source_numbers": list(plan["source_numbers"]),
        "pending_source_numbers": list(plan["source_numbers"]), "pending_group_ids": list(identities),
        "failed_group_ids": [], "attempts": [], "cache_errors": [], "source_verified": False,
        "groups": [{"id": key, "source_numbers": numbers, "status": "pending", "attempts": 0,
                    "question_count": 0, "estimated_output_characters": sum(weights[n] for n in numbers)}
                   for key, numbers in zip(identities, scopes)], "estimate_is_token_measurement": False}
    diagnostics["pdf_parallel_split"] = report
    mutex, publishing = threading.RLock(), threading.Lock()
    stopped = threading.Event()
    accepted, failed, retry_ok, delays = {}, {}, {}, {}

    def checked():
        check_cancelled()
        if stopped.is_set():
            raise TaskCancelled()

    def refresh():
        report.update(completed_groups=len(accepted),
            failed_group_ids=[identities[i] for i in sorted(failed)],
            pending_group_ids=[identities[i] for i in range(2) if i not in accepted],
            pending_source_numbers=[n for i, nums in enumerate(scopes) if i not in accepted for n in nums],
            question_count=sum(len(rows) for rows in accepted.values()))

    def publish(index, rows, *, reused=False):
        with publishing:
            checked()
            persisted = False
            if not reused and cache is not None:
                try:
                    saved = cache.save_partial(identities[index], {"schema": SCHEMA,
                        "source_numbers": scopes[index], "questions": deepcopy(rows)}, input_binding=bindings[index])
                    persisted = saved is not False
                    if not persisted:
                        report["cache_errors"].append({"scope_id": identities[index], "error_type": "SaveReturnedFalse"})
                except (TaskCancelled, DocumentProjectError):
                    raise
                except Exception as error:
                    report["cache_errors"].append({"scope_id": identities[index], "error_type": type(error).__name__})
            checked()
            with mutex:
                accepted[index] = deepcopy(rows)
                failed.pop(index, None)
                report["groups"][index].update(status="reused" if reused else "completed",
                    question_count=len(rows), persisted=bool(reused or persisted))
                report["reused_groups"] += int(reused)
                refresh()

    for index in range(2):
        checked()
        if cache is not None:
            packet = cache.load_partial(identities[index], input_binding=bindings[index])
            if isinstance(packet, dict) and packet.get("schema") == SCHEMA and packet.get("source_numbers") == scopes[index]:
                try:
                    rows = _validate(packet.get("questions"), scopes[index])
                except ValueError:
                    continue
                publish(index, rows, reused=True)

    def send(index, phase):
        checked()
        local = {}
        attempt = {"scope_id": identities[index], "source_numbers": scopes[index], "phase": phase,
                   "status": "waiting", "calls": 0, "usage": {}}
        with mutex:
            report["attempts"].append(attempt)
            report["groups"][index].update(status="running", attempts=report["groups"][index]["attempts"]+1)
        started = time.monotonic()

        def emit(status, **extra):
            if stopped.is_set():
                return
            with mutex:
                report_attempt({"stage": "paper_split", "page_index": None, "status": status,
                    "attempt": attempt.get("number", 1), "max_attempts": 3, "parallel_mode": "full_context",
                    "parallel_scope_id": identities[index], "source_numbers": scopes[index],
                    "requested_source_numbers": scopes[index], **extra})

        def scoped_event(value):
            if stopped.is_set():
                return
            with mutex:
                for key in ("usage", "http_status", "finish_reason", "stream_content_characters",
                            "stream_complete", "stream_usage", "response_characters", "elapsed_seconds"):
                    if key in value:
                        attempt[key] = deepcopy(value[key])
                report_attempt({**value, "parallel_mode": "full_context", "parallel_scope_id": identities[index],
                                "source_numbers": scopes[index], "requested_source_numbers": scopes[index]})

        def post(config, payload, **kwargs):
            checked()
            with mutex:
                if report["calls"] >= 3 or attempt["calls"]:
                    raise ValueError("整卷并发拆题请求预算已用尽，未追加请求。")
                report["calls"] += 1
                attempt.update(calls=1, number=report["calls"], status="running")
            emit("running")
            kwargs.update(retry_connection=False, allow_redirects=False)
            try:
                response = request_post(config, payload, **kwargs)
            except requests.exceptions.HTTPError as error:
                if error.response is not None:
                    with mutex:
                        attempt["http_status"] = error.response.status_code
                        if error.response.status_code in (429, 503):
                            attempt["retry_delay_seconds"] = _retry_delay(error.response)
                raise
            with mutex:
                attempt["http_status"] = response.status_code
                if response.status_code in (429, 503):
                    attempt["retry_delay_seconds"] = _retry_delay(response)
            checked()
            return response

        try:
            with pdf_split_request_slot(provider, check_cancelled=checked):
                with mutex:
                    attempt["permit_wait_seconds"] = round(time.monotonic()-started, 3)
                rows = parse_full(locked_source, pdf_retry=True, pdf_max_attempts=1, connection_retry=False,
                    request_post=post, completion_cache=cache, requested_source_numbers=list(scopes[index]),
                    request_diagnostics=local, request_report_attempt=scoped_event)
            checked()
            rows = _validate(rows, scopes[index])
            publish(index, rows)
            with mutex:
                attempt.update(status="completed", elapsed_seconds=round(time.monotonic()-started, 3),
                               usage=deepcopy(local.get("usage") or attempt.get("usage") or {}),
                               source_number_resolution=deepcopy(local.get("pdf_source_number_resolution")))
            emit("succeeded", elapsed_seconds=attempt["elapsed_seconds"], usage=attempt["usage"])
        except (TaskCancelled, DocumentProjectError):
            raise
        except Exception as error:
            checked()
            with mutex:
                failed[index] = type(error).__name__
                retry_ok[index] = _retryable(error)
                delays[index] = attempt.get("retry_delay_seconds", 0)
                attempt.update(status="failed", error_type=type(error).__name__, retryable=retry_ok[index],
                               elapsed_seconds=round(time.monotonic()-started, 3))
                report["groups"][index]["status"] = "failed"
                refresh()
            emit("failed", elapsed_seconds=attempt["elapsed_seconds"], usage=deepcopy(local.get("usage") or {}))

    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf-full-context")
    try:
        report["status"] = "running"
        pending = {executor.submit(send, index, "primary") for index in range(2) if index not in accepted}
        while pending:
            checked()
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
        eligible = [index for index in sorted(failed) if retry_ok.get(index)]
        if eligible and report["calls"] < 3:
            index = eligible[0]
            deadline = time.monotonic()+delays.get(index, 0)
            while time.monotonic() < deadline:
                checked()
                time.sleep(min(0.1, max(0, deadline-time.monotonic())))
            send(index, "repair")
        checked()
        refresh()
        if not accepted:
            report["status"] = "failed"
            raise ValueError("两组完整上下文拆题均未得到可用题目；已保留识图稿和失败记录，请核对模型服务。")
        rows = sorted((row for values in accepted.values() for row in values), key=lambda row: row["source_number"])
        report["status"] = "partial" if failed else "used"
        return PdfParallelResult(rows, frozenset(), bool(failed), True)
    except TaskCancelled:
        report["status"] = "cancelled"
        raise
    except DocumentProjectError:
        report["status"] = "source_changed"
        raise
    finally:
        stopped.set()
        executor.shutdown(wait=False, cancel_futures=True)
