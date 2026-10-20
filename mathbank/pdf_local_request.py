"""Bounded transport for local PDF source references, without body generation."""
from __future__ import annotations

from copy import deepcopy

from .ai_providers import apply_model_thinking_policy, apply_structured_output_policy
from .paper_parse import PAPER_SPLIT_TIMEOUT_SECONDS, request_pdf_paper_completion
from .pdf_local_structure import (
    PdfLocalStructureError, prepare_pdf_local_structure,
    build_pdf_local_structure_messages, adopt_pdf_local_structure_response,
)
from .pdf_vision_request import completion_content, collected_usage, PDFVisionRequestError
from .source_review_results import parse_review_response
from .task_manager import TaskCancelled

MAX_STRUCTURE_OUTPUT_TOKENS = 6000


def _merge(primary, repair, plan, requested):
    """Retain successful groups exactly; index ranges after source-order merge."""
    records = {qid: (deepcopy(question), primary, index) for index, (qid, question) in
               enumerate(zip(primary["question_ids"], primary["questions"]))}
    for index, (qid, question) in enumerate(zip(repair["question_ids"], repair["questions"])):
        if qid not in requested or qid in records:
            raise PdfLocalStructureError("scoped_retry_returned_unrequested_question")
        records[qid] = (deepcopy(question), repair, index)
    position = {b["id"]: b["start"] for b in plan["blocks"]}
    ordered = sorted(records, key=lambda qid: min(position[v] for v in records[qid][0]["pdf_local_source"]["stem_blocks"]))
    result = deepcopy(primary)
    result["question_ids"] = ordered
    result["questions"] = [records[qid][0] for qid in ordered]
    result["source_matches"] = []
    for new_index, qid in enumerate(ordered):
        _, origin, old_index = records[qid]
        for match in origin["source_matches"]:
            if match["question_index"] == old_index:
                result["source_matches"].append({**deepcopy(match), "question_index": new_index})
    succeeded = set(repair["question_ids"])
    result["failed_groups"] = {k: v for k, v in primary["failed_groups"].items() if k not in succeeded}
    result["failed_groups"].update(repair["failed_groups"])
    used = {v for question in result["questions"] for key in ("stem_blocks", "answer_blocks")
            for v in question["pdf_local_source"][key]}
    result["unassigned"] = [v for v in primary["unassigned"] if v not in used]
    # Retry roles can clarify only formerly unassigned source, never replace an
    # adopted question or silently remove unresolved material from the project.
    scopes = {**primary.get("retry_scopes", {}), **repair.get("retry_scopes", {})}
    result["retry_scopes"] = {qid: values for qid, values in scopes.items() if qid in result["failed_groups"]
                              and not set(values).intersection(used)}
    result["retry_question_ids"] = [qid for qid in result["failed_groups"] if qid in result["retry_scopes"]]
    result["partial"] = bool(not result["questions"] or result["failed_groups"] or result["unassigned"])
    result["diagnostics"].update(question_count=len(ordered), failed_group_count=len(result["failed_groups"]),
                                 unassigned_block_count=len(result["unassigned"]))
    return result


def _validated_seed(seed, plan, curriculum):
    """Reassemble cached successes; a JSON cache cannot assert source truth."""
    if (not isinstance(seed, dict) or (seed.get("diagnostics") or {}).get("input_sha256") != plan["input_sha256"]
            or not isinstance(seed.get("questions"), list) or not isinstance(seed.get("question_ids"), list)
            or len(seed["questions"]) != len(seed["question_ids"])):
        raise PdfLocalStructureError("cached_structure_result_stale_or_invalid")
    rows = []
    for qid, question in zip(seed["question_ids"], seed["questions"]):
        bound = question.get("pdf_local_source") if isinstance(question, dict) else None
        if (not isinstance(bound, dict) or bound.get("input_sha256") != plan["input_sha256"]
                or bound.get("source_basis") != "first_pass_transcription" or bound.get("native_reliable") is not False
                or bound.get("original_verified", False) is not False):
            raise PdfLocalStructureError("cached_structure_question_binding_invalid")
        row = {key: question.get(key, "") for key in ("source_number", "question_type", "difficulty",
               "category_compulsory", "category_chapter", "category_knowledge")}
        row.update(id=qid, stem=bound.get("stem_blocks"), answer=bound.get("answer_blocks"), answer_cells=bound.get("answer_cells"))
        rows.append(row)
    roles = seed.get("roles")
    if not isinstance(roles, list):
        raise PdfLocalStructureError("cached_structure_roles_invalid")
    canonical_roles = [{"role": role.get("role"), "blocks": role.get("blocks")} for role in roles if isinstance(role, dict)]
    rebuilt = adopt_pdf_local_structure_response({"input_sha256": plan["input_sha256"], "questions": rows,
                                                "roles": canonical_roles}, plan, curriculum)
    if rebuilt["failed_groups"] or rebuilt["question_ids"] != seed["question_ids"]:
        raise PdfLocalStructureError("cached_structure_sources_invalid")
    for index, (old, new) in enumerate(zip(seed["questions"], rebuilt["questions"])):
        if any(old.get(key) != new.get(key) for key in ("content", "answer_markdown", "referenced_images", "source_number")):
            raise PdfLocalStructureError("cached_structure_body_changed")
        # Review originals and application audit fields survive a JSON cache
        # round trip. Only the source-derived fields above were reassembled.
        kept = deepcopy(old)
        kept["pdf_local_source"].setdefault("original_verified", False)
        review = kept.get("source_review") or {}
        if review.get("reasons") == ["首次OCR稿已按来源块保留；尚未证明与原PDF逐字一致。"] and not review.get("verified_by"):
            review.update(required=False, status="not_verified")
        rebuilt["questions"][index] = kept
    failures = seed.get("failed_groups", {})
    if not isinstance(failures, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in failures.items()):
        raise PdfLocalStructureError("cached_structure_failures_invalid")
    from .pdf_local_structure import _expand, _number
    positions = {b["id"]: i for i, b in enumerate(plan["blocks"])}
    by_id = {b["id"]: b for b in plan["blocks"]}
    used = {value for question in rebuilt["questions"] for key in ("stem_blocks", "answer_blocks")
            for value in question["pdf_local_source"][key]}
    scopes = seed.get("retry_scopes", {})
    requested = seed.get("retry_question_ids", [])
    reliable = {}
    if isinstance(scopes, dict) and isinstance(requested, list):
        for qid in requested:
            if not isinstance(qid, str) or qid not in failures or qid in rebuilt["question_ids"]:
                continue
            try:
                refs = _expand(scopes.get(qid), plan["blocks"], positions)
                number = int(qid[1:]) if qid.startswith("q") else 0
                numbers = {_number(by_id[v]["text"]) for v in refs} - {None}
                if refs and number > 0 and number in numbers and numbers <= {number} and not used.intersection(refs):
                    reliable[qid] = refs
            except (ValueError, TypeError, KeyError):
                pass
    rebuilt.update(failed_groups=deepcopy(failures), retry_scopes=reliable, retry_question_ids=list(reliable),
                   partial=bool(seed.get("partial") or failures or rebuilt["unassigned"]))
    rebuilt["diagnostics"].update(failed_group_count=len(failures))
    if not reliable and rebuilt["partial"]:
        rebuilt["manual_reasons"] = ["cached_partial_has_no_reliable_retry_scope"]
    return rebuilt


def validate_cached_pdf_local_result(seed, plan, curriculum):
    """Pure cached-success validation; does not perform requests or persistence.

    Callers may ignore the returned projection to retain their original cache
    dict after validation, including classification reviews and audit fields.
    """
    return _validated_seed(seed, plan, curriculum)


def request_pdf_local_structure(
    source, source_pages, curriculum, *, provider, post, diagnostics=None,
    check_cancelled=lambda: None, report_attempt=lambda _event: None,
    source_document_sha256=None, prepared_plan=None, on_plan=None, on_result=None, seed_result=None,
):
    """At most two physical POSTs for this invocation, including scoped repair.

    Transport errors/invalid JSON may consume the existing second attempt. A
    complete response with local group errors gets only the remaining budget.
    Neither failure starts an automatic whole-document transcription request.
    """
    check_cancelled()
    diagnostics = diagnostics if diagnostics is not None else {}
    current = prepare_pdf_local_structure(source, source_pages, source_document_sha256=source_document_sha256)
    if prepared_plan is not None:
        if (not current.get("ready") or prepared_plan.get("input_sha256") != current.get("input_sha256")):
            raise PdfLocalStructureError("prepared_structure_plan_stale")
        plan = deepcopy(prepared_plan)
        # Build performs the exact-slice/hash validation before any POST.
        build_pdf_local_structure_messages(plan, curriculum)
    else:
        plan = current
    if on_plan is not None:
        on_plan(deepcopy(plan))
    check_cancelled()
    report = {"mode": "block_references", "source_basis": "first_pass_transcription", "native_reliable": False,
              "timeout_seconds": PAPER_SPLIT_TIMEOUT_SECONDS, "max_attempts": 2, "max_output_tokens": MAX_STRUCTURE_OUTPUT_TOKENS,
              "calls": 0, "attempts": [], "usage": {}, "input_sha256": plan.get("input_sha256"), "status": "preparing"}
    report.update(page_issues=deepcopy(plan.get("page_issues", [])), blocked_scopes=deepcopy(plan.get("blocked_scopes", [])))
    diagnostics["pdf_local_structure"] = report
    if not plan.get("ready"):
        result = {"questions": [], "question_ids": [], "source_matches": [], "failed_groups": {},
                  "retry_question_ids": [], "retry_scopes": {}, "roles": [], "unassigned": [], "partial": True,
                  "page_issues": deepcopy(plan.get("page_issues", [])), "blocked_scopes": deepcopy(plan.get("blocked_scopes", [])),
                  "manual_reasons": list(plan["fallback_reasons"]), "diagnostics": deepcopy(report)}
        report.update(status="manual_review", manual_reasons=list(plan["fallback_reasons"]))
        if on_result is not None:
            on_result(deepcopy(result))
        return result

    def send(*, budget, requested=None, retry_scopes=None):
        check_cancelled()
        messages = build_pdf_local_structure_messages(plan, curriculum, retry_question_ids=requested, retry_scopes=retry_scopes)
        payload = apply_model_thinking_policy({"model": provider.model_name, "messages": messages,
            "response_format": {"type": "json_object"}, "temperature": 0.2,
            "max_tokens": MAX_STRUCTURE_OUTPUT_TOKENS}, provider=provider, task="parse")
        payload = apply_structured_output_policy(payload, provider=provider)
        before = report["calls"]
        stage_report = {}
        def record(event):
            adjusted = {**deepcopy(event), "attempt": before + event["attempt"], "max_attempts": 2,
                        "scope": "repair" if requested is not None else "primary"}
            previous = next((v for v in report["attempts"] if v["attempt"] == adjusted["attempt"]), None)
            if previous is None:
                report["attempts"].append(adjusted)
            else:
                previous.update(adjusted)
            report["calls"] = sum(v.get("http_started", True) for v in report["attempts"])
            report["usage"] = collected_usage(report["attempts"])
            report_attempt(adjusted)

        def parse(body, metadata):
            check_cancelled()
            raw = completion_content(body, "PDF 来源块拆题", max_chars=100_000)
            metadata["output_characters"] = len(raw)
            parsed = parse_review_response(raw)
            if requested is not None and isinstance(parsed, dict):
                rows = parsed.get("questions")
                if isinstance(rows, list) and any(not isinstance(v, dict) or v.get("id") not in requested for v in rows):
                    raise PdfLocalStructureError("scoped_retry_returned_unrequested_question")
                # An allowed ID alone does not give the retry ownership of a
                # successful group's source. Check the actual reference scope.
                if isinstance(rows, list):
                    from .pdf_local_structure import _expand
                    positions = {b["id"]: i for i, b in enumerate(plan["blocks"])}
                    for row in rows:
                        allowed = set(retry_scopes.get(row.get("id"), []))
                        for field in ("stem", "answer"):
                            refs = _expand(row.get(field, []), plan["blocks"], positions)
                            if not set(refs) <= allowed:
                                raise PdfLocalStructureError("scoped_retry_exceeded_source_scope")
                        by_cell = {cell["id"]: cell for cell in plan["answer_cells"]}
                        cell_refs = row.get("answer_cells", [])
                        if not isinstance(cell_refs, list) or any(not isinstance(cid, str) or cid not in by_cell
                                or by_cell[cid]["block_id"] not in allowed for cid in cell_refs):
                            raise PdfLocalStructureError("scoped_retry_exceeded_answer_cell_scope")
            return adopt_pdf_local_structure_response(parsed, plan, curriculum, expected_input_sha256=plan["input_sha256"])

        return request_pdf_paper_completion(provider, payload, post=post, diagnostics=stage_report,
            check_cancelled=check_cancelled, report_attempt=record, completion_parser=parse,
            diagnostics_key="local_structure_attempts", label="PDF 来源块拆题", max_attempts=budget)

    try:
        report["status"] = "requesting"
        result = (_validated_seed(seed_result, plan, curriculum) if seed_result is not None else send(budget=2))
        report["resumed_from_cached_result"] = seed_result is not None
        check_cancelled()
        if on_result is not None:
            on_result(deepcopy(result))
        remaining = 2 - report["calls"]
        while result["retry_question_ids"] and remaining > 0:
            report["status"] = "repairing_groups"
            try:
                repair = send(budget=remaining, requested=result["retry_question_ids"], retry_scopes=result["retry_scopes"])
                result = _merge(result, repair, plan, set(result["retry_question_ids"]))
            except TaskCancelled:
                raise
            except (PDFVisionRequestError, PdfLocalStructureError) as exc:
                result["manual_reasons"] = ["scoped_structure_retry_incomplete"]
                report["scoped_retry_error_type"] = type(exc).__name__
                break
            remaining = 2 - report["calls"]
        check_cancelled()
        report.update(status="partial" if result["partial"] else "completed",
                      question_count=len(result["questions"]), failed_group_count=len(result["failed_groups"]),
                      unassigned_block_count=len(result["unassigned"]))
        result["diagnostics"]["transport"] = deepcopy(report)
        if on_result is not None:
            on_result(deepcopy(result))
        return result
    except TaskCancelled:
        report["status"] = "cancelled"
        raise
    except Exception:
        report["status"] = "failed"
        raise
