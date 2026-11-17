"""A preview can compile and still fail the geometry of the actual vector circle."""

import json
import os
import shutil
from pathlib import Path

import pytest

from mathbank.tikz_circle_review import Circumcircle, circumcircles
from mathbank.tikz_geometry import TikzGeometryError, render_geometry_reply


STEM = (
    r"如图，等腰梯形\(\text{ABCD}\)的底边长分别为\(8\)和\(6\)，高为\(7\)，"
    r"圆\(E\)为等腰梯形\(\text{ABCD}\)的外接圆。"
)

RAW = r"""\begin{tikzpicture}[x=.46cm,y=.46cm]
\coordinate (A) at (-4,0);
\coordinate (B) at (4,0);
\coordinate (C) at (3,7);
\coordinate (D) at (-3,7);
\coordinate (E) at (0,3);
\draw (A)--(B)--(C)--(D)--cycle;
\draw (E) circle[radius=RADIUS];
\fill (E) circle[radius=1.5pt];
\end{tikzpicture}"""


def test_find_explicit_outer_circle_in_latex_and_leave_other_circles_unbound():
    assert circumcircles(STEM) == (Circumcircle("E", ("A", "B", "C", "D")),)
    assert circumcircles("如图，画圆与任意三角形 ABC。") == ()


def test_planar_renderer_constructs_circumcenter_and_rejects_different_render_radius():
    plan = {
        "mathbank_geometry": 1, "axes": True,
        "points": [
            ["A", "below left", "xy", -4, 0],
            ["B", "below right", "xy", 4, 0],
            ["C", "above right", "xy", 3, 7],
            ["D", "above left", "xy", -3, 7],
            ["E", "right", "circumcenter", "A", "B", "C"],
        ],
        "draw": [
            ["segment", "A", "B"], ["segment", "B", "C"],
            ["segment", "C", "D"], ["segment", "D", "A"],
            ["circle_through", "E", "A"],
        ],
        "checks": [["distance", "A", "B", 8, r"底边长分别为\(8\)和\(6\)"]],
    }
    code = render_geometry_reply(json.dumps(plan), context=STEM)
    assert r"\draw (E) ellipse [x radius=3.250000000cm,y radius=3.250000000cm];" in code
    plan["draw"][-1] = ["circle", "E", 6]
    with pytest.raises(TikzGeometryError, match="圆 E 未经过"):
        render_geometry_reply(json.dumps(plan), context=STEM)


@pytest.mark.skipif(os.getenv("MATHBANK_TEST_TIKZ_NATIVE") != "1", reason="requires native XeLaTeX")
def test_preview_rejects_measured_wrong_radius_and_accepts_same_unit(client):
    import main

    if not shutil.which("xelatex") and not Path("/Library/TeX/texbin/xelatex").exists():
        pytest.skip("XeLaTeX unavailable")
    headers = {"X-Local-Token": main.LOCAL_TOKEN}
    before = set(Path(main.UPLOAD_DIR).glob("tikz_*.png"))
    bad = client.post("/api/render_tikz", data={"tikz_code": RAW.replace("RADIUS", "5cm"), "context": STEM}, headers=headers)
    assert bad.status_code == 400, bad.json()
    assert "未经过题设点 A、B、C、D" in bad.json()["detail"]
    assert set(Path(main.UPLOAD_DIR).glob("tikz_*.png")) == before

    good = client.post("/api/render_tikz", data={"tikz_code": RAW.replace("RADIUS", "5"), "context": STEM}, headers=headers)
    assert good.status_code == 200, good.json()
    assert good.json()["circle_review"] == {
        "status": "verified", "circles": [{"center": "E", "vertices": ["A", "B", "C", "D"]}],
    }

    unbound = client.post("/api/render_tikz", data={"tikz_code": RAW.replace("RADIUS", "5"), "context": "如图。"}, headers=headers)
    assert unbound.status_code == 200
    assert unbound.json()["circle_review"]["status"] == "unverified"
