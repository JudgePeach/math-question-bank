"""Conservative repairs for AI-generated exam-zh fill-in markup."""

from __future__ import annotations

import re
from bisect import bisect_right

from mathbank.math_markdown import _is_escaped
from mathbank.question_assets import _mask_literals, markdown_literal_ranges


_COMMAND = re.compile(r"\\(fillin|texttt|includegraphics)(?![A-Za-z])\*?")


def _group_end(value: str, start: int) -> int | None:
    """Consume a whole TeX argument, including nested braces in options."""
    stack = ["]" if value[start] == "[" else "}"]
    for index in range(start + 1, len(value)):
        if _is_escaped(value, index):
            continue
        char = value[index]
        if char == "{":
            stack.append("}")
        elif char == "[" and stack[-1] == "]":
            stack.append("]")
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
        elif char == "}" or (char == "]" and stack[-1] == "]"):
            return None
    return None


def normalize_fillin_math_wrappers(value: str) -> str:
    r"""Unwrap only a complete math span consisting of fill-in macros.

    Regex locates commands; balanced arguments and complete math boundaries
    prevent a broad substitution from consuming formulas, answers or source
    examples. Mixed math such as $x=\fillin$ is deliberately preserved.
    """
    if not isinstance(value, str) or not value or r"\fillin" not in value:
        return value or ""

    ranges = markdown_literal_ranges(value)
    masked = _mask_literals(value, tex_comments=False)
    # Reuse the existing literal scanner without interpreting prose '%' as
    # TeX comments. Preserve HTML attributes and read-only formula-lock nodes.
    ranges.extend(match.span() for match in re.finditer(
        r"<mathbank-math\b[^>]*>[\s\S]*?(?:</mathbank-math\s*>|\Z)|"
        r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*|/?)>", value, re.IGNORECASE,
    ))
    start = None
    for index, (original, replacement) in enumerate(zip(value, masked)):
        if original != replacement and start is None:
            start = index
        elif original == replacement and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(value)))
    merged = []
    for left, right in sorted(ranges):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    ranges = merged
    starts = [left for left, _ in ranges]

    def covered(position: int) -> bool:
        index = bisect_right(starts, position) - 1
        return index >= 0 and position < merged[index][1]

    # Protect whole argument lists before scanning dollars: an answer such as
    # \fillin[width=2cm][$x^2$] must not supply the outer closing delimiter.
    macros = []
    cursor = 0
    for match in _COMMAND.finditer(value):
        if match.start() < cursor or _is_escaped(value, match.start()) or covered(match.start()):
            continue
        end = match.end()
        argument = end
        count, valid = 0, True
        while True:
            while argument < len(value) and value[argument].isspace():
                argument += 1
            if value[argument:argument + 1] not in ("[", "{"):
                break
            # Native exam-zh uses s O{} o: at most two optional arguments,
            # never a mandatory {...} group. Moving a following math group
            # (e.g. {x_1}) into prose would invalidate otherwise legal TeX.
            if match.group(1) == "fillin" and (value[argument] != "[" or count == 2):
                break
            stop = _group_end(value, argument)
            if stop is None:
                # A malformed command makes the remaining boundaries unsafe.
                ranges.append((match.start(), len(value)))
                end = len(value)
                valid = False
                break
            end = argument = stop
            count += 1
        cursor = end
        if match.group(1) == "fillin":
            if valid:
                macros.append((match.start(), end))
        else:
            ranges.append((match.start(), end))

    spans = [(start, end, False) for start, end in ranges]
    spans.extend((start, end, True) for start, end in macros)
    prefix = "MATHBANKFILLINWRAP"
    while prefix in value:
        prefix += "X"
    pieces, originals, cursor = [], {}, 0
    for start, end, is_macro in sorted(spans, key=lambda row: (row[0], -row[1])):
        if start < cursor:
            continue
        token = f"{prefix}{'F' if is_macro else 'L'}{len(originals)}END"
        pieces.extend((value[cursor:start], token))
        originals[token] = value[start:end]
        cursor = end
    pieces.append(value[cursor:])
    protected = "".join(pieces)
    only_fillins = re.compile(r"\s*(?:" + re.escape(prefix) + r"F[0-9]+END\s*)+")
    token_pattern = re.compile(re.escape(prefix) + r"[FL][0-9]+END")

    whitespace = re.compile(r"\s*")

    def next_source_char(position: int) -> str:
        token = token_pattern.match(protected, position)
        return originals[token.group()][:1] if token else protected[position:position + 1]

    def unwrap(source: str, finish: int) -> str:
        size = 2 if source.startswith(("$$", r"\(", r"\[")) else 1
        inner = source[size:-size]
        if not only_fillins.fullmatch(inner):
            return source
        # Removing '$' must not create \fillincm or turn a visible [2] / '*'
        # into a new optional macro argument. Keep ambiguous source intact.
        following = next_source_char(whitespace.match(protected, finish).end())
        if following in ("[", "*", "%"):
            return source
        if not inner[-1:].isspace() and next_source_char(finish).isalpha():
            last = token_pattern.findall(inner)[-1]
            if re.fullmatch(r"\\fillin\*?", originals[last]):
                return source
        return inner

    # Scan whole delimiters before replacing. A single '$' cannot close on
    # half of '$$'; incomplete/mismatched shells remain unchanged.
    pieces, cursor, copied = [], 0, 0
    openings = re.compile(r"\$+|\\[([]")
    while match := openings.search(protected, cursor):
        cursor = match.end()
        if _is_escaped(protected, match.start()):
            continue
        opening = match.group()
        if opening.startswith("$") and len(opening) > 2:
            break
        closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
        endings = re.compile(r"\$+" if opening.startswith("$") else re.escape(closing))
        end_match = next((end for end in endings.finditer(protected, cursor)
                          if not _is_escaped(protected, end.start())), None)
        if end_match is None or end_match.group() != closing:
            break
        end = end_match.end()
        pieces.extend((protected[copied:match.start()], unwrap(protected[match.start():end], end)))
        copied = cursor = end
    pieces.append(protected[copied:])
    result = "".join(pieces)
    return token_pattern.sub(lambda match: originals[match.group()], result)
