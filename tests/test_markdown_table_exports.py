import io
import zipfile

import pytest
from lxml import etree
from PIL import Image

from mathbank.markdown_tables import (
    find_markdown_tables, normalize_table_cell_math, split_markdown_row,
)
from mathbank.paper_helper import clean_content_for_latex, _normalize_display_array_tags
from mathbank import word_export_helper as word


DISTRIBUTION = (
    "因此 $X$ 的分布列为\n\n"
    "| X | -1 | 0 | 1 |\n"
    "| --- | --- | --- | --- |\n"
    "| P | (1-α)β | αβ+(1-α)(1-β) | α(1-β) |\n\n"
    "由此得到期望。"
)


def test_strict_table_offsets_and_cells_preserve_original_source():
    source = DISTRIBUTION.replace("\n", "\r\n")
    table, = find_markdown_tables(source)
    assert table.header == ("X", "-1", "0", "1")
    assert table.rows == (("P", "(1-α)β", "αβ+(1-α)(1-β)", "α(1-β)"),)
    assert source[table.start:table.end].startswith("| X |")
    assert source[:table.start] == "因此 $X$ 的分布列为\r\n\r\n"
    assert source[table.end:] == "\r\n由此得到期望。"


def test_row_pipes_in_formula_literal_link_and_tex_group_are_not_columns():
    row = r"| $P(|X|=1)$ | `a|b` | [标签](https://example.test/a|b) | \text{a|b} | \| |"
    assert split_markdown_row(row) == (
        r"$P(|X|=1)$", "`a|b`", "[标签](https://example.test/a|b)",
        r"\text{a|b}", r"\|",
    )
    assert split_markdown_row(r"| \verb!a|b! | c |") == (r"\verb!a|b!", "c")


@pytest.mark.parametrize("source", [
    "| A | B |\n| --- | --- |",  # missing body
    "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |",  # ragged
    "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 |",  # do not convert a prefix
    "| A | B |\n| --- | --- |\n| 1 | 2 |\n| --- | --- |",
    "| A | B |\n| --- | --- |\n| $x | y |",  # incomplete math
    "| A | B |\n| --- | --- |\n| `x | y |",  # incomplete code
    "| A | B |\n| --- | --- |\n| [x | y |",  # incomplete grouping
    "    | A | B |\n    | --- | --- |\n    | 1 | 2 |",  # indented code
    "| A | B |\n| --- | --- |\n| \t`x | y |",  # incomplete code
])
def test_uncertain_or_incomplete_table_is_left_unrecognized(source):
    assert not find_markdown_tables(source)


@pytest.mark.parametrize("opening,closing", [
    ("```text\n", "\n```"), ("~~~\n", "\n~~~"),
    ("`\n", "\n`"), ("``\n", "\n``"),
    ("$$\n", "\n$$"), (r"\[" + "\n", "\n" + r"\]"),
    (r"\begin{tabular}{cc}" + "\n", "\n" + r"\end{tabular}"),
    (r"\begin{tikzpicture}" + "\n", "\n" + r"\end{tikzpicture}"),
    ("<pre>\n", "\n</pre>"), ("<table>\n", "\n</table>"),
])
def test_table_shaped_text_inside_literal_or_latex_block_is_protected(opening, closing):
    table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert not find_markdown_tables(opening + table + closing)


def test_half_open_interval_cells_remain_two_columns():
    source = "| 分组 | 频数 |\n| --- | --- |\n| [10000,+∞) | 2 |"
    table, = find_markdown_tables(source)
    assert table.rows[0] == ("[10000,+∞)", "2")
    assert normalize_table_cell_math(table.rows[0][0]) == r"$[10000,+\infty )$"


@pytest.mark.parametrize("is_answer", [False, True])
def test_latex_distribution_table_wraps_cells_without_rewriting_source(is_answer):
    before = DISTRIBUTION
    result = clean_content_for_latex(DISTRIBUTION, is_answer=is_answer)
    assert r"\begin{tabularx}{\linewidth}" in result
    assert result.count(r"\\ \hline") == 2
    assert r"$\alpha \beta +(1-\alpha )(1-\beta )$" in result
    assert "| --- |" not in result
    assert "因此 $X$ 的分布列为" in result
    assert "由此得到期望。" in result
    assert DISTRIBUTION == before
    assert r"\resizebox" not in result


def test_latex_table_images_use_cell_width_and_code_does_not_enter_math():
    source = ("| 图 | 文字 |\n| --- | --- |\n"
              r"| ![](/static/uploads/a_b.png) | `x_y|$5` & 20% |")
    result = clean_content_for_latex(source)
    assert r"\includegraphics[max width=\linewidth,max height=4.0cm" in result
    assert "{a_b.png}" in result
    assert r"\texttt{x\_y\textbar{}\textdollar{}5}" in result
    assert r"\& 20\%" in result


def _native_formula(formula):
    root = etree.Element("{" + word.MATH_NS + "}oMath", nsmap={"m": word.MATH_NS})
    run = etree.SubElement(root, "{" + word.MATH_NS + "}r")
    etree.SubElement(run, "{" + word.MATH_NS + "}t").text = formula
    return etree.tostring(root)


def _document(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def test_word_tables_are_native_editable_and_keep_formulas_and_anchor_order(monkeypatch, tmp_path):
    converted = []

    def convert(self, formulas):
        converted.extend(formulas)
        return {formula: _native_formula(formula) for formula in formulas}

    monkeypatch.setattr(word.PandocOmmlConverter, "convert_many", convert)
    Image.new("RGB", (320, 80), "white").save(tmp_path / "a_b.png")
    source = ("前文\n\n| 图 | 数值 |\n| :--- | ---: |\n"
              r"| ![](/static/uploads/a_b.png) | $\dfrac{1}{2}$ |"
              "\n\n后文")
    data, diagnostics = word.build_word_document("表格验证", "", "quiz", [{
        "question": {"id": 1, "question_type": "detailed_answer", "content": source,
                     "answer_markdown": DISTRIBUTION, "image_paths": []}, "score": 5,
    }], include_answers=True, uploads_dir=tmp_path)
    root = _document(data)
    ns = {"w": word.WORD_NS, "m": word.MATH_NS,
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    tables = root.xpath(".//w:tbl", namespaces=ns)
    assert len(tables) == 2
    assert len(tables[0].xpath("./w:tr", namespaces=ns)) == 2
    assert len(tables[1].xpath("./w:tr[1]/w:tc", namespaces=ns)) == 4
    assert tables[0].xpath(".//a:blip", namespaces=ns)
    assert tables[0].xpath(".//m:oMath", namespaces=ns)
    assert r"\alpha \beta +(1-\alpha )(1-\beta )" in converted
    assert diagnostics["failed_formulas"] == 0
    text = "".join(root.xpath(".//w:t/text()", namespaces=ns))
    assert "---" not in text and "前文" in text and "后文" in text
    children = list(root.find("w:body", namespaces=ns))
    before = next(i for i, child in enumerate(children) if "前文" in "".join(child.itertext()))
    table_index = children.index(tables[0])
    after = next(i for i, child in enumerate(children) if "后文" in "".join(child.itertext()))
    assert before < table_index < after


def test_word_wide_table_keeps_complete_grid_and_normal_font(monkeypatch):
    monkeypatch.setattr(word.PandocOmmlConverter, "convert_many", lambda self, formulas: {})
    header = tuple("列" + str(index) for index in range(12))
    rows = [tuple(str(row * 12 + col) for col in range(12)) for row in range(13)]
    source = "\n".join("| " + " | ".join(row) + " |" for row in
                       (header, ("---",) * 12, *rows))
    data, _ = word.build_word_document("宽表验证", "", "quiz", [{
        "question": {"id": 1, "question_type": "detailed_answer", "content": source},
        "score": 5,
    }])
    root = _document(data)
    ns = {"w": word.WORD_NS}
    table, = root.xpath(".//w:tbl", namespaces=ns)
    assert len(table.xpath("./w:tr", namespaces=ns)) == 14
    assert all(len(row.findall("w:tc", namespaces=ns)) == 12
               for row in table.xpath("./w:tr", namespaces=ns))
    widths = [int(node.get("{" + word.WORD_NS + "}w"))
              for node in table.xpath("./w:tblGrid/w:gridCol", namespaces=ns)]
    assert sum(widths) == 9000
    assert set(table.xpath(".//w:sz/@w:val", namespaces=ns)) == {"21"}
    assert "155" in "".join(table.xpath(".//w:t/text()", namespaces=ns))
    assert table.xpath("./w:tr[1]/w:trPr/w:tblHeader", namespaces=ns)


def test_word_table_code_dollars_are_literal_and_escaped_pipes_keep_one_cell(monkeypatch):
    converted = []

    def convert(self, formulas):
        converted.extend(formulas)
        return {formula: _native_formula(formula) for formula in formulas}

    monkeypatch.setattr(word.PandocOmmlConverter, "convert_many", convert)
    source = ("| 代码 | 正文 |\n| --- | --- |\n"
              r"| `$x$|2` \verb!$y$|3! | a \| b，$\|z\|$ |")
    data, _ = word.build_word_document("字面内容", "", "quiz", [{
        "question": {"id": 1, "question_type": "detailed_answer", "content": source},
        "score": 5,
    }])
    root = _document(data)
    ns = {"w": word.WORD_NS, "m": word.MATH_NS}
    assert "x" not in converted and "y" not in converted
    assert r"\|z\|" in converted
    texts = "".join(root.xpath(".//w:tbl//w:t/text()", namespaces=ns))
    assert "$x$|2" in texts and "$y$|3" in texts and "a | b" in texts
    table, = find_markdown_tables(source)
    from mathbank.markdown_tables import markdown_table_to_latex
    latex = markdown_table_to_latex(table)
    assert r"\texttt{\textdollar{}y\textdollar{}\textbar{}3}" in latex


@pytest.mark.parametrize("opening,closing", [("$$", "$$"), (r"\[", r"\]")])
def test_latex_moves_unique_array_tag_to_its_outer_display(opening, closing):
    body = r" P(B)=P(Y>n) \tag {6.9.7} \\ = P(Y=n+1)+P(Y=2n) \\ "
    source = opening + r"\begin{array}{l}" + body + r"\end{array}" + closing
    result = _normalize_display_array_tags(source)
    assert result == (r"\[\begin{array}{l}" + body.replace(r"\tag {6.9.7}", "")
                      + r"\end{array}\tag {6.9.7}\]")
    assert source != result
    assert _normalize_display_array_tags(result) == result


@pytest.mark.parametrize("source", [
    r"$$\begin{array}{l}x\tag{a}\\y\tag{b}\end{array}$$",
    r"$$z+\begin{array}{l}x\tag{a}\end{array}$$",
    r"$$\begin{array}{l}\begin{array}{c}x\tag{a}\end{array}\end{array}$$",
    r"$$\begin{array}{l}x\text{\tag{a}}\end{array}$$",
    r"$$\begin{array}{l}x\tag{a}\end{array}",
    r"`$$\begin{array}{l}x\tag{a}\end{array}$$`",
    r"\texttt{$$\begin{array}{l}x\tag{a}\end{array}$$}",
    "<pre>" + r"$$\begin{array}{l}x\tag{a}\end{array}$$" + "</pre>",
])
def test_latex_array_tag_adapter_leaves_uncertain_and_literal_source_unchanged(source):
    assert _normalize_display_array_tags(source) == source


def test_tagged_dollar_display_uses_amsmath_shell_and_keeps_math_and_tag_exactly():
    source = r"前文 $$ P(A)=P(X=n)+P(X=0) \tag* {6.9.6} $$ 后文"
    assert _normalize_display_array_tags(source) == (
        r"前文 \[ P(A)=P(X=n)+P(X=0) \tag* {6.9.6} \] 后文")
    untagged = r"$$x^2+y^2=1$$"
    assert _normalize_display_array_tags(untagged) == untagged


@pytest.mark.parametrize("is_answer", [False, True])
def test_latex_paragraph_assembly_preserves_complete_multiline_math_and_table_rows(is_answer):
    math = "$$\n" + r"\begin{array}{l}x=1\\y=2\end{array}" + "\n$$"
    source = "前文\n\n" + math + "\n\n| 步骤 | 内容 |\n| --- | --- |\n| (1) 说明 | 甲 |"
    result = clean_content_for_latex(source, is_answer=is_answer)
    assert math in result
    assert "\n\n" + r"\begin{array}" not in result
    assert "\\hline\n(1) 说明 & 甲" in result


def test_latex_choice_inference_does_not_reinterpret_markdown_table_or_display_math():
    table = "| A 说明 | B 说明 |\n| --- | --- |\n| C 说明 | D 说明 |"
    math = "$$\nA. B+C\n$$"
    source = table + "\n\n" + math
    result = clean_content_for_latex(source)
    assert r"\begin{choices}" not in result
    assert "A 说明 & B 说明" in result
    assert "C 说明 & D 说明" in result
    assert math in result
