"""Safety and cost boundaries for optional PDF source suspicion verification."""

from copy import deepcopy
import json
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from mathbank import pdf_source_verify as verify
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.task_manager import TaskCancelled


def add_question(state, *, source=None, output=None, page=1, extra_reason=None):
    index = len(state.questions)
    number = 14 + index
    source = source if source is not None else f"{number}. 已知函数 $y=x^2$，求顶点。"
    output = output if output is not None else "给出函数 $y=x^2$，求顶点。"
    review = {"required": True, "reasons": [verify.POSITION_REASON], "source_excerpt": source}
    if extra_reason:
        review["reasons"].append(extra_reason)
    state.questions.append({"content": output, "answer_markdown": "", "source_review": review})
    start = sum(len(item["source_excerpt"]) for item in state.diagnostics["source_matches"])
    state.diagnostics["source_matches"].append({"question_index": index, "field": "content", "source_number": number,
        "source_excerpt": source, "source_start": start, "source_end": start + len(source)})
    state.diagnostics["pdf_review_items"].append({"question_index": index, "source_number": number,
        "source_pages": [page], "reasons": list(review["reasons"])})
    state.diagnostics["source_review_count"] = len(state.questions)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("No model calls are allowed in unit tests")
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused-test-key",
                                                 "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3-VL-8B-Instruct"})
    monkeypatch.setattr(verify, "resolve_ocr_provider", lambda engine: provider)
    root = tmp_path / "uploads"
    (root / "tmp").mkdir(parents=True)
    monkeypatch.setattr(verify, "UPLOADS_DIR", root)
    urls = []
    for number in range(1, 6):
        filename = f"pdf_page_test_{number}.png"
        Image.new("RGB", (20, 20), "white").save(root / "tmp" / filename)
        urls.append("/static/uploads/tmp/" + filename)
    state = SimpleNamespace(questions=[], diagnostics={"source_matches": [], "pdf_review_items": []},
                            urls=urls, numbers=list(range(1, 6)), provider=provider, root=root,
                            calls=[], prompt_items=[])
    add_question(state)
    add_question(state, page=2)
    state.response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"items": [
        {"id": "item_001", "decision": "equivalent", "evidence": "原页函数及所求不变，已知改为给出。"},
        {"id": "item_002", "decision": "uncertain", "evidence": "原页图像中的条件边界看不清。"},
    ]})}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 155}}
    builder = verify.prompts.build_pdf_source_verification_prompt
    def prompt(items):
        state.prompt_items.append(deepcopy(items))
        return builder(items)
    monkeypatch.setattr(verify.prompts, "build_pdf_source_verification_prompt", prompt)
    def post(config, payload, **kwargs):
        state.calls.append((config, deepcopy(payload), kwargs))
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(state.response))
    monkeypatch.setattr(verify, "post_chat_completion", post)
    return state


def call(setup, data=None, **kwargs):
    if data is not None:
        setup.response["choices"][0]["message"]["content"] = json.dumps(data)
    return verify.verify_pdf_source_suspicions(setup.questions, setup.diagnostics, setup.urls, setup.numbers, **kwargs)


def all_equivalent(count):
    return {"items": [{"id": f"item_{index + 1:03d}", "decision": "equivalent", "evidence": "原页全部条件与题干一致，仅表述排版差异。"}
                      for index in range(count)]}


def test_confirmed_only_clear_review_keep_original_evidence_and_text(setup):
    original = deepcopy(setup.questions)
    report = call(setup)
    assert report == {"status": "completed", "calls": 1, "checked": 2, "confirmed": 1, "pending": 1, "skipped": 0,
        "usage": setup.response["usage"], "notes": [], "skipped_reasons": [], "items": [
            {"id": "item_001", "question_index": 0, "source_number": 14, "source_pages": [1],
             "decision": "equivalent", "evidence": "原页函数及所求不变，已知改为给出。"},
            {"id": "item_002", "question_index": 1, "source_number": 15, "source_pages": [2],
             "decision": "uncertain", "evidence": "原页图像中的条件边界看不清。"}]}
    confirmed = setup.questions[0]["source_review"]
    assert confirmed["required"] is False and confirmed["verified_by"] == "vision"
    assert confirmed["source_excerpt"] == original[0]["source_review"]["source_excerpt"]
    assert confirmed["reasons"] == original[0]["source_review"]["reasons"]
    assert len(confirmed["verification"]["snapshot_hash"]) == 64
    assert confirmed["verification"]["model"] == setup.provider.model_name
    assert setup.questions[1] == original[1]
    for new, old in zip(setup.questions, original):
        assert new["content"] == old["content"] and new["answer_markdown"] == old["answer_markdown"]
    assert setup.diagnostics["source_review_count"] == 1
    assert [item["question_index"] for item in setup.diagnostics["pdf_review_items"]] == [1]
    payload = setup.calls[0][1]
    assert setup.calls[0][2]["retry_connection"] is False
    assert payload["max_tokens"] == 4096
    assert len([part for part in payload["messages"][0]["content"] if part["type"] == "image_url"]) == 2


def test_no_required_items_do_not_resolve_provider_or_touch_pages(setup, monkeypatch):
    for question in setup.questions:
        question["source_review"]["required"] = False
    monkeypatch.setattr(verify, "resolve_ocr_provider", lambda _: pytest.fail("No provider needed"))
    setup.urls = ["file:///private/never-read"]
    report = call(setup)
    assert report["status"] == "no_candidates" and report["calls"] == report["pending"] == 0
    assert report["usage"] == {}


@pytest.mark.parametrize("source,output", [
    ("14. 已知 $x+1=2$，求值。", "给出 $x-1=2$，求值。"),
    ("14. 已知 $x=1$、$y=2$，求值。", "给出 $y=2$、$x=1$，求值。"),
    ("14. 已知 $x=1$，求值。", "给出 $x=1$、$x=1$，求值。"),
    ("14. 已知 $x=1$，求值。", "给出 $x=1$，为 2。"),
    ("14. 已知增长率 20%，求值。", "给出增长率 20，求值。"),
    ("14. 已知长 1.5 米，求值。", "给出长 15 米，求值。"),
    ("14. 已知长度 .5 米，求值。", "给出长度 5 米，求值。"),
    ("14. 已知长度 ．5 米，求值。", "给出长度 5 米，求值。"),
    ("14. 已知两个实数，求值。", "给出三个实数，求值。"),
    ("14. 已知 $x$ 不大于 $y$，求值。", "给出 $x$ 大于 $y$，求值。"),
    ("14. 已知至少两个根，求值。", "给出至多两个根，求值。"),
    ("14. 已知两直线平行，求值。", "给出两直线垂直，求值。"),
    ("14. 求函数 $f(x)$ 的最大值。", "求函数 $f(x)$ 的最小值。"),
    ("14. 已知两边相等，求值。", "给出两边不等，求值。"),
    ("14. 已知函数没有零点，求值。", "给出函数有零点，求值。"),
    ("14. 全部实数都满足条件。", "部分实数都满足条件。"),
    ("14. 任一实数 x 均满足条件。", "某一实数 x 均满足条件。"),
    ("14. 这些点均位于曲线上。", "这些点位于曲线上。"),
    ("14. 已知长度 2 米，求值。", "给出长度 2 厘米，求值。"),
    ("14. 已知 a_b+cd，求值。", "给出 ab+c_d，求值。"),
    ("14. 已知 αa，求值。", "给出 aα，求值。"),
    ("14. 两组观测值依次为1，23。", "两组数据值依次为12，3。"),
    (r"14. 已知分式 \frac{1}{23}，求值。", r"给出分式 \frac{12}{3}，求值。"),
    ("14. 已知 a_{b+c}，求值。", "给出 a_b+c，求值。"),
    ("14. 已知 x−1，求值。", "给出 x1，求值。"),
    ("14. 已知 x*2，求值。", "给出 x2，求值。"),
    ("14. 已知 x_1，求值。", "给出 x1，求值。"),
    ("14. 已知 a，求值。", "给出 b，求值。"),
    ("14. 参数 α 满足条件，求值。", "参数 β 满足条件，求值。"),
    ("14. 选取编号①的区域，求值。", "选取编号②的区域，求值。"),
    ("14. 选取编号Ⅰ的区域，求值。", "选取编号Ⅱ的区域，求值。"),
    ("14. 选取编号１的区域，求值。", "选取编号２的区域，求值。"),
    ("14. 已知 x²，求值。", "给出 x³，求值。"),
    ("14. 已知 $x=1$，求值。", "给出 [[MBM_unknown]]，求值。"),
    ("14. 已知 $x=1$，求值。", "给出 $x=1$ [公式待核对]，求值。"),
    ("14. 已知 $x=1$，求值。", "给出 $x=1，求值。"),
    ("14. 已知 $x=1$。![图](/static/uploads/a.png)", "给出 $x=1$。![图](/static/uploads/b.png)"),
    ("14. 已知 $x=1$。![图](/static/uploads/a.png)求值。", "给出 $x=1$。求值。![图](/static/uploads/a.png)"),
    ("14. 已知 $x=1$。![图](/static/uploads/a.png)", "给出 $x=1$。"),
])
def test_critical_formula_condition_or_image_change_never_reaches_model(setup, source, output):
    setup.questions.clear()
    setup.diagnostics = {"source_matches": [], "pdf_review_items": []}
    add_question(setup, source=source, output=output)
    original = deepcopy(setup.questions)
    report = call(setup, all_equivalent(1))
    assert report["status"] == "no_candidates" and report["skipped"] == report["pending"] == 1
    assert not setup.calls and setup.questions == original


def test_math_tokens_compare_full_content_not_formula_count(setup):
    assert verify._locally_eligible("14. 已知 $\\dfrac{1}{2}$。", "给出 $\\frac{1}{2}$。")
    assert not verify._locally_eligible("14. 已知 $\\sin x$。", "给出 $\\sinx$。")
    assert not verify._locally_eligible("14. 已知 $\\text{a b}$。", "给出 $\\text{ab}$。")
    assert verify._locally_eligible("14. 已知长度 $.5$ 米，求值。", "给出长度 $.5$ 米，求值。")
    assert not verify._locally_eligible("14. 已知长度 $.5$ 米，求值。", "给出长度 $5$ 米，求值。")


def test_source_metadata_numbers_are_masked_before_critical_condition_guard(setup):
    source = "14. (13分) 已知函数 $y=x^2$，求顶点。\n高三数学 第 6 页（共 6 页）"
    output = "给出函数 $y=x^2$，求顶点。"
    assert verify._locally_eligible(source, output)


@pytest.mark.parametrize("change", ["extra_reason", "answer_reason", "unknown_id_reason", "duplicate_match", "duplicate_source", "answer_match",
                                    "missing_match", "duplicate_item", "missing_pages", "unknown_number", "stale_excerpt", "wrong_offsets",
                                    "boolean_index", "conflicting_review_item"])
def test_ambiguous_provenance_or_other_review_reasons_stay_manual(setup, change):
    setup.questions = setup.questions[:1]
    setup.diagnostics["source_matches"] = setup.diagnostics["source_matches"][:1]
    setup.diagnostics["pdf_review_items"] = setup.diagnostics["pdf_review_items"][:1]
    match, item, review = setup.diagnostics["source_matches"][0], setup.diagnostics["pdf_review_items"][0], setup.questions[0]["source_review"]
    if change == "extra_reason": review["reasons"].append("本题仍有未补齐的插图，请对照原页补图或确认。")
    elif change == "answer_reason": review["reasons"] = ["原版答案文字或公式位置与原文未能完整对应，请对照原文核对。"]
    elif change == "unknown_id_reason": review["reasons"].append("模型返回了无法识别的公式编号，请对照原文补全公式。")
    elif change == "duplicate_match": setup.diagnostics["source_matches"].append(deepcopy(match))
    elif change == "duplicate_source": setup.diagnostics["source_matches"].append({**match, "question_index": 1})
    elif change == "answer_match": match["field"] = "answer_markdown"
    elif change == "missing_match": setup.diagnostics["source_matches"] = []
    elif change == "duplicate_item": setup.diagnostics["pdf_review_items"].append(deepcopy(item))
    elif change == "missing_pages": item["source_pages"] = []
    elif change == "unknown_number": match["source_number"] = None
    elif change == "stale_excerpt": review["source_excerpt"] = "不同的源摘录"
    elif change == "wrong_offsets": match["source_end"] += 1
    elif change == "boolean_index": match["question_index"] = False
    elif change == "conflicting_review_item": item["reasons"] = ["配图待核对"]
    report = call(setup, all_equivalent(1))
    assert report["calls"] == 0 and report["pending"] == 1 and report["skipped"] == 1
    assert review["required"] is True


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "decision", "extra", "empty_evidence", "long_evidence", "root_field", "not_list"])
def test_bad_model_contract_cannot_clear_even_first_valid_item(setup, change):
    data = all_equivalent(2)
    if change == "missing": data["items"].pop()
    elif change == "duplicate": data["items"][1]["id"] = "item_001"
    elif change == "unknown": data["items"][1]["id"] = "unknown"
    elif change == "decision": data["items"][1]["decision"] = "probably"
    elif change == "extra": data["items"][1]["confidence"] = 1
    elif change == "empty_evidence": data["items"][1]["evidence"] = " "
    elif change == "long_evidence": data["items"][1]["evidence"] = "字" * 401
    elif change == "root_field": data["confidence"] = 1
    elif change == "not_list": data["items"] = {}
    original, diagnostics = deepcopy(setup.questions), deepcopy(setup.diagnostics)
    report = call(setup, data)
    assert report["status"] == "failed" and report["calls"] == 1 and report["confirmed"] == 0
    assert setup.questions == original and setup.diagnostics == diagnostics


@pytest.mark.parametrize("finish", ["length", "content_filter", None])
def test_truncated_response_preserves_required_and_actual_usage(setup, finish):
    setup.response["choices"][0]["finish_reason"] = finish
    report = call(setup, all_equivalent(2))
    assert report["status"] == "failed" and report["confirmed"] == 0
    assert report["usage"] == setup.response["usage"]
    assert all(question["source_review"]["required"] for question in setup.questions)


@pytest.mark.parametrize("field", ["content", "answer_markdown", "source_review", "diagnostics"])
def test_inflight_edits_invalidate_all_decisions(setup, monkeypatch, field):
    def post(*args, **kwargs):
        setup.calls.append(1)
        if field == "diagnostics": setup.diagnostics["pdf_review_items"][0]["source_pages"] = [3]
        elif field == "source_review": setup.questions[0][field]["reasons"].append("用户新增待核对")
        else: setup.questions[0][field] += "用户编辑"
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(setup.response))
    monkeypatch.setattr(verify, "post_chat_completion", post)
    report = call(setup, all_equivalent(2))
    assert report["status"] == "failed" and "已变化" in report["notes"][0]
    assert all(question["source_review"]["required"] for question in setup.questions)


def test_eight_item_limit_and_four_page_limit_do_not_truncate_items(setup):
    for _ in range(8): add_question(setup)
    report = call(setup, all_equivalent(8))
    assert report["checked"] == report["confirmed"] == 8 and report["skipped"] == report["pending"] == 2
    assert len(setup.calls) == 1


def test_page_limit_skips_fifth_page_and_keeps_full_available_pages(setup):
    for page in [3, 4, 5]: add_question(setup, page=page)
    report = call(setup, all_equivalent(4))
    assert report["checked"] == 4 and report["pending"] == report["skipped"] == 1
    assert len([part for part in setup.calls[0][1]["messages"][0]["content"] if part["type"] == "image_url"]) == 4


@pytest.mark.parametrize("mode", ["single", "total"])
def test_text_budget_skips_whole_items_and_never_truncates(setup, monkeypatch, mode):
    if mode == "single":
        monkeypatch.setattr(verify, "MAX_ITEM_CHARS", 10)
        report = call(setup)
        assert report["calls"] == 0 and report["pending"] == report["skipped"] == 2
    else:
        monkeypatch.setattr(verify, "MAX_TOTAL_CHARS", 65)
        report = call(setup, all_equivalent(1))
        assert report["checked"] == 1 and report["pending"] == report["skipped"] == 1
        assert setup.prompt_items[0][0]["source_excerpt"] == setup.diagnostics["source_matches"][0]["source_excerpt"]


@pytest.mark.parametrize("change", ["external", "file", "traversal", "wrong_extension", "nonpage", "missing", "duplicate_page", "wrong_png"])
def test_invalid_page_evidence_fails_without_call_or_clearing(setup, change):
    if change == "external": setup.urls[0] = "https://example.test/page.png"
    elif change == "file": setup.urls[0] = "file:///private/secret.png"
    elif change == "traversal": setup.urls[0] = "/static/uploads/tmp/../secret.png"
    elif change == "wrong_extension": setup.urls[0] = "/static/uploads/tmp/pdf_page_test_1.txt"
    elif change == "nonpage":
        (setup.root / "tmp" / "other.png").write_bytes((setup.root / "tmp" / "pdf_page_test_1.png").read_bytes())
        setup.urls[0] = "/static/uploads/tmp/other.png"
    elif change == "missing": setup.numbers[0] = 6
    elif change == "duplicate_page": setup.numbers[1] = 1
    elif change == "wrong_png": (setup.root / "tmp" / "pdf_page_test_1.png").write_text("not an image")
    report = call(setup)
    assert report["status"] == "failed" and report["calls"] == 0 and report["pending"] == 2
    assert all(question["source_review"]["required"] for question in setup.questions)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_cancellation_propagates_without_mutation_or_retry(setup, stage):
    original = deepcopy(setup.questions)
    def cancel():
        if stage == "before" or setup.calls:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled): call(setup, check_cancelled=cancel)
    assert len(setup.calls) == (0 if stage == "before" else 1) and setup.questions == original


def test_timeout_has_no_retry_and_does_not_leak_credentials(setup, monkeypatch):
    def post(*args, **kwargs):
        setup.calls.append(1)
        raise requests.ReadTimeout("secret API key/provider url")
    monkeypatch.setattr(verify, "post_chat_completion", post)
    report = call(setup)
    assert report["status"] == "failed" and report["calls"] == len(setup.calls) == 1
    assert "secret" not in report["notes"][0] and "未自动重试" in report["notes"][0]
    assert report["usage"] == {} and report["pending"] == 2


def test_http_failure_does_not_read_body_or_retry(setup, monkeypatch):
    def post(*args, **kwargs):
        setup.calls.append(1)
        return SimpleNamespace(status_code=503, json=lambda: pytest.fail("Failed response body must not be read"))
    monkeypatch.setattr(verify, "post_chat_completion", post)
    report = call(setup)
    assert report["status"] == "failed" and report["calls"] == 1 and "HTTP 503" in report["notes"][0]


def test_usage_is_only_actual_nonnegative_provider_fields_and_policy_receives_cap(setup, monkeypatch):
    setup.response["usage"] = {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": 77, "estimate": 888}
    def policy(payload, *, provider, task):
        assert payload["max_tokens"] == 4096 and task == "ocr"
        payload = dict(payload)
        payload.pop("max_tokens")
        payload["max_completion_tokens"] = 4096
        return payload
    monkeypatch.setattr(verify, "apply_model_thinking_policy", policy)
    report = call(setup)
    assert report["usage"] == {"total_tokens": 77}
    assert setup.calls[0][1]["max_completion_tokens"] == 4096


def test_missing_usage_is_not_reported_as_zero(setup):
    setup.response.pop("usage")
    assert call(setup)["usage"] == {}


def test_bailian_full_page_policy_does_not_raise_this_referees_4096_cap(setup, monkeypatch):
    provider = resolve_ocr_provider("bailian", {"ALI_BAILIAN_API_KEY": "unused", "ALI_BAILIAN_OCR_MODEL": "qwen3.7-flash"})
    monkeypatch.setattr(verify, "resolve_ocr_provider", lambda engine: provider)
    report = call(setup)
    assert report["status"] == "completed"
    payload = setup.calls[0][1]
    assert payload["max_completion_tokens"] == 4096 and "max_tokens" not in payload
    assert payload["enable_thinking"] is False


def test_provider_setup_error_does_not_leak_exception_contents(setup, monkeypatch):
    def fail(_):
        raise ValueError("secret provider url/key")
    monkeypatch.setattr(verify, "resolve_ocr_provider", fail)
    report = call(setup)
    assert report["status"] == "failed" and report["calls"] == 0
    assert "secret" not in report["notes"][0]


@pytest.mark.parametrize("footer", [r"高三数学 $\cdot$ 第 1 页（共 4 页）", r"高三数试 $\cdot$ 第2页（共4页）", "高三数试 · 第4页（共4页）"])
def test_shared_footer_mask_allows_referee_without_discarding_true_multiplication(footer):
    source = "14. 已知 $a\\cdot b=2$，求值。\n" + footer
    assert verify._locally_eligible(source, "给出 $a\\cdot b=2$，求值。")
    assert not verify._locally_eligible(source, "给出 $ab=2$，求值。")
    assert not verify._locally_eligible(source, "给出 $a\\cdot b=3$，求值。")


@pytest.mark.parametrize("tail", [r"$\cdot$", r"高三数学 $2\cdot3$ 第1页（共4页）", "按第2页（共4页）的条件计算。"])
def test_math_dots_and_page_numbers_outside_proven_footer_remain_evidence(tail):
    assert not verify._locally_eligible("14. 已知 $a=2$，求值。\n" + tail, "给出 $a=2$，求值。")


def first_pass_cache(setup):
    cached = []
    for number in setup.numbers:
        sources = [match["source_excerpt"] for match in setup.diagnostics["source_matches"]
                   if setup.diagnostics["pdf_review_items"][match["question_index"]]["source_pages"] == [number]]
        cached.append({"page_number": number, "origin": "ocr", "figures": [],
                       "markdown": f"<!-- MATHBANK_PDF_PAGE:{number} -->\n" + "\n\n".join(sources)
                                   + f"\n\nUNRELATED_CACHE_MUST_NOT_SEND_{number}"})
    baseline = verify._source_baseline(cached, setup.numbers)
    for match in setup.diagnostics["source_matches"]:
        match["source_start"] = baseline["markdown"].index(match["source_excerpt"])
        match["source_end"] = match["source_start"] + len(match["source_excerpt"])
    return cached


def test_first_pass_cache_is_slice_verified_and_not_resent_in_full(setup):
    cache = first_pass_cache(setup)
    original = deepcopy(cache)
    report = call(setup, all_equivalent(2), source_pages=cache)
    assert report["confirmed"] == 2 and report["calls"] == 1 and report["skipped_reasons"] == []
    assert cache == original
    for question in setup.questions:
        audit = question["source_review"]["verification"]
        assert audit["evidence_reused"] is True and len(audit["source_baseline_hash"]) == 64
    assert all(item["evidence_reused"] is True for item in report["items"])
    sent = json.dumps(setup.calls[0][1], ensure_ascii=False)
    assert "UNRELATED_CACHE_MUST_NOT_SEND" not in sent
    assert "复用既有识图结果" in sent
    assert setup.prompt_items[0][0]["source_excerpt"] == setup.diagnostics["source_matches"][0]["source_excerpt"]


@pytest.mark.parametrize("change", ["missing_page", "reordered_page", "wrong_number", "wrong_marker", "duplicate_marker",
                                    "missing_origin", "missing_figures", "truncated", "too_long", "not_list"])
def test_incomplete_or_mismatched_cache_never_qualifies_a_referee_call(setup, change):
    cache = first_pass_cache(setup)
    if change == "missing_page": cache.pop()
    elif change == "reordered_page": cache[0], cache[1] = cache[1], cache[0]
    elif change == "wrong_number": cache[0]["page_number"] = 2
    elif change == "wrong_marker": cache[0]["markdown"] = cache[0]["markdown"].replace("PAGE:1", "PAGE:2")
    elif change == "duplicate_marker": cache[0]["markdown"] += "<!-- MATHBANK_PDF_PAGE:1 -->"
    elif change == "missing_origin": cache[0].pop("origin")
    elif change == "missing_figures": cache[0].pop("figures")
    elif change == "truncated": cache[0]["truncated"] = True
    elif change == "too_long": cache[0]["markdown"] = "字" * (verify.MAX_CACHED_PAGE_CHARS + 1)
    elif change == "not_list": cache = {}
    report = call(setup, all_equivalent(2), source_pages=cache)
    assert report["calls"] == 0 and report["pending"] == report["skipped"] == 2
    assert all(item["code"] == "source_evidence_missing" for item in report["skipped_reasons"])
    assert all(question["source_review"]["required"] for question in setup.questions)


def test_cache_excerpt_mismatch_skips_only_that_item_and_keeps_other_proven_item(setup):
    cache = first_pass_cache(setup)
    cache[0]["markdown"] = cache[0]["markdown"].replace("已知", "另知")  # Same length, so later page offsets stay exact.
    report = call(setup, {"items": [{"id": "item_002", "decision": "equivalent", "evidence": "第15题原页核对通过。"}]}, source_pages=cache)
    assert report["confirmed"] == 1 and report["skipped"] == 1 and report["pending"] == 1
    assert report["skipped_reasons"][0]["question_index"] == 0
    assert report["skipped_reasons"][0]["code"] == "source_evidence_missing"
    assert setup.questions[0]["source_review"]["required"] is True


def test_cache_changes_during_request_invalidate_the_snapshot(setup, monkeypatch):
    cache = first_pass_cache(setup)
    def post(*args, **kwargs):
        setup.calls.append(1)
        cache[0]["markdown"] += "后来编辑"
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(setup.response))
    monkeypatch.setattr(verify, "post_chat_completion", post)
    report = call(setup, all_equivalent(2), source_pages=cache)
    assert report["status"] == "failed" and report["confirmed"] == 0 and report["calls"] == 1
    assert all(question["source_review"]["required"] for question in setup.questions)


@pytest.mark.parametrize("code", ["formula_difference", "condition_difference", "source_evidence_missing", "figure_risk", "budget_limit", "other_review_reason"])
def test_skipped_reasons_explain_the_block_without_exposing_source_text(setup, monkeypatch, code):
    if code == "formula_difference": setup.questions[0]["content"] = "给出函数 $y=x^3$，求顶点。"
    elif code == "condition_difference": setup.questions[0]["content"] = "给出函数 $y=x^2$，求两个顶点。"
    elif code == "source_evidence_missing": setup.diagnostics["source_matches"].pop(0)
    elif code == "figure_risk": setup.questions[0]["source_review"]["reasons"].append("配图裁剪范围不完整。")
    elif code == "budget_limit": monkeypatch.setattr(verify, "MAX_ITEMS", 0)
    elif code == "other_review_reason": setup.questions[0]["source_review"]["reasons"].append("原版答案来源不明。")
    # Keep this check local to the skipped first question, with no paid path.
    monkeypatch.setattr(verify, "resolve_ocr_provider", lambda engine: SimpleNamespace(api_key=None))
    report = call(setup)
    skipped = next(item for item in report["skipped_reasons"] if item["question_index"] == 0)
    assert skipped["code"] == code and skipped["reason"]
    assert "source_excerpt" not in skipped and "output" not in skipped
    assert "求顶点" not in json.dumps(skipped, ensure_ascii=False)
