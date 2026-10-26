"""Snapshot correctness and query work on disposable file-backed SQLite only."""

from datetime import datetime
import sqlite3

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from mathbank.database import Base, Question
from mathbank import question_queries
from mathbank.question_queries import QuestionReadSnapshot, page_questions
from mathbank.question_references import (
    reference_payloads, sync_question_references, sync_question_source_labels,
)
from mathbank.question_search import (
    SQLITE_SEARCH_FUNCTION, apply_question_search, searchable_question_text,
)


@pytest.fixture
def query_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'question-query.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one() == "wal"
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        db.add_all([
            Question(id=qid, content=f"needle original {qid}", display_order=order,
                     created_at=datetime(2020, 1, 1))
            for qid, order in ((3, 10), (8, 20), (15, 30))
        ])
        db.commit()
    try:
        yield engine, sessions
    finally:
        engine.dispose()


@pytest.mark.parametrize("single_scan", [False, True])
def test_request_reads_keep_one_snapshot_across_another_connection_commit(query_store, single_scan):
    engine, sessions = query_store
    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        assert snapshot.sequences([15]) == {15: 3}
        with sessions() as writer:
            writer.query(Question).filter_by(id=8).delete(synchronize_session=False)
            writer.execute(update(Question).where(Question.id == 15).values(
                content="needle changed by writer", display_order=1))
            writer.add(Question(id=21, content="needle inserted by writer", display_order=2))
            writer.commit()

        query = apply_question_search(reader.query(Question), "needle", order_plan=snapshot.order_plan)
        result = page_questions(query, snapshot, page=1, page_size=10,
                                descending=False, single_scan=single_scan)
        assert result.total == 3
        assert [(row.id, row.content) for row in result.items] == [
            (3, "needle original 3"), (8, "needle original 8"), (15, "needle original 15"),
        ]
        assert snapshot.sequences([3, 8, 15, 21]) == {3: 1, 8: 2, 15: 3}
        assert snapshot.is_current(reader)
        snapshot.close()
        reader.rollback()

    with sessions() as reader:
        fresh = QuestionReadSnapshot(reader)
        result = page_questions(reader.query(Question), fresh, page=1, descending=False)
        assert [row.id for row in result.items] == [15, 21, 3]
        assert fresh.sequences([3, 8, 15, 21]) == {15: 1, 21: 2, 3: 3}
        fresh.close()


def test_same_transaction_core_write_invalidates_sequence_reuse_without_committing(query_store):
    _engine, sessions = query_store
    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        assert snapshot.sequences([3]) == {3: 1}
        transaction = reader.get_transaction()
        reader.connection().exec_driver_sql("UPDATE questions SET display_order=50 WHERE id=3")
        assert reader.get_transaction() is transaction
        assert not snapshot.is_current(reader)
        with pytest.raises(RuntimeError):
            snapshot.sequences([3])
        with pytest.raises(RuntimeError):
            page_questions(reader.query(Question), snapshot, page=1)
        # The helper neither publishes nor rolls back its owner's Core write.
        assert reader.execute(select(Question.display_order).where(Question.id == 3)).scalar_one() == 50
        reader.rollback()
    with sessions() as reader:
        assert reader.execute(select(Question.display_order).where(Question.id == 3)).scalar_one() == 10


def test_reference_reader_cannot_revive_snapshot_after_raw_rollback(query_store):
    _engine, sessions = query_store
    with sessions() as writer:
        target = writer.get(Question, 8)
        source = writer.get(Question, 15)
        target.source = "原始回滚教材 | 例1.2"
        source.source = "原始回滚教材 | 例1.3"
        source.answer_markdown = "见例1.2。"
        writer.flush()
        for question in (target, source):
            sync_question_source_labels(writer, question)
        sync_question_references(writer, source)
        writer.commit()

    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        source = reader.get(Question, 15)
        assert snapshot.sequences([8]) == {8: 2}
        # Do not call is_current here: the reference reader must detect the
        # ended DBAPI transaction before its own compatibility BEGIN.
        reader.connection().exec_driver_sql("ROLLBACK")
        assert not snapshot.raw.in_transaction
        with sessions() as writer:
            writer.query(Question).filter_by(id=3).delete(synchronize_session=False)
            writer.commit()

        reference = reference_payloads(
            reader, [source], seq_mapping={8: 999}, read_snapshot=snapshot,
        )[15][0]
        assert reference["status"] == "resolved"
        assert reference["target_question_id"] == 8
        assert reference["seq_num"] == 1
        assert not snapshot.is_current(reader)
        with pytest.raises(RuntimeError):
            snapshot.sequences([8])


@pytest.mark.parametrize("action", ["snapshot_close", "rollback", "commit", "session_close"])
def test_ended_snapshot_cannot_reuse_numbers(query_store, action):
    _engine, sessions = query_store
    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        assert snapshot.sequences([8]) == {8: 2}
        if action == "snapshot_close":
            snapshot.close()
        elif action == "session_close":
            reader.close()
        else:
            getattr(reader, action)()
        assert not snapshot.is_current(reader)
        with pytest.raises(RuntimeError):
            snapshot.sequences([8])


def test_no_materialized_capability_falls_back_with_the_same_page(query_store, monkeypatch):
    engine, sessions = query_store
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        query = apply_question_search(reader.query(Question), "needle", order_plan=snapshot.order_plan)
        expected = page_questions(query, snapshot, page=2, page_size=2, descending=False,
                                  single_scan=False)
        monkeypatch.setattr(question_queries, "supports_materialized_cte", lambda _db: False)
        event.listen(engine, "before_cursor_execute", record)
        try:
            actual = page_questions(query, snapshot, page=2, page_size=2, descending=False,
                                    single_scan=True)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert (actual.total, actual.page, actual.page_size, actual.total_pages) == (3, 2, 2, 2)
        assert [row.id for row in actual.items] == [row.id for row in expected.items] == [15]
        assert not any("MATERIALIZED" in sql.upper() for sql in statements)
        snapshot.close()


@pytest.mark.parametrize("single_scan", [False, True])
def test_three_matches_keep_first_last_and_clamped_page(query_store, single_scan):
    _engine, sessions = query_store
    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        query = apply_question_search(reader.query(Question), "needle", order_plan=snapshot.order_plan)
        for requested, actual_page, expected in ((1, 1, [3, 8]), (2, 2, [15]), (3, 2, [15])):
            result = page_questions(query, snapshot, page=requested, page_size=2,
                                    descending=False, single_scan=single_scan)
            assert (result.total, result.page, result.total_pages) == (3, actual_page, 2)
            assert [row.id for row in result.items] == expected
        snapshot.close()


@pytest.mark.parametrize("custom_order", [False, True])
def test_bounded_id_page_uses_existing_order_indexes_without_temporary_sort(query_store, custom_order):
    engine, sessions = query_store
    if not custom_order:
        with sessions() as writer:
            writer.execute(update(Question).values(display_order=None))
            writer.commit()
    executed = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        executed.append((statement, parameters))

    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = page_questions(reader.query(Question), snapshot, page=2, page_size=2,
                                    descending=False, single_scan=False)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert [row.id for row in result.items] == [15]
        id_pages = [(sql, params) for sql, params in executed
                    if "LIMIT" in sql.upper() and "OFFSET" in sql.upper()]
        assert len(id_pages) == 1, executed
        sql, params = id_pages[0]
        plan = reader.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params).all()
        details = "\n".join(str(row[-1]) for row in plan)
        assert "USE TEMP B-TREE" not in details.upper(), details
        assert ("idx_questions_display_order" if custom_order else "idx_questions_created_at_id") in details
        snapshot.close()


def test_materialized_search_reduces_projection_work_without_schema_or_data_writes(query_store):
    engine, sessions = query_store
    with sessions() as writer:
        writer.add_all(Question(id=qid, content=f"needle original {qid}", display_order=qid)
                       for qid in range(30, 42))
        writer.commit()

    with sessions() as reader:
        if not question_queries.supports_materialized_cte(reader):
            pytest.skip("Installed SQLite has no MATERIALIZED CTE capability")
        reader.rollback()

    def database_state():
        path = engine.url.database
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            return (connection.execute("PRAGMA user_version").fetchone()[0], tuple(connection.iterdump()))

    before = database_state()
    calls = []

    def counted(value):
        calls.append(value)
        return searchable_question_text(value)

    with sessions() as reader:
        snapshot = QuestionReadSnapshot(reader)
        snapshot.raw.create_function(SQLITE_SEARCH_FUNCTION, 1, counted, deterministic=True)
        query = apply_question_search(reader.query(Question), "needle", order_plan=snapshot.order_plan)
        ordinary = page_questions(query, snapshot, page=3, page_size=3,
                                  descending=False, single_scan=False)
        ordinary_calls = len(calls)
        calls.clear()
        materialized = page_questions(query, snapshot, page=3, page_size=3,
                                     descending=False, single_scan=True)
        assert [row.id for row in materialized.items] == [row.id for row in ordinary.items]
        assert (materialized.total, materialized.page, materialized.total_pages) == (
            ordinary.total, ordinary.page, ordinary.total_pages)
        assert 0 < len(calls) < ordinary_calls
        snapshot.close()
    assert database_state() == before
