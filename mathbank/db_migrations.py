"""Small, versioned SQLite migrations for MathBank.

The project intentionally avoids a heavyweight migration framework.  This
module keeps schema upgrades explicit, backup-first, and fail-closed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from mathbank.paths import SCHEMA_SNAPSHOT_DIR


LATEST_SCHEMA_VERSION = 8
LEGACY_REQUIRED_TABLES = {
    "questions",
    "question_curriculums",
    "papers",
    "paper_questions",
}
REQUIRED_TABLES = LEGACY_REQUIRED_TABLES | {"question_fingerprints"}

V6_QUESTION_FINGERPRINT_COLUMNS = {
    "question_id",
    "fingerprint_version",
    "content_revision_hash",
    "exact_hash",
    "critical_math_hash",
    "answer_hash",
    "simhash_hex",
    "token_count",
    "choice_count",
    "figure_count",
    "visible_image_hashes",
    "tikz_hashes",
    "band0",
    "band1",
    "band2",
    "band3",
    "band4",
    "band5",
    "band6",
    "band7",
    "status",
    "updated_at",
}
V7_QUESTION_FINGERPRINT_COLUMNS = set(V6_QUESTION_FINGERPRINT_COLUMNS)
QUESTION_FINGERPRINT_COLUMNS = V7_QUESTION_FINGERPRINT_COLUMNS | {
    "text_band0",
    "text_band1",
    "text_band2",
    "text_band3",
    "text_band4",
    "text_band5",
    "text_band6",
    "text_band7",
}

V6_QUESTION_FINGERPRINT_INDEXES = {
    "idx_question_fingerprints_exact": ("fingerprint_version", "exact_hash"),
    "idx_question_fingerprints_band0": ("fingerprint_version", "band0"),
    "idx_question_fingerprints_band1": ("fingerprint_version", "band1"),
    "idx_question_fingerprints_band2": ("fingerprint_version", "band2"),
    "idx_question_fingerprints_band3": ("fingerprint_version", "band3"),
    "idx_question_fingerprints_band4": ("fingerprint_version", "band4"),
    "idx_question_fingerprints_band5": ("fingerprint_version", "band5"),
    "idx_question_fingerprints_band6": ("fingerprint_version", "band6"),
    "idx_question_fingerprints_band7": ("fingerprint_version", "band7"),
}

V7_QUESTION_FINGERPRINT_INDEXES = {
    "idx_question_fingerprints_exact": ("fingerprint_version", "exact_hash"),
    "idx_question_fingerprints_band0": (
        "fingerprint_version",
        "band0",
        "token_count",
    ),
    "idx_question_fingerprints_band1": (
        "fingerprint_version",
        "band1",
        "token_count",
    ),
    "idx_question_fingerprints_band2": (
        "fingerprint_version",
        "band2",
        "token_count",
    ),
    "idx_question_fingerprints_band3": (
        "fingerprint_version",
        "band3",
        "token_count",
    ),
    "idx_question_fingerprints_band4": (
        "fingerprint_version",
        "band4",
        "token_count",
    ),
    "idx_question_fingerprints_band5": (
        "fingerprint_version",
        "band5",
        "token_count",
    ),
    "idx_question_fingerprints_band6": (
        "fingerprint_version",
        "band6",
        "token_count",
    ),
    "idx_question_fingerprints_band7": (
        "fingerprint_version",
        "band7",
        "token_count",
    ),
}

QUESTION_FINGERPRINT_INDEXES = {
    **V7_QUESTION_FINGERPRINT_INDEXES,
    "idx_question_fingerprints_text_band0": (
        "fingerprint_version",
        "text_band0",
        "token_count",
    ),
    "idx_question_fingerprints_text_band1": (
        "fingerprint_version",
        "text_band1",
        "token_count",
    ),
    "idx_question_fingerprints_text_band2": (
        "fingerprint_version",
        "text_band2",
        "token_count",
    ),
    "idx_question_fingerprints_text_band3": (
        "fingerprint_version",
        "text_band3",
        "token_count",
    ),
    "idx_question_fingerprints_text_band4": (
        "fingerprint_version",
        "text_band4",
        "token_count",
    ),
    "idx_question_fingerprints_text_band5": (
        "fingerprint_version",
        "text_band5",
        "token_count",
    ),
    "idx_question_fingerprints_text_band6": (
        "fingerprint_version",
        "text_band6",
        "token_count",
    ),
    "idx_question_fingerprints_text_band7": (
        "fingerprint_version",
        "text_band7",
        "token_count",
    ),
}


def _database_path(engine: Engine) -> Path | None:
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_version(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def create_pre_migration_backup(
    engine: Engine,
    *,
    from_version: int,
    to_version: int,
) -> Path | None:
    """Create and verify a consistent SQLite snapshot before a migration."""

    database_path = _database_path(engine)
    if database_path is None or not database_path.exists():
        return None

    backup_dir = SCHEMA_SNAPSHOT_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / (
        f"{database_path.stem}.schema-v{from_version}-to-v{to_version}.{timestamp}.db"
    )

    with closing(sqlite3.connect(database_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as target:
        source.backup(target)
        journal_mode = str(target.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode.lower() != "delete":
            raise RuntimeError(
                f"迁移前快照无法转换为独立日志模式: {journal_mode}"
            )
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"迁移前数据库快照校验失败: {integrity}")

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{backup_path}{suffix}")
        if suffix == "-wal" and sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError(f"迁移前快照仍依赖未归档 WAL: {sidecar}")
        sidecar.unlink(missing_ok=True)

    backup_path.chmod(0o600)
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    checksum_path.write_text(f"{_sha256(backup_path)}  {backup_path.name}\n", encoding="utf-8")
    checksum_path.chmod(0o600)
    return backup_path


def _ensure_tikz_asset_columns(connection) -> dict[str, int]:
    """Add editable TikZ asset columns when an older table lacks them."""

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    additions = {
        "answer_tikz_assets": "TEXT DEFAULT '[]'",
        "tikz_reference_image_path": "TEXT DEFAULT ''",
        "content_tikz_assets": "TEXT DEFAULT '[]'",
    }
    stats: dict[str, int] = {}
    for column, definition in additions.items():
        added = column not in columns
        if added:
            connection.exec_driver_sql(
                f"ALTER TABLE questions ADD COLUMN {column} {definition}"
            )
        stats[f"added_{column}"] = int(added)
    return stats


def _validate_question_fingerprint_schema(
    connection,
    *,
    expected_columns: set[str] | None = None,
    expected_indexes: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Fail closed when a versioned fingerprint table is structurally damaged."""

    expected_columns = (
        QUESTION_FINGERPRINT_COLUMNS
        if expected_columns is None
        else expected_columns
    )
    expected_indexes = (
        QUESTION_FINGERPRINT_INDEXES
        if expected_indexes is None
        else expected_indexes
    )

    table_names = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "question_fingerprints" not in table_names:
        raise RuntimeError(
            "数据库结构不完整，缺少必要数据表: question_fingerprints"
        )

    table_info = connection.exec_driver_sql(
        'PRAGMA table_info("question_fingerprints")'
    ).fetchall()
    columns = {row[1] for row in table_info}
    missing_columns = expected_columns - columns
    if missing_columns:
        raise RuntimeError(
            "数据库表 question_fingerprints 缺少核心字段: "
            + ", ".join(sorted(missing_columns))
        )
    unexpected_columns = columns - expected_columns
    if unexpected_columns:
        raise RuntimeError(
            "数据库表 question_fingerprints 包含与版本不符的字段: "
            + ", ".join(sorted(unexpected_columns))
        )

    primary_key = [
        row[1]
        for row in sorted(table_info, key=lambda item: item[5])
        if row[5]
    ]
    if primary_key != ["question_id", "fingerprint_version"]:
        raise RuntimeError("question_fingerprints 复合主键结构异常")

    foreign_keys = connection.exec_driver_sql(
        'PRAGMA foreign_key_list("question_fingerprints")'
    ).fetchall()
    has_question_cascade = any(
        row[2] == "questions"
        and row[3] == "question_id"
        and row[4] == "id"
        and str(row[6]).upper() == "CASCADE"
        for row in foreign_keys
    )
    if not has_question_cascade:
        raise RuntimeError("question_fingerprints 缺少 questions 级联删除外键")

    index_rows = {
        row[1]: row
        for row in connection.exec_driver_sql(
            'PRAGMA index_list("question_fingerprints")'
        ).fetchall()
    }
    missing_indexes = set(expected_indexes) - set(index_rows)
    if missing_indexes:
        raise RuntimeError(
            "question_fingerprints 缺少查重索引: "
            + ", ".join(sorted(missing_indexes))
        )
    managed_indexes = {
        name
        for name in index_rows
        if name.startswith("idx_question_fingerprints_")
    }
    unexpected_indexes = managed_indexes - set(expected_indexes)
    if unexpected_indexes:
        raise RuntimeError(
            "question_fingerprints 包含与版本不符的查重索引: "
            + ", ".join(sorted(unexpected_indexes))
        )
    for index_name, expected_columns in expected_indexes.items():
        actual_columns = tuple(
            row[2]
            for row in connection.exec_driver_sql(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
        )
        if actual_columns != expected_columns:
            raise RuntimeError(f"question_fingerprints 索引结构异常: {index_name}")
        index_row = index_rows[index_name]
        if int(index_row[2]) != 0:
            raise RuntimeError(
                f"question_fingerprints 查重索引不得设为唯一: {index_name}"
            )
        if len(index_row) < 5 or int(index_row[4]) != 0:
            raise RuntimeError(
                f"question_fingerprints 查重索引不得设为部分索引: {index_name}"
            )


def _ensure_question_fingerprint_table(connection) -> int:
    """Create the rebuildable fingerprint table with current lookup indexes."""

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).first()
    )
    if not table_exists:
        connection.exec_driver_sql(
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
                text_band0 VARCHAR(16) NOT NULL DEFAULT '',
                text_band1 VARCHAR(16) NOT NULL DEFAULT '',
                text_band2 VARCHAR(16) NOT NULL DEFAULT '',
                text_band3 VARCHAR(16) NOT NULL DEFAULT '',
                text_band4 VARCHAR(16) NOT NULL DEFAULT '',
                text_band5 VARCHAR(16) NOT NULL DEFAULT '',
                text_band6 VARCHAR(16) NOT NULL DEFAULT '',
                text_band7 VARCHAR(16) NOT NULL DEFAULT '',
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
                CONSTRAINT fk_question_fingerprints_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
            """
        )
        for index_name, columns in QUESTION_FINGERPRINT_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX {index_name} ON question_fingerprints "
                f"({', '.join(columns)})"
            )

    _validate_question_fingerprint_schema(connection)
    return int(not table_exists)


def _rebuild_relationship_tables(engine: Engine) -> dict[str, int]:
    """Rebuild relation tables with real FKs while repairing legacy drift."""

    stats: dict[str, int] = {}
    # AUTOCOMMIT lets us issue an explicit BEGIN IMMEDIATE.  Relying on the
    # sqlite3 driver's implicit transaction behavior is unsafe for DDL because
    # older driver modes may otherwise auto-commit CREATE/DROP statements.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            before_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            before_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS question_curriculums__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE question_curriculums__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    version_code VARCHAR(50) NOT NULL,
                    compulsory VARCHAR(100) DEFAULT '',
                    chapter VARCHAR(100) DEFAULT '',
                    knowledge VARCHAR(100) DEFAULT '',
                    CONSTRAINT uq_question_curriculum_version
                        UNIQUE (question_id, version_code),
                    CONSTRAINT fk_question_curriculums_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO question_curriculums__new
                    (id, question_id, version_code, compulsory, chapter, knowledge)
                SELECT qc.id, qc.question_id, qc.version_code,
                       qc.compulsory, qc.chapter, qc.knowledge
                FROM question_curriculums AS qc
                JOIN questions AS q ON q.id = qc.question_id
                JOIN (
                    SELECT question_id, version_code, MAX(id) AS keep_id
                    FROM question_curriculums
                    GROUP BY question_id, version_code
                ) AS newest ON newest.keep_id = qc.id
                """
            )
            connection.exec_driver_sql("DROP TABLE question_curriculums")
            connection.exec_driver_sql(
                "ALTER TABLE question_curriculums__new RENAME TO question_curriculums"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_lookup "
                "ON question_curriculums (version_code, compulsory, chapter, knowledge)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_qid "
                "ON question_curriculums (question_id)"
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS paper_questions__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE paper_questions__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    paper_id INTEGER NOT NULL,
                    question_id INTEGER NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    score INTEGER NOT NULL DEFAULT 5 CHECK (score >= 0),
                    CONSTRAINT uq_paper_question_order UNIQUE (paper_id, order_index),
                    CONSTRAINT fk_paper_questions_paper
                        FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    CONSTRAINT fk_paper_questions_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO paper_questions__new
                    (id, paper_id, question_id, order_index, score)
                SELECT pq.id, pq.paper_id, pq.question_id,
                       COALESCE(pq.order_index, 0),
                       CASE WHEN pq.score IS NULL OR pq.score < 0 THEN 0 ELSE pq.score END
                FROM paper_questions AS pq
                JOIN papers AS p ON p.id = pq.paper_id
                JOIN questions AS q ON q.id = pq.question_id
                JOIN (
                    SELECT paper_id, COALESCE(order_index, 0), MAX(id) AS keep_id
                    FROM paper_questions
                    GROUP BY paper_id, COALESCE(order_index, 0)
                ) AS newest ON newest.keep_id = pq.id
                """
            )
            connection.exec_driver_sql("DROP TABLE paper_questions")
            connection.exec_driver_sql(
                "ALTER TABLE paper_questions__new RENAME TO paper_questions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_paper_id ON paper_questions (paper_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_question_id ON paper_questions (question_id)"
            )
            connection.exec_driver_sql(
                """
                UPDATE papers
                SET total_score = COALESCE(
                    (
                        SELECT SUM(pq.score)
                        FROM paper_questions AS pq
                        WHERE pq.paper_id = papers.id
                    ),
                    0
                )
                """
            )

            remaining_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            remaining_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )
            tikz_column_stats = _ensure_tikz_asset_columns(connection)
            added_question_fingerprints = _ensure_question_fingerprint_table(connection)

            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"迁移后仍存在外键异常: {violations[:5]}")

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            stats = {
                "removed_question_curriculums": before_curriculums - remaining_curriculums,
                "removed_paper_questions": before_paper_questions - remaining_paper_questions,
                "added_question_fingerprints": added_question_fingerprints,
                **tikz_column_stats,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            enabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
            if enabled != 1:
                raise RuntimeError("迁移连接未能恢复 SQLite 外键检查")
    return stats


def _upgrade_derived_schema(engine: Engine) -> dict[str, int]:
    """Upgrade v2-v5 databases with TikZ fields and the current derived table."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_tikz_asset_columns(connection)
            stats["added_question_fingerprints"] = (
                _ensure_question_fingerprint_table(connection)
            )
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v6_or_v7_fingerprint_schema(
    engine: Engine,
    *,
    from_version: int,
) -> dict[str, int]:
    """Upgrade v6/v7 indexes and add v8 text bands in one transaction."""

    if from_version not in {6, 7}:
        raise ValueError(f"unsupported fingerprint schema upgrade: {from_version}")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            _validate_question_fingerprint_schema(
                connection,
                expected_columns=(
                    V6_QUESTION_FINGERPRINT_COLUMNS
                    if from_version == 6
                    else V7_QUESTION_FINGERPRINT_COLUMNS
                ),
                expected_indexes=(
                    V6_QUESTION_FINGERPRINT_INDEXES
                    if from_version == 6
                    else V7_QUESTION_FINGERPRINT_INDEXES
                ),
            )
            rebuilt_indexes = 0
            if from_version == 6:
                for index_name, columns in V7_QUESTION_FINGERPRINT_INDEXES.items():
                    if index_name == "idx_question_fingerprints_exact":
                        continue
                    connection.exec_driver_sql(f'DROP INDEX "{index_name}"')
                    connection.exec_driver_sql(
                        f'CREATE INDEX "{index_name}" ON question_fingerprints '
                        f"({', '.join(columns)})"
                    )
                    rebuilt_indexes += 1

            for band_no in range(8):
                connection.exec_driver_sql(
                    "ALTER TABLE question_fingerprints "
                    f"ADD COLUMN text_band{band_no} VARCHAR(16) "
                    "NOT NULL DEFAULT ''"
                )

            added_text_indexes = 0
            for band_no in range(8):
                index_name = f"idx_question_fingerprints_text_band{band_no}"
                columns = QUESTION_FINGERPRINT_INDEXES[index_name]
                connection.exec_driver_sql(
                    f'CREATE INDEX "{index_name}" ON question_fingerprints '
                    f"({', '.join(columns)})"
                )
                added_text_indexes += 1
            _validate_question_fingerprint_schema(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {
                "rebuilt_question_fingerprint_indexes": rebuilt_indexes,
                "added_question_fingerprint_text_columns": 8,
                "added_question_fingerprint_text_indexes": added_text_indexes,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def migrate_database(
    engine: Engine,
    *,
    pre_migration_backup: Path | None = None,
) -> dict[str, object]:
    """Upgrade the connected SQLite database and fail loudly on errors."""

    current = schema_version(engine)
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于程序支持版本 {LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    table_names = set(inspect(engine).get_table_names())
    if current == LATEST_SCHEMA_VERSION:
        if not REQUIRED_TABLES.issubset(table_names):
            missing = ", ".join(sorted(REQUIRED_TABLES - table_names))
            raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")
        with engine.connect() as connection:
            _validate_question_fingerprint_schema(connection)
        return {"from_version": current, "to_version": current, "backup": None}

    if not table_names:
        # A caller may version an empty database immediately before creating the
        # current ORM metadata.
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        return {"from_version": current, "to_version": LATEST_SCHEMA_VERSION, "backup": None}
    if not LEGACY_REQUIRED_TABLES.issubset(table_names):
        missing = ", ".join(sorted(LEGACY_REQUIRED_TABLES - table_names))
        raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")

    backup = pre_migration_backup or create_pre_migration_backup(
        engine, from_version=current, to_version=LATEST_SCHEMA_VERSION
    )
    if current < 2:
        stats = _rebuild_relationship_tables(engine)
    elif current in {6, 7}:
        stats = _upgrade_v6_or_v7_fingerprint_schema(
            engine,
            from_version=current,
        )
    else:
        stats = _upgrade_derived_schema(engine)
    return {
        "from_version": current,
        "to_version": LATEST_SCHEMA_VERSION,
        "backup": str(backup) if backup else None,
        **stats,
    }
