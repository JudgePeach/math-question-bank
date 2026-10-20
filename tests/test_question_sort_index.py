"""Backup-first, same-version sorting indexes; synthetic isolated SQLite only."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from mathbank import database, db_migrations as migrations


@pytest.fixture
def indexed_database(tmp_path, monkeypatch):
    path = tmp_path / "questions.db"
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    database.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={migrations.LATEST_SCHEMA_VERSION}")
        connection.exec_driver_sql(
            "INSERT INTO questions (id, content, question_type, category_chapter, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(i, f"synthetic {i}", "single_choice" if i % 2 else "detailed_answer",
              "chapter-a" if i % 3 else "chapter-b",
              None if i < 3 else f"2026-01-{(i % 9) + 1:02d} 00:00:00")
             for i in range(1, 129)],
        )
    yield engine, path
    engine.dispose()


def drop_sort_index(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {migrations.QUESTION_SORT_INDEX_NAME}")


def snapshot(engine):
    with engine.connect() as connection:
        return (connection.exec_driver_sql("SELECT * FROM questions ORDER BY id").fetchall(),
                connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def index_names(engine):
    with engine.connect() as connection:
        return {row[1] for row in connection.exec_driver_sql('PRAGMA index_list("questions")')}


def test_orm_new_database_already_has_sort_index_without_backup(indexed_database, monkeypatch):
    engine, _ = indexed_database
    assert any(tuple(column.name for column in index.columns) == ("created_at", "id")
               for index in database.Question.__table__.indexes)

    def forbidden_backup(*args, **kwargs):
        pytest.fail("An already indexed database must not create a backup")

    monkeypatch.setattr(migrations, "create_pre_migration_backup", forbidden_backup)
    result = migrations.ensure_question_sort_index(engine)
    assert result == {"added_question_sort_indexes": 0,
                      "question_sort_index": migrations.QUESTION_SORT_INDEX_NAME,
                      "sort_index_backup": None}


def test_missing_index_is_snapshotted_before_ddl_and_supplement_is_idempotent(indexed_database):
    engine, _ = indexed_database
    drop_sort_index(engine)
    before = snapshot(engine)
    result = migrations.ensure_question_sort_index(engine)
    assert result["added_question_sort_indexes"] == 1
    backup = Path(result["sort_index_backup"])
    assert backup.is_file()
    assert backup.with_suffix(".db.sha256").read_text().split()[0] == hashlib.sha256(backup.read_bytes()).hexdigest()
    with closing(sqlite3.connect(backup)) as connection:
        assert migrations.QUESTION_SORT_INDEX_NAME not in {row[1] for row in connection.execute('PRAGMA index_list("questions")')}
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT id, content FROM questions ORDER BY id").fetchall() == [
            (i, f"synthetic {i}") for i in range(1, 129)]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_SCHEMA_VERSION
    assert snapshot(engine) == before
    assert migrations.ensure_question_sort_index(engine)["added_question_sort_indexes"] == 0
    assert len(list(backup.parent.glob("*.db"))) == 1


@pytest.mark.parametrize("keys", [
    "created_at, id", "created_at DESC, id DESC", "created_at, id, question_type",
])
def test_equivalent_indexes_are_reused_by_shape_not_name(indexed_database, monkeypatch, keys):
    engine, _ = indexed_database
    drop_sort_index(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE INDEX "existing""sort" ON questions ({keys})')

    def forbidden_backup(*args, **kwargs):
        pytest.fail("Equivalent index must be reused without DDL or backup")

    monkeypatch.setattr(migrations, "create_pre_migration_backup", forbidden_backup)
    result = migrations.ensure_question_sort_index(engine)
    assert result["added_question_sort_indexes"] == 0
    assert result["question_sort_index"] == 'existing"sort'
    assert migrations.QUESTION_SORT_INDEX_NAME not in index_names(engine)


@pytest.mark.parametrize("definition", [
    "(created_at, id) WHERE created_at IS NOT NULL",
    "(created_at ASC, id DESC)",
    "(created_at COLLATE NOCASE, id)",
    "(id, created_at)",
    "(date(created_at), id)",
])
def test_partial_mixed_direction_collation_and_expression_are_not_equivalent(indexed_database, definition):
    engine, _ = indexed_database
    drop_sort_index(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE INDEX preserve_this_index ON questions {definition}")
    result = migrations.ensure_question_sort_index(engine)
    assert result["added_question_sort_indexes"] == 1
    assert {migrations.QUESTION_SORT_INDEX_NAME, "preserve_this_index"}.issubset(index_names(engine))


def test_name_collision_does_not_replace_or_hide_existing_index(indexed_database):
    engine, _ = indexed_database
    drop_sort_index(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE INDEX {migrations.QUESTION_SORT_INDEX_NAME} ON questions (content)")
    before = snapshot(engine)
    with pytest.raises(RuntimeError, match="未替换原索引"):
        migrations.ensure_question_sort_index(engine)
    with engine.connect() as connection:
        assert [row[2] for row in connection.exec_driver_sql(
            f"PRAGMA index_info({migrations.QUESTION_SORT_INDEX_NAME})")] == ["content"]
    assert snapshot(engine) == before


def test_failed_backup_prevents_index_ddl(indexed_database, monkeypatch):
    engine, _ = indexed_database
    drop_sort_index(engine)
    before = snapshot(engine)

    def failed_backup(*args, **kwargs):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(migrations, "create_pre_migration_backup", failed_backup)
    with pytest.raises(OSError, match="snapshot unavailable"):
        migrations.ensure_question_sort_index(engine)
    assert migrations.QUESTION_SORT_INDEX_NAME not in index_names(engine)
    assert snapshot(engine) == before


def test_failure_after_ddl_rolls_back_sort_index(indexed_database):
    engine, _ = indexed_database
    drop_sort_index(engine)
    before = snapshot(engine)

    def fail_after_create(_connection, _cursor, statement, *_args):
        if statement.startswith(f"CREATE INDEX {migrations.QUESTION_SORT_INDEX_NAME}"):
            raise RuntimeError("synthetic failure after DDL")

    event.listen(engine, "after_cursor_execute", fail_after_create)
    try:
        with pytest.raises(RuntimeError, match="synthetic failure after DDL"):
            migrations.ensure_question_sort_index(engine)
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_create)
    assert migrations.QUESTION_SORT_INDEX_NAME not in index_names(engine)
    assert snapshot(engine) == before


def test_init_db_existing_table_does_not_create_sort_index_before_backup(indexed_database, monkeypatch):
    engine, _ = indexed_database
    drop_sort_index(engine)
    observed = []
    original_backup = migrations.create_pre_migration_backup

    def check_backup(*args, **kwargs):
        observed.append(migrations.QUESTION_SORT_INDEX_NAME in index_names(engine))
        return original_backup(*args, **kwargs)

    monkeypatch.setattr(migrations, "create_pre_migration_backup", check_backup)
    monkeypatch.setattr(database, "engine", engine)
    before = snapshot(engine)
    database.init_db()
    assert observed == [False]
    assert migrations.QUESTION_SORT_INDEX_NAME in index_names(engine)
    assert snapshot(engine) == before
    database.init_db()
    assert observed == [False]


@pytest.mark.parametrize("direction", ["ASC", "DESC"])
@pytest.mark.parametrize("where", ["", "WHERE created_at >= '2026-01-04'"])
def test_sort_and_date_filter_plans_avoid_temporary_sorting(indexed_database, direction, where):
    engine, _ = indexed_database
    query = f"SELECT id, created_at FROM questions {where} ORDER BY created_at {direction}, id {direction} LIMIT 15 OFFSET 15"
    with engine.connect() as connection:
        plan = [row[3] for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + query)]
        rows = connection.exec_driver_sql(query).fetchall()
        all_rows = connection.exec_driver_sql("SELECT id, created_at FROM questions " + where).fetchall()
    assert any(migrations.QUESTION_SORT_INDEX_NAME in detail for detail in plan), plan
    assert not any("TEMP B-TREE" in detail.upper() for detail in plan), plan
    assert rows == sorted(all_rows, key=lambda row: (row[1] or "", row[0]),
                          reverse=direction == "DESC")[15:30]


@pytest.mark.parametrize("direction", ["ASC", "DESC"])
def test_category_filter_keeps_stable_order_with_the_planners_chosen_index(indexed_database, direction, record_property):
    engine, _ = indexed_database
    query = ("SELECT id, created_at FROM questions WHERE category_chapter = 'chapter-a' "
             f"ORDER BY created_at {direction}, id {direction} LIMIT 15 OFFSET 15")
    with engine.connect() as connection:
        plan = [row[3] for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + query)]
        rows = connection.exec_driver_sql(query).fetchall()
        all_rows = connection.exec_driver_sql("SELECT id, created_at FROM questions WHERE category_chapter = 'chapter-a'").fetchall()
    record_property("query_plan", "; ".join(plan))
    # A selective metadata index may remain cheaper and require a sort. This
    # supplement promises stable results, not elimination of every filter sort.
    assert rows == sorted(all_rows, key=lambda row: (row[1] or "", row[0]),
                          reverse=direction == "DESC")[15:30]


def test_prior_verified_startup_backup_is_reused(indexed_database, monkeypatch):
    engine, _ = indexed_database
    drop_sort_index(engine)
    prior = migrations.create_pre_migration_backup(
        engine, from_version=13, to_version=migrations.LATEST_SCHEMA_VERSION)

    def forbidden_backup(*args, **kwargs):
        pytest.fail("The existing startup backup must cover the index supplement")

    monkeypatch.setattr(migrations, "create_pre_migration_backup", forbidden_backup)
    result = migrations.ensure_question_sort_index(engine, pre_migration_backup=prior)
    assert result["added_question_sort_indexes"] == 1
    assert result["sort_index_backup"] == str(prior)


def test_future_schema_is_refused_without_supplement(indexed_database):
    engine, _ = indexed_database
    drop_sort_index(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={migrations.LATEST_SCHEMA_VERSION + 1}")
    before = snapshot(engine)
    with pytest.raises(RuntimeError, match="高于程序支持版本"):
        migrations.ensure_question_sort_index(engine)
    assert migrations.QUESTION_SORT_INDEX_NAME not in index_names(engine)
    assert snapshot(engine) == before
