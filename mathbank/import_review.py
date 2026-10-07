"""Source comparison is diagnostic information, not an import permission gate.

The underlying comparison/verification evidence is retained verbatim. A failed
alignment is not proof that a question is wrong, and must not force users to
approve every question before importing it. This policy does not approve content
or relax upload, duplicate, database, or model-response validation.
"""
from __future__ import annotations

import re


WORD_FORMULA_EXTRACTION_REASON = "Word 原生公式提取存在疑点，请对照原页核对。"
_WORD_FORMULA_NOTICE = re.compile(r"\[(?:公式(?:结构)?|特殊字符)待核对(?:[：:][^\]\n]*)?\]")
_WORD_FORMULA_WARNINGS = ("部分 Office 公式含暂未完整支持的结构", "MathType 公式包含无法识别的字符", "无法转换 Word 特殊字符：")


def _word_formula_owners(source: str, diagnostics: dict, count: int, *,
                        source_review_evidence=None, extraction_diagnostics=None,
                        source_document_sha256=None) -> list[dict]:
    items = []
    for marker in _WORD_FORMULA_NOTICE.finditer(source):
        owners = set()
        for match in diagnostics.get("source_matches", []):
            index, start, end = (match.get(key) for key in ("question_index", "source_start", "source_end"))
            if (type(index) is int and 0 <= index < count and type(start) is int and type(end) is int
                    and 0 <= start <= marker.start() < marker.end() <= end <= len(source)
                    and source[start:end] == match.get("source_excerpt")
                    and match.get("field") in {"content", "answer_markdown"}):
                owners.add(index)
        items.append({"source_start": marker.start(), "source_end": marker.end(),
                      "question_index": next(iter(owners)) if len(owners) == 1 else None})
    if source_review_evidence is not None and isinstance(extraction_diagnostics, dict):
        from mathbank.docx_source_scopes import verify_source_review_evidence
        verified = verify_source_review_evidence(source, extraction_diagnostics,
            source_review_evidence, source_document_sha256=source_document_sha256)
        missing_count = extraction_diagnostics.get("native_missing_glyphs", 0)
        missing_count = missing_count if type(missing_count) is int and missing_count > 0 else 0
        unlocated = missing_count if verified.get("status") != "ready" else 0
        for block in verified.get("blocks", []):
            counters = block.get("risk_counters", {})
            if not any(counters.get(key, 0) for key in ("omml_unsupported", "mtef_fallback_images",
                "mtef_unavailable", "symbols_unavailable", "native_missing_glyphs")):
                continue
            start, end = block["range"]
            owners = set()
            for match in diagnostics.get("source_matches", []):
                index, first, last = (match.get(key) for key in ("question_index", "source_start", "source_end"))
                if (type(index) is int and 0 <= index < count and type(first) is int and type(last) is int
                    and 0 <= first < last <= len(source) and first < end and last > start
                    and source[first:last] == match.get("source_excerpt")
                    and match.get("field") in {"content", "answer_markdown"}
                    and source[max(first, start):min(last, end)].strip()):
                    owners.add(index)
            missing = bool(counters.get("native_missing_glyphs", 0))
            if not owners and missing:
                unlocated += int(counters["native_missing_glyphs"])
            for index in sorted(owners) if owners else [None]:
                native = {"source_start": start, "source_end": end, "question_index": index,
                    "native_risk": True, "native_missing_glyphs": missing,
                    "risk_counters": dict(counters)}
                same = [item for item in items if item["question_index"] == index
                        and start <= item["source_start"] <= item["source_end"] <= end]
                if same:
                    for item in same:
                        item.update(native_risk=True, native_missing_glyphs=missing,
                                    risk_counters=dict(counters), native_source_range=[start, end])
                else:
                    items.append(native)
        diagnostics["word_native_missing_glyphs_unlocated"] = unlocated
    return items


def prepare_word_extraction_reviews(questions: list[dict], diagnostics: dict, source: str, *,
                                    source_review_evidence=None, extraction_diagnostics=None,
                                    source_document_sha256=None) -> None:
    """An unchanged extraction placeholder is not proof of a correct formula.

    Bind native formula diagnostics to exact source ranges where available, so
    successful text alignment does not bypass the visual review of extraction.
    Unlocated warnings stay global instead of being broadcast to every card.
    """
    items = _word_formula_owners(source, diagnostics, len(questions),
        source_review_evidence=source_review_evidence, extraction_diagnostics=extraction_diagnostics,
        source_document_sha256=source_document_sha256)
    diagnostics["word_formula_extraction_items"] = items
    indices = {item["question_index"] for item in items if item["question_index"] is not None}
    indices.update(index for index, question in enumerate(questions)
                   if any(_WORD_FORMULA_NOTICE.search(question.get(field) or "")
                          for field in ("content", "answer_markdown")))
    for index in indices:
        review = questions[index].setdefault("source_review", {})
        review["required"] = True
        native = [item for item in items if item["question_index"] == index and item.get("native_risk")]
        if native:
            review["native_extraction_risks"] = [{key: value for key, value in item.items()
                if key != "question_index"} for item in native]
            if any(item.get("native_missing_glyphs") for item in native):
                review["native_missing_glyphs"] = True
        reasons = review.setdefault("reasons", [])
        if WORD_FORMULA_EXTRACTION_REASON not in reasons:
            reasons.append(WORD_FORMULA_EXTRACTION_REASON)
        if not review.get("source_excerpt"):
            review["source_excerpt"] = "\n\n".join(
                match["source_excerpt"] for match in diagnostics.get("source_matches", [])
                if match.get("question_index") == index and isinstance(match.get("source_excerpt"), str)
            )
    diagnostics["source_review_count"] = sum(bool((q.get("source_review") or {}).get("required")) for q in questions)


def finalize_word_extraction_reviews(questions: list[dict], diagnostics: dict, source: str, *,
                                     source_review_evidence=None, extraction_diagnostics=None,
                                     source_document_sha256=None) -> None:
    """Report only unresolved extraction notices; retain original audit counts."""
    # Visual retrieval may have added exact source mappings during verification.
    items = _word_formula_owners(source, diagnostics, len(questions),
        source_review_evidence=source_review_evidence, extraction_diagnostics=extraction_diagnostics,
        source_document_sha256=source_document_sha256)
    resolved = 0
    for item in items:
        index = item["question_index"]
        review = (questions[index].get("source_review") or {}) if index is not None else {}
        item["verified"] = (not item.get("native_missing_glyphs")
                            and review.get("required") is False and review.get("verified_by") == "vision"
                            and (review.get("verification") or {}).get("decision") == "equivalent")
        resolved += int(item["verified"])
    diagnostics["word_formula_extraction_items"] = items
    diagnostics["word_extraction_review_count"] = max(int(diagnostics.get("review_required", 0)), len(items)) - resolved
    original = diagnostics.setdefault("word_extraction_warnings_original", list(diagnostics.get("word_extraction_warnings", [])))
    if items and resolved == len(items):
        diagnostics["word_extraction_warnings"] = [warning for warning in original
            if not warning.startswith(_WORD_FORMULA_WARNINGS)]
    else:
        diagnostics["word_extraction_warnings"] = list(original)


def make_source_review_advisory(questions: list[dict], diagnostics: dict) -> None:
    pending = 0
    for question in questions:
        review = question.get("source_review")
        if not isinstance(review, dict):
            continue
        if review.get("required") is True:
            pending += 1
        if review.get("required") is True or review.get("verified_by") == "vision":
            review["blocking"] = False
            review["disposition"] = "advisory"
    diagnostics.update(
        source_review_mode="advisory",
        source_review_notice_count=pending,
        source_review_blocking_count=0,
    )
