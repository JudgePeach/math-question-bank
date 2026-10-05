"""Conservative single-file Markdown exam import, using the TeX text flow."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from mathbank.content_locks import (
    _is_escaped, _literal_math_scan_source, _next_math_span, _reference_literal_ranges,
)
from mathbank.question_assets import image_reference_spans, unsupported_markdown_image_references
from mathbank.question_assets import markdown_literal_ranges
from mathbank.tex_helper import MAX_TEX_BYTES, decode_tex_bytes, tex_asset_basename, tex_asset_references_match


MAX_MARKDOWN_BYTES = MAX_TEX_BYTES
MARKDOWN_IMPORT_WARNING = (
    "Markdown（.md）格式支持尚不完善，拆分结果可能有误，请谨慎使用；"
    "入库前请逐题核对题干、公式、配图和答案对应。"
)


def markdown_image_references(source: str) -> list[str]:
    return list(dict.fromkeys(path for _, _, path in image_reference_spans(
        source, local_only=False, tex_comments=False, markdown_mode=True,
    )))


def _record_unsupported_images(source: str, diagnostics: dict) -> None:
    references = unsupported_markdown_image_references(source)
    if not references:
        return
    previous = diagnostics.setdefault("unsupported_image_references", [])
    previous.extend(reference for reference in references if reference not in previous)
    warning = (
        "检测到尚不支持的带括号 Markdown 图片路径，未自动匹配配图；"
        "请改用 ![说明](<完整图片路径>)，并在入库前核对配图。"
    )
    warnings = diagnostics.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)


def _markdown_title(source: str) -> str:
    """Read a top-level ATX/Setext title, never a fenced code example."""
    fence = None
    in_comment = False
    previous = ""
    for line in source.splitlines():
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line:
            in_comment = "-->" not in line.split("<!--", 1)[1]
            previous = ""
            continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            previous = ""
            continue
        title = re.match(r"^ {0,3}#\s+(.+?)\s*#*\s*$", line)
        if title:
            return re.sub(r"\*\*([^*\n]+)\*\*", r"\1", title[1]).strip()
        if previous and re.fullmatch(r" {0,3}=+[ \t]*", line):
            return re.sub(r"\*\*([^*\n]+)\*\*", r"\1", previous).strip()
        previous = line if line.strip() and not line.startswith(("    ", "\t")) else ""
    return ""


def prepare_markdown_source(source: str) -> dict:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Markdown 源码为空。")
    if len(source.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise ValueError("Markdown 源码过大，请控制在 5MB 以内。")
    normalized = source.lstrip("\ufeff").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    graphics = markdown_image_references(normalized)
    warnings = [MARKDOWN_IMPORT_WARNING]
    if graphics:
        warnings.append("检测到配图引用，请上传同名配套图片；不会自动读取本地路径或下载外部图片。")
    diagnostics = {
        "source_format": "markdown",
        "referenced_graphics": graphics,
        "warnings": warnings,
        "source_characters": len(normalized),
        "model_characters": len(normalized),
    }
    _record_unsupported_images(normalized, diagnostics)
    return {
        "source": normalized,
        "model_source": normalized,
        "title": _markdown_title(normalized),
        "diagnostics": diagnostics,
    }


def decode_and_prepare_markdown(file_bytes: bytes) -> dict:
    try:
        source, encoding_diagnostics = decode_tex_bytes(file_bytes)
    except ValueError as exc:
        raise ValueError(str(exc).replace("TeX", "Markdown")) from exc
    result = prepare_markdown_source(source)
    result["diagnostics"].update(encoding_diagnostics)
    return result


def _mapped_image_path(reference: str, mapping: dict) -> str | None:
    # Relative paths are bound only to explicitly uploaded files. Never fetch
    # URLs or infer that a remote curve.png is an uploaded curve.png.
    reference = unquote(reference)
    try:
        remote = reference.startswith("//") or bool(urlsplit(reference).scheme)
    except ValueError:
        return None
    if remote:
        return None

    def normalized(value):
        return str(value or "").strip().replace("\\", "/").lstrip("./").lower()

    reference = normalized(reference)
    if not reference:
        return None
    candidates = [(normalized(name), str(path)) for name, path in mapping.items()]
    # Preserve explicit extension/path evidence before considering the TeX
    # compatibility fallback. An unrelated fig.jpg must not block fig.png.
    for matches in (
        [path for name, path in candidates if name == reference],
        [path for name, path in candidates if tex_asset_basename(name) == tex_asset_basename(reference)],
        [path for name, path in candidates if tex_asset_references_match(reference, name)],
    ):
        if matches:
            return matches[0] if len(set(matches)) == 1 else None
    return None


def map_markdown_question_images(question: dict, mapping: dict, diagnostics: dict) -> None:
    """Replace only visible destinations in place, including original answers."""
    paths = []
    visible_refs = []
    for field in ("content", "answer_markdown"):
        value = str(question.get(field) or "")
        _record_unsupported_images(value, diagnostics)
        spans = list(image_reference_spans(value, local_only=False, tex_comments=False, markdown_mode=True))
        for start, end, reference in reversed(spans):
            path = _mapped_image_path(reference, mapping)
            if path:
                value = value[:start] + path + value[end:]
        question[field] = value
        for _, _, reference in spans:
            visible_refs.append(reference)
            path = _mapped_image_path(reference, mapping)
            if path:
                paths.append(path)
            else:
                diagnostics.setdefault("unmapped_images", []).append(reference)
    # Metadata alone cannot supply the original anchor. Retain a warning
    # instead of appending an image that might belong to the answer or options.
    for reference in question.get("referenced_images", []):
        reference = str(reference)
        if not any(tex_asset_references_match(reference, item) for item in visible_refs):
            diagnostics.setdefault("unassigned_source_images", []).append(reference)
    question["image_paths"] = list(dict.fromkeys(paths))


def _storage_math_ranges(value: str, literals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Protect math bytes without using a literal's dollar as its closing edge."""
    ranges, cursor = [], 0
    # URLs/link text inside existing math must not mask that math's closing
    # delimiter. Only code examples/macros supply inert delimiter examples.
    code_literals = markdown_literal_ranges(value, include_links=False) + [
        (left, right) for left, right in _reference_literal_ranges(value, tex_comments=False)
        if value[left:left + 1] == "\\"
    ]
    scan_value = _literal_math_scan_source(value, code_literals)
    while span := _next_math_span(scan_value, cursor):
        start, end = span
        literal_open = max((right for left, right in literals if left <= start < right), default=None)
        if literal_open is not None:
            cursor = literal_open
            continue
        raw_span = _next_math_span(value, cursor)
        literal_close = (max((right for left, right in code_literals if left < raw_span[1] <= right), default=None)
                         if raw_span and raw_span[0] == start else None)
        if literal_close is not None:
            cursor = literal_close
            continue
        ranges.append(span)
        cursor = end
    return ranges


def _safe_code_literal(body: str, *, inline: bool) -> str:
    """Choose delimiters absent from the payload; never edit code/path bytes."""
    if inline and "\n" not in body and "\r" not in body:
        if not body:
            return ""
        count = max((len(match.group()) for match in re.finditer(r"`+", body)), default=0) + 1
        marker = "`" * count
        padding = " " if body.startswith("`") or body.endswith("`") else ""
        return marker + padding + body + padding + marker
    count = max(3, max((len(match.group()) for match in re.finditer(r"~+", body)), default=0) + 1)
    marker = "~" * count
    return marker + "\n" + body + ("" if body.endswith("\n") else "\n") + marker


def prepare_markdown_question_for_storage(question: dict, diagnostics: dict) -> None:
    """Adapt only a new candidate to the existing stored-question grammar.

    Original Markdown and source evidence are untouched. Stored questions do
    not carry a format flag, so legacy asset parsing needs escaped prose
    percentages and delimiters it already recognizes around literal examples.
    """
    percent_count = code_count = math_percent_count = 0
    for field in ("content", "answer_markdown"):
        value = str(question.get(field) or "")
        # Markdown owns fence lengths/indentation. Reuse only the TeX literal
        # macros/environments from the legacy mask, not its looser code regex.
        tex_literals = [(left, right) for left, right in _reference_literal_ranges(value, tex_comments=False)
                        if value[left:left + 1] == "\\"]
        literals = markdown_literal_ranges(value) + tex_literals
        math_ranges = _storage_math_ranges(value, literals)
        protected = literals + math_ranges
        edits = []
        for marker in re.finditer("%", value):
            position = marker.start()
            if _is_escaped(value, position):
                continue
            if any(left <= position < right for left, right in math_ranges):
                math_percent_count += 1
            elif not any(left <= position < right for left, right in protected):
                edits.append(position)
        for position in reversed(edits):
            value = value[:position] + "\\" + value[position:]
        percent_count += len(edits)

        ranges = markdown_literal_ranges(value, include_links=False)
        current_literals = markdown_literal_ranges(value) + [
            (left, right) for left, right in _reference_literal_ranges(value, tex_comments=False)
            if value[left:left + 1] == "\\"
        ]
        current_math = _storage_math_ranges(value, current_literals)
        # Nested inline examples inside an existing fence/comment remain part
        # of that already protected outer literal, rather than gaining fences.
        outer = [(left, right) for left, right in ranges if not any(
            other_left <= left and right <= other_right and (left, right) != (other_left, other_right)
            for other_left, other_right in ranges
        )]
        replacements = []
        for left, right in outer:
            if any(start <= left and right <= end for start, end in current_math):
                continue
            snippet = value[left:right]
            html = re.match(r"<(pre|code)\b[^>]*>", snippet, re.IGNORECASE)
            indented = re.match(r"[ \t]+", snippet)
            if not html and not (indented and len(indented.group().expandtabs(4)) >= 4):
                continue
            body, inline = snippet, False
            if html:
                body = snippet[html.end():]
                closing = re.search(r"</" + html.group(1) + r"\s*>$", body, re.IGNORECASE)
                if closing:
                    body = body[:closing.start()]
                inline = html.group(1).lower() == "code"
            replacement = _safe_code_literal(body, inline=inline)
            if not inline or "\n" in body or "\r" in body:
                replacement = ("\n\n" if left and not value[:left].endswith("\n\n") else "") + replacement
                replacement += "\n\n" if right < len(value) and not value[right:].startswith("\n\n") else ""
            replacements.append((left, right, replacement))
        for left, right, replacement in reversed(replacements):
            value = value[:left] + replacement + value[right:]
        code_count += len(replacements)
        question[field] = value

    if percent_count or code_count or math_percent_count:
        report = diagnostics.setdefault("markdown_storage_compatibility", {
            "percent_escapes": 0, "code_literals_wrapped": 0, "math_percent_requires_review": 0,
        })
        report["percent_escapes"] += percent_count
        report["code_literals_wrapped"] += code_count
        report["math_percent_requires_review"] += math_percent_count
        warnings = diagnostics.setdefault("warnings", [])
        if percent_count or code_count:
            warning = "Markdown 题卡保存副本已适配正文百分号及代码字面包装，原文和来源证据保持原样；请核对预览。"
            if warning not in warnings:
                warnings.append(warning)
        if math_percent_count:
            warning = "Markdown 原数学环境含未转义百分号，已保留原公式，请对照原文核对显示。"
            if warning not in warnings:
                warnings.append(warning)
