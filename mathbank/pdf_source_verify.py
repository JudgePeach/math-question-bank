"""Bounded visual adjudication of source-position suspicions after local guards.

Formula and wording suspicions are checked against the original page images,
including when first-pass text disagrees with the candidate. Bounded correction
drafts require a fresh complete verdict and unchanged evidence before adoption.
Image/provenance defects remain reviewable by the user.
"""

from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
import re
import unicodedata
from typing import Callable

from mathbank import prompts
from mathbank.ai_http import post_chat_completion
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.asset_security import resolve_upload_asset
from mathbank.content_locks import (
    _NUMBER, _NUMERIC_LITERAL, _REFERENCE, _comparison_layout, _formulas, _image_positions, _math_key, _plain_key,
)
from mathbank.paths import TEST_UPLOADS_DIR, UPLOADS_DIR
from mathbank.document_requests import DOCUMENT_AI_TIMEOUT_SECONDS
from mathbank.source_review_images import prepare_candidate_images, _references
from mathbank.source_review_results import parse_review_response
from mathbank.source_review_repair import MAX_EXTRA_CALLS, RepairStopped, output_hash, repair_verified_differences
from mathbank.pdf_symbol_risks import SYMBOL_RISK_REASONS, bound_symbol_risk_hints
from mathbank.task_manager import TaskCancelled


MAX_CALLS = 8
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
_CHECKS = {"same_question", "complete_content", "math_and_conditions", "options_and_subquestions", "figures", "answer"}
_INVALID_REASONS = {
    "missing_result": "本题未返回核验结论，保留原核对提示。",
    "duplicate_id": "本题重复返回核验结论，无法确定唯一结果，保留原核对提示。",
    "invalid_fields": "本题核验结论字段不完整或不受支持，保留原核对提示。",
    "invalid_verdict": "本题核验结论的原题号、页码、逐项判断或依据无效，保留原核对提示。",
}
_REVIEWABLE_REASONS = {
    POSITION_REASON,
    "原版答案文字或公式位置与原文未能完整对应，请对照原文核对。",
    "原文公式定位信息不完整，请对照原文核对。",
    "公式编号重复出现，请核对公式是否放错位置。",
    "题干插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。",
    "原版答案插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。",
}
_FORMULA_REASON = re.compile(
    r"(?:题干|原版答案)第 \d+ 处公式(?:与原文不同，请核对符号、数值和次序。|"
    r"编号属于其他位置，可能发生串题或调换。|编号重复出现，请核对是否重复或遗漏内容。)"
)


def _reviewable_reasons(reasons) -> bool:
    return (isinstance(reasons, list) and bool(reasons)
            and all(isinstance(reason, str) and (reason in _REVIEWABLE_REASONS or reason in SYMBOL_RISK_REASONS or _FORMULA_REASON.fullmatch(reason))
                    for reason in reasons))
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
_UNCERTAIN_TEXT = re.compile(r"\[插图待补|\[公式[^\]\n]{0,20}待核对|无法识别的公式|<\s*img\b", re.IGNORECASE)


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


def _visual_block_reason(source: str, output: str, *, allow_image_changes: bool = False) -> str | None:
    """Reject unusable evidence, not the formula difference being adjudicated."""
    if "MATHBANKGUARDSYMBOL" in source + output or _REFERENCE.search(source + "\n" + output):
        return "source_evidence_missing"
    # First-pass OCR may leave an unresolved formula in its excerpt while the
    # candidate already contains a complete formula. The original page, not
    # this imperfect excerpt, can establish whether that candidate is faithful.
    # A still-incomplete candidate cannot be approved without editing it.
    for value in (source, output):
        if any("插图" in match.group() or "img" in match.group().lower()
               for match in _UNCERTAIN_TEXT.finditer(value)):
            return "figure_risk"
    if _UNCERTAIN_TEXT.search(output):
        return "formula_difference"
    source_images = _image_positions(source, _formulas(source))
    output_images = _image_positions(output, _formulas(output))
    source_refs = _references(source, "source", "content")
    output_refs = _references(output, "output", "content")
    # Once every candidate image is supplied as actual pixels, the referee may
    # inspect changed paths/positions against the original page. Missing or
    # added occurrences still require editing, rather than visual approval.
    if (any(not item["path"] for item in source_refs + output_refs)
            or len(source_refs) != len(output_refs)
            or not allow_image_changes and source_images != output_images):
        return "figure_risk"
    return None


def _candidate(index: int, question: dict, diagnostics: dict, *, allow_image_changes: bool = False) -> dict | None:
    if not isinstance(diagnostics, dict):
        return None
    review = question.get("source_review")
    if (not isinstance(review, dict) or review.get("required") is not True
            or not _reviewable_reasons(review.get("reasons"))):
        return None
    symbol_hints = None
    if set(review["reasons"]) & SYMBOL_RISK_REASONS:
        symbol_hints = bound_symbol_risk_hints(question, diagnostics, index)
        if symbol_hints is None:
            return None
    output = question.get("content")
    answer_output = question.get("answer_markdown", "")
    if not isinstance(output, str) or not output.strip() or not isinstance(answer_output, str):
        return None
    all_matches, review_items = diagnostics.get("source_matches", []), diagnostics.get("pdf_review_items", [])
    if not isinstance(all_matches, list) or not isinstance(review_items, list):
        return None
    matches = [item for item in all_matches if isinstance(item, dict)
               and type(item.get("question_index")) is int and item["question_index"] == index
               and item.get("field") in {"content", "answer_markdown"}]
    stems = [item for item in matches if item["field"] == "content"]
    answers = [item for item in matches if item["field"] == "answer_markdown"]
    items = [item for item in review_items if isinstance(item, dict)
             and type(item.get("question_index")) is int and item["question_index"] == index]
    if len(stems) != 1 or len(answers) > 1 or len(items) != 1:
        return None
    # A nonempty answer needs its own exact source range. Checking the stem
    # image alone cannot establish an answer printed elsewhere in the paper.
    if (answer_output.strip() or any(reason.startswith("原版答案") for reason in review["reasons"])) and not answers:
        return None
    stem, item = stems[0], items[0]
    from mathbank.pdf_figures import _review_explanations
    if item.get("reasons") != _review_explanations(review["reasons"]):
        return None
    number, pages = stem.get("source_number"), item.get("source_pages")
    if (not _positive_int(number) or item.get("source_number") != number or not _positive_int(item.get("source_number"))
            or not isinstance(pages, list) or not pages or any(not _positive_int(page) for page in pages)
            or len(set(pages)) != len(pages) or len(pages) > MAX_PAGES):
        return None
    for match in matches:
        start, end, source = match.get("source_start"), match.get("source_end"), match.get("source_excerpt")
        if (type(start) is not int or start < 0 or type(end) is not int or end <= start
                or not isinstance(source, str) or not source.strip() or end - start != len(source)
                or match.get("source_number") not in (None, number)):
            return None
        if sum(isinstance(other, dict) and other.get("source_start") == start and other.get("source_end") == end
               for other in all_matches) != 1:
            return None
        field_output = output if match["field"] == "content" else answer_output.replace("[EXTRACTED_ORIGINAL]", "").strip()
        if _visual_block_reason(source, field_output, allow_image_changes=allow_image_changes) is not None:
            return None
    source = stem["source_excerpt"]
    answer_source = answers[0]["source_excerpt"] if answers else ""
    # The existing review excerpt contains precisely the suspect source parts,
    # which can be only the answer. It is not necessarily the stem's excerpt.
    excerpts = [match["source_excerpt"] for match in sorted(matches, key=lambda item: item["source_start"])]
    if review.get("source_excerpt") not in [*excerpts, "\n\n".join(excerpts)]:
        return None
    if sum(map(len, (source, output, answer_source, answer_output))) > MAX_ITEM_CHARS:
        return None
    heading = _NUMBER.match(source)
    if not heading or int(heading.group(1) or heading.group(2)) != number:
        return None
    result = {"id": f"item_{index + 1:03d}", "question_index": index, "source_number": number,
              "source_pages": sorted(pages), "source_excerpt": source, "output": output}
    if answers:
        result.update(source_answer_excerpt=answer_source, output_answer=answer_output.replace("[EXTRACTED_ORIGINAL]", "").strip())
    if symbol_hints is not None:
        result["visual_symbol_risks"] = symbol_hints
    return result


def _skipped_entry(index: int, question: dict, diagnostics: dict, code: str | None = None) -> dict:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    all_matches, all_items = diagnostics.get("source_matches"), diagnostics.get("pdf_review_items")
    matches = ([item for item in all_matches if isinstance(item, dict)
               and type(item.get("question_index")) is int and item["question_index"] == index
               and item.get("field") in {"content", "answer_markdown"}]
               if isinstance(all_matches, list) else [])
    items = [item for item in all_items if isinstance(item, dict)
             and type(item.get("question_index")) is int and item["question_index"] == index] if isinstance(all_items, list) else []
    stems = [match for match in matches if match["field"] == "content"]
    match = stems[0] if len(stems) == 1 else {}
    item = items[0] if len(items) == 1 else {}
    number = match.get("source_number")
    pages = item.get("source_pages")
    review = question.get("source_review")
    reasons = review.get("reasons", []) if isinstance(review, dict) else []
    reasons = reasons if isinstance(reasons, list) else []
    if code is None:
        if any(isinstance(reason, str) and any(word in reason for word in ("插图", "配图", "裁剪")) for reason in reasons):
            code = "figure_risk"
        elif not _reviewable_reasons(reasons):
            code = "other_review_reason"
        else:
            size = 0
            for source_match in matches:
                source, output = source_match.get("source_excerpt"), question.get(source_match["field"], "")
                if isinstance(source, str) and isinstance(output, str):
                    size += len(source) + len(output)
                    code = code or _visual_block_reason(source, output)
            if size > MAX_ITEM_CHARS or isinstance(pages, list) and len(pages) > MAX_PAGES:
                code = "budget_limit"
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
    matches = [item for item in diagnostics["source_matches"]
               if isinstance(item, dict) and type(item.get("question_index")) is int
               and item["question_index"] == candidate["question_index"]
               and item.get("field") in {"content", "answer_markdown"}]
    actual_pages = set()
    for match in matches:
        start, end = match["source_start"], match["source_end"]
        if baseline["markdown"][start:end] != match["source_excerpt"]:
            return False
        actual_pages.update(number for number, left, right, text in baseline["ranges"]
                            if max(start, left) < min(end, right)
                            and text[max(start, left) - left:min(end, right) - left].strip())
    nonempty_pages = {number for number, _, _, text in baseline["ranges"] if text.strip()}
    return bool(actual_pages) and actual_pages <= set(candidate["source_pages"]) <= nonempty_pages


def _snapshot(questions: list, diagnostics: dict, indices: list[int], source_pages=None) -> str:
    data = {"question_count": len(questions), "questions": [{"index": index, "content": questions[index].get("content"),
            "answer_markdown": questions[index].get("answer_markdown"), "source_review": questions[index].get("source_review")}
            for index in indices],
            "source_matches": diagnostics.get("source_matches"), "pdf_review_items": diagnostics.get("pdf_review_items"),
            "source_pages": source_pages}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _page_label(number: int) -> dict:
    return {"type": "text", "text": f"原PDF第 {number} 页"}


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
        messages.extend([_page_label(number),
                         {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}}])
    return messages


def _valid_decisions(parsed, candidates: list[dict]) -> tuple[dict, list[dict]]:
    if isinstance(parsed, list):
        parsed = {"items": parsed}
    expected = {item["id"]: item for item in candidates}
    if not isinstance(parsed, dict) or set(parsed) != {"items"} or not isinstance(parsed["items"], list):
        raise _VerificationError("核验结果结构无效")
    # An unknown identity cannot be safely attributed to a single question.
    # Reject the envelope before accepting any apparently valid result.
    for item in parsed["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in expected:
            raise _VerificationError("核验结果包含无法归属的题目标识")
    counts = Counter(item["id"] for item in parsed["items"])
    by_id = {item["id"]: item for item in parsed["items"]}
    results, invalid = {}, []

    def retain(candidate, code):
        invalid.append({key: candidate[key] for key in ("id", "question_index", "source_number", "source_pages")}
                       | {"code": code, "reason": _INVALID_REASONS[code]})

    for identifier, candidate in expected.items():
        if counts[identifier] == 0:
            retain(candidate, "missing_result")
            continue
        if counts[identifier] != 1:
            retain(candidate, "duplicate_id")
            continue
        item = by_id[identifier]
        if not isinstance(item, dict) or set(item) != {"id", "source_number", "source_pages", "decision", "checks", "evidence"}:
            retain(candidate, "invalid_fields")
            continue
        identifier, decision, evidence = item["id"], item["decision"], item["evidence"]
        if (not isinstance(decision, str) or decision not in _DECISIONS
                or not isinstance(evidence, str) or not 6 <= len(evidence.strip()) <= MAX_EVIDENCE_CHARS
                or not _positive_int(item["source_number"]) or item["source_number"] != expected[identifier]["source_number"]
                or not isinstance(item["source_pages"], list) or any(not _positive_int(page) for page in item["source_pages"])
                or sorted(item["source_pages"]) != expected[identifier]["source_pages"]
                or not isinstance(item["checks"], dict) or set(item["checks"]) != _CHECKS
                or any(type(value) is not bool for value in item["checks"].values())
                or decision == "equivalent" and not all(item["checks"].values())):
            retain(candidate, "invalid_verdict")
            continue
        results[identifier] = {"decision": decision, "evidence": evidence.strip(), "checks": dict(item["checks"])}
    return results, invalid


def _candidate_images(candidates, allowed_paths):
    items = [{"id": item["id"], "output": {"content": item["output"],
              "answer_markdown": item.get("output_answer", "")}} for item in candidates]
    return prepare_candidate_images(items, allowed_paths=allowed_paths,
                                    uploads_dir=UPLOADS_DIR, test_uploads_dir=TEST_UPLOADS_DIR)


def _complete_images(image_evidence) -> bool:
    return all(item["complete"] for item in image_evidence["per_item"].values())


def _verification_prompt(candidates, baseline, image_evidence=None):
    prompt_items = [{key: value for key, value in item.items() if key != "question_index"} for item in candidates]
    for item in prompt_items:
        images = image_evidence["per_item"][item["id"]]["images"] if image_evidence else []
        if images:
            item["candidate_images"] = images
        item["local_suspicions"] = {
            field: reason for field, source, output in (
                ("content", item["source_excerpt"], item["output"]),
                ("answer_markdown", item.get("source_answer_excerpt", ""), item.get("output_answer", "")),
            ) if (reason := _local_block_reason(source, output)) is not None
        }
    if baseline is not None:
        for item in prompt_items:
            item["evidence_reused"] = True
    return prompts.build_pdf_source_verification_prompt(prompt_items)


def _text_chars(content) -> int:
    """Count all request text; image bytes have their own bounded budget."""
    return sum(len(part["text"]) for part in content if part["type"] == "text")


def _planned_text_chars(candidates, baseline, image_evidence) -> int:
    pages = sorted({page for candidate in candidates for page in candidate["source_pages"]})
    return _text_chars([{"type": "text", "text": _verification_prompt(candidates, baseline, image_evidence)},
                        *(_page_label(page) for page in pages), *image_evidence["messages"]])


def verify_pdf_source_suspicions(
    questions: list[dict], diagnostics: dict, page_urls: list[str], page_numbers: list[int], *,
    check_cancelled: Callable[[], None] = _no_cancel, source_pages: list[dict] | None = None,
    candidate_image_paths=(), progress=lambda message: None,
) -> dict:
    """Check bounded batches once each; errors retain unresolved review details."""
    check_cancelled()
    required = [index for index, question in enumerate(questions)
                if isinstance(question, dict) and isinstance(question.get("source_review"), dict)
                and question["source_review"].get("required") is True]
    report = {"status": "no_candidates", "calls": 0, "checked": 0, "confirmed": 0,
              "pending": len(required), "skipped": 0, "usage": {}, "notes": [], "items": [], "skipped_reasons": [], "invalid_items": []}
    if not required:
        return report
    batches = []
    wanted_pages = set()
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
        check_cancelled()
        candidate = _candidate(index, questions[index], diagnostics, allow_image_changes=True)
        if candidate is None:
            report["skipped_reasons"].append(_skipped_entry(index, questions[index], diagnostics))
            continue
        if baseline is not None and not _cached_excerpt_matches(candidate, diagnostics, baseline):
            report["skipped_reasons"].append(_skipped_entry(index, questions[index], diagnostics, "source_evidence_missing"))
            continue
        single_images = _candidate_images([candidate], candidate_image_paths)
        if not _complete_images(single_images):
            skipped = _skipped_entry(index, questions[index], diagnostics, "figure_risk")
            skipped["details"] = single_images["per_item"][candidate["id"]]["reasons"]
            report["skipped_reasons"].append(skipped)
            continue
        pages = wanted_pages | set(candidate["source_pages"])
        trial = [*(batches[-1] if batches else []), candidate]
        trial_images = _candidate_images(trial, candidate_image_paths) if batches else single_images
        batch_size = _planned_text_chars(trial, baseline, trial_images)
        if (not batches or len(batches[-1]) >= MAX_ITEMS or len(pages) > MAX_PAGES
                or batch_size > MAX_TOTAL_CHARS or not _complete_images(trial_images)):
            if (len(batches) >= MAX_CALLS or MAX_ITEMS < 1
                    or _planned_text_chars([candidate], baseline, single_images) > MAX_TOTAL_CHARS):
                report["skipped_reasons"].append(_skipped_entry(index, questions[index], diagnostics, "budget_limit"))
                continue
            batches.append([])
            pages = set(candidate["source_pages"])
        batches[-1].append(candidate)
        wanted_pages = pages
    report["skipped"] = len(required) - sum(map(len, batches))
    if not batches:
        return report
    failed = False
    repair_budget = {"remaining": MAX_EXTRA_CALLS}
    for candidates in batches:
        check_cancelled()
        batch_report = _verify_candidate_batch(questions, diagnostics, candidates, page_urls, page_numbers,
                                                check_cancelled=check_cancelled, source_pages=source_pages, baseline=baseline,
                                                candidate_image_paths=candidate_image_paths,
                                                repair_budget=repair_budget, progress=progress)
        for key in ("calls", "checked", "confirmed"):
            report[key] += batch_report[key]
        for key in ("repair_calls", "recheck_calls", "repaired"):
            if key in batch_report:
                report[key] = report.get(key, 0) + batch_report[key]
        report["items"].extend(batch_report["items"])
        report["invalid_items"].extend(batch_report["invalid_items"])
        report["notes"].extend(batch_report["notes"])
        for key, value in batch_report["usage"].items():
            report["usage"][key] = report["usage"].get(key, 0) + value
        failed = failed or batch_report["status"] in {"failed", "partial"}
    report["notes"] = list(dict.fromkeys(report["notes"]))
    report["pending"] = sum(isinstance(q.get("source_review"), dict) and q["source_review"].get("required") is True
                            for q in questions if isinstance(q, dict))
    report["status"] = ("partial" if report["checked"] else "failed") if failed else "completed"
    return report


def _verify_candidate_batch(questions, diagnostics, candidates, page_urls, page_numbers, *,
                            check_cancelled, source_pages, baseline, candidate_image_paths,
                            repair_budget=None, progress=lambda message: None):
    """Execute one batch once; a failed batch never retries or erases evidence."""
    required = [index for index, q in enumerate(questions)
                if isinstance(q, dict) and isinstance(q.get("source_review"), dict) and q["source_review"].get("required") is True]
    report = {"status": "failed", "calls": 0, "checked": 0, "confirmed": 0,
              "usage": {}, "notes": [], "items": [], "invalid_items": []}
    wanted_pages = set().union(*(set(item["source_pages"]) for item in candidates))
    try:
        indices = [item["question_index"] for item in candidates]
        if any(_candidate(item["question_index"], questions[item["question_index"]], diagnostics,
                          allow_image_changes=True) != item for item in candidates):
            raise _VerificationError("核验前题目或来源条件已变化，未使用旧候选")
        if baseline is not None and _source_baseline(source_pages, page_numbers) != baseline:
            raise _VerificationError("核验前首次页面证据已变化，未使用旧候选")
        snapshot = _snapshot(questions, diagnostics, indices, source_pages)
        provider = resolve_ocr_provider(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
        if not provider.api_key or not provider.chat_completions_url or not provider.supports_image_input:
            raise _VerificationError("识图模型未配置或不支持图片输入")
        page_content = _page_messages(page_urls, page_numbers, wanted_pages)
        page_evidence_hash = hashlib.sha256(json.dumps(page_content, sort_keys=True).encode()).hexdigest()
        image_evidence = _candidate_images(candidates, candidate_image_paths)
        if not _complete_images(image_evidence):
            raise _VerificationError("候选图片证据不完整，未发起核验")
        prompt = _verification_prompt(candidates, baseline, image_evidence)
        content = [{"type": "text", "text": prompt}, *page_content, *image_evidence["messages"]]
        if _text_chars(content) > MAX_TOTAL_CHARS:
            raise _VerificationError("完整核验请求超过文字额度")
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
            response = post_chat_completion(provider, payload, timeout=DOCUMENT_AI_TIMEOUT_SECONDS,
                                            check_status=False, retry_connection=False)
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
            parsed = parse_review_response(raw)
        except Exception as exc:
            raise _VerificationError("核验结果无法解析") from exc
        decisions, invalid_items = _valid_decisions(parsed, candidates)
        check_cancelled()
        if page_evidence_hash != hashlib.sha256(json.dumps(_page_messages(page_urls, page_numbers, wanted_pages), sort_keys=True).encode()).hexdigest():
            raise _VerificationError("核验期间原页图像已变化，旧结果未采用")
        if snapshot != _snapshot(questions, diagnostics, indices, source_pages):
            raise _VerificationError("核验期间题目或原文证据已变化，旧结果未采用")
        if image_evidence["fingerprint"] != _candidate_images(candidates, candidate_image_paths)["fingerprint"]:
            raise _VerificationError("核验期间候选图片已变化，旧结果未采用")
        if any(_candidate(item["question_index"], questions[item["question_index"]], diagnostics,
                          allow_image_changes=True) != item for item in candidates):
            raise _VerificationError("核验期间本地校验条件已变化，旧结果未采用")
        def check_repair_evidence():
            check_cancelled()
            if (snapshot != _snapshot(questions, diagnostics, indices, source_pages)
                    or page_evidence_hash != hashlib.sha256(json.dumps(
                        _page_messages(page_urls, page_numbers, wanted_pages), sort_keys=True).encode()).hexdigest()
                    or image_evidence["fingerprint"] != _candidate_images(candidates, candidate_image_paths)["fingerprint"]):
                raise _VerificationError("修正期间题目、原页或候选图片证据发生变化")

        original_candidates = {item["id"]: item for item in candidates}

        def revised_candidates(items):
            return [{**original_candidates[item["id"]], "output": item["output"]["content"],
                     **({"output_answer": item["output"]["answer_markdown"]}
                        if "source_answer_excerpt" in original_candidates[item["id"]] else {})} for item in items]

        def repair_attachments(items):
            changed = revised_candidates(items)
            images = _candidate_images(changed, candidate_image_paths)
            if not _complete_images(images):
                raise RepairStopped("修正候选图片证据不完整，未继续请求。")
            pages_needed = set().union(*(set(item["source_pages"]) for item in changed))
            return [*_page_messages(page_urls, page_numbers, pages_needed), *images["messages"]]

        def recheck_content(items):
            changed = revised_candidates(items)
            images = _candidate_images(changed, candidate_image_paths)
            return [{"type": "text", "text": _verification_prompt(changed, baseline, images)}, *repair_attachments(items)]

        def validate_repair(item, output):
            if sum(len(value) for value in [*item["original"].values(), *output.values()]) > MAX_ITEM_CHARS:
                raise RepairStopped("修正后的完整题文超出单题核验额度。")
            if any(_visual_block_reason(item["original"][field], output[field], allow_image_changes=True)
                   for field in ("content", "answer_markdown")):
                raise RepairStopped("修正后仍有来源、缺字或图片完整性问题，未采用。")

        repair = repair_verified_differences([
            {key: item[key] for key in ("id", "source_number", "source_pages")} | {
                "original": {"content": item["source_excerpt"], "answer_markdown": item.get("source_answer_excerpt", "")},
                "output": {"content": item["output"], "answer_markdown": item.get("output_answer", "")}}
            for item in candidates], decisions, provider=provider, request=post_chat_completion,
            attachments=repair_attachments, verification_content=recheck_content,
            parse_verdicts=lambda raw, items: _valid_decisions(parse_review_response(raw), revised_candidates(items))[0],
            validate_output=validate_repair, check_evidence=check_repair_evidence,
            check_cancelled=check_cancelled, budget=repair_budget if repair_budget is not None else {"remaining": MAX_EXTRA_CALLS},
            max_chars=MAX_TOTAL_CHARS, max_output_tokens=MAX_OUTPUT_TOKENS, progress=progress,
            timeout_seconds=DOCUMENT_AI_TIMEOUT_SECONDS)
        report["calls"] += repair["calls"]
        if repair["repairs"]:
            report.update(repair_calls=repair["repair_calls"], recheck_calls=repair["recheck_calls"], repaired=0)
        for key, value in repair["usage"].items():
            report["usage"][key] = report["usage"].get(key, 0) + value
        check_repair_evidence()
        staged, staged_outputs, confirmed = {}, {}, set()
        for candidate in candidates:
            if candidate["id"] not in decisions:
                continue
            decision = repair["decisions"].get(candidate["id"], decisions[candidate["id"]])
            index = candidate["question_index"]
            report["items"].append({key: value for key, value in candidate.items()
                                    if key not in {"source_excerpt", "output", "source_answer_excerpt", "output_answer"}} | decision)
            if baseline is not None:
                report["items"][-1]["evidence_reused"] = True
            review = deepcopy(questions[index]["source_review"])
            if candidate["id"] in repair["repairs"]:
                review["repair"] = repair["repairs"][candidate["id"]]
            verification = {
                **decision, "model": provider.model_name, "snapshot_hash": snapshot,
                "page_evidence_hash": page_evidence_hash,
                "source_number": candidate["source_number"], "source_pages": list(candidate["source_pages"]),
            }
            if image_evidence["per_item"][candidate["id"]]["images"]:
                verification.update(candidate_images=image_evidence["per_item"][candidate["id"]]["images"],
                                    candidate_image_evidence_hash=image_evidence["fingerprint"])
            if baseline is not None:
                verification.update(evidence_reused=True, source_baseline_hash=baseline["hash"])
            if decision["decision"] == "equivalent":
                if candidate["id"] in repair["outputs"]:
                    corrected = repair["outputs"][candidate["id"]]
                    staged_outputs[index] = corrected
                    verification["verified_output_sha256"] = output_hash(corrected)
                    verification["output_before_repair_sha256"] = output_hash({
                        "content": candidate["output"], "answer_markdown": candidate.get("output_answer", "")})
                    corrected_images = _candidate_images(revised_candidates([{
                        "id": candidate["id"], "output": corrected}]), candidate_image_paths)
                    verification.update(candidate_images=corrected_images["per_item"][candidate["id"]]["images"],
                                        candidate_image_evidence_hash=corrected_images["fingerprint"])
                review.update(required=False, verified_by="vision", verification=verification)
                confirmed.add(index)
            else:
                review["verification_attempt"] = verification
                review["reasons"] = list(dict.fromkeys([*review.get("reasons", []), "原页自动核验：" + decision["evidence"]]))
            staged[index] = review
        remaining_items = [item for item in diagnostics.get("pdf_review_items", [])
                           if item.get("question_index") not in confirmed]
        check_repair_evidence()
        # No await or fallible validation inside the commit block. Retain all
        # original reasons and excerpts as audit evidence for confirmed items.
        for index, review in staged.items():
            if index in staged_outputs:
                questions[index].update(staged_outputs[index])
            questions[index]["source_review"] = review
        diagnostics["pdf_review_items"] = remaining_items
        diagnostics["source_review_count"] = len(required) - len(confirmed)
        if repair["repairs"]:
            report["repaired"] = len(staged_outputs)
        report["invalid_items"] = invalid_items
        if invalid_items:
            report["notes"] = [f"{len(invalid_items)} 题的核验结论缺失或无效，已逐题保留原核对提示；其他有效结论已独立采纳，未自动重试。"]
        report.update(status=("partial" if invalid_items else "completed") if decisions else "failed",
                      checked=len(decisions), confirmed=len(confirmed), pending=len(required) - len(confirmed))
        return report
    except TaskCancelled:
        raise
    except Exception as exc:
        report["status"] = "failed"
        # Provider exceptions must not leak URLs, credentials or response text.
        reason = str(exc) if isinstance(exc, _VerificationError) else f"本地核验准备失败（{type(exc).__name__}）"
        report["notes"] = [f"自动核验未完成：{reason}；已保留原人工核对提示，未自动重试。"]
        return report
