"""Bounded whole-question follow-up, with synthetic DBs and no real providers."""
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from mathbank.paper_selection_ai import candidate_prompt_items, prepare_full_review
from test_paper_selection_api import add, select, no_real_selection_requests  # noqa: F401


def body(ids, analysis='模拟模型说明'):
    return SimpleNamespace(json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content':
        json.dumps({'selected_ids': ids, 'ai_analysis': analysis})}}]})


def mock_stages(monkeypatch, callback, *, images=False):
    import main
    provider = SimpleNamespace(api_key='synthetic', api_base='https://example.invalid',
        model_name='gemini-3.8-flash-high' if images else 'synthetic',
        provider_code='zhongzhan_claude' if images else 'test',
        provider_label='测试', credential_label='测试', reasoning_effort=None)
    monkeypatch.setattr(main, 'resolve_text_provider', lambda _: provider)
    calls = []
    def post(_provider, payload, **kwargs):
        calls.append((payload, kwargs))
        return callback(len(calls), payload)
    monkeypatch.setattr(main, 'post_chat_completion', post)
    return calls


def pool(payload):
    content = payload['messages'][1]['content']
    if isinstance(content, list):
        content = content[0]['text']
    return json.loads(content.split('【候选试题池】:\n', 1)[1])


def long_stem():
    return ('设函数满足如下条件。' + '背景条件。' * 600 + '\n\n'
        '(1) 求函数的定义域。' + '原题条件。' * 400 + '\n\n'
        '(2) 证明中间小问的特殊单调性。' + '推理条件。' * 400 + '\n\n'
        '(3) 求参数的完整范围。')


def test_middle_target_is_screened_and_second_call_receives_whole_stem(client, db_session, monkeypatch):
    q = add(db_session, long_stem(), 'detailed_answer')
    original = q.content
    calls = mock_stages(monkeypatch, lambda _n, _p: body([q.id]))
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == result['selection_reading']['model_calls'] == 2
    first, second = pool(calls[0][0])[0], pool(calls[1][0])[0]
    assert first['stem_truncated'] and '特殊单调性' not in first['stem']
    assert any('特殊单调性' in part['text'] for part in first['structure_outline'])
    assert second['stem'] == original and second['full_text_provided'] and not second['stem_truncated']
    assert result['selection_engine'] == 'ai'
    assert result['selection_reading']['selected'][0]['basis'] == 'ai_full_review'
    assert q.content == original and q.usage_count == 0
    assert '实际模型请求2次：初筛完成；整题复读完成' in result['ai_analysis']


def test_short_text_needs_one_call(client, db_session, monkeypatch):
    q = add(db_session, '求函数 $f(x)=x+1$ 的定义域。')
    calls = mock_stages(monkeypatch, lambda _n, _p: body([q.id]))
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1 and not result['selection_reading']['review_required']
    assert result['selection_reading']['selected'][0]['basis'] == 'ai_screening_full_text'


def test_unknown_second_request_result_never_retried_or_inherited(client, db_session, monkeypatch):
    q = add(db_session, long_stem())
    def callback(number, _payload):
        if number == 2:
            raise ConnectionError('private-token-should-not-leak')
        return body([q.id], '错误的初筛声明：完整读过所有题目')
    calls = mock_stages(monkeypatch, callback)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 2 and result['fallback']
    assert result['selection_reading']['stages'][-1]['status'] == 'failed'
    assert result['selection_reading']['selected'][0]['basis'] == 'local_unreviewed'
    assert not result['selection_reading']['selected'][0]['text_complete']
    assert '完整读过所有题目' not in result['ai_analysis']
    assert 'private-token' not in json.dumps(result)
    assert '整题复读失败' in result['ai_analysis'] and '未自动重发' in result['ai_analysis']


def test_text_budget_never_truncates_a_full_review(client, db_session, monkeypatch):
    q = add(db_session, '函数条件。' * 6000)
    calls = mock_stages(monkeypatch, lambda _n, _p: body([q.id], '初筛说明'))
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1 and result['fallback']
    assert result['selection_reading']['review']['withheld'][0]['id'] == q.id
    item = result['selection_reading']['selected'][0]
    assert item['basis'] == 'local_unreviewed' and not item['text_provided_complete']
    assert '未截断冒充全文' in item['text_unavailable_reason']


def test_unknown_model_does_not_read_or_fetch_missing_pixels(client, db_session, monkeypatch, tmp_path):
    import mathbank.paper_selection_ai as module
    q = add(db_session, '函数图像如图。![](https://example.invalid/never-download.png)')
    monkeypatch.setattr(module, 'resolve_upload_asset', lambda *_a, **_k: pytest.fail('unknown model must not open images'))
    calls = mock_stages(monkeypatch, lambda _n, _p: body([q.id]))
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1 and result['fallback']
    item = result['selection_reading']['selected'][0]
    assert item['basis'] == 'local_unreviewed' and not item['image_complete']
    assert item['unavailable_images'][0]['reason'] == 'model_images_unsupported'
    assert '当前型号未确认识图' in result['ai_analysis']


@pytest.mark.parametrize('change', [False, True])
def test_confirmed_model_gets_safe_pixels_and_changed_assets_are_excluded(client, db_session, monkeypatch, tmp_path, change):
    import mathbank.paper_selection_ai as module
    monkeypatch.setattr(module, 'UPLOADS_DIR', tmp_path)
    image = tmp_path / 'diagram.png'
    Image.new('RGB', (80, 60), 'white').save(image)
    q = add(db_session, '函数图像如图。![](/static/uploads/diagram.png)')
    def callback(number, payload):
        if number == 2:
            parts = payload['messages'][1]['content']
            assert isinstance(parts, list) and any(part['type'] == 'image_url' for part in parts)
            assert pool(payload)[0]['image_pixels_complete']
            if change:
                Image.new('RGB', (80, 60), 'black').save(image)
        return body([q.id])
    calls = mock_stages(monkeypatch, callback, images=True)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 2
    if change:
        assert result['data'] == [] and any('题图字节发生变化' in warning for warning in result['warnings'])
    else:
        item = result['selection_reading']['selected'][0]
        assert item['basis'] == 'ai_full_review' and item['image_complete']
        assert result['selection_reading']['review']['image_count'] == 1


def test_revision_changes_after_second_call_are_excluded(client, db_session, monkeypatch):
    q = add(db_session, long_stem())
    def callback(number, _payload):
        if number == 2:
            q.content += '\n用户更改条件。';db_session.commit()
        return body([q.id])
    calls = mock_stages(monkeypatch, callback)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 2 and result['data'] == []
    assert any('等待期间' in warning for warning in result['warnings'])


def test_complete_review_never_follows_external_symlink_or_unsafe_paths(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside.png')
    Image.new('RGB', (10, 10), 'white').save(outside)
    (tmp_path / 'link.png').symlink_to(outside)
    provider = SimpleNamespace(provider_code='zhongzhan_claude', model_name='gemini-3.8-flash-high')
    q = SimpleNamespace(id=1, content='![](/static/uploads/link.png) ![](https://example.invalid/x.png)',
        knowledge_points=[], question_type='single_choice', difficulty='normal', usage_count=0,
        source='', category_chapter='', category_knowledge='', tags='')
    items, images, fingerprints, report = prepare_full_review([q], provider, uploads_dir=tmp_path)
    assert not images and not fingerprints and report['image_count'] == 0
    assert not items[0]['eligible_for_ai_final']
    assert all(item['reason'] == 'unsafe_missing_or_invalid_image' for item in items[0]['unavailable_images'])


def test_screening_and_followup_are_bounded_to_existing_pool(client, db_session, monkeypatch):
    q = add(db_session, long_stem(), 'detailed_answer')
    outside = add(db_session, '不得越过已选题排除项。')
    calls = mock_stages(monkeypatch, lambda _n, _p: body([outside.id, q.id]))
    result = select(client, prompt='选1道函数题', exclude_ids=[outside.id],
                    type_counts={'detailed_answer': 1}).json()
    assert len(calls) == 2 and [item['id'] for item in result['data']] == [q.id]
    assert all(outside.id not in {item['id'] for item in pool(payload)} for payload, _ in calls)


def test_image_budget_shortfall_does_not_inherit_ai_selection(client, db_session, monkeypatch, tmp_path):
    import mathbank.paper_selection_ai as module
    monkeypatch.setattr(module, 'UPLOADS_DIR', tmp_path)
    monkeypatch.setattr(module, 'MAX_REVIEW_IMAGES', 1)
    for name in ['first.png', 'second.png']:
        Image.new('RGB', (10, 10), 'white').save(tmp_path / name)
    a = add(db_session, '函数题甲 ![](/static/uploads/first.png)')
    b = add(db_session, '函数题乙 ![](/static/uploads/second.png)', 'detailed_answer')
    calls = mock_stages(monkeypatch, lambda _n, _p: body([a.id, b.id]), images=True)
    result = select(client, prompt='选函数题', type_counts={'single_choice': 1, 'detailed_answer': 1}).json()
    assert len(calls) == 2 and result['selection_engine'] == 'ai_with_local_completion'
    assert result['selection_summary']['type_counts'] == {'single_choice': 1, 'detailed_answer': 1}
    reading = {item['id']: item for item in result['selection_reading']['selected']}
    assert reading[a.id]['basis'] == 'ai_full_review'
    assert reading[b.id]['basis'] == 'local_unreviewed'
    assert reading[b.id]['unavailable_images'][0]['reason'] == 'image_count_budget'


def test_explicit_figure_without_an_asset_remains_unreviewed(client, db_session, monkeypatch):
    q = add(db_session, '如图，求函数在阴影区域的性质。')
    calls = mock_stages(monkeypatch, lambda _n, _p: body([q.id]), images=True)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1 and result['fallback']
    assert result['selection_reading']['selected'][0]['unavailable_images'][0]['reason'] == 'image_reference_missing'


def test_large_pool_and_followup_preserve_original_capacity_limits(client, db_session, monkeypatch):
    ids = []
    for number in range(130):
        ids.append(add(db_session, long_stem() + f'\n独立条件{number}。').id)
    calls = mock_stages(monkeypatch, lambda _n, _p: body(list(reversed(ids))))
    result = select(client, prompt='函数练习', limit=30).json()
    assert len(calls) == 2
    assert len(pool(calls[0][0])) == 120 and len(pool(calls[1][0])) <= 36
    assert result['count'] == 30
    assert all(len(item['stem']) <= 24000 and not item['stem_truncated'] for item in pool(calls[1][0]))
    assert sum(len(item['stem']) for item in pool(calls[1][0])) <= 180000


def test_partial_figure_set_does_not_consume_later_complete_question_budget(monkeypatch, tmp_path):
    import mathbank.paper_selection_ai as module
    monkeypatch.setattr(module, 'MAX_REVIEW_IMAGES', 2)
    Image.new('RGB', (10, 10), 'white').save(tmp_path / 'valid.png')
    def question(qid, text):
        return SimpleNamespace(id=qid, content=text, knowledge_points=[], question_type='single_choice',
            difficulty='normal', usage_count=0, source='', category_chapter='', category_knowledge='', tags='')
    incomplete = question(1, '![](/static/uploads/valid.png) ![](/static/uploads/missing.png)')
    complete = question(2, '![](/static/uploads/valid.png)')
    provider = SimpleNamespace(provider_code='zhongzhan_claude', model_name='gemini-3.8-flash-high')
    items, parts, fingerprints, report = prepare_full_review([incomplete, complete], provider, uploads_dir=tmp_path)
    assert not items[0]['eligible_for_ai_final'] and not items[0]['image_pixels_provided']
    assert items[1]['eligible_for_ai_final'] and items[1]['image_pixels_complete']
    assert report['image_count'] == 1 and {qid for qid, _ref, _sha in fingerprints} == {2}
    assert sum(part['type'] == 'image_url' for part in parts) == 1


@pytest.mark.parametrize('limit,reason', [('MAX_REVIEW_IMAGE_BYTES', 'image_single_byte_budget'),
                                       ('MAX_REVIEW_IMAGE_PIXELS', 'image_pixels_format_or_encoding')])
def test_single_image_resource_limits_are_explicit_without_followup(client, db_session, monkeypatch, tmp_path, limit, reason):
    import mathbank.paper_selection_ai as module
    monkeypatch.setattr(module, 'UPLOADS_DIR', tmp_path)
    monkeypatch.setattr(module, limit, 1)
    Image.new('RGB', (10, 10), 'white').save(tmp_path / 'budget.png')
    q = add(db_session, '函数如图 ![](/static/uploads/budget.png)')
    calls = mock_stages(monkeypatch, lambda _n, _p: body([q.id]), images=True)
    result = select(client, prompt='选1道函数题').json()
    assert len(calls) == 1 and result['fallback']
    item = result['selection_reading']['selected'][0]
    assert item['basis'] == 'local_unreviewed' and item['unavailable_images'][0]['reason'] == reason
