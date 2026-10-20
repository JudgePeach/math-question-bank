"""Two bounded PDF hybrid requests over already certified dependency closures.

This is a scheduler for the existing hybrid prompt/adopter, not a new source
parser.  It never infers question boundaries, changes a group's route, or
falls back to a paid whole-paper call after starting a parallel request.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import threading
import time

from mathbank.ai_providers import apply_model_thinking_policy
from mathbank.paper_parse import PAPER_SPLIT_TIMEOUT_SECONDS, request_pdf_paper_completion
from mathbank.pdf_hybrid_plan import (
    PdfHybridPlanError, require_pdf_hybrid_plan, require_pdf_snapshot_hybrid_plan,
)
from mathbank.pdf_vision_request import completion_content, PDFVisionRequestError, _retry_delay
from mathbank.source_hybrid_request import build_source_hybrid_messages, _adopt_parts
from mathbank.source_metadata import SourceMetadataContractError, SourceMetadataMessageBudgetError
from mathbank.source_review_results import parse_review_response
from mathbank.task_manager import TaskCancelled


SCHEMA = "mathbank.pdf-parallel-split.v1"
MIN_SPLIT_CHARACTERS = 256
_HARD_ERRORS = (PdfHybridPlanError, SourceMetadataContractError)


@dataclass
class PdfParallelResult:
    questions: list[dict]
    preserved_indices: frozenset[int] = frozenset()
    partial: bool = False
    used_parallel: bool = False


class PdfParallelPersistenceError(ValueError):
    """A valid result could not be saved; it must not cause another POST."""


class _RequestGate:
    """One shared FIFO gate also covers providers that do not use SSE."""

    def __init__(self):
        self.condition = threading.Condition()
        self.waiters = deque()
        self.active = 0
        self.providers = {}

    @contextmanager
    def acquire(self, key, check_cancelled, timeout=PAPER_SPLIT_TIMEOUT_SECONDS):
        ticket = object()
        deadline = time.monotonic() + timeout
        acquired = False
        with self.condition:
            self.waiters.append(ticket)
        try:
            while not acquired:
                check_cancelled()
                with self.condition:
                    if (self.waiters[0] is ticket and self.active < 2
                            and self.providers.get(key, 0) < 2):
                        self.waiters.popleft()
                        self.active += 1
                        self.providers[key] = self.providers.get(key, 0) + 1
                        acquired = True
                        self.condition.notify_all()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError("拆题并发名额等待超时，未发送新的模型请求。")
                        self.condition.wait(min(0.1, remaining))
            check_cancelled()
            yield
        finally:
            with self.condition:
                if acquired:
                    self.active -= 1
                    self.providers[key] -= 1
                    if not self.providers[key]:
                        del self.providers[key]
                else:
                    self.waiters.remove(ticket)
                self.condition.notify_all()


_REQUEST_GATE = _RequestGate()


@contextmanager
def pdf_split_request_slot(provider, *, check_cancelled=lambda: None):
    """Share the same limit with a caller's original whole-paper route."""
    with _REQUEST_GATE.acquire(_provider_key(provider), check_cancelled):
        yield


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value):
    # Curriculum insertion order is significant for classification priority.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _provider_key(provider):
    return (str(getattr(provider, "provider_code", "")),
            str(getattr(provider, "api_base", "")),
            str(getattr(provider, "chat_completions_url", "")))


def _validator(plan, task_id, generation):
    validate = (require_pdf_hybrid_plan if plan.get("source_basis") == "native_pdf"
                else require_pdf_snapshot_hybrid_plan)
    validate(plan, task_id=task_id, generation=generation)


def _cache_identity(plan, curriculum, provider, group):
    # The certificate was checked before this projection. Keep the pixel
    # hashes but omit relocatable filesystem paths/task-specific URLs, so the
    # same sealed input can survive a restart without accepting changed images.
    evidence_payload = getattr(plan.get("_source_review_evidence"), "payload", None)
    evidence = json.loads(evidence_payload) if isinstance(evidence_payload, str) else {}
    # Preserve the issuer's page/detail/figure ordering: a multiset would miss
    # two different page or figure pixels being exchanged.
    asset_hashes = [row["sha256"] for row in evidence.get("assets", [])]
    scope = {"schema": SCHEMA, "source_sha256": plan["source_sha256"],
             "document_sha256": plan["document_sha256"], "source_basis": plan["source_basis"],
             "owned_ranges": group["owned_ranges"], "context_ranges": group["context_ranges"],
             "source_numbers": group["source_numbers"], "route": group["route"],
             "asset_sha256": asset_hashes, "reasons": group["reasons"]}
    scope_id = "group_" + _sha(_json(scope))[:32]
    # Group IDs are task-bound; normalize only that literal in the prompt for
    # a restart-stable binding. Private certificates are always rechecked.
    messages, _ = build_source_hybrid_messages(plan, curriculum, [group], source_label="PDF")
    canonical = _json(messages).replace(group["id"], "PDF_PARALLEL_CACHE_GROUP")
    binding = _sha(_json([scope, canonical, getattr(provider, "model_name", ""),
                         _provider_key(provider), getattr(provider, "reasoning_effort", None)]))
    return scope_id, binding


def _cached_questions(cached, group, plan, curriculum, normalize_fillin):
    """Replay trusted local data through the original adopter, without HTTP."""
    if (not isinstance(cached, dict) or cached.get("source_numbers") != group["source_numbers"]
            or cached.get("route") != group["route"] or not isinstance(cached.get("questions"), list)
            or len(cached["questions"]) != len(group["question_ids"])
            or not all(isinstance(q, dict) for q in cached["questions"])):
        return None
    questions = cached["questions"]
    messages, locks = build_source_hybrid_messages(plan, curriculum, [group], source_label="PDF")
    if group["route"] == "metadata":
        fields = ("question_type", "category_compulsory", "category_chapter", "difficulty")
        parsed = {"metadata": {"items": [dict(id=qid, **{k: q.get(k) for k in fields})
                    for qid, q in zip(group["question_ids"], questions)]}, "splits": []}
    else:
        parsed = {"metadata": {"items": []}, "splits": [{"group_id": group["id"],
                  "questions": [dict(deepcopy(q), source_id=qid)
                                for qid, q in zip(group["question_ids"], questions)]}]}
    adopted, errors = _adopt_parts(parsed, [group], locks, curriculum, normalize_fillin,
                                  plan["_inspection"]["questions"], plan["source"])
    if errors or group["id"] not in adopted:
        return None
    # Replayed metadata must still be byte-for-byte locally assembled. For
    # risk groups the adopter proves source/answer ownership, not PDF truth.
    for old, new in zip(questions, adopted[group["id"]]):
        if any(old.get(field, "") != new.get(field, "") for field in ("content", "answer_markdown")):
            return None
    return deepcopy(questions)


def request_pdf_parallel_split(
    plan: dict, curriculum: dict, *, provider, post, diagnostics: dict,
    task_id: str, generation: int = 0, full_source_fallback=None,
    single_legacy_fallback=None, normalize_fillin=lambda value: value,
    check_cancelled=lambda: None, report_attempt=lambda event: None,
    on_group_result=None, load_group_result=None, max_posts: int = 3,
    min_split_characters: int = MIN_SPLIT_CHARACTERS,
) -> PdfParallelResult:
    """Use two initial envelopes and at most one shared failed-groups repair.

    Cache callbacks accept ``(scope_id, result, input_binding=sha)`` and
    ``(scope_id, input_binding=sha)`` respectively. Only locally stored results
    may be supplied; model-supplied cache/review declarations are never trusted.
    The fallback retains its original request budget and is called only before
    this scheduler has sent any POST. No private plan is serialized as proof.
    """
    if type(max_posts) is not int or max_posts not in (2, 3):
        raise ValueError("并发拆题总请求预算仅支持2或3次。")
    if type(min_split_characters) is not int or min_split_characters < 0:
        raise ValueError("并发拆题最小正文长度无效。")
    check_cancelled()
    _validator(plan, task_id, generation)
    groups = plan.get("groups", [])
    report = {"schema": SCHEMA, "status": "preparing", "used_parallel": False,
              "calls": 0, "max_posts": max_posts, "concurrency": 2,
              "group_count": len(groups), "completed_groups": 0, "reused_groups": 0,
              "failed_group_ids": [], "pending_group_ids": [], "attempts": [], "groups": [],
              "source_sha256": plan["source_sha256"], "question_count": 0}
    diagnostics["pdf_parallel_split"] = report
    fallback = single_legacy_fallback or full_source_fallback
    split_characters = sum(end - start for group in groups if group["route"] == "split"
                           for start, end in group["owned_ranges"])

    def original(reason):
        check_cancelled()
        _validator(plan, task_id, generation)
        report.update(status="original", fallback_reason=reason)
        if fallback is None:
            raise ValueError("当前来源不适合并发拆题，需要原拆题入口。")
        with pdf_split_request_slot(provider, check_cancelled=check_cancelled):
            value = fallback()
        check_cancelled()
        _validator(plan, task_id, generation)
        if isinstance(value, dict):
            return PdfParallelResult(value["questions"], frozenset(value.get("preserved_indices", ())),
                                     bool(value.get("partial", False)), False)
        return PdfParallelResult(getattr(value, "questions", value),
            frozenset(getattr(value, "preserved_indices", ())), bool(getattr(value, "partial", False)), False)

    if plan.get("mode") != "hybrid" or len(groups) < 2 or split_characters < min_split_characters:
        return original("not_enough_independent_split_work")
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("PDF 试卷拆题所用的模型服务未配置。")

    # Prepare every full message before the first paid call; an oversize prompt
    # may still use the established original route without consuming a retry.
    identities = {}
    try:
        for group in groups:
            identities[group["id"]] = _cache_identity(plan, curriculum, provider, group)
        weights = [max(1, sum(b - a for a, b in group["owned_ranges"]))
                   if group["route"] == "split" else max(1, len(group["question_ids"]) * 100)
                   for group in groups]
        boundary = min(range(1, len(groups)), key=lambda i: abs(sum(weights[:i]) - sum(weights[i:])))
        for batch in (groups[:boundary], groups[boundary:]):
            build_source_hybrid_messages(plan, curriculum, batch, source_label="PDF")
    except SourceMetadataMessageBudgetError:
        return original("message_budget_exceeded")

    report.update(status="running", used_parallel=True)
    lock = threading.RLock()
    publication = threading.Lock()
    abort = threading.Event()
    fatal_error = []
    accepted, errors, retryable, retry_delays = {}, {}, {}, {}
    states = {group["id"]: {"id": group["id"], "source_numbers": list(group["source_numbers"]),
              "route": group["route"], "status": "pending", "question_count": 0, "attempts": 0}
              for group in groups}
    report["groups"] = list(states.values())

    def checked():
        check_cancelled()
        if abort.is_set():
            if fatal_error:
                raise fatal_error[0]
            raise TaskCancelled()

    def recheck():
        checked()
        _validator(plan, task_id, generation)

    def refresh():
        report.update(completed_groups=len(accepted),
            failed_group_ids=[group["id"] for group in groups if group["id"] in errors],
            pending_group_ids=[gid for gid, state in states.items() if state["status"] in ("pending", "running")],
            question_count=sum(len(value) for value in accepted.values()))

    def publish(group, questions, *, reused=False):
        with publication:
            recheck()
            gid = group["id"]
            if not reused and on_group_result is not None:
                scope_id, binding = identities[gid]
                try:
                    on_group_result(scope_id, {"questions": deepcopy(questions),
                        "source_numbers": list(group["source_numbers"]), "route": group["route"]},
                        input_binding=binding)
                except Exception as exc:
                    report["persistence_error_type"] = type(exc).__name__
                    failure = PdfParallelPersistenceError("拆题结果未能保存，未追加模型请求。")
                    failure.__cause__ = exc
                    fatal_error.append(failure)
                    abort.set()
                    raise failure
            recheck()
            with lock:
                accepted[gid] = questions
                errors.pop(gid, None)
                states[gid].update(status="reused" if reused else "completed", question_count=len(questions))
                if reused:
                    report["reused_groups"] += 1
                refresh()

    for group in groups:
        recheck()
        if load_group_result is not None:
            scope_id, binding = identities[group["id"]]
            cached = load_group_result(scope_id, input_binding=binding)
            if cached is not None:
                questions = _cached_questions(cached, group, plan, curriculum, normalize_fillin)
                if questions is not None:
                    publish(group, questions, reused=True)

    def send(batch, phase):
        recheck()
        local_diagnostics = {}
        messages, locks = build_source_hybrid_messages(plan, curriculum, batch, source_label="PDF")
        payload = apply_model_thinking_policy({"model": provider.model_name, "messages": messages,
            "response_format": {"type": "json_object"}, "temperature": 0.2, "max_tokens": 65536},
            provider=provider, task="parse")
        attempt = {"phase": phase, "group_ids": [g["id"] for g in batch], "status": "waiting",
                   "usage": {}, "calls": 0}
        with lock:
            report["attempts"].append(attempt)
            for group in batch:
                states[group["id"]].update(status="running", attempts=states[group["id"]]["attempts"] + 1)
            refresh()

        def counted_post(config, data, **kwargs):
            recheck()
            with lock:
                if report["calls"] >= max_posts:
                    raise ValueError("整卷拆题请求预算已用尽。")
                report["calls"] += 1
                attempt.update(calls=1, number=report["calls"], status="running")
            response = post(config, data, **kwargs)
            if response.status_code in (429, 503):
                with lock:
                    attempt["retry_delay_seconds"] = _retry_delay(response)
            return response

        def event(value):
            if abort.is_set():
                return
            with lock:
                attempt.update({k: deepcopy(v) for k, v in value.items()
                    if k not in ("attempt", "max_attempts", "error", "group_ids", "calls", "status")})
            report_attempt({**value, "parallel_phase": phase,
                            "parallel_group_ids": [g["id"] for g in batch]})

        def parse(body, metadata):
            raw = completion_content(body, "PDF 混合拆题", max_chars=500000)
            parsed = parse_review_response(raw)
            return _adopt_parts(parsed, batch, locks, curriculum, normalize_fillin,
                                plan["_inspection"]["questions"], plan["source"])

        try:
            started = time.monotonic()
            with _REQUEST_GATE.acquire(_provider_key(provider), checked):
                attempt["permit_wait_seconds"] = round(time.monotonic() - started, 3)
                adopted, failed = request_pdf_paper_completion(provider, payload, post=counted_post,
                    diagnostics=local_diagnostics, check_cancelled=checked, report_attempt=event,
                    completion_parser=parse, diagnostics_key="request", max_attempts=1)
            recheck()
            for group in batch:
                if group["id"] in adopted:
                    publish(group, adopted[group["id"]])
            with lock:
                for gid, reason in failed.items():
                    errors[gid] = reason
                    retryable[gid] = True
                    retry_delays[gid] = attempt.get("retry_delay_seconds", 0)
                    states[gid]["status"] = "failed"
                attempt["status"] = "partial" if failed else "completed"
                refresh()
        except (TaskCancelled, PdfParallelPersistenceError, *_HARD_ERRORS):
            raise
        except Exception as exc:
            recheck()
            cause = exc.__cause__ or exc
            can_retry = getattr(cause, "retryable", isinstance(exc, PDFVisionRequestError))
            with lock:
                attempt.update(status="failed", error_type=type(exc).__name__, retryable=can_retry)
                for group in batch:
                    gid = group["id"]
                    errors[gid] = type(exc).__name__
                    retryable[gid] = can_retry
                    retry_delays[gid] = attempt.get("retry_delay_seconds", 0)
                    states[gid]["status"] = "failed"
                refresh()

    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf-split")
    try:
        batches = [[group for group in batch if group["id"] not in accepted]
                   for batch in (groups[:boundary], groups[boundary:])]
        futures = {executor.submit(send, batch, "primary") for batch in batches if batch}
        while futures:
            checked()
            done, futures = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
        failed = [group for group in groups if group["id"] in errors and retryable.get(group["id"])]
        # One repair envelope shared by the entire paper; no per-job retry
        # loop, and no repair at all when the caller chooses the old quota 2.
        if failed and max_posts == 3 and report["calls"] < max_posts:
            delay = max((retry_delays.get(g["id"], 0) for g in failed), default=0)
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                checked()
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            try:
                send(failed, "repair")
            except SourceMetadataMessageBudgetError:
                report["repair_skipped"] = "message_budget_exceeded"
        recheck()
        original_order = {q["id"]: q["source_range"][0] for q in plan["_inspection"]["questions"]}
        rows = [(original_order[qid], question, group["route"] == "metadata")
                for group in groups for qid, question in zip(group["question_ids"], accepted.get(group["id"], []))]
        rows.sort(key=lambda row: row[0])
        report.update(status="partial" if errors else "used", question_count=len(rows))
        return PdfParallelResult([row[1] for row in rows],
            frozenset(i for i, row in enumerate(rows) if row[2]), bool(errors), True)
    except TaskCancelled:
        abort.set()
        report["status"] = "cancelled"
        raise
    except _HARD_ERRORS:
        abort.set()
        report["status"] = "source_changed"
        raise
    except PdfParallelPersistenceError:
        abort.set()
        report["status"] = "persistence_failed"
        raise
    finally:
        abort.set()
        executor.shutdown(wait=False, cancel_futures=True)
