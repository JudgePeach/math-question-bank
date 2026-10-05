import datetime as dt
import threading
from types import SimpleNamespace

import pytest

import mathbank.task_manager as task_manager
from mathbank.task_manager import (
    TaskCancelled,
    TaskManager,
    TaskQueueFull,
)


@pytest.fixture
def task_clock(monkeypatch):
    class Clock(dt.datetime):
        current = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

        @classmethod
        def advance(cls, seconds):
            cls.current += dt.timedelta(seconds=seconds)

    monkeypatch.setattr(
        task_manager,
        "dt",
        SimpleNamespace(datetime=Clock, timezone=dt.timezone, timedelta=dt.timedelta),
    )
    return Clock


def test_cancelled_task_cannot_be_overwritten_as_complete():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", document_type="pdf")
    cancelled = manager.cancel("task-1")

    assert cancelled["status"] == "cancelled"
    assert manager.complete("task-1", data=[1, 2, 3]) is False
    assert manager.snapshot("task-1")["status"] == "cancelled"
    with pytest.raises(TaskCancelled):
        manager.check_cancelled("task-1")
    manager.shutdown()


def test_task_queue_is_bounded_and_releases_capacity():
    manager = TaskManager(max_workers=1, max_queue=0)
    started = threading.Event()
    release = threading.Event()

    def blocking_job():
        started.set()
        release.wait(timeout=2)

    manager.create("task-1")
    future = manager.submit("task-1", blocking_job)
    assert started.wait(timeout=1)
    manager.create("task-2")
    with pytest.raises(TaskQueueFull):
        manager.submit("task-2", lambda: None)

    release.set()
    future.result(timeout=2)
    second = manager.submit("task-2", lambda: "ok")
    assert second.result(timeout=2) == "ok"
    manager.shutdown()


def test_snapshot_is_isolated_from_internal_state():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", data={"items": [1]})

    snapshot = manager.snapshot("task-1")
    snapshot["data"]["items"].append(2)

    assert manager.snapshot("task-1")["data"] == {"items": [1]}
    manager.shutdown()


def test_terminal_state_rejects_late_updates_and_repeated_cancel():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", log="running")
    assert manager.complete("task-1", data=[1]) is True
    completed = manager.snapshot("task-1")

    assert manager.update("task-1", progress=25, log="late worker") is False
    assert manager.fail("task-1", "late error") is False
    cancelled = manager.cancel("task-1")
    assert cancelled == {
        key: value
        for key, value in completed.items()
        if key not in {"created_at", "updated_at"}
    }
    assert manager.snapshot("task-1") == completed
    manager.shutdown()


def test_duplicate_submit_is_rejected_without_leaking_queue_capacity():
    manager = TaskManager(max_workers=1, max_queue=1)
    started = threading.Event()
    release = threading.Event()

    def blocking_job():
        started.set()
        release.wait(timeout=2)

    manager.create("task-1")
    first = manager.submit("task-1", blocking_job)
    assert started.wait(timeout=1)
    with pytest.raises(ValueError, match="已提交"):
        manager.submit("task-1", lambda: None)

    manager.create("task-2")
    second = manager.submit("task-2", lambda: "queued")
    release.set()
    first.result(timeout=2)
    assert second.result(timeout=2) == "queued"
    assert manager.snapshot("task-1")["status"] == "completed"
    assert manager.snapshot("task-2")["status"] == "completed"
    manager.shutdown()


def test_unhandled_worker_error_becomes_terminal_and_releases_capacity():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1")

    def fail_job():
        raise LookupError("private detail")

    failed = manager.submit("task-1", fail_job)
    with pytest.raises(LookupError):
        failed.result(timeout=2)
    assert manager.snapshot("task-1")["status"] == "error"
    assert manager.snapshot("task-1")["error"].endswith("LookupError")

    manager.create("task-2")
    assert manager.submit("task-2", lambda: "ok").result(timeout=2) == "ok"
    manager.shutdown()


def test_temp_assets_are_deduplicated_concurrently_after_terminal_state():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", temp_assets=[])
    assert manager.complete("task-1") is True

    threads = [
        threading.Thread(
            target=manager.add_temp_asset,
            args=("task-1", f"/tmp/crop-{index % 5}.png"),
        )
        for index in range(100)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    snapshot = manager.snapshot("task-1")
    assert snapshot["status"] == "completed"
    assert len(snapshot["temp_assets"]) == 5
    assert set(snapshot["temp_assets"]) == {
        f"/tmp/crop-{index}.png" for index in range(5)
    }
    assert manager.add_temp_asset("missing", "/tmp/missing.png") is False
    manager.shutdown()


def test_terminal_eviction_cleans_temp_assets_for_ttl_and_record_capacity():
    cleaned = []
    manager = TaskManager(
        max_workers=1,
        max_queue=0,
        terminal_ttl_seconds=0,
        max_records=1,
        temp_asset_cleanup=lambda paths: cleaned.append(paths),
    )
    manager.create("ttl-task", temp_assets=["/tmp/a.png", "/tmp/a.png"])
    manager.complete("ttl-task")

    assert manager.cleanup() == 1
    assert cleaned == [["/tmp/a.png"]]
    manager.shutdown()

    capacity_manager = TaskManager(
        max_workers=1,
        max_queue=0,
        terminal_ttl_seconds=3600,
        max_records=1,
        temp_asset_cleanup=lambda paths: cleaned.append(paths),
    )
    capacity_manager.create("capacity-task", temp_assets=["/tmp/b.png"])
    capacity_manager.complete("capacity-task")
    capacity_manager.create("replacement-task")

    assert cleaned == [["/tmp/a.png"], ["/tmp/b.png"]]
    assert capacity_manager.snapshot("capacity-task") is None
    capacity_manager.shutdown()


def test_periodic_maintenance_cleans_expired_assets_without_new_tasks():
    cleaned = threading.Event()
    manager = TaskManager(
        max_workers=1,
        max_queue=0,
        terminal_ttl_seconds=0,
        max_records=1,
        temp_asset_cleanup=lambda _paths: cleaned.set(),
    )
    manager.create("idle-terminal", temp_assets=["/tmp/idle.png"])
    manager.complete("idle-terminal")

    manager.start_maintenance(interval_seconds=0.01)

    assert cleaned.wait(timeout=1)
    assert manager.snapshot("idle-terminal") is None
    manager.shutdown()


def test_retained_completed_import_preserves_images_until_activity_stops(task_clock, tmp_path):
    image = tmp_path / "unsaved-figure.png"
    image.write_bytes(b"temporary import image")
    cleaned = []

    def cleanup(paths):
        cleaned.extend(paths)
        image.unlink()

    manager = TaskManager(max_workers=1, max_queue=0, temp_asset_cleanup=cleanup)
    try:
        manager.create("paper", temp_assets=[str(image)])
        manager.complete("paper", data=[{"content": "question", "source_review": {"proof": "unchanged"}}])
        completed = manager.snapshot("paper")
        # A teacher continues proofreading well beyond the original one-hour TTL.
        for _minute in range(125):
            task_clock.advance(60)
            assert manager.retain("paper") is True
            assert manager.cleanup() == 0
            assert image.is_file()
            assert manager.snapshot("paper") == completed
        assert cleaned == []
        task_clock.advance(3599)
        assert manager.cleanup() == 0
        task_clock.advance(1)
        assert manager.cleanup() == 1
        assert not image.exists()
        assert manager.snapshot("paper") is None
        assert manager.retain("paper") is False
        assert cleaned == [str(image)]
    finally:
        manager.shutdown()


def test_retention_uses_completion_when_later_than_active_heartbeat(task_clock):
    manager = TaskManager(max_workers=1, max_queue=0)
    try:
        manager.create("paper", progress=25)
        active = manager.snapshot("paper")
        assert manager.retain("paper") is True
        assert manager.snapshot("paper") == active
        task_clock.advance(3601)
        assert manager.cleanup() == 0  # Live jobs never expire as terminal results.
        manager.complete("paper", data=[1])
        task_clock.advance(3599)
        assert manager.cleanup() == 0
        task_clock.advance(1)
        assert manager.cleanup() == 1
    finally:
        manager.shutdown()


@pytest.mark.parametrize("terminal", ["cancelled", "error"])
def test_retention_cannot_revive_cancelled_or_failed_tasks(task_clock, terminal):
    manager = TaskManager(max_workers=1, max_queue=0)
    try:
        manager.create("paper")
        assert manager.retain("paper") is True
        if terminal == "cancelled":
            manager.cancel("paper")
        else:
            manager.fail("paper", "failed")
        ended = manager.snapshot("paper")
        task_clock.advance(3599)
        assert manager.retain("paper") is False
        assert manager.snapshot("paper") == ended
        task_clock.advance(1)
        assert manager.cleanup() == 1
        assert manager.retain("paper") is False
    finally:
        manager.shutdown()


def test_record_capacity_keeps_retained_results_and_evicts_inactive_ones(task_clock):
    cleaned = []
    manager = TaskManager(
        max_workers=1, max_queue=0, max_records=2,
        temp_asset_cleanup=lambda paths: cleaned.extend(paths),
    )
    try:
        manager.create("active", temp_assets=["active.png"])
        manager.complete("active")
        assert manager.retain("active") is True
        manager.create("inactive", temp_assets=["inactive.png"])
        manager.complete("inactive")
        manager.create("replacement")
        assert manager.snapshot("active") is not None
        assert manager.snapshot("inactive") is None
        assert cleaned == ["inactive.png"]
        manager.complete("replacement")
        assert manager.retain("replacement") is True
        with pytest.raises(TaskQueueFull):
            manager.create("must-wait")
        assert manager.snapshot("active") is not None
        assert manager.snapshot("replacement") is not None
        assert manager.snapshot("must-wait") is None
        assert cleaned == ["inactive.png"]
        task_clock.advance(3600)
        manager.create("after-expiry")
        assert manager.snapshot("after-expiry") is not None
        assert cleaned == ["inactive.png", "active.png"]
    finally:
        manager.shutdown()


def test_concurrent_retention_and_cleanup_keep_terminal_evidence(task_clock):
    cleaned = []
    manager = TaskManager(
        max_workers=1, max_queue=0,
        temp_asset_cleanup=lambda paths: cleaned.extend(paths),
    )
    try:
        manager.create("paper", temp_assets=["image.png"])
        manager.complete("paper", data=[{"source_review": {"proof": "unchanged"}}])
        completed = manager.snapshot("paper")
        task_clock.advance(3599)
        barrier = threading.Barrier(25)
        failures = []

        def worker(index):
            try:
                barrier.wait(timeout=2)
                if index % 2:
                    assert manager.retain("paper") is True
                else:
                    assert manager.cleanup() == 0
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(25)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        assert not any(thread.is_alive() for thread in threads)
        assert failures == []
        task_clock.advance(1)
        assert manager.cleanup() == 0
        assert manager.snapshot("paper") == completed
        assert cleaned == []
    finally:
        manager.shutdown()
