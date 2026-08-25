import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError

from mathbank.database import configure_sqlite_wal
from mathbank.db_migrations import LATEST_SCHEMA_VERSION
from mathbank.health import readiness_report


@pytest.fixture
def engine_factory():
    engines = []

    def create(url):
        engine = create_engine(url)
        engines.append(engine)
        return engine

    yield create
    for engine in engines:
        engine.dispose()


def test_readiness_report_checks_schema_foreign_keys_and_directories(
    tmp_path, engine_factory
):
    engine = engine_factory(f"sqlite:///{tmp_path / 'health.db'}")

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


def test_readiness_report_rejects_old_schema(tmp_path, engine_factory):
    engine = engine_factory(f"sqlite:///{tmp_path / 'old.db'}")
    required = (tmp_path / "uploads",)
    required[0].mkdir()

    report = readiness_report(engine, required_directories=required)

    assert report["status"] == "not_ready"
    assert report["ready"] is False


def test_readiness_report_accepts_delete_journal_fallback(
    tmp_path, engine_factory
):
    engine = engine_factory(f"sqlite:///{tmp_path / 'delete-journal.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
    required = (tmp_path / "uploads",)
    required[0].mkdir()

    report = readiness_report(engine, required_directories=required)

    assert report["ready"] is True
    assert report["checks"]["journal_mode"] == "delete"
    assert report["checks"]["journal_fallback"] is True


def test_readiness_report_rejects_unsupported_persistent_journal_mode(
    tmp_path, engine_factory
):
    engine = engine_factory(f"sqlite:///{tmp_path / 'memory-journal.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        assert connection.exec_driver_sql("PRAGMA journal_mode=MEMORY").scalar_one() == "memory"
    required = (tmp_path / "uploads",)
    required[0].mkdir()

    report = readiness_report(engine, required_directories=required)

    assert report["ready"] is False
    assert report["checks"]["journal_mode"] == "memory"


def test_configure_sqlite_wal_falls_back_to_delete(tmp_path):
    commands = []

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class Connection:
        def execution_options(self, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec_driver_sql(self, command):
            commands.append(command)
            if command == "PRAGMA journal_mode=WAL":
                return Result("delete")
            if command == "PRAGMA journal_mode=DELETE":
                return Result("delete")
            return Result(None)

    database_path = tmp_path / "fallback.db"
    database_path.touch()
    engine = SimpleNamespace(
        url=SimpleNamespace(database=str(database_path)),
        connect=lambda: Connection(),
    )

    assert configure_sqlite_wal(engine) == "delete"
    assert "PRAGMA journal_mode=DELETE" in commands
    assert "PRAGMA synchronous=FULL" in commands


def test_configure_sqlite_wal_falls_back_after_operational_error(tmp_path):
    class Result:
        def scalar_one(self):
            return "delete"

    class Connection:
        def execution_options(self, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec_driver_sql(self, command):
            if command == "PRAGMA journal_mode=WAL":
                raise OperationalError(command, {}, OSError("shared memory unavailable"))
            return Result()

    database_path = tmp_path / "operational-error.db"
    database_path.touch()
    engine = SimpleNamespace(
        url=SimpleNamespace(database=str(database_path)),
        connect=lambda: Connection(),
    )

    assert configure_sqlite_wal(engine) == "delete"


def test_configure_sqlite_wal_fails_when_delete_fallback_also_fails(tmp_path):
    class Connection:
        def execution_options(self, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec_driver_sql(self, command):
            raise OperationalError(command, {}, OSError("filesystem is read-only"))

    database_path = tmp_path / "no-journal.db"
    database_path.touch()
    engine = SimpleNamespace(
        url=SimpleNamespace(database=str(database_path)),
        connect=lambda: Connection(),
    )

    with pytest.raises(RuntimeError, match="降级至 DELETE 模式失败"):
        configure_sqlite_wal(engine)


def test_healthz_endpoint_is_lightweight_and_ready(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
