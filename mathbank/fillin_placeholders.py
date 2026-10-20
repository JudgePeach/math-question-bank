"""Repair the specific Word blank–triangle–blank placeholder losslessly.

The returned edits use offsets in the original string so an offline repair can
retain exact before/after evidence instead of running a broad normalizer.
"""

from __future__ import annotations

from bisect import bisect_right
import re

from mathbank.content_locks import _MATH_ENVIRONMENTS
from mathbank.math_markdown import _is_escaped
from mathbank.question_assets import _mask_literals, markdown_literal_ranges


_TOKEN = re.compile(
    r"\\(?:(?P<fillin>fillin)(?![A-Za-z])|underline(?![A-Za-z])\s*\{\s*▲\s*\})"
)
_WHITESPACE = re.compile(r"\s*")
_HTML = re.compile(
    r"<(?P<tag>[A-Za-z][A-Za-z0-9:_-]*)\b(?:\"[^\"]*\"|'[^']*'|[^'\">])*?>"
    r"[\s\S]*?</(?P=tag)\s*>|"
    r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\"[^\"]*\"|'[^']*'|[^'\">])*?>",
    re.IGNORECASE,
)
_MATH_ENV_NAMES = "|".join(re.escape(name) for name in (
    *_MATH_ENVIRONMENTS, "alignat", "alignat*", "alignedat*", "flalign", "flalign*",
))
_MATH_TOKEN = re.compile(
    r"\$+|\\[()[\]]|\\(?P<edge>begin|end)\s*\{(?P<env>"
    + _MATH_ENV_NAMES + r")\}"
)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _protected_ranges(value: str) -> list[tuple[int, int]]:
    ranges = markdown_literal_ranges(value)
    ranges.extend(match.span() for match in _HTML.finditer(value))
    ranges.extend(match.span() for match in re.finditer(
        r"<mathbank-math\b[^>]*>[\s\S]*?(?:</mathbank-math\s*>|\Z)", value, re.IGNORECASE,
    ))
    # This scanner already understands verbatim commands, TikZ, TeX comments,
    # URLs and paths. Keep offsets, including line breaks, for the audit edits.
    masked = _mask_literals(value)
    start = None
    for index, (original, replacement) in enumerate(zip(value, masked)):
        if original != replacement and start is None:
            start = index
        elif original == replacement and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(value)))
    ranges = _merge_ranges(ranges)
    chars = list(masked)
    for left, right in ranges:
        chars[left:right] = " " * (right - left)
    scan = "".join(chars)

    # A mismatch leaves the remainder unproven. Never reinterpret a real
    # mathematical triangle, an incomplete formula, or a literal delimiter.
    opening = None
    environment_stack: list[str] = []
    for match in _MATH_TOKEN.finditer(scan):
        if _is_escaped(scan, match.start()):
            continue
        token = match.group()
        if opening is None:
            if token in ("$", "$$", r"\(", r"\[") or match.group("edge") == "begin":
                closing = {r"\(": r"\)", r"\[": r"\]"}.get(token, token)
                opening = (match.start(), closing, match.group("env"))
                environment_stack = [match.group("env")] if match.group("env") else []
            else:
                ranges.append((match.start(), len(value)))
                break
        elif match.group("edge") == "begin":
            # matrix/cases/aligned are legal inside a dollar shell or another
            # math environment; they must close before that shell can close.
            environment_stack.append(match.group("env"))
        elif match.group("edge") == "end":
            if not environment_stack or environment_stack[-1] != match.group("env"):
                ranges.append((opening[0], len(value)))
                opening = None
                break
            environment_stack.pop()
            if opening[2] is not None and not environment_stack:
                ranges.append((opening[0], match.end()))
                opening = None
        elif opening[2] is not None:
            continue  # Whole native math environments are already protected.
        elif token == opening[1] and not environment_stack:
            ranges.append((opening[0], match.end()))
            opening = None
        else:
            ranges.append((opening[0], len(value)))
            opening = None
            break
    if opening is not None:
        ranges.append((opening[0], len(value)))
    return _merge_ranges(ranges)


def fillin_placeholder_edits(value: str) -> list[tuple[int, int, str]]:
    r"""Return ordered, non-overlapping edits for proven prose placeholders.

    Only bare ``\fillin`` macros on both sides of ``\underline{▲}`` qualify.
    Whitespace and additional bare macros are allowed. Parameters, stars,
    mathematical environments, argument groups and literal source stay exact.
    The last macro's suffix is outside the edit: existing following letters,
    CJK text and whitespace therefore cannot acquire a new TeX boundary.
    """
    if not isinstance(value, str) or "▲" not in value or r"\fillin" not in value:
        return []
    protected = _protected_ranges(value)
    starts = [left for left, _ in protected]

    def covered(position: int) -> bool:
        index = bisect_right(starts, position) - 1
        return index >= 0 and position < protected[index][1]

    # Track outer argument groups once; the triangle's own braces are allowed
    # because its command begins outside that group. Both unclosed and
    # mismatched groups are conservative barriers for the remaining source.
    groups: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    for match in re.finditer(r"[{}\[\]]", value):
        position, char = match.start(), match.group()
        if covered(position) or _is_escaped(value, position):
            continue
        if char in "{[":
            stack.append(("}" if char == "{" else "]", position))
        elif not stack or stack[-1][0] != char:
            groups.append((stack[0][1] if stack else position, len(value)))
            stack = []
            break
        else:
            _, left = stack.pop()
            if not stack:
                groups.append((left, position + 1))
    if stack:
        groups.append((stack[0][1], len(value)))
    protected = _merge_ranges([*protected, *groups])
    starts = [left for left, _ in protected]

    edits: list[tuple[int, int, str]] = []
    run: list[tuple[int, int, bool]] = []

    def flush() -> None:
        if (run and run[0][2] and run[-1][2]
                and any(not token[2] for token in run)
                and all(left[2] or right[2] for left, right in zip(run, run[1:]))):
            edits.append((run[0][0], run[-1][1], r"\fillin"))
        run.clear()

    for match in _TOKEN.finditer(value):
        is_fillin = match.group("fillin") is not None
        valid = not covered(match.start()) and not _is_escaped(value, match.start())
        if is_fillin:
            suffix = _WHITESPACE.match(value, match.end()).end()
            # A star or argument belongs to this macro, even across spaces.
            valid = valid and value[suffix:suffix + 1] not in ("*", "[", "{", "]", "}")
        if not valid:
            flush()
            continue
        if run and value[run[-1][1]:match.start()] and not value[run[-1][1]:match.start()].isspace():
            flush()
        run.append((match.start(), match.end(), is_fillin))
    flush()
    return edits


def normalize_fillin_placeholder_clusters(value: str) -> str:
    """Apply exactly the same edits used by an offline repair's audit trail."""
    if not isinstance(value, str) or not value:
        return value or ""
    pieces, cursor = [], 0
    for start, end, replacement in fillin_placeholder_edits(value):
        pieces.extend((value[cursor:start], replacement))
        cursor = end
    pieces.append(value[cursor:])
    return "".join(pieces)
