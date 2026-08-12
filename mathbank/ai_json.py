"""Defensive parsing for structured JSON returned by AI providers."""

import json
from typing import Any, List, Optional


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


def realign_missing_images_to_questions(questions: list, raw_markdown: str) -> list:
    """Ensure all images extracted in raw_markdown are preserved in questions' content with '如图' keyword priority."""
    if not questions or not raw_markdown or not isinstance(raw_markdown, str):
        return questions

    raw_images = re.findall(r'!\[.*?\]\((/static/uploads/[^)]+)\)', raw_markdown)
    if not raw_images:
        return questions

    assigned_images = set()
    for q in questions:
        if isinstance(q, dict):
            c = q.get('content', '') or ''
            for img in re.findall(r'!\[.*?\]\((/static/uploads/[^)]+)\)', c):
                assigned_images.add(img)

    unassigned = [img for img in raw_images if img not in assigned_images]
    if not unassigned:
        return questions

    figure_keyword_pattern = re.compile(r'如[右下简]?图(?:所示)?|图形|图\s*\d+')

    # Find candidate questions that explicitly mention '如图' but lack images
    fig_candidate_qs = [
        q for q in questions
        if isinstance(q, dict) and figure_keyword_pattern.search(q.get('content', '')) and not re.search(r'!\[.*?\]\(', q.get('content', ''))
    ]

    for img_url in unassigned:
        matched_q = None

        # Strategy 1: Assign in sequence to candidate questions that explicitly mention '如图'
        if fig_candidate_qs:
            matched_q = fig_candidate_qs.pop(0)

        # Strategy 2: Text snippet fingerprint matching (pre-image 25 chars)
        pattern = r'([\s\S]{1,250})!\[.*?\]\(' + re.escape(img_url) + r'\)'
        m = re.search(pattern, raw_markdown)
        if not matched_q and m:
            snippet = m.group(1).strip()
            kw = snippet[-25:].strip()
            if kw:
                for q in questions:
                    if isinstance(q, dict) and kw in q.get('content', ''):
                        matched_q = q
                        break

        # Strategy 3: Fallback to last question
        if not matched_q and questions:
            matched_q = questions[-1]

        if matched_q and isinstance(matched_q, dict):
            content = matched_q.get('content', '').strip()
            matched_q['content'] = (content + f'\n\n![]({img_url})').strip()
            if 'referenced_images' not in matched_q or not isinstance(matched_q['referenced_images'], list):
                matched_q['referenced_images'] = []
            if img_url not in matched_q['referenced_images']:
                matched_q['referenced_images'].append(img_url)

    return questions


def parse_ai_json(raw_text: str, raw_markdown: Optional[str] = None) -> Any:
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
        if raw_markdown:
            parsed_data["questions"] = realign_missing_images_to_questions(parsed_data["questions"], raw_markdown)
    elif isinstance(parsed_data, list):
        for q in parsed_data:
            if isinstance(q, dict) and "content" in q and isinstance(q["content"], str):
                q["content"] = normalize_subquestions_double_newlines(q["content"])
        if raw_markdown:
            parsed_data = realign_missing_images_to_questions(parsed_data, raw_markdown)

    return parsed_data
