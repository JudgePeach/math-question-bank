"""Joint OCR/figure protocol checks without network, model fees or app state."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PIL import Image
import requests

from mathbank import pdf_page_vision as vision
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_figures import _validate_layout, describe_figure_slots
from mathbank.task_manager import TaskCancelled


def page_result():
    return {
        "markdown": "1. 题干 $AC=\\sqrt{2}$。\n[插图待补: 图1]",
        "figures": [{"slot": "图1", "bbox": [100, 200, 400, 500],
                     "candidate_ids": ["p3_raster_001"],
                     "review_required": False, "review_reason": ""}],
        "ignored_candidates": [], "page_complete": True, "warnings": [],
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Unit tests must not contact any model")
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    provider = resolve_ocr_provider("siliconflow", {
        "SILICONFLOW_API_KEY": "unused-test-key",
        "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3-VL-8B-Instruct",
    })
    monkeypatch.setattr(vision, "resolve_ocr_provider", lambda engine: provider)
    prompt_inputs = []
    def prompt(info):
        prompt_inputs.append(deepcopy(info))
        return "Return a joint PDF page result."
    monkeypatch.setattr(vision.prompts, "build_pdf_page_vision_prompt", prompt, raising=False)
    image = tmp_path / "page.png"
    Image.new("RGB", (8, 8), "white").save(image)
    calls = []
    response = {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(page_result())}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 200, "total_tokens": 1600},
    }
    def post(config, payload, **kwargs):
        calls.append((config, deepcopy(payload), kwargs))
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(response))
    monkeypatch.setattr(vision, "post_chat_completion", post)
    info = {"page_index": 2, "width": 600, "height": 800,
            "candidates": [{"id": "p3_raster_001", "bbox": [100, 200, 400, 500], "type": "raster"}],
            "markdown": "discarded bad native text", "text_blocks": [{"text": "private native text"}]}
    return SimpleNamespace(image=str(image), info=info, calls=calls, response=response,
                           provider=provider, prompt_inputs=prompt_inputs)


def call(setup, result=None, **kwargs):
    if result is not None:
        setup.response["choices"][0]["message"]["content"] = json.dumps(result)
    return vision.request_pdf_page(setup.image, setup.info, **kwargs)


def test_joint_page_uses_one_configured_call_and_server_owned_slot(setup):
    original = page_result()
    result = call(setup, original)
    assert len(setup.calls) == 1
    assert result["markdown"] == original["markdown"]
    assert result["model"] == setup.provider.model_name
    assert result["usage"] == setup.response["usage"]
    figure = result["layout"]["figures"][0]
    assert figure["slot_id"] == "p3-s1"
    assert figure["anchor_before"] == figure["anchor_after"] == ""
    assert "image_path" not in figure
    sent = setup.calls[0][1]
    assert sent["max_tokens"] == vision.MAX_OUTPUT_TOKENS
    assert sent["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "enable_thinking" not in sent
    assert set(setup.prompt_inputs[0]) == {"page_index", "width", "height", "candidates"}
    assert "private" not in str(setup.prompt_inputs)
    checked, warnings = _validate_layout(result["layout"], {
        **setup.info, "figure_slots": describe_figure_slots(result["markdown"], 2),
    })
    assert checked[0]["slot_id"] == "p3-s1" and warnings == []


def test_named_nonsquare_model_bbox_uses_named_axes_and_retains_raw_audit(setup):
    setup.info["candidates"][0]["type"] = "vector"
    data = page_result()
    named = {"top": 798, "bottom": 932, "right": 913, "left": 700}
    data["figures"][0]["bbox"] = named
    result = call(setup, data)
    figure = result["layout"]["figures"][0]
    assert figure["bbox"] == figure["model_bbox"] == [700, 798, 913, 932]
    assert figure["model_bbox_raw"] == named and figure["model_bbox_space"] == "page"


def test_named_raster_estimate_does_not_trim_exact_native_box(setup):
    data = page_result()
    data["figures"][0]["bbox"] = {"left": 140, "top": 240, "right": 300, "bottom": 350}
    result = call(setup, data)
    figure = result["layout"]["figures"][0]
    assert figure["bbox"] == setup.info["candidates"][0]["bbox"]
    assert figure["model_bbox"] == [140, 240, 300, 350] and figure["native_box"] is True


@pytest.mark.parametrize("box", [
    {"left": 1, "top": 2, "right": 3}, {"left": 1, "top": 2, "right": 3, "bottom": 4, "confidence": 1},
    {"left": False, "top": 2, "right": 3, "bottom": 4}, {"left": 1, "top": 2, "right": float("nan"), "bottom": 4},
    {"left": 1, "top": 2, "right": 1001, "bottom": 4}, {"left": 900, "top": 2, "right": 400, "bottom": 40},
])
def test_bad_named_bbox_keeps_transcript_for_review_without_retry(setup, box):
    setup.info["candidates"][0]["type"] = "vector"
    data = page_result()
    data["figures"][0]["bbox"] = box
    result = call(setup, data)
    assert result["markdown"] == data["markdown"] and result["layout"]["figures"] == []
    assert result["layout"]["page_complete"] is False and len(setup.calls) == 1


def test_all_figure_prompts_name_axes_with_a_nonsquare_example():
    from mathbank import prompts
    info = {"page_index": 0, "candidates": []}
    values = [prompts.build_pdf_page_vision_prompt(info), prompts.build_pdf_layout_prompt("题干", info),
              prompts.build_pdf_region_vision_prompt([{"id": "r1", "page_info": info}])]
    for value in values:
        assert '"bbox":{"left":120,"top":640,"right":480,"bottom":820}' in value
        assert "宽360高180" in value and "不能交换" in value and "禁止返回XY或YX顺序数组" in value
        assert "示例仅解释方向，不可照抄，须根据当前图像定位" in value
        assert '"left":700,"top":798,"right":913,"bottom":932' not in value
    assert "bbox填null" in values[0] and "bbox填null" in values[2]


@pytest.mark.parametrize("label", ["图1", "图(1)", "图（1）", "图一"])
def test_original_chinese_figure_labels_are_bound_without_rewriting(setup, label):
    data = page_result()
    data["markdown"] = "题干\n[插图待补: " + label + "]"
    data["figures"][0]["slot"] = label
    result = call(setup, data)
    assert result["markdown"] == data["markdown"]
    assert result["layout"]["figures"][0]["slot_id"] == "p3-s1"


def test_warning_and_incomplete_coverage_remain_actionable(setup):
    data = page_result()
    data["warnings"] = ["局部文字存在疑问"]
    data["page_complete"] = False
    result = call(setup, data)
    assert result["layout"]["page_complete"] is False
    assert result["layout"]["notes"] == ["待核对：局部文字存在疑问"]
    _, warnings = _validate_layout(result["layout"], {**setup.info,
        "figure_slots": describe_figure_slots(result["markdown"], 2)})
    assert any("局部文字存在疑问" in warning for warning in warnings)


def test_no_figure_page_returns_transcript_without_placeholder(setup):
    data = page_result()
    data.update(markdown="1. 纯文字和公式 $x^2+1$。", figures=[],
                ignored_candidates=[{"id": "p3_raster_001", "reason": "formula"}])
    result = call(setup, data)
    assert result["layout"]["figures"] == []
    assert result["layout"]["page_complete"] is True


@pytest.mark.parametrize("finish", ["length", "max_tokens", "content_filter", "tool_calls", None])
def test_truncated_or_unknown_completion_is_rejected_even_with_valid_json(setup, finish):
    setup.response["choices"][0]["finish_reason"] = finish
    with pytest.raises(ValueError, match="未正常结束|截断"):
        call(setup)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("field,value", [
    ("markdown", ""), ("markdown", 2), ("page_complete", "true"),
    ("warnings", "warning"), ("warnings", [None]),
    ("warnings", ["x" * 501]), ("warnings", ["warning"] * 17),
])
def test_invalid_transcript_or_envelope_is_not_accepted(setup, field, value):
    data = page_result()
    data[field] = value
    with pytest.raises(ValueError):
        call(setup, data)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("change", ["unknown", "missing", "not_object", "invalid_json"])
def test_unknown_page_fields_and_invalid_json_fail_once_without_echo(setup, change):
    data = page_result()
    if change == "unknown":
        data["image_path"] = "/private/do-not-echo-secret.png"
    elif change == "missing":
        del data["markdown"]
    elif change == "not_object":
        data = [data]
    else:
        setup.response["choices"][0]["message"]["content"] = '{"markdown":"do-not-echo-secret"'
    with pytest.raises(ValueError) as caught:
        call(setup, None if change == "invalid_json" else data)
    assert "do-not-echo-secret" not in str(caught.value)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("markup", [
    "![图](/static/uploads/file.png)", "![图](https://example.test/file.png)",
    r"\includegraphics{figure.png}", '<img src="file.png">',
    "file:///private/photo.png", "data:image/png;base64,abc",
])
def test_model_cannot_write_asset_paths_into_transcript(setup, markup):
    data = page_result()
    data["markdown"] += markup
    with pytest.raises(ValueError, match="图片路径"):
        call(setup, data)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("bbox", [
    [0, 0, 1001, 200], [-1, 0, 200, 200], [300, 200, 100, 500],
    [0, 0, True, 200], [0, 0, float("nan"), 200], [0, 0, float("inf"), 200],
    [0, 0, 0.2, 300], [0, 0, 1000, 1000], [0, 0, 100], [0, 0, 10**400, 200], "bad",
])
def test_bad_geometry_keeps_text_but_cannot_attach_any_figure_or_retry(setup, bbox):
    setup.info["candidates"][0]["type"] = "vector"
    data = page_result()
    data["figures"][0]["bbox"] = bbox
    result = call(setup, data)
    assert result["markdown"] == data["markdown"]
    assert result["layout"]["figures"] == []
    assert result["layout"]["page_complete"] is False
    assert result["layout"]["notes"]
    assert len(setup.calls) == 1


@pytest.mark.parametrize("estimate", [None, [140, 240, 300, 350], [500, 600, 750, 900], [-40, -80, 1100, 1500]])
def test_unique_raster_uses_its_full_native_occurrence_not_estimated_corners(setup, estimate):
    data = page_result()
    data["figures"][0]["bbox"] = estimate
    result = call(setup, data)
    figure = result["layout"]["figures"][0]
    assert figure["bbox"] == [100, 200, 400, 500]
    assert figure["model_bbox"] == estimate
    assert figure["native_box"] is True
    assert figure["slot_id"] == "p3-s1"
    assert result["layout"]["page_complete"] is True


@pytest.mark.parametrize("bad_box", [None, [], [0, 0, 1001, 200], [0, 0, float("nan"), 200],
                                    [0, 0, 1000, 1000], [400, 500, 100, 200]])
def test_invalid_native_geometry_is_not_replaced_with_an_unverified_estimate(setup, bad_box):
    setup.info["candidates"][0]["bbox"] = bad_box
    result = call(setup)
    assert result["layout"]["figures"] == []
    assert result["layout"]["page_complete"] is False
    assert result["markdown"] == page_result()["markdown"]


@pytest.mark.parametrize("mode", ["vector", "multiple_candidates", "shared_candidate", "unknown_candidate"])
def test_null_bbox_only_works_for_a_unique_known_single_raster(setup, mode):
    data = page_result()
    data["figures"][0]["bbox"] = None
    if mode == "vector":
        setup.info["candidates"][0]["type"] = "vector"
    elif mode == "multiple_candidates":
        setup.info["candidates"].append({"id": "other", "type": "raster", "bbox": [450, 200, 650, 500]})
        data["figures"][0]["candidate_ids"].append("other")
    elif mode == "shared_candidate":
        data["markdown"] += "\n[插图待补: 图2]"
        data["figures"].append({**deepcopy(data["figures"][0]), "slot": "图2"})
    else:
        data["figures"][0]["candidate_ids"] = ["unknown"]
    result = call(setup, data)
    assert result["layout"]["figures"] == []
    assert result["layout"]["page_complete"] is False
    assert result["markdown"] == data["markdown"]


def test_shared_raster_with_real_visual_boxes_keeps_existing_duplicate_review(setup):
    data = page_result()
    data["markdown"] += "\n[插图待补: 图2]"
    data["figures"].append({**deepcopy(data["figures"][0]), "slot": "图2"})
    result = call(setup, data)
    assert all(figure["native_box"] is False for figure in result["layout"]["figures"])
    _, warnings = _validate_layout(result["layout"], {**setup.info,
        "figure_slots": describe_figure_slots(result["markdown"], 2)})
    assert any("重复" in warning or "重叠" in warning for warning in warnings)


def test_candidate_physical_order_does_not_reorder_abcd_slot_bindings(setup):
    # Native top sorting is A, B, D, C because D starts slightly above C.
    ids = ["image_a", "image_b", "image_d", "image_c"]
    boxes = [[100, 100, 250, 280], [500, 100, 650, 280],
             [500, 390, 650, 590], [100, 400, 250, 590]]
    setup.info["candidates"] = [{"id": identifier, "type": "raster", "bbox": bbox}
                                for identifier, bbox in zip(ids, boxes)]
    data = page_result()
    data["markdown"] = "\n".join(f"{letter}. [插图待补: 图{number}]" for number, letter in enumerate("ABCD", 1))
    data["figures"] = [{"slot": f"图{number}", "bbox": None, "candidate_ids": [identifier],
                        "review_required": False, "review_reason": ""}
                       for number, identifier in enumerate(["image_a", "image_b", "image_c", "image_d"], 1)]
    result = call(setup, data)
    figures = result["layout"]["figures"]
    assert [figure["slot_id"] for figure in figures] == ["p3-s1", "p3-s2", "p3-s3", "p3-s4"]
    assert [figure["bbox"] for figure in figures] == [boxes[0], boxes[1], boxes[3], boxes[2]]
    assert result["markdown"] == data["markdown"]


@pytest.mark.parametrize("change", [
    "unknown_slot", "duplicate_slot", "duplicate_placeholder", "invalid_placeholder",
    "unknown_candidate", "duplicate_candidate", "candidate_type", "unknown_figure_field",
    "missing_figure_field", "review_type", "reason_type", "too_many_figures", "figures_type",
    "ignored_type", "ignored_unknown_field", "ignored_unknown_id", "ignored_bad_reason",
    "ignored_duplicate", "both_used_and_ignored",
])
def test_invalid_figure_contract_preserves_transcript_for_manual_review(setup, change):
    data = page_result()
    figure = data["figures"][0]
    if change == "unknown_slot": figure["slot"] = "图99"
    elif change == "duplicate_slot": data["figures"].append(deepcopy(figure))
    elif change == "duplicate_placeholder": data["markdown"] += "\n[插图待补: 图1]"
    elif change == "invalid_placeholder": data["markdown"] += "\n[插图待补: 图2"
    elif change == "unknown_candidate": figure["candidate_ids"] = ["unknown"]
    elif change == "duplicate_candidate": figure["candidate_ids"] *= 2
    elif change == "candidate_type": figure["candidate_ids"] = [{}]
    elif change == "unknown_figure_field": figure["image_path"] = "/private/model-path.png"
    elif change == "missing_figure_field": del figure["bbox"]
    elif change == "review_type": figure["review_required"] = "false"
    elif change == "reason_type": figure["review_reason"] = {}
    elif change == "too_many_figures": data["figures"] *= 33
    elif change == "figures_type": data["figures"] = None
    elif change == "ignored_type": data["ignored_candidates"] = {}
    elif change == "ignored_unknown_field": data["ignored_candidates"] = [{"id": "p3_raster_001", "reason": "formula", "path": "bad"}]
    elif change == "ignored_unknown_id": data["ignored_candidates"] = [{"id": "unknown", "reason": "formula"}]
    elif change == "ignored_bad_reason": data["ignored_candidates"] = [{"id": "p3_raster_001", "reason": "guess"}]
    elif change == "ignored_duplicate": data["ignored_candidates"] = [{"id": "p3_raster_001", "reason": "formula"}] * 2
    elif change == "both_used_and_ignored": data["ignored_candidates"] = [{"id": "p3_raster_001", "reason": "formula"}]
    result = call(setup, data)
    assert result["markdown"] == data["markdown"]
    assert result["layout"]["figures"] == []
    assert result["layout"]["page_complete"] is False
    assert all(note.startswith("待核对：") for note in result["layout"]["notes"])
    assert len(setup.calls) == 1


def test_unbound_slot_keeps_other_valid_figure_and_requires_review(setup):
    data = page_result()
    data["markdown"] += "\n2. 另一题 [插图待补: 图2]"
    result = call(setup, data)
    assert result["markdown"] == data["markdown"]
    assert len(result["layout"]["figures"]) == 1
    assert result["layout"]["page_complete"] is False
    assert any("1 幅插图占位" in note for note in result["layout"]["notes"])
    assert len(setup.calls) == 1


def test_cancellation_before_post_avoids_paid_call(setup):
    with pytest.raises(TaskCancelled):
        call(setup, check_cancelled=lambda: (_ for _ in ()).throw(TaskCancelled("cancelled")))
    assert setup.calls == []


def test_cancellation_after_post_discards_the_result_without_retry(setup):
    def cancel_after_call():
        if setup.calls:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        call(setup, check_cancelled=cancel_after_call)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("failure", [requests.ReadTimeout, requests.ConnectionError])
def test_transport_failure_is_not_retried_or_echoed(setup, monkeypatch, failure):
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise failure("secret provider url/key must not be returned")
    monkeypatch.setattr(vision, "post_chat_completion", fail)
    with pytest.raises(ValueError) as caught:
        call(setup)
    assert "secret" not in str(caught.value)
    assert "未自动重试" in str(caught.value)
    assert attempts == [1]


def test_http_failure_does_not_read_or_echo_body_and_never_retries(setup, monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=503, json=lambda: pytest.fail("failed HTTP body must not be read"))
    monkeypatch.setattr(vision, "post_chat_completion", post)
    with pytest.raises(ValueError, match="HTTP 503"):
        call(setup)
    assert calls == [1]


@pytest.mark.parametrize("changes", [{"api_key": ""}, {"chat_completions_url": ""}, {"supports_image_input": False}])
def test_missing_or_nonvision_provider_is_rejected_before_post(setup, monkeypatch, changes):
    provider = SimpleNamespace(api_key="unused", chat_completions_url="https://example.test", supports_image_input=True)
    provider.__dict__.update(changes)
    monkeypatch.setattr(vision, "resolve_ocr_provider", lambda engine: provider)
    with pytest.raises(ValueError):
        call(setup)
    assert setup.calls == []


def test_usage_is_bounded_provider_data_and_cap_precedes_policy(setup, monkeypatch):
    setup.response["usage"] = {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": 42, "extra": "secret"}
    def policy(payload, *, provider, task):
        assert payload["max_tokens"] == vision.MAX_OUTPUT_TOKENS and task == "ocr"
        result = dict(payload)
        result.pop("max_tokens")
        result["max_completion_tokens"] = 12345
        return result
    monkeypatch.setattr(vision, "apply_model_thinking_policy", policy)
    result = call(setup)
    assert result["usage"] == {"total_tokens": 42}
    assert setup.calls[0][1]["max_completion_tokens"] == 12345
    assert "max_tokens" not in setup.calls[0][1]


def test_text_and_response_size_caps(setup, monkeypatch):
    monkeypatch.setattr(vision, "MAX_SOURCE_CHARS", 10)
    with pytest.raises(ValueError, match="过长"):
        call(setup)
    assert len(setup.calls) == 1
    monkeypatch.setattr(vision, "MAX_RESPONSE_CHARS", 10)
    with pytest.raises(ValueError, match="过长"):
        call(setup)
    assert len(setup.calls) == 2
