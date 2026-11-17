import json
import math
import re
from unittest.mock import Mock

import pytest

from mathbank.tikz_geometry import TikzGeometryError, render_geometry_reply


STEM = "等腰三角形 ABC 中 AB=AC，BC=1，角 BAC=30 度。D 为 B 到直线 AC 的垂足。"


def triangle_plan():
    return {
        "mathbank_geometry": 1,
        "axes": False,
        "points": [
            ["B", "below left", "xy", 0, 0],
            ["C", "below right", "xy", 1, 0],
            ["A", "above", "isosceles_apex", "B", "C", 30, "left"],
            ["D", "right", "foot", "B", "A", "C"],
        ],
        "draw": [
            ["segment", "B", "C"], ["segment", "A", "B"],
            ["segment", "A", "C"], ["segment", "B", "D", "dashed"], ["angle", "B", "A", "C"],
        ],
        "checks": [
            ["distance", "B", "C", 1, "BC=1"],
            ["angle", "B", "A", "C", 30, "角 BAC=30 度"],
            ["on_line", "D", "A", "C", "D 为 B 到直线 AC 的垂足"],
            ["perpendicular", "B", "D", "A", "C", "D 为 B 到直线 AC 的垂足"],
        ],
    }


def tangent_plan():
    return {
        "mathbank_geometry": 1,
        "axes": False,
        "points": [
            ["O", "left", "xy", 0, 0],
            ["P", "right", "xy", 5, 1],
            ["T1", "above", "tangent", "P", "O", 2, "left"],
            ["T2", "below", "tangent", "P", "O", 2, "right"],
            ["M", "below right", "intersection", "O", "P", "T1", "T2"],
        ],
        "draw": [
            ["circle", "O", 2], ["segment", "O", "P", "dashed"],
            ["segment", "P", "T1"], ["segment", "P", "T2"], ["segment", "T1", "T2"],
        ],
        "checks": [
            ["on_conic", "T1", "O", 2, 2, "半径为 2"],
            ["perpendicular", "O", "T1", "P", "T1", "PT1 和 PT2 为切线"],
            ["y_less", "T2", "T1", "T1 在上方"],
        ],
    }


TANGENT_STEM = "圆 O 半径为 2，PT1 和 PT2 为切线，T1 在上方，M 是切点弦与 OP 的交点。"


def coordinates(code):
    return {
        name: (float(x), float(y))
        for name, x, y in re.findall(r"\\coordinate \((\w+)\) at \(([-.\d]+),([-.\d]+)\);", code)
    }


def test_angle_and_foot_use_same_named_endpoints():
    code = render_geometry_reply(json.dumps(triangle_plan()), context=STEM)
    coords = coordinates(code)
    a, b, c, d = (coords[n] for n in "ABCD")
    assert (a[1] - b[1]) / math.dist(b, c) == pytest.approx(1 / (2 * math.tan(math.pi / 12)))
    assert (b[0] - d[0]) * (c[0] - a[0]) + (b[1] - d[1]) * (c[1] - a[1]) == pytest.approx(0, abs=1e-7)
    assert r"\draw[dashed] (B) -- (D);" in code
    assert "{angle=B--A--C};" in code
    assert code.count(r"\fill") == 4


def test_tangent_and_intersection_share_the_radius_and_named_points():
    code = render_geometry_reply(json.dumps(tangent_plan()), context=TANGENT_STEM)
    coords = coordinates(code)
    assert coords["T1"][1] > coords["T2"][1]
    assert "$T_{1}$" in code and "$T_{2}$" in code
    radius = float(re.search(r"x radius=([.\d]+)cm", code)[1])
    for name in ("T1", "T2"):
        assert math.dist(coords["O"], coords[name]) == pytest.approx(radius)
    ratio = math.dist(coords["O"], coords["M"]) / math.dist(coords["O"], coords["P"])
    assert ratio == pytest.approx(2 / 13)
    bad = tangent_plan()
    bad["draw"][0][-1] = 3
    with pytest.raises(TikzGeometryError, match="参数不一致"):
        render_geometry_reply(json.dumps(bad), context=TANGENT_STEM)

    bad["draw"][0] = ["circle_through", "O", "T1"]
    assert render_geometry_reply(json.dumps(bad), context=TANGENT_STEM) == code


def test_right_angle_requires_perpendicular_rays():
    plan = triangle_plan()
    plan["draw"].append(["right_angle", "B", "D", "C"])
    code = render_geometry_reply(json.dumps(plan), context=STEM)
    assert "{right angle=" in code

    plan["draw"][-1] = ["right_angle", "B", "A", "C"]
    with pytest.raises(TikzGeometryError, match="垂直射线"):
        render_geometry_reply(json.dumps(plan), context=STEM)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda plan: plan["checks"][1].__setitem__(4, 60), "未满足条件"),
        (lambda plan: plan["checks"][1].__setitem__(-1, "原文并不存在"), "原文依据"),
        (lambda plan: plan["points"][0].__setitem__(0, "B)\\input"), "点名"),
        (lambda plan: plan["points"][1].__setitem__(-2, 0), "两点重合"),
    ],
)
def test_invalid_plan_is_not_used(change, reason):
    plan = triangle_plan()
    change(plan)
    with pytest.raises(TikzGeometryError, match=reason):
        render_geometry_reply(json.dumps(plan), context=STEM)


def test_workbench_accepts_plan_and_draws_it_through_normal_preview(client, monkeypatch):
    import main

    monkeypatch.setenv("PREFER_DRAW_MODEL", "ZHONGZHAN_CLAUDE/gpt-6.1-sol:high")
    monkeypatch.setenv("ZHONGZHAN_CLAUDE_API_KEY", "mock-drawing-key")
    response = Mock(status_code=200)
    response.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(triangle_plan())}}],
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(main, "post_chat_completion", post)
    headers = {"X-Local-Token": main.LOCAL_TOKEN}
    result = client.post(
        "/api/ai/draw_tikz", data={"instruction": "请画图", "context": STEM}, headers=headers,
    )
    assert result.status_code == 200, result.json()
    assert post.call_count == 1
    assert post.call_args.args[1]["reasoning_effort"] == "high"
    source = result.json()["tikz_code"]
    assert r"\draw[dashed] (B) -- (D);" in source

    # Workbench still uses the same render endpoint and exact returned source.
    renderer = Mock(return_value="/static/test_uploads/generated.png")
    monkeypatch.setattr(main, "compile_tikz_to_png", renderer)
    rendered = client.post("/api/render_tikz", data={"tikz_code": source}, headers=headers)
    assert rendered.status_code == 200
    renderer.assert_called_once_with(source)


def test_plan_rejected_after_model_truncation(monkeypatch):
    import main

    response = Mock(status_code=200)
    response.json.return_value = {
        "choices": [{"finish_reason": "length", "message": {"content": json.dumps(triangle_plan())}}],
    }
    transport = Mock(return_value=response)
    monkeypatch.setattr(main, "post_chat_completion", transport)
    provider = main.resolve_draw_provider("ZHONGZHAN_CLAUDE/gpt-6.1-sol:high")
    with pytest.raises(ValueError, match="截断"):
        main.request_tikz_completion(provider, "画图", geometry_context=(STEM, ""))
    assert transport.call_count == 1
