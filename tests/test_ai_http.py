from unittest.mock import MagicMock, patch

import pytest
import requests

from mathbank.ai_http import (
    AIProviderHTTPError,
    post_chat_completion,
    robust_request_get,
    robust_request_post,
)
from mathbank.ai_providers import resolve_text_provider


def test_post_chat_completion_builds_common_request():
    provider = resolve_text_provider(
        "ZHONGZHAN_GPT/gpt-5.6-sol",
        {
            "ZHONGZHAN_GPT_API_KEY": "secret-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://transit.example/v1/",
        },
    )
    response = MagicMock(status_code=200)

    with patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = post_chat_completion(
            provider,
            {"model": "gpt-5.6-sol", "messages": []},
            timeout=45,
        )

    assert result is response
    mock_post.assert_called_once_with(
        "https://transit.example/v1/chat/completions",
        headers={
            "Authorization": "Bearer secret-key",
            "Content-Type": "application/json",
        },
        json={"model": "gpt-5.6-sol", "messages": []},
        timeout=45,
    )


def test_post_chat_completion_raises_consistent_http_error():
    provider = resolve_text_provider(
        "DEEPSEEK/deepseek-chat",
        {"DEEPSEEK_API_KEY": "secret-key"},
    )
    response = MagicMock(status_code=429, text="rate limited")

    with patch("mathbank.ai_http.robust_request_post", return_value=response):
        with pytest.raises(AIProviderHTTPError) as exc_info:
            post_chat_completion(
                provider,
                {"model": "deepseek-chat"},
                timeout=30,
                provider_name="DeepSeek (DEEPSEEK_API_KEY)",
            )

    assert str(exc_info.value) == (
        "DeepSeek (DEEPSEEK_API_KEY) 接口错误: "
        "HTTP 429, 内容: rate limited"
    )


@pytest.mark.parametrize(
    ("request_helper", "request_target"),
    [
        (robust_request_post, "mathbank.ai_http.requests.post"),
        (robust_request_get, "mathbank.ai_http.requests.get"),
    ],
)
def test_domestic_provider_requests_bypass_proxies_immediately(
    request_helper, request_target
):
    response = MagicMock(status_code=200)

    with patch(request_target, return_value=response) as request_mock:
        result = request_helper("https://dashscope.aliyuncs.com/api", timeout=10)

    assert result is response
    request_mock.assert_called_once_with(
        "https://dashscope.aliyuncs.com/api",
        timeout=10,
        proxies={"http": None, "https": None},
    )


def test_external_request_retries_once_without_proxies():
    response = MagicMock(status_code=200)

    with patch(
        "mathbank.ai_http.requests.post",
        side_effect=[requests.exceptions.ConnectionError("proxy failed"), response],
    ) as request_mock:
        result = robust_request_post("https://transit.example/v1", timeout=10)

    assert result is response
    assert request_mock.call_count == 2
    retry_kwargs = request_mock.call_args_list[1].kwargs
    assert retry_kwargs["proxies"] == {"http": None, "https": None}
