"""Curriculum context reuses existing paper-selection calls and bound evidence.

Run API fixtures only in a source-only temporary app copy. Every provider call
below is mocked; no real questions, credentials or images are needed.
"""
from copy import deepcopy
import json

import pytest

from test_paper_selection_api import add, select, no_real_selection_requests  # noqa: F401
from test_paper_selection_full_reading import body, long_stem, mock_stages


def capture_context(monkeypatch):
    """Observe the actual request builder, without substituting prompt behavior."""
    import main
    import mathbank.paper_standards as standards
    built, supplied = [], []
    build = standards.build_paper_standard_context
    prompts = main.build_paper_selection_prompts

    def recording_build(*args, **kwargs):
        value = build(*args, **kwargs)
        built.append(value)
        return value

    def recording_prompts(*args, **kwargs):
        supplied.append(kwargs.get('standard_context'))
        return prompts(*args, **kwargs)

    monkeypatch.setattr(standards, 'build_paper_standard_context', recording_build)
    monkeypatch.setattr(main, 'build_paper_selection_prompts', recording_prompts)
    return built, supplied


@pytest.mark.parametrize('long_question,expected_calls', [(False, 1), (True, 2)])
def test_one_standard_context_is_shared_by_existing_stages(client, db_session, monkeypatch, long_question, expected_calls):
    q = add(db_session, long_stem() if long_question else '求函数 $f(x)=x+1$ 的定义域。', 'detailed_answer')
    original = q.content
    built, supplied = capture_context(monkeypatch)
    calls = mock_stages(monkeypatch, lambda _number, _payload: body([q.id]))
    response = select(client, prompt='选1道函数题')
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(built) == 1
    assert len(calls) == len(supplied) == result['selection_reading']['model_calls'] == expected_calls
    assert all(context is built[0] for context in supplied)
    assert result['standard_context'] == built[0]
    assert result['standard_context']['enabled']
    assert result['standard_context']['references']
    assert result['standards_alignment'] == []  # Legacy model outputs remain usable.
    assert result['data'][0]['id'] == q.id
    assert q.content == original and q.usage_count == 0


def test_no_credentials_builds_context_without_any_model_request(client, db_session, monkeypatch):
    q = add(db_session, '研究函数的单调性。', 'detailed_answer')
    built, supplied = capture_context(monkeypatch)
    response = select(client, prompt='选1道函数题')
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(built) == 1 and supplied == []
    assert result['selection_reading']['model_calls'] == 0 and result['fallback']
    assert result['standard_context']['enabled'] and result['standards_alignment'] == []
    assert q.usage_count == 0


def test_unavailable_standard_context_does_not_fail_or_add_a_request(client, db_session, monkeypatch):
    import mathbank.paper_standards as standards
    original_build = standards.build_paper_standard_context
    unavailable = deepcopy(original_build('函数专题'))
    unavailable.update(enabled=False, references=[], requirements=[], warnings=['课标资料不可用，未进行课标对应判断。'])
    monkeypatch.setattr(standards, 'build_paper_standard_context', lambda *_a, **_k: unavailable)
    q = add(db_session, '求函数定义域。')
    calls = mock_stages(monkeypatch, lambda _n, _payload: body([q.id]))
    response = select(client, prompt='选1道函数题')
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(calls) == result['selection_reading']['model_calls'] == 1
    assert [item['id'] for item in result['data']] == [q.id]
    assert result['standard_context'] == unavailable and result['standards_alignment'] == []
    assert any('课标' in warning and '不可用' in warning for warning in result['warnings'])


def response_with_alignment(ids, alignment):
    from types import SimpleNamespace
    packet = {'selected_ids': ids, 'ai_analysis': '依据本次提供资料选择。', 'standards_alignment': alignment}
    return SimpleNamespace(json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(packet, ensure_ascii=False)}}]})


def alignment_item(question_id, reference_id, **changes):
    return {'question_id': question_id, 'reference_ids': [reference_id],
            'target': '确定函数定义域', 'evidence': '求函数定义域', **changes}


def test_valid_alignment_binds_final_question_and_real_reference(client, db_session, monkeypatch):
    q = add(db_session, '已知 $f(x)=x+1$，求函数定义域。')
    built, _supplied = capture_context(monkeypatch)
    def callback(_number, _payload):
        reference_id = built[0]['references'][0]['id']
        return response_with_alignment([q.id], [alignment_item(q.id, reference_id,
            book_pages=[999], document='伪造来源', source_sha256='fake')])
    calls = mock_stages(monkeypatch, callback)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1
    assert len(result['standards_alignment']) == 1
    accepted = result['standards_alignment'][0]
    assert accepted['question_id'] == q.id
    assert accepted['reference_ids'] == [built[0]['references'][0]['id']]
    assert accepted['evidence'] in q.content
    # Citation metadata must come from local resources, never model fields.
    assert '伪造来源' not in json.dumps(accepted, ensure_ascii=False)
    assert accepted.get('book_pages') != [999] and accepted.get('source_sha256') != 'fake'
    assert '课标' in result['ai_analysis'] and str(q.id) in result['ai_analysis']
    assert q.usage_count == 0


@pytest.mark.parametrize('corruption', [
    'outside_question', 'outside_reference', 'changed_evidence', 'quality_level',
    'literacy_level', 'student_attainment', 'official_verification', 'boolean_question',
])
def test_unsupported_alignment_is_warning_not_lost_question_or_extra_call(client, db_session, monkeypatch, corruption):
    q = add(db_session, '已知 $f(x)=x+1$，求函数定义域。')
    built, _supplied = capture_context(monkeypatch)
    def callback(_number, _payload):
        item = alignment_item(q.id, built[0]['references'][0]['id'])
        if corruption == 'outside_question': item['question_id'] = q.id + 5000
        if corruption == 'outside_reference': item['reference_ids'] = ['invented:standard:999']
        if corruption == 'changed_evidence': item['evidence'] = '题干并没有这些条件'
        if corruption == 'quality_level': item['target'] = '该题属于学业质量水平二'
        if corruption == 'literacy_level': item['target'] = '该题属于核心素养水平三'
        if corruption == 'student_attainment': item['target'] = '学生已经达到课标要求'
        if corruption == 'official_verification': item['target'] = '官方核验通过'
        if corruption == 'boolean_question': item['question_id'] = True
        return response_with_alignment([q.id], [item])
    calls = mock_stages(monkeypatch, callback)
    response = select(client, prompt='选1道函数题')
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(calls) == result['selection_reading']['model_calls'] == 1
    assert [item['id'] for item in result['data']] == [q.id]
    assert result['standards_alignment'] == []
    assert any('课标' in warning for warning in result['warnings'])


def test_first_stage_alignment_is_not_inherited_after_full_review(client, db_session, monkeypatch):
    q = add(db_session, long_stem(), 'detailed_answer')
    built, _supplied = capture_context(monkeypatch)
    def callback(number, _payload):
        if number == 1:
            return response_with_alignment([q.id], [alignment_item(q.id, built[0]['references'][0]['id'],
                target='研究函数的单调性', evidence='证明中间小问的特殊单调性')])
        return response_with_alignment([q.id], [])
    calls = mock_stages(monkeypatch, callback)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 2 and result['data'][0]['id'] == q.id
    assert result['standards_alignment'] == []


def test_local_completion_does_not_inherit_model_alignment(client, db_session, monkeypatch):
    a = add(db_session, '甲：求函数定义域。')
    b = add(db_session, '乙：求函数定义域。')
    built, _supplied = capture_context(monkeypatch)
    def callback(_number, _payload):
        reference = built[0]['references'][0]['id']
        # The second ID is not selected by the model. A valid quote alone does
        # not turn subsequent local completion into an AI curriculum judgment.
        return response_with_alignment([a.id], [alignment_item(a.id, reference), alignment_item(b.id, reference)])
    calls = mock_stages(monkeypatch, callback)
    result = select(client, prompt='选2道函数题').json()
    assert len(calls) == 1 and result['selection_engine'] == 'ai_with_local_completion'
    assert {item['id'] for item in result['data']} == {a.id, b.id}
    assert [item['question_id'] for item in result['standards_alignment']] == [a.id]
    assert any('课标' in warning for warning in result['warnings'])


@pytest.mark.parametrize('raw', [None, {}, [None] * 61])
def test_optional_alignment_invalid_envelopes_keep_question(raw):
    from types import SimpleNamespace
    from mathbank.paper_standard_alignment import validate_standard_alignment
    q = SimpleNamespace(id=7, content='甲题求函数定义域。')
    alignment, warnings = validate_standard_alignment(raw, selected=[q], ai_question_ids={7},
        context={'enabled': True, 'references': [{'id': 'real:1'}]})
    assert alignment == [] and warnings
    assert q.content == '甲题求函数定义域。'


def test_evidence_from_another_question_cannot_support_selected_question():
    from types import SimpleNamespace
    from mathbank.paper_standard_alignment import validate_standard_alignment
    selected = SimpleNamespace(id=7, content='甲题求函数值。')
    raw = [alignment_item(7, 'real:1', evidence='乙题求函数定义域')]
    alignment, warnings = validate_standard_alignment(raw, selected=[selected], ai_question_ids={7},
        context={'enabled': True, 'references': [{'id': 'real:1'}]})
    assert alignment == [] and warnings
