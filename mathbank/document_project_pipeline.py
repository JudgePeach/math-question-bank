"""Persist/reuse local PDF structure results without repeating transcription.

Stage ``complete`` means that a whole validated result packet was received; its
``partial`` flag independently records unresolved question groups. Incomplete
HTTP/SSE prefixes never reach these callbacks. Source truth/vision checks remain
the caller's responsibility after assembling these first-pass transcript slices.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from mathbank.document_projects import DocumentProjectError, ProjectCorrupt
from mathbank.pdf_local_request import request_pdf_local_structure, validate_cached_pdf_local_result
from mathbank.pdf_local_structure import PdfLocalStructureError, prepare_pdf_local_structure, SCHEMA as STRUCTURE_SCHEMA
from mathbank.task_manager import TaskCancelled


SCHEMA = "mathbank.document-project-local-structure.v2"
PLAN_STAGE = "pdf_local_structure_plan"
RESULT_STAGE = "pdf_local_structure_result"


def _json(value):
    try:
        # Catalogue insertion order is a classification priority rule, so it is
        # deliberately part of the fingerprint instead of sorted away.
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PdfLocalStructureError("project_structure_input_not_json") from exc


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _inputs(source, source_pages, curriculum, source_document_sha256):
    if (not isinstance(source, str) or not isinstance(source_pages, list)
            or any(not isinstance(page, dict) for page in source_pages)
            or not isinstance(curriculum, dict)
            or not isinstance(source_document_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_document_sha256)):
        raise PdfLocalStructureError("project_structure_input_invalid")
    snapshot = {"source": source, "source_pages": deepcopy(source_pages), "curriculum": deepcopy(curriculum)}
    binding = {"schema": SCHEMA, "structure_schema": STRUCTURE_SCHEMA, "source_document_sha256": source_document_sha256,
               "source_sha256": _sha(source), "source_pages_sha256": _sha(_json(snapshot["source_pages"])),
               "curriculum_sha256": _sha(_json(snapshot["curriculum"]))}
    return snapshot, binding, _sha(_json(binding))


def _packet(payload, kind, binding, fingerprint):
    return {"schema": SCHEMA, "kind": kind, "fingerprint": fingerprint,
            "binding": deepcopy(binding), kind: deepcopy(payload)}


def _unwrap(packet, kind, binding, fingerprint):
    if packet is None:
        return None
    if (not isinstance(packet, dict) or packet.get("schema") != SCHEMA
            or packet.get("kind") != kind or packet.get("fingerprint") != fingerprint
            or packet.get("binding") != binding or not isinstance(packet.get(kind), dict)):
        raise ProjectCorrupt("工程拆题缓存的输入绑定无效，未重新请求模型。")
    return deepcopy(packet[kind])


def _validate_result(result, plan):
    if (not isinstance(result, dict) or not isinstance(result.get("questions"), list)
            or not isinstance(result.get("question_ids"), list)
            or len(result["questions"]) != len(result["question_ids"])
            or any(not isinstance(qid, str) for qid in result["question_ids"])
            or len(set(result["question_ids"])) != len(result["question_ids"])
            or type(result.get("partial")) is not bool
            or not isinstance(result.get("source_matches"), list)
            or any(not isinstance(match, dict) for match in result["source_matches"])
            or not result["questions"] and not result["partial"]):
        raise PdfLocalStructureError("project_structure_result_invalid")
    for question in result["questions"]:
        if (not isinstance(question, dict) or not isinstance(question.get("content"), str)
                or not question["content"].strip()
                or not isinstance(question.get("pdf_local_source"), dict)
                or question["pdf_local_source"].get("input_sha256") != plan.get("input_sha256")):
            raise PdfLocalStructureError("project_structure_result_source_binding_invalid")


def _retry_scope_available(result, plan):
    ids = result.get("retry_question_ids")
    scopes = result.get("retry_scopes")
    failures = result.get("failed_groups")
    if (not isinstance(ids, list) or not ids or not isinstance(scopes, dict)
            or not isinstance(failures, dict) or any(not isinstance(qid, str) for qid in ids)
            or len(set(ids)) != len(ids)):
        return False
    blocks = {block["id"] for block in plan.get("blocks", [])}
    for qid in ids:
        scope = scopes.get(qid)
        if (qid in result["question_ids"] or qid not in failures
                or not isinstance(scope, list) or not scope
                or any(not isinstance(block, str) or block not in blocks for block in scope)):
            return False
    return True


def _validate_cached_result(result, plan, curriculum):
    _validate_result(result, plan)
    if not plan.get("ready"):
        if result["questions"]:
            raise PdfLocalStructureError("project_manual_plan_cannot_have_adopted_questions")
        return deepcopy(result)
    rebuilt = validate_cached_pdf_local_result(result, plan, curriculum)
    # Validate the original evidence without replacing its dictionaries (which
    # can carry additional application-owned diagnostics and review metadata).
    keys = ("question_index", "field", "source_number", "source_start", "source_end",
            "block_id", "source_basis", "native_reliable")
    expected = [tuple(match.get(key) for key in keys) for match in rebuilt["source_matches"]]
    observed = [tuple(match.get(key) for key in keys) for match in result["source_matches"]]
    if observed != expected:
        raise PdfLocalStructureError("project_cached_source_matches_changed")
    canonical = deepcopy(result)
    for old, verified in zip(canonical["questions"], rebuilt["questions"]):
        # Upgrade only the transport-owned, generic first-pass status. Exact
        # source bodies/classification audits and unknown caller fields remain.
        for field in ("source_review", "pdf_local_source"):
            if field in verified:
                old[field] = deepcopy(verified[field])
    return canonical


def parse_project_pdf(
    store, project_id, source, source_pages, curriculum, *, provider, post,
    diagnostics, check_cancelled=lambda: None, report_attempt=lambda _event: None,
    source_document_sha256, force=False,
):
    """Return the adopted result dictionary; never invoke a whole-text fallback.

    A cache hit makes zero model POSTs. A cached partial result can request only
    independently bounded failed groups via ``seed_result``. Unscoped ambiguity
    stays manual until a user edits the source or explicitly forces analysis.
    Private write errors are reported in diagnostics and do not cause another
    paid transcription; corrupt cache reads fail locally before any POST.
    """
    if type(force) is not bool:
        raise DocumentProjectError("重新分析选项无效。")
    diagnostics = diagnostics if diagnostics is not None else {}
    snapshot, binding, fingerprint = _inputs(source, source_pages, curriculum, source_document_sha256)
    report = {"schema": SCHEMA, "fingerprint": fingerprint, "reused": False,
              "plan_reused": False, "result_reused": False, "force": force,
              "plan_saved": False, "result_saved": False, "retry_from_cached_partial": False,
              "cache_errors": []}
    diagnostics["pdf_project_structure"] = report

    with store.operation(project_id):
        check_cancelled()
        if store.get(project_id).get("source_sha256") != source_document_sha256:
            raise PdfLocalStructureError("project_structure_document_changed")
        current_plan = prepare_pdf_local_structure(snapshot["source"], snapshot["source_pages"],
                                                  source_document_sha256=source_document_sha256)
        cached_plan = _unwrap(store.load_stage(project_id, PLAN_STAGE, fingerprint), "plan", binding, fingerprint)
        if cached_plan is not None:
            if cached_plan != current_plan:
                raise ProjectCorrupt("工程拆题计划与当前原文不一致，未重新请求模型。")
            report.update(plan_reused=True, plan_saved=True)
        plan = cached_plan or current_plan
        cached = None if force else _unwrap(store.load_stage(project_id, RESULT_STAGE, fingerprint), "result", binding, fingerprint)
        cache_needs_upgrade = False
        if cached is not None:
            canonical = _validate_cached_result(cached, plan, snapshot["curriculum"])
            cache_needs_upgrade = canonical != cached
            cached = canonical
            report["result_saved"] = True

        def save(kind, value, stage):
            check_cancelled()
            packet = _packet(value, kind, binding, fingerprint)
            if kind == "plan":
                # Keep the original page objects, including revisions and source
                # evidence flags, even when no question can yet be assembled.
                packet["source_pages"] = deepcopy(snapshot["source_pages"])
            try:
                store.save_stage(project_id, stage, packet, fingerprint, complete=True)
                report[kind + "_saved"] = True
            except TaskCancelled:
                raise
            except Exception as exc:
                report[kind + "_saved"] = False
                report["cache_errors"].append({"stage": kind, "error_type": type(exc).__name__})

        def on_plan(value):
            nonlocal plan
            if value != current_plan:
                raise PdfLocalStructureError("project_structure_callback_plan_changed")
            plan = deepcopy(value)
            save("plan", value, PLAN_STAGE)

        def on_result(value):
            _validate_result(value, plan)
            save("result", value, RESULT_STAGE)

        def reuse(value, status):
            check_cancelled()
            report.update(reused=True, result_reused=True, status=status)
            if not report["plan_saved"]:
                save("plan", plan, PLAN_STAGE)
            if cache_needs_upgrade:
                save("result", value, RESULT_STAGE)
            # Do not count the cached invocation's historic tokens as new calls.
            previous = value.get("diagnostics", {}).get("transport", {})
            diagnostics["pdf_local_structure"] = {
                "mode": "block_references", "status": status, "reused": True,
                "calls": 0, "attempts": [], "usage": {}, "input_sha256": plan.get("input_sha256"),
                "question_count": len(value["questions"]), "cached_calls": previous.get("calls"),
                "source_basis": "first_pass_transcription", "native_reliable": False,
                "page_issues":deepcopy(plan.get("page_issues", [])),
                "blocked_scopes":deepcopy(plan.get("blocked_scopes", [])),
                "manual_reasons":deepcopy(value.get("manual_reasons") or plan.get("fallback_reasons") or []),
            }
            return deepcopy(value)

        if cached is not None and not cached["partial"]:
            return reuse(cached, "reused")
        if cached is not None and not _retry_scope_available(cached, plan):
            return reuse(cached, "partial_manual_review")

        kwargs = {}
        if cached is not None:
            report["retry_from_cached_partial"] = True
            kwargs["seed_result"] = deepcopy(cached)
        result = request_pdf_local_structure(
            snapshot["source"], snapshot["source_pages"], snapshot["curriculum"],
            provider=provider, post=post, diagnostics=diagnostics,
            check_cancelled=check_cancelled, report_attempt=report_attempt,
            source_document_sha256=source_document_sha256,
            prepared_plan=deepcopy(plan) if plan.get("ready") else None,
            on_plan=on_plan, on_result=on_result, **kwargs,
        )
        check_cancelled()
        _validate_result(result, plan)
        report.update(status="partial" if result["partial"] else "completed")
        # request_pdf_local_structure invokes on_result before any scoped repair
        # and again at completion. No separate paid request is made for saving.
        return result
