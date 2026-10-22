"""Reference exports fit old SQLite without losing source or target identities."""

import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from mathbank.database import Base, Question, QuestionReference, QuestionSourceLabel
from mathbank.question_references import (
    reference_payloads, serialize_question_reference_data, text_sha256,
)
from mathbank.sync_helper import serialize_exchange_questions


@pytest.fixture
def reference_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reference-batches.db'}")

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        # Python 3.10 has neither setlimit nor SQLITE_LIMIT_VARIABLE_NUMBER.
        # Query assertions below also enforce the bound on that runtime.
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=14")
    try:
        yield engine
    finally:
        engine.dispose()


def _reference(source_id, field, source, start, text, target_id):
    end = start + len(text)
    return {
        "source_question_id": source_id, "target_question_id": target_id,
        "field": field, "start": start, "end": end, "text": text,
        "label_key": text, "book_key": "测试书", "field_sha256": text_sha256(source),
        "prefix": source[max(0, start - 24):start], "suffix": source[end:end + 24],
        "resolution_state": "resolved", "reason": "",
    }


def _expected_payload(row, sequence, *, portable=False):
    result = {key: value for key, value in row.items() if key != "source_question_id"}
    result.update(status="resolved" if sequence is not None else "target_deleted",
                  reason="" if sequence is not None else "target_deleted")
    if not portable:
        result["seq_num"] = sequence
    return result


def _record_bounded_queries(engine):
    queries = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and " IN (" in statement:
            assert len(parameters) <= 500
            queries.append((statement, len(parameters)))

    event.listen(engine, "before_cursor_execute", record)
    return queries, record


def test_thousand_sources_keep_references_labels_and_portable_order(reference_database):
    engine = reference_database
    content, answer = "参考例1.1。", "见例1.2。"
    ids = list(range(1, 1001))
    refs = []
    for qid in ids:
        target = qid % 1000 + 1
        # Insert content first; the public payload order is field/start order.
        refs.extend([_reference(qid, "content", content, 2, "例1.1", target),
                     _reference(qid, "answer_markdown", answer, 1, "例1.2", target)])
    labels = [{"question_id": qid, "book_key": "测试书", "label_key": label, "original_label": label}
              for label in ("例1.2", "例1.1") for qid in ids]
    with engine.begin() as connection:
        connection.execute(Question.__table__.insert(), [
            {"id": qid, "content": content, "answer_markdown": answer, "source": "测试书 | 例1.2 / 例1.1"}
            for qid in ids
        ])
        connection.execute(QuestionReference.__table__.insert(), refs)
        connection.execute(QuestionSourceLabel.__table__.insert(), labels)
    queries, listener = _record_bounded_queries(engine)
    try:
        with Session(engine) as db:
            # Exercise the standalone reader, which can receive a whole page
            # or library independently of the sharing serializer's batches.
            questions = db.query(Question).order_by(Question.id.desc()).all()
            direct = reference_payloads(db, questions)
            assert list(direct) == list(reversed(ids))
            requested = list(reversed(ids)) + [1, 500, 1000]
            portable = serialize_question_reference_data(db, requested)
            assert list(portable) == list(set(requested))
            exchange, _ = serialize_exchange_questions(db, questions, "A")
            assert [row["id"] for row in exchange] == list(reversed(ids))
            assert all({key: row[key] for key in ("source_labels", "question_references")} == portable[row["id"]]
                       for row in exchange)
            for qid in ids:
                expected = [refs[(qid - 1) * 2 + 1], refs[(qid - 1) * 2]]
                sequence = qid % 1000 + 1
                assert direct[qid] == [_expected_payload(row, sequence) for row in expected]
                assert portable[qid] == {
                    "source_labels": [{"book_key": "测试书", "label_key": label, "original_label": label}
                                      for label in ("例1.2", "例1.1")],
                    "question_references": [_expected_payload(row, sequence, portable=True) for row in expected],
                }
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert any(count == 500 for _, count in queries)


def test_one_share_batch_can_resolve_more_than_999_distinct_targets(reference_database):
    engine = reference_database
    sources, refs = [], []
    target_ids = list(range(1001, 2153))
    for source_id in range(1, 129):
        selected = target_ids[(source_id - 1) * 9:source_id * 9]
        texts = [f"例2.{target_id}" for target_id in selected]
        content = "；".join(texts)
        sources.append({"id": source_id, "content": content})
        offset = 0
        for target_id, text in zip(selected, texts):
            refs.append(_reference(source_id, "content", content, offset, text, target_id))
            offset += len(text) + 1
    with engine.begin() as connection:
        connection.execute(Question.__table__.insert(), sources + [
            {"id": target_id, "content": f"目标{target_id}"} for target_id in target_ids
        ])
        connection.execute(QuestionReference.__table__.insert(), refs)
    queries, listener = _record_bounded_queries(engine)
    try:
        with Session(engine) as db:
            questions = db.query(Question).filter(Question.id <= 128).order_by(Question.id).all()
            direct = reference_payloads(db, questions)
            portable = serialize_question_reference_data(db, list(range(128, 0, -1)))
            for source_id in range(1, 129):
                expected = refs[(source_id - 1) * 9:source_id * 9]
                assert direct[source_id] == [
                    _expected_payload(row, 128 + row["target_question_id"] - 1000) for row in expected
                ]
                assert portable[source_id]["question_references"] == [
                    _expected_payload(row, 128 + row["target_question_id"] - 1000, portable=True)
                    for row in expected
                ]
                assert portable[source_id]["source_labels"] == []
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    target_query_sizes = [count for statement, count in queries if "row_number()" in statement]
    assert target_query_sizes == [500, 500, 152, 500, 500, 152]
