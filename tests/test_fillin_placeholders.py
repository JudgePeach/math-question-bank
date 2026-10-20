"""The Word triangle placeholder repair changes only proven source spans."""

import pytest

from mathbank.fillin_placeholders import (
    fillin_placeholder_edits, normalize_fillin_placeholder_clusters,
)


CLUSTER = r"\fillin\underline{▲}\fillin"


def apply_edits(source, edits):
    for start, end, replacement in reversed(edits):
        source = source[:start] + replacement + source[end:]
    return source


@pytest.mark.parametrize("cluster", [
    CLUSTER,
    r"\fillin \underline { ▲ } \fillin",
    "\\fillin\n\\underline{▲}\t\\fillin",
    r"\fillin\fillin \underline{▲} \fillin\fillin",
    r"\fillin\underline{▲}\fillin\underline{▲}\fillin",
])
def test_proven_prose_cluster_has_one_auditable_idempotent_edit(cluster):
    source = "前文 $x=1$，填写 " + cluster + "．后文 $a_n$。"
    expected = "前文 $x=1$，填写 " + r"\fillin" + "．后文 $a_n$。"
    edits = fillin_placeholder_edits(source)
    assert edits == [(source.index(cluster), source.index(cluster) + len(cluster), r"\fillin")]
    assert apply_edits(source, edits) == expected
    assert normalize_fillin_placeholder_clusters(source) == expected
    assert normalize_fillin_placeholder_clusters(expected) == expected
    assert fillin_placeholder_edits(expected) == []


@pytest.mark.parametrize("suffix", ["。", "．", "，", "  cm", "\n厘米", "厘米", "é", r"\text{厘米}", ""])
def test_last_macros_existing_suffix_and_separation_survive_exactly(suffix):
    source = "值为 " + CLUSTER + suffix
    assert normalize_fillin_placeholder_clusters(source) == "值为 " + r"\fillin" + suffix


@pytest.mark.parametrize("source", [
    r"\fillin\underline{△}\fillin", r"\fillin\underline{ABC}\fillin",
    r"\fillin\underline{▲+1}\fillin", r"\fillin\underline{\triangle}\fillin",
    r"\fillin\underline{▲}答案\fillin", r"\fillin\underline{▲}",
    r"\underline{▲}\fillin", r"\fillin\underline{▲}\underline{▲}\fillin",
    r"\fillin*\underline{▲}\fillin", r"\fillin[2]\underline{▲}\fillin",
    r"\fillin[]\underline{▲}\fillin", r"\fillin{a}\underline{▲}\fillin",
    CLUSTER + "*", CLUSTER + " [2]", CLUSTER + "{x}", CLUSTER + " ]",
    CLUSTER + "cm", CLUSTER + "Custom", "\\" + CLUSTER,
    r"\fillin\underline{▲\fillin", r"\fillin\underline{▲]}\fillin",
    "{" + CLUSTER + "}", r"\textbf{" + CLUSTER + "}",
    "[" + CLUSTER + "]", "{" + CLUSTER, "{[}" + CLUSTER,
    CLUSTER + "}", CLUSTER + "]",
    "$" + CLUSTER + "$", "$$" + CLUSTER + "$$",
    r"\(" + CLUSTER + r"\)", r"\[" + CLUSTER + r"\]",
    "$x+" + CLUSTER + "$", "$" + CLUSTER, "$$" + CLUSTER + "$",
    r"\(" + CLUSTER + r"\]", "$$$" + CLUSTER + "$$$",
    r"\begin{alignat}{2}x=&" + CLUSTER + r"\end{alignat}",
    r"\begin{equation}" + CLUSTER + r"\end{equation}",
    r"\begin{equation}" + CLUSTER,
    r"\begin{tikzpicture}\node{" + CLUSTER + r"};\end{tikzpicture}",
    "`" + CLUSTER + "`", "```tex\n" + CLUSTER + "\n```",
    "~~~tex\n" + CLUSTER + "\n~~~", "\n    " + CLUSTER + "\n",
    r"\verb|" + CLUSTER + "|", r"\Verb+" + CLUSTER + "+",
    r"\lstinline{" + CLUSTER + "}", r"\mintinline{tex}|" + CLUSTER + "|",
    r"\detokenize{" + CLUSTER + "}", r"\texttt{" + CLUSTER + "}",
    "<!-- " + CLUSTER + " -->", "% " + CLUSTER + "\n",
    "<code>" + CLUSTER + "</code>", "<pre>" + CLUSTER + "</pre>",
    '<span title="a">' + CLUSTER + '</span>',
    '<img title="' + CLUSTER + '" src="/static/uploads/a.png">',
    "![" + CLUSTER + "](/static/uploads/a.png)",
    "![图](/static/uploads/" + CLUSTER + ".png)",
    "[链接](https://example.test/" + CLUSTER + ")",
    "https://example.test/" + CLUSTER,
    r"\includegraphics{" + CLUSTER + ".png}",
    '<mathbank-math id="MBM_0001">' + CLUSTER + '</mathbank-math>',
    '<mathbank-math id="MBM_0001">' + CLUSTER,
    "<!-- MathBank source attribution:\n" + CLUSTER + "\n-->\n\n正文",
])
def test_real_symbols_arguments_math_malformed_source_and_literals_stay_exact(source):
    assert fillin_placeholder_edits(source) == []
    assert normalize_fillin_placeholder_clusters(source) == source


def test_literal_delimiters_do_not_hide_a_neighboring_proven_blank():
    source = "`$` " + CLUSTER + "；" + r"\verb|{|" + " " + CLUSTER
    expected = "`$` " + r"\fillin" + "；" + r"\verb|{|" + " " + r"\fillin"
    assert normalize_fillin_placeholder_clusters(source) == expected


@pytest.mark.parametrize("formula", [
    r"$\begin{matrix}1&2\\3&4\end{matrix}$",
    r"$$\begin{pmatrix}1&2\\3&4\end{pmatrix}$$",
    r"\(\begin{cases}x&x>0\\0&x\le0\end{cases}\)",
    r"\[\begin{aligned}x&=1\\y&=2\end{aligned}\]",
    r"$\begin{matrix}\begin{cases}1&x>0\end{cases}&2\end{matrix}$",
    r"\begin{equation}\begin{matrix}1&2\end{matrix}\end{equation}",
])
def test_complete_math_subenvironments_do_not_hide_later_prose_clusters(formula):
    source = formula + "。后题答案 " + CLUSTER + "。"
    expected = formula + "。后题答案 " + r"\fillin" + "。"
    assert normalize_fillin_placeholder_clusters(source) == expected
    assert fillin_placeholder_edits(source) == [(
        len(formula) + len("。后题答案 "), len(source) - 1, r"\fillin",
    )]


@pytest.mark.parametrize("formula", [
    r"$\begin{matrix}1&2$",
    r"$\begin{matrix}1&2\end{cases}$",
    r"$\end{matrix}$",
    r"\begin{equation}\begin{matrix}1&2\end{equation}",
])
def test_malformed_math_subenvironments_keep_later_unproven_source(formula):
    source = formula + "。后题答案 " + CLUSTER + "。"
    assert normalize_fillin_placeholder_clusters(source) == source
    assert fillin_placeholder_edits(source) == []


def test_multiple_edits_keep_original_offsets_and_all_other_bytes():
    source = "甲 " + CLUSTER + "\n乙 " + CLUSTER + "  。"
    edits = fillin_placeholder_edits(source)
    assert len(edits) == 2
    assert all(source[start:end] == CLUSTER for start, end, _ in edits)
    assert apply_edits(source, edits) == "甲 " + r"\fillin" + "\n乙 " + r"\fillin" + "  。"
    assert normalize_fillin_placeholder_clusters(source) == apply_edits(source, edits)


def test_table_cell_remains_at_its_original_anchor():
    source = r"\begin{tabular}{cc}数值 & " + CLUSTER + r" \\\end{tabular}"
    assert normalize_fillin_placeholder_clusters(source) == (
        r"\begin{tabular}{cc}数值 & \fillin \\\end{tabular}"
    )


def test_empty_or_absent_input_has_no_edits():
    for source in ("", None, "普通正文", r"\fillin", "▲"):
        assert fillin_placeholder_edits(source) == []
        assert normalize_fillin_placeholder_clusters(source) == (source or "")
