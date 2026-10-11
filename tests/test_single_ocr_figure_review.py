"""Actual-pixel review identity, bounded correction and fail-closed delivery."""

import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest
import requests

from mathbank.ai_providers import resolve_ocr_provider
from mathbank.ocr_figure_review import review_single_ocr_figures, review_tikz_previews
from mathbank.ocr_illustrations import materialize_ocr_illustrations, parse_single_ocr_response


BOX = {"left": 100, "top": 100, "right": 500, "bottom": 700}
CHECKS = {name: True for name in ("same_figure", "full_graphic", "labels_complete", "no_foreign_content", "correct_anchor")}
CODE = r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "source.png"
    image = Image.new("RGB", (500, 400), "white")
    ImageDraw.Draw(image).rectangle((55, 45, 245, 275), outline="black", width=3)
    image.save(path)
    provider = resolve_ocr_provider("zhongzhan_gpt", {"ZHONGZHAN_GPT_API_KEY": "test-only",
        "ZHONGZHAN_GPT_BASE_URL": "https://example.test/v1", "ZHONGZHAN_GPT_OCR_MODEL": "gpt-6-astra:medium"})
    return tmp_path, path, image, provider


def result(bbox=BOX):
    return parse_single_ocr_response({"markdown": r"题干\begin{choices}\item [插图待补: 图1]\item B\end{choices}后文",
        "figures": [{"slot": "图1", "bbox": bbox}]})


def metadata(payload):
    user = next(message for message in payload["messages"] if message["role"] == "user")
    text = user["content"][0]["text"]
    return json.loads(text.split("\n输入资料JSON：\n", 1)[1]), user["content"]


def accepted(record, *, preview=False):
    return {**{key: record[key] for key in ("id", "slot", "source_sha256", "crop_sha256")},
            "decision": "accepted", "checks": {**CHECKS, **({"geometry_faithful": True} if preview else {})},
            "corrected_bbox": None, "evidence": "图形四边、字母与原图对应，未混入邻文。"}


def post_for(callback=None):
    calls = []
    def post(_provider, payload, **kwargs):
        data, content = metadata(payload)
        assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
        calls.append((data, content))
        entries = callback(data, content) if callback else [accepted(record, preview=data["stage"] == "tikz_preview") for record in data["figures"]]
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"figures": entries}, ensure_ascii=False)}}]})
    return post, calls


def deliver(reviewed, image, root, *, tikz=False):
    def compile_one(_code):
        path = root / "tikz.png"
        Image.new("RGB", (80, 80), "white").save(path)
        return "/static/uploads/tikz.png"
    return materialize_ocr_illustrations(reviewed, image, skip_tikz=not tikz, upload_root=root,
        url_prefix="static/uploads", draw=lambda *_args: CODE, compile_tikz=compile_one,
        require_review=True, verified_crops=reviewed["_verified_crops"])


def test_verified_crop_delivers_exact_reviewed_png_bytes_and_preserves_original_slot(setup):
    root, path, image, provider = setup
    post, calls = post_for()
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    assert reviewed["illustration_review"]["calls"] == 1
    record = calls[0][0]["figures"][0]
    pngs = [base64.b64decode(item["image_url"]["url"].split(",", 1)[1]) for item in calls[0][1] if item["type"] == "image_url"]
    assert hashlib.sha256(pngs[0]).hexdigest() == record["source_sha256"]
    assert hashlib.sha256(pngs[1]).hexdigest() == record["crop_sha256"]
    output = deliver(reviewed, image, root)
    item = output["illustrations"][0]
    assert item["status"] == "cropped" and item["review"]["status"] == "verified"
    actual = root / Path(item["image_path"]).name
    assert actual.read_bytes() == pngs[1]
    assert r"\item ![](" in output["markdown"] and output["markdown"].endswith(r"\end{choices}后文")


@pytest.mark.parametrize("initial_box", [BOX, None])
def test_bbox_proposal_requires_second_review_of_actual_corrected_pixels(setup, initial_box):
    root, path, image, provider = setup
    corrected = {"left": 95, "top": 95, "right": 530, "bottom": 710}
    def respond(data, _content):
        entry = accepted(data["figures"][0])
        if data["stage"] == "crop_initial":
            entry.update(decision="correct", corrected_bbox=corrected,
                         checks={**CHECKS, "labels_complete": False}, evidence="右侧字母被切掉，需要扩展右边界。")
        return [entry]
    post, calls = post_for(respond)
    reviewed = review_single_ocr_figures(result(initial_box), path, provider, post=post)
    assert [call[0]["stage"] for call in calls] == ["crop_initial", "crop_corrected"]
    assert calls[1][0]["figures"][0]["crop_sha256"] != calls[0][0]["figures"][0]["crop_sha256"]
    assert reviewed["illustrations"][0]["bbox"] == corrected
    assert reviewed["illustrations"][0]["review"]["corrected"] is True
    assert not any("边界尚需核对" in message for message in reviewed["warnings"])
    output = deliver(reviewed, image, root)
    assert output["illustrations"][0]["status"] == "cropped"


def test_rejected_corrected_actual_crop_never_becomes_verified_or_gets_materialized(setup):
    root, path, image, provider = setup
    def respond(data, _content):
        entry = accepted(data["figures"][0])
        if data["stage"] == "crop_initial":
            entry.update(decision="correct", corrected_bbox={**BOX, "right": 530})
        else:
            entry["checks"]["labels_complete"] = False
        return [entry]
    post, calls = post_for(respond)
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    output = deliver(reviewed, image, root)
    assert len(calls) == 2 and "[插图待补: 图1]" in output["markdown"]
    assert output["illustrations"][0]["review"]["status"] != "verified"
    assert not list(root.glob("ocr_figure_*"))


@pytest.mark.parametrize("mutation", ["hash", "id", "duplicate", "check", "missing"])
def test_malformed_or_unaccepted_review_is_fail_closed_without_retry(setup, mutation):
    root, path, image, provider = setup
    def respond(data, _content):
        entry = accepted(data["figures"][0])
        if mutation == "hash": entry["crop_sha256"] = "0" * 64
        if mutation == "id": entry["id"] = "other_figure"
        if mutation == "check": entry["checks"]["same_figure"] = False
        if mutation == "missing": return []
        return [entry, entry] if mutation == "duplicate" else [entry]
    post, calls = post_for(respond)
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    assert len(calls) == 1 and reviewed["_verified_crops"] == {}
    assert "[插图待补: 图1]" in deliver(reviewed, image, root)["markdown"]


def test_timeout_does_not_retry_or_leak_private_provider_error(setup):
    root, path, image, provider = setup
    calls = []
    def post(*_args, **_kwargs):
        calls.append(1)
        raise requests.ReadTimeout("secret-key private-prompt")
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    assert calls == [1] and reviewed["illustration_review"]["calls"] == 1
    assert "secret-key" not in " ".join(reviewed["warnings"])
    assert "[插图待补: 图1]" in deliver(reviewed, image, root)["markdown"]


def test_no_figures_or_duplicate_source_slots_never_send_review(setup):
    _root, path, _image, provider = setup
    for source in ({"markdown": "纯文字 $x=1$", "figures": []},
                   {"markdown": "[插图待补: 图1]及[插图待补: 图1]", "figures": [{"slot": "图1", "bbox": BOX}]}):
        reviewed = review_single_ocr_figures(parse_single_ocr_response(source), path, provider,
                                            post=lambda *_args, **_kwargs: pytest.fail("no POST"))
        assert reviewed["illustration_review"]["calls"] == 0


def test_local_growth_budget_rejects_proposal_without_second_paid_review(setup):
    _root, path, _image, provider = setup
    def respond(data, _content):
        entry = accepted(data["figures"][0])
        entry.update(decision="correct", corrected_bbox={"left": 0, "top": 0, "right": 950, "bottom": 800})
        return [entry]
    post, calls = post_for(respond)
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    assert len(calls) == 1 and reviewed["_verified_crops"] == {}


def test_changed_original_during_review_cannot_verify_its_crop(setup):
    _root, path, _image, provider = setup
    def respond(data, _content):
        entries = [accepted(record) for record in data["figures"]]
        Image.new("RGB", (500, 400), "red").save(path)
        return entries
    post, _calls = post_for(respond)
    assert review_single_ocr_figures(result(), path, provider, post=post)["_verified_crops"] == {}


def test_actual_delivery_pixel_hash_and_private_server_proof_are_required(setup):
    root, path, image, provider = setup
    post, _calls = post_for()
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    bad_image = image.copy()
    ImageDraw.Draw(bad_image).rectangle((55, 45, 245, 275), fill="red")
    assert "[插图待补: 图1]" in deliver(reviewed, bad_image, root)["markdown"]
    reviewed["_verified_crops"] = {}
    assert "[插图待补: 图1]" in deliver(reviewed, image, root)["markdown"]
    assert not list(root.glob("ocr_figure_*"))


@pytest.mark.parametrize("accepted_preview", [True, False])
def test_tikz_actual_preview_is_verified_or_falls_back_to_verified_source_crop(setup, accepted_preview):
    root, path, image, provider = setup
    post, _calls = post_for()
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    generated = deliver(reviewed, image, root, tikz=True)
    reference = generated["illustrations"][0]["reference_image_path"]
    def respond(data, _content):
        entry = accepted(data["figures"][0], preview=True)
        entry["checks"]["geometry_faithful"] = accepted_preview
        return [entry]
    preview_post, calls = post_for(respond)
    output = review_tikz_previews(generated, provider, upload_root=root, url_prefix="static/uploads",
                                 summary=reviewed["illustration_review"], post=preview_post)
    assert len(calls) == 1 and calls[0][0]["stage"] == "tikz_preview"
    item = output["illustrations"][0]
    if accepted_preview:
        assert item["status"] == "tikz" and item["preview_review"]["status"] == "verified"
        assert len(output["tikz_assets"]) == 1
    else:
        assert item["status"] == "cropped" and item["image_path"] == reference
        assert output["tikz_assets"] == [] and reference in output["image_layouts"]
        assert "tikz.png" not in output["markdown"]


@pytest.mark.parametrize("changed", ["preview", "source"])
def test_changed_file_during_preview_review_cannot_be_returned_as_verified(setup, changed):
    root, path, image, provider = setup
    post, _calls = post_for()
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    generated = deliver(reviewed, image, root, tikz=True)
    item = generated["illustrations"][0]
    def respond(data, _content):
        target = root / Path(item["image_path"] if changed == "preview" else item["reference_image_path"]).name
        Image.new("RGB", (80, 80), "red").save(target)
        return [accepted(data["figures"][0], preview=True)]
    preview_post, _calls = post_for(respond)
    output = review_tikz_previews(generated, provider, upload_root=root, url_prefix="static/uploads",
                                 summary=reviewed["illustration_review"], post=preview_post)
    assert output["illustrations"][0]["status"] == ("cropped" if changed == "preview" else "unresolved")
    assert output["tikz_assets"] == [] and output["illustrations"][0]["preview_review"]["status"] != "verified"


def test_preflight_pixel_limit_counts_no_actual_post(setup, monkeypatch):
    _root, path, _image, provider = setup
    monkeypatch.setattr("mathbank.ocr_figure_review.MAX_REVIEW_PIXELS", 1)
    reviewed = review_single_ocr_figures(result(), path, provider, post=lambda *_args, **_kwargs: pytest.fail("no POST"))
    assert reviewed["illustration_review"]["calls"] == 0 and reviewed["_verified_crops"] == {}


def test_failed_second_batch_cannot_publish_first_batch_proof_after_source_changes(setup):
    _root, path, _image, provider = setup
    parsed = parse_single_ocr_response({"markdown": "[插图待补: 图1]与[插图待补: 图2]", "figures": [
        {"slot": "图1", "bbox": BOX}, {"slot": "图2", "bbox": {"left": 600, "top": 100, "right": 900, "bottom": 700}}]})
    def respond(data, _content):
        if data["stage"] == "crop_corrected":
            Image.new("RGB", (500, 400), "red").save(path)
            raise requests.ReadTimeout("private")
        entries = [accepted(record) for record in data["figures"]]
        entries[1].update(decision="correct", corrected_bbox={"left": 590, "top": 100, "right": 910, "bottom": 700})
        return entries
    post, calls = post_for(respond)
    reviewed = review_single_ocr_figures(parsed, path, provider, post=post)
    assert len(calls) == 2 and reviewed["_verified_crops"] == {}
    assert all(item["review"]["status"] != "verified" for item in reviewed["illustrations"])


def test_review_provider_json_system_instruction_is_for_comparison_not_transcription(setup):
    _root, path, _image, _provider = setup
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "test-only",
        "SILICONFLOW_OCR_MODEL": "Qwen/Qwen3.8-27B"})
    observed = []
    normal_post, _calls = post_for()
    def post(actual_provider, payload, **kwargs):
        observed.append(payload)
        return normal_post(actual_provider, payload, **kwargs)
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    assert reviewed["illustration_review"]["verified"] == 1
    payload = observed[0]
    assert payload["response_format"] == {"type": "json_object"} and payload["enable_thinking"] is False
    assert payload["messages"][0]["role"] == "system"
    system = payload["messages"][0]["content"]
    assert "独立核验原图与实际裁图或TikZ预览" in system and "禁止转录" in system
    assert "执行单题图文转录" not in system
    user = payload["messages"][1]["content"][0]["text"]
    assert '"decision"' in user and "source_sha256" in user and "corrected_bbox" in user


def test_actual_source_context_and_dimensions_are_review_only_not_delivered(setup):
    root, path, image, provider = setup
    post, calls = post_for()
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    record = calls[0][0]["figures"][0]
    assert record["source_dimensions"] == {"width": 500, "height": 400}
    assert record["bbox_pixels"] == {"left": 50, "top": 40, "right": 250, "bottom": 280}
    assert record["crop_dimensions"] == {"width": 200, "height": 240}
    pictures = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) for part in calls[0][1] if part["type"] == "image_url"]
    context = record["source_context"]
    assert len(pictures) == 3 and hashlib.sha256(pictures[2]).hexdigest() == context["sha256"]
    assert context["bbox_pixels"]["left"] < record["bbox_pixels"]["left"]
    output = deliver(reviewed, image, root)
    delivered = (root / Path(output["illustrations"][0]["image_path"]).name).read_bytes()
    assert delivered == pictures[1] and delivered != pictures[2]


@pytest.mark.parametrize("status,code", [(401, "http_401"), (429, "http_429"), (503, "http_5xx")])
def test_safe_http_failure_diagnostics_do_not_mislabel_protocol_or_expose_response_body(setup, capsys, status, code):
    _root, path, _image, provider = setup
    def post(*_args, **_kwargs):
        return SimpleNamespace(status_code=status, json=lambda: pytest.fail("error body must not be read"))
    reviewed = review_single_ocr_figures(result(), path, provider, post=post)
    assert reviewed["illustrations"][0]["review"]["failure_code"] == code
    assert reviewed["illustration_review"]["failures"][0]["failure_code"] == code
    logged = capsys.readouterr().out
    assert code in logged and "test-only" not in logged and "example.test" not in logged and "题干" not in logged


def test_timeout_check_failure_and_rejected_box_have_distinct_diagnostics(setup):
    _root, path, _image, provider = setup
    timed = review_single_ocr_figures(result(), path, provider, post=lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("private")))
    assert timed["illustrations"][0]["review"]["failure_code"] == "timeout"
    def incomplete(data, _content):
        entry = accepted(data["figures"][0]); entry["checks"]["labels_complete"] = False
        return [entry]
    post, _calls = post_for(incomplete)
    checked = review_single_ocr_figures(result(), path, provider, post=post)
    assert checked["illustrations"][0]["review"]["failure_code"] == "checks_failed"
    assert checked["illustrations"][0]["review"]["failed_checks"] == ["labels_complete"]
    def far_box(data, _content):
        entry = accepted(data["figures"][0]); entry.update(decision="correct", corrected_bbox={"left": 0, "top": 0, "right": 950, "bottom": 800})
        return [entry]
    post, calls = post_for(far_box)
    rejected = review_single_ocr_figures(result(), path, provider, post=post)
    assert len(calls) == 1 and rejected["illustrations"][0]["review"]["failure_code"] == "bbox_rejected"


def test_ultrawide_complete_rectangle_correction_still_requires_actual_second_review(setup):
    root, path, _image, provider = setup
    image = Image.new("RGB", (1009, 157), "white")
    ImageDraw.Draw(image).rectangle((320, 70, 473, 137), outline="black", width=3)
    image.save(path)
    initial = {"left": 325, "top": 376, "right": 495, "bottom": 784}
    full = {"left": 311.199207, "top": 407.643312, "right": 475.718533, "bottom": 917.197452}
    def respond(data, _content):
        entry = accepted(data["figures"][0])
        if data["stage"] == "crop_initial":
            entry.update(decision="correct", corrected_bbox=full, checks={**CHECKS, "full_graphic": False})
        return [entry]
    post, calls = post_for(respond)
    reviewed = review_single_ocr_figures(result(initial), path, provider, post=post)
    assert len(calls) == 2 and reviewed["illustrations"][0]["bbox"] == full
    record = calls[0][0]["figures"][0]
    assert record["source_dimensions"] == {"width": 1009, "height": 157}
    assert len(record["raster_candidates"]) == 1
    assert record["source_context"]["bbox_pixels"]["top"] == 0 and record["source_context"]["bbox_pixels"]["bottom"] == 157
    assert deliver(reviewed, image, root)["illustrations"][0]["review"]["status"] == "verified"
