"""Conservative, source-independent classification repair for AI imports.

Call this on decoded model objects before presenting import cards. Plain dict
``classification_review`` values are untrusted and are always discarded. A
private dict subclass identifies reviews made in this process, allowing an
unchanged object (including a deepcopy) to be normalized again without losing
its original classification. JSON round trips intentionally remove that trust;
persisted application reviews must be restored by a separate trusted boundary,
not fed back through the raw-model normalization entry point.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import re


_FIELDS = ("category_compulsory", "category_chapter", "category_knowledge")
_LEGACY_BOOK_ALIASES = {
    "必修第一册": "必修一",
    "必修第二册": "必修二",
    "必修第三册": "必修三",
    "必修第四册": "必修四",
    "选择性必修一": "选修一",
    "选择性必修二": "选修二",
    "选择性必修三": "选修三",
    "选择性必修第一册": "选修一",
    "选择性必修第二册": "选修二",
    "选择性必修第三册": "选修三",
}
_DISPLAY_CHARACTERS = str.maketrans("０１２３４５６７８９．：", "0123456789.:")
_CHAPTER_NUMBER = re.compile(r"^(\d{1,3})[.、:](?!\d)(.+)$")
_CHINESE_CHAPTER = re.compile(r"^第([零〇一二三四五六七八九十百\d]+)章(.+)$")
_SECTION_NUMBER = re.compile(r"^(\d{1,3}(?:\.\d{1,3})+)[.、:]?([^\d.].*)$")


class _LocalReview(dict):
    """Runtime-only provenance; a model JSON object cannot construct this type."""


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).translate(_DISPLAY_CHARACTERS)


def _chapter_integer(value: str) -> str | None:
    if value.isascii() and value.isdigit():
        return str(int(value))
    digits = {char: number for number, char in enumerate("零一二三四五六七八九")}
    digits["〇"] = 0
    total = pending = 0
    for char in value:
        if char in digits:
            pending = digits[char]
        elif char in {"十", "百"}:
            total += (pending or 1) * {"十": 10, "百": 100}[char]
            pending = 0
        else:
            return None
    number = total + pending
    # Reject malformed or ambiguous numeral strings such as 一二/一百二.
    if not 0 <= number <= 999:
        return None
    chinese_digits = "零一二三四五六七八九"
    canonical = ""
    hundreds, rest = divmod(number, 100)
    if hundreds:
        canonical = chinese_digits[hundreds] + "百"
        if 0 < rest < 10:
            canonical += "零"
    tens, ones = divmod(rest, 10)
    if tens:
        canonical += (chinese_digits[tens] if tens != 1 or hundreds else "") + "十"
    if ones:
        canonical += chinese_digits[ones]
    canonical = canonical or "零"
    return str(number) if canonical == value.replace("〇", "零") else None


def _number_and_title(value: str, level: str) -> tuple[str | None, str]:
    compact = _compact(value)
    if level == "chapter":
        match = _CHINESE_CHAPTER.fullmatch(compact)
        if match:
            number = _chapter_integer(match[1])
            return (number, match[2]) if number is not None else (None, compact)
        match = _CHAPTER_NUMBER.fullmatch(compact)
        if match:
            return str(int(match[1])), match[2]
    elif level == "knowledge":
        match = _SECTION_NUMBER.fullmatch(compact)
        if match:
            return ".".join(str(int(part)) for part in match[1].split(".")), match[2]
    return None, compact


def _resolve(value, names: list[str], level: str) -> tuple[str, str, list[str]]:
    if not isinstance(value, str):
        return "", "invalid_type", []
    if not value.strip():
        return "", "missing", []
    # Exact custom names always win, including names containing digits.
    if value in names:
        return value, "exact", []
    compact = _compact(value)
    matches = [name for name in names if _compact(name) == compact]
    if not matches and level == "book" and compact in _LEGACY_BOOK_ALIASES:
        target = _LEGACY_BOOK_ALIASES[compact]
        matches = [name for name in names if _compact(name) == _compact(target)]
        kind = "alias_normalized"
    else:
        kind = "format_normalized"
    if not matches and level != "book":
        number, title = _number_and_title(value, level)
        for name in names:
            candidate_number, candidate_title = _number_and_title(name, level)
            # Explicitly conflicting chapter/section numbers are not formatting.
            if title == candidate_title and (
                number is None or (candidate_number is not None and number == candidate_number)
            ):
                matches.append(name)
    if len(matches) == 1:
        return matches[0], kind, []
    return "", "ambiguous" if matches else "unknown", matches


def _catalogue(curriculum: dict) -> dict[str, dict[str, list[str]]]:
    if not isinstance(curriculum, Mapping):
        return {}
    return {
        book: {
            chapter: list(dict.fromkeys(section for section in sections if isinstance(section, str)))
            if isinstance(sections, (list, tuple)) else []
            for chapter, sections in chapters.items() if isinstance(chapter, str)
        }
        for book, chapters in curriculum.items()
        if isinstance(book, str) and isinstance(chapters, Mapping)
    }


def _normalize_question(question: dict, catalogue: dict, signature: tuple) -> None:
    previous = question.pop("classification_review", None)
    current = {field: question.get(field) for field in _FIELDS}
    if isinstance(previous, _LocalReview) and previous._snapshot == current:
        if previous._catalogue == signature:
            question["classification_review"] = previous
            return
        original = deepcopy(previous["original"])
        missing = list(previous["original_missing_fields"])
    else:
        original = deepcopy(current)
        missing = [field for field in _FIELDS if field not in question]

    values = {field: "" for field in _FIELDS}
    reason_codes: list[str] = []
    messages: list[str] = []
    normalized_fields: list[str] = []
    candidates: dict[str, list[str]] = {}
    required = False

    def accept(field: str, level: str, names: list[str], label: str) -> str:
        nonlocal required
        value = original[field]
        resolved, kind, alternatives = _resolve(value, names, level)
        if field in missing:
            kind = "missing"
        values[field] = resolved
        if kind == "exact":
            return resolved
        reason_codes.append(f"{level}_{kind}")
        if resolved:
            normalized_fields.append(field)
            messages.append(f"{label}名称已按当前目录规范化")
        else:
            required = True
            messages.append({
                "missing": f"缺少{label}，请人工选择",
                "invalid_type": f"{label}字段格式无效，请人工选择",
                "ambiguous": f"{label}名称对应多个目录项，请人工确认",
                "unknown": f"{label}不在已确认的当前目录范围内，请人工选择",
            }[kind])
            if alternatives:
                candidates[field] = alternatives
        return resolved

    book = accept(_FIELDS[0], "book", list(catalogue), "册别")
    if book:
        chapter = accept(_FIELDS[1], "chapter", list(catalogue[book]), "章节")
        if chapter:
            raw_knowledge = original[_FIELDS[2]]
            # The subsection is optional: an omitted/empty field means whole chapter.
            if _FIELDS[2] in missing or (isinstance(raw_knowledge, str) and not raw_knowledge.strip()):
                if isinstance(raw_knowledge, str) and raw_knowledge:
                    normalized_fields.append(_FIELDS[2])
                    reason_codes.append("knowledge_format_normalized")
                    messages.append("空白小节已规范为整章定位")
            else:
                accept(_FIELDS[2], "knowledge", catalogue[book][chapter], "小节")

    question.update(values)
    if not reason_codes:
        return
    cleared_fields = [field for field in _FIELDS if not values[field] and original[field] not in (None, "")]
    review = _LocalReview(
        required=required,
        reason_codes=reason_codes,
        message="；".join(messages) + "。",
        original=original,
        original_missing_fields=missing,
        normalized_fields=normalized_fields,
        cleared_fields=cleared_fields,
    )
    if candidates:
        review["candidates"] = candidates
    review._snapshot = dict(values)
    review._catalogue = signature
    question["classification_review"] = review


def normalize_import_classifications(
    questions: list[dict], curriculum: dict, diagnostics: dict | None = None,
) -> None:
    """Normalize only unambiguous labels, retaining reliable parent locations.

    All question content, answers, source-review evidence and unknown fields stay
    untouched. Classification failures become independent review metadata, never
    exceptions or omitted questions. Candidate suggestions are limited to the
    already confirmed parent; no semantic or cross-book matching is performed.
    ``diagnostics['import_classification']`` reports question and field counts.
    """
    catalogue = _catalogue(curriculum)
    signature = tuple((book, tuple((chapter, tuple(sections)) for chapter, sections in chapters.items()))
                      for book, chapters in catalogue.items())
    for question in questions:
        if isinstance(question, dict):
            _normalize_question(question, catalogue, signature)
    if diagnostics is not None:
        diagnostics["import_classification"] = summarize_import_classifications(questions)


def summarize_import_classifications(questions: list[dict]) -> dict:
    """Read existing application-owned reviews without changing any card.

    This also accepts plain JSON reviews restored from an accepted task
    checkpoint. It does not authenticate model metadata: call normalization at
    the raw-model boundary first, and use this helper only to summarize that
    application's accepted results, including after a JSON restore.
    """
    counts = {"total": len(questions), "normalized": 0, "review_required": 0,
              "normalized_fields": 0, "invalid_question_objects": 0}
    reason_counts: Counter = Counter()
    for question in questions:
        if not isinstance(question, dict):
            counts["invalid_question_objects"] += 1
            continue
        review = question.get("classification_review")
        if isinstance(review, dict):
            recorded_fields = review.get("normalized_fields")
            fields = (len({field for field in recorded_fields if isinstance(field, str) and field in _FIELDS})
                      if isinstance(recorded_fields, list) else 0)
            counts["normalized"] += bool(fields)
            counts["normalized_fields"] += fields
            counts["review_required"] += review.get("required") is True
            codes = review.get("reason_codes")
            if isinstance(codes, list):
                reason_counts.update(code for code in codes if isinstance(code, str))
    return {**counts, "reason_counts": dict(reason_counts)}
