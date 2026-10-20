"""Derived exports coalesce bursts and retain the final committed state."""

import threading

from mathbank.background_export import DatabaseExportQueue


def test_burst_exports_latest_state_once_and_shutdown_flushes():
    state = {"revision": 0}
    exported = []
    queue = DatabaseExportQueue(
        lambda: exported.append(state["revision"]),
        debounce_seconds=30, max_delay_seconds=30,
    )
    for revision in range(40):
        state["revision"] = revision
        queue.request(operation="save")
    queue.close()
    assert exported == [39]


def test_commit_during_export_runs_one_followup_snapshot():
    entered = threading.Event()
    release = threading.Event()
    exported = []
    state = {"revision": 1}

    def export():
        exported.append(state["revision"])
        if len(exported) == 1:
            entered.set()
            assert release.wait(5)

    queue = DatabaseExportQueue(export, debounce_seconds=0, max_delay_seconds=0)
    queue.request()
    assert entered.wait(5)
    try:
        for revision in range(2, 42):
            state["revision"] = revision
            queue.request()
        queue.close(wait=False)
    finally:
        release.set()
        queue.close()
    assert exported == [1, 41]


def test_failed_export_remains_retryable_on_next_commit():
    failed = threading.Event()
    errors = []
    attempts = []

    def export():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("output unavailable")

    def report(exc, operation):
        errors.append((type(exc), operation))
        failed.set()

    queue = DatabaseExportQueue(
        export, debounce_seconds=0, max_delay_seconds=0, on_error=report,
    )
    queue.request(operation="first-save")
    assert failed.wait(5)
    queue.request(operation="second-save")
    queue.close()
    assert attempts == [True, True]
    assert errors == [(OSError, "first-save")]


def test_failed_worker_start_does_not_lose_next_request(monkeypatch):
    from mathbank import background_export

    calls = []
    real_thread = threading.Thread

    class FailedThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    queue = DatabaseExportQueue(lambda: calls.append("latest"), debounce_seconds=30)
    monkeypatch.setattr(background_export.threading, "Thread", FailedThread)
    try:
        queue.request()
    except RuntimeError:
        pass
    else:
        raise AssertionError("start failure must be reported")
    monkeypatch.setattr(background_export.threading, "Thread", real_thread)
    queue.request()
    queue.close()
    assert calls == ["latest"]
