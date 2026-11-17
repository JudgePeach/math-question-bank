"""Bind named circumcircles to their actual full vector paths in TikZ previews."""

from dataclasses import dataclass
import math
import re

from mathbank.tikz_geometry_protocol import TikzGeometryError

# Only explicit prose of the form "circle E is the circumcircle of ABCD".
# This intentionally avoids guessing points from an arbitrary TeX drawing.
_POLYGON = r"(?:等腰|直角|等边)?(?:三角形|四边形|梯形|正方形|矩形|△|\\triangle)?"
_CIRCUMCIRCLE = re.compile(
    r"圆(?P<center>[A-Z][0-9]?)(?:是|为)" + _POLYGON
    + r"(?P<vertices>[A-Z]{3,8})的外接圆"
)
_END = re.compile(r"\\end\s*\{\s*tikzpicture\s*\}")
_CIRCLE_STROKE = re.compile(r"\\(?:draw|path)\b[^;]{0,2048}\bcircle\s*(?:\[|\()", re.DOTALL)
_SCALE = 72 / 72.27  # TeX pt to PDF bp.
_BORDER_BP = 2 * 72 / 25.4  # Match standalone's 2 mm figure border.


@dataclass(frozen=True)
class Circumcircle:
    center: str
    vertices: tuple[str, ...]


def circumcircles(context: str) -> tuple[Circumcircle, ...]:
    text = re.sub(r"\\(?:text|mathrm|mathbf)\{([A-Z0-9]+)\}", r"\1", (context or "")[:30000])
    text = re.sub(r"\\[()\[\]]|\$|\s+", "", text)
    found = {}
    for match in _CIRCUMCIRCLE.finditer(text):
        center, vertices = match.group("center", "vertices")
        if len(set(vertices)) == len(vertices) and center not in vertices:
            found[(center, frozenset(vertices))] = Circumcircle(center, tuple(vertices))
    return tuple(found.values()) if len(found) <= 4 else ()


def wants_circle_review(source: str) -> bool:
    return bool(_CIRCLE_STROKE.search(source))


def add_anchor_probes(source: str, circles: tuple[Circumcircle, ...], token: str) -> str | None:
    ends = list(_END.finditer(source))
    if not circles or len(ends) != 1:
        return None
    names = sorted({name for circle in circles for name in (circle.center, *circle.vertices)})
    probe = r"""
\begin{scope}[overlay]
\pgftransformreset
\makeatletter
\def\mbauditpoint#1#2#3{%
  \pgfextractx{\dimen0}{\pgfpointanchor{#2}{#3}}%
  \pgfextracty{\dimen2}{\pgfpointanchor{#2}{#3}}%
  \typeout{MB-AUDIT-TOKEN:#1:\the\dimen0:\the\dimen2}}
\mbauditpoint{NW}{current bounding box}{north west}
\mbauditpoint{SE}{current bounding box}{south east}
""".replace("MB-AUDIT-TOKEN", token)
    for name in names:
        probe += rf"\ifcsname pgf@sh@ns@{name}\endcsname\mbauditpoint{{p{name}}}{{{name}}}{{center}}\fi" + "\n"
    return source[:ends[0].start()] + probe + "\\makeatother\n\\end{scope}\n" + source[ends[0].start():]


def _global_anchors(page, compiler_output, token):
    pattern = re.compile(r"^" + re.escape(token) + r":([A-Za-z0-9]+):(-?[\d.]+)pt:(-?[\d.]+)pt$", re.M)
    rows = pattern.findall(compiler_output)
    raw = {name: (float(x), float(y)) for name,x,y in rows}
    if len(raw) != len(rows) or "NW" not in raw or "SE" not in raw:
        return {}
    left, top = raw.pop("NW")
    right, bottom = raw.pop("SE")
    # Check the coordinate frame against the actual standalone page before
    # trying to identify any path. Unknown clipping/export transforms stay unverified.
    frame = ((right-left)*_SCALE+2*_BORDER_BP, (top-bottom)*_SCALE+2*_BORDER_BP)
    if math.dist(frame, (page.rect.width, page.rect.height)) > .35:
        return {}
    return {name[1:]: ((x-left)*_SCALE+_BORDER_BP, (top-y)*_SCALE+_BORDER_BP)
            for name,(x,y) in raw.items() if name.startswith("p")}


def _cubic_at(points, t):
    u = 1-t
    weights = (u**3, 3*u*u*t, 3*u*t*t, t**3)
    return tuple(sum(a*b for a,b in zip(values,weights)) for values in zip(*points))


def _circle_profiles(page):
    """Recognize closed four-cubic oval strokes from the real PDF content."""
    profiles = []
    for path in page.get_drawings():
        items = path["items"]
        if path["type"] not in ("s", "fs") or len(items) != 4 or any(item[0] != "c" for item in items):
            continue
        if not path.get("stroke_opacity", 1) or not path.get("width", 0):
            continue
        curves = [tuple(tuple(point) for point in item[1:]) for item in items]
        if any(math.dist(curves[i][-1], curves[(i+1)%4][0]) > .06 for i in range(4)):
            continue
        rect = path["rect"]
        a, b = rect.width/2, rect.height/2
        if min(a,b) < 1:
            continue
        center = (rect.x0+a, rect.y0+b)
        probes = [_cubic_at(curve,t) for curve in curves for t in (0,.25,.5,.75)]
        if any(abs(((x-center[0])/a)**2+((y-center[1])/b)**2-1) > .015 for x,y in probes):
            continue
        profiles.append((center,a,b))
    return profiles


def review_compiled_circles(page, compiler_output: str, token: str, circles: tuple[Circumcircle,...]) -> dict:
    """Return measured evidence, or require the caller to show manual review.

    When the declared center and vertices can be associated with a full vector
    circle, actual discrepancies are blocking. Other curve forms stay unverified.
    """
    anchors = _global_anchors(page, compiler_output, token)
    candidates = _circle_profiles(page)
    verified = []
    for requirement in circles:
        name, vertices = requirement.center, requirement.vertices
        if name not in anchors or any(v not in anchors for v in vertices) or not candidates:
            return {"status":"unverified", "message":"圆的锚点或完整矢量圆无法核对，请检查预览。"}
        near = [candidate for candidate in candidates if math.dist(anchors[name], candidate[0]) <= .3]
        if len(near) == 1:
            center, a, b = near[0]
        elif len(candidates) == 1:
            center, a, b = candidates[0]
        else:
            return {"status":"unverified", "message":"无法把多条曲线唯一对应到题设的圆，请检查预览。"}
        tolerance = max(.25, .004*max(a,b))
        if math.dist(anchors[name],center) > tolerance or abs(a-b) > tolerance:
            raise TikzGeometryError(f"编译后的圆 {name} 圆心或圆形与题设不符。")
        radius = (a+b)/2
        off_circle = [v for v in vertices if abs(math.dist(anchors[v],center)-radius) > tolerance]
        if off_circle:
            raise TikzGeometryError(f"编译后的圆 {name} 未经过题设点 {'、'.join(off_circle)}，请检查半径和坐标单位。")
        verified.append({"center":name,"vertices":list(vertices)})
    return {"status":"verified", "circles":verified}
