"""Small, versioned SQLite migrations for MathBank.

The project intentionally avoids a heavyweight migration framework.  This
module keeps schema upgrades explicit, backup-first, and fail-closed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from mathbank.paths import SCHEMA_SNAPSHOT_DIR
from mathbank.sub_knowledge import normalize_sub_knowledge


LATEST_SCHEMA_VERSION = 15
LEGACY_REQUIRED_TABLES = {
    "questions",
    "question_curriculums",
    "papers",
    "paper_questions",
}
KNOWLEDGE_POINT_TABLES = {"knowledge_points", "knowledge_point_curriculums", "question_knowledge_points"}
QUESTION_REFERENCE_TABLES = {"question_source_labels", "question_references"}
REQUIRED_TABLES = LEGACY_REQUIRED_TABLES | {"question_fingerprints", "question_sub_knowledge", "document_import_receipts"} | KNOWLEDGE_POINT_TABLES | QUESTION_REFERENCE_TABLES

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


def _validate_question_order_schema(connection):
    columns = {row[1]: row for row in connection.exec_driver_sql('PRAGMA table_info("questions")')}
    column = columns.get("display_order")
    if column is None or str(column[2]).upper() != "INTEGER" or int(column[3]):
        raise RuntimeError("题目独立排序字段缺失或结构不正确")
    bad = connection.exec_driver_sql(
        "SELECT id FROM questions WHERE display_order IS NOT NULL AND "
        "(typeof(display_order) != 'integer' OR display_order <= 0) LIMIT 1"
    ).first()
    if bad:
        raise RuntimeError("题目独立排序值必须为正整数")
    indexes = {row[1] for row in connection.exec_driver_sql('PRAGMA index_list("questions")')}
    if "idx_questions_display_order" not in indexes:
        raise RuntimeError("题目独立排序索引缺失")
    keys = [row[2] for row in connection.exec_driver_sql(
        'PRAGMA index_info("idx_questions_display_order")')]
    if keys != ["display_order"]:
        raise RuntimeError("题目独立排序索引结构不正确")


def _ensure_question_order_schema(connection):
    columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info("questions")')}
    added = 0
    if "display_order" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE questions ADD COLUMN display_order INTEGER "
            "CHECK(display_order IS NULL OR display_order > 0)"
        )
        added = 1
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_questions_display_order ON questions(display_order)"
    )
    _validate_question_order_schema(connection)
    return {"added_question_display_order_columns": added}


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


QUESTION_SORT_INDEX_NAME = "idx_questions_created_at_id"


def _question_sort_index_name(connection) -> str | None:
    """Accept a full BINARY index whose leading keys serve either sort direction."""

    for index in connection.exec_driver_sql('PRAGMA index_list("questions")'):
        if len(index) < 5 or int(index[4]):
            continue  # A partial index cannot serve the unfiltered question list.
        name = str(index[1])
        quoted_name = '"' + name.replace('"', '""') + '"'
        keys = [row for row in connection.exec_driver_sql(
            f"PRAGMA index_xinfo({quoted_name})"
        ) if len(row) >= 6 and int(row[5])]
        if (len(keys) >= 2
                and tuple(row[2] for row in keys[:2]) == ("created_at", "id")
                and all(str(row[4]).upper() == "BINARY" for row in keys[:2])
                and int(keys[0][3]) == int(keys[1][3])):
            return name
    return None


def ensure_question_sort_index(
    engine: Engine,
    *,
    pre_migration_backup: Path | str | None = None,
) -> dict[str, object]:
    """Supplement a validated database without changing its local schema version.

    init_db calls this after the existing schema migration. New ORM databases
    already contain the index. Existing files are snapshotted before the first
    DDL; an earlier verified migration snapshot can cover the same startup.
    No question rows or existing indexes are changed.
    """

    current = schema_version(engine)
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于程序支持版本 {LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    with engine.connect() as connection:
        columns = {row[1] for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        )}
        if not {"created_at", "id"}.issubset(columns):
            raise RuntimeError("questions 缺少 created_at/id，不能补建排序索引")
        existing = _question_sort_index_name(connection)
    if existing:
        return {"added_question_sort_indexes": 0,
                "question_sort_index": existing, "sort_index_backup": None}

    backup = pre_migration_backup or create_pre_migration_backup(
        engine, from_version=current, to_version=current
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            # Another startup may have completed the supplement after our read.
            existing = _question_sort_index_name(connection)
            added = 0
            if not existing:
                conflict = connection.exec_driver_sql(
                    "SELECT type, tbl_name FROM sqlite_master WHERE name = ?",
                    (QUESTION_SORT_INDEX_NAME,),
                ).first()
                if conflict:
                    raise RuntimeError(
                        f"排序索引名称 {QUESTION_SORT_INDEX_NAME} 已被其他结构占用；"
                        "请先核查，未替换原索引"
                    )
                connection.exec_driver_sql(
                    f"CREATE INDEX {QUESTION_SORT_INDEX_NAME} "
                    "ON questions (created_at, id)"
                )
                existing = _question_sort_index_name(connection)
                if not existing:
                    raise RuntimeError("questions 排序索引补建后验证失败")
                added = 1
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise
    return {"added_question_sort_indexes": added,
            "question_sort_index": existing,
            "sort_index_backup": str(backup) if backup else None}


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


def _ensure_figure_layout_columns(connection) -> dict[str, int]:
    """Add v9 group preferences and v10 per-image layouts without rewriting rows."""

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    additions = {
        "figure_size": "VARCHAR(20) NOT NULL DEFAULT 'auto'",
        "image_layouts": "TEXT NOT NULL DEFAULT '{}'",
        "figure_align_custom": "INTEGER NOT NULL DEFAULT 0",
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


def _validate_figure_layout_schema(connection) -> None:
    """Fail closed when the current database lacks a layout preference."""

    table_info = connection.exec_driver_sql(
        'PRAGMA table_info("questions")'
    ).fetchall()
    info_by_name = {row[1]: row for row in table_info}
    image_info = info_by_name.get("image_layouts")
    if image_info is None or int(image_info[3]) != 1:
        raise RuntimeError("数据库表 questions 缺少核心字段或结构异常: image_layouts")
    column_info = info_by_name.get("figure_size")
    if column_info is None:
        raise RuntimeError("数据库表 questions 缺少核心字段: figure_size")
    if int(column_info[3]) != 1 or str(column_info[4]).strip("'\"") != "auto":
        raise RuntimeError("questions.figure_size 结构异常")
    invalid_count = int(
        connection.exec_driver_sql(
            "SELECT COUNT(*) FROM questions "
            "WHERE figure_size NOT IN ('auto', 'small', 'medium', 'large')"
        ).scalar_one()
    )
    if invalid_count:
        raise RuntimeError("questions.figure_size 包含无效值")
    custom_info = info_by_name.get("figure_align_custom")
    if custom_info is None:
        raise RuntimeError(
            "数据库表 questions 缺少核心字段: figure_align_custom"
        )
    if int(custom_info[3]) != 1 or str(custom_info[4]).strip("'\"") != "0":
        raise RuntimeError("questions.figure_align_custom 结构异常")
    invalid_custom_count = int(
        connection.exec_driver_sql(
            "SELECT COUNT(*) FROM questions WHERE figure_align_custom NOT IN (0, 1)"
        ).scalar_one()
    )
    if invalid_custom_count:
        raise RuntimeError("questions.figure_align_custom 包含无效值")


def _ensure_sub_knowledge_columns(connection) -> dict[str, int]:
    """v11 adds an empty relation; no historical chapter labels/tags change."""

    exists = bool(connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_sub_knowledge'"
    ).first())
    if not exists:
        connection.exec_driver_sql("""
            CREATE TABLE question_sub_knowledge (
                id INTEGER NOT NULL PRIMARY KEY,
                curriculum_id INTEGER NOT NULL,
                compulsory VARCHAR(100) NOT NULL,
                chapter VARCHAR(100) NOT NULL,
                knowledge VARCHAR(100) NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                CONSTRAINT uq_question_sub_knowledge_path UNIQUE (curriculum_id, compulsory, chapter, knowledge),
                CONSTRAINT ck_question_sub_knowledge_position CHECK (position >= 0),
                FOREIGN KEY(curriculum_id) REFERENCES question_curriculums(id) ON DELETE CASCADE
            )
        """)
        connection.exec_driver_sql("CREATE INDEX ix_question_sub_knowledge_curriculum_id ON question_sub_knowledge (curriculum_id)")
    _validate_sub_knowledge_schema(connection)
    return {"added_question_sub_knowledge": int(not exists)}


def _validate_sub_knowledge_schema(connection) -> None:
    table = "question_sub_knowledge"
    info = {row[1]: row for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()}
    fields = {"id", "curriculum_id", "compulsory", "chapter", "knowledge", "position"}
    if set(info) != fields or any(int(info[field][3]) != 1 for field in fields - {"id"}):
        raise RuntimeError(f"数据库表 {table} 缺少核心字段或结构异常")
    if int(info["id"][5]) != 1 or any(int(info[field][5]) != 0 for field in fields - {"id"}):
        raise RuntimeError("question_sub_knowledge 主键结构异常")
    if str(info["position"][4]).strip("'\"") != "0":
        raise RuntimeError("question_sub_knowledge 排序默认值异常")
    foreign_keys = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').fetchall()
    if not any(row[2] == "question_curriculums" and row[3] == "curriculum_id" and row[4] == "id"
               and str(row[6]).upper() == "CASCADE" for row in foreign_keys):
        raise RuntimeError("question_sub_knowledge 缺少版本镜像级联外键")
    indexes = connection.exec_driver_sql(f'PRAGMA index_list("{table}")').fetchall()
    if not any(tuple(entry[2] for entry in connection.exec_driver_sql(
        f'PRAGMA index_info("{row[1]}")'
    ).fetchall()) == ("curriculum_id",) for row in indexes):
        raise RuntimeError("question_sub_knowledge 缺少版本镜像索引")
    if not any(int(row[2]) == 1 and tuple(entry[2] for entry in connection.exec_driver_sql(
        f'PRAGMA index_info("{row[1]}")'
    ).fetchall()) == ("curriculum_id", "compulsory", "chapter", "knowledge") for row in indexes):
        raise RuntimeError("question_sub_knowledge 缺少完整路径唯一约束")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check(question_sub_knowledge)").fetchall()
    if violations:
        raise RuntimeError("question_sub_knowledge 存在外键异常")
    for row in connection.exec_driver_sql("SELECT id, compulsory, chapter, knowledge, position FROM question_sub_knowledge"):
        try:
            normalize_sub_knowledge([dict(zip(("compulsory", "chapter", "knowledge"), row[1:4]))])
            if row[4] < 0:
                raise ValueError("position")
        except ValueError as exc:
            raise RuntimeError(f"question_sub_knowledge 包含无效值 (id={row[0]})") from exc


def _ensure_knowledge_point_tables(connection) -> dict[str, int]:
    """v12 adds independent point tables; existing question fields stay intact."""

    from mathbank.database import KnowledgePoint, KnowledgePointCurriculum, QuestionKnowledgePoint
    existing = set(inspect(connection).get_table_names())
    stats = {}
    for model in (KnowledgePoint, KnowledgePointCurriculum, QuestionKnowledgePoint):
        missing = model.__tablename__ not in existing
        if missing:
            model.__table__.create(bind=connection)
        stats[f"added_{model.__tablename__}"] = int(missing)
    _validate_knowledge_point_schema(connection)
    return stats


def _validate_knowledge_point_schema(connection) -> None:
    """Fail closed for incomplete constraints, indexes, or persisted point data."""

    from mathbank.database import KnowledgePoint, KnowledgePointCurriculum, QuestionKnowledgePoint
    models = (KnowledgePoint, KnowledgePointCurriculum, QuestionKnowledgePoint)
    expected_indexes = {
        "knowledge_points": {(False, ("name",))},
        "knowledge_point_curriculums": {
            (True, ("point_code", "version_code", "compulsory", "chapter", "knowledge", "reference_section", "level")),
            (False, ("point_code",)),
            (False, ("version_code", "compulsory", "chapter", "knowledge", "point_code")),
        },
        "question_knowledge_points": {
            (True, ("question_id", "point_code")), (False, ("question_id",)),
            (False, ("point_code", "role", "status", "question_id")),
        },
    }
    for model in models:
        table = model.__tablename__
        info = {row[1]: row for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')}
        columns = model.__table__.columns
        if set(info) != set(columns.keys()) or any(not column.nullable and not int(info[column.name][3]) for column in columns if column.name != "id"):
            raise RuntimeError(f"知识点表 {table} 缺少核心字段或非空约束")
        primary_keys = tuple(row[1] for row in sorted(info.values(), key=lambda row: row[5]) if row[5])
        if primary_keys != tuple(column.name for column in columns if column.primary_key):
            raise RuntimeError(f"知识点表 {table} 主键结构异常")
        actual_indexes = set()
        for index in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
            actual_indexes.add((bool(index[2]), tuple(row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info("{index[1]}")'))))
        if not expected_indexes[table].issubset(actual_indexes):
            raise RuntimeError(f"知识点表 {table} 缺少索引或唯一约束")
        for foreign_key in model.__table__.foreign_keys:
            if not any(row[2] == foreign_key.column.table.name and row[3] == foreign_key.parent.name and row[4] == foreign_key.column.name and row[6].upper() == "CASCADE" for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')):
                raise RuntimeError(f"知识点表 {table} 缺少级联外键")
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar_one()
        for constraint in model.__table__.constraints:
            if hasattr(constraint, "sqltext") and str(constraint.sqltext) not in ddl:
                raise RuntimeError(f"知识点表 {table} 缺少值域约束")
        if connection.exec_driver_sql(f'PRAGMA foreign_key_check("{table}")').fetchall():
            raise RuntimeError(f"知识点表 {table} 存在外键异常")
    try:
        for row in connection.exec_driver_sql("SELECT code, name, original_name, source, aliases FROM knowledge_points"):
            if any(not isinstance(value, str) or not value.strip() for value in row[:4]):
                raise ValueError("point identity")
            aliases = json.loads(row[4])
            if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
                raise ValueError("aliases")
        for row in connection.exec_driver_sql("SELECT level, knowledge FROM knowledge_point_curriculums"):
            if row[0] not in {"leaf", "parent", "chapter"} or (row[0] == "chapter") != (not row[1]):
                raise ValueError("point curriculum")
        for row in connection.exec_driver_sql("SELECT role, status, position, scope, evidence_source, evidence, reason, origin FROM question_knowledge_points"):
            if row[0] not in {"main", "auxiliary"} or row[1] not in {"confirmed", "suggested"} or not isinstance(row[2], int) or row[2] < 0:
                raise ValueError("point role/status/position")
            for value in row[3:]:
                json.loads(value)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("知识点表包含无效数据") from exc


def _ensure_document_import_receipts(connection) -> dict[str, int]:
    from mathbank.database import DocumentImportReceipt
    missing = DocumentImportReceipt.__tablename__ not in set(inspect(connection).get_table_names())
    if missing:
        DocumentImportReceipt.__table__.create(bind=connection)
    _validate_document_import_receipts(connection)
    return {"added_document_import_receipts": int(missing)}


def _validate_document_import_receipts(connection) -> None:
    inspector = inspect(connection)
    columns = {row["name"] for row in inspector.get_columns("document_import_receipts")}
    required = {"id", "project_id", "card_id", "payload_sha256", "draft_revision", "question_id", "created_at"}
    if not required.issubset(columns):
        raise RuntimeError("导入回执表缺少必要字段")
    uniques = {tuple(row.get("column_names") or ()) for row in inspector.get_unique_constraints("document_import_receipts")}
    foreign_keys = inspector.get_foreign_keys("document_import_receipts")
    if (("project_id", "card_id") not in uniques or not any(
            row.get("constrained_columns") == ["question_id"] and row.get("referred_table") == "questions"
            and (row.get("options") or {}).get("ondelete", "").upper() == "SET NULL" for row in foreign_keys)):
        raise RuntimeError("导入回执表的唯一约束或外键无效")


def _ensure_question_reference_tables(connection) -> dict[str, int]:
    """v13 creates empty reference tables; startup never guesses bindings."""

    from mathbank.database import QuestionReference, QuestionSourceLabel
    existing = set(inspect(connection).get_table_names())
    stats = {}
    for model in (QuestionSourceLabel, QuestionReference):
        missing = model.__tablename__ not in existing
        if missing:
            model.__table__.create(bind=connection)
        stats[f"added_{model.__tablename__}"] = int(missing)
    _validate_question_reference_schema(connection)
    _ensure_document_import_receipts(connection)
    return stats


def _validate_question_reference_schema(connection) -> None:
    """Require index/constraint/FK contracts, including target SET NULL safety."""

    from sqlalchemy import UniqueConstraint
    from mathbank.database import QuestionReference, QuestionSourceLabel
    from mathbank.question_references import normalize_book_key, normalize_label_key
    for model in (QuestionSourceLabel, QuestionReference):
        table = model.__tablename__
        columns = model.__table__.columns
        info = {row[1]: row for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')}
        if set(info) != set(columns.keys()) or any(not column.nullable and not int(info[column.name][3]) for column in columns if column.name != "id"):
            raise RuntimeError(f"交叉引用表 {table} 缺少核心字段或非空约束")
        if model is QuestionReference and int(info["target_question_id"][3]):
            raise RuntimeError("交叉引用目标必须允许置空")
        primary_keys = tuple(row[1] for row in sorted(info.values(), key=lambda row: row[5]) if row[5])
        if primary_keys != ("id",):
            raise RuntimeError(f"交叉引用表 {table} 主键结构异常")
        expected_indexes = {(index.unique, tuple(column.name for column in index.columns)) for index in model.__table__.indexes}
        expected_indexes.update((True, tuple(column.name for column in constraint.columns)) for constraint in model.__table__.constraints if isinstance(constraint, UniqueConstraint))
        actual_indexes = set()
        for index in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
            actual_indexes.add((bool(index[2]), tuple(row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info("{index[1]}")'))))
        if not expected_indexes.issubset(actual_indexes):
            raise RuntimeError(f"交叉引用表 {table} 缺少索引或唯一约束")
        actual_foreign_keys = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').fetchall()
        for foreign_key in model.__table__.foreign_keys:
            if not any(row[2] == foreign_key.column.table.name and row[3] == foreign_key.parent.name and row[4] == foreign_key.column.name and row[6].upper() == foreign_key.ondelete for row in actual_foreign_keys):
                raise RuntimeError(f"交叉引用表 {table} 缺少删除保护外键")
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar_one()
        for constraint in model.__table__.constraints:
            if hasattr(constraint, "sqltext") and str(constraint.sqltext) not in ddl:
                raise RuntimeError(f"交叉引用表 {table} 缺少值域约束")
        if connection.exec_driver_sql(f'PRAGMA foreign_key_check("{table}")').fetchall():
            raise RuntimeError(f"交叉引用表 {table} 存在外键异常")
    try:
        for book, label, original in connection.exec_driver_sql("SELECT book_key,label_key,original_label FROM question_source_labels"):
            if not book or normalize_book_key(book) != book or len(book) > 200 or not label or normalize_label_key(label) != label or len(label) > 100 or not original or len(original) > 200:
                raise ValueError("source label")
        for field, start, end, text, book, label, digest, prefix, suffix, state, target in connection.exec_driver_sql("SELECT field,start,end,text,book_key,label_key,field_sha256,prefix,suffix,resolution_state,target_question_id FROM question_references"):
            if field not in {"content", "answer_markdown", "review"} or type(start) is not int or type(end) is not int or start < 0 or end <= start or len(text) != end - start:
                raise ValueError("reference span")
            if normalize_book_key(book) != book or len(book) > 200 or (label and normalize_label_key(label) != label) or len(label) > 100 or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("reference identity")
            if len(prefix) > 24 or len(suffix) > 24 or state not in {"resolved", "unresolved", "ambiguous"} or (state != "resolved" and target is not None):
                raise ValueError("reference state")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("交叉引用表包含无效数据") from exc


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
            figure_layout_stats = _ensure_figure_layout_columns(connection)
            sub_knowledge_stats = _ensure_sub_knowledge_columns(connection)
            knowledge_point_stats = _ensure_knowledge_point_tables(connection)
            question_reference_stats = _ensure_question_reference_tables(connection)
            question_order_stats = _ensure_question_order_schema(connection)
            added_question_fingerprints = _ensure_question_fingerprint_table(connection)
            _validate_figure_layout_schema(connection)

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
                **figure_layout_stats,
                **sub_knowledge_stats,
                **knowledge_point_stats,
                **question_reference_stats,
                **question_order_stats,
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
            stats.update(_ensure_figure_layout_columns(connection))
            stats.update(_ensure_sub_knowledge_columns(connection))
            stats.update(_ensure_knowledge_point_tables(connection))
            stats.update(_ensure_question_reference_tables(connection))
            stats.update(_ensure_question_order_schema(connection))
            stats["added_question_fingerprints"] = (
                _ensure_question_fingerprint_table(connection)
            )
            _validate_figure_layout_schema(connection)
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
    """Upgrade v6/v7 indexes, v8 text bands, and the v9 figure layout."""

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
            figure_layout_stats = _ensure_figure_layout_columns(connection)
            sub_knowledge_stats = _ensure_sub_knowledge_columns(connection)
            knowledge_point_stats = _ensure_knowledge_point_tables(connection)
            question_reference_stats = _ensure_question_reference_tables(connection)
            question_order_stats = _ensure_question_order_schema(connection)
            _validate_figure_layout_schema(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {
                "rebuilt_question_fingerprint_indexes": rebuilt_indexes,
                "added_question_fingerprint_text_columns": 8,
                "added_question_fingerprint_text_indexes": added_text_indexes,
                **figure_layout_stats,
                **sub_knowledge_stats,
                **knowledge_point_stats,
                **question_reference_stats,
                **question_order_stats,
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
            _validate_figure_layout_schema(connection)
            _validate_sub_knowledge_schema(connection)
            _validate_knowledge_point_schema(connection)
            _validate_question_reference_schema(connection)
            _validate_document_import_receipts(connection)
            _validate_question_order_schema(connection)
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
