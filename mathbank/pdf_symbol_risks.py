"""First-pass PDF symbol risk hints; never source truth or a text correction."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from mathbank.content_locks import _REFERENCE, _formulas, _reference_literal_ranges


ARC_MARK_REASON = "圆弧语境中的点名可能缺顶线，请按原图核对。"
POINT_CASE_REASON = "几何点名大小写存在疑点，请按原图核对。"
SYMBOL_RISK_REASONS = frozenset({ARC_MARK_REASON, POINT_CASE_REASON})
MAX_FIELD_CHARS = 50000
MAX_FORMULAS = 512
MAX_RISKS = 8
MAX_CONTEXT_CHARS = 80
_POINT = r"[A-Z](?:_(?:[A-Za-z0-9]|\{[A-Za-z0-9]{1,8}\}))?"
_SINGLE_POINT = re.compile(_POINT + r"\Z")
_TWO_POINTS = re.compile(_POINT + _POINT + r"\Z")
_GEOMETRY = re.compile(r"圆弧|圆心|直线|线段|直径|半径|三角形|正方形|菱形|交点|顶点|垂直|平行")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _bare_body(formula: str) -> str | None:
    for opening, closing in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        if formula.startswith(opening) and formula.endswith(closing):
            return re.sub(r"\s+", "", formula[len(opening):-len(closing)])
    return None


def _scan(content: str) -> tuple[list[dict], int, str | None]:
    if len(content) > MAX_FIELD_CHARS:
        return [], 0, "field_size_limit"
    if _REFERENCE.search(content):
        # Unrestored source protocols already lack complete readable evidence.
        # Do not interpret an unfinished reference body's math as a new risk.
        return [], 0, "reference_protocol_pending"
    # Reuse the existing code/URL/TikZ/TeX-literal and reference protection.
    # Space masking preserves original character offsets for the final field.
    literals = [*_reference_literal_ranges(content), *(match.span() for match in _REFERENCE.finditer(content))]
    masked = list(content)
    for start, end in literals:
        masked[start:end] = " " * (end - start)
    masked_text = "".join(masked)
    spans = _formulas(masked_text)
    if len(spans) > MAX_FORMULAS:
        return [], 0, "formula_count_limit"
    visible = [span for span in spans
               if content[span.start:span.end] == span.formula
               and not any(start < span.end and end > span.start for start, end in literals)]
    prose = list(masked_text)
    for span in spans:
        prose[span.start:span.end] = " " * (span.end - span.start)
    prose = "".join(prose)
    has_arc_context = "圆弧" in prose
    has_geometry = bool(_GEOMETRY.search(prose))
    upper_points = set()
    for span in visible:
        body = _bare_body(span.formula)
        if body is not None and (_SINGLE_POINT.fullmatch(body) or _TWO_POINTS.fullmatch(body)):
            upper_points.update(atom.group()[0] for atom in re.finditer(_POINT, body))

    risks = []
    for span in visible:
        body = _bare_body(span.formula)
        before_start = max(0, span.start - MAX_CONTEXT_CHARS)
        before = content[before_start:span.start]
        direct_after = content[span.end:span.end + 200]
        after = prose[span.end:span.end + 200]
        kind = None
        if body is not None and _TWO_POINTS.fullmatch(body):
            definition = bool(re.match(r"\s*是", direct_after) and re.match(r"\s*是[^。；;]{0,180}圆弧", after))
            requested = has_arc_context and bool(re.match(r"\s*(?:的长度|长为|的?弧长)", direct_after))
            segment = re.search(r"(?:线段|弦|直径|半径)\s*$", before)
            explicit_segment = bool(segment and not any(
                start < span.start and end > before_start + segment.start() for start, end in literals))
            if (definition or requested) and not explicit_segment:
                kind = "arc_mark"
        elif body is not None and re.fullmatch(r"[a-z]", body) and has_geometry and body.upper() in upper_points:
            point = re.search(r"点\s*$", before)
            if point and not any(start < span.start and end > before_start + point.start() for start, end in literals):
                kind = "point_case"
        if kind:
            risks.append({
                "field": "content", "span": [span.start, span.end], "exactformula": span.formula,
                "context": {"before": content[max(0, span.start - MAX_CONTEXT_CHARS):span.start],
                            "after": content[span.end:span.end + MAX_CONTEXT_CHARS]},
                "kind": kind,
            })
    return risks[:MAX_RISKS], max(0, len(risks) - MAX_RISKS), None


def _unique_source_match(diagnostics: dict, index: int) -> dict | None:
    matches = diagnostics.get("source_matches")
    if not isinstance(matches, list):
        return None
    related = [item for item in matches if isinstance(item, dict)
               and type(item.get("question_index")) is int and item["question_index"] == index
               and item.get("field") == "content"]
    if len(related) != 1:
        return None
    item = related[0]
    start, end, excerpt, number = (item.get(key) for key in
                                   ("source_start", "source_end", "source_excerpt", "source_number"))
    if (type(start) is not int or type(end) is not int or start < 0 or end <= start
            or not isinstance(excerpt, str) or not excerpt.strip() or end - start != len(excerpt)
            or type(number) is not int or number <= 0
            or sum(isinstance(other, dict) and other.get("source_start") == start
                   and other.get("source_end") == end for other in matches) != 1):
        return None
    return {key: item[key] for key in
            ("question_index", "field", "source_start", "source_end", "source_excerpt", "source_number")}


def _source_match_hash(diagnostics: dict, index: int) -> str | None:
    item = _unique_source_match(diagnostics, index)
    return _hash(json.dumps(item, sort_keys=True, ensure_ascii=False)) if item is not None else None


def annotate_pdf_symbol_risks(questions: list[dict], diagnostics: dict) -> dict:
    """Annotate current content only; preserve text, images and source matching.

    A matching excerpt is reused, never manufactured. The existing visual
    verifier still proves cached offsets, page identity and complete evidence.
    """
    report = {"annotated_questions": 0, "risk_count": 0, "omitted": 0, "unavailable": []}
    for index, question in enumerate(questions):
        if not isinstance(question, dict) or not isinstance(question.get("content"), str):
            continue
        content = question["content"]
        risks, omitted, unavailable = _scan(content)
        if unavailable:
            report["unavailable"].append({"question_index": index, "reason": unavailable})
        if not risks:
            continue
        review = question.get("source_review")
        if review is not None and not isinstance(review, dict):
            report["unavailable"].append({"question_index": index, "reason": "invalid_existing_review"})
            continue
        if not isinstance(review, dict):
            review = {}
            question["source_review"] = review
        reasons = review.get("reasons")
        if not isinstance(reasons, list):
            # Malformed existing evidence is not discarded or made eligible.
            if reasons is not None:
                continue
            reasons = []
        if any(not isinstance(reason, str) for reason in reasons):
            continue
        new_reasons = [ARC_MARK_REASON if item["kind"] == "arc_mark" else POINT_CASE_REASON for item in risks]
        review.update(required=True, advisory=True,
                      reasons=list(dict.fromkeys([*reasons, *new_reasons])),
                      symbol_risks={"content_sha256": _hash(content),
                                    "source_match_sha256": _source_match_hash(diagnostics, index),
                                    "items": risks, "omitted": omitted})
        match = _unique_source_match(diagnostics, index)
        if match is not None:
            review.setdefault("source_excerpt", match["source_excerpt"])
        report["annotated_questions"] += 1
        report["risk_count"] += len(risks)
        report["omitted"] += omitted
    diagnostics["pdf_symbol_risks"] = report
    diagnostics["source_review_count"] = sum(isinstance(question, dict)
        and isinstance(question.get("source_review"), dict)
        and question["source_review"].get("required") is True for question in questions)
    return report


def bound_symbol_risk_hints(question: dict, diagnostics: dict, index: int) -> dict | None:
    """Return only fresh locally reproducible hints, never a visual verdict."""
    content = question.get("content")
    review = question.get("source_review")
    if not isinstance(content, str) or not isinstance(review, dict):
        return None
    stored = review.get("symbol_risks")
    risks, omitted, unavailable = _scan(content)
    expected = {"content_sha256": _hash(content),
                "source_match_sha256": _source_match_hash(diagnostics, index),
                "items": risks, "omitted": omitted}
    if unavailable or not risks or stored != expected:
        return None
    required_reasons = {ARC_MARK_REASON if item["kind"] == "arc_mark" else POINT_CASE_REASON for item in risks}
    if set(review.get("reasons", [])) & SYMBOL_RISK_REASONS != required_reasons:
        return None
    return {"advisory_only": True, "field": "content", "field_sha256": stored["content_sha256"],
            "items": deepcopy(risks), "omitted": omitted}


def visible_symbol_risk_hints(output: str, hints: dict) -> dict | None:
    """Keep current exact hints only; edited repair drafts drop old positions."""
    if not isinstance(output, str):
        return None
    risks, omitted, unavailable = _scan(output)
    expected = {"advisory_only": True, "field": "content", "field_sha256": _hash(output),
                "items": risks, "omitted": omitted}
    return deepcopy(expected) if not unavailable and risks and hints == expected else None
