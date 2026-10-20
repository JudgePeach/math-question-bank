"""Recover missing optional numbers from existing unique content-source matches.

The original matcher runs on a disposable copy. Only a missing number may be
added to the returned original rows; formulas, answers, reviews and all unknown
fields remain unchanged. This is not original-PDF visual verification.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import re

from mathbank.content_locks import lock_visible_math, reconcile_visible_math
from mathbank.pdf_full_context_split import full_context_pdf_split_eligibility


def resolve_pdf_scope_source_numbers(rows, source, source_namespace, requested_source_numbers, *, diagnostics=None):
    """Return copied rows with only uniquely proved missing numbers filled.

    A present integer keeps the existing parser's semantics even when source
    matching is uncertain. A reliable contradictory match is rejected. Missing
    numbers require a unique content match inside the independently counted
    stem section; array order is never identity evidence.
    """
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or not isinstance(requested_source_numbers, (list, tuple)) or not requested_source_numbers
            or any(type(number) is not int or number < 1 for number in requested_source_numbers)
            or len(set(requested_source_numbers)) != len(requested_source_numbers)
            or not isinstance(source_namespace, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source_namespace)):
        raise ValueError("拆题原题号核对输入无效。")
    expected = sorted(requested_source_numbers)
    plan = full_context_pdf_split_eligibility(source)
    if (plan is None or not set(expected).issubset(plan["source_numbers"]) or len(rows) != len(expected)
            or any(not isinstance(row.get("content"), str) or not row["content"].strip() for row in rows)):
        raise ValueError("拆题原文没有可确认的完整题号范围。")
    for row in rows:
        value = row.get("source_number")
        if value is not None and (type(value) is not int or value not in expected):
            raise ValueError("已有原题号不是本范围的有效整数，未自动改写。")
    _, locks = lock_visible_math(source, source_namespace)
    disposable = deepcopy(rows)
    report = reconcile_visible_math(disposable, locks, source)
    candidates = defaultdict(list)
    left, right = plan["stem_range"]
    for match in report.get("source_matches", []):
        index = match.get("question_index")
        start, end, number = match.get("source_start"), match.get("source_end"), match.get("source_number")
        if (match.get("field") == "content" and type(index) is int and 0 <= index < len(rows)
                and type(start) is int and type(end) is int and left <= start < end <= right
                and type(number) is int and number in plan["source_numbers"]):
            candidates[index].append((number, start, end))
    result = deepcopy(rows)
    inferred, proofs, original_claims = [], [], []
    used_ranges = set()
    for index, row in enumerate(result):
        existing = row.get("source_number")
        matches = candidates.get(index, [])
        proof = matches[0] if len(matches) == 1 else None
        if proof is not None:
            number, start, end = proof
            if existing is not None and existing != number:
                raise ValueError("已有原题号与唯一原题文来源冲突，未自动改写。")
            if number not in expected or (start, end) in used_ranges:
                raise ValueError("原题文来源重复或超出指定题号范围。")
            used_ranges.add((start, end))
            proofs.append({"question_index": index, "source_number": number, "source_start": start, "source_end": end})
        if existing is None:
            if proof is None:
                raise ValueError("缺失原题号的题目没有唯一原题文来源，不能按数组位置补号。")
            row["source_number"] = proof[0]
            inferred.append(proof[0])
        elif proof is None:
            original_claims.append(existing)
    supplied = [row["source_number"] for row in result]
    if sorted(supplied) != expected or any(count != 1 for count in Counter(supplied).values()):
        raise ValueError("原题号仍有重复、遗漏或超出指定范围，未采用不完整结果。")
    if diagnostics is not None:
        diagnostics["pdf_source_number_resolution"] = {
            "method": "original_content_source_matches", "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "requested_source_numbers": expected, "inferred_count": len(inferred), "inferred_source_numbers": inferred,
            "proven_matches": proofs, "retained_unproved_model_numbers": original_claims,
            "source_verified": False, "original_verified": False,
        }
    return result
