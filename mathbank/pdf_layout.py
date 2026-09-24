"""Local PDF figure candidates, bounded crops, and exact source anchors.

All public rectangles use 0..1000 coordinates on the *visible, rotated* page.
Candidates are evidence for layout review, never a claim that a figure belongs
to a particular question. Files are owned and registered by the calling task.
"""

from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any
import uuid


MAX_CANDIDATES = 80
MAX_DRAWINGS = 3000
MAX_TEXT_BLOCKS = 500
MAX_TEXT_CHARS = 30000
MAX_FIGURE_AREA_RATIO = 0.80
MAX_CROP_SIDE = 2400
MAX_CROP_PIXELS = 4_000_000
CROP_DPI = 200


def normalize_model_bbox(value) -> list:
    """Normalize named model coordinates or a legacy XYXY list, without guessing.

    Geometric validation belongs to the caller's existing checker. In
    particular, a legacy array is never silently interpreted as YXYX.
    """
    keys = ("left", "top", "right", "bottom")
    if isinstance(value, dict) and set(value) == set(keys):
        coordinates = [value[key] for key in keys]
    elif isinstance(value, list) and len(value) == 4:
        coordinates = list(value)
    else:
        raise ValueError("PDF 模型图框必须是 left/top/right/bottom 命名对象或兼容的四项坐标列表。")
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) for number in coordinates):
        raise ValueError("PDF 模型图框的四个坐标必须是数值。")
    return coordinates


_MATH_ENVIRONMENTS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*",
    "gather", "gather*", "multline", "multline*", "eqnarray", "eqnarray*",
    "displaymath", "math", "aligned", "alignedat", "gathered", "split",
    "array", "cases", "matrix", "pmatrix", "bmatrix", "Bmatrix",
    "vmatrix", "Vmatrix", "smallmatrix",
}
_LITERAL_ENVIRONMENTS = {"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "tikzpicture"}
_ENV_TOKEN = re.compile(r"\\(begin|end)\s*\{([^{}\n]+)\}")
_CONTROL_WORD = re.compile(r"\\(?:[A-Za-z@]+\*?|[^A-Za-z\s])")
_LOCAL_IMAGE = re.compile(r"/static/(?:uploads|test_uploads)/(?:tmp/)?[A-Za-z0-9_-][A-Za-z0-9_.-]*\.png")
_HTML_START = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s|/?>)")
_HTML_TAG = re.compile(r"<(?:[^\"'<>]|\"[^\"]*\"|'[^']*')*>")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_MATH_MACRO_ARGUMENTS = {
    name: count
    for count, names in (
        (2, ("frac", "dfrac", "tfrac", "cfrac", "sfrac", "binom", "dbinom", "tbinom")),
        (1, ("sqrt", "vec", "overline", "underline", "widehat", "widetilde",
             "hat", "tilde", "bar", "dot", "ddot", "overrightarrow", "overleftarrow",
             "overleftrightarrow", "overbrace", "underbrace", "boldsymbol", "mathbf",
             "mathrm", "mathit", "mathsf", "mathbb", "mathcal", "mathfrak",
             "wideparen", "overparen", "overarc", "widearc")),
    )
    for name in names
}


def _page_rect(page):
    import pymupdf as fitz

    rect = fitz.Rect(page.rect)
    if not all(math.isfinite(value) for value in rect) or rect.is_empty or rect.is_infinite:
        raise ValueError("PDF 页面尺寸无效。")
    return rect


def _display_rect(page, rectangle):
    """PyMuPDF extraction is unrotated and already relative to the CropBox."""
    import pymupdf as fitz

    rect = fitz.Rect(rectangle)
    if not all(math.isfinite(value) for value in rect) or rect.is_empty or rect.is_infinite:
        return None
    rect = (rect * page.rotation_matrix) & _page_rect(page)
    return None if rect.is_empty or rect.is_infinite else rect


def _normalized(rect, page_rect) -> list[float]:
    return [
        round((rect.x0 - page_rect.x0) / page_rect.width * 1000, 4),
        round((rect.y0 - page_rect.y0) / page_rect.height * 1000, 4),
        round((rect.x1 - page_rect.x0) / page_rect.width * 1000, 4),
        round((rect.y1 - page_rect.y0) / page_rect.height * 1000, 4),
    ]


def inspect_pdf_page(page, page_index: int) -> dict[str, Any]:
    """Return visible raster occurrences, vector clusters and native text boxes.

    Reused image resources retain every displayed occurrence. A near-full-page
    image triggers visual review but is not advertised as an independent figure.
    Tables and vector diagrams deliberately remain candidates for the vision
    stage to distinguish. Truncation or extraction failures stay explicit.
    """
    if isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0:
        raise ValueError("PDF 页码无效。")
    rect = _page_rect(page)
    warnings: list[str] = []
    raw_candidates: list[tuple[str, Any]] = []
    full_page_image = False

    try:
        for item in page.get_image_info():
            image_rect = _display_rect(page, item["bbox"])
            if image_rect is None:
                continue
            if image_rect.get_area() / rect.get_area() >= MAX_FIGURE_AREA_RATIO:
                full_page_image = True
                continue
            if min(image_rect.width, image_rect.height) >= 3 and image_rect.get_area() >= 24:
                raw_candidates.append(("raster", image_rect))
    except Exception:
        warnings.append("原生图片位置提取不完整，请对照原页核对。")

    try:
        drawings = page.get_drawings()
        if len(drawings) > MAX_DRAWINGS:
            warnings.append("本页矢量对象过多，已跳过自动聚类，请使用原页核对插图。")
        else:
            # A page border otherwise connects every nearby diagram into one
            # huge cluster. Remove obvious frames before clustering, not after.
            usable = []
            for drawing in drawings:
                drawing_rect = _display_rect(page, drawing["rect"])
                if drawing_rect is not None and (
                    drawing_rect.width >= rect.width * 0.95
                    and drawing_rect.height >= rect.height * 0.95
                ):
                    continue
                usable.append(drawing)
            for cluster in page.cluster_drawings(drawings=usable):
                cluster_rect = _display_rect(page, cluster)
                if cluster_rect is None:
                    continue
                if min(cluster_rect.width, cluster_rect.height) < 4 or cluster_rect.get_area() < 36:
                    continue
                if cluster_rect.get_area() / rect.get_area() >= MAX_FIGURE_AREA_RATIO:
                    warnings.append("存在覆盖大部分页面的矢量结构，请核对其插图或表格范围。")
                    continue
                raw_candidates.append(("vector", cluster_rect))
    except Exception:
        warnings.append("矢量插图位置提取不完整，请对照原页核对。")

    # Keep the order deterministic, without deduplicating reused resources or
    # promising that this physical top-to-bottom order is semantic reading order.
    raw_candidates.sort(key=lambda value: (value[1].y0, value[1].x0, value[0], value[1].y1, value[1].x1))
    if len(raw_candidates) > MAX_CANDIDATES:
        warnings.append(f"本页插图候选超过 {MAX_CANDIDATES} 个，未显示的区域仍需核对。")
    counts: dict[str, int] = {}
    candidates = []
    for kind, candidate_rect in raw_candidates[:MAX_CANDIDATES]:
        counts[kind] = counts.get(kind, 0) + 1
        candidates.append({
            "id": f"p{page_index + 1}_{kind}_{counts[kind]:03d}",
            "bbox": _normalized(candidate_rect, rect),
            "type": kind,
        })

    text_blocks = []
    text_chars = 0
    try:
        import pymupdf as fitz

        flags = fitz.TEXTFLAGS_BLOCKS & ~fitz.TEXT_PRESERVE_IMAGES
        for block in page.get_text("blocks", flags=flags, sort=True):
            if len(block) < 7 or block[6] != 0 or not str(block[4]).strip():
                continue
            text_rect = _display_rect(page, block[:4])
            if text_rect is None:
                continue
            text = str(block[4])
            if len(text_blocks) >= MAX_TEXT_BLOCKS or text_chars + len(text) > MAX_TEXT_CHARS:
                warnings.append("本页文字位置清单已截断，原始正文仍由文字提取链路保留。")
                break
            text_chars += len(text)
            text_blocks.append({"bbox": _normalized(text_rect, rect), "text": text})
    except Exception:
        warnings.append("本页文字坐标提取不完整，请对照原页核对。")

    if full_page_image:
        warnings.append("检测到接近整页的图片，须从原页识别独立插图，不能把整页当作配图。")
    return {
        "width": rect.width,
        "height": rect.height,
        "page_index": page_index,
        "candidates": candidates,
        "text_blocks": text_blocks,
        "needs_visual": bool(candidates or full_page_image or warnings),
        "full_page_image": full_page_image,
        "warnings": warnings,
    }


def _validated_bbox(bbox) -> tuple[float, float, float, float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("插图区域必须包含四个 0–1000 坐标。")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox):
        raise ValueError("插图区域坐标必须是有限数字。")
    values = tuple(float(value) for value in bbox)
    if any(not math.isfinite(value) or value < 0 or value > 1000 for value in values):
        raise ValueError("插图区域越出页面，或包含非有限坐标。")
    x0, y0, x1, y1 = values
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ValueError("插图区域为空、过小或坐标次序不正确。")
    if (x1 - x0) * (y1 - y0) > MAX_FIGURE_AREA_RATIO * 1_000_000:
        raise ValueError("插图区域覆盖过多页面，请缩小到独立插图。")
    return values


def refine_figure_bbox(page_info: dict, bbox, candidate_ids: list[str]) -> dict[str, Any]:
    """Repair small clipping errors using intersecting, local native evidence.

    This never searches nearby text or turns an incomplete half-figure into a
    complete candidate. Every coverage/growth test uses the *original* model
    box, so a sequence of individually small repairs cannot grow without bound.
    Invalid/unknown candidate references preserve that box for explicit review.
    """
    original = _validated_bbox(bbox)
    current = original
    warnings: list[str] = []

    def evidence_box(value):
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        if any(isinstance(number, bool) or not isinstance(number, (int, float)) for number in value):
            return None
        result = tuple(float(number) for number in value)
        if any(not math.isfinite(number) or not 0 <= number <= 1000 for number in result):
            return None
        return result if result[0] < result[2] and result[1] < result[3] else None

    def area(rect):
        return (rect[2] - rect[0]) * (rect[3] - rect[1])

    def overlap(first, second):
        return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
            0, min(first[3], second[3]) - max(first[1], second[1])
        )

    def union(first, second):
        return (min(first[0], second[0]), min(first[1], second[1]),
                max(first[2], second[2]), max(first[3], second[3]))

    def bounded(proposed):
        return (
            area(proposed) <= area(original) * 1.5 + 1e-6
            and area(proposed) <= MAX_FIGURE_AREA_RATIO * 1_000_000
            and proposed[2] - proposed[0] <= (original[2] - original[0]) * 1.4 + 1e-6
            and proposed[3] - proposed[1] <= (original[3] - original[1]) * 1.4 + 1e-6
        )

    if not isinstance(page_info, dict):
        raise ValueError("PDF 页面位置证据无效。")
    candidates = {}
    for candidate in page_info.get("candidates", []):
        if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
            candidates.setdefault(candidate["id"], []).append(candidate)
    if not isinstance(candidate_ids, list) or any(not isinstance(identifier, str) for identifier in candidate_ids):
        return {"bbox": list(original), "warnings": ["插图候选标识无效，未自动扩大裁框。"]}

    # First validate all references. Padding must not accidentally make a tiny
    # half-covered or unknown candidate appear to pass a later coverage check.
    named_boxes = []
    invalid_reference = False
    for identifier in dict.fromkeys(candidate_ids):
        records = candidates.get(identifier, [])
        candidate_box = evidence_box(records[0].get("bbox")) if len(records) == 1 else None
        if candidate_box is None:
            warnings.append(f"候选 {identifier} 不存在、标识不唯一或坐标无效，未自动扩大裁框。")
            invalid_reference = True
        elif overlap(original, candidate_box) / area(candidate_box) < 0.75:
            warnings.append(f"裁框未覆盖候选 {identifier} 的 75%，不能自动补成完整插图。")
            invalid_reference = True
        else:
            named_boxes.append((identifier, candidate_box))
    if invalid_reference:
        return {"bbox": list(original), "warnings": warnings}

    texts = []
    option_label_boxes = set()
    for block in page_info.get("text_blocks", []):
        if not isinstance(block, dict):
            continue
        block_box = evidence_box(block.get("bbox"))
        text = str(block.get("text") or "").strip()
        if block_box is None or not text:
            continue
        # A./B./C./D. are native option markers outside an embedded image,
        # sometimes merged into one text block across two columns. They must
        # not be recruited as clipped diagram annotations such as vertex A.
        if re.fullmatch(r"(?:[A-H]\s*[.．、:：]\s*)+", text):
            option_label_boxes.add(tuple(block_box))
            texts.append((block_box, False))
            continue
        # Small labels may be Latin letters, dimensions, ticks, or short Chinese
        # chart labels. Recognizable question/prose fragments remain a barrier.
        prose = bool(
            len(text) > 24 or block_box[3] - block_box[1] > 80
            or re.search(r"[。！？；]|^\s*\d{1,3}[.．、]\s*\S", text)
            or len(re.findall(r"[\u4e00-\u9fff]", text)) > 4
            or re.match(r"(?:find|calculate|given|determine|prove|solve|show|choose|which)\b", text, re.IGNORECASE)
        )
        texts.append((block_box, prose))
        if prose and overlap(original, block_box) > 0:
            warnings.append("裁框与原生正文相交，未按正文范围扩大，请核对是否包含题干或遗漏图内说明。")

    def crosses_prose(proposed):
        return any(prose and overlap(proposed, block_box) > overlap(original, block_box) + 1e-6
                   for block_box, prose in texts)

    for identifier, candidate_box in named_boxes:
        proposed = union(current, candidate_box)
        if proposed == current:
            continue
        if not bounded(proposed):
            warnings.append(f"补全候选 {identifier} 超出有限扩张范围，未自动补全，请核对裁框。")
        elif crosses_prose(proposed):
            warnings.append(f"补全候选 {identifier} 会跨入原生正文，未自动补全，请核对裁框。")
        else:
            current = proposed
            warnings.append(f"已按原生候选 {identifier} 补全被裁去的小幅边缘。")

    label_repairs = 0
    for block_box, prose in texts:
        # Test against original, not current: do not recruit an adjacent label
        # merely because an earlier expansion brought it into the rectangle.
        if prose or tuple(block_box) in option_label_boxes or overlap(original, block_box) <= 0:
            continue
        proposed = union(current, block_box)
        if proposed == current:
            continue
        extra_x = max(0, original[0] - proposed[0], proposed[2] - original[2])
        extra_y = max(0, original[1] - proposed[1], proposed[3] - original[3])
        if (
            not bounded(proposed)
            or extra_x > min(32, (original[2] - original[0]) * 0.2)
            or extra_y > min(32, (original[3] - original[1]) * 0.2)
            or crosses_prose(proposed)
        ):
            warnings.append("相交的短标签超出局部补边范围或邻接正文，未自动扩大，请核对图内标注。")
        else:
            current = proposed
            label_repairs += 1
    if label_repairs:
        warnings.append(f"已补全 {label_repairs} 处与原裁框相交的原生短标签边界。")

    width, height = page_info.get("width"), page_info.get("height")
    valid_size = all(
        not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        for value in (width, height)
    )
    if valid_size:
        margin_x, margin_y = 1500 / width, 1500 / height  # 1.5 PDF points
        padded = (max(0, current[0] - margin_x), max(0, current[1] - margin_y),
                  min(1000, current[2] + margin_x), min(1000, current[3] + margin_y))
        touches_other_text = any(overlap(original, block_box) <= 0 and overlap(padded, block_box) > 0
                                 for block_box, _ in texts)
        if bounded(padded) and not crosses_prose(padded) and not touches_other_text:
            current = padded
        else:
            warnings.append("小边距会超过扩张上限或碰到其他文字，已保留当前边界。")
    else:
        warnings.append("页面尺寸无效，未添加裁图边距。")
    return {"bbox": [round(value, 4) for value in current], "warnings": list(dict.fromkeys(warnings))}


def crop_pdf_figure(
    page,
    bbox,
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
) -> str:
    """Render the visible figure region, including text, to a bounded PNG.

    The caller supplies an absolute trusted output directory and registers the
    returned URL for cleanup immediately. No paths are accepted from the model.
    """
    import pymupdf as fitz

    x0, y0, x1, y1 = _validated_bbox(bbox)
    page_rect = _page_rect(page)
    output_dir = Path(output_dir)
    if not output_dir.is_absolute() or output_dir.is_symlink():
        raise ValueError("插图输出目录必须是可信的绝对目录。")
    if not isinstance(asset_prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", asset_prefix):
        raise ValueError("插图文件名前缀无效。")
    if not isinstance(url_prefix, str) or not re.fullmatch(r"/static/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+/?", url_prefix):
        raise ValueError("插图 URL 前缀必须属于本地静态资源目录。")
    clip = fitz.Rect(
        page_rect.x0 + x0 / 1000 * page_rect.width,
        page_rect.y0 + y0 / 1000 * page_rect.height,
        page_rect.x0 + x1 / 1000 * page_rect.width,
        page_rect.y0 + y1 / 1000 * page_rect.height,
    )
    # get_pixmap's display-list clip follows the rotated visible page, unlike
    # extraction boxes. Do not derotate this rectangle a second time.
    scale = min(
        CROP_DPI / 72,
        (MAX_CROP_SIDE - 2) / clip.width,
        (MAX_CROP_SIDE - 2) / clip.height,
        math.sqrt((MAX_CROP_PIXELS - 4 * MAX_CROP_SIDE) / clip.get_area()),
    )
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, alpha=False)
    if min(pixmap.width, pixmap.height) < 2 or max(pixmap.width, pixmap.height) > MAX_CROP_SIDE or pixmap.width * pixmap.height > MAX_CROP_PIXELS:
        raise ValueError("插图渲染尺寸无效或超过安全上限。")
    png = pixmap.tobytes("png")
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{asset_prefix}_{uuid.uuid4().hex}.png"
    destination = output_dir / filename
    created = False
    try:
        with destination.open("xb") as handle:
            created = True
            handle.write(png)
    except Exception:
        if created and destination.exists() and not destination.is_symlink():
            destination.unlink(missing_ok=True)
        raise
    return f"{url_prefix.rstrip('/')}/{filename}"


def _escaped(text: str, index: int) -> bool:
    count = 0
    while index > 0 and text[index - 1] == "\\":
        count += 1
        index -= 1
    return bool(count % 2)


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(text)):
        if _escaped(text, index):
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text) + 1


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Protect syntax interiors while leaving choices/table cell text usable."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        end = None
        # Fences, inline code and unclosed code are treated conservatively.
        if text[cursor] in "`~" and not _escaped(text, cursor):
            run = re.match(r"(`+|~+)", text[cursor:]).group(0)
            line_start = text.rfind("\n", 0, cursor) + 1
            fence = len(run) >= 3 and not text[line_start:cursor].strip() and cursor - line_start <= 3
            if fence:
                pattern = re.compile(r"^[ \t]{0,3}" + re.escape(run[0]) + "{" + str(len(run)) + r",}[ \t]*(?:\n|$)", re.MULTILINE)
                first_newline = text.find("\n", cursor)
                match = pattern.search(text, first_newline + 1) if first_newline >= 0 else None
                end = match.end() if match else len(text) + 1
            elif run[0] == "`":
                pattern = re.compile(r"(?<!`)" + re.escape(run) + r"(?!`)")
                match = pattern.search(text, cursor + len(run))
                end = match.end() if match else len(text) + 1
        if end is None and text.startswith("![", cursor) and not _escaped(text, cursor):
            alt_end = _balanced_end(text, cursor + 1, "[", "]")
            end = alt_end
            if alt_end < len(text) and text[alt_end] in "([":
                opening = text[alt_end]
                end = _balanced_end(text, alt_end, opening, ")" if opening == "(" else "]")
        if end is None and text.startswith("<!--", cursor):
            close = text.find("-->", cursor + 4)
            end = close + 3 if close >= 0 else len(text) + 1
        if end is None and text[cursor] == "<" and _HTML_START.match(text, cursor):
            match = _HTML_TAG.match(text, cursor)
            end = match.end() if match else len(text) + 1
        if end is None and not _escaped(text, cursor):
            delimiter = next((value for value in ("$$", "$", r"\(", r"\[") if text.startswith(value, cursor)), None)
            if delimiter:
                closing = {r"\(": r"\)", r"\[": r"\]"}.get(delimiter, delimiter)
                close = text.find(closing, cursor + len(delimiter))
                while close >= 0 and _escaped(text, close):
                    close = text.find(closing, close + len(closing))
                end = close + len(closing) if close >= 0 else len(text) + 1
        if end is None and text[cursor] == "\\" and not _escaped(text, cursor):
            environment = _ENV_TOKEN.match(text, cursor)
            if environment:
                name = environment.group(2)
                end = environment.end()
                if environment.group(1) == "begin" and name in _MATH_ENVIRONMENTS | _LITERAL_ENVIRONMENTS:
                    depth = 1
                    end = len(text) + 1
                    for token in _ENV_TOKEN.finditer(text, environment.end()):
                        if token.group(2) != name or _escaped(text, token.start()):
                            continue
                        depth += 1 if token.group(1) == "begin" else -1
                        if depth == 0:
                            end = token.end()
                            break
                elif environment.group(1) == "begin":
                    # A table's column specification is syntax; its later cell
                    # bodies are normal anchorable source, including multicolumn.
                    probe = end
                    while probe < len(text) and text[probe].isspace():
                        probe += 1
                    while probe < len(text) and text[probe] in "[{":
                        opening = text[probe]
                        probe = _balanced_end(text, probe, opening, "]" if opening == "[" else "}")
                        end = probe
                        while probe < len(text) and text[probe].isspace():
                            probe += 1
            else:
                command = _CONTROL_WORD.match(text, cursor)
                if command:
                    end = command.end()
                    math_arguments = _MATH_MACRO_ARGUMENTS.get(command.group(0)[1:])
                    if math_arguments:
                        # Even imperfect native text may contain bare formula
                        # macros. Preserve their arguments without blocking
                        # ordinary text-bearing commands such as multicolumn.
                        probe = end
                        while probe < len(text) and text[probe].isspace():
                            probe += 1
                        if probe < len(text) and text[probe] == "[":
                            probe = _balanced_end(text, probe, "[", "]")
                            end = probe
                        for _ in range(math_arguments):
                            while probe < len(text) and text[probe].isspace():
                                probe += 1
                            if probe >= len(text) or text[probe] != "{":
                                break
                            probe = _balanced_end(text, probe, "{", "}")
                            end = probe
                    elif command.group(0) in (r"\verb", r"\verb*") and end < len(text):
                        close = text.find(text[end], end + 1)
                        end = close + 1 if close >= 0 else len(text) + 1
                    elif command.group(0) == r"\includegraphics":
                        probe = end
                        while probe < len(text) and text[probe].isspace():
                            probe += 1
                        if probe < len(text) and text[probe] == "[":
                            probe = _balanced_end(text, probe, "[", "]")
                        while probe < len(text) and text[probe].isspace():
                            probe += 1
                        if probe < len(text) and text[probe] == "{":
                            end = _balanced_end(text, probe, "{", "}")
        if end is not None:
            spans.append((cursor, end))
            cursor = end
        else:
            cursor += 1
    return spans


def _occurrences(text: str, fragment: str):
    start = 0
    while True:
        position = text.find(fragment, start)
        if position < 0:
            return
        yield position
        start = position + 1


def _anchor_positions(text: str, before: str, after: str) -> list[int]:
    if not before:
        positions = []
        for position in _occurrences(text, after):
            positions.append(position)
            if len(positions) == 2:
                break
        return positions
    if not after:
        positions = []
        for position in _occurrences(text, before):
            positions.append(position + len(before))
            if len(positions) == 2:
                break
        return positions
    positions = []
    for start in _occurrences(text, before):
        position = start + len(before)
        gap_end = position
        while gap_end < len(text) and text[gap_end].isspace():
            gap_end += 1
        # Whitespace inside an exact after-fragment remains significant too.
        after_start = text.find(after, position)
        if position <= after_start <= gap_end:
            positions.append(position)
            if len(positions) == 2:
                break
    return positions


def _inside_markdown_table(text: str, position: int) -> bool:
    """A new line inside a pipe-table cell would silently destroy its row."""
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end < 0:
        line_end = len(text)
    if "|" not in text[line_start:line_end]:
        return False
    # The separator may be above this row or below the header. Blank/non-table
    # lines end the block, preventing a distant table from changing an anchor.
    block_start, block_end = line_start, line_end
    while block_start > 0:
        previous_start = text.rfind("\n", 0, block_start - 1) + 1
        if "|" not in text[previous_start:block_start - 1]:
            break
        block_start = previous_start
    while block_end < len(text):
        next_end = text.find("\n", block_end + 1)
        if next_end < 0:
            next_end = len(text)
        if "|" not in text[block_end + 1:next_end]:
            break
        block_end = next_end
    return any(_TABLE_SEPARATOR.fullmatch(line) for line in text[block_start:block_end].splitlines())


def apply_figure_anchors(markdown: str, figures: list[dict]) -> dict[str, Any]:
    """Insert figure references only at unique, exact, syntactically safe anchors.

    Resolve all anchors against the unchanged source so earlier insertions do
    not invalidate later ones. Preserve multiple displays at a shared anchor in
    input order. Review flags and model confidence cannot bypass validation.
    """
    if not isinstance(markdown, str) or not isinstance(figures, list):
        raise ValueError("插图归位需要原始正文和插图数组。")
    protected = _protected_spans(markdown)
    insertions: dict[int, list[str]] = {}
    attached: list[str] = []
    unmatched: list[dict] = []
    for figure in figures:
        reason = ""
        if not isinstance(figure, dict):
            unmatched.append({"reason": "插图信息不是对象。"})
            continue
        before = figure.get("anchor_before", "")
        after = figure.get("anchor_after", "")
        identifier = figure.get("id")
        image_path = figure.get("image_path")
        position = None
        if not isinstance(identifier, str) or not identifier.strip():
            reason = "插图缺少稳定标识。"
        elif not isinstance(image_path, str) or not _LOCAL_IMAGE.fullmatch(image_path):
            reason = "插图路径不是已生成的本地 PNG 资源。"
        elif not isinstance(before, str) or not isinstance(after, str) or not (before.strip() or after.strip()):
            reason = "插图缺少非空的原文位置片段。"
        else:
            positions = _anchor_positions(markdown, before, after)
            if not positions:
                reason = "原文片段未精确匹配，或两个片段之间并非只有空白。"
            elif len(positions) != 1:
                reason = "原文片段对应多个位置，无法唯一归位。"
            else:
                position = positions[0]
                if any(start < position < end for start, end in protected):
                    reason = "插入点位于公式、代码、标签、控制词或已有图片内部。"
        if reason:
            unmatched.append({**figure, "reason": reason})
            continue
        spacing = " " if _inside_markdown_table(markdown, position) else "\n"
        insertions.setdefault(position, []).append(f"{spacing}![插图]({image_path}){spacing}")
        attached.append(identifier)

    result = markdown
    for position in sorted(insertions, reverse=True):
        result = result[:position] + "".join(insertions[position]) + result[position:]
    return {"markdown": result, "attached": attached, "unmatched": unmatched}
