"""Conservative, bounded dependencies in visible Word source text.

This is a source ownership check, not a semantic or extraction certificate.
Only unique explicit question numbers establish dependency edges. Ambiguous
references retain uncertainty and conservative grouping evidence instead of
inventing a target. Source text and question dictionaries are never changed.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import re

from mathbank.content_locks import _NUMBER
from mathbank.question_assets import _mask_literals, markdown_literal_ranges


MAX_SOURCE_CHARACTERS = 500_000
MAX_QUESTIONS = 1_000
MAX_LINES = 12_000
MAX_LITERAL_MARKERS = 512
MAX_IMAGE_MARKERS = 2_048
MAX_PROTECTED_RANGES = 2_048
MAX_REFERENCES = 256

_IMAGE = re.compile(
    r"!\[(?:\\.|[^\]\\])*\]\([^\n]*?\)"
    r"|\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{[^{}]*\}"
    r"|<img\b[^>]*>", re.I,
)
_URL = re.compile(r"(?:https?|ftp)://[^\s<>\[\]{}\"'，。；]+", re.I)
_LITERAL_MARKER = re.compile(
    r"`|~{3,}|<!--|<(?:pre|code)\b|"
    r"\\(?:begin\s*\{(?:verbatim\*?|Verbatim\*?|lstlisting|minted|tikzpicture)\}"
    r"|verb|Verb|lstinline|mintinline|detokenize|url|path)(?![A-Za-z])", re.I,
)
_MATH_OPEN = re.compile(
    r"\$\$?|\\[\[(]|\\begin\s*\{"
    r"(equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath|math|"
    r"aligned|alignedat|gathered|split|array|cases|[pbBvV]?matrix|smallmatrix)\}",
)
_NUMBER_LIST = r"(?:第\s*)?\d{1,4}\s*(?:题)?(?:\s*[、,，与和及～~\-至到]\s*(?:第\s*)?\d{1,4}\s*(?:题)?){1,9}"
_SHARED_NOUN = r"(?:同一(?:幅|张|个|组)?\s*)?(?:条件|函数|图(?:片|形|表|甲|乙)?|表(?:格)?|材料|说明|数据|背景|信息|结论)"
_NUMBERED_SHARED = re.compile(
    r"(?P<numbers>" + _NUMBER_LIST + r")\s*(?:题)?\s*"
    r"(?:共用|共享|使用同一|共用同一)\s*" + _SHARED_NOUN,
)
_RELATIVE_SHARED = re.compile(
    r"(?:本题|此题|这题)?\s*(?:与|和|及)\s*(?P<relative>下题|下一题|上题|上一题|前一题)"
    r"\s*(?:共用|共享)\s*" + _SHARED_NOUN,
)
_ONE_TARGET_SHARED = re.compile(
    r"(?:本题|此题|这题)?\s*(?:与|和|及)\s*(?:第\s*)?(?P<number>\d{1,4})\s*题"
    r"\s*(?:共用|共享)\s*" + _SHARED_NOUN,
)
_UNSCOPED_SHARED = re.compile(
    r"(?:各题|本组(?:各)?题|本题组|这(?:几|两|二|三)题|相邻(?:的)?(?:两|二)题|"
    r"以下(?:所有|各)题|下列各题|本卷各题)\s*(?:共用|共享)\s*" + _SHARED_NOUN,
)
_COUNT_SHARED = re.compile(
    r"(?:以下|下列|接下来(?:的)?)\s*(?P<count>[二两三四五六七八九十]|[2-9]|10)\s*(?:道|个)?题"
    r"\s*(?:共用|共享)\s*" + _SHARED_NOUN,
)
_REFERENCE = re.compile(
    r"(?:(?P<verb>根据|利用|结合|参照|参考|沿用|借助|使用|采用|由|见)\s*(?:第\s*)?"
    r"(?P<prefix_number>\d{1,4})\s*题"
    r"|第\s*(?P<suffix_number>\d{1,4})\s*题\s*(?:中(?:的)?|所给(?:的)?|给出(?:的)?|的)\s*"
    r"(?:函数|图(?:甲|乙|形|片|表)?|表(?:格)?|条件|结论|结果|数据|材料|定义|说明))",
)
_IMPLICIT = re.compile(
    r"同上|(?:如|根据|利用|结合)\s*上(?:图|表)|"
    r"(?:上述|以上|前述|上方(?:的)?|上面(?:的)?|前面(?:的)?)\s*"
    r"(?:图(?:甲|乙|形|片|表)?|表(?:格)?|条件|函数|材料|说明|结论|结果|数据)"
    r"|(?:上一题|前一题|上题|下一题|下题)\s*(?:中(?:的)?|的|所给(?:的)?)\s*"
    r"(?:函数|图(?:甲|乙|形|片|表)?|表(?:格)?|条件|结论|结果|数据|材料)",
)
_DATA_TABLE_TOKEN = re.compile(r"\\(begin|end)\{(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)\}")


def _local_data_table_reference(source: str, view: str, start: int, end: int):
    """Prove one immediately introduced, complete native data table reference.

    Only the canonical rectangular tabular emitted by native Word extraction
    qualifies. This is local source ownership, not a claim about its values.
    Literal/math masking uses the caller's existing protected view, so a table
    mentioned in a formula or code cannot become structural evidence.
    """
    local = view[start:end]
    tokens = [token for token in _DATA_TABLE_TOKEN.finditer(local) if not _escaped(local, token.start())]
    if (len(tokens) != 2 or tokens[0].groups() != ("begin", "tabular")
            or tokens[1].groups() != ("end", "tabular")):
        return None
    first, last = tokens
    if (re.search(r"上一题|上题|前一题|前题|下一题|下题|第\s*\d{1,4}\s*题|共用|共享", local)
            or re.search(r"\\(?:begin|end)\{", local[first.end():last.start()])):
        return None
    introduction = re.search(r"数据[^\n。；;：:]{0,120}如下[：:]\s*$", local[:first.start()])
    table_reference = re.match(r"\s*(?P<data>根据上表)(?=[，,。；; \t]|$)", local[last.end():])
    if introduction is None and table_reference is None:
        return None
    columns = re.match(r"\s*\{([|clr \t]+)\}", local[first.end():last.start()])
    if columns is None:
        return None
    width = len(re.findall(r"[clr]", columns.group(1)))
    if not 2 <= width <= 64:
        return None
    body = local[first.end() + columns.end():last.start()]
    if re.search(r"\\(?:multirow|multicolumn|cline|newcommand|renewcommand)\b", body):
        return None
    # Plain TeX braces must also close; complete math was already protected.
    depth = 0
    for position, char in enumerate(body):
        if char not in "{}" or _escaped(body, position):
            continue
        depth += 1 if char == "{" else -1
        if depth < 0:
            return None
    if depth:
        return None
    rows = re.split(r"\\\\", body)
    if re.sub(r"\\hline\b", "", rows[-1]).strip():
        return None  # A final unterminated row is not a complete source table.
    rows = [re.sub(r"\\hline\b", "", row).strip() for row in rows[:-1]]
    if not 2 <= len(rows) <= 1_000 or any(not row for row in rows):
        return None
    for row in rows:
        separators = [m.start() for m in re.finditer("&", row) if not _escaped(row, m.start())]
        if len(separators) != width - 1:
            return None
    original_table = source[start + first.start():start + last.end()]
    if not re.search(r"\d", original_table):
        return None
    reference = table_reference or re.match(r"\s*(?:由|根据)[ \t]*(?P<data>(?:以上|上述)[ \t]*数据)", local[last.end():])
    if reference is None:
        return None
    offset = start + last.end()
    return {"table_range": [start + first.start(), start + last.end()],
            "introduction_range": ([start + introduction.start(), start + introduction.end()]
                                   if introduction is not None else None),
            "reference_range": [offset + reference.start("data"), offset + reference.end("data")]}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _merged(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _escaped(source: str, position: int) -> bool:
    cursor = position - 1
    while cursor >= 0 and source[cursor] == "\\":
        cursor -= 1
    return (position - cursor - 1) % 2 == 1


def _protected(source: str):
    """Reuse literal rules; locate complete math with one forward scan."""
    literal = _merged(markdown_literal_ranges(source, include_links=False))
    starts = [start for start, _ in literal]
    ranges = list(literal)
    images = []
    # Hide Markdown literal ranges before the independent TeX-literal scan.
    initial = list(source)
    for start, end in literal:
        initial[start:end] = ["\n" if char == "\n" else " " for char in source[start:end]]
    base = _mask_literals("".join(initial), tex_comments=False)

    def literal_end(position):
        index = bisect_right(starts, position) - 1
        return literal[index][1] if index >= 0 and position < literal[index][1] else None

    for match in _IMAGE.finditer(source):
        if literal_end(match.start()) is None and base[match.start():match.start() + 1] == source[match.start():match.start() + 1]:
            images.append(match.span())
    ranges.extend(images)
    ranges.extend(match.span() for match in _URL.finditer(source))
    ranges.extend(match.span() for match in re.finditer(r"\[[^\[\]\n]*\]\([^\n)]*\)", source))
    if len(ranges) > MAX_PROTECTED_RANGES:
        return "", images, "protected_range_limit", 0
    chars = list(base)
    for start, end in _merged(ranges):
        chars[start:end] = ["\n" if char == "\n" else " " for char in source[start:end]]
    view = "".join(chars)
    cursor = 0
    math_count = 0
    while match := _MATH_OPEN.search(view, cursor):
        covered_end = literal_end(match.start())
        if covered_end is not None:
            cursor = covered_end
            continue
        if _escaped(source, match.start()):
            cursor = match.end()
            continue
        closing = ("\\end{" + match.group(1) + "}") if match.group(1) else {
            "$": "$", "$$": "$$", "\\(": "\\)", "\\[": "\\]",
        }[match.group()]
        end = view.find(closing, match.end())
        while end >= 0 and _escaped(source, end):
            end = view.find(closing, end + len(closing))
        if end < 0:
            return "", images, "unclosed_math_context", math_count
        cursor = end + len(closing)
        ranges.append((match.start(), cursor))
        math_count += 1
        if len(ranges) > MAX_PROTECTED_RANGES:
            return "", images, "protected_range_limit", math_count
    if len(ranges) > MAX_PROTECTED_RANGES:
        return "", images, "protected_range_limit", math_count
    for start, end in _merged(ranges):
        chars[start:end] = ["\n" if char == "\n" else " " for char in source[start:end]]
    return "".join(chars), images, None, math_count


def analyze_word_source_dependencies(source: str, questions: Sequence[Mapping]) -> dict:
    """Report references for existing local source ranges, without certification.

    ``questions`` uses the current source-metadata fields: ``id``,
    ``source_number``, ``source_range`` and optional ``raw_content``. A whole
    source path may certify independence only for a complete report with no
    dependencies or unresolved references. The conservative groups in this
    version must not be treated as exhaustive scopes for partial certification.
    """
    report = {
        "schema": "mathbank.word-source-dependencies.v1", "status": "complete",
        "source_sha256": _sha(source) if isinstance(source, str) else None,
        "has_dependencies": False, "has_unresolved": False, "references": [],
        "groups": [], "question_risks": [], "limits": [],
        "partial_scope_certified": False,
        "budget": {"source_characters": len(source) if isinstance(source, str) else 0,
                   "question_count": 0, "reference_count": 0, "math_spans": 0},
    }

    def unavailable(reason):
        report.update(status="unavailable", has_unresolved=True)
        report["limits"].append(reason)
        return report

    if not isinstance(source, str) or len(source) > MAX_SOURCE_CHARACTERS:
        return unavailable("source_limit_or_type")
    if source.count("\n") > MAX_LINES:
        return unavailable("line_limit")
    literal_count = 0
    for _ in _LITERAL_MARKER.finditer(source):
        literal_count += 1
        if literal_count > MAX_LITERAL_MARKERS:
            return unavailable("literal_marker_limit")
    if source.count("![") + source.count("\\includegraphics") + source.lower().count("<img") > MAX_IMAGE_MARKERS:
        return unavailable("image_marker_limit")
    if not isinstance(questions, (list, tuple)) or len(questions) > MAX_QUESTIONS:
        return unavailable("question_limit_or_type")
    entries = []
    ownership = []
    seen_ids = set()
    previous_end = 0
    for index, question in enumerate(questions):
        if not isinstance(question, Mapping):
            return unavailable("invalid_question_range")
        identifier, bounds = question.get("id"), question.get("source_range")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 128 or identifier in seen_ids
                or not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(type(value) is not int for value in bounds)):
            return unavailable("invalid_question_range")
        start, end = bounds
        if not 0 <= previous_end <= start < end <= len(source):
            return unavailable("invalid_question_range")
        if "raw_content" in question and question["raw_content"] != source[start:end]:
            return unavailable("question_source_changed")
        number = question.get("source_number")
        if number is not None and (type(number) is not int or number <= 0):
            return unavailable("invalid_question_number")
        entries.append({"id": identifier, "number": number, "start": start, "end": end, "index": index})
        ownership.append((start, end, index))
        answer_bounds = question.get("answer_source_range")
        if answer_bounds is not None:
            if (not isinstance(answer_bounds, (list, tuple)) or len(answer_bounds) != 2
                    or any(type(value) is not int for value in answer_bounds)
                    or not 0 <= answer_bounds[0] < answer_bounds[1] <= len(source)):
                return unavailable("invalid_answer_range")
            if "raw_answer" in question and question["raw_answer"] != source[slice(*answer_bounds)]:
                return unavailable("answer_source_changed")
            ownership.append((*answer_bounds, index))
        seen_ids.add(identifier)
        previous_end = end
    report["budget"]["question_count"] = len(entries)
    if not entries:
        return unavailable("no_question_ranges")
    ownership.sort()
    if any(left[1] > right[0] for left, right in zip(ownership, ownership[1:])):
        return unavailable("overlapping_source_ownership")
    view, images, error, math_count = _protected(source)
    report["budget"]["math_spans"] = math_count
    if error:
        return unavailable(error)
    chars = list(view)
    for left, right, _ in ownership:
        raw = source[left:right]
        offset = len(raw) - len(raw.lstrip())
        heading = _NUMBER.match(raw.lstrip())
        if heading:
            start, end = left + offset, left + offset + heading.end()
            chars[start:end] = " " * (end - start)
    view = "".join(chars)
    starts = [entry["start"] for entry in entries]
    ownership_starts = [bounds[0] for bounds in ownership]
    by_number = defaultdict(list)
    for entry in entries:
        if entry["number"] is not None:
            by_number[entry["number"]].append(entry["index"])
    parents = list(range(len(entries)))
    risks = defaultdict(set)
    consumed = []
    local_data_proofs = {}

    def owner(position):
        index = bisect_right(ownership_starts, position) - 1
        return ownership[index][2] if index >= 0 and position < ownership[index][1] else None

    image_records = []
    for left, right in images:
        position = bisect_right(ownership_starts, left) - 1
        member = ownership[position][2] if position >= 0 and right <= ownership[position][1] else None
        image_records.append((left, right, member))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def record(match, kind, members, *, unresolved=False, numbered_targets=None):
        if len(report["references"]) >= MAX_REFERENCES:
            return False
        members = sorted(set(members))
        for index in members:
            risks[index].add(kind)
            if members:
                parents[find(index)] = find(members[0])
        report["references"].append({
            "kind": kind, "source_range": list(match.span()), "evidence": source[match.start():match.end()],
            "owner_id": entries[owner(match.start())]["id"] if owner(match.start()) is not None else None,
            "member_ids": [entries[index]["id"] for index in members],
            "target_numbers": numbered_targets or [], "unresolved": unresolved,
            "scope_exhaustive": not unresolved,
        })
        report["has_unresolved"] |= unresolved
        report["has_dependencies"] |= not unresolved and len(members) > 1
        consumed.append(match.span())
        return True

    def resolved_numbers(match, numbers, include_owner=True):
        indices = []
        unresolved = False
        for number in numbers:
            targets = by_number.get(number, [])
            if len(targets) != 1:
                unresolved = True
            else:
                indices.extend(targets)
        source_owner = owner(match.start())
        if include_owner and source_owner is not None:
            indices.append(source_owner)
        return indices, unresolved

    for match in _NUMBERED_SHARED.finditer(view):
        numbers = [int(value) for value in re.findall(r"\d{1,4}", match.group("numbers"))]
        if re.search(r"[～~\-至到]", match.group("numbers")):
            if len(numbers) == 2 and 0 < numbers[1] - numbers[0] < MAX_QUESTIONS:
                numbers = list(range(numbers[0], numbers[1] + 1))
            else:
                if not record(match, "shared_question_range_unresolved", [], unresolved=True, numbered_targets=numbers):
                    return unavailable("reference_limit")
                continue
        members, uncertain = resolved_numbers(match, numbers)
        if not record(match, "explicit_shared_source", members, unresolved=uncertain, numbered_targets=numbers):
            return unavailable("reference_limit")
    for match in _RELATIVE_SHARED.finditer(view):
        index = owner(match.start())
        other = index + (1 if match.group("relative") in {"下题", "下一题"} else -1) if index is not None else -1
        members = [index, other] if index is not None and 0 <= other < len(entries) else ([index] if index is not None else [])
        if not record(match, "adjacent_shared_source", members, unresolved=len(members) != 2):
            return unavailable("reference_limit")
    for match in _ONE_TARGET_SHARED.finditer(view):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        members, uncertain = resolved_numbers(match, [int(match.group("number"))])
        if owner(match.start()) is None:
            uncertain = True
        if not record(match, "explicit_shared_source", members, unresolved=uncertain, numbered_targets=[int(match.group("number"))]):
            return unavailable("reference_limit")
    for match in _UNSCOPED_SHARED.finditer(view):
        index = owner(match.start())
        members = [index] if index is not None else list(range(len(entries)))
        if not record(match, "shared_scope_unresolved", members, unresolved=True):
            return unavailable("reference_limit")
    for match in _COUNT_SHARED.finditer(view):
        index = owner(match.start())
        first = index if index is not None else bisect_right(starts, match.start())
        value = match.group("count")
        count = int(value) if value.isdecimal() else (2 if value == "两" else "零一二三四五六七八九十".index(value))
        members = list(range(first, min(first + count, len(entries))))
        if not record(match, "adjacent_shared_source", members, unresolved=len(members) != count):
            return unavailable("reference_limit")
    for match in _REFERENCE.finditer(view):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        index = owner(match.start())
        if index is None:
            continue  # Plain document titles/administrative headings do not own a question.
        number = int(match.group("prefix_number") or match.group("suffix_number"))
        members, uncertain = resolved_numbers(match, [number])
        # References to this same uniquely identified question are not cross-question dependencies.
        if not uncertain and set(members) == {index}:
            continue
        if not record(match, "explicit_question_reference", members, unresolved=uncertain, numbered_targets=[number]):
            return unavailable("reference_limit")
    for match in _IMPLICIT.finditer(view):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        index = owner(match.start())
        if index is None:
            continue
        text = match.group()
        if re.fullmatch(r"(?:以上|上述)[ \t]*数据|根据上表", text) and entries[index]["start"] <= match.start() < entries[index]["end"]:
            if index not in local_data_proofs:
                entry = entries[index]
                local_data_proofs[index] = _local_data_table_reference(source, view, entry["start"], entry["end"])
            proof = local_data_proofs[index]
            if proof is not None and list(match.span()) == proof["reference_range"]:
                report.setdefault("local_data_references", []).append({
                    "owner_id": entries[index]["id"], **proof,
                    "evidence": source[match.start():match.end()],
                    "reason": "one_complete_local_data_table_immediately_introduced_and_referenced",
                })
                continue
        preceding = [bounds for bounds in image_records if bounds[1] <= match.start() and bounds[2] == index]
        prior_external_images = [bounds for bounds in image_records if bounds[1] <= match.start() and bounds[2] != index]
        # A unique preceding image in this same question supplies local ownership,
        # unlike a preamble image. Pixels (including an empty bitmap) are irrelevant.
        if (re.search("图|表", text) and len(preceding) == 1 and not prior_external_images
                and "题" not in text and "同上" not in text):
            continue
        local_prefix = view[entries[index]["start"]:match.start()]
        if (index == 0 and entries[index]["start"] == 0
                and re.search("条件|函数|结论|结果|数据", text) and "题" not in text
                and re.search(r"(?:已知|设|给定|定义)[^\n]{0,300}[。；;]", local_prefix)):
            continue
        kind = "prior_external_visual_reference" if re.search("图|表", text) and prior_external_images else "implicit_source_reference"
        if text.startswith(("下一题", "下题")):
            members = list(range(index, len(entries)))
        elif text == "同上":
            members = list(range(max(0, index - 1), index + 1))
        else:
            members = list(range(index + 1))
        if not record(match, kind, members, unresolved=True):
            return unavailable("reference_limit")
    grouped = defaultdict(list)
    for index in sorted(risks):
        grouped[find(index)].append(index)
    for members in grouped.values():
        group_refs = [item for item in report["references"] if set(item["member_ids"]) & {entries[index]["id"] for index in members}]
        group_key = ":".join(entries[index]["id"] for index in members)
        report["groups"].append({
            "id": "DEP_" + _sha(report["source_sha256"] + ":" + group_key)[:18],
            "member_ids": [entries[index]["id"] for index in members],
            "question_indices": members,
            "source_range": [entries[members[0]]["start"], entries[members[-1]]["end"]],
            "reasons": sorted(set().union(*(risks[index] for index in members))),
            "unresolved": any(item["unresolved"] for item in group_refs),
            "scope_exhaustive": not any(item["unresolved"] for item in group_refs),
        })
    report["question_risks"] = [{"id": entries[index]["id"], "question_index": index,
                                 "reasons": sorted(reasons)} for index, reasons in sorted(risks.items())]
    report["budget"]["reference_count"] = len(report["references"])
    return report
