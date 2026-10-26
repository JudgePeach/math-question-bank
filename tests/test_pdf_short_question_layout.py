"""Short graphic questions stay together; long bodies and answer space can split."""

import copy
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from mathbank.paper_helper import build_latex_document, build_restricted_tex_environment


def _item(number, content, *, qtype="fill_in_blank", space=0):
    return {
        "question": {
            "id": number, "question_type": qtype, "content": content,
            "figure_align": "bottom_right", "figure_align_custom": True,
            "figure_size": "small", "image_paths": [],
        },
        "score": 5, "solution_space": space,
    }


def _tex(items, paper_type="quiz", **kwargs):
    return build_latex_document("图文分页验证", "", paper_type, items,
                                show_secret=False, show_notice=False, **kwargs)


def _body(tex, number):
    start = tex.index(f"% MathBank-Question-ID: {number}\n")
    end = tex.index("% MathBank-Question-End", start)
    return tex[start:end]


def test_graphic_body_is_typeset_once_and_answer_space_is_outside_its_box():
    item = _item(42, "SHORTSTEM\n\n![](/static/uploads/figure.png)",
                 qtype="detailed_answer", space=30)
    original = copy.deepcopy(item)
    tex = _tex([item])
    body = _body(tex, 42)
    assert item == original
    assert r"\tracinglostchars=3" in tex
    assert body.count(r"\mathbankbeginquestionbody") == 1
    assert body.count("SHORTSTEM") == 1
    assert body.index(r"\end{problem}") < body.index(r"\mathbankendquestionbody")
    assert body.index(r"\mathbankendquestionbody") < body.index(r"\mathbanksolutionspace")
    assert r"\mathbanksolutionspace{26.0cm}" in body
    assert r"\unvbox\mathbankquestionbox" in tex
    assert r"\ifdim\dimen0>\textheight" in tex
    # Streaming capture retains native source line numbers and verbatim scanning.
    assert r"\setbox\mathbankquestionbox=\vbox\bgroup" in tex
    assert r"\newcommand{\mathbankbeginquestionbody}[" not in tex


@pytest.mark.parametrize("page_aware", [
    r"\begin{longtable}{c} A \\ \end{longtable}",
    r"Text\footnote{footnote}", r"Text\newpage following text",
    r"\begin{figure} Text \end{figure}",
    r"\begin{tikzpicture}[remember picture,overlay]\node {A};\end{tikzpicture}",
])
def test_page_aware_content_keeps_its_existing_outer_page_flow(page_aware):
    tex = _tex([_item(2, page_aware + "\n\n![](/static/uploads/figure.png)")])
    assert r"\mathbankbeginquestionbody" not in _body(tex, 2)


def test_inline_graphics_and_choices_are_part_of_the_measured_body():
    content = ("INLINESTART\n\n![](/static/uploads/figure.png)\n\nINLINEEND\n"
               r"\begin{choices}\item A\item B\item C\item D\end{choices}")
    body = _body(_tex([_item(3, content, qtype="single_choice")]), 3)
    assert body.index(r"\mathbankbeginquestionbody") < body.index("INLINESTART")
    assert body.index("INLINEEND") < body.index(r"\mathbankendquestionbody")
    assert body.index(r"\end{choices}") < body.index(r"\mathbankendquestionbody")


def test_question_and_field_markers_follow_actual_template_numbering():
    items = [
        _item(101, "A", qtype="single_choice"),
        _item(102, "B", qtype="single_choice"),
        _item(109, "C", qtype="multi_choice"),
        _item(112, "D\n\n![](/static/uploads/figure.png)"),
        _item(115, "E", qtype="detailed_answer"),
    ]
    items[-1]["question"]["answer_markdown"] = "ANSWERFIELD"
    tex = _tex(items, "exam_19", include_answers=True)
    assert re.findall(r"% MathBank-Question-Number: (\d+)", tex) == ["1", "2", "9", "12", "15"]
    assert "% MathBank-Field: figure" in _body(tex, 112)
    answer_body = _body(tex, 115)
    assert ("% MathBank-Field: answer_markdown\n"
            r"\typeout{MathBank-Question-Start: 115 15 answer_markdown}" "\n"
            r"\begin{solution}") in answer_body
    assert answer_body.index(r"\end{solution}") < answer_body.index(
        r"\typeout{MathBank-Question-Finish: 115}")
    quiz = _tex(items)
    assert re.findall(r"% MathBank-Question-Number: (\d+)", quiz) == ["1", "2", "3", "4", "5"]


NATIVE = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_LAYOUT_NATIVE") != "1" or not shutil.which("xelatex"),
    reason="Opt-in native XeLaTeX graphic-question pagination validation",
)


def _native_compile(tex, tmp_path):
    from PIL import Image, ImageDraw
    import pymupdf as fitz

    tmp_path.mkdir(parents=True, exist_ok=True)
    picture = Image.new("RGB", (180, 180), "white")
    ImageDraw.Draw(picture).polygon([(15, 165), (90, 15), (165, 165)],
                                   outline="black", width=3)
    picture.save(tmp_path / "figure.png")
    picture.save(tmp_path / "figure-two.png")
    (tmp_path / "paper.tex").write_text(tex, encoding="utf-8")
    command = [shutil.which("xelatex"), "-no-shell-escape", "-halt-on-error",
               "-interaction=nonstopmode", "-file-line-error", "paper.tex"]
    for _ in range(2):
        process = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True,
                                 timeout=45, env=build_restricted_tex_environment(str(tmp_path)))
        log = (tmp_path / "paper.log").read_text(encoding="utf-8", errors="replace")
        assert process.returncode == 0, log[-7000:]
        assert "Missing character" not in log
        assert "Overfull \\vbox" not in log
    document = fitz.open(tmp_path / "paper.pdf")
    for index, page in enumerate(document):
        page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0)).save(tmp_path / f"page-{index + 1}.png")
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                assert line["bbox"][3] < page.rect.height - 15
    return document, log


@NATIVE
@pytest.mark.parametrize("qtype", ["fill_in_blank", "single_choice"])
def test_native_short_stem_figure_and_options_move_to_the_same_next_page(tmp_path, qtype):
    content = r"SHORTSTEM 已知 $\angle ABC=60^\circ$，求 $AB$。"
    if qtype == "single_choice":
        content += (r"\begin{choices}\item CHOICEONE\item CHOICETWO"
                    r"\item CHOICETHREE\item CHOICEFOUR\end{choices}")
    content = r"\typeout{MATHBANKLAYOUTONCE}" + content + "\n\n![](/static/uploads/figure.png)"
    items = [_item(1, "PRECEDING", qtype=qtype), _item(2, content, qtype=qtype)]
    tex = _tex(items)
    tex = tex.replace("% MathBank-Question-ID: 2\n",
                      "\\par\\vspace*{17cm}\\par\n% MathBank-Question-ID: 2\n", 1)
    document, log = _native_compile(tex, tmp_path)
    pages = [page.get_text() for page in document]
    assert len(document) == 2
    assert "SHORTSTEM" not in pages[0] and "SHORTSTEM" in pages[1]
    assert not document[0].get_images() and document[1].get_images()
    assert "PRECEDING" in pages[0]
    if qtype == "single_choice":
        assert "CHOICEONE" in pages[1] and "CHOICEFOUR" in pages[1]
    assert log.count("MATHBANKLAYOUTONCE") == 1
    if qtype == "fill_in_blank":
        # The identical old flow demonstrates the original stem/figure split.
        old_flow = tex.replace("\\mathbankbeginquestionbody\n", "").replace(
            "\\mathbankendquestionbody\n", "")
        baseline, _ = _native_compile(old_flow, tmp_path / "baseline-unbound")
        assert "SHORTSTEM" in baseline[0].get_text()
        assert not baseline[0].get_images() and baseline[1].get_images()


@NATIVE
def test_native_long_graphic_body_is_unboxed_and_keeps_all_content(tmp_path):
    rows = [f"LONGROW{number:03d} 这是一段用于验证长题自然跨页的正文，保留全部文字与题末插图。"
            for number in range(72)]
    content = "\n\n".join(rows) + "\n\n![](/static/uploads/figure.png)"
    document, _ = _native_compile(_tex([_item(1, content)]), tmp_path)
    assert len(document) >= 2
    text = "\n".join(page.get_text() for page in document)
    for number in range(72):
        assert text.count(f"LONGROW{number:03d}") == 1
    assert document[-1].get_images()


@NATIVE
def test_native_graphic_body_does_not_capture_thirty_cm_answer_space(tmp_path):
    first = _item(1, "ANSWERSTEM\n\n![](/static/uploads/figure.png)",
                  qtype="detailed_answer", space=30)
    second = _item(2, "AFTERANSWERSPACE", qtype="detailed_answer")
    document, _ = _native_compile(_tex([first, second]), tmp_path)
    assert len(document) == 2
    assert "ANSWERSTEM" in document[0].get_text() and document[0].get_images()
    assert "AFTERANSWERSPACE" not in document[0].get_text()
    assert "AFTERANSWERSPACE" in document[1].get_text()


@NATIVE
@pytest.mark.parametrize("figure_size", ["medium", "large"])
def test_native_long_body_with_multiple_large_figures_keeps_live_page_flow(tmp_path, figure_size):
    rows = [f"MULTIROW{number:03d} 长题正文保留自然分页，随后依次显示两张中等尺寸插图。"
            for number in range(70)]
    content = "\n\n".join(rows) + (
        "\n\n![](/static/uploads/figure.png)\n\n![](/static/uploads/figure-two.png)"
    )
    target = _item(2, content)
    target["question"]["figure_size"] = figure_size
    tex = _tex([_item(1, "BEFOREMULTI"), target])
    tex = tex.replace("% MathBank-Question-ID: 2\n",
                      "\\par\\vspace*{17cm}\\par\n% MathBank-Question-ID: 2\n", 1)
    document, _ = _native_compile(tex, tmp_path)
    text = "\n".join(page.get_text() for page in document)
    assert "MULTIROW000" in document[0].get_text(), "A long body must use the remaining first page"
    for number in range(70):
        assert text.count(f"MULTIROW{number:03d}") == 1
    assert sum(len(page.get_image_info()) for page in document) == 2
    # Page-aware space checks inside a measuring box cannot use the stale
    # outer page goal to insert an extra forced break before the last figures.
    assert r"\ifinner\else\ifdim\pagegoal" in tex
    last_text_page = next(page for page in document if "MULTIROW069" in page.get_text())
    assert last_text_page.get_images(), "The first figure must use the real remaining space after the long text"
    assert len(document) <= 4
