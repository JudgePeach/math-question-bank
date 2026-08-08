import pytest

from mathbank.ai_providers import parse_model_and_effort, resolve_text_provider


@pytest.mark.parametrize(
    ("configured", "expected_model", "expected_effort"),
    [
        ("gpt-5.6-sol:high", "gpt-5.6-sol", "high"),
        ("gpt-5.6-sol(XHigh)", "gpt-5.6-sol", "xhigh"),
        (
            "ZHONGZHAN_GPT/gpt-5.6-sol:max",
            "ZHONGZHAN_GPT/gpt-5.6-sol",
            "max",
        ),
        ("deepseek-chat", "deepseek-chat", None),
        ("model:unsupported", "model:unsupported", None),
    ],
)
def test_parse_model_and_effort(configured, expected_model, expected_effort):
    assert parse_model_and_effort(configured) == (expected_model, expected_effort)


@pytest.mark.parametrize(
    ("configured", "provider_code", "key_env", "expected_base"),
    [
        (
            "SILICONFLOW/deepseek-ai/DeepSeek-V3",
            "siliconflow",
            "SILICONFLOW_API_KEY",
            "https://api.siliconflow.cn/v1",
        ),
        (
            "BAILIAN/qwen-max",
            "bailian",
            "ALI_BAILIAN_API_KEY",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "DEEPSEEK/deepseek-chat",
            "deepseek",
            "DEEPSEEK_API_KEY",
            "https://api.deepseek.com",
        ),
        (
            "ZHONGZHAN_GPT/gpt-5.6-sol",
            "zhongzhan_gpt",
            "ZHONGZHAN_GPT_API_KEY",
            "https://api.openai.com/v1",
        ),
        (
            "ZHONGZHAN_CLAUDE/claude-sonnet",
            "zhongzhan_claude",
            "ZHONGZHAN_CLAUDE_API_KEY",
            "https://api.openai.com/v1",
        ),
    ],
)
def test_explicit_text_providers_resolve_to_expected_defaults(
    configured, provider_code, key_env, expected_base
):
    config = resolve_text_provider(configured, {key_env: "secret-value"})

    assert config.provider_code == provider_code
    assert config.api_key == "secret-value"
    assert config.api_base == expected_base
    assert config.chat_completions_url == f"{expected_base}/chat/completions"
    assert key_env in config.credential_label
    assert "secret-value" not in repr(config)


def test_unprefixed_models_keep_legacy_provider_selection_and_alias():
    environment = {
        "ALI_BAILIAN_API_KEY": "bailian-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
    }

    qwen = resolve_text_provider("qwen3.7-max", environment)
    deepseek = resolve_text_provider("deepseek-v4-flash", environment)

    assert (qwen.provider_code, qwen.model_name, qwen.api_key) == (
        "bailian",
        "qwen-max",
        "bailian-key",
    )
    assert (deepseek.provider_code, deepseek.model_name, deepseek.api_key) == (
        "deepseek",
        "deepseek-v4-flash",
        "deepseek-key",
    )


def test_custom_base_and_reasoning_effort_are_resolved_together():
    config = resolve_text_provider(
        "ZHONGZHAN_GPT/gpt-5.6-sol:high",
        {
            "ZHONGZHAN_GPT_API_KEY": "transit-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://transit.example/v1/",
        },
    )

    assert config.model_name == "gpt-5.6-sol"
    assert config.reasoning_effort == "high"
    assert config.chat_completions_url == "https://transit.example/v1/chat/completions"


def test_effort_parsing_can_be_disabled_for_legacy_call_sites():
    config = resolve_text_provider(
        "DEEPSEEK/deepseek-v4-flash:high",
        {"DEEPSEEK_API_KEY": "key"},
        parse_effort=False,
    )

    assert config.model_name == "deepseek-v4-flash:high"
    assert config.reasoning_effort is None


def test_bailian_alias_keeps_legacy_order_when_effort_is_present():
    config = resolve_text_provider(
        "BAILIAN/qwen3.7-max:high",
        {"ALI_BAILIAN_API_KEY": "key"},
    )

    assert config.model_name == "qwen3.7-max"
    assert config.reasoning_effort == "high"


def test_unknown_explicit_prefix_is_not_silently_rerouted():
    config = resolve_text_provider(
        "UNKNOWN/model-name",
        {"DEEPSEEK_API_KEY": "must-not-be-used"},
    )

    assert config.provider_code == "unknown"
    assert config.api_key is None
    assert config.api_base is None
    assert config.model_name == "model-name"
