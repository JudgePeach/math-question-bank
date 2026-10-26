"""Conservative, process-local recovery for the ordinary PDF page route.

These drafts preserve machine transcription; they are not visual verification
or a new PDF processing mode.  A missing page is never passed to the splitter.
"""
from copy import deepcopy
import hashlib
import json
import re


SCHEMA = "mathbank.cloud-pdf-partial.v1"


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_binding(document_input):
    fields = {key: document_input.get(key) for key in
              ("sha256", "bytes", "filename", "document_type", "options", "curriculum_sha256")}
    return _sha(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def resumable_checkpoint(task):
    state = task.get("pdf_resume_state")
    return bool(isinstance(state, dict) and state.get("schema") == SCHEMA
                and state.get("task_id") == task.get("task_id")
                and state.get("binding") == source_binding(task.get("document_input") or {})
                and state.get("phase") == "page_retry" and state.get("failed_pages")
                and task.get("document_type") == "pdf")


def checkpoint_summary(state):
    return {"version": 1, "task_id": state["task_id"],
            "failed_pages": list(state["failed_pages"]),
            "quarantined_ranges": deepcopy(state.get("quarantined_ranges", [])),
            "delivered_cards": len(state.get("cards", [])),
            "resume_action": "retry_failed_pages", "preserve_edits": True}


def complete_boundary_drafts(pages, *, source_sha256, paper_title):
    """Keep only an interior numbered block terminated on the same page.

    Existing strict source parsing checks math, environment, table, answer and
    dependency boundaries.  First/last regions and figure-dependent blocks are
    deliberately left in the page transcript for manual review.
    """
    from .content_locks import _source_parts, lock_visible_math
    from .source_metadata import inspect_source_structure
    cards, isolated = [], []
    for page in pages:
        number, text = page["page_number"], page.get("markdown") or ""
        if not text:
            isolated.append({"pages": [number], "reason": "page_not_transcribed"})
            continue
        _, locks = lock_visible_math(text, "pdf_partial")
        parts = [part for part in _source_parts(text, locks) if part.field == "content"]
        shared = re.search(r"共用|共同题干|本题组|(?:完成|回答).{0,12}(?:小题|各题|题组)", text)
        accepted = []
        for index in range(1, len(parts) - 1):
            part, following = parts[index:index + 2]
            if (shared or part.number is None or following.number != part.number + 1
                    or sum(item.number == part.number for item in parts) != 1):
                continue
            start, end = part.source_start, following.source_start
            block = text[start:end]
            if re.search(r"上题|下题|前题|后题|如图|见图|下图|上图|插图待补|!\[|\\includegraphics|第\s*\d+\s*题", block):
                continue
            plan = inspect_source_structure(block)
            if not plan["eligible"] or len(plan["questions"]) != 1:
                continue
            question = plan["questions"][0]
            if question["source_number"] != part.number:
                continue
            identity = _sha(f"{source_sha256}:{number}:{start}:{end}:{_sha(block)}")
            # Preserve exact source fields, including their number/prefix and
            # whitespace.  Unknown classifications stay empty for the teacher.
            cards.append({"content": question["raw_content"],
                "answer_markdown": question["raw_answer"], "analysis": "",
                "question_type": question["explicit_question_type"] or "",
                "difficulty": None, "category": "", "chapter": "", "knowledge": "",
                "knowledge_points": [], "source": paper_title,
                "pdf_source_card_id": "pdfsrc_" + identity,
                "pdf_source_scope": {"page_number": number, "source_start": start,
                    "source_end": end, "source_number": part.number, "sha256": _sha(block)},
                "classification_review": {"required": True, "message": "局部原文题稿，请确认题型、难度和教材分类。"},
                "source_review": {"required": True, "blocking": False, "disposition": "advisory",
                    "reason_codes": ["partial_transcription_draft"],
                    "reasons": ["同页边界题稿，尚未对照原页核验，请教师核对。"],
                    "message": "同页完整边界候选，原文保留；尚未视觉核验，请教师对照原页核对。"}})
            accepted.append((start, end))
        cursor = 0
        for start, end in accepted + [(len(text), len(text))]:
            if text[cursor:start].strip():
                isolated.append({"pages": [number], "source_start": cursor,
                    "source_end": start, "reason": "boundary_or_dependency_not_proven"})
            cursor = end
    return cards, isolated


def merge_partial_drafts(previous, incoming):
    result = deepcopy(previous)
    seen = {card["pdf_source_card_id"] for card in result}
    for card in incoming:
        if card["pdf_source_card_id"] not in seen:
            result.append(deepcopy(card))
            seen.add(card["pdf_source_card_id"])
    return result


def merge_recovered_questions(previous, incoming, source, *, source_sha256):
    """Keep delivered drafts; append only uniquely source-matched new cards.

    An ambiguous full result is isolated instead of duplicating a card which
    the teacher may already have edited or imported.
    """
    from .content_locks import _source_parts, lock_visible_math, reconcile_visible_math
    from .source_metadata import _canonical_body
    from collections import Counter
    # Navigation markers are server metadata, not part of a question.  Use
    # this projection only to establish identity; all delivered fields remain
    # the original parser or exact frozen draft values.
    source = re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->", "", source)
    _, locks = lock_visible_math(source, "pdf_partial_recovered")
    checked = deepcopy(incoming)
    report = reconcile_visible_math(checked, locks, source)
    matches = [row for row in report.get("source_matches", []) if row["field"] == "content"]
    parts = [part for part in _source_parts(source, locks) if part.field == "content"]
    number_counts = Counter(part.number for part in parts)
    def comparable(value):
        return " ".join(value.split())
    old_numbers = {card["pdf_source_scope"]["source_number"] for card in previous}
    result, isolated = deepcopy(previous), []
    for index, card in enumerate(incoming):
        owned = [row for row in matches if row["question_index"] == index]
        if not owned:
            # Formula reconciliation intentionally declines some short plain
            # questions. Exact canonical text is still a useful unique source
            # identity; no similarity threshold or array position is used.
            body = comparable(_canonical_body(str(card.get("content") or ""))[0])
            exact = [part for part in parts if comparable(_canonical_body(part.text)[0]) == body]
            if body and len(exact) == 1:
                part = exact[0]
                owned = [{"source_number": part.number, "source_start": part.source_start,
                          "source_end": part.source_end, "source_excerpt": part.text}]
        if len(owned) != 1 or owned[0]["source_number"] is None:
            isolated.append({"question_index": index, "reason": "recovered_card_identity_uncertain"})
            continue
        match = owned[0]
        if number_counts[match["source_number"]] != 1:
            isolated.append({"question_index": index, "reason": "recovered_source_number_not_unique"})
            continue
        if match["source_number"] in old_numbers:
            earlier = [card for card in previous if card["pdf_source_scope"]["source_number"] == match["source_number"]]
            if len(earlier) != 1 or earlier[0]["content"].strip() != match["source_excerpt"].strip():
                isolated.append({"question_index": index, "reason": "delivered_source_scope_uncertain"})
            continue
        value = deepcopy(card)
        value["pdf_source_card_id"] = "pdfsrc_" + _sha(
            f"{source_sha256}:recovered:{match['source_start']}:{match['source_end']}:{_sha(match['source_excerpt'])}")
        result.append(value)
    return result, isolated
