"""Parallelogram export contracts; no application startup or database writes."""

import copy
import io
import os
import shutil
import zipfile

import pytest
from lxml import etree

from mathbank.geometry_symbols import normalize_parallelogram_symbols
from mathbank.paper_helper import build_latex_document, clean_content_for_latex, compile_tex_to_pdf
from mathbank.word_export_helper import MathToken, build_word_document, tokenize_mixed_content


SAMPLES = [
    r"在 $\parallelogram ABCD$ 中。",
    r"在 $▱ABCD$ 中。",
    r"在 $\text{▱}ABCD$ 中。",
    r"在▱$ABCD$中。",
    r"在\parallelogram $ABCD$中。",
    r"在\text{▱}$ABCD$中。",
    r"在▱ABCD中。",
    r"在\ensuremath{\parallelogram}$ABCD$中。",
]


@pytest.mark.parametrize("source", SAMPLES)
def test_forms_use_math_font_in_latex_and_remain_idempotent(source):
    result = normalize_parallelogram_symbols(source)
    assert result.count(r"\ensuremath{\parallelogram}") == 1
    assert "▱" not in result
    assert normalize_parallelogram_symbols(result) == result
    assert "$$" not in result
    assert r"\ensuremath{\parallelogram}" in clean_content_for_latex(source)


@pytest.mark.parametrize("source", SAMPLES)
def test_forms_become_word_math_without_touching_adjacent_delimiters(source):
    result = normalize_parallelogram_symbols(source, target="word")
    assert result.count("▱") == 1
    assert r"\parallelogram" not in result
    assert normalize_parallelogram_symbols(result, target="word") == result
    assert "$$" not in result
    tokens = tokenize_mixed_content(source)
    assert sum(token.latex.count("▱") for token in tokens if isinstance(token, MathToken)) == 1
    assert not any("▱" in token for token in tokens if isinstance(token, str))


@pytest.mark.parametrize("protected", [
    r"`$▱\parallelogram$`",
    "```latex\n$▱\\parallelogram$\n```\n",
    "~~~latex\n$▱\\parallelogram$\n~~~\n",
    r"\verb|$▱\parallelogram$|",
    r"\begin{tikzpicture}\node {$▱\parallelogram$};\end{tikzpicture}",
    r"\tikz[baseline]{\node {$▱\parallelogram$};}",
    r"\begin{verbatim}$▱\parallelogram$\end{verbatim}",
    r'<mathbank-math id="MBM_TEST_0001">$▱\parallelogram$</mathbank-math>',
    r"[[MBM_TEST_0001]]",
    r"![▱\parallelogram](/static/uploads/▱(1).png)",
    r"[source](https://example.org/▱(1).png)",
    r"\includegraphics[width=3cm]{/static/uploads/▱.png}",
    r"\url{https://example.org/▱}",
    r"\href{https://example.org/▱}{original}",
    r"\label{▱}",
    r"https://example.org/▱.png",
    r"% ▱\parallelogram",
    r"\\parallelogram",
    r"\parallelogramblack",
    r"\parallelogramCustom",
])
@pytest.mark.parametrize("target", ["latex", "word"])
def test_protected_source_and_longer_commands_remain_unchanged(protected, target):
    source = protected + "\n在▱中。"
    result = normalize_parallelogram_symbols(source, target=target)
    assert result.startswith(protected + "\n")
    assert result != source


def test_math_environments_and_text_context_keep_their_boundaries():
    source = r"\begin{align}\text{在▱中} & = \parallelogram ABCD\end{align}"
    result = normalize_parallelogram_symbols(source)
    assert r"\text{在\ensuremath{\parallelogram}中}" in result
    assert "$" not in result
    assert normalize_parallelogram_symbols(source, target="word") == source.replace(r"\parallelogram", "▱")
    table = r"\begin{tabular}{cc}\begin{aligned}\parallelogram ABCD\end{aligned} & ▱\end{tabular}"
    word = normalize_parallelogram_symbols(table, target="word")
    assert r"\begin{aligned}▱ ABCD\end{aligned}" in word
    assert r"& \(▱\)\end{tabular}" in word


def _questions():
    return [{"question": {
        "id": 701,
        "question_type": "single_choice",
        "content": "\n\n".join(SAMPLES) + "\n" + r"\begin{choices}\item $\parallelogram ABCD$\item $▱EFGH$\end{choices}",
        "answer_markdown": r"解：在 $\text{▱}ABCD$ 中，$AB=CD$。",
        "image_paths": [],
    }, "score": 5}]


def test_latex_export_adapts_stem_choices_answer_without_mutating_question():
    questions = _questions()
    original = copy.deepcopy(questions)
    tex = build_latex_document("平行四边形符号", "", "quiz", questions, include_answers=True)
    assert tex.count(r"\ensuremath{\parallelogram}") == len(SAMPLES) + 3
    assert "▱" not in tex
    assert questions == original


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Native Pandoc required")
def test_native_pandoc_exports_all_forms_as_editable_omml():
    questions = _questions()
    original = copy.deepcopy(questions)
    data, diagnostics = build_word_document(
        "平行四边形符号", "", "quiz", questions, include_answers=True,
        show_secret=False, show_notice=False,
    )
    assert questions == original
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        assert not any(name.startswith("word/media/") for name in archive.namelist())
    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
          "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    symbols = root.xpath(".//m:oMath//m:r[m:t[contains(., '▱')]]", namespaces=ns)
    assert sum("".join(node.xpath("./m:t/text()", namespaces=ns)).count("▱") for node in symbols) == len(SAMPLES) + 3
    assert not root.xpath(".//w:t[contains(., '▱')]", namespaces=ns)
    assert not any(node.xpath("./w:rPr/w:rFonts", namespaces=ns) for node in symbols)


@pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_SYMBOL_NATIVE") != "1" or not shutil.which("xelatex"),
    reason="Opt-in native XeLaTeX regression",
)
def test_native_xelatex_renders_all_forms_without_missing_characters(tmp_path):
    import pymupdf as fitz

    questions = _questions()
    questions[0]["question"]["content"] += "\n\n" + r"$\text{在▱中}$"
    tex = build_latex_document(
        "平行四边形符号", "", "quiz", questions, include_answers=True,
        show_secret=False, show_notice=False,
    )
    pdf, log = compile_tex_to_pdf(tex)
    assert pdf, log
    assert "Missing character" not in log
    assert "Undefined control sequence" not in log
    (tmp_path / "parallelogram.pdf").write_bytes(pdf)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert len(document) == 1
        assert not document[0].get_images(full=True)
        document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(tmp_path / "parallelogram.png")
