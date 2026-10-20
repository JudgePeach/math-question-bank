"""Reissue first-pass integrity witnesses from server-owned sealed OCR caches.

No caller-provided JSON or confidence flag is an authority. The original PDF,
checkpoint, successful request records and original input images must still be
available. This preserves a machine transcript, never certifies PDF accuracy.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import re

from PIL import Image

from mathbank.asset_lifecycle import ASSET_LIFECYCLE_LOCK
from mathbank.document_projects import DocumentProjectStore, DocumentProjectError
from mathbank.pdf_task_resume import prepare_pdf_checkpoint
from mathbank.pdf_transcript_quality import assess_pdf_transcript_quality
from mathbank.pdf_transcription_scopes import begin_pdf_transcription_witness, finish_pdf_transcription_witness
from mathbank.pdf_vision_details import prepare_detail_messages
from mathbank.task_manager import TaskCancelled


SCHEMA = "mathbank.pdf-checkpoint-transcription-witness.v1"
_NAV = re.compile(r"pdf_page_([A-Za-z0-9_-]+)_([0-9]+)\.png")
_DETAIL = re.compile(r"pdf_detail_([A-Za-z0-9_-]+)_([0-9]+)_(p[0-9]+_d[12])_[0-9a-f]{32}\.png")
_SHA = re.compile(r"[0-9a-f]{64}")
_DETAIL_FIELDS = {"id", "page_index", "bbox", "actualdpi", "width", "height", "sha256"}


class _SkipPage(ValueError):
    pass


def _exclusions(values):
    if not isinstance(values, (list, tuple, set, frozenset, dict)):
        raise ValueError("页面排除范围必须为页号集合。")
    if any(type(number) is not int or number < 1 for number in values):
        raise ValueError("页面排除范围包含无效原页号。")
    return set(values)


def _original_asset(url, resolve_asset):
    """Require the original URL spelling, never a substitute current thumbnail."""
    if not isinstance(url, str) or not url.startswith("/static/") or "\\" in url or "%" in url:
        raise ValueError("original_asset_url_invalid")
    parts = PurePosixPath(url).parts
    if any(part in {".", ".."} for part in parts):
        raise ValueError("original_asset_url_invalid")
    path = Path(resolve_asset(url))
    if (not path.is_absolute() or ".." in path.parts or any(parent.is_symlink() for parent in path.parents)
            or path.name != PurePosixPath(url).name or path.is_symlink() or not path.is_file()):
        raise ValueError("original_asset_missing_or_substituted")
    return path


def _png(path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise _SkipPage("original_navigation_invalid")
    data = path.read_bytes()
    with Image.open(BytesIO(data)) as image:
        if (image.format != "PNG" or getattr(image, "n_frames", 1) != 1
                or image.width * image.height > 30_000_000):
            raise _SkipPage("original_navigation_invalid")
        image.verify()
    return data


def _successful_original_joint(joint, index):
    if (not isinstance(joint, dict) or not isinstance(joint.get("markdown"), str)
            or not joint["markdown"].strip() or not isinstance(joint.get("layout"), dict)):
        raise _SkipPage("original_joint_missing")
    quality = joint.get("transcript_quality")
    if (joint.get("ocr_succeeded") is False
            or isinstance(quality, dict) and (quality.get("retryable_failure") is True
                or quality.get("failure_codes") or quality.get("ocr_succeeded") is False
                or quality.get("status") == "retained_for_manual_review")
            or assess_pdf_transcript_quality(joint["markdown"])["retryable_failure"]):
        raise _SkipPage("original_joint_quality_failed")
    attempts = joint.get("attempts")
    if (not isinstance(attempts, list) or not 1 <= len(attempts) <= 2
            or type(joint.get("visual_calls")) is not int or joint["visual_calls"] != len(attempts)):
        raise _SkipPage("successful_request_evidence_missing")
    for number, attempt in enumerate(attempts, 1):
        if (not isinstance(attempt, dict) or attempt.get("stage") != "joint_page"
                or type(attempt.get("page_index")) is not int or attempt["page_index"] != index
                or type(attempt.get("attempt")) is not int or attempt["attempt"] != number
                or attempt.get("status") != ("succeeded" if number == len(attempts) else "failed")):
            raise _SkipPage("successful_request_evidence_missing")
    success = attempts[-1]
    if (type(success.get("http_status")) is not int or success["http_status"] != 200
            or success.get("finish_reason") != "stop"
            or type(success.get("response_characters")) is not int or success["response_characters"] < 1
            or not isinstance(success.get("response_sha256"), str) or not _SHA.fullmatch(success["response_sha256"])):
        raise _SkipPage("successful_request_evidence_missing")
    return success


def _original_inputs(state, joint, index, resolve_asset, check_cancelled):
    detail_input = joint.get("detail_input")
    if not isinstance(detail_input, dict):
        raise _SkipPage("original_input_evidence_missing")
    status = detail_input.get("status")
    views = detail_input.get("detail_views")
    count = detail_input.get("detail_count")
    if status not in {"none", "skipped", "included"} or not isinstance(views, list) or type(count) is not int:
        raise _SkipPage("original_input_evidence_missing")
    assets = state["assets"]
    details, owners, identifiers = [], set(), set()
    if status == "included":
        if count != len(views) or not 1 <= count <= 2:
            raise _SkipPage("original_detail_evidence_missing")
        for view in views:
            check_cancelled()
            if (not isinstance(view, dict) or set(view) != _DETAIL_FIELDS
                    or view.get("page_index") != index or type(view.get("page_index")) is not int
                    or view.get("id") not in {f"p{index + 1}_d1", f"p{index + 1}_d2"}
                    or view["id"] in identifiers or not isinstance(view.get("sha256"), str)):
                raise _SkipPage("original_detail_evidence_missing")
            matches = []
            for url, receipt in assets.items():
                identity = _DETAIL.fullmatch(PurePosixPath(url).name)
                if (identity and int(identity[2]) == index and identity[3] == view["id"]
                        and receipt.get("sha256") == view["sha256"]):
                    matches.append((url, identity[1]))
            if len(matches) != 1:
                raise _SkipPage("original_detail_missing_or_ambiguous")
            url, owner = matches[0]
            owners.add(owner)
            identifiers.add(view["id"])
            details.append({**deepcopy(view), "url": url, "path": str(resolve_asset(url))})
        if len(owners) != 1:
            raise _SkipPage("original_detail_page_identity_ambiguous")
    elif count != 0 or views:
        raise _SkipPage("original_detail_evidence_missing")

    navigation = []
    for url in assets:
        identity = _NAV.fullmatch(PurePosixPath(url).name)
        if identity and int(identity[2]) == index and (not owners or identity[1] in owners):
            navigation.append(url)
    if len(navigation) != 1:
        raise _SkipPage("original_navigation_missing_or_ambiguous")
    image_url = navigation[0]
    image = resolve_asset(image_url)
    data = _png(image)
    receipt = assets[image_url]
    if receipt != {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
        raise _SkipPage("original_navigation_changed")
    if details:
        for view in details:
            if Path(view["path"]).parent != image.parent:
                raise _SkipPage("original_detail_page_identity_ambiguous")
        # Reuse the actual input validator: dimensions, PNG bytes, canonical
        # page boxes and original per-page detail hashes must all still agree.
        _messages, metadata = prepare_detail_messages(details, page_index=index, check_cancelled=check_cancelled)
        if any(detail_input.get(key) != metadata[key] for key in
               ("detail_count", "detail_total_pixels", "detail_total_png_bytes")):
            raise _SkipPage("original_detail_evidence_changed")
    return image, receipt["sha256"], details


def restore_project_pdf_transcription_witnesses(
    store, project_id, source_bytes, *, resolve_asset, transcript_normalizer,
    edited_page_numbers=(), recovered_page_numbers=(), check_cancelled=lambda: None,
    diagnostics=None,
):
    """Return physical zero-based page -> private first-pass witness, no API.

    Read only this project's original ``load_task`` checkpoint. Missing,
    changed or ambiguous evidence makes a page ineligible rather than creating
    replacement evidence. The resolver must resolve each original sealed URL;
    it cannot substitute a new navigation/detail image. Prepared working pages,
    manual edits and recovered transcripts are never witness input.
    """
    report = {"schema": SCHEMA, "status": "unavailable", "restored_pages": [], "skipped_pages": [],
              "source_basis": "first_pass_transcription", "native_reliable": False,
              "original_verified": False, "ai_calls": 0}
    if diagnostics is not None:
        diagnostics["pdf_cached_transcription_witness"] = report
    excluded = _exclusions(edited_page_numbers) | _exclusions(recovered_page_numbers)
    if not isinstance(store, DocumentProjectStore) or not isinstance(source_bytes, bytes) or not source_bytes:
        report["reason"] = "server_project_source_required"
        return {}
    try:
        check_cancelled()
    except TaskCancelled:
        report["status"] = "cancelled"
        raise
    witnesses = {}
    try:
        with store.operation(project_id), ASSET_LIFECYCLE_LOCK:
            metadata = store.get(project_id)
            source_sha = hashlib.sha256(source_bytes).hexdigest()
            actual_source = store.load_source(project_id)
            if (metadata.get("source_sha256") != source_sha or len(actual_source) != len(source_bytes)
                    or hashlib.sha256(actual_source).hexdigest() != source_sha):
                report["reason"] = "original_pdf_changed"
                return {}
            import pymupdf as fitz
            try:
                with fitz.open(stream=actual_source, filetype="pdf") as document:
                    physical_pages = len(document)
                    if document.needs_pass or physical_pages < 1:
                        report["reason"] = "original_pdf_unavailable"
                        return {}
            except (RuntimeError, ValueError):
                report["reason"] = "original_pdf_unavailable"
                return {}
            task = store.load_task(project_id)
            original = task.get("pdf_resume_state")
            if (not isinstance(original, dict) or not isinstance(original.get("task_id"), str)
                    or original.get("strategy") != "layout_aware"
                    or type(original.get("verify_suspicions")) is not bool
                    or not isinstance(original.get("page_indices"), list)
                    or not original["page_indices"] or len(original["page_indices"]) > 80
                    or any(type(index) is not int or not 0 <= index < physical_pages for index in original["page_indices"])
                    or original["page_indices"] != sorted(set(original["page_indices"]))):
                report["reason"] = "sealed_checkpoint_missing"
                return {}
            def original_asset(url):
                check_cancelled()
                return _original_asset(url, resolve_asset)
            state = prepare_pdf_checkpoint(original, task_id=original["task_id"], document_sha256=source_sha,
                page_indices=original["page_indices"], strategy=original.get("strategy"),
                verify_suspicions=original.get("verify_suspicions"), resolve_asset=original_asset)
            for index in state["page_indices"]:
                check_cancelled()
                number = index + 1
                if number in excluded:
                    report["skipped_pages"].append({"page_number": number, "reason": "working_page_excluded"})
                    continue
                try:
                    page = state["pages"].get(str(index))
                    joint = page.get("joint") if isinstance(page, dict) else None
                    success = _successful_original_joint(joint, index)
                    normalized = transcript_normalizer(joint["markdown"])
                    if (not isinstance(normalized, str)
                            or page["markdown"] != f"<!-- MATHBANK_PDF_PAGE:{number} -->\n" + normalized.strip()):
                        raise _SkipPage("original_page_text_changed")
                    image, image_sha, details = _original_inputs(state, joint, index, original_asset, check_cancelled)
                    ticket = begin_pdf_transcription_witness(image, {"page_index": index},
                        expected_image_sha256=image_sha, detail_views=details,
                        request_inputs={"schema": SCHEMA, "document_sha256": source_sha,
                            "checkpoint_sha256": original["state_sha256"],
                            "original_response_sha256": success["response_sha256"]})
                    witness = finish_pdf_transcription_witness(ticket, joint)
                    if witness is None:
                        raise _SkipPage("original_input_changed_during_restore")
                    witnesses[index] = witness
                    report["restored_pages"].append(number)
                except TaskCancelled:
                    raise
                except (ValueError, OSError, KeyError, TypeError, AttributeError, Image.DecompressionBombError) as exc:
                    reason = str(exc) if isinstance(exc, _SkipPage) else "original_page_evidence_incomplete"
                    report["skipped_pages"].append({"page_number": number, "reason": reason})
            check_cancelled()
            # A card-only draft save does not change immutable OCR evidence.
            # A different task/source checkpoint must never inherit this proof.
            current = store.get(project_id)
            latest_state = store.load_task(project_id).get("pdf_resume_state") or {}
            if (current.get("source_sha256") != metadata.get("source_sha256")
                    or current.get("task_id") != metadata.get("task_id")
                    or latest_state.get("state_sha256") != original.get("state_sha256")):
                report.update(status="unavailable", reason="project_changed", restored_pages=[])
                return {}
    except TaskCancelled:
        report["status"] = "cancelled"
        raise
    except (DocumentProjectError, OSError, ValueError, KeyError, TypeError, AttributeError):
        report.update(reason="sealed_checkpoint_or_original_asset_unavailable", restored_pages=[])
        return {}
    report["status"] = "restored" if witnesses else "unavailable"
    return witnesses
