"""Repair only the text-table shell, preserving literal and mathematical data."""

import pytest

from mathbank.math_markdown import normalize_table_math_wrappers
from mathbank.content_locks import lock_visible_math, restore_visible_math


TABLE = r"\begin{tabular}{|c|c|}\hline 值 & $x_1\leqslant 2$ \\ \hline ![图](/static/uploads/a.png) & $\dfrac{1}{2}$ \\ \hline \end{tabular}"


@pytest.mark.parametrize("opening,closing", [("$$", "$$"), ("$", "$"), (r"\[", r"\]"), (r"\(", r"\)")])
@pytest.mark.parametrize("environment", ["tabular", "tabular*", "tabularx", "longtable", "tblr", "longtblr", "talltblr"])
def test_removes_only_complete_table_outer_math_shell(opening, closing, environment):
    table = TABLE.replace("{tabular}", "{" + environment + "}")
    source = "正文\n" + opening + "\n" + table + "\n" + closing + "\n后文 $y=3$。"
    assert normalize_table_math_wrappers(source) == "正文\n\n" + table + "\n\n后文 $y=3$。"


@pytest.mark.parametrize("template", [
    "```tex\n$$TABLE$$\n```", "```tex\n$$TABLE$$", "~~~tex\n$$TABLE$$\n~~~", "~~~tex\n$$TABLE$$", "`$$TABLE$$`",
    r"\begin{tikzpicture}\node{$$TABLE$$};\end{tikzpicture}",
    r"\begin{verbatim}$$TABLE$$\end{verbatim}",
    r"\begin{verbatim*}$$TABLE$$\end{verbatim*}",
    r"\begin{minted}{tex}$$TABLE$$\end{minted}",
    r"\verb|$$TABLE$$|", r"\Verb|$$TABLE$$|", r"\lstinline[language=TeX]|$$TABLE$$|",
    r"\lstinline{$$TABLE$$}", r"\mintinline{tex}|$$TABLE$$|", r"\mintinline{tex}{$$TABLE$$}",
    r"\detokenize{$$TABLE$$}", r"\url{$$TABLE$$}", r"\path{$$TABLE$$}",
    r"<mathbank-math id='MBM_A_1'>$$TABLE$$</mathbank-math>",
    '<span data-example="$$TABLE$$">literal attribute</span>',
    '<code>$$TABLE$$</code>', '<pre class="example">$$TABLE$$</pre>',
    '<CODE class="example">$$TABLE$$</code>', '<pre>$$TABLE$$',
    "% $$TABLE$$", r"$A+TABLE$", r"$$TABLE + x$$", r"$$x + TABLE$$",
])
def test_literal_and_mixed_math_are_unchanged(template):
    source = template.replace("TABLE", TABLE)
    assert normalize_table_math_wrappers(source) == source


def test_array_cases_and_incomplete_tables_are_unchanged():
    for source in (r"$\begin{array}{cc}1&2\end{array}$", r"$$\begin{cases}x=1\end{cases}$$",
                   "$$" + TABLE.replace(r"\end{tabular}", "") + "$$",
                   "$$" + TABLE.replace(r"\end{tabular}", r"\end{array}") + "$$"):
        assert normalize_table_math_wrappers(source) == source


def test_cell_math_locks_remain_editable_table_structure_and_restore_exactly():
    repaired = normalize_table_math_wrappers("$$" + TABLE + "$$")
    assert repaired == TABLE
    locked, locks = lock_visible_math(repaired, "TABLE_TEST")
    assert len(locks) == 2
    assert r"\begin{tabular}" in locked and r"\end{tabular}" in locked
    questions = [{"content": locked, "answer_markdown": ""}]
    restored = restore_visible_math(questions, locks)
    assert restored["math_locks_restored"] == 2
    assert questions[0]["content"] == repaired


def test_multiple_tables_and_nested_table_preserve_all_inner_bytes():
    nested = r"\begin{tabular}{c}" + TABLE + r"\end{tabular}"
    source = "$$" + TABLE + "$$\n$$" + nested + "$$"
    assert normalize_table_math_wrappers(source) == TABLE + "\n" + nested
    assert normalize_table_math_wrappers(normalize_table_math_wrappers(source)) == normalize_table_math_wrappers(source)


def test_internal_placeholder_text_is_never_reinterpreted_or_replaced():
    literal = "\ue0000\ue001 \ue000OCR_TABLE_0\ue001 `literal`"
    source = literal + "\n$$" + TABLE + "$$"
    assert normalize_table_math_wrappers(source) == literal + "\n" + TABLE


def test_display_table_image_is_not_mistaken_for_a_markdown_link():
    source = r"\[" + TABLE + r"\]"
    assert normalize_table_math_wrappers(source) == TABLE
    assert normalize_table_math_wrappers(source + "(备注)") == TABLE + "(备注)"
