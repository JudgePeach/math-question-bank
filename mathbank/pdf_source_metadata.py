"""PDF-specific certificates for native source or retained machine transcripts."""
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json

from mathbank import source_metadata as structure

@dataclass(frozen=True)
class _PdfSourceMetadataCertificate:
    original_source_sha256: str
    projection_sha256: str
    question_snapshot_sha256: str
    metadata_snapshot_sha256: str
    proof_sha256: str
    owned_ranges: tuple
    context_ranges: tuple
    original_question_ids: tuple
    task_id: str
    generation: int
    group_id: str
    source_basis: str
    native_reliable: bool

def _sha(value):return hashlib.sha256(value.encode()).hexdigest()
def _snapshot(value):return _sha(json.dumps(value,ensure_ascii=False,sort_keys=True))

def _verify(source,diagnostics,evidence,*,kind,source_document_sha256,source_pages,layout_result):
    if kind=="native":
        from .pdf_source_scopes import verify_pdf_source_review_evidence
        return verify_pdf_source_review_evidence(source,diagnostics,evidence,
            source_document_sha256=source_document_sha256,source_pages=source_pages,layout_result=layout_result)
    from .pdf_transcription_scopes import verify_pdf_transcription_snapshot
    return verify_pdf_transcription_snapshot(source,diagnostics,evidence,
        source_document_sha256=source_document_sha256,source_pages=source_pages,layout_result=layout_result)

def _prepare(source,diagnostics,*,kind,owned_ranges,context_ranges,group_id,task_id,generation,
             source_document_sha256,source_review_evidence,source_pages,layout_result):
    proof=_verify(source,diagnostics,source_review_evidence,kind=kind,
        source_document_sha256=source_document_sha256,source_pages=source_pages,layout_result=layout_result)
    error=structure.SourceMetadataContractError
    if proof.get("status")!="ready" or proof.get("global_reasons"):
        raise error("PDF来源证据未完整对应。")
    if (not isinstance(task_id,str) or not task_id or type(generation) is not int or generation<0
            or not isinstance(group_id,str) or not group_id):
        raise error("PDF来源任务身份无效。")
    def ranges(values):
        if not isinstance(values,(list,tuple)) or len(values)>3000:raise error("PDF来源范围无效。")
        result=[]
        for v in values:
            if (not isinstance(v,(list,tuple)) or len(v)!=2 or any(type(n) is not int for n in v)
                    or not 0<=v[0]<v[1]<=len(source)):raise error("PDF来源范围无效。")
            result.append(tuple(v))
        result.sort()
        if len(set(result))!=len(result) or any(a[1]>b[0] for a,b in zip(result,result[1:])):
            raise error("PDF来源范围交叠。")
        return tuple(result)
    owned,context=ranges(owned_ranges),ranges(context_ranges)
    original=structure.inspect_source_structure(source)
    if structure._GROUP_FATAL_REASONS.intersection(original["fallback_reasons"]):
        raise error("PDF全源结构仍有歧义。")
    expected_context=tuple(sorted((r["start"],r["end"]) for r in original["document_metadata"]))
    if context!=expected_context:raise error("PDF来源声明上下文遗漏。")
    selected=[];expected=set()
    for q in original["questions"]:
        body=tuple(q["source_range"]);answer=tuple(q["answer_source_range"]) if q["answer_source_range"] else None
        if body in owned:
            if answer and answer not in owned:raise error("PDF原答案未完整保留。")
            selected.append(q);expected.add(body)
            if answer:expected.add(answer)
    if not selected or set(owned)!=expected:raise error("PDF局部范围并非完整原题及唯一原答。")
    for q in selected:
        qbounds=[q["source_range"]]+([q["answer_source_range"]] if q["answer_source_range"] else [])
        numbers=sorted({page["page_number"] for page in proof["pages"]
            if any(x<page["range"][1] and page["range"][0]<y
                   and source[max(x,page["range"][0]):min(y,page["range"][1])].strip()
                   for x,y in qbounds)})
        selected_pages={p["page_number"] for p in proof["pages"]}
        if numbers and any(number not in selected_pages for number in range(numbers[0],numbers[-1]+1)):
            raise error("PDF原题或原答案跨越未选页面，不能认证完整来源。")
    dependency=original.get("source_dependencies",{})
    risky={r["id"] for r in dependency.get("question_risks",[])}
    if dependency.get("status")!="complete" or dependency.get("has_unresolved") or any(q["id"] in risky for q in selected):
        raise error("PDF局部原题仍有跨题依赖。")
    for page in proof["pages"]:
        a,b=page["range"]
        if any(x<b and a<y and source[max(a,x):min(b,y)].strip() for x,y in owned):
            accepted=page.get("reliable") if kind=="native" else page.get("snapshot_eligible")
            if not accepted:raise error("PDF题干或原答跨越未获对应来源证据的页面。")
        if any(x<b and a<y and source[max(a,x):min(b,y)].strip() for x,y in context):
            accepted=page.get("reliable") if kind=="native" else page.get("origin") in {"joint_vision","regional_vision"}
            if not accepted:raise error("PDF题头上下文缺少对应来源证据。")
    combined=tuple(sorted((*owned,*context)))
    if any(a[1]>b[0] for a,b in zip(combined,combined[1:])):raise error("PDF上下文与题文范围相交。")
    projection="".join(source[slice(*v)] for v in combined)
    local=structure.inspect_source_structure(projection)
    if not local["eligible"] or len(local["questions"])!=len(selected):raise error("PDF局部结构未通过完整源核对。")
    by_number={q["source_number"]:q for q in selected}
    for q in local["questions"]:
        old=by_number.get(q["source_number"])
        if old is None or q["raw_content"]!=old["raw_content"] or q["raw_answer"]!=old["raw_answer"]:
            raise error("PDF局部投影改变原题或原答边界。")
        q["id"]=old["id"];q["original_source_range"]=old["source_range"]
        q["original_answer_source_range"]=deepcopy(old["answer_source_range"])
    basis="native_pdf" if kind=="native" else "first_pass_transcription"
    local["_pdf_source_certificate"]=_PdfSourceMetadataCertificate(_sha(source),_sha(projection),
        structure._question_snapshot(local["questions"]),_snapshot(local["document_metadata"]),proof["proof_sha256"],
        owned,context,tuple(q["id"] for q in selected),task_id,generation,group_id,basis,kind=="native")
    local.update(source_basis=basis,native_reliable=kind=="native",original_source_sha256=_sha(source))
    local["_pdf_origin"]={"source":source,"diagnostics":deepcopy(diagnostics),"evidence":source_review_evidence,
        "source_document_sha256":source_document_sha256,"source_pages":deepcopy(source_pages),"layout_result":deepcopy(layout_result)}
    return local

def prepare_pdf_source_group(source,diagnostics,**kwargs):return _prepare(source,diagnostics,kind="native",**kwargs)
def prepare_pdf_transcription_group(source,diagnostics,**kwargs):return _prepare(source,diagnostics,kind="snapshot",**kwargs)

def require_pdf_metadata_certificate(plan,*,task_id=None,generation=None,group_id=None,source_basis=None):
    certificate=plan.get("_pdf_source_certificate");error=structure.SourceMetadataContractError
    if type(certificate) is not _PdfSourceMetadataCertificate or not plan.get("eligible"):
        raise error("缺少PDF私有来源证据。")
    if ((task_id is not None and certificate.task_id!=task_id)
            or generation is not None and certificate.generation!=generation
            or group_id is not None and certificate.group_id!=group_id
            or source_basis is not None and certificate.source_basis!=source_basis):
        raise error("PDF来源任务或证据种类已改变。")
    origin=plan["_pdf_origin"]
    proof=_verify(origin["source"],origin["diagnostics"],origin["evidence"],
        kind="native" if certificate.native_reliable else "snapshot",
        source_document_sha256=origin["source_document_sha256"],source_pages=origin["source_pages"],layout_result=origin["layout_result"])
    projection="".join(origin["source"][slice(*v)] for v in sorted((*certificate.owned_ranges,*certificate.context_ranges)))
    if (proof.get("status")!="ready" or proof.get("proof_sha256")!=certificate.proof_sha256
            or _sha(origin["source"])!=certificate.original_source_sha256
            or projection!=plan.get("source") or _sha(projection)!=certificate.projection_sha256
            or structure._question_snapshot(plan["questions"])!=certificate.question_snapshot_sha256
            or _snapshot(plan["document_metadata"])!=certificate.metadata_snapshot_sha256
            or plan.get("source_basis")!=certificate.source_basis
            or plan.get("native_reliable") is not certificate.native_reliable):
        raise error("PDF源、资产或局部投影已改变。")

def require_pdf_source_group_certificate(plan,**kwargs):
    return require_pdf_metadata_certificate(plan,source_basis="native_pdf",**kwargs)

def require_pdf_transcription_group_certificate(plan,**kwargs):
    return require_pdf_metadata_certificate(plan,source_basis="first_pass_transcription",**kwargs)
