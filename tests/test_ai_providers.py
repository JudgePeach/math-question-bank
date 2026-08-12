import pytest

from mathbank.ai_providers import (
    apply_bailian_thinking_policy,
    parse_model_and_effort,
    resolve_draw_provider,
    resolve_ocr_fallbacks,
    resolve_ocr_provider,
    resolve_text_provider,
)


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


def test_unprefixed_models_keep_provider_selection_and_model_id():
    environment = {
        "ALI_BAILIAN_API_KEY": "bailian-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
    }

    qwen = resolve_text_provider("qwen3.7-max", environment)
    deepseek = resolve_text_provider("deepseek-v4-flash", environment)

    assert (qwen.provider_code, qwen.model_name, qwen.api_key) == (
        "bailian",
        "qwen3.7-max",
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


def test_bailian_model_id_is_preserved_after_effort_is_removed():
    config = resolve_text_provider(
        "BAILIAN/qwen3.7-max:high",
        {"ALI_BAILIAN_API_KEY": "key"},
    )

    assert config.model_name == "qwen3.7-max"
    assert config.reasoning_effort == "high"


@pytest.mark.parametrize(
    "provider_code",
    ["deepseek", "siliconflow", "zhongzhan_gpt", "zhongzhan_claude"],
)
def test_bailian_thinking_policy_does_not_change_other_providers(provider_code):
    original = {
        "model": "qwen3.8-max",
        "max_tokens": 4096,
        "reasoning_effort": "high",
        "enable_thinking": True,
    }

    result = apply_bailian_thinking_policy(
        original,
        provider_code=provider_code,
        model_name="qwen3.8-max",
        task="solve",
        thinking_enabled=False,
    )

    assert result == original
    assert result is not original


def test_bailian_thinking_policy_preserves_legacy_custom_models():
    original = {"model": "qwen-vl-plus", "max_tokens": 2048}

    result = apply_bailian_thinking_policy(
        original,
        provider_code="bailian",
        model_name="qwen-vl-plus",
        task="ocr",
    )

    assert result == original


@pytest.mark.parametrize(
    ("enabled", "expected_limit"),
    [(True, 32768), (False, 16384)],
)
def test_qwen37_solve_uses_bounded_thinking_budget(enabled, expected_limit):
    result = apply_bailian_thinking_policy(
        {
            "model": "qwen3.7-plus",
            "max_tokens": 8192,
            "reasoning_effort": "high",
        },
        provider_code="bailian",
        model_name="qwen3.7-plus",
        task="solve",
        thinking_enabled=enabled,
    )

    assert result["enable_thinking"] is enabled
    assert result["max_completion_tokens"] == expected_limit
    assert "max_tokens" not in result
    assert "reasoning_effort" not in result
    if enabled:
        assert result["thinking_budget"] == 16384
    else:
        assert "thinking_budget" not in result


def test_qwen38_draw_uses_medium_effort_without_thinking_budget():
    result = apply_bailian_thinking_policy(
        {"model": "qwen3.8-max", "thinking_budget": 99999},
        provider_code="bailian",
        model_name="qwen3.8-max",
        task="draw",
    )

    assert result["enable_thinking"] is True
    assert result["reasoning_effort"] == "medium"
    assert result["max_completion_tokens"] == 32768
    assert "thinking_budget" not in result


@pytest.mark.parametrize(
    "task",
    ["classify", "parse", "paper_selection", "latex_diagnostic"],
)
def test_bailian_structured_tasks_disable_thinking_and_old_token_cap(task):
    result = apply_bailian_thinking_policy(
        {
            "model": "qwen3.7-flash",
            "max_tokens": 512,
            "reasoning_effort": "high",
        },
        provider_code="bailian",
        model_name="qwen3.7-flash",
        task=task,
    )

    assert result["enable_thinking"] is False
    assert "max_tokens" not in result
    assert "reasoning_effort" not in result
    assert "thinking_budget" not in result


def test_bailian_ocr_disables_thinking_with_bounded_completion():
    result = apply_bailian_thinking_policy(
        {"model": "qwen3.7-flash"},
        provider_code="bailian",
        model_name="qwen3.7-flash",
        task="ocr",
    )

    assert result["enable_thinking"] is False
    assert result["max_completion_tokens"] == 16384


def test_unknown_explicit_prefix_is_not_silently_rerouted():
    config = resolve_text_provider(
        "UNKNOWN/model-name",
        {"DEEPSEEK_API_KEY": "must-not-be-used"},
    )

    assert config.provider_code == "unknown"
    assert config.api_key is None
    assert config.api_base is None
    assert config.model_name == "model-name"


def test_ocr_provider_resolves_legacy_transit_settings_and_effort():
    config = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_API_KEY": "legacy-key",
            "ZHONGZHAN_BASE_URL": "https://legacy.example/v1/chat/completions",
            "ZHONGZHAN_OCR_MODEL": "gpt-5.6-luna:high",
        },
    )

    assert config.provider_code == "zhongzhan_gpt"
    assert config.api_key == "legacy-key"
    assert config.model_name == "gpt-5.6-luna"
    assert config.reasoning_effort == "high"
    assert config.chat_completions_url == (
        "https://legacy.example/v1/chat/completions"
    )


def test_pdf_ocr_fallbacks_respect_claude_preference():
    providers = resolve_ocr_fallbacks(
        "zhongzhan_claude",
        {
            "ZHONGZHAN_CLAUDE_API_KEY": "claude-key",
            "SILICONFLOW_API_KEY": "sf-key",
            "ALI_BAILIAN_API_KEY": "bailian-key",
            "ZHONGZHAN_GPT_API_KEY": "gpt-key",
        },
    )

    assert [provider.provider_code for provider in providers] == [
        "zhongzhan_claude",
        "siliconflow",
        "bailian",
        "zhongzhan_gpt",
    ]


def test_draw_provider_strips_siliconflow_prefix_and_detects_images():
    config = resolve_draw_provider(
        "SILICONFLOW/Qwen/Qwen3-VL-32B-Instruct",
        {"SILICONFLOW_API_KEY": "sf-key"},
    )

    assert config.provider_code == "siliconflow"
    assert config.model_name == "Qwen/Qwen3-VL-32B-Instruct"
    assert config.supports_image_input is True
    assert "sf-key" not in repr(config)


def test_draw_provider_keeps_text_only_siliconflow_mode():
    config = resolve_draw_provider(
        "Qwen/Qwen3.5-397B-A17B",
        {"SILICONFLOW_API_KEY": "sf-key"},
    )

    assert config.model_name == "Qwen/Qwen3.5-397B-A17B"
    assert config.supports_image_input is False
