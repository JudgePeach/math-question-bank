"""Transcribe bounded PDF crops once and preserve reliable native text locally.

The planner owns text order and crop bounds. The model can fill only region
slots, never replace native pieces. Geometry is converted back to page space
before entering the existing figure validation and crop pipeline.
"""

from __future__ import annotations

import base64
from io import BytesIO
import math
import os
import re
from typing import Callable

from PIL import Image

from mathbank import prompts
from mathbank.ai_http import post_chat_completion
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.pdf_figures import MAX_FIGURES_PER_PAGE, MAX_SOURCE_CHARS, describe_figure_slots
from mathbank.pdf_layout import normalize_model_bbox
from mathbank.pdf_page_vision import (
    MAX_OUTPUT_TOKENS, MAX_RESPONSE_CHARS, _PAGE_FIELDS, _SLOT_LABEL, _finite_number,
    _no_cancel, _prompt_page_info, _require_fields, _validated_page,
)
from mathbank.task_manager import TaskCancelled


MAX_REGIONS = 3
_REGION_ID = re.compile(r"r[1-9][0-9]{0,2}\Z")


def _page_bbox(bbox, label: str) -> list[float]:
    if (not isinstance(bbox, list) or len(bbox) != 4
            or any(not _finite_number(value) for value in bbox)):
        raise ValueError(f"PDF 局部识别的{label}坐标格式无效。")
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        raise ValueError(f"PDF 局部识别的{label}坐标越界。")
    return list(bbox)


def _to_page(bbox: list, crop: list) -> list:
    x0, y0, x1, y1 = crop
    return [x0 + bbox[0] * (x1 - x0) / 1000,
            y0 + bbox[1] * (y1 - y0) / 1000,
            x0 + bbox[2] * (x1 - x0) / 1000,
            y0 + bbox[3] * (y1 - y0) / 1000]


def _validated_plan(page_info: dict, plan: dict) -> tuple[list[dict], list[dict]]:
    clean_page = _prompt_page_info(page_info)
    if not isinstance(plan, dict) or plan.get("kind") not in {"mixed", "image_only"}:
        raise ValueError("PDF 局部识别计划格式无效。")
    regions, pieces = plan.get("regions"), plan.get("pieces")
    if not isinstance(regions, list) or not 1 <= len(regions) <= MAX_REGIONS:
        raise ValueError("PDF 局部识别区域数量无效。")
    if not isinstance(pieces, list) or not pieces:
        raise ValueError("PDF 局部识别缺少本地拼接顺序。")
    original_candidates = {item["id"]: item for item in clean_page["candidates"]}
    candidate_owners, clean_regions = set(), []
    seen = set()
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("PDF 局部识别区域格式无效。")
        identifier = region.get("id")
        if not isinstance(identifier, str) or not _REGION_ID.fullmatch(identifier) or identifier in seen:
            raise ValueError("PDF 局部识别区域标识无效或重复。")
        seen.add(identifier)
        bbox = _page_bbox(region.get("bbox"), "区域")
        if any(min(bbox[2], other["bbox"][2]) > max(bbox[0], other["bbox"][0])
               and min(bbox[3], other["bbox"][3]) > max(bbox[1], other["bbox"][1])
               for other in clean_regions):
            raise ValueError("PDF 局部识别区域重叠，可能重复转录原文。")
        info = _prompt_page_info(region.get("page_info"))
        if info["page_index"] != clean_page["page_index"]:
            raise ValueError("PDF 局部识别区域页码不一致。")
        for candidate in info["candidates"]:
            candidate_id = candidate["id"]
            original = original_candidates.get(candidate_id)
            if original is None or candidate_id in candidate_owners:
                raise ValueError("PDF 局部识别候选区域未知或跨区域重复。")
            if candidate.get("type") != original.get("type"):
                raise ValueError("PDF 局部识别候选区域类型不一致。")
            local_box = _page_bbox(candidate.get("bbox"), "候选")
            original_box = _page_bbox(original.get("bbox"), "原生候选")
            if any(abs(a - b) > 1 for a, b in zip(_to_page(local_box, bbox), original_box)):
                raise ValueError("PDF 局部识别未保留候选的完整原生坐标。")
            candidate_owners.add(candidate_id)
        clean_regions.append({"id": identifier, "bbox": bbox, "page_info": info})
    if candidate_owners != set(original_candidates):
        raise ValueError("PDF 局部识别计划遗漏原页候选区域。")
    references = []
    clean_pieces = []
    native_count = 0
    for piece in pieces:
        if not isinstance(piece, dict):
            raise ValueError("PDF 局部识别拼接内容格式无效。")
        if set(piece) == {"text"} and isinstance(piece["text"], str):
            if "[插图待补" in piece["text"]:
                raise ValueError("PDF 原生文字包含未分配的插图占位。")
            native_count += len(piece["text"])
            clean_pieces.append(dict(piece))
        elif set(piece) == {"region_id"} and isinstance(piece["region_id"], str):
            references.append(piece["region_id"])
            clean_pieces.append(dict(piece))
        else:
            raise ValueError("PDF 局部识别拼接内容格式无效。")
    if len(references) != len(seen) or set(references) != seen:
        raise ValueError("PDF 局部识别拼接区域缺失、重复或未知。")
    if native_count > MAX_SOURCE_CHARS:
        raise ValueError("PDF 局部识别的原生文字过长。")
    return clean_regions, clean_pieces


def _crop_messages(image_path: str, regions: list[dict], check_cancelled: Callable[[], None]) -> list[dict]:
    messages = []
    try:
        with Image.open(image_path) as source:
            source.load()
            width, height = source.size
            for region in regions:
                check_cancelled()
                bbox = region["bbox"]
                extent = (bbox[0] * width / 1000, bbox[1] * height / 1000,
                          bbox[2] * width / 1000, bbox[3] * height / 1000)
                # EXTENT preserves continuous planner coordinates; integer crop
                # rounding would subtly shift all subsequent figure boxes.
                size = (max(1, math.ceil(extent[2] - extent[0])),
                        max(1, math.ceil(extent[3] - extent[1])))
                with source.transform(size, Image.Transform.EXTENT, extent, Image.Resampling.BICUBIC) as crop:
                    with BytesIO() as buffer:
                        crop.save(buffer, format="PNG")
                        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                messages.extend([
                    {"type": "text", "text": "region_id=" + region["id"]},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                ])
    except TaskCancelled:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取或裁剪 PDF 局部预览图。") from exc
    return messages


def _merged_result(parsed, page_info: dict, regions: list[dict], pieces: list[dict], *, include_figures: bool) -> dict:
    _require_fields(parsed, {"regions"}, "局部结果")
    returned = parsed["regions"]
    if not isinstance(returned, list) or len(returned) != len(regions):
        raise ValueError("PDF 局部识别返回的区域数量不完整。")
    known = {region["id"]: region for region in regions}
    original_candidates = {candidate["id"]: candidate for candidate in page_info.get("candidates", [])}
    results = {}
    for value in returned:
        fields = {"id", *_PAGE_FIELDS} if include_figures else {"id", "markdown"}
        _require_fields(value, fields, "区域")
        identifier = value["id"]
        if not isinstance(identifier, str) or identifier not in known or identifier in results:
            raise ValueError("PDF 局部识别返回了重复或未知区域。")
        data = {key: value[key] for key in _PAGE_FIELDS} if include_figures else {
            "markdown": value["markdown"], "figures": [], "ignored_candidates": [],
            "page_complete": True, "warnings": [],
        }
        region = known[identifier]
        invalid_local_slots = set()
        raw_boxes = {}
        if include_figures and isinstance(data["figures"], list):
            converted = []
            for figure in data["figures"]:
                if isinstance(figure, dict):
                    figure = dict(figure)
                    bbox = figure.get("bbox")
                    label = figure.get("slot")
                    if isinstance(label, str):
                        raw_boxes[label] = dict(bbox) if isinstance(bbox, dict) else list(bbox) if isinstance(bbox, list) else None
                    try:
                        bbox = normalize_model_bbox(bbox) if bbox is not None else None
                    except ValueError:
                        # Preserve the malformed original for _validated_page,
                        # which keeps the region's text and requests review.
                        pass
                    if (isinstance(bbox, list) and len(bbox) == 4
                            and all(_finite_number(value) for value in bbox)):
                        if not (0 <= bbox[0] < bbox[2] <= 1000 and 0 <= bbox[1] < bbox[3] <= 1000):
                            slot = figure.get("slot")
                            if isinstance(slot, str):
                                invalid_local_slots.add(slot)
                        figure["bbox"] = _to_page(bbox, region["bbox"])
                converted.append(figure)
            data["figures"] = converted
        # Validate in original page coordinates. A real figure may occupy an
        # entire tight crop, while the whole-page background guard still holds.
        info = {**region["page_info"], "candidates": [original_candidates[candidate["id"]]
                 for candidate in region["page_info"]["candidates"]]}
        result = _validated_page(data, info)
        local_slot_labels = {slot["id"]: match.group(1).strip()
                             for slot in describe_figure_slots(result["markdown"], page_info["page_index"])
                             if (match := _SLOT_LABEL.fullmatch(slot["label"]))}
        for figure in result["layout"]["figures"]:
            figure.update(model_bbox_raw=raw_boxes.get(local_slot_labels.get(figure["slot_id"])),
                          model_bbox_space="region", model_region_bbox=list(region["bbox"]))
        if invalid_local_slots:
            slots = describe_figure_slots(result["markdown"], page_info["page_index"])
            invalid_ids = {slot["id"] for slot in slots if (match := _SLOT_LABEL.fullmatch(slot["label"]))
                           and match.group(1).strip() in invalid_local_slots}
            if any(not figure["native_box"] and figure["slot_id"] in invalid_ids
                   for figure in result["layout"]["figures"]):
                result["layout"] = {"figures": [], "ignored_candidates": [], "page_complete": False,
                                    "notes": [*result["layout"]["notes"],
                                              "待核对：配图坐标超出所见局部裁片，已保留正文，请对照原页补图；未重新调用模型。"]}
        results[identifier] = result
    if set(results) != set(known):
        raise ValueError("PDF 局部识别遗漏区域，未接受不完整结果。")

    parts, region_offsets = [], {}
    offset = 0
    for piece in pieces:
        identifier = piece.get("region_id")
        text = results[identifier]["markdown"] if identifier else piece["text"]
        if parts:
            offset += 2
        if identifier:
            region_offsets[identifier] = offset
        parts.append(text)
        offset += len(text)
    markdown = "\n\n".join(parts)
    if len(markdown) > MAX_SOURCE_CHARS:
        raise ValueError("PDF 局部识别拼接后的正文过长。")
    if not include_figures:
        return {"markdown": markdown, "layout": {"figures": [], "ignored_candidates": [],
                "page_complete": False, "notes": []}}
    global_slots = describe_figure_slots(markdown, page_info["page_index"])
    if len(global_slots) > MAX_FIGURES_PER_PAGE:
        raise ValueError("PDF 局部识别拼接后的插图占位过多。")
    slots_by_position = {(slot["start"], slot["end"]): slot["id"] for slot in global_slots}
    figures, ignored, notes = [], [], []
    complete = True
    for piece in pieces:
        identifier = piece.get("region_id")
        if not identifier:
            continue
        result = results[identifier]
        local_slots = describe_figure_slots(result["markdown"], page_info["page_index"])
        offset = region_offsets[identifier]
        slot_map = {slot["id"]: slots_by_position[(offset + slot["start"], offset + slot["end"])]
                    for slot in local_slots}
        for figure in result["layout"]["figures"]:
            figures.append({**figure, "slot_id": slot_map[figure["slot_id"]]})
        ignored.extend(result["layout"]["ignored_candidates"])
        notes.extend("待核对：区域 " + identifier + "：" + note.removeprefix("待核对：")
                     for note in result["layout"]["notes"])
        complete = complete and result["layout"]["page_complete"]
    return {"markdown": markdown, "layout": {"figures": figures, "ignored_candidates": ignored,
            "page_complete": complete, "notes": notes}}


def request_pdf_regions(
    image_path: str, page_info: dict, plan: dict, *, include_figures: bool = True,
    check_cancelled: Callable[[], None] = _no_cancel,
) -> dict:
    """One request for all planned crops; malformed results never trigger retries."""
    check_cancelled()
    regions, pieces = _validated_plan(page_info, plan)
    provider = resolve_ocr_provider(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("PDF 局部识别所用的识图服务未配置。")
    if not provider.supports_image_input:
        raise ValueError("当前识图模型不支持 PDF 局部图片输入。")
    content = [{"type": "text", "text": prompts.build_pdf_region_vision_prompt(regions, include_figures=include_figures)}]
    content.extend(_crop_messages(image_path, regions, check_cancelled))
    payload = {"model": provider.model_name, "messages": [{"role": "user", "content": content}],
               "max_tokens": MAX_OUTPUT_TOKENS, "stream": False}
    payload = apply_model_thinking_policy(payload, provider=provider, task="ocr")
    check_cancelled()
    try:
        response = post_chat_completion(provider, payload, timeout=120, check_status=False)
    except TaskCancelled:
        raise
    except Exception as exc:
        raise ValueError(f"PDF 局部识别请求失败（{type(exc).__name__}），未自动重试。") from exc
    check_cancelled()
    if response.status_code != 200:
        raise ValueError(f"PDF 局部识别请求失败（HTTP {response.status_code}），未自动重试。")
    try:
        body = response.json()
    except Exception as exc:
        raise ValueError("PDF 局部识别服务未返回有效 JSON。") from exc
    choices = body.get("choices") if isinstance(body, dict) else None
    if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "stop"):
        raise ValueError("PDF 局部识别未正常结束或输出被截断，未接受不完整结果。")
    message = choices[0].get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RESPONSE_CHARS:
        raise ValueError("PDF 局部识别内容为空、格式无效或过长。")
    try:
        parsed = parse_ai_json(raw)
    except Exception as exc:
        raise ValueError("PDF 局部识别的结构化结果无法解析，未自动重试。") from exc
    result = _merged_result(parsed, page_info, regions, pieces, include_figures=include_figures)
    check_cancelled()
    usage = body.get("usage")
    result["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                       if isinstance(usage, dict) and isinstance(usage.get(key), int)
                       and not isinstance(usage[key], bool) and usage[key] >= 0}
    result.update(model=provider.model_name,
                  extraction_mode="native_regions" if plan["kind"] == "mixed" else "image_regions",
                  native_characters=sum(len(re.sub(r"\s", "", piece["text"])) for piece in pieces if "text" in piece),
                  region_count=len(regions),
                  image_area_ratio=sum((region["bbox"][2] - region["bbox"][0]) *
                                       (region["bbox"][3] - region["bbox"][1]) / 1_000_000 for region in regions))
    return result
