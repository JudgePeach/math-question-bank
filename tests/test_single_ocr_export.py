"""Single-OCR drawings retain their option anchors and individual export layout."""

import os
import shutil

import pytest

from mathbank.paper_helper import build_latex_document, compile_tex_to_pdf


COLORS = {
    "A": ("red", (1.0, 0.0, 0.0)),
    "B": ("green", (0.0, 1.0, 0.0)),
    "C": ("blue", (0.0, 0.0, 1.0)),
    "D": ("red!50!blue", (0.5, 0.0, 0.5)),
}
ASSETS = [
    {
        "id": label,
        "image_path": f"/static/uploads/tikz_option_{label}.png",
        "reference_image_path": f"/static/uploads/reference_{label}.png",
        "tikz_code": (
            "\\begin{tikzpicture}\n"
            f"% option {label}: preserve the source newline\n"
            rf"\draw[{color},line width=0.7pt] (0,0) rectangle (12,1);" "\n"
            "\\end{tikzpicture}"
        ),
    }
    for label, (color, _) in COLORS.items()
]
LAYOUTS = {
    f"tikz_option_{label}.png": {"align": align, "size": size}
    for label, align, size in [
        ("A", "left", "small"),
        ("B", "center", "medium"),
        ("C", "right", "large"),
        ("D", "center", "auto"),
    ]
}


def make_tex(paper_type="quiz", *, custom=False, mixed=False):
    content = "选择正确的图象。\\begin{choices}\n" + "\n".join(
        "\\item " + ("图象说明 " if mixed else "")
        + f"![]({asset['image_path']})" for asset in ASSETS
    ) + "\n\\end{choices}"
    question = {
        "id": 9901, "question_type": "single_choice", "content": content,
        "content_tikz_assets": ASSETS,
        "image_layouts": LAYOUTS if custom else {},
    }
    return build_latex_document(
        "单题识图导出验证", "", paper_type, [{"question": question, "score": 5}],
        show_secret=False, show_notice=False,
    )


@pytest.mark.parametrize("paper_type", ["exam", "quiz", "exam_19"])
@pytest.mark.parametrize("custom", [False, True])
def test_four_tikz_options_keep_source_order_and_individual_layout(paper_type, custom):
    tex = make_tex(paper_type, custom=custom)
    positions = [tex.index(asset["tikz_code"]) for asset in ASSETS]
    assert positions == sorted(positions)
    assert tex.index(r"\begin{choices}") < positions[0] < positions[-1] < tex.index(r"\end{choices}")
    assert all(tex.count(asset["tikz_code"]) == 1 for asset in ASSETS)
    assert "tikz_option_" not in tex and "reference_" not in tex
    assert "MATHBANKTIKZ" not in tex
    if custom:
        assert r"\begin{choices}[columns=4" not in tex
        for width, height in [(5.0, 4.2), (8.0, 6.0), (10.5, 7.5), (9.0, 6.0)]:
            assert f"max width={width}cm,max height={height}cm" in tex
        for alignment in "lcr":
            assert rf"\makebox[\linewidth][{alignment}]" in tex
        choice_body = tex[tex.index(r"\begin{choices}"):tex.index(r"\end{choices}")]
        assert not any(r"\begin{" + env + "}" in choice_body for env in ["center", "flushleft", "flushright"])
    else:
        assert r"\begin{choices}[columns=4,label-pos=left]" in tex
        assert tex.count("max width=3.0cm,max height=3.0cm") == 4


def test_mixed_image_options_use_inline_bounded_tikz_boxes():
    tex = make_tex(mixed=True)
    choice_body = tex[tex.index(r"\begin{choices}"):tex.index(r"\end{choices}")]
    assert choice_body.count(r"\adjustbox{max width=\linewidth}") == 4
    assert r"\begin{center}" not in choice_body
    assert all(asset["tikz_code"] in choice_body for asset in ASSETS)


@pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_TIKZ_NATIVE") != "1" or not shutil.which("xelatex"),
    reason="opt-in native XeLaTeX regression",
)
@pytest.mark.parametrize("paper_type", ["exam", "quiz", "exam_19"])
@pytest.mark.parametrize("custom", [False, True])
def test_native_four_tikz_options_are_vector_bounded_and_aligned(paper_type, custom):
    import pymupdf as fitz

    # No preview or reference PNG is supplied: compilation must use the source.
    pdf, diagnostic = compile_tex_to_pdf(make_tex(paper_type, custom=custom))
    assert pdf, diagnostic
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert len(document) == 1
        page = document[0]
        assert page.get_images(full=True) == []
        drawings = page.get_drawings()
        boxes = []
        for _, expected in COLORS.values():
            candidates = [drawing for drawing in drawings if drawing.get("color")
                          and all(abs(a - b) < 0.02 for a, b in zip(drawing["color"], expected))]
            assert len(candidates) == 1, candidates
            boxes.append(candidates[0]["rect"])
        assert all(0 <= box.x0 < box.x1 <= page.rect.width for box in boxes)
        if custom:
            # The full option line supplies alignment; the drawings keep their
            # separate requested caps and are never stretched to fill that line.
            for box, width in zip(boxes, [5.0, 8.0, 10.5, 9.0]):
                # The visible path is inset from TikZ's full bounding box by
                # its stroke/outer padding, so verify a cap rather than exact size.
                cap = width * 72 / 2.54
                assert 0.95 * cap <= box.width <= cap + 1
            assert [box.y0 for box in boxes] == sorted(box.y0 for box in boxes)
            assert boxes[0].x0 < boxes[1].x0 and boxes[0].x0 < boxes[2].x0
            line_center = (boxes[0].x0 + boxes[2].x1) / 2
            assert abs((boxes[1].x0 + boxes[1].x1) / 2 - line_center) < 2
            assert abs((boxes[3].x0 + boxes[3].x1) / 2 - line_center) < 2
        else:
            assert max(box.y0 for box in boxes) - min(box.y0 for box in boxes) < 1
            assert [box.x0 for box in boxes] == sorted(box.x0 for box in boxes)
            assert all(box.width <= 3 * 72 / 2.54 + 1 for box in boxes)


@pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_TIKZ_NATIVE") != "1" or not shutil.which("xelatex"),
    reason="opt-in native XeLaTeX regression",
)
def test_native_mixed_image_options_compile_from_source_without_block_environments():
    import pymupdf as fitz

    pdf, diagnostic = compile_tex_to_pdf(make_tex(mixed=True))
    assert pdf, diagnostic
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert sum(len(page.get_images(full=True)) for page in document) == 0
        for _, expected in COLORS.values():
            candidates = [drawing for page in document for drawing in page.get_drawings()
                          if drawing.get("color") and all(
                              abs(a - b) < 0.02 for a, b in zip(drawing["color"], expected))]
            assert len(candidates) == 1
            assert candidates[0]["rect"].width <= 9 * 72 / 2.54 + 1
