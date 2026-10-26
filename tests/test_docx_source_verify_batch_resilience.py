"""Independent Word batches survive bad replies without repeating paid calls."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import requests

from mathbank import docx_source_verify as verify
from mathbank.content_locks import _source_parts
from mathbank.task_manager import TaskCancelled
from test_docx_source_verify import state, run, _two_question_batch


def _response(item, *, raw=None, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {
        'content': json.dumps({'items': [item]}) if raw is None else raw}}]}


@pytest.mark.parametrize('failure', ['json', 'unknown_id', 'truncated', 'empty',
                                    'duplicate_key', 'body_list', 'missing_choice', 'json_decode'])
def test_bad_reply_is_isolated_and_later_word_batch_is_checked(state, monkeypatch, failure):
    decisions = _two_question_batch(state)
    before = deepcopy(state.questions)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)

    def post(provider, payload, **kwargs):
        state.calls.append((payload, kwargs))
        assert kwargs['retry_connection'] is False
        if len(state.calls) == 2:
            return SimpleNamespace(status_code=200, json=lambda: _response(decisions[1]))
        item = deepcopy(decisions[0])
        if failure == 'unknown_id':
            item['id'] = 'foreign'
        body = _response(item, raw='{' if failure == 'json' else '' if failure == 'empty'
                         else '{"items":[],"items":[]}' if failure == 'duplicate_key' else None,
                         finish='length' if failure == 'truncated' else 'stop')
        if failure == 'body_list':
            body = []
        if failure == 'missing_choice':
            body = {'choices': []}
        if failure == 'json_decode':
            def bad_json():
                raise ValueError('private provider content must not be copied')
            return SimpleNamespace(status_code=200, json=bad_json)
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert len(state.calls) == report['calls'] == 2
    assert report['checked'] == report['confirmed'] == report['pending'] == 1
    assert report['status'] == 'partial' and report['skipped'] == 0
    assert state.questions[0] == before[0]
    assert state.questions[1]['source_review']['required'] is False
    assert report['failed_batches'][0]['question_indices'] == [0]
    assert report['failed_batches'][0]['code'] == 'invalid_batch_response'
    assert 'private provider' not in json.dumps(report)
    # Each distinct batch was sent once; the failed first POST is not retried.
    assert 'word_001' in str(state.calls[0][0]) and 'word_002' in str(state.calls[1][0])


@pytest.mark.parametrize('status', [400, 401, 402, 403, 429, 500, 503])
def test_provider_failure_stops_later_word_batches_without_retry(state, monkeypatch, status):
    _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(*args, **kwargs):
        state.calls.append(True)
        return SimpleNamespace(status_code=status)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert len(state.calls) == report['calls'] == 1
    assert report['status'] == 'failed' and report['skipped'] == 1
    assert report['failed_batches'][0]['code'] == 'verification_stopped'
    assert f'HTTP {status}' in report['notes'][-1]
    assert all(q['source_review']['required'] for q in state.questions)


@pytest.mark.parametrize('change', ['page', 'question', 'evidence', 'matches'])
def test_bad_reply_cannot_hide_changed_evidence_and_continue(state, monkeypatch, change):
    _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(*args, **kwargs):
        state.calls.append(True)
        if change == 'page':
            state.image.write_bytes(b'changed')
        elif change == 'question':
            state.questions[0]['content'] += ' changed'
        elif change == 'matches':
            state.diagnostics['source_matches'][0]['source_start'] += 1
        else:
            state.evidence['pages'][0]['text'] += ' changed'
        return SimpleNamespace(status_code=200, json=lambda: _response({}, raw='{'))
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == len(state.calls) == 1
    assert report['checked'] == report['confirmed'] == 0
    assert report['skipped'] == 1
    assert report['failed_batches'][0]['code'] == 'verification_stopped'


def test_cancel_after_failed_word_request_prevents_later_batch(state, monkeypatch):
    _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(*args, **kwargs):
        state.calls.append(True)
        return SimpleNamespace(status_code=200, json=lambda: _response({}, raw='{'))
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    def check_cancelled():
        if state.calls:
            raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        run(state, check_cancelled=check_cancelled)
    assert len(state.calls) == 1


def test_transport_with_unknown_result_stops_without_replaying(state, monkeypatch):
    _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(*args, **kwargs):
        state.calls.append(True)
        raise requests.ReadTimeout('provider-private-details')
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == len(state.calls) == 1
    assert report['skipped'] == 1 and report['checked'] == 0
    assert 'provider-private-details' not in json.dumps(report)


@pytest.mark.parametrize('body', [{'error': {'code': 'insufficient_quota', 'message': 'private'}},
                                 {'status': 'error', 'message': 'private'}])
def test_service_error_wrapped_in_http_200_stops_later_batches(state, monkeypatch, body):
    _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(*args, **kwargs):
        state.calls.append(True)
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == len(state.calls) == 1 and report['skipped'] == 1
    assert report['failed_batches'][0]['code'] == 'verification_stopped'
    assert 'private' not in json.dumps(report)


def test_failed_batches_do_not_expand_document_call_budget(state, monkeypatch):
    _two_question_batch(state)
    state.source += '\n3. 已知 $z=3$，求结果。'
    state.questions.append({'content': '3. 已知 $z=3$ [公式结构待核对]，求结果。',
        'answer_markdown': '', 'source_review': {'required': True,
            'reasons': ['Word 原生公式提取存在疑点，请对照原页核对。']}})
    part = _source_parts(state.source, [])[-1]
    state.diagnostics['source_matches'].append({'question_index': 2, 'field': 'content',
        'source_number': 3, 'source_start': part.source_start, 'source_end': part.source_end,
        'source_excerpt': part.text})
    state.evidence['pages'][0]['text'] = state.source
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    monkeypatch.setattr(verify, 'MAX_CALLS', 2)
    def post(*args, **kwargs):
        state.calls.append(True)
        return SimpleNamespace(status_code=200, json=lambda: _response({}, raw='{'))
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == len(state.calls) == len(report['failed_batches']) == 2
    assert report['pending'] == 3 and report['skipped'] == 1 and report['status'] == 'failed'


@pytest.mark.parametrize('failure', ['http', 'transport', 'wrapped_error'])
def test_repair_service_failure_retains_valid_review_and_stops_later_batches(state, monkeypatch, failure):
    decisions = _two_question_batch(state)
    decisions[0]['decision'] = 'different'
    decisions[0]['checks']['math_and_conditions'] = False
    decisions[0]['evidence'] = '原文与候选题目存在需要重新核验的明确公式差异。'
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    before = [q['content'] for q in state.questions]
    def post(*args, **kwargs):
        state.calls.append(True)
        if len(state.calls) == 1:
            return SimpleNamespace(status_code=200, json=lambda: _response(decisions[0]))
        if failure == 'transport':
            raise requests.ReadTimeout('private-service-details')
        return SimpleNamespace(status_code=429 if failure == 'http' else 200,
            json=lambda: {'error': {'code': 'quota', 'message': 'private-service-details'}})
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == len(state.calls) == 2
    assert report['checked'] == 1 and report['confirmed'] == 0
    assert report['pending'] == 2 and report['skipped'] == 1 and report['status'] == 'partial'
    assert report['items'][0]['decision'] == 'different'
    assert state.questions[0]['source_review']['repair']['status'] == 'failed'
    assert [q['content'] for q in state.questions] == before
    assert 'private-service-details' not in json.dumps(report)
