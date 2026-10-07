"""PDF adapters retain native and first-pass-transcript evidence distinctions."""
from mathbank.source_hybrid_request import request_source_hybrid, SourceHybridResult
from mathbank.source_metadata import (
    SourceMetadataContractError,
    SourceMetadataMessageBudgetError as PdfHybridMessageBudgetError,
)
from mathbank.pdf_hybrid_plan import (
    PdfHybridPlanError, require_pdf_hybrid_plan, require_pdf_snapshot_hybrid_plan,
    pdf_hybrid_plan_diagnostics,
)

def request_pdf_hybrid(plan,*args,**kwargs):
    validator=(require_pdf_hybrid_plan if plan.get("source_basis")=="native_pdf"
               else require_pdf_snapshot_hybrid_plan)
    return request_source_hybrid(plan,*args,**kwargs,plan_validator=validator,
        plan_diagnostics=pdf_hybrid_plan_diagnostics,
        hard_stop_errors=(SourceMetadataContractError,PdfHybridPlanError),
        diagnostics_key="pdf_hybrid",source_label="PDF",allowed_modes=("hybrid","whole_metadata"))

def prepare_pdf_task_plan(source,diagnostics,*,source_pages,layout_result,task_id,generation,
                          source_document_sha256,native_evidence,witnesses,transcript_normalizer,figure_assets):
    from .pdf_source_scopes import finalize_pdf_source_review_evidence
    from .pdf_transcription_scopes import finalize_pdf_transcription_snapshot
    from .pdf_hybrid_plan import build_pdf_hybrid_plan,build_pdf_snapshot_hybrid_plan
    common={"source_pages":source_pages,"layout_result":layout_result,"task_id":task_id,
            "generation":generation,"source_document_sha256":source_document_sha256,
            "min_metadata_source_fraction":0.5}
    evidence=finalize_pdf_source_review_evidence(source,diagnostics,
        source_document_sha256=source_document_sha256,source_pages=source_pages,layout_result=layout_result,
        native_evidence=native_evidence,asset_paths=figure_assets)
    plan=build_pdf_hybrid_plan(source,diagnostics,source_review_evidence=evidence,**common)
    if plan["mode"]=="original" and source_pages:
        snapshot=finalize_pdf_transcription_snapshot(source,diagnostics,
            source_document_sha256=source_document_sha256,source_pages=source_pages,layout_result=layout_result,
            witnesses=witnesses,transcript_normalizer=transcript_normalizer,figure_assets=figure_assets)
        plan=build_pdf_snapshot_hybrid_plan(source,diagnostics,source_review_evidence=snapshot,**common)
    return plan
