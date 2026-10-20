"""Locate proven LaTeX prose lists for editable Word export.

The parser returns offsets and untouched item bodies. Unsupported or damaged
structures stay ordinary source; callers may recursively parse nested bodies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from mathbank.question_assets import _escaped, _mask_literals, markdown_literal_ranges


@dataclass(frozen=True)
class LatexListItem:
    content: str
    label: str | None = None


@dataclass(frozen=True)
class LatexList:
    start: int
    end: int
    environment: str
    items: tuple[LatexListItem, ...]


_LIST_ENVIRONMENTS = frozenset(("enumerate", "itemize"))
_STRUCTURE = re.compile(
    r"\\(?P<edge>begin|end)\s*\{(?P<environment>[^{}\r\n]+)\}"
    r"|\\(?P<item>item)(?![A-Za-z])|(?P<brace>[{}])"
)
_MATH_BOUNDARY = re.compile(r"\$+|\\[()[\]]")
_HTML = re.compile(
    r"<(?:script|style|textarea)\b(?:\"[^\"]*\"|'[^']*'|[^'\">])*?>"
    r"[\s\S]*?(?:</(?:script|style|textarea)\s*>|\Z)"
    r"|<mathbank-math\b[^>]*>[\s\S]*?(?:</mathbank-math\s*>|\Z)"
    r"|</?[A-Za-z][A-Za-z0-9:_-]*(?:\"[^\"]*\"|'[^']*'|[^'\">])*?>",
    re.IGNORECASE,
)


def _mask_ranges(source: str, ranges: list[tuple[int, int]]) -> str:
    chars = list(source)
    for start, end in ranges:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _literal_mask(source: str) -> str:
    # Reuse the shared code/URL/TikZ scanners, preserving every source offset.
    scan = _mask_literals(source)
    ranges = markdown_literal_ranges(source)
    ranges.extend(match.span() for match in _HTML.finditer(source))
    scan = _mask_ranges(scan, ranges)

    # texttt is a source example rather than a list-bearing prose argument.
    cursor = 0
    while match := re.search(r"\\texttt(?![A-Za-z])\s*\{", scan[cursor:]):
        start = cursor + match.start()
        opening = cursor + match.end() - 1
        cursor = opening + 1
        if _escaped(source, start):
            continue
        depth = 1
        while cursor < len(scan) and depth:
            if not _escaped(source, cursor):
                depth += (scan[cursor] == "{") - (scan[cursor] == "}")
            cursor += 1
        scan = _mask_ranges(scan, [(start, cursor)])

    # All mathematical shells are opaque, including malformed shells whose
    # end cannot be established. Literal dollars were already hidden above.
    cursor = 0
    while match := _MATH_BOUNDARY.search(scan, cursor):
        cursor = match.end()
        if _escaped(source, match.start()):
            continue
        opening = match.group()
        if opening not in ("$", "$$", r"\(", r"\["):
            scan = _mask_ranges(scan, [(match.start(), len(scan))])
            break
        closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
        finish = len(scan)
        for boundary in _MATH_BOUNDARY.finditer(scan, cursor):
            if _escaped(source, boundary.start()):
                continue
            if boundary.group() == closing:
                finish = boundary.end()
            # Mismatched shells cannot prove where prose starts again.
            break
        scan = _mask_ranges(scan, [(match.start(), finish)])
        cursor = finish
    return scan


def _optional_label(source: str, scan: str, start: int) -> tuple[str | None, int] | None:
    cursor = start
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    if source[cursor:cursor + 1] != "[":
        return None, start
    opening = cursor
    stack = ["]"]
    cursor += 1
    while cursor < len(scan):
        char = scan[cursor]
        if _escaped(source, cursor):
            cursor += 1
            continue
        if char == "{":
            stack.append("}")
        elif char == "[" and stack[-1] == "]":
            stack.append("]")
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return source[opening + 1:cursor], cursor + 1
        elif char == "}":
            return None
        cursor += 1
    return None


@dataclass
class _Frame:
    environment: str
    body_start: int
    brace_depth: int
    capture: bool = False
    items: list[LatexListItem] = field(default_factory=list)
    item_start: int | None = None
    item_label: str | None = None


def _empty_introduction(source: str, start: int, end: int) -> bool:
    """Permit only whitespace and actual comments before the first item."""
    cursor = start
    while cursor < end:
        if source[cursor].isspace():
            cursor += 1
        elif source[cursor] == "%" and not _escaped(source, cursor):
            newline = source.find("\n", cursor, end)
            cursor = newline + 1 if newline >= 0 else end
        elif source.startswith("<!--", cursor):
            closing = source.find("-->", cursor + 4, end)
            if closing < 0:
                return False
            cursor = closing + 3
        else:
            return False
    return True


def _consume_environment(
    source: str, scan: str, opening: re.Match, *, capture: bool,
) -> tuple[int, LatexList | None, bool]:
    environment = opening.group("environment")
    frames = [_Frame(environment, opening.end(), 0, capture)]
    cursor = opening.end()
    brace_depth = 0
    valid = True

    def finish_item(frame: _Frame, stop: int) -> None:
        nonlocal valid
        if frame.item_start is not None:
            content = source[frame.item_start:stop]
            if not content.strip():
                valid = False
            frame.items.append(LatexListItem(content, frame.item_label))

    while token := _STRUCTURE.search(scan, cursor):
        cursor = token.end()
        if _escaped(source, token.start()):
            continue
        edge = token.group("edge")
        name = token.group("environment")
        frame = frames[-1]
        if edge == "begin":
            nested_capture = (
                frame.capture and name in _LIST_ENVIRONMENTS
                and brace_depth == frame.brace_depth
            )
            frames.append(_Frame(name, token.end(), brace_depth, nested_capture))
        elif edge == "end":
            if name != frame.environment or brace_depth != frame.brace_depth:
                return len(source), None, False
            if frame.capture:
                finish_item(frame, token.start())
                if not frame.items:
                    valid = False
            frames.pop()
            if not frames:
                result = (
                    LatexList(opening.start(), token.end(), environment, tuple(frame.items))
                    if capture and valid else None
                )
                return token.end(), result, True
        elif token.group("item"):
            if brace_depth != frame.brace_depth:
                continue
            label = _optional_label(source, scan, token.end())
            if label is None:
                return len(source), None, False
            if frame.capture:
                if frame.item_start is None:
                    if not _empty_introduction(source, frame.body_start, token.start()):
                        valid = False
                else:
                    finish_item(frame, token.start())
                frame.item_label, frame.item_start = label
            cursor = label[1]
        elif token.group("brace") == "{":
            brace_depth += 1
        else:
            if brace_depth == 0:
                return len(source), None, False
            brace_depth -= 1
    return len(source), None, False


def find_latex_lists(source: str) -> list[LatexList]:
    """Return only complete outermost prose enumerate/itemize structures.

    List options, empty items, nonempty introductions and damaged environment
    boundaries stay raw. Invalid outer structures never expose inner lists as
    independently usable fragments. Ordinary environments and argument groups
    are opaque to the outer scan.
    """
    if not isinstance(source, str) or not source or r"\begin" not in source:
        return []
    scan = _literal_mask(source)
    lists: list[LatexList] = []
    cursor = 0
    brace_depth = 0
    while token := _STRUCTURE.search(scan, cursor):
        cursor = token.end()
        if _escaped(source, token.start()):
            continue
        if token.group("edge") == "begin":
            cursor, parsed, complete = _consume_environment(
                source, scan, token,
                capture=brace_depth == 0 and token.group("environment") in _LIST_ENVIRONMENTS,
            )
            if parsed is not None:
                lists.append(parsed)
            if not complete:
                break
        elif token.group("edge") == "end":
            break
        elif token.group("brace") == "{":
            brace_depth += 1
        elif token.group("brace") == "}":
            if brace_depth == 0:
                break
            brace_depth -= 1
    return lists
