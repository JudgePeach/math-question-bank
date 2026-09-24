"""Conservative, local structure recovery for a bounded subset of TeX PDFs.

Only original page glyph geometry is evidence. No filename, neighbouring TeX,
model cache, or semantic answer inference is used. Unsupported structures return
no adoptable text. Every raw character is accounted for exactly once.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import math
import re
import statistics


MAX_GLYPHS = 10000
MAX_DRAWINGS = 24
_MATH_FONT = re.compile(r"^(CMMI|CMSY|CMR|CMEX)(\d+)$")
_BOLD_FONT = re.compile(r"^LMRomanDemi\d+(?:-Regular)?(?:-Identity-H)?$")
_PARTS = {"\uf8f1": "top", "\uf8f2": "middle", "\uf8f3": "bottom"}
_SYMBOLS = {"−": "-", "∗": "*", "×": r"\times ", "·": r"\cdot ", "∈": r"\in ",
            "⊂": r"\subset ", "⊆": r"\subseteq ", "{": r"\{", "}": r"\}",
            "°": r"\circ "}
_NEGATED = {"∈": r"\notin ", "⊂": r"\not\subset "}


class _Unsupported(ValueError):
    def __init__(self, reason: str, atoms=()):
        super().__init__(reason)
        self.atoms = list(atoms)


@dataclass(frozen=True)
class _Glyph:
    id: int
    text: str
    font: str
    size: float
    x: float
    y: float
    bbox: tuple
    line: int
    kind: str


@dataclass
class _Atom:
    text: str
    tex: str
    kind: str
    ids: tuple[int, ...]
    x: float
    x1: float
    y: float
    size: float
    bbox: tuple
    line: int
    drawing_ids: tuple[int, ...] = ()
    role: str = ""


def _font_name(font: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", font).removesuffix("-Identity-H")


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _finite(values):
    try:
        return all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in values)
    except (TypeError, OverflowError):
        return False


def _collect(page):
    if getattr(page, "rotation", 0) != 0:
        raise _Unsupported("当前本地公式恢复不处理旋转页面。")
    if page.get_images(full=True):
        raise _Unsupported("页面含位图，不能用本轮纯字形恢复替代完整识别。")
    data = page.get_text("rawdict")
    # rawdict moves some zero-advance combining strokes to the previous
    # character's end. The physical drawing trace retains their true origin.
    overlay_trace = []
    trace_reader = getattr(page, "get_texttrace", None)
    if callable(trace_reader):
        for span in trace_reader():
            for char in span.get("chars", []):
                if char[0] == 0x338:
                    overlay_trace.append((_font_name(span.get("font", "")), span.get("size"), char[2], char[3]))
        if len(overlay_trace) > 100:
            raise _Unsupported("页面叠印字形过多。")
    trace_used = set()
    glyphs, spaces = [], []
    raw_count = line_id = 0
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            raise _Unsupported("页面含非文字内容，无法完整证明字形消费。")
        for line in block.get("lines", []):
            line_id += 1
            if line.get("wmode", 0) or tuple(line.get("dir", (1, 0))) != (1, 0):
                raise _Unsupported("页面含竖排或倾斜文字，当前阅读顺序不适用。")
            for span in line.get("spans", []):
                font = _font_name(str(span.get("font", "")))
                size = span.get("size")
                if not _finite([size]) or not 3 <= size <= 60 or span.get("alpha", 255) == 0:
                    raise _Unsupported("文字字号或可见性不可靠。")
                family = _MATH_FONT.fullmatch(font)
                kind = "math" if family else "bold_math" if _BOLD_FONT.fullmatch(font) else "text"
                if not font or any(word in font.lower() for word in ("symbol", "math", "mathtype")) and not family:
                    raise _Unsupported("页面使用尚未验证的数学字体。")
                for char in span.get("chars", []):
                    raw_count += 1
                    if raw_count > MAX_GLYPHS:
                        raise _Unsupported("页面字形数量超过本地恢复上限。")
                    value, origin, box = char.get("c"), char.get("origin"), char.get("bbox")
                    if not isinstance(value, str) or len(value) != 1 or not origin or not box or not _finite([*origin, *box]):
                        raise _Unsupported("页面含无法完整定位的字形。")
                    if value.isspace():
                        spaces.append(raw_count)
                        continue
                    if kind == "text" and value in "$\\%&#_^{}~`":
                        raise _Unsupported("普通文字含需专门转义的控制字符，本轮不改写。")
                    if value == "\u0338" and overlay_trace:
                        matches = [(index, entry) for index, entry in enumerate(overlay_trace) if index not in trace_used
                                   and entry[0] == font and abs(entry[1] - size) < .05
                                   and abs(entry[2][1] - origin[1]) < .05 and abs(entry[2][0] - origin[0]) <= size * .4]
                        if len(matches) != 1:
                            raise _Unsupported("组合否定的文本位置与物理字形轨迹不唯一。")
                        trace_used.add(matches[0][0])
                        origin, box = matches[0][1][2:]
                    if value == "\ufffd" or (ord(value) < 32) or (0xE000 <= ord(value) <= 0xF8FF and value not in _PARTS) or ord(value) >= 0xF0000:
                        raise _Unsupported("存在未知或缺失字形，不能猜测数学内容。")
                    if value in _PARTS and not font.startswith("CMEX"):
                        raise _Unsupported("私用括号字形不来自受支持的扩展数学字体。")
                    if font.startswith("CMEX") and value not in _PARTS:
                        raise _Unsupported("包含尚未重建的根号、大操作符或扩展定界符。")
                    if box[2] < box[0] or box[3] <= box[1] or box[2] == box[0] and value != "\u0338":
                        raise _Unsupported("存在不可见或退化字形，不能据此恢复原文。")
                    glyphs.append(_Glyph(raw_count, value, font, float(size), float(origin[0]), float(origin[1]), tuple(box), line_id, kind))
    return glyphs, spaces, raw_count


def _glyph_tex(glyph):
    value = glyph.text
    if glyph.kind == "bold_math":
        if not re.fullmatch(r"[A-Za-z0-9]", value):
            raise _Unsupported("粗体数学字体包含未支持的字形。", [glyph])
        return r"\mathbf{" + value + "}"
    if value in _SYMBOLS:
        return _SYMBOLS[value]
    if re.fullmatch(r"[A-Za-z0-9+,.;:=<>/()\[\]|!?-]", value):
        if glyph.font.startswith("CMR") and value.isalpha():
            return r"\mathrm{" + value + "}"
        return value
    raise _Unsupported("数学字体含当前未支持的符号。", [glyph])


def _atoms(glyphs):
    result = []
    for glyph in glyphs:
        kind = "part" if glyph.text in _PARTS else "overlay" if glyph.text == "\u0338" else glyph.kind
        tex = glyph.text if kind in {"text", "part", "overlay"} else _glyph_tex(glyph)
        result.append(_Atom(glyph.text, tex, kind, (glyph.id,), glyph.x, glyph.bbox[2], glyph.y,
                            glyph.size, glyph.bbox, glyph.line))
    return result


def _negations(atoms, stats):
    removed = set()
    for index, stroke in enumerate(atoms):
        if stroke.text not in {"/", "\u0338"} or stroke.kind == "text":
            continue
        candidates = []
        for target_index, target in enumerate(atoms):
            if target.text not in _NEGATED or target.kind == "text" or target_index in removed:
                continue
            if abs(stroke.y - target.y) > .12 * target.size or abs(stroke.size - target.size) > .1:
                continue
            overlap = max(0, min(stroke.x1, target.x1) - max(stroke.x, target.x))
            if (stroke.text == "\u0338" and abs(stroke.x - target.x) <= .15 * target.size
                    or stroke.text == "/" and overlap >= .55 * min(stroke.x1 - stroke.x, target.x1 - target.x)):
                candidates.append(target_index)
        if len(candidates) > 1 or stroke.text == "\u0338" and not candidates:
            raise _Unsupported("否定叠印无法唯一对应关系符。", [stroke])
        if not candidates:
            continue
        target_index = candidates[0]
        target = atoms[target_index]
        if len(target.ids) != 1:
            raise _Unsupported("同一关系符被重复否定叠印。", [target, stroke])
        target.tex = _NEGATED[target.text]
        target.ids += stroke.ids
        target.bbox = _union([target.bbox, stroke.bbox])
        target.x, target.x1 = target.bbox[0], target.bbox[2]
        removed.add(index)
        stats["negations"] += 1
    return [atom for i, atom in enumerate(atoms) if i not in removed]


def _scripts(atoms, size, stats):
    for small_text in atoms:
        if small_text.kind != "text" or small_text.size >= size * .82 or not re.fullmatch(r"[A-Za-z0-9]", small_text.text):
            continue
        if any(parent.kind in {"math", "bold_math", "text"} and re.fullmatch(r"[A-Za-z0-9)]", parent.text)
               and parent.size >= small_text.size * 1.25
               and -.1 <= small_text.x - parent.x1 <= parent.size * .65
               and .1 * parent.size <= abs(small_text.y - parent.y) <= .8 * parent.size for parent in atoms):
            raise _Unsupported("混合字体的小字可能是脚标，但缺少受支持的数学字体证据。", [small_text])
    small = [a for a in atoms if a.kind in {"math", "bold_math"} and a.size < size * .82]
    baseline = [a for a in atoms if a not in small and a.kind != "part"]
    groups = []
    for atom in sorted(small, key=lambda a: (a.line, a.y, a.x)):
        if (groups and groups[-1][-1].line == atom.line and abs(groups[-1][-1].y - atom.y) <= .4
                and abs(groups[-1][-1].size - atom.size) <= .3 and -.05 <= atom.x - groups[-1][-1].x1 <= atom.size * .65):
            groups[-1].append(atom)
        else:
            groups.append([atom])
    attached = {}
    for group in groups:
        first = group[0]
        candidates = []
        for parent in baseline:
            delta = first.y - parent.y
            if (parent.kind not in {"math", "bold_math"} or parent.line != first.line
                    or not re.fullmatch(r"[A-Za-z0-9)\]}]", parent.text)
                    or not .52 <= first.size / parent.size <= .81
                    or not -.1 <= first.x - parent.x1 <= parent.size * .55):
                continue
            if -.75 * parent.size <= delta <= -.15 * parent.size:
                candidates.append((parent, "^"))
            elif .1 * parent.size <= delta <= .55 * parent.size:
                candidates.append((parent, "_"))
        if len(candidates) != 1:
            raise _Unsupported("较小字形未能唯一绑定上标或下标基底。", group)
        parent, mode = candidates[0]
        if mode in attached.setdefault(id(parent), set()):
            raise _Unsupported("同一基底出现多个不明确的同类脚标。", [parent, *group])
        # A raised small glyph directly over another ordinary glyph is not a
        # proven attachment to the preceding character.
        if any(other is not parent and other.kind in {"math", "bold_math"} and other.line == parent.line
               and first.x < other.x1 - .1 and group[-1].x1 > other.x + .1 for other in baseline):
            raise _Unsupported("脚标与另一基底重叠，不能可靠恢复。", group)
        attached[id(parent)].add(mode)
        parent.tex += mode + "{" + "".join(a.tex for a in group) + "}"
        parent.ids += tuple(i for a in group for i in a.ids)
        parent.x1 = max(parent.x1, group[-1].x1)
        parent.bbox = _union([parent.bbox, *(a.bbox for a in group)])
        stats["superscripts" if mode == "^" else "subscripts"] += 1
    return [a for a in atoms if a not in small]


def _rows(atoms, size):
    result = []
    for atom in sorted(atoms, key=lambda a: (a.y, a.x)):
        if result and abs(statistics.mean(a.y for a in result[-1]) - atom.y) <= size * .2:
            result[-1].append(atom)
        else:
            result.append([atom])
    return [sorted(row, key=lambda a: a.x) for row in result]


def _case_row(row):
    # The expression and its condition must be separated by a real gap; this
    # preserves the visible comma as part of the expression, not a guess.
    splits = [i for i in range(1, len(row)) if row[i].x - row[i - 1].x1 >= row[i].size * .65]
    if len(splits) != 1 or not any(a.kind == "text" and re.search(r"[\u3400-\u9fff]", a.text) for a in row[splits[0]:]):
        raise _Unsupported("分支公式缺少唯一的表达式/条件列分界。", row)
    split = splits[0]
    if any(a.kind == "text" for a in row[:split]):
        raise _Unsupported("分支表达式列包含未识别的文字结构。", row)
    output = "".join(a.tex for a in row[:split]) + " & "
    text = ""
    for atom in row[split:]:
        if atom.kind == "text":
            text += atom.text
        else:
            if text:
                output += r"\text{" + _escape_text(text) + "}"
                text = ""
            output += atom.tex
    if text:
        output += r"\text{" + _escape_text(text) + "}"
    return output


def _escape_text(value):
    return value.replace("\\", r"\textbackslash{}").replace("{", r"\{").replace("}", r"\}").replace("$", r"\$").replace("%", r"\%").replace("&", r"\&").replace("#", r"\#").replace("_", r"\_")


def _cases(atoms, size, stats):
    parts = [a for a in atoms if a.kind == "part"]
    while parts:
        top = min(parts, key=lambda a: a.y)
        group = [a for a in parts if abs(a.x - top.x) <= .3 and abs(a.size - top.size) <= .1]
        group.sort(key=lambda a: a.y)
        if [a.text for a in group] != ["\uf8f1", "\uf8f2", "\uf8f3"]:
            raise _Unsupported("扩展大括号未组成唯一完整的上中下组件。", group)
        low = group[0].bbox[1]
        high = group[-1].bbox[3] + size * .55
        body = [a for a in atoms if a.kind != "part" and a.x >= top.x1 - .15 and low <= a.y <= high]
        rows = _rows(body, size)
        if len(rows) != 2 or any(not row for row in rows):
            raise _Unsupported("当前只支持可完整覆盖的两行分支公式。", [*group, *body])
        if abs(rows[0][0].x - rows[1][0].x) > .4 or rows[0][0].x - top.x1 > size * .2:
            raise _Unsupported("分支行与大括号未形成一致的左边界。", body)
        candidates = [a for a in atoms if a not in body and a not in group and a.kind in {"math", "bold_math"}
                      and a.text == "=" and -.1 <= top.x - a.x1 <= size * .7
                      and rows[0][0].y < a.y < rows[1][0].y]
        if len(candidates) != 1:
            raise _Unsupported("分支公式未能唯一接回左侧主公式。", group)
        parent = candidates[0]
        latex = r"\begin{cases}" + _case_row(rows[0]) + r"\\" + _case_row(rows[1]) + r"\end{cases}"
        covered = group + body
        box = _union(a.bbox for a in covered)
        compound = _Atom("cases", latex, "math", tuple(i for a in covered for i in a.ids),
                         top.x, box[2], parent.y, size, box, parent.line)
        atoms = [a for a in atoms if a not in covered] + [compound]
        parts = [a for a in parts if a not in group]
        stats["cases"] += 1
    return atoms


def _blank_rules(page, atoms, size, stats):
    drawings = list(page.get_drawings())
    if len(drawings) > MAX_DRAWINGS:
        raise _Unsupported("页面矢量内容超过普通填空线的支持范围。")
    for index, drawing in enumerate(drawings):
        items = drawing.get("items", [])
        if (drawing.get("type") != "s" or len(items) != 1 or items[0][0] != "l"
                or not _finite([drawing.get("width", 0)]) or not 0 < drawing["width"] <= 1
                or drawing.get("stroke_opacity", 1) < .99
                or any(channel > .4 for channel in drawing.get("color", (0, 0, 0)))
                or str(drawing.get("dashes", "[] 0")).replace(" ", "") != "[]0"):
            raise _Unsupported("页面含尚未重建的矢量图形或数学横线。")
        first, last = items[0][1:]
        x0, x1 = sorted([float(first[0]), float(last[0])])
        y = float(first[1])
        if abs(y - float(last[1])) > .1 or not 1.5 * size <= x1 - x0 <= page.rect.width * .35:
            raise _Unsupported("矢量线不能确认为普通填空横线。")
        blockers = [a for a in atoms if a.x < x1 - .2 and a.x1 > x0 + .2 and abs(a.y - y) < size * 1.5]
        if blockers:
            raise _Unsupported("数学横线附近存在字形，不能把分式或下划线误作填空。", blockers)
        left = [a for a in atoms if a.text in {"为", "是", "得", "="} and 0 <= x0 - a.x1 <= size * .7
                and 0 <= y - a.y <= size * .4]
        right = [a for a in atoms if a.kind == "text" and a.text in "，。；,.;" and abs(a.x - x1) <= size * .3
                 and 0 <= y - a.y <= size * .4]
        if len(left) != 1 or len(right) != 1 or abs(left[0].y - right[0].y) > .4:
            raise _Unsupported("填空横线缺少明确的正文两侧锚点。")
        atoms.append(_Atom("fillin", r"\fillin", "fillin", (), x0, x1, left[0].y, size,
                           (x0, y, x1, y + max(.1, drawing["width"])), left[0].line, (index,)))
        stats["fillins"] += 1
    stats["drawings_total"] = len(drawings)
    return atoms


def _layout_rows(atoms, size, page):
    rows = _rows(atoms, size)
    for row in rows:
        text = "".join(a.text for a in row)
        if len(re.findall(r"题目\s*\d+[：:]", text)) > 1:
            raise _Unsupported("同一高度出现多列题目，阅读顺序不唯一。", row)
        for i in range(1, len(row)):
            if row[i].x - row[i - 1].x1 > size * 2:
                left = "".join(a.text for a in row[:i] if a.kind == "text")
                right = "".join(a.text for a in row[i:] if a.kind == "text")
                if len(re.findall(r"[\u3400-\u9fff]", left)) >= 4 and len(re.findall(r"[\u3400-\u9fff]", right)) >= 4:
                    raise _Unsupported("正文可能为并排栏，不能按单栏自动重排。", row)
        # Question/choice labels are prose even if a producer used CMR.
        labels = list(re.finditer(r"^题目\s*\d+[：:]|^[A-D][.．]", text))
        if re.match(r"^A[.．]", text):
            labels = list(re.finditer(r"[A-D][.．]", text))
        citation = re.match(r"^题目\s*\d+[：:][（(](?:19|20)\d{2}[·•.．-][\u3400-\u9fffA-Za-z0-9·•.．-]{2,80}(?:月考|联考|期中|期末|测试|高考|中考)[）)]", text)
        if citation:
            labels.append(citation)
        for label in labels:
            offset = 0
            for atom in row:
                if label.start() <= offset < label.end():
                    atom.kind = "text"
                    if offset == label.start() and re.fullmatch(r"[A-D][.．]", label.group()):
                        atom.role = "option_start"
                offset += len(atom.text)
        if re.fullmatch(r"答案[：:][A-H]+", text):
            for atom in row:
                atom.kind = "text"
        if (len(row) <= 3 and text.isdigit() and row[0].y < page.rect.height * .09
                and row[0].x > page.rect.width * .6):
            for atom in row:
                atom.kind = "text"
                atom.role = "page_number"
    return rows


def _render(rows, size):
    chunks, blocks, math_open = [], [], False
    previous = None
    for row_index, row in enumerate(rows):
        if all(atom.role == "page_number" for atom in row):
            if len(row) > 3 or not "".join(a.text for a in row).isdigit():
                raise _Unsupported("非页码正文不能作为元信息省略。", row)
            blocks.append({"role": "page_number", "bbox": list(_union(a.bbox for a in row)),
                           "glyph_ids": [i for a in row for i in a.ids], "drawing_ids": [], "emitted": False})
            continue
        visible = "".join(a.text for a in row)
        hard = bool(re.match(r"题目\s*\d|[A-D][.．]|答案[：:]|评析[：:]", visible))
        if previous is not None:
            keep_math = (math_open and not hard and row[0].kind in {"math", "bold_math"}
                         and row[0].y - previous[-1].y <= size * 1.9)
            if math_open and not keep_math:
                chunks.append("$")
                math_open = False
            chunks.append(" " if keep_math else "\n\n" if hard else "\n")
        previous_atom = None
        block_start = sum(map(len, chunks))
        for atom in row:
            new_option = False
            if atom.role == "option_start" and previous_atom is not None:
                if math_open:
                    chunks.append("$")
                    math_open = False
                chunks.append("\n")
                new_option = True
            if (previous_atom is not None and atom.kind in {"math", "bold_math"}
                    and previous_atom.kind in {"math", "bold_math"}
                    and atom.x - previous_atom.x1 >= size):
                raise _Unsupported("数学片段之间存在明显分栏或分隔空隙，不能拼成同一表达式。", [previous_atom, atom])
            if (previous_atom is not None and atom.kind in {"math", "bold_math"}
                    and previous_atom.kind in {"math", "bold_math"}
                    and previous_atom.text.isdigit() and atom.text.isdigit()
                    and atom.x - previous_atom.x1 >= size * .35):
                raise _Unsupported("数学数字之间有显著间隔，不能猜测合并为多位数。", [previous_atom, atom])
            is_math = atom.kind in {"math", "bold_math"}
            if is_math != math_open:
                chunks.append("$")
                math_open = is_math
            if previous_atom is not None and not new_option and not is_math and previous_atom.kind == "text" and atom.kind == "text":
                if atom.x - previous_atom.x1 >= size * .18:
                    chunks.append(" ")
            chunks.append(atom.tex if is_math or atom.kind == "fillin" else atom.text)
            previous_atom = atom
        blocks.append({"bbox": list(_union(a.bbox for a in row)), "glyph_ids": [i for a in row for i in a.ids],
                       "drawing_ids": [i for a in row for i in a.drawing_ids], "source_start": block_start,
                       "source_end": sum(map(len, chunks))})
        previous = row
    if math_open:
        chunks.append("$")
    return "".join(chunks).strip(), blocks


def repair_native_page(page, inspector_items=None) -> dict:
    """Return an entire proved page, or an explicit unsupported result.

    ``inspector_items`` is accepted for caller compatibility; reconstruction
    deliberately uses original per-glyph PyMuPDF geometry instead of already
    coalesced/normalized Inspector strings.
    """
    del inspector_items
    stats = {"glyphs_total": 0, "glyphs_consumed": 0, "has_supported_math": False,
             "cases": 0, "superscripts": 0, "subscripts": 0, "negations": 0, "fillins": 0,
             "drawings_total": 0, "drawings_consumed": 0}
    result = {"status": "unsupported", "markdown": "", "notes": [], "reason": "",
              "blocks": [], "risk_regions": [], "stats": stats}
    try:
        glyphs, spaces, raw_count = _collect(page)
        stats.update(glyphs_total=raw_count, whitespace_glyphs=len(spaces), visible_glyphs=len(glyphs))
        stats["has_supported_math"] = any(g.kind != "text" for g in glyphs)
        sizes = [g.size for g in glyphs if g.kind in {"math", "bold_math"} and not g.font.startswith("CMEX")]
        if not sizes:
            raise _Unsupported("没有当前支持的数学字形结构，不改写普通文本。")
        size = max(Counter(round(value, 2) for value in sizes), key=lambda value: (Counter(round(v, 2) for v in sizes)[value], value))
        atoms = _negations(_atoms(glyphs), stats)
        for i, atom in enumerate(atoms):
            for other in atoms[i + 1:]:
                if abs(atom.x - other.x) < .04 and abs(atom.y - other.y) < .04:
                    raise _Unsupported("相同位置有重复或冲突字形，不能静默去重。", [atom, other])
        atoms = _scripts(atoms, size, stats)
        atoms = _cases(atoms, size, stats)
        atoms = _blank_rules(page, atoms, size, stats)
        rows = _layout_rows(atoms, size, page)
        used = [*spaces, *(i for atom in atoms for i in atom.ids)]
        drawings_used = [i for atom in atoms for i in atom.drawing_ids]
        if sorted(used) != list(range(1, raw_count + 1)) or sorted(drawings_used) != list(range(stats["drawings_total"])):
            raise _Unsupported("字形或矢量线未被完整且唯一消费。")
        markdown, blocks = _render(rows, size)
        if not markdown or re.search(r"[\ufffd\ue000-\uf8ff\u0338]", markdown):
            raise _Unsupported("恢复结果仍含未处理字形，不输出不完整原文。")
        emitted = [i for block in blocks if block.get("emitted", True) for i in block["glyph_ids"]]
        metadata = [i for block in blocks if not block.get("emitted", True) for i in block["glyph_ids"]]
        if sorted([*emitted, *metadata, *spaces]) != list(range(1, raw_count + 1)):
            raise _Unsupported("渲染输出未完整覆盖正文与单独登记的元信息。")
        # Only generated unescaped delimiters may delimit math. Source text
        # control characters were rejected before this rendering stage.
        if len(re.findall(r"(?<!\\)\$", markdown)) % 2:
            raise _Unsupported("恢复结果数学定界不成对。")
        stats.update(glyphs_consumed=len(used), drawings_consumed=len(drawings_used),
                     emitted_glyphs=len(emitted), metadata_glyphs=len(metadata))
        stats["structural_repairs"] = sum(stats[key] for key in ("cases", "superscripts", "subscripts", "negations", "fillins"))
        if not stats["structural_repairs"]:
            raise _Unsupported("没有当前可确认的结构修复需求，保留既有原生提取。")
        result.update(status="repaired", markdown=markdown, blocks=blocks,
                      notes=[f"已依据原PDF字形位置恢复分支公式{stats['cases']}处、上下标{stats['superscripts'] + stats['subscripts']}处、否定关系{stats['negations']}处和填空线{stats['fillins']}处；未调用模型。"])
        if any(block.get("role") == "page_number" for block in blocks):
            result["notes"].append("顶部右侧孤立页码已按位置作为元信息登记，不计入题干或数学公式。")
    except _Unsupported as exc:
        result["reason"] = str(exc)
        if exc.atoms:
            box = _union(a.bbox for a in exc.atoms)
            result["risk_regions"] = [[round(box[0] / page.rect.width * 1000, 4), round(box[1] / page.rect.height * 1000, 4),
                                       round(box[2] / page.rect.width * 1000, 4), round(box[3] / page.rect.height * 1000, 4)]]
    except Exception as exc:
        result["reason"] = f"原生字形结构无法安全读取（{type(exc).__name__}）。"
    return result
