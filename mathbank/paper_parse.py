"""Validation and source-answer handling for paper splitting responses."""

from typing import Any

from mathbank.ai_json import parse_ai_json


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
