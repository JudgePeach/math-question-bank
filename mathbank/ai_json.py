"""Defensive parsing for structured JSON returned by AI providers."""

import json
from typing import Any, List


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_envelope(text: str) -> str:
    """Remove a short prose prefix/suffix while keeping the JSON envelope."""

    object_start = text.find("{")
    array_start = text.find("[")
    starts = [position for position in (object_start, array_start) if position >= 0]
    if not starts:
        return text

    start = min(starts)
    closing = "}" if text[start] == "{" else "]"
    end = text.rfind(closing)
    if end <= start:
        return text
    return text[start : end + 1]


def _repair_json_string_content(text: str) -> str:
    """Escape raw controls and unescaped LaTeX commands inside JSON strings."""

    repaired: List[str] = []
    in_string = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue

        if char == '"':
            repaired.append(char)
            in_string = False
            index += 1
            continue

        if ord(char) < 0x20:
            repaired.append(json.dumps(char)[1:-1])
            index += 1
            continue

        if char != "\\":
            repaired.append(char)
            index += 1
            continue

        if index + 1 >= length:
            repaired.append("\\\\")
            index += 1
            continue

        next_char = text[index + 1]
        if next_char in {'"', "\\", "/"}:
            repaired.extend((char, next_char))
            index += 2
            continue

        if next_char.isascii() and next_char.isalpha():
            command_end = index + 1
            while (
                command_end < length
                and text[command_end].isascii()
                and text[command_end].isalpha()
            ):
                command_end += 1
            command = text[index + 1 : command_end]

            is_json_unicode = (
                next_char == "u"
                and index + 6 <= length
                and all(
                    digit in "0123456789abcdefABCDEF"
                    for digit in text[index + 2 : index + 6]
                )
            )
            if is_json_unicode:
                repaired.append(text[index : index + 6])
                index += 6
                continue

            if len(command) == 1 and next_char in "bfnrt":
                repaired.extend((char, next_char))
            else:
                # Multi-letter sequences such as \frac, \textbf and \nabla
                # are LaTeX commands, not JSON escape sequences.
                repaired.append("\\\\")
                repaired.append(command)
            index = command_end
            continue

        # Preserve unknown LaTeX escapes such as \{ by escaping the slash for
        # JSON while keeping the original content after decoding.
        repaired.append("\\\\")
        repaired.append(next_char)
        index += 2

    return "".join(repaired)


def parse_ai_json(raw_text: str) -> Any:
    """Parse AI JSON with narrowly scoped repairs for common model output."""

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("AI 返回了空的 JSON 内容。")

    stripped = _strip_markdown_fence(raw_text)
    envelope = _extract_json_envelope(stripped)
    candidates = [stripped]
    if envelope != stripped:
        candidates.append(envelope)

    last_error = None
    missing = object()
    for candidate in candidates:
        strict_result = missing
        try:
            strict_result = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc

        repaired = _repair_json_string_content(candidate)
        if repaired != candidate:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError as exc:
                last_error = exc

        if strict_result is not missing:
            return strict_result

    raise last_error
