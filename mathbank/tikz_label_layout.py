"""Review single-letter point labels against the actual TikZ preview strokes."""

from dataclasses import dataclass
import math
import re

from mathbank.tikz_circle_review import _cubic_at
from mathbank.tikz_spatial import _crosses_box, _overlaps


_POINT = re.compile(r"\\coordinate\s*\(([A-Z])\)")
_NODE = re.compile(
    r"^[ \t]*\\node\s*\[(?P<options>[^]\n]{0,160})\]\s*at\s*\((?P<name>[A-Z])\)"
    r"\s*\{\s*\$(?P=name)\$\s*\};[ \t]*$", re.M,
)
_TRAILING_LABEL = re.compile(
    r"^[ \t]*\\node\s*\[[^]\n]{0,160}\]\s*at\s*"
    r"\((?:[A-Z][0-9]?|[-.\d+\w\\{}*/ ]+,\s*[-.\d+\w\\{}*/ ]+)\)"
    r"\s*\{\s*\$[A-Za-z]\$\s*\};[ \t]*$", re.M,
)
_OPTION = re.compile(
    r"(?:(?:(?:above|below)(?: (?:left|right))?|left|right)(?:\s*=\s*\d+(?:\.\d+)?(?:pt|mm|cm))?|"
    r"(?:inner|outer) sep\s*=\s*\d+(?:\.\d+)?(?:pt|mm|cm))"
)
_END = re.compile(r"\s*\\end\s*\{tikzpicture\}\s*")
_DIRECTIONS = ((1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (-1, -1), (1, 1), (-1, 1))


@dataclass(frozen=True)
class LabelReview:
    collisions: tuple[str, ...] = ()
    adjustment: str | None = None


@dataclass(frozen=True)
class TikzPreview:
    image_path: str
    code: str
    collisions: tuple[str, ...] = ()


def _strokes(page):
    strokes = []
    for path in page.get_drawings():
        width = path.get("width") or 0
        if path["type"] not in ("s", "fs") or width <= 0 or not path.get("stroke_opacity", 1):
            continue
        for item in path["items"]:
            kind = item[0]
            if kind == "l":
                vertices = [tuple(p) for p in item[1:]]
            elif kind == "c":
                control = [tuple(p) for p in item[1:]]
                count = min(32, max(4, math.ceil(sum(math.dist(a, b) for a, b in zip(control, control[1:]))/3)))
                vertices = [_cubic_at(control, i/count) for i in range(count+1)]
            elif kind == "re":
                rect = item[1]
                vertices = [tuple(p) for p in (rect.tl, rect.tr, rect.br, rect.bl, rect.tl)]
            else:
                continue
            strokes.extend((a, b, width/2) for a, b in zip(vertices, vertices[1:]))
            if len(strokes) > 6000:
                return []
    return strokes


def _label_boxes(page, point_names):
    labels = {}
    for span in page.get_texttrace():
        if span["type"] != 0 or not span["opacity"] or span["dir"] != (1., 0.) or len(span["chars"]) != 1:
            continue
        character, _, origin, box = span["chars"][0]
        if not 65 <= character <= 90 or (name := chr(character)) not in point_names:
            continue
        x0, _, x1, _ = box
        # Use the interior of the capital glyph, avoiding the PDF font's
        # extra ascent/descent and horizontal side bearings.
        ink = (x0+.08*(x1-x0), origin[1]-.68*span["size"],
               x1-.08*(x1-x0), origin[1]-.04*span["size"])
        labels.setdefault(name, []).append(ink)
    return {name: copies[0] for name, copies in labels.items() if len(copies) == 1}


def _touches(box, strokes, margin):
    for a, b, radius in strokes:
        pad = radius+margin
        if _crosses_box(a, b, (box[0]-pad, box[1]-pad, box[2]+pad, box[3]+pad)):
            return True
    return False


def review_compiled_labels(page, source, *, suggest=False):
    point_names = set(_POINT.findall(source))
    if not point_names:
        return LabelReview()
    labels = _label_boxes(page, point_names)
    strokes = _strokes(page)
    collisions = tuple(name for name, box in labels.items() if _touches(box, strokes, 0))
    if not collisions or not suggest:
        return LabelReview(collisions)

    occupied = list(labels.values())
    edits = []
    for name in collisions:
        nodes = [m for m in _NODE.finditer(source) if m["name"] == name]
        if len(nodes) != 1:
            continue
        node = nodes[0]
        # A later path might use the bounding box to position geometry.
        # Only edit a dedicated group of labels after the drawing is complete.
        if not _END.fullmatch(_TRAILING_LABEL.sub("", source[node.end():])):
            continue
        if not all(_OPTION.fullmatch(opt.strip()) for opt in node["options"].split(",")):
            continue
        x0, y0, x1, y1 = labels[name]
        for dx, dy in ((step*dx, step*dy) for step in (3, 6, 9) for dx, dy in _DIRECTIONS):
            target = (x0+dx, y0+dy, x1+dx, y1+dy)
            if any(_overlaps(target, other) for other in occupied if other != labels[name]):
                continue
            if not _touches(target, strokes, .65):
                occupied.append(target)
                # PDF y increases downwards, TikZ y increases upwards.
                edits.append((node.end("options"), f",xshift={dx}pt,yshift={-dy}pt"))
                break
    if not edits:
        return LabelReview(collisions)
    for offset, addition in sorted(edits, reverse=True):
        source = source[:offset] + addition + source[offset:]
    return LabelReview(collisions, source)
