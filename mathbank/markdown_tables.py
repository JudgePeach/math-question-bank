"""Strict Markdown tables for presentation and export copies.

Only complete pipe-framed tables with a delimiter row and consistent column
counts are recognized. Source offsets and cell strings remain unchanged; this
module never repairs missing rows or guesses cell values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .math_markdown import normalize_question_math_markdown


@dataclass(frozen=True)
class MarkdownTable:
    start: int
    end: int
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    alignments: tuple[str, ...]


_ENV_TOKEN = re.compile(r"\\(begin|end)\{([^{}\n]+)\}")
_DELIMITER = re.compile(r":?-{3,}:?")
_GREEK = dict(zip(
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩ",
    ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
     "omicron pi rho sigma tau upsilon phi chi psi omega Gamma Delta Theta "
     "Lambda Xi Pi Sigma Upsilon Phi Psi Omega").split(),
))


def _escaped(source: str, index: int) -> bool:
    cursor = index - 1
    while cursor >= 0 and source[cursor] == "\\":
        cursor -= 1
    return (index - cursor - 1) % 2 == 1


def _protected_blocks(source: str) -> list[tuple[int, int]]:
    """Locate literal/multiline structures, including unfinished environments."""
    ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(source):
        html = re.match(r"<(pre|code|script|style|textarea|table)\b[^>]*>",
                        source[index:], re.IGNORECASE)
        if html:
            close = re.search(r"</" + html[1] + r"\s*>", source[index + html.end():],
                              re.IGNORECASE)
            end = index + html.end() + close.end() if close else len(source)
            ranges.append((index, end))
            index = end
            continue
        verb = re.match(r"\\(?:verb|Verb|lstinline)\*?(?:\[[^]\n]*\])?([^\w\s{])",
                        source[index:])
        if verb and not _escaped(source, index):
            end = source.find(verb[1], index + verb.end())
            if end >= 0:
                index = end + 1
                continue
        fence = (re.match(r"[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n", source[index:])
                 if index == 0 or source[index - 1] == "\n" else None)
        if fence:
            start = index
            delimiter = fence.group(1)
            close = re.search(r"(?m)^[ \t]{0,3}" + re.escape(delimiter[0])
                              + "{" + str(len(delimiter)) + r",}[ \t]*$",
                              source[index + fence.end():])
            end = index + fence.end() + close.end() if close else len(source)
            ranges.append((start, end))
            index = end
            continue
        token = _ENV_TOKEN.match(source, index)
        if token and token[1] == "begin" and not _escaped(source, index):
            stack = [token[2]]
            end = len(source)
            for nested in _ENV_TOKEN.finditer(source, token.end()):
                if _escaped(source, nested.start()):
                    continue
                if nested[1] == "begin":
                    stack.append(nested[2])
                elif not stack or nested[2] != stack[-1]:
                    break
                else:
                    stack.pop()
                if not stack:
                    end = nested.end()
                    break
            ranges.append((index, end))
            index = end
            continue
        opening = next((value for value in ("$$", "$", r"\[", r"\(")
                        if source.startswith(value, index)), None)
        if opening and not _escaped(source, index):
            closing = {r"\[": r"\]", r"\(": r"\)"}.get(opening, opening)
            end = source.find(closing, index + len(opening))
            while end >= 0 and _escaped(source, end):
                end = source.find(closing, end + len(closing))
            if end >= 0:
                end += len(closing)
                if "\n" in source[index:end]:
                    ranges.append((index, end))
                index = end
                continue
        # Inline code cannot open a TeX environment or multiline formula.
        if source[index] == "`":
            length = len(re.match(r"`+", source[index:])[0])
            end = source.find("`" * length, index + length)
            if end >= 0:
                if "\n" in source[index:end + length]:
                    ranges.append((index, end + length))
                index = end + length
                continue
        index += 1
    return ranges


def split_markdown_row(line: str) -> tuple[str, ...] | None:
    """Split only unescaped pipes outside math, code, links and TeX groups."""
    if line.startswith("\t") or len(line) - len(line.lstrip(" ")) >= 4:
        return None  # Markdown's indented code block, not a table.
    value = line.strip()
    if not value.startswith("|") or not value.endswith("|") or _escaped(value, len(value) - 1):
        return None
    cells: list[str] = []
    start = index = 1
    closing_math: str | None = None
    closing_code: str | None = None
    groups: list[str] = []
    while index < len(value):
        char = value[index]
        if closing_code:
            if value.startswith(closing_code, index):
                index += len(closing_code)
                closing_code = None
            else:
                index += 1
            continue
        if closing_math:
            if value.startswith(closing_math, index) and not _escaped(value, index):
                index += len(closing_math)
                closing_math = None
            else:
                index += 1
            continue
        verb = re.match(r"\\(?:verb|Verb|lstinline)\*?(?:\[[^]\n]*\])?([^\w\s{])",
                        value[index:])
        if verb and not _escaped(value, index):
            end = value.find(verb[1], index + verb.end())
            if end < 0:
                return None
            index = end + 1
            continue
        if char == "`" and not _escaped(value, index):
            closing_code = re.match(r"`+", value[index:])[0]
            index += len(closing_code)
            continue
        opening = next((token for token in ("$$", "$", r"\(", r"\[")
                        if value.startswith(token, index)), None)
        if opening and not _escaped(value, index):
            closing_math = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
            index += len(opening)
            continue
        if char in "{[(" and not _escaped(value, index):
            groups.append({"{": "}", "[": "]", "(": ")"}[char])
        elif char in "}])" and not _escaped(value, index):
            expected = groups.pop() if groups else None
            # Half-open numeric intervals are ordinary mathematical table cells.
            if expected is None or (expected != char and {expected, char} != {"]", ")"}):
                return None
        elif char == "|" and not groups and not _escaped(value, index):
            cells.append(value[start:index].strip())
            start = index + 1
        index += 1
    if closing_math or closing_code or groups or start != len(value):
        return None
    return tuple(cells) if 2 <= len(cells) <= 32 else None


def find_markdown_tables(source: str) -> list[MarkdownTable]:
    """Return complete, unambiguous table blocks in their original order."""
    source = str(source or "")
    lines = source.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    protected = _protected_blocks(source)
    result: list[MarkdownTable] = []
    index = 0
    while index + 2 < len(lines):
        start = offsets[index]
        header = split_markdown_row(lines[index])
        delimiter = split_markdown_row(lines[index + 1]) if header else None
        if (not header or not delimiter or len(header) != len(delimiter)
                or not all(_DELIMITER.fullmatch(cell) for cell in delimiter)
                or any(first <= start < last for first, last in protected)):
            index += 1
            continue
        rows: list[tuple[str, ...]] = []
        cursor = index + 2
        valid = True
        while cursor < len(lines) and lines[cursor].lstrip().startswith("|"):
            row = split_markdown_row(lines[cursor])
            if row is None or len(row) != len(header):
                valid = False
            elif any(_DELIMITER.fullmatch(cell) for cell in row):
                # Adjacent/duplicate headers are ambiguous without separation.
                if all(_DELIMITER.fullmatch(cell) for cell in row):
                    valid = False
            rows.append(row or ())
            cursor += 1
        end = offsets[cursor] if cursor < len(lines) else len(source)
        if (valid and rows and not any(first < end and last > start
                                      for first, last in protected)):
            alignments = tuple("c" if cell.startswith(":") and cell.endswith(":")
                               else "r" if cell.endswith(":") else "l"
                               for cell in delimiter)
            result.append(MarkdownTable(start, end, header, tuple(rows), alignments))
        index = max(index + 1, cursor)
    return result


def normalize_table_cell_math(cell: str) -> str:
    """Add a math shell only to clear standalone math cells in an export copy."""
    value = str(cell or "")
    if re.search(r"\\(?:verb|Verb|lstinline)\b", value):
        return value
    if (not value or re.search(r"[\u3400-\u9fff]", value)
            or any(token in value for token in ("$", r"\(", r"\[", "`", "![", "](", "http", "<"))):
        return normalize_question_math_markdown(value)
    if (re.fullmatch(r"[A-Za-zα-ωΓΔΘΛΞΠΣΥΦΨΩ∞0-9\s{}()\[\].,+*/^_=\\-]+", value)
            and (re.search(r"[α-ωΓΔΘΛΞΠΣΥΦΨΩ_^=\\]", value)
                 or re.fullmatch(r"[A-Za-z]", value)
                 or re.fullmatch(r"[\[(][+\-\d.,\s∞]+[\])]", value)
                 or (re.search(r"[+*/-]", value) and re.search(r"[A-Za-zα-ω]", value)))):
        math = "".join("\\" + _GREEK[char] + " " if char in _GREEK else r"\infty "
                       if char == "∞" else char for char in value)
        return "$" + math.rstrip() + "$"
    return normalize_question_math_markdown(value)


def markdown_table_to_latex(table: MarkdownTable) -> str:
    """Render bounded wrapping columns at the existing body font size."""

    def cell_source(cell: str) -> str:
        saved: dict[str, str] = {}
        prefix = "\ue010"
        while prefix in cell:
            prefix += "\ue010"

        def save(value: str) -> str:
            token = prefix + str(len(saved)) + "\ue011"
            saved[token] = value
            return token

        def literal(content: str) -> str:
            replacements = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
                            "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_",
                            "$": r"\textdollar{}", "^": r"\textasciicircum{}",
                            "~": r"\textasciitilde{}", "|": r"\textbar{}"}
            escaped_content = "".join(replacements.get(char, char) for char in content)
            return save(r"\texttt{" + escaped_content + "}")

        value = re.sub(r"(`+)(.*?)\1", lambda match: literal(match[2]),
                       normalize_table_cell_math(cell))
        value = re.sub(
            r"\\(?:verb|Verb|lstinline)\*?(?:\[[^]\n]*\])?(?P<sep>[^\w\s{])"
            r"(?P<body>.*?)\1",
            lambda match: literal(match["body"]), value)
        # Protect visible images, formula-lock tokens and complete inline TeX
        # before escaping prose. This remains independent of OCR normalization.
        value = re.sub(r"!\[[^\]\n]*\]\([^\n)]*\)|\[\[MBM_[A-Za-z0-9_:-]+\]\]",
                       lambda match: save(match[0]), value)
        output: list[str] = []
        cursor = copied = 0
        while cursor < len(value):
            opening = next((token for token in ("$$", "$", r"\(", r"\[")
                            if value.startswith(token, cursor)), None)
            if opening and not _escaped(value, cursor):
                closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
                end = value.find(closing, cursor + len(opening))
                while end >= 0 and _escaped(value, end):
                    end = value.find(closing, end + len(closing))
                if end >= 0:
                    end += len(closing)
                    output.extend((value[copied:cursor], save(value[cursor:end])))
                    cursor = copied = end
                    continue
            macro = re.match(r"\\[A-Za-z]+\*?", value[cursor:])
            if macro and not _escaped(value, cursor):
                end = cursor + macro.end()
                # Preserve explicitly authored macro groups, including nested
                # text commands. Never convert their pipes into cell boundaries.
                while end < len(value) and value[end] == "{":
                    depth = 1
                    group_end = end + 1
                    while group_end < len(value) and depth:
                        if not _escaped(value, group_end):
                            depth += (value[group_end] == "{") - (value[group_end] == "}")
                        group_end += 1
                    if depth:
                        break
                    end = group_end
                if end > cursor + macro.end():
                    output.extend((value[copied:cursor], save(value[cursor:end])))
                    cursor = copied = end
                    continue
            cursor += 1
        output.append(value[copied:])
        protected = "".join(output)
        protected = re.sub(r"(?<!\\)([&%#_])", r"\\\1", protected)
        protected = protected.replace(r"\|", r"\textbar{}")
        result = protected
        for token, original in reversed(list(saved.items())):
            result = result.replace(token, original)
        return result

    alignment_commands = {
        "l": r"\raggedright\arraybackslash",
        "c": r"\centering\arraybackslash",
        "r": r"\raggedleft\arraybackslash",
    }
    spec = "|" + "|".join(r">{" + alignment_commands[alignment] + "}X"
                           for alignment in table.alignments) + "|"
    rows = (table.header, *table.rows)
    body = "\n".join(" & ".join(cell_source(cell) for cell in row)
                     + r" \\ \hline" for row in rows)
    padding = "2pt" if len(table.header) >= 8 else "4pt"
    return ("\n\\begingroup\n\\setlength{\\tabcolsep}{" + padding + "}\n"
            r"\renewcommand{\arraystretch}{1.2}" + "\n"
            r"\noindent\begin{tabularx}{\linewidth}{" + spec + "}\n"
            r"\hline" + "\n" + body + "\n" + r"\end{tabularx}"
            + "\n\\endgroup\n")


def replace_markdown_tables_for_latex(source: str) -> str:
    output: list[str] = []
    cursor = 0
    for table in find_markdown_tables(source):
        output.extend((source[cursor:table.start], markdown_table_to_latex(table)))
        cursor = table.end
    output.append(source[cursor:])
    return "".join(output)
