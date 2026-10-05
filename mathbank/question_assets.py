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


def _mask_literals(value):
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


def image_reference_spans(text: str):
    """Yield local image destination spans; paths still need security validation."""
    value = str(text or "")
    masked = _mask_literals(value)
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
            if path is not None and _LOCAL.match(path.strip()):
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
