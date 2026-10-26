"""Library sequence order, independent of stable question identities."""

from dataclasses import dataclass

from sqlalchemy import func, select

from mathbank.database import Question


def assign_append_order(connection, question):
    """New rows stay at the end even after high-ID questions were deleted."""
    if question.display_order is not None:
        return
    table = Question.__table__
    if connection.execute(select(table.c.id).where(
            table.c.display_order.isnot(None)).limit(1)).first() is None:
        return
    maximum = connection.execute(select(func.max(func.coalesce(
        table.c.display_order, table.c.id)))).scalar()
    question.display_order = int(maximum or 0) + 1


@dataclass(frozen=True)
class QuestionOrderPlan:
    """Ordering facts valid only within the caller's existing read snapshot."""

    custom: bool
    sequence_key: object


def read_order_plan(db):
    custom = db.query(Question.id).filter(
        Question.display_order.isnot(None)).limit(1).first() is not None
    if not custom:
        return QuestionOrderPlan(False, Question.id)
    has_null = db.query(Question.id).filter(
        Question.display_order.is_(None)).limit(1).first() is not None
    key = (func.coalesce(Question.display_order, Question.id)
           if has_null else Question.display_order)
    return QuestionOrderPlan(True, key)


def sequence_order(*, descending=False, order_plan=None):
    key = (order_plan.sequence_key if order_plan is not None
           else func.coalesce(Question.display_order, Question.id))
    return ((key.desc(), Question.id.desc()) if descending
            else (key.asc(), Question.id.asc()))


def library_list_order(db, *, descending=True, order_plan=None):
    if order_plan is not None:
        if order_plan.custom:
            return sequence_order(descending=descending, order_plan=order_plan)
        return ((Question.created_at.desc(), Question.id.desc()) if descending
                else (Question.created_at.asc(), Question.id.asc()))
    # Keep historical date sorting until an explicit library reorder exists.
    custom = db.query(Question.id).filter(
        Question.display_order.isnot(None)).limit(1).first()
    if custom:
        return sequence_order(descending=descending)
    return ((Question.created_at.desc(), Question.id.desc()) if descending
            else (Question.created_at.asc(), Question.id.asc()))


def sequence_mapping(db, question_ids=None, *, order_plan=None):
    if question_ids is None:
        return {qid: number for number, (qid,) in enumerate(
            db.query(Question.id).order_by(*sequence_order(order_plan=order_plan)), 1)}
    ids = sorted({int(qid) for qid in question_ids})
    if not ids:
        return {}
    ranked = db.query(
        Question.id.label("qid"),
        func.row_number().over(order_by=sequence_order(order_plan=order_plan)).label("seq"),
    ).subquery()
    result = {}
    for start in range(0, len(ids), 500):
        result.update({qid: int(seq) for qid, seq in db.query(
            ranked.c.qid, ranked.c.seq).filter(
                ranked.c.qid.in_(ids[start:start + 500]))})
    return result
