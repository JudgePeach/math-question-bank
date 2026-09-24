"""Native-text quality gates, without network, models or personal PDF fixtures."""

from types import SimpleNamespace

import pytest

from mathbank import pdf_inspector_helper as helper


def table(rows, columns=13):
    def row(cells):
        return "|" + "|".join([*cells, *([""] * (columns - len(cells)))]) + "|"
    return "\n".join([row(["项目", "说明"]), row(["---"] * columns), *map(row, rows)])


def broken_exam_table():
    # A realistic failure shape: several questions drift across a wide sparse
    # table, and question 4 precedes question 3 in the same physical row.
    return table([
        ["", "1．将方程化为一般形式", "", "x²", "= 5x"],
        ["2．抛物线", "y = (x + 2)²"],
        ["（ A． C．", "）4．用配方法", "", "x² - 2x", "", "", "3．以下图标 B．D．"],
        ["A．", "", "", "", "", "", "5．某商店营业额"],
    ])


def test_scrambled_sparse_table_is_not_trusted_as_native_question_text():
    reasons = helper.native_text_quality_reasons(broken_exam_table())
    assert any("题号跨列" in reason for reason in reasons)


def test_two_question_paragraphs_spliced_into_wide_columns_are_rejected():
    long_header = "18．如图用一段长为三十二米的篱笆围成一个矩形菜园其中一面靠墙墙长十四米若矩"
    markdown = table([
        ["形菜园的面积为九十六米² 19．如图，在", "ABC", "中", "AB=AC", "，求垂直墙的边长", "若点", "E", "是", "BC", "边上任意一点"],
        ["旋转得到", "ADB", "点", "E", "的对应点为点", "D", "连接", "DE"],
    ], columns=10).replace("|项目|说明", "|" + long_header + "|说明", 1)
    assert helper.native_text_quality_reasons(markdown)


def test_subquestion_starts_piled_into_one_cell_and_suffixes_scattered():
    long_body = "22．学校购入一台羽毛球发球机球网飞行路线可以看作是抛物线的一部分如图建立平面直角坐标系"
    markdown = table([
        ["(2)在前图基础上在射线 (3)在另一图先画点 (4)在原图基础上将线段", "", "BE", "使点", "P", "BC", "上画一点", "F", "使得", "AF"],
        ["点", "与点", "对应", "距离", "O", long_body, "从", "A", "发出"],
    ])
    assert helper.native_text_quality_reasons(markdown)


def test_short_question_title_as_header_with_next_question_in_other_column():
    markdown = table([
        ["条．", "", "", "", "", "", "", "21．如图是由小正方形组成的网格每个小正方形的顶点叫做格点请完成全部画图任务", "ABC", "三个顶点"],
    ], columns=10)
    markdown = markdown.replace(markdown.splitlines()[0],
        "|20．抛物线||y=x²-4|x||+3的图像与||轴交于两点|y轴交于点|C|", 1)
    assert helper.native_text_quality_reasons(markdown)


def test_dense_false_table_with_fused_question_order_is_rejected():
    # Authored equivalent of Wuhan p2: fewer than 35% empty cells, Q10/Q9
    # reversed inside one row, each glued onto earlier option/math fragments.
    markdown = table([
        ["A．75° 7．如图，求角的度数", "b", "=9", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"],
        ["A．105° 8．已知点的坐标", "y₁", "y₂", "y₃", "x", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"],
        ["y A．12 10．抛物线的值不可能是", "m", "y₃ 9．国际数学教育大会会徽展现了古代数学文化请根据给定计数方法将八进制数换成十进制并说明理由", "n", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"],
    ], columns=16)
    assert any("题号跨列" in reason for reason in helper.native_text_quality_reasons(markdown))


def test_dense_comparison_table_can_contain_complete_questions_in_reverse_order():
    markdown = table([
        ["10．比较各组数据并根据样本计算结果解释本方案在不同年份之间发生变化的原因与可能影响", "9．分析方案乙的数据", "甲", "乙", "丙", "丁", "戊", "己"],
        ["8．说明其他方案", "7．计算频数", "A", "B", "C", "D", "E", "F"],
    ], columns=8)
    assert helper.native_text_quality_reasons(markdown) == []


@pytest.mark.parametrize("markdown", [
    "|组别|频数|频率|\n|---|---|---|\n|甲|12|0.4|\n|乙|18|0.6|",
    table([["甲", "", "", "3.5"], ["乙", "2.1"], ["丙", "", "", "", "1.2"]]),
    table([["3．题目丙"], ["2．题目乙"], ["1．题目甲"]]),
    table([["1．题目甲", "", "2．题目乙"], ["3．题目丙", "", "4．题目丁"]]),
    # A genuine wide comparison table may contain long explanations, short
    # labels and complete numbered questions, without fused paragraph starts.
    table([["1．已知某班学生参加项目的人数如下请根据统计数据分析各项目之间的关系并说明理由", "甲", "乙", "丙", "丁", "戊", "己", "庚"],
           ["2．分别比较项目", "(1)计算甲组", "(2)计算乙组", "(3)计算丙组", "a", "b", "c", "d"]]),
    "|内容|方案甲|方案乙|\n|---|---|---|\n|计算|(1)先算 (2)再算 (3)最后算|" + "说明长段落" * 4 + "|",
    "<table><tr><th rowspan='2'>题型</th><th colspan='2'>数量</th></tr>"
    "<tr><td>单选</td><td>多选</td></tr></table>",
    r"\begin{tabular}{|c|c|}\hline 分组 & 数据\\ 甲 & $\dfrac{1}{2}$\\\end{tabular}",
])
def test_real_simple_complex_and_sparse_tables_are_not_rejected(markdown):
    assert helper.native_text_quality_reasons(markdown) == []


@pytest.mark.parametrize("markdown", [
    "函数 *y*=-*x²*+ ô*x* ô 性质描述正确的是()",
    "集合 *A*= àáâ*x*-1<*x*<3 àáâ",
    "函数 *f* î*x* î= î*m*+2 î*x²*+1",
    "A. ö-2,2 ö B. ö-1,2 ö",
    "函数 y=-x²+ôxô",
    "如图，在 *Rt*V*ABC* 中，Ð*BAC*=90°，AB=AC。",
    "在 V*ABC* 和 V*ADE* 中，AD<AB，Ð*DAE*=Ð*BAC*=a。",
    "求ÐAED的度数。",
    "如图，VABC中，AB=AC。",
    "当 x 满足 -2 < x £ 4 时，y 的取值范围是。",
    "变量的范围满足 2 £ x < 4。",
    "函数定义域为 (1,\uef04 2)",
    "关系为 x \ufffd 1",
])
def test_mathematical_font_garbage_routes_to_ocr_without_guessing(markdown):
    assert helper.native_text_quality_reasons(markdown)


@pytest.mark.parametrize("markdown", [
    "数学试卷",
    r"函数 $y=-x^2+|x|$，其中 $x\in\mathbb{R}$。",
    "函数 y = x² + 2x，定义域为 [0,+∞)，且 x ≠ 1。",
    "Évariste Galois，côté = forêt，naïve 和 hôtel 是外文单词。",
    "重音单词 côte、forêt、même、où 不属于损坏的数学符号。",
    "<u>这里列出外文字母 ô、î 和 ö。</u>",
    "Ð、ð是外文字母。Đà Nẵng是地名。",
    "英国价格 £4，预算 £100；Price x £4。",
    "本题商品价格 £20，数量 x=3，计算总价。",
    "在 VABC 公司中研究数据；VABC是普通缩写。",
    "变量 VABC = 3，V = 2，三角形 ABC 的面积为 4。",
])
def test_normal_math_short_titles_and_foreign_accents_remain_native(markdown):
    assert helper.native_text_quality_reasons(markdown) == []


def test_per_page_gate_preserves_raw_evidence_and_only_routes_bad_pages(monkeypatch, capsys):
    corrupt = broken_exam_table()
    normal = "数学试卷\n1. 已知函数 $f(x)=x^2$，求其最小值。"
    inspector = SimpleNamespace(extract_pages_markdown=lambda *a, **k: SimpleNamespace(pages=[
        SimpleNamespace(page=2, markdown=normal, needs_ocr=False),
        SimpleNamespace(page=4, markdown=corrupt, needs_ocr=False),
    ]))
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", inspector)
    result = helper.inspect_and_extract_pdf(b"test-stub", page_indices=[2, 4])
    assert result["pdf_type"] == "mixed"
    assert result["pages_needing_ocr"] == [4]
    assert result["pages"][1]["markdown"] == corrupt
    assert result["pages"][1]["quality_reasons"]
    assert result["pages"][0]["quality_reasons"] == []
    assert "MATHBANK_PDF_PAGE:3" in result["markdown"]
    assert "MATHBANK_PDF_PAGE:5" not in result["markdown"]
    assert capsys.readouterr().out == ""


def test_legacy_native_path_also_checks_symbol_quality(monkeypatch):
    markdown = "1. 函数 y=-x²+ôxô 的性质描述正确的是，以下请选择所有正确结论。"
    inspector = SimpleNamespace(process_pdf=lambda *a, **k: SimpleNamespace(
        pdf_type="text_based", markdown=markdown, has_encoding_issues=False,
        pages_needing_ocr=[], confidence=0.99,
    ))
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", inspector)
    result = helper.inspect_and_extract_pdf(b"test-stub", page_indices=[3])
    assert result["pages_needing_ocr"] == [3]
    assert result["pages"][0]["markdown"] == markdown
    assert result["pages"][0]["quality_reasons"]
    assert result["markdown"] is None


def test_pymupdf_fallback_does_not_trust_long_private_glyph_text(monkeypatch):
    import pymupdf as fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 50), "A long ordinary line that would pass the old length threshold.")
    pdf = document.tobytes()
    document.close()
    monkeypatch.setattr(fitz.Page, "get_text", lambda *a, **k: "A long source with a broken symbol \uf02d and enough characters.")
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", False)
    result = helper.inspect_and_extract_pdf(pdf)
    assert result["pages_needing_ocr"] == [0]
    assert result["pages"][0]["quality_reasons"]


def _position(kind, x, y, width, height, text=""):
    return SimpleNamespace(item_type=kind, x=x, y=y, width=width, height=height, text=text)


def _inline_formula_items(count=3, width=28, height=12, context="已知函数"):
    items = []
    for index in range(count):
        y = 80 + index * 40
        items.extend([
            _position("text", 30, y, 50, 10, context),
            _position("image", 84, y + 5 - height / 2, width, height),
            _position("text", 84 + width + 4, y, 70, 10, "，求其最小值。"),
        ])
    return items


@pytest.mark.parametrize(("width", "height"), [(28, 12), (7, 20), (60, 24)])
def test_multiple_inline_formula_images_need_context_and_matching_text_lines(width, height):
    assert helper._has_multiple_inline_formula_images(_inline_formula_items(width=width, height=height))
    assert not helper._has_multiple_inline_formula_images(_inline_formula_items(count=2, width=width, height=height))


@pytest.mark.parametrize("case", ["large", "tiny", "decoration", "no_context", "unpaired", "distant", "different_line", "missing_bounds", "duplicate", "overlaid"])
def test_ordinary_images_or_incomplete_position_evidence_do_not_imply_formula_loss(case):
    items = _inline_formula_items()
    if case == "large":
        items = _inline_formula_items(width=110, height=110)
    elif case == "tiny":
        items = _inline_formula_items(width=0.96, height=0.96)
    elif case == "decoration":
        items = _inline_formula_items(context="函数图例的色块")
    elif case == "no_context":
        items = _inline_formula_items(context="看到这个")
    elif case == "unpaired":
        items = [item for item in items if item.item_type != "text" or item.x < 80]
    elif case == "distant":
        for item in items:
            if item.item_type == "image":
                item.x += 100
    elif case == "different_line":
        for item in items:
            if item.item_type == "text" and item.x > 80:
                item.y += 8
    elif case == "missing_bounds":
        items = [SimpleNamespace(item_type="image") for _ in range(8)]
    elif case == "duplicate":
        items = _inline_formula_items(count=1) * 3
    else:
        items = _inline_formula_items(count=1)
        items.extend(_position("image", 84 + offset, 79, 28, 12) for offset in (0.2, 0.4))
    assert not helper._has_multiple_inline_formula_images(items)


def test_formula_image_position_api_uses_one_based_original_page(monkeypatch):
    calls = []
    def extract(_path, pages):
        calls.append(pages)
        return _inline_formula_items()
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", SimpleNamespace(extract_text_with_positions=extract))
    assert helper._has_math_formula_loss("1. 已知函数，求其最小值。", page_index=1, pdf_path="local.pdf")
    assert calls == [[2]]


def test_three_normal_figures_with_no_dollar_marks_remain_native(monkeypatch):
    items = [_position("image", 20 + index * 120, 200, 110, 110) for index in range(3)]
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", SimpleNamespace(extract_text_with_positions=lambda *a, **k: items))
    assert not helper._has_math_formula_loss("图中给出了三幅几何图形，请比较各图的面积。", 0, "local.pdf")
