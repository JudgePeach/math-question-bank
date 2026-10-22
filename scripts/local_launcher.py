"""Shared, standard-library-only launcher for local Windows and macOS installs."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from mathbank.storage_access import StorageAccessError, check_startup_storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORT = 8000
BASE_URL = f"http://127.0.0.1:{PORT}"
REPOSITORY = "JudgePeach/math-question-bank"
IMPORT_CHECK = (
    "import fastapi,uvicorn,sqlalchemy,greenlet,colorama,multipart,dotenv,"
    "requests,PIL,docx,lxml,defusedxml,olefile,exceptiongroup,sniffio;"
    "import pymupdf as fitz;import pdf_inspector"
)


class LauncherError(RuntimeError):
    pass


def read_json(route: str):
    # Local service requests must not go through a system/network proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(BASE_URL + route, timeout=2) as response:
        return json.loads(response.read(1024 * 1024))


def existing_service_ready() -> bool:
    try:
        version = read_json("/api/version")
        health = read_json("/healthz")
        return (
            isinstance(version, dict) and version.get("repo") == REPOSITORY
            and isinstance(health, dict) and health.get("ready") is True
        )
    except (OSError, ValueError):
        return False


def port_in_use() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


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
def launcher_lock(state_dir: Path, timeout: float = 60):
    """Serialize double clicks; the file itself is never treated as stale state."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "launcher.lock").open("a+b") as handle:
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
        with contextlib.suppress(OSError):
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))
        raise LauncherError("题库尚未启动成功，请根据上面的具体错误处理后重试。")
    # No auto-stop on timeout: a slow server can finish and be opened next time.
    with contextlib.suppress(OSError):
        (state_dir / "probe.log").unlink(missing_ok=True)
    print("题库已就绪。")
    open_browser()


def run_launcher(root: Path = PROJECT_ROOT) -> None:
    if existing_service_ready():
        print("题库已经在运行，正在打开网页。如刚更新了程序，请先在网页关闭服务再启动。")
        open_browser()
        return
    _check_startup_storage(root)
    with launcher_lock(root / ".system_generated"):
        if existing_service_ready():
            open_browser()
            return
        if port_in_use():
            raise LauncherError("端口 8000 已被占用，或题库尚未就绪；请稍后重试，或关闭占用该端口的程序。")
        python, backup = prepare_python(root)
        ensure_dependencies(root, python)
        start_server(root, python)
        if backup:
            try:
                shutil.rmtree(backup)
            except OSError:
                print(f"旧 Python 环境备份保留在：{backup}")


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
