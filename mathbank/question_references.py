"""Source-number indexes and prose references without rewriting question text.

All writers participate in the caller's transaction. A resolved reference whose
target was deleted is a tombstone: a reused SQLite ID or a new source alias must
never reconnect it automatically. Offsets count Python Unicode characters.
"""

from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import re
import sqlite3
import unicodedata

from sqlalchemy import func, inspect, tuple_

from mathbank.database import Question, QuestionReference, QuestionSourceLabel
from mathbank.question_assets import _mask_literals, markdown_literal_ranges


REFERENCE_FIELDS = ("content", "answer_markdown", "review")
REFERENCE_TABLES = ("question_source_labels", "question_references")
_SPACE = r"[ \t\u3000]*"
_NUMBER = r"\d+(?:" + _SPACE + r"[.．·∙⋅]" + _SPACE + r"\d+)+"
_KIND = r"(?:变式题|变式|例题|例)"
_NUMBER_END = r"(?!\d|[.．·∙⋅][.．·∙⋅ \t\u3000]*\d)"
_HEAD = re.compile(r"(?P<kind>" + _KIND + r")" + _SPACE + r"(?P<number>" + _NUMBER + r")" + _NUMBER_END)
_TAIL = re.compile(_SPACE + r"(?P<join>以及|、|，|,|和|及|与|或|/|至|到|～|~|—|–|-)" + _SPACE
                   + r"(?P<kind>" + _KIND + r")?" + _SPACE + r"(?P<number>" + _NUMBER + r")" + _NUMBER_END)
_SUSPECT = re.compile(_KIND + _SPACE + r"(?:\d+(?:[.．·∙⋅\d \t\u3000]*)|[xX?？])")
_MATH_OPEN = re.compile(r"\$\$?|\\[\[(]|\\begin\s*\{(equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath|math|aligned|alignedat|gathered|split|array|cases|[pbBvV]?matrix|smallmatrix)\}")
_RANGE_JOINS = frozenset(("至", "到", "～", "~", "—", "–", "-"))
_EXPLICIT_BOOK = re.compile(r"《(?P<book>[^《》\n]{1,200})》\s*(?:(?:书)?中(?:的)?|的|内(?:的)?|一书(?:中(?:的)?)?)?\s*$")
_ANCHOR_CONTEXT = 24
_MAX_TEXT = 500_000
_MAX_REFERENCES = 512


def _get(value, key, default=""):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def text_sha256(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def normalize_book_key(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def normalize_label_key(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = re.sub(r"[·∙⋅]", ".", value)
    value = re.sub(r"\s+", "", value)
    match = re.fullmatch(r"(变式题|变式|例题|例)(\d+(?:\.\d+)+)", value)
    if not match:
        return ""
    kind = "变式" if match.group(1).startswith("变式") else "例"
    return kind + ".".join(str(int(part)) for part in match.group(2).split("."))


def _escaped(text, position):
    cursor = position - 1
    while cursor >= 0 and text[cursor] == "\\":
        cursor -= 1
    return (position - cursor - 1) % 2 == 1


def _prose_view(text):
    """Mask literals and complete/unclosed math while preserving every offset."""
    chars = list(text)
    for start, end in markdown_literal_ranges(text):
        chars[start:end] = ["\n" if char == "\n" else " " for char in text[start:end]]
    view = _mask_literals("".join(chars), tex_comments=False)
    chars = list(view)
    # HTML attributes are not prose. Body text remains available.
    for match in re.finditer(r"<[^>\n]*>", view):
        chars[match.start():match.end()] = [" "] * len(match.group())
    view = "".join(chars)
    cursor = 0
    while match := _MATH_OPEN.search(view, cursor):
        if _escaped(text, match.start()):
            cursor = match.end()
            continue
        closing = ("\\end{" + match.group(1) + "}") if match.group(1) else {
            "$": "$", "$$": "$$", "\\(": "\\)", "\\[": "\\]",
        }[match.group()]
        end = view.find(closing, match.end())
        while end >= 0 and _escaped(text, end):
            end = view.find(closing, end + len(closing))
        cursor = end + len(closing) if end >= 0 else len(text)
        chars[match.start():cursor] = ["\n" if char == "\n" else " " for char in text[match.start():cursor]]
    return "".join(chars)


def _range_labels(first, last):
    a = re.fullmatch(r"(例|变式)(\d+(?:\.\d+)+)", first)
    b = re.fullmatch(r"(例|变式)(\d+(?:\.\d+)+)", last)
    if not a or not b or a.group(1) != b.group(1):
        return []
    left, right = a.group(2).split("."), b.group(2).split(".")
    if len(left) != len(right) or left[:-1] != right[:-1]:
        return []
    lo, hi = int(left[-1]), int(right[-1])
    if not 0 <= hi - lo < 50:
        return []
    prefix = a.group(1) + ".".join(left[:-1]) + "."
    return [prefix + str(number) for number in range(lo, hi + 1)]


def extract_references(text, book_key=""):
    """Find explicit dotted example/variant citations in prose only.

    Lists with an omitted repeated kind (例2.6及2.7) are separate occurrences.
    A proven same-kind, same-level range has one member per label at the same
    original span. The display groups these members without pretending their
    current library numbers form a continuous range. Unprovable ranges stay open.
    """
    text = str(text or "")
    if len(text) > _MAX_TEXT:
        raise ValueError("交叉引用文本超过长度上限")
    book_key = normalize_book_key(book_key)
    view = _prose_view(text)
    digest = text_sha256(text)
    found, occupied = [], []

    def add(start, end, label, reason="", reference_book_key=None, **extra):
        if len(found) >= _MAX_REFERENCES:
            raise ValueError("单字段交叉引用超过数量上限")
        if len(label) > 100:
            label, reason = "", "source_number_too_long"
        namespace = reference_book_key if reference_book_key is not None else book_key
        if len(namespace) > 200:
            namespace, reason = "", "book_name_too_long"
        found.append({"start": start, "end": end, "text": text[start:end],
                      "label_key": label, "book_key": namespace,
                      "field_sha256": digest,
                      "prefix": text[max(0, start - _ANCHOR_CONTEXT):start],
                      "suffix": text[end:end + _ANCHOR_CONTEXT], "reason": reason, **extra})
        occupied.append((start, end))

    for match in _HEAD.finditer(view):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        kind = "变式" if match.group("kind").startswith("变式") else "例"
        label = normalize_label_key(kind + match.group("number"))
        book_match = _EXPLICIT_BOOK.search(view[max(0, match.start() - 220):match.start()])
        citation_book = normalize_book_key(book_match.group("book")) if book_match else book_key
        start, end = match.span()
        while True:
            tail = _TAIL.match(view, end)
            if tail and tail.group("join") in _RANGE_JOINS:
                tail_kind = tail.group("kind") or kind
                last = normalize_label_key(tail_kind + tail.group("number"))
                candidates = _range_labels(label, last)
                if candidates:
                    for candidate in candidates:
                        add(start, tail.end(), candidate, reference_book_key=citation_book, candidate_label_keys=candidates, is_range=True)
                else:
                    add(start, tail.end(), "", "range_requires_multiple_targets", reference_book_key=citation_book, candidate_label_keys=[], is_range=True)
                end = tail.end()
                kind = "变式" if tail_kind.startswith("变式") else "例"
                tail = _TAIL.match(view, end)
            else:
                add(start, end, label, reference_book_key=citation_book)
            if tail is None or tail.group("join") in _RANGE_JOINS:
                break
            next_kind = tail.group("kind") or kind
            start = tail.start("kind") if tail.group("kind") else tail.start("number")
            end = tail.end()
            label = normalize_label_key(next_kind + tail.group("number"))
            kind = "变式" if next_kind.startswith("变式") else "例"
    for match in _SUSPECT.finditer(view):
        if not any(start <= match.start() < end for start, end in occupied):
            add(match.start(), match.end(), "", "incomplete_or_placeholder_number")
    return sorted(found, key=lambda item: (item["start"], item["end"]))


def extract_source_labels(source):
    """Index only an explicit book | original-number source, not exam titles."""
    source = str(source or "").replace("｜", "|")
    if "|" not in source:
        return []
    book, evidence = source.split("|", 1)
    book_key = normalize_book_key(book)
    if not book_key or len(book_key) > 200:
        return []
    result = {}
    for item in extract_references(evidence, book_key):
        if item["label_key"] and not item["reason"] and item["book_key"] == book_key:
            if len(item["text"]) > 200:
                raise ValueError("来源原题号文字超过长度上限")
            result.setdefault(item["label_key"], {"book_key": book_key,
                "label_key": item["label_key"], "original_label": item["text"]})
    return list(result.values())


def _question_occurrences(question):
    labels = extract_source_labels(_get(question, "source"))
    book_key = labels[0]["book_key"] if labels else ""
    return [{**item, "field": field} for field in REFERENCE_FIELDS
            for item in extract_references(_get(question, field), book_key)]


def _resolve(db, book_key, label_key, reason=""):
    if reason or not label_key:
        return None, "unresolved", reason or "invalid_source_number"
    if not book_key:
        return None, "unresolved", "missing_explicit_book_source"
    indexed = db.query(QuestionSourceLabel.question_id, Question.source).join(
        Question, Question.id == QuestionSourceLabel.question_id).filter(
        QuestionSourceLabel.book_key == book_key, QuestionSourceLabel.label_key == label_key
    ).distinct()
    candidates = set()
    for qid, source in indexed:
        # Older exchange clients deliberately omit relation fields. A retained
        # historical alias is evidence for existing bindings, not authority to
        # establish a new binding after its target's source was changed.
        try:
            proven = extract_source_labels(source)
        except ValueError:
            continue
        if any(item["book_key"] == book_key and item["label_key"] == label_key for item in proven):
            candidates.add(qid)
            if len(candidates) == 2:
                break
    if len(candidates) == 1:
        return next(iter(candidates)), "resolved", ""
    return None, "ambiguous" if candidates else "unresolved", "multiple_source_targets" if candidates else "target_not_found"


def sync_question_source_labels(db, question):
    """Replace derived labels and refresh only unresolved affected references."""
    db.flush()
    qid = _get(question, "id", None)
    if not qid:
        raise ValueError("题目尚未保存，不能建立来源索引")
    old = db.query(QuestionSourceLabel).filter_by(question_id=qid).all()
    affected = {(row.book_key, row.label_key) for row in old}
    definitions = extract_source_labels(_get(question, "source"))
    affected.update((item["book_key"], item["label_key"]) for item in definitions)
    db.query(QuestionSourceLabel).filter_by(question_id=qid).delete(synchronize_session="fetch")
    for item in definitions:
        db.add(QuestionSourceLabel(question_id=qid, **item))
    db.flush()
    refresh_reference_targets_for_labels(db, affected)
    return len(definitions)


def refresh_reference_targets_for_labels(db, labels):
    """Resolve pending labels after an alias is added, changed, or deleted."""
    labels = set(labels)
    if not labels:
        return
    db.flush()
    pending = db.query(QuestionReference).filter(
        tuple_(QuestionReference.book_key, QuestionReference.label_key).in_(labels),
        QuestionReference.resolution_state.in_(("unresolved", "ambiguous")),
    ).all()
    source_ids = {row.source_question_id for row in pending}
    sources = {q.id: q for q in db.query(Question).filter(Question.id.in_(source_ids)).all()} if source_ids else {}
    for row in pending:
        # A prior identity disagreement needs explicit repair. Source-index
        # changes must not silently choose one of those historical identities.
        if row.reason == "multiple_previous_reference_targets":
            continue
        source = sources.get(row.source_question_id)
        if source is None or text_sha256(_get(source, row.field)) != row.field_sha256:
            continue
        row.target_question_id, row.resolution_state, row.reason = _resolve(db, row.book_key, row.label_key, row.reason if not row.label_key else "")


def sync_question_references(db, question):
    """Rescan current prose, retaining resolved identities and deleted tombstones."""
    db.flush()
    qid = _get(question, "id", None)
    if not qid:
        raise ValueError("题目尚未保存，不能建立交叉引用")
    existing = defaultdict(deque)
    previous_targets = defaultdict(set)
    blocked_labels = set()
    rows = db.query(QuestionReference).filter_by(source_question_id=qid).populate_existing().order_by(QuestionReference.start).all()
    for row in rows:
        existing[(row.field, row.text, row.book_key, row.label_key)].append(row)
        if row.resolution_state == "resolved":
            previous_targets[(row.book_key, row.label_key)].add(row.target_question_id)
        elif row.reason == "multiple_previous_reference_targets":
            blocked_labels.add((row.book_key, row.label_key))
    definitions = _question_occurrences(question)
    templates = []
    for item in definitions:
        definition = {key: item[key] for key in ("field", "start", "end", "text", "book_key", "label_key", "field_sha256", "prefix", "suffix", "reason")}
        previous = existing[(item["field"], item["text"], item["book_key"], item["label_key"])]
        old = previous.popleft() if previous else None
        if old is not None and old.resolution_state == "resolved":
            definition.update(target_question_id=old.target_question_id, resolution_state="resolved", reason="target_deleted" if old.target_question_id is None else "")
        elif len(previous_targets[(item["book_key"], item["label_key"])]) == 1:
            # The same semantic citation may gain spaces, move between fields,
            # or be expanded from a range. Keep its established identity,
            # including a deleted-target tombstone, independently of formatting.
            target = next(iter(previous_targets[(item["book_key"], item["label_key"])]))
            definition.update(target_question_id=target, resolution_state="resolved", reason="target_deleted" if target is None else "")
        elif len(previous_targets[(item["book_key"], item["label_key"])]) > 1 or (item["book_key"], item["label_key"]) in blocked_labels:
            definition.update(target_question_id=None, resolution_state="ambiguous", reason="multiple_previous_reference_targets")
        else:
            target, state, reason = _resolve(db, item["book_key"], item["label_key"], item["reason"])
            definition.update(target_question_id=target, resolution_state=state, reason=reason)
        templates.append(definition)
    db.query(QuestionReference).filter_by(source_question_id=qid).delete(synchronize_session="fetch")
    # Flush deletion before inserts, because occurrence uniqueness is positional.
    db.flush()
    for item in templates:
        db.add(QuestionReference(source_question_id=qid, **item))
    db.flush()
    return len(templates)


def _has_tables(db):
    inspector = inspect(db.connection())
    return all(inspector.has_table(name) for name in REFERENCE_TABLES)


def reference_payloads(db, questions, seq_mapping=None):
    """Return {source ID: occurrences} in bulk; never mutate question objects."""
    # sqlite3's legacy mode does not BEGIN for SELECT, even though SQLAlchemy
    # has an autobegin transaction object. Reference rows and window numbers
    # must share a real snapshot so a concurrent delete/reused ID cannot be
    # mistaken for the previously bound target.
    connection = db.connection()
    raw = connection.connection.dbapi_connection
    if isinstance(raw, sqlite3.Connection) and not raw.in_transaction:
        connection.exec_driver_sql("BEGIN")
    question_map = {_get(question, "id", None): question for question in questions}
    result = {qid: [] for qid in question_map if qid is not None}
    if not result or not _has_tables(db):
        return result
    rows = db.query(QuestionReference).filter(QuestionReference.source_question_id.in_(result)).populate_existing().order_by(
        QuestionReference.source_question_id, QuestionReference.field, QuestionReference.start).all()
    target_ids = {row.target_question_id for row in rows if row.target_question_id is not None}
    # A caller's display cache may have been read before this snapshot began.
    # Compute the target numbers here rather than trust that external cache.
    sequences = {}
    missing = target_ids
    if missing:
        ranked = db.query(Question.id.label("qid"), func.row_number().over(order_by=Question.id).label("seq")).subquery()
        sequences.update({qid: int(seq) for qid, seq in db.query(ranked.c.qid, ranked.c.seq).filter(ranked.c.qid.in_(missing))})
    parser_cache = {}
    for row in rows:
        source = str(_get(question_map[row.source_question_id], row.field) or "")
        current = text_sha256(source) == row.field_sha256 and source[row.start:row.end] == row.text
        if not current:
            status, reason = "stale", "source_text_changed"
        elif row.resolution_state == "resolved":
            status = "resolved" if row.target_question_id in sequences else "target_deleted"
            reason = "" if status == "resolved" else "target_deleted"
        else:
            status, reason = "unresolved", row.reason
        payload = {key: getattr(row, key) for key in ("field", "start", "end", "text", "label_key", "book_key", "target_question_id", "field_sha256", "prefix", "suffix")}
        payload.update(seq_num=sequences.get(row.target_question_id) if status == "resolved" else None,
                       status=status, reason=reason, resolution_state=row.resolution_state)
        if re.search(r"[至到～~—–-]", row.text) and current:
            cache_key = (row.source_question_id, row.field)
            if cache_key not in parser_cache:
                parser_cache[cache_key] = {(item["start"], item["end"], item["label_key"]): item for item in extract_references(source, row.book_key)}
            metadata = parser_cache[cache_key].get((row.start, row.end, row.label_key), {})
            if metadata.get("is_range"):
                payload.update(candidate_label_keys=metadata.get("candidate_label_keys", []), is_range=True)
        result[row.source_question_id].append(payload)
    return result


def serialize_question_reference_data(db, question_ids):
    """Preserve portable definitions, including tombstones, without sequence cache."""
    ids = set(question_ids)
    if not ids or not _has_tables(db):
        return {}
    questions = db.query(Question).filter(Question.id.in_(ids)).all()
    payloads = reference_payloads(db, questions)
    result = {qid: {"source_labels": [], "question_references": [
        {key: value for key, value in item.items() if key != "seq_num"}
        for item in payloads.get(qid, [])]} for qid in ids}
    for row in db.query(QuestionSourceLabel).filter(QuestionSourceLabel.question_id.in_(ids)).order_by(QuestionSourceLabel.id):
        result[row.question_id]["source_labels"].append({key: getattr(row, key) for key in ("book_key", "label_key", "original_label")})
    return result


def normalize_exchange_reference_data(question, payload):
    """Validate explicit portable definitions; target ID hints are never trusted."""
    output = {}
    if "source_labels" in payload:
        incoming = payload["source_labels"]
        if not isinstance(incoming, list):
            raise ValueError("source_labels 必须是数组")
        proven = {(item["book_key"], item["label_key"]): item for item in extract_source_labels(_get(question, "source"))}
        seen, labels = set(), []
        for item in incoming:
            if not isinstance(item, dict):
                raise ValueError("source_labels 包含无效项")
            key = (normalize_book_key(item.get("book_key")), normalize_label_key(item.get("label_key")))
            if key not in proven or key in seen:
                raise ValueError("来源标签与当前 source 不一致或重复")
            seen.add(key)
            labels.append(proven[key])
        output["source_labels"] = labels
    if "question_references" in payload:
        incoming = payload["question_references"]
        if not isinstance(incoming, list):
            raise ValueError("question_references 必须是数组")
        proven = {(item["field"], item["start"], item["end"], item["label_key"]): item for item in _question_occurrences(question)}
        seen, references = set(), []
        for item in incoming:
            if not isinstance(item, dict) or type(item.get("start")) is not int or type(item.get("end")) is not int:
                raise ValueError("交叉引用位置无效")
            key = (item.get("field"), item["start"], item["end"], item.get("label_key"))
            expected = proven.get(key)
            if expected is None or key in seen or any(item.get(name) != expected[name] for name in ("text", "label_key", "book_key", "field_sha256")):
                raise ValueError("交叉引用与当前正文证据不一致或重复")
            seen.add(key)
            template = {name: expected[name] for name in ("field", "start", "end", "text", "label_key", "book_key", "field_sha256", "prefix", "suffix", "reason")}
            deleted = item.get("status") == "target_deleted" or (item.get("resolution_state") == "resolved" and item.get("target_question_id") is None)
            template.update(target_question_id=None, resolution_state="resolved" if deleted else "unresolved",
                            reason="target_deleted" if deleted else expected["reason"])
            if not deleted and item.get("reason") == "multiple_previous_reference_targets":
                template.update(resolution_state="ambiguous", reason="multiple_previous_reference_targets")
            references.append(template)
        output["question_references"] = references
    return output


def replace_exchange_reference_data(db, question, payload):
    """ORM adapter; SQLite exchange uses the same pure normalization helper."""
    normalized = normalize_exchange_reference_data(question, payload)
    db.flush()
    qid = _get(question, "id", None)
    if not qid:
        raise ValueError("题目尚未保存")
    if "source_labels" in normalized:
        db.query(QuestionSourceLabel).filter_by(question_id=qid).delete(synchronize_session="fetch")
        for item in normalized["source_labels"]:
            db.add(QuestionSourceLabel(question_id=qid, **item))
        db.flush()
    if "question_references" in normalized:
        db.query(QuestionReference).filter_by(source_question_id=qid).delete(synchronize_session="fetch")
        for item in normalized["question_references"]:
            if item["resolution_state"] != "resolved" and item["reason"] != "multiple_previous_reference_targets":
                item["target_question_id"], item["resolution_state"], item["reason"] = _resolve(db, item["book_key"], item["label_key"], item["reason"])
            db.add(QuestionReference(source_question_id=qid, **item))
        db.flush()
    return normalized


def export_reference_text(question_dict):
    """Replace only resolved, hash-validated spans in a new export dictionary."""
    result = dict(question_dict)
    references = question_dict.get("question_references") or []
    for field in REFERENCE_FIELDS:
        source = str(question_dict.get(field) or "")
        digest = text_sha256(source)
        groups = defaultdict(list)
        for item in references:
            if item.get("field") == field and item.get("field_sha256") == digest and item.get("status") != "stale":
                groups[(item["start"], item["end"], item["text"])].append(item)
        boundary = len(source)
        for (start, end, original), members in sorted(groups.items(), key=lambda pair: pair[0][0], reverse=True):
            resolved = [item for item in members if item.get("status") == "resolved" and type(item.get("seq_num")) is int]
            if resolved and 0 <= start < end <= boundary and source[start:end] == original:
                numbers = "、".join(str(number) for number in dict.fromkeys(item["seq_num"] for item in resolved))
                unavailable = [item.get("label_key", "") + ("目标已删除" if item.get("status") == "target_deleted" else "待关联") for item in members if item not in resolved]
                note = "；" + "、".join(unavailable) if unavailable else ""
                source = source[:start] + f"题库第 {numbers} 题（原{original}{note}）" + source[end:]
                boundary = start
        if field in question_dict:
            result[field] = source
    return result
