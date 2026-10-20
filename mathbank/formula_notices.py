"""Move import diagnostics out of question text without changing mathematics."""
from __future__ import annotations

import hashlib
import re

from mathbank.question_assets import _mask_literals, markdown_literal_ranges
from mathbank.math_markdown import _is_escaped
from mathbank.content_locks import _formulas


_NOTICE = re.compile(r"\[(?:公式(?:结构)?|特殊字符)待(?:核对|复核)(?:[：:][^\]\n]{0,200})?\]")
_WRAPPER = re.compile(r"\\(?:text|mathrm|mbox)\s*\{(\[(?:公式(?:结构)?|特殊字符)待(?:核对|复核)(?:[：:][^\]\n]{0,200})?\])\}")


def separate_formula_notices(questions: list[dict]) -> None:
    """Operate only on new import copies; original source evidence stays exact."""
    for question in questions:
        notices = []
        for field in ("content", "answer_markdown"):
            original = question.get(field)
            if not isinstance(original, str):
                continue
            mask = list(_mask_literals(original))
            for first, last in markdown_literal_ranges(original):
                mask[first:last] = " " * (last - first)
            visible = "".join(mask)
            edits = []
            wrappers = {item.start(1): item for item in _WRAPPER.finditer(visible)
                        if not _is_escaped(visible, item.start())}
            source_sha256 = hashlib.sha256(original.encode()).hexdigest()
            for match in _NOTICE.finditer(visible):
                first, last = match.span()
                wrapper = wrappers.get(first)
                if wrapper:
                    first, last = wrapper.span()
                edits.append((first, last))
                notices.append({"field": field, "message": match.group(),
                                "source_range": list(match.span()),
                                "source_sha256": source_sha256,
                                "structure_only": match.group() == "[公式结构待核对]"})
            if edits:
                # Delimiters must belong to one complete parsed math span.
                # Adjacent $x$[notice]$y$ must never become the product $xy$.
                for span in _formulas(visible):
                    for left, right in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
                        if not (span.formula.startswith(left) and span.formula.endswith(right)):
                            continue
                        start, end = span.start + len(left), span.end - len(right)
                        contained = [(first, last) for first, last in edits if start <= first <= last <= end]
                        residue = list(visible[start:end])
                        for first, last in contained:
                            residue[first-start:last-start] = " " * (last - first)
                        if contained and not "".join(residue).strip():
                            edits = [(first, last) for first, last in edits if not (start <= first <= last <= end)]
                            edits.append((span.start, span.end))
                        break
                text = original
                merged = []
                for first, last in sorted(edits):
                    if merged and first <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
                    else:
                        merged.append((first, last))
                for first, last in reversed(merged):
                    # Removing a diagnostic must not turn \alpha + x tokens
                    # into the different control word \alphax.
                    separator = (" " if re.search(r"\\[A-Za-z]+$", original[:first])
                                 and re.match(r"[A-Za-z]", original[last:]) else "")
                    text = text[:first] + separator + text[last:]
                question[field] = text
        if notices:
            review = question.setdefault("source_review", {})
            review["formula_notices"] = notices
            # A familiar but unsupported layout alone is not evidence of an
            # incorrect formula. Keep its record folded rather than shouting.
            review["formula_notice_required"] = (not (review.get("verified_by") == "vision"
                and review.get("required") is False)
                and any(not item["structure_only"] for item in notices))
            if review["formula_notice_required"]:
                review["required"] = True
                reason = "原文中有无法确认的公式或字符，请对照原页查看。"
                if reason not in review.setdefault("reasons", []):
                    review["reasons"].append(reason)
