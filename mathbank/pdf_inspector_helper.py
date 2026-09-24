# mathbank/pdf_inspector_helper.py
# -*- coding: utf-8 -*-
"""pdf-inspector 的逐页 PDF 检查与文本提取适配器。"""

import os
import math
import tempfile
from collections import Counter
from typing import Any, Dict, List, Optional

try:
    import pdf_inspector
    _PDF_INSPECTOR_AVAILABLE = True
except ImportError:
    pdf_inspector = None
    _PDF_INSPECTOR_AVAILABLE = False

try:
    import pymupdf as fitz
    _FITZ_AVAILABLE = True
except ImportError:
    fitz = None
    _FITZ_AVAILABLE = False


import re


def is_pdf_inspector_available() -> bool:
    """检查当前 Python 环境是否安装了 pdf-inspector。"""
    return _PDF_INSPECTOR_AVAILABLE


_TABLE_DIVIDER = re.compile(r":?-{3,}:?")
_QUESTION_IN_CELL = re.compile(r"(?<![\w.])(\d{1,3})[.．、]\s*(?=[\u4e00-\u9fffA-Za-z\\])")
_SUBQUESTION_START = re.compile(r"(?<!\w)[(（]([1-9])[)）]\s*(?=[\u4e00-\u9fff])")
_PRIVATE_OR_MISSING_GLYPH = re.compile(r"[\ufffd\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")
_BRACE_COMPONENT_GROUPS = (
    frozenset("\uf8f1\uf8f2\uf8f3"), frozenset("\uf8fc\uf8fd\uf8fe"),
    frozenset("⎧⎨⎩"), frozenset("⎫⎬⎭"),
)
_DETACHED_RELATION_NEGATION = re.compile(
    # A stroke already combining with a Latin letter is not evidence that a
    # following relation lost its negation (for example o + U+0338).
    r"(?<![A-Za-zÀ-ÖØ-öø-ÿ])\u0338[ \t]*[∈⊂⊆⊃⊇=<>≤≥]"
    r"|(?<=[\w)}\]])[ \t]*/[ \t]*[∈⊂⊆⊃⊇]"
)
_MATH_FONT_GLYPHS = "àáâãäèéêëìíîïôöË"
_ISOLATED_FONT_GLYPH = re.compile(
    r"(?<![A-Za-zÀ-ÖØ-öø-ÿ])[" + _MATH_FONT_GLYPHS + r"](?![A-Za-zÀ-ÖØ-öø-ÿ])"
)


def _table_cells(line: str) -> list[str]:
    """Keep empty cells; escaped pipes are cell text, not column boundaries."""
    line = line.strip()
    if "|" not in line:
        return []
    cells = re.split(r"(?<!\\)\|", line)
    if not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.strip() for cell in cells]


def _has_scrambled_sparse_table(markdown: str) -> bool:
    """Reject a wide table only with question-order or prose-fragment evidence."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        separator = _table_cells(line)
        if len(separator) < 3 or not all(_TABLE_DIVIDER.fullmatch(cell) for cell in separator):
            continue
        if index == 0 or len(_table_cells(lines[index - 1])) < 2:
            continue
        rows = []
        for data_line in lines[index + 1:]:
            cells = _table_cells(data_line)
            if len(cells) < 2:
                break
            rows.append(cells)
        plain_rows = [[re.sub(r"<[^>]*>|[*_]", "", cell) for cell in row] for row in rows]
        header = [re.sub(r"<[^>]*>|[*_]", "", cell) for cell in _table_cells(lines[index - 1])]
        nonempty = [cell for row in [header, *plain_rows] for cell in row if cell]
        # Consecutive empty copies of an option label inside one cell are not
        # an ordinary question-per-cell table. They expose rows folded together.
        if any(re.search(r"(?<![A-Za-z])([A-D])[.．、]\s*\1[.．、]", cell) for cell in nonempty):
            return True
        if len(separator) < 8:
            continue  # Other broad-table heuristics retain their old scope.
        # A long paragraph plus many tiny cells is insufficient by itself:
        # require fused subquestion starts, or a new question embedded in the
        # previous question's unfinished sentence. Real comparison/data tables
        # and a whole ordinary question per cell remain native.
        fragmented_prose = (
            sum(len(cell) <= 12 for cell in nonempty) >= 5
            and any(len(re.findall(r"[\u4e00-\u9fff]", cell)) >= 30 for cell in nonempty)
        )
        if fragmented_prose:
            if any(len(set(_SUBQUESTION_START.findall(cell))) >= 3 for cell in nonempty):
                return True
            header_numbers = {int(match.group(1)) for cell in header for match in _QUESTION_IN_CELL.finditer(cell)}
            if header_numbers:
                short_header_questions = [
                    (int(match.group(1)), column)
                    for column, cell in enumerate(header) if len(cell) <= 12
                    for match in _QUESTION_IN_CELL.finditer(cell)
                ]
                for row in plain_rows:
                    for column, cell in enumerate(row):
                        for match in _QUESTION_IN_CELL.finditer(cell):
                            # A short question title becomes a column heading,
                            # while the next question starts in a different
                            # column of its supposed data. This is distinct
                            # from legitimate question tables with stable rows.
                            if any(int(match.group(1)) > number and column != header_column
                                   for number, header_column in short_header_questions):
                                return True
                            prefix = cell[:match.start()].strip()
                            if (int(match.group(1)) > min(header_numbers)
                                    and len(re.findall(r"[\u4e00-\u9fff]", prefix)) >= 4
                                    and not re.search(r"[。；！？.!?;．]", prefix)):
                                return True
        question_positions = []
        for row_index, row in enumerate(plain_rows):
            for column, cell in enumerate(row):
                question_positions.extend(
                    (int(match.group(1)), column, row_index, cell[:match.start()].strip())
                    for match in _QUESTION_IN_CELL.finditer(cell)
                )
        if len(question_positions) < 3 or len({position[1] for position in question_positions}) < 2:
            continue
        cell_count = sum(len(row) for row in rows)
        sparse = bool(cell_count and sum(not cell for row in rows for cell in row) / cell_count >= 0.35)
        for previous, current in zip(question_positions, question_positions[1:]):
            if current[0] >= previous[0]:
                continue
            # Even a relatively dense false table can put Q10 before Q9 in
            # one row, both appended to earlier equation/option fragments.
            # Complete numbered questions in comparison cells do not have
            # those prefixes, so width or reverse numbering alone is not enough.
            fused_row = (fragmented_prose and previous[2] == current[2]
                         and previous[1] != current[1] and previous[3] and current[3])
            if sparse or fused_row:
                return True
    return False


def native_text_quality_reasons(markdown: str) -> list[str]:
    """Return content-free OCR reasons; never guess or repair mathematical glyphs.

    The raw Markdown remains in the page result, while unsafe pages stay out of
    the trusted merged text and image-anchor stage. Width/sparsity alone cannot
    reject real tables, and accents inside ordinary words remain untouched.
    """
    text = str(markdown or "")
    reasons = []
    if not text.strip():
        return ["原生提取未得到有效文字。"]
    if _PRIVATE_OR_MISSING_GLYPH.search(text):
        reasons.append("原生文字含缺字或未解码的私用字体符号。")
    if any(components <= set(text) for components in _BRACE_COMPONENT_GROUPS):
        reasons.append("原生提取含分段括号组件，分支公式的二维结构尚未完整重建。")
    relation_text = re.sub(r"https?://[^\s<>]+|`[^`\n]*`", "", text)
    relation_text = re.sub(r"<[^>]*>|[*_]", "", relation_text)
    if _DETACHED_RELATION_NEGATION.search(relation_text):
        reasons.append("原生提取的否定斜线与关系符分离，需对照原页确认不属于或非包含等条件。")
    if _has_scrambled_sparse_table(text):
        reasons.append("原生正文被错排为宽表，出现题号跨列错序或小问片段混排。")
    for line in text.splitlines():
        plain = re.sub(r"<[^>]*>|[*_]", "", line)
        # PDF Inspector can place the sign, its name and relation in different
        # cells. Check that visible adjacency without rewriting source text.
        if len(_table_cells(line)) >= 2:
            plain = " ".join(_table_cells(plain))
        angle = re.search(r"Ð\s*(?:[A-Z]{2,3}|[1-9])\s*(?:[=<>≤≥]|的(?:度数|大小)|[，,；;。])", plain)
        angle = angle or re.search(r"Ð\s*[A-Z]\s*(?:[=<>≤≥]\s*\d+(?:\.\d+)?\s*°|的(?:度数|大小))", plain)
        angle = angle or re.search(r"(?:平分|角平分线)\s*Ð\s*[A-Z]{1,3}(?![A-Za-z])", plain)
        triangle = re.search(r"(?:在|如图[，,]?|三角形)\s*(?:Rt\s*)?V\s*[A-Z]{3}(?![A-Za-z])\s*(?:中|和|与|[，,。；;])", plain)
        relation_context = bool(re.search(r"[=<>≤≥]|不等式|取值|满足|范围|变量|实数|函数", plain))
        pound_relation = relation_context and re.search(
            r"(?<![A-Za-z])(?:[A-Za-z]\d*|\d+(?:\.\d+)?|\))\s*£\s*(?:[+-]?\d|[A-Za-z](?![A-Za-z]))",
            plain,
        )
        repeated_pound_relation = bool(re.search(r"满足|取值|范围|变量|函数|不等式", plain)) and re.search(
            r"(?:\d|[A-Za-z])\s*£\s*£\s*(?:\d|[A-Za-z])", plain,
        )
        if angle or triangle or pound_relation or repeated_pound_relation:
            reasons.append("原生数学表达式中的角、三角形或不等号存在字体错解迹象。")
            break
    for line in text.splitlines():
        context = re.sub(r"<[^>]*>", "", line)
        math_context = bool(re.search(r"[=<>≤≥∈∉∪∩∞]|\\(?:frac|sqrt|left|right|in)\b", context))
        math_context = math_context or bool(re.search(r"[A-D][.．、].*\d", context))
        if not math_context:
            continue
        if (len(_ISOLATED_FONT_GLYPH.findall(line)) >= 2
                or re.search(r"àáâ|èéê|[ôîöËä]\s*\*{0,2}[A-Za-z0-9][^\n]{0,60}[ôîöËä](?![A-Za-zÀ-ÖØ-öø-ÿ])", line)):
            reasons.append("原生数学表达式含未正确解码的括号、绝对值或其他字体符号。")
            break
    return reasons


def _native_table_position_reasons(markdown: str, page) -> list[str]:
    """Prove cross-question disorder using original PDF positions, not width.

    Real tables and uncertain columns are excluded. A page containing genuine
    data tables is not itself a failure: only a unique, physically misplaced
    question or prose anchor can reject its Markdown baseline.
    """
    if len(markdown) > 60000 or getattr(page, "rotation", 0):
        return []
    lines = markdown.splitlines()
    table_lines = []
    for index, line in enumerate(lines):
        cells = _table_cells(line)
        if len(cells) < 2 or not all(_TABLE_DIVIDER.fullmatch(cell) for cell in cells) or index == 0:
            continue
        table_lines.append(lines[index - 1])
        for body in lines[index + 1:]:
            if len(_table_cells(body)) < 2:
                break
            table_lines.append(body)
    if not table_lines:
        return []
    rows = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            box = line.get("bbox")
            text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
            if (not box or len(box) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
                    or box[3] <= box[1]):
                continue
            rows.append({"text": re.sub(r"\s+", "", text), "box": box})
    if len(rows) > 800 or sum(len(row["text"]) for row in rows) > 60000:
        return []
    headings = []
    for row in rows:
        match = re.match(r"^(\d{1,3})[.．、](?!\d)(?=[\u3400-\u9fffA-Za-z])", row["text"])
        if match:
            headings.append({"number": int(match.group(1)), **row})
    if not headings:
        return []
    tables = []
    finder = getattr(page, "find_tables", None)
    if callable(finder):
        # Use vector-line tables, never the optional learned layout analyzer.
        # Older PyMuPDF has no use_layout option and its finder is line-based.
        try:
            found = finder(use_layout=False)
        except TypeError as exc:
            if "use_layout" not in str(exc):
                raise
            found = finder()
        tables = [table.bbox for table in found.tables]
    def in_real_table(row):
        x, y, _, bottom = row["box"]
        cy = (y + bottom) / 2
        return any(left - 1 <= x <= right + 1 and top - 1 <= cy <= end + 1 for left, top, right, end in tables)
    headings = [heading for heading in headings if not in_real_table(heading)]
    if not headings:
        return []
    height = sorted(row["box"][3] - row["box"][1] for row in rows)[len(rows) // 2]
    xs = [heading["box"][0] for heading in headings]
    if max(xs) - min(xs) > height * 2 or min(xs) > page.rect.width * .35:
        return []  # A question-per-cell or genuine multi-column layout is not proved single-column.
    physical_counts = Counter(heading["number"] for heading in headings)
    plain = re.sub(r"<[^>]*>|[*_]", "", markdown)
    positions = {}
    for match in _QUESTION_IN_CELL.finditer(plain):
        positions.setdefault(int(match.group(1)), []).append(match.start())
    unique = [heading for heading in sorted(headings, key=lambda h: (h["box"][1], h["box"][0]))
              if physical_counts[heading["number"]] == 1 and len(positions.get(heading["number"], [])) == 1]
    if len(unique) >= 2:
        physical = [heading["number"] for heading in unique]
        extracted = sorted(physical, key=lambda number: positions[number][0])
        if physical != extracted:
            return ["原生表格的题号顺序与原页位置不一致，出现跨题混排。"]
    # With only one unambiguous heading, require two separate real tables as
    # additional context. This avoids treating a long cell in a borderless
    # comparison table as an ordinary paragraph crossing a question boundary.
    if len(unique) < 2 and len(tables) < 2:
        return []
    anchors = set()
    table_text = re.sub(r"<[^>]*>|[*_]", "", "\n".join(table_lines))
    for match in re.finditer(r"[\u3400-\u9fff]{8,}", table_text):
        anchors.update((match.group()[:8], match.group()[-8:]))
    if len(anchors) > 256:
        return []
    for anchor in sorted(anchors):
        if plain.count(anchor) != 1:
            continue
        occurrences = [(row, match.start()) for row in rows for match in re.finditer(re.escape(anchor), row["text"])]
        if len(occurrences) != 1:
            continue
        physical_row = occurrences[0][0]
        md_start = plain.index(anchor)
        for heading in unique:
            head_y = heading["box"][1]
            anchor_y = physical_row["box"][1]
            head_start = positions[heading["number"]][0]
            if ((anchor_y < head_y - height * 1.5 and md_start > head_start)
                    or (anchor_y > head_y + height * 1.5 and md_start < head_start)):
                return [f"原生表格把第 {heading['number']} 题附近正文移过了原页题号边界，存在跨题片段混排。"]
    return []


def _native_position_quality_reasons(markdown: str, page_index: int, pdf_path: str | None) -> list[str]:
    if not _FITZ_AVAILABLE or not pdf_path or not any(
        len(cells := _table_cells(line)) >= 2 and all(_TABLE_DIVIDER.fullmatch(cell) for cell in cells)
        for line in markdown.splitlines()
    ):
        return []
    try:
        with fitz.open(pdf_path) as document:
            if not 0 <= page_index < len(document):
                return []
            return _native_table_position_reasons(markdown, document[page_index])
    except Exception:
        return []  # Missing geometry is not fabricated evidence of corruption.


def _has_multiple_inline_formula_images(items: list[Any]) -> bool:
    """Require line/context evidence, not merely a count of embedded images."""
    def bounds(item):
        try:
            result = tuple(float(getattr(item, name)) for name in ("x", "y", "width", "height"))
        except (AttributeError, TypeError, ValueError):
            return None
        return result if all(math.isfinite(value) for value in result) and result[2] > 0 and result[3] > 0 else None

    text_items = [(item, box) for item in items
                  if getattr(item, "item_type", "") == "text"
                  and str(getattr(item, "text", "")).strip() and (box := bounds(item))]
    candidates = set()
    math_context = re.compile(r"函数|方程|等式|不等式|表达式|代数式|关系式|解析式|定义域|值域|向量|数列|集合|矩阵|导数|积分|[=<>≤≥±×÷]")
    picture_context = re.compile(r"图标|图案|会徽|示意图|插图|图片|照片|标志|装饰|色块|图例|表情")
    for item in items:
        if getattr(item, "item_type", "") != "image" or not (box := bounds(item)):
            continue
        x, y, width, height = box
        left, right = [], []
        for text_item, (tx, ty, tw, th) in text_items:
            overlap = min(y + height, ty + th) - max(y, ty)
            if overlap < min(height, th) * 0.6:
                continue
            if 0 <= x - tx - tw <= th * 1.5:
                left.append((x - tx - tw, text_item, (tx, ty, tw, th)))
            if 0 <= tx - x - width <= th * 1.5:
                right.append((tx - x - width, text_item, (tx, ty, tw, th)))
        if not left or not right:
            continue
        _, left_item, left_box = min(left, key=lambda entry: entry[0])
        _, right_item, right_box = min(right, key=lambda entry: entry[0])
        line_height = max(left_box[3], right_box[3])
        if abs(left_box[1] + left_box[3] / 2 - right_box[1] - right_box[3] / 2) > line_height * 0.45:
            continue
        if not (0.65 * line_height <= height <= 2.5 * line_height and 0.45 * line_height <= width <= 30 * line_height):
            continue
        context = str(left_item.text)[-40:] + str(right_item.text)[:40]
        if not math_context.search(context) or picture_context.search(context):
            continue
        if any(max(0, min(x + width, cx + cw) - max(x, cx))
               * max(0, min(y + height, cy + ch) - max(y, cy)) >= 0.8 * min(width * height, cw * ch)
               for cx, cy, cw, ch in candidates):
            continue  # Overlaid image/mask layers are one inline region.
        candidates.add(box)
        if len(candidates) >= 3:
            return True
    return False


def _has_math_formula_loss(
    markdown: str,
    page_index: int = 0,
    pdf_path: Optional[str] = None,
) -> bool:
    """检测提取出的 Markdown 是否存在 Word/MathType 公式转换流失的特征。"""
    text = markdown or ""
    if not text.strip():
        return True

    # 1. 特征 A: 连续连写的空选择题选项 (如 A. B. C. D. 之间无实质表达式)
    if re.search(r"\bA\.\s*B\.\s*C\.\s*D\.", text):
        return True
    if re.search(r"\bA\.\s*[\n\r]+\s*B\.\s*[\n\r]+\s*C\.\s*[\n\r]+\s*D\.", text):
        return True

    # 特征 B: 丢失变量只留分隔逗号的句式 (如 "设 ， 为", "已知 ， ，", "对边分别为 ， ，")
    if re.search(r"(?:设|已知|若|满足)\s*[，,]\s*(?:[，,]\s*)*(?:为|满足|则|已知)", text):
        return True
    if re.search(r"对边分别为\s*[，,]\s*[，,]", text):
        return True
    if re.search(r"使得\s*”\s*是\s*“\s*”\s*的", text):
        return True

    # 2. 特征 C: 三幅以上与正文同高、两侧紧邻文字且处于数学语境的行内图片。
    # 普通配图、装饰与小点不能充当“公式图片”证据；$ 数量也不能证明公式丢失。
    if pdf_path and _PDF_INSPECTOR_AVAILABLE:
        try:
            extract_positions = getattr(pdf_inspector, "extract_text_with_positions", None)
            if callable(extract_positions):
                # Unlike extract_pages_markdown, this API takes one-based pages.
                items = extract_positions(pdf_path, pages=[page_index + 1])
                if _has_multiple_inline_formula_images(list(items)):
                    return True
        except Exception:
            pass

    return False



def _page_marker(page_index: int) -> str:
    """生成不会强制中断跨页题目的来源页标记。"""
    return f"<!-- MATHBANK_PDF_PAGE:{page_index + 1} -->"


def _extract_fitz_pages(
    file_bytes: bytes,
    page_indices: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """在 pdf-inspector 不可用时，按用户所选页码保序提取 PyMuPDF 文本。"""
    if not _FITZ_AVAILABLE:
        return []
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            selected = page_indices if page_indices is not None else list(range(len(doc)))
            pages = []
            for page_index in selected:
                if page_index < 0 or page_index >= len(doc):
                    continue
                text = doc.load_page(page_index).get_text("text").strip()
                quality_reasons = native_text_quality_reasons(text)
                if len(text) < 30 and not quality_reasons:
                    quality_reasons.append("原生文字过少，需通过原页识别。")
                pages.append({
                    "page_index": page_index,
                    "markdown": text,
                    "needs_ocr": bool(quality_reasons),
                    "source": "pymupdf",
                    "quality_reasons": quality_reasons,
                })
        return pages
    except Exception:
        return []


def _join_native_pages(pages: List[Dict[str, Any]]) -> str:
    """按原页序合并可靠文本；页标记仅追踪来源，不表示题目边界。"""
    chunks = []
    for page in pages:
        if page.get("needs_ocr"):
            continue
        text = str(page.get("markdown") or "").strip()
        if text:
            chunks.append(f"{_page_marker(int(page['page_index']))}\n{text}")
    return "\n\n".join(chunks)


def merge_pdf_page_texts(page_texts: List[Optional[str]]) -> str:
    """按页序连续合并文本，不把换页误当成一道题的结束。"""
    return "\n\n".join(str(text).strip() for text in page_texts if text and str(text).strip())


def _build_result(
    *,
    available: bool,
    pages: List[Dict[str, Any]],
    pdf_type: str,
    error: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    reliable_pages = [page for page in pages if not page.get("needs_ocr")]
    pages_needing_ocr = [
        int(page["page_index"])
        for page in pages
        if page.get("needs_ocr")
    ]
    return {
        "available": available,
        "pdf_type": pdf_type,
        "is_text_based": bool(pages) and len(reliable_pages) == len(pages),
        "markdown": _join_native_pages(pages) if reliable_pages else None,
        "pages": pages,
        "pages_needing_ocr": pages_needing_ocr,
        "confidence": confidence,
        "error": error,
    }


def _repair_native_pages(file_bytes: bytes, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Try geometric math recovery before paying for rejected native pages.

    Only a complete, validated page is adopted. The original Markdown remains
    available for local auditing; unsupported pages retain their former route.
    This is never used on the legacy API's multi-page aggregate Markdown.
    """
    if not _FITZ_AVAILABLE or not any(page.get("needs_ocr") for page in pages):
        return pages
    try:
        from mathbank.pdf_native_math import repair_native_page

        with fitz.open(stream=file_bytes, filetype="pdf") as document:
            for page in pages:
                index = page.get("page_index")
                if (not page.get("needs_ocr") or type(index) is not int
                        or not 0 <= index < len(document)):
                    continue
                original_reasons = list(page.get("quality_reasons", []))
                try:
                    result = repair_native_page(document[index])
                except Exception:
                    # Local repair is an optional deterministic step, not a
                    # reason to abort extraction or silently accept its input.
                    result = {"status": "unsupported", "reason": "本地公式结构修复未完成，保留原识别流程。"}
                markdown = result.get("markdown")
                remaining = native_text_quality_reasons(markdown) if isinstance(markdown, str) else []
                complete = (result.get("status") == "repaired" and isinstance(markdown, str)
                            and bool(markdown.strip()) and not remaining
                            and not _has_math_formula_loss(markdown))
                page["native_repair"] = {
                    "status": "repaired" if complete else "fallback",
                    "notes": result.get("notes", []),
                    "reason": ("" if complete else (result.get("reason") or
                               "本地结构修复未通过完整性检查，保留视觉识别。")),
                    "stats": result.get("stats", {}),
                    "original_quality_reasons": original_reasons,
                }
                if complete:
                    page["original_markdown"] = page["markdown"]
                    page.update(markdown=markdown.strip(), needs_ocr=False,
                                quality_reasons=[], source="native-math-repaired")
    except Exception:
        # An unreadable PDF or unavailable optional extractor keeps the exact
        # pre-repair result. No incomplete page can enter the trusted text.
        return pages
    return pages


def inspect_and_extract_pdf(
    file_bytes: bytes,
    task_id: Optional[str] = None,
    page_indices: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """逐页提取可靠原生文本，并明确列出需要视觉 OCR 的页面。

    ``page_indices`` 使用 0 起始页码。返回的 ``pages`` 与调用者给出的页序一致；
    原生页和 OCR 页可在上层逐页混合，再整卷一次性拆题，从而保留跨页题。
    ``task_id`` 保留用于兼容既有调用。
    """
    del task_id
    if not _PDF_INSPECTOR_AVAILABLE:
        fitz_pages = _repair_native_pages(file_bytes, _extract_fitz_pages(file_bytes, page_indices))
        if fitz_pages and all(not page["needs_ocr"] for page in fitz_pages):
            return _build_result(
                available=False,
                pages=fitz_pages,
                pdf_type="text_based",
            )
        return _build_result(
            available=False,
            pages=fitz_pages,
            pdf_type="unknown",
            error="pdf-inspector is not installed",
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_file.write(file_bytes)
            tmp_path = tmp_file.name

        extract_pages = getattr(pdf_inspector, "extract_pages_markdown", None)
        if callable(extract_pages):
            extracted = extract_pages(tmp_path, pages=page_indices)
            raw_pages = list(getattr(extracted, "pages", []) or [])
            pages = []
            for raw_page in raw_pages:
                page_index = int(getattr(raw_page, "page"))
                markdown = str(getattr(raw_page, "markdown", "") or "").strip()
                quality_reasons = native_text_quality_reasons(markdown)
                quality_reasons.extend(_native_position_quality_reasons(markdown, page_index, tmp_path))
                # pdf-inspector 的逐页结论是首要依据；短标题页不能仅因字符少就被误送 OCR。
                needs_ocr = bool(getattr(raw_page, "needs_ocr", False)) or not markdown
                if not needs_ocr and markdown:
                    if _has_math_formula_loss(markdown, page_index, tmp_path):
                        quality_reasons.append("原生提取出现公式丢失或空选项迹象。")
                if needs_ocr and not quality_reasons:
                    quality_reasons.append("PDF Inspector 判定本页原生提取不可靠。")
                needs_ocr = needs_ocr or bool(quality_reasons)
                pages.append({
                    "page_index": page_index,
                    "markdown": markdown,
                    "needs_ocr": needs_ocr,
                    "source": "pdf-inspector",
                    "quality_reasons": quality_reasons,
                })

            if pages:
                pages = _repair_native_pages(file_bytes, pages)
                ocr_count = sum(1 for page in pages if page["needs_ocr"])
                if ocr_count == 0:
                    pdf_type = "text_based"
                elif ocr_count == len(pages):
                    pdf_type = "scanned"
                else:
                    pdf_type = "mixed"
                return _build_result(
                    available=True,
                    pages=pages,
                    pdf_type=pdf_type,
                )
            # 极旧或异常版本若未返回逐页结果，继续走原有整卷 API，避免功能退化。

        # 兼容旧版 pdf-inspector：仍尊重用户页码范围，但只能整段返回。
        try:
            result = pdf_inspector.process_pdf(tmp_path, pages=page_indices)
        except TypeError:
            # 更早版本尚无 pages 参数，保留其原有整卷直提能力。
            result = pdf_inspector.process_pdf(tmp_path)
        raw_type = str(getattr(result, "pdf_type", "")).lower().replace("-", "_")
        markdown = str(getattr(result, "markdown", "") or "").strip()
        has_encoding_issues = bool(getattr(result, "has_encoding_issues", False))
        pages_needing_ocr = set(getattr(result, "pages_needing_ocr", []) or [])
        is_text_type = "text_based" in raw_type or "textbased" in raw_type
        if is_text_type and markdown and len(markdown) >= 30 and not has_encoding_issues and not pages_needing_ocr:
            legacy_page = page_indices[0] if page_indices else 0
            has_loss = _has_math_formula_loss(markdown, legacy_page, tmp_path)
            quality_reasons = native_text_quality_reasons(markdown)
            if has_loss:
                quality_reasons.append("原生提取出现公式丢失或空选项迹象。")
            pages = [{
                "page_index": legacy_page,
                "markdown": markdown,
                "needs_ocr": bool(quality_reasons),
                "source": "pdf-inspector-legacy",
                "quality_reasons": quality_reasons,
            }]
            return _build_result(
                available=True,
                pages=pages,
                pdf_type="scanned" if quality_reasons else "text_based",
                confidence=getattr(result, "confidence", None),
            )
        return _build_result(
            available=True,
            pages=[],
            pdf_type=raw_type or "scanned",
        )
    except Exception as ex:
        # 探测器异常时保留旧有 PyMuPDF 兜底；但不覆盖探测器明确给出的编码异常。
        fitz_pages = _repair_native_pages(file_bytes, _extract_fitz_pages(file_bytes, page_indices))
        if fitz_pages and all(not page["needs_ocr"] for page in fitz_pages):
            return _build_result(
                available=True,
                pages=fitz_pages,
                pdf_type="text_based",
                error=f"pdf-inspector failed, used PyMuPDF: {ex}",
            )
        return _build_result(
            available=True,
            pages=fitz_pages,
            pdf_type="error",
            error=str(ex),
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
