from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from mathbank.import_review import make_source_review_advisory


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
