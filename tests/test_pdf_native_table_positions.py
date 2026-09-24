"""Reject proved table scrambling while preserving real authored tables."""

from types import SimpleNamespace

import pytest

from mathbank import pdf_inspector_helper as helper


def markdown_table(rows):
    width = max(map(len, rows))
    def emit(values):
        return "|" + "|".join([*values, *([""] * (width-len(values)))]) + "|"
    return "\n".join([emit(rows[0]), emit(["---"] * width), *(emit(row) for row in rows[1:])])


def physical_page(entries, tables=()):
    calls = []
    def finder(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(tables=[SimpleNamespace(bbox=box) for box in tables])
    return SimpleNamespace(rect=SimpleNamespace(width=600), rotation=0, calls=calls,
        find_tables=finder,
        get_text=lambda kind: {"blocks": [{"type": 0, "lines": [{"bbox": (x,y,x+min(480,len(text)*6),y+10),
            "spans": [{"text": text}]}]} for text,x,y in entries]})


def test_five_column_table_cannot_move_question_13_after_question_16():
    page = physical_page([(f"{n}. 已知条件并求结果。",50,50+(n-10)*30) for n in range(10,17)])
    markdown = markdown_table([
        ["10. 分解因式 11. 方程 12. 已知 14. 如图", "x", "y", "A", "B"],
        ["15. 如图", "", "AB", "C", "D"], ["16. 节目彩排", "A", "B", "C", "D"],
    ]) + "\n\n13. 某厂加工工件，求数量。"
    reasons = helper._native_table_position_reasons(markdown, page)
    assert reasons and "题号顺序" in reasons[0]
    assert page.calls == [{"use_layout": False}]


def test_header_question_reversal_is_checked_against_physical_order():
    page = physical_page([(f"{n}. 已知函数并求结果。",50,50+(n-26)*40) for n in [26,27,28]])
    markdown = markdown_table([["27. 已知角度", "26. 在平面坐标系", "函数"], ["AC", "28. 在平面坐标系", "半径"]])
    assert helper._native_table_position_reasons(markdown,page)


def test_two_real_tables_do_not_license_moving_previous_question_text_after_next_heading():
    phrase = "丙三位选手中的排序居中"
    page = physical_page([("评委打分说明",50,40),(phrase,50,220),("24. 如图，求证。",50,300)],
                         tables=[(45,60,230,110),(45,140,250,190)])
    markdown = markdown_table([["平均数", "中位数", "众数"], ["教师评委", "91", "93"],
                              ["甲", "93", "92"], ["24. 如图", "（1）求证", phrase]])
    reasons = helper._native_table_position_reasons(markdown,page)
    assert reasons and "24" in reasons[0] and "题号边界" in reasons[0]


def test_real_question_table_with_reverse_numbers_remains_native():
    page = physical_page([("10. 比较两个方案",50,100),("9. 比较三个方案",300,100),
                          ("8. 说明方案",50,130),("7. 求统计结果",300,130)],
                         tables=[(40,80,560,160)])
    markdown = markdown_table([["方案甲", "方案乙"], ["10. 比较两个方案", "9. 比较三个方案"],
                              ["8. 说明方案", "7. 求统计结果"]])
    assert helper._native_table_position_reasons(markdown,page) == []
    assert helper.native_text_quality_reasons(markdown) == []


def test_real_single_column_question_table_order_need_not_be_ascending():
    page = physical_page([("3. 已知数据比较甲乙方案",50,100),("2. 已知数据进行具体运算",50,130),
                          ("1. 已知条件进行演算说明",50,160)])
    markdown = markdown_table([["题目", "类别"], ["3. 已知数据比较甲乙方案", "甲"],
                              ["2. 已知数据进行具体运算", "乙"], ["1. 已知条件进行演算说明", "丙"]])
    assert helper._native_table_position_reasons(markdown,page) == []


def test_borderless_parallel_question_cells_are_not_guessed_single_column():
    page = physical_page([("10. 已知条件甲",50,100),("9. 已知条件乙",350,100),
                          ("8. 已知条件丙",50,130),("7. 已知条件丁",350,130)])
    markdown = markdown_table([["甲列", "乙列"], ["10. 已知条件甲 8. 已知条件丙", "9. 已知条件乙 7. 已知条件丁"]])
    assert helper._native_table_position_reasons(markdown,page) == []


def test_unambiguous_table_data_and_a_following_question_stay_native():
    page = physical_page([("平均数中位数众数说明",50,40),("若丙在三位选手中排序居中",50,220),("24. 如图，求证。",50,300)],
                         tables=[(45,60,230,110),(45,140,250,190)])
    markdown = markdown_table([["平均数", "中位数", "众数"],["教师评委", "91", "93"]])
    markdown += "\n\n"+markdown_table([["选手", "评委甲", "评委乙"],["甲", "93", "92"]])
    markdown += "\n\n若丙在三位选手中排序居中，求值。\n24. 如图，求证。"
    assert helper._native_table_position_reasons(markdown,page) == []


@pytest.mark.parametrize("value", [
    markdown_table([["编号", "说明", "项目"],["A. A.", "图案", "选项"]]),
    markdown_table([["编号", "说明", "项目"],["C. C. 4", "性质", "统计"]]),
])
def test_repeated_empty_option_labels_in_one_cell_expose_folded_rows(value):
    assert helper._has_scrambled_sparse_table(value)


def test_repeated_labels_with_real_content_are_not_empty_option_folding():
    value = markdown_table([["试题", "说明", "项目"], ["A. 第一题内容完整；A. 第二题内容完整", "甲", "乙"]])
    assert helper.native_text_quality_reasons(value) == []


@pytest.mark.parametrize("value", [
    markdown_table([["2. 如图，若 Ð", "AOC", "=58°，求值。"],["角度", "58", "度"]]),
    "若 ÐD=35°，则角C的度数为。",
    markdown_table([["24. 如图，OD平分 Ð", "AOC", "，求证。"],["线段", "OD", "长度"]]),
    "工件质量满足49.98££x50.02时，记为合格。",
])
def test_table_boundaries_do_not_hide_concrete_angle_or_relation_corruption(value):
    assert any("字体错解" in reason for reason in helper.native_text_quality_reasons(value))


@pytest.mark.parametrize("value", ["拉丁字母序列 Ð A, B, C。", "££只是这里讨论的货币符号。",
                                   "普通外文字符串 naïve、côté、o\u0338。"])
def test_foreign_letters_and_currency_examples_do_not_become_geometry_errors(value):
    assert helper.native_text_quality_reasons(value) == []


def test_position_probe_is_page_scoped_and_missing_geometry_is_not_a_failure(monkeypatch):
    markdown = markdown_table([["数据", "数量"],["甲", "1"]])
    monkeypatch.setattr(helper,"_FITZ_AVAILABLE",False)
    assert helper._native_position_quality_reasons(markdown,2,"unused.pdf") == []
