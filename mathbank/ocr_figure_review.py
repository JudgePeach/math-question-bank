"""Bounded visual comparison of actual OCR crops and compiled TikZ previews.

Coordinate suggestions are proposals. Only a second view of the actual recrop
can verify a correction. Model identities and hashes never authorize file paths,
source edits, or changes to a different figure slot.
"""

from __future__ import annotations

import base64
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import stat
import requests

from PIL import Image

from mathbank.ai_http import post_chat_completion
from mathbank.ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_model_thinking_policy, apply_structured_output_policy
from mathbank.asset_security import resolve_upload_asset, normalize_raster_image
from mathbank.ocr_illustrations import MAX_OCR_FIGURES, _checked_bbox, prepare_ocr_figure_crop
from mathbank.pdf_vision_request import completion_content
from mathbank.prompts import SINGLE_OCR_FIGURE_REVIEW_INSTRUCTIONS, build_single_ocr_figure_review_prompt
from mathbank.ocr_raster_layout import source_pixel_bbox, normalized_bbox, context_bbox, raster_ink_components, compatible_ink_hint, bounded_bbox_change


REVIEW_TIMEOUT_SECONDS = AI_REQUEST_TIMEOUT_SECONDS
MAX_REVIEW_PNG_BYTES = 24 * 1024 * 1024
MAX_REVIEW_PIXELS = 16_000_000
_CHECKS = frozenset({"same_figure", "full_graphic", "labels_complete", "no_foreign_content", "correct_anchor"})
_FIELDS = frozenset({"id", "slot", "source_sha256", "crop_sha256", "decision", "checks", "corrected_bbox", "evidence"})


class FigureReviewFailure(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _failure_code(error: Exception) -> str:
    if isinstance(error, FigureReviewFailure): return error.code
    if isinstance(error, requests.Timeout): return "timeout"
    if isinstance(error, requests.RequestException): return "network_error"
    if isinstance(error, OSError): return "source_unavailable"
    return "protocol_invalid" if isinstance(error, ValueError) else "review_error"


def _png_dimensions(data: bytes) -> dict:
    with Image.open(io.BytesIO(data)) as image:
        return {"width": image.width, "height": image.height}


def _enrich_record(record: dict, image: Image.Image, crop: bytes | None, components: list[dict], contexts: dict) -> dict:
    record = {**record, "source_dimensions": {"width": image.width, "height": image.height},
              "crop_dimensions": _png_dimensions(crop) if crop is not None else None,
              "bbox_pixels": source_pixel_bbox(record["bbox"], image.size) if record["bbox"] is not None else None,
              "bbox_coordinate_space": "full_source_0_1000", "raster_candidates": [], "source_context": None}
    if record["bbox"] is not None:
        pixels = context_bbox(record["bbox"], image.size)
        context = image.crop(tuple(pixels[name] for name in ("left", "top", "right", "bottom")))
        buffer = io.BytesIO()
        context.save(buffer, format="PNG")
        data = normalize_raster_image(buffer.getvalue()).data
        contexts[record["id"]] = data
        record["source_context"] = {"sha256": _sha(data), "bbox_pixels": pixels,
                                    "bbox": normalized_bbox(pixels, image.size), "dimensions": _png_dimensions(data)}
        record["raster_candidates"] = compatible_ink_hint(record["bbox"], components, image.size)
    return record


def _log_review(stage: str, entries: dict, code: str = "") -> None:
    counts = {decision: sum(entry["decision"] == decision for entry in entries.values()) for decision in ("accepted", "correct", "uncertain")}
    print(f"[OCR Figure Review] stage={stage} records={len(entries)} model_accepted={counts['accepted']} "
          f"model_correct={counts['correct']} model_uncertain={counts['uncertain']} failure_code={code or 'none'}", flush=True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _image_part(data: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}}


def _file_identity(value) -> tuple:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _read_snapshot(path: Path) -> dict:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_REVIEW_PNG_BYTES:
        raise ValueError("核对图片不是有界普通文件。")
    with path.open("rb") as stream:
        data = stream.read(MAX_REVIEW_PNG_BYTES + 1)
    after = path.lstat()
    if len(data) != before.st_size or _file_identity(before) != _file_identity(after):
        raise ValueError("核对图片在读取期间已变化。")
    return {"path": path, "identity": _file_identity(before), "data": data, "sha256": _sha(data)}


def _unchanged(snapshot: dict) -> bool:
    try:
        current = _read_snapshot(snapshot["path"])
        return current["identity"] == snapshot["identity"] and current["sha256"] == snapshot["sha256"]
    except (OSError, ValueError):
        return False


def _checked_reply(raw: str, metadata: dict, *, preview: bool) -> dict[str, dict]:
    result = parse_ai_json(raw)
    if not isinstance(result, dict) or set(result) != {"figures"} or not isinstance(result["figures"], list):
        raise ValueError("插图视觉核对结果结构无效。")
    records = {record["id"]: record for record in metadata["figures"]}
    if len(result["figures"]) > len(records):
        raise ValueError("插图视觉核对返回了过多记录。")
    checked = {}
    expected_checks = _CHECKS | {"geometry_faithful"} if preview else _CHECKS
    for entry in result["figures"]:
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            raise ValueError("插图视觉核对字段无效。")
        identifier = entry["id"]
        if not isinstance(identifier, str) or identifier not in records or identifier in checked:
            raise FigureReviewFailure("identity_mismatch")
        expected = records[identifier]
        if any(entry[key] != expected[key] for key in ("slot", "source_sha256", "crop_sha256")):
            raise FigureReviewFailure("identity_mismatch")
        checks = entry["checks"]
        if not isinstance(checks, dict) or set(checks) != expected_checks or any(type(value) is not bool for value in checks.values()):
            raise ValueError("插图视觉核对检查项无效。")
        evidence = entry["evidence"]
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 500:
            raise ValueError("插图视觉核对缺少具体观察。")
        decision = entry["decision"]
        allowed = {"accepted", "uncertain"} if preview else {"accepted", "correct", "uncertain"}
        if decision not in allowed:
            raise ValueError("插图视觉核对判定无效。")
        if decision == "correct":
            bbox = _checked_bbox(entry["corrected_bbox"])
            if bbox is None:
                raise ValueError("插图修正缺少命名图框。")
        elif entry["corrected_bbox"] is not None:
            raise ValueError("插图核对无权修改此图框。")
        checked[identifier] = deepcopy(entry)
    return checked


def request_figure_review(provider, metadata: dict, *, original_png: bytes | None,
                          crop_pngs: dict[str, bytes], source_pngs: dict[str, bytes] | None = None,
                          context_pngs: dict[str, bytes] | None = None,
                          post=None, semaphore=None, on_post=None) -> dict:
    """One physical review POST; callable without importing the app/database."""
    records = metadata.get("figures")
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_OCR_FIGURES:
        raise ValueError("插图核对清单为空或超过20幅。")
    preview = metadata.get("stage") == "tikz_preview"
    if not (getattr(provider, "api_key", None) and getattr(provider, "chat_completions_url", None)
            and getattr(provider, "supports_image_input", False)):
        raise FigureReviewFailure("provider_unavailable")
    known = {record["id"] for record in records}
    if len(known) != len(records) or set(crop_pngs) - known or set(source_pngs or {}) - known or set(context_pngs or {}) - known:
        raise ValueError("插图核对输入身份无效。")
    images = ([original_png] if original_png is not None else []) + list(crop_pngs.values()) + list((source_pngs or {}).values()) + list((context_pngs or {}).values())
    if not images or sum(len(data) for data in images) > MAX_REVIEW_PNG_BYTES:
        raise FigureReviewFailure("input_budget_exceeded")
    pixels = 0
    for data in images:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG":
                raise ValueError("插图核对只接受内部PNG。")
            pixels += image.width * image.height
    if pixels > MAX_REVIEW_PIXELS:
        raise FigureReviewFailure("input_budget_exceeded")
    for record in records:
        source = (source_pngs or {}).get(record["id"]) if preview else original_png
        crop = crop_pngs.get(record["id"])
        if source is None or record["source_sha256"] != _sha(source) or record["crop_sha256"] != (_sha(crop) if crop is not None else None):
            raise FigureReviewFailure("input_identity_mismatch")
        context = (context_pngs or {}).get(record["id"])
        if context is not None and (not isinstance(record.get("source_context"), dict)
                or record["source_context"].get("sha256") != _sha(context)
                or record["source_context"].get("dimensions") != _png_dimensions(context)):
            raise FigureReviewFailure("input_identity_mismatch")
    content = [{"type": "text", "text": build_single_ocr_figure_review_prompt(metadata, preview=preview)}]
    if original_png is not None:
        content.extend([{"type": "text", "text": "整题原图（唯一全图坐标基准）"}, _image_part(original_png)])
    for record in records:
        identifier = record["id"]
        if preview:
            content.extend([{"type": "text", "text": f"{identifier} / {record['slot']} 已核对的原始裁图"}, _image_part(source_pngs[identifier])])
        crop = crop_pngs.get(identifier)
        content.append({"type": "text", "text": f"{identifier} / {record['slot']} 本次实际{'TikZ预览' if preview else '裁图'}" if crop is not None else f"{identifier} / {record['slot']} 暂无可用裁图，请从原图提出图框或保留核对"})
        if crop is not None:
            content.append(_image_part(crop))
        context = (context_pngs or {}).get(identifier)
        if context is not None:
            content.extend([{"type": "text", "text": f"{identifier} / {record['slot']} SOURCE辅助上下文（只供定位，不是交付裁图）"}, _image_part(context)])
    payload = {"model": provider.model_name, "messages": [{"role": "user", "content": content}],
               "max_tokens": 16384, "stream": False}
    payload = apply_model_thinking_policy(payload, provider=provider, task="ocr")
    payload = apply_structured_output_policy(payload, provider=provider, system_instruction=SINGLE_OCR_FIGURE_REVIEW_INSTRUCTIONS)
    with semaphore if semaphore is not None else nullcontext():
        if on_post is not None:
            on_post()
        response = (post or post_chat_completion)(provider, payload, timeout=REVIEW_TIMEOUT_SECONDS,
            check_status=False, retry_connection=False, allow_redirects=False)
    if response.status_code != 200:
        status = response.status_code
        code = "http_5xx" if type(status) is int and 500 <= status <= 599 else f"http_{status}" if type(status) is int and 100 <= status <= 599 else "http_error"
        raise FigureReviewFailure(code)
    try:
        body = response.json()
    except Exception as error:
        raise FigureReviewFailure("invalid_transport_json") from error
    try:
        raw = completion_content(body, "插图视觉核对", max_chars=50_000)
    except ValueError as error:
        choices = body.get("choices") if isinstance(body, dict) else None
        truncated = bool(isinstance(choices, list) and choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "length")
        raise FigureReviewFailure("response_truncated" if truncated else "response_incomplete") from error
    return _checked_reply(raw, metadata, preview=preview)


def _record_post(summary: dict, *, preview=False) -> None:
    summary["calls"] += 1
    summary["preview_calls" if preview else "crop_calls"] += 1


def _area(bbox: dict) -> float:
    return (bbox["right"] - bbox["left"]) * (bbox["bottom"] - bbox["top"])


def _overlaps(first: dict, second: dict) -> bool:
    overlap = max(0, min(first["right"], second["right"]) - max(first["left"], second["left"])) * max(0, min(first["bottom"], second["bottom"]) - max(first["top"], second["top"]))
    return overlap > .15 * min(_area(first), _area(second))


def _safe_box(bbox: dict, original: dict | None, identifier: str, others: dict[str, dict], dimensions=None) -> bool:
    if any(other_id != identifier and other is not None and _overlaps(bbox, other) for other_id, other in others.items()):
        return False
    if original is not None and bbox != original:
        if _area(bbox) > _area(original) * 2.2:
            return False
        if not bounded_bbox_change(original, bbox, dimensions or (1000, 1000)):
            return False
    return True


def _review_data(*, status: str, source_hash: str, crop_hash: str | None, corrected=False,
                 evidence="", checks=None, attempts=0) -> dict:
    return {"status": status, "corrected": corrected, "evidence": evidence,
            "source_sha256": source_hash, "crop_sha256": crop_hash,
            "checks": dict(checks or {}), "attempts": attempts}


def review_single_ocr_figures(result: dict, image_path: str | Path, provider, *, post=None, semaphore=None) -> dict:
    """Initial batch plus at most one batch of actual corrected crops."""
    reviewed = deepcopy(result)
    items = reviewed["illustrations"]
    initial_messages = {item["id"]: item["message"] for item in items}
    summary = {"status": "not_needed", "calls": 0, "crop_calls": 0, "preview_calls": 0,
               "verified": 0, "total": len(items), "corrected": 0, "warnings": [], "failures": []}
    reviewed["illustration_review"] = summary
    reviewed["_verified_crops"] = {}
    if not items:
        return reviewed
    try:
        snapshot = _read_snapshot(Path(image_path))
        source_png, source_hash = snapshot["data"], snapshot["sha256"]
        with Image.open(io.BytesIO(source_png)) as opened:
            if opened.width * opened.height > MAX_REVIEW_PIXELS:
                raise FigureReviewFailure("input_budget_exceeded")
            image = opened.convert("RGB")
    except (OSError, ValueError) as error:
        summary["status"] = "unavailable"
        code = _failure_code(error)
        for item in items:
            item.update(status="unresolved", message="原图核对证据不可用，已保留插图占位。")
            item["review"] = _review_data(status="unavailable", source_hash="", crop_hash=None)
            item["review"].update(stage="crop_initial", failure_code=code, failed_checks=[], bbox=deepcopy(item["bbox"]))
            summary["failures"].append({"id": item["id"], "slot": item["slot"], "stage": "crop_initial", "failure_code": code})
        reviewed["warnings"].append("原图核对证据不可用，请对照原图手动截图。")
        _log_review("crop_initial", {}, code)
        return reviewed
    eligible = [item for item in items if item.get("anchor_valid") is True]
    records, crops, contexts = [], {}, {}
    components = raster_ink_components(image)
    for ordinal, item in enumerate(items, 1):
        item["status"] = "unresolved"
        item["review"] = _review_data(status="unverified", source_hash=source_hash, crop_hash=None)
        item["review"].update(stage="crop_initial", failure_code="anchor_invalid" if item not in eligible else "", failed_checks=[], bbox=deepcopy(item["bbox"]))
        if item not in eligible:
            continue
        crop = None
        if item["bbox"] is not None:
            try:
                crop = prepare_ocr_figure_crop(image, item["bbox"])
            except (ValueError, OSError):
                pass
        if crop is not None:
            crops[item["id"]] = crop
        item["review"]["crop_sha256"] = _sha(crop) if crop is not None else None
        record = {"id": item["id"], "slot": item["slot"], "source_sha256": source_hash,
                        "crop_sha256": item["review"]["crop_sha256"], "bbox": deepcopy(item["bbox"]),
                        "ordinal": ordinal, "context_before": reviewed["markdown"][max(0, item["start"] - 350):item["start"]],
                        "placeholder": item["placeholder"], "context_after": reviewed["markdown"][item["end"]:item["end"] + 350]}
        record = _enrich_record(record, image, crop, components, contexts)
        records.append(record)
        item["review"].update({key: record[key] for key in ("source_dimensions", "crop_dimensions", "bbox_pixels", "source_context", "raster_candidates")})
    if not records:
        summary["status"] = "partial"
        summary["failures"] = [{"id": item["id"], "slot": item["slot"], "stage": "crop_initial", "failure_code": "anchor_invalid"} for item in items]
        _log_review("crop_initial", {}, "anchor_invalid")
        return reviewed
    available = bool(getattr(provider, "api_key", None) and getattr(provider, "chat_completions_url", None) and getattr(provider, "supports_image_input", False))
    first = {}
    failure = False
    failure_code = "provider_unavailable" if not available else ""
    if available:
        try:
            first = request_figure_review(provider, {"stage": "crop_initial", "markdown": reviewed["markdown"], "figures": records},
                                          original_png=source_png, crop_pngs=crops, context_pngs=contexts, post=post, semaphore=semaphore,
                                          on_post=lambda: _record_post(summary))
            if not _unchanged(snapshot):
                raise FigureReviewFailure("source_changed")
        except Exception as error:
            first = {}
            failure = True
            failure_code = _failure_code(error)
    else:
        failure = True
    _log_review("crop_initial", first, failure_code)
    all_boxes = {item["id"]: item["bbox"] for item in items}
    proposals, accepted = {}, {}
    for item in eligible:
        entry = first.get(item["id"])
        item["review"]["attempts"] = summary["crop_calls"]
        if entry is None:
            item["review"]["status"] = "unavailable" if failure else "unverified"
            item["review"]["failure_code"] = failure_code or "missing_figure"
            continue
        failed_checks = [name for name, passed in entry["checks"].items() if not passed]
        item["review"].update(evidence=entry["evidence"], checks=entry["checks"], failed_checks=failed_checks,
                             failure_code="model_uncertain" if entry["decision"] == "uncertain" else "checks_failed" if failed_checks else "bbox_rejected")
        if (entry["decision"] == "accepted" and all(entry["checks"].values())
                and item["id"] in crops and _safe_box(item["bbox"], item["bbox"], item["id"], all_boxes, image.size)):
            accepted[item["id"]] = (item["bbox"], crops[item["id"]], entry, False)
        elif entry["decision"] == "correct" and entry["checks"]["same_figure"] and entry["checks"]["correct_anchor"]:
            proposed = _checked_bbox(entry["corrected_bbox"])
            item["review"].update(proposed_bbox=deepcopy(proposed), failure_code="bbox_rejected")
            if _safe_box(proposed, item["bbox"], item["id"], all_boxes, image.size):
                proposals[item["id"]] = proposed
                item["review"]["failure_code"] = "correction_pending"
    if proposals:
        competing = {**all_boxes, **proposals}
        proposals = {identifier: bbox for identifier, bbox in proposals.items() if _safe_box(bbox, all_boxes[identifier], identifier, competing, image.size)}
    second_records, second_crops, second_contexts = [], {}, {}
    for record in records:
        identifier = record["id"]
        if identifier not in proposals:
            continue
        try:
            crop = prepare_ocr_figure_crop(image, proposals[identifier])
        except (ValueError, OSError):
            continue
        second_crops[identifier] = crop
        second_records.append(_enrich_record({**record, "bbox": proposals[identifier], "crop_sha256": _sha(crop)}, image, crop, components, second_contexts))
    second_failure = ""
    if second_records:
        try:
            second = request_figure_review(provider, {"stage": "crop_corrected", "markdown": reviewed["markdown"], "figures": second_records},
                                           original_png=source_png, crop_pngs=second_crops, context_pngs=second_contexts, post=post, semaphore=semaphore,
                                           on_post=lambda: _record_post(summary))
            if not _unchanged(snapshot):
                accepted = {}
                raise FigureReviewFailure("source_changed")
        except Exception as error:
            second = {}
            second_failure = _failure_code(error)
        _log_review("crop_corrected", second, second_failure)
        for item in eligible:
            if item["id"] not in second_crops:
                continue
            item["review"].update(attempts=summary["crop_calls"], corrected=True, crop_sha256=_sha(second_crops[item["id"]]),
                                 stage="crop_corrected", failure_code=second_failure or "missing_figure", failed_checks=[], bbox=deepcopy(proposals[item["id"]]))
            entry = second.get(item["id"])
            if entry is not None:
                item["review"].update(evidence=entry["evidence"], checks=entry["checks"],
                                     failed_checks=[name for name, passed in entry["checks"].items() if not passed],
                                     failure_code="model_uncertain" if entry["decision"] == "uncertain" else "checks_failed")
                if entry["decision"] == "accepted" and all(entry["checks"].values()):
                    accepted[item["id"]] = (proposals[item["id"]], second_crops[item["id"]], entry, True)
    # Even a failed/malformed second response may have changed source pixels
    # while the first batch's accepted evidence was waiting to be published.
    if not _unchanged(snapshot):
        accepted = {}
        failure = True
        for item in items: item["review"]["failure_code"] = "source_changed"
    for item in items:
        proof = accepted.get(item["id"])
        if proof is None:
            item["message"] = "插图视觉核对未通过或未完成，已保留原位占位，可对照原图手动截图。"
            reviewed["warnings"].append(f"{item['slot'] or '插图'}：{item['message']}")
            continue
        bbox, crop, entry, corrected = proof
        item.update(bbox=deepcopy(bbox), status="pending", message="")
        obsolete = f"{item['slot'] or '插图'}：{initial_messages[item['id']]}"
        reviewed["warnings"] = [message for message in reviewed["warnings"] if message != obsolete]
        item["review"] = _review_data(status="verified", source_hash=source_hash, crop_hash=_sha(crop),
            corrected=corrected, evidence=entry["evidence"], checks=entry["checks"], attempts=2 if corrected else 1)
        record = next(record for record in (second_records if corrected else records) if record["id"] == item["id"])
        item["review"].update(stage="crop_corrected" if corrected else "crop_initial", failure_code="", failed_checks=[], bbox=deepcopy(bbox),
            **{key: record[key] for key in ("source_dimensions", "crop_dimensions", "bbox_pixels", "source_context", "raster_candidates")})
        reviewed["_verified_crops"][item["id"]] = {"id": item["id"], "slot": item["slot"], "bbox": deepcopy(bbox),
                                                   "source_sha256": source_hash, "crop_sha256": _sha(crop)}
    summary["verified"] = len(accepted)
    summary["corrected"] = sum(bool(item["review"]["corrected"]) for item in items if item["review"]["status"] == "verified")
    summary["status"] = "verified" if len(accepted) == len(items) else "unavailable" if failure else "partial"
    summary["warnings"] = list(dict.fromkeys(message for message in reviewed["warnings"] if message not in result["warnings"]))
    summary["failures"] = [{key: item["review"].get(key) for key in ("stage", "failure_code", "failed_checks", "bbox", "proposed_bbox")}
                           | {"id": item["id"], "slot": item["slot"]} for item in items if item["review"]["status"] != "verified"]
    print(f"[OCR Figure Review] stage=crop_final calls={summary['crop_calls']} figures={len(items)} verified={summary['verified']} corrected={summary['corrected']} failed={len(summary['failures'])}", flush=True)
    reviewed["warnings"] = list(dict.fromkeys(reviewed["warnings"]))
    return reviewed


def review_tikz_previews(result: dict, provider, *, upload_root, url_prefix: str,
                         summary: dict, post=None, semaphore=None) -> dict:
    """One final batch; unverified drawings fall back to the verified crop."""
    output = deepcopy(result)
    output["illustration_review"] = deepcopy(summary)
    items = [item for item in output["illustrations"] if item["status"] == "tikz"]
    if not items:
        return output
    records, sources, previews, snapshots = [], {}, {}, {}
    source_valid = {}
    for item in items:
        item["preview_review"] = _review_data(status="unverified", source_hash="", crop_hash=None)
        item["preview_review"].update(stage="tikz_preview", failure_code="input_invalid", failed_checks=[])
        try:
            source_snapshot = _read_snapshot(resolve_upload_asset(item["reference_image_path"], uploads_dir=upload_root, url_prefix=url_prefix))
            preview_snapshot = _read_snapshot(resolve_upload_asset(item["image_path"], uploads_dir=upload_root, url_prefix=url_prefix))
            source, preview = source_snapshot["data"], preview_snapshot["data"]
            if _sha(source) != item["review"]["crop_sha256"]:
                raise ValueError("原始裁图已变化。")
            source_valid[item["id"]] = True
            snapshots[item["id"]] = (source_snapshot, preview_snapshot)
            sources[item["id"]], previews[item["id"]] = source, preview
            records.append({"id": item["id"], "slot": item["slot"], "source_sha256": _sha(source),
                            "crop_sha256": _sha(preview), "bbox": None, "kind": "tikz_preview",
                            "source_dimensions": _png_dimensions(source), "crop_dimensions": _png_dimensions(preview)})
            item["preview_review"].update(source_sha256=_sha(source), crop_sha256=_sha(preview))
        except Exception:
            source_valid[item["id"]] = False
    reviewed = {}
    failure = False
    failure_code = ""
    if records:
        try:
            reviewed = request_figure_review(provider, {"stage": "tikz_preview", "figures": records}, original_png=None,
                                             crop_pngs=previews, source_pngs=sources, post=post, semaphore=semaphore,
                                             on_post=lambda: _record_post(output["illustration_review"], preview=True))
        except Exception as error:
            failure = True
            failure_code = _failure_code(error)
    _log_review("tikz_preview", reviewed, failure_code)
    verified = 0
    rejected_ids = set()
    for item in items:
        entry = reviewed.get(item["id"])
        if item["id"] in snapshots:
            source_snapshot, preview_snapshot = snapshots[item["id"]]
            source_valid[item["id"]] = _unchanged(source_snapshot)
            if not source_valid[item["id"]] or not _unchanged(preview_snapshot):
                entry = None
                item["preview_review"]["failure_code"] = "source_changed" if not source_valid[item["id"]] else "preview_changed"
            if not source_valid[item["id"]]:
                item["review"] = {**item["review"], "status": "unverified"}
        if entry is not None:
            item["preview_review"].update(evidence=entry["evidence"], checks=entry["checks"], attempts=1,
                                         failed_checks=[name for name, passed in entry["checks"].items() if not passed],
                                         failure_code="model_uncertain" if entry["decision"] == "uncertain" else "checks_failed")
        if entry is not None and entry["decision"] == "accepted" and all(entry["checks"].values()):
            item["preview_review"]["status"] = "verified"
            item["preview_review"]["failure_code"] = ""
            verified += 1
            continue
        item["preview_review"]["status"] = "unavailable" if failure else "unverified"
        if entry is None and item["preview_review"]["failure_code"] not in {"source_changed", "preview_changed"}:
            item["preview_review"]["failure_code"] = failure_code or "missing_figure"
        rejected_ids.add(item["id"])
        previous = item["image_path"]
        fallback = item["reference_image_path"] if source_valid.get(item["id"]) else ""
        output["markdown"] = output["markdown"].replace(f"![]({previous})", f"![]({fallback})" if fallback else item["placeholder"])
        layout = output["image_layouts"].pop(previous, None)
        if fallback and layout is not None:
            output["image_layouts"][fallback] = layout
        item.update(status="cropped" if fallback else "unresolved", image_path=fallback,
                    tikz_code="", tikz_image_path="", reference_image_path="",
                    message="TikZ预览视觉核对未通过或未完成，已保留经核对的原始截图。" if fallback else "原图证据已变化，已保留插图占位，请手动核对。")
        output["warnings"].append(f"{item['slot']}：{item['message']}")
        output["illustration_review"].setdefault("failures", []).append({"id": item["id"], "slot": item["slot"],
            **{key: item["preview_review"].get(key) for key in ("stage", "failure_code", "failed_checks")}})
    output["tikz_assets"] = [asset for asset in output["tikz_assets"] if asset["id"] not in rejected_ids]
    output["illustration_review"].update(preview_verified=verified, preview_total=len(items))
    output["warnings"] = list(dict.fromkeys(output["warnings"]))
    return output
