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


import re


def normalize_subquestions_double_newlines(text: str) -> str:
    """Ensure subquestions (1), (2), (i), (ii), （1）, （2） start on a clean paragraph with double newlines."""
    if not text or not isinstance(text, str):
        return text

    s = text.strip()
    # Replace single newline or punctuation-attached subquestion with standard double newlines
    s = re.sub(
        r'(?<!^)(?<!\n\n)(?:[。；;!！\.]|\s+|\n)\s*([(（](?:[1-9]|10|[ivxIVX]+)[)）]|\([1-9]\)|（[1-9]）|[①②③④⑤⑥⑦⑧⑨⑩])(?=\s*[\u4e00-\u9fa5a-zA-Z\$])',
        r'\n\n\1 ',
        s
    )
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


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
    parsed_data = missing
    for candidate in candidates:
        try:
            parsed_data = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc

        repaired = _repair_json_string_content(candidate)
        if repaired != candidate:
            try:
                parsed_data = json.loads(repaired)
                break
            except json.JSONDecodeError as exc:
                last_error = exc

    if parsed_data is missing:
        raise last_error if last_error else ValueError("无法解析返回的 JSON 内容。")

    # Post-process: auto-heal subquestions double newlines in questions
    if isinstance(parsed_data, dict) and "questions" in parsed_data and isinstance(parsed_data["questions"], list):
        for q in parsed_data["questions"]:
            if isinstance(q, dict) and "content" in q and isinstance(q["content"], str):
                q["content"] = normalize_subquestions_double_newlines(q["content"])
    elif isinstance(parsed_data, list):
        for q in parsed_data:
            if isinstance(q, dict) and "content" in q and isinstance(q["content"], str):
                q["content"] = normalize_subquestions_double_newlines(q["content"])

    return parsed_data
