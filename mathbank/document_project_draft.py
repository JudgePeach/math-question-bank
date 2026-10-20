"""Persist first-pass PDF text as editable cards before optional metadata AI.

The raw source and range receipts are immutable project stages. Downstream AI
can annotate metadata; it never supplies the saved question or answer bodies.
"""
from copy import deepcopy
import hashlib
import json
import time

from .document_projects import DocumentProjectError
from .task_manager import TaskCancelled


SCHEMA = "mathbank.document-project-local-draft.v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda item: list(item) if isinstance(item, (set, frozenset, tuple)) else item)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def execute_pdf_local_draft(source, source_pages, layout_result, curriculum, *, store, project_id,
        source_document_sha256, provider, post, cache=None, diagnostics=None,
        prepare_questions=lambda rows: rows, publish_draft=lambda _plan: None,
        check_cancelled=lambda: None, report_attempt=lambda _event: None,
        build_plan=None):
    from .pdf_local_draft import prepare_pdf_local_draft
    from .pdf_draft_metadata import request_pdf_local_draft_metadata, PdfDraftMetadataCache

    diagnostics = diagnostics if diagnostics is not None else {}
    check_cancelled()
    planner = build_plan or prepare_pdf_local_draft
    plan = planner(source, source_pages, layout_result=layout_result,
                   source_document_sha256=source_document_sha256)
    # JSON projection is intentional: the durable stage can be opened after a
    # restart without keeping model bodies or PDF images alive in RAM.
    frozen = json.loads(_json(plan))
    signature = _sha(_json({"schema": SCHEMA, "source_document_sha256": source_document_sha256,
                           "source": source, "source_pages": source_pages, "plan": frozen}))
    stage = "local-draft-" + signature[:32]
    check_cancelled()
    previous = store.load_stage(project_id, stage, signature)
    if previous is None:
        # Required, rather than best-effort: do not spend money on metadata
        # when the first editable source draft could not be saved locally.
        store.save_stage(project_id, stage, frozen, signature, complete=True)
    elif previous != frozen:
        raise DocumentProjectError("本地初稿与首次识图来源不一致，已保留原工程。")
    check_cancelled()

    plan = deepcopy(frozen)
    diagnostics["source_matches"] = deepcopy(plan.get("source_matches", []))
    diagnostics["source_review_count"] = sum(bool(row.get("source_review", {}).get("required"))
                                            for row in plan["questions"])
    questions = prepare_questions(deepcopy(plan["questions"]))
    if len(questions) != len(plan["items"]):
        raise DocumentProjectError("本地题稿数量与原文位置回执不一致。")
    if any(any(old.get(field) != new.get(field) for field in
               ("content", "answer_markdown", "referenced_images", "source_number"))
           for old, new in zip(plan["questions"], questions)):
        raise DocumentProjectError("本地题稿后处理改变了首次识图正文或原答案，已保留原稿；未请求分类。")
    plan["questions"] = questions
    for item, question in zip(plan["items"], questions):
        item.update(question=deepcopy(question), content=question["content"],
                    answer_markdown=question.get("answer_markdown", ""))
    preview_signature = _sha(_json({"raw_draft": signature, "questions": questions}))
    store.save_stage(project_id, "local-preview-" + preview_signature[:32], plan,
                     preview_signature, complete=True)
    check_cancelled()
    local = {"schema": SCHEMA, "rules_version": plan.get("rules_version"),
             "status": "partial" if plan.get("partial") else "ready",
             "question_count": len(questions), "source_preserved": True, "persisted": True,
             "source_basis": "first_pass_transcription", "original_verified": False,
             "source_sha256": plan["source_sha256"], "draft_stage": stage,
             "preview_stage": "local-preview-" + preview_signature[:32],
             "saved_at": time.time(), "new_body_generation_calls": 0,
             "reused_local_plan": previous is not None,
             "unresolved_ranges": deepcopy(plan.get("unresolved_ranges", [row for row in plan.get("unassigned", [])
                                                   if row.get("role") == "manual"])),
             "page_issues": deepcopy(plan.get("page_issues", [])),
             "page_furniture": deepcopy(plan.get("page_furniture", [])),
             "structure_census": deepcopy(plan.get("structure_census", {})),
             "manual_reasons": deepcopy(plan.get("manual_reasons", []))}
    if plan.get("workflow") == "page-fragments.v2":
        local["assembly"] = "first_pass_blocks"
        diagnostics["pdf_fragment_draft"] = deepcopy(plan.get("fragment_summary", {}))
    diagnostics["pdf_local_draft"] = local
    publish_draft(deepcopy(plan))
    check_cancelled()
    metadata_cache = PdfDraftMetadataCache(store, project_id, source_document_sha256,
                                           check_cancelled=check_cancelled)
    result = request_pdf_local_draft_metadata(plan, curriculum, provider=provider, post=post, cache=metadata_cache,
        diagnostics=diagnostics, check_cancelled=check_cancelled, report_attempt=report_attempt)
    check_cancelled()
    # A transport or model contract can never become the source of a body.
    if len(result["questions"]) != len(questions) or any(
            any(old.get(field) != new.get(field) for field in
                ("content", "answer_markdown", "image_paths", "referenced_images", "source", "source_number"))
            for old, new in zip(questions, result["questions"])):
        raise DocumentProjectError("分类步骤改变了本地题文，未采用该结果；原稿已保留。")
    diagnostics["pdf_legacy_requests"] = {"new_calls": 0, "replayed_responses": 0,
                                           "body_generation_disabled": True}
    return {"workflow": plan.get("workflow", "local-draft.v1"), "questions": result["questions"],
            "preserved_indices": frozenset(range(len(questions))),
            "partial": bool(result.get("partial")),
            "diagnostics": deepcopy({key: value for key, value in diagnostics.items()
                                     if key != "pdf_project_legacy_parse"})}
