"""Windows launcher core for the ASCII ``启动题库系统.bat`` wrapper.

The module intentionally imports only the Python standard library.  It runs
before Release overlay reconciliation and dependency repair, so importing the
application or any third-party package here would make a damaged environment
impossible to repair.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import ntpath
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

STATE_SCHEMA = 1
STATE_NAME = "server-state.json"
LEGACY_PID_NAME = "server.pid"
LEGACY_IDENTITY_NAME = "server.identity"
LAUNCHER_LOCK_NAME = "launcher.lock"
RUNTIME_LOCK_NAME = "runtime.lock"
DIAGNOSTIC_NAME = "launcher-diagnostic.log"
PROBE_NAME = "probe.log"
OUT_LOG_NAME = "server.out.log"
ERR_LOG_NAME = "server.error.log"
REQUIREMENTS_STAMP_NAME = "requirements.sha256"
PORT = 8000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_GENERATED_DIR = PROJECT_ROOT / ".system_generated"
EXPECTED_REPOSITORY = "JudgePeach/math-question-bank"
HEALTH_URL = f"http://127.0.0.1:{PORT}/healthz"
VERSION_URL = f"http://127.0.0.1:{PORT}/api/version"
SHUTDOWN_URL = f"http://127.0.0.1:{PORT}/api/shutdown"
# ``Win32_Process.CreationDate`` is converted to ``System.DateTime.Ticks``
# (epoch 0001-01-01), not Windows FILETIME (epoch 1601-01-01).
WINDOWS_EPOCH_TICKS = 621_355_968_000_000_000
UVICORN_PATTERN = re.compile(
    r"(?i)(?:^|\s)-m\s+uvicorn\s+main:app(?:\s|$)"
)
EXPECTED_UVICORN_ARGS = (
    "-u",
    "-m",
    "uvicorn",
    "main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
)

RUNTIME_IMPORTS = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "greenlet",
    "colorama",
    "multipart",
    "dotenv",
    "requests",
    "PIL",
    "docx",
    "lxml",
    "defusedxml",
    "olefile",
    "exceptiongroup",
    "sniffio",
)

DECISION_NO_STATE = "no_state"
DECISION_STALE_DEAD = "stale_dead"
DECISION_STALE_REUSED = "stale_pid_reused"
DECISION_OWNED = "owned"
DECISION_UNKNOWN = "unknown"


class LauncherError(RuntimeError):
    """A fail-closed launcher error with a stable support code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProcessInspectionError(LauncherError):
    pass


@dataclasses.dataclass(frozen=True)
class LauncherPaths:
    root: Path
    state_dir: Path
    state: Path
    legacy_pid: Path
    legacy_identity: Path
    launcher_lock: Path
    runtime_lock: Path
    diagnostic: Path
    probe: Path
    out_log: Path
    err_log: Path
    requirements: Path
    requirements_stamp: Path
    local_token: Path


@dataclasses.dataclass(frozen=True)
class ProcessInfo:
    pid: int
    creation_ticks: int
    executable: str
    command_line: str


@dataclasses.dataclass(frozen=True)
class ServerState:
    schema: int
    pid: int
    creation_ticks: int
    root: str
    executable: str
    command: tuple[str, ...]
    launch_id: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "pid": self.pid,
            "creation_ticks": self.creation_ticks,
            "root": self.root,
            "executable": self.executable,
            "command": list(self.command),
            "launch_id": self.launch_id,
        }


@dataclasses.dataclass(frozen=True)
class LoadedState:
    kind: str
    pid: int
    root: str | None
    record: ServerState | None
    recorded_at_ticks: int | None


def launcher_paths(root: Path = PROJECT_ROOT) -> LauncherPaths:
    root = Path(root).resolve()
    state_dir = (
        SYSTEM_GENERATED_DIR
        if root == PROJECT_ROOT.resolve()
        else root / ".system_generated"
    )
    return LauncherPaths(
        root=root,
        state_dir=state_dir,
        state=state_dir / STATE_NAME,
        legacy_pid=state_dir / LEGACY_PID_NAME,
        legacy_identity=state_dir / LEGACY_IDENTITY_NAME,
        launcher_lock=state_dir / LAUNCHER_LOCK_NAME,
        runtime_lock=state_dir / RUNTIME_LOCK_NAME,
        diagnostic=state_dir / DIAGNOSTIC_NAME,
        probe=state_dir / PROBE_NAME,
        out_log=state_dir / OUT_LOG_NAME,
        err_log=state_dir / ERR_LOG_NAME,
        requirements=root / "requirements.txt",
        requirements_stamp=state_dir / REQUIREMENTS_STAMP_NAME,
        local_token=state_dir / "local_token",
    )


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _normal_path(value: str | os.PathLike[str]) -> str:
    return ntpath.normcase(ntpath.normpath(os.fspath(value)))


def _same_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return _normal_path(left) == _normal_path(right)


def _expected_executables(root: str | os.PathLike[str]) -> tuple[str, str]:
    root_text = os.fspath(root)
    return (
        ntpath.join(root_text, "python", "python.exe"),
        ntpath.join(root_text, "venv", "Scripts", "python.exe"),
    )


def _is_expected_executable(executable: str, root: str | os.PathLike[str]) -> bool:
    return any(_same_path(executable, candidate) for candidate in _expected_executables(root))


def _is_uvicorn_command(command_line: str) -> bool:
    return bool(UVICORN_PATTERN.search(command_line or ""))


def _saved_command_is_exact(record: ServerState) -> bool:
    return bool(
        len(record.command) == 1 + len(EXPECTED_UVICORN_ARGS)
        and _same_path(record.command[0], record.executable)
        and record.command[1:] == EXPECTED_UVICORN_ARGS
    )


def _process_matches_saved_command(process: ProcessInfo, record: ServerState) -> bool:
    if not _saved_command_is_exact(record):
        return False
    return process.command_line.strip() == subprocess.list2cmdline(record.command)


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise LauncherError("E_STATE_LINK", f"运行状态目录不能是符号链接：{path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise LauncherError("E_STATE_DIR", f"无法创建运行状态目录：{path}")
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("E_STATE_DIR", f"无法解析运行状态目录：{path}") from exc
    if os.path.normcase(os.fspath(absolute)) != os.path.normcase(os.fspath(resolved)):
        raise LauncherError(
            "E_STATE_LINK", f"运行状态目录不能经过联接点或重解析路径：{path}"
        )
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _reject_link(path: Path, label: str) -> None:
    if path.is_symlink():
        raise LauncherError("E_STATE_LINK", f"{label}不能是符号链接：{path}")
    if path.exists():
        try:
            absolute = Path(os.path.abspath(path))
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise LauncherError("E_STATE_LINK", f"无法解析{label}：{path}") from exc
        if os.path.normcase(os.fspath(absolute)) != os.path.normcase(os.fspath(resolved)):
            raise LauncherError("E_STATE_LINK", f"{label}不能是重解析路径：{path}")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    _ensure_private_directory(path.parent)
    _reject_link(path, path.name)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    handle = None
    try:
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary_path, path)
    except Exception:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary_path.unlink()
        raise


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(path, text.encode(encoding))


def _append_diagnostic(paths: LauncherPaths, code: str, detail: str) -> None:
    try:
        _ensure_private_directory(paths.state_dir)
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        safe_detail = " ".join(str(detail).splitlines())[:2000]
        _reject_link(paths.diagnostic, DIAGNOSTIC_NAME)
        with paths.diagnostic.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{timestamp}\t{code}\t{safe_detail}\n")
        with contextlib.suppress(OSError):
            paths.diagnostic.chmod(0o600)
    except Exception:
        pass


def _parse_state_mapping(raw: Mapping[str, Any]) -> ServerState:
    required = {
        "schema",
        "pid",
        "creation_ticks",
        "root",
        "executable",
        "command",
        "launch_id",
    }
    if set(raw) != required:
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态字段不完整。")
    schema = raw["schema"]
    pid = raw["pid"]
    creation_ticks = raw["creation_ticks"]
    command = raw["command"]
    if schema != STATE_SCHEMA:
        raise LauncherError("E_STATE_VERSION", "Windows 启动状态版本不受支持。")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态 PID 无效。")
    if (
        not isinstance(creation_ticks, int)
        or isinstance(creation_ticks, bool)
        or creation_ticks <= 0
    ):
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态创建时间无效。")
    if not isinstance(raw["root"], str) or not raw["root"].strip():
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态根目录无效。")
    if not isinstance(raw["executable"], str) or not raw["executable"].strip():
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态解释器路径无效。")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态命令无效。")
    if not isinstance(raw["launch_id"], str) or not re.fullmatch(
        r"[0-9a-f]{32}", raw["launch_id"]
    ):
        raise LauncherError("E_STATE_INVALID", "Windows 启动状态标识无效。")
    return ServerState(
        schema=schema,
        pid=pid,
        creation_ticks=creation_ticks,
        root=raw["root"],
        executable=raw["executable"],
        command=tuple(command),
        launch_id=raw["launch_id"],
    )


def read_server_state(paths: LauncherPaths) -> LoadedState | None:
    if paths.state.exists() or paths.state.is_symlink():
        _reject_link(paths.state, STATE_NAME)
        try:
            raw = json.loads(paths.state.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LauncherError("E_STATE_INVALID", "无法读取 Windows 原子启动状态。") from exc
        if not isinstance(raw, dict):
            raise LauncherError("E_STATE_INVALID", "Windows 原子启动状态不是对象。")
        record = _parse_state_mapping(raw)
        return LoadedState(
            kind="json",
            pid=record.pid,
            root=record.root,
            record=record,
            recorded_at_ticks=None,
        )

    pid_exists = paths.legacy_pid.exists() or paths.legacy_pid.is_symlink()
    identity_exists = paths.legacy_identity.exists() or paths.legacy_identity.is_symlink()
    if not pid_exists and not identity_exists:
        return None
    if not pid_exists or not identity_exists:
        raise LauncherError("E_STATE_LEGACY_PARTIAL", "旧版 PID/identity 状态不完整。")
    _reject_link(paths.legacy_pid, LEGACY_PID_NAME)
    _reject_link(paths.legacy_identity, LEGACY_IDENTITY_NAME)
    try:
        pid_text = paths.legacy_pid.read_text(encoding="ascii").strip()
        root = paths.legacy_identity.read_text(encoding="utf-8-sig").strip()
        pid = int(pid_text)
        if pid <= 0 or not root:
            raise ValueError
        recorded_ns = max(
            paths.legacy_pid.stat().st_mtime_ns,
            paths.legacy_identity.stat().st_mtime_ns,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise LauncherError("E_STATE_LEGACY_INVALID", "旧版 PID/identity 状态无效。") from exc
    recorded_ticks = WINDOWS_EPOCH_TICKS + recorded_ns // 100
    return LoadedState(
        kind="legacy",
        pid=pid,
        root=root,
        record=None,
        recorded_at_ticks=recorded_ticks,
    )


def write_server_state(paths: LauncherPaths, state: ServerState) -> None:
    payload = json.dumps(
        state.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    _atomic_write_text(paths.state, payload)
    # Compatibility mirrors are not authoritative.  The atomic JSON above is
    # always read first on future launches.
    _atomic_write_text(paths.legacy_identity, state.root + "\n")
    _atomic_write_text(paths.legacy_pid, f"{state.pid}\n", encoding="ascii")


def _safe_unlink(path: Path) -> None:
    if path.is_symlink():
        raise LauncherError("E_STATE_LINK", f"拒绝删除符号链接状态：{path}")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def clear_server_state(paths: LauncherPaths) -> None:
    # Compatibility mirrors go first.  The authoritative JSON remains valid
    # if antivirus/file locking interrupts cleanup between operations.
    for path in (paths.legacy_pid, paths.legacy_identity, paths.state):
        _safe_unlink(path)


def quarantine_server_state(
    paths: LauncherPaths, loaded: LoadedState, reason: str
) -> list[Path]:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:8]
    moved: list[Path] = []
    state_target = paths.state_dir / f"stale-launcher-state-{stamp}-{suffix}.json"
    if not paths.state.exists():
        snapshot = {
            "schema": 1,
            "reason": reason,
            "legacy_pid": loaded.pid,
            "legacy_root": loaded.root,
            "recorded_at_ticks": loaded.recorded_at_ticks,
        }
        _atomic_write_text(
            state_target,
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n",
        )
        moved.append(state_target)
    for source, label in (
        (paths.legacy_pid, "pid"),
        (paths.legacy_identity, "identity"),
    ):
        if source.exists():
            target = paths.state_dir / f"stale-server-{label}-{stamp}-{suffix}"
            os.replace(source, target)
            moved.append(target)
    if paths.state.exists():
        os.replace(paths.state, state_target)
        moved.append(state_target)
    return moved


def _windows_system_tool(*relative_parts: str) -> str:
    system_root = os.environ.get("SystemRoot", "").strip()
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetWindowsDirectoryW(  # type: ignore[attr-defined]
            buffer, len(buffer)
        )
        if 0 < length < len(buffer):
            system_root = buffer.value
    if not system_root:
        raise ProcessInspectionError("E_SYSTEM_TOOL", "Windows SystemRoot 未定义。")
    tool = Path(system_root).joinpath("System32", *relative_parts)
    if not tool.is_file() or tool.is_symlink():
        raise ProcessInspectionError(
            "E_SYSTEM_TOOL", f"Windows 系统工具不存在或不安全：{tool}"
        )
    return os.fspath(tool)


def _encoded_powershell(script: str) -> list[str]:
    executable = _windows_system_tool(
        "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def query_process(pid: int) -> ProcessInfo | None:
    if not isinstance(pid, int) or pid <= 0:
        raise ProcessInspectionError("E_PROCESS_QUERY", "待检查 PID 无效。")
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$process = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction Stop
if ($null -eq $process) {{ exit 3 }}
if ($null -eq $process.ExecutablePath -or $null -eq $process.CommandLine -or $null -eq $process.CreationDate) {{ exit 4 }}
$payload = [ordered]@{{
  pid = [int]$process.ProcessId
  creation_ticks = ([datetime]$process.CreationDate).ToUniversalTime().Ticks
  executable = [string]$process.ExecutablePath
  command_line = [string]$process.CommandLine
}}
$payload | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            _encoded_powershell(script),
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessInspectionError("E_PROCESS_QUERY", "无法调用 CIM 检查进程身份。") from exc
    if result.returncode == 3:
        return None
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessInspectionError(
            "E_PROCESS_QUERY", f"CIM 无法完整读取 PID {pid} 的身份：{detail[:300]}"
        )
    try:
        output = result.stdout.decode("utf-8", errors="strict").strip()
        raw = json.loads(output.splitlines()[-1])
        return ProcessInfo(
            pid=int(raw["pid"]),
            creation_ticks=int(raw["creation_ticks"]),
            executable=str(raw["executable"]),
            command_line=str(raw["command_line"]),
        )
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProcessInspectionError("E_PROCESS_QUERY", "CIM 返回了无效的进程身份。") from exc


def classify_state(
    loaded: LoadedState | None,
    process: ProcessInfo | None,
    expected_root: Path | str,
) -> str:
    """Classify saved state without performing any process or file mutation."""

    if loaded is None:
        return DECISION_NO_STATE
    if process is None:
        return DECISION_STALE_DEAD

    expected_root_text = os.fspath(expected_root)
    if loaded.record is not None:
        record = loaded.record
        if process.pid != record.pid:
            return DECISION_UNKNOWN
        if process.creation_ticks != record.creation_ticks:
            return DECISION_STALE_REUSED
        matches = (
            _same_path(record.root, expected_root_text)
            and _same_path(process.executable, record.executable)
            and _is_expected_executable(process.executable, expected_root_text)
            and _process_matches_saved_command(process, record)
        )
        return DECISION_OWNED if matches else DECISION_UNKNOWN

    if loaded.recorded_at_ticks is None or not loaded.root:
        return DECISION_UNKNOWN
    # Legacy state has no CreationDate.  A live PID therefore cannot be bound
    # to the saved process instance, even when its visible command looks
    # unrelated.  Keep the evidence and fail closed; a reboot/dead PID can be
    # migrated safely on the next launch.
    return DECISION_UNKNOWN


def parse_netstat_listeners(output: str, port: int = PORT) -> set[int]:
    listeners: set[int] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        if fields[-2].upper() != "LISTENING":
            continue
        local_address = fields[1]
        try:
            local_port = int(local_address.rsplit(":", 1)[1])
            pid = int(fields[-1])
        except (IndexError, ValueError):
            continue
        if local_port == port and pid > 0:
            listeners.add(pid)
    return listeners


def listener_pids(port: int = PORT) -> set[int]:
    try:
        result = subprocess.run(
            [_windows_system_tool("netstat.exe"), "-aon", "-p", "tcp"],
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessInspectionError("E_PORT_QUERY", "无法检查端口占用。") from exc
    if result.returncode != 0:
        raise ProcessInspectionError("E_PORT_QUERY", "netstat 无法检查端口占用。")
    return parse_netstat_listeners(result.stdout.decode("ascii", errors="ignore"), port)


class ByteRangeLock:
    """Cross-platform one-byte lock used by the launcher and tests."""

    def __init__(self, path: Path, *, code: str, message: str):
        self.path = path
        self.code = code
        self.message = message
        self.handle = None

    def acquire(self) -> "ByteRangeLock":
        _reject_link(self.path, self.path.name)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
        except OSError as exc:
            raise LauncherError(self.code, self.message) from exc
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise LauncherError(self.code, self.message) from exc
        self.handle = handle
        return self

    def close(self) -> None:
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self) -> "ByteRangeLock":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def runtime_lock_is_free(paths: LauncherPaths) -> bool:
    lock = ByteRangeLock(
        paths.runtime_lock,
        code="E_RUNTIME_LOCKED",
        message="题库运行锁仍被占用。",
    )
    try:
        lock.acquire()
    except LauncherError as exc:
        if exc.code == "E_RUNTIME_LOCKED":
            return False
        raise
    else:
        lock.close()
        return True


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_json_url(url: str, timeout: float = 2.0) -> Mapping[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with _no_proxy_opener().open(request, timeout=timeout) as response:
        payload = response.read(1024 * 1024)
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("JSON response is not an object")
    return decoded


def _request_shutdown(
    paths: LauncherPaths, *, expected_launch_id: str | None = None
) -> None:
    try:
        token = paths.local_token.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise LauncherError("E_TOKEN", "无法读取本项目本地关闭令牌。") from exc
    if not token:
        raise LauncherError("E_TOKEN", "本项目本地关闭令牌为空。")
    headers = {"X-Local-Token": token}
    if expected_launch_id:
        headers["X-MathBank-Launch-ID"] = expected_launch_id
    request = urllib.request.Request(
        SHUTDOWN_URL,
        data=b"",
        headers=headers,
        method="POST",
    )
    try:
        with _no_proxy_opener().open(request, timeout=2.0):
            pass
    except (OSError, urllib.error.URLError) as exc:
        raise LauncherError("E_SHUTDOWN", "已确认服务未接受安全关闭请求。") from exc


def _verify_local_version(*, expected_launch_id: str | None = None) -> Mapping[str, Any]:
    try:
        version = _read_json_url(VERSION_URL)
    except Exception as exc:
        raise LauncherError("E_VERSION_ID", "端口服务无法通过 MathBank 仓库身份检查。") from exc
    if version.get("repo") != EXPECTED_REPOSITORY:
        raise LauncherError("E_VERSION_ID", "端口服务不是当前 MathBank 仓库。")
    if expected_launch_id and version.get("server_instance_id") != expected_launch_id:
        raise LauncherError("E_INSTANCE_ID", "端口服务实例与保存的启动标识不一致。")
    return version


def _wait_for_process_instance_exit(
    pid: int, creation_ticks: int, *, timeout: float = 5.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process = query_process(pid)
        if process is None or process.creation_ticks != creation_ticks:
            return True
        time.sleep(0.25)
    return False


def _require_same_process_instance(expected: ProcessInfo) -> ProcessInfo:
    current = query_process(expected.pid)
    if current is None or current.creation_ticks != expected.creation_ticks:
        raise LauncherError(
            "E_PROCESS_CHANGED",
            "进程身份在操作前发生变化；启动器未向该 PID 发送任何终止请求。",
        )
    return current


def _stop_owned_process(
    paths: LauncherPaths, loaded: LoadedState, process: ProcessInfo
) -> None:
    current = query_process(process.pid)
    if current is None or current.creation_ticks != process.creation_ticks:
        quarantine_server_state(paths, loaded, DECISION_STALE_REUSED)
        return
    listeners = listener_pids(PORT)
    if listeners != {process.pid}:
        raise LauncherError(
            "E_OWNED_NOT_LISTENING",
            "端口 8000 的监听者不能唯一绑定到已确认 MathBank 进程，已保留证据。",
        )
    expected_launch_id = loaded.record.launch_id if loaded.record else None
    _verify_local_version(expected_launch_id=expected_launch_id)
    _require_same_process_instance(process)
    if listener_pids(PORT) != {process.pid}:
        raise LauncherError(
            "E_PROCESS_CHANGED",
            "端口监听身份在关闭请求前发生变化；未发送关闭请求。",
        )
    print(f"正在安全关闭本项目上一次服务 [PID: {process.pid}]...")
    _request_shutdown(paths, expected_launch_id=expected_launch_id)
    if not _wait_for_process_instance_exit(process.pid, process.creation_ticks):
        raise LauncherError("E_SHUTDOWN_TIMEOUT", "旧服务未在 5 秒内正常退出。")
    clear_server_state(paths)


def handle_previous_state(paths: LauncherPaths) -> str:
    loaded = read_server_state(paths)
    if loaded is None:
        return DECISION_NO_STATE
    process = query_process(loaded.pid)
    decision = classify_state(loaded, process, paths.root)
    if decision in {DECISION_STALE_DEAD, DECISION_STALE_REUSED}:
        quarantine_server_state(paths, loaded, decision)
        _append_diagnostic(paths, decision.upper(), f"pid={loaded.pid}")
        print("检测到已失效的 Windows 启动状态，已安全隔离；未结束任何进程。")
        return decision
    if decision == DECISION_OWNED and process is not None:
        _stop_owned_process(paths, loaded, process)
        return decision
    raise LauncherError(
        "E_STATE_UNKNOWN",
        "状态文件指向无法完整验证身份的运行中进程；启动器不会终止该进程。",
    )


def _stop_verified_legacy_listener(paths: LauncherPaths, pid: int) -> None:
    process = query_process(pid)
    if process is None:
        return
    if not (
        _is_expected_executable(process.executable, paths.root)
        and _is_uvicorn_command(process.command_line)
    ):
        raise LauncherError(
            "E_PORT_FOREIGN",
            f"端口 8000 被无法安全确认身份的进程占用 [PID: {pid}]。",
        )
    version = _verify_local_version()
    server_instance_id = version.get("server_instance_id")
    expected_launch_id = (
        server_instance_id if isinstance(server_instance_id, str) and server_instance_id else None
    )
    _require_same_process_instance(process)
    if listener_pids(PORT) != {pid}:
        raise LauncherError(
            "E_PROCESS_CHANGED",
            "端口监听身份在旧版服务关闭前发生变化；未发送关闭请求。",
        )
    print(f"检测到当前目录的旧版 MathBank 服务 [PID: {pid}]，正在安全关闭...")
    _request_shutdown(paths, expected_launch_id=expected_launch_id)
    if not _wait_for_process_instance_exit(pid, process.creation_ticks):
        raise LauncherError("E_SHUTDOWN_TIMEOUT", "旧版服务未在 5 秒内正常退出。")


def stop_verified_port_listeners(paths: LauncherPaths) -> None:
    for pid in sorted(listener_pids(PORT)):
        _stop_verified_legacy_listener(paths, pid)
    remaining = listener_pids(PORT)
    if remaining:
        raise LauncherError(
            "E_PORT_BUSY", f"端口 8000 仍被占用 [PID: {', '.join(map(str, sorted(remaining)))}]。"
        )


def _run_checked(command: Sequence[str], *, cwd: Path, label: str) -> None:
    try:
        completed = subprocess.run(list(command), cwd=cwd, check=False)
    except OSError as exc:
        raise LauncherError("E_COMMAND", f"无法运行{label}。") from exc
    if completed.returncode != 0:
        raise LauncherError("E_COMMAND", f"{label}失败，退出码 {completed.returncode}。")


def run_release_overlay(paths: LauncherPaths) -> None:
    if not (paths.root / "RELEASE-MANIFEST.json").is_file():
        return
    print("正在校验并完成 Release 覆盖升级...")
    _run_checked(
        [
            sys.executable,
            "-B",
            "-m",
            "scripts.release_overlay",
            "--platform",
            "windows-x64",
        ],
        cwd=paths.root,
        label="Release 覆盖升级校验",
    )


def _runtime_import_code(*, portable: bool) -> str:
    prefix = ""
    if portable:
        prefix = (
            "import ctypes,pathlib,sys;"
            "runtime=pathlib.Path(sys.executable).resolve().parent;"
            "ctypes.WinDLL(str(runtime/'msvcp140.dll'));"
        )
    imports = ",".join(RUNTIME_IMPORTS)
    return f"{prefix}import {imports};import pymupdf as fitz;import pdf_inspector"


def _command_succeeds(command: Sequence[str], *, cwd: Path) -> bool:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def ensure_dependencies(paths: LauncherPaths) -> None:
    if not paths.requirements.is_file():
        raise LauncherError("E_REQUIREMENTS", "缺少 requirements.txt。")
    portable = os.environ.get("MATHBANK_PORTABLE_RUNTIME") == "1"
    import_command = [sys.executable, "-u", "-c", _runtime_import_code(portable=portable)]
    print("正在检查运行环境依赖是否完整...")
    if portable:
        _run_checked(import_command, cwd=paths.root, label="便携运行库导入检查")
        return

    digest = hashlib.sha256(paths.requirements.read_bytes()).hexdigest()
    try:
        installed_digest = paths.requirements_stamp.read_text(encoding="ascii").strip()
    except OSError:
        installed_digest = ""
    needs_install = installed_digest != digest
    if not _command_succeeds(import_command, cwd=paths.root):
        needs_install = True
    if not _command_succeeds(
        [sys.executable, "-m", "pip", "check"], cwd=paths.root
    ):
        needs_install = True
    if needs_install:
        print("检测到依赖缺失、冲突或锁文件变化，正在同步依赖...")
        _run_checked(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                os.fspath(paths.requirements),
            ],
            cwd=paths.root,
            label="依赖安装",
        )
        _run_checked(
            [sys.executable, "-m", "pip", "check"],
            cwd=paths.root,
            label="依赖一致性检查",
        )
        _run_checked(import_command, cwd=paths.root, label="依赖导入检查")
        _atomic_write_text(paths.requirements_stamp, digest + "\n", encoding="ascii")


def _query_process_with_retry(pid: int, timeout: float = 3.0) -> ProcessInfo:
    deadline = time.monotonic() + timeout
    last_error: LauncherError | None = None
    while time.monotonic() < deadline:
        try:
            process = query_process(pid)
            if process is not None:
                return process
        except LauncherError as exc:
            last_error = exc
        time.sleep(0.1)
    if last_error is not None:
        raise last_error
    raise LauncherError("E_CHILD_ID", "无法读取新服务进程身份。")


def _tail(path: Path, limit: int = 80) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-limit:])


def _write_probe(paths: LauncherPaths, text: str) -> None:
    with contextlib.suppress(OSError):
        _atomic_write_text(paths.probe, text.strip() + "\n")


def _cooperative_child_shutdown(
    paths: LauncherPaths,
    child: subprocess.Popen[Any],
    expected_creation_ticks: int,
    expected_launch_id: str,
) -> bool:
    if child.poll() is not None:
        return True
    try:
        current = query_process(child.pid)
        if (
            current is not None
            and current.creation_ticks == expected_creation_ticks
            and listener_pids(PORT) == {child.pid}
        ):
            _verify_local_version(expected_launch_id=expected_launch_id)
            current = query_process(child.pid)
            if (
                current is None
                or current.creation_ticks != expected_creation_ticks
                or listener_pids(PORT) != {child.pid}
            ):
                return False
            _request_shutdown(paths, expected_launch_id=expected_launch_id)
            child.wait(timeout=3)
            return True
    except (LauncherError, subprocess.TimeoutExpired):
        pass
    return child.poll() is not None


def _state_matches_child(paths: LauncherPaths, child: subprocess.Popen[Any], ticks: int) -> bool:
    try:
        loaded = read_server_state(paths)
    except LauncherError:
        return False
    return bool(
        loaded
        and loaded.record
        and loaded.record.pid == child.pid
        and loaded.record.creation_ticks == ticks
    )


def wait_until_ready(
    paths: LauncherPaths,
    child: subprocess.Popen[Any],
    state: ServerState,
    *,
    timeout: float = 60.0,
) -> tuple[bool, str]:
    opener = _no_proxy_opener()
    deadline = time.monotonic() + timeout
    last_probe = "服务尚未响应"
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return False, f"进程已提前退出 [PID: {child.pid}] - {last_probe}"
        try:
            current = query_process(child.pid)
            if current is None or current.creation_ticks != state.creation_ticks:
                return False, "新服务进程身份在健康检查期间发生变化。"
            listeners = listener_pids(PORT)
            if listeners and listeners != {child.pid}:
                return False, "端口 8000 被其他进程抢占。"
            if listeners != {child.pid}:
                last_probe = "本次子进程尚未监听端口 8000"
            else:
                with opener.open(HEALTH_URL, timeout=2.0) as response:
                    body = response.read(1024 * 1024).decode("utf-8", errors="replace")
                    if response.status == 200:
                        try:
                            health = json.loads(body)
                        except json.JSONDecodeError:
                            last_probe = "健康响应不是有效 JSON"
                        else:
                            if (
                                not isinstance(health, dict)
                                or health.get("server_instance_id") != state.launch_id
                            ):
                                return False, "健康响应来自其他 MathBank 实例。"
                            after = query_process(child.pid)
                            if (
                                child.poll() is None
                                and after is not None
                                and after.creation_ticks == state.creation_ticks
                                and listener_pids(PORT) == {child.pid}
                            ):
                                return True, "ready"
                            return False, "健康响应无法绑定到本次子进程。"
                    last_probe = f"HTTP {response.status}: {body}"
        except urllib.error.HTTPError as exc:
            body = exc.read(1024 * 1024).decode("utf-8", errors="replace")
            last_probe = f"HTTP {exc.code} ({exc.reason}): {body}"
        except (urllib.error.URLError, OSError, LauncherError) as exc:
            last_probe = str(exc)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, remaining))
    return False, f"探测超时（已等待 {int(timeout)} 秒）：{last_probe}"


def start_server(paths: LauncherPaths) -> None:
    listeners = listener_pids(PORT)
    if listeners:
        raise LauncherError(
            "E_PORT_BUSY", f"端口 8000 已被占用 [PID: {', '.join(map(str, sorted(listeners)))}]。"
        )
    for log_path in (paths.out_log, paths.err_log, paths.probe):
        _safe_unlink(log_path)

    command = (os.fspath(Path(sys.executable).resolve()), *EXPECTED_UVICORN_ARGS)
    launch_id = uuid.uuid4().hex
    child_environment = os.environ.copy()
    child_environment["MATHBANK_LAUNCH_ID"] = launch_id
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    print(f"正在启动服务：http://127.0.0.1:{PORT}")
    try:
        with paths.out_log.open("wb") as out_handle, paths.err_log.open("wb") as err_handle:
            child = subprocess.Popen(
                command,
                cwd=paths.root,
                stdin=subprocess.DEVNULL,
                stdout=out_handle,
                stderr=err_handle,
                close_fds=True,
                creationflags=creation_flags,
                env=child_environment,
            )
    except OSError as exc:
        raise LauncherError("E_CHILD_START", "无法创建后台服务进程。") from exc

    process: ProcessInfo | None = None
    try:
        process = _query_process_with_retry(child.pid)
        if not _same_path(
            process.executable, command[0]
        ) or process.command_line.strip() != subprocess.list2cmdline(command):
            raise LauncherError("E_CHILD_ID", "新服务进程身份与启动命令不一致。")
        state = ServerState(
            schema=STATE_SCHEMA,
            pid=child.pid,
            creation_ticks=process.creation_ticks,
            root=os.fspath(paths.root),
            executable=process.executable,
            command=command,
            launch_id=launch_id,
        )
        write_server_state(paths, state)
    except Exception as exc:
        if child.poll() is None:
            # Preserve a conservative legacy pointer when registration could
            # not complete.  A later launcher will fail closed unless it can
            # prove this PID is stale; no process is force-terminated here.
            with contextlib.suppress(OSError):
                _atomic_write_text(
                    paths.legacy_identity, os.fspath(paths.root) + "\n"
                )
                _atomic_write_text(
                    paths.legacy_pid, f"{child.pid}\n", encoding="ascii"
                )
            _append_diagnostic(
                paths,
                "E_CHILD_ORPHAN",
                f"pid={child.pid}; registration={type(exc).__name__}",
            )
            raise LauncherError(
                "E_CHILD_ORPHAN",
                f"新服务状态登记失败且进程 PID {child.pid} 仍在运行，证据已保留。",
            ) from exc
        raise

    ready, detail = wait_until_ready(paths, child, state)
    if not ready:
        _write_probe(paths, detail)
        stopped = _cooperative_child_shutdown(
            paths,
            child,
            state.creation_ticks,
            state.launch_id,
        )
        if stopped and _state_matches_child(paths, child, state.creation_ticks):
            clear_server_state(paths)
        else:
            _append_diagnostic(paths, "E_CHILD_STOP", "新服务未能确认停止，状态已保留。")
        stderr_tail = _tail(paths.err_log)
        stdout_tail = _tail(paths.out_log)
        if stderr_tail:
            print("---------------- 标准错误日志 ----------------")
            print(stderr_tail)
        if stdout_tail:
            print("---------------- 标准输出日志 ----------------")
            print(stdout_tail)
        raise LauncherError("E_HEALTH", detail)

    print("[成功] 后台服务已就绪。")
    if os.environ.get("MATHBANK_NO_BROWSER") != "1":
        try:
            os.startfile(f"http://127.0.0.1:{PORT}")  # type: ignore[attr-defined]
        except Exception as exc:
            _append_diagnostic(paths, "W_BROWSER", str(exc))
            print("[提示] 无法自动打开浏览器，请手动访问 http://127.0.0.1:8000。")


def run_launcher(paths: LauncherPaths) -> None:
    print("================================================")
    print("     本地数学题库教研系统（MathBank）Windows 启动器")
    print("================================================")
    if not (paths.root / "main.py").is_file():
        raise LauncherError("E_PROJECT_ROOT", "当前目录缺少 main.py。")
    _ensure_private_directory(paths.state_dir)
    handle_previous_state(paths)
    stop_verified_port_listeners(paths)
    run_release_overlay(paths)
    ensure_dependencies(paths)
    if not runtime_lock_is_free(paths):
        raise LauncherError("E_RUNTIME_LOCKED", "题库运行锁仍被占用，拒绝启动第二个服务。")
    start_server(paths)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MathBank Windows launcher core")
    parser.add_argument(
        "--self-test-stale-state",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    args = _parse_args(argv)
    paths = launcher_paths()
    try:
        if os.name != "nt":
            raise LauncherError("E_PLATFORM", "Windows 启动器只能在 Windows 上运行。")
        _ensure_private_directory(paths.state_dir)
        with ByteRangeLock(
            paths.launcher_lock,
            code="E_LAUNCHER_BUSY",
            message="另一份 MathBank 启动器正在运行，请稍后重试。",
        ):
            if args.self_test_stale_state:
                result = handle_previous_state(paths)
                if result not in {
                    DECISION_STALE_DEAD,
                    DECISION_STALE_REUSED,
                }:
                    raise LauncherError(
                        "E_SELF_TEST", f"陈旧状态回归得到意外结果：{result}"
                    )
                print("[ok] stale launcher state was isolated safely")
                return 0
            run_launcher(paths)
            return 0
    except LauncherError as exc:
        _append_diagnostic(paths, exc.code, str(exc))
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        _append_diagnostic(paths, "E_INTERRUPTED", "用户中断启动。")
        print("[E_INTERRUPTED] 用户中断启动。", file=sys.stderr)
        return 130
    except Exception as exc:
        _append_diagnostic(paths, "E_INTERNAL", f"{type(exc).__name__}: {exc}")
        print(f"[E_INTERNAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
