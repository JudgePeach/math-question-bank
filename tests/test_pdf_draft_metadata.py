"""Metadata-only fake transport must never replace a saved local PDF body."""

from mathbank.ai_timeouts import DOCUMENT_AI_TIMEOUT_SECONDS
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
import requests

from mathbank.ai_providers import resolve_text_provider
from mathbank.pdf_draft_metadata import PdfDraftMetadataCache, build_pdf_draft_metadata_messages, request_pdf_local_draft_metadata
from mathbank.document_projects import DocumentProjectStore
from mathbank.task_manager import TaskCancelled


TREE = {"必修一": {"3. 函数": ["3.1 函数概念"]}}
NORMAL = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY": "fake-no-network"})
STREAM = resolve_text_provider("SILICONFLOW/Qwen/Qwen3.8-27B", {"SILICONFLOW_API_KEY": "fake-no-network"})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_kw: pytest.fail("No live model request"))
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)


def plan(count=2):
    questions = [{"content": f"  原题{n}：$x_{{{n}}}+1$。\n\n表格与原图 ![](/static/uploads/q{n}.png)\n",
        "answer_markdown": f"\t原版答案{n}，公式$\\dfrac{{1}}{{2}}$。\n",
        "source_number": n, "question_type": "", "difficulty": "normal", "category_compulsory": "",
        "category_chapter": "", "category_knowledge": "", "image_paths": [f"/static/uploads/q{n}.png"],
        "referenced_images": [f"/static/uploads/q{n}.png"], "source": "原卷",
        "source_review": {"required": True, "reasons": ["实际OCR符号需人工核对"], "original_verified": False},
        "local_audit": {"keep": [n, "unknown field"]}} for n in range(1, count + 1)]
    source = "\n".join(f"{q['source_number']}. {q['content']}\n{q['answer_markdown']}" for q in questions)
    sha = hashlib.sha256(source.encode()).hexdigest()
    items = [{"id": f"PLD_card_{n}", "source_number": q["source_number"], "content": q["content"],
        "answer_markdown": q["answer_markdown"], "question": deepcopy(q), "manual_reasons": [],
        "section_context": "完整共享条件：各题变量均为实数。", "stem_ranges": [[0, 1]],
        "answer_ranges": [[1, 2]], "raw_content": q["content"], "raw_answer": q["answer_markdown"],
        "removals": []} for n, q in enumerate(questions, 1)]
    return {"schema": "mathbank.pdf-local-draft.v1", "input_sha256": sha, "source_sha256": sha,
        "document_sha256": "a" * 64, "source": source, "ready": True, "partial": False,
        "questions": questions, "items": items,
        "source_matches": [{"question_index": n, "source_number": n + 1, "field": "content",
                            "source_start": n, "source_end": n + 1} for n in range(count)],
        "unassigned": [], "manual_reasons": []}


def mutable_provider():
    return SimpleNamespace(**vars(NORMAL), chat_completions_url=NORMAL.chat_completions_url)


def response_rows(payload):
    items = json.loads(payload["messages"][1]["content"])["items"]
    return {"items": [{"id": item["id"], "question_type": "detailed_answer", "difficulty": "normal",
        "category_compulsory": "必修一", "category_chapter": "函数", "category_knowledge": "3.1 函数概念"}
        for item in reversed(items)]}


class Response:
    headers = {}
    def __init__(self, value, *, status=200, finish="stop", done=True):
        self.value, self.status_code, self.finish, self.done = value, status, finish, done
    def json(self):
        return {"choices": [{"finish_reason": self.finish, "message": {"content": json.dumps(self.value, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
    def iter_content(self, **_kwargs):
        yield ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": json.dumps(self.value, ensure_ascii=False)},
            "finish_reason": None}]}, ensure_ascii=False) + "\n\n").encode()
        if self.finish:
            yield ("data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": self.finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}) + "\n\n").encode()
        if self.done:
            yield b"data: [DONE]\n\n"
    def close(self):
        pass


class Cache:
    def __init__(self):
        self.cards, self.raw, self.bindings = {}, {}, []
    @staticmethod
    def key(stage, binding):
        return stage, json.dumps(binding, sort_keys=True)
    def load_partial(self, stage, input_binding=None):
        return deepcopy(self.cards.get(self.key(stage, input_binding)))
    def save_partial(self, stage, value, input_binding=None):
        self.cards[self.key(stage, input_binding)] = deepcopy(value)
        return True
    def load_raw_response(self, stage, input_binding=None):
        return deepcopy(self.raw.get(self.key(stage, input_binding)))
    def on_response(self, stage, value, input_binding=None):
        self.bindings.append(deepcopy(input_binding))
        self.raw[self.key(stage, input_binding)] = deepcopy(value)
        return True


def run(post, *, draft=None, **options):
    return request_pdf_local_draft_metadata(draft or plan(), TREE, provider=options.pop("provider", NORMAL), post=post, **options)


def assert_bodies(result, draft):
    allowed = {"question_type", "difficulty", "category_compulsory", "category_chapter", "category_knowledge", "classification_review"}
    assert len(result["questions"]) == len(draft["questions"])
    for old, new in zip(draft["questions"], result["questions"]):
        assert {k: v for k, v in old.items() if k not in allowed} == {k: v for k, v in new.items() if k not in allowed}
        assert new["content"].encode() == old["content"].encode()
        assert new["answer_markdown"].encode() == old["answer_markdown"].encode()
    assert result["source_matches"] == draft["source_matches"]
    assert result["preserved_indices"] == frozenset(range(len(draft["questions"])))


def test_metadata_response_keeps_every_body_answer_image_and_review_byte_identical():
    draft = plan(19); original = deepcopy(draft); calls = []
    def post(config, payload, **options):
        calls.append((deepcopy(payload), options))
        data = json.loads(payload["messages"][1]["content"])
        assert data["input_sha256"] == draft["input_sha256"]
        assert all(set(item) == {"id", "key", "content", "section_context"} for item in data["items"])
        assert "原版答案" not in payload["messages"][1]["content"]
        assert "<mathbank-math" not in payload["messages"][1]["content"]
        return Response(response_rows(payload))
    result = run(post, draft=draft)
    assert len(calls) == 1 and all(options["timeout"] == DOCUMENT_AI_TIMEOUT_SECONDS for _payload, options in calls)
    assert calls[0][1]["retry_connection"] is False and calls[0][1]["allow_redirects"] is False
    assert all(payload["max_tokens"] <= 3000 for payload, _options in calls)
    assert all(q["category_chapter"] == "3. 函数" for q in result["questions"])
    assert not result["partial"] and draft == original
    assert_bodies(result, draft)


def test_only_bad_metadata_id_gets_one_repair_while_successes_are_kept():
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); value = response_rows(payload)
        if len(calls) == 1:
            next(row for row in value["items"] if row["id"] == "PLD_card_1")["content"] = "forbidden rewrite"
        return Response(value)
    result = run(post)
    assert len(calls) == 2
    assert [item["id"] for item in json.loads(calls[1]["messages"][1]["content"])["items"]] == ["PLD_card_1"]
    assert all(q["category_chapter"] == "3. 函数" for q in result["questions"])
    assert_bodies(result, plan())


@pytest.mark.parametrize("extra", ["content", "answer_markdown", "image_paths", "source_review", "source_number"])
def test_extra_body_or_identity_fields_are_rejected_and_never_replace_local_cards(extra):
    calls = []
    def post(config, payload, **options):
        calls.append(1); value = response_rows(payload)
        for row in value["items"]: row[extra] = "forbidden"
        return Response(value)
    result = run(post)
    assert len(calls) == 2 and not result["partial"]
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert result["diagnostics"]["pdf_draft_metadata"]["metadata_failed"]
    assert_bodies(result, plan())


def test_optional_bad_classification_labels_soft_clear_without_paid_repair():
    calls = []
    def post(config, payload, **options):
        calls.append(1); value = response_rows(payload)
        value["items"][0]["category_compulsory"] = ["invalid"]
        value["items"][1]["category_chapter"] = None
        return Response(value)
    result = run(post)
    assert len(calls) == 1 and not result["partial"]
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert result["diagnostics"]["pdf_draft_metadata"]["status"] == "classification_pending"
    assert_bodies(result, plan())


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_http_failure_returns_all_cards_and_does_not_retry(status):
    calls = []
    def post(*args, **kwargs): calls.append(1); return Response({}, status=status)
    result = run(post)
    assert len(calls) == 1 and not result["partial"]
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert_bodies(result, plan())


def test_transport_failure_and_bad_repair_share_two_physical_posts():
    calls = []
    def post(config, payload, **options):
        calls.append(1)
        if len(calls) == 1: raise requests.ReadTimeout("private provider text")
        value = response_rows(payload)
        for row in value["items"]: row["difficulty"] = []
        return Response(value)
    result = run(post)
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert len(calls) == report["new_calls"] == report["calls"] == 2
    assert "private provider text" not in json.dumps(result, ensure_ascii=False, default=list)
    assert_bodies(result, plan())


@pytest.mark.parametrize("finish,done", [("length", True), (None, True), ("stop", False)])
def test_stream_requires_complete_stop_and_done_and_keeps_all_local_cards(finish, done):
    calls = []
    def post(config, payload, **options):
        calls.append(1)
        assert payload["stream"] is True and payload["enable_thinking"] is False and options["stream"] is True
        return Response(response_rows(payload), finish=finish, done=done)
    result = run(post, provider=STREAM)
    assert len(calls) == 2 and all(q["classification_review"]["required"] for q in result["questions"])
    assert_bodies(result, plan())


def test_streaming_success_caches_the_actual_stream_and_thinking_payload_binding():
    cache = Cache(); calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(response_rows(payload))
    result = run(post, provider=STREAM, cache=cache)
    assert len(calls) == 1 and calls[0]["enable_thinking"] is False
    expected = hashlib.sha256(json.dumps(calls[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert cache.bindings[0]["payload_sha256"] == expected
    assert_bodies(result, plan())


def test_per_card_and_raw_packet_replay_issue_zero_http_and_preserve_full_result():
    cache = Cache(); calls = []
    def post(config, payload, **options): calls.append(1); return Response(response_rows(payload))
    first = run(post, cache=cache)
    forbidden = lambda *_a, **_kw: pytest.fail("Cache replay must never request a model")
    second = run(forbidden, cache=cache)
    assert second["questions"] == first["questions"]
    assert second["diagnostics"]["pdf_draft_metadata"]["reused_cards"] == ["PLD_card_1", "PLD_card_2"]
    cache.cards.clear()
    third = run(forbidden, cache=cache)
    assert third["questions"] == first["questions"] and len(calls) == 1
    assert third["diagnostics"]["pdf_draft_metadata"]["replayed_responses"] == 1
    assert third["diagnostics"]["pdf_draft_metadata"]["new_calls"] == 0


@pytest.mark.parametrize("change", ["answer", "source_review", "manual_reasons"])
def test_classification_input_cache_ignores_answer_and_review_only_changes(change):
    cache = Cache()
    first = plan()
    run(lambda _provider, payload, **_kw: Response(response_rows(payload)), draft=first, cache=cache)
    changed = deepcopy(first)
    if change == "answer":
        changed["questions"][0]["answer_markdown"] += "原文恢复的续答，……17分。"
        changed["items"][0]["answer_markdown"] = changed["questions"][0]["answer_markdown"]
        changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    elif change == "source_review":
        changed["questions"][0]["source_review"]["reasons"].append("fragment_answer_boundary_recovered")
        changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    else:
        changed["items"][0]["manual_reasons"].append("original_answer_unassigned")
    result = run(lambda *_a, **_kw: pytest.fail("Unchanged classification text must reuse labels"), draft=changed, cache=cache)
    assert result["diagnostics"]["pdf_draft_metadata"]["reused_cards"] == ["PLD_card_1", "PLD_card_2"]
    assert_bodies(result, changed)


def seed_legacy_card_cache(cache, draft):
    from mathbank.pdf_draft_metadata import _legacy_card_binding, _profile, SCHEMA
    payload = {"messages": build_pdf_draft_metadata_messages(draft, TREE, compact=True)}
    rows = {row["id"]: row for row in response_rows(payload)["items"]}
    for item in draft["items"]:
        binding = _legacy_card_binding(item, draft, TREE, _profile(NORMAL))
        stage = "pdf-draft-metadata-card-" + hashlib.sha256(item["id"].encode()).hexdigest()[:24]
        cache.save_partial(stage, {"schema": SCHEMA, "binding": binding, "row": rows[item["id"]]}, input_binding=binding)


def test_identical_old_card_binding_migrates_without_relaxing_raw_identity():
    cache = Cache(); draft = plan()
    seed_legacy_card_cache(cache, draft)
    result = run(lambda *_a, **_kw: pytest.fail("Exact legacy card needs no POST"), draft=draft, cache=cache)
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert report["migrated_cards"] == ["PLD_card_1", "PLD_card_2"]
    assert all(row["mode"] == "legacy_exact_card" and row["row_revalidated"] for row in report["card_migrations"])
    assert all(row["new_binding"]["card_input_version"] == "classification-input.v2" for row in report["card_migrations"])
    assert not cache.raw
    assert_bodies(result, draft)


def test_checked_old_preview_can_migrate_answer_only_card_with_durable_proof(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-legacy-metadata-local", "legacy.pdf", {}, TREE)
    draft = plan(); draft["document_sha256"] = project["source_sha256"]
    signature = hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()
    store.save_stage(project["project_id"], "local-preview-" + signature[:32], draft, signature)
    seed_legacy_card_cache(PdfDraftMetadataCache(store, project["project_id"]), draft)
    changed = deepcopy(draft)
    changed["questions"][0]["answer_markdown"] += "首次原稿中恢复的原答尾部。"
    changed["questions"][0]["source_review"]["reasons"].append("answer_edge_pending")
    changed["items"][0]["answer_markdown"] = changed["questions"][0]["answer_markdown"]
    changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    result = run(lambda *_a, **_kw: pytest.fail("Verified same-input legacy migration needs no POST"), draft=changed,
                 cache=PdfDraftMetadataCache(store, project["project_id"]))
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert report["new_calls"] == 0 and report["replayed_responses"] == 0
    proof = next(row for row in report["card_migrations"] if row["id"] == "PLD_card_1")
    assert proof["mode"] == "checked_local_preview" and proof["preview_fingerprint"] == signature
    with store._locked(project["project_id"]) as directory:
        assert any(name.startswith("pdf-draft-metadata-card-migration-") for name in store._manifest(directory)["stages"])
    assert_bodies(result, changed)


def test_answer_changed_legacy_labels_without_a_checked_preview_are_not_migrated():
    cache = Cache(); draft = plan(1)
    seed_legacy_card_cache(cache, draft)
    changed = deepcopy(draft)
    changed["questions"][0]["answer_markdown"] += "恢复续答。"
    changed["items"][0]["answer_markdown"] = changed["questions"][0]["answer_markdown"]
    changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    calls = []
    def post(_provider, payload, **_kw): calls.append(1); return Response(response_rows(payload))
    result = run(post, draft=changed, cache=cache)
    assert calls == [1] and not result["diagnostics"]["pdf_draft_metadata"]["migrated_cards"]
    assert_bodies(result, changed)


def test_corrupt_historical_preview_cannot_supply_migration_evidence(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-corrupt-legacy-boundaries", "legacy.pdf", {}, TREE)
    draft = plan(1); draft["document_sha256"] = project["source_sha256"]
    signature = hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()
    name = "local-preview-" + signature[:32]
    store.save_stage(project["project_id"], name, draft, signature)
    seed_legacy_card_cache(PdfDraftMetadataCache(store, project["project_id"]), draft)
    with store._locked(project["project_id"]) as directory:
        receipt = store._manifest(directory)["stages"][name]
        path = directory / receipt["file"]
        path.write_bytes(path.read_bytes() + b" ")
    changed = deepcopy(draft)
    changed["questions"][0]["answer_markdown"] += "恢复续答。"
    changed["items"][0]["answer_markdown"] = changed["questions"][0]["answer_markdown"]
    changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    provider = mutable_provider(); provider.api_key = ""
    result = run(lambda *_a, **_kw: pytest.fail("No POST during corrupt-cache fixture"), draft=changed,
                 provider=provider, cache=PdfDraftMetadataCache(store, project["project_id"]))
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert not report["reused_cards"] and not report["migrated_cards"] and report["cache_errors"]
    assert_bodies(result, changed)


@pytest.mark.parametrize("change", ["content", "context", "curriculum", "provider", "document"])
def test_old_preview_migration_never_crosses_classification_inputs_or_provider_identity(tmp_path, change):
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-legacy-boundaries", "legacy.pdf", {}, TREE)
    draft = plan(1); draft["document_sha256"] = project["source_sha256"]
    signature = hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()
    store.save_stage(project["project_id"], "local-preview-" + signature[:32], draft, signature)
    seed_legacy_card_cache(PdfDraftMetadataCache(store, project["project_id"]), draft)
    changed, tree, provider = deepcopy(draft), deepcopy(TREE), NORMAL
    if change == "content":
        changed["questions"][0]["content"] += "修改关键条件。"
        changed["items"][0]["content"] = changed["questions"][0]["content"]
        changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    elif change == "context": changed["items"][0]["section_context"] += "更正共享条件。"
    elif change == "curriculum": tree["必修一"]["4. 新章节"] = []
    elif change == "provider":
        provider = mutable_provider(); provider.model_name = "different-model"
    else: changed["document_sha256"] = "b" * 64
    calls = []
    def post(_provider, payload, **_kw): calls.append(1); return Response(response_rows(payload))
    result = request_pdf_local_draft_metadata(changed, tree, provider=provider, post=post,
                 cache=PdfDraftMetadataCache(store, project["project_id"]))
    assert calls == [1] and not result["diagnostics"]["pdf_draft_metadata"]["migrated_cards"]
    assert_bodies(result, changed)


def test_fragment_answer_doubt_does_not_block_classification_of_proved_stems():
    draft = plan(2); draft["workflow"] = "page-fragments.v2"
    for item in draft["items"]:
        item["manual_reasons"] = ["original_answer_unassigned"]
        item["classification_blocked_reasons"] = []
    draft["items"][1]["classification_blocked_reasons"] = ["fragment_continuation_unclosed"]
    calls = []
    def post(_provider, payload, **_kw): calls.append(payload); return Response(response_rows(payload))
    result = run(post, draft=draft)
    assert len(calls) == 1
    assert [row["id"] for row in json.loads(calls[0]["messages"][1]["content"])["items"]] == ["PLD_card_1"]
    assert result["diagnostics"]["pdf_draft_metadata"]["unclassified_ids"] == ["PLD_card_2"]
    assert_bodies(result, draft)


def test_old_packet_replay_after_answer_only_change_keeps_actual_payload_exact():
    cache = Cache(); draft = plan()
    run(lambda _provider, payload, **_kw: Response(response_rows(payload)), draft=draft, cache=cache)
    bindings = deepcopy(cache.bindings); cache.cards.clear()
    changed = deepcopy(draft)
    changed["questions"][0]["answer_markdown"] += "首次原稿原答尾。"
    changed["items"][0]["answer_markdown"] = changed["questions"][0]["answer_markdown"]
    changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    result = run(lambda *_a, **_kw: pytest.fail("Exact actual-payload raw replay must be free"), draft=changed, cache=cache)
    assert cache.bindings == bindings and result["diagnostics"]["pdf_draft_metadata"]["replayed_responses"] == 1
    assert_bodies(result, changed)


def test_editing_one_local_card_relabels_only_that_id_and_shared_context_is_bound():
    cache = Cache(); calls = []
    def post(config, payload, **options): calls.append(deepcopy(payload)); return Response(response_rows(payload))
    run(post, cache=cache)
    changed = plan(); changed["source"] += "\n教师校对原稿"
    changed["input_sha256"] = changed["source_sha256"] = hashlib.sha256(changed["source"].encode()).hexdigest()
    changed["questions"][0]["content"] += "教师修正一个条件。"
    changed["items"][0]["question"] = deepcopy(changed["questions"][0])
    changed["items"][0]["content"] = changed["questions"][0]["content"]
    run(post, draft=changed, cache=cache)
    assert [item["id"] for item in json.loads(calls[-1]["messages"][1]["content"])["items"]] == ["PLD_card_1"]
    changed["items"][1]["section_context"] += "共用范围已改变。"
    run(post, draft=changed, cache=cache)
    assert [item["id"] for item in json.loads(calls[-1]["messages"][1]["content"])["items"]] == ["PLD_card_2"]


@pytest.mark.parametrize("mutation", ["document", "curriculum", "provider", "tampered_receipt"])
def test_stale_metadata_cache_never_crosses_bound_identity(mutation):
    cache = Cache(); calls = []
    def post(config, payload, **options): calls.append(1); return Response(response_rows(payload))
    run(post, cache=cache)
    draft = plan(); tree = deepcopy(TREE); provider = NORMAL
    if mutation == "document": draft["document_sha256"] = "b" * 64
    elif mutation == "curriculum": tree["必修一"]["4. 新章节"] = []
    elif mutation == "provider":
        provider = mutable_provider(); provider.model_name = "different-model"
    else:
        for packet in cache.cards.values(): packet["binding"]["card_sha256"] = "0" * 64
        cache.raw.clear()
    result = request_pdf_local_draft_metadata(draft, tree, provider=provider, post=post, cache=cache)
    assert len(calls) == 2 and not result["diagnostics"]["pdf_draft_metadata"]["reused_cards"]
    assert_bodies(result, draft)


def test_manual_structural_scope_never_calls_a_model_and_remains_local():
    draft = plan()
    for item in draft["items"]: item["manual_reasons"] = ["ambiguous_original_boundary"]
    draft["partial"] = True
    result = run(lambda *_a, **_kw: pytest.fail("Ambiguous body must stay local"), draft=draft)
    assert result["partial"] and all(q["classification_review"]["required"] for q in result["questions"])
    assert_bodies(result, draft)


def test_source_hash_mismatch_does_not_send_or_adopt_any_metadata():
    draft = plan(); draft["input_sha256"] = "0" * 64
    result = run(lambda *_a, **_kw: pytest.fail("No POST for stale local source"), draft=draft)
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert_bodies(result, draft)


def test_local_draft_changes_during_request_reject_late_labels_without_losing_original_cards():
    draft = plan(); frozen = deepcopy(draft); calls = []
    def post(config, payload, **options):
        calls.append(1); draft["questions"][0]["content"] += "新人工编辑"
        return Response(response_rows(payload))
    result = run(post, draft=draft)
    assert len(calls) == 1 and all(q["classification_review"]["required"] for q in result["questions"])
    assert_bodies(result, frozen)


def test_cancellation_propagates_and_never_retries_or_clears_local_cards():
    stopped = [False]; calls = []
    def check():
        if stopped[0]: raise TaskCancelled("cancel")
    def post(*args, **options): calls.append(1); stopped[0] = True; return Response({}, status=503)
    with pytest.raises(TaskCancelled): run(post, check_cancelled=check)
    assert len(calls) == 1


def test_cache_write_failure_cannot_cause_an_extra_paid_post():
    class Broken(Cache):
        def save_partial(self, *a, **kw): raise OSError("private local path")
        def on_response(self, *a, **kw): raise OSError("private local path")
    calls = []
    def post(config, payload, **options): calls.append(1); return Response(response_rows(payload))
    result = run(post, cache=Broken())
    assert len(calls) == 1 and all(q["category_chapter"] == "3. 函数" for q in result["questions"])
    assert "private local path" not in json.dumps(result, ensure_ascii=False, default=list)
    assert {row["kind"] for row in result["diagnostics"]["pdf_draft_metadata"]["cache_errors"]} == {"raw_save", "card_save"}


def test_configured_provider_and_catalogue_are_frozen_for_both_metadata_attempts():
    provider = mutable_provider(); tree = deepcopy(TREE); calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); value = response_rows(payload)
        if len(calls) == 1:
            provider.model_name = "edited-global-model"; tree.clear()
            value["items"][0]["difficulty"] = "invalid"
        return Response(value)
    result = request_pdf_local_draft_metadata(plan(), tree, provider=provider, post=post)
    assert len(calls) == 2 and all(payload["model"] == NORMAL.model_name for payload in calls)
    assert all(q["category_chapter"] == "3. 函数" for q in result["questions"])


def test_shared_global_provider_gate_allows_at_most_two_concurrent_metadata_requests():
    mutex = threading.Lock(); released = threading.Event(); active = maximum = 0
    def post(config, payload, **options):
        nonlocal active, maximum
        with mutex:
            active += 1; maximum = max(maximum, active)
            if active == 2: released.set()
        assert released.wait(3)
        try: return Response(response_rows(payload))
        finally:
            with mutex: active -= 1
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: run(post), range(3)))
    assert maximum == 2 and all(len(result["questions"]) == 2 for result in results)


def test_message_builder_never_includes_unassigned_body_or_answer_context():
    draft = plan(); draft["unassigned"] = [{"text": "未能可靠分题的长正文"}]
    messages = build_pdf_draft_metadata_messages(draft, TREE, requested_ids=["PLD_card_1"])
    data = json.loads(messages[1]["content"])
    assert [item["id"] for item in data["items"]] == ["PLD_card_1"]
    assert "未能可靠分题的长正文" not in messages[1]["content"] and "原版答案" not in messages[1]["content"]


@pytest.mark.parametrize("mode", ["truncated_json", "duplicate_json_keys", "unknown_id"])
def test_whole_packet_identity_and_json_boundaries_never_adopt_a_complete_prefix(mode):
    cache = Cache(); calls = []
    class Bad(Response):
        def json(self):
            body = super().json()
            if mode == "truncated_json":
                body["choices"][0]["message"]["content"] = body["choices"][0]["message"]["content"][:-1]
            elif mode == "duplicate_json_keys":
                body["choices"][0]["message"]["content"] = '{"items":[],"items":' + json.dumps(self.value["items"]) + '}'
            return body
    def post(config, payload, **options):
        calls.append(1); value = response_rows(payload)
        if mode == "unknown_id": value["items"][0]["id"] = "unknown-local-card"
        return Bad(value)
    result = run(post, cache=cache)
    assert len(calls) == 2 and all(q["classification_review"]["required"] for q in result["questions"])
    if mode != "unknown_id": assert not cache.raw
    assert_bodies(result, plan())


def test_incomplete_stream_cache_is_rejected_before_any_local_metadata_adoption():
    cache = Cache(); calls = []
    def post(config, payload, **options): calls.append(1); return Response(response_rows(payload))
    first = run(post, cache=cache, provider=STREAM)
    cache.cards.clear()
    for body in cache.raw.values(): body["_stream_complete"] = False
    second = run(post, cache=cache, provider=STREAM)
    assert len(calls) == 2 and second["questions"] == first["questions"]
    assert second["diagnostics"]["pdf_draft_metadata"]["replayed_responses"] == 0


def test_question_level_shared_context_is_sent_and_its_change_relabels_only_its_card():
    draft = plan(); cache = Cache(); calls = []
    for index, q in enumerate(draft["questions"]):
        q["shared_context"] = "独立共同条件$k>0$。"
        draft["items"][index]["question"] = deepcopy(q)
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(response_rows(payload))
    run(post, draft=draft, cache=cache)
    assert all(item["shared_context"] == "独立共同条件$k>0$。" for item in json.loads(calls[0]["messages"][1]["content"])["items"])
    draft["questions"][1]["shared_context"] = "共同条件已改成$k<0$。"
    draft["items"][1]["question"] = deepcopy(draft["questions"][1])
    run(post, draft=draft, cache=cache)
    assert [item["id"] for item in json.loads(calls[-1]["messages"][1]["content"])["items"]] == ["PLD_card_2"]


def test_unconfigured_metadata_service_keeps_all_local_cards_without_post():
    provider = mutable_provider(); provider.api_key = ""
    result = run(lambda *_a, **_kw: pytest.fail("Unconfigured optional annotation must stay local"), provider=provider)
    assert result["diagnostics"]["pdf_draft_metadata"]["new_calls"] == 0
    assert all(q["classification_review"]["required"] for q in result["questions"])
    assert_bodies(result, plan())


def test_real_project_stage_cache_survives_restart_revision_save_and_other_card_edits(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-local-only-fixture", "local.pdf", {}, TREE)
    pid = project["project_id"]; draft = plan(); draft["document_sha256"] = project["source_sha256"]
    prepared = {"page_texts": [draft["source"]], "original": "must remain immutable"}
    store.save_stage(pid, "prepared-control", prepared, "prepared-control-sha", complete=True)
    cache = PdfDraftMetadataCache(store, pid, project["source_sha256"])
    calls = []
    def post(config, payload, **options): calls.append(deepcopy(payload)); return Response(response_rows(payload))
    first = run(post, draft=draft, cache=cache)
    # Saving a teacher's card draft changes the revision, not a metadata input.
    store.save_draft(pid, {"questions": deepcopy(first["questions"]), "paperTitle": "教师稿"}, 0)
    reopened = DocumentProjectStore(tmp_path / "projects")
    second = run(lambda *_a, **_kw: pytest.fail("Persisted metadata must survive a process restart"), draft=draft,
                 cache=PdfDraftMetadataCache(reopened, pid, project["source_sha256"]))
    assert second["questions"] == first["questions"]
    changed = deepcopy(draft); changed["source"] += "\n本地逐页改稿"
    changed["input_sha256"] = changed["source_sha256"] = hashlib.sha256(changed["source"].encode()).hexdigest()
    changed["questions"][0]["content"] += "修正一个条件。"
    changed["items"][0]["question"] = deepcopy(changed["questions"][0]); changed["items"][0]["content"] = changed["questions"][0]["content"]
    third = run(post, draft=changed, cache=PdfDraftMetadataCache(reopened, pid))
    assert len(calls) == 2
    assert [item["id"] for item in json.loads(calls[-1]["messages"][1]["content"])["items"]] == ["PLD_card_1"]
    assert third["questions"][1] == second["questions"][1]
    assert reopened.load_stage(pid, "prepared-control", "prepared-control-sha") == prepared
    assert (reopened.root / pid / "source.bin").read_bytes() == b"%PDF-local-only-fixture"


def test_real_project_raw_response_history_retains_each_complete_packet_and_exact_binding(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-local-only-fixture", "local.pdf", {}, TREE)
    cache = PdfDraftMetadataCache(store, project["project_id"])
    first = Response({"items": []}).json(); second = Response({"items": [{"id": "unchanged-raw"}]}).json()
    binding = {"payload_sha256": "request-one", "model": "frozen-model"}
    assert cache.on_response("pdf-draft-metadata", first, input_binding=binding)
    assert cache.on_response("pdf-draft-metadata", second, input_binding=binding)
    assert cache.load_raw_response("pdf-draft-metadata", input_binding=binding) == second
    assert cache.load_raw_response("pdf-draft-metadata", input_binding={**binding, "payload_sha256": "request-two"}) is None
    manifest = json.loads((store.root / project["project_id"] / "manifest.json").read_text())
    histories = [name for name in manifest["stages"] if name.startswith("pdf-draft-metadata-raw-")
                 and not name.startswith("pdf-draft-metadata-raw-index-")]
    assert len(histories) == 2
    with pytest.raises(ValueError):
        cache.on_response("pdf-draft-metadata", {**first, "_stream_complete": False}, input_binding=binding)
    with pytest.raises(ValueError):
        cache.on_response("pdf-draft-metadata", Response({}, finish="length").json(), input_binding=binding)


def compact_response_rows(payload, *, path=2):
    from mathbank.curriculums import DEFAULT_DIFFICULTIES, DEFAULT_QUESTION_TYPES
    type_code = sorted(row["value"] for row in DEFAULT_QUESTION_TYPES).index("detailed_answer")
    difficulty_code = sorted(row["value"] for row in DEFAULT_DIFFICULTIES).index("normal")
    items = json.loads(payload["messages"][1]["content"])["items"]
    return {"items": [[item["key"], type_code, difficulty_code, path] for item in reversed(items)]}


def test_compact_integer_protocol_restores_exact_real_paths_without_sending_hashes_or_answers():
    draft = plan(19); calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload))
        data = json.loads(payload["messages"][1]["content"])
        assert all("card_sha256" not in row and type(row["key"]) is int for row in data["items"])
        short, verbose = compact_response_rows(payload), response_rows(payload)
        assert len(json.dumps(short)) < len(json.dumps(verbose)) / 4
        return Response(short)
    result = run(post, draft=draft)
    assert len(calls) == 1
    assert all(q["category_chapter"] == "3. 函数" and q["category_knowledge"] == "3.1 函数概念"
               for q in result["questions"])
    assert result["diagnostics"]["pdf_draft_metadata"]["classified_count"] == 19
    assert_bodies(result, draft)


@pytest.mark.parametrize("bad", [True, -1, 1000, "2", 2.0])
def test_invalid_compact_path_codes_never_guess_labels_or_modify_local_bodies(bad):
    calls = []
    def post(config, payload, **options):
        calls.append(1)
        return Response(compact_response_rows(payload, path=bad))
    result = run(post)
    assert len(calls) == 2
    assert result["diagnostics"]["pdf_draft_metadata"]["unclassified_ids"] == ["PLD_card_1", "PLD_card_2"]
    assert_bodies(result, plan())


@pytest.mark.parametrize("mode", ["unknown_key", "duplicate_key", "extra_value", "mixed_formats"])
def test_compact_alias_identity_and_bounded_metadata_columns_are_enforced(mode):
    calls = []
    def post(config, payload, **options):
        calls.append(1); value = compact_response_rows(payload)
        if mode == "unknown_key": value["items"][0][0] = 999
        elif mode == "duplicate_key": value["items"].append(deepcopy(value["items"][0]))
        elif mode == "extra_value":
            for row in value["items"]: row.extend([[], "模型补写正文"])
        else: value["items"].append(response_rows(payload)["items"][0])
        return Response(value)
    result = run(post)
    assert len(calls) == 2 and result["diagnostics"]["pdf_draft_metadata"]["metadata_failed"]
    assert_bodies(result, plan())


def test_compact_zero_path_is_an_explicit_manual_classification_not_a_paid_repair():
    calls = []
    def post(config, payload, **options):
        calls.append(1); return Response(compact_response_rows(payload, path=0))
    result = run(post)
    assert len(calls) == 1 and all(q["classification_review"]["required"] for q in result["questions"])
    assert all(q["category_compulsory"] == q["category_chapter"] == q["category_knowledge"] == ""
               for q in result["questions"])
    assert_bodies(result, plan())


def test_nineteen_card_batches_overlap_and_balanced_whole_cards_keep_source_order():
    entered = threading.Barrier(2); mutex = threading.Lock(); calls = []; active = maximum = 0
    def post(config, payload, **options):
        nonlocal active, maximum
        with mutex:
            calls.append(deepcopy(payload)); active += 1; maximum = max(maximum, active)
        try:
            entered.wait(timeout=3)
            return Response(compact_response_rows(payload))
        finally:
            with mutex: active -= 1
    result = run(post, draft=plan(19), parallelism=2)
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert maximum == 2 and len(calls) == 2
    sizes = [len(json.loads(payload["messages"][1]["content"])["items"]) for payload in calls]
    assert min(sizes) >= 6 and max(sizes) - min(sizes) <= 1
    assert [q["source_number"] for q in result["questions"]] == list(range(1, 20))
    assert report["concurrency"] == report["group_count"] == 2 and report["max_posts"] == 3
    assert_bodies(result, plan(19))


def test_two_initial_batch_failures_share_only_one_repair_for_failed_ids():
    calls = []; mutex = threading.Lock()
    def post(config, payload, **options):
        data = json.loads(payload["messages"][1]["content"])
        with mutex: calls.append(deepcopy(payload))
        value = compact_response_rows(payload)
        # One bad row in each initial envelope. Both are repaired together.
        if len(data["items"]) >= 6:
            target = min(row[0] for row in value["items"])
            next(row for row in value["items"] if row[0] == target)[3] = 999
        return Response(value)
    result = run(post, draft=plan(19), parallelism=2)
    assert len(calls) == 3
    assert len(json.loads(calls[-1]["messages"][1]["content"])["items"]) == 2
    assert result["diagnostics"]["pdf_draft_metadata"]["classified_count"] == 19
    assert_bodies(result, plan(19))


def test_successful_batch_is_cached_before_failed_batch_retry_and_resume_only_requests_failed_cards():
    cache = Cache(); calls = []; mutex = threading.Lock()
    def post(config, payload, **options):
        ids = [row["id"] for row in json.loads(payload["messages"][1]["content"])["items"]]
        with mutex: calls.append(ids)
        if "PLD_card_1" in ids: return Response({}, status=503)
        return Response(compact_response_rows(payload))
    result = run(post, draft=plan(19), cache=cache, parallelism=2)
    assert len(calls) == 3
    failed = set(result["diagnostics"]["pdf_draft_metadata"]["unclassified_ids"])
    assert 6 <= len(failed) <= 13 and cache.cards
    def success(config, payload, **options):
        ids = [row["id"] for row in json.loads(payload["messages"][1]["content"])["items"]]
        assert set(ids) == failed
        calls.append(ids)
        return Response(compact_response_rows(payload))
    resumed = run(success, draft=plan(19), cache=cache, parallelism=2)
    assert len(calls) == 4 and resumed["diagnostics"]["pdf_draft_metadata"]["classified_count"] == 19
    assert_bodies(resumed, plan(19))


def test_single_lane_keyword_retains_compact_output_with_only_one_initial_envelope():
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(compact_response_rows(payload))
    result = run(post, draft=plan(19), parallelism=1)
    assert len(calls) == 1
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert report["concurrency"] == report["group_count"] == 1 and report["max_posts"] == 2
    assert_bodies(result, plan(19))


def test_cancelled_parallel_collector_does_not_wait_for_blocked_transport_or_save_late_packets():
    entered = threading.Barrier(3); release = threading.Event(); cancelled = threading.Event(); cache = Cache()
    def check():
        if cancelled.is_set(): raise TaskCancelled("cancelled fixture")
    def post(config, payload, **options):
        entered.wait(timeout=3)
        assert release.wait(3)
        return Response(compact_response_rows(payload))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: run(post, draft=plan(19), cache=cache, check_cancelled=check, parallelism=2))
        try:
            entered.wait(timeout=3); cancelled.set()
            with pytest.raises(TaskCancelled): future.result(timeout=1)
        finally:
            release.set()
    assert not cache.cards and not cache.raw


def test_parallel_jobs_continue_to_share_global_two_request_gate():
    mutex = threading.Lock(); release = threading.Event(); active = maximum = 0
    def post(config, payload, **options):
        nonlocal active, maximum
        with mutex:
            active += 1; maximum = max(maximum, active)
            if active == 2: release.set()
        try:
            assert release.wait(3)
            return Response(compact_response_rows(payload))
        finally:
            with mutex: active -= 1
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(post, draft=plan(19), parallelism=2), range(2)))
    assert maximum == 2
    assert all(result["diagnostics"]["pdf_draft_metadata"]["classified_count"] == 19 for result in results)


@pytest.mark.parametrize("count,expected", [(11, 1), (12, 2)])
def test_only_sufficient_pending_card_count_enables_two_initial_requests(count, expected):
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(compact_response_rows(payload))
    result = run(post, draft=plan(count), parallelism=2)
    assert len(calls) == expected
    if expected == 2:
        assert [len(json.loads(payload["messages"][1]["content"])["items"]) for payload in calls] == [6, 6]
    assert_bodies(result, plan(count))


def test_qwen_compact_budget_keeps_512_floor_for_small_compatible_replies():
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(compact_response_rows(payload))
    run(post, draft=plan(1), provider=STREAM)
    assert calls[0]["max_tokens"] == 512 and calls[0]["enable_thinking"] is False


def test_non_qwen_explicit_reasoning_retains_the_original_output_budget():
    provider = mutable_provider()
    provider.provider_code = "zhongzhan_gpt"
    provider.model_name = "gemini-3.8-flash"
    provider.api_base = "https://relay.invalid/v1"
    provider.reasoning_effort = "high"
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(compact_response_rows(payload))
    result = run(post, draft=plan(19), provider=provider, parallelism=1)
    assert len(calls) == 1
    assert calls[0]["reasoning_effort"] == "high" and calls[0]["max_tokens"] == 256 + 128 * 19
    assert result["diagnostics"]["pdf_draft_metadata"]["classified_count"] == 19


def test_ordinary_nineteen_card_paper_defaults_to_compact_single_lane_after_measured_overhead():
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(compact_response_rows(payload))
    result = run(post, draft=plan(19))
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert len(calls) == 1 and report["concurrency"] == 1 and report["max_posts"] == 2
    assert report["routing"]["mode"] == "auto"
    assert report["routing"]["reason"] == "ordinary_paper_single_lane"
    assert report["routing"]["is_token_measurement"] is False
    assert_bodies(result, plan(19))


def long_plan(count):
    draft = plan(count)
    for index, question in enumerate(draft["questions"]):
        question["content"] += "完整题干条件、所有小问与公式保留。" * 40
        draft["items"][index]["content"] = question["content"]
        draft["items"][index]["question"] = deepcopy(question)
    draft["source"] = "\n".join(question["content"] + question["answer_markdown"] for question in draft["questions"])
    draft["input_sha256"] = draft["source_sha256"] = hashlib.sha256(draft["source"].encode()).hexdigest()
    return draft


def test_large_thirty_two_card_input_automatically_overlaps_two_complete_card_groups():
    draft = long_plan(32); entered = threading.Barrier(2); calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); entered.wait(timeout=3)
        return Response(compact_response_rows(payload))
    result = run(post, draft=draft)
    report = result["diagnostics"]["pdf_draft_metadata"]
    assert len(calls) == 2 and report["concurrency"] == 2 and report["max_posts"] == 3
    assert report["routing"]["reason"] == "large_input_two_lanes"
    assert report["routing"]["pending_cards"] == 32 and report["routing"]["input_characters"] >= 12000
    assert report["routing"]["is_token_measurement"] is False
    assert_bodies(result, draft)


@pytest.mark.parametrize("draft,reason", [(plan(32), "short_input_single_lane"),
                                          (long_plan(31), "ordinary_paper_single_lane")])
def test_auto_two_lanes_requires_both_large_card_count_and_long_full_input(draft, reason):
    calls = []
    def post(config, payload, **options):
        calls.append(deepcopy(payload)); return Response(compact_response_rows(payload))
    result = run(post, draft=draft)
    assert len(calls) == 1 and result["diagnostics"]["pdf_draft_metadata"]["routing"]["reason"] == reason
    assert_bodies(result, draft)
