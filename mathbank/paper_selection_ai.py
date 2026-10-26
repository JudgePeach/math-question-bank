"""Bounded model inputs and locally checked paper-selection output; no writes."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import Counter

from .pdf_vision_request import completion_content
from .source_comments import strip_source_attribution_comment
from .source_review_results import parse_review_response
from .paper_selection import _content_key
from .ai_providers import supports_classification_images
from .asset_security import (InvalidImageError, UploadTooLargeError, normalize_raster_image,
                             read_stream_limited, resolve_upload_asset)
from .knowledge_annotation_targets import extract_knowledge_annotation_targets
from .paths import UPLOADS_DIR
from .question_assets import image_reference_spans


MAX_REVIEW_CANDIDATES = 36
MAX_REVIEW_STEM_CHARS = 24000
MAX_REVIEW_TEXT_CHARS = 180000
MAX_REVIEW_IMAGES = 12
MAX_REVIEW_IMAGE_BYTES = 4 * 1024 * 1024
MAX_REVIEW_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
MAX_REVIEW_IMAGE_PIXELS = 4000000


def _mentions_unavailable_figure(content):
    return bool(re.search(r"如图|见图|图中|[下左右]图", content))


def _excerpt(value, budget):
    text = str(value or "")
    if len(text) <= budget:
        return text, False
    half = (budget - 22) // 2
    return text[:half] + "\n[中间内容未提供]\n" + text[-half:], True


def candidate_prompt_items(questions, *, status_scope="all"):
    """Screen complete short stems and label bounded outlines of longer ones."""
    budget = min(3600, max(900, 144000 // max(1, len(questions))))
    items = []
    for question in questions:
        content = strip_source_attribution_comment(question.content or "")
        stem, truncated = _excerpt(content, budget)
        outline = []
        if truncated:
            # Inspect the whole bounded source before choosing any excerpts;
            # a middle subquestion must not disappear behind head/tail alone.
            plan = extract_knowledge_annotation_targets(content) if len(content) <= 100000 else {}
            blocks = ([item["quote"] for item in plan["targets"]]
                      if plan.get("mode") == "subquestions" else re.split(r"\n\s*\n", content))
            part_budget = min(240, max(40, min(2400, max(480, 72000 // max(1, len(questions)))) // max(1, min(24, len(blocks)))))
            outline = [{"part": index + 1, "text": _excerpt(block, part_budget)[0],
                        "truncated": len(block) > part_budget}
                       for index, block in enumerate(blocks[:24]) if block.strip()]
        points = []
        for row in question.knowledge_points:
            if status_scope == "confirmed" and row.status != "confirmed":
                continue
            points.append({"code": row.point_code, "name": row.point.name[:100],
                           "role": row.role, "status": row.status})
        items.append({
            "id": question.id, "question_type": question.question_type,
            "difficulty_label": question.difficulty, "usage_count": question.usage_count or 0,
            "source": (question.source or "")[:240],
            "chapter_hint": (question.category_chapter or "")[:120],
            "knowledge_hint": (question.category_knowledge or "")[:120],
            "tags": (question.tags or "")[:240], "knowledge_points": points[:16],
            "stem": stem, "stem_truncated": truncated,
            "stem_chars": len(content), "structure_outline": outline,
            "outline_is_full_text": False, "structure_outline_complete": not truncated,
            "image_pixels_provided": False,
            "has_image_reference": bool(_question_image_references(question)
                or re.search(r"!\[|\\includegraphics|\\begin\{tikzpicture\}", content)
                or _mentions_unavailable_figure(content)
                or getattr(question, "tikz_code", "")
                or str(getattr(question, "image_paths", "") or "").strip() not in {"", "[]", "null"}),
        })
    return items


def review_shortlist(candidates, raw_ids, *, limit, local_order, type_counts=None):
    """Bound the follow-up pool and reserve the same final type slots locally."""
    budget = min(MAX_REVIEW_CANDIDATES, max(limit + 4, limit * 2))
    # First reserve quota coverage without accepting any extra type. Fill the
    # remaining review slots in model order; this is a pool, not the final paper.
    required = []
    if type_counts:
        required, _ = checked_selection(candidates, raw_ids + [q.id for q in local_order],
                                        limit=limit, type_counts=type_counts)
    pool, _ = checked_selection(candidates,
        [q.id for q in required] + raw_ids + [q.id for q in local_order], limit=budget)
    return pool


def _question_image_references(question):
    content = strip_source_attribution_comment(question.content or "")
    references = [span[2] for span in image_reference_spans(content, local_only=False)]
    legacy = getattr(question, "image_paths", []) or []
    if isinstance(legacy, str):
        try:
            legacy = json.loads(legacy)
        except (ValueError, TypeError):
            legacy = []
    if not references and isinstance(legacy, list):
        references.extend(path for path in legacy[:64] if isinstance(path, str))
    return list(dict.fromkeys(references))


def prepare_full_review(questions, provider, *, status_scope="all", uploads_dir=None):
    """Keep whole question bodies; report every missing pixel without network IO.

    No original file is restored, rewritten or resized in place. An item whose
    complete text does not fit is withheld rather than silently summarized.
    """
    uploads_dir = UPLOADS_DIR if uploads_dir is None else uploads_dir
    items, image_parts, fingerprints, withheld = [], [], [], []
    remaining_text, remaining_bytes = MAX_REVIEW_TEXT_CHARS, MAX_REVIEW_TOTAL_IMAGE_BYTES
    image_count = 0
    capable = supports_classification_images(provider)
    for question in questions[:MAX_REVIEW_CANDIDATES]:
        content = strip_source_attribution_comment(question.content or "")
        if len(content) > MAX_REVIEW_STEM_CHARS or len(content) > remaining_text:
            withheld.append({"id": question.id, "reason": "完整题干超过本次复读文字预算，未截断冒充全文。"})
            continue
        item = candidate_prompt_items([question], status_scope=status_scope)[0]
        item.update(stem=content, stem_truncated=False, structure_outline=[],
                    reading_stage="full_review", full_text_provided=True)
        remaining_text -= len(content)
        references = _question_image_references(question)
        unavailable, visible = [], []
        item_parts, item_fingerprints, item_bytes, item_image_count = [], [], 0, 0
        for index, reference in enumerate(references):
            reason = None
            if not capable:
                reason = "model_images_unsupported"
            elif index >= 4 or image_count + item_image_count >= MAX_REVIEW_IMAGES:
                reason = "image_count_budget"
            else:
                try:
                    path = resolve_upload_asset(reference, uploads_dir=uploads_dir,
                        url_prefix="static/uploads", require_file=False)
                    if not path.is_file():
                        raise FileNotFoundError
                    with path.open("rb") as stream:
                        raw = read_stream_limited(stream, MAX_REVIEW_IMAGE_BYTES)
                    normalized = normalize_raster_image(raw, max_pixels=MAX_REVIEW_IMAGE_PIXELS,
                        max_output_bytes=MAX_REVIEW_IMAGE_BYTES)
                    if len(normalized.data) > remaining_bytes - item_bytes:
                        reason = "image_byte_budget"
                    else:
                        digest = hashlib.sha256(raw).hexdigest()
                        item_fingerprints.append((question.id, reference, digest))
                        visible.append(index + 1)
                        item_parts.extend([
                            {"type": "text", "text": f"候选题id={question.id}，题干插图{index + 1}（仅此题图像）："},
                            {"type": "image_url", "image_url": {"url": f"data:{normalized.media_type};base64,"
                                + base64.b64encode(normalized.data).decode("ascii")}},
                        ])
                        item_bytes += len(normalized.data)
                        item_image_count += 1
                except UploadTooLargeError:
                    reason = "image_single_byte_budget"
                except InvalidImageError:
                    reason = "image_pixels_format_or_encoding"
                except (OSError, ValueError):
                    reason = "unsafe_missing_or_invalid_image"
            if reason:
                unavailable.append({"image": index + 1, "reason": reason})
        # Inline TikZ has full source, but a text task has not rendered pixels.
        has_unrendered = bool(getattr(question, "tikz_code", "") or re.search(r"\\begin\{tikzpicture\}", content))
        if has_unrendered and not references:
            unavailable.append({"image": 1, "reason": "tikz_pixels_unavailable"})
        if not references and not has_unrendered and _mentions_unavailable_figure(content):
            unavailable.append({"image": 1, "reason": "image_reference_missing"})
        if unavailable:
            # Partial pixels cannot establish the complete figure conditions.
            # Do not spend the shared budget on an unusable partial figure set.
            failed_numbers = {entry["image"] for entry in unavailable}
            unavailable.extend({"image": number, "reason": "incomplete_figure_set"}
                               for number in visible if number not in failed_numbers)
            visible = []
        else:
            image_parts.extend(item_parts)
            fingerprints.extend(item_fingerprints)
            remaining_bytes -= item_bytes
            image_count += item_image_count
        item.update(has_image_reference=bool(references) or has_unrendered or bool(unavailable),
                    image_pixels_provided=bool(visible),
                    image_pixels_complete=bool(references) and not unavailable,
                    referenced_image_count=len(references), provided_image_numbers=visible,
                    unavailable_image_count=len(unavailable), unavailable_images=unavailable[:64],
                    eligible_for_ai_final=not unavailable)
        items.append(item)
    report = {"full_text_ids": [item["id"] for item in items], "withheld": withheld,
              "items": [{"id": item["id"], "text_provided_complete": True,
                         "image_provided_complete": not item["unavailable_images"],
                         "unavailable_images": item["unavailable_images"],
                         "unavailable_image_count": item["unavailable_image_count"]} for item in items],
              "image_count": image_count,
              "image_bytes": MAX_REVIEW_TOTAL_IMAGE_BYTES - remaining_bytes,
              "limits": {"candidates": MAX_REVIEW_CANDIDATES, "stem_chars": MAX_REVIEW_STEM_CHARS,
                         "text_chars": MAX_REVIEW_TEXT_CHARS, "images": MAX_REVIEW_IMAGES,
                         "single_image_bytes": MAX_REVIEW_IMAGE_BYTES,
                         "total_image_bytes": MAX_REVIEW_TOTAL_IMAGE_BYTES,
                         "single_image_pixels": MAX_REVIEW_IMAGE_PIXELS},
              "images_supported": capable,
              "image_unavailable_ids": [item["id"] for item in items if item["unavailable_images"]]}
    return items, image_parts, fingerprints, report


def changed_review_image_ids(fingerprints, *, uploads_dir=None):
    uploads_dir = UPLOADS_DIR if uploads_dir is None else uploads_dir
    changed = set()
    for qid, reference, expected in fingerprints:
        try:
            path = resolve_upload_asset(reference, uploads_dir=uploads_dir,
                url_prefix="static/uploads", require_file=False)
            with path.open("rb") as stream:
                raw = read_stream_limited(stream, MAX_REVIEW_IMAGE_BYTES)
            if hashlib.sha256(raw).hexdigest() != expected:
                changed.add(qid)
        except (OSError, ValueError):
            changed.add(qid)
    return changed


def candidate_revision(question):
    """Freeze selection-relevant facts before a potentially long model wait."""
    return (_content_key(question), question.question_type, question.difficulty,
            question.source, question.category_compulsory, question.category_chapter,
            question.category_knowledge, question.tags,
            tuple(sorted((row.point_code, row.role, row.status) for row in question.knowledge_points)))


def parse_selection_response(body):
    raw = completion_content(body, "AI选题", max_chars=40000, allow_missing_finish=True)
    parsed = parse_review_response(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("selected_ids"), list):
        raise ValueError("AI选题未返回有效的题目编号列表。")
    if not isinstance(parsed.get("ai_analysis", ""), str):
        raise ValueError("AI选题说明格式无效。")
    return parsed


def checked_selection(candidates, raw_ids, *, limit, type_counts=None):
    """Only accept supplied IDs, distinct stems and available type slots."""
    allowed = {q.id: q for q in candidates}
    selected, seen, stems, counts = [], set(), set(), Counter()
    rejected = 0
    for raw in raw_ids:
        qid = (raw if type(raw) is int else int(raw)
               if isinstance(raw, str) and re.fullmatch(r"[1-9]\d{0,18}", raw) else None)
        question = allowed.get(qid)
        if question is None or qid in seen:
            rejected += 1
            continue
        stem = _content_key(question)
        if stem in stems or (type_counts is not None
                and counts[question.question_type] >= type_counts.get(question.question_type, 0)):
            rejected += 1
            continue
        selected.append(question)
        seen.add(qid)
        stems.add(stem)
        counts[question.question_type] += 1
        if len(selected) == limit:
            break
    return selected, rejected


def selection_summary(questions, *, requested, candidate_count, type_counts=None):
    types = dict(Counter(q.question_type for q in questions))
    main, auxiliary = Counter(), Counter()
    for question in questions:
        for role, target in (("main", main), ("auxiliary", auxiliary)):
            target.update({row.point.name for row in question.knowledge_points if row.role == role})
    return {"requested": requested, "selected": len(questions), "candidate_count": candidate_count,
            "type_counts": types, "requested_type_counts": type_counts or {},
            "main_knowledge_counts": dict(main.most_common(24)),
            "auxiliary_knowledge_counts": dict(auxiliary.most_common(16)),
            "shortfall": max(0, requested - len(questions))}
