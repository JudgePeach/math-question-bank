"""Export-only prose glyph fallback; database access is forbidden in this suite."""

import copy
import sqlite3

import pytest
from sqlalchemy.engine import Engine

from mathbank.geometry_symbols import normalize_school_math_symbols


@pytest.fixture(autouse=True)
def forbid_database_access(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Geometry export tests must not access a database")

    monkeypatch.setattr(sqlite3, "connect", deny)
    monkeypatch.setattr(Engine, "connect", deny)


def latex(source):
    return normalize_school_math_symbols(source, target="latex")


@pytest.mark.parametrize("source,expected", [
    ("在△ABC中，∠A=30°。", r"在\ensuremath{\triangle}ABC中，\ensuremath{\angle}A=30°。"),
    ("Rt△ABC，∠D=∠ABC。", r"Rt\ensuremath{\triangle}ABC，\ensuremath{\angle}D=\ensuremath{\angle}ABC。"),
    (r"△$ABC$，∠\(ABC\)", r"\ensuremath{\triangle}$ABC$，\ensuremath{\angle}\(ABC\)"),
    (r"△\triangleCustom，∠\angleCustom", r"\ensuremath{\triangle}\triangleCustom，\ensuremath{\angle}\angleCustom"),
    (r"\\△ABC", r"\\\ensuremath{\triangle}ABC"),
])
def test_prose_glyphs_use_math_font_without_including_adjacent_letters(source, expected):
    assert latex(source) == expected
    assert latex(expected) == expected


@pytest.mark.parametrize("source", [
    r"$△ABC+∠A$",
    r"$$△_1^2+∠A_{1}$$",
    r"\(△_{ABC}+∠A^2\)",
    r"\[△ABC+∠A\]",
    r"$\triangle ABC+\angle A$",
    r"\ensuremath{△ABC+∠A}",
    r"\begin{align}△ABC&=∠A_{1}\\△DEF&=∠D\end{align}",
    r"\begin{equation}△ABC+∠A\end{equation}",
    r"\begin{aligned}△ABC&=∠A\end{aligned}",
    r"\begin{tabular}{cc}$△ABC$ & \(∠A\)\end{tabular}",
])
def test_existing_math_unicode_spacing_and_commands_are_preserved(source):
    assert latex(source) == source


@pytest.mark.parametrize("command", [
    "text", "textrm", "textnormal", "textbf", "textit", "emph", "mbox", "hbox",
])
def test_text_arguments_inside_math_use_math_font_without_changing_scripts(command):
    source = rf"$△ABC+\{command}{{在△中∠A}}_1+∠A$"
    expected = rf"$△ABC+\{command}{{在\ensuremath{{\triangle}}中\ensuremath{{\angle}}A}}_1+∠A$"
    assert latex(source) == expected
    assert latex(expected) == expected


def test_nested_text_and_existing_math_keep_their_own_context():
    source = r"\text{△ABC $∠A$ 与\textbf{∠B}，\ensuremath{△DEF+\text{∠D}}}"
    expected = (r"\text{\ensuremath{\triangle}ABC $∠A$ 与\textbf{\ensuremath{\angle}B}，"
                r"\ensuremath{△DEF+\text{\ensuremath{\angle}D}}}")
    assert latex(source) == expected
    assert latex(expected) == expected


def test_table_prose_and_math_cells_remain_distinct():
    source = r"\begin{tabular}{cc}△ABC & $∠A$\\\begin{aligned}△&=∠\end{aligned} & ∠B\end{tabular}"
    expected = (r"\begin{tabular}{cc}\ensuremath{\triangle}ABC & $∠A$\\"
                r"\begin{aligned}△&=∠\end{aligned} & \ensuremath{\angle}B\end{tabular}")
    assert latex(source) == expected
    assert latex(expected) == expected


@pytest.mark.parametrize("protected", [
    r"`△ ∠ $∠A$`",
    "```latex\n△ ∠ $∠A$\n```\n",
    "~~~latex\n△ ∠\n~~~\n",
    r"\verb|△ ∠ $∠A$|",
    r"\Verb|△ ∠|",
    r"\lstinline|△ ∠|",
    r"\mintinline{tex}|△ ∠|",
    r"\detokenize{△ {∠}}",
    r"\begin{verbatim}△ ∠\end{verbatim}",
    r"\begin{comment}△ ∠\end{comment}",
    r"\begin{tikzpicture}\node {$△+∠$};\end{tikzpicture}",
    r"\begin{axis}\addplot coordinates {(0,0)};\node{△ ∠};\end{axis}",
    r"\tikz[baseline]{\node{△ ∠};}",
    r"![△ ∠](/static/uploads/△∠(1).png)",
    r"[source](https://example.org/△∠(1).png)",
    r"\includegraphics[width=3cm]{/static/uploads/△∠.png}",
    r"\url{https://example.org/△∠}",
    r"\href{https://example.org/△∠}{original}",
    r"\path{/static/uploads/△∠.png}",
    r"\label{△∠}",
    r"https://example.org/△∠.png",
    r"/static/uploads/△∠.png",
    r'<img src="/static/uploads/△∠.png">',
    r'<span title="△ ∠">',
    r"<code>△ ∠</code>",
    r"<pre>△ ∠</pre>",
    r"<!-- △ ∠ -->",
    r"% △ ∠ $∠A$",
    r'<mathbank-math id="MBM_TEST_0001">$△+\text{∠}$</mathbank-math>',
    r"[[MBM_TEST_0001]]",
    r"\△ \∠",
])
def test_literal_assets_drawings_comments_and_source_locks_are_unchanged(protected):
    source = protected + "\n外△∠。"
    assert latex(source) == protected + "\n外\\ensuremath{\\triangle}\\ensuremath{\\angle}。"


@pytest.mark.parametrize("source", [
    "$△ABC未闭合",
    "$$∠ABC未闭合",
    r"\(△ABC未闭合",
    r"\[∠ABC未闭合",
    r"\text{△ABC未闭合",
    r"\ensuremath{∠ABC未闭合",
    r"\begin{align}△ABC未闭合",
])
def test_unfinished_math_or_text_argument_is_not_guessed(source):
    assert latex(source) == source


def test_existing_reasoning_symbol_behavior_is_independent_of_geometry_pass():
    source = r"∵△ABC，$∴△ABC+\text{∵∠A}$"
    expected = (r"\ensuremath{\because}\ensuremath{\triangle}ABC，"
                r"$\therefore △ABC+\text{\ensuremath{\because}\ensuremath{\angle}A}$")
    assert latex(source) == expected
    assert latex(expected) == expected


@pytest.mark.parametrize("source", [
    "在△ABC中，∠A=30°。",
    r"$△ABC+\text{∠A}$",
    r"\text{△ABC}，\ensuremath{∠A}",
])
def test_word_export_keeps_its_existing_unicode_behavior(source):
    assert normalize_school_math_symbols(source, target="word") == source


def test_clean_content_entry_adapts_prose_and_text_but_preserves_math():
    from mathbank.paper_helper import clean_content_for_latex

    source = r"在△ABC中，∠A=30°，$△DEF+∠D+\text{∠E}$。"
    expected = (r"在\ensuremath{\triangle}ABC中，\ensuremath{\angle}A=30°，"
                r"$△DEF+∠D+\text{\ensuremath{\angle}E}$。")
    assert clean_content_for_latex(source, q_type="fill_in_blank") == expected
    assert clean_content_for_latex(expected, q_type="fill_in_blank") == expected


def test_latex_stem_and_answer_export_does_not_mutate_question_source():
    from mathbank.paper_helper import build_latex_document

    questions = [{"question": {"id": 1, "question_type": "fill_in_blank",
        "content": "在△ABC中，∠A已知。", "answer_markdown": "由∠A可得△ABC的面积。",
        "image_paths": []}, "score": 5}]
    original = copy.deepcopy(questions)
    tex = build_latex_document("几何符号", "", "quiz", questions, include_answers=True,
                               show_secret=False, show_notice=False)
    assert r"在\ensuremath{\triangle}ABC中，\ensuremath{\angle}A已知。" in tex
    assert r"由\ensuremath{\angle}A可得\ensuremath{\triangle}ABC的面积。" in tex
    assert questions == original
