"""Selection contracts use synthetic questions and mocked provider traffic."""
import json
from types import SimpleNamespace

import pytest

from mathbank.database import Question
from mathbank.paper_selection_ai import candidate_prompt_items, parse_selection_response, checked_selection
from mathbank.prompts import build_paper_selection_prompts


@pytest.fixture(autouse=True)
def no_real_selection_requests(monkeypatch):
    import main
    monkeypatch.setattr(main, 'resolve_text_provider', lambda _: SimpleNamespace(
        api_key='', api_base='', model_name='synthetic', provider_label='测试',
        credential_label='测试', provider_code='test', reasoning_effort=None))
    monkeypatch.setattr(main, 'post_chat_completion', lambda *_a, **_k: pytest.fail('unmocked AI request'))


def add(db, content, kind='single_choice', **kw):
    q = Question(content=content, question_type=kind, difficulty='normal', **kw)
    db.add(q)
    db.commit()
    return q


def select(client, **kw):
    import main
    return client.post('/api/paper/ai-select', json={'reference_mode':'none', **kw},
                       headers={'X-Local-Token':main.LOCAL_TOKEN})


def mock_model(monkeypatch, ids, analysis='模型选题说明', *, finish='stop'):
    import main
    monkeypatch.setattr(main, 'resolve_text_provider', lambda _: SimpleNamespace(
        api_key='synthetic', api_base='https://example.invalid', model_name='synthetic',
        provider_label='测试', credential_label='测试', provider_code='test', reasoning_effort=None))
    calls = []
    def post(provider, payload, **kwargs):
        calls.append((payload, kwargs))
        return SimpleNamespace(json=lambda: {'choices':[{'finish_reason':finish, 'message':{'content':
            json.dumps({'selected_ids':ids, 'ai_analysis':analysis})}}]})
    monkeypatch.setattr(main, 'post_chat_completion', post)
    return calls


def test_keyword_and_existing_cart_remain_hard_constraints(client, db_session):
    old = add(db_session, '函数A', source='原创专题')
    fresh = add(db_session, '函数B', source='原创专题')
    add(db_session, '函数C', source='其他来源')
    result = select(client, prompt='选5道函数单选题', keyword='原创专题', exclude_ids=[old.id]).json()
    assert [q['id'] for q in result['data']] == [fresh.id]
    assert result['selection_summary']['shortfall'] == 4
    assert result['fallback'] and result['warnings']
    assert fresh.usage_count == 0


def test_old_matching_question_is_retrieved_before_latest_35(client, db_session):
    target = add(db_session, '函数目标题')
    for index in range(45):
        add(db_session, '无关几何'+str(index))
    result = select(client, prompt='选1道函数单选题').json()
    assert [q['id'] for q in result['data']] == [target.id]


def test_model_ids_are_checked_and_missing_slots_locally_completed(client, db_session, monkeypatch):
    a = add(db_session, '函数甲')
    b = add(db_session, '函数乙')
    outside = add(db_session, '函数解答', 'detailed_answer')
    calls = mock_model(monkeypatch, [True, a.id, a.id, outside.id, 999999])
    result = select(client, prompt='选2道函数单选题').json()
    assert len(calls) == 1
    assert {q['id'] for q in result['data']} == {a.id,b.id}
    assert result['selection_engine'] == 'ai_with_local_completion'
    assert result['selection_summary']['shortfall'] == 0
    envelope = calls[0][0]
    assert 'main直接考查' in envelope['messages'][0]['content']
    assert 'stem_truncated' in envelope['messages'][1]['content']
    assert calls[0][1]['timeout'] >= 60


def test_truncated_model_output_falls_back_once(client, db_session, monkeypatch):
    q = add(db_session, '函数题')
    calls = mock_model(monkeypatch, [q.id], finish='length')
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1 and result['fallback']
    assert any('未自动重发' in warning for warning in result['warnings'])


@pytest.mark.parametrize('change', ['edit', 'delete'])
def test_deleted_or_edited_candidate_during_model_wait_is_not_selected(client, db_session, monkeypatch, change):
    import main
    q = add(db_session, '函数旧题文')
    qid = q.id
    mock_model(monkeypatch, [qid])
    def post(*_args, **_kwargs):
        if change == 'delete':
            db_session.delete(q)
        else:
            q.content = '等待期间已经改动的题文'
        db_session.commit()
        return SimpleNamespace(json=lambda: {'choices':[{'message':{'content':json.dumps({
            'selected_ids':[qid], 'ai_analysis':'旧说明'})}}]})
    monkeypatch.setattr(main, 'post_chat_completion', post)
    result = select(client, prompt='选1道函数题')
    assert result.status_code == 200, result.text
    assert result.json()['data'] == []
    assert any('等待期间' in warning for warning in result.json()['warnings'])


def test_explicit_negative_question_type_is_kept_in_local_fallback(client, db_session):
    valid = add(db_session, '目标题')
    add(db_session, '解答题不得加入', 'detailed_answer')
    result = select(client, prompt='选5道题，不要解答题').json()
    assert [q['id'] for q in result['data']] == [valid.id]


def test_partial_topic_requirement_does_not_filter_entire_paper(client, db_session, monkeypatch):
    a = add(db_session, '函数导数题')
    b = add(db_session, '集合题')
    calls = mock_model(monkeypatch, [a.id, b.id])
    result = select(client, prompt='组2题，其中1题考查导数').json()
    assert result['selection_summary']['requested'] == 2
    assert {q['id'] for q in result['data']} == {a.id, b.id}
    assert len(calls) == 1


def test_reference_whole_paper_counts_and_partial_pool_shortfall(client, db_session):
    kinds = ['single_choice']*8 + ['multi_choice']*3 + ['fill_in_blank']*3 + ['detailed_answer']*5
    for index, kind in enumerate(kinds,1):
        add(db_session, '参考真题'+str(index), kind, source=f'2026年全国一卷 第{index}题')
    # An unrelated scattered source is never an extra paper slot.
    add(db_session, '散题', source='2026新高考一卷')
    response = client.get('/api/paper/reference-exams')
    profile = response.json()['data'][0]
    assert profile['complete'] and profile['question_count'] == 19
    result = select(client, prompt='组19题高考卷', reference_mode='recent_gaokao').json()
    assert result['count'] == 19
    assert result['selection_summary']['type_counts'] == profile['type_counts']
    assert result['selection_summary']['requested_type_counts'] == profile['type_counts']
    scoped = select(client, prompt='选3道单选题', reference_mode='recent_gaokao').json()
    assert scoped['count'] == 3
    assert not scoped['selection_summary']['requested_type_counts']
    assert '参考来源' in scoped['ai_analysis']


@pytest.mark.parametrize('payload', [
    {'limit':True}, {'limit':31}, {'limit':0}, {'limit':'5'}, {'prompt':[]},
    {'exclude_ids':[True]}, {'reference_mode':'unknown'}, {'keyword':[]},
    {'reference_key':'missing','reference_mode':'recent_gaokao'},
])
def test_invalid_selection_configuration_is_400(client, payload):
    assert select(client, **payload).status_code == 400


@pytest.mark.parametrize('raw', ['{"selected_ids":[],"selected_ids":[1]}',
                                '{"selected_ids":[NaN]}', '[]', '{}'])
def test_ambiguous_envelopes_rejected(raw):
    body = {'choices':[{'message':{'content':raw}}]}
    with pytest.raises(ValueError):
        parse_selection_response(body)


def test_candidate_stems_keep_tail_and_mark_unseen_images_and_no_claimed_verification():
    question = SimpleNamespace(id=8, content='条件'+('甲'*6000)+'最终求证！![](/static/uploads/a.png)',
        knowledge_points=[], question_type='detailed_answer', difficulty='easy_error',
        usage_count=0, source='', category_chapter='', category_knowledge='', tags='')
    item = candidate_prompt_items([question])[0]
    assert item['stem_truncated'] and '最终求证' in item['stem']
    assert item['has_image_reference'] and not item['image_pixels_provided']
    system, user = build_paper_selection_prompts('函数复习', 1, [item], False,
        reference_context={'enabled':False}, selection_plan={'type_counts':{}})
    assert 'easy_error表示易错' in system and '不宣称已演算核验答案' in system
    assert '库内真题参考' in user
