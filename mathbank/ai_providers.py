"""Pure helpers for resolving text-model provider configuration.

This module deliberately does not send HTTP requests.  It only turns the model
value stored by the settings UI into a provider, credential, endpoint, clean
model name, and optional reasoning effort.
"""

from dataclasses import dataclass, field
import os
import re
from typing import List, Mapping, Optional, Tuple


_VALID_REASONING_EFFORTS = frozenset(
    {"high", "medium", "low", "xhigh", "max", "default"}
)


@dataclass(frozen=True)
class TextProviderConfig:
    """Resolved configuration for one OpenAI-compatible text provider."""

    provider_code: str
    provider_label: str
    api_key_env: str
    api_key: Optional[str] = field(repr=False)
    api_base: Optional[str]
    model_name: str
    reasoning_effort: Optional[str]
    raw_model: str

    @property
    def credential_label(self) -> str:
        """Human-readable provider name used by configuration errors."""

        if not self.api_key_env:
            return self.provider_label
        return f"{self.provider_label} ({self.api_key_env})"

    @property
    def chat_completions_url(self) -> Optional[str]:
        """Return the provider's OpenAI-compatible chat completion URL."""

        if not self.api_base:
            return None
        return f"{self.api_base.rstrip('/')}/chat/completions"


@dataclass(frozen=True)
class MultimodalProviderConfig:
    """Resolved configuration for OCR and TikZ image-aware requests."""

    provider_code: str
    provider_label: str
    api_key_env: str
    api_key: Optional[str] = field(repr=False)
    api_base: str
    model_name: str
    reasoning_effort: Optional[str]
    supports_image_input: bool
    raw_config: str

    @property
    def credential_label(self) -> str:
        return f"{self.provider_label} ({self.api_key_env})"

    @property
    def chat_completions_url(self) -> str:
        normalized_base = self.api_base.rstrip("/")
        if normalized_base.endswith("/chat/completions"):
            return normalized_base
        return f"{normalized_base}/chat/completions"


@dataclass(frozen=True)
class _ProviderSpec:
    code: str
    label: str
    api_key_env: str
    api_base_env: Optional[str]
    default_api_base: str


_PROVIDER_SPECS = {
    "SILICONFLOW": _ProviderSpec(
        code="siliconflow",
        label="硅基流动",
        api_key_env="SILICONFLOW_API_KEY",
        api_base_env=None,
        default_api_base="https://api.siliconflow.cn/v1",
    ),
    "BAILIAN": _ProviderSpec(
        code="bailian",
        label="阿里百炼",
        api_key_env="ALI_BAILIAN_API_KEY",
        api_base_env="ALI_BAILIAN_API_BASE",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "DEEPSEEK": _ProviderSpec(
        code="deepseek",
        label="DeepSeek",
        api_key_env="DEEPSEEK_API_KEY",
        api_base_env="DEEPSEEK_API_BASE",
        default_api_base="https://api.deepseek.com",
    ),
    "ZHONGZHAN_GPT": _ProviderSpec(
        code="zhongzhan_gpt",
        label="中转站 A",
        api_key_env="ZHONGZHAN_GPT_API_KEY",
        api_base_env="ZHONGZHAN_GPT_BASE_URL",
        default_api_base="https://api.openai.com/v1",
    ),
    "ZHONGZHAN_CLAUDE": _ProviderSpec(
        code="zhongzhan_claude",
        label="中转站 B",
        api_key_env="ZHONGZHAN_CLAUDE_API_KEY",
        api_base_env="ZHONGZHAN_CLAUDE_BASE_URL",
        default_api_base="https://api.openai.com/v1",
    ),
}


def parse_model_and_effort(model_str: str) -> Tuple[str, Optional[str]]:
    """Split a model setting into its clean name and reasoning effort."""

    if not model_str:
        return "", None

    normalized = str(model_str).strip()
    match_paren = re.search(
        r"^(.*?)\((high|medium|low|xhigh|max|default)\)$",
        normalized,
        re.IGNORECASE,
    )
    if match_paren:
        return match_paren.group(1).strip(), match_paren.group(2).lower()

    if ":" in normalized:
        model_name, potential_effort = normalized.rsplit(":", 1)
        potential_effort = potential_effort.strip().lower()
        if potential_effort in _VALID_REASONING_EFFORTS:
            return model_name.strip(), potential_effort

    return normalized, None


def resolve_text_provider(
    model_config: str,
    environ: Optional[Mapping[str, str]] = None,
    *,
    parse_effort: bool = True,
) -> TextProviderConfig:
    """Resolve a saved model setting without performing any side effects.

    ``parse_effort=False`` is available for older call sites that historically
    treated an effort suffix as part of the literal model name.
    """

    environment = os.environ if environ is None else environ
    raw_model = str(model_config or "").strip()
    prefix = None
    model_name = raw_model
    if "/" in raw_model:
        prefix, model_name = raw_model.split("/", 1)
        prefix = prefix.upper()
        spec = _PROVIDER_SPECS.get(prefix)
        if spec is None:
            # Preserve the previous route behavior for an unknown explicit
            # prefix: report an unconfigured provider instead of guessing.
            return TextProviderConfig(
                provider_code="unknown",
                provider_label="DeepSeek",
                api_key_env="",
                api_key=None,
                api_base=None,
                model_name=model_name,
                reasoning_effort=None,
                raw_model=raw_model,
            )
    elif "qwen" in raw_model.lower():
        spec = _PROVIDER_SPECS["BAILIAN"]
    else:
        spec = _PROVIDER_SPECS["DEEPSEEK"]

    # Keep the legacy order: provider-specific aliases were applied before the
    # optional effort suffix was removed by the solve route.
    if spec.code == "bailian" and model_name == "qwen3.7-max":
        model_name = "qwen-max"

    if parse_effort:
        model_name, reasoning_effort = parse_model_and_effort(model_name)
    else:
        reasoning_effort = None

    api_base = spec.default_api_base
    if spec.api_base_env:
        api_base = environment.get(spec.api_base_env, spec.default_api_base)

    return TextProviderConfig(
        provider_code=spec.code,
        provider_label=spec.label,
        api_key_env=spec.api_key_env,
        api_key=environment.get(spec.api_key_env),
        api_base=api_base,
        model_name=model_name,
        reasoning_effort=reasoning_effort,
        raw_model=raw_model,
    )


def _first_configured(
    environment: Mapping[str, str], *names: str, default: str = ""
) -> str:
    for name in names:
        value = environment.get(name)
        if value:
            return value
    return default


def resolve_ocr_provider(
    engine: str,
    environ: Optional[Mapping[str, str]] = None,
) -> MultimodalProviderConfig:
    """Resolve one OCR engine and its engine-specific model setting."""

    environment = os.environ if environ is None else environ
    normalized_engine = str(engine or "siliconflow").strip().lower()

    if normalized_engine in {"ali_bailian", "bailian"}:
        return MultimodalProviderConfig(
            provider_code="bailian",
            provider_label="阿里云百炼",
            api_key_env="ALI_BAILIAN_API_KEY",
            api_key=environment.get("ALI_BAILIAN_API_KEY"),
            # Preserve the OCR flow's historical fixed compatible-mode URL.
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_name=environment.get("ALI_BAILIAN_OCR_MODEL", "qwen3-vl-flash"),
            reasoning_effort=None,
            supports_image_input=True,
            raw_config=normalized_engine,
        )

    if normalized_engine == "zhongzhan_claude":
        model_name, reasoning_effort = parse_model_and_effort(
            environment.get("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
        )
        return MultimodalProviderConfig(
            provider_code="zhongzhan_claude",
            provider_label="中转站 B",
            api_key_env="ZHONGZHAN_CLAUDE_API_KEY",
            api_key=environment.get("ZHONGZHAN_CLAUDE_API_KEY"),
            api_base=environment.get(
                "ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1"
            ),
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            supports_image_input=True,
            raw_config=normalized_engine,
        )

    if normalized_engine in {"zhongzhan", "zhongzhan_gpt"}:
        model_name, reasoning_effort = parse_model_and_effort(
            _first_configured(
                environment,
                "ZHONGZHAN_GPT_OCR_MODEL",
                "ZHONGZHAN_OCR_MODEL",
                default="gpt-4o",
            )
        )
        return MultimodalProviderConfig(
            provider_code="zhongzhan_gpt",
            provider_label="中转站 A",
            api_key_env="ZHONGZHAN_GPT_API_KEY",
            api_key=_first_configured(
                environment, "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_API_KEY"
            ),
            api_base=_first_configured(
                environment,
                "ZHONGZHAN_GPT_BASE_URL",
                "ZHONGZHAN_BASE_URL",
                default="https://api.openai.com/v1",
            ),
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            supports_image_input=True,
            raw_config=normalized_engine,
        )

    return MultimodalProviderConfig(
        provider_code="siliconflow",
        provider_label="SiliconFlow",
        api_key_env="SILICONFLOW_API_KEY",
        api_key=environment.get("SILICONFLOW_API_KEY"),
        api_base="https://api.siliconflow.cn/v1",
        model_name=environment.get(
            "SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct"
        ),
        reasoning_effort=None,
        supports_image_input=True,
        raw_config=normalized_engine,
    )


def resolve_ocr_fallbacks(
    preferred_engine: str,
    environ: Optional[Mapping[str, str]] = None,
) -> List[MultimodalProviderConfig]:
    """Return configured PDF OCR providers in deterministic fallback order."""

    environment = os.environ if environ is None else environ
    preferred = str(preferred_engine or "siliconflow").strip().lower()
    if preferred == "siliconflow":
        engine_order = ["siliconflow", "ali_bailian", "zhongzhan_gpt"]
    elif preferred in {"ali_bailian", "bailian"}:
        engine_order = ["ali_bailian", "siliconflow", "zhongzhan_gpt"]
    elif preferred == "zhongzhan_claude":
        engine_order = [
            "zhongzhan_claude",
            "siliconflow",
            "ali_bailian",
            "zhongzhan_gpt",
        ]
    else:
        engine_order = ["zhongzhan_gpt", "siliconflow", "ali_bailian"]

    providers = [resolve_ocr_provider(name, environment) for name in engine_order]
    return [provider for provider in providers if provider.api_key and provider.api_key.strip()]


def resolve_draw_provider(
    model_config: str,
    environ: Optional[Mapping[str, str]] = None,
) -> MultimodalProviderConfig:
    """Resolve the configured TikZ drawing/correction provider."""

    environment = os.environ if environ is None else environ
    raw_config = str(model_config or "Qwen/Qwen3-VL-32B-Instruct").strip()

    if raw_config.startswith("BAILIAN/"):
        provider_code = "bailian"
        provider_label = "阿里百炼"
        key_env = "ALI_BAILIAN_API_KEY"
        api_key = environment.get(key_env)
        api_base = environment.get(
            "ALI_BAILIAN_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model_name = raw_config.split("/", 1)[1]
        if model_name == "qwen3.7-max":
            model_name = "qwen-max"
        force_multimodal = True
    elif raw_config.startswith("ZHONGZHAN_GPT/") or raw_config.startswith(
        "ZHONGZHAN/"
    ):
        provider_code = "zhongzhan_gpt"
        provider_label = "中转站 A"
        key_env = "ZHONGZHAN_GPT_API_KEY"
        api_key = _first_configured(
            environment, "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_API_KEY"
        )
        api_base = _first_configured(
            environment,
            "ZHONGZHAN_GPT_BASE_URL",
            "ZHONGZHAN_BASE_URL",
            default="https://api.openai.com/v1",
        )
        model_name = raw_config.split("/", 1)[1]
        force_multimodal = True
    elif raw_config.startswith("ZHONGZHAN_CLAUDE/"):
        provider_code = "zhongzhan_claude"
        provider_label = "中转站 B"
        key_env = "ZHONGZHAN_CLAUDE_API_KEY"
        api_key = environment.get(key_env)
        api_base = environment.get(
            "ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1"
        )
        model_name = raw_config.split("/", 1)[1]
        force_multimodal = True
    else:
        provider_code = "siliconflow"
        provider_label = "SiliconFlow"
        key_env = "SILICONFLOW_API_KEY"
        api_key = environment.get(key_env)
        api_base = "https://api.siliconflow.cn/v1"
        if raw_config.startswith("SILICONFLOW/"):
            model_name = raw_config.split("/", 1)[1]
        else:
            model_name = raw_config
        force_multimodal = False

    supports_image_input = force_multimodal or any(
        marker in model_name.lower() for marker in ("vl", "thinking")
    )
    return MultimodalProviderConfig(
        provider_code=provider_code,
        provider_label=provider_label,
        api_key_env=key_env,
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        reasoning_effort=None,
        supports_image_input=supports_image_input,
        raw_config=raw_config,
    )
