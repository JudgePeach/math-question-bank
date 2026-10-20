"""Choice blanks follow prose, while export figures and source stay intact."""

from copy import deepcopy

import pytest

from mathbank.paper_helper import build_latex_document, clean_content_for_latex
from mathbank.source_comments import SOURCE_ATTRIBUTION_PREFIX, SOURCE_ATTRIBUTION_SUFFIX


IMAGE = "![](/static/uploads/diagram.png)"
CHOICES = r"\begin{choices}\item $1$\item $2$\item $3$\item $4$\end{choices}"
DRAWING = "\\begin{tikzpicture}\n% Keep $ and \\paren literally here.\n" + r"\draw (0,0)--(1,1);" + "\n\\end{tikzpicture}"


def clean(source, **kwargs):
    return clean_content_for_latex(source, q_type="single_choice", preserve_image_positions=True, **kwargs)


@pytest.mark.parametrize("placeholder", [
    "（   ）", r"(\quad)", r"$(\quad)$", r"$（\qquad）$",
    r"（$\quad$）", r"\(\quad\)", r"\((\quad)\)",
    r"（\(\qquad\)）", r"(\hspace{2em})", r"\paren",
    r"( ) \paren （ ）",
])
def test_blank_is_unique_on_last_prose_line_before_image_and_choices(placeholder):
    source = "$x^2+1$ 最后文字段" + placeholder + "\n\n" + IMAGE + "\n\n" + CHOICES
    result = clean(source)
    assert result.count(r"\paren") == 1
    assert "$x^2+1$" in result
    assert result.index("最后文字段") < result.index(r"\paren") < result.index(r"\includegraphics") < result.index(r"\begin{choices}")
    assert result.count(r"\item") == 4
    assert result.count("{diagram.png}") == 1
    assert source.endswith(CHOICES)


@pytest.mark.parametrize("meaningful", [
    "坐标(0, 1)", "说明（含端点）", r"$x+( )$", r"\(x+( )\)",
    r"$(x+1)$", r"\((x+1)\)", r"正文价格 \$5 与 $x^2$",
])
def test_meaningful_parentheses_math_shells_and_currency_are_preserved(meaningful):
    result = clean(meaningful + "\n" + IMAGE + "\n" + CHOICES)
    assert meaningful in result
    assert result.count(r"\paren") == 1
    assert result.index(r"\paren") < result.index(r"\includegraphics")


def test_last_prose_after_an_earlier_image_is_not_moved_before_that_image():
    result = clean("前文\n" + IMAGE + "\n最后说明（ ）\n" + IMAGE.replace("diagram", "last") + "\n" + CHOICES)
    assert result.index("{diagram.png}") < result.index("最后说明") < result.index(r"\paren") < result.index("{last.png}")
    assert result.count(r"\paren") == 1


@pytest.mark.parametrize("wrapper", ["center", "figure", "flushleft", "flushright"])
def test_pure_authored_figure_wrapper_stays_after_the_blank(wrapper):
    source = "题干（ ）\n\\begin{" + wrapper + "}\n" + IMAGE + "\n\\end{" + wrapper + "}\n" + CHOICES
    result = clean(source)
    assert result.count(r"\paren") == 1
    assert result.index(r"\paren") < result.index("\\begin{" + wrapper + "}") < result.index("{diagram.png}")
    assert "\\end{" + wrapper + "}" in result


def test_native_restoration_and_direct_tikz_keep_source_and_anchor():
    restored = clean("题干（ ）\n" + IMAGE + "\n" + CHOICES, tikz_sources={"diagram.png": DRAWING})
    direct = clean("题干（ ）\n" + DRAWING + "\n" + CHOICES)
    for result in (restored, direct):
        assert DRAWING in result
        assert result.index(r"\paren") < result.index(DRAWING) < result.index(r"\begin{choices}")
        assert result.replace(DRAWING, "").count(r"\paren") == 1
    assert "{diagram.png}" not in restored


@pytest.mark.parametrize("literal", [
    r"`( ) \paren \begin{choices}`",
    "```latex\n( ) \\paren\nA. 伪选项\n```",
    r"\verb|( ) \paren|", r"\detokenize{( ) \paren}",
    "\\begin{verbatim}\n( ) \\paren\n\\end{verbatim}",
    "<!-- 普通注释 ( ) \\paren -->",
    "    \\paren ( )\n",
])
def test_code_and_ordinary_comments_remain_literal(literal):
    source = "题干\n\n" + literal + "\n\n最后正文（ ）\n" + IMAGE + "\n" + CHOICES
    result = clean(source)
    assert literal.rstrip("\n") in result
    assert result.replace(literal.rstrip("\n"), "").count(r"\paren") == 1
    assert result.count(r"\item") == 4


def test_table_cell_parentheses_and_table_image_size_are_not_answer_blanks():
    table = r"\begin{tabular}{cc}空位 & ( )\\图 & " + IMAGE + r"\end{tabular}"
    result = clean("题干\n" + table + "\n最后文字（ ）\n" + IMAGE.replace("diagram", "tail") + "\n" + CHOICES)
    assert "空位 & ( )" in result
    assert result.index("{diagram.png}") < result.index("最后文字") < result.index(r"\paren") < result.index("{tail.png}")
    assert result.count(r"\paren") == 1
    assert "max width=\\linewidth,max height=4.0cm" in result


@pytest.mark.parametrize("align", ["center", "right", "bottom_left", "bottom_right"])
@pytest.mark.parametrize("native", [False, True])
def test_detached_end_figure_keeps_layout_size_and_one_stem_blank(align, native):
    question = {
        "id": 9, "question_type": "single_choice",
        "content": "$x=1$ 最后文字（ ）\n" + CHOICES + "\n\n" + IMAGE,
        "figure_align": align, "figure_align_custom": True, "figure_size": "medium",
        "content_tikz_assets": [{"id": "drawing", "image_path": "/static/uploads/diagram.png", "tikz_code": DRAWING, "instruction": ""}] if native else [],
    }
    original = deepcopy(question)
    tex = build_latex_document("末图测试", "", "exam", [{"question": question, "score": 5}], show_secret=False, show_notice=False)
    assert question == original
    assert tex.replace(DRAWING, "").count(r"\paren") == 1
    drawing = tex.index(DRAWING) if native else tex.index("{diagram.png}")
    assert tex.index(r"\paren") < drawing < tex.index(r"\begin{choices}")
    assert "max width=5.0cm" in tex if align == "right" else "max width=8.0cm" in tex
    assert "max height=6.0cm" in tex
    if align == "right":
        assert r"\begin{minipage}[t]{\dimexpr\linewidth-5.8cm\relax}" in tex
    else:
        assert "\\begin{" + {"center": "center", "bottom_left": "flushleft", "bottom_right": "flushright"}[align] + "}" in tex


def test_hidden_source_and_original_content_are_unchanged_by_export():
    block = SOURCE_ATTRIBUTION_PREFIX + "来源 https://example.invalid/a_b；许可 50%" + SOURCE_ATTRIBUTION_SUFFIX
    source = block + "$x^2+1$ 题干（ ）\n" + IMAGE + "\n" + CHOICES
    assert clean(source) == clean(source[len(block):])
    assert source.startswith(block)


def test_detached_figure_does_not_split_at_choices_literal_in_source_code():
    literal = r"\verb|\begin{choices}\paren|"
    question = {"id": 10, "question_type": "single_choice",
                "content": "代码示例 " + literal + "\n最后正文（ ）\n" + CHOICES + "\n\n" + IMAGE,
                "figure_align": "right"}
    tex = build_latex_document("代码边界", "", "exam", [{"question": question, "score": 5}], show_secret=False, show_notice=False)
    assert literal in tex
    code_start = tex.index(literal)
    figure_start = tex.index("{diagram.png}")
    real_choices = tex.index(CHOICES)
    assert code_start < tex.index("最后正文") < figure_start < real_choices
    assert tex.replace(literal, "").count(r"\paren") == 1


def test_image_only_source_keeps_hidden_header_hidden_after_detachment():
    block = SOURCE_ATTRIBUTION_PREFIX + "来源 https://example.invalid/a_b" + SOURCE_ATTRIBUTION_SUFFIX
    q = {"id": 11, "question_type": "single_choice", "content": block + IMAGE, "figure_align": "center"}
    tex = build_latex_document("图题来源", "", "exam", [{"question": q, "score": 5}], show_secret=False, show_notice=False)
    assert "MathBank source attribution" not in tex
    assert "example.invalid" not in tex
    assert tex.count(r"\paren") == 1
    assert tex.index(r"\paren") < tex.index("{diagram.png}")


def test_source_example_after_image_still_prevents_end_group_detachment():
    from mathbank.paper_helper import _split_export_image_anchors
    source = "题干\n" + IMAGE + "\n" + r"\verb|\begin{choices}|"
    assert _split_export_image_anchors(source) == (source, "")


def test_written_question_keeps_authored_empty_parentheses_without_auto_blank():
    result = clean_content_for_latex("正文（ ）\n" + IMAGE, q_type="detailed_answer", preserve_image_positions=True)
    assert "正文（ ）" in result
    assert r"\paren" not in result
