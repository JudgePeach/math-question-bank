"""Pure DOCX regressions for editable LaTeX list export, without the API or DB."""

import copy
import io
import zipfile

import pytest
from lxml import etree
from PIL import Image

from mathbank import word_export_helper
from mathbank.word_export_helper import build_word_document


NS = {
    "w": word_export_helper.WORD_NS,
    "m": word_export_helper.MATH_NS,
}
WVAL = "{" + NS["w"] + "}val"


def _export(content, *, answer="", include_answers=False, uploads_dir=None, images=()):
    questions = [{
        "question": {
            "id": 1,
            "question_type": "detailed_answer",
            "content": content,
            "answer_markdown": answer,
            "image_paths": list(images),
            "figure_align": "center",
        },
        "score": 12,
    }]
    original = copy.deepcopy(questions)
    data, diagnostics = build_word_document(
        "列表导出回归", "", "exam", questions,
        include_answers=include_answers, uploads_dir=uploads_dir,
    )
    assert questions == original, "Word export must not rewrite the input questions"
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        document = etree.fromstring(package.read("word/document.xml"))
        numbering = etree.fromstring(package.read("word/numbering.xml"))
    return document, numbering, diagnostics


def _text(element):
    return "".join(element.xpath(".//w:t/text()", namespaces=NS))


def _list_paragraphs(document):
    return document.xpath(".//w:p[w:pPr/w:numPr]", namespaces=NS)


def _numbering(paragraph, numbering):
    num_id = paragraph.xpath("./w:pPr/w:numPr/w:numId/@w:val", namespaces=NS)[0]
    level = int(paragraph.xpath("./w:pPr/w:numPr/w:ilvl/@w:val", namespaces=NS)[0])
    concrete = numbering.xpath("./w:num[@w:numId=$value]", value=num_id, namespaces=NS)[0]
    abstract_id = concrete.xpath("./w:abstractNumId/@w:val", namespaces=NS)[0]
    definition = numbering.xpath(
        "./w:abstractNum[@w:abstractNumId=$value]/w:lvl[@w:ilvl=$level]",
        value=abstract_id, level=str(level), namespaces=NS,
    )[0]
    override = concrete.xpath(
        "./w:lvlOverride[@w:ilvl=$level]/w:startOverride/@w:val",
        level=str(level), namespaces=NS,
    )
    start = int(override[0] if override else definition.xpath("./w:start/@w:val", namespaces=NS)[0])
    return {
        "id": num_id,
        "level": level,
        "format": definition.find("w:numFmt", NS).get(WVAL),
        "label": definition.find("w:lvlText", NS).get(WVAL),
        "start": start,
    }


def _assert_no_list_source(document):
    text = _text(document)
    for marker in (r"\begin{enumerate}", r"\end{enumerate}",
                   r"\begin{itemize}", r"\end{itemize}", r"\item"):
        assert marker not in text


@pytest.mark.parametrize("environment, expected_format", [("enumerate", "decimal"), ("itemize", "bullet")])
@pytest.mark.parametrize("separator", [" ", "\n"])
def test_complete_lists_on_one_or_multiple_lines_use_editable_word_numbering(
    environment, expected_format, separator,
):
    source = separator.join([
        "列表前正文。", rf"\begin{{{environment}}}",
        r"\item 第一小问。", r"\item 第二小问。",
        rf"\end{{{environment}}}", "列表后正文。",
    ])
    document, numbering, diagnostics = _export(source)
    paragraphs = _list_paragraphs(document)
    assert [_text(p).strip() for p in paragraphs] == ["第一小问。", "第二小问。"]
    properties = [_numbering(p, numbering) for p in paragraphs]
    assert {p["format"] for p in properties} == {expected_format}
    assert {p["level"] for p in properties} == {0}
    assert len({p["id"] for p in properties}) == 1
    assert {p["start"] for p in properties} == {1}
    visible = _text(document)
    assert visible.index("列表前正文。") < visible.index("第一小问。")
    assert visible.index("第二小问。") < visible.index("列表后正文。")
    _assert_no_list_source(document)
    assert diagnostics["failed_formulas"] == diagnostics["fallback_formulas"] == 0


def test_nested_mixed_lists_keep_list_order_and_actual_word_levels():
    document, numbering, _ = _export(
        r"\begin{enumerate}\item 外层甲。"
        r"\begin{itemize}\item 项目甲。"
        r"\begin{enumerate}\item 内层甲。\item 内层乙。\end{enumerate}"
        r"\item 项目乙。\end{itemize}"
        r"\item 外层乙。\end{enumerate}"
    )
    paragraphs = _list_paragraphs(document)
    assert [_text(p).strip() for p in paragraphs] == [
        "外层甲。", "项目甲。", "内层甲。", "内层乙。", "项目乙。", "外层乙。",
    ]
    properties = [_numbering(p, numbering) for p in paragraphs]
    assert [p["level"] for p in properties] == [0, 1, 2, 2, 1, 0]
    assert [p["format"] for p in properties] == [
        "decimal", "bullet", "lowerLetter", "lowerLetter", "bullet", "decimal",
    ]
    assert [p["label"] for p in properties] == ["%1.", "•", "(%3)", "(%3)", "•", "%1."]
    assert properties[0]["id"] == properties[-1]["id"]
    assert properties[1]["id"] == properties[-2]["id"]
    assert properties[2]["id"] == properties[3]["id"]
    _assert_no_list_source(document)


def test_enumeration_inside_itemize_starts_with_decimal_despite_deeper_indentation():
    document, numbering, _ = _export(
        r"\begin{itemize}\item 外层项目。"
        r"\begin{enumerate}\item 内层甲。\item 内层乙。\end{enumerate}"
        r"\end{itemize}"
    )
    paragraphs = _list_paragraphs(document)
    assert [_text(p).strip() for p in paragraphs] == ["外层项目。", "内层甲。", "内层乙。"]
    properties = [_numbering(p, numbering) for p in paragraphs]
    assert [p["level"] for p in properties] == [0, 1, 1]
    assert [p["format"] for p in properties] == ["bullet", "decimal", "decimal"]
    assert [p["label"] for p in properties] == ["•", "%2.", "%2."]
    assert properties[1]["id"] == properties[2]["id"]
    assert properties[1]["start"] == 1
    _assert_no_list_source(document)


def test_itemize_markers_follow_itemize_depth_independent_of_interleaved_enumerations():
    document, numbering, _ = _export(
        r"\begin{itemize}\item 项目零层。"
        r"\begin{enumerate}\item 枚举零层。"
        r"\begin{itemize}\item 项目一层。"
        r"\begin{enumerate}\item 枚举一层。"
        r"\begin{itemize}\item 项目二层。"
        r"\begin{itemize}\item 项目三层。\end{itemize}"
        r"\end{itemize}\end{enumerate}\end{itemize}\end{enumerate}\end{itemize}"
    )
    properties = [_numbering(p, numbering) for p in _list_paragraphs(document)]
    assert [p["level"] for p in properties] == [0, 1, 2, 3, 4, 5]
    assert [p["format"] for p in properties] == ["bullet", "decimal", "bullet", "lowerLetter", "bullet", "bullet"]
    assert [p["label"] for p in properties] == ["•", "%2.", "–", "(%4)", "∗", "·"]
    _assert_no_list_source(document)


def test_enumeration_labels_cycle_through_word_supported_nesting_levels():
    source = ""
    for level in range(9):
        source += rf"\begin{{enumerate}}\item 第{level}层。"
    source += r"\end{enumerate}" * 9
    document, numbering, _ = _export(source)
    properties = [_numbering(p, numbering) for p in _list_paragraphs(document)]
    assert [p["level"] for p in properties] == list(range(9))
    formats = ["decimal", "lowerLetter", "lowerRoman", "upperLetter"]
    assert [p["format"] for p in properties] == [formats[level % 4] for level in range(9)]
    assert [p["label"] for p in properties] == [
        "%1.", "(%2)", "%3.", "%4.", "%5.", "(%6)", "%7.", "%8.", "%9.",
    ]
    _assert_no_list_source(document)


def test_independent_enumerations_restart_at_one_with_separate_numbering_instances():
    document, numbering, _ = _export(
        r"\begin{enumerate}\item 首组甲。\item 首组乙。\end{enumerate}"
        "两组之间的正文。"
        r"\begin{enumerate}\item 次组甲。\item 次组乙。\end{enumerate}"
    )
    paragraphs = _list_paragraphs(document)
    assert [_text(p).strip() for p in paragraphs] == ["首组甲。", "首组乙。", "次组甲。", "次组乙。"]
    properties = [_numbering(p, numbering) for p in paragraphs]
    assert properties[0]["id"] == properties[1]["id"]
    assert properties[2]["id"] == properties[3]["id"]
    assert properties[0]["id"] != properties[2]["id"]
    assert [p["start"] for p in properties] == [1, 1, 1, 1]


def test_custom_item_labels_are_editable_text_and_do_not_consume_automatic_numbers():
    document, numbering, _ = _export(
        r"\begin{enumerate}\item 默认甲。\item[步骤甲] 指定标签。"
        r"\item[(提示)] 另一标签。\item 默认乙。\end{enumerate}"
    )
    numbered = _list_paragraphs(document)
    assert [_text(p).strip() for p in numbered] == ["默认甲。", "默认乙。"]
    properties = [_numbering(p, numbering) for p in numbered]
    assert properties[0]["id"] == properties[1]["id"]
    assert properties[0]["start"] == 1
    for label, body in [("步骤甲", "指定标签。"), ("(提示)", "另一标签。")]:
        candidates = [p for p in document.xpath(".//w:p", namespaces=NS) if body in _text(p)]
        assert len(candidates) == 1
        assert label in _text(candidates[0])
        assert not candidates[0].xpath("./w:pPr/w:numPr", namespaces=NS)
    _assert_no_list_source(document)


@pytest.mark.parametrize("table_kind", ["latex", "markdown"])
def test_lists_preserve_surrounding_text_and_inline_table_and_picture_anchors(tmp_path, table_kind):
    Image.new("RGB", (80, 40), "navy").save(tmp_path / "list-figure.png")
    table = (
        "\n" + r"\begin{tabular}{cc}表头甲 & 表头乙 \\ 单元甲 & 单元乙\end{tabular}" + "\n"
        if table_kind == "latex" else
        "\n\n| 表头甲 | 表头乙 |\n| --- | --- |\n| 单元甲 | 单元乙 |\n\n"
    )
    document, _, diagnostics = _export(
        "列表前说明。\n\n" + r"\begin{enumerate}\item 第一项表前。" + table +
        "第一项表后。\n\n" + r"\item 第二项图前。" +
        "![列表图片](/static/uploads/list-figure.png)第二项图后。" +
        r"\end{enumerate}" + "\n\n列表后说明。",
        uploads_dir=tmp_path, images=["/static/uploads/list-figure.png"],
    )
    assert [_text(p).strip() for p in _list_paragraphs(document)] == ["第一项表前。", "第二项图前。第二项图后。"]
    assert len(document.xpath(".//w:tbl", namespaces=NS)) == 1
    assert len(document.xpath(".//w:drawing", namespaces=NS)) == 1
    flow = []
    for element in document.xpath("./w:body/*", namespaces=NS):
        flow.append((element.tag.rsplit("}", 1)[-1], _text(element), bool(element.xpath(".//w:drawing", namespaces=NS))))
    index = lambda value: next(i for i, (_, text, _) in enumerate(flow) if value in text)
    assert index("列表前说明。") < index("第一项表前。") < index("表头甲")
    assert index("表头甲") < index("第一项表后。") < index("第二项图前。") < index("列表后说明。")
    assert next(i for i, (_, _, drawing) in enumerate(flow) if drawing) == index("第二项图前。")
    assert diagnostics["missing_images"] == 0
    _assert_no_list_source(document)


@pytest.mark.parametrize("include_answers", [False, True])
def test_stem_and_optional_answer_lists_share_the_conversion_path(include_answers):
    document, numbering, _ = _export(
        r"题干。\begin{enumerate}\item 题干甲。\item 题干乙。\end{enumerate}",
        answer=r"解析。\begin{itemize}\item 解析甲。\item 解析乙。\end{itemize}",
        include_answers=include_answers,
    )
    paragraphs = _list_paragraphs(document)
    assert [_text(p).strip() for p in paragraphs] == (
        ["题干甲。", "题干乙。", "解析甲。", "解析乙。"] if include_answers else ["题干甲。", "题干乙。"]
    )
    assert [_numbering(p, numbering)["format"] for p in paragraphs] == (
        ["decimal", "decimal", "bullet", "bullet"] if include_answers else ["decimal", "decimal"]
    )
    assert ("解析甲。" in _text(document)) == include_answers
    _assert_no_list_source(document)


def test_latex_list_keeps_markdown_parent_context_when_items_are_indented():
    document, _, diagnostics = _export(
        "- 外层：" + r"\begin{enumerate}" + "\n\n"
        "    " + r"\item 甲" + "\n"
        "    " + r"\item 乙" + "\n" + r"\end{enumerate}"
    )
    assert [_text(p).strip() for p in _list_paragraphs(document)] == ["甲", "乙"]
    assert "外层：" in _text(document)
    _assert_no_list_source(document)
    assert diagnostics["failed_formulas"] == diagnostics["fallback_formulas"] == 0


@pytest.mark.skipif(word_export_helper._find_pandoc() is None, reason="Usable Pandoc is not installed")
def test_bare_math_in_markdown_table_inside_list_is_preconverted_to_native_omml():
    document, _, diagnostics = _export(
        r"\begin{enumerate}\item 比较下表。" + "\n\n"
        "| 量 | 值 |\n| --- | --- |\n| x | x^2 |\n\n"
        r"\item 继续判断。\end{enumerate}"
    )
    assert [_text(p).strip() for p in _list_paragraphs(document)] == ["比较下表。", "继续判断。"]
    tables = document.xpath(".//w:tbl", namespaces=NS)
    assert len(tables) == 1
    value_cell = tables[0].xpath("./w:tr[2]/w:tc[2]", namespaces=NS)[0]
    assert value_cell.xpath(".//m:oMath/m:sSup", namespaces=NS)
    assert value_cell.xpath(".//m:sSup/m:e//m:t/text()", namespaces=NS) == ["x"]
    assert value_cell.xpath(".//m:sSup/m:sup//m:t/text()", namespaces=NS) == ["2"]
    assert diagnostics["native_formulas"] >= 1
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0
    assert not value_cell.xpath(".//w:drawing", namespaces=NS)
    _assert_no_list_source(document)


@pytest.mark.skipif(word_export_helper._find_pandoc() is None, reason="Usable Pandoc is not installed")
def test_reported_ellipse_question_exports_editable_lists_and_native_fraction_equations():
    source = (
        r"已知椭圆 $E:\dfrac{x^2}{a^2}+\dfrac{y^2}{b^2}=1$（$a>b>0$）"
        r"的一个顶点是 $(2,0)$，离心率为 $\dfrac{1}{2}$。" "\n\n"
        r"\begin{enumerate} \item 求 $E$ 的方程；"
        r"\item 过点 $A(1,1)$，斜率为 $k$（$k\ne\pm1$）的直线交椭圆 $E$"
        r"于 $B,C$ 两点，$B$ 关于 $y=x$ 的对称点为 $D$，$DC$ 交 $y=x$ 于 $Q$，"
        r"若 $|S_{\triangle ABQ}-S_{\triangle ACQ}|=\dfrac{5}{8}$，求 $k$。"
        r"\end{enumerate}"
    )
    document, _, diagnostics = _export(source, answer=source, include_answers=True)
    assert len(_list_paragraphs(document)) == 4
    _assert_no_list_source(document)
    assert document.xpath("count(.//m:oMath)", namespaces=NS) >= 20
    fractions = document.xpath(".//m:f", namespaces=NS)
    fraction_texts = [(
        "".join(f.xpath("./m:num//m:t/text()", namespaces=NS)),
        "".join(f.xpath("./m:den//m:t/text()", namespaces=NS)),
    ) for f in fractions]
    assert fraction_texts.count(("1", "2")) == 2
    assert fraction_texts.count(("5", "8")) == 2
    assert ("x2", "a2") in fraction_texts
    assert ("y2", "b2") in fraction_texts
    assert diagnostics["native_formulas"] >= 20
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0
    assert "公式待核对" not in _text(document)
