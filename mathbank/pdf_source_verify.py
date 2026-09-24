"""Bounded visual adjudication of source-position suspicions after local guards.

This optional step can clear only a single, narrow source-review reason. It
cannot repair content or override an actual formula, image, origin or condition
change. All results are checked and snapshot-bound before any review is updated.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
import re
import unicodedata
from typing import Callable

from mathbank import prompts
from mathbank.ai_http import post_chat_completion
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.asset_security import resolve_upload_asset
from mathbank.content_locks import (
    _NUMBER, _NUMERIC_LITERAL, _REFERENCE, _comparison_layout, _formulas, _image_positions, _math_key, _plain_key,
)
from mathbank.paths import TEST_UPLOADS_DIR, UPLOADS_DIR
from mathbank.task_manager import TaskCancelled


MAX_ITEMS = 8
MAX_PAGES = 4
MAX_ITEM_CHARS = 8000
MAX_TOTAL_CHARS = 24000
MAX_OUTPUT_TOKENS = 4096
MAX_RESPONSE_CHARS = 24000
MAX_EVIDENCE_CHARS = 400
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_CACHED_PAGE_CHARS = 50000
POSITION_REASON = "题干文字或公式位置与原文未能完整对应，请对照原文核对。"
_SKIPPED_REASONS = {
    "formula_difference": "公式内容、符号或顺序存在差异，或尚不能可靠核对。",
    "condition_difference": "数字、单位、量词或其他关键条件与原文不一致。",
    "source_evidence_missing": "缺少唯一、完整且一致的原文与页码证据。",
    "figure_risk": "存在配图引用、位置或裁剪风险，保留人工核对。",
    "budget_limit": "超过本次自动核验的题数、页数或文字额度。",
    "other_review_reason": "还存在其他原文或答案来源问题，保留人工核对。",
}
_DECISIONS = {"equivalent", "different", "uncertain"}
_CRITICAL_WORDS = (
    "当且仅当", "逆时针", "顺时针", "不存在", "不超过", "不低于", "不少于", "不多于", "不能", "至少", "至多", "最多", "最少",
    "大于", "小于", "等于", "超过", "低于", "任意", "所有", "存在", "唯一", "整数", "自然数", "实数",
    "有理数", "奇数", "偶数", "平方", "立方", "平行", "垂直", "最大", "最小", "相等", "不等", "严格",
    "递增", "递减", "单调", "总是", "少于", "多于", "不", "非", "无", "未", "没", "恰", "仅", "都", "各", "每",
    "全部", "全体", "部分", "任何", "任一", "某个", "某些", "某一", "一切", "均", "皆", "必", "一定", "可能",
    "充分", "必要", "充要", "只要", "只有", "如果", "假设", "当", "设", "有",
    "千米", "厘米", "毫米", "分米", "微米", "纳米", "米", "秒", "小时", "千克", "公斤", "克", "毫升", "升",
    "元", "角", "分", "吨", "公顷", "亩", "度",
    "且", "或", "若", "则", "正", "负", "增", "减", "上", "下", "左", "右", "倍", "半", "交", "切",
    "内", "外", "开", "闭",
)
_CRITICAL = re.compile(
    r"[0-9]+|[A-Za-z]+|[零〇一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟萬億兆]+|"
    + "|".join(re.escape(word) for word in sorted(_CRITICAL_WORDS, key=len, reverse=True))
    + r"|[+\-=<>%‰‱°×÷±∓√∞∈∉∪∩⊂⊆⊃⊇≤≥≠≈∥⊥∠△^/():\[\]]"
)
_IMAGES = re.compile(r"!\[[^\]]*\]\([^\n)]*\)|\\includegraphics(?:\[[^\]]*\])?\{[^{}]*\}")
_UNCERTAIN_TEXT = re.compile(r"\[插图待补|\[公式待核对|无法识别的公式|<\s*img\b", re.IGNORECASE)


class _VerificationError(ValueError):
    """Only these fixed local messages may be returned in an ordinary note."""


def _no_cancel() -> None:
    pass


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _plain_conditions(value: str, formulas: list) -> tuple:
    cursor, parts = 0, []
    for formula in formulas:
        parts.extend([value[cursor:formula.start], " MATHBANKFORMULASLOT "])
        cursor = formula.end
    parts.append(value[cursor:])
    prose = _comparison_layout(_IMAGES.sub("", "".join(parts)))
    heading = _NUMBER.match(prose)
    if heading:
        prose = prose[heading.end():]
    numbers = tuple(match.group().replace(r"\%", "%") for match in _NUMERIC_LITERAL.finditer(prose))
    prose = re.sub(r"\\(?:begin|end)\{(?:choices|enumerate|questions|question|solution|answer)\}", "", prose)
    # These symbols are intentionally excluded by the ordinary prose key. A
    # visual referee must not be allowed to erase their mathematical meaning.
    guarded = []
    for char in prose:
        special = (char in "_*'`·•′″‴⁗−{}|"
                   or (ord(char) > 127 and (unicodedata.category(char)[0] in "SMN"
                       or unicodedata.category(char) == "Pd"
                       or unicodedata.category(char)[0] == "L"
                       and not ("\u3400" <= char <= "\u9fff" or "\U00020000" <= char <= "\U000323af"))))
        # Interleave preserved symbols with the other condition tokens. Keeping
        # a separate symbol list would wrongly equate a_b+cd and ab+c_d.
        guarded.append(f"MATHBANKGUARDSYMBOL{ord(char):X}END" if special else char)
    return tuple(_CRITICAL.findall(_plain_key("".join(guarded)))), numbers


def _local_block_reason(source: str, output: str) -> str | None:
    if "MATHBANKGUARDSYMBOL" in source + output:
        return "source_evidence_missing"
    if _REFERENCE.search(source) or _REFERENCE.search(output):
        return "source_evidence_missing"
    uncertain = _UNCERTAIN_TEXT.search(source + "\n" + output)
    if uncertain:
        return "figure_risk" if "插图" in uncertain.group() or "img" in uncertain.group().lower() else "formula_difference"
    expected, actual = _formulas(source), _formulas(output)
    if [_math_key(item.formula) for item in expected] != [_math_key(item.formula) for item in actual]:
        return "formula_difference"
    if _image_positions(source, expected) != _image_positions(output, actual):
        return "figure_risk"
    # Unbalanced math delimiters must not disappear into ordinary prose keys.
    for value, spans in ((source, expected), (output, actual)):
        residue = value
        for span in reversed(spans):
            residue = residue[:span.start] + " " * (span.end - span.start) + residue[span.end:]
        residue = _comparison_layout(_IMAGES.sub("", residue))
        # The numeric atom grammar does not cover omitted-leading-zero forms.
        # Keep bare .5 / ．5 manual rather than letting prose cleanup erase the
        # point and accidentally certify 5. Delimited formulas were masked.
        if re.search(r"(?<!\d)[.．]\d+", residue):
            return "formula_difference"
        if re.search(r"(?<!\\)\$|\\[()[\]]|\\(?:begin|end)\{(?:equation|align|math|cases|array)", residue):
            return "formula_difference"
        # Undelimited mathematical macros have no trustworthy formula spans;
        # do not let punctuation cleanup flatten their argument boundaries.
        layout_commands = {"begin", "end", "item", "question", "qitem", "noindent", "fillin", "paren",
                           "textbf", "textit", "textrm", "emph"}
        if any(command not in layout_commands for command in re.findall(r"\\([A-Za-z]+)", residue)):
            return "formula_difference"
    return None if _plain_conditions(source, expected) == _plain_conditions(output, actual) else "condition_difference"


def _locally_eligible(source: str, output: str) -> bool:
    return _local_block_reason(source, output) is None


def _candidate(index: int, question: dict, diagnostics: dict) -> dict | None:
    if not isinstance(diagnostics, dict):
        return None
    review = question.get("source_review")
    if not isinstance(review, dict) or review.get("required") is not True:
        return None
    if review.get("reasons") != [POSITION_REASON]:
        return None
    output = question.get("content")
    if not isinstance(output, str) or not output.strip():
        return None
    all_matches = diagnostics.get("source_matches", [])
    review_items = diagnostics.get("pdf_review_items", [])
    if not isinstance(all_matches, list) or not isinstance(review_items, list):
        return None
    matches = [item for item in all_matches if isinstance(item, dict)
               and isinstance(item.get("question_index"), int) and not isinstance(item["question_index"], bool)
               and item["question_index"] == index and item.get("field") == "content"]
    items = [item for item in review_items if isinstance(item, dict)
             and isinstance(item.get("question_index"), int) and not isinstance(item["question_index"], bool)
             and item["question_index"] == index]
    if len(matches) != 1 or len(items) != 1:
        return None
    match, item = matches[0], items[0]
    if item.get("reasons") != [POSITION_REASON]:
        return None
    start, end = match.get("source_start"), match.get("source_end")
    if (not isinstance(start, int) or isinstance(start, bool) or start < 0
            or not isinstance(end, int) or isinstance(end, bool) or end <= start):
        return None
    if sum(isinstance(other, dict) and other.get("field") == "content"
           and other.get("source_start") == start and other.get("source_end") == end for other in all_matches) != 1:
        return None
    source, number, pages = match.get("source_excerpt"), match.get("source_number"), item.get("source_pages")
    if (not isinstance(source, str) or not source.strip() or source != review.get("source_excerpt")
            or end - start != len(source) or not _positive_int(number)
            or not _positive_int(item.get("source_number")) or item["source_number"] != number or not isinstance(pages, list)
            or not pages or any(not _positive_int(page) for page in pages) or len(set(pages)) != len(pages)
            or len(pages) > MAX_PAGES or len(source) + len(output) > MAX_ITEM_CHARS):
        return None
    if not _locally_eligible(source, output):
        return None
    heading = _NUMBER.match(source)
    if not heading or int(heading.group(1) or heading.group(2)) != number:
        return None
    return {"id": f"item_{index + 1:03d}", "question_index": index, "source_number": number,
            "source_pages": sorted(pages), "source_excerpt": source, "output": output}


def _skipped_entry(index: int, question: dict, diagnostics: dict, code: str | None = None) -> dict:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    all_matches, all_items = diagnostics.get("source_matches"), diagnostics.get("pdf_review_items")
    matches = ([item for item in all_matches if isinstance(item, dict)
               and type(item.get("question_index")) is int and item["question_index"] == index and item.get("field") == "content"]
               if isinstance(all_matches, list) else [])
    items = [item for item in all_items if isinstance(item, dict)
             and type(item.get("question_index")) is int and item["question_index"] == index] if isinstance(all_items, list) else []
    match = matches[0] if len(matches) == 1 else {}
    item = items[0] if len(items) == 1 else {}
    number = match.get("source_number")
    pages = item.get("source_pages")
    review = question.get("source_review")
    reasons = review.get("reasons", []) if isinstance(review, dict) else []
    reasons = reasons if isinstance(reasons, list) else []
    if code is None:
        if any(isinstance(reason, str) and any(word in reason for word in ("插图", "配图", "裁剪")) for reason in reasons):
            code = "figure_risk"
        elif reasons != [POSITION_REASON]:
            code = "other_review_reason"
        else:
            source, output = match.get("source_excerpt"), question.get("content")
            if isinstance(source, str) and isinstance(output, str):
                if len(source) + len(output) > MAX_ITEM_CHARS or isinstance(pages, list) and len(pages) > MAX_PAGES:
                    code = "budget_limit"
                else:
                    code = _local_block_reason(source, output)
            code = code or "source_evidence_missing"
    return {"question_index": index, "source_number": number if _positive_int(number) else None,
            "source_pages": sorted(set(pages)) if isinstance(pages, list) and all(_positive_int(page) for page in pages) else [],
            "code": code, "reason": _SKIPPED_REASONS[code]}


def _source_baseline(source_pages: list[dict], page_numbers: list[int]) -> dict:
    """Rebuild the exact first-pass source; never truncate or resend page text."""
    from mathbank.pdf_inspector_helper import merge_pdf_page_texts

    if (not isinstance(source_pages, list) or not source_pages or not isinstance(page_numbers, list)
            or len(source_pages) != len(page_numbers) or any(not _positive_int(page) for page in page_numbers)
            or len(set(page_numbers)) != len(page_numbers)):
        raise _VerificationError("首次页面识别缓存与原页范围不一致")
    page_marker = re.compile(r"<!-- MATHBANK_PDF_PAGE:(\d+) -->")
    chunks, ranges, cursor = [], [], 0
    for page, number in zip(source_pages, page_numbers):
        if (not isinstance(page, dict) or not _positive_int(page.get("page_number")) or page["page_number"] != number
                or not isinstance(page.get("origin"), str) or not page["origin"].strip()
                or not isinstance(page.get("markdown"), str) or len(page["markdown"]) > MAX_CACHED_PAGE_CHARS
                or not isinstance(page.get("figures"), list) or page.get("truncated") is True):
            raise _VerificationError("首次页面识别缓存缺失、不完整或超过核对范围")
        text = page["markdown"].strip()
        markers = page_marker.findall(text)
        if len(markers) > 1 or markers and markers != [str(number)]:
            raise _VerificationError("首次页面识别缓存中的页码标记不一致")
        chunks.append(page["markdown"])
        if not text:
            continue
        value = page_marker.sub("", text)
        if ranges:
            cursor += 2
        ranges.append((number, cursor, cursor + len(value), value))
        cursor += len(value)
    baseline = page_marker.sub("", merge_pdf_page_texts(chunks))
    return {"markdown": baseline, "ranges": ranges,
            "hash": hashlib.sha256(baseline.encode("utf-8")).hexdigest()}


def _cached_excerpt_matches(candidate: dict, diagnostics: dict, baseline: dict) -> bool:
    match = next(item for item in diagnostics["source_matches"]
                 if isinstance(item, dict) and type(item.get("question_index")) is int
                 and item["question_index"] == candidate["question_index"] and item.get("field") == "content")
    start, end = match["source_start"], match["source_end"]
    if baseline["markdown"][start:end] != candidate["source_excerpt"]:
        return False
    nonempty_pages = {number for number, _, _, text in baseline["ranges"] if text.strip()}
    actual_pages = {number for number, left, right, text in baseline["ranges"]
                    if max(start, left) < min(end, right)
                    and text[max(start, left) - left:min(end, right) - left].strip()}
    return bool(actual_pages) and actual_pages <= set(candidate["source_pages"]) <= nonempty_pages


def _snapshot(questions: list, diagnostics: dict, indices: list[int], source_pages=None) -> str:
    data = {"question_count": len(questions), "questions": [{"index": index, "content": questions[index].get("content"),
            "answer_markdown": questions[index].get("answer_markdown"), "source_review": questions[index].get("source_review")}
            for index in indices],
            "source_matches": diagnostics.get("source_matches"), "pdf_review_items": diagnostics.get("pdf_review_items"),
            "source_pages": source_pages}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _page_messages(page_urls: list, page_numbers: list, wanted: set[int]) -> list[dict]:
    if (not isinstance(page_urls, list) or not isinstance(page_numbers, list)
            or len(page_urls) != len(page_numbers) or any(not _positive_int(page) for page in page_numbers)
            or len(set(page_numbers)) != len(page_numbers) or not wanted <= set(page_numbers)):
        raise _VerificationError("原页图像与页码无法唯一对应")
    mapping = dict(zip(page_numbers, page_urls))
    messages = []
    for number in sorted(wanted):
        url = mapping[number]
        if not isinstance(url, str):
            raise _VerificationError("原页图像路径无效")
        if url.startswith("/static/uploads/tmp/"):
            root, prefix = UPLOADS_DIR, "/static/uploads"
        elif url.startswith("/static/test_uploads/tmp/"):
            root, prefix = TEST_UPLOADS_DIR, "/static/test_uploads"
        else:
            raise _VerificationError("原页图像不是本次导入的本地临时资源")
        path = resolve_upload_asset(url, uploads_dir=root, url_prefix=prefix, allowed_extensions={".png"})
        if not path.name.startswith("pdf_page_") or path.stat().st_size > MAX_PAGE_BYTES:
            raise _VerificationError("原页图像格式或大小不符合核验范围")
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise _VerificationError("原页图像不是有效PNG")
        messages.extend([{"type": "text", "text": f"原PDF第 {number} 页"},
                         {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}}])
    return messages


def _valid_decisions(parsed, expected: set[str]) -> dict:
    if not isinstance(parsed, dict) or set(parsed) != {"items"} or not isinstance(parsed["items"], list):
        raise _VerificationError("核验结果结构无效")
    if len(parsed["items"]) != len(expected):
        raise _VerificationError("核验结果缺少对应题目")
    results = {}
    for item in parsed["items"]:
        if not isinstance(item, dict) or set(item) != {"id", "decision", "evidence"}:
            raise _VerificationError("核验结果字段缺失或不受支持")
        identifier, decision, evidence = item["id"], item["decision"], item["evidence"]
        if (not isinstance(identifier, str) or identifier not in expected or identifier in results
                or not isinstance(decision, str) or decision not in _DECISIONS
                or not isinstance(evidence, str) or not evidence.strip() or len(evidence) > MAX_EVIDENCE_CHARS):
            raise _VerificationError("核验结果标识、结论或依据无效")
        results[identifier] = {"decision": decision, "evidence": evidence.strip()}
    return results


def verify_pdf_source_suspicions(
    questions: list[dict], diagnostics: dict, page_urls: list[str], page_numbers: list[int], *,
    check_cancelled: Callable[[], None] = _no_cancel, source_pages: list[dict] | None = None,
) -> dict:
    """One optional call; errors preserve the original manual-review requirements."""
    check_cancelled()
    required = [index for index, question in enumerate(questions)
                if isinstance(question, dict) and isinstance(question.get("source_review"), dict)
                and question["source_review"].get("required") is True]
    report = {"status": "no_candidates", "calls": 0, "checked": 0, "confirmed": 0,
              "pending": len(required), "skipped": 0, "usage": {}, "notes": [], "items": [], "skipped_reasons": []}
    if not required:
        return report
    candidates, wanted_pages, chars = [], set(), 0
    baseline = None
    if source_pages is not None:
        try:
            baseline = _source_baseline(source_pages, page_numbers)
        except Exception:
            report["skipped"] = len(required)
            report["skipped_reasons"] = [_skipped_entry(index, questions[index], diagnostics, "source_evidence_missing") for index in required]
            report["notes"] = ["首次页面识别缓存无法完整对应原页，已保留人工核对；未重新识图。"]
            return report
    for index in required:
        candidate = _candidate(index, questions[index], diagnostics)
        if candidate is None:
            report["skipped_reasons"].append(_skipped_entry(index, questions[index], diagnostics))
            continue
        if baseline is not None and not _cached_excerpt_matches(candidate, diagnostics, baseline):
            report["skipped_reasons"].append(_skipped_entry(index, questions[index], diagnostics, "source_evidence_missing"))
            continue
        pages = wanted_pages | set(candidate["source_pages"])
        size = len(candidate["source_excerpt"]) + len(candidate["output"])
        if len(candidates) >= MAX_ITEMS or len(pages) > MAX_PAGES or chars + size > MAX_TOTAL_CHARS:
            report["skipped_reasons"].append(_skipped_entry(index, questions[index], diagnostics, "budget_limit"))
            continue
        candidates.append(candidate)
        wanted_pages, chars = pages, chars + size
    report["skipped"] = len(required) - len(candidates)
    if not candidates:
        return report
    try:
        indices = [item["question_index"] for item in candidates]
        snapshot = _snapshot(questions, diagnostics, indices, source_pages)
        provider = resolve_ocr_provider(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
        if not provider.api_key or not provider.chat_completions_url or not provider.supports_image_input:
            raise _VerificationError("识图模型未配置或不支持图片输入")
        prompt_items = [{key: value for key, value in item.items() if key != "question_index"} for item in candidates]
        if baseline is not None:
            for item in prompt_items:
                item["evidence_reused"] = True
        content = [{"type": "text", "text": prompts.build_pdf_source_verification_prompt(prompt_items)},
                   *_page_messages(page_urls, page_numbers, wanted_pages)]
        payload = {"model": provider.model_name, "messages": [{"role": "user", "content": content}],
                   "max_tokens": MAX_OUTPUT_TOKENS, "stream": False}
        payload = apply_model_thinking_policy(payload, provider=provider, task="ocr")
        # The general OCR policy reserves a larger budget for full pages on
        # some providers. This bounded referee must keep its own smaller cap.
        cap_key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
        cap = payload.get(cap_key, MAX_OUTPUT_TOKENS)
        if not _positive_int(cap):
            raise _VerificationError("核验输出预算配置无效")
        payload[cap_key] = min(cap, MAX_OUTPUT_TOKENS)
        payload.pop("max_tokens" if cap_key == "max_completion_tokens" else "max_completion_tokens", None)
        check_cancelled()
        report["calls"] = 1
        try:
            response = post_chat_completion(provider, payload, timeout=120, check_status=False, retry_connection=False)
        except TaskCancelled:
            raise
        except Exception as exc:
            raise _VerificationError(f"核验请求失败（{type(exc).__name__}）") from exc
        check_cancelled()
        if response.status_code != 200:
            raise _VerificationError(f"核验请求失败（HTTP {response.status_code}）")
        try:
            body = response.json()
        except Exception as exc:
            raise _VerificationError("核验服务未返回有效JSON") from exc
        usage = body.get("usage") if isinstance(body, dict) else None
        report["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                           if isinstance(usage, dict) and isinstance(usage.get(key), int)
                           and not isinstance(usage[key], bool) and usage[key] >= 0}
        choices = body.get("choices") if isinstance(body, dict) else None
        if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"):
            raise _VerificationError("核验结果被截断或未正常结束")
        message = choices[0].get("message")
        raw = message.get("content") if isinstance(message, dict) else None
        if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RESPONSE_CHARS:
            raise _VerificationError("核验内容为空、格式无效或过长")
        try:
            parsed = parse_ai_json(raw)
        except Exception as exc:
            raise _VerificationError("核验结果无法解析") from exc
        decisions = _valid_decisions(parsed, {item["id"] for item in candidates})
        check_cancelled()
        if snapshot != _snapshot(questions, diagnostics, indices, source_pages):
            raise _VerificationError("核验期间题目或原文证据已变化，旧结果未采用")
        if any(_candidate(item["question_index"], questions[item["question_index"]], diagnostics) != item for item in candidates):
            raise _VerificationError("核验期间本地校验条件已变化，旧结果未采用")
        staged = {}
        for candidate in candidates:
            decision = decisions[candidate["id"]]
            index = candidate["question_index"]
            report["items"].append({key: value for key, value in candidate.items()
                                    if key not in {"source_excerpt", "output"}} | decision)
            if baseline is not None:
                report["items"][-1]["evidence_reused"] = True
            if decision["decision"] == "equivalent":
                review = deepcopy(questions[index]["source_review"])
                review.update(required=False, verified_by="vision", verification={
                    **decision, "model": provider.model_name, "snapshot_hash": snapshot,
                    "source_number": candidate["source_number"], "source_pages": list(candidate["source_pages"]),
                })
                if baseline is not None:
                    review["verification"].update(evidence_reused=True, source_baseline_hash=baseline["hash"])
                staged[index] = review
        remaining_items = [item for item in diagnostics.get("pdf_review_items", [])
                           if item.get("question_index") not in staged]
        # No await or fallible validation inside the commit block. Retain all
        # original reasons and excerpts as audit evidence for confirmed items.
        for index, review in staged.items():
            questions[index]["source_review"] = review
        diagnostics["pdf_review_items"] = remaining_items
        diagnostics["source_review_count"] = len(required) - len(staged)
        report.update(status="completed", checked=len(candidates), confirmed=len(staged), pending=len(required) - len(staged))
        return report
    except TaskCancelled:
        raise
    except Exception as exc:
        report["status"] = "failed"
        # Provider exceptions must not leak URLs, credentials or response text.
        reason = str(exc) if isinstance(exc, _VerificationError) else f"本地核验准备失败（{type(exc).__name__}）"
        report["notes"] = [f"自动核验未完成：{reason}；已保留原人工核对提示，未自动重试。"]
        return report
