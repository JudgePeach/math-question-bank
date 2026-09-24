"""Local PDF crop transcription protocol, with network and paid calls blocked."""

import base64
from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from mathbank import pdf_region_vision as vision
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_figures import _validate_layout, describe_figure_slots
from mathbank.task_manager import TaskCancelled


def region_result(identifier, *, candidate="p3_raster_001", bbox=None):
    return {"id": identifier, "markdown": "A. [插图待补: 图1]",
            "figures": [{"slot": "图1", "bbox": bbox, "candidate_ids": [candidate],
                         "review_required": False, "review_reason": ""}],
            "ignored_candidates": [], "page_complete": True, "warnings": []}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Unit tests must not contact a provider")
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused-test-key",
                                                 "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3-VL-8B-Instruct"})
    monkeypatch.setattr(vision, "resolve_ocr_provider", lambda engine: provider)
    image = tmp_path / "page.png"
    im = Image.new("RGB", (1000, 1000), "white")
    im.paste((255, 0, 0), (100, 100, 900, 300))
    im.paste((0, 0, 255), (100, 600, 900, 800))
    im.save(image)
    info = {"page_index": 2, "width": 600, "height": 800,
            "candidates": [{"id": "p3_raster_001", "type": "raster", "bbox": [180, 140, 340, 260]},
                           {"id": "p3_vector_001", "type": "vector", "bbox": [180, 640, 340, 760]}]}
    regions = [{"id": "r1", "bbox": [100, 100, 900, 300],
                "page_info": {"page_index": 2, "width": 480, "height": 160,
                              "candidates": [{"id": "p3_raster_001", "type": "raster", "bbox": [100, 200, 300, 800]}]}},
               {"id": "r2", "bbox": [100, 600, 900, 800],
                "page_info": {"page_index": 2, "width": 480, "height": 160,
                              "candidates": [{"id": "p3_vector_001", "type": "vector", "bbox": [100, 200, 300, 800]}]}}]
    plan = {"kind": "mixed", "pieces": [{"text": "可靠原生文字 PRIVATE_NATIVE $a=1$"}, {"region_id": "r1"},
                                         {"text": "可靠原生中段"}, {"region_id": "r2"}, {"text": "原生尾段"}],
            "regions": regions, "native_characters": 41, "area_ratio": 0.32}
    data = {"regions": [region_result("r1"), region_result("r2", candidate="p3_vector_001", bbox=[100, 200, 300, 800])]}
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 155}}
    calls = []
    def post(config, payload, **kwargs):
        calls.append((config, deepcopy(payload), kwargs))
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(response))
    monkeypatch.setattr(vision, "post_chat_completion", post)
    return SimpleNamespace(image=str(image), info=info, plan=plan, data=data, response=response,
                           calls=calls, provider=provider, root=tmp_path)


def call(setup, data=None, **kwargs):
    if data is not None:
        setup.response["choices"][0]["message"]["content"] = json.dumps(data)
    return vision.request_pdf_regions(setup.image, setup.info, setup.plan, **kwargs)


def test_two_regions_one_request_preserves_native_text_and_remaps_geometry_slots(setup):
    result = call(setup)
    assert len(setup.calls) == 1
    assert result["markdown"] == "\n\n".join([setup.plan["pieces"][0]["text"], "A. [插图待补: 图1]",
                                              "可靠原生中段", "A. [插图待补: 图1]", "原生尾段"])
    figures = result["layout"]["figures"]
    assert [figure["slot_id"] for figure in figures] == ["p3-s1", "p3-s2"]
    assert [figure["bbox"] for figure in figures] == [candidate["bbox"] for candidate in setup.info["candidates"]]
    assert figures[0]["native_box"] is True and figures[0]["model_bbox"] is None
    assert figures[1]["native_box"] is False and figures[1]["model_bbox"] == [180, 640, 340, 760]
    assert result["usage"] == setup.response["usage"]  # Do not invent token reconciliation.
    assert result["model"] == setup.provider.model_name
    assert result["extraction_mode"] == "native_regions" and result["region_count"] == 2
    assert result["image_area_ratio"] == pytest.approx(0.32)
    checked, warnings = _validate_layout(result["layout"], {
        **setup.info, "figure_slots": describe_figure_slots(result["markdown"], 2)})
    assert len(checked) == 2 and not warnings
    sent = setup.calls[0][1]
    assert sent["max_tokens"] == vision.MAX_OUTPUT_TOKENS
    assert "PRIVATE_NATIVE" not in json.dumps(sent)
    assert list(setup.root.iterdir()) == [setup.root / "page.png"]


def test_named_region_rectangle_is_normalized_before_page_transform_and_preserves_raw_space(setup):
    data = deepcopy(setup.data)
    named = {"bottom": 800, "right": 300, "top": 200, "left": 100}
    data["regions"][1]["figures"][0]["bbox"] = named
    result = call(setup, data)
    figure = result["layout"]["figures"][1]
    assert figure["bbox"] == figure["model_bbox"] == [180, 640, 340, 760]
    assert figure["model_bbox_raw"] == named and figure["model_bbox_space"] == "region"
    assert figure["model_region_bbox"] == [100, 600, 900, 800] and figure["slot_id"] == "p3-s2"


@pytest.mark.parametrize("box", [
    {"top": 200, "left": 100, "right": 300},
    {"top": 200, "left": -20, "right": 300, "bottom": 800},
    {"top": 200, "left": 100, "right": 300, "bottom": float("inf")},
    {"top": 200, "left": 100, "right": "300", "bottom": 800},
])
def test_bad_named_region_geometry_preserves_other_region_and_text(setup, box):
    data = deepcopy(setup.data)
    data["regions"][1]["figures"][0]["bbox"] = box
    result = call(setup, data)
    assert result["markdown"].count("[插图待补: 图1]") == 2
    assert [figure["slot_id"] for figure in result["layout"]["figures"]] == ["p3-s1"]
    assert result["layout"]["page_complete"] is False and len(setup.calls) == 1


def test_only_bounded_crops_are_encoded_and_identified(setup):
    call(setup)
    content = setup.calls[0][1]["messages"][0]["content"]
    assert [item["text"] for item in content[1:] if item["type"] == "text"] == ["region_id=r1", "region_id=r2"]
    images = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
    assert len(images) == 2
    for url, color in zip(images, [(255, 0, 0), (0, 0, 255)]):
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert image.size == (800, 200)
            assert image.getpixel((400, 100)) == color


def test_model_result_order_cannot_reorder_native_merge(setup):
    data = deepcopy(setup.data)
    data["regions"].reverse()
    data["regions"][0]["markdown"] = "第二片 [插图待补: 图1]"
    data["regions"][1]["markdown"] = "第一片 [插图待补: 图1]"
    result = call(setup, data)
    assert result["markdown"].index("第一片") < result["markdown"].index("可靠原生中段") < result["markdown"].index("第二片")
    assert [f["candidate_ids"] for f in result["layout"]["figures"]] == [["p3_raster_001"], ["p3_vector_001"]]


def test_tight_image_only_region_keeps_complete_native_box(setup):
    setup.plan["kind"] = "image_only"
    setup.info["candidates"] = setup.info["candidates"][:1]
    region = setup.plan["regions"][0]
    region["bbox"] = setup.info["candidates"][0]["bbox"][:]
    region["page_info"]["candidates"][0]["bbox"] = [0, 0, 1000, 1000]
    setup.plan["regions"] = [region]
    setup.plan["pieces"] = [{"region_id": "r1"}]
    data = {"regions": [region_result("r1", bbox=[0, 0, 1000, 1000])]}
    result = call(setup, data)
    figure = result["layout"]["figures"][0]
    assert figure["bbox"] == setup.info["candidates"][0]["bbox"]
    assert figure["model_bbox"] == figure["bbox"] and figure["native_box"] is True
    assert result["extraction_mode"] == "image_regions" and result["native_characters"] == 0


def test_region_abcd_figures_bind_by_slots_not_candidate_order(setup):
    setup.info["candidates"] = []
    setup.plan["regions"] = [setup.plan["regions"][0]]
    region = setup.plan["regions"][0]
    region["page_info"]["candidates"] = []
    setup.plan["pieces"] = [{"text": "题干"}, {"region_id": "r1"}, {"text": "下一题"}]
    boxes = [[20, 100, 200, 800], [260, 100, 440, 800], [500, 100, 680, 800], [740, 100, 920, 800]]
    ids = ["a", "b", "d", "c"]
    for identifier, box in zip(ids, boxes):
        region["page_info"]["candidates"].append({"id": identifier, "type": "raster", "bbox": box})
        setup.info["candidates"].append({"id": identifier, "type": "raster", "bbox": vision._to_page(box, region["bbox"])})
    data = region_result("r1")
    data["markdown"] = "\\begin{choices}\n" + "\n".join(f"\\item [插图待补: 图{i}]" for i in range(1, 5)) + "\n\\end{choices}"
    data["figures"] = [{"slot": f"图{index}", "bbox": None, "candidate_ids": [identifier],
                        "review_required": False, "review_reason": ""} for index, identifier in enumerate("abcd", 1)]
    result = call(setup, {"regions": [data]})
    assert [f["candidate_ids"][0] for f in result["layout"]["figures"]] == list("abcd")
    assert [f["slot_id"] for f in result["layout"]["figures"]] == [f"p3-s{i}" for i in range(1, 5)]
    assert data["markdown"] in result["markdown"]


@pytest.mark.parametrize("malformation", ["missing", "duplicate", "unknown", "extra", "not_list", "not_object", "bad_id"])
def test_region_envelope_must_be_complete_and_unique(setup, malformation):
    data = deepcopy(setup.data)
    if malformation == "missing": data["regions"].pop()
    elif malformation == "duplicate": data["regions"][1] = deepcopy(data["regions"][0])
    elif malformation == "unknown": data["regions"][1]["id"] = "r99"
    elif malformation == "extra": data["regions"][0]["native_text"] = "replace local source"
    elif malformation == "not_list": data["regions"] = {}
    elif malformation == "not_object": data = [data]
    elif malformation == "bad_id": data["regions"][0]["id"] = []
    with pytest.raises(ValueError):
        call(setup, data)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("text", ["", "   ", None, 4, "![image](/static/uploads/model.png)", "<img src='bad'>", "file:///private/secret"])
def test_bad_transcript_rejects_entire_merge_without_retry(setup, text):
    data = deepcopy(setup.data)
    data["regions"][0]["markdown"] = text
    with pytest.raises(ValueError):
        call(setup, data)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("change", ["unknown_candidate", "other_region_candidate", "invalid_box", "outside_crop", "duplicate_slot", "missing_slot"])
def test_bad_figure_layout_keeps_local_text_and_other_regions(setup, change):
    data = deepcopy(setup.data)
    figure = data["regions"][1]["figures"][0]
    if change == "unknown_candidate": figure["candidate_ids"] = ["unknown"]
    elif change == "other_region_candidate": figure["candidate_ids"] = ["p3_raster_001"]
    elif change == "invalid_box": figure["bbox"] = [0, 0, float("nan"), 300]
    elif change == "outside_crop": figure["bbox"] = [-20, 0, 1000, 900]
    elif change == "duplicate_slot": data["regions"][1]["figures"] *= 2
    elif change == "missing_slot": figure["slot"] = "图9"
    result = call(setup, data)
    assert [f["slot_id"] for f in result["layout"]["figures"]] == ["p3-s1"]
    assert result["layout"]["page_complete"] is False
    assert all("区域 r2" in note for note in result["layout"]["notes"])
    assert result["markdown"].count("[插图待补: 图1]") == 2
    assert len(setup.calls) == 1


def test_shared_raster_null_box_cannot_claim_complete_native_box(setup):
    data = deepcopy(setup.data)
    data["regions"][0]["markdown"] += "\n[插图待补: 图2]"
    data["regions"][0]["figures"].append({**deepcopy(data["regions"][0]["figures"][0]), "slot": "图2"})
    result = call(setup, data)
    assert [f["slot_id"] for f in result["layout"]["figures"]] == ["p3-s3"]
    assert result["layout"]["page_complete"] is False


def test_text_only_mode_omits_candidate_task_and_keeps_native_text(setup):
    data = {"regions": [{"id": "r1", "markdown": "$x=1$ [插图待补: 图1]"},
                        {"id": "r2", "markdown": "$y=2$"}]}
    result = call(setup, data, include_figures=False)
    assert result["markdown"].startswith(setup.plan["pieces"][0]["text"])
    assert "$x=1$ [插图待补: 图1]" in result["markdown"]
    assert result["layout"]["figures"] == []
    sent = setup.calls[0][1]["messages"][0]["content"][0]["text"]
    assert "candidate_ids" not in sent and "p3_raster_001" not in sent and '"bbox"' not in sent
    assert "PRIVATE_NATIVE" not in sent
    assert len(setup.calls) == 1


def test_text_only_mode_rejects_unrequested_layout_fields(setup):
    with pytest.raises(ValueError):
        call(setup, include_figures=False)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("change", ["duplicate_region", "missing_piece", "duplicate_piece", "unknown_piece", "native_slot",
                                    "invalid_bbox", "overlap", "unknown_candidate", "duplicate_candidate", "missing_candidate",
                                    "clipped_candidate", "wrong_page"])
def test_invalid_local_plan_rejected_before_model_call(setup, change):
    if change == "duplicate_region": setup.plan["regions"][1]["id"] = "r1"
    elif change == "missing_piece": setup.plan["pieces"].pop(1)
    elif change == "duplicate_piece": setup.plan["pieces"].append({"region_id": "r1"})
    elif change == "unknown_piece": setup.plan["pieces"][1] = {"region_id": "r99"}
    elif change == "native_slot": setup.plan["pieces"][0]["text"] += "[插图待补: 图1]"
    elif change == "invalid_bbox": setup.plan["regions"][0]["bbox"] = [0, 0, True, 1000]
    elif change == "overlap": setup.plan["regions"][1]["bbox"] = [100, 200, 900, 800]
    elif change == "unknown_candidate": setup.plan["regions"][0]["page_info"]["candidates"][0]["id"] = "unknown"
    elif change == "duplicate_candidate": setup.plan["regions"][1]["page_info"]["candidates"] = setup.plan["regions"][0]["page_info"]["candidates"]
    elif change == "missing_candidate": setup.plan["regions"][1]["page_info"]["candidates"] = []
    elif change == "clipped_candidate": setup.plan["regions"][0]["page_info"]["candidates"][0]["bbox"][2] = 250
    elif change == "wrong_page": setup.plan["regions"][0]["page_info"]["page_index"] = 1
    with pytest.raises(ValueError):
        call(setup)
    assert not setup.calls


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", None])
def test_truncation_rejected_even_with_complete_json(setup, finish):
    setup.response["choices"][0]["finish_reason"] = finish
    with pytest.raises(ValueError, match="未正常结束|截断"):
        call(setup)
    assert len(setup.calls) == 1


@pytest.mark.parametrize("before_post", [True, False])
def test_cancellation_is_not_swallowed_or_retried(setup, before_post):
    def cancel():
        if before_post or setup.calls:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        call(setup, check_cancelled=cancel)
    assert len(setup.calls) == (0 if before_post else 1)


@pytest.mark.parametrize("failure", [requests.ReadTimeout, requests.ConnectionError])
def test_network_error_never_retried_or_echoed(setup, monkeypatch, failure):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise failure("secret provider/key")
    monkeypatch.setattr(vision, "post_chat_completion", fail)
    with pytest.raises(ValueError, match="未自动重试") as caught:
        call(setup)
    assert "secret" not in str(caught.value) and calls == [1]


def test_invalid_json_and_http_error_are_not_retried(setup, monkeypatch):
    setup.response["choices"][0]["message"]["content"] = "not JSON secret"
    with pytest.raises(ValueError, match="结构化") as caught:
        call(setup)
    assert "secret" not in str(caught.value) and len(setup.calls) == 1
    monkeypatch.setattr(vision, "post_chat_completion", lambda *a, **kw: SimpleNamespace(
        status_code=503, json=lambda: pytest.fail("Must not read failed HTTP response")))
    with pytest.raises(ValueError, match="HTTP 503"):
        call(setup)


def test_usage_only_contains_actual_nonnegative_provider_counts(setup, monkeypatch):
    setup.response["usage"] = {"prompt_tokens": True, "completion_tokens": -2, "total_tokens": 100, "estimated": 42}
    def policy(payload, *, provider, task):
        assert task == "ocr" and payload["max_tokens"] == vision.MAX_OUTPUT_TOKENS
        payload = dict(payload)
        payload.pop("max_tokens")
        payload["max_completion_tokens"] = 15000
        return payload
    monkeypatch.setattr(vision, "apply_model_thinking_policy", policy)
    result = call(setup)
    assert result["usage"] == {"total_tokens": 100}
    assert setup.calls[0][1]["max_completion_tokens"] == 15000


def test_missing_usage_is_unknown_not_an_estimate(setup):
    del setup.response["usage"]
    assert call(setup)["usage"] == {}


@pytest.mark.parametrize("change", ["key", "image_support", "image_file"])
def test_unavailable_provider_or_source_never_calls_model(setup, monkeypatch, change):
    if change == "image_file":
        setup.image = str(setup.root / "missing.png")
    else:
        provider = SimpleNamespace(api_key="unused", chat_completions_url="https://example.test",
                                   supports_image_input=True)
        if change == "key": provider.api_key = ""
        else: provider.supports_image_input = False
        monkeypatch.setattr(vision, "resolve_ocr_provider", lambda engine: provider)
    with pytest.raises(ValueError):
        call(setup)
    assert not setup.calls


def test_combined_source_cap_cannot_be_bypassed_by_multiple_regions(setup, monkeypatch):
    monkeypatch.setattr(vision, "MAX_SOURCE_CHARS", 45)
    with pytest.raises(ValueError, match="正文过长"):
        call(setup)
    assert len(setup.calls) == 1
