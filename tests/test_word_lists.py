"""Word list parsing preserves prose, formulas and unproven source blocks."""

from dataclasses import FrozenInstanceError

import pytest

from mathbank.word_lists import LatexList, LatexListItem, find_latex_lists


SIMPLE = r"\begin{enumerate}\item 求 $E$ 的方程；\item 求 $k$.\end{enumerate}"


def test_same_line_items_have_exact_source_offsets_and_bodies():
    source = "前文\n" + SIMPLE + "\n后文"
    assert find_latex_lists(source) == [LatexList(
        3, 3 + len(SIMPLE), "enumerate",
        (LatexListItem(" 求 $E$ 的方程；"), LatexListItem(" 求 $k$.")),
    )]
    assert source[find_latex_lists(source)[0].start:find_latex_lists(source)[0].end] == SIMPLE


def test_multiline_items_preserve_all_whitespace_and_labels():
    source = "\\begin {enumerate}\n\\item [(1)]\n第一问\n\n续写\n\\item[{(2)[附]}] 第二问\n\\end {enumerate}"
    parsed = find_latex_lists(source)[0]
    assert parsed.items == (
        LatexListItem("\n第一问\n\n续写\n", "(1)"),
        LatexListItem(" 第二问\n", "{(2)[附]}"),
    )


def test_nested_mixed_lists_remain_in_parent_body_and_can_be_parsed_recursively():
    nested = r"\begin{itemize}\item 甲\item[(乙)] 乙\end{itemize}"
    source = r"\begin{enumerate}\item 第一问 " + nested + r" 续写\item 第二问\end{enumerate}"
    outer = find_latex_lists(source)
    assert len(outer) == 1
    assert len(outer[0].items) == 2
    assert outer[0].items[0].content == " 第一问 " + nested + " 续写"
    inner = find_latex_lists(outer[0].items[0].content)
    assert len(inner) == 1
    assert inner[0].environment == "itemize"
    assert inner[0].items == (LatexListItem(" 甲"), LatexListItem(" 乙", "(乙)"))


def test_independent_lists_do_not_share_offsets_or_numbering_state():
    source = SIMPLE + "\n正文\n" + SIMPLE.replace("enumerate", "itemize")
    parsed = find_latex_lists(source)
    assert len(parsed) == 2
    assert parsed[0].end < parsed[1].start
    assert [item.environment for item in parsed] == ["enumerate", "itemize"]


@pytest.mark.parametrize("environment,body", [
    ("choices", r"\item A\item B"),
    ("cases", r"x&\item\\y&z"),
    ("tabular", r"{cc}甲&\item 乙\\丙&丁"),
    ("unknown", r"\item 内部项"),
])
def test_items_in_non_list_environments_do_not_split_parent_items(environment, body):
    block = rf"\begin{{{environment}}}" + body + rf"\end{{{environment}}}"
    source = r"\begin{enumerate}\item 第一问 " + block + r" 续写\item 第二问\end{enumerate}"
    parsed = find_latex_lists(source)
    assert len(parsed) == 1
    assert len(parsed[0].items) == 2
    assert block in parsed[0].items[0].content
    assert find_latex_lists(block) == []


@pytest.mark.parametrize("label", [
    "(1)", "第一问", r"{a]b}", "[嵌套]", r"$x_{[0]}$", r"{\textbf{甲}}",
    r"左\{右\}", "",
])
def test_balanced_optional_labels_are_preserved_exactly(label):
    source = r"\begin{enumerate}\item[" + label + r"] 正文\end{enumerate}"
    assert find_latex_lists(source)[0].items == (LatexListItem(" 正文", label),)


@pytest.mark.parametrize("wrapper", [
    lambda source: "$" + source + "$",
    lambda source: "$$" + source + "$$",
    lambda source: r"\(" + source + r"\)",
    lambda source: r"\[" + source + r"\]",
    lambda source: r"\begin{equation}" + source + r"\end{equation}",
    lambda source: "`" + source + "`",
    lambda source: "```tex\n" + source + "\n```",
    lambda source: "~~~tex\n" + source + "\n~~~",
    lambda source: "\n    " + source + "\n",
    lambda source: r"\verb|" + source + "|",
    lambda source: r"\Verb+" + source + "+",
    lambda source: r"\lstinline{" + source + "}",
    lambda source: r"\mintinline{tex}|" + source + "|",
    lambda source: r"\detokenize{" + source + "}",
    lambda source: r"\texttt{" + source + "}",
    lambda source: r"\url{" + source + "}",
    lambda source: r"\path{" + source + "}",
    lambda source: r"\begin{verbatim}" + source + r"\end{verbatim}",
    lambda source: r"\begin{tikzpicture}\node{" + source + r"};\end{tikzpicture}",
    lambda source: "<!-- " + source + " -->",
    lambda source: "% " + source + "\n",
    lambda source: "<pre>" + source + "</pre>",
    lambda source: "<code>" + source + "</code>",
    lambda source: '<span title="' + source + '">文字</span>',
    lambda source: '<span title="含 > ' + source + '">文字</span>',
    lambda source: '<img alt="' + source + '" src="/static/uploads/a.png">',
    lambda source: "![" + source + "](/static/uploads/a.png)",
    lambda source: "[链接](https://example.test/" + source + ")",
    lambda source: "https://example.test/" + source.replace(" ", ""),
    lambda source: '<mathbank-math id="MBM_0001">' + source + '</mathbank-math>',
    lambda source: "{" + source + "}",
    lambda source: r"\textbf{" + source + "}",
])
def test_literals_math_links_attributes_and_argument_groups_are_opaque(wrapper):
    assert find_latex_lists(wrapper(SIMPLE)) == []


@pytest.mark.parametrize("literal", [
    r"$x+\item+y$", r"$$x+\item+y$$", r"\(x+\item+y\)", r"\[x+\item+y\]",
    r"`\item`", r"\verb|\item|", r"\texttt{\item}",
    r"![\item](/static/uploads/a.png)", '<span title="\\item">内容</span>',
    r"\textbf{\item 不是父项}", r"\\item 不是命令",
])
def test_protected_item_tokens_do_not_split_parent_items(literal):
    source = r"\begin{enumerate}\item 第一问 " + literal + r" 续写\item 第二问\end{enumerate}"
    parsed = find_latex_lists(source)
    assert len(parsed) == 1
    assert len(parsed[0].items) == 2
    assert literal in parsed[0].items[0].content


@pytest.mark.parametrize("source", [
    r"\begin{enumerate}\end{enumerate}",
    r"\begin{enumerate}正文\end{enumerate}",
    r"\begin{enumerate}前言\item 甲\end{enumerate}",
    r"\begin{enumerate}[label=(\arabic*)]\item 甲\end{enumerate}",
    r"\begin{enumerate}\item\end{enumerate}",
    r"\begin{enumerate}\item 甲\item\end{enumerate}",
    r"\begin{enumerate}\item[未闭合 正文\end{enumerate}",
    r"\begin{enumerate}\item[{错] 正文\end{enumerate}",
    r"\begin{enumerate}\item 甲\end{itemize}",
    r"\begin{enumerate}\item 甲",
    r"\begin{enumerate}\item {甲\end{enumerate}",
    r"\begin{enumerate}\item 甲}\end{enumerate}",
    r"\begin{enumerate}\item 甲\begin{tabular}{c}\item 乙\end{enumerate}",
    r"\\begin{enumerate}\item 甲\\end{enumerate}",
])
def test_unproven_lists_remain_raw(source):
    assert find_latex_lists(source) == []


@pytest.mark.parametrize("prefix,suffix", [
    (r"\begin{enumerate}", ""),
    (r"\begin{enumerate}前言", r"\end{enumerate}"),
    (r"\begin{enumerate}[label=custom]", r"\end{enumerate}"),
    (r"\begin{enumerate}\item 甲", r"\end{itemize}"),
    (r"\begin{unknown}", ""),
])
def test_bad_or_unsupported_outer_structures_never_expose_inner_lists(prefix, suffix):
    assert find_latex_lists(prefix + SIMPLE + suffix) == []


def test_complete_unsupported_list_does_not_hide_a_separate_valid_list():
    unsupported = r"\begin{enumerate}[label=custom]\item 甲\end{enumerate}"
    parsed = find_latex_lists(unsupported + "\n" + SIMPLE)
    assert len(parsed) == 1
    assert parsed[0].start == len(unsupported) + 1


@pytest.mark.parametrize("introduction", [
    "  % 说明和伪 \\item\n", "\n<!-- 说明和伪 \\item -->\n",
    " % 第一行\n % 第二行\n",
])
def test_comments_before_first_item_do_not_make_a_visible_introduction(introduction):
    source = r"\begin{enumerate}" + introduction + r"\item 正文\end{enumerate}"
    assert find_latex_lists(source)[0].items == (LatexListItem(" 正文"),)


@pytest.mark.parametrize("introduction", [
    "`可见代码`", "<code>可见代码</code>", r"\texttt{可见代码}",
    "$x$", "![图](/static/uploads/a.png)", '<span title="属性">可见前言</span>',
])
def test_protected_visible_introductions_are_not_silently_discarded(introduction):
    source = r"\begin{enumerate}" + introduction + r"\item 正文\end{enumerate}"
    assert find_latex_lists(source) == []


def test_literal_math_markers_do_not_consume_neighboring_lists():
    source = r"`$` \verb|\[| " + SIMPLE
    assert len(find_latex_lists(source)) == 1


def test_data_objects_are_immutable():
    parsed = find_latex_lists(SIMPLE)[0]
    with pytest.raises(FrozenInstanceError):
        parsed.start = 1
    with pytest.raises(FrozenInstanceError):
        parsed.items[0].content = "changed"
