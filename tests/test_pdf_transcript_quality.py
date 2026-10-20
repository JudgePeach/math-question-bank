"""Local transcript quality and captured-OCR regressions, never real requests."""
import json
from pathlib import Path

import pytest

from mathbank.pdf_transcript_quality import assess_pdf_transcript_quality, transcript_quality_reasons


def choices(*values, close=True):
    return '\\begin{choices}\n'+'\n'.join('\\item '+value for value in values)+('\n\\end{choices}' if close else '')


def test_six_open_choices_and_empty_middle_options_are_definite_damage():
    value='\n'.join(f'{i}. 题干\n'+choices('','','','',close=False) for i in range(1,7))
    result=assess_pdf_transcript_quality(value)
    assert result['retryable_failure']
    assert 'multiple_unclosed_choices' in result['failure_codes']
    assert 'empty_choice_option' in result['failure_codes']
    assert result['choice_count']==6 and result['source_verified'] is False
    assert all(0<=r['start']<r['end']<=len(value) for r in result['blocked_ranges'])


def test_wrong_outer_math_does_not_hide_real_broken_choices():
    value='\n'.join('$$'+choices('','','','',close=False)+'$$' for _ in range(6))
    result=assess_pdf_transcript_quality(value)
    assert result['retryable_failure'] and result['choice_count']==6
    assert 'multiple_unclosed_choices' in result['failure_codes']


@pytest.mark.parametrize('empty',['','   ','$ $',r'\quad {}',r'\text{}'])
def test_explicit_empty_option_of_closed_choices_is_not_accepted(empty):
    assert 'empty_choice_option' in transcript_quality_reasons(choices('0',empty,'2','3'))


@pytest.mark.parametrize('value',['0','$0$',r'$\frac{1}{2}$',r'\fillin','[插图待补: 图1]',r'\unknownmacro'])
def test_real_formula_zero_picture_slot_and_unknown_tex_are_not_empty(value):
    assert transcript_quality_reasons(choices(value,'1','2','3'))==[]


def test_normal_single_page_tail_and_previous_page_continuations_only_warn():
    result=assess_pdf_transcript_quality('1. 原题\n'+choices('甲','乙',close=False))
    assert not result['retryable_failure']
    assert result['warnings']==['choices_may_continue_on_next_page']
    result=assess_pdf_transcript_quality('续接前页\n\\item 丙\n\\item 丁\n\\end{choices}')
    assert not result['retryable_failure'] and result['warnings']==['choices_may_start_on_previous_page']


def test_single_unclosed_choices_followed_by_another_question_is_not_normal_continuation():
    assert 'unclosed_choices_before_next_question' in transcript_quality_reasons('1. 题干\n'+choices('甲','乙',close=False)+'\n2. 新题')


def test_code_math_and_actual_nested_tables_are_not_choices_protocol_damage():
    examples=[
        '```tex\n'+choices('','','','',close=False)+'\n```',
        '`\\begin{choices}\\item \\item `',
        '$\\text{\\begin{choices}\\item \\item}$',
        '\\begin{tabular}{c}\n\\begin{tabular}{c}\n甲 \\\\\n乙\\end{tabular}\\end{tabular}',
        '\\begin{tabular}{c}\n\\begin{choices}\\item \\item \\end{choices}\\end{tabular}',
        'A. 0 B. 1 C. 2 D. 3',
    ]
    assert all(transcript_quality_reasons(value)==[] for value in examples)


def test_table_inside_real_option_does_not_close_or_erase_the_outer_choices():
    value=choices('\\begin{tabular}{c}\\begin{choices}\\item \\end{choices}\\end{tabular}','1','2','3')
    result=assess_pdf_transcript_quality(value)
    assert not result['retryable_failure'] and result['choice_count']==1


def test_old_nine_page_transcripts_have_no_new_automatic_retry_reason():
    root=Path(__file__).resolve().parents[1]/'.system_generated/pdf-classification-20261018/responses'
    if not (root/'03/response.json').is_file():
        pytest.skip('Private captured OCR evidence is not distributed')
    for call in [3,4,2,1,10,6,7,8,9]:
        outer=json.loads((root/f'{call:02}/response.json').read_text())
        text=json.loads(outer['choices'][0]['message']['content'])['markdown']
        assert transcript_quality_reasons(text)==[],call


def test_captured_new_page_one_is_rejected_even_inside_display_math():
    root=Path(__file__).resolve().parents[1]
    path=root/'.system_generated/document_tasks/d1b3aadf-be85-4a04-bba7-416d910843d7.json'
    if not path.is_file():
        pytest.skip('Private failed-page evidence is not distributed')
    value=json.loads(path.read_text())['pdf_resume_state']['pages']['0']['joint']['markdown']
    result=assess_pdf_transcript_quality(value)
    assert result['choice_count']==6
    assert result['retryable_failure']
    assert 'multiple_unclosed_choices' in result['failure_codes']
