"""Request-local question reads; no persistent indexes, cache or migration."""

from dataclasses import dataclass
import sqlite3

from sqlalchemy import func, select, true

from mathbank.database import Question
from mathbank.question_ordering import (
    library_list_order, read_order_plan, sequence_mapping,
)


_MAX_SQLITE_INTEGER = (1 << 63) - 1


class QuestionReadSnapshot:
    """Bind ordering and optional sequence reuse to one real read transaction.

    Closing this object discards only request-local facts. The Session owner
    retains responsibility for its transaction; existing caller transactions
    are never committed or rolled back here.
    """

    def __init__(self, db):
        self.db = db
        self.connection = db.connection()
        self.raw = self.connection.connection.dbapi_connection
        if isinstance(self.raw, sqlite3.Connection) and not self.raw.in_transaction:
            self.connection.exec_driver_sql("BEGIN")
        # BEGIN alone does not pin a SQLite WAL snapshot. Read the table before
        # releasing the caller's metadata lock or inspecting ordering facts.
        self.connection.execute(select(Question.id).limit(1)).first()
        self.transaction = db.get_transaction()
        self._changes = self.raw.total_changes
        self._schema = self.connection.exec_driver_sql("PRAGMA schema_version").scalar_one()
        self.order_plan = read_order_plan(db)
        self._sequences = {}
        self._numbered = set()
        self._active = True

    def is_current(self, db):
        if (not self._active or db is not self.db
                or db.get_transaction() is not self.transaction
                or not self.transaction.is_active):
            self.close()
            return False
        if (db.connection() is not self.connection or not self.raw.in_transaction
                or self.raw.total_changes != self._changes
                or db.new or db.dirty or db.deleted):
            self.close()
            return False
        if self.connection.exec_driver_sql("PRAGMA schema_version").scalar_one() != self._schema:
            self.close()
            return False
        return True

    def sequences(self, question_ids):
        if not self.is_current(self.db):
            raise RuntimeError("Question read snapshot is no longer current")
        ids = {int(qid) for qid in question_ids}
        missing = ids - self._numbered
        if missing:
            self._sequences.update(sequence_mapping(
                self.db, missing, order_plan=self.order_plan))
            self._numbered.update(missing)
        return {qid: self._sequences[qid] for qid in ids if qid in self._sequences}

    def close(self):
        self._active = False
        self._sequences.clear()
        self._numbered.clear()


@dataclass
class QuestionPage:
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int


def supports_materialized_cte(db):
    """Use the connected driver capability, without changing old installations."""
    dialect = db.connection().dialect
    version = dialect.server_version_info or sqlite3.sqlite_version_info
    return dialect.name == "sqlite" and version >= (3, 35, 0)


def _hydrate(query, ids):
    if not ids:
        return []
    # Preserve the caller's compact/full relationship loaders. Rechecking the
    # predicates touches only these IDs, and all reads share the same snapshot.
    by_id = {item.id: item for item in query.filter(
        Question.id.in_(ids)).order_by(None).all()}
    return [by_id[qid] for qid in ids]


def page_questions(query, snapshot, *, page, page_size=20, descending=True,
                   single_scan=False):
    """Page an unjoined Question query with WHERE filters, preserving loaders.

    Relationship predicates remain EXISTS/IN subqueries, as used by the list
    API. This is not a general JOIN/GROUP BY/DISTINCT query paginator.
    """
    db = query.session
    if not snapshot.is_current(db):
        raise RuntimeError("Question pagination requires a current read snapshot")
    size = max(1, min(int(page_size), 100))
    requested = max(1, min(int(page), _MAX_SQLITE_INTEGER))
    columns = library_list_order(db, descending=descending, order_plan=snapshot.order_plan)

    if single_scan and supports_materialized_cte(db):
        # Scalar CTEs deliberately have no ORM loader options. The same filtered
        # Query supplies both the predicates and, later, the loaded Question rows.
        key = snapshot.order_plan.sequence_key if snapshot.order_plan.custom else Question.created_at
        statement = select(Question.id.label("qid"), key.label("sort_key"))
        if query.whereclause is not None:
            statement = statement.where(query.whereclause)
        matches = statement.cte("question_matches").prefix_with("MATERIALIZED")
        totals = select(func.count().label("total")).select_from(matches).cte("question_totals")
        # Subtraction before division avoids integer overflow for huge counts.
        pages = func.max(1, (totals.c.total - 1).self_group().op("/", precedence=8)(size) + 1)
        selected_page = func.min(requested, pages)
        offset = select((selected_page - 1) * size).select_from(totals).scalar_subquery()
        sort = ((matches.c.sort_key.desc(), matches.c.qid.desc()) if descending
                else (matches.c.sort_key.asc(), matches.c.qid.asc()))
        selected = (select(matches.c.qid, matches.c.sort_key).order_by(*sort)
                    .limit(size).offset(offset).cte("question_page"))
        page_sort = ((selected.c.sort_key.desc(), selected.c.qid.desc()) if descending
                     else (selected.c.sort_key.asc(), selected.c.qid.asc()))
        rows = db.execute(select(totals.c.total, selected.c.qid).select_from(
            totals.outerjoin(selected, true())).order_by(*page_sort)).all()
        total = int(rows[0][0])
        ids = [row[1] for row in rows if row[1] is not None]
    else:
        total = query.order_by(None).count()
        pages = max(1, (total + size - 1) // size)
        current = min(requested, pages)
        ids = ([row[0] for row in query.enable_eagerloads(False).with_entities(
            Question.id).order_by(*columns).offset((current - 1) * size).limit(size).all()]
               if total else [])
    pages = max(1, (total + size - 1) // size)
    return QuestionPage(_hydrate(query, ids), total, min(requested, pages), size, pages)
