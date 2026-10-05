"""Provider contract regressions: switches are not interchangeable API fields."""
from copy import deepcopy

import pytest

from mathbank.ai_providers import apply_model_thinking_policy, apply_structured_output_policy, resolve_text_provider


TASKS = ['ocr', 'draw', 'solve', 'classify', 'parse', 'paper_selection', 'latex_diagnostic']


@pytest.mark.parametrize('model', ['Qwen/Qwen3.8-27B', 'Pro/Qwen/Qwen3.8-27B'])
@pytest.mark.parametrize('task', ['ocr', 'parse', 'classify', 'paper_selection', 'latex_diagnostic'])
def test_exact_siliconflow_qwen38_transcription_tasks_disable_implicit_thinking(model, task):
    provider = resolve_text_provider('SILICONFLOW/' + model + ':high')
    original = {'model': model, 'max_tokens': 65536, 'enable_thinking': True,
                'thinking': {'type': 'enabled'}, 'thinking_budget': 32768, 'reasoning_effort': 'high'}
    before = deepcopy(original)
    result = apply_model_thinking_policy(original, provider=provider, task=task)
    assert result == {'model': model, 'max_tokens': 65536, 'enable_thinking': False}
    assert original == before


@pytest.mark.parametrize('enabled', [True, False])
def test_siliconflow_qwen38_solve_obeys_switch_with_bounded_reasoning(enabled):
    provider = resolve_text_provider('SILICONFLOW/Qwen/Qwen3.8-27B:high')
    result = apply_model_thinking_policy({'model': provider.model_name, 'max_tokens': 16384},
                                        provider=provider, task='solve', thinking_enabled=enabled)
    assert result['enable_thinking'] is enabled
    assert result['max_tokens'] == 16384 and 'reasoning_effort' not in result
    assert result.get('thinking_budget') == (16384 if enabled else None)


@pytest.mark.parametrize('budget', [None, True, 0, 32769, 2048])
def test_siliconflow_qwen38_draw_uses_only_valid_bounded_thinking_budget(budget):
    provider = resolve_text_provider('SILICONFLOW/Pro/Qwen/Qwen3.8-27B')
    payload = {'model': provider.model_name, 'max_tokens': 8192}
    if budget is not None: payload['thinking_budget'] = budget
    result = apply_model_thinking_policy(payload, provider=provider, task='draw')
    assert result['enable_thinking'] is True
    assert result['thinking_budget'] == (2048 if budget == 2048 else 8192)


@pytest.mark.parametrize('config', ['SILICONFLOW/Qwen/Qwen3.8-27B', 'SILICONFLOW/Pro/Qwen/Qwen3.8-27B'])
def test_json_mode_is_explicitly_applied_only_to_verified_model(config):
    provider = resolve_text_provider(config)
    payload = {'model': provider.model_name, 'messages': []}
    assert apply_structured_output_policy(payload, provider=provider) == {
        **payload, 'response_format': {'type': 'json_object'}}
    assert 'response_format' not in payload
    custom = {**payload, 'response_format': {'type': 'json_schema'}}
    assert apply_structured_output_policy(custom, provider=provider) == custom


@pytest.mark.parametrize('config', [
    'ZHONGZHAN_GPT/Qwen/Qwen3.8-27B', 'BAILIAN/Qwen/Qwen3.8-27B',
    'SILICONFLOW/Qwen/Qwen3.8-27B-custom', 'SILICONFLOW/Qwen/Qwen3.5-397B-A17B',
    'SILICONFLOW/Qwen/Qwen3-VL-8B-Instruct'])
def test_json_mode_does_not_guess_other_models_or_providers(config):
    provider = resolve_text_provider(config)
    payload = {'model': provider.model_name, 'messages': []}
    assert apply_structured_output_policy(payload, provider=provider) == payload


def test_structured_system_rules_do_not_promote_page_data_or_mutate_messages():
    provider = resolve_text_provider('SILICONFLOW/Qwen/Qwen3.8-27B')
    data = {'role': 'user', 'content': [{'type': 'text', 'text': 'Untrusted original page data'}]}
    payload = {'model': provider.model_name, 'messages': [data]}
    before = deepcopy(payload)
    result = apply_structured_output_policy(payload, provider=provider, system_instruction='Trusted fixed JSON rules')
    assert result['messages'] == [{'role': 'system', 'content': 'Trusted fixed JSON rules'}, data]
    assert payload == before
    existing = {**payload, 'messages': [{'role': 'system', 'content': 'Existing rules'}, data]}
    assert apply_structured_output_policy(existing, provider=provider, system_instruction='Other rules')['messages'] == existing['messages']
    other = resolve_text_provider('ZHONGZHAN_GPT/Qwen/Qwen3.8-27B')
    assert apply_structured_output_policy(payload, provider=other, system_instruction='Trusted fixed JSON rules') == payload


def configured(model, base='https://transit.example/v1'):
    return resolve_text_provider(model, {
        'ZHONGZHAN_GPT_BASE_URL': base,
        'ZHONGZHAN_CLAUDE_BASE_URL': base,
    })


@pytest.mark.parametrize('base', ['https://api.openai.com/v1', 'https://transit.example/v1'])
@pytest.mark.parametrize('model,effort', [('gpt-6-astra', 'medium'), ('gpt-5.6-luna', 'max')])
@pytest.mark.parametrize('task', TASKS)
def test_gpt_contract_preserves_effort_without_vendor_switches(base, model, effort, task):
    provider = configured(f'ZHONGZHAN_GPT/{model}:{effort}', base)
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': '求 1+1'}],
        'stream': True,
        'max_tokens': 16384,
        'temperature': 0.2,
        'top_p': 0.9,
        'enable_thinking': True,
        'thinking': {'type': 'enabled'},
        'thinking_budget': 8000,
    }
    before = deepcopy(payload)
    result = apply_model_thinking_policy(payload, provider=provider, task=task, thinking_enabled=False)
    assert result == {
        'model': model, 'messages': payload['messages'], 'stream': True,
        'max_completion_tokens': 16384, 'reasoning_effort': effort,
    }
    assert payload == before


def test_astra_drops_logprobs_and_preserves_explicit_completion_limit():
    provider = configured('ZHONGZHAN_GPT/gpt-6-astra')
    result = apply_model_thinking_policy({
        'model': provider.model_name, 'max_tokens': 512, 'max_completion_tokens': 8192,
        'logprobs': False, 'top_logprobs': 2,
    }, provider=provider, task='solve', thinking_enabled=True)
    assert result == {'model': 'gpt-6-astra', 'max_completion_tokens': 8192, 'reasoning_effort': 'high'}


@pytest.mark.parametrize('task', ['solve', 'classify', 'parse', 'paper_selection', 'latex_diagnostic'])
@pytest.mark.parametrize('enabled', [True, False])
def test_deepseek_official_and_siliconflow_use_different_switches(task, enabled):
    actual_enabled = enabled if task == 'solve' else False
    for config, switch in [
        ('DEEPSEEK/deepseek-flash', 'thinking'),
        ('DEEPSEEK/deepseek-v4-pro', 'thinking'),
        ('DEEPSEEK/deepseek-v4-flash', 'thinking'),
        ('SILICONFLOW/deepseek-ai/DeepSeek-V4-Pro', 'enable_thinking'),
        ('SILICONFLOW/deepseek-ai/DeepSeek-V4-Flash', 'enable_thinking'),
    ]:
        p = configured(config)
        result = apply_model_thinking_policy({'model': p.model_name}, provider=p, task=task, thinking_enabled=enabled)
        if switch == 'thinking':
            assert result['thinking'] == {'type': 'enabled' if actual_enabled else 'disabled'}
            assert 'enable_thinking' not in result
        else:
            assert result['enable_thinking'] is actual_enabled
            assert 'thinking' not in result
        if not actual_enabled:
            assert 'reasoning_effort' not in result


@pytest.mark.parametrize('provider,expected', [('DEEPSEEK/deepseek-v4-pro', 'high'), ('SILICONFLOW/deepseek-ai/DeepSeek-V4-Pro', 'max')])
def test_explicit_effort_overrides_toggle_using_platform_mapping(provider, expected):
    p = configured(provider + ':xhigh')
    result = apply_model_thinking_policy({'model': p.model_name}, provider=p, task='solve', thinking_enabled=False)
    assert result['reasoning_effort'] == expected
    assert result.get('enable_thinking', result.get('thinking') == {'type': 'enabled'})


@pytest.mark.parametrize('size', ['8B', '32B'])
@pytest.mark.parametrize('task', ['ocr', 'draw', 'solve'])
def test_instruct_does_not_gain_a_thinking_mode(size, task):
    p = configured(f'SILICONFLOW/Qwen/Qwen3-VL-{size}-Instruct:max')
    result = apply_model_thinking_policy({'model': p.model_name, 'max_tokens': 1234}, provider=p, task=task, thinking_enabled=True)
    assert result == {'model': p.model_name, 'max_tokens': 1234}


@pytest.mark.parametrize('alias', ['gemini-3.8-flash-high', 'gemini-3.8-flash-medium', 'claude-3-5-sonnet', 'custom/model'])
@pytest.mark.parametrize('task', TASKS)
def test_unknown_transit_aliases_are_not_renamed_or_given_a_switch(alias, task):
    p = configured('ZHONGZHAN_CLAUDE/' + alias)
    original = {'model': alias, 'messages': [], 'max_tokens': 512, 'temperature': 0.2}
    assert p.reasoning_effort is None
    assert apply_model_thinking_policy(original, provider=p, task=task, thinking_enabled=True) == original
    p = configured('ZHONGZHAN_CLAUDE/' + alias + ':high')
    assert apply_model_thinking_policy(original, provider=p, task=task) == {**original, 'reasoning_effort': 'high'}


def test_bailian_still_uses_enable_thinking_and_task_budget():
    p = configured('BAILIAN/qwen3.7-plus')
    for task, enabled in [('ocr', False), ('classify', False), ('draw', True), ('solve', True)]:
        result = apply_model_thinking_policy({'model': p.model_name}, provider=p, task=task, thinking_enabled=True)
        assert result['enable_thinking'] is enabled
        assert ('thinking_budget' in result) is enabled
        assert 'thinking' not in result
        assert 'reasoning_effort' not in result


def test_unknown_provider_cannot_be_misidentified_by_host_substring():
    p = configured('ZHONGZHAN_GPT/custom', 'https://api.deepseek.com.example/v1')
    original = {'model': 'custom', 'provider_option': 'keep'}
    assert apply_model_thinking_policy(original, provider=p, task='solve', thinking_enabled=True) == original


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('model,effort', [('gpt-6-astra', 'medium'), ('gpt-5.6-luna', 'max')])
def test_solve_route_sends_gpt_contract_in_both_response_modes(client, monkeypatch, stream, model, effort):
    import json
    from unittest.mock import MagicMock, patch
    from main import LOCAL_TOKEN

    monkeypatch.setenv('ZHONGZHAN_GPT_API_KEY', 'test-key')
    monkeypatch.setenv('ZHONGZHAN_GPT_BASE_URL', 'https://api.openai.com/v1')
    upstream = MagicMock(status_code=200)
    upstream.json.return_value = {'choices': [{'message': {'content': '答案：2'}}]}
    upstream.iter_lines.return_value = iter([
        ('data: ' + json.dumps({'choices': [{'delta': {'content': '答案：2'}}]})).encode(),
        b'data: [DONE]',
    ])
    with patch('mathbank.ai_http.robust_request_post', return_value=upstream) as post:
        response = client.post('/api/ai/solve', data={
            'content': '求 1+1', 'question_type': 'detailed_answer',
            'thinking': 'disabled', 'model': f'ZHONGZHAN_GPT/{model}:{effort}',
            'stream': str(stream).lower(),
        }, headers={'X-Local-Token': LOCAL_TOKEN})
    assert response.status_code == 200
    if stream:
        assert '"status": "done"' in response.text
    else:
        assert response.json()['status'] == 'success'
    post.assert_called_once()
    sent = post.call_args.kwargs['json']
    assert sent['model'] == model
    assert sent['reasoning_effort'] == effort
    assert sent['max_completion_tokens'] == 16384
    for key in ['enable_thinking', 'thinking', 'max_tokens', 'temperature']:
        assert key not in sent


def test_siliconflow_v32_keeps_switch_without_v4_effort_field():
    p = configured('SILICONFLOW/Pro/deepseek-ai/DeepSeek-V3.2:max')
    result = apply_model_thinking_policy({'model': p.model_name}, provider=p, task='solve', thinking_enabled=True)
    assert result == {'model': p.model_name, 'enable_thinking': True}


@pytest.mark.parametrize('task', TASKS)
def test_deepseek_flash_uses_documented_thinking_fields(task):
    provider = configured('DEEPSEEK/deepseek-flash')
    payload = {'model':provider.model_name, 'enable_thinking':True, 'thinking_budget':2048,
               'temperature':0.2, 'top_p':0.97, 'presence_penalty':0.2, 'frequency_penalty':0.2}
    original = deepcopy(payload)
    result = apply_model_thinking_policy(payload, provider=provider, task=task, thinking_enabled=True)
    enabled = task in {'solve', 'draw'}
    assert result['thinking'] == {'type':'enabled' if enabled else 'disabled'}
    assert 'enable_thinking' not in result and 'thinking_budget' not in result
    if enabled:
        assert result['top_p'] == 0.97
        assert all(field not in result for field in ('temperature', 'presence_penalty', 'frequency_penalty'))
    else:
        assert 'top_p' not in result and 'reasoning_effort' not in result
    assert payload == original


@pytest.mark.parametrize('effort,expected', [('low','low'), ('medium','high'), ('high','high'), ('xhigh','high'), ('max','max')])
def test_deepseek_flash_effort_mapping(effort, expected):
    provider = configured('DEEPSEEK/deepseek-flash:' + effort)
    result = apply_model_thinking_policy({'model':provider.model_name}, provider=provider, task='ocr')
    assert result['thinking'] == {'type':'enabled'}
    assert result['reasoning_effort'] == expected
