"""Conservative local quality checks for one returned PDF page transcript.

These detect demonstrably lost/invalid choices, not OCR mathematical accuracy.
A lone page-edge continuation remains a human-review warning. No request,
cache mutation, native-source attestation or whole-document fallback occurs.
"""
from __future__ import annotations

import re

from .content_locks import _formulas
from .question_assets import markdown_literal_ranges

_TOKEN = re.compile(r"\\(begin|end)\s*\{([^{}\n]+)\}|\\item\b(?:\[[^\]\n]*\])?")
_TABLES = frozenset({"tabular", "tabular*", "tabularx", "longtable", "tblr", "longtblr", "talltblr", "array"})
_CHOICES = frozenset({"choices", "choices*"})


def _escaped(text, position):
    cursor = position - 1
    while cursor >= 0 and text[cursor] == "\\":
        cursor -= 1
    return (position - cursor - 1) % 2 == 1


def _empty_option(text):
    # Only layout-only atoms are removed in this comparison copy. Unknown TeX,
    # a code example, an image/slot, zero, punctuation and real formulas count
    # as visible content. A placeholder is not guessed into a missing option.
    value = re.sub(r"\\(?:quad|qquad|hfill|enspace|thinspace|medspace|thickspace|space)\b|\\[ ,;:!]", "", text)
    value = re.sub(r"\\(?:text|mathrm|mbox)\{\s*\}", "", value)
    value = re.sub(r"\\[()\[\]]", "", value)
    return not value.strip(" \t\r\n${}")


def _has_direct_choices(text):
    """Real choices accidentally wrapped in math are still structural input.

    A command shown inside a text/braced macro remains a protected example.
    Only top-level begin/end commands revoke the outer math-span protection.
    """
    depth, cursor = 0, 0
    for token in _TOKEN.finditer(text):
        for index in range(cursor, token.start()):
            if not _escaped(text, index):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth = max(0, depth - 1)
        if token[1] in {"begin", "end"} and token[2].strip() in _CHOICES and depth == 0 and not _escaped(text, token.start()):
            return True
        cursor = token.end()
    return False


def assess_pdf_transcript_quality(markdown):
    """Return content-free failure codes, exact bad ranges and review warnings."""
    result = {"retryable_failure": False, "failure_codes": [], "blocked_ranges": [],
              "warnings": [], "choice_count": 0, "source_verified": False}
    if not isinstance(markdown, str) or not markdown.strip():
        result.update(retryable_failure=True, failure_codes=["empty_transcript"])
        return result
    literal = markdown_literal_ranges(markdown)
    # Scan math with literal bodies blanked, so a dollar inside code cannot
    # swallow the next real formula or hide structural choice tokens.
    masked = list(markdown)
    for left, right in literal:
        masked[left:right] = [ch if ch in "\r\n" else " " for ch in markdown[left:right]]
    protected = [*literal, *[(f.start, f.end) for f in _formulas("".join(masked))
                            if not _has_direct_choices(markdown[f.start:f.end])]]
    environment, frames, unclosed = [], [], []

    def fail(code, start, end):
        if code not in result["failure_codes"]:
            result["failure_codes"].append(code)
        record = {"start": start, "end": end, "code": code}
        if record not in result["blocked_ranges"]:
            result["blocked_ranges"].append(record)

    def finish(frame, end, closed):
        items = frame["items"]
        if not items:
            if closed and _empty_option(markdown[frame["body_start"]:end]):
                fail("choices_without_options", frame["start"], end)
            return
        # In an open page-tail environment, the last option may continue on
        # the next physical page. Earlier options already have exact ends.
        count = len(items) if closed else max(0, len(items) - 1)
        for index in range(count):
            right = items[index + 1][0] if index + 1 < len(items) else end
            if _empty_option(markdown[items[index][1]:right]):
                fail("empty_choice_option", frame["start"], end)

    for token in _TOKEN.finditer(markdown):
        if _escaped(markdown, token.start()) or any(a <= token.start() < b for a, b in protected):
            continue
        action, name = token[1], token[2]
        if action == "begin":
            name = name.strip()
            in_table = any(value in _TABLES for value in environment)
            if name in _CHOICES and not in_table:
                result["choice_count"] += 1
                if frames:
                    fail("nested_or_repeated_unclosed_choices", frames[-1]["start"], token.start())
                environment.append(name)
                frames.append({"start": token.start(), "body_start": token.end(),
                               "depth": len(environment), "items": []})
            else:
                environment.append(name)
        elif action == "end":
            name = name.strip()
            if name in _CHOICES and frames and environment[-1:] == [name] and len(environment) == frames[-1]["depth"]:
                frame = frames.pop()
                finish(frame, token.start(), True)
            elif name in _CHOICES and not any(value in _TABLES for value in environment):
                result["warnings"].append("choices_may_start_on_previous_page")
            if environment[-1:] == [name]:
                environment.pop()
        elif frames and len(environment) == frames[-1]["depth"]:
            frames[-1]["items"].append((token.start(), token.end()))
    unclosed.extend(frames)
    for frame in unclosed:
        finish(frame, len(markdown), False)
    if len(unclosed) > 1:
        fail("multiple_unclosed_choices", unclosed[0]["start"], len(markdown))
    elif unclosed:
        result["warnings"].append("choices_may_continue_on_next_page")
        # A new numbered question after an unclosed choices environment is
        # not a single page-edge continuation; it proves a missing boundary.
        tail = markdown[unclosed[0]["body_start"]:]
        for heading in re.finditer(r"(?m)^[ \t]*(?:第[ \t]*)?\d{1,3}(?:[.．、](?!\d)|[ \t]*题)", tail):
            position = unclosed[0]["body_start"] + heading.start()
            if not any(a <= position < b for a, b in protected):
                fail("unclosed_choices_before_next_question", unclosed[0]["start"], len(markdown))
                break
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["retryable_failure"] = bool(result["failure_codes"])
    return result


def transcript_quality_reasons(markdown):
    """Only definite damage codes; warnings never create an automatic retry."""
    return assess_pdf_transcript_quality(markdown)["failure_codes"]
