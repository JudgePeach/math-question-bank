"""Small, export-only adapters for school mathematics notation.

Question source is never rewritten. XeLaTeX must use its math font even when
the symbol occurs in prose; Pandoc needs the Unicode symbol in a math span.
"""

from __future__ import annotations

import re

from mathbank.math_markdown import (
    _INNER_MATH_ENVIRONMENTS,
    _STANDALONE_MATH_ENVIRONMENTS,
    _is_escaped,
    _replace_delimited_math,
    _replace_latex_environments,
)
from mathbank.tex_helper import _balanced_group


_PROTECTED = re.compile(
    r"(?m:^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n)"
    r"[\s\S]*?(?:(?m:^[ \t]{0,3}(?P=fence)[ \t]*(?:\n|$))|\Z)"
    r"|<mathbank-math\b[^>]*>[\s\S]*?(?:</mathbank-math\s*>|\Z)"
    r"|\[\[\s*MBM_[A-Za-z0-9_:\-]+\s*\]\]"
    r"|<(?:code|pre)\b[^>]*>[\s\S]*?</(?:code|pre)\s*>"
    r"|\\begin\{(?P<environment>tikzpicture|pgfpicture|axis|verbatim\*?|Verbatim|lstlisting|minted)\}"
    r"[\s\S]*?(?:\\end\{(?P=environment)\}|\Z)",
    re.IGNORECASE,
)
_COMMAND = re.compile(r"\\(?:[A-Za-z]+|[^\n])")
_SYMBOL = re.compile(
    r"\\ensuremath\{\s*\\parallelogram(?:\{\})?\s*\}"
    r"|\\text\{\s*(?:▱|\\parallelogram(?:\{\})?)\s*\}"
    r"|\\parallelogram(?![A-Za-z])(?:\{\})?"
    r"|▱"
)
_PROTECTED_ARGUMENT_COMMANDS = {"includegraphics", "url", "path", "href", "label", "tikz"}
_WORD_SCHOOL_ARITIES = {
    "wideparen": 1, "overparen": 1, "widearc": 1, "overarc": 1,
    "celsius": 0, "℃": 0, "perthousand": 0, "textperthousand": 0,
    "permil": 0, "‰": 0, "sfrac": 2, "ang": 1,
}
_SCHOOL_TOKEN = re.compile(r"\\(?:[A-Za-z]+|[^\n])|[℃‰]")
_TEXT_UNIT = re.compile(r"\\(?:celsius|perthousand|textperthousand|permil)(?![A-Za-z])|℃")
_LATEX_UNIT = re.compile(r"\\(?:textperthousand|permil)(?![A-Za-z])|[℃‰]")
_REASONING_SYMBOLS = {"∵": r"\because", "∴": r"\therefore"}
_REASONING_TOKEN = re.compile(r"\\(?:[A-Za-z]+|[^\n])|[∵∴]")
_TEXT_ARGUMENT_COMMANDS = {"text", "textrm", "textnormal", "textbf", "textit", "emph", "mbox", "hbox"}


def _replace_latex_reasoning_symbols(source: str, *, in_math: bool) -> str:
    """Use the math font for Unicode reasoning signs, including text macros."""
    parts: list[str] = []
    cursor = copied = 0
    while match := _REASONING_TOKEN.search(source, cursor):
        cursor = match.end()
        if _is_escaped(source, match.start()):
            continue
        token = match.group()
        if token in _REASONING_SYMBOLS:
            command = _REASONING_SYMBOLS[token]
            # A lexical space ends the control word without an empty group
            # that would steal a following subscript/superscript attachment.
            replacement = command + " " if in_math else r"\ensuremath{" + command + "}"
        elif token.removeprefix("\\") in _TEXT_ARGUMENT_COMMANDS | {"ensuremath"}:
            group_start = cursor
            while group_start < len(source) and source[group_start].isspace():
                group_start += 1
            group = _balanced_group(source, group_start)
            if group is None:
                continue
            content = _replace_latex_reasoning_symbols(group[0], in_math=token == r"\ensuremath")
            replacement = source[match.start():group_start] + "{" + content + "}"
            cursor = group[1]
        else:
            continue
        parts.extend((source[copied:match.start()], replacement))
        copied = cursor
    parts.append(source[copied:])
    return "".join(parts)


def _replace_word_school_commands(source: str, *, in_math: bool) -> str:
    """Retain editable arc/slanted-fraction semantics in Pandoc's vocabulary."""
    parts: list[str] = []
    cursor = copied = 0
    while match := _SCHOOL_TOKEN.search(source, cursor):
        cursor = match.end()
        name = match.group().removeprefix("\\")
        if _is_escaped(source, match.start()):
            continue
        if name in {"text", "textrm", "textnormal", "mbox"}:
            group = _balanced_group(source, cursor)
            if group is not None:
                def unit_text(token):
                    if _is_escaped(group[0], token.start()):
                        return token.group()
                    return "°C" if token.group() in {"℃", r"\celsius"} else "‰"
                text = _TEXT_UNIT.sub(unit_text, group[0])
                mixed = _replace_word_school_commands(text, in_math=False)
                def leave_text(span):
                    size = 2 if span.startswith(("$$", r"\(", r"\[")) else 1
                    return "}" + span[size:-size] + match.group() + "{"
                mixed = _replace_delimited_math(mixed, leave_text)
                replacement = match.group() + "{" + mixed + "}"
                replacement = replacement.replace(match.group() + "{}", "")
                if not in_math and (text != group[0] or mixed != text):
                    replacement = r"\(" + replacement + r"\)"
                parts.extend((source[copied:match.start()], replacement))
                copied = cursor = group[1]
            continue
        if name not in _WORD_SCHOOL_ARITIES:
            continue
        arguments: list[str] = []
        finish = cursor
        if name == "overarc":
            while finish < len(source) and source[finish].isspace():
                finish += 1
            if finish < len(source) and source[finish] == "[":
                option_end = _paired_end(source, finish, "[", "]")
                if source[finish + 1:option_end - 1].strip() != "1":
                    continue
                finish = option_end
        for _ in range(_WORD_SCHOOL_ARITIES[name]):
            while finish < len(source) and source[finish].isspace():
                finish += 1
            group = _balanced_group(source, finish)
            if group is None:
                break  # Incomplete/unsupported syntax remains visible unchanged.
            arguments.append(_replace_word_school_commands(group[0], in_math=True))
            finish = group[1]
        if len(arguments) != _WORD_SCHOOL_ARITIES[name]:
            continue
        if name in {"wideparen", "overparen", "widearc", "overarc"}:
            replacement = rf"\overparen{{{arguments[0]}}}"
        elif name in {"celsius", "℃"}:
            replacement = r"{}^\circ\mathrm{C}"
        elif name in {"perthousand", "textperthousand", "permil", "‰"}:
            replacement = r"\text{‰}"
        elif name == "ang":
            replacement = rf"\ang{{{arguments[0]}}}"
        else:
            # A superscript numerator and subscript denominator preserve the
            # authored diagonal fraction; a normal \frac would change its form.
            replacement = rf"{{}}^{{{arguments[0]}}}\!/\!_{{{arguments[1]}}}"
        if not in_math:
            replacement = r"\(" + replacement + r"\)"
        parts.extend((source[copied:match.start()], replacement))
        copied = cursor = finish
    parts.append(source[copied:])
    return "".join(parts)


def _paired_end(source: str, start: int, opening: str, closing: str) -> int:
    """Consume an image destination/optional argument without changing it."""
    depth = 0
    for index in range(start, len(source)):
        if _is_escaped(source, index):
            continue
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return len(source)


def _normalize_export_symbols(value: str, *, target: str, school_commands: bool = False) -> str:
    r"""Adapt only ``▱`` / ``\parallelogram`` for a rendering destination.

    Code, images/URLs, TikZ, comments and source locks remain byte-for-byte
    intact. Prose symbols use ``\(...\)`` for Word so adjacent existing dollar
    delimiters cannot accidentally form display math. No stored source changes.
    """
    if target not in {"latex", "word"}:
        raise ValueError("Unsupported geometry-symbol export target")
    if not value or ("▱" not in value and r"\parallelogram" not in value
                     and not (school_commands and (any(char in value for char in "℃‰")
                              or any("\\" + name in value for name in _WORD_SCHOOL_ARITIES)
                              or target == "latex" and any(char in value for char in _REASONING_SYMBOLS)))):
        return value or ""

    protected: dict[str, str] = {}
    next_marker = 0xF0000
    original_characters = set(value)

    def save(original: str) -> str:
        nonlocal next_marker
        while chr(next_marker) in original_characters:
            next_marker += 1
        marker = chr(next_marker)
        next_marker += 1
        protected[marker] = original
        return marker

    source = _PROTECTED.sub(lambda match: save(match.group()), value)
    parts: list[str] = []
    cursor = copied = 0
    while cursor < len(source):
        end = cursor
        if _is_escaped(source, cursor):
            cursor += 1
            continue
        if source[cursor] == "`":
            opening = re.match(r"`+", source[cursor:]).group()
            end = len(source)
            for closing in re.finditer(r"`+", source[cursor + len(opening):]):
                if closing.group() == opening:
                    end = cursor + len(opening) + closing.end()
                    break
        elif source.startswith("![", cursor) or source[cursor] == "[":
            bracket = cursor + (1 if source[cursor] == "!" else 0)
            bracket_end = _paired_end(source, bracket, "[", "]")
            if bracket_end < len(source) and source[bracket_end] == "(":
                end = _paired_end(source, bracket_end, "(", ")")
        elif source.startswith(("https://", "http://", "/static/uploads/", "/static/test_uploads/"), cursor):
            end = cursor + re.match(r"[^\s<>]+", source[cursor:]).end()
        elif source[cursor] == "%":
            end = source.find("\n", cursor)
            end = len(source) if end < 0 else end
        elif source[cursor] == "\\":
            command = _COMMAND.match(source, cursor)
            if command and command.group()[1:] in _PROTECTED_ARGUMENT_COMMANDS | {"verb"}:
                argument = command.end()
                if argument < len(source) and source[argument] == "*":
                    argument += 1
                while argument < len(source) and source[argument].isspace():
                    argument += 1
                if command.group() == r"\verb" and argument < len(source):
                    close = source.find(source[argument], argument + 1)
                    end = len(source) if close < 0 else close + 1
                else:
                    if argument < len(source) and source[argument] == "[":
                        argument = _paired_end(source, argument, "[", "]")
                    while argument < len(source) and source[argument].isspace():
                        argument += 1
                    if argument < len(source) and source[argument] == "{":
                        group = _balanced_group(source, argument)
                        end = group[1] if group else len(source)
        if end > cursor:
            parts.extend((source[copied:cursor], save(source[cursor:end])))
            copied = cursor = end
        else:
            cursor += 1
    parts.append(source[copied:])
    source = "".join(parts)

    def replace_symbols(text: str, replacement: str) -> str:
        return _SYMBOL.sub(
            lambda match: match.group() if _is_escaped(text, match.start()) else replacement,
            text,
        )

    if target == "latex":
        result = replace_symbols(source, r"\ensuremath{\parallelogram}")
        if school_commands:
            result = _LATEX_UNIT.sub(
                lambda match: match.group() if _is_escaped(result, match.start())
                else (r"\celsius{}" if match.group() == "℃" else r"\perthousand{}"),
                result,
            )
            if any(char in result for char in _REASONING_SYMBOLS):
                result = _replace_delimited_math(
                    result, lambda span: save(_replace_latex_reasoning_symbols(span, in_math=True)),
                )

                def reasoning_environment(span: str, name: str) -> str:
                    if name in _STANDALONE_MATH_ENVIRONMENTS | _INNER_MATH_ENVIRONMENTS:
                        return save(_replace_latex_reasoning_symbols(span, in_math=True))
                    opening = span.index("}") + 1
                    closing = span.rfind(r"\end{")
                    return (span[:opening] + _replace_latex_environments(span[opening:closing], reasoning_environment)
                            + span[closing:])

                result = _replace_latex_environments(result, reasoning_environment)
                result = _replace_latex_reasoning_symbols(result, in_math=False)
    else:
        # Save existing math first; Unicode inside \text remains native OMML.
        def adapt_math(span: str) -> str:
            span = replace_symbols(span, "▱")
            return _replace_word_school_commands(span, in_math=True) if school_commands else span

        source = _replace_delimited_math(source, lambda span: save(adapt_math(span)))

        def visit_environment(span: str, name: str) -> str:
            if name in _STANDALONE_MATH_ENVIRONMENTS | _INNER_MATH_ENVIRONMENTS:
                return save(adapt_math(span))
            opening = span.index("}") + 1
            closing = span.rfind(r"\end{")
            return span[:opening] + _replace_latex_environments(span[opening:closing], visit_environment) + span[closing:]

        source = _replace_latex_environments(source, visit_environment)
        result = replace_symbols(source, r"\(▱\)")
        if school_commands:
            result = _replace_word_school_commands(result, in_math=False)

    if not protected:
        return result
    marker_pattern = re.compile("[" + chr(0xF0000) + "-" + chr(next_marker - 1) + "]")

    def restore(match: re.Match[str]) -> str:
        original = protected.get(match.group())
        return match.group() if original is None else marker_pattern.sub(restore, original)

    return marker_pattern.sub(restore, result)


def normalize_parallelogram_symbols(value: str, *, target: str = "latex") -> str:
    """Adapt parallelogram notation without modifying saved question source."""
    return _normalize_export_symbols(value, target=target)


def normalize_school_math_symbols(value: str, *, target: str = "word") -> str:
    """Adapt the small supported school-notation set on an export-only copy."""
    return _normalize_export_symbols(value, target=target, school_commands=True)
