"""Offline concurrency over real private transcript certificates; no model API."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import threading
from types import SimpleNamespace

import pytest

from mathbank.pdf_split_parallel import (
    PdfParallelPersistenceError, pdf_split_request_slot, request_pdf_parallel_split,
)
from mathbank.pdf_hybrid_plan import PdfHybridPlanError
from mathbank.pdf_hybrid_plan import build_pdf_snapshot_hybrid_plan
from mathbank.ai_providers import resolve_text_provider
from mathbank.source_hybrid_request import build_source_hybrid_messages, _adopt_parts
from mathbank.task_manager import TaskCancelled


_spec = importlib.util.spec_from_file_location("parallel_pdf_witness_fixture",
    Path(__file__).with_name("test_pdf_hybrid_safety.py"))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
prepared, Response = _fixtures.prepared, _fixtures.Response
CURRICULUM, PROVIDER = _fixtures.CURRICULUM, _fixtures.PROVIDER


def run(plan, post, **kwargs):
    diagnostics = kwargs.pop("diagnostics", {})
    value = request_pdf_parallel_split(plan, CURRICULUM, provider=kwargs.pop("provider", PROVIDER), post=post,
        diagnostics=diagnostics, task_id=kwargs.pop("task_id", "pdf-integrity-test"),
        generation=kwargs.pop("generation", 2),
        min_split_characters=0, full_source_fallback=lambda: pytest.fail("Paid whole fallback"), **kwargs)
    return value, diagnostics["pdf_parallel_split"]


def reply(plan, payload):
    return Response(_fixtures.response_for(plan, payload))


def test_same_routes_and_adoption_as_existing_full_envelope(tmp_path):
    plan, _, _ = prepared(tmp_path)
    messages, locks = build_source_hybrid_messages(plan, CURRICULUM, plan["groups"], source_label="PDF")
    before = deepcopy(plan["source"])
    packet = _fixtures.response_for(plan, {"messages": messages})
    adopted, errors = _adopt_parts(packet, plan["groups"], locks, CURRICULUM, lambda x: x,
                                  plan["_inspection"]["questions"], plan["source"])
    assert not errors
    calls = []
    def post(_provider, payload, **kwargs):
        calls.append((payload, kwargs))
        return reply(plan, payload)
    result, report = run(plan, post)
    expected = [q for group in plan["groups"] for q in adopted[group["id"]]]
    assert result.questions == expected
    assert result.preserved_indices == frozenset({0, 2})
    assert result.used_parallel and not result.partial
    assert report["calls"] == len(calls) == 2 and report["completed_groups"] == 3
    assert plan["source"] == before
    assert all(kw["retry_connection"] is False and kw["allow_redirects"] is False for _, kw in calls)
    packets = [json.loads(payload["messages"][1]["content"]) for payload, _ in calls]
    assert sum(len(p["metadata_items"]) for p in packets) == 2
    assert sum(len(p["split_groups"]) for p in packets) == 1
    assert all("content" not in row for row in packet["metadata"]["items"])


def test_real_concurrency_and_out_of_order_completion_still_merges_source_order(tmp_path):
    plan, _, _ = prepared(tmp_path)
    barrier = threading.Barrier(2)
    second_done = threading.Event()
    completion = []
    lock = threading.Lock()
    active = 0
    maximum = 0
    def post(_provider, payload, **kwargs):
        nonlocal active, maximum
        packet = json.loads(payload["messages"][1]["content"])
        first = bool(packet["metadata_items"] and packet["metadata_items"][0]["id"] ==
                     plan["groups"][0]["question_ids"][0])
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait(timeout=3)
        if first:
            assert second_done.wait(3)
        else:
            second_done.set()
        with lock:
            active -= 1
        return reply(plan, payload)
    def save(scope, result, *, input_binding):
        completion.extend(result["source_numbers"])
    result, report = run(plan, post, on_group_result=save)
    assert maximum == 2
    assert ["甲题" in result.questions[0]["content"], "乙题" in result.questions[1]["content"],
            "丙题" in result.questions[2]["content"]] == [True, True, True]
    assert sorted(completion) == [1, 2, 3]
    assert report["pending_group_ids"] == []


def test_failed_scope_alone_is_repaired_successes_saved_before_repair(tmp_path):
    plan, _, _ = prepared(tmp_path)
    calls, saved = [], []
    lock = threading.Lock()
    def post(_provider, payload, **kwargs):
        packet = json.loads(payload["messages"][1]["content"])
        with lock:
            calls.append(packet)
            number = len(calls)
        result = _fixtures.response_for(plan, payload)
        if number <= 2:
            result["splits"] = []
        else:
            assert sorted(n for row in saved for n in row) == [1, 3]
            assert packet["metadata_items"] == []
            assert [g["source_numbers"] for g in packet["split_groups"]] == [[2]]
        return Response(result)
    result, report = run(plan, post, on_group_result=lambda _, value, **kw: saved.append(value["source_numbers"]))
    assert len(result.questions) == 3 and not result.partial
    assert report["calls"] == 3 and [a["phase"] for a in report["attempts"]] == ["primary", "primary", "repair"]
    assert len(saved) == 3


def test_budget_two_does_not_repair_failed_scope(tmp_path):
    plan, _, _ = prepared(tmp_path)
    calls = []
    def post(_provider, payload, **kwargs):
        calls.append(True)
        result = _fixtures.response_for(plan, payload)
        result["splits"] = []
        return Response(result)
    result, report = run(plan, post, max_posts=2)
    assert result.partial and len(result.questions) == 2
    assert len(calls) == report["calls"] == 2
    assert report["failed_group_ids"] == [plan["groups"][1]["id"]]


@pytest.mark.parametrize("status", [401, 403, 413])
def test_permanent_error_has_no_retry_or_whole_fallback(tmp_path, status):
    plan, _, _ = prepared(tmp_path)
    def post(*args, **kwargs):
        response = Response({})
        response.status_code = status
        return response
    result, report = run(plan, post)
    assert result.partial and not result.questions
    assert report["calls"] == 2


def test_both_transient_failures_share_one_repair_not_one_per_job(tmp_path):
    plan, _, _ = prepared(tmp_path)
    calls = []
    lock = threading.Lock()
    def post(_provider, payload, **kwargs):
        with lock:
            calls.append(payload)
            number = len(calls)
        response = reply(plan, payload)
        if number <= 2:
            response.status_code = 408
        return response
    result, report = run(plan, post)
    assert len(calls) == report["calls"] == 3 and len(result.questions) == 3
    repaired = json.loads(calls[-1]["messages"][1]["content"])
    assert len(repaired["metadata_items"]) == 2 and len(repaired["split_groups"]) == 1


def test_invalid_complete_json_has_only_one_shared_repair(tmp_path):
    plan, _, _ = prepared(tmp_path)
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        return Response({"unknown_envelope": []})
    result, report = run(plan, post)
    assert result.partial and not result.questions
    assert report["calls"] == len(calls) == 3


def test_rate_limit_repair_honors_bounded_retry_after_without_extra_loop(tmp_path):
    plan, _, _ = prepared(tmp_path)
    calls = []
    lock = threading.Lock()
    def post(_p, payload, **kw):
        with lock:
            calls.append(True)
            number = len(calls)
        result = reply(plan, payload)
        if number <= 2:
            result.status_code = 429
            result.headers = {"Retry-After": "0"}
        return result
    result, report = run(plan, post)
    assert len(result.questions) == 3 and report["calls"] == 3
    assert [item["retry_delay_seconds"] for item in report["attempts"][:2]] == [0, 0]


def test_all_successes_reused_without_post_and_keep_local_review_fields(tmp_path):
    plan, _, _ = prepared(tmp_path)
    cache = {}
    def save(scope, result, *, input_binding):
        cache[(scope, input_binding)] = deepcopy(result)
    first, _ = run(plan, lambda p, payload, **kw: reply(plan, payload), on_group_result=save)
    for item in cache.values():
        for question in item["questions"]:
            question["local_audit"] = {"kept": True}
    second, report = run(plan, lambda *a, **kw: pytest.fail("Cached successful scope was resent"),
        load_group_result=lambda scope, **kw: cache.get((scope, kw["input_binding"])))
    assert report["calls"] == 0 and report["reused_groups"] == report["completed_groups"] == 3
    assert [q["content"] for q in second.questions] == [q["content"] for q in first.questions]
    assert all(q["local_audit"] == {"kept": True} for q in second.questions)
    assert second.preserved_indices == first.preserved_indices


def test_cached_partial_only_sends_failed_group(tmp_path):
    plan, _, _ = prepared(tmp_path)
    cache = {}
    def bad(_p, payload, **kw):
        result = _fixtures.response_for(plan, payload)
        result["splits"] = []
        return Response(result)
    first, _ = run(plan, bad, max_posts=2,
        on_group_result=lambda scope, result, **kw: cache.__setitem__((scope, kw["input_binding"]), result))
    assert first.partial
    sent = []
    def good(_p, payload, **kw):
        packet = json.loads(payload["messages"][1]["content"])
        sent.append(packet)
        assert not packet["metadata_items"]
        assert [g["source_numbers"] for g in packet["split_groups"]] == [[2]]
        return reply(plan, payload)
    second, report = run(plan, good,
        load_group_result=lambda scope, **kw: cache.get((scope, kw["input_binding"])))
    assert len(sent) == report["calls"] == 1
    assert report["reused_groups"] == 2 and len(second.questions) == 3


def test_restart_task_identity_does_not_invalidate_successful_scope_cache(tmp_path):
    plan, _, _ = prepared(tmp_path)
    cache = {}
    first, _ = run(plan, lambda p, payload, **kw: reply(plan, payload),
        on_group_result=lambda scope, result, **kw: cache.__setitem__((scope, kw["input_binding"]), result))
    renewed = build_pdf_snapshot_hybrid_plan(plan["source"], plan["_source_diagnostics"],
        source_pages=plan["_source_pages"], layout_result=plan["_layout_result"], task_id="restarted-task",
        generation=3, source_document_sha256=plan["document_sha256"],
        source_review_evidence=plan["_source_review_evidence"], min_metadata_source_characters=0)
    assert renewed["groups"][0]["id"] != plan["groups"][0]["id"]
    second, report = run(renewed, lambda *a, **kw: pytest.fail("Restart resent an accepted group"),
        task_id="restarted-task", generation=3,
        load_group_result=lambda scope, **kw: cache.get((scope, kw["input_binding"])))
    assert report["calls"] == 0 and report["reused_groups"] == 3
    assert first.questions == second.questions


def test_changed_cached_metadata_body_is_rejected_without_discarding_other_caches(tmp_path):
    plan, _, _ = prepared(tmp_path)
    cache = {}
    run(plan, lambda p, payload, **kw: reply(plan, payload),
        on_group_result=lambda scope, result, **kw: cache.__setitem__((scope, kw["input_binding"]), result))
    for row in cache.values():
        if row["source_numbers"] == [1]:
            row["questions"][0]["content"] = "changed cached source"
    sent = []
    def post(_p, payload, **kw):
        sent.append(json.loads(payload["messages"][1]["content"]))
        return reply(plan, payload)
    result, report = run(plan, post,
        load_group_result=lambda scope, **kw: cache.get((scope, kw["input_binding"])))
    assert report["calls"] == 1 and report["reused_groups"] == 2
    assert len(result.questions) == 3 and len(sent[0]["metadata_items"]) == 1


def test_changed_pixels_with_fresh_valid_proof_cannot_reuse_old_group_result(tmp_path):
    plan, images, _ = prepared(tmp_path)
    cache = {}
    run(plan, lambda p, payload, **kw: reply(plan, payload),
        on_group_result=lambda scope, result, **kw: cache.__setitem__((scope, kw["input_binding"]), result))
    _fixtures.raster(images[0], "blue")
    witnesses = {}
    for index, page in enumerate(plan["_source_pages"]):
        before = _fixtures.begin_pdf_transcription_witness(images[index], {"page_index": index, "width": 8, "height": 8})
        original = re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->\n", "", page["markdown"])
        witnesses[index] = _fixtures.finish_pdf_transcription_witness(before,
            {"markdown": original, "layout": {"page_complete": index != 1, "figures": []}})
    evidence = _fixtures.finalize_pdf_transcription_snapshot(plan["source"], plan["_source_diagnostics"],
        source_document_sha256=plan["document_sha256"], source_pages=plan["_source_pages"],
        layout_result=plan["_layout_result"], witnesses=witnesses, transcript_normalizer=lambda x: x, figure_assets={})
    renewed = build_pdf_snapshot_hybrid_plan(plan["source"], plan["_source_diagnostics"],
        source_pages=plan["_source_pages"], layout_result=plan["_layout_result"], task_id="restarted-task",
        generation=3, source_document_sha256=plan["document_sha256"],
        source_review_evidence=evidence, min_metadata_source_characters=0)
    result, report = run(renewed, lambda p, payload, **kw: reply(renewed, payload),
        task_id="restarted-task", generation=3,
        load_group_result=lambda scope, **kw: cache.get((scope, kw["input_binding"])))
    assert len(result.questions) == 3 and report["calls"] == 2 and report["reused_groups"] == 0


def test_cached_stage_read_failure_is_not_an_excuse_for_paid_resubmission(tmp_path):
    plan, _, _ = prepared(tmp_path)
    def load(*a, **kw):
        raise OSError("fake corrupt private file")
    with pytest.raises(OSError):
        run(plan, lambda *a, **kw: pytest.fail("Cache read failed, must not POST"), load_group_result=load)


def test_streaming_uses_same_total_post_budget_and_original_hybrid_json(tmp_path):
    plan, _, _ = prepared(tmp_path)
    provider = resolve_text_provider("SILICONFLOW/Qwen/Qwen3.8-27B", {"SILICONFLOW_API_KEY": "fake"})
    calls = []
    def post(_p, payload, **kw):
        calls.append(kw)
        assert payload["stream"] is True
        packet = _fixtures.response_for(plan, payload)
        events = [{"choices": [{"index": 0, "delta": {"content": json.dumps(packet, ensure_ascii=False)},
                   "finish_reason": "stop"}]},
                  {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}]
        raw = b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in events) + b"data: [DONE]\n\n"
        return SimpleNamespace(status_code=200, iter_content=lambda chunk_size: iter([raw]), close=lambda: None)
    result, report = run(plan, post, provider=provider)
    assert report["calls"] == len(calls) == 2 and len(result.questions) == 3
    assert all(kw["stream"] is True and kw["retry_connection"] is False for kw in calls)
    assert all(attempt["usage"]["total_tokens"] == 30 for attempt in report["attempts"])


def test_two_documents_share_global_nonstream_gate_not_four_parallel_posts(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    plans = [prepared(one)[0], prepared(two)[0]]
    rendezvous = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = []
    def work(plan):
        def post(_p, payload, **kw):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                calls.append(True)
            rendezvous.wait(timeout=3)
            result = reply(plan, payload)
            with lock:
                active -= 1
            return result
        return run(plan, post)[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [f.result(timeout=5) for f in [executor.submit(work, plan) for plan in plans]]
    assert maximum == 2 and len(calls) == 4 and all(len(r.questions) == 3 for r in results)


def test_persistence_failure_never_causes_repair(tmp_path):
    plan, _, _ = prepared(tmp_path)
    calls = []
    barrier = threading.Barrier(2)
    def post(_p, payload, **kw):
        calls.append(True)
        barrier.wait(timeout=3)
        return reply(plan, payload)
    diagnostics = {}
    def save(*a, **kw):
        raise OSError("fake disk full")
    with pytest.raises(PdfParallelPersistenceError):
        run(plan, post, on_group_result=save, diagnostics=diagnostics)
    assert len(calls) == 2
    assert diagnostics["pdf_parallel_split"]["status"] == "persistence_failed"


def test_source_change_after_post_rejects_every_late_result(tmp_path):
    plan, _, _ = prepared(tmp_path)
    barrier = threading.Barrier(2, action=lambda: plan.__setitem__("source", plan["source"] + "changed"))
    saved = []
    def post(_p, payload, **kw):
        response = reply(plan, payload)
        barrier.wait(timeout=3)
        return response
    with pytest.raises(PdfHybridPlanError):
        run(plan, post, on_group_result=lambda *a, **kw: saved.append(True))
    assert not saved


def test_cancelled_worker_cannot_publish_a_late_response(tmp_path):
    plan, _, _ = prepared(tmp_path)
    entered = threading.Barrier(3)
    release = threading.Event()
    cancelled = threading.Event()
    saved = []
    def post(_p, payload, **kw):
        entered.wait(timeout=3)
        assert release.wait(3)
        return reply(plan, payload)
    def check():
        if cancelled.is_set():
            raise TaskCancelled()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, plan, post, check_cancelled=check,
            on_group_result=lambda *a, **kw: saved.append(True))
        entered.wait(timeout=3)
        cancelled.set()
        with pytest.raises(TaskCancelled):
            future.result(timeout=2)
        release.set()
    assert not saved


def test_shared_noncontiguous_dependency_is_never_split_and_original_order_is_restored(tmp_path):
    plan, _, _ = prepared(tmp_path, risk=False, shared=True)
    assert [g["source_numbers"] for g in plan["groups"]] == [[1, 3], [2]]
    packets = []
    def post(_p, payload, **kw):
        packets.append(json.loads(payload["messages"][1]["content"]))
        return reply(plan, payload)
    result, report = run(plan, post)
    assert len(result.questions) == 3 and result.preserved_indices == frozenset({1})
    assert any([g["source_numbers"] for g in p["split_groups"]] == [[1, 3]] for p in packets)
    assert "甲题" in result.questions[0]["content"] and "乙题" in result.questions[1]["content"]


def test_whole_metadata_uses_unchanged_original_route_without_parallel_post(tmp_path):
    plan, _, _ = prepared(tmp_path, risk=False)
    expected = [{"content": "untouched full result"}]
    result = request_pdf_parallel_split(plan, CURRICULUM, provider=PROVIDER,
        post=lambda *a, **kw: pytest.fail("Unexpected POST"), diagnostics={},
        task_id="pdf-integrity-test", generation=2, single_legacy_fallback=lambda: expected)
    assert result.questions is expected and not result.used_parallel


def test_original_route_accepts_existing_cache_result_dict(tmp_path):
    plan, _, _ = prepared(tmp_path, risk=False)
    result = request_pdf_parallel_split(plan, CURRICULUM, provider=PROVIDER,
        post=lambda *a, **kw: pytest.fail("Unexpected POST"), diagnostics={},
        task_id="pdf-integrity-test", generation=2,
        single_legacy_fallback=lambda: {"questions": [{"content": "same"}], "preserved_indices": [0], "partial": True})
    assert result.questions == [{"content": "same"}] and result.preserved_indices == frozenset({0})
    assert result.partial and not result.used_parallel


def test_global_provider_gate_includes_nonstreaming_original_requests():
    entered = threading.Barrier(3)
    release = threading.Event()
    third_entered = threading.Event()
    def first():
        with pdf_split_request_slot(PROVIDER):
            entered.wait(timeout=3)
            assert release.wait(3)
    def third():
        with pdf_split_request_slot(PROVIDER):
            third_entered.set()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(first), executor.submit(first)]
        entered.wait(timeout=3)
        last = executor.submit(third)
        assert not third_entered.wait(0.1)
        release.set()
        for future in futures + [last]:
            future.result(timeout=3)
    assert third_entered.is_set()
