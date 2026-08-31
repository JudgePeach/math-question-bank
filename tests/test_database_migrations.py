import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from mathbank import database as database_module
from mathbank import db_migrations


@contextmanager
def _sqlite_connection(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


@pytest.fixture(autouse=True)
def _dispose_created_engines(monkeypatch):
    created_engines = []
    original_create_engine = create_engine

    def tracked_create_engine(*args, **kwargs):
        engine = original_create_engine(*args, **kwargs)
        created_engines.append(engine)
        return engine

    monkeypatch.setattr(sys.modules[__name__], "create_engine", tracked_create_engine)
    yield
    for engine in created_engines:
        engine.dispose()


def _create_legacy_database(path):
    with _sqlite_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                paper_type TEXT DEFAULT 'exam',
                total_score INTEGER DEFAULT 150,
                metadata_json TEXT DEFAULT '{}',
                created_at DATETIME
            );
            CREATE TABLE question_curriculums (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL,
                version_code VARCHAR(50) NOT NULL,
                compulsory VARCHAR(100) DEFAULT '',
                chapter VARCHAR(100) DEFAULT '',
                knowledge VARCHAR(100) DEFAULT ''
            );
            CREATE TABLE paper_questions (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                order_index INTEGER DEFAULT 0,
                score INTEGER DEFAULT 5
            );
            INSERT INTO questions VALUES (1, 'question');
            INSERT INTO papers VALUES (1, 'paper', '', 'exam', 999, '{}', NULL);
            INSERT INTO question_curriculums VALUES (1, 1, 'A', 'old', '', '');
            INSERT INTO question_curriculums VALUES (2, 1, 'A', 'new', '', '');
            INSERT INTO question_curriculums VALUES (3, 999, 'B', 'orphan', '', '');
            INSERT INTO paper_questions VALUES (1, 1, 1, 0, 5);
            INSERT INTO paper_questions VALUES (2, 1, 1, 0, 10);
            INSERT INTO paper_questions VALUES (3, 1, 999, 1, 5);
            """
        )


def _create_version_five_database(path):
    engine = create_engine(f"sqlite:///{path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(path) as connection:
        connection.execute("DROP TABLE question_fingerprints")
        connection.execute("PRAGMA user_version=5")
        connection.execute(
            "INSERT INTO questions (id, content, image_paths) "
            "VALUES (1, '已存题目', '[]')"
        )


def _replace_with_pre_v8_fingerprint_table(connection, indexes):
    connection.execute("DROP TABLE question_fingerprints")
    connection.execute(
        """
        CREATE TABLE question_fingerprints (
            question_id INTEGER NOT NULL,
            fingerprint_version INTEGER NOT NULL,
            content_revision_hash VARCHAR(64) NOT NULL DEFAULT '',
            exact_hash VARCHAR(64) NOT NULL DEFAULT '',
            critical_math_hash VARCHAR(64) NOT NULL DEFAULT '',
            answer_hash VARCHAR(64) NOT NULL DEFAULT '',
            simhash_hex VARCHAR(32) NOT NULL DEFAULT '',
            token_count INTEGER NOT NULL DEFAULT 0,
            choice_count INTEGER NOT NULL DEFAULT 0,
            figure_count INTEGER NOT NULL DEFAULT 0,
            visible_image_hashes TEXT NOT NULL DEFAULT '[]',
            tikz_hashes TEXT NOT NULL DEFAULT '[]',
            band0 INTEGER NOT NULL DEFAULT 0,
            band1 INTEGER NOT NULL DEFAULT 0,
            band2 INTEGER NOT NULL DEFAULT 0,
            band3 INTEGER NOT NULL DEFAULT 0,
            band4 INTEGER NOT NULL DEFAULT 0,
            band5 INTEGER NOT NULL DEFAULT 0,
            band6 INTEGER NOT NULL DEFAULT 0,
            band7 INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (question_id, fingerprint_version),
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
        )
        """
    )
    for index_name, columns in indexes.items():
        connection.execute(
            f'CREATE INDEX "{index_name}" ON question_fingerprints '
            f"({', '.join(columns)})"
        )


def _create_pre_v8_database(path, *, version, indexes):
    engine = create_engine(f"sqlite:///{path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(path) as connection:
        _replace_with_pre_v8_fingerprint_table(connection, indexes)
        connection.execute(f"PRAGMA user_version={version}")
        connection.execute(
            "INSERT INTO questions (id, content, image_paths) "
            f"VALUES (1, '已存 v{version} 题目', '[]')"
        )
        connection.execute(
            "INSERT INTO question_fingerprints "
            "(question_id, fingerprint_version, exact_hash, token_count, "
            "band0, band1, band2, band3, band4, band5, band6, band7, status) "
            "VALUES (1, 2, 'preserve-me', 17, 1, 2, 3, 4, 5, 6, 7, 8, 'ready')"
        )


def _create_version_six_database(path):
    _create_pre_v8_database(
        path,
        version=6,
        indexes=db_migrations.V6_QUESTION_FINGERPRINT_INDEXES,
    )


def _create_version_seven_database(path):
    _create_pre_v8_database(
        path,
        version=7,
        indexes=db_migrations.V7_QUESTION_FINGERPRINT_INDEXES,
    )


def test_migration_repairs_legacy_relationships_and_adds_constraints(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    backup_dir = tmp_path / "backups"
    _create_legacy_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", backup_dir / "schema_snapshots")
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 0
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["removed_question_curriculums"] == 2
    assert result["removed_paper_questions"] == 2
    assert result["backup"]
    assert list((backup_dir / "schema_snapshots").glob("*.sha256"))

    with _sqlite_connection(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        assert connection.execute(
            "SELECT id, compulsory FROM question_curriculums"
        ).fetchall() == [(2, "new")]
        assert connection.execute(
            "SELECT id, score FROM paper_questions"
        ).fetchall() == [(2, 10)]
        assert connection.execute(
            "SELECT total_score FROM papers WHERE id = 1"
        ).fetchone()[0] == 10
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO question_curriculums "
                "(question_id, version_code) VALUES (1, 'A')"
            )

        connection.execute("DELETE FROM questions WHERE id = 1")
        assert connection.execute("SELECT COUNT(*) FROM question_curriculums").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_questions").fetchone()[0] == 0


def test_migration_refuses_unknown_future_schema(tmp_path):
    database_path = tmp_path / "future.db"
    _create_legacy_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute(f"PRAGMA user_version={db_migrations.LATEST_SCHEMA_VERSION + 1}")

    engine = create_engine(f"sqlite:///{database_path}")
    with pytest.raises(RuntimeError, match="高于程序支持版本"):
        db_migrations.migrate_database(engine)


def test_version_five_adds_indexed_non_unique_fingerprint_table(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-five.db"
    _create_version_five_database(database_path)
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 5
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["added_question_fingerprints"] == 1
    assert result["backup"]
    assert list(snapshot_dir.glob("*.sha256"))
    with _sqlite_connection(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("question_fingerprints")'
            )
        }
        assert db_migrations.QUESTION_FINGERPRINT_COLUMNS == columns
        primary_key = [
            row[1]
            for row in sorted(
                connection.execute(
                    'PRAGMA table_info("question_fingerprints")'
                ).fetchall(),
                key=lambda item: item[5],
            )
            if row[5]
        ]
        assert primary_key == ["question_id", "fingerprint_version"]

        indexes = {
            row[1]: row
            for row in connection.execute(
                'PRAGMA index_list("question_fingerprints")'
            )
        }
        assert set(db_migrations.QUESTION_FINGERPRINT_INDEXES).issubset(indexes)
        assert indexes["idx_question_fingerprints_exact"][2] == 0
        for index_name, expected_columns in (
            db_migrations.QUESTION_FINGERPRINT_INDEXES.items()
        ):
            actual_columns = tuple(
                row[2]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                )
            )
            assert actual_columns == expected_columns

        connection.execute(
            "INSERT INTO questions (id, content, image_paths) "
            "VALUES (2, '另一道题', '[]')"
        )
        for question_id in (1, 2):
            connection.execute(
                "INSERT INTO question_fingerprints "
                "(question_id, fingerprint_version, exact_hash, band0) "
                "VALUES (?, 1, 'same-exact-hash', 42)",
                (question_id,),
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM question_fingerprints "
            "WHERE fingerprint_version = 1 AND exact_hash = 'same-exact-hash'"
        ).fetchone()[0] == 2
        exact_plan = " ".join(
            str(value)
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT question_id "
                "FROM question_fingerprints "
                "WHERE fingerprint_version = 1 AND exact_hash = 'same-exact-hash'"
            )
            for value in row
        )
        assert "idx_question_fingerprints_exact" in exact_plan
        band_plan = " ".join(
            str(value)
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT question_id "
                "FROM question_fingerprints "
                "WHERE fingerprint_version = 1 AND band0 = 42 "
                "AND token_count BETWEEN 0 AND 20"
            )
            for value in row
        )
        assert "idx_question_fingerprints_band0" in band_plan
        assert "token_count" in band_plan
        text_band_plan = " ".join(
            str(value)
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT question_id "
                "FROM question_fingerprints "
                "WHERE fingerprint_version = 1 AND text_band0 = '' "
                "AND token_count BETWEEN 0 AND 20"
            )
            for value in row
        )
        assert "idx_question_fingerprints_text_band0" in text_band_plan
        assert "token_count" in text_band_plan

        connection.execute("DELETE FROM questions WHERE id = 1")
        assert connection.execute(
            "SELECT question_id FROM question_fingerprints"
        ).fetchall() == [(2,)]


def test_version_six_rebuilds_band_indexes_transactionally(tmp_path, monkeypatch):
    database_path = tmp_path / "version-six.db"
    _create_version_six_database(database_path)
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 6
    assert result["to_version"] == 8
    assert result["rebuilt_question_fingerprint_indexes"] == 8
    assert result["added_question_fingerprint_text_columns"] == 8
    assert result["added_question_fingerprint_text_indexes"] == 8
    assert result["backup"]
    assert list(snapshot_dir.glob("*.db"))
    assert list(snapshot_dir.glob("*.sha256"))
    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert connection.execute(
            "SELECT exact_hash, token_count, band0, band7, text_band0, "
            "text_band7, status "
            "FROM question_fingerprints WHERE question_id = 1"
        ).fetchone() == ("preserve-me", 17, 1, 8, "", "", "ready")
        index_rows = {
            row[1]: row
            for row in connection.execute(
                'PRAGMA index_list("question_fingerprints")'
            )
        }
        for index_name, expected_columns in (
            db_migrations.QUESTION_FINGERPRINT_INDEXES.items()
        ):
            actual_columns = tuple(
                row[2]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                )
            )
            assert actual_columns == expected_columns
            assert index_rows[index_name][2] == 0
            assert index_rows[index_name][4] == 0


def test_init_db_upgrades_v6_indexes_without_create_all_masking_them(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "init-version-six.db"
    _create_version_six_database(database_path)
    snapshot_dir = tmp_path / "schema_snapshots"
    engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)

    database_module.init_db()

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        for index_name, expected_columns in (
            db_migrations.QUESTION_FINGERPRINT_INDEXES.items()
        ):
            actual_columns = tuple(
                row[2]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                )
            )
            assert actual_columns == expected_columns
    assert list(snapshot_dir.glob("*.db"))
    assert list(snapshot_dir.glob("*.sha256"))


def test_version_six_index_ddl_failure_rolls_back_all_indexes(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-six-rollback.db"
    _create_version_six_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    def inject_failure(_conn, _cursor, statement, _parameters, _context, _many):
        if "CREATE INDEX" in statement and "text_band4" in statement:
            raise RuntimeError("injected combined v8 migration failure")

    event.listen(engine, "before_cursor_execute", inject_failure)
    with pytest.raises(RuntimeError, match="injected combined v8 migration failure"):
        db_migrations.migrate_database(engine)
    event.remove(engine, "before_cursor_execute", inject_failure)

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute(
            "SELECT exact_hash FROM question_fingerprints WHERE question_id = 1"
        ).fetchone() == ("preserve-me",)
        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("question_fingerprints")'
            )
        }
        assert columns == db_migrations.V6_QUESTION_FINGERPRINT_COLUMNS
        for index_name, expected_columns in (
            db_migrations.V6_QUESTION_FINGERPRINT_INDEXES.items()
        ):
            actual_columns = tuple(
                row[2]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                )
            )
            assert actual_columns == expected_columns


def test_version_seven_adds_text_bands_transactionally(tmp_path, monkeypatch):
    database_path = tmp_path / "version-seven.db"
    _create_version_seven_database(database_path)
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 7
    assert result["to_version"] == 8
    assert result["rebuilt_question_fingerprint_indexes"] == 0
    assert result["added_question_fingerprint_text_columns"] == 8
    assert result["added_question_fingerprint_text_indexes"] == 8
    assert result["backup"]
    assert list(snapshot_dir.glob("*.db"))
    assert list(snapshot_dir.glob("*.sha256"))
    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        table_info = connection.execute(
            'PRAGMA table_info("question_fingerprints")'
        ).fetchall()
        columns = {row[1] for row in table_info}
        assert columns == db_migrations.QUESTION_FINGERPRINT_COLUMNS
        info_by_column = {row[1]: row for row in table_info}
        for band_no in range(8):
            column_info = info_by_column[f"text_band{band_no}"]
            assert column_info[2].upper() == "VARCHAR(16)"
            assert column_info[3] == 1
            assert column_info[4] == "''"
        assert connection.execute(
            "SELECT exact_hash, text_band0, text_band7 "
            "FROM question_fingerprints WHERE question_id = 1"
        ).fetchone() == ("preserve-me", "", "")
        for index_name, expected_columns in (
            db_migrations.QUESTION_FINGERPRINT_INDEXES.items()
        ):
            actual_columns = tuple(
                row[2]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                )
            )
            assert actual_columns == expected_columns


def test_version_seven_text_index_failure_rolls_back_columns_and_indexes(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-seven-rollback.db"
    _create_version_seven_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    def inject_failure(_conn, _cursor, statement, _parameters, _context, _many):
        if "CREATE INDEX" in statement and "text_band4" in statement:
            raise RuntimeError("injected text-band migration failure")

    event.listen(engine, "before_cursor_execute", inject_failure)
    with pytest.raises(RuntimeError, match="injected text-band migration failure"):
        db_migrations.migrate_database(engine)
    event.remove(engine, "before_cursor_execute", inject_failure)

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("question_fingerprints")'
            )
        }
        assert columns == db_migrations.V7_QUESTION_FINGERPRINT_COLUMNS
        assert not {
            row[1]
            for row in connection.execute(
                'PRAGMA index_list("question_fingerprints")'
            )
            if row[1].startswith("idx_question_fingerprints_text_band")
        }
        for index_name, expected_columns in (
            db_migrations.V7_QUESTION_FINGERPRINT_INDEXES.items()
        ):
            actual_columns = tuple(
                row[2]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                )
            )
            assert actual_columns == expected_columns


@pytest.mark.parametrize("malformed_kind", ["unique", "partial"])
def test_current_schema_rejects_unique_or_partial_lookup_index(
    tmp_path, malformed_kind
):
    database_path = tmp_path / f"malformed-{malformed_kind}.db"
    engine = create_engine(f"sqlite:///{database_path}")
    database_module.Base.metadata.create_all(bind=engine)
    with _sqlite_connection(database_path) as connection:
        connection.execute(
            f"PRAGMA user_version={db_migrations.LATEST_SCHEMA_VERSION}"
        )
        if malformed_kind == "unique":
            connection.execute("DROP INDEX idx_question_fingerprints_band0")
            connection.execute(
                "CREATE UNIQUE INDEX idx_question_fingerprints_band0 "
                "ON question_fingerprints (fingerprint_version, band0, token_count)"
            )
        else:
            connection.execute("DROP INDEX idx_question_fingerprints_text_band0")
            connection.execute(
                "CREATE INDEX idx_question_fingerprints_text_band0 "
                "ON question_fingerprints "
                "(fingerprint_version, text_band0, token_count) "
                "WHERE text_band0 <> ''"
            )

    with pytest.raises(RuntimeError, match="不得设为"):
        db_migrations.migrate_database(engine)


def test_version_six_missing_fingerprint_table_fails_closed(tmp_path):
    database_path = tmp_path / "damaged-v6.db"
    _create_version_five_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute("PRAGMA user_version=6")
    engine = create_engine(f"sqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="question_fingerprints"):
        db_migrations.migrate_database(engine)

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).fetchone() is None


def test_init_db_upgrades_valid_v5_before_creating_fingerprint_table(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "init-v5.db"
    _create_version_five_database(database_path)
    snapshot_dir = tmp_path / "schema_snapshots"
    engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)

    database_module.init_db()

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).fetchone() == ("question_fingerprints",)
    assert list(snapshot_dir.glob("*.db"))
    assert list(snapshot_dir.glob("*.sha256"))


def test_init_db_refuses_v6_missing_fingerprint_table_before_create_all(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "init-damaged-v6.db"
    _create_version_five_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute("PRAGMA user_version=6")
    engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database_module, "engine", engine)

    with pytest.raises(RuntimeError, match="question_fingerprints"):
        database_module.init_db()

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).fetchone() is None


def test_version_five_fingerprint_ddl_failure_rolls_back(tmp_path, monkeypatch):
    database_path = tmp_path / "version-five-rollback.db"
    _create_version_five_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    def inject_failure(_conn, _cursor, statement, _parameters, _context, _many):
        if "CREATE INDEX idx_question_fingerprints_band4" in statement:
            raise RuntimeError("injected fingerprint migration failure")

    event.listen(engine, "before_cursor_execute", inject_failure)
    with pytest.raises(RuntimeError, match="injected fingerprint migration failure"):
        db_migrations.migrate_database(engine)
    event.remove(engine, "before_cursor_execute", inject_failure)

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).fetchone() is None


def test_migration_deduplicates_null_and_zero_paper_order_together(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "null-order.db"
    _create_legacy_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO paper_questions "
            "(id, paper_id, question_id, order_index, score) "
            "VALUES (4, 1, 1, NULL, 20)"
        )
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["removed_paper_questions"] == 3
    with _sqlite_connection(database_path) as connection:
        assert connection.execute(
            "SELECT id, order_index, score FROM paper_questions"
        ).fetchall() == [(4, 0, 20)]
        assert connection.execute(
            "SELECT total_score FROM papers WHERE id = 1"
        ).fetchone()[0] == 20


def test_version_one_database_is_backed_up_and_recalculates_paper_totals(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-one.db"
    _create_legacy_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute("PRAGMA user_version=1")
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 1
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["backup"]
    with _sqlite_connection(database_path) as connection:
        assert connection.execute(
            "SELECT total_score FROM papers WHERE id = 1"
        ).fetchone()[0] == 10


def test_version_two_database_adds_tikz_asset_fields_with_verified_backup(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-two.db"
    _create_legacy_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute("PRAGMA user_version=2")
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 2
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["added_answer_tikz_assets"] == 1
    assert result["added_tikz_reference_image_path"] == 1
    assert result["added_content_tikz_assets"] == 1
    assert result["backup"]
    assert list((tmp_path / "schema_snapshots").glob("*.sha256"))
    with _sqlite_connection(database_path) as connection:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("questions")')
        }
        assert "answer_tikz_assets" in columns
        assert "tikz_reference_image_path" in columns
        assert "content_tikz_assets" in columns
        assert connection.execute(
            "SELECT answer_tikz_assets FROM questions WHERE id = 1"
        ).fetchone()[0] == "[]"
        assert connection.execute(
            "SELECT tikz_reference_image_path FROM questions WHERE id = 1"
        ).fetchone()[0] == ""
        assert connection.execute(
            "SELECT content_tikz_assets FROM questions WHERE id = 1"
        ).fetchone()[0] == "[]"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )


def test_version_three_database_adds_tikz_reference_field_only(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-three.db"
    _create_legacy_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute(
            "ALTER TABLE questions ADD COLUMN answer_tikz_assets TEXT DEFAULT '[]'"
        )
        connection.execute("PRAGMA user_version=3")
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 3
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["added_answer_tikz_assets"] == 0
    assert result["added_tikz_reference_image_path"] == 1
    assert result["added_content_tikz_assets"] == 1
    assert result["backup"]
    with _sqlite_connection(database_path) as connection:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("questions")')
        }
        assert "answer_tikz_assets" in columns
        assert "tikz_reference_image_path" in columns
        assert "content_tikz_assets" in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )


def test_version_four_database_adds_content_tikz_assets_only(tmp_path, monkeypatch):
    database_path = tmp_path / "version-four.db"
    _create_legacy_database(database_path)
    with _sqlite_connection(database_path) as connection:
        connection.execute(
            "ALTER TABLE questions ADD COLUMN answer_tikz_assets TEXT DEFAULT '[]'"
        )
        connection.execute(
            "ALTER TABLE questions ADD COLUMN tikz_reference_image_path TEXT DEFAULT ''"
        )
        connection.execute("PRAGMA user_version=4")
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 4
    assert result["added_answer_tikz_assets"] == 0
    assert result["added_tikz_reference_image_path"] == 0
    assert result["added_content_tikz_assets"] == 1
    with _sqlite_connection(database_path) as connection:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("questions")')
        }
        assert "content_tikz_assets" in columns
        assert connection.execute(
            "SELECT content_tikz_assets FROM questions WHERE id = 1"
        ).fetchone()[0] == "[]"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )


def test_migration_ddl_failure_rolls_back_original_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "crash-safe.db"
    _create_legacy_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    def inject_failure(_conn, _cursor, statement, _parameters, _context, _many):
        if "ALTER TABLE question_curriculums__new RENAME" in statement:
            raise RuntimeError("injected migration failure")

    event.listen(engine, "before_cursor_execute", inject_failure)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        db_migrations.migrate_database(engine)
    event.remove(engine, "before_cursor_execute", inject_failure)

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT id, compulsory FROM question_curriculums ORDER BY id"
        ).fetchall() == [(1, "old"), (2, "new"), (3, "orphan")]
        assert connection.execute(
            "SELECT id, score FROM paper_questions ORDER BY id"
        ).fetchall() == [(1, 5), (2, 10), (3, 5)]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "question_curriculums__new" not in table_names
        assert "paper_questions__new" not in table_names


def test_migration_refuses_partially_missing_schema(tmp_path):
    database_path = tmp_path / "partial.db"
    with _sqlite_connection(database_path) as connection:
        connection.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
    engine = create_engine(f"sqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="缺少必要数据表"):
        db_migrations.migrate_database(engine)

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_persistence_modules_remain_importable_on_python_310():
    project_root = Path(__file__).resolve().parents[1]
    module_paths = [
        project_root / "mathbank" / "backup.py",
        project_root / "mathbank" / "db_migrations.py",
        project_root / "mathbank" / "database.py",
        project_root / "mathbank" / "task_manager.py",
        project_root / "mathbank" / "health.py",
        project_root / "scripts" / "backup.py",
        project_root / "scripts" / "restore.py",
    ]

    for module_path in module_paths:
        source = module_path.read_text(encoding="utf-8")
        assert "datetime.UTC" not in source
        assert "dt.UTC" not in source


def test_init_db_refuses_future_schema_before_mutating_it(tmp_path, monkeypatch):
    database_path = tmp_path / "future-init.db"
    with _sqlite_connection(database_path) as connection:
        connection.executescript(
            f"""
            PRAGMA user_version={db_migrations.LATEST_SCHEMA_VERSION + 1};
            CREATE TABLE future_only (id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO future_only (payload) VALUES ('preserve-me');
            """
        )
    before = database_path.read_bytes()
    future_engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database_module, "engine", future_engine)

    with pytest.raises(RuntimeError, match="高于程序支持版本"):
        database_module.init_db()

    assert database_path.read_bytes() == before
    with _sqlite_connection(database_path) as connection:
        assert connection.execute(
            "SELECT payload FROM future_only"
        ).fetchall() == [("preserve-me",)]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert table_names == {"future_only"}


@pytest.mark.parametrize("include_curriculum", [False, True])
def test_init_db_upgrades_known_questions_only_legacy_layouts(
    tmp_path, monkeypatch, include_curriculum
):
    database_path = tmp_path / "questions-only.db"
    with _sqlite_connection(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                question_type VARCHAR(50) DEFAULT 'single_choice',
                category_compulsory VARCHAR(100) DEFAULT '',
                category_chapter VARCHAR(100) DEFAULT '',
                category_knowledge VARCHAR(100) DEFAULT '',
                difficulty VARCHAR(50) DEFAULT 'medium',
                source VARCHAR(200) DEFAULT '',
                answer_markdown TEXT DEFAULT '',
                image_paths TEXT DEFAULT '[]',
                created_at DATETIME
            );
            INSERT INTO questions (
                id, content, question_type, category_compulsory,
                category_chapter, category_knowledge, difficulty,
                source, answer_markdown, image_paths, created_at
            ) VALUES (
                1, '历史题目', 'single_choice', '必修一',
                '第一章', '集合', 'medium', '', '', '[]', NULL
            );
            """
        )
        if include_curriculum:
            connection.executescript(
                """
                CREATE TABLE question_curriculums (
                    id INTEGER PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    version_code VARCHAR(50) NOT NULL,
                    compulsory VARCHAR(100) DEFAULT '',
                    chapter VARCHAR(100) DEFAULT '',
                    knowledge VARCHAR(100) DEFAULT ''
                );
                INSERT INTO question_curriculums VALUES
                    (1, 1, 'A', '必修一', '第一章', '集合');
                """
            )

    legacy_engine = create_engine(f"sqlite:///{database_path}")
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)

    database_module.init_db()

    with _sqlite_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert db_migrations.REQUIRED_TABLES.issubset(table_names)
        assert connection.execute(
            "SELECT question_id, version_code FROM question_curriculums"
        ).fetchall() == [(1, "A")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert list(snapshot_dir.glob("*.db"))
    assert list(snapshot_dir.glob("*.sha256"))
    legacy_engine.dispose()


def test_init_db_rejects_damaged_questions_table_before_creating_missing_tables(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "damaged.db"
    with _sqlite_connection(database_path) as connection:
        connection.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
    damaged_engine = create_engine(f"sqlite:///{database_path}")
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(database_module, "engine", damaged_engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)

    with pytest.raises(RuntimeError, match="缺少核心字段"):
        database_module.init_db()

    with _sqlite_connection(database_path) as connection:
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        } == {"questions"}
    assert not snapshot_dir.exists()
    damaged_engine.dispose()
