"""Pure helpers for resolving text-model provider configuration.

This module deliberately does not send HTTP requests.  It only turns the model
value stored by the settings UI into a provider, credential, endpoint, clean
model name, and optional reasoning effort.
"""

from dataclasses import dataclass, field
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union
from urllib.parse import urlsplit


_VALID_REASONING_EFFORTS = frozenset(
    {"high", "medium", "low", "xhigh", "max", "default"}
)


def _normalize_deepseek_model(model_name: str) -> str:
    """Use the current official Flash ID without rewriting other providers."""
    if model_name.lower() in {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
        return "deepseek-flash"
    return model_name


def _deepseek_supports_images(model_name: str) -> bool:
    return _normalize_deepseek_model(model_name).lower() == "deepseek-flash"


def build_chat_completions_url(api_base: Optional[str]) -> Optional[str]:
    """Normalize an OpenAI-compatible base URL to its chat endpoint."""

    normalized_base = str(api_base or "").strip().rstrip("/")
    if not normalized_base:
        return None
    if normalized_base.lower().endswith("/chat/completions"):
        return normalized_base
    return f"{normalized_base}/chat/completions"


def inject_reasoning_effort(
    payload: Mapping[str, Any], reasoning_effort: Optional[str]
) -> Dict[str, Any]:
    """Return a payload copy with one allowlisted reasoning effort applied.

    ``default`` and invalid values deliberately leave provider-specific payload
    behavior untouched.  The input mapping is never mutated.
    """

    result = dict(payload)
    # Parsed efforts are already trimmed.  Do not silently normalize surrounding
    # whitespace here: control characters must never reach an outbound payload.
    normalized_effort = str(reasoning_effort or "").lower()
    if (
        normalized_effort not in _VALID_REASONING_EFFORTS
        or normalized_effort == "default"
    ):
        return result
    result["reasoning_effort"] = normalized_effort
    return result


_BAILIAN_NO_THINKING_TASKS = frozenset(
    {"classify", "parse", "paper_selection", "latex_diagnostic"}
)


def apply_bailian_thinking_policy(
    payload: Mapping[str, Any],
    *,
    provider_code: str,
    model_name: str,
    task: str,
    thinking_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply task-specific reasoning parameters only to current Bailian Qwen.

    Other providers and legacy/custom Bailian models are returned unchanged so
    this policy cannot alter DeepSeek, SiliconFlow, transit, or saved legacy
    behavior. Qwen3.7 uses ``thinking_budget`` while Qwen3.8 Max uses
    ``reasoning_effort``; the two controls are never sent together.
    """

    result = dict(payload)
    normalized_provider = str(provider_code or "").strip().lower()
    normalized_model = str(model_name or "").strip().lower()
    is_qwen37 = normalized_model.startswith("qwen3.7-")
    is_qwen38_max = normalized_model == "qwen3.8-max" or normalized_model.startswith(
        "qwen3.8-max-"
    )
    if normalized_provider != "bailian" or not (is_qwen37 or is_qwen38_max):
        return result

    normalized_task = str(task or "").strip().lower()
    result.pop("reasoning_effort", None)
    result.pop("thinking_budget", None)
    result.pop("max_completion_tokens", None)
    # Current Qwen APIs deprecate max_tokens; structured JSON tasks should not
    # impose the old answer-only cap because it can truncate valid JSON.
    result.pop("max_tokens", None)

    if normalized_task == "ocr":
        result["enable_thinking"] = False
        result["max_completion_tokens"] = 16384
        return result

    if normalized_task in _BAILIAN_NO_THINKING_TASKS:
        result["enable_thinking"] = False
        return result

    if normalized_task == "solve":
        enabled = bool(thinking_enabled)
        result["enable_thinking"] = enabled
        result["max_completion_tokens"] = 32768 if enabled else 16384
        if enabled:
            if is_qwen38_max:
                result["reasoning_effort"] = "medium"
            else:
                result["thinking_budget"] = 16384
        return result

    if normalized_task == "draw":
        result["enable_thinking"] = True
        if is_qwen38_max:
            # Medium effort maps to a 16K reasoning budget, so reserve another
            # 16K for the emitted TikZ source itself.
            result["reasoning_effort"] = "medium"
            result["max_completion_tokens"] = 32768
        else:
            result["thinking_budget"] = 8192
            result["max_completion_tokens"] = 16384
        return result

    raise ValueError(f"未知的百炼思考策略任务: {task}")


def apply_model_thinking_policy(
    payload: Mapping[str, Any],
    *,
    provider: "Union[TextProviderConfig, MultimodalProviderConfig]",
    task: str,
    thinking_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build thinking parameters by provider AND model, without mutating input.

    An effort selection is not a universal enable_thinking switch. Unknown
    transit aliases keep their model ID and explicit effort unchanged; their
    gateway, rather than a guessed vendor protocol, defines their semantics.
    """
    result = inject_reasoning_effort(payload, provider.reasoning_effort)
    model = provider.model_name.lower()
    code = provider.provider_code
    host = urlsplit(provider.api_base or "").hostname
    explicit = provider.reasoning_effort in (_VALID_REASONING_EFFORTS - {"default"})
    enabled = bool(thinking_enabled) if task == "solve" else task == "draw"
    if explicit:
        enabled = True

    if code == "bailian":
        # Preserve the established task policy, including OCR/parse off and
        # bounded solve/draw budgets. Do not enable unknown Bailian models.
        return apply_bailian_thinking_policy(
            result, provider_code=code, model_name=model, task=task,
            thinking_enabled=thinking_enabled,
        )

    if _is_siliconflow_qwen38_27b(provider):
        # This exact model defaults to thinking on SiliconFlow. Recognition,
        # splitting and classification must emit their requested result rather
        # than spending an implicit, unbounded reasoning budget first.
        qwen_enabled = bool(thinking_enabled) if task == "solve" else task == "draw"
        for key in ("thinking", "reasoning_effort"):
            result.pop(key, None)
        result["enable_thinking"] = qwen_enabled
        if qwen_enabled:
            default_budget = 8192 if task == "draw" else 16384
            budget = result.get("thinking_budget")
            if type(budget) is not int or not 128 <= budget <= 32768:
                budget = default_budget
            result["thinking_budget"] = budget
        else:
            result.pop("thinking_budget", None)
        return result

    if code == "siliconflow" or host in {"api.siliconflow.cn", "api.siliconflow.com"}:
        if re.fullmatch(r"(?:pro/)?qwen/qwen3-vl-(?:8b|32b)-instruct", model):
            for key in ("enable_thinking", "thinking", "thinking_budget", "reasoning_effort"):
                result.pop(key, None)
        elif re.fullmatch(r"(?:pro/)?deepseek-ai/deepseek-v(?:4(?:-pro|-flash)?|3\.2)(?:-\d+)?", model):
            result.pop("thinking", None)
            result["enable_thinking"] = enabled
            if not enabled:
                result.pop("reasoning_effort", None)
                result.pop("thinking_budget", None)
            elif "deepseek-v4" in model:
                effort = result.get("reasoning_effort", "max" if task == "solve" else "high")
                result["reasoning_effort"] = "max" if effort in {"max", "xhigh"} else "high"
            else:
                # V3.2 has the switch but not SiliconFlow's V4 effort control.
                result.pop("reasoning_effort", None)
        return result

    if (code == "deepseek" or host == "api.deepseek.com") and re.fullmatch(
        r"deepseek-(?:flash|v4-(?:pro|flash(?:-vision-exp)?))(?:-\d+)?", model
    ):
        result.pop("enable_thinking", None)
        result.pop("thinking_budget", None)
        result["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if not enabled:
            result.pop("reasoning_effort", None)
            result.pop("top_p", None)
        elif result.get("reasoning_effort") in {"medium", "xhigh"}:
            result["reasoning_effort"] = "high"
        if enabled:
            for key in ("temperature", "presence_penalty", "frequency_penalty"):
                result.pop(key, None)
        return result

    is_gpt_reasoning = bool(re.match(r"^(?:gpt-[56](?:[.-]|$)|o[134](?:-|$))", model))
    if is_gpt_reasoning or model.startswith("gpt-4") or host == "api.openai.com":
        for key in ("enable_thinking", "thinking", "thinking_budget"):
            result.pop(key, None)
        if is_gpt_reasoning:
            if task == "solve" and not explicit:
                result["reasoning_effort"] = "high" if thinking_enabled else "medium"
            if "max_tokens" in result:
                limit = result.pop("max_tokens")
                result.setdefault("max_completion_tokens", limit)
            # Sampling controls are unnecessary for these reasoning requests.
            for key in ("temperature", "top_p"):
                result.pop(key, None)
            if re.match(r"^gpt-6-astra(?:-|$)", model):
                for key in ("logprobs", "top_logprobs"):
                    result.pop(key, None)
        elif model.startswith("gpt-4"):
            result.pop("reasoning_effort", None)
        return result

    # Gemini/Claude and other transit models receive no invented thinking
    # switch. In particular, '-high' in a model alias is not parsed or renamed.
    return result


def _is_siliconflow_qwen38_27b(provider) -> bool:
    """Do not infer this model's API contract from a vendor-like hostname."""
    return provider.provider_code == "siliconflow" and bool(
        re.fullmatch(r"(?:pro/)?qwen/qwen3\.8-27b", provider.model_name.lower())
    )


def supports_pdf_split_stream(provider) -> bool:
    """The streaming PDF adapter has been verified for this exact contract."""
    return _is_siliconflow_qwen38_27b(provider)


def apply_structured_output_policy(
    payload: Mapping[str, Any], *, provider, system_instruction: Optional[str] = None,
) -> Dict[str, Any]:
    """Enable verified JSON mode only for explicitly structured call sites."""
    result = dict(payload)
    if _is_siliconflow_qwen38_27b(provider):
        result.setdefault("response_format", {"type": "json_object"})
        messages = result.get("messages")
        if (system_instruction and isinstance(messages, list) and messages
                and not any(isinstance(message, Mapping) and message.get("role") == "system" for message in messages)):
            result["messages"] = [{"role": "system", "content": system_instruction}, *messages]
    return result


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

        return build_chat_completions_url(self.api_base)


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
    def chat_completions_url(self) -> Optional[str]:
        return build_chat_completions_url(self.api_base)


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
) -> TextProviderConfig:
    """Resolve a saved model setting without performing any side effects."""

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

    model_name, reasoning_effort = parse_model_and_effort(model_name)
    if spec.code == "deepseek":
        model_name = _normalize_deepseek_model(model_name)

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

    if normalized_engine == "deepseek":
        model_name, reasoning_effort = parse_model_and_effort(
            environment.get("DEEPSEEK_OCR_MODEL") or "deepseek-flash"
        )
        model_name = _normalize_deepseek_model(model_name)
        return MultimodalProviderConfig(
            provider_code="deepseek",
            provider_label="DeepSeek",
            api_key_env="DEEPSEEK_API_KEY",
            api_key=environment.get("DEEPSEEK_API_KEY"),
            api_base=environment.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com",
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            supports_image_input=_deepseek_supports_images(model_name),
            raw_config=normalized_engine,
        )

    if normalized_engine in {"ali_bailian", "bailian"}:
        return MultimodalProviderConfig(
            provider_code="bailian",
            provider_label="阿里云百炼",
            api_key_env="ALI_BAILIAN_API_KEY",
            api_key=environment.get("ALI_BAILIAN_API_KEY"),
            # Preserve the OCR flow's historical fixed compatible-mode URL.
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_name=environment.get("ALI_BAILIAN_OCR_MODEL", "qwen3.7-flash"),
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
    if preferred == "deepseek":
        engine_order = ["deepseek", "siliconflow", "ali_bailian", "zhongzhan_gpt"]
    elif preferred == "siliconflow":
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

    prefix, separator, configured_model = raw_config.partition("/")
    normalized_prefix = prefix.upper() if separator else ""
    if normalized_prefix == "ZHONGZHAN":
        normalized_prefix = "ZHONGZHAN_GPT"
    known_prefixes = {
        "DEEPSEEK",
        "BAILIAN",
        "SILICONFLOW",
        "ZHONGZHAN_GPT",
        "ZHONGZHAN_CLAUDE",
    }
    if normalized_prefix in known_prefixes:
        spec = _PROVIDER_SPECS[normalized_prefix]
        model_name = configured_model
    elif not separator and raw_config.lower().startswith("deepseek-"):
        spec = _PROVIDER_SPECS["DEEPSEEK"]
        model_name = raw_config
    else:
        spec = _PROVIDER_SPECS["SILICONFLOW"]
        model_name = raw_config

    model_name, reasoning_effort = parse_model_and_effort(model_name)
    if spec.code == "deepseek":
        model_name = _normalize_deepseek_model(model_name)
    key_env = spec.api_key_env
    api_key = environment.get(key_env)
    api_base = (
        environment.get(spec.api_base_env, spec.default_api_base)
        if spec.api_base_env
        else spec.default_api_base
    )
    if normalized_prefix == "ZHONGZHAN_GPT":
        api_key = _first_configured(
            environment, "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_API_KEY"
        )
        api_base = _first_configured(
            environment,
            "ZHONGZHAN_GPT_BASE_URL",
            "ZHONGZHAN_BASE_URL",
            default=spec.default_api_base,
        )

    force_multimodal = normalized_prefix in {
        "BAILIAN",
        "ZHONGZHAN_GPT",
        "ZHONGZHAN_CLAUDE",
    }

    supports_image_input = (
        _deepseek_supports_images(model_name) if spec.code == "deepseek"
        else force_multimodal or (
            spec.code == "siliconflow" and model_name.lower() == "qwen/qwen3.8-27b"
        ) or any(
            marker in model_name.lower() for marker in ("vl", "thinking")
        )
    )
    return MultimodalProviderConfig(
        provider_code=spec.code,
        provider_label=spec.label,
        api_key_env=key_env,
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        reasoning_effort=reasoning_effort,
        supports_image_input=supports_image_input,
        raw_config=raw_config,
    )
