from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from mathbank.import_review import (
    make_source_review_advisory, prepare_word_extraction_reviews, finalize_word_extraction_reviews,
    WORD_FORMULA_EXTRACTION_REASON,
)


def _word_extraction_fixture():
    source = '1. 求 $x+1$ [公式结构待核对]。\n2. 求 $y+1$ [公式结构待核对]。'
    split = source.index('2.')
    questions = [{'content': source[:split]}, {'content': source[split:]}]
    report = {'review_required': 3, 'word_extraction_warnings': ['部分 Office 公式含暂未完整支持的结构', '无法定位 Word 图片资源：media/MathType公式.png'],
              'source_matches': [
                  {'question_index': index, 'field': 'content', 'source_start': start, 'source_end': end,
                   'source_excerpt': source[start:end]} for index, (start, end) in enumerate(((0, split), (split, len(source))))]}
    return source, questions, report


def _confirm_word_question(question):
    question['source_review'].update(required=False, verified_by='vision',
                                    verification={'decision': 'equivalent'})


def test_native_formula_notices_enter_visual_review_even_when_source_alignment_passed():
    source, questions, report = _word_extraction_fixture()
    prepare_word_extraction_reviews(questions, report, source)
    assert report['source_review_count'] == 2
    assert all(q['source_review']['reasons'] == [WORD_FORMULA_EXTRACTION_REASON] for q in questions)
    assert [item['question_index'] for item in report['word_formula_extraction_items']] == [0, 1]
    _confirm_word_question(questions[0])
    finalize_word_extraction_reviews(questions, report, source)
    assert report['word_extraction_review_count'] == 2
    assert report['word_extraction_warnings'] == report['word_extraction_warnings_original']
    _confirm_word_question(questions[1])
    finalize_word_extraction_reviews(questions, report, source)
    assert report['word_extraction_review_count'] == 1  # the independent image failure remains
    assert report['word_extraction_warnings'] == ['无法定位 Word 图片资源：media/MathType公式.png']
    assert report['review_required'] == 3
    assert len(report['word_extraction_warnings_original']) == 2


def test_word_unavailable_special_glyph_is_reviewed_even_when_text_matching_passed():
    source = '1. 求 [特殊字符待核对] 的值。'
    questions = [{'content': source}]
    report = {'review_required': 1, 'source_matches': [
        {'question_index': 0, 'field': 'content', 'source_start': 0, 'source_end': len(source),
         'source_excerpt': source}]}
    prepare_word_extraction_reviews(questions, report, source)
    assert report['source_review_count'] == 1
    assert questions[0]['source_review']['required'] is True


@pytest.mark.parametrize('change', ['unmatched', 'duplicated', 'wrong_excerpt', 'no_visual_verdict'])
def test_native_formula_notice_cannot_be_cleared_without_exact_verified_ownership(change):
    source, questions, report = _word_extraction_fixture()
    prepare_word_extraction_reviews(questions, report, source)
    for question in questions:
        _confirm_word_question(question)
    if change == 'unmatched':
        report['source_matches'] = []
    elif change == 'duplicated':
        report['source_matches'] += [{**match, 'question_index': 1 - match['question_index']}
                                      for match in report['source_matches']]
    elif change == 'wrong_excerpt':
        for match in report['source_matches']:
            match['source_excerpt'] = 'other source'
    else:
        for question in questions:
            question['source_review']['verification'] = {}
    finalize_word_extraction_reviews(questions, report, source)
    assert report['word_extraction_review_count'] == 3
    assert len(report['word_extraction_warnings']) == 2


def test_advisory_policy_is_not_a_claim_that_uncertain_or_changed_content_is_correct():
    questions = [
        {'content': '$x+1$', 'answer_markdown': '', 'source_review': {
            'required': True, 'reasons': ['无法唯一确定来源'], 'source_excerpt': 'source'}},
        {'content': '$x-1$', 'source_review': {'required': True, 'reasons': ['确实存在差异'],
            'verification_attempt': {'decision': 'different', 'evidence': '原式为x+1'}}},
        {'content': 'verified', 'source_review': {'required': False, 'verified_by': 'vision',
            'verification': {'decision': 'equivalent', 'snapshot_hash': 'original'}}},
        {'content': 'no source diagnostic'},
    ]
    original = deepcopy(questions)
    report = {'source_review_count': 2, 'unmatched_source': [{'source_excerpt': 'missing source'}]}
    make_source_review_advisory(questions, report)
    assert report['source_review_mode'] == 'advisory'
    assert report['source_review_blocking_count'] == 0
    assert report['source_review_notice_count'] == report['source_review_count'] == 2
    for before, after in zip(original, questions):
        if 'source_review' not in before:
            assert before == after
            continue
        assert after['source_review']['blocking'] is False
        assert after['source_review']['disposition'] == 'advisory'
        for key, value in before['source_review'].items():
            assert after['source_review'][key] == value
        assert after['content'] == before['content']
    assert report['unmatched_source'] == [{'source_excerpt': 'missing source'}]
    repeat = deepcopy((questions, report))
    make_source_review_advisory(questions, report)
    assert (questions, report) == repeat


@pytest.mark.skipif(not os.environ.get('MATHBANK_BEIJING_TASK'), reason='opt-in cached private task, no model call')
def test_beijing_28_questions_are_importable_without_claiming_21_suspicions_were_verified():
    task = json.loads(Path(os.environ['MATHBANK_BEIJING_TASK']).read_text())
    assert len(task['data']) == 28
    original = deepcopy(task)
    make_source_review_advisory(task['data'], task['diagnostics'])
    pending = [q for q in task['data'] if q.get('source_review', {}).get('required') is True]
    assert len(pending) == 21
    assert all(q['source_review']['blocking'] is False for q in pending)
    assert task['diagnostics']['source_review_blocking_count'] == 0
    assert task['diagnostics']['source_review_notice_count'] == 21
    for before, after in zip(original['data'], task['data']):
        assert after['content'] == before['content']
        assert after.get('answer_markdown') == before.get('answer_markdown')
        assert after.get('image_paths') == before.get('image_paths')
