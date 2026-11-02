"""PDF image fidelity across Markdown syntax and editable/native drawings."""

from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import shutil

import pytest
from PIL import Image

from mathbank.export_preflight import (
    ExportPreflightError,
    preflight_paper_export,
    tex_image_references,
)
from mathbank.paper_helper import build_latex_document, clean_content_for_latex, compile_tex_to_pdf


IMAGE_FORMS = [
    '![示意图](/static/uploads/figure.png "辅助线示意图")',
    '![示意图](</static/uploads/figure.png>)',
    '![示意图](</static/uploads/figure.png> "含 空格 (括号) 的说明")',
    "![示意图](/static/uploads/figure.png '辅助线说明')",
]
VECTOR_A = (
    r"\begin{tikzpicture}\draw[draw={rgb,1:red,.1;green,.7;blue,.2},line width=1pt]"
    r"(0,0) rectangle (3,1);\node at (1.5,.5){VECTOR-A};\end{tikzpicture}"
)
VECTOR_B = VECTOR_A.replace("VECTOR-A", "VECTOR-B")


def _item(content, answer="", *, number=1, **extra):
    return {"question": {
        "id": number, "question_type": "detailed_answer", "content": content,
        "answer_markdown": answer, "image_paths": [], "figure_align": "center",
        "figure_align_custom": True, **extra,
    }, "score": 12}


def _build(items, paper_type="exam", *, answers=False):
    frozen = deepcopy(items)
    tex = build_latex_document("图片完整性验证", "", paper_type, items,
                               include_answers=answers, show_secret=False, show_notice=False)
    assert items == frozen, "Export must not rewrite question text, paths or drawing assets"
    return tex


def _preflight(items, tex, tmp_path):
    return preflight_paper_export(items, {"paper": tex}, target="pdf", uploads_dir=tmp_path,
                                  directory=tmp_path / "reports")


@pytest.mark.parametrize("paper_type", ["exam", "quiz", "exam_19"])
@pytest.mark.parametrize("image", IMAGE_FORMS)
@pytest.mark.parametrize("placement", ["tail", "inline", "choice", "tabular", "markdown_table", "answer"])
def test_image_titles_and_angle_paths_keep_the_real_file_and_anchor(
    tmp_path, paper_type, image, placement,
):
    Image.new("RGB", (100, 60), "red").save(tmp_path / "figure.png")
    cases = {
        "tail": ("题干前文。\n\n" + image, ""),
        "inline": ("题干前文。\n\n" + image + "\n\n题干后文。", ""),
        "choice": (r"选择正确的图形。\begin{choices}\item " + image + r"\item 无图\end{choices}", ""),
        "tabular": (r"\begin{tabular}{cc}示意图 & " + image + r"\end{tabular}", ""),
        "markdown_table": ("| 项目 | 图示 |\n| --- | --- |\n| 图 | " + image + " |", ""),
        "answer": ("题干不含图片。", "答案前文。\n\n" + image + "\n\n答案后文。"),
    }
    content, answer = cases[placement]
    items = [_item(content, answer)]
    if placement == "choice":
        items[0]["question"]["question_type"] = "single_choice"
    tex = _build(items, paper_type, answers=bool(answer))
    assert list(tex_image_references(tex)) == [(1, "figure.png")]
    assert "辅助线示意图" not in tex and "含 空格" not in tex
    if placement == "inline":
        assert tex.index("题干前文") < tex.index("{figure.png}") < tex.index("题干后文")
    elif placement == "answer":
        assert "{figure.png}" not in tex.split(r"\begin{solution}")[0]
        assert tex.index("答案前文") < tex.index("{figure.png}") < tex.index("答案后文")
        student = _build(items, paper_type)
        assert not list(tex_image_references(student))
    elif placement in {"choice", "tabular", "markdown_table"}:
        table_environment = "choices" if placement == "choice" else ("tabular" if placement == "tabular" else "tabularx")
        assert tex.index(r"\begin{" + table_environment + "}") < tex.index("{figure.png}")
        assert tex.index("{figure.png}") < tex.index(r"\end{" + table_environment + "}")
    report, paths = _preflight(items, tex, tmp_path)
    assert report["status"] == "passed"
    assert paths == [str(tmp_path / "figure.png")]


@pytest.mark.parametrize("reference", ["figure with space.png", "figure(1).png", "../figure.png"])
def test_angle_syntax_does_not_relax_controlled_export_filename_security(tmp_path, reference):
    items = [_item("图片：![图](</static/uploads/" + reference + ">)")]
    tex = _build(items)
    with pytest.raises(ExportPreflightError):
        _preflight(items, tex, tmp_path)


def test_four_image_options_keep_all_images_in_one_compact_choice_group(tmp_path):
    names = ["option_a.png", "option_b.png", "option_c.png", "option_d.png"]
    options = []
    for name in names:
        Image.new("RGB", (100, 60), "blue").save(tmp_path / name)
        options.append(r'\item ![图](</static/uploads/' + name + '> "选项说明")')
    items = [_item(r"选择正确图形。\begin{choices}" + "".join(options) + r"\end{choices}")]
    items[0]["question"]["question_type"] = "single_choice"
    tex = _build(items)
    assert [name for _, name in tex_image_references(tex)] == names
    assert r"\begin{choices}[columns=4,label-pos=left]" in tex
    assert tex.count("max width=3.0cm,max height=3.0cm") == 4
    assert _preflight(items, tex, tmp_path)[0]["status"] == "passed"


@pytest.mark.parametrize("paper_type", ["exam", "quiz", "exam_19"])
def test_native_tikz_and_independent_answer_pngs_survive_in_authored_order(tmp_path, paper_type):
    for name in ("first.png", "last.png"):
        Image.new("RGB", (100, 60), "red").save(tmp_path / name)
    answer = ("答案开头。\n\n![一](/static/uploads/first.png)\n\n"
              + VECTOR_A + '\n\n![二](</static/uploads/last.png> "独立图")\n\n答案结尾。')
    items = [_item("根据条件证明。", answer)]
    tex = _build(items, paper_type, answers=True)
    assert tex.index("答案开头") < tex.index("{first.png}") < tex.index(VECTOR_A)
    assert tex.index(VECTOR_A) < tex.index("{last.png}") < tex.index("答案结尾")
    assert tex.count(VECTOR_A) == 1
    assert [name for _, name in tex_image_references(tex)] == ["first.png", "last.png"]
    assert _preflight(items, tex, tmp_path)[0]["status"] == "passed"
    assert not list(tex_image_references(_build(items, paper_type)))


def test_structured_answer_tikz_replaces_only_its_exact_preview_reference(tmp_path):
    Image.new("RGB", (100, 60), "blue").save(tmp_path / "other.png")
    answer = ('答案开头。\n\n![可编辑图](</static/uploads/preview.png> "预览")\n\n'
              '中间说明。\n\n![独立图](/static/uploads/other.png "截图")\n\n答案结尾。')
    items = [_item("根据条件证明。", answer, answer_tikz_assets=[{
        "id": "a", "image_path": "/static/uploads/preview.png", "tikz_code": VECTOR_B,
    }])]
    tex = _build(items, answers=True)
    assert tex.count(VECTOR_B) == 1
    assert list(tex_image_references(tex)) == [(1, "other.png")]
    assert tex.index("答案开头") < tex.index(VECTOR_B) < tex.index("中间说明")
    assert tex.index("中间说明") < tex.index("{other.png}") < tex.index("答案结尾")
    assert _preflight(items, tex, tmp_path)[0]["status"] == "passed"


def test_image_examples_in_code_and_tex_literal_content_remain_literal():
    literal = r'\begin{verbatim}![示例](</static/uploads/example.png> "标题")\end{verbatim}'
    rendered = clean_content_for_latex("代码示例。\n\n" + literal + "\n\n后文。")
    assert literal in rendered
    assert list(tex_image_references(rendered)) == []


@pytest.mark.parametrize("environment", [r"\begin{tabular}{c}", r"\begin{choices}"])
@pytest.mark.parametrize("metadata_field", ["title", "alt"])
def test_environment_literals_in_image_metadata_do_not_reclassify_the_next_image(
    environment, metadata_field,
):
    if metadata_field == "title":
        first = '![第一图](/static/uploads/first.png "字面环境 ' + environment + '")'
    else:
        first = '![字面环境 ' + environment + '](/static/uploads/first.png)'
    second = '![](/static/uploads/last.png)'
    rendered = clean_content_for_latex(
        '第一段。\n\n' + first + '\n\n中间正文。\n\n' + second + '\n\n最后正文。',
        preserve_image_positions=True,
    )
    assert [name for _, name in tex_image_references(rendered)] == ["first.png", "last.png"]
    assert "字面环境" not in rendered and environment not in rendered
    assert r"\includegraphics[max width=9.0cm,max height=6.0cm,keepaspectratio]{last.png}" in rendered
    assert rendered.index("中间正文") < rendered.index("{last.png}") < rendered.index("最后正文")


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_EXPORT_NATIVE") != "1" or not shutil.which("xelatex"),
                    reason="Opt-in native PDF image and TikZ fidelity validation")
@pytest.mark.parametrize("paper_type", ["exam", "quiz", "exam_19"])
def test_real_pdf_contains_each_independent_image_and_both_vector_drawings(tmp_path, paper_type):
    import pymupdf

    colors = {"stem.png": (38, 93, 204), "answer.png": (212, 45, 56), "other.png": (173, 87, 18)}
    for name, color in colors.items():
        Image.new("RGB", (600, 180), color).save(tmp_path / name)
    items = [
        _item('题干甲。\n\n![题图](</static/uploads/stem.png> "题干图")',
              'ANSWER-START-A\n\n' + VECTOR_A + '\n\n![答案图](/static/uploads/answer.png "答案图")\n\nANSWER-END-A'),
        _item('题干乙。', 'ANSWER-START-B\n\n![预览](</static/uploads/preview.png> "可编辑图")\n\n'
              '![独立图](/static/uploads/other.png "独立图")\n\nANSWER-END-B', number=2,
              answer_tikz_assets=[{"id": "b", "image_path": "/static/uploads/preview.png", "tikz_code": VECTOR_B}]),
    ]
    for include_answers in (False, True):
        tex = _build(items, paper_type, answers=include_answers)
        _, paths = _preflight(items, tex, tmp_path)
        pdf, diagnostics = compile_tex_to_pdf(tex, paths)
        assert pdf, diagnostics
        raster_entries, vector_entries, words = [], [], []
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            for page_number, page in enumerate(document):
                words.extend(page.get_text().split())
                for info in page.get_image_info(xrefs=True):
                    assert info["xref"] > 0
                    blob = document.extract_image(info["xref"])["image"]
                    with Image.open(BytesIO(blob)) as image:
                        color = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
                    box = pymupdf.Rect(info["bbox"])
                    assert page.rect.contains(box), "An image was clipped outside the PDF page"
                    assert box.width > 0 and box.height > 0
                    raster_entries.append((page_number, box.y0, color))
                for drawing in page.get_drawings():
                    color = drawing.get("color")
                    if color and color[1] > .6 and color[0] < .2 and color[2] < .3:
                        box = drawing["rect"]
                        assert page.rect.contains(box)
                        vector_entries.append((page_number, box.y0))
            text = " ".join(words)
        assert [entry[2] for entry in raster_entries] == (
            [colors["stem.png"], colors["answer.png"], colors["other.png"]]
            if include_answers else [colors["stem.png"]]
        )
        assert len(vector_entries) == (2 if include_answers else 0)
        assert ("VECTOR-A" in text and "VECTOR-B" in text) == include_answers
        if include_answers:
            assert vector_entries[0] < raster_entries[1][:2] < vector_entries[1] < raster_entries[2][:2]
