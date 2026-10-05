"""Bounded source-backed corrections, committed only after a fresh visual verdict.

The repair model proposes exact replacements. The referee receives the original
pages and the complete proposed question without the repairer's conclusion.
Unconfirmed drafts never replace the import result. Transport failures are not
retried; additional calls are deliberate correction/referee rounds.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re

from mathbank import prompts
from mathbank.ai_providers import apply_model_thinking_policy
from mathbank.source_review_images import _references
from mathbank.source_review_results import parse_review_response
from mathbank.task_manager import TaskCancelled

MAX_ROUNDS = 3
MAX_EXTRA_CALLS = 12  # Shared by all batches in a document; two calls per round.
MAX_PATCHES = 8
MAX_PATCH_CHARS = 4000
_FIELDS = {"content", "answer_markdown"}
_INCOMPLETE = re.compile(r"\[(?:公式[^\]\n]{0,80}待核对|特殊字符待核对|插图待补)|MATHBANKGUARDSYMBOL|MBM[0-9]+|\[EXTRACTED_ORIGINAL\]|\ufffd")


class RepairStopped(ValueError):
    """Only fixed, locally constructed messages may enter a user report."""


def output_hash(output):
    return hashlib.sha256(json.dumps(output, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply_patches(item, proposal):
    if (not isinstance(proposal, dict)
            or set(proposal) != {"id", "source_number", "source_pages", "patches", "evidence"}
            or proposal["id"] != item["id"]
            or type(proposal["source_number"]) is not int or proposal["source_number"] != item["source_number"]
            or not isinstance(proposal["source_pages"], list)
            or any(type(page) is not int for page in proposal["source_pages"])
            or proposal["source_pages"] != item["source_pages"]
            or not isinstance(proposal["evidence"], str) or not 6 <= len(proposal["evidence"].strip()) <= 600
            or not isinstance(proposal["patches"], list) or not 1 <= len(proposal["patches"]) <= MAX_PATCHES):
        raise RepairStopped("修正建议缺少有效的原题、页码或具体依据，未采用。")
    output = deepcopy(item["output"])
    replacements = {field: [] for field in _FIELDS}
    for patch in proposal["patches"]:
        if (not isinstance(patch, dict) or set(patch) != {"field", "before", "after"}
                or not isinstance(patch["field"], str) or patch["field"] not in _FIELDS
                or not isinstance(patch["before"], str) or len(patch["before"]) > MAX_PATCH_CHARS
                or not isinstance(patch["after"], str) or len(patch["after"]) > MAX_PATCH_CHARS
                or patch["before"] == patch["after"]):
            raise RepairStopped("修正建议为空、过长或格式无效，未采用。")
        field, before, after = (patch[key] for key in ("field", "before", "after"))
        if field == "answer_markdown" and not item["original"].get(field, "").strip():
            raise RepairStopped("原文没有可核对的答案，不能自动生成或改写答案。")
        if not before:
            if field != "answer_markdown" or output[field] or replacements[field] or not after.strip():
                raise RepairStopped("空片段只能用于补回有原文依据的完整空答案。")
            replacements[field].append((0, 0, after))
            continue
        start = output[field].find(before)
        if start < 0 or output[field].find(before, start + 1) >= 0:
            raise RepairStopped("修正片段在题文中无法唯一定位，未猜测替换位置。")
        end = start + len(before)
        if any(start < other_end and end > other_start for other_start, other_end, _ in replacements[field]):
            raise RepairStopped("修正片段相互重叠，未采用。")
        replacements[field].append((start, end, after))
    for field, parts in replacements.items():
        for start, end, after in sorted(parts, reverse=True):
            output[field] = output[field][:start] + after + output[field][end:]
        old_refs = _references(item["output"][field], item["id"], field)
        new_refs = _references(output[field], item["id"], field)
        if (any(not ref["path"] for ref in old_refs + new_refs)
                or Counter(ref["path"] for ref in old_refs) != Counter(ref["path"] for ref in new_refs)):
            raise RepairStopped("修正涉及新增、删除或更换图片，现有原图证据不足，未采用。")
        if _INCOMPLETE.search(output[field]) or any(
                0xE000 <= ord(char) <= 0xF8FF or 0xF0000 <= ord(char) <= 0xFFFFD
                or 0x100000 <= ord(char) <= 0x10FFFD for char in output[field]):
            raise RepairStopped("修正后仍有缺字、公式编号或未解析占位，未采用。")
    if not output["content"].strip() or output == item["output"]:
        raise RepairStopped("修正没有产生完整有效的题文变化，已停止重复尝试。")
    return output


def repair_verified_differences(items, decisions, *, provider, request, attachments,
                                verification_content, parse_verdicts, validate_output,
                                check_evidence, check_cancelled, budget, max_chars=24000,
                                max_output_tokens=4096, progress=lambda message: None,
                                timeout_seconds=120):
    """Return confirmed outputs and audit records; never mutate caller questions.

    Adapters reapply their local gates to every edited candidate and verify the
    original question/source/page/image snapshot before and after every request.
    """
    result = {"outputs": {}, "decisions": {}, "repairs": {}, "calls": 0,
              "repair_calls": 0, "recheck_calls": 0, "usage": {}}
    current, latest, seen = {}, {}, {}
    for item in items:
        identifier = item["id"]
        decision = decisions.get(identifier, {})
        if decision.get("decision") != "different":
            continue
        record = {"status": "stopped", "attempts": 0, "reason": "", "history": []}
        result["repairs"][identifier] = record
        if item.get("repair_block_reason"):
            record["reason"] = item["repair_block_reason"]
        elif not decision.get("checks", {}).get("same_question"):
            record["reason"] = "尚未确认是同一道原题，不能自动修正。"
        elif all(decision.get("checks", {}).values()):
            record["reason"] = "模型的差异结论与逐项判断矛盾，未据此改写题目。"
        else:
            current[identifier] = deepcopy(item)
            latest[identifier] = deepcopy(decision)
            seen[identifier] = {output_hash(item["output"])}

    def ask(content, phase):
        check_cancelled()
        check_evidence()
        if sum(len(part.get("text", "")) for part in content) > max_chars:
            raise RepairStopped("修正或复核请求超出文字额度，已保留原题文。")
        payload = apply_model_thinking_policy({"model": provider.model_name,
            "messages": [{"role": "user", "content": content}], "max_tokens": max_output_tokens,
            "stream": False, "response_format": {"type": "json_object"}}, provider=provider, task="ocr")
        cap_key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
        payload[cap_key] = max_output_tokens
        payload.pop("max_tokens" if cap_key == "max_completion_tokens" else "max_completion_tokens", None)
        budget["remaining"] -= 1
        result["calls"] += 1
        result[phase + "_calls"] += 1
        response = request(provider, payload, timeout=timeout_seconds, check_status=False, retry_connection=False)
        check_cancelled()
        if response.status_code != 200:
            raise RepairStopped(f"自动修正或复核请求未成功（HTTP {response.status_code}），未重试请求。")
        body = response.json()
        usage = body.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(usage.get(key)) is int and usage[key] >= 0:
                result["usage"][key] = result["usage"].get(key, 0) + usage[key]
        check_evidence()
        choice = body["choices"][0]
        raw = choice.get("message", {}).get("content")
        if choice.get("finish_reason") != "stop" or not isinstance(raw, str) or not 0 < len(raw) <= 24000:
            raise RepairStopped("自动修正或复核响应不完整，未采用也未重试请求。")
        return raw

    for round_number in range(1, MAX_ROUNDS + 1):
        if not current:
            break
        if budget["remaining"] < 2:
            for identifier in current:
                result["repairs"][identifier].update(reason="已达到本次文档的自动修正调用额度。")
            break
        active = list(current.values())
        try:
            progress(f"正在依据原页修正 {len(active)} 题，第 {round_number}/{MAX_ROUNDS} 轮...")
            for item in active:
                result["repairs"][item["id"]]["attempts"] += 1
            raw = ask([{"type": "text", "text": prompts.build_source_repair_prompt(active, latest)}, *attachments(active)], "repair")
            parsed = parse_review_response(raw)
            if (not isinstance(parsed, dict) or set(parsed) != {"items"} or not isinstance(parsed["items"], list)
                    or any(not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                           or entry["id"] not in current for entry in parsed["items"])):
                raise RepairStopped("修正响应包含未知题目或无效结构，未采用。")
            proposed = []
            for item in active:
                identifier = item["id"]
                record = result["repairs"][identifier]
                entries = [entry for entry in parsed["items"] if entry["id"] == identifier]
                try:
                    if len(entries) != 1:
                        raise RepairStopped("修正响应缺失或重复本题，未采用。")
                    output = apply_patches(item, entries[0])
                    digest = output_hash(output)
                    if digest in seen[identifier]:
                        raise RepairStopped("修正回到了已尝试的题文，已停止循环。")
                    validate_output(item, output)
                    seen[identifier].add(digest)
                    record["history"].append({"round": round_number, "patches": deepcopy(entries[0]["patches"]),
                        "evidence": entries[0]["evidence"], "output_sha256": digest, "decision": "not_checked"})
                    proposed.append({**item, "output": output})
                except RepairStopped as exc:
                    record.update(status="stopped", reason=str(exc))
                    current.pop(identifier, None)
            if not proposed:
                continue
            progress(f"正在独立复核第 {round_number} 轮修正后的 {len(proposed)} 题...")
            raw = ask(verification_content(proposed), "recheck")
            verdicts = parse_verdicts(raw, proposed)
            for item in proposed:
                identifier = item["id"]
                record = result["repairs"][identifier]
                verdict = verdicts.get(identifier)
                if verdict is None:
                    record.update(status="failed", reason="修正后的本题复核结论缺失或无效，保留原题文。")
                    current.pop(identifier, None)
                    continue
                record["history"][-1].update(decision=verdict["decision"], verification_evidence=verdict["evidence"])
                if verdict["decision"] == "equivalent" and all(verdict["checks"].values()):
                    record.update(status="confirmed", reason="修正后已重新对照原页，六项检查均通过。")
                    result["outputs"][identifier] = item["output"]
                    result["decisions"][identifier] = verdict
                    current.pop(identifier, None)
                elif verdict["decision"] == "different" and verdict["checks"].get("same_question"):
                    current[identifier], latest[identifier] = item, verdict
                    record.update(status="exhausted" if round_number == MAX_ROUNDS else "stopped",
                                  reason=f"已尝试 {round_number} 轮修正，尚未通过原页复核，保留原题文。")
                else:
                    record.update(status="stopped", reason="修正后仍无法确认完整原文，已停止猜测并保留原题文。")
                    current.pop(identifier, None)
        except TaskCancelled:
            raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc, RepairStopped) else f"自动修正或复核未完成（{type(exc).__name__}），保留原题文。"
            for item in active:
                record = result["repairs"][item["id"]]
                if record["status"] != "confirmed":
                    record.update(status="failed", reason=reason)
            break
    check_cancelled()
    try:
        check_evidence()  # Mutation invalidates even earlier successes in this batch.
    except TaskCancelled:
        raise
    except Exception:
        result["outputs"].clear()
        result["decisions"].clear()
        for record in result["repairs"].values():
            record.update(status="failed", reason="核验期间题目或原页证据变化，修正结果未采用。")
        result["invalidated"] = True
    return result
