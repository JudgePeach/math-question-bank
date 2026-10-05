"""Collect and rewrite question image references without changing literal code."""

import json
import re


_LITERAL_START = re.compile(
    r"<!--|^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$|`+|%|"
    r"\\begin\s*\{(verbatim\*?|Verbatim\*?|lstlisting|minted|tikzpicture)\}|"
    r"\\(?:verb|Verb|lstinline|mintinline|detokenize|url|path)(?![A-Za-z])\*?",
    re.MULTILINE,
)
_IMAGE = re.compile(
    r'!\[(?:\\.|[^\]\\])*\]\(\s*(?:<(?P<angle>[^>\n]+)>|(?P<markdown>[^\s)]+))(?:[ \t]+[^)\n]*)?\s*\)'
    r'|\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{(?P<latex>[^{}]+)\}'
    r'''|<img\b[^>]*?\bsrc\s*=\s*(?:"(?P<html_double>[^"]+)"|'(?P<html_single>[^']+)'|(?P<html_bare>[^\s>]+))[^>]*>''',
    re.IGNORECASE,
)
_LOCAL = re.compile(r"^/?static/(?:uploads|test_uploads)/")


def markdown_literal_ranges(text: str, *, include_links=True) -> list[tuple[int, int]]:
    """Locate Markdown literal examples without treating list continuations as code.

    Image discovery excludes links from this mask. Formula locking includes
    them, so dollars in examples, URLs, and image filenames remain literal.
    """
    value = str(text or "")
    ranges = [match.span() for match in re.finditer(
        r"<!--[\s\S]*?(?:-->|$)|<(pre|code)\b[^>]*>[\s\S]*?(?:</\1\s*>|$)",
        value, re.IGNORECASE,
    )]

    def covered(position):
        return any(start <= position < end for start, end in ranges)

    opened = None
    for marker in re.finditer(r"^ {0,3}(`{3,}|~{3,})[^\n]*$", value, re.MULTILINE):
        if covered(marker.start()):
            continue
        fence = marker.group(1)
        if opened is None:
            opened = (marker.start(), fence[0], len(fence))
        elif (fence[0] == opened[1] and len(fence) >= opened[2]
              and re.fullmatch(r" {0,3}" + re.escape(fence[0]) + r"{" + str(opened[2]) + r",}[ \t]*", marker.group())):
            ranges.append((opened[0], marker.end()))
            opened = None
    if opened:
        ranges.append((opened[0], len(value)))

    # A blank line or document start establishes a top-level indented code
    # block. Under an active list, ordinary continuation indentation remains
    # visible; only four additional columns establish a code block.
    offset, block_start, previous_blank, list_content_indent = 0, None, True, None
    for line in value.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if covered(offset):
            if block_start is not None:
                ranges.append((block_start, offset))
                block_start = None
            previous_blank = not body.strip()
            offset += len(line)
            continue
        if not body.strip():
            previous_blank = True
            offset += len(line)
            continue
        leading = re.match(r"[ \t]*", body).group()
        indent = len(leading.expandtabs(4))
        marker = re.match(r"(?:[-+*]|\d{1,9}[.)])[ \t]+", body[len(leading):])
        code_indent = (list_content_indent + 4) if list_content_indent is not None else 4
        if (marker and (indent < 4 or list_content_indent is not None)
                and not (indent >= code_indent and (previous_blank or block_start is not None))):
            if block_start is not None:
                ranges.append((block_start, offset))
                block_start = None
            list_content_indent = indent + len(marker.group().expandtabs(4))
        else:
            if indent >= code_indent and (previous_blank or block_start is not None):
                if block_start is None:
                    block_start = offset
            else:
                if block_start is not None:
                    ranges.append((block_start, offset))
                    block_start = None
                if indent < 4:
                    list_content_indent = None
        previous_blank = False
        offset += len(line)
    if block_start is not None:
        ranges.append((block_start, len(value)))

    cursor = 0
    while marker := re.search(r"`+", value[cursor:]):
        start, end = cursor + marker.start(), cursor + marker.end()
        if covered(start) or _escaped(value, start):
            cursor = end
            continue
        closing = next((match for match in re.finditer(r"`+", value[end:])
                        if len(match.group()) == end - start), None)
        if closing is not None:
            end += closing.end()
            ranges.append((start, end))
        cursor = end
    if include_links:
        ranges.extend(match.span() for match in _IMAGE.finditer(value)
                      if not covered(match.start()) and not _escaped(value, match.start()))
        for pattern in (r"!?\[[^\[\]\n]*\]\([^\n)]*\)", r"(?:https?|ftp)://[^\s<>]+"):
            ranges.extend(match.span() for match in re.finditer(pattern, value, re.IGNORECASE)
                          if not covered(match.start()) and not _escaped(value, match.start()))
    return sorted(set(ranges))


def _escaped(value, start):
    cursor = start - 1
    while cursor >= 0 and value[cursor] == "\\":
        cursor -= 1
    return (start - cursor - 1) % 2 == 1


def _brace_end(value, start):
    depth = 0
    for index in range(start, len(value)):
        if _escaped(value, index):
            continue
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(value)


def _mask_literals(value, *, tex_comments=True):
    chars, cursor = list(value), 0
    image_ranges = [(item.start(), item.end()) for item in _IMAGE.finditer(value)]
    while match := _LITERAL_START.search(value, cursor):
        token, end = match.group(), match.end()
        image_end = next((stop for start, stop in image_ranges
                          if start <= match.start() < stop and not _escaped(value, start)
                          and chars[start] == value[start]), None)
        if image_end is not None:
            cursor = image_end
            continue
        if _escaped(value, match.start()):
            cursor = end
            continue
        if token == "%" and not tex_comments:
            cursor = end
            continue
        if token == "<!--":
            closing = value.find("-->", end)
            end = closing + 3 if closing >= 0 else len(value)
        elif token == "%":
            closing = value.find("\n", end)
            end = closing if closing >= 0 else len(value)
        elif match.group(1):
            fence = match.group(1)
            closing = re.search(r"^[ \t]{0,3}" + re.escape(fence[0]) + "{" + str(len(fence))
                                + r",}[ \t]*$", value[end:], re.MULTILINE)
            end = end + closing.end() if closing else len(value)
        elif token.startswith("`"):
            closing = next((part for part in re.finditer(r"`+", value[end:])
                            if len(part.group()) == len(token)), None)
            if closing is None:
                cursor = end
                continue
            end += closing.end()
        elif match.group(2):
            closing = re.search(r"\\end\s*\{" + re.escape(match.group(2)) + r"\}", value[end:])
            end = end + closing.end() if closing else len(value)
        else:
            command = token.lstrip("\\").rstrip("*")
            argument = end
            while argument < len(value) and value[argument] in " \t":
                argument += 1
            if value[argument:argument + 1] == "[" and command in {"Verb", "lstinline", "mintinline"}:
                closing = value.find("]", argument + 1)
                argument = closing + 1 if closing >= 0 else len(value)
            if command == "mintinline" and value[argument:argument + 1] == "{":
                argument = _brace_end(value, argument)
            while argument < len(value) and value[argument] in " \t":
                argument += 1
            if argument >= len(value):
                end = len(value)
            elif value[argument] == "{" and command not in {"verb", "Verb"}:
                end = _brace_end(value, argument)
            elif not value[argument].isalnum() and value[argument] not in " \t\r\n":
                closing = value.find(value[argument], argument + 1)
                line_end = value.find("\n", argument + 1)
                line_end = len(value) if line_end < 0 else line_end
                end = closing + 1 if 0 <= closing < line_end else line_end
            else:
                cursor = end
                continue
        for index in range(match.start(), end):
            if chars[index] not in "\r\n":
                chars[index] = " "
        cursor = end
    return "".join(chars)


def _markdown_masked_image_source(value: str, masked: str) -> str:
    chars = list(masked)
    for start, end in markdown_literal_ranges(value, include_links=False):
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def unsupported_markdown_image_references(text: str) -> list[str]:
    """Report bare parenthesized destinations that the legacy regex truncates.

    The angle-bracket form can represent these names without ambiguity. A
    partial bare name must never reach the filename/stem matching fallback.
    """
    value = str(text or "")
    masked = _markdown_masked_image_source(value, _mask_literals(value, tex_comments=False))
    return list(dict.fromkeys(match.group("markdown") for match in _IMAGE.finditer(value)
                             if match.group("markdown") is not None
                             and any(char in match.group("markdown") for char in "()")
                             and masked[match.start():match.start() + 2] == value[match.start():match.start() + 2]
                             and not _escaped(value, match.start())))


def image_reference_spans(text: str, *, local_only=True, tex_comments=True, markdown_mode=False):
    """Yield image destinations outside literals; paths need security validation.

    Importing Markdown can include relative destinations and literal percent
    signs. Existing question lifecycle callers retain the local/TeX defaults.
    """
    value = str(text or "")
    masked = _mask_literals(value, tex_comments=tex_comments)
    if markdown_mode:
        masked = _markdown_masked_image_source(value, masked)
    for match in _IMAGE.finditer(value):
        # A percent sign in image alt/title text is part of that image, not a
        # TeX comment. Only the opening syntax determines literal ownership.
        if masked[match.start():match.start() + 2] != value[match.start():match.start() + 2]:
            continue
        preceding = match.start() - 1
        while preceding >= 0 and masked[preceding] == "\\":
            preceding -= 1
        if (match.start() - preceding - 1) % 2:
            continue
        for key, path in match.groupdict().items():
            if markdown_mode and key == "markdown" and path is not None and any(char in path for char in "()"):
                continue
            if path is not None and (not local_only or _LOCAL.match(path.strip())):
                start, end = match.span(key)
                yield start + len(path) - len(path.lstrip()), end - len(path) + len(path.rstrip()), path.strip()
                break


def embedded_question_assets(*texts: str) -> list[str]:
    return list(dict.fromkeys(path for text in texts for _, _, path in image_reference_spans(text)))


def structured_question_assets(*values, legacy_reference: str = "") -> list[str]:
    """Collect rendered and hidden reference images, never TikZ source literals."""
    paths = [legacy_reference] if legacy_reference else []
    for value in values:
        assets = json.loads(value) if isinstance(value, str) and value else (value or [])
        if assets is None:
            continue
        if not isinstance(assets, list):
            raise ValueError("TikZ 资产必须是数组")
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError("TikZ 资产格式无效")
            for key in ("image_path", "reference_image_path"):
                if asset.get(key):
                    paths.append(asset[key])
    return paths


def rewrite_question_asset_paths(text: str, replacements: dict[str, str]) -> str:
    value = str(text or "")
    for start, end, path in reversed(list(image_reference_spans(value))):
        if path in replacements:
            value = value[:start] + replacements[path] + value[end:]
    return value


def rewrite_structured_asset_paths(value, replacements: dict[str, str]):
    if value is None:
        return None
    assets = json.loads(value) if isinstance(value, str) and value else (value or [])
    if not isinstance(assets, list):
        raise ValueError("TikZ 资产必须是数组")
    return [dict(asset, **{
        key: replacements.get(asset[key], asset[key])
        for key in ("image_path", "reference_image_path") if asset.get(key)
    }) for asset in assets]


def rewrite_image_layout_paths(value, replacements: dict[str, str]):
    if value is None:
        return None
    layouts = json.loads(value) if isinstance(value, str) else value
    if not isinstance(layouts, dict):
        raise ValueError("插图独立排版必须是对象")
    from mathbank.image_layout import image_key

    keys = {image_key(old): image_key(new) for old, new in replacements.items()}
    return {keys.get(image_key(key), key): settings for key, settings in layouts.items()}
