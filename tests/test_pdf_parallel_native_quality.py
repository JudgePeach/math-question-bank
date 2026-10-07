"""Content-backed native gates; authored fixtures, no personal PDF or API."""

from types import SimpleNamespace

import pytest

from mathbank import pdf_inspector_helper as helper


def _exam_table(*, options=("A． C．", "B． D．"), columns=15,
                instruction="在每小题给出的四个选项中，只选一项。",
                graph_stem="3．函数的图象大致是（ ）"):
    def row(cells):
        return "|" + "|".join([*cells, *([""] * (columns - len(cells)))]) + "|"

    return "\n".join([
        row([instruction, "单项选择题"]), row(["---"] * columns),
        row(["1．已知集合 A，求交集。", "A∩B"]),
        row(["2．设 x∈R，判断条件。", "x²>1"]),
        row([graph_stem]), row([options[0], "", "", *options[1:]]),
        row(["4．已知函数 f(x)，求值。", "f(x)=x²"]),
    ])


@pytest.mark.parametrize("markdown", [
    "1．已知集合 A={x Î Z，-6<x³<6}，求交集。",
    "2．设 x ÎR，则 x>1 的含义为。",
    "2．设 $x Î R$，求值。",
    "函数定义域为 x Î C，求参数。",
    "|1．已知集合|A={|x Î Z|-6<x³<6}|",
])
def test_set_declaration_with_symbol_font_membership_is_not_native_safe(markdown):
    assert any("集合归属符" in reason for reason in helper.native_text_quality_reasons(markdown))


def test_membership_reason_keeps_existing_other_glyph_evidence_and_is_not_duplicated():
    markdown = "设 x Î R，求值。\n已知集合 A={n Î Z}。\n如图，在 VABC 中，AB=AC。"
    reasons = helper.native_text_quality_reasons(markdown)
    assert sum("集合归属符" in reason for reason in reasons) == 1
    assert any("三角形" in reason for reason in reasons)


@pytest.mark.parametrize("markdown", [
    "Île-de-France、Île 和 ÎLES 是法文名称，Évariste Galois 是人名。",
    "数学课列出外文字母 Î、î、ö，已知 $x=1$。",
    "字符 x Î R 是字母排列表。",
    r"设 $x\in\mathbb{R}$，且 $x>1$。",
    "设 Î=2，R=3，x=ÎR。",
    "任意 Île 的面积为 x，集合 R 的元素有三个。",
    "集合 A 的说明参考 https://example.test/xÎR 。",
    "集合 A 的说明参考 [链接](https://example.test/xÎR)。",
    "设 `x Î R` 是待阅读的代码，求 $x=1$。",
    "集合 A 示例。\n```text\n设 x Î R，求值。\n```",
    "集合 A 示例。\n~~~text\n设 x Î R，求值。\n~~~",
    "<span title='集合 x Î R'>已知 $x=1$，求值。</span>",
    r"设 $\text{Î}=x$，$x\in\mathbb{R}$。",
    "|区域|数值|\n|---|---|\n|Île-de-France|3.2|\n|Île|1.2|",
])
def test_foreign_letters_real_memberships_and_literals_are_not_membership_damage(markdown):
    assert helper.native_text_quality_reasons(markdown) == []


@pytest.mark.parametrize("options", [
    ("A． C．", "B． D．"), ("A. C.", "B. D."),
    ("C、A、", "D、B、"), ("A．B．", "C．D．"),
])
def test_graph_choice_labels_folded_into_two_empty_cells_are_rejected(options):
    assert any("宽表" in reason for reason in helper.native_text_quality_reasons(_exam_table(options=options)))


@pytest.mark.parametrize("kwargs", [
    {"options": ("A．甲图 C．丙图", "B．乙图 D．丁图")},
    {"options": ("A． C．", "B． D．", "图片说明")},
    {"options": ("A． C．", "B． E．")},
    {"options": ("A．", "B．", "C．", "D．")},
    {"instruction": "各区域统计表，标签对 A．C． 与 B．D．。"},
    {"graph_stem": "3．请比较下表各项目的频数。"},
    {"columns": 7},
    {"graph_stem": "3．请阅读代码 `函数图象大致是` 的含义。"},
])
def test_width_empty_cells_or_label_pairs_alone_do_not_reject_tables(kwargs):
    assert helper.native_text_quality_reasons(_exam_table(**kwargs)) == []


def test_real_statistical_wide_table_can_have_empty_cells_and_foreign_labels():
    columns = ["项目", "频数", "频率", "平均数", "方差", "区间", "备注", "区域"]
    rows = [columns, ["---"] * 8,
            ["甲", "12", "0.4", "3.2", "0.5", "[1,4]", "", "Île"],
            ["乙", "18", "0.6", "2.1", "0.7", "[0,5]", "", "Île-de-France"]]
    markdown = "\n".join("|" + "|".join(row) + "|" for row in rows)
    assert helper.native_text_quality_reasons(markdown) == []


def test_option_table_requires_several_independent_question_numbers():
    markdown = _exam_table().replace("2．设", "设").replace("4．已知", "已知")
    assert helper.native_text_quality_reasons(markdown) == []


def test_fenced_bad_option_table_is_literal_content():
    assert helper.native_text_quality_reasons("阅读下列代码：\n```text\n" + _exam_table() + "\n```") == []


def test_per_page_routes_new_failure_preserving_every_source_character(monkeypatch):
    bad = _exam_table()
    good = r"1．已知 $x\in\mathbb{R}$，求 $x^2$ 的最小值。"
    inspector = SimpleNamespace(extract_pages_markdown=lambda *a, **k: SimpleNamespace(pages=[
        SimpleNamespace(page=0, markdown=bad, needs_ocr=False),
        SimpleNamespace(page=1, markdown=good, needs_ocr=False),
    ]))
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", inspector)
    result = helper.inspect_and_extract_pdf(b"authored-stub")
    assert result["pages_needing_ocr"] == [0]
    assert result["pages"][0]["markdown"] == bad
    assert result["pages"][0]["quality_reasons"]
    assert result["pages"][1]["markdown"] == good
    assert result["pages"][1]["needs_ocr"] is False
    assert bad not in (result["markdown"] or "")
