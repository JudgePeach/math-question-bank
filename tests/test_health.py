from sqlalchemy import create_engine, event

from mathbank.database import configure_sqlite_wal
from mathbank.db_migrations import LATEST_SCHEMA_VERSION
from mathbank.health import readiness_report


def test_readiness_report_checks_schema_foreign_keys_and_directories(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'health.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
    assert configure_sqlite_wal(engine) == "wal"
    required = (tmp_path / "uploads", tmp_path / "backups")
    for directory in required:
        directory.mkdir()

    report = readiness_report(engine, required_directories=required)

    assert report["status"] == "ready"
    assert report["checks"]["schema_version"] == LATEST_SCHEMA_VERSION
    assert report["checks"]["foreign_keys"] is True
    assert report["checks"]["journal_mode"] == "wal"


def test_readiness_report_rejects_old_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    required = (tmp_path / "uploads",)
    required[0].mkdir()

    report = readiness_report(engine, required_directories=required)

    assert report["status"] == "not_ready"
    assert report["ready"] is False


def test_readiness_report_rejects_non_wal_persistent_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'delete-journal.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
    required = (tmp_path / "uploads",)
    required[0].mkdir()

    report = readiness_report(engine, required_directories=required)

    assert report["ready"] is False
    assert report["checks"]["journal_mode"] == "delete"


def test_healthz_endpoint_is_lightweight_and_ready(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
