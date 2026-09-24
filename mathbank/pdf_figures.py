"""Layout-first PDF illustration extraction using the configured vision provider.

Page geometry and exact source anchors are retained until import. Vision only
proposes regions; it cannot choose asset paths, rewrite source, or certify itself.
"""

from __future__ import annotations

import base64
import math
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Callable

from mathbank.ai_http import post_chat_completion
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.pdf_layout import (
    apply_figure_anchors, crop_pdf_figure, inspect_pdf_page, refine_figure_bbox, normalize_model_bbox,
)
from mathbank.prompts import build_pdf_layout_prompt
from mathbank.task_manager import TaskCancelled


LAYOUT_SCHEMA = "mathbank.pdf-layout.v1"
MAX_FIGURES_PER_PAGE = 32
MAX_SOURCE_CHARS = 50000
PDF_STRATEGIES = frozenset({"native_preferred", "force_ocr", "layout_aware"})
_FIGURE_SLOT = re.compile(r"\[插图待补(?:\s*[:：]\s*[^\]\n]{0,80})?\]")


def describe_figure_slots(source: str, page_index: int) -> list[dict]:
    """Give each OCR image slot an identity independent of repeated option text."""
    slots = []
    for index, match in enumerate(_FIGURE_SLOT.finditer(source)):
        slots.append({"id": f"p{page_index + 1}-s{index + 1}", "label": match.group(),
                      "start": match.start(), "end": match.end(),
                      "before": source[max(0, match.start() - 100):match.start()],
                      "after": source[match.end():match.end() + 100]})
    return slots


def _is_processing_note(message: str) -> bool:
    return message.startswith(("已按原生候选 ", "已补全 ", "小边距会超过扩张上限或碰到其他文字，已保留当前边界。",
                               "检测到接近整页的图片，须从原页识别独立插图"))


def _review_explanations(reasons: list[str]) -> list[str]:
    """Translate diagnostic geometry into concrete teacher-facing actions."""
    result = []
    for reason in reasons:
        for message in re.split(r"[；;]", str(reason)):
            message = re.sub(r"^PDF 第 \d+ 页：", "", message).strip()
            if not message:
                continue
            if "同一原生区域" in message or "区域高度重叠" in message:
                message = "同一张原图被拆成多幅配图，请核对是否完整、是否重复。"
            elif "裁框未覆盖候选" in message or "未完整覆盖" in message:
                message = "配图裁剪范围可能不完整，请对照原图确认。"
            elif "候选" in message and ("未被解释" in message or "未采用" in message):
                message = "原页还有未确认的图形区域，请检查是否漏图。"
            else:
                message = re.sub(r"\bp\d+_(?:raster|vector)_\d+\b", "原图区域", message)
                message = message.replace("公式编号", "公式")
            if message not in result:
                result.append(message)
    return result


def request_pdf_layout(
    image_path: str, markdown: str, page_info: dict, *,
    diagnostics: dict, check_cancelled: Callable[[], None],
) -> dict:
    """One bounded request, no paid fallback/retry on an uncertain response."""
    if len(markdown) > MAX_SOURCE_CHARS:
        raise ValueError("单页原文过长，配图定位已转为人工核对。")
    provider = resolve_ocr_provider(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("识图服务未配置，配图请对照原页手动补充。")
    if not provider.supports_image_input:
        raise ValueError("当前识图模型不支持图片，配图请对照原页手动补充。")
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    payload = {
        "model": provider.model_name,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": build_pdf_layout_prompt(markdown, page_info)},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
        ]}],
        "max_tokens": 8192,
        "stream": False,
    }
    payload = apply_model_thinking_policy(payload, provider=provider, task="ocr")
    check_cancelled()
    diagnostics["visual_calls"] += 1
    response = post_chat_completion(provider, payload, timeout=120, check_status=False)
    check_cancelled()
    if response.status_code != 200:
        raise ValueError(f"配图定位请求失败（HTTP {response.status_code}），未自动重试。")
    body = response.json()
    usage = body.get("usage") if isinstance(body, dict) else None
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                diagnostics["usage"][key] = diagnostics["usage"].get(key, 0) + value
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("配图定位未返回有效结果。")
    choice = choices[0]
    if choice.get("finish_reason") not in (None, "stop"):
        raise ValueError("配图定位未完整结束，未使用截断或异常结果。")
    message = choice.get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError("配图定位返回格式无效。")
    result = parse_ai_json(raw)
    if not isinstance(result, dict):
        raise ValueError("配图定位应返回结构化对象。")
    return result


def _validate_layout(result: dict, page_info: dict, *, notes: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Reject malformed geometry/anchors; retain incomplete-coverage warnings."""
    if not isinstance(result, dict) or not isinstance(result.get("figures"), list):
        raise ValueError("配图区域列表格式无效。")
    if len(result["figures"]) > MAX_FIGURES_PER_PAGE:
        raise ValueError("单页配图数量超出安全处理上限，请缩小导入范围或手动补图。")
    warnings = []
    notes = notes if notes is not None else []
    if result.get("page_complete") is not True:
        warnings.append("视觉检查未确认覆盖整页，请检查是否漏图。")
    known = {candidate["id"] for candidate in page_info.get("candidates", [])}
    accounted = set()
    figures = []
    used_slots = set()
    for index, item in enumerate(result["figures"]):
        warning_start = len(warnings)
        if not isinstance(item, dict):
            raise ValueError("配图区域格式无效。")
        bbox = normalize_model_bbox(item.get("bbox"))
        if not isinstance(bbox, list) or len(bbox) != 4 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in bbox
        ):
            raise ValueError("配图坐标格式无效。")
        x0, y0, x1, y1 = bbox
        if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
            raise ValueError("配图坐标越界或为空，未自动裁剪。")
        if (x1 - x0) * (y1 - y0) > 800000:
            raise ValueError("配图区域覆盖几乎整页，无法与正文可靠区分。")
        for previous in figures:
            a, b, c, d = previous["bbox"]
            intersection = max(0, min(c, x1) - max(a, x0)) * max(0, min(d, y1) - max(b, y0))
            union = (c-a)*(d-b) + (x1-x0)*(y1-y0) - intersection
            if intersection / max(1, union) >= 0.8:
                message = "配图区域高度重叠，可能把同一幅图重复插入，请核对。"
                warnings.append(message)
                previous["review_reasons"].append(message)
        anchors = {key: item.get(key, "") for key in ("anchor_before", "anchor_after")}
        if any(not isinstance(value, str) or len(value) > 1500 for value in anchors.values()):
            raise ValueError("配图的原文锚点格式无效。")
        slot_id = item.get("slot_id", "")
        if not isinstance(slot_id, str):
            raise ValueError("配图占位编号格式无效。")
        slot = next((slot for slot in page_info.get("figure_slots", []) if slot["id"] == slot_id), None)
        if slot_id and slot is None:
            warnings.append("配图引用了不存在的原文占位，未自动归位。")
            anchors = {"anchor_before": "", "anchor_after": ""}
        if slot_id in used_slots:
            warnings.append("多幅图引用同一原文占位，请核对选项是否错配。")
        if slot_id:
            used_slots.add(slot_id)
        candidate_ids = item.get("candidate_ids", [])
        if not isinstance(candidate_ids, list) or any(not isinstance(value, str) for value in candidate_ids):
            raise ValueError("配图候选引用无效。")
        if set(candidate_ids) - known:
            warnings.append("视觉结果引用了未知候选区域，请核对配图范围。")
        for previous in figures:
            if set(candidate_ids) & set(previous["candidate_ids"]):
                message = "同一原生区域被多幅图重复引用，请核对是否错误拆分或重复插图。"
                warnings.append(message)
                previous["review_reasons"].append(message)
        native_box = bool(item.get("native_box") and len(candidate_ids) == 1 and any(
            candidate["id"] == candidate_ids[0] and candidate.get("type") == "raster"
            and candidate["bbox"] == bbox for candidate in page_info.get("candidates", [])
        ))
        model_bbox = item.get("model_bbox") if native_box else list(bbox)
        refined = refine_figure_bbox(page_info, bbox, candidate_ids)
        bbox = refined["bbox"]
        x0, y0, x1, y1 = bbox
        for message in refined["warnings"]:
            (notes if _is_processing_note(message) else warnings).append(message)
        # A claimed ID is evidence only when the proposed region actually
        # intersects it. Models cannot satisfy coverage by listing all IDs.
        for candidate in page_info.get("candidates", []):
            if candidate["id"] not in candidate_ids:
                continue
            a, b, c, d = candidate["bbox"]
            overlap = max(0, min(c, x1) - max(a, x0)) * max(0, min(d, y1) - max(b, y0))
            if overlap >= 0.97 * max(1, (c-a)*(d-b)):
                accounted.add(candidate["id"])
            else:
                warnings.append("配图区域未完整覆盖其声明的候选，请核对裁剪。")
        review = item.get("review_required") is not False
        reason = str(item.get("review_reason") or "配图归属或范围仍需核对。")[:500]
        if review:
            warnings.append(reason)
        figures.append({
            "id": f"p{page_info['page_index'] + 1}-f{index + 1}",
            "bbox": bbox, "model_bbox": model_bbox, "native_box": native_box, **anchors, "candidate_ids": candidate_ids,
            "model_bbox_raw": item.get("model_bbox_raw", item.get("bbox")),
            "model_bbox_space": item.get("model_bbox_space", "page"),
            **({"model_region_bbox": item["model_region_bbox"]} if "model_region_bbox" in item else {}),
            "slot_id": slot_id if slot else "", "review_required": review,
            "review_reason": reason if review else "", "review_reasons": list(warnings[warning_start:]),
        })
    ignored = result.get("ignored_candidates", [])
    if not isinstance(ignored, list):
        raise ValueError("未采用候选列表格式无效。")
    for item in ignored:
        if (isinstance(item, dict) and isinstance(item.get("id"), str)
                and item.get("id") in known
                and item.get("reason") in {"formula", "table_border", "decoration", "page_background"}):
            accounted.add(item["id"])
            candidate = next(candidate for candidate in page_info["candidates"] if candidate["id"] == item["id"])
            a, b, c, d = candidate["bbox"]
            if (c - a) * (d - b) >= 2500:
                if candidate.get("type") == "raster":
                    warnings.append("原生位图被判为非配图，请对照本页确认没有遗漏图片。")
                else:
                    notes.append("已区分原生公式、表格边框或背景区域，未将其作为配图插入。")
    declared = {identifier for figure in figures for identifier in figure["candidate_ids"]}
    if known - accounted - declared:
        warnings.append("部分原生图形候选未被解释，请检查是否漏图。")
    if isinstance(result.get("notes"), list):
        for note in result["notes"][:8]:
            if note:
                target = warnings if re.search(r"无法|遗漏|缺失|错乱|失败|不完整|待核对", str(note)) else notes
                target.append(str(note)[:500])
    return figures, list(dict.fromkeys(warnings))


def clear_resolved_figure_placeholders(markdown: str, figures: list[dict]) -> str:
    """Consume at most one slot explicitly bound to each attached figure."""
    placeholder = r"\[插图待补(?:\s*[:：]\s*[^\]\n]{0,80})?\]"
    for figure in figures:
        before = re.search("(" + placeholder + r")\s*$", figure.get("anchor_before", ""))
        after = re.match(r"\s*(" + placeholder + ")", figure.get("anchor_after", ""))
        # Two neighboring slots are ambiguous: a single image must never erase
        # both, or guess which of the two has been filled.
        if bool(before) == bool(after):
            continue
        image = r"!\[插图\]\(" + re.escape(figure["image_path"]) + r"\)"
        if before:
            markdown = re.sub(re.escape(before.group(1)) + r"(\s*)(" + image + ")", r"\1\2", markdown, count=1)
        else:
            markdown = re.sub("(" + image + r")(\s*)" + re.escape(after.group(1)), r"\1\2", markdown, count=1)
    return markdown


def enrich_pdf_with_figures(
    file_bytes: bytes, page_indices: list[int], page_images: list[str],
    page_urls: list[str], page_texts: list[str], ocr_page_indices: set[int], *,
    output_dir: Path, url_prefix: str, task_id: str,
    check_cancelled: Callable[[], None], register_asset: Callable[[str], None],
    report_progress: Callable[[int, str], None],
    precomputed_layouts: dict[int, dict] | None = None,
) -> dict:
    """Enrich each page independently and preserve evidence on any failure."""
    import pymupdf as fitz

    texts = list(page_texts)
    report = {"pages_checked": 0, "figures_extracted": 0, "figures_attached": 0,
              "unmatched_figures": 0, "visual_calls": 0, "skipped_pages": 0,
              "review_pages": 0, "warnings": [], "notes": [], "usage": {}, "joint_visual_calls": 0}
    pages, issues = [], []
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        for local_index, page_index in enumerate(page_indices):
            check_cancelled()
            page = document[page_index]
            source = texts[local_index] or ""
            page_state = {"page_index": page_index, "page_number": page_index + 1,
                          "page_image": page_urls[local_index], "figures": [],
                          "source": "ocr" if page_index in ocr_page_indices else "native",
                          "rotation": page.rotation, "status": "skipped", "warnings": []}
            page_warnings = []
            page_notes = []
            unmatched = []
            try:
                info = inspect_pdf_page(page, page_index)
                info["figure_slots"] = describe_figure_slots(source, page_index)
                page_state.update({key: info[key] for key in ("width", "height", "candidates")})
                if not info.get("needs_visual") and page_index not in ocr_page_indices and "插图待补" not in source:
                    report["skipped_pages"] += 1
                    pages.append(page_state)
                    continue
                report_progress(local_index, f"正在核对第 {page_index + 1} 页的配图范围与位置...")
                for message in info.get("warnings", []):
                    (page_notes if _is_processing_note(message) else page_warnings).append(message)
                if precomputed_layouts is not None and page_index in precomputed_layouts:
                    result = precomputed_layouts[page_index]
                    report["joint_visual_calls"] += 1
                else:
                    result = request_pdf_layout(page_images[local_index], source, info,
                                                diagnostics=report, check_cancelled=check_cancelled)
                check_cancelled()
                figures, warnings = _validate_layout(result, info, notes=page_notes)
                page_warnings.extend(warnings)
                report["pages_checked"] += 1
                for figure in figures:
                    check_cancelled()
                    if info.get("full_page_image") and not figure.get("native_box"):
                        from mathbank.pdf_raster_guard import guard_raster_figure_bbox
                        guarded = guard_raster_figure_bbox(page_images[local_index], figure["model_bbox"])
                        check_cancelled()
                        figure["raster_guard"] = {
                            key: value for key, value in guarded.items() if key not in {"warnings", "notes"}
                        }
                        if guarded.get("changed"):
                            figure["bbox"] = guarded["bbox"]
                        guard_warnings = list(guarded.get("warnings", []))
                        if guard_warnings:
                            figure["review_required"] = True
                            figure["review_reasons"].extend(guard_warnings)
                            page_warnings.extend(guard_warnings)
                        page_notes.extend(guarded.get("notes", []))
                        # A repaired box must not silently become a duplicate
                        # of another figure whose original model box differed.
                        x0, y0, x1, y1 = figure["bbox"]
                        for previous in page_state["figures"]:
                            a, b, c, d = previous["bbox"]
                            intersection = max(0, min(c, x1) - max(a, x0)) * max(0, min(d, y1) - max(b, y0))
                            union = (c-a)*(d-b) + (x1-x0)*(y1-y0) - intersection
                            if intersection / max(1, union) >= 0.8:
                                message = "图框校验后两幅配图高度重叠，请核对是否重复或对应错误。"
                                for affected in (previous, figure):
                                    affected["review_required"] = True
                                    affected["review_reasons"].append(message)
                                page_warnings.append(message)
                    if figure.get("slot_id"):
                        slot = next(slot for slot in info["figure_slots"] if slot["id"] == figure["slot_id"])
                        # The model selects a server-issued ID. The server owns
                        # its exact insertion point, including identical slots.
                        figure["anchor_before"] = source[:slot["end"]]
                        figure["anchor_after"] = ""
                    path = crop_pdf_figure(page, figure["bbox"], output_dir, url_prefix,
                                           f"pdf_figure_{task_id}_{page_index}_{figure['id']}")
                    register_asset(path)
                    check_cancelled()
                    figure["image_path"] = path
                    figure["page_index"] = page_index
                    page_state["figures"].append(figure)
                    report["figures_extracted"] += 1
                    # Native text inside a graphic must not be silently deleted:
                    # it can include genuine body text. Surface it for review.
                    if page_state["source"] == "native":
                        x0, y0, x1, y1 = figure["bbox"]
                        for block in info.get("text_blocks", []):
                            a, b, c, d = block["bbox"]
                            if x0 <= a and y0 <= b and c <= x1 and d <= y1 and str(block.get("text", "")).strip():
                                page_warnings.append("图框中包含原生文字，请核对图中字母是否完整、正文是否重复或被裁入。")
                                figure["review_reasons"].append(page_warnings[-1])
                                break
                anchored = apply_figure_anchors(source, page_state["figures"])
                attached = set(anchored["attached"])
                texts[local_index] = clear_resolved_figure_placeholders(
                    anchored["markdown"],
                    [figure for figure in page_state["figures"] if figure["id"] in attached],
                )
                for figure in page_state["figures"]:
                    figure["attached"] = figure["id"] in attached
                report["figures_attached"] += len(attached)
                unmatched = anchored["unmatched"]
                report["unmatched_figures"] += len(unmatched)
                if unmatched:
                    page_warnings.append(f"{len(unmatched)} 幅图未找到唯一安全的原文位置，已保留供人工核对。")
                page_state["status"] = "checked"
            except TaskCancelled:
                raise
            except Exception as exc:
                # Never dump provider request/response/URLs/credentials in the UI.
                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                page_warnings.append(f"配图检查未完成（{detail[:300]}），请对照原页补图。")
                page_state["status"] = "review"
                # A later crop can fail after earlier files were registered.
                # Keep those files visible instead of losing their provenance.
                unmatched = [figure for figure in page_state["figures"] if not figure.get("attached")]
                report["unmatched_figures"] += len(unmatched)
            if page_warnings:
                report["review_pages"] += 1
                page_state["warnings"] = list(dict.fromkeys(page_warnings))
                report["warnings"].extend(f"第 {page_index + 1} 页：{message}" for message in page_state["warnings"])
                evidence = f"PDF 原页 {page_index + 1}\n\n![原页]({page_urls[local_index]})\n\n" + source
                for figure in unmatched:
                    evidence += f"\n\n![未归位配图]({figure['image_path']})"
                issues.append({"page_index": page_index, "reason": "；".join(page_state["warnings"]),
                               "source_excerpt": evidence,
                               "figure_ids": [figure["id"] for figure in page_state["figures"] if figure.get("review_reasons") or not figure.get("attached")],
                               "page_wide": bool(unmatched) or not page_state["figures"] or bool(set(page_state["warnings"]) - {
                                   message for figure in page_state["figures"] for message in figure.get("review_reasons", [])
                               } - {f"{len(unmatched)} 幅图未找到唯一安全的原文位置，已保留供人工核对。"})})
            page_state["notes"] = list(dict.fromkeys(page_notes))
            report["notes"].extend(f"第 {page_index + 1} 页：{message}" for message in page_state["notes"])
            pages.append(page_state)
    return {"schema": LAYOUT_SCHEMA, "page_texts": texts, "pages": pages,
            "diagnostics": report, "issues": issues}


def apply_pdf_layout_reviews(questions: list[dict], result: dict, diagnostics: dict) -> None:
    """Attach uncertainty to its source question; never broadcast across a paper."""
    issues = result.get("issues", [])
    if issues:
        diagnostics.setdefault("unmatched_source", []).extend(issues)
    source_ranges = []
    cursor = 0
    # Match exactly main's merge-then-remove-page-markers operation. Offsets
    # originate in the same source used by formula reconciliation, not a guess
    # based on output order or a model-supplied question number.
    for page, source in zip(result["pages"], result.get("page_texts", [])):
        value = re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->", "", str(source or "").strip())
        if not str(source or "").strip():
            continue
        if source_ranges:
            cursor += 2
        source_ranges.append((page["page_index"], cursor, cursor + len(value), value))
        cursor += len(value)
    matched_pages: dict[int, set[int]] = {}
    source_numbers: dict[int, set[int]] = {}
    matches = diagnostics.get("source_matches", [])
    for match in matches if isinstance(matches, list) else []:
        index, start, end = (match.get(key) for key in ("question_index", "source_start", "source_end"))
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (index, start, end)):
            continue
        matched_pages.setdefault(index, set()).update({
            page_index for page_index, left, right, value in source_ranges
            if max(start, left) < min(end, right)
            and value[max(start, left) - left:min(end, right) - left].strip()
        })
        number = match.get("source_number")
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            source_numbers.setdefault(index, set()).add(number)
    def origin_number(index):
        numbers = source_numbers.get(index, set())
        return next(iter(numbers)) if len(numbers) == 1 else None
    for issue in issues:
        issue["affected_questions"] = []
        issue["scope"] = "page_unassigned"
        issue.setdefault("diagnostic_reason", issue.get("reason", ""))
        issue["reason"] = "；".join(_review_explanations([issue["diagnostic_reason"]]))
    for index, question in enumerate(questions):
        body = str(question.get("content") or "") + "\n" + str(question.get("answer_markdown") or "")
        reasons, excerpts = [], []
        for issue in issues:
            page = next((page for page in result["pages"] if page["page_index"] == issue["page_index"]), {})
            affected_figures = [figure for figure in page.get("figures", [])
                                if figure["id"] in issue.get("figure_ids", [])]
            belongs = any(figure.get("image_path") and figure["image_path"] in body for figure in affected_figures)
            # If a page-level problem has a proven source match, only that
            # page's questions need confirmation. Unowned evidence stays once
            # in the global report instead of locking unrelated cards.
            if belongs or (issue.get("page_wide") and issue["page_index"] in matched_pages.get(index, set())):
                reasons.append(f"PDF 第 {issue['page_index'] + 1} 页：{issue['reason']}")
                excerpts.append(issue["source_excerpt"])
                issue["affected_questions"].append({"question_index": index, "source_number": origin_number(index)})
                if issue.get("page_wide") and not belongs:
                    issue["scope"] = "possible_questions"
                elif issue["scope"] == "page_unassigned":
                    issue["scope"] = "question"
        if _FIGURE_SLOT.search(body):
            reasons.append("本题仍有未补齐的插图，请对照原页补图或确认。")
        if reasons:
            review = question.setdefault("source_review", {})
            review["required"] = True
            review["reasons"] = list(dict.fromkeys(review.get("reasons", []) + reasons))
            if excerpts:
                review.setdefault("source_excerpt", excerpts[0])
        question["pdf_source_figures"] = [
            {key: figure[key] for key in ("id", "page_index", "bbox", "image_path")}
            for page in result["pages"] for figure in page["figures"]
            if figure.get("image_path") in body
        ]
    diagnostics["pdf_review_items"] = [
        {"question_index": index, "source_number": origin_number(index),
         "source_pages": sorted({page + 1 for page in matched_pages.get(index, set())}
                                | {figure["page_index"] + 1 for figure in question.get("pdf_source_figures", [])}),
         "reasons": _review_explanations(question["source_review"].get("reasons", []))}
        for index, question in enumerate(questions) if question.get("source_review", {}).get("required")
    ]
    diagnostics["source_review_count"] = sum(bool(q.get("source_review", {}).get("required")) for q in questions)


def isolate_shared_pdf_figures(
    questions: list[dict], result: dict, *, output_dir: Path, url_prefix: str,
    register_asset: Callable[[str], None], check_cancelled: Callable[[], None],
) -> None:
    """Give different cards independent files before the move-on-save boundary.

    Multiple references inside one card remain shared. Only this task's known
    generated PNGs may be copied; model-provided paths cannot select files.
    """
    known = {figure["image_path"] for page in result["pages"] for figure in page["figures"]}
    seen = set()
    image_refs = re.compile(
        r'!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:[ \t]+[^)]*)?\s*\)'
        r'|\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}'
    )
    for question in questions:
        body = str(question.get("content") or "") + "\n" + str(question.get("answer_markdown") or "")
        references = {next(value for value in match.groups() if value is not None)
                      for match in image_refs.finditer(body)} & known
        for path in references & seen:
            check_cancelled()
            original = output_dir / Path(path).name
            filename = "pdf_figure_shared_" + uuid.uuid4().hex + ".png"
            destination = output_dir / filename
            try:
                shutil.copyfile(original, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            replacement = url_prefix.rstrip("/") + "/" + filename
            register_asset(replacement)
            check_cancelled()
            for field in ("content", "answer_markdown"):
                question[field] = str(question.get(field) or "").replace(path, replacement)
            for figure in question.get("pdf_source_figures", []):
                if figure["image_path"] == path:
                    figure["image_path"] = replacement
        seen.update(references)
        # The text splitter cannot append images to a question via a parallel
        # resource list that bypasses source-position reconciliation.
        question["referenced_images"] = []
