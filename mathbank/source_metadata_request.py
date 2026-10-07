"""One bounded cloud request annotates locally verified Word source bodies."""
from __future__ import annotations

import hashlib
from typing import Callable

from mathbank.source_review_results import parse_review_response as strict_json_loads
from mathbank.ai_providers import apply_model_thinking_policy
from mathbank.paper_parse import PAPER_SPLIT_TIMEOUT_SECONDS
from mathbank.pdf_vision_request import completion_content
from mathbank.source_metadata import (
    SourceMetadataContractError,
    apply_source_metadata_response,
    build_source_metadata_messages,
    source_metadata_diagnostics,
)
from mathbank.task_manager import TaskCancelled


def request_word_source_metadata(
    plan: dict, curriculum: dict, *, provider, post: Callable,
    diagnostics: dict, normalize_fillin: Callable[[str], str],
    check_cancelled: Callable[[], None] = lambda: None,
) -> list[dict]:
    """Send at most one POST; the caller owns any bounded full-source fallback.

    Original question/answer bodies and the private certificate never depend on
    returned prose. The ordinary source comparison still runs after this stage.
    """
    report = source_metadata_diagnostics(plan)
    report.update(status="preparing", calls=0, usage=None)
    diagnostics["word_source_metadata"] = report
    check_cancelled()
    if not provider.api_key or not provider.chat_completions_url:
        raise SourceMetadataContractError("拆题服务未配置，沿用原整卷拆题入口。")
    messages = build_source_metadata_messages(plan, curriculum)
    payload = apply_model_thinking_policy({
        "model": provider.model_name, "messages": messages,
        "response_format": {"type": "json_object"}, "temperature": 0.2,
        "max_tokens": 65536,
    }, provider=provider, task="parse")
    report.update(status="requesting", calls=1, model=provider.model_name)
    try:
        response = post(provider, payload, timeout=PAPER_SPLIT_TIMEOUT_SECONDS,
                        check_status=False, retry_connection=False, allow_redirects=False)
        check_cancelled()
        report["http_status"] = response.status_code
        if response.status_code != 200:
            raise SourceMetadataContractError(f"元数据请求失败（HTTP {response.status_code}）。")
        body = response.json()
        usage = body.get("usage") if isinstance(body, dict) else None
        report["usage"] = ({key: usage[key] for key in
                            ("prompt_tokens", "completion_tokens", "total_tokens")
                            if type(usage.get(key)) is int and usage[key] >= 0}
                           if isinstance(usage, dict) else None)
        raw = completion_content(body, "Word 来源元数据", max_chars=500000)
        report.update(response_characters=len(raw),
                      response_sha256=hashlib.sha256(raw.encode()).hexdigest())
        parsed = strict_json_loads(raw)
        questions = apply_source_metadata_response(parsed, plan, curriculum,
                                                     normalize_fillin=normalize_fillin)
        report.update(status="used", question_count=len(questions))
        return questions
    except Exception:
        try:
            check_cancelled()
        except TaskCancelled:
            report["status"] = "cancelled"
            raise
        # Content or provider exception text must not enter task diagnostics.
        report["status"] = "fallback"
        raise
