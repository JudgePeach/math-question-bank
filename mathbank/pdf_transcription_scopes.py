"""Private integrity evidence for first-pass PDF machine transcripts.

This proves preservation of a returned transcript and its page/figure assets,
never that the machine transcript is mathematically equivalent to the PDF.
"""
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets

_SECRET = secrets.token_bytes(32)
_MARKER = re.compile(r"<!-- MATHBANK_PDF_PAGE:\d+ -->")
_DIAG_KEYS = ("pdf_extraction", "pdf_native_quality", "pdf_native_repair", "pdf_layout", "pdf_failed_pages")

def _diagnostic_snapshot(value):
    return {key:value.get(key) for key in _DIAG_KEYS}

def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def _asset(path):
    p=Path(path)
    if not p.is_file() or p.is_symlink():
        raise ValueError("transcription_asset_unavailable")
    return {"path":str(p.absolute()),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}

@dataclass(frozen=True)
class _InputWitness:
    payload: str
    signature: str

@dataclass(frozen=True)
class _TranscriptWitness:
    payload: str
    signature: str

@dataclass(frozen=True)
class _PdfTranscriptionSnapshot:
    payload: str
    signature: str

def begin_pdf_transcription_witness(image_path, page_info, *, expected_image_sha256=None, detail_views=(), request_inputs=None):
    """Called before a successful vision request; not a public confidence flag."""
    asset = _asset(image_path)
    if expected_image_sha256 is not None and asset["sha256"] != expected_image_sha256:
        raise ValueError("transcription_input_changed_before_request")
    details = []
    for view in detail_views:
        detail = _asset(view["path"])
        if detail["sha256"] != view["sha256"]:
            raise ValueError("transcription_detail_changed_before_request")
        details.append(detail)
    body=_json({"page_index":page_info["page_index"],"asset":asset,"detail_assets":details,"page_info":page_info,
                "request_inputs_sha256":_sha(_json(request_inputs)) if request_inputs is not None else None})
    return _InputWitness(body,hmac.new(_SECRET,body.encode(),hashlib.sha256).hexdigest())

def finish_pdf_transcription_witness(input_witness, result):
    if type(input_witness) is not _InputWitness or not hmac.compare_digest(input_witness.signature,
            hmac.new(_SECRET,input_witness.payload.encode(),hashlib.sha256).hexdigest()):
        return None
    data=json.loads(input_witness.payload)
    if _asset(data["asset"]["path"])!=data["asset"]:
        return None
    if any(_asset(a["path"]) != a for a in data.get("detail_assets", [])):
        return None
    if not isinstance(result.get("markdown"),str) or not result["markdown"].strip():
        return None
    body=_json({**data,"markdown":result["markdown"],"layout":result.get("layout")})
    return _TranscriptWitness(body,hmac.new(_SECRET,body.encode(),hashlib.sha256).hexdigest())

def _verify_witness(witness):
    if type(witness) is not _TranscriptWitness or not hmac.compare_digest(witness.signature,
            hmac.new(_SECRET,witness.payload.encode(),hashlib.sha256).hexdigest()):
        raise ValueError("transcription_witness_invalid")
    value=json.loads(witness.payload)
    if _asset(value["asset"]["path"])!=value["asset"]:
        raise ValueError("transcription_input_changed")
    if any(_asset(a["path"]) != a for a in value.get("detail_assets", [])):
        raise ValueError("transcription_detail_changed")
    return value

def finalize_pdf_transcription_snapshot(source,diagnostics,*,source_document_sha256,source_pages,
                                        layout_result,witnesses,transcript_normalizer,figure_assets):
    """Bind the exact first-pass result after deterministic figure attachment."""
    from mathbank.pdf_layout import apply_figure_anchors
    from mathbank.pdf_figures import clear_resolved_figure_placeholders
    if (not isinstance(source,str) or len(source)>500000 or not isinstance(source_pages,list)
            or not isinstance(layout_result,dict) or len(source_pages)>80):
        return None
    layout_pages={p["page_index"]:p for p in layout_result.get("pages",[])}
    records, assets, pieces, cursor = [], [], [], 0
    for page in source_pages:
        number=page.get("page_number");origin=page.get("origin");marked=str(page.get("markdown") or "").strip()
        if type(number) is not int or number<=0 or not marked:
            return None
        index=number-1;state=layout_pages.get(index,{})
        text=_MARKER.sub("",marked)
        reasons=[];eligible=False
        witness=witnesses.get(index)
        if origin in {"joint_vision","regional_vision"} and witness is not None:
            data=_verify_witness(witness)
            if data["page_index"]!=index:
                return None
            initial_figures = data.get("layout", {}).get("figures", [])
            final_figures = state.get("figures", [])
            if not isinstance(initial_figures, list) or len(initial_figures) != len(final_figures):
                return None
            initial_by_slot = {f.get("slot_id"): f for f in initial_figures if isinstance(f, dict)}
            if len(initial_by_slot) != len(initial_figures) or any(not slot for slot in initial_by_slot):
                return None
            for figure in final_figures:
                initial = initial_by_slot.get(figure.get("slot_id"))
                if initial is None or any(initial.get(key) != figure.get(key)
                        for key in ("candidate_ids", "model_bbox_raw", "native_box")):
                    return None
            before=f"<!-- MATHBANK_PDF_PAGE:{number} -->\n"+transcript_normalizer(data["markdown"]).strip()
            anchored=apply_figure_anchors(before,state.get("figures",[]))
            expected=clear_resolved_figure_placeholders(anchored["markdown"],
                [f for f in state.get("figures",[]) if f.get("id") in set(anchored["attached"])])
            if expected.strip()!=marked:
                return None
            assets.append(data["asset"])
            assets.extend(data.get("detail_assets", []))
            eligible=True
            if data.get("layout",{}).get("page_complete") is not True:
                reasons.append("first_pass_reported_incomplete")
            if state.get("status")!="checked" or state.get("warnings"):
                reasons.append("first_pass_layout_requires_review")
        else:
            reasons.append("no_verified_visual_transcript_witness")
        for figure in state.get("figures",[]):
            url=figure.get("image_path")
            path=figure_assets.get(url)
            if isinstance(path,str):
                assets.append(_asset(path))
            else:
                reasons.append("figure_asset_not_bound")
            if not figure.get("attached") or figure.get("review_reasons") or figure.get("review_required"):
                reasons.append("figure_requires_review")
        records.append({"page_index":index,"page_number":number,"range":[cursor,cursor+len(text)],
            "origin":origin,"snapshot_eligible":eligible and not reasons,"reliable":False,
            "native_reliable":False,"source_basis":"first_pass_transcription",
            "reasons":reasons,"global_reasons":[],"figure_ownership":"first_pass_bound"})
        pieces.append(text);cursor+=len(text)+2
    if "\n\n".join(pieces)!=source or len({r["page_number"] for r in records})!=len(records):
        return None
    for record in records[:-1]:record["range"][1]+=2
    public_layout=deepcopy(layout_result)
    for page in public_layout.get("pages",[]):
        for figure in page.get("figures",[]):figure.pop("_absolute_path",None)
    payload=_json({"source_sha256":_sha(source),"source_document_sha256":source_document_sha256,
        "diagnostics":_diagnostic_snapshot(diagnostics),
        "source_pages":source_pages,"layout_result":public_layout,"pages":records,"assets":assets,
        "global_reasons":[],"source_basis":"first_pass_transcription","native_reliable":False})
    return _PdfTranscriptionSnapshot(payload,hmac.new(_SECRET,payload.encode(),hashlib.sha256).hexdigest())

def verify_pdf_transcription_snapshot(source,diagnostics,evidence,*,source_document_sha256,
                                     source_pages,layout_result):
    if type(evidence) is not _PdfTranscriptionSnapshot or not hmac.compare_digest(evidence.signature,
            hmac.new(_SECRET,evidence.payload.encode(),hashlib.sha256).hexdigest()):
        return {"status":"uncertain","global_reasons":["first_pass_snapshot_missing"]}
    value=json.loads(evidence.payload)
    if (value["source_sha256"]!=_sha(source) or value["source_document_sha256"]!=source_document_sha256
            or value["source_pages"]!=source_pages or value["layout_result"]!=layout_result
            or value["diagnostics"]!=_diagnostic_snapshot(diagnostics)):
        return {"status":"uncertain","global_reasons":["first_pass_snapshot_changed"]}
    try:
        if any(_asset(a["path"])!=a for a in value["assets"]):
            return {"status":"uncertain","global_reasons":["first_pass_asset_changed"]}
    except (ValueError,OSError):
        return {"status":"uncertain","global_reasons":["first_pass_asset_missing"]}
    return {**value,"status":"ready","proof_sha256":_sha(evidence.payload)}
