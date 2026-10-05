"""One bounded vision response supplies both PDF transcription and figure slots.

This module never writes assets. Source coordinates
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
from mathbank.ai_providers import apply_model_thinking_policy, apply_structured_output_policy, resolve_ocr_provider
from mathbank.pdf_figures import MAX_FIGURES_PER_PAGE, MAX_SOURCE_CHARS, describe_figure_slots
from mathbank.pdf_layout import normalize_model_bbox, scan_background_candidate_ids
from mathbank.pdf_vision_request import completion_content, request_pdf_vision


MAX_RESPONSE_CHARS = 200_000
MAX_OUTPUT_TOKENS = 16_384
MAX_WARNINGS = 16
MAX_REASON_CHARS = 500
MAX_PAGE_INPUT_PNG_BYTES = 24 * 1024 * 1024
PDF_DETAIL_NAVIGATION_RULE = (
    "【图像关系】刚才的第一张图是整页导航图，是唯一题序、版面、插图位置及所有返回bbox的坐标基准。"
    "其left/right为整页水平X，top/bottom为整页垂直Y，范围均为0–1000。"
    "后面的图是同一原PDF页的高清详情，原页范围在各图前标明，可能相互重叠。"
    "详情只用于精读文字和符号，结合导航图输出一份完整整页转录；重叠内容只写一次，不得重复题目或插图。"
    "仍按第一张整页图返回原协议及全页bbox，绝不能把详情图的局部坐标当作全页坐标。"
)
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
    background_ids = scan_background_candidate_ids(page_info)
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str) or not candidate["id"]:
            raise ValueError("PDF 原生候选标识无效。")
        if candidate["id"] in seen:
            raise ValueError("PDF 原生候选标识重复。")
        seen.add(candidate["id"])
        clean_candidates.append({**{key: candidate[key] for key in ("id", "bbox", "type") if key in candidate},
                                 "native_box_eligible": candidate.get("type") == "raster" and candidate["id"] not in background_ids})
    clean = {"page_index": page_index, "candidates": clean_candidates}
    for key in ("width", "height"):
        if key in page_info:
            clean[key] = page_info[key]
    if isinstance(page_info.get("full_page_image"), bool):
        clean["full_page_image"] = page_info["full_page_image"]
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
    background_ids = scan_background_candidate_ids(page_info)
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
                      and known_candidates[ids[0]].get("type") == "raster"
                      and ids[0] not in background_ids)
        background_used = bool(set(ids) & background_ids)
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
            if background_used and model_bbox is None:
                raise ValueError("扫描背景切片不是独立配图，不能借用其原生整列图框；已保留正文及插图占位，请提供独立配图范围。")
            bbox = _checked_bbox(model_bbox, "配图")
        if not isinstance(figure["review_required"], bool):
            raise ValueError("PDF 页面联合识别的配图核对标记必须为布尔值。")
        reason = figure["review_reason"]
        if not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS:
            raise ValueError("PDF 页面联合识别的配图核对说明格式无效。")
        if background_used:
            message = "原生候选为扫描背景切片，已保留模型提供的独立图框，请核对裁剪边缘和正文。"
            warnings.append(message)
            reason = reason or message
        layout_figures.append({
            "bbox": bbox, "model_bbox": list(model_bbox) if model_bbox is not None else None,
            "model_bbox_raw": dict(raw_bbox) if isinstance(raw_bbox, dict) else list(raw_bbox) if isinstance(raw_bbox, list) else None,
            "model_bbox_space": "page",
            "native_box": native_box, "candidate_ids": list(ids),
            "slot_id": slots_by_label[label]["id"], "anchor_before": "", "anchor_after": "",
            "review_required": figure["review_required"] or background_used, "review_reason": reason,
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
    report_attempt: Callable[[dict], None] = lambda _event: None,
    detail_views: list[dict] | None = None,
) -> dict:
    """Recognize a page with one bounded retry of the configured provider."""
    check_cancelled()
    clean_info = _prompt_page_info(page_info)
    provider = resolve_ocr_provider(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("PDF 页面联合识别所用的识图服务未配置。")
    if not provider.supports_image_input:
        raise ValueError("当前识图模型不支持 PDF 页面的图片输入。")
    try:
        source_png = Path(image_path).read_bytes()
        encoded = base64.b64encode(source_png).decode("ascii")
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
    detail_input = {"status": "none", "detail_count": 0, "detail_views": [], "notes": []}
    if detail_views:
        from mathbank.pdf_vision_details import prepare_detail_messages
        from mathbank.task_manager import TaskCancelled
        try:
            # These are internally rendered task assets, never client/model
            # paths. Bind them to the navigation image's task and page too.
            navigation = Path(image_path)
            identity = re.fullmatch(r"pdf_page_([A-Za-z0-9_-]+)_([0-9]+)\.png", navigation.name)
            if not identity or int(identity[2]) != clean_info["page_index"]:
                raise ValueError("导航图与详情图原页身份不一致。")
            expected_prefix = f"pdf_detail_{identity[1]}_{clean_info['page_index']}_"
            for view in detail_views:
                detail_path = Path(view.get("path", ""))
                if (detail_path.parent != navigation.parent
                        or not detail_path.name.startswith(expected_prefix)):
                    raise ValueError("详情图不属于当前导航图任务。")
            detail_messages, metadata = prepare_detail_messages(
                detail_views, page_index=clean_info["page_index"], check_cancelled=check_cancelled,
            )
            if len(source_png) + metadata["detail_total_png_bytes"] > MAX_PAGE_INPUT_PNG_BYTES:
                raise ValueError("整页及详情图合计超过输入额度。")
            payload["messages"][0]["content"].extend([
                {"type": "text", "text": PDF_DETAIL_NAVIGATION_RULE}, *detail_messages,
            ])
            detail_input = {"status": "included", **metadata, "notes": []}
        except TaskCancelled:
            raise
        except (OSError, ValueError, TypeError, AttributeError):
            # One complete overview remains available. Do not use partially
            # prepared details or open an additional paid fallback/retry chain.
            detail_input = {"status": "skipped", "detail_count": 0, "detail_views": [],
                            "notes": ["高清辅助视图未通过输入校验，沿用整页识图。"]}
    check_cancelled()
    payload = apply_model_thinking_policy(payload, provider=provider, task="ocr")
    payload = apply_structured_output_policy(payload, provider=provider,
                                            system_instruction=prompts.PDF_STRUCTURED_OUTPUT_INSTRUCTIONS)
    def validate(body):
        raw = completion_content(body, "PDF 页面联合识别", max_chars=MAX_RESPONSE_CHARS)
        try:
            parsed = parse_ai_json(raw)
        except Exception as exc:
            raise ValueError("PDF 页面联合识别的结构化结果无法解析。") from exc
        return _validated_page(parsed, clean_info)

    result = request_pdf_vision(
        provider, payload, post=post_chat_completion, validate=validate,
        label="PDF 页面联合识别", stage="joint_page", page_index=clean_info["page_index"],
        check_cancelled=check_cancelled, report_attempt=report_attempt,
    )
    result["model"] = provider.model_name
    result["detail_input"] = detail_input
    return result
