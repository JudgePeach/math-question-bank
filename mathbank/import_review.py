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


def _word_formula_owners(source: str, diagnostics: dict, count: int) -> list[dict]:
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
    return items


def prepare_word_extraction_reviews(questions: list[dict], diagnostics: dict, source: str) -> None:
    """An unchanged extraction placeholder is not proof of a correct formula.

    Bind native formula diagnostics to exact source ranges where available, so
    successful text alignment does not bypass the visual review of extraction.
    Unlocated warnings stay global instead of being broadcast to every card.
    """
    items = _word_formula_owners(source, diagnostics, len(questions))
    diagnostics["word_formula_extraction_items"] = items
    indices = {item["question_index"] for item in items if item["question_index"] is not None}
    indices.update(index for index, question in enumerate(questions)
                   if any(_WORD_FORMULA_NOTICE.search(question.get(field) or "")
                          for field in ("content", "answer_markdown")))
    for index in indices:
        review = questions[index].setdefault("source_review", {})
        review["required"] = True
        reasons = review.setdefault("reasons", [])
        if WORD_FORMULA_EXTRACTION_REASON not in reasons:
            reasons.append(WORD_FORMULA_EXTRACTION_REASON)
        if not review.get("source_excerpt"):
            review["source_excerpt"] = "\n\n".join(
                match["source_excerpt"] for match in diagnostics.get("source_matches", [])
                if match.get("question_index") == index and isinstance(match.get("source_excerpt"), str)
            )
    diagnostics["source_review_count"] = sum(bool((q.get("source_review") or {}).get("required")) for q in questions)


def finalize_word_extraction_reviews(questions: list[dict], diagnostics: dict, source: str) -> None:
    """Report only unresolved extraction notices; retain original audit counts."""
    # Visual retrieval may have added exact source mappings during verification.
    items = _word_formula_owners(source, diagnostics, len(questions))
    resolved = 0
    for item in items:
        index = item["question_index"]
        review = (questions[index].get("source_review") or {}) if index is not None else {}
        item["verified"] = (review.get("required") is False and review.get("verified_by") == "vision"
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
