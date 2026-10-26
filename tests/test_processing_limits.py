"""Bounded resource configuration preserves defaults and never echoes secrets."""
from dataclasses import FrozenInstanceError, asdict

import pytest

from mathbank.processing_limits import ProcessingLimits, LIMIT_SPECS, resolve_processing_limits
from mathbank.classification_policy import resolve_classification_policy
from mathbank.ai_providers import apply_model_thinking_policy, resolve_text_provider


def test_existing_defaults_and_immutable_policy():
    value = resolve_processing_limits({})
    assert asdict(value) == {'image_upload_bytes':10485760, 'image_pixels':30000000,
        'normalized_image_bytes':20971520, 'document_upload_bytes':31457280,
        'text_source_bytes':5242880, 'pdf_pages':80, 'document_workers':2, 'document_queue':4,
        'classify_images':4, 'classify_image_bytes':4194304, 'pdf_ocr_workers':4}
    with pytest.raises(FrozenInstanceError):
        value.pdf_pages = 500


@pytest.mark.parametrize('field', list(LIMIT_SPECS))
def test_each_capacity_override_reaches_exact_units_and_preserves_other_fields(field):
    name, default, low, high, unit = LIMIT_SPECS[field]
    for selected in {low, high}:
        value = asdict(resolve_processing_limits({name: str(selected)}))
        before = asdict(ProcessingLimits())
        assert value[field] == selected * unit
        value.pop(field);before.pop(field)
        assert value == before


@pytest.mark.parametrize('bad', ['', '-1', '1.5', 'NaN', 'inf', '１２', '9'*100, 'PRIVATE_TOKEN_OR_PATH', True])
def test_bad_limits_fall_back_without_exposing_the_supplied_value(bad):
    field = 'document_upload_bytes'
    name, default, low, high, unit = LIMIT_SPECS[field]
    with pytest.warns(RuntimeWarning) as captured:
        actual = getattr(resolve_processing_limits({name: bad}), field)
    assert actual == default * unit
    if bad == 'PRIVATE_TOKEN_OR_PATH':
        assert str(bad) not in str(captured[0].message)
    assert name in str(captured[0].message)


def test_classification_profiles_and_explicit_mode_validation():
    assert resolve_classification_policy({}).mode == 'quick'
    assert resolve_classification_policy({}).output_tokens == 4096
    precise = resolve_classification_policy({}, mode='precise')
    assert precise.output_tokens == 8192 and precise.thinking_enabled
    with pytest.raises(ValueError, match='quick'):
        resolve_classification_policy({}, mode='PRIVATE_TOKEN_OR_PATH')
    with pytest.warns(RuntimeWarning) as captured:
        assert resolve_classification_policy({'MATHBANK_CLASSIFY_MODE':'PRIVATE_TOKEN_OR_PATH'}).mode == 'quick'
    assert 'PRIVATE_TOKEN_OR_PATH' not in str(captured[0].message)


@pytest.mark.parametrize('mode,name,default', [('quick','MATHBANK_CLASSIFY_QUICK_MAX_TOKENS',4096),
                                            ('precise','MATHBANK_CLASSIFY_PRECISE_MAX_TOKENS',8192)])
def test_profile_output_budget_is_bounded(mode, name, default):
    assert resolve_classification_policy({name:'2048'},mode=mode).output_tokens == 2048
    with pytest.warns(RuntimeWarning):
        assert resolve_classification_policy({name:'999999999999'},mode=mode).output_tokens == default


def test_verified_deepseek_classification_switch_is_explicit_and_payload_is_not_mutated():
    provider = resolve_text_provider('SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash', {'SILICONFLOW_API_KEY':'test'})
    original = {'model':provider.model_name,'max_tokens':4096}
    assert apply_model_thinking_policy(original,provider=provider,task='classify')['enable_thinking'] is False
    for mode in ['quick','precise']:
        profile = resolve_classification_policy({}, mode=mode)
        payload = apply_model_thinking_policy({**original,'max_tokens':profile.output_tokens},
            provider=provider,task='classify',thinking_enabled=profile.thinking_enabled)
        assert payload['enable_thinking'] is profile.thinking_enabled
        assert payload['max_tokens'] == profile.output_tokens
    assert original == {'model':provider.model_name,'max_tokens':4096}


def test_unknown_relay_does_not_receive_invented_thinking_parameters():
    provider = resolve_text_provider('ZHONGZHAN_CLAUDE/custom-vision-model',
        {'ZHONGZHAN_CLAUDE_API_KEY':'test','ZHONGZHAN_CLAUDE_BASE_URL':'https://example.invalid/v1'})
    payload = apply_model_thinking_policy({'model':provider.model_name,'max_tokens':8192},
        provider=provider,task='classify',thinking_enabled=True)
    assert not any(key in payload for key in ['enable_thinking','thinking','thinking_budget','reasoning_effort'])


def test_verified_qwen_precision_reserves_budget_for_the_json_answer():
    provider=resolve_text_provider('SILICONFLOW/Qwen/Qwen3.8-27B',{'SILICONFLOW_API_KEY':'test'})
    result=apply_model_thinking_policy({'model':provider.model_name,'max_tokens':8192,'thinking_budget':32768},
        provider=provider,task='classify',thinking_enabled=True)
    assert result['enable_thinking'] is True
    assert result['max_tokens']==8192 and result['thinking_budget']==4096
