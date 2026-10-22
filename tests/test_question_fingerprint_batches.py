"""Fingerprint batch persistence against real file SQLite and foreign keys."""

from dataclasses import replace
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from mathbank.database import Base, Question, QuestionFingerprint
from mathbank.question_duplicate_service import (
    upsert_question_fingerprint,
    upsert_question_fingerprints,
)
from mathbank.question_duplicates import build_question_fingerprint


@pytest.fixture
def fingerprint_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fingerprints.sqlite3'}")

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        # Exercise the old SQLite variable limit even on modern runtimes.
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db, engine
    engine.dispose()


def _fingerprint(question):
    return build_question_fingerprint({"content": question.content})


def _stored_values(row):
    return {
        column.name: getattr(row, column.name)
        for column in QuestionFingerprint.__table__.columns
        if column.name not in {"id", "question_id", "updated_at"}
    }


def test_batch_matches_single_writer_and_retires_other_versions(fingerprint_database):
    db, _ = fingerprint_database
    single = Question(content=r"已知 $x^2=1$，求 $x$。")
    batch = Question(content=single.content)
    db.add_all([single, batch])
    db.flush()
    fingerprint = _fingerprint(single)
    for question in (single, batch):
        upsert_question_fingerprint(db, question, replace(fingerprint, fingerprint_version=1))
    db.commit()

    upsert_question_fingerprint(db, single, fingerprint)
    upsert_question_fingerprints(db, [(batch, fingerprint)])
    db.commit()
    single_row = db.query(QuestionFingerprint).filter_by(question_id=single.id).one()
    batch_row = db.query(QuestionFingerprint).filter_by(question_id=batch.id).one()
    assert _stored_values(single_row) == _stored_values(batch_row)
    assert batch_row.status == "ready"

    updated = build_question_fingerprint({"content": "题干已修改 $x=2$"})
    upsert_question_fingerprint(db, single, updated)
    upsert_question_fingerprints(db, [(batch, updated)])
    db.commit()
    db.expire_all()
    single_row = db.query(QuestionFingerprint).filter_by(question_id=single.id).one()
    batch_row = db.query(QuestionFingerprint).filter_by(question_id=batch.id).one()
    assert _stored_values(single_row) == _stored_values(batch_row)
    assert batch_row.exact_hash == updated.exact_hash


def test_batch_uses_bounded_executemany_under_old_sqlite_limit(fingerprint_database):
    db, engine = fingerprint_database
    questions = [Question(content=f"第 {index} 题 $x=1$") for index in range(257)]
    db.add_all(questions)
    db.flush()
    writes = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(_, __, statement, parameters, ___, executemany):
        if statement.startswith(("DELETE FROM question_fingerprints", "INSERT INTO question_fingerprints")):
            writes.append((statement, parameters, executemany))

    upsert_question_fingerprints(db, ((question, _fingerprint(question)) for question in questions))
    assert len(writes) == 6
    assert len([write for write in writes if write[2]]) == 2
    assert db.query(QuestionFingerprint).count() == 257
    # A second connection cannot see partial batches before the caller commits.
    with engine.connect() as independent:
        assert independent.exec_driver_sql("SELECT count(*) FROM question_fingerprints").scalar() == 0
    db.commit()
    with engine.connect() as independent:
        assert independent.exec_driver_sql("SELECT count(*) FROM question_fingerprints").scalar() == 257


def test_batch_failure_rolls_back_prior_batches_and_version_retirement(fingerprint_database):
    db, _ = fingerprint_database
    question = Question(content="原题 $x=1$")
    db.add(question)
    db.flush()
    fingerprint = _fingerprint(question)
    old = replace(fingerprint, fingerprint_version=1)
    upsert_question_fingerprint(db, question, old)
    db.commit()

    missing = Question(id=999999, content="不存在的题目")
    with pytest.raises(IntegrityError):
        upsert_question_fingerprints(
            db, [(question, fingerprint), (missing, _fingerprint(missing))], batch_size=1
        )
    db.rollback()
    row = db.query(QuestionFingerprint).one()
    assert row.question_id == question.id
    assert row.fingerprint_version == 1
    assert row.exact_hash == old.exact_hash


@pytest.mark.parametrize("batch_size", (0, -1, 501, True, 1.5))
def test_batch_rejects_invalid_bound(fingerprint_database, batch_size):
    db, _ = fingerprint_database
    with pytest.raises(ValueError, match="batch_size"):
        upsert_question_fingerprints(db, [], batch_size=batch_size)


def test_batch_rejects_duplicate_ids_without_committing(fingerprint_database):
    db, _ = fingerprint_database
    question = Question(content="原题 $x=1$")
    db.add(question)
    db.commit()
    fingerprint = _fingerprint(question)
    with pytest.raises(ValueError, match="distinct persisted"):
        upsert_question_fingerprints(db, [(question, fingerprint), (question, fingerprint)], batch_size=1)
    db.rollback()
    assert db.query(QuestionFingerprint).count() == 0
