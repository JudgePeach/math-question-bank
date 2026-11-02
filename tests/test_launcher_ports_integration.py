"""Real Uvicorn children on disposable sources, ports, and synthetic HTTP data."""
import contextlib
import json
import shutil
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import local_launcher as launcher


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_TOKEN = "integration-only-synthetic-token-12345678"

# This is deliberately not the product main.py. It cannot read a database,
# uploads, .env, or a model configuration. It models only the launcher protocol.
APP_SOURCE = r'''
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import signal
import time

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from mathbank.paths import PROJECT_ROOT, project_instance_id
from scripts.local_launcher import launcher_lock

STATE = PROJECT_ROOT / ".system_generated"
INSTANCE = os.environ["MATHBANK_LAUNCH_ID"]
TOKEN = "integration-only-synthetic-token-12345678"
LEGACY = os.environ.get("MATHBANK_SYNTHETIC_LEGACY") == "1"


@asynccontextmanager
async def lifespan(_app):
    with launcher_lock(STATE, filename="runtime.lock"):
        (STATE / "local_token").write_text(TOKEN, encoding="utf-8")
        with (STATE / "synthetic-starts.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"pid": os.getpid(), "instance": INSTANCE}) + "\n")
        yield


app = FastAPI(lifespan=lifespan)


def identity():
    return {"server_instance_id": INSTANCE, **(
        {} if LEGACY else {"project_instance_id": project_instance_id()}
    )}


@app.get("/api/version")
def version():
    return {"repo": "JudgePeach/math-question-bank", **identity()}


@app.get("/healthz")
def health():
    return {"ready": True, **identity()}


@app.get("/")
def index():
    return HTMLResponse('<script>window.__localToken = "' + TOKEN + '";</script>')


@app.post("/synthetic/shutdown")
def shutdown(background_tasks: BackgroundTasks, x_synthetic_token: str = Header("")):
    if x_synthetic_token != TOKEN:
        raise HTTPException(403)

    def stop():
        time.sleep(0.05)
        signal.raise_signal(signal.SIGINT)

    background_tasks.add_task(stop)
    return {"stopping": INSTANCE}
'''


def _disposable_source(root):
    root.mkdir()
    for relative in (
        "scripts/local_launcher.py", "scripts/windows_launcher.py",
        "mathbank/paths.py", "mathbank/storage_access.py",
    ):
        target = root / relative
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(SOURCE_ROOT / relative, target)
    (root / "mathbank/__init__.py").write_text("# Synthetic integration package.\n")
    (root / "main.py").write_text(APP_SOURCE, encoding="utf-8")


def _json_request(port, path, *, shutdown=False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=b"" if shutdown else None,
        headers={"X-Synthetic-Token": SYNTHETIC_TOKEN} if shutdown else {},
    )
    with opener.open(request, timeout=2) as response:
        return json.load(response)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


@contextlib.contextmanager
def _unrelated_application():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"repo":"synthetic-unrelated-app","still_running":true}'
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
            yield server.server_port
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


@pytest.mark.parametrize("legacy", [False, True], ids=["current-service", "old-service-after-update"])
def test_real_confirmed_port_start_memory_reuse_and_cooperative_shutdown(
    tmp_path, monkeypatch, legacy, record_property,
):
    pytest.importorskip("uvicorn")
    root = tmp_path / "中文 空格题库"
    _disposable_source(root)
    monkeypatch.delenv("MATHBANK_PORT", raising=False)
    monkeypatch.setenv("MATHBANK_SYNTHETIC_LEGACY", "1" if legacy else "0")
    monkeypatch.setattr(launcher, "ACTIVE_ROOT", root)
    monkeypatch.setattr(launcher, "PORT", 8000)
    monkeypatch.setattr(launcher, "BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(launcher, "prepare_python", lambda _root: (Path(sys.executable), None))
    monkeypatch.setattr(launcher, "ensure_dependencies", lambda *_args: None)
    monkeypatch.setattr(launcher.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    confirmations, opened, children = [], [], []
    monkeypatch.setattr("builtins.input", lambda prompt: confirmations.append(prompt) or "y")
    monkeypatch.setattr(launcher, "open_browser", lambda: opened.append(launcher.BASE_URL))
    original_spawn = launcher.subprocess.Popen

    def owned_spawn(command, **kwargs):
        child = original_spawn(command, **kwargs)
        children.append(child)
        record_property("owned_child", json.dumps({
            "pid": child.pid, "purpose": "disposable Uvicorn launcher protocol test",
            "port": int(command[-1]),
        }))
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", owned_spawn)
    port = _free_port()
    try:
        with _unrelated_application() as occupied:
            launcher._set_port(occupied)
            assert launcher.port_in_use()
            state = root / ".system_generated"
            state.mkdir()
            # Dynamic high ports exercise the exact default/conflict path
            # without touching another task's 8000 or a user's real service.
            (state / "launcher-settings.json").write_text(json.dumps({"port": occupied}))
            monkeypatch.setattr(launcher, "candidate_ports", lambda *_args: [occupied, port])
            launcher.run_launcher(root)
            assert len(confirmations) == 1
            assert len(children) == 1
            child = children[0]
            first = _json_request(port, "/api/version")
            assert len(first["server_instance_id"]) == 32
            assert ("project_instance_id" not in first) is legacy
            assert launcher.configured_port(root) == (port, False)
            assert child.poll() is None
            assert launcher.runtime_is_busy(root)

            # Updating files does not replace the process already listening.
            with (root / "main.py").open("a", encoding="utf-8") as handle:
                handle.write("\n# Simulated source update while the old service remains open.\n")
            monkeypatch.setattr(launcher, "prepare_python", lambda *_args: pytest.fail("duplicate preparation"))
            launcher.run_launcher(root)
            launcher.run_launcher(root)
            assert len(children) == 1
            assert len(confirmations) == 1
            assert opened == [f"http://127.0.0.1:{port}"] * 3
            assert _json_request(port, "/api/version") == first
            starts = [json.loads(line) for line in (state / "synthetic-starts.jsonl").read_text().splitlines()]
            assert starts == [{"pid": child.pid, "instance": first["server_instance_id"]}]
            assert _json_request(occupied, "/api/version")["still_running"] is True

            stopping = _json_request(port, "/synthetic/shutdown", shutdown=True)
            assert stopping["stopping"] == first["server_instance_id"]
            assert child.wait(timeout=10) == 0
            assert not launcher.runtime_is_busy(root)
            launcher._set_port(port)
            assert not launcher.port_in_use()
            assert _json_request(occupied, "/api/version")["still_running"] is True

            # A normal close followed immediately by another launch must keep
            # the remembered origin, even while old HTTP sockets are TIME_WAIT.
            monkeypatch.setattr(launcher, "prepare_python", lambda _root: (Path(sys.executable), None))
            launcher.run_launcher(root)
            assert len(children) == 2
            assert len(confirmations) == 1
            assert opened == [f"http://127.0.0.1:{port}"] * 4
            second = _json_request(port, "/api/version")
            assert second["server_instance_id"] != first["server_instance_id"]
            assert launcher.configured_port(root) == (port, False)
            assert _json_request(port, "/synthetic/shutdown", shutdown=True)["stopping"] == second["server_instance_id"]
            assert children[1].wait(timeout=10) == 0
            assert not launcher.runtime_is_busy(root)
            assert not launcher.port_in_use()
            assert _json_request(occupied, "/api/version")["still_running"] is True
            assert not (root / "math_question_bank.db").exists()
    finally:
        # Every handle below came from owned_spawn in this test. Prefer the
        # synthetic app's cooperative HTTP exit; signal only this known child
        # if a failed assertion left it without a reachable HTTP endpoint.
        for child in children:
            if child.poll() is not None:
                continue
            try:
                _json_request(port, "/synthetic/shutdown", shutdown=True)
                child.wait(timeout=10)
            except (OSError, ValueError, launcher.subprocess.TimeoutExpired):
                child.terminate()
                try:
                    child.wait(timeout=5)
                except launcher.subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            assert child.poll() is not None
