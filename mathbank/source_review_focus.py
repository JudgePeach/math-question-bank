"""Bounded visual-review hints; never an acceptance rule or content repair.

The complete candidate and original page remain the evidence. Formula groups
are deliberately left unpaired when repeated values or missing items prevent
an unambiguous alignment. Every shortened hint is explicitly marked.
"""

from collections import Counter
from difflib import SequenceMatcher

from mathbank.content_locks import _formulas, _math_key


MAX_ITEMS = 8
MAX_FORMULA_CHARS = 240
MAX_CONTEXT_CHARS = 40
MAX_TEXT_CHARS = 160
MAX_FIELD_CHARS = 50000
MAX_FORMULAS = 512
MAX_TEXT_COMPARISON_PRODUCT = 250000
_FORMULA_PLACEHOLDER = "⟦公式⟧"


def _excerpt(value, start, end, limit):
    text = value[start:end]
    return {"text": text[:limit], "truncated": len(text) > limit,
            "chars": len(text), "before": value[max(0, start - MAX_CONTEXT_CHARS):start],
            "after": value[end:end + MAX_CONTEXT_CHARS],
            "before_truncated": start > MAX_CONTEXT_CHARS,
            "after_truncated": end + MAX_CONTEXT_CHARS < len(value)}


def _formula_group(value, formulas, start, end):
    refs = []
    for index in range(start, min(end, start + MAX_ITEMS)):
        span = formulas[index]
        excerpt = _excerpt(value, span.start, span.end, MAX_FORMULA_CHARS)
        refs.append({"index": index + 1, "formula": excerpt.pop("text"), **excerpt})
    return {"formulas": refs, "omitted": max(0, end - start - len(refs))}


def _formula_focus(source, output, expected, actual):
    source_keys = [_math_key(span.formula) for span in expected]
    output_keys = [_math_key(span.formula) for span in actual]
    if source_keys == output_keys:
        return [], False
    source_counts, output_counts = Counter(source_keys), Counter(output_keys)
    unique = {key for key, count in source_counts.items()
              if count == 1 and output_counts[key] == 1}
    # Only unique formulas can anchor a change interval. Repeated values get
    # side-specific tokens so SequenceMatcher cannot choose an arbitrary copy.
    source_tokens = [("formula", key) if key in unique else ("source", index)
                     for index, key in enumerate(source_keys)]
    output_tokens = [("formula", key) if key in unique else ("output", index)
                     for index, key in enumerate(output_keys)]
    matcher = SequenceMatcher(None, source_tokens, output_tokens, autojunk=False)
    repeated = any(source_counts[key] > 1 or output_counts[key] > 1
                   for key in set(source_keys) | set(output_keys))
    source_order = [key for key in source_keys if key in unique]
    output_order = [key for key in output_keys if key in unique]
    ambiguous = repeated or source_order != output_order
    items = []
    for opcode, left, right, out_left, out_right in matcher.get_opcodes():
        if opcode == "equal":
            continue
        # A group of unchanged repeated values between unique anchors adds no
        # formula hint. Its surrounding prose is still compared independently.
        if source_keys[left:right] == output_keys[out_left:out_right]:
            continue
        is_single = right - left == out_right - out_left == 1 and not ambiguous
        items.append({"kind": "formula_change" if is_single else "formula_group",
                      "alignment_ambiguous": ambiguous,
                      "source": _formula_group(source, expected, left, right),
                      "output": _formula_group(output, actual, out_left, out_right)})
    return items, ambiguous


def _prose_view(value, formulas):
    chunks, cursor = [], 0
    for span in formulas:
        chunks.extend((value[cursor:span.start], _FORMULA_PLACEHOLDER))
        cursor = span.end
    chunks.append(value[cursor:])
    return "".join(chunks)


def _text_focus(source, output):
    if source == output:
        return []
    # Strip only identical outer context to keep a tiny change in a long
    # question cheap. No numbers, signs, negations or punctuation are normalized.
    left = 0
    while left < min(len(source), len(output)) and source[left] == output[left]:
        left += 1
    tail = 0
    while (tail < min(len(source), len(output)) - left
           and source[len(source) - tail - 1] == output[len(output) - tail - 1]):
        tail += 1
    source_end, output_end = len(source) - tail, len(output) - tail
    source_middle, output_middle = source[left:source_end], output[left:output_end]
    coarse = len(source_middle) * len(output_middle) > MAX_TEXT_COMPARISON_PRODUCT
    if coarse:
        changes = [("replace", 0, len(source_middle), 0, len(output_middle))]
    else:
        changes = SequenceMatcher(None, source_middle, output_middle, autojunk=False).get_opcodes()
    return [{"kind": "text_change", "coarse": coarse,
             "source": _excerpt(source, left + start, left + end, MAX_TEXT_CHARS),
             "output": _excerpt(output, left + out_start, left + out_end, MAX_TEXT_CHARS)}
            for kind, start, end, out_start, out_end in changes if kind != "equal"]


def build_review_focus(original: dict[str, str], output: dict[str, str]) -> dict:
    """Return short source/candidate differences without deciding equivalence.

    Formula spelling uses the shared conservative token comparison, including
    known literal relation aliases and optional single-character script braces.
    Formula groups are never zipped by count. Text contexts contain a visible
    formula placeholder; complete text and page images must accompany the hints.
    """
    result = {"advisory_only": True, "fields": {}}
    for field in sorted(set(original) | set(output)):
        source, candidate = original.get(field, ""), output.get(field, "")
        if not isinstance(source, str) or not isinstance(candidate, str):
            result["fields"][field] = {"unavailable": "non_text_field"}
            continue
        if max(len(source), len(candidate)) > MAX_FIELD_CHARS:
            result["fields"][field] = {"unavailable": "field_size_limit",
                                        "source_chars": len(source), "output_chars": len(candidate)}
            continue
        expected, actual = _formulas(source), _formulas(candidate)
        summary = {"source_formula_count": len(expected), "output_formula_count": len(actual)}
        if max(len(expected), len(actual)) > MAX_FORMULAS:
            result["fields"][field] = {**summary, "unavailable": "formula_count_limit"}
            continue
        formula_items, ambiguous = _formula_focus(source, candidate, expected, actual)
        text_items = _text_focus(_prose_view(source, expected), _prose_view(candidate, actual))
        items = [*formula_items, *text_items]
        result["fields"][field] = {**summary, "alignment_ambiguous": ambiguous,
                                    "items": items[:MAX_ITEMS], "omitted": max(0, len(items) - MAX_ITEMS)}
    return result
