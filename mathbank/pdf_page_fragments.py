"""First-pass page fragments with one immutable copy of each source text.

The model supplies local roles and page-edge hints, never final database rows.
Ranges, hashes and block IDs are derived here. They prove source association,
not OCR accuracy, mathematical correctness or a successful visual crop review.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from mathbank.pdf_figures import MAX_FIGURES_PER_PAGE, MAX_SOURCE_CHARS, describe_figure_slots
from mathbank.pdf_transcript_quality import assess_pdf_transcript_quality
from mathbank.question_assets import markdown_literal_ranges


SCHEMA = "mathbank.pdf-page-fragments.v2"
MAX_BLOCKS = 256
MAX_QUESTION_NUMBER = 10000
ROLES = frozenset({"stem", "answer", "answer_key", "context", "furniture", "appendix", "unknown"})
EDGES = frozenset({"none", "from_previous", "to_next", "both", "unknown"})
_FIELDS = {"schema", "blocks", "figures", "ignored_candidates", "page_complete", "warnings"}
_BLOCK_FIELDS = {"role", "number", "edge", "text"}
_SLOT = re.compile(r"^\[插图待补\s*[:：]\s*([^\]\n]+)\]$")
_SERVER_MARKER = re.compile(r"<!--\s*MATHBANK(?:_|\b)|\[\[MBM_", re.IGNORECASE)
_PREFIX = re.compile(r"\s*(\*\*)?([1-9][0-9]{0,4})[.．、]")
_ENV = re.compile(r"\\(begin|end)\s*\{([^{}\n]+)\}")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def number_prefix(text):
    """Return (visible top-level number, prefix end), or (None, None).

    Only a page/block-leading Arabic question label is evidence. The original
    score, answer label and body remain after the returned offset. Decimals,
    math, subquestions and a number inside an answer are never searched for.
    """
    if not isinstance(text, str):
        return None, None
    match = _PREFIX.match(text)
    if not match or int(match[2]) > MAX_QUESTION_NUMBER:
        return None, None
    end = match.end()
    if match[1]:
        if text[end:end + 2] != "**":
            return None, None
        end += 2
    if end < len(text) and (text[end].isascii() and text[end].isalnum() or text[end] in ".．"):
        return None, None
    while end < len(text) and text[end].isspace():
        end += 1
    return int(match[2]), end


def _escaped(text, position):
    index = position - 1
    while index >= 0 and text[index] == "\\":
        index -= 1
    return (position - index - 1) % 2 == 1


def _math_needs_review(text):
    """A fragment-local advisory; it never masks a following block."""
    chars = list(text)
    for left, right in markdown_literal_ranges(text):
        chars[left:right] = [" " for _ in chars[left:right]]
    value = "".join(chars)
    stack = []
    for token in _ENV.finditer(value):
        if _escaped(value, token.start()):
            continue
        if token[1] == "begin":
            stack.append(token[2].strip())
        elif not stack or stack.pop() != token[2].strip():
            return True
    if stack:
        return True
    opened = None
    index = 0
    while index < len(value):
        if _escaped(value, index):
            index += 1
            continue
        if value.startswith((r"\(", r"\["), index):
            if opened is not None:
                return True
            opened = value[index:index + 2]
            index += 2
        elif value.startswith((r"\)", r"\]"), index):
            if opened != (r"\(" if value[index:index + 2] == r"\)" else r"\["):
                return True
            opened = None
            index += 2
        elif value[index] == "$":
            delimiter = "$$" if value.startswith("$$", index) else "$"
            if opened is None:
                opened = delimiter
            elif opened == delimiter:
                opened = None
            else:
                return True
            index += len(delimiter)
        else:
            index += 1
    return opened is not None


def _metadata_notes(result, page_index):
    # Reuse the established typed/limited diagnostics compatibility. This
    # private sentinel is only a field-validation input, never returned as
    # source, archived as OCR, or made into a transcription witness. No real
    # choices/math are fed through the old whole-page retry quality gate.
    from mathbank.pdf_page_vision import _validated_page
    packet = {"markdown": "page diagnostics", "figures": [], "ignored_candidates": [],
              "page_complete": result["page_complete"], "warnings": deepcopy(result["warnings"])}
    for key in ("WARN", "review_required"):
        if key in result:
            packet[key] = deepcopy(result[key])
    notes = _validated_page(packet, {"page_index": page_index, "candidates": []})["layout"]["notes"]
    return [note.removeprefix("待核对：") for note in notes]


def _reject_asset_metadata(value, depth=0):
    if depth > 8:
        raise ValueError("PDF 首轮片段配图字段嵌套过深。")
    if isinstance(value, dict):
        if set(value) & {"path", "url", "image_path", "image_url", "page_image", "src"}:
            raise ValueError("PDF 首轮片段不能提供图片路径或URL字段。")
        for item in value.values():
            _reject_asset_metadata(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_asset_metadata(item, depth + 1)
    elif isinstance(value, str) and (re.match(r"(?:https?://|file:|data:image/)", value, re.I)
            or _SERVER_MARKER.search(value)):
        raise ValueError("PDF 首轮片段不能提供图片URL或来源标记。")


def validate_pdf_page_fragments(result, page_info):
    """Adapt a strictly parsed v2 response without rewriting any source text.

    The caller must use strict whole-JSON parsing before this boundary. Unknown
    roles are represented explicitly by ``unknown``; invalid protocol types,
    asset identity, source markers and resource overflow still fail locally.
    Number contradictions and formatting defects stay with their own block.
    """
    from mathbank.pdf_page_vision import (
        MAX_REASON_CHARS, _FIGURE_FIELDS, _IGNORED_FIELDS, _IGNORED_REASONS,
        _IMAGE_OR_ASSET_PATH, _prompt_page_info, _validated_layout,
    )
    if (not isinstance(result, dict)
            or set(result) != _FIELDS | (set(result) & {"WARN", "review_required"})
            or result.get("schema") != SCHEMA):
        raise ValueError("PDF 首轮片段协议字段或版本无效。")
    info = _prompt_page_info(page_info)
    warnings = _metadata_notes(result, info["page_index"])
    blocks = result["blocks"]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ValueError("PDF 首轮片段数量无效或超过上限。")
    texts = []
    records = []
    cursor = 0
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or set(block) != _BLOCK_FIELDS:
            raise ValueError("PDF 首轮片段字段缺失或包含未支持字段。")
        role, number, edge, text = (block[key] for key in ("role", "number", "edge", "text"))
        if (not isinstance(role, str) or role not in ROLES
                or not isinstance(edge, str) or edge not in EDGES
                or number is not None and (type(number) is not int or not 1 <= number <= MAX_QUESTION_NUMBER)
                or not isinstance(text, str) or not text.strip()
                or len(text) > MAX_SOURCE_CHARS or cursor + len(text) > MAX_SOURCE_CHARS):
            raise ValueError("PDF 首轮片段的角色、题号、续接或正文资源无效。")
        if _IMAGE_OR_ASSET_PATH.search(text) or _SERVER_MARKER.search(text):
            raise ValueError("PDF 首轮片段不能生成图片路径或服务端来源标记。")
        slots = describe_figure_slots(text, info["page_index"])
        if text.count("[插图待补") != len(slots):
            raise ValueError("PDF 首轮片段的插图占位必须在同一片段内完整闭合。")
        # Preserve an explicit transcription-incomplete signal separately from
        # a derived layout-only failure. Neither is proof of a complete paper.
        pending = ["page_incomplete"] if result["page_complete"] is False else []
        visible, _end = number_prefix(text)
        if role in {"stem", "answer", "answer_key"}:
            if number is not None:
                if visible is None:
                    pending.append("number_prefix_missing")
                elif visible != number:
                    pending.append("number_mismatch")
            elif role != "answer_key" and edge not in {"from_previous", "both"}:
                pending.append("number_missing")
        elif number is not None:
            pending.append("number_not_applicable")
        if role == "unknown":
            pending.append("unknown_role")
        if edge == "unknown":
            pending.append("unknown_edge")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            pending.append("control_characters")
        if _math_needs_review(text):
            pending.append("fragment_math_review")
        quality = assess_pdf_transcript_quality(text)
        if quality["retryable_failure"] or quality["warnings"]:
            pending.append("fragment_choices_review")
        text_sha = _sha(text)
        records.append({"id": f"p{info['page_index'] + 1}-b{index + 1}-{text_sha[:12]}",
            "role": role, "number": number, "edge": edge, "range": [cursor, cursor + len(text)],
            "text_sha256": text_sha, "pending_reasons": pending})
        texts.append(text)
        cursor += len(text)
    markdown = "".join(texts)
    if _IMAGE_OR_ASSET_PATH.search(markdown) or _SERVER_MARKER.search(markdown):
        raise ValueError("PDF 首轮片段不能跨片段拼入图片路径或服务端标记。")
    slots = describe_figure_slots(markdown, info["page_index"])
    if len(slots) > MAX_FIGURES_PER_PAGE:
        raise ValueError("PDF 首轮片段插图数量超过上限。")
    slot_blocks = {}
    labels = set()
    for slot in slots:
        match = _SLOT.fullmatch(slot["label"])
        label = match[1].strip() if match else ""
        owners = [block for block in records if block["range"][0] <= slot["start"]
                  and slot["end"] <= block["range"][1]]
        if (not label.startswith("图") or re.search(r"[/\\<>]", label)
                or label in labels or len(owners) != 1):
            raise ValueError("PDF 首轮片段插图标识或所属片段不唯一。")
        labels.add(label)
        slot_blocks[slot["id"]] = owners[0]["id"]
    figures, ignored = result["figures"], result["ignored_candidates"]
    if (not isinstance(figures, list) or len(figures) > MAX_FIGURES_PER_PAGE
            or not isinstance(ignored, list)):
        raise ValueError("PDF 首轮片段配图或未采用候选列表无效。")
    _reject_asset_metadata(figures)
    _reject_asset_metadata(ignored)
    known = {candidate["id"] for candidate in info["candidates"]}
    used_labels = set()
    used_ids = set()
    for figure in figures:
        if not isinstance(figure, dict) or set(figure) != _FIGURE_FIELDS:
            raise ValueError("PDF 首轮片段配图字段无效。")
        ids = figure["candidate_ids"]
        if (not isinstance(figure["slot"], str) or figure["slot"] not in labels
                or figure["slot"] in used_labels or not isinstance(ids, list)
                or any(not isinstance(identifier, str) for identifier in ids)
                or len(ids) != len(set(ids)) or set(ids) - known
                or type(figure["review_required"]) is not bool
                or not isinstance(figure["review_reason"], str) or len(figure["review_reason"]) > MAX_REASON_CHARS):
            raise ValueError("PDF 首轮片段配图身份或核对字段无效。")
        used_labels.add(figure["slot"]); used_ids.update(ids)
    ignored_ids = set()
    for item in ignored:
        if (not isinstance(item, dict) or set(item) != _IGNORED_FIELDS
                or not isinstance(item["id"], str) or item["id"] not in known
                or item["id"] in ignored_ids or item["id"] in used_ids
                or not isinstance(item["reason"], str) or item["reason"] not in _IGNORED_REASONS):
            raise ValueError("PDF 首轮片段未采用候选身份或原因无效。")
        ignored_ids.add(item["id"])
    packet = {"markdown": markdown, "figures": deepcopy(figures),
        "ignored_candidates": deepcopy(ignored), "page_complete": result["page_complete"]}
    try:
        joint = _validated_layout(packet, info, markdown, warnings)
    except ValueError:
        joint = {"markdown": markdown, "layout": {"figures": [], "ignored_candidates": [],
            "page_complete": False, "notes": [*["待核对：" + value for value in warnings],
                "待核对：本页配图范围未通过检查，已保留全部片段与图位，请对照原页补图。"]}}
        for block in records:
            if block["id"] in slot_blocks.values():
                block["pending_reasons"].append("figure_layout_review")
    joint["fragments"] = {"schema": SCHEMA, "page_index": info["page_index"],
        "source_sha256": _sha(markdown), "blocks": records, "slot_blocks": slot_blocks}
    return joint
