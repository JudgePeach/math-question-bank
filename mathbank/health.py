"""Lightweight readiness checks used by launchers and release smoke tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from mathbank.db_migrations import LATEST_SCHEMA_VERSION
from mathbank.paths import DATA_BACKUP_DIR, SYSTEM_GENERATED_DIR, UPLOADS_DIR


def readiness_report(
    engine: Engine,
    *,
    required_directories: tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    errors: list[str] = []
    try:
        with engine.connect() as connection:
            checks["database"] = connection.exec_driver_sql("SELECT 1").scalar_one() == 1
            checks["schema_version"] = int(
                connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            )
            checks["foreign_keys"] = bool(
                connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            )
            checks["journal_mode"] = str(
                connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            ).lower()
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            checks["foreign_key_violations"] = len(violations)
    except Exception as exc:
        checks["database"] = False
        errors.append(f"database: {type(exc).__name__}")

    directories = required_directories or (
        UPLOADS_DIR,
        DATA_BACKUP_DIR,
        SYSTEM_GENERATED_DIR,
    )
    directory_checks = {}
    for directory in directories:
        directory_checks[directory.name] = {
            "exists": directory.is_dir(),
            "writable": directory.is_dir() and os.access(directory, os.W_OK),
        }
    checks["directories"] = directory_checks

    schema_ready = checks.get("schema_version") == LATEST_SCHEMA_VERSION
    database_name = engine.url.database
    persistent_sqlite = bool(
        engine.dialect.name == "sqlite"
        and database_name
        and database_name != ":memory:"
    )
    journal_ready = not persistent_sqlite or checks.get("journal_mode") == "wal"
    directories_ready = all(
        result["exists"] and result["writable"] for result in directory_checks.values()
    )
    ready = bool(
        checks.get("database")
        and checks.get("foreign_keys")
        and checks.get("foreign_key_violations") == 0
        and journal_ready
        and schema_ready
        and directories_ready
    )
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "checks": checks,
        "errors": errors,
    }
