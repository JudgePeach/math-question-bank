"""Layout-only empty fields remain source data, never mathematical evidence."""
import pytest

from mathbank.content_locks import _formulas, lock_visible_math
from mathbank.source_metadata import _atomic_ranges, _metadata_extra, inspect_source_structure


CHOICES = '\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}'


@pytest.mark.parametrize('blank', [r'$(\quad)$', r'$(  )$', r'$\left(\quad\right)$',
                                  r'\((\quad)\)', r'\[(\quad)\]', r'($\quad$)'])
def test_complete_choice_blank_is_structural_without_adding_formula_lock(blank):
    source = '1. 独立中文选择题 ' + blank + CHOICES
    _, reasons = _atomic_ranges(source)
    assert 'unmatched_math_delimiter' not in reasons
    assert _formulas(source) == []
    locked, locks = lock_visible_math(source, 'empty-layout')
    assert not locks and locked == source
    plan = inspect_source_structure(source)
    assert plan['eligible'], plan['fallback_reasons']
    assert plan['source'] == source


@pytest.mark.parametrize('fragment', [r'$(\quad)', r'(\quad)$', r'\((\quad)', r'$\quad)$',
                                     r'$(\unknown)$', r'$(\quad $', r'$(\quad`x`)$',
                                     '售价$5 ' + r'$(\quad)$'])
def test_incomplete_or_unknown_shell_does_not_receive_empty_blank_exemption(fragment):
    source = '1. 独立题 ' + fragment + CHOICES
    ranges, reasons = _atomic_ranges(source)
    # Arbitrary complete unknown expressions are ordinary formula locks;
    # they are never structural empty blanks. Broken shells remain rejected.
    if fragment in {r'$(\unknown)$', r'$\quad)$', r'$(\quad $', r'$(\quad`x`)$'}:
        assert len(_formulas(source)) == 1
        assert (source.index('$'), source.rindex('$') + 1) not in ranges
    else:
        assert 'unmatched_math_delimiter' in reasons


def test_blank_like_shell_containing_variable_keeps_real_math_lock():
    source = '1. 计算 ' + r'$(x+\quad)$' + CHOICES
    _, reasons = _atomic_ranges(source)
    _, locks = lock_visible_math(source, 'real-math')
    assert len(locks) == 1 and locks[0].original == r'$(x+\quad)$'
    assert 'unmatched_math_delimiter' not in reasons


@pytest.mark.parametrize('literal', [r'`$(\quad)$`', r'\verb|$(\quad)$|', r'\path{$(\quad)$}'])
def test_code_examples_are_not_structural_blank_proof(literal):
    source = '1. 阅读代码 ' + literal + CHOICES
    ranges, _ = _atomic_ranges(source)
    assert not any(source[a:b] == r'$(\quad)$' for a, b in ranges)


@pytest.mark.parametrize('line', ['姓名：___________ 班级：___________', '姓名:___班级:___'])
def test_two_empty_identity_fields_are_only_preamble_metadata(line):
    source = line + '\n1. 独立问题。'
    plan = inspect_source_structure(source)
    assert plan['eligible'], plan['fallback_reasons']
    assert plan['source'] == source and line in plan['document_metadata'][0]['text']
    assert _metadata_extra(line, 0, len(line), before_questions=False) == (False, 'unowned_source_text')


@pytest.mark.parametrize('line', ['姓名：张三 班级：___', '姓名：___ 班级：一班',
    r'姓名：$x$ 班级：___', '姓名：___ 班级：___ 设所有变量均为正数', '姓名：___',
    r'姓名：\fillin 班级：___', '班级：___ 姓名：___', '`姓名：___ 班级：___`'])
def test_identity_template_never_absorbs_names_variables_or_extra_conditions(line):
    source = line + '\n1. 独立问题。'
    plan = inspect_source_structure(source)
    assert not plan['eligible'] and plan['source'] == source
