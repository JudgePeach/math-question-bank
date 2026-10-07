"""Narrow declarations/explicit values do not rewrite mathematical sources."""

import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import (
    _atomic_ranges, _visible_heading_census, inspect_source_structure,
    prepare_word_source_metadata,
)


@pytest.mark.parametrize('line', [
    r'考试时间：90 分钟\quad 分值：120 分',
    r'考试时间：90分钟，分值：120分',
    r'考试时长90分钟 总分120分。',
    r'考试用时：90分钟;满分：120分',
])
def test_complete_time_score_pair_is_context_and_source_is_untouched(line):
    source = line + '\n1. 独立中文问题，请解释理由。'
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan['eligible'], plan['fallback_reasons']
    assert plan['source'] == source and plan['document_metadata'][0]['text'].strip() == line


@pytest.mark.parametrize('line', [
    r'考试时间：90分钟\quad 分值：120分，设所有变量均为正数',
    r'考试时间：$x$分钟\quad 分值：120分',
    r'考试时间：$1+1$分钟\quad 分值：120分',
    r'考试时间：90分钟\quad{条件}分值：120分',
    r'考试时间：90分钟\\quad 分值：120分',
    r'考试时间：90分钟\qquad 分值：120分',
    r'`考试时间：90分钟\quad 分值：120分`',
    r'考试时间：90分钟 分值：120分 未知备注',
])
def test_duration_pair_cannot_absorb_formula_literal_macro_or_extra_conditions(line):
    source = line + '\n1. 独立中文问题。'
    plan = inspect_source_structure(source)
    assert not plan['eligible'] and plan['source'] == source


@pytest.mark.parametrize('header', [
    '一、单项选择题：本题共8小题，每小题4分，共32分、在每小题给出的四个选项中，只有一项是符合题目要求.',
    '一、选择题：在每小题给出的选项中，只有一项符合题目要求。',
    '四、解答题：本小题共4小题，共58分，解答应写出文字说明、证明过程或演算步骤。',
])
def test_full_score_shell_aliases_are_context_without_deleting_words(header):
    source = header + '\n1. 独立中文问题。'
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan['eligible'], plan['fallback_reasons']
    assert plan['source'] == source and header in plan['document_metadata'][0]['text']


@pytest.mark.parametrize('header', [
    '四、解答题：本小题共4小题，共58分，且以下各题均设$x>0$。',
    '一、选择题：在每小题给出的选项中，只有一项符合题目要求，所有题共用下图。',
    '四、解答题：本小题共4小题，共58分，未知计分条件。',
])
def test_same_score_shell_with_shared_or_unknown_source_data_still_fails(header):
    source = header + '\n1. 独立中文问题。'
    plan = inspect_source_structure(source)
    assert not plan['eligible'] and 'unowned_section_statement' in plan['fallback_reasons']
    assert plan['source'] == source


def census(source):
    atomic, _ = _atomic_ranges(source)
    return _visible_heading_census(source, atomic)


@pytest.mark.parametrize('line', ['故答案为：0.', '故答案为19.', '故答案是：-19.', '故答案：2.5．'])
def test_only_full_numeric_answer_summary_is_not_a_question_head(line):
    source = '1. 独立问题。\n【解析】推导过程。\n' + line + '\n2. 下一题。'
    assert census(source) == 2
    assert inspect_source_structure(source)['source'] == source


@pytest.mark.parametrize('line', [r'解得 -2 < m \le -1 或 m \ge 2.', r'x\leqslant -3.',
                                  r'x\geq 2.5.', r'x\neq 19.', r'x\lt 9．'])
def test_known_bare_relation_immediately_before_numeric_line_end_is_a_value(line):
    source = '1. 独立问题。\n' + line + '\n2. 下一题。'
    assert census(source) == 2
    assert inspect_source_structure(source)['source'] == source


@pytest.mark.parametrize('line', [
    r'x\ge 未知条件2.', r'x\ge' + '\n2.', r'x\\ge 2.', r'x\generate 2.',
    r'x\ge{未知}2.', r'x\ge 2. 3. 新题必须保留。', r'故答案为：2. 3. 新题必须保留。',
    r'故答案为：2. 未知解释', r'x\ge 2. 后续未知文字',
])
def test_relation_or_summary_cannot_cross_unknown_text_newline_or_real_heading(line):
    source = '1. 独立问题。\n' + line + '\n4. 另一真实题。'
    assert census(source) > 2


@pytest.mark.parametrize('line', [r'`x\ge 2.`', r'$x\ge 2.$',
                                  r'$\text{故答案为：19.}$'])
def test_preexisting_code_math_and_literal_protection_remains(line):
    source = '1. 独立问题。\n' + line + '\n2. 下一题。'
    assert census(source) == 2


def test_real_heading_on_next_line_and_heading_before_relation_are_still_counted():
    source = '1. 独立问题。\n' + r'解得x\ge 2.' + '\n' + r'题 2. 比较y\le 3.' + '\n题 3. 第三个完整题干。'
    assert census(source) == 3


@pytest.mark.parametrize('literal', [r'\verb|x\ge 2.|', r'\verb|x\ge 2.', r'\path{x\ge 2.}'])
def test_new_value_exemptions_do_not_grant_old_unprotected_tex_literals_extra_eligibility(literal):
    source = '1. 独立问题。\n' + literal + '\n2. 下一题。'
    # The preexisting census conservatively counts this unprotected numeric
    # token. This patch must not authorize it via an apparent relation macro.
    assert census(source) == 3
