from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from scripts import windows_launcher as launcher
from mathbank import GITHUB_REPO


def _server_state(
    root: str,
    *,
    pid: int = 4312,
    creation_ticks: int = 638_999_000_000_000_000,
) -> launcher.ServerState:
    executable = launcher._expected_executables(root)[0]
    return launcher.ServerState(
        schema=launcher.STATE_SCHEMA,
        pid=pid,
        creation_ticks=creation_ticks,
        root=root,
        executable=executable,
        command=(
            executable,
            "-u",
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ),
        launch_id="0123456789abcdef0123456789abcdef",
    )


def test_pre_overlay_launcher_constants_match_application_sources():
    assert launcher.PROJECT_ROOT == Path(__file__).resolve().parents[1]
    assert launcher.EXPECTED_REPOSITORY == GITHUB_REPO


def test_windows_system_tools_ignore_project_and_path_decoys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    system_root = tmp_path / "Windows"
    powershell = (
        system_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    netstat = system_root / "System32" / "netstat.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"system")
    netstat.write_bytes(b"system")
    (tmp_path / "powershell.exe").write_bytes(b"decoy")
    (tmp_path / "netstat.exe").write_bytes(b"decoy")
    monkeypatch.setenv("SystemRoot", str(system_root))
    monkeypatch.setenv("PATH", str(tmp_path))

    assert launcher._windows_system_tool(
        "WindowsPowerShell", "v1.0", "powershell.exe"
    ) == str(powershell)
    assert launcher._windows_system_tool("netstat.exe") == str(netstat)


def _loaded(state: launcher.ServerState) -> launcher.LoadedState:
    return launcher.LoadedState(
        kind="json",
        pid=state.pid,
        root=state.root,
        record=state,
        recorded_at_ticks=None,
    )


def _process(
    state: launcher.ServerState,
    *,
    creation_ticks: int | None = None,
    executable: str | None = None,
    command_line: str | None = None,
) -> launcher.ProcessInfo:
    return launcher.ProcessInfo(
        pid=state.pid,
        creation_ticks=(
            state.creation_ticks if creation_ticks is None else creation_ticks
        ),
        executable=state.executable if executable is None else executable,
        command_line=(
            subprocess.list2cmdline(state.command)
            if command_line is None
            else command_line
        ),
    )


def test_classify_dead_pid_as_stale_without_inspecting_a_real_process():
    root = r"C:\MathBank"
    state = _server_state(root)

    assert (
        launcher.classify_state(_loaded(state), None, root)
        == launcher.DECISION_STALE_DEAD
    )


def test_classify_reused_pid_by_creation_date_before_other_identity_fields():
    root = r"C:\MathBank"
    state = _server_state(root)
    reused = _process(state, creation_ticks=state.creation_ticks + 1)

    assert (
        launcher.classify_state(_loaded(state), reused, root)
        == launcher.DECISION_STALE_REUSED
    )


def test_classify_exact_pid_instance_and_identity_as_owned():
    root = r"C:\MathBank"
    state = _server_state(root)

    assert (
        launcher.classify_state(_loaded(state), _process(state), root)
        == launcher.DECISION_OWNED
    )


@pytest.mark.parametrize(
    "mutate_state,mutate_process",
    [
        (
            lambda state: dataclasses.replace(state, root=r"C:\OtherMathBank"),
            lambda state: _process(state),
        ),
        (
            lambda state: state,
            lambda state: _process(
                state, executable=r"C:\Unrelated\python\python.exe"
            ),
        ),
        (
            lambda state: state,
            lambda state: _process(
                state, command_line=r'"C:\Windows\py.exe" -m http.server 8000'
            ),
        ),
        (
            lambda state: state,
            lambda state: _process(
                state,
                command_line=(
                    subprocess.list2cmdline((state.executable, "-X", "dev"))
                    + " "
                    + " ".join(launcher.EXPECTED_UVICORN_ARGS)
                ),
            ),
        ),
        (
            lambda state: dataclasses.replace(
                state,
                command=(state.executable, "-m", "http.server", "8000"),
            ),
            lambda state: _process(state),
        ),
    ],
    ids=(
        "saved-root",
        "live-executable",
        "live-command",
        "live-command-prefix",
        "saved-command",
    ),
)
def test_same_creation_date_but_identity_mismatch_is_unknown_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate_state, mutate_process
):
    paths = launcher.launcher_paths(tmp_path)
    baseline = _server_state(str(paths.root))
    state = mutate_state(baseline)
    process = mutate_process(baseline)
    launcher.write_server_state(paths, state)
    dangerous_calls: list[str] = []

    monkeypatch.setattr(launcher, "query_process", lambda _pid: process)

    def unexpected_stop(*_args, **_kwargs):
        dangerous_calls.append("stop")
        raise AssertionError("unknown state must never reach a stop path")

    monkeypatch.setattr(launcher, "_stop_owned_process", unexpected_stop)
    monkeypatch.setattr(launcher, "_request_shutdown", unexpected_stop)
    monkeypatch.setattr(launcher, "listener_pids", unexpected_stop)

    loaded = launcher.read_server_state(paths)
    assert loaded is not None
    assert (
        launcher.classify_state(loaded, process, paths.root)
        == launcher.DECISION_UNKNOWN
    )
    with pytest.raises(launcher.LauncherError) as error:
        launcher.handle_previous_state(paths)

    assert error.value.code == "E_STATE_UNKNOWN"
    assert dangerous_calls == []
    assert paths.state.is_file()
    assert not list(paths.state_dir.glob("stale-launcher-state-*.json"))


def test_loaded_state_with_missing_identity_fields_is_unknown():
    root = r"C:\MathBank"
    state = _server_state(root)
    incomplete = launcher.LoadedState(
        kind="json",
        pid=state.pid,
        root=None,
        record=None,
        recorded_at_ticks=None,
    )

    assert (
        launcher.classify_state(incomplete, _process(state), root)
        == launcher.DECISION_UNKNOWN
    )


def test_atomic_json_state_round_trip_takes_precedence_over_legacy_mirrors(
    tmp_path: Path,
):
    paths = launcher.launcher_paths(tmp_path / "中文 MathBank")
    state = _server_state(str(paths.root))

    launcher.write_server_state(paths, state)
    loaded = launcher.read_server_state(paths)

    assert loaded is not None
    assert loaded.kind == "json"
    assert loaded.record == state
    assert json.loads(paths.state.read_text(encoding="utf-8")) == state.to_mapping()
    assert paths.legacy_pid.read_text(encoding="ascii") == f"{state.pid}\n"
    assert paths.legacy_identity.read_text(encoding="utf-8") == state.root + "\n"
    assert not list(paths.state_dir.glob(f".{launcher.STATE_NAME}.*.tmp"))


@pytest.mark.parametrize(
    "payload,expected_code",
    [
        ("{not json", "E_STATE_INVALID"),
        (
            json.dumps(
                {
                    "schema": launcher.STATE_SCHEMA,
                    "pid": 123,
                    "creation_ticks": 638_999_000_000_000_000,
                    "root": r"C:\MathBank",
                    "executable": r"C:\MathBank\python\python.exe",
                    "command": ["python.exe", "-m", "uvicorn", "main:app"],
                    # launch_id deliberately omitted: partial state is unsafe.
                }
            ),
            "E_STATE_INVALID",
        ),
    ],
    ids=("invalid-json", "missing-field"),
)
def test_corrupt_or_partial_atomic_state_is_rejected(
    tmp_path: Path, payload: str, expected_code: str
):
    paths = launcher.launcher_paths(tmp_path)
    paths.state_dir.mkdir(parents=True)
    paths.state.write_text(payload, encoding="utf-8")

    with pytest.raises(launcher.LauncherError) as error:
        launcher.read_server_state(paths)

    assert error.value.code == expected_code


def test_legacy_pid_and_identity_are_loaded_without_touching_processes(tmp_path: Path):
    paths = launcher.launcher_paths(tmp_path)
    paths.state_dir.mkdir(parents=True)
    paths.legacy_pid.write_text("7312\n", encoding="ascii")
    paths.legacy_identity.write_text("\ufeffC:\\MathBank\n", encoding="utf-8")

    loaded = launcher.read_server_state(paths)

    assert loaded is not None
    assert loaded.kind == "legacy"
    assert loaded.pid == 7312
    assert loaded.root == r"C:\MathBank"
    assert loaded.record is None
    assert isinstance(loaded.recorded_at_ticks, int)
    assert loaded.recorded_at_ticks > launcher.WINDOWS_EPOCH_TICKS


def test_matching_legacy_live_process_without_creation_date_fails_closed():
    root = r"C:\MathBank"
    state = _server_state(root)
    loaded = launcher.LoadedState(
        kind="legacy",
        pid=state.pid,
        root=root,
        record=None,
        recorded_at_ticks=state.creation_ticks + 50,
    )

    assert (
        launcher.classify_state(loaded, _process(state), root)
        == launcher.DECISION_UNKNOWN
    )


def test_unrelated_legacy_live_process_without_creation_date_also_fails_closed():
    root = r"C:\MathBank"
    state = _server_state(root)
    loaded = launcher.LoadedState(
        kind="legacy",
        pid=state.pid,
        root=root,
        record=None,
        recorded_at_ticks=state.creation_ticks + 50,
    )
    unrelated = _process(
        state,
        executable=r"C:\Windows\System32\notepad.exe",
        command_line=r"C:\Windows\System32\notepad.exe",
    )

    assert (
        launcher.classify_state(loaded, unrelated, root)
        == launcher.DECISION_UNKNOWN
    )


@pytest.mark.parametrize("process_exists", [False, True], ids=("dead", "reused"))
def test_proven_stale_state_is_quarantined_without_any_stop_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_exists: bool
):
    paths = launcher.launcher_paths(tmp_path)
    state = _server_state(str(paths.root))
    launcher.write_server_state(paths, state)
    current = (
        _process(state, creation_ticks=state.creation_ticks + 10)
        if process_exists
        else None
    )
    dangerous_calls: list[str] = []

    monkeypatch.setattr(launcher, "query_process", lambda _pid: current)

    def unexpected_stop(*_args, **_kwargs):
        dangerous_calls.append("stop")
        raise AssertionError("stale state must be quarantined without stopping a process")

    monkeypatch.setattr(launcher, "_stop_owned_process", unexpected_stop)
    monkeypatch.setattr(launcher, "_request_shutdown", unexpected_stop)
    monkeypatch.setattr(launcher, "listener_pids", unexpected_stop)

    decision = launcher.handle_previous_state(paths)

    expected = (
        launcher.DECISION_STALE_REUSED
        if process_exists
        else launcher.DECISION_STALE_DEAD
    )
    assert decision == expected
    assert dangerous_calls == []
    assert not paths.state.exists()
    assert not paths.legacy_pid.exists()
    assert not paths.legacy_identity.exists()
    quarantined = list(paths.state_dir.glob("stale-launcher-state-*.json"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text(encoding="utf-8"))["pid"] == state.pid


def test_netstat_parser_matches_exact_port_8000_not_18000():
    output = "\n".join(
        (
            "  Proto  Local Address          Foreign Address        State           PID",
            "  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       101",
            "  TCP    127.0.0.1:18000        0.0.0.0:0              LISTENING       202",
            "  TCP    [::]:8000              [::]:0                 LISTENING       303",
            "  TCP    127.0.0.1:8000         127.0.0.1:52000        ESTABLISHED     404",
            "  UDP    0.0.0.0:8000           *:*                                    505",
        )
    )

    assert launcher.parse_netstat_listeners(output) == {101, 303}
    assert launcher.parse_netstat_listeners(output, port=18000) == {202}


def test_authoritative_state_is_cleared_after_compatibility_mirrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = launcher.launcher_paths(tmp_path)
    launcher.write_server_state(paths, _server_state(str(paths.root)))
    calls: list[Path] = []
    original = launcher._safe_unlink

    def record(path: Path):
        calls.append(path)
        original(path)

    monkeypatch.setattr(launcher, "_safe_unlink", record)
    launcher.clear_server_state(paths)

    assert calls == [paths.legacy_pid, paths.legacy_identity, paths.state]


def test_authoritative_state_is_quarantined_after_compatibility_mirrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = launcher.launcher_paths(tmp_path)
    state = _server_state(str(paths.root))
    launcher.write_server_state(paths, state)
    loaded = launcher.read_server_state(paths)
    assert loaded is not None
    calls: list[Path] = []
    original = launcher.os.replace

    def record(source, target):
        calls.append(Path(source))
        original(source, target)

    monkeypatch.setattr(launcher.os, "replace", record)
    launcher.quarantine_server_state(paths, loaded, "test")

    assert calls[-1] == paths.state


def test_shutdown_request_binds_the_expected_launch_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = launcher.launcher_paths(tmp_path)
    paths.state_dir.mkdir(parents=True)
    paths.local_token.write_text("token-value", encoding="utf-8")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Opener:
        def open(self, request, timeout):
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(launcher, "_no_proxy_opener", lambda: Opener())
    launcher._request_shutdown(paths, expected_launch_id="launch-123")

    assert captured["headers"]["X-local-token"] == "token-value"
    assert captured["headers"]["X-mathbank-launch-id"] == "launch-123"
    assert captured["timeout"] == 2.0


def test_health_response_from_another_launch_id_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = launcher.launcher_paths(tmp_path)
    state = _server_state(str(paths.root), pid=9912)
    process = _process(state)

    class Child:
        pid = state.pid

        @staticmethod
        def poll():
            return None

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read(_limit):
            return json.dumps({"server_instance_id": "other"}).encode("utf-8")

    class Opener:
        @staticmethod
        def open(_url, timeout):
            assert timeout == 2.0
            return Response()

    monkeypatch.setattr(launcher, "query_process", lambda _pid: process)
    monkeypatch.setattr(launcher, "listener_pids", lambda _port=8000: {state.pid})
    monkeypatch.setattr(launcher, "_no_proxy_opener", lambda: Opener())

    ready, detail = launcher.wait_until_ready(paths, Child(), state, timeout=1.0)

    assert ready is False
    assert detail == "健康响应来自其他 MathBank 实例。"
