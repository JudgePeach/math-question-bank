"""Fill-in shells are formatting repairs, never broad formula substitutions."""

import ast
import copy
import os
import re
import shutil
import subprocess

import pytest

from mathbank.fillin import normalize_fillin_math_wrappers
from mathbank.paper_helper import build_latex_document, clean_content_for_latex
from mathbank.paths import PROJECT_ROOT, TEMPLATES_DIR


SHELLS = [("$", "$"), ("$$", "$$"), (r"\(", r"\)"), (r"\[", r"\]")]
MACROS = [r"\fillin", r"\fillin*", r"\fillin[]", r"\fillin[width=2cm][$x^2$]",
          r"\fillin[{$x[1]$}]", r"\fillin \fillin*[]"]


@pytest.mark.parametrize("opening,closing", SHELLS)
@pytest.mark.parametrize("macro", MACROS)
def test_only_complete_fillin_shells_are_unwrapped_without_changing_arguments(opening, closing, macro):
    source = "若 $a_n=1$，则 " + opening + " \n" + macro + " " + closing + "。邻式 $b^2$。"
    expected = "若 $a_n=1$，则  \n" + macro + " 。邻式 $b^2$。"
    assert normalize_fillin_math_wrappers(source) == expected
    assert normalize_fillin_math_wrappers(expected) == expected


@pytest.mark.parametrize("source", [
    r"$x=\fillin$", r"$$x+\fillin$$", r"$\frac{\fillin}{2}$",
    r"$\text{填写}\fillin$", r"$\fillin，$", r"$x$\fillin$y$",
    r"$\fillin", r"$\fillin$$", r"$$\fillin$", r"$$$\fillin$$$",
    r"\(\fillin\]", r"$\fillin[2cm$", r"$\fillin[{a]}$",
    r"\fillin[$x$+$y$]", r"\fillin[$2$]$z$", r"$\\fillin$", r"\$\fillin\$",
    r"$\fillinfoo$", r"$\fillinCustom$",
    r"$\fillin$cm", r"$\fillin$[2]", r"$\fillin$ [2]", r"$\fillin$*",
    r"$\fillin$厘米", r"$\fillin$é", r"$\fillin$的值",
    "$\\fillin$% comment\n[2]",
    r"$\fillin{x_1}$", r"$\fillin[1][2][x_1]$",
    r"`$\fillin$`", "````tex\n$\\fillin$\n````", "~~~tex\n$\\fillin$\n~~~",
    "\n    $\\fillin$\n", r"\verb|$\fillin$|", r"\Verb+$\fillin$+",
    r"\lstinline{$\fillin$}", r"\mintinline{tex}|$\fillin$|",
    r"\detokenize{保留{$\fillin$}}", r"\texttt{$\fillin$}",
    r"\begin{tikzpicture}\node{$\fillin$};\end{tikzpicture}",
    r"<!-- $\fillin$ -->", r'<code>$\fillin$</code>', r'<pre>$\fillin$</pre>',
    r'<img title="$\fillin$" src="/static/uploads/a.png">',
    r'![图](/static/uploads/$\fillin$.png)', r'[链接](https://example.test/$\fillin$)',
    r'https://example.test/$\fillin$', r'\includegraphics{图$\fillin$.png}',
    r'<mathbank-math id="MBM_0001">$\fillin$</mathbank-math>',
])
def test_mixed_math_malformed_source_and_literal_examples_stay_exact(source):
    assert normalize_fillin_math_wrappers(source) == source


def test_literals_do_not_supply_dollars_to_a_neighboring_real_blank():
    source = r'`$` \fillin `$`；`$\fillin$`；$\fillin$；$x$\fillin$y$'
    assert normalize_fillin_math_wrappers(source) == (
        r'`$` \fillin `$`；`$\fillin$`；\fillin；$x$\fillin$y$'
    )


def test_blank_inside_table_stays_in_its_original_cell():
    source = r'\begin{tabular}{cc}$\fillin$ & $x=\fillin$ \\\end{tabular}'
    assert normalize_fillin_math_wrappers(source) == (
        r'\begin{tabular}{cc}\fillin & $x=\fillin$ \\\end{tabular}'
    )


def test_legacy_normalizer_uses_shared_repair_after_converting_blank_glyphs():
    # Exercise the actual function without importing the live service/DB.
    module = ast.parse((PROJECT_ROOT / "main.py").read_text())
    function = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == "normalize_fillin_macro")
    namespace = {"re": re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
    normalize = namespace["normalize_fillin_macro"]
    for raw in (r'$____$', r'$\_\_\_\_$', r'$\underline{\quad}$', r'$\fillin$'):
        assert normalize(raw) == r'\fillin'
        assert normalize(normalize(raw)) == r'\fillin'
    assert normalize(r'$S_n=\_$') == r'$S_n=\fillin$'


def test_pdf_export_repairs_legacy_copy_and_keeps_mixed_formula_source():
    questions = [{"question": {"id": 1, "question_type": "fill_in_blank",
        "content": r'结果为 $\fillin$，且 $x=\fillin$；$a_n$。',
        "answer_markdown": r'答案 $\fillin[2]$。'}, "score": 5}]
    original = copy.deepcopy(questions)
    tex = build_latex_document("填空规范验证", "", "quiz", questions,
                               include_answers=True, show_secret=False, show_notice=False)
    assert r'结果为 \fillin，且 $x=\fillin$；$a_n$。' in tex
    assert r'答案 \fillin[2]。' in tex
    assert questions == original
    # General legacy underscore normalization is deliberately excluded here.
    assert "____" in clean_content_for_latex("原文 ____")


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_FILLIN_NATIVE") != "1"
                    or not shutil.which("xelatex"), reason="Opt-in native XeLaTeX fill-in validation")
@pytest.mark.parametrize("include_answers", [False, True])
def test_real_examzh_compiles_cleaned_and_mixed_fillins_with_visible_lines(tmp_path, include_answers):
    import pymupdf

    labels = ['独立横线', '混合公式', '嵌套分式', '答案参数', '星号横线', '三角占位']
    contents = [r'独立横线 $\fillin$。', r'混合公式 $x=\fillin$。',
                r'嵌套分式 $\frac{1}{\fillin}$。', r'答案参数 $\fillin[width=2cm][$x^2$]$。',
                r'星号横线 $\fillin*[]$。', r'三角占位 \fillin\underline{▲}\fillin。']
    questions = [{"question": {"id": index + 1, "question_type": "fill_in_blank", "content": content},
                  "score": 5} for index, content in enumerate(contents)]
    tex = build_latex_document("填空横线实编译", "", "quiz", questions,
                               include_answers=include_answers, show_secret=False, show_notice=False)
    (tmp_path / "paper.tex").write_text(tex)
    environment = os.environ.copy()
    environment["TEXINPUTS"] = str(TEMPLATES_DIR / "exam-zh") + os.pathsep + environment.get("TEXINPUTS", "")
    result = subprocess.run([shutil.which("xelatex"), "-no-shell-escape", "-halt-on-error",
                             "-interaction=nonstopmode", "paper.tex"], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout[-6000:]
    assert "Missing character" not in result.stdout
    with pymupdf.open(tmp_path / "paper.pdf") as document:
        # Require horizontal drawing segments, not just an exit code or text.
        page = document[0]
        horizontal = [item for drawing in page.get_drawings() for item in drawing["items"]
                      if item[0] == "l" and abs(item[1].y - item[2].y) < 0.1]
        rows = [page.search_for(label)[0] for label in labels]
        for index, row in enumerate(rows):
            bottom = rows[index + 1].y0 if index + 1 < len(rows) else row.y1 + 15
            # CJK underline may consist of many 2.1pt segments. Sum their
            # lengths within this question rather than requiring one stroke.
            length = sum(abs(item[1].x - item[2].x) for item in horizontal
                         if row.y0 <= item[1].y < bottom)
            assert length > 15, (labels[index], length)
