"""Exact original-render page ranges for separate Word answer sections."""

from copy import deepcopy

import pytest

from mathbank import docx_source_verify as verify


STEM = '1. 已知抛物线的焦点为 $F(1,0)$，另求直线与抛物线的交点坐标。'
ANSWER = '1. 根据抛物线的定义计算可得 $p=2$，因此所有交点坐标满足 $x=y^2$。'


def long_document():
    texts = [
        '1. 已知抛物线的焦点为 F(1,0)，',
        '另求直线与抛物线的交点坐标。',
        '2. 后续题目的完整题干内容。',
        '3. 后续试题的条件以及问题。',
        '参考答案',
        '1. 根据抛物线的定义计算可得 p=2，',
        '因此所有交点坐标满足 x=y²。',
        '2. 下面给出后续题目的解答。',
    ]
    return [{'page_number': index + 1, 'text': text} for index, text in enumerate(texts)]


def candidate(stem=STEM, answer=ANSWER, number=1):
    return {'source_number': number, 'inline_answer': False,
            'original': {'content': stem, 'answer_markdown': answer}}


def test_independent_full_stem_and_answer_ranges_avoid_unrelated_middle_pages():
    pages, item = long_document(), candidate()
    before = deepcopy((pages, item))
    assert verify._locate_pages(item, pages) == [1, 2, 6, 7]
    assert (pages, item) == before


def test_rendered_line_wrapping_and_spacing_do_not_require_approximate_matching():
    pages = long_document()
    pages[0]['text'] = '1. 已知抛物线的\n焦点为 F(1,0)，'
    pages[5]['text'] = '1. 根 据 抛 物 线 的 定 义 计 算 可 得 p=2，'
    assert verify._locate_pages(candidate(), pages) == [1, 2, 6, 7]


@pytest.mark.parametrize('field', ['content', 'answer_markdown'])
def test_one_changed_prose_character_does_not_get_a_fuzzy_page_match(field):
    item = candidate()
    item['original'][field] = item['original'][field].replace('抛物线', '椭圆线')
    assert verify._locate_pages(item, long_document()) == []


def test_repeated_question_number_requires_unique_field_anchors():
    pages = long_document()
    pages[5]['text'] = '1. 已知抛物线的焦点为 F(1,0)，另求直线与抛物线的交点坐标。'
    pages[6]['text'] = '根据抛物线的定义计算可得 p=2，因此所有交点坐标满足 x=y²。'
    # Both numbered 1 segments now contain the entire stem; no nearest-page,
    # first-occurrence, or answer-position heuristic may resolve it.
    assert verify._locate_pages(candidate(), pages) == []


def test_same_segment_cannot_stand_in_for_two_independent_source_fields():
    pages = long_document()
    pages[0]['text'] += '根据抛物线的定义计算可得 p=2，'
    pages[1]['text'] += '因此所有交点坐标满足 x=y²。'
    pages[5]['text'] = '1. 此处并没有对应题目的原版答案。'
    pages[6]['text'] = '此处仍然是其他无关文字。'
    assert verify._locate_pages(candidate(), pages) == []


@pytest.mark.parametrize('field,value', [
    ('content', '1. 求解 $x^2=1$。'),
    ('answer_markdown', '1. A'),
    ('answer_markdown', r'1. $\frac{x+1}{2}$'),
    ('answer_markdown', '1. ![根据抛物线的定义计算可得](/static/uploads/tmp/formula.png)'),
    ('answer_markdown', '1. `[根据抛物线的定义计算可得]`'),
])
def test_short_formula_only_image_only_or_literal_only_fields_are_not_guessed(field, value):
    item = candidate()
    item['original'][field] = value
    assert verify._locate_pages(item, long_document()) == []


def test_diagnostic_labels_and_math_text_are_not_page_identity_anchors():
    assert verify._locator_text_anchors('[公式结构待核对] [特殊字符待核对] [公式待核对]') == []
    assert verify._locator_text_anchors(r'$\text{根据抛物线的定义计算可得}$') == []


def test_full_cross_page_answer_is_not_trimmed_to_fit_four_page_cap():
    pages = long_document()
    pages.insert(6, {'page_number': 7, 'text': '中间推导仍属于本题的完整解答。'})
    for number, page in enumerate(pages, 1):
        page['page_number'] = number
    assert verify._locate_pages(candidate(), pages) == []


def test_intervening_blank_page_is_still_part_of_complete_field_range():
    pages = long_document()
    pages[1]['text'] = ''
    pages[2]['text'] = '另求直线与抛物线的交点坐标。\n2. 后续题目的完整题干内容。'
    # The text did not prove that the intermediate page was irrelevant.
    # Including pages 1, 2, 3 and 6, 7 exceeds the cap rather than truncating.
    assert verify._locate_pages(candidate(), pages) == []


def test_image_bearing_field_keeps_next_heading_page_for_floating_picture():
    pages = long_document()
    pages[0]['text'] = '1. 已知抛物线的焦点为 F(1,0)，另求直线与抛物线的交点坐标。'
    pages[1]['text'] = '2. 后续题目的完整题干内容。'
    pages[2]['text'] = '3. 后续试题的条件以及问题。'
    pages[3]['text'] = '4. 最后一题的完整题干内容。'
    pages[5]['text'] += '因此所有交点坐标满足 x=y²。\n2. 下面给出后续题目的解答。'
    pages[6]['text'] = '3. 本页只包含后续题目的答案。'
    pages[7]['text'] = '4. 本页只包含最后题目的答案。'
    item = candidate(stem=STEM + ' ![图](/static/uploads/tmp/picture.png)')
    assert verify._locate_pages(item, pages) == [1, 2, 6]


def test_answer_section_heading_is_a_safe_boundary_for_last_stem():
    pages = [
        {'page_number': 1, 'text': '1. 已知抛物线的焦点为 F(1,0)，另求直线与抛物线的交点坐标。'},
        {'page_number': 2, 'text': '参考答案'},
        {'page_number': 3, 'text': '1. 根据抛物线的定义计算可得 p=2，因此所有交点坐标满足 x=y²。'},
        {'page_number': 4, 'text': '2. 下面给出后续题目的解答。'},
        {'page_number': 5, 'text': '后续题目的继续推导。'},
    ]
    assert verify._locate_pages(candidate(), pages) == [1, 3]


def test_numbering_reset_without_explicit_answer_boundary_is_not_guessed():
    pages = [
        {'page_number': 1, 'text': '1. 已知抛物线的焦点为 F(1,0)，另求直线与抛物线的交点坐标。'},
        {'page_number': 2, 'text': '其他文字'},
        {'page_number': 3, 'text': '1. 根据抛物线的定义计算可得 p=2，因此所有交点坐标满足 x=y²。'},
        {'page_number': 4, 'text': '2. 后续试题的条件以及问题。'},
        {'page_number': 5, 'text': '后续题目的继续推导。'},
    ]
    assert verify._locate_pages(candidate(), pages) == []


def test_unsuccessful_next_numbering_does_not_skip_over_intervening_question():
    pages = long_document()
    pages[2]['text'] = '4. 错序或多栏提取的未知题干。'
    assert verify._locate_pages(candidate(), pages) == []


def test_last_answer_range_includes_all_remaining_pages():
    pages = long_document()
    pages[7]['text'] = '此页仍可能有本题的最后推导和插图。'
    assert verify._locate_pages(candidate(), pages) == []


@pytest.mark.parametrize('change', ['missing_page', 'duplicate_page', 'out_of_order', 'nontext', 'bool_page'])
def test_incomplete_or_unordered_page_index_cannot_prove_separate_ranges(change):
    pages = long_document()
    if change == 'missing_page':
        pages.pop(3)
    elif change == 'duplicate_page':
        pages[3]['page_number'] = 3
    elif change == 'out_of_order':
        pages[0], pages[1] = pages[1], pages[0]
    elif change == 'nontext':
        pages[2]['text'] = None
    else:
        pages[0]['page_number'] = True
    assert verify._locate_pages(candidate(), pages) == []


def test_small_document_retains_complete_document_fallback_without_guessing():
    pages = [{'page_number': index, 'text': ''} for index in range(1, 5)]
    assert verify._locate_pages(candidate(stem='1. $x$', answer='1. A'), pages) == [1, 2, 3, 4]


def test_existing_inline_answer_heading_flow_is_unchanged():
    pages = long_document()
    pages[5]['text'] = '4. 本页的内容与目标试题无关。'
    pages[7]['text'] = '5. 这是后续试题的独立题干。'
    item = candidate()
    item['inline_answer'] = True
    assert verify._locate_pages(item, pages) == [1, 2]
