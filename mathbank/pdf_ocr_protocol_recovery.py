"""Read-only recovery of one narrowly compatible archived OCR protocol.

This does not send a request, create a transcription witness, accept a source,
or rewrite an archived attempt. The returned pages are manual-review working
copies of complete HTTP-200/stop packets with known bounded diagnostic fields.
"""
from copy import deepcopy
import hashlib
import re

from mathbank.pdf_ocr_archive import SCHEMA as ARCHIVE_SCHEMA, _digest, _image_sha
from mathbank.pdf_page_vision import (
    MAX_REASON_CHARS, MAX_RESPONSE_CHARS, _PAGE_FIELDS, _prompt_page_info,
    _validated_layout, _validated_page,
)
from mathbank.pdf_task_resume import validate_pdf_transcript_checkpoint
from mathbank.pdf_vision_request import completion_content
from mathbank.source_review_results import parse_review_response
from mathbank.task_manager import TaskCancelled


SCHEMA = "mathbank.pdf-ocr-protocol-recovery.v1"
MAX_ARCHIVES = 2048
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NAVIGATION = re.compile(
    r"/static/(?:uploads|test_uploads)/tmp/pdf_page_"
    r"[A-Za-z0-9_-]{1,160}_(0|[1-9][0-9]*)\.png"
)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _failed_attempts(task, checkpoint, page_index, *, run=None):
    """Read both sealed history and current diagnostics without changing either.

    Diagnostics-only task updates may be throttled. The sealed source snapshot
    remains evidence even when rebinding erased the small task's diagnostics.
    Distinct contradictory observations remain distinct and fail closed below.
    """
    rows = {}
    for source in (checkpoint, task):
        attempts = (source.get("diagnostics") or {}).get("pdf_vision", {}).get("attempts", [])
        if not isinstance(attempts, list):
            continue
        for row in attempts:
            if (not isinstance(row, dict) or row.get("stage") != "joint_page"
                    or type(row.get("page_index")) is not int or row["page_index"] != page_index
                    or type(row.get("run")) is not int or not 0 <= row["run"] <= checkpoint["run"]
                    or (run is not None and row["run"] != run)):
                continue
            proof = {key: row.get(key) for key in ("stage", "page_index", "run", "attempt",
                "status", "http_status", "finish_reason", "response_retained", "response_sha256")}
            rows[_digest(proof)] = deepcopy(row)
    return list(rows.values())


def _has_failed_proof(attempts, receipt, raw_sha):
    matches = [row for row in attempts if type(row.get("attempt")) is int
               and row["attempt"] == receipt["attempt"]]
    proven = [row for row in matches if row.get("status") == "failed"
              and row.get("http_status") == 200 and row.get("finish_reason") == "stop"
              and row.get("response_retained") is True and row.get("response_sha256") == raw_sha]
    contradictions = [row for row in matches if row.get("status") in {"succeeded", "cancelled"}
        or (row.get("response_sha256") is not None and row["response_sha256"] != raw_sha)]
    return bool(proven) and not contradictions


def _packet(record, stage, entry, checkpoint, page_index, pixels, page_info):
    """All failures are local cache misses; never repair JSON or infer fields."""
    if not isinstance(record, dict):
        raise ValueError("archive_shape")
    binding = {key: record.get(key) for key in (
        "schema", "document_sha256", "image_sha256", "page_number",
        "request_payload_sha256", "run", "attempt", "stage")}
    if (binding["schema"] != ARCHIVE_SCHEMA
            or binding["document_sha256"] != checkpoint["document_sha256"]
            or binding["image_sha256"] != pixels["sha256"]
            or type(binding["page_number"]) is not int or binding["page_number"] != page_index + 1
            or type(binding["run"]) is not int or not 0 <= binding["run"] <= checkpoint["run"]
            or type(binding["attempt"]) is not int or not 1 <= binding["attempt"] <= 2
            or binding["stage"] != "joint_page"
            or not isinstance(binding["request_payload_sha256"], str)
            or not _SHA.fullmatch(binding["request_payload_sha256"])
            or record.get("accepted_source") is not False
            or record.get("original_verified") is not False
            or entry.get("input_fingerprint") != _digest(binding)
            or record.get("response_sha256") != _digest(record.get("body"))
            or stage != "ocr-response-" + _digest({
                "binding": binding, "response_sha256": record.get("response_sha256")})):
        raise ValueError("archive_binding")
    metadata = record.get("metadata")
    if (not isinstance(metadata, dict) or type(metadata.get("http_status")) is not int
            or metadata["http_status"] != 200
            or type(metadata.get("page_index")) is not int or metadata["page_index"] != page_index
            or type(metadata.get("attempt")) is not int or metadata["attempt"] != binding["attempt"]
            or metadata.get("stage") != "joint_page"):
        raise ValueError("http_binding")
    body = record["body"]
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or len(body["choices"]) != 1:
        raise ValueError("completion_shape")
    raw = completion_content(body, "PDF 本地协议恢复", max_chars=MAX_RESPONSE_CHARS)
    parsed = parse_review_response(raw)
    if not isinstance(parsed, dict):
        raise ValueError("not_diagnostic_compatibility")
    diagnostics = set(parsed) - _PAGE_FIELDS
    if (not _PAGE_FIELDS <= set(parsed) or not diagnostics
            or diagnostics - {"WARN", "review_required"}
            or ("WARN" in parsed and (not isinstance(parsed["WARN"], str)
                or not parsed["WARN"].strip() or len(parsed["WARN"]) > MAX_REASON_CHARS))
            or ("review_required" in parsed and not (
                type(parsed["review_required"]) is bool
                or isinstance(parsed["review_required"], str)
                    and parsed["review_required"] in {"true", "false"}))):
        raise ValueError("not_diagnostic_compatibility")
    # The ordinary page parser can retain usable text when geometry is broken.
    # Recovery is narrower: no second protocol defect may use that fallback.
    joint = _validated_page(deepcopy(parsed), page_info)
    _validated_layout(parsed, page_info, parsed["markdown"], [*parsed["warnings"],
        *([parsed["WARN"]] if "WARN" in parsed else [])])
    if joint.get("markdown") != parsed["markdown"]:
        raise ValueError("transcript_changed")
    if parsed["page_complete"] is False and joint["layout"].get("page_complete") is not False:
        raise ValueError("completeness_promoted")
    return joint, {
        "schema": SCHEMA, "page_number": page_index + 1, "page_index": page_index,
        "document_sha256": checkpoint["document_sha256"], "navigation_pixels": deepcopy(pixels),
        "checkpoint_sha256": checkpoint["state_sha256"], "checkpoint_task_id": checkpoint["task_id"],
        "run": binding["run"], "source_run": binding["run"], "working_run": checkpoint["run"],
        "attempt": binding["attempt"], "archive_stage": stage,
        "archive_input_fingerprint": entry["input_fingerprint"],
        "archive_response_sha256": record["response_sha256"],
        "request_payload_sha256": binding["request_payload_sha256"],
        "transcript_sha256": _sha(parsed["markdown"]),
        "compatibility": "bounded_top_level_diagnostics", "compatibility_fields": sorted(diagnostics),
        "accepted_source": False, "original_verified": False, "ocr_succeeded": False,
        "ai_calls": 0, "new_calls": 0,
    }, _sha(raw)


def restore_pdf_protocol_pages(store, project_id, checkpoint, page_urls, page_layout_infos, *,
                               resolve_asset, check_cancelled=lambda: None):
    """Return missing-page working copies and a safe, content-free receipt.

    ``checkpoint`` must be the original sealed server-owned resume snapshot,
    before prepare_pdf_checkpoint increments run. URLs follow its selected-page
    order; only missing pages need page_info. A new working task may own the
    same project; historical failed attempts may be proved by the sealed
    snapshot even when current top-level diagnostics were not yet persisted.
    ``has_compatible_archive_pages`` lists structurally compatible bound pages
    before failed-attempt proof: the caller must not pay again for an unresolved
    candidate just because its diagnostic evidence is temporarily unavailable.
    No store mutation, original-attempt mutation, witness or paid call occurs.
    """
    pages = {}
    report = {"schema": SCHEMA, "recovered_pages": [], "pending_pages": [],
              "has_compatible_archive_pages": [], "compatible_archives": [],
              "rejected_archives": [], "accepted_source": False, "original_verified": False,
              "ai_calls": 0, "new_calls": 0, "model_calls": 0}
    check_cancelled()
    try:
        with store.operation(project_id):
            document_sha = hashlib.sha256(store.load_source(project_id)).hexdigest()
            checked = validate_pdf_transcript_checkpoint(checkpoint, document_sha)
            if (type(checked.get("run")) is not int or checked["run"] < 0
                    or not isinstance(page_urls, (list, tuple))
                    or len(page_urls) != len(checked["page_indices"])
                    or not isinstance(page_layout_infos, dict)):
                raise ValueError("invalid_inputs")
            with store._locked(project_id) as directory:
                manifest = deepcopy(store._manifest(directory))
            task = store.load_task(project_id)
            entries = {name: entry for name, entry in manifest["stages"].items()
                       if name.startswith("ocr-response-")}
            if len(entries) > MAX_ARCHIVES:
                raise ValueError("archive_limit")
            missing = [index for index in checked["page_indices"] if str(index) not in checked["pages"]]
            selected = {}
            for index in missing:
                check_cancelled()
                number = index + 1
                try:
                    url = page_urls[checked["page_indices"].index(index)]
                    match = _NAVIGATION.fullmatch(url) if isinstance(url, str) else None
                    pixels = checked["assets"].get(url)
                    navigation = checked.get("navigation_pages", {}).get(str(index))
                    if (not match or int(match[1]) != index or not isinstance(pixels, dict)
                            or set(pixels) != {"size", "sha256"}
                            or type(pixels["size"]) is not int or pixels["size"] < 8
                            or not isinstance(pixels["sha256"], str) or not _SHA.fullmatch(pixels["sha256"])
                            or not isinstance(navigation, dict)
                            or navigation != {"page_index": index, "url": url, "dpi": 150,
                                "document_sha256": document_sha}):
                        raise ValueError("navigation_binding")
                    path = resolve_asset(url)
                    if _image_sha(path) != pixels["sha256"] or path.stat().st_size != pixels["size"]:
                        raise ValueError("navigation_changed")
                    info = _prompt_page_info(page_layout_infos.get(index))
                    if info["page_index"] != index:
                        raise ValueError("page_info_binding")
                    candidates = []
                    compatible = False
                    for stage, entry in sorted(entries.items()):
                        check_cancelled()
                        record = store.load_stage(project_id, stage, entry["input_fingerprint"])
                        if not isinstance(record, dict) or record.get("page_number") != number:
                            continue
                        try:
                            joint, receipt, raw_sha = _packet(record, stage, entry, checked, index, pixels, info)
                        except TaskCancelled:
                            raise
                        except (ValueError, TypeError, KeyError, OverflowError):
                            report["rejected_archives"].append({"page_number": number,
                                "archive_stage": stage, "reason": "ineligible_archived_packet"})
                            continue
                        compatible = True
                        if number not in report["has_compatible_archive_pages"]:
                            report["has_compatible_archive_pages"].append(number)
                        report["compatible_archives"].append({"page_number": number,
                            "archive_stage": stage, "source_run": receipt["source_run"]})
                        attempts = _failed_attempts(task, checked, index, run=receipt["source_run"])
                        if _has_failed_proof(attempts, receipt, raw_sha):
                            candidates.append((joint, receipt, stage, entry, url, path))
                    if len(candidates) != 1:
                        raise ValueError("ambiguous_archives" if candidates else
                            "failed_attempt_evidence_missing" if compatible else "no_compatible_archive")
                    selected[index] = candidates[0]
                except TaskCancelled:
                    raise
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    # Only our own fixed reason codes leave this boundary.
                    reason = str(exc) if str(exc) in {"navigation_binding", "navigation_changed",
                        "page_info_binding", "failed_attempt_evidence_missing", "ambiguous_archives",
                        "no_compatible_archive"} else "invalid_page_evidence"
                    report["pending_pages"].append({"page_number": number, "reason": reason})
            check_cancelled()
            if (validate_pdf_transcript_checkpoint(checkpoint, document_sha) != checked
                    or hashlib.sha256(store.load_source(project_id)).hexdigest() != document_sha):
                raise ValueError("source_changed")
            latest_task = store.load_task(project_id)
            with store._locked(project_id) as directory:
                latest_stages = store._manifest(directory)["stages"]
            for index, (joint, receipt, stage, entry, url, path) in selected.items():
                check_cancelled()
                if (latest_stages.get(stage) != entry
                        or _image_sha(path) != receipt["navigation_pixels"]["sha256"]
                        or path.stat().st_size != receipt["navigation_pixels"]["size"]
                        or _failed_attempts(latest_task, checked, index, run=receipt["source_run"])
                            != _failed_attempts(task, checked, index, run=receipt["source_run"])):
                    report["pending_pages"].append({"page_number": index + 1, "reason": "evidence_changed"})
                    continue
                receipt["working_task_id"] = latest_task.get("task_id")
                receipt["later_attempts"] = [{key: row.get(key) for key in (
                    "run", "attempt", "status", "http_status", "finish_reason", "response_sha256")}
                    for row in _failed_attempts(latest_task, checked, index)
                    if row["run"] > receipt["source_run"]]
                joint.update(ocr_succeeded=False, original_verified=False, visual_calls=0,
                    protocol_recovery=deepcopy(receipt),
                    attempts=_failed_attempts(task, checked, index, run=receipt["source_run"]))
                pages[index] = {"markdown": joint["markdown"], "joint": joint}
                report["recovered_pages"].append(deepcopy(receipt))
    except TaskCancelled:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Global evidence failures discard all newly derived working pages.
        pages = {}
        report["recovered_pages"] = []
        report["reason"] = "invalid_project_or_checkpoint_evidence"
    check_cancelled()
    report["recovered"] = len(pages)
    return pages, report
