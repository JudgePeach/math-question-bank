"""Literal question searches over copies with image destinations masked.

Stored text and assets stay untouched. SQLite first checks the original text,
then calls the pure text projection only for fields that could match, so a
search never materializes the library's question/knowledge ORM graphs.
"""

import json
import re

from sqlalchemy import case, false, func, or_

from mathbank.question_assets import image_search_spans


SQLITE_SEARCH_FUNCTION = "mathbank_search_text"
_SEQUENCE = re.compile(r"(?P<exact>#)?(?P<number>[0-9]+)\Z")
_SQLITE_MAX_INTEGER = (1 << 63) - 1
MAX_SEARCH_RULES = 8
MAX_SEARCH_RULE_KEYWORD = 64
_TEXT_FIELDS = ("content", "answer_markdown", "review", "source", "tags")
IMAGE_SEARCH_FIELDS = frozenset(("content", "answer_markdown", "review"))


def searchable_question_text(value: str | None) -> str:
    """Mask actual image URI targets; keep visible alt text and literal code."""
    source = str(value or "")
    if "![" not in source and "\\includegraphics" not in source and "<img" not in source.lower():
        return source
    chars = list(source)
    for start, end in image_search_spans(source):
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def register_search_functions(connection) -> None:
    """Register a deterministic, read-only projection on a SQLite connection."""
    register_question_search_function(connection)


def register_question_search_function(connection) -> None:
    connection.create_function(
        SQLITE_SEARCH_FUNCTION, 1, searchable_question_text, deterministic=True,
    )


def literal_text_match(column, keyword: str, *, mask_image_targets=False):
    """Build a literal LIKE, projecting only raw matches when text has images."""
    raw_match = column.contains(keyword, autoescape=True)
    if not mask_image_targets:
        return raw_match
    # CASE guarantees SQLite does not run Python's image parser for every field
    # in every question, unlike an unguarded UDF around each full-text column.
    return case(
        (raw_match, getattr(func, SQLITE_SEARCH_FUNCTION)(column).contains(keyword, autoescape=True)),
        else_=False,
    )


def _text_conditions(keyword, fields=_TEXT_FIELDS):
    from mathbank.database import Question

    return [
        literal_text_match(getattr(Question, field), keyword,
                           mask_image_targets=field in IMAGE_SEARCH_FIELDS)
        for field in fields
    ]


def decode_question_search_rule(raw: str):
    if len(raw) > 1024:
        raise ValueError("关键词条件格式过长")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError("关键词条件格式无效") from exc


def normalize_question_search_rules(rules):
    """Validate keyword conditions and expand the fields they search."""
    if not isinstance(rules, list):
        raise ValueError("关键词条件须为列表")
    if len(rules) > MAX_SEARCH_RULES:
        raise ValueError(f"最多可添加 {MAX_SEARCH_RULES} 条关键词条件")
    normalized = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.keys() != {"field", "operator", "keyword"}:
            raise ValueError("关键词条件格式无效")
        field, operator, keyword = rule["field"], rule["operator"], rule["keyword"]
        if field not in ("all", *_TEXT_FIELDS) or operator not in ("include", "exclude"):
            raise ValueError("关键词条件的字段或匹配方式无效")
        if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= MAX_SEARCH_RULE_KEYWORD:
            raise ValueError(f"每条关键词条件需输入 1 至 {MAX_SEARCH_RULE_KEYWORD} 个字符")
        if "\0" in keyword or any("\ud800" <= char <= "\udfff" for char in keyword):
            raise ValueError("关键词包含无效字符")
        fields = _TEXT_FIELDS if field == "all" else (field,)
        normalized.append((fields, operator, keyword.strip()))
    return normalized


def apply_question_search_rules(query, rules):
    """Apply all field-specific literal rules before counting or paginating."""
    for fields, operator, keyword in normalize_question_search_rules(rules):
        match = or_(*_text_conditions(keyword, fields))
        # SQL NULL in an optional field counts as no match, even under exclusion.
        query = query.filter(match if operator == "include" else match.is_not(True))
    return query


def apply_question_search(query, keyword: str | None, *, order_plan=None):
    """Keep plain-number compatibility and make #number a strict display lookup."""
    from mathbank.database import Question
    from mathbank.question_ordering import sequence_order

    keyword = str(keyword or "").strip()
    if not keyword:
        return query
    sequence = _SEQUENCE.fullmatch(keyword)
    target_id = None
    if sequence:
        # Avoid binding arbitrarily large user input as a SQLite OFFSET.
        digits = sequence["number"].lstrip("0") or "0"
        number = int(digits) if len(digits) <= 19 else 0
        if 1 <= number <= _SQLITE_MAX_INTEGER:
            target = query.session.query(Question.id).order_by(
                *sequence_order(order_plan=order_plan)).offset(number - 1).limit(1).first()
            if target:
                target_id = target[0]
        if sequence["exact"]:
            return query.filter(Question.id == target_id if target_id is not None else false())
    conditions = _text_conditions(keyword)
    if target_id is not None:
        conditions.append(Question.id == target_id)
    return query.filter(or_(*conditions))
