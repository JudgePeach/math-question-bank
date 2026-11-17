"""Checked constructions and hidden-line projection for one convex solid."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

from mathbank.tikz_geometry_protocol import (
    TikzGeometryError, _LABEL_POSITIONS, _NAME, _TOL, _evidence, _label_text,
    _number, _point, _positive, _require, _row,
)

Vec = tuple[float, float, float]
_PLAN_FIELDS = {"mathbank_geometry", "points", "solid", "draw", "checks", "view"}
_VIEWS = {
    "front_right": (4, -7, 5), "front_left": (-4, -7, 5),
    "back_right": (4, 7, 5), "back_left": (-4, 7, 5),
}
_LABEL_DIRECTIONS = {
    "above": (0, 1), "below": (0, -1), "left": (-1, 0), "right": (1, 0),
    "above left": (-1, 1), "above right": (1, 1),
    "below left": (-1, -1), "below right": (1, -1),
}
_COUNT = {"三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
_SHAPE = re.compile(r"正方体|长方体|(?:正|直|斜)?[三四五六七八]?棱柱|正?[三四五六七八]?棱锥|正?四面体")
_REQUESTED_ANGLE = re.compile(r"(?:标出|添加|画出)(?:∠|\\angle)([A-Z])([A-Z])([A-Z])(?:的)?直角(?:标记|符号)")


def spatial_geometry_prompt_applicable(text: str) -> bool:
    return bool(_SHAPE.search(text))


def _add(a: Vec, b: Vec) -> Vec:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _sub(a: Vec, b: Vec) -> Vec:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(a: Vec, k: float) -> Vec:
    return a[0] * k, a[1] * k, a[2] * k


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0]


def _norm(a: Vec) -> float:
    return math.hypot(*a)


def _unit(a: Vec) -> Vec:
    length = _norm(a)
    _require(length > _TOL, "空间构造的方向向量为零或过短。")
    return _scale(a, 1 / length)


def _normal(a: Vec, b: Vec, c: Vec) -> Vec:
    u, v = _unit(_sub(b, a)), _unit(_sub(c, a))
    return _unit(_cross(u, v))


def _mean(points) -> Vec:
    return tuple(sum(values) / len(values) for values in zip(*points))


def _nearest_point(p: Vec, a: Vec, axis: Vec) -> Vec:
    return _add(a, _scale(axis, _dot(_sub(p, a), axis)))


def _same_length(value: float, expected: float) -> bool:
    return math.isclose(value, expected, rel_tol=_TOL, abs_tol=1e-9)


_CONSTRUCTIONS = {
    "xyz": 3, "translate": 4, "midpoint": 2, "divide": 3,
    "foot_line": 3, "foot_plane": 4, "reflect_plane": 4,
    "intersection": 4, "line_plane": 5,
}


def _construct(points, op, args):
    def get(*names):
        return [_point(points, name) for name in names]

    if op == "xyz":
        return tuple(_number(x) for x in args)
    if op == "translate":
        p = _point(points, args[0])
        return _add(p, tuple(_number(x) for x in args[1:]))
    if op in {"midpoint", "divide"}:
        a, b = get(*args[:2])
        t = .5 if op == "midpoint" else _number(args[2])
        v = _sub(b, a)
        _unit(v)
        return _add(a, _scale(v, t))
    if op == "foot_line":
        p, a, b = get(*args)
        return _nearest_point(p, a, _unit(_sub(b, a)))
    if op in {"foot_plane", "reflect_plane"}:
        p, a, b, c = get(*args)
        n = _normal(a, b, c)
        dx = _dot(_sub(p, a), n)
        return _sub(p, _scale(n, dx if op == "foot_plane" else 2*dx))
    if op == "line_plane":
        a, b, p, q, r = get(*args)
        axis, n = _unit(_sub(b, a)), _normal(p, q, r)
        divisor = _dot(axis, n)
        _require(abs(divisor) > _TOL, "直线与平面平行或在面内，没有唯一交点。")
        return _add(a, _scale(axis, _dot(_sub(p, a), n)/divisor))
    a, b, c, d = get(*args)
    u, v = _unit(_sub(b, a)), _unit(_sub(d, c))
    cross = _cross(u, v)
    sine = _norm(cross)
    _require(sine > _TOL, "平行直线没有唯一空间交点。")
    diff = _sub(c, a)
    _require(abs(_dot(diff, cross)) <= _TOL*sine*max(1, _norm(diff)),
             "两直线异面，没有空间交点。")
    t = _dot(_cross(diff, v), cross) / sine**2
    return _add(a, _scale(u, t))


_CHECKS = {
    "xyz": 4, "distance": 3, "equal_length": 4, "angle": 4, "perpendicular": 4,
    "parallel": 4, "on_line": 3, "on_segment": 3, "midpoint": 3, "on_plane": 4,
    "perpendicular_plane": 5, "parallel_plane": 5, "perpendicular_planes": 6,
    "drawn_segment": 2,
}


def _matches(points, op, args, eps):
    def get(*names):
        return [_point(points, name) for name in names]

    if op == "xyz":
        expected = tuple(_number(value) for value in args[1:])
        return all(_same_length(a, b) for a, b in zip(_point(points, args[0]), expected))
    if op == "distance":
        return _same_length(math.dist(*get(*args[:2])), _positive(args[2]))
    if op == "equal_length":
        a, b, c, d = get(*args)
        ab, cd = math.dist(a, b), math.dist(c, d)
        return min(ab, cd) > eps and _same_length(ab, cd)
    if op == "angle":
        a, b, c = get(*args[:3])
        u, v = _unit(_sub(a, b)), _unit(_sub(c, b))
        actual = math.degrees(math.atan2(_norm(_cross(u, v)), _dot(u, v)))
        return abs(actual - _number(args[3])) <= 1e-4
    if op in {"perpendicular", "parallel"}:
        a, b, c, d = get(*args)
        u, v = _unit(_sub(b, a)), _unit(_sub(d, c))
        return abs(_dot(u, v)) <= _TOL if op == "perpendicular" else _norm(_cross(u, v)) <= _TOL
    if op == "on_plane":
        p, a, b, c = get(*args)
        return abs(_dot(_sub(p, a), _normal(a, b, c))) <= eps
    if op == "perpendicular_planes":
        a, b, c, d, e, f = get(*args)
        return abs(_dot(_normal(a, b, c), _normal(d, e, f))) <= _TOL
    if op in {"perpendicular_plane", "parallel_plane"}:
        a, b, p, q, r = get(*args)
        u, n = _unit(_sub(b, a)), _normal(p, q, r)
        if op == "perpendicular_plane":
            return _norm(_cross(u, n)) <= _TOL
        return abs(_dot(u, n)) <= _TOL and abs(_dot(_sub(a, p), n)) > eps

    p, a, b = get(*args)
    offset, axis = _sub(p, a), _unit(_sub(b, a))
    if op == "midpoint":
        return math.dist(p, _mean([a, b])) <= eps
    if _norm(_cross(axis, offset)) > eps:
        return False
    return op == "on_line" or -eps <= _dot(offset, axis) <= math.dist(a, b) + eps


@dataclass(frozen=True)
class Face:
    vertices: tuple[str, ...]
    normal: Vec


def _cycle(polygon):
    return zip(polygon, polygon[1:] + polygon[:1])


def _face(points, names, interior, eps):
    polygon = [points[name] for name in names]
    a, b, c = polygon[:3]
    normal = _normal(a, b, c)
    _require(all(abs(_dot(_sub(p, a), normal)) <= eps for p in polygon),
             "同一面上的顶点不共面。")
    # Every other vertex must lie strictly to the same side of each directed
    # edge. Checking only consecutive turns would also accept star polygons.
    for start, end in _cycle(polygon):
        edge = _unit(_sub(end, start))
        _require(all(_dot(_cross(edge, _sub(p, start)), normal) > eps
                     for p in polygon if p != start and p != end),
                 "底面或侧面退化，或顶点未按凸多边形边界排列。")
    if _dot(_sub(interior, a), normal) > 0:
        return Face(tuple(reversed(names)), _scale(normal, -1))
    return Face(tuple(names), normal)


def _vertex_list(value, points, n=None):
    _require(isinstance(value, list) and 3 <= len(value) <= 8 and (n is None or len(value) == n),
             "棱柱或棱锥的底面需要按顺序列出 3 至 8 个顶点。")
    for name in value:
        _point(points, name)
    _require(len(set(value)) == len(value), "同一底面包含重复顶点。")
    return value


def _shape_from_source(evidence, kind, count):
    matches = _SHAPE.findall(evidence)
    _require(len(set(matches)) == 1, "立体类型须用题干中明确的单一名称标明。")
    shape = matches[0]
    expected = ("box" if shape in {"正方体", "长方体"} else
                "prism" if shape.endswith("棱柱") else "pyramid")
    _require(kind == expected, f"立体类型与题设「{shape}」不一致。")
    for numeral, sides in _COUNT.items():
        if numeral in shape:
            _require(count == (3 if shape.endswith("四面体") else sides),
                     f"底面顶点数与题设「{shape}」不一致。")
            break
    return shape


def _solid_faces(points, solid, sources):
    kind, base, other, evidence = _row(solid, (4,))
    _require(kind in ("box", "prism", "pyramid"), "仅支持单个长方体、凸棱柱或凸棱锥。")
    bottom = _vertex_list(base, points)
    shape = _shape_from_source(_evidence(evidence, sources), kind, len(bottom))
    vertices = [points[name] for name in bottom]
    a, b, c = vertices[:3]
    normal = _normal(a, b, c)
    longest_base_edge = max(math.dist(a, b) for a, b in _cycle(vertices))
    eps = _TOL*max(1, longest_base_edge)
    if kind == "pyramid":
        apex = _point(points, other)
        _require(other not in bottom and abs(_dot(_sub(apex, a), normal)) > eps,
                 "棱锥顶点在底面内，无法形成立体。")
        offset = _sub(apex, _mean(vertices))
        faces = [bottom, *[[u, v, other] for u, v in _cycle(bottom)]]
        names = bottom + [other]
    else:
        top = _vertex_list(other, points, len(bottom))
        _require(not set(bottom) & set(top), "棱柱上下底面不能共用顶点。")
        offset = _sub(points[top[0]], a)
        _require(abs(_dot(offset, normal)) > eps, "棱柱两底面重合或过近。")
        _require(all(math.dist(points[v], _add(points[u], offset)) <= eps
                     for u, v in zip(bottom, top)), "棱柱上下底面并非同一平移。")
        sides = [[u, v, w, t] for (u, v), (t, w) in zip(_cycle(bottom), _cycle(top))]
        faces = [bottom, top, *sides]
        names = bottom + top

    edges = [_sub(v, u) for u, v in _cycle(vertices)]
    lengths = [_norm(edge) for edge in edges]
    if kind == "box":
        _require(len(bottom) == 4 and all(abs(_dot(_unit(u), _unit(v))) <= _TOL
                                          for u, v in _cycle(edges)),
                 "长方体底面须为矩形。")
    if kind == "box" or (shape.startswith(("直", "正")) and shape != "正四面体"):
        _require(_norm(_cross(_unit(offset), normal)) <= _TOL,
                 "侧棱不垂直于底面，或正棱锥顶点未在底面中心的法线上。")
    if shape.startswith("斜"):
        _require(_norm(_cross(_unit(offset), normal)) > _TOL, "斜棱柱的侧棱不能垂直于底面。")
    if shape.startswith("正"):
        unit_edges = [_unit(edge) for edge in edges]
        turns = [_dot(u, v) for u, v in _cycle(unit_edges)]
        _require(all(_same_length(lengths[0], x) for x in lengths)
                 and all(abs(turns[0] - x) <= _TOL for x in turns), "题设要求正多边形底面。")
    if shape == "正方体":
        _require(_same_length(_norm(offset), lengths[0]), "正方体的棱长应相等。")
    if shape == "正四面体":
        _require(all(_same_length(math.dist(points[other], p), lengths[0]) for p in vertices),
                 "正四面体的全部棱长应相等。")

    interior = _mean([points[name] for name in names])
    return [_face(points, face, interior, eps) for face in faces]


@dataclass(frozen=True)
class Projection:
    camera: Vec
    right: Vec
    vertical: Vec

    @classmethod
    def from_view(cls, direction):
        camera = _unit(direction)
        right = _unit(_cross((0, 0, 1), camera))
        return cls(camera, right, _cross(camera, right))

    def point(self, point):
        return _dot(point, self.right), _dot(point, self.vertical)


def _bounds(projected):
    xs, ys = zip(*projected)
    return min(xs), min(ys), max(max(xs)-min(xs), max(ys)-min(ys))


def _projection(points, view):
    _require(isinstance(view, str) and (view == "auto" or view in _VIEWS), "空间投影视角不受支持。")
    directions = _VIEWS.values() if view == "auto" else [_VIEWS[view]]
    world = list(points.values())
    scores = []
    for direction in directions:
        projection = Projection.from_view(direction)
        projected = [projection.point(p) for p in world]
        _, _, span = _bounds(projected)
        spacing = min(math.dist(p, q) for i, p in enumerate(projected) for q in projected[i+1:])
        scores.append((spacing/span if span else 0, projection))
    score, chosen = max(scores, key=lambda item: item[0])
    _require(score >= .008, "选定视角下多个点重叠，请调整视角或使用原图。")
    return chosen


def _clip_positive(interval, left, right):
    """Clip t to f(t) >= 0, given f(0)=left and f(1)=right."""
    if left >= 0 and right >= 0:
        return interval
    if left < 0 and right < 0:
        return None
    start, stop = interval
    t = left/(left-right)
    return (max(start, t), stop) if left < 0 else (start, min(stop, t))


@dataclass(frozen=True)
class FrontFace:
    outline: tuple[tuple[float, float], ...]
    normal: Vec
    origin: Vec


def _front_faces(points, faces, projection):
    result = []
    for face in faces:
        if _dot(face.normal, projection.camera) > _TOL:
            vertices = tuple(points[name] for name in face.vertices)
            result.append(FrontFace(tuple(projection.point(p) for p in vertices), face.normal, vertices[0]))
    return result


def _hidden_interval(a, b, pa, pb, face, eps):
    # Front faces have counterclockwise projected outlines. First clip the
    # segment to the footprint, then keep only the part strictly behind it.
    interval = (0., 1.)
    for (x, y), (ex, ey) in _cycle(face.outline):
        u, v = ex-x, ey-y
        margin = eps*math.hypot(u, v)
        near = u*(pa[1]-y)-v*(pa[0]-x)+margin
        far = u*(pb[1]-y)-v*(pb[0]-x)+margin
        interval = _clip_positive(interval, near, far)
        if interval is None:
            return None
    interval = _clip_positive(interval, _dot(_sub(face.origin, a), face.normal)-eps,
                              _dot(_sub(face.origin, b), face.normal)-eps)
    if interval and interval[1]-interval[0] > 1e-5:
        return interval
    return None


def _stroke_intervals(a, b, pa, pb, faces, eps):
    intervals = sorted(interval for face in faces
                       if (interval := _hidden_interval(a, b, pa, pb, face, eps)))
    merged = []
    for lo, hi in intervals:
        if merged and lo <= merged[-1][1] + 1e-5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))

    cursor = 0.
    for lo, hi in merged:
        if lo-cursor > 1e-5:
            yield cursor, lo, ""
        yield lo, hi, "[dashed]"
        cursor = hi
    if 1-cursor > 1e-5:
        yield cursor, 1., ""


def _segments_and_markers(faces, draw, points):
    edges = {}
    markers = {}
    for face in faces:
        for a, b in _cycle(face.vertices):
            edges.setdefault(frozenset((a, b)), (a, b, False))
    for row in draw:
        kind, a, b, *style = _row(row, (3, 4))
        if kind == "right_angle" and len(style) == 1:
            c = style[0]
            pa, pb, pc = (_point(points, name) for name in (a, b, c))
            u, v = _unit(_sub(pa, pb)), _unit(_sub(pc, pb))
            _require(abs(_dot(u, v)) <= _TOL, f"{b} 点的空间夹角不垂直，不能绘制直角标记。")
            key = (b, frozenset((a, c)))
            _require(key not in markers and len(markers) < 8, "空间直角标记重复或过多。")
            markers[key] = (a, b, c)
            continue
        _require(kind == "segment" and (not style or style == ["dashed"]), "立体构造只支持线段、虚线和直角标记。")
        _require(math.dist(_point(points, a), _point(points, b)) > _TOL, "线段两端重合。")
        key = frozenset((a, b))
        # The solid's edges always use depth-based visibility. Explicit dash
        # applies only to supplemental segments such as auxiliary lines.
        edges.setdefault(key, (a, b, bool(style)))
    return list(edges.values()), markers


def _verify_requested_markers(markers, instruction):
    text = re.sub(r"\\[()\[\]]|\$|\s+", "", instruction)
    for clause in re.split(r"[。；，;!！\n]", text):
        if any(negation in clause for negation in ("不", "勿", "禁止", "别", "去掉", "移除", "无需")):
            continue
        for match in _REQUESTED_ANGLE.finditer(clause):
            a, b, c = match.groups()
            _require((b, frozenset((a,c))) in markers,
                     f"立体图未画出要求的 {b} 点直角标记。")


def _verify_reference_markers(plan, points, markers, has_reference):
    if not has_reference and "reference_right_angles" not in plan:
        return
    required = plan.get("reference_right_angles")
    _require(isinstance(required, list) and len(required) <= 8,
             "立体参考图须列出原图中可见的直角标记，没有则明确返回空列表。")
    keys = set()
    for row in required:
        a, b, c = _row(row, (3,))
        for name in (a, b, c):
            _point(points, name)
        key = (b, frozenset((a, c)))
        _require(key not in keys, "原图直角标记不能重复。")
        keys.add(key)
        _require(key in markers, f"立体图漏画了原参考图在 {b} 点的直角标记。")


def _marker_strokes(points, markers, project_to_cm, unit):
    for a, b, c in markers.values():
        base = points[b]
        u, v = _sub(points[a], base), _sub(points[c], base)
        side = min(.23/unit, .18*_norm(u), .18*_norm(v))
        u, v = _scale(_unit(u), side), _scale(_unit(v), side)
        corners = (_add(base, u), _add(base, _add(u, v)), _add(base, v))
        first, middle, last = (project_to_cm(point) for point in corners)
        area = abs((first[0]-middle[0])*(last[1]-middle[1]) - (first[1]-middle[1])*(last[0]-middle[0]))
        _require(area > .0015, f"{b} 点的直角标记在当前视角过于扁平，请调整投影视角。")
        yield r"\draw " + " -- ".join(f"({x:.9f},{y:.9f})" for x,y in (first,middle,last)) + ";"


def _overlaps(box, other):
    left, bottom, right, top = box
    x, y, nx, ny = other
    return left < nx and x < right and bottom < ny and y < top


def _crosses_box(a, b, box):
    left, bottom, right, top = box
    interval = (0., 1.)
    for near, far in ((a[0]-left, b[0]-left), (right-a[0], right-b[0]),
                      (a[1]-bottom, b[1]-bottom), (top-a[1], top-b[1])):
        interval = _clip_positive(interval, near, far)
        if interval is None or interval[1] < interval[0]:
            return False
    return True


def _placed_labels(points, segments, preferences):
    """Use the suggested side when clear, or move the label away from lines."""
    paths = [(points[a], points[b]) for a, b, _ in segments]
    occupied = [(x-.07, y-.07, x+.07, y+.07) for x, y in points.values()]
    for name, (x, y) in points.items():
        half_width = .105 + .082*len(name)
        half_height = .15
        preferred = _LABEL_DIRECTIONS[preferences[name]]
        best = None
        for extra in (0., .13, .28):
            for dx, dy in _LABEL_DIRECTIONS.values():
                ox = dx*(half_width+.08+extra)
                oy = dy*(half_height+.08+extra)
                cx, cy = x+ox, y+oy
                box = (cx-half_width, cy-half_height, cx+half_width, cy+half_height)
                collisions = (5*sum(_overlaps(box, other) for other in occupied)
                              + sum(_crosses_box(a, b, box) for a, b in paths))
                departure = abs(dx-preferred[0]) + abs(dy-preferred[1])
                score = (collisions, departure+8*extra, math.hypot(ox, oy))
                if best is None or score < best[0]:
                    best = score, ox, oy, box
        _, ox, oy, box = best
        occupied.append(box)
        yield (rf"\fill ({name}) circle[radius=1pt] node[inner sep=0pt,"
               rf"xshift={ox:.4f}cm,yshift={oy:.4f}cm] {{${_label_text(name)}$}};")


def render_spatial_plan(plan, sources, *, has_reference=False):
    _require(set(plan) - {"reference_right_angles"} == _PLAN_FIELDS, "立体几何计划字段不符。")
    _require(isinstance(plan["points"], list) and 4 <= len(plan["points"]) <= 24
             and isinstance(plan["draw"], list) and len(plan["draw"]) <= 48
             and isinstance(plan["checks"], list) and 1 <= len(plan["checks"]) <= 32,
             "立体几何计划的点、图元或条件数量不符。")
    points, labels = {}, {}
    for row in plan["points"]:
        name, label, op, *args = _row(row, range(5, 9))
        _require(isinstance(name, str) and _NAME.fullmatch(name) and name not in points,
                 "立体几何计划有无效或重复点名。")
        _require(isinstance(label, str) and label in _LABEL_POSITIONS, "立体图的点名位置无效。")
        _require(isinstance(op, str) and _CONSTRUCTIONS.get(op) == len(args), "空间构造或参数不受支持。")
        try:
            points[name] = tuple(_number(x) for x in _construct(points, op, args))
        except TikzGeometryError as exc:
            raise TikzGeometryError(f"点 {name} 的空间构造失败。{exc}") from exc
        labels[name] = label

    faces = _solid_faces(points, plan["solid"], sources)
    world = list(points.values())
    solid_points = [points[name] for name in {vertex for face in faces for vertex in face.vertices}]
    eps = _TOL * max(1, max(math.dist(a, b) for a in solid_points for b in solid_points))
    _require(all(math.dist(a, b) > eps for i, a in enumerate(world) for b in world[i+1:]),
             "空间图存在重合或无法区分的点。")
    segments, markers = _segments_and_markers(faces, plan["draw"], points)
    _verify_requested_markers(markers, sources[1])
    _verify_reference_markers(plan, points, markers, has_reference)
    drawn = {frozenset((a, b)) for a, b, _ in segments}
    for a, b, c in markers.values():
        _require(frozenset((a, b)) in drawn and frozenset((b, c)) in drawn,
                 f"{b} 点的直角标记须对应两条实际绘制的线段。")
    for row in plan["checks"]:
        op, *args, evidence = _row(row, range(4, 9))
        _require(isinstance(op, str) and _CHECKS.get(op) == len(args), "几何计划使用了不支持的空间条件检查。")
        evidence = _evidence(evidence, sources)
        if op == "drawn_segment":
            for name in args:
                _point(points, name)
            _require(frozenset(args) in drawn, f"未实际绘制条件「{evidence}」所指的线段。")
            continue
        _require(_matches(points, op, args, eps), f"空间几何方案未满足条件「{evidence}」。")

    projection = _projection(points, plan["view"])
    projected = {name: projection.point(p) for name, p in points.items()}
    low_x, low_y, span = _bounds(projected.values())
    unit = 6.5/span
    display = {name: ((x-low_x)*unit, (y-low_y)*unit) for name, (x, y) in projected.items()}
    code = [r"\begin{tikzpicture}[line width=.6pt]"]
    for name, (x, y) in display.items():
        code.append(rf"\coordinate ({name}) at ({x:.9f},{y:.9f});")
    front = _front_faces(points, faces, projection)
    dashed, visible = [], []
    for a, b, force_dash in segments:
        strokes = [(0, 1, "[dashed]")] if force_dash else _stroke_intervals(
            points[a], points[b], projected[a], projected[b], front, eps)
        for lo, hi, style in strokes:
            start = f"({a})" if lo <= 1e-5 else f"($({a})!{lo:.8f}!({b})$)"
            end = f"({b})" if hi >= 1-1e-5 else f"($({a})!{hi:.8f}!({b})$)"
            (dashed if style else visible).append(rf"\draw{style} {start} -- {end};")
    def project_to_cm(point):
        x, y = projection.point(point)
        return (x-low_x)*unit, (y-low_y)*unit

    return "\n".join([*code, *dashed, *visible,
                      *_marker_strokes(points, markers, project_to_cm, unit),
                      *_placed_labels(display, segments, labels), r"\end{tikzpicture}"])
