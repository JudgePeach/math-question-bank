"""Port changes use disposable roots, synthetic HTTP replies, and no app imports."""
import contextlib
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from mathbank.paths import project_instance_id
from scripts import local_launcher as launcher


@pytest.fixture(autouse=True)
def isolated_address(tmp_path, monkeypatch):
    monkeypatch.delenv("MATHBANK_PORT", raising=False)
    monkeypatch.setattr(launcher, "ACTIVE_ROOT", tmp_path)
    monkeypatch.setattr(launcher, "PORT", 8000)
    monkeypatch.setattr(launcher, "BASE_URL", "http://127.0.0.1:8000")


@contextlib.contextmanager
def synthetic_server(responses):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            payload = responses.get(self.path)
            if isinstance(payload, tuple):
                self.send_response(payload[0])
                self.send_header("Location", payload[1])
                self.end_headers()
                return
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server.server_port, requests
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


def service_responses(root, *, legacy=False, token="synthetic-token-12345678"):
    identity = {} if legacy else {"project_instance_id": project_instance_id(root)}
    return {
        "/api/version": {"repo": launcher.REPOSITORY, "server_instance_id": "a" * 32, **identity},
        "/healthz": {"ready": True, "server_instance_id": "a" * 32, **identity},
        "/": f'<head><script>window.__localToken = "{token}";</script></head>'.encode(),
    }


def settings(root, value):
    state = root / ".system_generated"
    state.mkdir(exist_ok=True)
    path = state / "launcher-settings.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def mock_startup(monkeypatch):
    started, opened = [], []
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: False)
    monkeypatch.setattr(launcher, "prepare_python", lambda *_: (Path(sys.executable), None))
    monkeypatch.setattr(launcher, "ensure_dependencies", lambda *_: None)
    monkeypatch.setattr(launcher, "open_browser", lambda: opened.append(launcher.BASE_URL))

    def start(root, _python):
        started.append(launcher.PORT)
        launcher.remember_port(root, launcher.PORT)
        launcher.open_browser()

    monkeypatch.setattr(launcher, "start_server", start)
    return started, opened


def test_same_directory_real_http_reuses_port_without_environment_checks(tmp_path, monkeypatch):
    with synthetic_server(service_responses(tmp_path)) as (port, requests):
        settings(tmp_path, {"port": port})
        opened = []
        monkeypatch.setattr(launcher, "open_browser", lambda: opened.append(launcher.BASE_URL))
        monkeypatch.setattr(launcher, "prepare_python", lambda *_: pytest.fail("environment touched"))
        monkeypatch.setattr(launcher, "_check_startup_storage", lambda *_: pytest.fail("write probe"))
        launcher.run_launcher(tmp_path)
        launcher.run_launcher(tmp_path)
        assert opened == [f"http://127.0.0.1:{port}"] * 2
        assert [path for path, _ in requests] == ["/api/version", "/healthz"] * 2
        assert not any("X-Local-Token" in headers for _, headers in requests)


def test_other_directory_mathbank_is_not_reused(tmp_path):
    other = tmp_path / "other-library"
    other.mkdir()
    with synthetic_server(service_responses(other)) as (port, requests):
        launcher._set_port(port)
        assert not launcher.existing_service_ready()
        assert [path for path, _ in requests] == ["/api/version"]


def test_version_and_health_must_describe_same_instance(tmp_path, monkeypatch):
    responses = service_responses(tmp_path)
    responses["/healthz"]["server_instance_id"] = "replaced-between-requests"
    monkeypatch.setattr(launcher, "read_json", lambda path: responses[path])
    assert not launcher.existing_service_ready()


def test_legacy_running_service_requires_held_project_lock_and_matching_token(tmp_path):
    state = tmp_path / ".system_generated"
    state.mkdir()
    token = "synthetic-token-12345678"
    (state / "local_token").write_text(token)
    (state / "server-state.json").write_text(json.dumps({"pid": 1, "launch_id": "a" * 32}))
    with synthetic_server(service_responses(tmp_path, legacy=True, token=token)) as (port, requests):
        launcher._set_port(port)
        assert not launcher.existing_service_ready()
        with launcher.launcher_lock(state, filename="runtime.lock"):
            assert launcher.existing_service_ready()
        assert not any("X-Local-Token" in headers for _, headers in requests)
        assert token not in str(requests)
        assert (state / "local_token").read_text() == token


def test_legacy_copied_token_cannot_replace_launch_instance_evidence(tmp_path):
    state = tmp_path / ".system_generated"
    state.mkdir()
    (state / "local_token").write_text("synthetic-token-12345678")
    record = state / "server-state.json"
    record.write_text(json.dumps({"pid": 1, "launch_id": "b" * 32}))
    with synthetic_server(service_responses(tmp_path, legacy=True)) as (port, requests):
        launcher._set_port(port)
        with launcher.launcher_lock(state, filename="runtime.lock"):
            assert not launcher.existing_service_ready()
            record.write_text("broken stale state")
            assert not launcher.existing_service_ready()
        assert all(path == "/api/version" for path, _ in requests)


def test_unverified_old_service_and_held_runtime_lock_never_start_second_copy(tmp_path, monkeypatch):
    state = tmp_path / ".system_generated"
    state.mkdir()
    (state / "local_token").write_text("our-synthetic-token-123456")
    with synthetic_server(service_responses(tmp_path, legacy=True, token="other-synthetic-token-123456")) as (port, _):
        monkeypatch.setenv("MATHBANK_PORT", str(port))
        monkeypatch.setattr(launcher, "start_server", lambda *_: pytest.fail("second server started"))
        monkeypatch.setattr(launcher, "confirm_port_change", lambda *_: pytest.fail("offered a different port"))
        with launcher.launcher_lock(state, filename="runtime.lock"):
            with pytest.raises(launcher.LauncherError, match="未启动第二份"):
                launcher.run_launcher(tmp_path)


def test_unverified_legacy_without_runtime_guard_is_not_treated_as_another_app(tmp_path, monkeypatch):
    # Very old packages may not hold today's runtime lock. Their presence
    # must still block a second instance rather than offer another address.
    with synthetic_server(service_responses(tmp_path, legacy=True)) as (port, _):
        monkeypatch.setenv("MATHBANK_PORT", str(port))
        monkeypatch.setattr(launcher, "start_server", lambda *_: pytest.fail("started second copy"))
        monkeypatch.setattr(launcher, "confirm_port_change", lambda *_: pytest.fail("offered port change"))
        with pytest.raises(launcher.LauncherError, match="无法确认归属，未启动第二份"):
            launcher.run_launcher(tmp_path)


def test_same_project_still_starting_blocks_port_change_without_a_lock_file(tmp_path, monkeypatch):
    replies = service_responses(tmp_path)
    replies["/healthz"]["ready"] = False
    with synthetic_server(replies) as (port, _):
        monkeypatch.setenv("MATHBANK_PORT", str(port))
        monkeypatch.setattr(launcher, "start_server", lambda *_: pytest.fail("started second copy"))
        with pytest.raises(launcher.LauncherError, match="尚未就绪"):
            launcher.run_launcher(tmp_path)


def test_non_ascii_legacy_response_token_does_not_crash_or_match(tmp_path, monkeypatch):
    state = tmp_path / ".system_generated"
    state.mkdir()
    (state / "local_token").write_text("a" * 32)
    (state / "server-state.json").write_text(json.dumps({"launch_id": "a" * 32}))
    monkeypatch.setattr(launcher, "runtime_is_busy", lambda *_: True)
    monkeypatch.setattr(launcher, "read_local", lambda *_: ('window.__localToken="' + "é" * 16 + '";').encode())
    assert not launcher._legacy_service_matches({"server_instance_id": "a" * 32})


def test_real_socket_detects_bound_port_and_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        launcher._set_port(occupied.getsockname()[1])
        assert launcher.port_in_use()
    assert not launcher.port_in_use()


@pytest.mark.parametrize("platform,exclusive", [("posix", True), ("nt", True), ("nt", False)])
def test_bind_probe_matches_platform_restart_and_exclusive_rules(monkeypatch, platform, exclusive):
    options = []

    class Probe:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def setsockopt(self, *args): options.append(args)
        def bind(self, address): assert address == ("127.0.0.1", 8000)

    constants = dict(AF_INET=2, SOCK_STREAM=1, SOL_SOCKET=1, SO_REUSEADDR=2,
                     socket=lambda *_args: Probe())
    if exclusive:
        constants["SO_EXCLUSIVEADDRUSE"] = 4
    monkeypatch.setattr(launcher, "socket", SimpleNamespace(**constants))
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name=platform))
    assert not launcher.port_in_use()
    assert options == ([(1, 2, 1)] if platform == "posix" else [(1, 4, 1)] if exclusive else [])


@pytest.mark.parametrize("value", [{}, [], {"port": True}, {"port": 0}, {"port": 65536}, {"port": "8001"}])
def test_bad_remembered_port_is_optional_and_falls_back(tmp_path, value):
    path = settings(tmp_path, value)
    original = path.read_bytes()
    assert launcher.configured_port(tmp_path) == (8000, False)
    assert path.read_bytes() == original


@pytest.mark.parametrize("raw", [b"broken", b"\xff", b"x" * 9000])
def test_corrupt_settings_do_not_block_available_environment(tmp_path, raw):
    path = settings(tmp_path, {})
    path.write_bytes(raw)
    assert launcher.configured_port(tmp_path) == (8000, False)
    assert path.read_bytes() == raw


def test_explicit_port_overrides_memory_and_remains_fixed(tmp_path, monkeypatch):
    settings(tmp_path, {"port": 8001})
    monkeypatch.setenv("MATHBANK_PORT", "9001")
    assert launcher.configured_port(tmp_path) == (9001, True)
    assert launcher.candidate_ports(9001, True) == [9001]


@pytest.mark.parametrize("value", ["", "0", "65536", "8001.0", "８００１", "1" * 5000])
def test_invalid_explicit_port_reports_setting_name(tmp_path, monkeypatch, value):
    monkeypatch.setenv("MATHBANK_PORT", value)
    with pytest.raises(launcher.LauncherError, match="MATHBANK_PORT"):
        launcher.configured_port(tmp_path)


def test_free_default_starts_without_prompt_and_remembered_port_is_sticky(tmp_path, monkeypatch):
    started, opened = mock_startup(monkeypatch)
    monkeypatch.setattr(launcher, "port_in_use", lambda: False)
    monkeypatch.setattr(launcher, "confirm_port_change", lambda *_: pytest.fail("unneeded prompt"))
    launcher.run_launcher(tmp_path)
    assert started == [8000]
    assert opened == ["http://127.0.0.1:8000"]
    settings(tmp_path, {"port": 8002})
    launcher.run_launcher(tmp_path)
    assert started == [8000, 8002]
    assert launcher.configured_port(tmp_path) == (8002, False)


def test_missing_memory_finds_existing_nondefault_service_before_using_free_default(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: launcher.PORT == 8003)
    monkeypatch.setattr(launcher, "prepare_python", lambda *_: pytest.fail("created a second environment"))
    monkeypatch.setattr(launcher, "confirm_port_change", lambda *_: pytest.fail("existing service needs no prompt"))
    opened = []
    monkeypatch.setattr(launcher, "open_browser", lambda: opened.append(launcher.BASE_URL))
    launcher.run_launcher(tmp_path)
    assert opened == ["http://127.0.0.1:8003"]
    assert launcher.configured_port(tmp_path) == (8003, False)


def test_exhausted_port_candidates_stop_without_prompt_or_start(tmp_path, monkeypatch):
    started, _ = mock_startup(monkeypatch)
    monkeypatch.setattr(launcher, "port_in_use", lambda: True)
    monkeypatch.setattr(launcher, "confirm_port_change", lambda *_: pytest.fail("no free port to offer"))
    with pytest.raises(launcher.LauncherError, match="候选端口已用尽"):
        launcher.run_launcher(tmp_path)
    assert started == []
    assert not (tmp_path / ".system_generated/launcher-settings.json").exists()


def test_occupied_default_requires_yes_then_remembers_confirmed_port(tmp_path, monkeypatch, capsys):
    started, opened = mock_startup(monkeypatch)
    monkeypatch.setattr(launcher, "port_in_use", lambda: launcher.PORT in {8000, 8001})
    monkeypatch.setattr(launcher.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    launcher.run_launcher(tmp_path)
    assert started == [8002]
    assert opened == ["http://127.0.0.1:8002"]
    assert launcher.configured_port(tmp_path) == (8002, False)
    output = capsys.readouterr().out
    assert "http://127.0.0.1:8000" in output
    assert "草稿、选题篮" in output and "不会自动搬迁" in output


@pytest.mark.parametrize("interactive,answer", [(True, "n"), (True, ""), (False, "y")])
def test_no_confirmation_does_not_start_or_change_memory(tmp_path, monkeypatch, interactive, answer):
    started, _ = mock_startup(monkeypatch)
    monkeypatch.setattr(launcher, "port_in_use", lambda: launcher.PORT == 8000)
    monkeypatch.setattr(launcher.sys, "stdin", SimpleNamespace(isatty=lambda: interactive))
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    with pytest.raises(launcher.LauncherError):
        launcher.run_launcher(tmp_path)
    assert started == []
    assert not (tmp_path / ".system_generated/launcher-settings.json").exists()


def test_busy_runtime_without_http_response_blocks_prompt_and_second_launch(tmp_path, monkeypatch):
    state = tmp_path / ".system_generated"
    state.mkdir()
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: False)
    monkeypatch.setattr(launcher, "confirm_port_change", lambda *_: pytest.fail("changed port"))
    monkeypatch.setattr(launcher, "start_server", lambda *_: pytest.fail("started duplicate"))
    with launcher.launcher_lock(state, filename="runtime.lock"):
        with pytest.raises(launcher.LauncherError, match="未启动第二份"):
            launcher.run_launcher(tmp_path)
    assert not launcher.runtime_is_busy(tmp_path)


def test_confirmed_bind_race_can_prompt_again_but_generic_failure_does_not(tmp_path, monkeypatch):
    mock_startup(monkeypatch)
    monkeypatch.setattr(launcher, "port_in_use", lambda: False)
    confirmed, started = [], []
    monkeypatch.setattr(launcher, "confirm_port_change", lambda old, new: confirmed.append((old, new)))

    def race(_root, _python):
        started.append(launcher.PORT)
        if len(started) == 1:
            raise launcher.PortBusyError("confirmed exited child's bind race")

    monkeypatch.setattr(launcher, "start_server", race)
    launcher.run_launcher(tmp_path)
    assert started == [8000, 8001]
    assert confirmed == [(8000, 8001)]

    def failed(_root, _python):
        started.append(launcher.PORT)
        raise launcher.LauncherError("still starting or failed for unrelated reason")

    monkeypatch.setattr(launcher, "start_server", failed)
    with pytest.raises(launcher.LauncherError):
        launcher.run_launcher(tmp_path)
    assert started == [8000, 8001, 8000]
    assert confirmed == [(8000, 8001)]


@pytest.mark.parametrize("code,bind_error,is_busy", [(1, True, True), (None, True, False), (0, True, False), (1, False, False)])
def test_retryable_start_failure_requires_exited_child_and_bind_error(tmp_path, monkeypatch, code, bind_error, is_busy):
    state = tmp_path / ".system_generated"
    state.mkdir()
    child = SimpleNamespace(pid=12345, poll=lambda: code)

    def spawn(_command, **kwargs):
        kwargs["stdout"].write(b"[Errno 98] error while attempting to bind on address: address already in use\n"
                                if bind_error else b"ImportError: synthetic-missing-package\n")
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "wait_until_ready", lambda *_: (False, "synthetic failure"))
    monkeypatch.setattr(launcher, "open_browser", lambda: pytest.fail("opened failed server"))
    with pytest.raises(launcher.LauncherError) as failure:
        launcher.start_server(tmp_path, Path(sys.executable))
    assert isinstance(failure.value, launcher.PortBusyError) is is_busy
    assert not (state / "launcher-settings.json").exists()


def test_ready_child_uses_one_port_for_launch_probe_memory_and_browser(tmp_path, monkeypatch):
    (tmp_path / ".system_generated").mkdir()
    launcher._set_port(9001)
    commands, probed, opened = [], [], []

    def spawn(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert command[-2:] == ["--port", "9001"]
        assert len(kwargs["env"]["MATHBANK_LAUNCH_ID"]) == 32
        commands.append(command)
        return SimpleNamespace(pid=12345)

    def ready(_child, launch_id):
        assert len(launch_id) == 32
        probed.append(launcher.BASE_URL)
        return True, "ready"

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "wait_until_ready", ready)
    monkeypatch.setattr(launcher, "open_browser", lambda: opened.append(launcher.BASE_URL))
    launcher.start_server(tmp_path, Path(sys.executable))
    assert len(commands) == 1
    assert probed == opened == ["http://127.0.0.1:9001"]
    assert launcher.configured_port(tmp_path) == (9001, False)


def test_memory_write_failure_preserves_old_cache_and_ready_server(tmp_path, monkeypatch, capsys):
    path = settings(tmp_path, {"port": 8001})
    monkeypatch.setattr(launcher.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("synthetic")))
    launcher.remember_port(tmp_path, 8002)
    assert json.loads(path.read_text()) == {"port": 8001}
    assert list(path.parent.glob("*.tmp")) == []
    assert "题库已可用" in capsys.readouterr().out


def test_redirected_probe_never_follows_to_another_service():
    with synthetic_server({"/destination": {"ready": True}}) as (other, requests):
        with synthetic_server({"/api/version": (302, f"http://127.0.0.1:{other}/destination")}) as (port, _):
            launcher._set_port(port)
            with pytest.raises(urllib.error.HTTPError) as failure:
                launcher.read_json("/api/version")
            assert failure.value.code == 302
        assert requests == []


def test_project_identity_is_path_independent_of_cwd_and_does_not_reveal_path(tmp_path, monkeypatch):
    root = tmp_path / "中文题库"
    root.mkdir()
    identity = project_instance_id(root)
    monkeypatch.chdir(tmp_path.parent)
    assert project_instance_id(root / ".") == identity
    assert len(identity) == 64 and str(root) not in identity
    assert project_instance_id(tmp_path / "other") != identity
