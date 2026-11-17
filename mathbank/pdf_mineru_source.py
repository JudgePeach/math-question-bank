"""Page-bound source candidates from a MinerU precision extraction bundle.

This laboratory input is supplied explicitly by an internal caller. The normal
upload API does not acquire a MinerU bundle and does not depend on this engine.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from html import unescape
from io import BytesIO
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from zipfile import BadZipFile, ZipFile

from mathbank.pdf_fidelity import find_choice_grids

_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
_MAX_LIST_BYTES = 8 * 1024 * 1024
_MAX_TEXT = 45_000
_MAX_BLOCKS = 12_000
_MAX_PAGE_PIXELS = 2_500_000
_MAX_GRAPHICS = 30
_PAGE_FURNITURE = {"page_number", "header", "footer"}
_PLAIN_TYPES = {"text", "equation"}
_ASSET_LIKE = re.compile(r"!\[[^\n]{0,200}\]\(|\\includegraphics\b|<\s*(?:img|script|iframe)\b", re.I)
_ALGORITHM = re.compile(r'\s*<div class="mineru-algorithm"[^<>]*>([\s\S]*?)</div>\s*')
_SOURCE_CHOICE = re.compile(r'(?<![A-Za-z0-9])([A-D])[.．、]\s*(?=\S)')


@dataclass(frozen=True)
class MineruPDFSource:
    """Validated bundle bytes scoped to the exact original PDF."""
    source_sha256: str
    bundle_sha256: str
    _pages: dict = field(repr=False)

    def page(self, index):
        return self._pages.get(index)

    def __bool__(self):
        return bool(self._pages)


def _member(archive, ending, limit):
    choices = [i for i in archive.infolist() if i.filename.endswith(ending)]
    if len(choices) != 1:
        raise ValueError("MinerU 结果成员不唯一。")
    item = choices[0]
    if (len(PurePosixPath(item.filename).parts) != 1 or "\\" in item.filename
            or not 0 < item.file_size <= limit or item.is_dir()):
        raise ValueError("MinerU 结果成员路径或大小不符合要求。")
    with archive.open(item) as stream:
        data = stream.read(limit + 1)
    if len(data) != item.file_size or len(data) > limit:
        raise ValueError("MinerU 结果成员大小不一致。")
    return data


def _same_pixels(before, after):
    if before.rotation != after.rotation or before.rect != after.rect:
        return False
    if before.rect.get_area() > _MAX_PAGE_PIXELS:
        return False
    import pymupdf as fitz
    first = before.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
    second = after.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
    return (first.width, first.height, first.samples) == (second.width, second.height, second.samples)


def _box(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1000 for x in value)
            or value[0] >= value[2] or value[1] >= value[3]
            or (value[2]-value[0])*(value[3]-value[1]) > 450_000):
        raise ValueError("MinerU 图表未提供有效的原页坐标。")
    return value


def _algorithm_text(row):
    # The service may classify a printed solution with aligned math as an
    # algorithm. Read its one plain presentation wrapper as source text.
    match = _ALGORITHM.fullmatch(str(row.get("code_body") or ""))
    if row.get("sub_type") != "algorithm" or not match or re.search(r"</?[A-Za-z]+\b[^<>]*>", match[1]):
        raise ValueError("MinerU 未知正文块需要按原页重新识别。")
    return unescape(match[1]).strip()


def _separate_choices(value):
    from mathbank.content_locks import _formulas, markdown_literal_ranges

    lines = []
    for line in value.splitlines():
        if not re.match(r"\s*A[.．、]", line):
            lines.append(line)
            continue
        protected = markdown_literal_ranges(line)
        protected += [(span.start, span.end) for span in _formulas(line, literal_ranges=protected)]
        options = [m for m in _SOURCE_CHOICE.finditer(line)
                   if not any(a <= m.start() < b for a, b in protected)]
        if len(options) > 1 and "".join(m[1] for m in options) == "ABCD"[:len(options)]:
            for marker in reversed(options[1:]):
                line = line[:marker.start()].rstrip() + "\n" + line[marker.start():]
        lines.append(line)
    return "\n".join(lines)


def _page_candidate(rows, page):
    chunks = []
    figures = []
    possible_tables = None
    for r in rows:
        kind = r.get("type")
        if kind in _PAGE_FURNITURE:
            continue
        if kind in _PLAIN_TYPES:
            value = r.get("text")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("MinerU 正文块为空。")
            level = r.get("text_level")
            if kind == "text" and type(level) is int and 1 <= level <= 6:
                value = "#" * level + " " + value.strip()
            chunks.append(_separate_choices(value.strip()))
            continue
        if kind == "code":
            chunks.append(_algorithm_text(r))
            continue
        if kind not in ("image", "table") or len(figures) >= _MAX_GRAPHICS:
            raise ValueError("MinerU 未知或过多图文块需要按原页重新识别。")
        bounds = _box(r.get("bbox"))
        tag = f"图{len(figures) + 1}"
        # No untrusted markup or archive image path enters the task. Cropping
        # always uses our own uploaded original and these verified coordinates.
        prefix = ""
        if kind == "table":
            if possible_tables is None:
                possible_tables = find_choice_grids(page)
            matches = [item for item in possible_tables
                       if max(abs(a-b) for a,b in zip(bounds, item["bbox"])) <= 15]
            if len(matches) == 1:
                label = matches[0]["option"]
                previous = chunks[-1] if chunks else ""
                if not re.search(rf"(?m)^\s*{label}[.．、]\s*$",previous):
                    prefix = label + ". "
        chunks.append(prefix + f"[插图待补: {tag}]")
        for field in (f"{kind}_caption", f"{kind}_footnote"):
            values = r.get(field, [])
            if not isinstance(values, list) or len(values) > 12 or any(not isinstance(x, str) for x in values):
                raise ValueError("MinerU 图注结构无效。")
            chunks.extend(x for x in values if x.strip())
        figures.append({"slot": tag, "bbox": dict(zip(("left","top","right","bottom"),bounds)),
                        "candidate_ids": [], "review_required": True,
                        "review_reason": "云端版面图框，请按原页检查图表和归属。"})
    markdown = "\n\n".join(chunks)
    if not markdown.strip() or len(markdown) > _MAX_TEXT or _ASSET_LIKE.search(markdown):
        return None
    return {"markdown": markdown, "figures": figures, "ignored_candidates": [],
            "page_complete": True, "warnings": []}


def load_mineru_pdf_source(original, bundle, *, check_cancelled=lambda: None):
    """Bind indexed OCR blocks to the upload by matching per-page PDF pixels."""
    if type(original) is not bytes or type(bundle) is not bytes or not (
            0 < len(original) <= _MAX_DOCUMENT_BYTES and 0 < len(bundle) <= _MAX_ARCHIVE_BYTES):
        raise ValueError("MinerU 文档或结果包大小无效。")
    import pymupdf as fitz
    try:
        with ZipFile(BytesIO(bundle)) as archive:
            source_pdf = _member(archive, "_origin.pdf", _MAX_DOCUMENT_BYTES)
            blocks = json.loads(_member(archive, "_content_list.json", _MAX_LIST_BYTES))
        if not isinstance(blocks, list) or not 0 < len(blocks) <= _MAX_BLOCKS:
            raise ValueError("MinerU 内容列表无效。")
        grouped = defaultdict(list)
        with fitz.open(stream=original, filetype="pdf") as source, fitz.open(stream=source_pdf, filetype="pdf") as converted:
            if len(source) != len(converted) or not 0 < len(source) <= 80:
                raise ValueError("MinerU 结果页数与本地 PDF 不一致。")
            for row in blocks:
                index = row.get("page_idx") if isinstance(row, dict) else None
                if type(index) is not int or not 0 <= index < len(source):
                    raise ValueError("MinerU 结果含无效物理页码。")
                grouped[index].append(row)
            pages = {}
            for index in grouped:
                check_cancelled()
                page = source[index]
                if not _same_pixels(page, converted[index]):
                    raise ValueError("MinerU 返回的原件像素与本地 PDF 不一致。")
                try:
                    candidate = _page_candidate(grouped[index], page)
                except (TypeError, ValueError, RuntimeError):
                    continue
                if candidate:
                    pages[index] = candidate
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        raise ValueError("无法安全读取 MinerU 结果包。") from exc
    return MineruPDFSource(hashlib.sha256(original).hexdigest(), hashlib.sha256(bundle).hexdigest(),pages)
