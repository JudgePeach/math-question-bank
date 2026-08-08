"""Pure helpers for resolving text-model provider configuration.

This module deliberately does not send HTTP requests.  It only turns the model
value stored by the settings UI into a provider, credential, endpoint, clean
model name, and optional reasoning effort.
"""

from dataclasses import dataclass, field
import os
import re
from typing import Mapping, Optional, Tuple


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
