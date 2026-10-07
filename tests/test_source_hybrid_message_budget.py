"""Prompt limits fall back once; real source certificates and cancellation win."""
from copy import deepcopy

import pytest

from mathbank import source_hybrid_request, source_metadata
from mathbank.pdf_hybrid_request import PdfHybridMessageBudgetError, request_pdf_hybrid
from mathbank.source_metadata import SourceMetadataContractError, SourceMetadataMessageBudgetError
from mathbank.task_manager import TaskCancelled
from mathbank.word_hybrid_request import WordHybridMessageBudgetError, request_word_hybrid
from test_pdf_hybrid_safety import prepared, CURRICULUM as PDF_CURRICULUM, PROVIDER as PDF_PROVIDER
from test_word_hybrid_safety import native_plan, CURRICULUM as WORD_CURRICULUM, PROVIDER as WORD_PROVIDER


@pytest.fixture(params=["word", "pdf_hybrid", "pdf_whole_metadata"])
def request_case(request, tmp_path):
    if request.param == "word":
        plan = native_plan(tmp_path)
        adapter, curriculum, provider = request_word_hybrid, WORD_CURRICULUM, WORD_PROVIDER
    else:
        plan, _, _ = prepared(tmp_path, risk=request.param == "pdf_hybrid")
        adapter, curriculum, provider = request_pdf_hybrid, PDF_CURRICULUM, PDF_PROVIDER
    diagnostics, posts, fallbacks = {}, [], []
    old_questions = [{"content": "完整旧路径结果", "answer_markdown": ""}]

    def fallback():
        fallbacks.append(True)
        return deepcopy(old_questions)

    def run(*, check_cancelled=lambda: None, full_source_fallback=fallback):
        return adapter(plan, curriculum, provider=provider,
            post=lambda *_args, **_kwargs: posts.append(True) or pytest.fail("Hybrid POST forbidden"),
            diagnostics=diagnostics, normalize_fillin=lambda value: value,
            full_source_fallback=full_source_fallback, task_id=plan["task_id"],
            generation=plan["generation"], check_cancelled=check_cancelled)

    return plan, curriculum, diagnostics, posts, fallbacks, old_questions, run


@pytest.mark.parametrize("budget_layer", ["metadata_group", "shared_envelope"])
def test_prompt_budget_uses_zero_hybrid_posts_and_one_complete_old_path(
        request_case, monkeypatch, budget_layer):
    plan, curriculum, diagnostics, posts, fallbacks, old_questions, run = request_case
    budget_module = source_metadata if budget_layer == "metadata_group" else source_hybrid_request
    monkeypatch.setattr(budget_module, "MAX_METADATA_MESSAGE_CHARACTERS", 1)
    with pytest.raises(SourceMetadataMessageBudgetError) as caught:
        source_hybrid_request.build_source_hybrid_messages(plan, curriculum, plan["groups"])
    assert type(caught.value) is SourceMetadataMessageBudgetError
    assert type(caught.value) is not SourceMetadataContractError
    assert WordHybridMessageBudgetError is PdfHybridMessageBudgetError is type(caught.value)

    result = run()
    assert posts == [] and fallbacks == [True]
    assert result.questions == old_questions and not result.preserved_indices and not result.partial
    report = diagnostics.get("word_hybrid") or diagnostics["pdf_hybrid"]
    assert report["status"] == "full_source_fallback"
    assert report["first_error_type"] == "SourceMetadataMessageBudgetError"
    assert report["calls"] == 1 and report["attempts"] == []


def test_prompt_budget_does_not_retry_a_failed_old_path(request_case, monkeypatch):
    _, _, _, posts, fallbacks, _, run = request_case
    monkeypatch.setattr(source_hybrid_request, "MAX_METADATA_MESSAGE_CHARACTERS", 1)

    def failed_fallback():
        fallbacks.append(True)
        raise RuntimeError("Old path failed")

    with pytest.raises(RuntimeError, match="Old path failed"):
        run(full_source_fallback=failed_fallback)
    assert posts == [] and fallbacks == [True]


def test_cancellation_during_budget_preparation_prevents_any_old_path(request_case, monkeypatch):
    _, _, diagnostics, posts, fallbacks, _, run = request_case
    monkeypatch.setattr(source_hybrid_request, "MAX_METADATA_MESSAGE_CHARACTERS", 1)
    original = source_hybrid_request.build_source_hybrid_messages
    cancelled = False

    def prepare(*args, **kwargs):
        nonlocal cancelled
        try:
            return original(*args, **kwargs)
        except SourceMetadataMessageBudgetError:
            cancelled = True
            raise

    def check():
        if cancelled:
            raise TaskCancelled("Cancelled during complete prompt preparation")

    monkeypatch.setattr(source_hybrid_request, "build_source_hybrid_messages", prepare)
    with pytest.raises(TaskCancelled):
        run(check_cancelled=check)
    assert posts == fallbacks == []
    report = diagnostics.get("word_hybrid") or diagnostics["pdf_hybrid"]
    assert report["status"] == "cancelled" and report["calls"] == 0


def test_source_change_during_budget_preparation_still_hard_stops(request_case, monkeypatch):
    plan, _, diagnostics, posts, fallbacks, _, run = request_case
    monkeypatch.setattr(source_hybrid_request, "MAX_METADATA_MESSAGE_CHARACTERS", 1)
    original = source_hybrid_request.build_source_hybrid_messages

    def prepare(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except SourceMetadataMessageBudgetError:
            plan["source"] += "\nSource changed during preparation."
            raise

    monkeypatch.setattr(source_hybrid_request, "build_source_hybrid_messages", prepare)
    with pytest.raises(ValueError) as caught:
        run()
    assert not isinstance(caught.value, SourceMetadataMessageBudgetError)
    assert posts == fallbacks == []
    report = diagnostics.get("word_hybrid") or diagnostics["pdf_hybrid"]
    assert report["status"] == "source_changed" and report["calls"] == 0


def test_cancellation_at_the_last_fallback_boundary_keeps_zero_posts(request_case, monkeypatch):
    _, _, diagnostics, posts, fallbacks, _, run = request_case
    monkeypatch.setattr(source_hybrid_request, "MAX_METADATA_MESSAGE_CHARACTERS", 1)
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise TaskCancelled("Cancelled before old path invocation")

    with pytest.raises(TaskCancelled):
        run(check_cancelled=check)
    assert posts == fallbacks == []
    report = diagnostics.get("word_hybrid") or diagnostics["pdf_hybrid"]
    assert report["status"] == "cancelled" and report["calls"] == 0


def test_source_certificate_errors_remain_distinct_hard_stops(request_case, monkeypatch):
    _, _, diagnostics, posts, fallbacks, _, run = request_case

    def invalid_certificate(*_args, **_kwargs):
        raise SourceMetadataContractError("Source certificate invalid")

    monkeypatch.setattr(source_hybrid_request, "build_source_hybrid_messages", invalid_certificate)
    with pytest.raises(SourceMetadataContractError) as caught:
        run()
    assert type(caught.value) is SourceMetadataContractError
    assert posts == fallbacks == []
    report = diagnostics.get("word_hybrid") or diagnostics["pdf_hybrid"]
    assert report["status"] == "source_changed" and report["calls"] == 0
