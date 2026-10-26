"""Literal question searches over copies with image destinations masked.

Stored text and assets stay untouched. SQLite first checks the original text,
then calls the pure text projection only for fields that could match, so a
search never materializes the library's question/knowledge ORM graphs.
"""

import re

from sqlalchemy import case, false, func, or_

from mathbank.question_assets import image_search_spans


SQLITE_SEARCH_FUNCTION = "mathbank_search_text"
_SEQUENCE = re.compile(r"(?P<exact>#)?(?P<number>[0-9]+)\Z")
_SQLITE_MAX_INTEGER = (1 << 63) - 1


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
    conditions = [
        literal_text_match(column, keyword, mask_image_targets=True)
        for column in (Question.content, Question.answer_markdown, Question.review)
    ]
    conditions.extend(literal_text_match(column, keyword) for column in (Question.source, Question.tags))
    if target_id is not None:
        conditions.append(Question.id == target_id)
    return query.filter(or_(*conditions))
