"""Unicode reasoning signs use PDF math glyphs without rewriting source data."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from mathbank.geometry_symbols import normalize_school_math_symbols
from mathbank.paper_helper import build_latex_document, clean_content_for_latex


@pytest.mark.parametrize("source,expected", [
    ("∵$a=1$，∴$b=2$", r"\ensuremath{\because}$a=1$，\ensuremath{\therefore}$b=2$"),
    ("$∵a=1∴b=2$", r"$\because a=1\therefore b=2$"),
    (r"\[∵a=1，∴b=2\]", r"\[\because a=1，\therefore b=2\]"),
    (r"\begin{align}∵x=1\\∴y=2\end{align}", r"\begin{align}\because x=1\\\therefore y=2\end{align}"),
    (r"$\text{∵条件成立，∴}\;x=1$", r"$\text{\ensuremath{\because}条件成立，\ensuremath{\therefore}}\;x=1$"),
    (r"\textbf{∵条件成立}\textnormal{∴成立}", r"\textbf{\ensuremath{\because}条件成立}\textnormal{\ensuremath{\therefore}成立}"),
    (r"\ensuremath{∵x=1}", r"\ensuremath{\because x=1}"),
    (r"$∴_A$", r"$\therefore _A$"),
    (r"$∵^{2}$", r"$\because ^{2}$"),
    (r"$\text{条件\textbf{∵成立}，∴}$", r"$\text{条件\textbf{\ensuremath{\because}成立}，\ensuremath{\therefore}}$"),
])
def test_reasoning_signs_follow_existing_math_and_text_modes(source, expected):
    result = normalize_school_math_symbols(source, target="latex")
    assert result == expected
    assert normalize_school_math_symbols(result, target="latex") == result
    assert result.count("$") == source.count("$")


@pytest.mark.parametrize("source", [
    "`∵ ∴`", "```latex\n∵ ∴\n```\n", "% ∵ ∴\n", "<code>∵ ∴</code>",
    r"![∵ ∴](/static/uploads/∵∴.png)", r"\includegraphics[width=2cm]{∵∴.png}",
    r"\url{https://example.test/∵∴}", r"\path{images/∵∴.png}", "/static/uploads/∵∴.png",
    "/static/test_uploads/∵∴.png", r"\verb|∵∴|",
    r"\begin{tikzpicture}\node {$∵$ ∴};\end{tikzpicture}", r"\tikz{\node {∵∴};}",
    r'<mathbank-math id="MBM_REASON_0001">$∵$ ∴</mathbank-math>',
    r"\begin{verbatim}∵ ∴\end{verbatim}", r"\∵ \∴",
])
def test_reasoning_adapter_preserves_code_tikz_paths_comments_and_formula_locks(source):
    assert normalize_school_math_symbols(source, target="latex") == source


def test_mixed_protected_and_visible_reasoning_signs_do_not_rewrite_word_branch():
    source = r"∵$a=1$，`∴`，\path{∵.png}，$\text{∴}b=2$"
    latex = normalize_school_math_symbols(source, target="latex")
    assert r"`∴`" in latex and r"\path{∵.png}" in latex
    assert r"\ensuremath{\because}$a=1$" in latex
    assert normalize_school_math_symbols(source, target="word") == source


SAMPLE_ANSWER = (
    "C\n\n【解析】∵集合 $A=\\{1,2,4\\}$，$B=\\{x\\mid x^2-4x+m=0\\}$，$A\\cap B=\\{1\\}$。\n\n"
    "∴$x=1$ 是方程 $x^2-4x+m=0$ 的解，即 $1-4+m=0$。\n\n"
    "∴$m=3$，$∴B=\\{1,3\\}$，故选 C。"
)


def test_default_pdf_export_normalizes_reasoning_on_a_copy_only():
    items = [{"question": {"id": 1, "question_type": "detailed_answer", "content": "求集合。", "answer_markdown": SAMPLE_ANSWER}, "score": 0}]
    original = deepcopy(items)
    text = build_latex_document("推理符号验证", "", "quiz", items, include_answers=True, show_secret=False, show_notice=False)
    assert items == original
    assert "∵" not in text and "∴" not in text
    assert text.count(r"\because") == 1 and text.count(r"\therefore") == 3
    assert clean_content_for_latex(SAMPLE_ANSWER, is_answer=True).count("$") == SAMPLE_ANSWER.count("$")


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_SYMBOL_NATIVE") != "1" or not shutil.which("xelatex"),
                    reason="Opt-in real XeLaTeX reasoning-symbol glyph check")
def test_native_pdf_reasoning_prose_math_and_text_macros_have_glyphs(tmp_path):
    import pymupdf as fitz

    content = "正文∵条件成立，∴结论成立。\n\n$∵a=1,∴b=2$\n\n" + r"$\text{∵条件成立，∴}x=1$"
    items = [{"question": {"id": 1, "question_type": "detailed_answer", "content": content,
                            "answer_markdown": SAMPLE_ANSWER}, "score": 0}]
    text = build_latex_document("推理符号原生字形", "", "quiz", items, include_answers=True, show_secret=False, show_notice=False)
    (tmp_path / "paper.tex").write_text(text)
    for _ in range(2):
        result = subprocess.run([shutil.which("xelatex"), "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", "paper.tex"],
                                cwd=tmp_path, capture_output=True, text=True, timeout=45)
        log = result.stdout + result.stderr
        assert result.returncode == 0, log[-7000:]
        assert "Missing character" not in log and "Undefined control sequence" not in log
    with fitz.open(tmp_path / "paper.pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert text.count("∵") == 4 and text.count("∴") == 6
        fonts = [span["font"] for page in document for block in page.get_text("dict")["blocks"]
                 for line in block.get("lines", []) for span in line["spans"] if any(sign in span["text"] for sign in "∵∴")]
        assert fonts and all("XITS" in font for font in fonts)
        for index, page in enumerate(document):
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(tmp_path / f"page-{index + 1}.png")
