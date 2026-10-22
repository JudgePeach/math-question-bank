"""Shared local-launcher behavior; runs without importing the application or data."""
import hashlib
import errno
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import local_launcher as launcher


def test_windows_entry_uses_shared_launcher():
    from scripts import windows_launcher
    assert windows_launcher.main is launcher.main


@pytest.mark.parametrize("old_state", ['broken JSON', '{"pid": 1}', ''])
def test_existing_service_opens_without_environment_or_state_checks(tmp_path, monkeypatch, old_state):
    state = tmp_path / ".system_generated"
    state.mkdir()
    (state / "server-state.json").write_text(old_state)
    (tmp_path / "RELEASE-MANIFEST.json").write_text("invalid manifest")
    opened = []
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: True)
    monkeypatch.setattr(launcher, "open_browser", lambda: opened.append(True))
    monkeypatch.setattr(launcher, "prepare_python", lambda *_: pytest.fail("environment touched"))
    monkeypatch.setattr(launcher, "check_startup_storage", lambda *_: pytest.fail("write probe ran"))
    launcher.run_launcher(tmp_path)
    assert opened == [True]
    assert (state / "server-state.json").read_text() == old_state


def test_free_port_starts_despite_broken_old_state_and_manifest(tmp_path, monkeypatch):
    state = tmp_path / ".system_generated"
    state.mkdir()
    for name in ("server-state.json", "installed-release-manifest.json", "server.pid"):
        (state / name).write_text("invalid old record")
    (tmp_path / "RELEASE-MANIFEST.json").write_text("not JSON")
    events = []
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: False)
    monkeypatch.setattr(launcher, "port_in_use", lambda: False)
    monkeypatch.setattr(launcher, "prepare_python", lambda root: (Path(sys.executable), None))
    monkeypatch.setattr(launcher, "ensure_dependencies", lambda *_: events.append("dependencies"))
    monkeypatch.setattr(launcher, "start_server", lambda *_: events.append("start"))
    launcher.run_launcher(tmp_path)
    assert events == ["dependencies", "start"]
    assert (state / "server.pid").read_text() == "invalid old record"


def test_foreign_port_reports_conflict_without_starting_or_stopping(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: False)
    monkeypatch.setattr(launcher, "port_in_use", lambda: True)
    monkeypatch.setattr(launcher, "start_server", lambda *_: pytest.fail("started"))
    with pytest.raises(launcher.LauncherError, match="8000"):
        launcher.run_launcher(tmp_path)


def test_preflight_mode_only_checks_fixed_project_root(tmp_path, monkeypatch):
    root, elsewhere = tmp_path / "题库", tmp_path / "other"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(launcher, "PROJECT_ROOT", root)
    checked = []
    monkeypatch.setattr(launcher, "check_startup_storage", lambda path: checked.append(path))
    monkeypatch.setattr(launcher, "run_launcher", lambda *_: pytest.fail("started application"))
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: pytest.fail("contacted service"))
    assert launcher.main(["--check-write-access"]) == 0
    assert checked == [root]
    assert not (root / "math_question_bank.db").exists()
    assert not (root / "venv").exists()


def test_permission_failure_precedes_lock_environment_and_service(tmp_path, monkeypatch, capsys):
    root = tmp_path / "只读题库"
    root.mkdir()
    failure = launcher.StorageAccessError(root, label="程序目录", stage="create", cause=PermissionError(errno.EACCES, "raw private error"))
    checked = []
    def fail_check(path):
        checked.append(path)
        raise failure
    monkeypatch.setattr(launcher, "existing_service_ready", lambda: False)
    monkeypatch.setattr(launcher, "check_startup_storage", fail_check)
    monkeypatch.setattr(launcher, "launcher_lock", lambda *_: pytest.fail("lock opened"))
    monkeypatch.setattr(launcher, "prepare_python", lambda *_: pytest.fail("environment touched"))
    monkeypatch.setattr(launcher, "ensure_dependencies", lambda *_: pytest.fail("dependencies touched"))
    monkeypatch.setattr(launcher, "start_server", lambda *_: pytest.fail("started service"))
    with pytest.raises(launcher.LauncherError) as caught:
        launcher.run_launcher(root)
    assert str(caught.value) == failure.safe_message
    assert caught.value.__cause__ is failure
    monkeypatch.setattr(launcher, "PROJECT_ROOT", root)
    assert launcher.main(["--check-write-access"]) == 1
    output = capsys.readouterr()
    assert failure.safe_message in output.err
    assert "raw private error" not in output.err
    assert checked == [root, root]
    assert not list(root.iterdir())


def test_bootstrap_preflight_runs_without_application_or_database_imports(tmp_path):
    root = tmp_path / "中文题库"
    root.mkdir()
    source_root = Path(launcher.__file__).resolve().parents[1]
    for relative in (
        "scripts/local_launcher.py", "scripts/windows_launcher.py",
        "mathbank/__init__.py", "mathbank/paths.py", "mathbank/storage_access.py",
    ):
        target = root / relative
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source_root / relative, target)
    for relative in ("main.py", "mathbank/database.py"):
        (root / relative).write_text("raise AssertionError('application imported during write preflight')\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.windows_launcher", "--check-write-access"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "math_question_bank.db").exists()
    assert not (root / "venv").exists()
    assert not (root / ".system_generated" / "launcher.lock").exists()
    assert not (root / ".system_generated" / "server.log").exists()
    batch = (source_root / "启动题库系统.bat").read_bytes()
    assert batch.isascii()
    assert b"\n" not in batch.replace(b"\r\n", b"")
    preflight = batch.index(b"%BOOTSTRAP_CMD% -B -m scripts.windows_launcher --check-write-access")
    assert preflight < batch.index(b'%BOOTSTRAP_CMD% -m venv')
    assert batch[preflight:].split(b"\r\n", 2)[1] == b"if errorlevel 1 goto bootstrap_error"


@pytest.mark.parametrize("ready,repo,expected", [
    (True, launcher.REPOSITORY, True), (False, launcher.REPOSITORY, False),
    (True, "other/app", False),
])
def test_existing_service_requires_usable_mathbank_page(monkeypatch, ready, repo, expected):
    monkeypatch.setattr(launcher, "read_json", lambda route:
                        {"repo": repo} if route == "/api/version" else {"ready": ready})
    assert launcher.existing_service_ready() is expected


def test_new_service_readiness_does_not_depend_on_launcher_pid(monkeypatch):
    child = SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(launcher, "read_json", lambda _: {"ready": True, "server_instance_id": "ours"})
    assert launcher.wait_until_ready(child, "ours") == (True, "ready")


def test_another_services_health_cannot_mask_start_failure(monkeypatch):
    child = SimpleNamespace(poll=lambda: 1, returncode=1)
    monkeypatch.setattr(launcher, "read_json", lambda _: {"ready": True, "server_instance_id": "other"})
    ready, detail = launcher.wait_until_ready(child, "ours")
    assert not ready
    assert "退出码 1" in detail


def test_redirector_exit_zero_allows_delegated_service_to_finish_starting(monkeypatch):
    child = SimpleNamespace(poll=lambda: 0, returncode=0)
    responses = iter([OSError("not listening yet"), {"ready": True, "server_instance_id": "ours"}])
    def read(_route):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(launcher, "read_json", read)
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    assert launcher.wait_until_ready(child, "ours") == (True, "ready")


def test_proxy_variables_do_not_affect_local_requests(monkeypatch):
    import urllib.request
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    seen = []
    original = urllib.request.build_opener
    def opener(*handlers):
        seen.extend(handlers)
        result = original(*handlers)
        result.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline"))
        return result
    monkeypatch.setattr(urllib.request, "build_opener", opener)
    assert not launcher.existing_service_ready()
    assert seen[0].proxies == {}


def test_lock_file_is_reusable_after_exception(tmp_path):
    with pytest.raises(ValueError):
        with launcher.launcher_lock(tmp_path):
            raise ValueError("interrupted")
    with launcher.launcher_lock(tmp_path, timeout=0):
        pass


@pytest.mark.parametrize("changed,imports_ok,installs", [
    (False, True, False), (True, True, True), (False, False, True),
])
def test_dependencies_install_only_when_needed(tmp_path, monkeypatch, changed, imports_ok, installs):
    monkeypatch.delenv("MATHBANK_PORTABLE_RUNTIME", raising=False)
    requirements = b"fastapi==1.0\n"
    (tmp_path / "requirements.txt").write_bytes(requirements)
    state = tmp_path / ".system_generated"
    state.mkdir()
    stamp = state / "requirements.sha256"
    stamp.write_text("old" if changed else hashlib.sha256(requirements).hexdigest())
    monkeypatch.setattr(launcher, "environment_works", lambda *_: imports_ok)
    commands = []
    monkeypatch.setattr(launcher, "run_checked", lambda command, *_: commands.append(command))
    launcher.ensure_dependencies(tmp_path, Path(sys.executable))
    assert bool(commands) is installs
    if installs:
        assert "install" in commands[0]
        assert stamp.read_text().strip() == hashlib.sha256(requirements).hexdigest()


def test_portable_missing_dependency_shows_import_error_without_pip(tmp_path, monkeypatch):
    monkeypatch.setenv("MATHBANK_PORTABLE_RUNTIME", "1")
    monkeypatch.setattr(launcher, "environment_works", lambda *_: False)
    commands = []
    def fail(command, root, message):
        commands.append(command)
        raise launcher.LauncherError(message)
    monkeypatch.setattr(launcher, "run_checked", fail)
    with pytest.raises(launcher.LauncherError, match="依赖不完整"):
        launcher.ensure_dependencies(tmp_path, Path(sys.executable))
    assert "pip" not in commands[0]


def test_mac_rebuild_failure_restores_existing_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "environment_works", lambda *_: False)
    (tmp_path / ".system_generated").mkdir()
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "keep.txt").write_text("old environment")
    def fail(*_args):
        venv.mkdir()
        raise launcher.LauncherError("create failed")
    monkeypatch.setattr(launcher, "run_checked", fail)
    with pytest.raises(launcher.LauncherError):
        launcher.prepare_python(tmp_path)
    assert (venv / "keep.txt").read_text() == "old environment"


def test_failure_keeps_service_log_and_does_not_kill_child(tmp_path, monkeypatch, capsys):
    (tmp_path / ".system_generated").mkdir()
    class Child:
        pid = 123
        def terminate(self):
            pytest.fail("terminated")
        def kill(self):
            pytest.fail("killed")
    def spawn(*args, **kwargs):
        kwargs["stdout"].write(b"ImportError: missing_package\n")
        return Child()
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "wait_until_ready", lambda *_: (False, "failed"))
    monkeypatch.setattr(launcher, "open_browser", lambda: pytest.fail("opened"))
    with pytest.raises(launcher.LauncherError):
        launcher.start_server(tmp_path, Path(sys.executable))
    assert "missing_package" in capsys.readouterr().out
    assert (tmp_path / ".system_generated/probe.log").read_text() == "failed"
