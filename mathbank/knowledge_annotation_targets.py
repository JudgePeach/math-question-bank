"""Read-only subquestion anchors and conservative knowledge coverage diagnostics.

Anchors describe literal, explicitly numbered tasks. They do not infer a
knowledge point, certify mathematical relevance, or alter any source text.
Uncertain numbering stays a whole-question target and produces no missing-part
warning. The caller remains responsible for validating point identities/roles.
"""
from __future__ import annotations

import hashlib
from itertools import islice
import re

from .ai_json import _subquestion_literal_ranges
from .content_locks import _MATH_ENVIRONMENTS, _reference_literal_ranges
from .fillin_placeholders import _protected_ranges
from .markdown_tables import _protected_blocks, find_markdown_tables
from .question_assets import markdown_literal_ranges


MAX_SCAN_MARKERS = 48
MAX_DIAGNOSTIC_TARGETS = 48
MAX_TARGET_QUOTES = 12
MAX_TARGET_EVIDENCE_LENGTH = 2000
_MARKER = re.compile(
    r"\([ \t]*(?P<ascii>[1-9]\d{0,2}|[IVXivx]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]|[一二三四五六七八九十]+)[ \t]*\)"
    r"|（[ \t]*(?P<wide>[1-9]\d{0,2}|[IVXivx]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]|[一二三四五六七八九十]+)[ \t]*）"
    r"|(?P<circled>[①②③④⑤⑥⑦⑧⑨⑩⑪⑫])"
    r"|(?P<plain>(?m:^[ \t]{0,3}(?:[1-9]\d{0,2}|[一二三四五六七八九十]+)[.．、]))(?![ \t]*[(（])"
)
_ROMAN = dict(zip(("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"),
                  range(1, 13)))
_UNICODE_ROMAN = dict(zip("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ", range(1, 13)))
_CIRCLED = dict(zip("①②③④⑤⑥⑦⑧⑨⑩⑪⑫", range(1, 13)))
_CHINESE = {value: index for index, value in enumerate(
    ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"), 1)}
_TASK_VERB = r"(?:求(?!和公式|导法则|导(?:后|之后|以后|时|可得|得到|得))|证明(?!后)|试证|判断(?!后)|计算(?!后)|确定|讨论|写出|给出|说明|比较|作出|画出|建立|探究|研究)"
_ASK = re.compile(
    r"(?:^|[，,。；;：:\n]\s*|(?:并且|并|然后|同时|进而|以及|再)\s*)(?:(?:试|请你|请|分别|进一步|直接|并|则|再|要求)\s*){0,2}(?P<action>" + _TASK_VERB
    + r")|(?P<query>是否|能否|[?？])"
)
_TOOL_TASK = re.compile(r"^\s*(?:利用|根据|结合|通过|运用|用)[^\n。；;]{0,160}(?P<action>" + _TASK_VERB + ")")
_GENERIC_QUOTES = {"求", "求证", "证明", "试证", "求值", "求解", "计算", "判断", "讨论",
                   "说明理由", "第问", "第小问", "全题"}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ranges(content: str) -> list[tuple[int, int]]:
    # Reuse the established scanners: incomplete mathematics is protected to
    # EOF, and all TeX environments (including choices/tables) stay indivisible.
    return sorted(set([*_protected_ranges(content), *_subquestion_literal_ranges(content),
                       *_protected_blocks(content),
                       *((table.start, table.end) for table in find_markdown_tables(content))]))


def _visible(content: str, ranges: list[tuple[int, int]]) -> str:
    chars = list(content)
    for start, end in ranges:
        chars[start:end] = ["\n" if char == "\n" else " " for char in content[start:end]]
    return "".join(chars)


def _literal_goal_ranges(content: str) -> list[tuple[int, int]]:
    """Code/paths/tables cannot prove goals; a requested math object can."""
    ranges = [*markdown_literal_ranges(content), *_reference_literal_ranges(content)]
    math_names = {*_MATH_ENVIRONMENTS, "alignat", "alignat*", "alignedat*", "flalign", "flalign*"}
    for start, end in _protected_blocks(content):
        value = content[start:end]
        environment = re.match(r"\\begin\{([^{}]+)\}", value)
        if (value.startswith(("$", r"\(", r"\["))
                or environment and environment[1] in math_names):
            continue
        ranges.append((start, end))
    ranges.extend((table.start, table.end) for table in find_markdown_tables(content))
    return sorted(set(ranges))


def _goal_ranges(content: str, visible: str, start: int, end: int,
                 protected: list[tuple[int, int]]) -> list[dict]:
    """Bound explicit action clauses, preserving mathematical objects inside.

    A condition before "，求…" is outside the goal. Visible commas/semicolons
    terminate that goal; punctuation inside math/literals never splits it.
    """
    body = visible[start:end]
    leading = len(body) - len(body.lstrip())
    offset = start + leading
    body = body[leading:]
    matches = list(islice(_ASK.finditer(body), MAX_DIAGNOSTIC_TARGETS + 1))
    tool_match = _TOOL_TASK.search(body)
    if tool_match is not None:
        matches.append(tool_match)
    goals = []
    matches.sort(key=lambda match: match.start())
    filtered = []
    for match in matches:
        if match.groupdict().get("query") is not None and any(
                previous.groupdict().get("action") is not None
                and previous.end("action") <= match.start("query")
                and not re.search(r"[，,。；;：:\n]", body[previous.end("action"):match.start("query")])
                for previous in filtered):
            # "判断函数是否单调" is one requested product; 是否/问号
            # inside its object must not manufacture another overlapping goal.
            continue
        filtered.append(match)
    matches = filtered
    for ordinal, match in enumerate(matches):
        if match.groupdict().get("action") is not None:
            goal_start = offset + match.start("action")
            object_start = offset + match.end("action")
        else:
            goal_start = offset + match.start("query")
            object_start = offset + match.end("query")
            if match.group("query") in "?？":
                # A question-mark sentence has its object before the mark.
                boundaries = [token.end() for token in re.finditer(r"[，,。；;：:\n]", content[start:goal_start])
                              if not any(left <= start + token.start() < right for left, right in protected)]
                goal_start = start + (boundaries[-1] if boundaries else 0)
                object_start = goal_start
        goal_end = min(end, offset + matches[ordinal + 1].start()) if ordinal + 1 < len(matches) else end
        for delimiter in re.finditer(r"[，,。；;!?！？\n]", content[object_start:end]):
            position = object_start + delimiter.start()
            if not any(left <= position < right for left, right in protected):
                goal_end = min(goal_end, position)
                break
        while object_start < goal_end and content[object_start].isspace():
            object_start += 1
        while goal_end > object_start and content[goal_end - 1].isspace():
            goal_end -= 1
        if goal_end > object_start:
            goal = {"start": goal_start, "object_start": object_start, "end": goal_end,
                    "quote": content[goal_start:goal_end]}
            # A coordinated object may denote several requested products or
            # one relation ("A与B的和"). Do not guess its semantics: require
            # one continuous quote to substantively include the whole group.
            joins = list(re.finditer(r"以及|和|与|及|、", visible[object_start:goal_end]))
            if joins:
                starts = [object_start, *(object_start + join.end() for join in joins)]
                ends = [*(object_start + join.start() for join in joins), goal_end]
                parts = [(left, right) for left, right in zip(starts, ends) if content[left:right].strip()]
                if len(parts) > 1:
                    goal["object_parts"] = parts
            if goal not in goals:
                goals.append(goal)
    return sorted(goals, key=lambda item: (item["start"], item["end"]))


def _safe_boundary(content: str, start: int) -> bool:
    if start == 0 or content[start - 1].isspace() or content[start - 1] in "。；;：:!?！？":
        return True
    # A preserved outer question number may touch its first subquestion.
    line_start = content.rfind("\n", 0, start) + 1
    return re.fullmatch(r"[ \t]*\d{1,3}[.．、][ \t]*", content[line_start:start]) is not None


def _target(content: str, start: int, end: int, label: str, ordinal: str) -> dict:
    while end > start and content[end - 1].isspace():
        end -= 1
    quote = content[start:end]
    return {"id": f"target:{ordinal}:{_digest(quote)[:16]}", "label": label,
            "quote": quote, "start": start, "end": end}


def _numbered_nodes(matches):
    roots, stack = [], []
    for match in matches:
        token = match.group("ascii") or match.group("wide") or match.group("circled") or match.group("plain").strip()[:-1]
        if token.isascii() and token.isdigit():
            family, number = "arabic", int(token)
        elif token in _CHINESE:
            family, number = "chinese", _CHINESE[token]
        elif token in _CIRCLED:
            family, number = "circle", _CIRCLED[token]
        else:
            family, number = "roman", _ROMAN.get(token.upper(), _UNICODE_ROMAN.get(token))
        style = ("plain" if match.group("plain") else "circle" if match.group("circled") else "paren", family)
        if number is None:
            return None
        node = {"match": match, "number": number, "style": style, "children": []}
        if not stack:
            if number != 1 and style != ("plain", "arabic"):
                return None
            roots.append(node)
        else:
            same_level = next((index for index in reversed(range(len(stack)))
                               if stack[index]["style"] == style and stack[index]["number"] + 1 == number), None)
            if same_level is not None:
                stack = stack[:same_level]
                (stack[-1]["children"] if stack else roots).append(node)
            elif number == 1 and style != stack[-1]["style"]:
                stack[-1]["children"].append(node)
            elif number == stack[-1]["number"] + 1 and not any(parent["style"] == style for parent in stack[:-1]):
                stack.pop()
                (stack[-1]["children"] if stack else roots).append(node)
            else:
                return None
        stack.append(node)
    def valid(nodes, *, root=False):
        single_question_container = (root and len(nodes) == 1
            and nodes[0]["style"] == ("plain", "arabic") and bool(nodes[0]["children"]))
        # A preserved paper question number (19., 24., …) is one container,
        # never permission to combine several original questions into a flat
        # subquestion sequence. Ordinary flat 1./2. task lists still work.
        if root and len(nodes) > 1 and any(node["style"] == ("plain", "arabic")
                                         and node["children"] for node in nodes):
            return False
        return (bool(nodes) and (len(nodes) >= 2 or root and bool(nodes[0]["children"]))
                and (single_question_container
                     or [node["number"] for node in nodes] == list(range(1, len(nodes) + 1)))
                and all(not node["children"] or valid(node["children"]) for node in nodes))
    return roots if valid(roots, root=True) else None


def _attach_goals(plan, *, whole_goals=None):
    goals = []
    for target in plan["targets"]:
        ranges = target.get("goal_ranges", whole_goals or [])
        for index, goal in enumerate(ranges, 1):
            goals.append({**goal, "id": f"{target['id']}:goal:{index}", "target_id": target["id"],
                          "label": target["label"] + (f"·目标{index}" if len(ranges) > 1 else "")})
    if len(goals) > MAX_DIAGNOSTIC_TARGETS:
        plan["goal_diagnostics_limited"] = True
        plan["detected_goal_count"] = len(goals)
        goals = []
    plan["goals"] = goals
    return plan


def extract_knowledge_annotation_targets(content: str) -> dict:
    """Recognize bounded consecutive levels; never infer labels or missing tasks."""
    content = content if isinstance(content, str) else ""
    base = {"mode": "whole_question", "content_sha256": _digest(content), "targets": [], "goals": []}
    if content.strip():
        start = len(content) - len(content.lstrip())
        base["targets"] = [_target(content, start, len(content), "全题", "whole")]
    else:
        return {**base, "reason": "empty_content"}
    protected = _ranges(content)
    visible = _visible(content, protected)
    matches = list(islice((match for match in _MARKER.finditer(content)
               if not any(left < match.end() and match.start() < right for left, right in protected)
               and (_safe_boundary(content, match.start()) or match.group("circled"))), MAX_SCAN_MARKERS + 1))
    if not matches:
        return _attach_goals({**base, "reason": "no_clear_sequence"},
                             whole_goals=_goal_ranges(content, visible, 0, len(content), protected))
    if not 2 <= len(matches) <= MAX_SCAN_MARKERS:
        return {**base, "reason": "no_clear_sequence"}
    roots = _numbered_nodes(matches)
    if roots is None:
        return {**base, "reason": "ambiguous_numbering"}
    targets = []
    positions = {match.start(): index for index, match in enumerate(matches)}
    def visit(nodes, ancestors=()):
        for node in nodes:
            match = node["match"]
            index = positions[match.start()]
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            body = visible[match.end():end].lstrip()
            explicit = bool(_ASK.search(body) or _TOOL_TASK.search(body))
            if not explicit and not node["children"]:
                return False
            path = (*ancestors, match.group().strip())
            if explicit:
                target = _target(content, match.start(), end, "/".join(path), str(len(targets) + 1))
                target["goal_ranges"] = _goal_ranges(content, visible, match.end(), end, protected)
                targets.append(target)
            if not visit(node["children"], path):
                return False
        return True
    if not visit(roots) or len(targets) < 2:
        return {**base, "reason": "not_explicit_tasks"}
    return _attach_goals({"mode": "subquestions", "content_sha256": base["content_sha256"], "targets": targets})


def knowledge_annotation_targets_for_prompt(plan: dict) -> list[dict]:
    """Expose exact anchors, without implementation offsets, to the same request."""
    if not isinstance(plan, dict) or not isinstance(plan.get("targets"), list):
        return []
    return [{**{key: item[key] for key in ("id", "label", "quote")},
             **({"goal_quotes": [goal["quote"] for goal in item["goal_ranges"]]}
                if isinstance(item.get("goal_ranges"), list) else {})}
            for item in plan["targets"] if isinstance(item, dict)
            and all(isinstance(item.get(key), str) for key in ("id", "label", "quote"))]


def validate_target_evidence(content: str, value) -> list[str] | None:
    """Validate one quote or <=12 quotes; return unchanged, deduplicated strings.

    This checks literal grounding only. Coverage separately requires a unique
    real subquestion range and never treats a scope string as proof.
    """
    if not isinstance(content, str):
        return None
    quotes = [value] if isinstance(value, str) else value
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= MAX_TARGET_QUOTES:
        return None
    if any(not isinstance(quote, str) or not quote.strip() or "\x00" in quote
           or len(quote) > MAX_TARGET_EVIDENCE_LENGTH or quote not in content for quote in quotes):
        return None
    return list(dict.fromkeys(quotes))


def _substantive_quote(quote: str) -> bool:
    value = _MARKER.sub("", quote)
    value = re.sub(r"[\s:：；;。?？!！,.，、]", "", value)
    return value not in _GENERIC_QUOTES and len(value) >= 2 and bool(re.search(r"[\w\u3400-\u9fff]", value))


def _quote_covers_goal(content, start, end, goal):
    return all(max(start, left) < min(end, right)
               and _substantive_quote(content[max(start, left):min(end, right)])
               for left, right in goal.get("object_parts", [(goal["object_start"], goal["end"])]))


def diagnose_knowledge_target_coverage(content: str, accepted_points, *, targets=None) -> dict:
    """Report missing main anchors without generating points or editing inputs.

    A quote matching two parts is ambiguous, even when scope names one part.
    New target_evidence takes precedence; an invalid new field cannot fall back
    to old evidence. Legacy content evidence remains usable for compatibility.
    Caller-supplied plans are never trusted over a fresh parse of this content.
    """
    content = content if isinstance(content, str) else ""
    plan = extract_knowledge_annotation_targets(content)
    literal_ranges = _literal_goal_ranges(content)
    covered = set()
    covered_goals = set()
    goals = plan.get("goals", [])
    point_targets = []
    points = accepted_points if isinstance(accepted_points, list) else []
    for point in points:
        if not isinstance(point, dict) or point.get("role") != "main":
            continue
        if "target_evidence" in point:
            kind = "target_evidence"
            quotes = validate_target_evidence(content, point["target_evidence"])
        elif point.get("evidence_source") == "content":
            kind = "legacy_content_evidence"
            quotes = validate_target_evidence(content, point.get("evidence"))
        else:
            kind, quotes = "none", None
        matched = set()
        matched_goals = set()
        ambiguous = 0
        unmatched = 0
        for quote in quotes or []:
            if not _substantive_quote(quote):
                unmatched += 1
                continue
            candidates = set()
            goal_occurrences = []
            offset = content.find(quote)
            while offset >= 0:
                end = offset + len(quote)
                if not any(left <= offset and end <= right for left, right in literal_ranges):
                    for target in plan["targets"]:
                        if not target["start"] <= offset <= end <= target["end"]:
                            continue
                        if not plan.get("goal_diagnostics_limited") and (plan["mode"] == "whole_question" or any(
                                _substantive_quote(content[max(offset, goal["object_start"]):min(end, goal["end"])])
                                for goal in target.get("goal_ranges", [])
                                if max(offset, goal["object_start"]) < min(end, goal["end"]))):
                            candidates.add(target["id"])
                            at_offset = {goal["id"] for goal in goals if goal["target_id"] == target["id"]
                                         and _quote_covers_goal(content, offset, end, goal)}
                            if at_offset:
                                goal_occurrences.append(frozenset(at_offset))
                offset = content.find(quote, offset + 1)
            if len(candidates) == 1:
                if not goals:
                    matched.update(candidates)
                elif goal_occurrences and len(set(goal_occurrences)) == 1:
                    matched_goals.update(goal_occurrences[0])
                elif goal_occurrences:
                    ambiguous += 1
                else:
                    unmatched += 1
            elif candidates:
                ambiguous += 1
            else:
                unmatched += 1
        if goals:
            matched.update(target["id"] for target in plan["targets"]
                if (owned := {goal["id"] for goal in goals if goal["target_id"] == target["id"]})
                and owned <= matched_goals)
        ordered = [target["id"] for target in plan["targets"] if target["id"] in matched]
        covered.update(ordered)
        covered_goals.update(matched_goals)
        point_targets.append({"code": point.get("code"), "target_ids": ordered,
                              "evidence_kind": kind, "invalid_target_evidence": kind == "target_evidence" and quotes is None,
                              "goal_ids": [goal["id"] for goal in goals if goal["id"] in matched_goals],
                              "ambiguous_quote_count": ambiguous, "unmatched_quote_count": unmatched})
    if goals:
        covered = {target["id"] for target in plan["targets"]
            if (owned := {goal["id"] for goal in goals if goal["target_id"] == target["id"]})
            and owned <= covered_goals}
    missing = [target for target in plan["targets"] if target["id"] not in covered]
    missing_goals = [goal for goal in goals if goal["id"] not in covered_goals]
    warnings = []
    if plan.get("goal_diagnostics_limited"):
        warnings.append("明确目标超过本次本地诊断上限，无法确认完整覆盖，请人工逐目标复核。")
    if plan["mode"] == "subquestions" and missing:
        warnings.append("以下明确小问尚无有效主要考查标注的题干目标依据："
                        + "、".join(target["label"] for target in missing)
                        + "。请人工核对；此提示不代表系统已判断应补哪个知识点。")
    if len(goals) > len(plan["targets"]) and missing_goals:
        warnings.append("以下明确待求/待证目标尚无主要考查依据："
                        + "、".join(goal["label"] + "（" + goal["quote"][:100] + "）" for goal in missing_goals)
                        + "。同一小问的一个目标不能代替其他目标；请人工核对。")
    coordinated = [goal for goal in missing_goals if goal.get("object_parts")]
    if coordinated:
        warnings.append("以下并列对象或产物尚未完整锚定，引用其中一项不能代表整组："
                        + "、".join(goal["quote"][:100] for goal in coordinated) + "。请补充覆盖整组的连续原文依据。")
    return {"mode": plan["mode"], "content_sha256": plan["content_sha256"],
            "target_count": len(plan["targets"]),
            "covered_target_ids": [target["id"] for target in plan["targets"] if target["id"] in covered],
            "uncovered_target_ids": [target["id"] for target in missing],
            "goal_count": len(goals),
            "covered_goal_ids": [goal["id"] for goal in goals if goal["id"] in covered_goals],
            "uncovered_goal_ids": [goal["id"] for goal in missing_goals],
            "goal_diagnostics_limited": plan.get("goal_diagnostics_limited", False),
            "detected_goal_count": plan.get("detected_goal_count", len(goals)),
            "point_targets": point_targets, "warnings": warnings,
            "targets_recomputed": targets is not None and targets != plan}
