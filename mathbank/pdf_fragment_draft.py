"""Build editable PDF cards from explicit first-pass fragment relationships.

No whole-document question/answer regex, math masking or body generation is
used. Unproved relationships stay separate; broken local math cannot consume a
following block. Source ranges and the original score/formula bytes survive.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import re

from .pdf_fragment_sources import checked_fragment_page, text_sha, MAX_SOURCE
from .pdf_page_fragments import number_prefix, _math_needs_review
from .question_assets import embedded_question_assets, markdown_literal_ranges

SCHEMA = "mathbank.pdf-local-draft.v1"
RULES_VERSION = "pdf-fragment-draft.rules.v4"
_QUESTION_ROLES = {"stem", "answer", "answer_key"}
_SECTION_INSTRUCTION = re.compile(
    r"[一二三四五六七八九十]+[、.．]"
    r"(?P<kind>单项选择题|多项选择题|选择题|填空题|解答题|证明题)[:：]"
    r"本题共[1-9][0-9]*小题[，,](?:每小题[1-9][0-9]*分[，,])?共[1-9][0-9]*分[。.]"
    r"(?:(?P<choice>在每小题给出的(?:四个)?选项中[，,]"
    r"(?:只有(?:一项|一个)是符合题目要求的|有多项符合题目要求)[。.]"
    r"(?:全部选对的得[1-9][0-9]*分[，,]部分选对的得部分分[，,]有选错的得0分[。.])?)"
    r"|(?P<solution>解答应写出文字说明、证明过程或演算步骤[。.]))?"
)


def _hard(reasons):
    return [reason for reason in reasons if reason.startswith("number_")
            or reason in {"unknown_role", "unknown_edge"}]


def _page_records(source, pages):
    numbers = [page.get("page_number") if isinstance(page, dict) else None for page in pages]
    if (any(type(n) is not int or n < 1 for n in numbers) or numbers != sorted(set(numbers))):
        raise ValueError("PDF 片段来源物理页顺序无效。")
    chunks = [page["markdown"].strip() for page in pages if isinstance(page, dict)
              and isinstance(page.get("markdown"), str) and page["markdown"].strip()]
    if "\n\n".join(chunks) != source:
        raise ValueError("PDF 片段题稿与当前逐页原文不一致。")
    records, page_ranges, cursor, seen_text = [], {}, 0, False
    context = ""
    for page in pages:
        metadata = checked_fragment_page(page)
        text = page["markdown"]
        trimmed = text.strip()
        left_trim = len(text) - len(text.lstrip())
        if trimmed and seen_text:
            cursor += 2
        start = cursor
        cursor += len(trimmed)
        page_ranges[page["page_number"]] = {"range": [start, cursor], "empty": not trimmed}
        seen_text = seen_text or bool(trimmed)
        if metadata is None:
            if trimmed:
                records.append({"id": "manual-page-" + str(page["page_number"]), "page_number": page["page_number"],
                    "role": "unknown", "number": None, "edge": "unknown", "start": start, "end": cursor,
                    "text": trimmed, "raw_text": text, "prefix": 0,
                    "pending_reasons": ["fragment_metadata_missing"], "context": context})
            continue
        for block in metadata["blocks"]:
            a, b = block["range"]
            # Current source uses the established page-merge surrounding trim.
            # It is never allowed to discard a non-whitespace source character.
            local_a, local_b = max(a, left_trim), min(b, left_trim + len(trimmed))
            if local_b <= local_a:
                continue
            original = text[a:b]
            record = {**deepcopy(block), "page_number": page["page_number"],
                "start": start + local_a - left_trim, "end": start + local_b - left_trim,
                "text": text[local_a:local_b], "raw_text": original,
                "prefix": 0, "context": context, "page_sha256": metadata["source_sha256"]}
            visible, prefix = number_prefix(original)
            if visible is not None:
                record["visible_number"] = visible
                record["prefix"] = max(0, min(record["end"] - record["start"], a + prefix - local_a))
            if block["role"] == "context":
                context = original
                record["context"] = context
            records.append(record)
    return records, page_ranges


def _answer_heads(text):
    """Read only independent solution heads in one already typed answer block.

    A top-level printed number must be followed by an explicit solution label.
    Code, formulas, indented lists and an unclosed preceding math span cannot
    establish an identity. This is never run over the whole paper.
    """
    from .content_locks import _formulas
    literals = list(markdown_literal_ranges(text))
    protected = [(part.start, part.end) for part in _formulas(text)]
    candidates, offset, ambiguous = [], 0, False
    for line in text.splitlines(keepends=True):
        number, prefix = number_prefix(line)
        labelled = (number is not None and not line.startswith((" ", "\t"))
            and re.match(r"(?:[（(]1[）)]\s*)?(?:解\s*[:：]|证明\s*[:：]|【答案】)", line[prefix:]))
        if labelled and not any(a <= offset < b for a, b in literals):
            if any(a <= offset < b for a, b in protected) or _math_needs_review(text[:offset]):
                ambiguous = True
            else:
                candidates.append((offset, number))
        offset += len(line)
    return candidates, ambiguous


def _record_receipt(source, record):
    return {"id": record["id"], "role": record["role"], "number": record.get("number"),
        "edge": record["edge"], "range": [record["start"], record["end"]],
        "text_sha256": text_sha(source[record["start"]:record["end"]])}


def _parent_receipt(source, record, reason):
    parent_range = record.get("original_source_range", [record["start"], record["end"]])
    return {"reason": reason, "page_number": record["page_number"],
        "parent_block_id": record.get("parent_block_id", record["id"]),
        "record_id": record["id"], "record_range": [record["start"], record["end"]],
        "parent_range": list(parent_range),
        "parent_text_sha256": text_sha(source[slice(*parent_range)]),
        "original_block_text_sha256": record.get("original_block_text_sha256", record["text_sha256"]),
        "original_edge": record.get("original_edge", record["edge"]), "prior_derived_edge": record["edge"],
        "original_pending_reasons": list(record["pending_reasons"]), "original_verified": False}


def _mark_derived_edge(source, record, edge, reason, repairs, *, peer):
    """Change a working relation only, with an immutable source-range receipt."""
    receipt = _parent_receipt(source, record, reason)
    record.setdefault("original_edge", record["edge"])
    record["edge"] = edge
    record["pending_reasons"] = list(dict.fromkeys(record["pending_reasons"] + [reason]))
    receipt.update(parts=[_record_receipt(source, record)], peer=_record_receipt(source, peer))
    repairs.append(receipt)


def _recover_section_context(source, records):
    """Recover only a complete conventional section instruction, never a stem.

    The match covers the whole unnumbered block and needs an immediately
    following valid printed question on the same physical page. A short
    heading, embedded instruction, quotation or extra question text cannot
    establish a new section. The raw sidecar and every source byte stay intact.
    """
    repairs, context = [], ""
    for index, record in enumerate(records):
        following = records[index + 1] if index + 1 < len(records) else None
        text = source[record["start"]:record["end"]]
        compact = re.sub(r"\s+", "", text)
        instruction = _SECTION_INSTRUCTION.fullmatch(compact)
        complete_instruction = bool(instruction
            and (not instruction["choice"] or "选择题" in instruction["kind"])
            and (not instruction["solution"] or instruction["kind"] in {"解答题", "证明题"}))
        if (record["role"] == "stem" and record.get("number") is None
                and record.get("visible_number") is None and record["edge"] == "none"
                and set(record["pending_reasons"]) <= {"number_missing"}
                and len(text) <= 800 and complete_instruction
                and following and following["role"] == "stem"
                and following["page_number"] == record["page_number"]
                and record["end"] == following["start"]
                and type(following.get("number")) is int
                and following.get("visible_number") == following["number"]
                and following["prefix"] and not _hard(following["pending_reasons"])):
            receipt = _parent_receipt(source, record, "fragment_section_context_recovered")
            record["role"] = "context"
            record["pending_reasons"] = []
            receipt.update(original_role="stem", derived_role="context", requires_review=False,
                page_sha256=record.get("page_sha256"), parts=[_record_receipt(source, record)],
                peer=_record_receipt(source, following))
            repairs.append(receipt)
        if record["role"] == "context":
            context = record["raw_text"]
        record["context"] = context
    return records, repairs


def _unique_local_answer_heads(source, records):
    stems, answers = defaultdict(list), defaultdict(list)
    for record in records:
        number = record.get("visible_number")
        if record["role"] == "stem" and type(number) is int:
            stems[number].append(record["id"])
        if record["role"] == "answer":
            heads, _ambiguous = _answer_heads(source[record["start"]:record["end"]])
            for offset, found in heads:
                answers[found].append((record["id"], offset))
            if type(number) is int and not any(offset == 0 and found == number for offset, found in heads):
                answers[number].append((record["id"], 0))
    return lambda number: len(stems[number]) == len(answers[number]) == 1


def _recover_local_answer_relations(source, records):
    """Derive same-page answer edges bounded by two unique consecutive heads.

    A missing incoming hint on an unnumbered tail is recoverable only between
    answer N and answer N+1, with exact block adjacency and unique matching
    printed stems. An independent answer head also disproves the preceding
    outgoing-page hint. No text is inferred or copied across another role.
    """
    unique, repairs = _unique_local_answer_heads(source, records), []
    for previous, current, following in zip(records, records[1:], records[2:]):
        number = previous.get("number")
        if (type(number) is not int or not unique(number) or not unique(number + 1)
                or previous["role"] != "answer" or current["role"] != "answer" or following["role"] != "answer"
                or previous.get("visible_number") != number or not previous["prefix"]
                or current.get("number") is not None or current.get("visible_number") is not None
                or current["edge"] != "none" or previous["edge"] not in {"none", "to_next"}
                or following.get("number") != number + 1 or following.get("visible_number") != number + 1
                or not following["prefix"] or following["edge"] == "unknown"
                or previous["page_number"] != current["page_number"] or current["page_number"] != following["page_number"]
                or previous["end"] != current["start"] or current["end"] != following["start"]
                or _hard(previous["pending_reasons"]) or _hard(following["pending_reasons"])
                or set(_hard(current["pending_reasons"])) - {"number_missing"}
                or any(number_prefix(line)[0] is not None and not line.startswith((" ", "\t"))
                       for line in source[current["start"]:current["end"]].splitlines())):
            continue
        reason = "fragment_answer_same_page_tail_recovered"
        _mark_derived_edge(source, previous, "to_next", reason, repairs, peer=current)
        receipt = _parent_receipt(source, current, reason)
        current.setdefault("original_edge", current["edge"])
        current["edge"] = "from_previous"
        current["pending_reasons"] = [value for value in current["pending_reasons"] if value != "number_missing"]
        current["pending_reasons"].append(reason)
        receipt.update(parts=[_record_receipt(source, current)], peer=_record_receipt(source, previous),
                       following_head=_record_receipt(source, following), source_number=number)
        repairs.append(receipt)

    for previous, following in zip(records, records[1:]):
        number = previous.get("number")
        if (type(number) is int and unique(number) and unique(number + 1)
                and previous["role"] == following["role"] == "answer"
                and previous.get("visible_number") == number and previous["prefix"]
                and previous["edge"] in {"to_next", "both"}
                and following.get("number") == following.get("visible_number") == number + 1
                and following["prefix"] and following["edge"] != "unknown"
                and previous["page_number"] == following["page_number"]
                and previous["end"] == following["start"]
                and not _hard(previous["pending_reasons"]) and not _hard(following["pending_reasons"])):
            _mark_derived_edge(source, previous, "from_previous" if previous["edge"] == "both" else "none",
                "fragment_answer_outgoing_ignored_for_same_page_head", repairs, peer=following)
    return records, repairs


def _recover_embedded_answer_head(source, records, pages):
    """Derive bounded answer ranges and noisy edges; never change source text.

    Every inferred number needs one uniquely printed stem and no competing
    answer head. Cross-page joins need actual physical content edges plus an
    incoming answer and the next consecutive independent answer identity.
    All recovered relationships remain pending, even when all answers exist.
    """
    stems, original_heads = defaultdict(list), defaultdict(list)
    for record in records:
        if (record["role"] == "stem" and type(record.get("number")) is int
                and record.get("visible_number") == record["number"] and not _hard(record["pending_reasons"])):
            stems[record["number"]].append(record)
        if record["role"] == "answer":
            heads, _ambiguous = _answer_heads(source[record["start"]:record["end"]])
            for _offset, number in heads:
                original_heads[number].append(record["id"])
            # A numbered ordinary answer need not contain a solution label.
            if record.get("number") == record.get("visible_number") and type(record.get("number")) is int:
                if record["id"] not in original_heads[record["number"]]:
                    original_heads[record["number"]].append(record["id"])
    output, repairs = [], []

    def unique(number):
        return len(stems[number]) == 1 and len(original_heads[number]) == 1

    for record in records:
        if record["role"] != "answer" or _hard(record["pending_reasons"]):
            output.append(record)
            continue
        text = source[record["start"]:record["end"]]
        heads, ambiguous = _answer_heads(text)
        candidates = [(offset, number) for offset, number in heads if offset > 0]
        if not candidates:
            if ambiguous:
                record["pending_reasons"].append("number_embedded_answer_boundary_unproved")
            output.append(record)
            continue
        previous = next((value for value in reversed(output) if value["role"] != "furniture"), None)
        prior_number = record.get("number")
        own_head = (type(prior_number) is int and record.get("visible_number") == prior_number
                    and bool(record["prefix"]))
        incoming = record["edge"] in {"from_previous", "both"}
        if not own_head:
            prior_number = previous.get("number") if previous else None
        proved = own_head or bool(incoming and previous and previous["role"] == "answer"
            and previous["edge"] in {"none", "to_next", "both"}
            and type(prior_number) is int and previous.get("visible_number") == prior_number
            and not _hard(previous["pending_reasons"])
            and previous["page_number"] + 1 == record["page_number"])
        numbers = [number for _offset, number in candidates]
        if (not proved or ambiguous or not unique(prior_number)
                or numbers != list(range(prior_number + 1, prior_number + 1 + len(numbers)))
                or any(not unique(number) for number in numbers)):
            record["pending_reasons"].append("number_embedded_answer_boundary_unproved")
            output.append(record)
            continue
        cuts = [record["start"], *[record["start"] + offset for offset, _number in candidates], record["end"]]
        parts = []
        for index, (a, b) in enumerate(zip(cuts, cuts[1:])):
            part = deepcopy(record)
            number = prior_number if index == 0 and own_head else None if index == 0 else numbers[index-1]
            edge = "from_previous" if index == 0 and not own_head else "none"
            if index == len(cuts)-2 and record["edge"] in {"to_next", "both"}:
                edge = "to_next"
            part.update(id=record["id"] + ":part" + str(index+1), start=a, end=b,
                text=source[a:b], raw_text=source[a:b], number=number, edge=edge,
                original_edge=record["edge"], parent_block_id=record["id"],
                original_source_range=[record["start"], record["end"]],
                original_block_text_sha256=record["text_sha256"],
                text_sha256=text_sha(source[a:b]), prefix=0)
            part.pop("visible_number", None)
            part["pending_reasons"] = list(dict.fromkeys(part["pending_reasons"] + ["fragment_answer_boundary_recovered"]))
            visible, prefix = number_prefix(part["text"])
            if visible is not None:
                part.update(visible_number=visible, prefix=prefix)
            parts.append(part)
        receipt = _parent_receipt(source, record, "fragment_answer_boundary_recovered")
        receipt.update(previous_source_number=prior_number, next_source_number=numbers[0],
            split_offset=candidates[0][0], split_offsets=[offset for offset, _n in candidates],
            source_numbers=numbers, parts=[_record_receipt(source, part) for part in parts])
        repairs.append(receipt)
        output.extend(parts)

    # A single erroneous missing outgoing hint may be recovered only from a
    # real physical-page edge, an incoming continuation and its next known head.
    content = [record for record in output if record["role"] != "furniture"]
    for index, current in enumerate(content):
        previous = content[index-1] if index else None
        following = content[index+1] if index+1 < len(content) else None
        number = previous.get("number") if previous else None
        if (previous and following and type(number) is int and unique(number) and unique(number+1)
                and previous["role"] == current["role"] == following["role"] == "answer"
                and previous["edge"] == "none" and current["edge"] in {"from_previous", "both"}
                and previous.get("visible_number") == number and current.get("number") is None
                and current.get("visible_number") is None and not _hard(previous["pending_reasons"])
                and not _hard(current["pending_reasons"]) and not _hard(following["pending_reasons"])
                and previous["page_number"] + 1 == current["page_number"] == following["page_number"]
                and following.get("number") == following.get("visible_number") == number+1):
            _mark_derived_edge(source, previous, "to_next", "fragment_answer_outgoing_recovered", repairs, peer=current)
            current["pending_reasons"].append("fragment_answer_outgoing_recovered")

    # Same-page raw adjacency can establish an unnumbered answer tail even
    # when the model called it to_next. Keep that outgoing doubt on the tail.
    for previous, current in zip(output, output[1:]):
        number = previous.get("number")
        if (type(number) is int and unique(number) and previous["role"] == current["role"] == "answer"
                and previous.get("visible_number") == number and current.get("number") is None
                and current.get("visible_number") is None and current["edge"] == "to_next"
                and previous["edge"] in {"none", "to_next"} and previous["page_number"] == current["page_number"]
                and previous["end"] == current["start"] and not _hard(previous["pending_reasons"])
                and set(_hard(current["pending_reasons"])) <= {"number_missing"}
                # A tail with any independent printed number remains
                # ambiguous, including unlabeled or protected example heads.
                and not any(number_prefix(line)[0] is not None and not line.startswith((" ", "\t"))
                            for line in source[current["start"]:current["end"]].splitlines())):
            _mark_derived_edge(source, previous, "to_next", "fragment_answer_same_page_edge_recovered", repairs, peer=current)
            receipt = _parent_receipt(source, current, "fragment_answer_same_page_edge_recovered")
            current.setdefault("original_edge", current["edge"])
            current["edge"] = "both"
            current["pending_reasons"] = [reason for reason in current["pending_reasons"] if reason != "number_missing"]
            current["pending_reasons"].append("fragment_answer_same_page_edge_recovered")
            receipt.update(parts=[_record_receipt(source, current)], peer=_record_receipt(source, previous))
            repairs.append(receipt)
    return output, repairs


def _groups(records, pages, role, reject, retain_partial):
    """Only adjacent physical-page edge handshakes join same-role blocks."""
    candidates = [record for record in records if record["role"] == role]
    by_page = defaultdict(list)
    for record in records:
        if record["role"] != "furniture":
            by_page[record["page_number"]].append(record)
    groups, opened = [], None

    def abandon(reason, *, conflict=False):
        nonlocal opened
        if opened is not None:
            for record in opened["records"]:
                if conflict:
                    reject(record, reason)
                else:
                    retain_partial(record, reason)
            if not conflict:
                opened["pending_reasons"] = [reason]
                groups.append(opened)
            opened = None

    for record in candidates:
        pending = _hard(record["pending_reasons"])
        if pending:
            abandon("fragment_continuation_interrupted")
            reject(record, pending[0])
            continue
        page = record["page_number"]
        incoming = record["edge"] in {"from_previous", "both"}
        outgoing = record["edge"] in {"to_next", "both"}
        if record["edge"] == "unknown":
            abandon("fragment_continuation_interrupted")
            reject(record, "unknown_edge")
            continue
        visible_head = (type(record.get("number")) is int and record.get("visible_number") == record["number"]
                        and bool(record["prefix"]))
        if incoming and visible_head:
            other_heads = [value for value in candidates if value is not record
                           and value.get("number") == record["number"]
                           and value.get("visible_number") == record["number"] and value["prefix"]]
            if other_heads or opened is not None and opened["number"] == record["number"]:
                abandon("fragment_repeated_head_continuation_conflict", conflict=True)
                reject(record, "fragment_repeated_head_continuation_conflict")
                continue
            # The printed top-level identity is stronger than a single wrong
            # incoming-edge hint. It starts its own card, never joins another.
            record["pending_reasons"].append("fragment_incoming_edge_ignored_for_visible_head")
            incoming = False
        if incoming:
            previous = opened["records"][-1] if opened else None
            same_page = bool(previous and previous["page_number"] == page
                and previous["end"] == record["start"] and record.get("number") is None
                and record.get("visible_number") is None
                and by_page[page].index(record) > 0 and by_page[page][by_page[page].index(record) - 1] is previous)
            adjacent = bool(previous and previous["page_number"] < page
                and all(number in pages and pages[number]["empty"]
                        for number in range(previous["page_number"] + 1, page)))
            unique_edge = bool(previous and by_page[previous["page_number"]][-1] is previous
                               and by_page[page][0] is record)
            if (not (same_page or adjacent and unique_edge) or record.get("number") not in {None, opened["number"]}
                    or record.get("visible_number", opened["number"]) != opened["number"]):
                abandon("fragment_continuation_unproved")
                reject(record, "fragment_continuation_unproved")
                continue
            if same_page:
                for value in (previous, record):
                    if "fragment_same_page_continuation" not in value["pending_reasons"]:
                        value["pending_reasons"].append("fragment_same_page_continuation")
            opened["records"].append(record)
        else:
            abandon("fragment_continuation_unclosed")
            number = record.get("number")
            if type(number) is not int or record.get("visible_number") != number or not record["prefix"]:
                reject(record, "number_prefix_unproved")
                continue
            opened = {"number": number, "records": [record], "role": role}
        if outgoing:
            offset = by_page[page].index(record)
            next_record = by_page[page][offset + 1] if offset + 1 < len(by_page[page]) else None
            same_page_next = bool(next_record and next_record["role"] == role
                and record["end"] == next_record["start"] and next_record.get("number") is None
                and next_record.get("visible_number") is None
                and next_record["edge"] in {"from_previous", "both"}
                and not _hard(next_record["pending_reasons"]))
            if by_page[page][-1] is not record and not same_page_next:
                abandon("fragment_outgoing_not_page_tail")
        else:
            groups.append(opened)
            opened = None
    abandon("fragment_continuation_unclosed")
    return groups


def _copy_group(source, group, pages):
    ranges, removals, field_sources = [], [], []
    previous = None
    for record in group["records"]:
        if previous is not None and previous["page_number"] != record["page_number"]:
            left = pages[previous["page_number"]]["range"][1]
            right = pages[record["page_number"]]["range"][0]
            if source[left:right] != "\n\n":
                raise ValueError("PDF 跨页片段的原文分隔不一致。")
            ranges.append([left, right])
        start = record["start"] + record["prefix"]
        if record["prefix"]:
            removals.append({"start": record["start"], "end": start,
                             "reason": "source_number_prefix", "text": source[record["start"]:start]})
        if start < record["end"]:
            ranges.append([start, record["end"]])
        field_sources.append({"page_number": record["page_number"], "block_id": record["id"],
            "role": record["role"], "number": record.get("number"), "edge": record["edge"],
            "range": [start, record["end"]], "page_sha256": record.get("page_sha256"),
            "text_sha256": text_sha(source[start:record["end"]]),
            **({"original_edge": record["original_edge"]} if "original_edge" in record else {}),
            **({"parent_block_id": record["parent_block_id"]} if record.get("parent_block_id") else {})})
        previous = record
    return "".join(source[a:b] for a, b in ranges), ranges, removals, field_sources


def _link_unproved_answer_tails(source, records, plan, used):
    """Expose a possible local tail on its card without assigning any text.

    Only the same narrow neighboring-head evidence used for local repair may
    nominate a card. Conflicting metadata, hidden numbers or an unknown edge
    still prevent adoption. The candidate range remains in unresolved_source.
    """
    unique = _unique_local_answer_heads(source, records)
    cards = {question["source_number"]: (question, item)
             for question, item in zip(plan["questions"], plan["items"])}
    unresolved_ids = {row["block_id"] for row in plan["unresolved_ranges"]}
    for previous, current, following in zip(records, records[1:], records[2:]):
        number = previous.get("number")
        if (current["id"] in used or current["id"] not in unresolved_ids
                or type(number) is not int or number not in cards or not unique(number) or not unique(number + 1)
                or previous["role"] != "answer" or current["role"] != "answer" or following["role"] != "answer"
                or previous.get("visible_number") != number or current.get("number") is not None
                or current.get("visible_number") is not None
                or following.get("number") != following.get("visible_number") or following.get("number") != number + 1
                or previous["page_number"] != current["page_number"] or current["page_number"] != following["page_number"]
                or previous["end"] != current["start"] or current["end"] != following["start"]):
            continue
        reason = "fragment_possible_answer_tail_unassigned"
        evidence = {"block_id": current["id"], "page_number": current["page_number"],
            "range": [current["start"], current["end"]],
            "text_sha256": text_sha(source[current["start"]:current["end"]]),
            "possible_source_numbers": [number], "relationship_status": "unproved",
            "reason": reason}
        for row in plan["unassigned"] + plan["unresolved_ranges"]:
            if row.get("block_id") == current["id"]:
                row.update(possible_source_numbers=[number], relationship_status="unproved")
        question, item = cards[number]
        question["source_review"].update(required=True)
        question["source_review"].setdefault("unassigned_candidates", []).append(deepcopy(evidence))
        question["source_review"]["reasons"] = list(dict.fromkeys(question["source_review"]["reasons"] + [reason]))
        item["manual_reasons"] = list(dict.fromkeys(item["manual_reasons"] + [reason]))
        item["question"] = deepcopy(question)
        plan["manual_reasons"].append(reason)


def _kind(context):
    if "多项" in context or "多选" in context:
        return "multi_choice"
    if "单项" in context or "单选" in context or "只有一项" in context or "只有一个" in context:
        return "single_choice"
    if "填空题" in context:
        return "fill_in_blank"
    if "解答题" in context or "证明题" in context:
        return "detailed_answer"
    return ""


def prepare_pdf_fragment_draft(source, source_pages, layout_result=None, source_document_sha256=None):
    """Return a local-draft compatible plan from verified fragment sidecars.

    layout_result is deliberately not a second source of text or images: the
    canonical source adapter must already have bound any accepted crop slots.
    """
    if (not isinstance(source, str) or len(source) > MAX_SOURCE or not isinstance(source_pages, list)
            or not 1 <= len(source_pages) <= 80):
        raise ValueError("PDF 片段题稿来源为空、无效或超过保存上限。")
    records, pages = _page_records(source, source_pages)
    records, repairs = _recover_section_context(source, records)
    records, answer_repairs = _recover_embedded_answer_head(source, records, pages)
    repairs.extend(answer_repairs)
    records, local_repairs = _recover_local_answer_relations(source, records)
    repairs.extend(local_repairs)
    plan = {"schema": SCHEMA, "workflow": "page-fragments.v2", "rules_version": RULES_VERSION,
        "input_sha256": text_sha(source), "source_sha256": text_sha(source),
        "document_sha256": source_document_sha256, "source": source, "ready": False, "partial": False,
        "questions": [], "items": [], "source_matches": [], "preserved_indices": [],
        "unassigned": [], "unresolved_ranges": [], "manual_reasons": [], "page_issues": [], "page_furniture": [],
        "source_basis": "first_pass_transcription", "original_verified": False,
        "structure_census": {"method": "first_pass_fragment_relationships", "original_verified": False},
        "fragment_repairs": repairs}
    plan["manual_reasons"].extend(dict.fromkeys(row["reason"] for row in repairs
                                             if row.get("requires_review") is not False))
    rejected, used, page_issues = set(), set(), defaultdict(set)
    unresolved = set()

    def retain_partial(record, reason):
        key = (record["id"], reason)
        page_issues[record["page_number"]].add(reason)
        if key in unresolved:
            return
        unresolved.add(key)
        if reason not in record["pending_reasons"]:
            record["pending_reasons"].append(reason)
        row = {"range": [record["start"], record["end"]], "role": "manual", "reason": reason,
               "page_number": record["page_number"], "block_id": record["id"], "source_number": record.get("number"),
               "text": source[record["start"]:record["end"]], "retained_in_partial_card": True}
        plan["unresolved_ranges"].append(row)
        plan["manual_reasons"].append(reason)

    def reject(record, reason):
        key = record["id"]
        page_issues[record["page_number"]].add(reason)
        if key in rejected:
            return
        rejected.add(key)
        row = {"range": [record["start"], record["end"]], "role": "manual", "reason": reason,
               "page_number": record["page_number"], "block_id": key, "source_number": record.get("number"),
               "text": source[record["start"]:record["end"]]}
        plan["unassigned"].append(row)
        plan["unresolved_ranges"].append(deepcopy(row))
        plan["manual_reasons"].append(reason)

    for record in records:
        for reason in record["pending_reasons"]:
            page_issues[record["page_number"]].add(reason)
        if record["role"] == "unknown" or _hard(record["pending_reasons"]):
            reject(record, (_hard(record["pending_reasons"]) or record["pending_reasons"] or ["unknown_role"])[0])
        elif record["role"] not in _QUESTION_ROLES:
            row = {"range": [record["start"], record["end"]],
                   "role": "document_context" if record["role"] == "context" else record["role"],
                   "reason": "fragment_" + record["role"], "page_number": record["page_number"],
                   "block_id": record["id"], "text": source[record["start"]:record["end"]]}
            plan["unassigned"].append(row)
            if record["role"] == "furniture":
                plan["page_furniture"].append({"start": record["start"], "end": record["end"],
                    "reason": "fragment_furniture", "page_number": record["page_number"], "text": row["text"]})
        if "page_incomplete" in record["pending_reasons"]:
            plan["manual_reasons"].append("fragment_page_incomplete")

    stems = _groups(records, pages, "stem", reject, retain_partial)
    answers = _groups(records, pages, "answer", reject, retain_partial)
    # Numbered one-answer key blocks are safe fallback; full explicit answer
    # tables are handled within their own declared block below, never globally.
    key_records = [record for record in records if record["role"] == "answer_key" and record["id"] not in rejected]
    table_records = [record for record in key_records if record.get("number") is None]
    numbered_keys = _groups([record for record in key_records if record.get("number") is not None],
                           pages, "answer_key", reject, retain_partial)
    stem_map, answer_map, keys = defaultdict(list), defaultdict(list), defaultdict(list)
    for group in stems:
        stem_map[group["number"]].append(group)
    for group in answers:
        answer_map[group["number"]].append(group)
    for group in numbered_keys:
        keys[group["number"]].append(group)
    head_counts = defaultdict(int)
    for record in records:
        if (record["role"] in {"stem", "answer"}
                and type(record.get("number")) is int and record.get("visible_number") == record["number"]):
            head_counts[(record["role"], record["number"])] += 1
    for number, groups in stem_map.items():
        if len(groups) > 1 or head_counts[("stem", number)] > 1:
            for group in groups:
                for record in group["records"]:
                    reject(record, "duplicate_stem_number")
    from .pdf_local_draft import _table_answers
    table_cells = defaultdict(list)
    for record in table_records:
        a, b = record["start"], record["end"]
        text = source[a:b]
        a += len(text) - len(text.lstrip())
        b -= len(text) - len(text.rstrip())
        if a < b:
            for number, ranges in _table_answers(source, [(a, b)], a, b, set(stem_map)).items():
                for bounds in ranges:
                    table_cells[number].append((record, bounds))
        used.add(record["id"])
        plan["unassigned"].append({"range": [record["start"], record["end"]], "role": "answer_context",
            "reason": "fragment_answer_key_table", "block_id": record["id"],
            "page_number": record["page_number"], "text": source[record["start"]:record["end"]]})
    for number in sorted(stem_map):
        if len(stem_map[number]) != 1 or any(record["id"] in rejected for record in stem_map[number][0]["records"]):
            continue
        stem = stem_map[number][0]
        content, stem_ranges, removals, stem_sources = _copy_group(source, stem, pages)
        if not content.strip():
            for record in stem["records"]:
                reject(record, "empty_question_content")
            continue
        reasons, warnings, answer, answer_ranges, answer_sources = list(stem.get("pending_reasons", [])), [], "", [], []
        chosen_records = []
        selected = answer_map.get(number, [])
        if len(selected) > 1 or head_counts[("answer", number)] > 1:
            reasons.append("original_answer_conflict")
            for group in selected:
                for record in group["records"]:
                    reject(record, "original_answer_conflict")
        elif len(selected) == 1:
            group = selected[0]
            reasons.extend(group.get("pending_reasons", []))
            answer, answer_ranges, drops, answer_sources = _copy_group(source, group, pages)
            chosen_records = group["records"]
            removals.extend(drops)
            used.update(record["id"] for record in group["records"])
        elif len(keys[number]) == 1 and not table_cells[number]:
            answer, answer_ranges, drops, answer_sources = _copy_group(source, keys[number][0], pages)
            reasons.extend(keys[number][0].get("pending_reasons", []))
            chosen_records = keys[number][0]["records"]
            removals.extend(drops)
            used.update(record["id"] for record in keys[number][0]["records"])
        elif not keys[number] and len(table_cells[number]) == 1:
            record, bounds = table_cells[number][0]
            answer_ranges = [list(bounds)]
            answer = source[slice(*bounds)]
            chosen_records = [record]
            answer_sources = [{"page_number": record["page_number"], "block_id": record["id"],
                "range": list(bounds), "page_sha256": record.get("page_sha256"), "text_sha256": text_sha(answer)}]
        elif keys[number] or table_cells[number]:
            reasons.append("original_answer_conflict")
        elif answers or key_records:
            reasons.append("original_answer_unassigned")
        used.update(record["id"] for record in stem["records"])
        related = stem["records"] + chosen_records
        for record in related:
            warnings.extend(record["pending_reasons"])
        if "[插图待补" in content + answer:
            warnings.append("source_contains_review_marker")
        warnings = list(dict.fromkeys(warnings))
        context = stem["records"][0].get("context", "")
        question = {"source_number": number, "content": content, "answer_markdown": answer,
            "question_type": _kind(context), "category_compulsory": "", "category_chapter": "",
            "category_knowledge": "", "difficulty": "normal", "source": "",
            "referenced_images": embedded_question_assets(content, answer),
            "section_context": context, "shared_context": "",
            "source_review": {"required": bool(reasons or warnings), "status": "not_verified",
                "reasons": reasons + warnings, "source_basis": "first_pass_transcription", "original_verified": False}}
        identifier = "PFD_" + text_sha(json.dumps([source_document_sha256 or text_sha(source), number]))[:24]
        item = {"id": identifier, "source_number": number, "content": content, "answer_markdown": answer,
            "question": deepcopy(question), "stem_ranges": stem_ranges, "answer_ranges": answer_ranges,
            "context_ranges": [], "raw_stem_range": [stem["records"][0]["start"], stem["records"][-1]["end"]],
            "raw_answer_range": [min(a for a, b in answer_ranges), max(b for a, b in answer_ranges)] if answer_ranges else None,
            "raw_content": "".join(source[record["start"]:record["end"]] for record in stem["records"]),
            "raw_answer": answer, "manual_reasons": reasons, "source_warnings": warnings,
            "classification_blocked_reasons": list(stem.get("pending_reasons", [])),
            "insertions": [], "removals": removals,
            "field_sources": {"content": stem_sources, "answer_markdown": answer_sources}}
        index = len(plan["questions"])
        plan["questions"].append(question)
        plan["items"].append(item)
        for field, ranges in (("content", stem_ranges), ("answer_markdown", answer_ranges)):
            if ranges:
                plan["source_matches"].append({"question_index": index, "field": field, "source_number": number,
                    "source_start": min(a for a, b in ranges), "source_end": max(b for a, b in ranges),
                    "source_ranges": deepcopy(ranges), "source_excerpt": "".join(source[a:b] for a, b in ranges),
                    "source_basis": "first_pass_transcription", "original_verified": False})
        plan["manual_reasons"].extend(reasons)
    for record in records:
        if record["role"] not in _QUESTION_ROLES or record["id"] in used or record["id"] in rejected:
            continue
        if record["role"] == "answer_key" and record.get("number") in answer_map:
            plan["unassigned"].append({"range": [record["start"], record["end"]], "role": "answer_context",
                "reason": "detailed_answer_preferred", "block_id": record["id"],
                "text": source[record["start"]:record["end"]]})
        else:
            reject(record, "original_answer_without_question" if record["role"] != "stem" else "stem_relationship_unproved")
    _link_unproved_answer_tails(source, records, plan, used)
    plan["manual_reasons"] = list(dict.fromkeys(plan["manual_reasons"]))
    supplied = sorted(stem_map)
    if supplied and sorted(pages) == list(range(min(pages), max(pages) + 1)):
        missing = sorted(set(range(min(supplied), max(supplied) + 1)) - set(supplied))
        if missing:
            plan["manual_reasons"].append("fragment_question_number_gap")
            plan["structure_census"]["missing_source_numbers"] = missing
    if not plan["questions"] and any(record["role"] in _QUESTION_ROLES or record["role"] == "unknown"
                                     for record in records):
        plan["manual_reasons"].append("no_proved_stem_fragments")
    for record in records:
        page_issues[record["page_number"]].update(record["pending_reasons"])
    plan["page_issues"] = [{"page_number": number, "reason_codes": sorted(codes)}
                           for number, codes in sorted(page_issues.items()) if codes]
    plan["ready"] = bool(plan["questions"])
    plan["partial"] = bool(plan["manual_reasons"] or plan["unresolved_ranges"])
    plan["preserved_indices"] = list(range(len(plan["questions"])))
    plan["structure_census"].update(source_numbers=[question["source_number"] for question in plan["questions"]],
        fragment_blocks=len(records), method="first_pass_fragment_relationships")
    plan["fragment_summary"] = {"schema": "mathbank.pdf-page-fragments.v2", "pages": len(source_pages),
        "blocks": len(records), "questions": len(plan["questions"]),
        "answers": sum(bool(row["answer_markdown"].strip()) for row in plan["questions"]),
        "pending_blocks": len(rejected | {row["block_id"] for row in plan["unresolved_ranges"]}
                              | {row["parent_block_id"] for row in repairs if row.get("requires_review") is not False}),
        "unresolved_ranges": len(plan["unresolved_ranges"]),
        "source_numbers": [row["source_number"] for row in plan["questions"]],
        "source_preserved": True, "original_verified": False,
        "manual_reasons": list(plan["manual_reasons"])}
    return plan
