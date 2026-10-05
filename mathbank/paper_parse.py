"""Validation and source-answer handling for paper splitting responses."""

from copy import deepcopy
from typing import Any, Callable

from mathbank.ai_json import parse_ai_json
from mathbank.ai_stream import reserve_stream_attempts, receive_completion
from mathbank.ai_providers import supports_pdf_split_stream
from mathbank.document_requests import DOCUMENT_AI_TIMEOUT_SECONDS
from mathbank.pdf_vision_request import PDFVisionResponseError, collected_usage, request_pdf_vision


PAPER_SPLIT_TIMEOUT_SECONDS = DOCUMENT_AI_TIMEOUT_SECONDS
PAPER_SPLIT_CACHE_MAX_CHARACTERS = 500_000


def request_pdf_paper_completion(
    provider, payload: dict, *, post: Callable, raw_markdown: str = "",
    diagnostics: dict | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
    report_attempt: Callable[[dict], None] = lambda _event: None,
) -> list[dict[str, Any]]:
    """Retry only the PDF's prepared text request; never repeat page extraction."""
    report = diagnostics if diagnostics is not None else {}
    if not provider.api_key or not provider.chat_completions_url:
        raise ValueError("PDF 试卷拆题所用的模型服务未配置。")
    split_report = {"timeout_seconds": PAPER_SPLIT_TIMEOUT_SECONDS, "max_attempts": 2,
                    "calls": 0, "retries": 0, "attempts": [], "usage": {}, "usage_complete": False}
    report["pdf_splitting"] = split_report
    attempt_metadata = {}
    use_stream = supports_pdf_split_stream(provider) if hasattr(provider, "provider_code") else False
    if use_stream:
        payload = {**payload, "stream": True}
    stream_observation = {"http_started": False}

    def record(event):
        nonlocal attempt_metadata, stream_observation
        if event["status"] == "running":
            attempt_metadata = {}
            stream_observation = {"http_started": False}
        if use_stream:
            event["http_started"] = stream_observation["http_started"]
        if "output_characters" in attempt_metadata:
            event["output_characters"] = attempt_metadata["output_characters"]
        previous = next((item for item in split_report["attempts"] if item["attempt"] == event["attempt"]), None)
        if previous is None:
            split_report["attempts"].append(deepcopy(event))
        else:
            previous.update(deepcopy(event))
        if use_stream:
            current = split_report["attempts"][-1]
            if current.get("stream_usage") and not event.get("usage"):
                current["usage"] = dict(current["stream_usage"])
        split_report["attempts_started"] = len(split_report["attempts"])
        split_report["calls"] = (sum(item.get("http_started", False) for item in split_report["attempts"])
                                 if use_stream else len(split_report["attempts"]))
        split_report["retries"] = sum(item["attempt"] > 1 and (not use_stream or item.get("http_started", False))
                                      for item in split_report["attempts"])
        split_report["usage"] = collected_usage(split_report["attempts"])
        split_report["usage_complete"] = all(all(key in item["usage"] for key in
                                                ("prompt_tokens", "completion_tokens", "total_tokens"))
                                            and item.get("stream_complete", True)
                                            for item in split_report["attempts"])
        report_attempt(event)

    def validate(body):
        nonlocal attempt_metadata
        attempt_metadata = {}
        choices = body.get("choices") if isinstance(body, dict) else None
        if (isinstance(choices, list) and choices and isinstance(choices[0], dict)
                and choices[0].get("finish_reason") == "content_filter"):
            raise PDFVisionResponseError("PDF 试卷拆题被服务方过滤，未接受结果。", retryable=False)
        if use_stream and (not isinstance(body, dict) or not body.get("_stream_complete", False)
                           or body.get("_stream_error")):
            raise PDFVisionResponseError("PDF 试卷流式响应未完整stop并收到DONE，未采用不完整结果。")
        questions = parse_paper_completion(body, raw_markdown=raw_markdown, diagnostics=attempt_metadata)
        return {"questions": questions, "parse_diagnostics": attempt_metadata}

    def run(request_post):
        return request_pdf_vision(
            provider, payload, post=request_post, validate=validate,
            label="PDF 试卷拆题", stage="paper_split", timeout_seconds=PAPER_SPLIT_TIMEOUT_SECONDS,
            check_cancelled=check_cancelled, report_attempt=record,
        )

    if use_stream:
        # Reserving precedes the shared helper's attempt/POST accounting. Each
        # daemon keeps its own permit until public close eventually completes.
        with reserve_stream_attempts(2, check_cancelled=check_cancelled,
                                     timeout_seconds=PAPER_SPLIT_TIMEOUT_SECONDS) as permits:
            pending = iter(permits)
            def stream_progress(stats):
                current = split_report["attempts"][-1] if split_report["attempts"] else None
                if current is not None:
                    current.update(stats)
                    if "stream_usage" in stats:
                        current["usage"] = dict(stats["stream_usage"])
                    current["http_started"] = stream_observation["http_started"]
                    split_report["calls"] = sum(item.get("http_started", False) for item in split_report["attempts"])
                    split_report["retries"] = sum(item["attempt"] > 1 and item.get("http_started", False)
                                                  for item in split_report["attempts"])
                    report_attempt({**current, "stream_progress": True})
            def stream_post(config, data, **kwargs):
                return receive_completion(config, data, post=post, permit=next(pending),
                                          timeout_seconds=kwargs["timeout"], check_cancelled=check_cancelled,
                                          progress=stream_progress, observation=stream_observation)
            result = run(stream_post)
    else:
        result = run(post)
    report.update(result["parse_diagnostics"])
    # The aggregate belongs to the split request, independent of page vision.
    report["usage"] = split_report["usage"]
    return result["questions"]


def parse_paper_completion(
    response: dict[str, Any],
    *,
    raw_markdown: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate completion boundaries before accepting any question candidates.

    Keep only bounded, content-free response metadata in task diagnostics.
    A syntactically valid but truncated JSON object is still incomplete.
    """
    report = diagnostics if diagnostics is not None else {}
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("AI 未返回有效的拆题结果。")
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    report["finish_reason"] = str(finish_reason or "unknown")[:64]
    message = choice.get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    report["output_characters"] = len(raw) if isinstance(raw, str) else 0
    usage = response.get("usage")
    if isinstance(usage, dict):
        report["usage"] = {
            key: usage[key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(key), int) and not isinstance(usage[key], bool)
        }
    if finish_reason in ("length", "max_tokens", "max_output_tokens"):
        raise ValueError("AI 拆题输出被截断，结果可能漏题。请分段导入或调整模型后重试。")
    if finish_reason in ("content_filter", "tool_calls", "function_call"):
        raise ValueError("AI 未正常完成拆题，未接收不完整的题目结果。")
    parsed = parse_ai_json(raw, raw_markdown=raw_markdown)
    if isinstance(parsed, dict):
        if "questions" in parsed:
            questions = parsed["questions"]
        elif "data" in parsed:
            questions = parsed["data"]
        else:
            questions = next((value for value in parsed.values() if isinstance(value, list)), [parsed])
    else:
        questions = parsed
    if not isinstance(questions, list) or not questions or not all(isinstance(q, dict) for q in questions):
        raise ValueError("AI 未返回有效的题目对象列表。")
    for index, question in enumerate(questions, start=1):
        if not isinstance(question.get("content"), str) or not question["content"].strip():
            raise ValueError(f"AI 返回的第 {index} 题缺少有效题干，已停止导入以避免静默漏题。")
        if question.get("answer_markdown") is None:
            question["answer_markdown"] = ""
        elif not isinstance(question.get("answer_markdown", ""), str):
            raise ValueError(f"AI 返回的第 {index} 题答案格式无效。")
        if not isinstance(question.get("referenced_images"), list):
            question["referenced_images"] = []
        else:
            # Optional model metadata must not discard otherwise valid text or
            # inline images during later asset bookkeeping. Do not guess paths
            # from objects/numbers; source reconciliation still checks markup.
            question["referenced_images"] = list(dict.fromkeys(
                item.strip() for item in question["referenced_images"]
                if isinstance(item, str) and item.strip()
            ))
        if not isinstance(question.get("source"), str):
            question["source"] = ""
        # The model cannot assert that a source check or teacher review passed.
        question.pop("source_review", None)
    return questions


def finalize_source_answers(questions: list[dict[str, Any]], source: str) -> None:
    """Retain uncertain answer candidates for review, after source reconciliation.

    A missing origin marker must not erase source formulas before they can be
    compared. It also must not promote a generated answer to an original one.
    """
    marker = "[EXTRACTED_ORIGINAL]"
    for question in questions:
        answer = question.get("answer_markdown") or ""
        question["answer_markdown"] = answer.replace(marker, "").strip()
        if not answer.strip() or marker in answer:
            continue
        review = question.setdefault("source_review", {})
        review["required"] = True
        reason = "答案未注明原卷来源，可能是模型补写，请对照原文确认。"
        reasons = review.setdefault("reasons", [])
        if reason not in reasons:
            reasons.append(reason)
        if not review.get("source_excerpt") or question["answer_markdown"] not in review["source_excerpt"]:
            # The question's source fragment may exclude an answer section at
            # the end of the paper. Keep that evidence available for review.
            review["source_excerpt"] = source
