import json
import math
import re
from unittest.mock import Mock

import pytest

from mathbank.prompts import build_tikz_draw_prompt
from mathbank.tikz_geometry import TikzGeometryError, render_geometry_reply
from mathbank.tikz_spatial import FrontFace, _stroke_intervals


STEM = "正方体 ABCD-A1B1C1D1 的棱长为 2，M 是 A1B1 的中点，N 是 M 到平面 ABCD 的垂足。画出线段 MN。"


def box_plan():
    return {
        "mathbank_geometry": 2,
        "view": "front_right",
        "solid": ["box", ["A", "B", "C", "D"], ["A1", "B1", "C1", "D1"], "正方体"],
        "points": [
            ["A", "below left", "xyz", 0, 0, 0],
            ["B", "below right", "xyz", 2, 0, 0],
            ["C", "right", "xyz", 2, 2, 0],
            ["D", "left", "xyz", 0, 2, 0],
            ["A1", "above left", "translate", "A", 0, 0, 2],
            ["B1", "above right", "translate", "B", 0, 0, 2],
            ["C1", "above", "translate", "C", 0, 0, 2],
            ["D1", "above left", "translate", "D", 0, 0, 2],
            ["M", "above", "midpoint", "A1", "B1"],
            ["N", "below", "foot_plane", "M", "A", "B", "C"],
        ],
        "draw": [["segment", "M", "N"]],
        "checks": [
            ["distance", "A", "B", 2, "棱长为 2"],
            ["angle", "B", "A", "D", 90, "正方体"],
            ["midpoint", "M", "A1", "B1", "M 是 A1B1 的中点"],
            ["on_plane", "N", "A", "B", "C", "N 是 M 到平面 ABCD 的垂足"],
            ["perpendicular_plane", "M", "N", "A", "B", "C", "N 是 M 到平面 ABCD 的垂足"],
            ["drawn_segment", "M", "N", "画出线段 MN"],
        ],
    }


def test_cube_edges_visibility_and_projection_of_spatial_right_angle():
    code = render_geometry_reply(json.dumps(box_plan()), context=STEM)
    pairs = re.findall(r"\\draw(\[dashed\])? \((\w+)\) -- \((\w+)\);", code)
    assert len(pairs) == 13
    dashed = {frozenset((a, b)) for style, a, b in pairs if style}
    # Viewed from x+, y-, z+: D is the single hidden vertex.
    assert dashed == {frozenset(("D", n)) for n in ("A", "C", "D1")}
    coords = {
        name: (float(x), float(y))
        for name, x, y in re.findall(r"\\coordinate \((\w+)\) at \(([-.\d]+),([-.\d]+)\);", code)
    }
    a, b, d = (coords[n] for n in ("A", "B", "D"))
    u, v = ((p[0]-a[0], p[1]-a[1]) for p in (b, d))
    paper_angle = math.degrees(math.acos(sum(x*y for x, y in zip(u, v)) / (math.hypot(*u)*math.hypot(*v))))
    assert abs(paper_angle-90) > 20
    assert code.count(r"\fill ") == 10
    assert "$A_{1}$" in code


def test_skew_box_cannot_be_passed_off_as_cube_with_distance_only():
    plan = box_plan()
    plan["checks"] = [plan["checks"][0]]
    plan["points"][2][3] = 2.5
    plan["points"][3][3] = .5
    with pytest.raises(TikzGeometryError, match="矩形"):
        render_geometry_reply(json.dumps(plan), context=STEM)


def test_spatial_line_intersection_does_not_infer_from_the_projection():
    plan = box_plan()
    plan["points"] += [["P", "above", "intersection", "A", "B", "B1", "C1"]]
    with pytest.raises(TikzGeometryError, match="异面"):
        render_geometry_reply(json.dumps(plan), context=STEM)


def test_named_points_do_not_make_a_missing_auxiliary_line_pass():
    plan = box_plan()
    plan["draw"] = []
    with pytest.raises(TikzGeometryError, match="未实际绘制"):
        render_geometry_reply(json.dumps(plan), context=STEM)


def test_bottom_face_boundary_order_and_plane_are_checked():
    plan = box_plan()
    plan["solid"][1] = ["A", "C", "B", "D"]
    plan["solid"][2] = ["A1", "C1", "B1", "D1"]
    with pytest.raises(TikzGeometryError):
        render_geometry_reply(json.dumps(plan), context=STEM)

    plan = box_plan()
    plan["points"] = plan["points"][:4] + [["S", "above", "xyz", 1, 1, 3]]
    plan["points"][3][-1] = .25
    plan["solid"] = ["pyramid", ["A", "B", "C", "D"], "S", "四棱锥"]
    plan["draw"] = []
    plan["checks"] = [["distance", "A", "B", 2, "AB=2"]]
    with pytest.raises(TikzGeometryError, match="不共面"):
        render_geometry_reply(json.dumps(plan), context="四棱锥 S-ABCD，AB=2。")


def test_prism_with_sloping_lateral_edges_keeps_exact_coordinates():
    plan = box_plan()
    plan["points"] = [row for row in plan["points"] if row[0] not in {"D", "D1", "M", "N"}]
    for row in plan["points"]:
        if row[0].endswith("1"):
            row[-3] = 1
    plan["solid"] = ["prism", ["A", "B", "C"], ["A1", "B1", "C1"], "斜三棱柱"]
    plan["draw"] = []
    plan["checks"] = [["xyz", "A1", 1, 0, 2, "A1=(1,0,2)"]]
    text = "斜三棱柱 ABC-A1B1C1，A1=(1,0,2)。"
    assert render_geometry_reply(json.dumps(plan), context=text).count(r"\draw") == 9
    plan["checks"][0][3] = 1
    with pytest.raises(TikzGeometryError, match="未满足条件"):
        render_geometry_reply(json.dumps(plan), context=text)


def test_pyramid_builds_apex_edges_and_checks_plane_foot():
    plan = box_plan()
    plan["points"] = plan["points"][:4] + [
        ["S", "above", "xyz", 1, 1, 3],
        ["H", "below", "foot_plane", "S", "A", "B", "C"],
    ]
    plan["solid"] = ["pyramid", ["A", "B", "C", "D"], "S", "正四棱锥"]
    plan["draw"] = [["segment", "S", "H"]]
    plan["checks"] = [["perpendicular_plane", "S", "H", "A", "B", "C", "H 为 S 到底面的垂足"]]
    code = render_geometry_reply(json.dumps(plan), context="正四棱锥 S-ABCD 中 H 为 S 到底面的垂足。")
    assert len(re.findall(r"\\draw(?:\[dashed\])? \((\w+)\) -- \((\w+)\);", code)) == 9


def test_segment_entering_and_leaving_face_footprint_is_split():
    face = FrontFace(((0., 0.), (2., 0.), (2., 2.), (0., 2.)), (0., 0., 1.), (0., 0., 1.))
    a, b = (-1., 1., 0.), (3., 1., 0.)
    strokes = list(_stroke_intervals(a, b, a[:2], b[:2], [face], 1e-6))
    assert [style for _, _, style in strokes] == ["", "[dashed]", ""]
    assert strokes[1][:2] == pytest.approx((.25, .75), abs=1e-6)
    a, b = (-1., 1., 2.), (3., 1., 2.)
    assert list(_stroke_intervals(a, b, a[:2], b[:2], [face], 1e-6)) == [(0., 1., "")]


def test_complex_planar_figures_require_the_checked_segment_to_be_drawn():
    from test_tikz_geometry import STEM as TRIANGLE, triangle_plan

    plan = triangle_plan()
    plan["checks"].append(["on_drawn_segment", "D", "A", "C", "D 为 B 到直线 AC 的垂足"])
    assert render_geometry_reply(json.dumps(plan), context=TRIANGLE)
    plan["draw"].remove(["segment", "A", "C"])
    with pytest.raises(TikzGeometryError, match="未实际绘制"):
        render_geometry_reply(json.dumps(plan), context=TRIANGLE)


def test_spatial_protocol_used_in_workbench_and_existing_tikz_uses_original_path(client, monkeypatch):
    import main

    assert '"mathbank_geometry":2' in build_tikz_draw_prompt(STEM, multimodal=False)
    assert '"mathbank_geometry":1' in build_tikz_draw_prompt("平面等腰三角形", multimodal=False)
    old_code = r"\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}"
    assert '"mathbank_geometry":2' not in build_tikz_draw_prompt(STEM, existing_tikz=old_code)
    monkeypatch.setenv("PREFER_DRAW_MODEL", "ZHONGZHAN_CLAUDE/gpt-6.1-sol:high")
    monkeypatch.setenv("ZHONGZHAN_CLAUDE_API_KEY", "test-drawing-key")
    provider_reply = Mock(status_code=200)
    provider_reply.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(box_plan())}}],
    }
    post = Mock(return_value=provider_reply)
    monkeypatch.setattr(main, "post_chat_completion", post)
    headers = {"X-Local-Token": main.LOCAL_TOKEN}
    generated = client.post("/api/ai/draw_tikz", data={"context": STEM, "instruction": "画图"}, headers=headers)
    assert generated.status_code == 200
    assert "\\coordinate (N)" in generated.json()["tikz_code"]
    assert post.call_count == 1
    assert '"mathbank_geometry":2' in post.call_args.args[1]["messages"][0]["content"]


def test_reference_right_angle_must_be_present_and_match_the_spatial_rays():
    plan = box_plan()
    with pytest.raises(TikzGeometryError, match="须列出原图"):
        render_geometry_reply(json.dumps(plan), context=STEM, has_reference=True)
    plan["reference_right_angles"] = [["A1", "A", "B"]]
    with pytest.raises(TikzGeometryError, match="漏画了原参考图在 A 点"):
        render_geometry_reply(json.dumps(plan), context=STEM, has_reference=True)
    plan["draw"].append(["right_angle", "B", "A", "A1"])
    source = render_geometry_reply(json.dumps(plan), context=STEM, has_reference=True)
    mark = re.search(
        r"\\draw \(([-.\d]+),([-.\d]+)\) -- \(([-.\d]+),([-.\d]+)\) -- \(([-.\d]+),([-.\d]+)\);",
        source,
    )
    assert mark is not None
    x1, y1, x2, y2, x3, y3 = map(float, mark.groups())
    # The mark is projected with its host face, and therefore needn't look
    # like a Euclidean right angle on paper.
    assert abs((x1-x2)*(x3-x2)+(y1-y2)*(y3-y2)) > .001

    plan["reference_right_angles"] = [["B", "A", "C"]]
    plan["draw"][-1] = ["right_angle", "B", "A", "C"]
    with pytest.raises(TikzGeometryError, match="空间夹角不垂直"):
        render_geometry_reply(json.dumps(plan), context=STEM, has_reference=True)


def test_explicit_marker_request_does_not_get_confused_with_unmarked_reference():
    plan = box_plan()
    plan["reference_right_angles"] = []
    source = render_geometry_reply(json.dumps(plan), context=STEM, has_reference=True)
    assert source.count(r"\draw") == 13
    request = "标出∠BAD的直角标记。"  # Force coverage of an explicit requested tuple.
    with pytest.raises(TikzGeometryError, match="未画出要求的 A 点直角标记"):
        render_geometry_reply(json.dumps(plan), context=STEM, instruction=request, has_reference=True)
    render_geometry_reply(json.dumps(plan), context=STEM, instruction="不要" + request, has_reference=True)
