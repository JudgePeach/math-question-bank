"""Compute and draw a bounded set of planar constructions proposed by AI.

Assertions cover the conditions explicitly submitted with evidence. They do not
establish that the model interpreted or covered every condition in the source.
"""

from __future__ import annotations

import json
import math
import re

from mathbank.tikz_geometry_protocol import (
    TikzGeometryError, _LABEL_POSITIONS, _NAME, _TOL, _evidence, _label_code,
    _number, _point, _positive, _require, _row, _unique_object,
)
from mathbank.tikz_spatial import render_spatial_plan
from mathbank.tikz_circle_review import circumcircles

_PLAN_FIELDS = {"mathbank_geometry", "points", "draw", "checks", "axes"}


def _add(a, b):
    return a[0] + b[0], a[1] + b[1]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _scale(a, factor):
    return a[0] * factor, a[1] * factor


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _norm(a):
    return math.hypot(*a)


def _unit(a):
    length = _norm(a)
    _require(length > _TOL, "构造用的两点重合或方向向量为零。")
    return _scale(a, 1 / length)


def _normal(a):
    return -a[1], a[0]


def _choice(value, choices):
    _require(isinstance(value, str) and value in choices, "几何构造缺少有效的分支或样式。")
    return value


def _foot(p, a, b):
    axis = _unit(_sub(b, a))
    return _add(a, _scale(axis, _dot(_sub(p, a), axis)))


def _intersection(a, b, c, d):
    u, v = _unit(_sub(b, a)), _unit(_sub(d, c))
    denom = _cross(u, v)
    _require(abs(denom) > _TOL, "两直线平行、重合或过于接近平行。")
    return _add(a, _scale(u, _cross(_sub(c, a), v) / denom))


def _triangle_vertex(b, c, left_length, right_length, side):
    axis = _unit(_sub(c, b))
    base = _norm(_sub(c, b))
    _require(abs(left_length - right_length) + _TOL < base < left_length + right_length - _TOL,
             "三条边不能组成非退化三角形。")
    x = (left_length**2 - right_length**2 + base**2) / (2 * base)
    y = math.sqrt(max(0, left_length**2 - x**2))
    return _add(b, _add(_scale(axis, x), _scale(_normal(axis), side * y)))


def _tangent(p, center, radius, side):
    vec = _sub(p, center)
    distance = _norm(vec)
    _require(distance > radius + _TOL, "切线的起点须在圆外。")
    axis = _unit(vec)
    offset = radius**2 / distance
    height = radius * math.sqrt(1 - (radius / distance)**2)
    return _add(center, _add(_scale(axis, offset), _scale(_normal(axis), side * height)))


def _conic(p, direction, center, rx, ry, branch):
    """Intersect the line with the ellipse by mapping to a unit circle."""
    dx, dy = _unit(direction)
    offset = _sub(p, center)
    u = (dx / rx, dy / ry)
    delta = (offset[0] / rx, offset[1] / ry)
    axis = _unit(u)
    distance = _cross(axis, delta)
    _require(abs(distance) < 1 - _TOL, "直线须与圆或椭圆有两个不同的交点。")
    best = -_dot(delta, axis)
    t = (best + branch * math.sqrt(1 - distance**2)) / _norm(u)
    return _add(p, (t * dx, t * dy))


_CONSTRUCTIONS = {
    "xy": 2, "midpoint": 2, "foot": 3, "reflect": 3, "intersection": 4,
    "sss": 5, "isosceles_apex": 4, "incenter": 3, "bisector": 3,
    "tangent": 4, "conic": 7, "circumcenter": 3,
}


def _side(value):
    return 1 if _choice(value, ("left", "right")) == "left" else -1


def _construct(points, op, args):
    def get(*names):
        return [_point(points, name) for name in names]

    if op == "xy":
        return _number(args[0]), _number(args[1])
    if op == "midpoint":
        a, b = get(*args)
        _unit(_sub(b, a))
        return _scale(_add(a, b), .5)
    if op in ("foot", "reflect"):
        p, a, b = get(*args)
        foot = _foot(p, a, b)
        return foot if op == "foot" else _sub(_scale(foot, 2), p)
    if op == "intersection":
        return _intersection(*get(*args))
    if op == "circumcenter":
        origin, b, c = get(*args)
        u, v = _sub(b, origin), _sub(c, origin)
        _require(abs(_cross(_unit(u), _unit(v))) > _TOL, "三点共线，不能确定外接圆。")
        u2, v2 = _dot(u, u), _dot(v, v)
        return _add(origin, _scale((u2*v[1]-v2*u[1], u[0]*v2-v[0]*u2), 1/(2*_cross(u,v))))
    if op == "sss":
        b, c = get(*args[:2])
        return _triangle_vertex(b, c, _positive(args[2]), _positive(args[3]), _side(args[4]))
    if op == "isosceles_apex":
        b, c = get(*args[:2])
        base = _sub(c, b)
        axis, angle = _unit(base), _number(args[2])
        _require(0 < angle < 180, "等腰三角形顶角须介于 0° 与 180° 之间。")
        height = _norm(base) / (2 * math.tan(math.radians(angle / 2)))
        return _add(_scale(_add(b, c), .5), _scale(_normal(axis), _side(args[3]) * height))
    if op in ("incenter", "bisector"):
        a, b, c = get(*args)
        ab, ac, bc = _norm(_sub(a, b)), _norm(_sub(a, c)), _norm(_sub(b, c))
        _require(min(ab, ac, bc) > _TOL and abs(_cross(_sub(b, a), _sub(c, a))) > _TOL * ab * ac,
                 "内心或角平分线的三角形退化。")
        weighted = _add(_scale(b, ac), _scale(c, ab))
        if op == "bisector":
            return _scale(weighted, 1 / (ab + ac))
        return _scale(_add(weighted, _scale(a, bc)), 1 / (ab + ac + bc))
    if op == "tangent":
        p, center = get(*args[:2])
        return _tangent(p, center, _positive(args[2]), _side(args[3]))
    p, center = get(args[0], args[3])
    branch = -1 if _choice(args[6], ("first", "last")) == "first" else 1
    return _conic(p, (_number(args[1]), _number(args[2])), center,
                  _positive(args[4]), _positive(args[5]), branch)


_CHECK_ARGS = {
    "distance": 3, "equal_length": 4, "angle": 4, "perpendicular": 4,
    "parallel": 4, "on_line": 3, "midpoint": 3, "on_conic": 4,
    "same_side": 4, "opposite_side": 4, "on_segment": 3, "x_less": 2, "y_less": 2,
    "drawn_segment": 2, "on_drawn_segment": 3,
}


def _matches(points, op, args):
    def get(*names):
        return [_point(points, name) for name in names]

    def near(a, b):
        return abs(a - b) <= _TOL * max(1, abs(a), abs(b))

    if op == "distance":
        a, b = get(*args[:2])
        return near(_norm(_sub(a, b)), _positive(args[2]))
    if op == "equal_length":
        a, b, c, d = get(*args)
        first, second = _norm(_sub(a, b)), _norm(_sub(c, d))
        return min(first, second) > _TOL and near(first, second)
    if op == "angle":
        a, b, c = get(*args[:3])
        u, v = _unit(_sub(a, b)), _unit(_sub(c, b))
        return near(math.degrees(math.atan2(abs(_cross(u, v)), _dot(u, v))), _number(args[3]))
    if op in ("perpendicular", "parallel"):
        a, b, c, d = get(*args)
        u, v = _unit(_sub(b, a)), _unit(_sub(d, c))
        return abs((_dot if op == "perpendicular" else _cross)(u, v)) <= _TOL
    if op in ("same_side", "opposite_side"):
        p, q, a, b = get(*args)
        axis = _unit(_sub(b, a))
        left, right = _cross(axis, _sub(p, a)), _cross(axis, _sub(q, a))
        return min(abs(left), abs(right)) > _TOL and ((left * right > 0) == (op == "same_side"))
    if op in ("x_less", "y_less"):
        a, b = get(*args)
        index = 0 if op == "x_less" else 1
        return b[index] - a[index] > _TOL
    if op == "on_conic":
        p, center = get(*args[:2])
        x, y = _sub(p, center)
        return near((x / _positive(args[2]))**2 + (y / _positive(args[3]))**2, 1)
    p, a, b = get(*args)
    axis = _unit(_sub(b, a))
    if op == "midpoint":
        return near(_norm(_sub(p, _scale(_add(a, b), .5))), 0)
    offset = _sub(p, a)
    if abs(_cross(axis, offset)) > _TOL * max(1, _norm(offset)):
        return False
    return op == "on_line" or -_TOL <= _dot(axis, offset) <= _norm(_sub(b, a)) + _TOL


def _check_conditions(rows, points, sources, segments):
    for check in rows:
        op, *args, evidence = _row(check, range(4, 7))
        _require(isinstance(op, str) and _CHECK_ARGS.get(op) == len(args),
                 "几何计划使用了不支持的条件检查。")
        evidence = _evidence(evidence, sources)
        if op in ("drawn_segment", "on_drawn_segment"):
            a, b = args[-2:]
            _point(points, a)
            _point(points, b)
            _require(frozenset((a, b)) in segments, f"未实际绘制条件「{evidence}」所指的线段。")
            if op == "drawn_segment":
                continue
            op = "on_segment"
        _require(_matches(points, op, args), f"几何方案未满足条件「{evidence}」。")


def _parse_draw(rows, points):
    elements, extents, curves, segments = [], list(points.values()), {}, set()
    for row in rows:
        kind, *args = _row(row, range(3, 5))
        if kind == "segment":
            a, b, *style = _row(args, (2, 3))
            _unit(_sub(_point(points, a), _point(points, b)))
            option = f"[{_choice(style[0], ('dashed',))}]" if style else ""
            segments.add(frozenset((a, b)))
            elements.append(rf"\draw{option} ({a}) -- ({b});")
        elif kind in ("circle", "circle_through", "ellipse"):
            center, *radii = _row(args, (3,) if kind == "ellipse" else (2,))
            x, y = _point(points, center)
            if kind == "circle_through":
                rx = ry = _positive(_norm(_sub(_point(points, radii[0]), (x, y))))
            else:
                rx, ry = _positive(radii[0]), _positive(radii[-1])
            extents.extend(((x - rx, y - ry), (x + rx, y + ry)))
            _require(center not in curves, "同一圆心的多个圆或椭圆请使用普通 TikZ 源码。")
            curves[center] = (rx, ry)
        elif kind in ("angle", "right_angle"):
            a, b, c = _row(args, (3,))
            pa, pb, pc = (_point(points, name) for name in args)
            u, v = _unit(_sub(pa, pb)), _unit(_sub(pc, pb))
            orientation = _cross(u, v)
            _require(abs(orientation) > _TOL, "角标的两条射线不能共线。")
            degrees = math.degrees(math.atan2(abs(orientation), _dot(u, v)))
            if orientation < 0:
                a, c = c, a
            if kind == "right_angle":
                _require(abs(_dot(u, v)) <= _TOL, "只有垂直射线可绘制直角标记。")
                elements.append(rf"\pic[draw,angle radius=6pt] {{right angle={a}--{b}--{c}}};")
            else:
                eccentricity = 2.4 if degrees <= 35 else 1.65
                elements.append(
                    rf'\pic[draw,angle radius=14pt,angle eccentricity={eccentricity},'
                    rf'"${degrees:.5g}^\circ$" {{fill=white,inner sep=1pt,font=\small}}]'
                    rf" {{angle={a}--{b}--{c}}};"
                )
        else:
            raise TikzGeometryError("几何计划使用了不支持的图元。")
    return elements, extents, curves, segments


def render_geometry_reply(raw, *, context="", instruction="", has_reference=False):
    """Return deterministic TikZ from a structured reply, or None for raw TikZ."""
    text = raw.strip()
    if text.startswith("```json"):
        fenced = re.fullmatch(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        _require(fenced is not None and fenced[1].startswith("{"), "几何计划的 JSON 代码块不完整。")
        text = fenced[1]
    if not text.startswith("{"):
        return None
    _require(len(text) <= 24000, "几何计划过长。")
    try:
        plan = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise TikzGeometryError("几何计划不是完整 JSON。") from exc
    if isinstance(plan, dict) and type(plan.get("mathbank_geometry")) is int and plan["mathbank_geometry"] == 2:
        return render_spatial_plan(plan, (context or "", instruction or ""), has_reference=has_reference)
    _require(isinstance(plan, dict) and set(plan) == _PLAN_FIELDS
             and type(plan["mathbank_geometry"]) is int and plan["mathbank_geometry"] == 1
             and type(plan["axes"]) is bool, "几何计划的字段或版本不符。")
    _require(isinstance(plan["points"], list) and 2 <= len(plan["points"]) <= 24
             and isinstance(plan["draw"], list) and 1 <= len(plan["draw"]) <= 48
             and isinstance(plan["checks"], list) and 1 <= len(plan["checks"]) <= 32,
             "几何计划的点、图元或条件数量不符。")

    points, labels, required_curves = {}, [], []
    for row in plan["points"]:
        name, label, op, *args = _row(row, range(5, 11))
        _require(isinstance(name, str) and _NAME.fullmatch(name) and name not in points,
                 "几何计划有无效或重复点名。")
        _choice(label, _LABEL_POSITIONS)
        _require(isinstance(op, str) and _CONSTRUCTIONS.get(op) == len(args),
                 "几何计划使用了不支持的构造或参数。")
        try:
            x, y = _construct(points, op, args)
            points[name] = (_number(x), _number(y))
        except TikzGeometryError as exc:
            raise TikzGeometryError(f"点 {name} 的构造失败。{exc}") from exc
        if op == "tangent":
            required_curves.append((args[1], (args[2], args[2])))
        elif op == "conic":
            required_curves.append((args[3], (args[4], args[5])))
        labels.append(_label_code(name, label))

    elements, extents, curves, segments = _parse_draw(plan["draw"], points)
    _check_conditions(plan["checks"], points, (context or "", instruction or ""), segments)
    for circle in circumcircles((context or "") + "\n" + (instruction or "")):
        if circle.center not in points and not all(v in points for v in circle.vertices):
            continue  # The current figure may be another diagram in the question.
        _require(circle.center in curves and circle.center in points and all(v in points for v in circle.vertices),
                 f"几何计划缺少题设的外接圆 {circle.center} 或其顶点。")
        radius_x, radius_y = curves[circle.center]
        _require(math.isclose(radius_x, radius_y, rel_tol=_TOL) and
                 all(math.isclose(_norm(_sub(points[v], points[circle.center])), radius_x, rel_tol=_TOL)
                     for v in circle.vertices), f"绘制的圆 {circle.center} 未经过题设的所有顶点。")
    for op, *args, _evidence in plan["checks"]:
        if op == "on_conic":
            required_curves.append((args[1], (args[2], args[3])))
    _require(all(center in curves and all(math.isclose(given, actual, rel_tol=_TOL)
                                          for given, actual in zip(radii, curves[center]))
                 for center, radii in required_curves),
             "绘制的圆或椭圆与构造、验算使用的参数不一致。")
    # An isolated labeled point is legitimate (e.g. a focus or coordinate origin).
    # Require each computed point to have a distinct visible location.
    locations = list(points.values())
    if plan["axes"]:
        extents.append((0, 0))
    low_x, high_x = min(x for x, y in extents), max(x for x, y in extents)
    low_y, high_y = min(y for x, y in extents), max(y for x, y in extents)
    span = max(high_x - low_x, high_y - low_y)
    _require(span > _TOL, "绘图范围退化。")
    _require(all(_norm(_sub(a, b)) > _TOL * span
                 for i, a in enumerate(locations) for b in locations[i + 1:]),
             "几何方案存在重合或无法区分的点。")
    # Bake an isotropic display transform into coordinates. This avoids large TeX
    # dimensions and applies the same scale to curve radii and segment endpoints.
    unit = 6.5 / span
    pivot = (0, 0) if plan["axes"] else (low_x, low_y)
    origin_x, origin_y = pivot

    def display_point(x, y):
        return f"{(x-origin_x)*unit:.9f},{(y-origin_y)*unit:.9f}"

    code = [r"\begin{tikzpicture}[line width=.6pt]"]
    code.extend(rf"\coordinate ({name}) at ({display_point(x,y)});" for name, (x,y) in points.items())
    if plan["axes"]:
        margin = span * .1
        code.extend((
            rf"\draw[->] ({display_point(low_x-margin,0)}) -- ({display_point(high_x+margin,0)}) node[right] {{$x$}};",
            rf"\draw[->] ({display_point(0,low_y-margin)}) -- ({display_point(0,high_y+margin)}) node[above] {{$y$}};",
        ))
    for center, (rx, ry) in curves.items():
        code.append(rf"\draw ({center}) ellipse [x radius={rx*unit:.9f}cm,y radius={ry*unit:.9f}cm];")
    return "\n".join([*code, *elements, *labels, r"\end{tikzpicture}"])
