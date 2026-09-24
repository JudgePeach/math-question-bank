"""One bounded vision response supplies both PDF transcription and figure slots.

This module never writes assets or retries a failed response. Source coordinates
and slot identity are validated before the existing crop/anchor pipeline sees
them. Reliable native pages do not need this OCR-only entry point.
"""

from __future__ import annotations

import base64
from collections import Counter
import math
import os
from pathlib import Path
import re
from typing import Callable

from mathbank import prompts
from mathbank.ai_http import post_chat_completion
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.pdf_figures import MAX_FIGURES_PER_PAGE, MAX_SOURCE_CHARS, describe_figure_slots
from mathbank.pdf_layout import normalize_model_bbox
from mathbank.task_manager import TaskCancelled


MAX_RESPONSE_CHARS = 200_000
MAX_OUTPUT_TOKENS = 16_384
MAX_WARNINGS = 16
MAX_REASON_CHARS = 500
_PAGE_FIELDS = {"markdown", "figures", "ignored_candidates", "page_complete", "warnings"}
_FIGURE_FIELDS = {"slot", "bbox", "candidate_ids", "review_required", "review_reason"}
_IGNORED_FIELDS = {"id", "reason"}
_IGNORED_REASONS = {"formula", "table_border", "decoration", "page_background"}
_SLOT_LABEL = re.compile(r"^\[插图待补\s*[:：]\s*([^\]\n]+)\]$")
_IMAGE_OR_ASSET_PATH = re.compile(
    r"!\[[^\]]*\]\s*(?:\(|\[)|<\s*img\b|\\includegraphics\b"
    r"|/static/(?:uploads|test_uploads)/|file://|data:image/",
    re.IGNORECASE,
)


def _no_cancel() -> None:
    pass


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _require_fields(value, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"PDF 页面联合识别的{label}字段缺失或包含未支持字段。")


def _checked_bbox(bbox, label: str) -> list:
    if not isinstance(bbox, list) or len(bbox) != 4 or any(not _finite_number(value) for value in bbox):
        raise ValueError(f"PDF 页面联合识别的{label}坐标必须是四个有限数值。")
    x0, y0, x1, y1 = bbox
    if (not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000)
            or min(x1 - x0, y1 - y0) < 1 or (x1 - x0) * (y1 - y0) > 800000):
        raise ValueError(f"PDF 页面联合识别的{label}坐标越界、过小或覆盖整页。")
    return list(bbox)


def _prompt_page_info(page_info: dict) -> dict:
    """Do not send the discarded native transcript or its text blocks."""
    if not isinstance(page_info, dict):
        raise ValueError("PDF 页面位置资料格式无效。")
    page_index = page_info.get("page_index")
    candidates = page_info.get("candidates", [])
    if (isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0
            or not isinstance(candidates, list)):
        raise ValueError("PDF 页码或候选区域格式无效。")
    clean_candidates = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str) or not candidate["id"]:
            raise ValueError("PDF 原生候选标识无效。")
        if candidate["id"] in seen:
            raise ValueError("PDF 原生候选标识重复。")
        seen.add(candidate["id"])
        clean_candidates.append({key: candidate[key] for key in ("id", "bbox", "type") if key in candidate})
    clean = {"page_index": page_index, "candidates": clean_candidates}
    for key in ("width", "height"):
        if key in page_info:
            clean[key] = page_info[key]
    return clean


def _validated_page(result, page_info: dict) -> dict:
    _require_fields(result, _PAGE_FIELDS, "页面")
    markdown = result["markdown"]
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("PDF 页面联合识别没有返回有效正文。")
    if len(markdown) > MAX_SOURCE_CHARS:
        raise ValueError("PDF 页面联合识别正文过长，请缩小识别范围。")
    if _IMAGE_OR_ASSET_PATH.search(markdown):
        raise ValueError("PDF 页面联合识别不能生成图片路径，只能返回插图占位。")
    if not isinstance(result["page_complete"], bool):
        raise ValueError("PDF 页面联合识别的完整性标记必须为布尔值。")
    warnings = result["warnings"]
    if (not isinstance(warnings, list) or len(warnings) > MAX_WARNINGS
            or any(not isinstance(value, str) or not value.strip() or len(value) > MAX_REASON_CHARS for value in warnings)):
        raise ValueError("PDF 页面联合识别的核对提示格式无效。")
    try:
        return _validated_layout(result, page_info, markdown, warnings)
    except ValueError as exc:
        # The transcript is independently usable. A malformed figure contract
        # must not attach a wrong image or discard the whole page's text.
        return {
            "markdown": markdown,
            "layout": {"figures": [], "page_complete": False, "ignored_candidates": [],
                       "notes": [*["待核对：" + message for message in warnings],
                                 "待核对：" + str(exc) + " 已保留正文，请对照原页补图；未重新调用模型。"]},
        }


def _validated_layout(result: dict, page_info: dict, markdown: str, warnings: list[str]) -> dict:
    warnings = list(warnings)
    figures = result["figures"]
    if not isinstance(figures, list) or len(figures) > MAX_FIGURES_PER_PAGE:
        raise ValueError("PDF 页面联合识别的配图列表无效或数量过多。")

    slots = describe_figure_slots(markdown, page_info["page_index"])
    if len(slots) > MAX_FIGURES_PER_PAGE:
        raise ValueError("PDF 页面联合识别的插图占位数量过多。")
    slots_by_label = {}
    for slot in slots:
        label_match = _SLOT_LABEL.fullmatch(slot["label"])
        label = label_match.group(1).strip() if label_match else ""
        if not label or not label.startswith("图") or re.search(r"[/\\<>]", label):
            raise ValueError("PDF 页面联合识别的插图占位缺少有效图号。")
        if label in slots_by_label:
            raise ValueError("PDF 页面联合识别的插图占位图号重复，无法唯一归位。")
        slots_by_label[label] = slot
    if markdown.count("[插图待补") != len(slots):
        raise ValueError("PDF 页面联合识别包含不完整的插图占位。")

    known_candidates = {candidate["id"]: candidate for candidate in page_info["candidates"]}
    known = set(known_candidates)
    candidate_uses = Counter(
        identifier for figure in figures
        if isinstance(figure, dict) and isinstance(figure.get("candidate_ids"), list)
        for identifier in figure["candidate_ids"] if isinstance(identifier, str)
    )
    used_labels = set()
    layout_figures = []
    for figure in figures:
        _require_fields(figure, _FIGURE_FIELDS, "配图")
        label = figure["slot"]
        if not isinstance(label, str) or label not in slots_by_label:
            raise ValueError("PDF 页面联合识别的配图未对应到正文中的唯一插图占位。")
        if label in used_labels:
            raise ValueError("PDF 页面联合识别将多幅图绑定到同一占位。")
        used_labels.add(label)
        ids = figure["candidate_ids"]
        if (not isinstance(ids, list) or any(not isinstance(identifier, str) for identifier in ids)
                or len(ids) != len(set(ids)) or set(ids) - known):
            raise ValueError("PDF 页面联合识别引用了无效、重复或未知的候选区域。")
        raw_bbox = figure["bbox"]
        model_bbox = normalize_model_bbox(raw_bbox) if raw_bbox is not None else None
        native_box = (len(ids) == 1 and candidate_uses[ids[0]] == 1
                      and known_candidates[ids[0]].get("type") == "raster")
        if native_box:
            # The page's raster occurrence has exact local geometry. When the
            # model uniquely identifies that whole asset, guessing its corners
            # adds error without adding information. Keep the model estimate
            # for audit, but never use it to trim the native image.
            bbox = _checked_bbox(known_candidates[ids[0]].get("bbox"), "原生位图")
            if model_bbox is not None and (
                not isinstance(model_bbox, list) or len(model_bbox) != 4
                or any(not _finite_number(value) for value in model_bbox)
            ):
                raise ValueError("PDF 页面联合识别的模型图框只能是四个有限数值或空值。")
        else:
            # Shared raster occurrences can contain several subfigures;
            # vector or composite figures still need independently checked
            # visual rectangles. They cannot borrow the null-box shortcut.
            bbox = _checked_bbox(model_bbox, "配图")
        if not isinstance(figure["review_required"], bool):
            raise ValueError("PDF 页面联合识别的配图核对标记必须为布尔值。")
        reason = figure["review_reason"]
        if not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS:
            raise ValueError("PDF 页面联合识别的配图核对说明格式无效。")
        layout_figures.append({
            "bbox": bbox, "model_bbox": list(model_bbox) if model_bbox is not None else None,
            "model_bbox_raw": dict(raw_bbox) if isinstance(raw_bbox, dict) else list(raw_bbox) if isinstance(raw_bbox, list) else None,
            "model_bbox_space": "page",
            "native_box": native_box, "candidate_ids": list(ids),
            "slot_id": slots_by_label[label]["id"], "anchor_before": "", "anchor_after": "",
            "review_required": figure["review_required"], "review_reason": reason,
        })
    missing_slots = set(slots_by_label) - used_labels
    if missing_slots:
        warnings.append(f"{len(missing_slots)} 幅插图占位尚未绑定有效图框，已保留占位，请对照原页补图。")

    ignored = result["ignored_candidates"]
    if not isinstance(ignored, list) or len(ignored) > len(known):
        raise ValueError("PDF 页面联合识别的未采用候选列表格式无效。")
    ignored_ids = set()
    for item in ignored:
        _require_fields(item, _IGNORED_FIELDS, "未采用候选")
        identifier, reason = item["id"], item["reason"]
        if (not isinstance(identifier, str) or identifier not in known or identifier in ignored_ids
                or not isinstance(reason, str) or reason not in _IGNORED_REASONS):
            raise ValueError("PDF 页面联合识别的未采用候选标识或原因无效。")
        if any(identifier in figure["candidate_ids"] for figure in layout_figures):
            raise ValueError("PDF 页面联合识别将同一候选同时声明为配图和忽略区域。")
        ignored_ids.add(identifier)
    return {
        "markdown": markdown,
        "layout": {"figures": layout_figures, "page_complete": result["page_complete"] and not missing_slots,
                   "ignored_candidates": [dict(item) for item in ignored],
                   "notes": ["待核对：" + message for message in warnings]},
    }


def request_pdf_page(
    image_path: str, page_info: dict, *, check_cancelled: Callable[[], None] = _no_cancel,
) -> dict:
    """Call the configured vision provider once; never invoke a paid fallback."""
    check_cancelled()
    clean_info = _prompt_page_info(page_info)
    provider = resolve_ocr_provider(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("PDF 页面联合识别所用的识图服务未配置。")
    if not provider.supports_image_input:
        raise ValueError("当前识图模型不支持 PDF 页面的图片输入。")
    try:
        encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    except OSError as exc:
        raise ValueError("无法读取 PDF 页面预览图。") from exc
    payload = {
        "model": provider.model_name,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompts.build_pdf_page_vision_prompt(clean_info)},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
        ]}],
        "max_tokens": MAX_OUTPUT_TOKENS, "stream": False,
    }
    payload = apply_model_thinking_policy(payload, provider=provider, task="ocr")
    check_cancelled()
    try:
        response = post_chat_completion(provider, payload, timeout=120, check_status=False)
    except TaskCancelled:
        raise
    except Exception as exc:
        raise ValueError(f"PDF 页面联合识别请求失败（{type(exc).__name__}），未自动重试。") from exc
    check_cancelled()
    if response.status_code != 200:
        raise ValueError(f"PDF 页面联合识别请求失败（HTTP {response.status_code}），未自动重试。")
    try:
        body = response.json()
    except Exception as exc:
        raise ValueError("PDF 页面联合识别服务未返回有效 JSON。") from exc
    choices = body.get("choices") if isinstance(body, dict) else None
    if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "stop"):
        raise ValueError("PDF 页面联合识别未正常结束或输出被截断，未接受不完整结果。")
    message = choices[0].get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RESPONSE_CHARS:
        raise ValueError("PDF 页面联合识别内容为空、格式无效或过长。")
    try:
        parsed = parse_ai_json(raw)
    except Exception as exc:
        raise ValueError("PDF 页面联合识别的结构化结果无法解析，未自动重试。") from exc
    result = _validated_page(parsed, clean_info)
    check_cancelled()
    usage = body.get("usage")
    result["usage"] = {
        key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage, dict) and isinstance(usage.get(key), int)
        and not isinstance(usage[key], bool) and usage[key] >= 0
    }
    result["model"] = provider.model_name
    return result
