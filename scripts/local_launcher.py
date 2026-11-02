"""Shared, standard-library-only launcher for local Windows and macOS installs."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from mathbank.paths import PROJECT_ROOT, project_instance_id
from mathbank.storage_access import StorageAccessError, check_startup_storage

PORT = 8000
BASE_URL = f"http://127.0.0.1:{PORT}"
ACTIVE_ROOT = PROJECT_ROOT
MAX_PORT_ATTEMPTS = 20
UNVERIFIED_PORTS: set[int] = set()
REPOSITORY = "JudgePeach/math-question-bank"
IMPORT_CHECK = (
    "import fastapi,uvicorn,sqlalchemy,greenlet,colorama,multipart,dotenv,"
    "requests,PIL,docx,lxml,defusedxml,olefile,exceptiongroup,sniffio;"
    "import pymupdf as fitz;import pdf_inspector"
)


class LauncherError(RuntimeError):
    pass


class PortBusyError(LauncherError):
    """Only a confirmed bind failure of an exited child permits another try."""


def _set_port(port: int) -> None:
    global PORT, BASE_URL
    PORT = port
    BASE_URL = f"http://127.0.0.1:{port}"


def _valid_port(value) -> bool:
    return type(value) is int and 1 <= value <= 65535


def configured_port(root: Path) -> tuple[int, bool]:
    """A remembered port is a preference; an explicit environment port is fixed."""
    override = os.environ.get("MATHBANK_PORT")
    if override is not None:
        if (not 1 <= len(override) <= 5 or not override.isascii()
                or not override.isdecimal() or not _valid_port(int(override))):
            raise LauncherError("MATHBANK_PORT 必须是 1 至 65535 的整数。")
        return int(override), True
    try:
        with (root / ".system_generated" / "launcher-settings.json").open(
            encoding="utf-8-sig"
        ) as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            raise ValueError("oversized settings")
        settings = json.loads(raw)
        if not isinstance(settings, dict) or not _valid_port(settings.get("port")):
            raise ValueError("invalid port")
        return settings["port"], False
    except FileNotFoundError:
        return 8000, False
    except (OSError, ValueError):
        print("启动端口记忆不可用，将从 8000 开始检查；原有题库和浏览器数据保持不变。")
        return 8000, False


def remember_port(root: Path, port: int) -> None:
    """Best-effort atomic cache: a failure must not undo a ready service."""
    state = root / ".system_generated"
    temporary = state / f"launcher-settings-{uuid.uuid4().hex}.tmp"
    try:
        state.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"port": port}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state / "launcher-settings.json")
    except OSError:
        print("题库已可用，但未能记住端口；请保留当前访问地址并检查程序目录的写入权限。")
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def candidate_ports(preferred: int, fixed: bool) -> list[int]:
    if fixed:
        return [preferred]
    return list(dict.fromkeys([preferred, *range(8000, 8000 + MAX_PORT_ATTEMPTS)]))[:MAX_PORT_ATTEMPTS]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read_local(route: str) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(BASE_URL + route, timeout=2) as response:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("local response too large")
        return raw


def read_json(route: str):
    # Local service requests must not go through a system/network proxy.
    return json.loads(read_local(route))


def runtime_is_busy(root: Path) -> bool:
    """Check the existing database guard; never inspect or control old PIDs."""
    if not (root / ".system_generated" / "runtime.lock").exists():
        return False
    try:
        with launcher_lock(root / ".system_generated", timeout=0, filename="runtime.lock"):
            return False
    except (LauncherError, OSError):
        return True


def _legacy_service_matches(version: dict) -> bool:
    """Reuse an older server only with this directory's held lock and token.

    No token is sent to the probed service, logged, or changed. An unverified
    old MathBank remains a conflict; it must not cause a second server start.
    """
    if not runtime_is_busy(ACTIVE_ROOT):
        return False
    try:
        # Optional positive evidence for pre-project-ID servers only. A copied
        # local token plus a busy lock alone cannot locate the current library.
        # Never read/use the PID, nor require this record for newer servers.
        with (ACTIVE_ROOT / ".system_generated" / "server-state.json").open(encoding="utf-8") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            return False
        state = json.loads(raw)
        launch_id = state.get("launch_id") if isinstance(state, dict) else None
        if (not isinstance(launch_id, str) or not re.fullmatch(r"[0-9a-f]{32}", launch_id)
                or launch_id != version.get("server_instance_id")):
            return False
        with (ACTIVE_ROOT / ".system_generated" / "local_token").open(encoding="utf-8") as handle:
            expected = handle.read(4097).strip()
        if not 16 <= len(expected) <= 4096:
            return False
        html = read_local("/").decode("utf-8")
        match = re.search(r'window\.__localToken\s*=\s*("(?:[^"\\]|\\.)*")\s*;', html)
        return bool(match and secrets.compare_digest(json.loads(match[1]).encode("utf-8"), expected.encode("utf-8")))
    except (OSError, ValueError):
        return False


def existing_service_ready() -> bool:
    try:
        version = read_json("/api/version")
        if not isinstance(version, dict) or version.get("repo") != REPOSITORY:
            return False
        legacy = "project_instance_id" not in version
        if legacy:
            if not _legacy_service_matches(version):
                UNVERIFIED_PORTS.add(PORT)
                return False
        elif version["project_instance_id"] != project_instance_id(ACTIVE_ROOT):
            if (not isinstance(version["project_instance_id"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", version["project_instance_id"])):
                UNVERIFIED_PORTS.add(PORT)
            return False
        UNVERIFIED_PORTS.add(PORT)
        health = read_json("/healthz")
        ready = (
            isinstance(health, dict) and health.get("ready") is True
            and (legacy or health.get("project_instance_id") == version["project_instance_id"])
            and isinstance(version.get("server_instance_id"), str)
            and bool(version["server_instance_id"])
            and health.get("server_instance_id") == version["server_instance_id"]
        )
        if ready:
            UNVERIFIED_PORTS.discard(PORT)
        return ready
    except (OSError, ValueError):
        return False


def confirm_port_change(previous: int, proposed: int) -> None:
    print(f"端口 {previous} 被其他应用占用。可改用 http://127.0.0.1:{proposed}。")
    print(f"草稿、选题篮和未保存的卷面信息仍保留在原地址 http://127.0.0.1:{previous}；换端口不会自动搬迁。")
    if not sys.stdin or not sys.stdin.isatty():
        raise LauncherError(
            "请在终端中运行启动器并确认换端口；也可通过 MATHBANK_PORT 指定端口。当前未更换地址。"
        )
    try:
        answer = input("确认切换并记住这个端口？输入 y 确认，其他输入取消：").strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        raise LauncherError("已取消换端口，未启动新的题库服务。")


def port_in_use() -> bool:
    try:
        # Match Uvicorn's bind semantics: POSIX permits restarting after
        # TIME_WAIT. Windows must keep exclusive ownership (no SO_REUSEADDR).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            elif hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", PORT))
        return False
    except OSError:
        return True


def open_browser() -> None:
    if os.environ.get("MATHBANK_NO_BROWSER") == "1":
        return
    try:
        if os.name == "nt":
            os.startfile(BASE_URL)
        else:
            subprocess.run(["open", BASE_URL], check=True)
    except (OSError, subprocess.SubprocessError):
        print(f"请在浏览器中打开 {BASE_URL}")


@contextlib.contextmanager
def launcher_lock(state_dir: Path, timeout: float = 60, *, filename="launcher.lock"):
    """Serialize double clicks; the file itself is never treated as stale state."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / filename).open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LauncherError("另一窗口还在准备运行环境，请等待该窗口完成后再试。")
                time.sleep(0.5)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_checked(command, root: Path, message: str) -> None:
    try:
        result = subprocess.run(command, cwd=root, check=False)
    except OSError as exc:
        raise LauncherError(f"{message}：{exc}") from exc
    if result.returncode:
        raise LauncherError(message + "，请查看上方的具体错误。")


def environment_works(python: Path, code: str, root: Path) -> bool:
    try:
        return subprocess.run(
            [str(python), "-c", code], cwd=root, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def prepare_python(root: Path) -> tuple[Path, Path | None]:
    if sys.platform != "darwin":
        # Windows BAT already selects the embedded interpreter or source venv.
        return Path(sys.executable), None
    python = root / "venv" / "bin" / "python"
    supported = "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
    if python.is_file() and environment_works(python, supported, root):
        return python, None
    backup = None
    venv = root / "venv"
    if venv.exists():
        backup = root / ".system_generated" / f"venv-python-legacy-{uuid.uuid4().hex[:8]}"
        venv.rename(backup)
    print("正在创建项目 Python 环境...")
    try:
        run_checked([sys.executable, "-m", "venv", str(venv)], root, "创建 Python 环境失败")
    except Exception:
        if venv.exists():
            venv.rename(root / ".system_generated" / f"venv-failed-{uuid.uuid4().hex[:8]}")
        if backup:
            backup.rename(venv)
        raise
    return python, backup


def ensure_dependencies(root: Path, python: Path) -> None:
    print("正在检查 Python 依赖包...")
    portable = os.environ.get("MATHBANK_PORTABLE_RUNTIME") == "1"
    if portable:
        if not environment_works(python, IMPORT_CHECK, root):
            run_checked([str(python), "-c", IMPORT_CHECK], root,
                        "便携包中的依赖不完整，请重新解压完整 Windows 包并替换同名文件")
        return
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        raise LauncherError("缺少 requirements.txt，请补齐项目文件。")
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = root / ".system_generated" / "requirements.sha256"
    try:
        installed = stamp.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        installed = ""
    if installed == digest and environment_works(python, IMPORT_CHECK, root):
        return
    print("正在安装或更新项目需要的依赖包（需要联网）...")
    run_checked([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                 "-r", str(requirements)], root, "依赖安装失败，请检查网络后重试")
    run_checked([str(python), "-c", IMPORT_CHECK], root, "依赖包仍无法正常导入")
    # This cache is a convenience, not a prerequisite for running the app.
    with contextlib.suppress(OSError):
        stamp.write_text(digest + "\n", encoding="ascii")


def wait_until_ready(child, launch_id: str, timeout: float = 60) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    detail = "服务尚未响应"
    while time.monotonic() < deadline:
        try:
            health = read_json("/healthz")
            if isinstance(health, dict) and health.get("ready") is True:
                if health.get("server_instance_id") == launch_id:
                    # Windows venv may delegate to another Python process. The
                    # responding service is sufficient; no OS PID inspection.
                    return True, "ready"
                detail = "端口上已有另一份题库服务"
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}: {exc.read(8192).decode('utf-8', errors='replace')}"
        except (OSError, ValueError) as exc:
            detail = str(exc)
        if child.poll() not in (None, 0):
            return False, f"服务进程已退出（退出码 {child.returncode}）：{detail}"
        time.sleep(0.5)
    return False, f"等待服务启动超过 {int(timeout)} 秒：{detail}"


def start_server(root: Path, python: Path) -> None:
    state_dir = root / ".system_generated"
    log = state_dir / "server.log"
    launch_id = uuid.uuid4().hex
    environment = os.environ.copy()
    environment["MATHBANK_LAUNCH_ID"] = launch_id
    command = [str(python), "-u", "-m", "uvicorn", "main:app",
               "--host", "127.0.0.1", "--port", str(PORT)]
    options = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    print(f"正在启动题库：{BASE_URL}")
    with log.open("wb") as output:
        child = subprocess.Popen(
            command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
            stdout=output, stderr=output, **options,
        )
    # Optional support information only. Never read or use it to control a PID.
    with contextlib.suppress(OSError):
        (state_dir / "server-state.json").write_text(json.dumps({
            "pid": child.pid, "launch_id": launch_id,
        }), encoding="utf-8")
    ready, detail = wait_until_ready(child, launch_id)
    if not ready:
        with contextlib.suppress(OSError):
            (state_dir / "probe.log").write_text(detail, encoding="utf-8")
        print(detail)
        print(f"详细日志：{log}")
        log_text = ""
        with contextlib.suppress(OSError):
            log_text = log.read_text(encoding="utf-8", errors="replace")
            print("\n".join(log_text.splitlines()[-80:]))
        poll = getattr(child, "poll", None)
        exited = callable(poll) and poll() not in (None, 0)
        if exited and re.search(r"\[Errno (?:48|98|10048)\].*(?:address|bind)", log_text, re.I):
            raise PortBusyError(f"端口 {PORT} 在启动时被其他应用占用。")
        raise LauncherError("题库尚未启动成功，请根据上面的具体错误处理后重试。")
    # No auto-stop on timeout: a slow server can finish and be opened next time.
    with contextlib.suppress(OSError):
        (state_dir / "probe.log").unlink(missing_ok=True)
    remember_port(root, PORT)
    print(f"题库已就绪：{BASE_URL}")
    open_browser()


def run_launcher(root: Path = PROJECT_ROOT) -> None:
    global ACTIVE_ROOT
    root = Path(root).resolve()
    ACTIVE_ROOT = root
    UNVERIFIED_PORTS.clear()
    preferred, fixed = configured_port(root)
    _set_port(preferred)
    if existing_service_ready():
        print(f"题库已经在运行：{BASE_URL}，正在打开网页。如刚更新了程序，请先在网页关闭服务再启动。")
        open_browser()
        return
    _check_startup_storage(root)
    with launcher_lock(root / ".system_generated"):
        ports = candidate_ports(preferred, fixed)
        # Find an existing copy before choosing an unused port. This also
        # recovers a missing port cache without consulting old PID records.
        for port in ports:
            _set_port(port)
            if existing_service_ready():
                remember_port(root, port)
                print(f"题库已经在运行：{BASE_URL}。如刚更新程序，请先在原网页关闭服务再启动。")
                open_browser()
                return
        if runtime_is_busy(root):
            raise LauncherError(
                "本目录的题库仍在启动、运行或关闭中；未启动第二份服务。"
                "如刚更新程序，请先在原题库网页正常关闭服务，再重新启动。"
            )
        if preferred in UNVERIFIED_PORTS:
            raise LauncherError(
                f"端口 {preferred} 上的题库服务尚未就绪或无法确认归属，未启动第二份服务。"
                "如刚更新程序，请先在原题库网页正常关闭服务，再重新启动。"
            )
        _set_port(preferred)
        unavailable = set()
        if port_in_use():
            unavailable.add(preferred)
            _choose_another_port(ports, unavailable, preferred)
        python, backup = prepare_python(root)
        ensure_dependencies(root, python)
        while True:
            if runtime_is_busy(root):
                raise LauncherError("本目录已有题库服务正在运行或启动，未启动第二份服务。请从原网页继续操作。")
            try:
                start_server(root, python)
                break
            except PortBusyError:
                previous = PORT
                unavailable.add(previous)
                _choose_another_port(ports, unavailable, previous)
        if backup:
            try:
                shutil.rmtree(backup)
            except OSError:
                print(f"旧 Python 环境备份保留在：{backup}")


def _choose_another_port(ports: list[int], unavailable: set[int], previous: int) -> None:
    for port in ports:
        if port in unavailable:
            continue
        _set_port(port)
        if not port_in_use():
            confirm_port_change(previous, port)
            return
        unavailable.add(port)
    raise LauncherError(
        f"端口 {previous} 不可用，候选端口已用尽。请先关闭占用应用，或通过 MATHBANK_PORT 指定其他端口。"
    )


def _check_startup_storage(root: Path) -> None:
    try:
        check_startup_storage(root)
    except StorageAccessError as exc:
        raise LauncherError(exc.safe_message) from exc


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="replace")
    print("本地数学题库（MathBank）启动器")
    try:
        arguments = sys.argv[1:] if argv is None else argv
        if arguments == ["--check-write-access"]:
            _check_startup_storage(PROJECT_ROOT)
            return 0
        run_launcher()
        return 0
    except (LauncherError, OSError) as exc:
        print(f"启动未完成：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
