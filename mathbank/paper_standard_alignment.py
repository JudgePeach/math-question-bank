"""Bind optional model suggestions to real selected stems and source excerpts.

Matching evidence and references establishes provenance, not pedagogical
correctness, measured difficulty, or a student's attained competency level.
"""
from __future__ import annotations

import re

from .source_comments import strip_source_attribution_comment


_UNSUPPORTED_CLAIM = re.compile(
    r"(?:水平|等级)\s*[一二三123ⅠⅡⅢ]|学生.{0,12}(?:已|达到|达成)|"
    r"(?:已|官方).{0,5}(?:核验|认证|确认达标)|得分率|难度系数"
)


def validate_standard_alignment(raw, *, selected, ai_question_ids, context):
    """Invalid/missing optional metadata never discards a selected question."""
    if not context.get("enabled"):
        return [], []
    references = {row["id"]: row for row in context.get("references", [])}
    allowed = {q.id: q for q in selected if q.id in ai_question_ids}
    if not allowed:
        return [], []
    if raw is None:
        return [], ["模型未提供可核对的逐题课标对应说明，已列出参考条目供教师复核。"]
    if not isinstance(raw, list) or len(raw) > 60:
        return [], ["逐题课标对应说明格式无效，未采用该说明；选题结果保留。"]
    accepted, seen, rejected = [], set(), 0
    for item in raw:
        if not isinstance(item, dict):
            rejected += 1
            continue
        qid = item.get("question_id")
        refs, target, evidence = (item.get(field) for field in ("reference_ids", "target", "evidence"))
        if (type(qid) is not int or qid not in allowed or qid in seen
                or not isinstance(refs, list) or not 1 <= len(refs) <= 8
                or any(not isinstance(ref, str) or ref not in references for ref in refs)
                or not isinstance(target, str) or not 1 <= len(target.strip()) <= 600
                or not isinstance(evidence, str) or not 2 <= len(evidence.strip()) <= 1000
                or "\x00" in target or "\x00" in evidence
                or _UNSUPPORTED_CLAIM.search(target)
                or any(field in item for field in ("competency_level", "quality_level", "attained_level", "difficulty_coefficient"))):
            rejected += 1
            continue
        visible = strip_source_attribution_comment(allowed[qid].content or "")
        if evidence not in visible:
            rejected += 1
            continue
        ref_ids = list(dict.fromkeys(refs))
        accepted.append({
            "question_id": qid, "reference_ids": ref_ids, "target": target.strip(),
            "evidence": evidence, "status": "ai_suggestion_with_text_evidence",
            "references": [{key: references[ref].get(key) for key in (
                "id", "document", "heading", "pdf_pages", "book_pages", "source_sha256"
            )} for ref in ref_ids],
        })
        seen.add(qid)
    warnings = []
    if rejected:
        warnings.append("部分逐题课标对应说明的题号、出处或题文依据未通过核对，或包含未经支持的等级判断，未采用这些说明。")
    if allowed.keys() - seen:
        warnings.append("部分AI选定题尚无可核对的课标对应说明，请结合所列条目复核。")
    return accepted, warnings


def _pages(values):
    values = [str(value) for value in (values or []) if type(value) is int and value > 0]
    return "、".join(values) or "未标注"


def standard_analysis_text(context, alignment, *, local_unreviewed=0, question_numbers=None):
    if not context.get("enabled"):
        return "课标依据：本次资料不可用，未据此评估选题；请按既有选题说明复核。"
    references = context.get("references", [])
    if not references:
        return "课标依据：本次未匹配到可引用条目，未作课标契合度判断。"
    source = context.get("source", {})
    title = source.get("title") or "所提供的2017年版2025年修订课标语料"
    lines = ["课标参考：" + str(title) + "。"]
    topics = context.get("matched_topics") or []
    if topics:
        lines.append("匹配主题：" + "、".join(str(topic) for topic in topics) + "。")
    reference_numbers = {reference["id"]: index for index, reference in enumerate(references, 1)}
    for index, reference in enumerate(references, 1):
        heading = str(reference.get("heading") or reference.get("document") or "课标条目")[:180]
        lines.append(f"依据{index}：{heading}；书内第{_pages(reference.get('book_pages'))}页，PDF第{_pages(reference.get('pdf_pages'))}页。")
    for item in alignment:
        number = (question_numbers or {}).get(item["question_id"])
        label = f"题库第{number}题" if number is not None else f"题库ID {item['question_id']}"
        lines.append(f"{label}：{item['target']}；对应依据" + "、".join(
            str(reference_numbers[reference_id]) for reference_id in item["reference_ids"]) + "。")
    if alignment:
        lines.append("逐题对应为AI建议，题文引文和出处已核对，考查目标的适切性仍由教师审阅。")
    else:
        lines.append("以上为本次选题参考条目，尚未形成可核对的逐题课标对应结论。")
    if local_unreviewed:
        lines.append(f"其中{local_unreviewed}道为本地规则补选，未评估其课标契合度。")
    return "\n".join(lines)
