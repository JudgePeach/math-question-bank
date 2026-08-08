"""Shared HTTP transport helpers for AI provider requests."""

from typing import Any, Dict, Optional, Protocol

import requests


class ChatProviderConfig(Protocol):
    provider_label: str
    api_key: Optional[str]
    credential_label: str
    chat_completions_url: Optional[str]


class AIProviderHTTPError(RuntimeError):
    """Raised when an AI provider returns a non-success HTTP response."""


def robust_request_post(url: str, **kwargs):
    """POST with the project's existing proxy-bypass retry behavior."""

    is_domestic = any(
        domain in url.lower() for domain in ("aliyuncs.com", "siliconflow")
    )
    if is_domestic and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}

    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.RequestException as exc:
        if kwargs.get("proxies") == {"http": None, "https": None}:
            raise
        print(
            f"[Robust Network] POST request to {url} failed: {str(exc)}. "
            "Retrying with proxies bypassed..."
        )
        retry_kwargs = kwargs.copy()
        retry_kwargs["proxies"] = {"http": None, "https": None}
        return requests.post(url, **retry_kwargs)


def robust_request_get(url: str, **kwargs):
    """GET with the project's existing proxy-bypass retry behavior."""

    is_domestic = any(
        domain in url.lower() for domain in ("aliyuncs.com", "siliconflow")
    )
    if is_domestic and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}

    try:
        return requests.get(url, **kwargs)
    except requests.exceptions.RequestException as exc:
        if kwargs.get("proxies") == {"http": None, "https": None}:
            raise
        print(
            f"[Robust Network] GET request to {url} failed: {str(exc)}. "
            "Retrying with proxies bypassed..."
        )
        retry_kwargs = kwargs.copy()
        retry_kwargs["proxies"] = {"http": None, "https": None}
        return requests.get(url, **retry_kwargs)


def build_bearer_headers(api_key: str) -> Dict[str, str]:
    """Build the common OpenAI-compatible authorization headers."""

    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def post_chat_completion(
    provider: ChatProviderConfig,
    payload: Dict[str, Any],
    *,
    timeout: float,
    stream: bool = False,
    check_status: bool = True,
    provider_name: Optional[str] = None,
):
    """Send one OpenAI-compatible chat completion request.

    Payload construction and response parsing remain the responsibility of the
    calling business flow.  Streaming callers can disable status checking so
    they can preserve their existing SSE error format.
    """

    if not provider.api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})")
    if not provider.chat_completions_url:
        raise ValueError(f"未配置对应的 API Base ({provider.provider_label})")

    request_kwargs = {
        "headers": build_bearer_headers(provider.api_key),
        "json": payload,
        "timeout": timeout,
    }
    if stream:
        request_kwargs["stream"] = True

    response = robust_request_post(
        provider.chat_completions_url,
        **request_kwargs,
    )
    if check_status and response.status_code != 200:
        display_name = provider_name or provider.provider_label
        raise AIProviderHTTPError(
            f"{display_name} 接口错误: HTTP {response.status_code}, 内容: {response.text}"
        )
    return response
