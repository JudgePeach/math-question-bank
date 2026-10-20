"""Strict API PDF routing: fake full-body replies, never a local card fallback."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from mathbank import pdf_legacy_parse as module
from mathbank.ai_providers import resolve_text_provider
from mathbank.content_locks import lock_visible_math, reconcile_visible_math
from mathbank.document_project_parse import run_project_legacy_parse
from mathbank.document_projects import DocumentProjectStore
from mathbank.paper_parse import finalize_source_answers, parse_paper_completion
from mathbank.pdf_full_context_split import full_context_pdf_split_eligibility
from mathbank.prompts import build_pdf_parse_system_prompt


WORKFLOW = "cloud-api-full-body.v1"
CURRICULUM = {"必修一": {"3. 函数的概念与性质": []}}
PROVIDER = resolve_text_provider("SILICONFLOW/Qwen/test-model",
    {"SILICONFLOW_API_KEY": "fake-no-network"})
SOURCE = "1. OCR原始题干如图$x=1$。\n![](/static/uploads/tmp/source.png)\n【答案】$2$。"
MODEL_ROWS = [{"source_number": 1, "content": "API完整题干如图$x=1$。\n![](/static/uploads/tmp/source.png)",
    "answer_markdown": "[EXTRACTED_ORIGINAL]API原答案$2$。",
    "referenced_images": ["/static/uploads/tmp/source.png"], "question_type": "detailed_answer",
    "category_compulsory": "必修一", "category_chapter": "3. 函数的概念与性质", "difficulty": "normal"}]


def packet(rows):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"questions": rows}, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}


@pytest.fixture(autouse=True)
def no_local_card_routes_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Strict API mode must not call local assembly or metadata-only routing")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    for name in ("prepare_pdf_task_plan", "request_pdf_hybrid", "request_pdf_parallel_split"):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr("mathbank.pdf_fragment_draft.prepare_pdf_fragment_draft", forbidden)
    monkeypatch.setattr("mathbank.document_project_draft.execute_pdf_local_draft", forbidden)
    monkeypatch.setattr("mathbank.pdf_draft_metadata.request_pdf_local_draft_metadata", forbidden)


def parser(observed):
    def parse(source, *, request_post, requested_source_numbers=None, **options):
        payload = {"model": PROVIDER.model_name, "messages": [
            {"role": "system", "content": build_pdf_parse_system_prompt(CURRICULUM, False)},
            {"role": "user", "content": source}]}
        if requested_source_numbers is not None:
            payload["requested_source_numbers"] = list(requested_source_numbers)
        observed.append({"source": source, "options": options,
                         "numbers": deepcopy(requested_source_numbers)})
        response = request_post(PROVIDER, payload, timeout=600,
                                retry_connection=False, allow_redirects=False)
        return parse_paper_completion(response.json())
    return parse


def execute(source=SOURCE, *, post=None, parse=None, cache=None, diagnostics=None,
            parallel=False, finalize=None, layout=True):
    return module.execute_legacy_pdf_parse(source, source_namespace="cloud-fixture",
        curriculum=CURRICULUM, source_pages=[{"page_number": 1, "markdown": source}],
        layout_result={"pages": []} if layout else None, task_id="cloud-api-fixture",
        source_document_sha256="a"*64, diagnostics=diagnostics if diagnostics is not None else {},
        provider=PROVIDER, post=post or (lambda *a, **kw: SimpleNamespace(status_code=200,
            json=lambda: packet(deepcopy(MODEL_ROWS)))), parse_full=parse or parser([]),
        normalize_fillin=lambda value: value, cache=cache, parallel=parallel,
        finalize=finalize, full_body_only=True)


@pytest.mark.parametrize("parallel", [False, True])
def test_cloud_mode_adopts_complete_api_bodies_and_retains_original_source_checks(parallel):
    seen, sent, finalized = [], [], []
    diagnostics = {"pdf_hybrid": {"mode": "whole_metadata", "historical": True}}
    def post(provider, payload, **options):
        sent.append(deepcopy(payload))
        return SimpleNamespace(status_code=200, json=lambda: packet(deepcopy(MODEL_ROWS)))
    def finalizer(rows, preserved):
        finalized.append((deepcopy(rows), preserved))
        return rows
    value = execute(post=post, parse=parser(seen), diagnostics=diagnostics,
                    parallel=parallel, finalize=finalizer)
    expected = parse_paper_completion(packet(deepcopy(MODEL_ROWS)))
    locked, locks = lock_visible_math(SOURCE, "cloud-fixture")
    reconcile_visible_math(expected, locks, SOURCE)
    finalize_source_answers(expected, SOURCE)
    assert value["questions"] == expected
    assert value["questions"][0]["content"].startswith("API完整题干")
    assert value["questions"][0]["answer_markdown"].startswith("API原答案")
    assert value["questions"][0]["referenced_images"] == MODEL_ROWS[0]["referenced_images"]
    assert value["workflow"] == WORKFLOW and not value["preserved_indices"]
    assert finalized == [(expected, frozenset())]
    assert seen[0]["source"] == locked == sent[0]["messages"][1]["content"]
    assert "answer_markdown" in sent[0]["messages"][0]["content"]
    assert diagnostics["math_locks_created"] == len(locks) > 0
    assert "pdf_hybrid" not in diagnostics
    assert diagnostics["pdf_legacy_requests"] == {"new_calls": 1, "replayed_responses": 0}


def full_context_source():
    questions = "\n\n".join(f"{number}. 第{number}题完整条件。" + "原题完整说明。"*85
        for number in range(1, 20))
    return "一、单项选择题\n" + questions + "\n# 参考答案与解析\n" + "\n".join(
        f"{number}. 第{number}题原答案。" for number in range(1, 20))


def test_cloud_parallel_scopes_receive_entire_source_and_return_only_model_bodies():
    source, observed, calls = full_context_source(), [], []
    assert full_context_pdf_split_eligibility(source) is not None
    def post(provider, payload, **options):
        calls.append(deepcopy(payload))
        rows = [{"source_number": number, "content": f"API题干{number}",
                 "answer_markdown": f"[EXTRACTED_ORIGINAL]API原答案{number}"}
                for number in payload["requested_source_numbers"]]
        return SimpleNamespace(status_code=200, json=lambda: packet(rows))
    value = execute(source, post=post, parse=parser(observed), parallel=True)
    assert len(calls) == len(observed) == 2
    assert all(item["source"] == source for item in observed)
    assert sorted(number for item in observed for number in item["numbers"]) == list(range(1, 20))
    assert [row["content"] for row in value["questions"]] == [f"API题干{n}" for n in range(1, 20)]
    assert not value["preserved_indices"] and not value["partial"]
    assert value["diagnostics"]["pdf_parallel_split"]["mode"] == "full_context"


def test_warm_cloud_result_reuses_exact_cloud_cache_and_never_adopts_old_local_cards(tmp_path):
    raw, calls, observed = b"%PDF-cloud-cache-fixture", [], []
    source_sha = hashlib.sha256(raw).hexdigest()
    store = DocumentProjectStore(tmp_path/"projects")
    project_id = store.create(raw, "cloud.pdf", {}, CURRICULUM)["project_id"]
    pages = [{"page_number": 1, "markdown": SOURCE}]
    old_local = {"questions": [{"content": "旧本地组题内容"}], "partial": False,
                 "preserved_indices": frozenset({0}), "workflow": "page-fragments.v2"}
    def invoke(executor, version=WORKFLOW, **options):
        return run_project_legacy_parse(store, project_id, SOURCE, pages, CURRICULUM,
            source_document_sha256=source_sha, executor=executor, algorithm_version=version, **options)
    invoke(lambda cache: deepcopy(old_local), "first-pass-fragments.v2")
    def post(*args, **options):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: packet(deepcopy(MODEL_ROWS)))
    def executor(cache):
        return execute(post=post, parse=parser(observed), cache=cache)
    first = invoke(executor)
    report = {}
    second = invoke(executor, diagnostics=report)
    assert first == second and len(calls) == len(observed) == 1
    assert first["workflow"] == WORKFLOW
    assert first["questions"][0]["content"].startswith("API完整题干")
    assert report["pdf_project_legacy_parse"]["reused"] is True
    assert report["pdf_project_legacy_parse"]["new_calls"] == 0
    replay = invoke(executor, validation_version="cloud-validation.v2")
    assert replay["questions"] == first["questions"] and len(calls) == 1
    assert replay["diagnostics"]["pdf_legacy_requests"] == {"new_calls": 0, "replayed_responses": 1}
    assert invoke(lambda cache: pytest.fail("Original local archive should remain"),
                  "first-pass-fragments.v2") == old_local


@pytest.mark.parametrize("parallel", [False, True])
def test_failed_whole_api_never_falls_back_to_local_questions(parallel):
    calls = []
    def post(*args, **options):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: packet([]))
    with pytest.raises(ValueError, match="题目对象列表"):
        execute(post=post, parallel=parallel)
    assert calls == [1]


def test_all_parallel_api_scopes_failed_never_start_whole_or_local_fallback():
    calls, observed = [], []
    def post(provider, payload, **options):
        assert payload.get("requested_source_numbers"), "No whole-paper call after paid scopes"
        calls.append(deepcopy(payload["requested_source_numbers"]))
        return SimpleNamespace(status_code=200, json=lambda: packet([]))
    with pytest.raises(ValueError, match="两组完整上下文拆题"):
        execute(full_context_source(), post=post, parse=parser(observed), parallel=True)
    assert len(calls) == 3
    assert all(item["numbers"] for item in observed)
