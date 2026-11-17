"""Find authored math and damaged math boundaries in mixed Word export text."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import re

from mathbank.math_markdown import (
    _INNER_MATH_ENVIRONMENTS,
    _STANDALONE_MATH_ENVIRONMENTS,
    _is_escaped,
    _matching_latex_environment_end,
)
from mathbank.question_assets import _brace_end, _mask_literals, markdown_literal_ranges


STANDALONE_ENVIRONMENTS = _STANDALONE_MATH_ENVIRONMENTS | {
    "eqnarray", "eqnarray*", "flalign", "flalign*",
}
_MATH_ENVIRONMENTS = STANDALONE_ENVIRONMENTS | _INNER_MATH_ENVIRONMENTS
_ENV_EDGE = re.compile(r"\\(begin|end)\s*\{([^{}\r\n]+)\}")
_MATH_EDGE = re.compile(_ENV_EDGE.pattern + r"|\\[()[\]]|\$")
_MATH_CONTENT_EDGE = re.compile(_MATH_EDGE.pattern + r"|\n[ \t\r]*\n")
_MATH_CLOSE = {"$$": "$$", "$": "$", r"\(": r"\)", r"\[": r"\]"}
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_AMOUNT_PROSE = re.compile(
    r"\s*(?:[，。；！？：、\u3400-\u9fff]|[.,;:!?](?:\s+[A-Za-z]|\s*$)"
    r"|(?:and|or|to|each|per|for|costs?|price|total|dollars?|tax)\b)"
    r"|[ \t]*\r?\n[ \t]*[A-Za-z]{2,}\b",
    re.IGNORECASE,
)
_ENGLISH_PROSE = re.compile(r"[ \t]+[A-Za-z]{2,}[.!?,;:][ \t]+[A-Za-z]{2,}\b")
_POSSIBLE_FORMULA_START = re.compile(r"[ \t]*(?:[$\\A-Za-z0-9({+\-])")
_CURRENCY_UNIT = re.compile(r"\s*(?:美元|美金|元|块)\s*")
_MATH_AFTER_DOLLAR = re.compile(
    r"\s*(?:\\[A-Za-z]+|[A-Za-z]\s*(?:$|[。，；])"
    r"|(?:[A-Za-z0-9]+|\d+\.\d+)\s*[_^=<>≤≥+*/-])"
)
_LITERAL_HTML = re.compile(
    r"<(script|style|textarea|mathbank-math)\b[^>]*>[\s\S]*?(?:</\1\s*>|\Z)",
    re.IGNORECASE,
)
_LITERAL_TEXTTT = re.compile(r"\\texttt(?![A-Za-z@])\s*\{")


@dataclass(frozen=True)
class WordMathSpan:
    start: int
    end: int
    latex: str
    display: bool = False
    block: bool = False
    issue: str | None = None


def word_literal_ranges(source: str) -> list[tuple[int, int]]:
    """Locate code, links and literal HTML without masking math comparisons."""
    ranges = markdown_literal_ranges(source) + [m.span() for m in _LITERAL_HTML.finditer(source)]
    for match in _LITERAL_TEXTTT.finditer(source):
        if not _is_escaped(source, match.start()):
            group_end = _brace_end(source, match.end() - 1)
            ranges.append((match.start(), group_end))
    return ranges


def mask_word_math_literals(source: str) -> str:
    chars = list(_mask_literals(source, tex_comments=False))
    for start, end in word_literal_ranges(source):
        chars[start:end] = re.sub(r"[^\r\n]", " ", source[start:end])
    return "".join(chars)


def _is_currency(source: str, start: int, closing: int | None) -> bool:
    amount = _NUMBER.match(source, start + 1)
    if not amount:
        return False
    end = amount.end()
    if closing is not None:
        if not source[end:closing].strip():
            # An immediately closed $5$ is math. "$5 $10" is a list of prices.
            return end < closing and bool(_NUMBER.match(source, closing + 1))
        if _CURRENCY_UNIT.fullmatch(source, end, closing) or not _POSSIBLE_FORMULA_START.match(source, closing + 1):
            return False
    return bool(
        _AMOUNT_PROSE.match(source, end)
        or _ENGLISH_PROSE.match(source, end)
        or (closing is None and not source[end:].strip())
    )


def _delimited_end(scan: str, start: int, opening: str) -> tuple[int | None, int]:
    """Return the closing offset and where a damaged formula can resume."""
    close = _MATH_CLOSE[opening]
    cursor = start + len(opening)
    while edge := _MATH_CONTENT_EDGE.search(scan, cursor):
        position = edge.start()
        cursor = edge.end()
        if _is_escaped(scan, position):
            continue
        if edge.group().startswith("\n"):
            if opening == "$":
                return None, position
            continue
        if edge[1]:
            # A matrix or cases inside a delimiter belongs to the same formula.
            if edge[1] == "begin" and (ending := _matching_latex_environment_end(scan, edge, _ENV_EDGE)):
                cursor = ending.end()
            continue
        if scan.startswith(close, position):
            return position, position + len(close)
        # A new opener can still be scanned after this malformed fragment.
        resume = cursor if edge.group() in (r"\)", r"\]") else position
        return None, resume
    return None, len(scan)


def iter_word_math_spans(source: str, *, masked_source: str | None = None) -> Iterator[WordMathSpan]:
    """Yield complete math or one review marker for a provably damaged boundary."""
    if not _MATH_EDGE.search(source):
        return
    scan = mask_word_math_literals(source) if masked_source is None else masked_source
    cursor = 0
    while edge := _MATH_EDGE.search(scan, cursor):
        start = edge.start()
        cursor = edge.end()
        if _is_escaped(source, start):
            # An escaped opener can introduce a literal example with an
            # unescaped closer. Do not report the example's closer as orphaned.
            if edge[1] == "begin" and edge[2] in _MATH_ENVIRONMENTS:
                if closing := _matching_latex_environment_end(scan, edge, _ENV_EDGE):
                    cursor = closing.end()
            elif edge.group() in (r"\(", r"\["):
                closing, end = _delimited_end(scan, start, edge.group())
                if closing is not None:
                    cursor = end
            continue
        line_end = scan.find("\n", cursor)
        if line_end < 0:
            line_end = len(scan)
        if edge[1]:
            name = edge[2]
            if name not in _MATH_ENVIRONMENTS:
                continue
            if edge[1] == "end":
                yield WordMathSpan(start, cursor, source[start:cursor], issue="缺少对应的公式环境起点")
                continue
            closing = _matching_latex_environment_end(scan, edge, _ENV_EDGE)
            if closing is None:
                cursor = line_end
                yield WordMathSpan(start, cursor, source[start:cursor], issue=f"公式环境 {name} 未正确闭合")
                continue
            cursor = closing.end()
            display = name != "math"
            yield WordMathSpan(start, cursor, source[start:cursor], display, display and name in STANDALONE_ENVIRONMENTS)
            continue

        opening = edge.group()
        if opening in (r"\)", r"\]"):
            yield WordMathSpan(start, cursor, source[start:cursor], issue="缺少对应的公式定界符")
            continue
        if opening == "$" and scan.startswith("$$", start):
            opening = "$$"
            cursor = start + 2
        closing, resume = _delimited_end(scan, start, opening)
        if opening == "$" and _is_currency(source, start, closing):
            continue
        if closing is None:
            if opening == "$" and not _MATH_AFTER_DOLLAR.match(source[start + 1:line_end]):
                continue
            cursor = min(resume, line_end)
            yield WordMathSpan(start, cursor, source[start:cursor], issue=f"公式缺少闭合定界符 {_MATH_CLOSE[opening]}")
            continue
        cursor = resume
        yield WordMathSpan(start, cursor, source[start + len(opening):closing], opening in ("$$", r"\["))
