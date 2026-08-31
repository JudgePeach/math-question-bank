"""Verified, app-local runtime components used by optional MathBank features."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from mathbank.paths import SYSTEM_GENERATED_DIR


PANDOC_VERSION = "3.10.2"
PANDOC_RUNTIME_DIR = SYSTEM_GENERATED_DIR / "runtime" / "pandoc"
PANDOC_DOWNLOAD_DIR = PANDOC_RUNTIME_DIR / "downloads"
PANDOC_INSTALL_LOG = PANDOC_RUNTIME_DIR / "install.log"
PANDOC_MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
PANDOC_MAX_EXTRACTED_BYTES = 320 * 1024 * 1024

PANDOC_ASSETS: dict[str, dict[str, Any]] = {
    "windows-x86_64": {
        "filename": "pandoc-3.10.2-windows-x86_64.zip",
        "sha256": "52487faaa63f8cef5363d5a771097da001228d61c6f44f32ed41b27a98c0278c",
        "size": 41_677_120,
        "binary": "pandoc.exe",
    },
    "macos-arm64": {
        "filename": "pandoc-3.10.2-arm64-macOS.zip",
        "sha256": "a30bd546062f0b29c25f45a71f951b7a1cf4f998d5b43974ea2c2416133f2e99",
        "size": 41_747_373,
        "binary": "pandoc",
    },
    "macos-x86_64": {
        "filename": "pandoc-3.10.2-x86_64-macOS.zip",
        "sha256": "437d378af72e9648f6fb42c170031218a3c2f31cf5089234cf2d0413f91481d0",
        "size": 26_101_017,
        "binary": "pandoc",
    },
}


class PandocInstallError(RuntimeError):
    """Raised when the managed Pandoc component cannot be installed safely."""


def _platform_key() -> str | None:
    machine = platform.machine().lower()
    if os.name == "nt" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    if sys_platform() == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "macos-arm64"
        if machine in {"amd64", "x86_64"}:
            return "macos-x86_64"
    return None


def sys_platform() -> str:
    """Small seam for platform-focused tests."""
    import sys

    return sys.platform


def _asset_for_current_platform() -> tuple[str, dict[str, Any]]:
    key = _platform_key()
    if not key or key not in PANDOC_ASSETS:
        raise PandocInstallError("Pandoc 自动安装暂不支持当前系统或处理器架构。")
    return key, PANDOC_ASSETS[key]


def _managed_binary() -> Path | None:
    try:
        key, asset = _asset_for_current_platform()
    except PandocInstallError:
        return None
    candidate = PANDOC_RUNTIME_DIR / PANDOC_VERSION / key / asset["binary"]
    return candidate if candidate.is_file() else None


def _pandoc_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    configured = os.getenv("MATHBANK_PANDOC_PATH", "").strip()
    if configured:
        candidates.append(("configured", Path(configured)))

    managed = _managed_binary()
    if managed:
        candidates.append(("managed", managed))

    found = shutil.which("pandoc")
    if found:
        candidates.append(("system", Path(found)))

    common = [
        Path("/opt/homebrew/bin/pandoc"),
        Path("/usr/local/bin/pandoc"),
    ]
    if os.name == "nt":
        for env_name in ("ProgramFiles", "LOCALAPPDATA"):
            base = os.getenv(env_name, "").strip()
            if base:
                common.append(Path(base) / "Pandoc" / "pandoc.exe")
        common.append(Path(r"C:\Program Files\Pandoc\pandoc.exe"))
    candidates.extend(("system", item) for item in common)

    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, candidate in candidates:
        try:
            identity = os.path.normcase(os.path.realpath(candidate))
        except OSError:
            identity = os.path.normcase(os.fspath(candidate))
        if identity not in seen:
            seen.add(identity)
            unique.append((source, candidate))
    return unique


def _probe_pandoc(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        completed = subprocess.run(
            [os.fspath(path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    first_line = completed.stdout.decode("utf-8", errors="replace").splitlines()
    return first_line[0].strip() if first_line else "pandoc"


def find_usable_pandoc() -> tuple[Path, str, str] | None:
    """Return a working Pandoc path, its source and version line."""
    for source, candidate in _pandoc_candidates():
        version = _probe_pandoc(candidate)
        if version:
            return candidate.resolve(), source, version
    return None


def pandoc_status() -> dict[str, Any]:
    resolved = find_usable_pandoc()
    platform_key = _platform_key()
    if resolved:
        path, source, version = resolved
        return {
            "status": "ready",
            "available": True,
            "source": source,
            "version": version,
            "path": os.fspath(path),
            "platform": platform_key,
        }
    supported = bool(platform_key and platform_key in PANDOC_ASSETS)
    asset = PANDOC_ASSETS.get(platform_key or "", {})
    return {
        "status": "missing" if supported else "unsupported",
        "available": False,
        "source": None,
        "version": None,
        "path": None,
        "platform": platform_key,
        "download_size": asset.get("size"),
    }


def _source_urls(filename: str) -> tuple[tuple[str, str], ...]:
    official = (
        f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/{filename}"
    )
    sourceforge = (
        "https://sourceforge.net/projects/pandoc.mirror/files/"
        f"{PANDOC_VERSION}/{filename}/download"
    )
    return (("official", official), ("sourceforge", sourceforge))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_install_log(message: str) -> None:
    PANDOC_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with PANDOC_INSTALL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _download(
    url: str,
    destination: Path,
    *,
    expected_size: int,
    progress: Callable[[int], None],
) -> None:
    received = destination.stat().st_size if destination.exists() else 0
    last_error: Exception | None = None
    for _connection_attempt in range(5):
        if received == expected_size:
            return
        headers = {"User-Agent": f"MathBank/{PANDOC_VERSION} Pandoc installer"}
        if received:
            headers["Range"] = f"bytes={received}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                status = int(getattr(response, "status", response.getcode()))
                if received and status != 206:
                    # This server ignored Range. Restart this connection safely
                    # instead of appending a second complete archive.
                    received = 0
                    destination.write_bytes(b"")
                content_length = response.headers.get("Content-Length")
                if content_length and received + int(content_length) > PANDOC_MAX_ARCHIVE_BYTES:
                    raise PandocInstallError("Pandoc 下载文件大小异常。")
                mode = "ab" if received else "wb"
                with destination.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > PANDOC_MAX_ARCHIVE_BYTES:
                            raise PandocInstallError("Pandoc 下载文件超过允许大小。")
                        output.write(chunk)
                        progress(min(88, max(1, int(received * 88 / max(expected_size, 1)))))
        except (
            OSError,
            urllib.error.URLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ) as exc:
            last_error = exc
            received = destination.stat().st_size if destination.exists() else 0
            if received > expected_size:
                raise PandocInstallError("Pandoc 下载文件大小异常。") from exc
            continue
        if received == expected_size:
            return
        if received > expected_size:
            raise PandocInstallError("Pandoc 下载文件大小异常。")

    detail = f"：{last_error}" if last_error else ""
    raise PandocInstallError(
        f"Pandoc 下载不完整（应为 {expected_size} 字节，实际 {received} 字节）{detail}"
    )


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not members or len(members) > 20:
        raise PandocInstallError("Pandoc 压缩包文件数量异常。")
    total = 0
    safe: list[zipfile.ZipInfo] = []
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
            raise PandocInstallError("Pandoc 压缩包包含不安全路径。")
        mode = (member.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            # Official macOS archives include optional pandoc-lua and
            # pandoc-server links. MathBank only invokes the real pandoc
            # executable, so omit every link instead of materialising it.
            continue
        total += member.file_size
        if total > PANDOC_MAX_EXTRACTED_BYTES:
            raise PandocInstallError("Pandoc 压缩包解压体积异常。")
        safe.append(member)
    return safe


def _smoke_pandoc(binary: Path) -> None:
    version = _probe_pandoc(binary)
    if not version:
        raise PandocInstallError("Pandoc 可执行文件无法启动。")
    with tempfile.TemporaryDirectory(prefix="mathbank_pandoc_smoke_") as temp_dir:
        temp = Path(temp_dir)
        source = temp / "formula.md"
        output = temp / "formula.docx"
        source.write_text("$x^2+\\frac{1}{2}$\n", encoding="utf-8")
        try:
            completed = subprocess.run(
                [
                    os.fspath(binary),
                    "-f",
                    "markdown+tex_math_dollars",
                    "-t",
                    "docx",
                    os.fspath(source),
                    "-o",
                    os.fspath(output),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PandocInstallError("Pandoc Word 公式能力验证失败。") from exc
        if completed.returncode != 0 or not output.is_file():
            raise PandocInstallError("Pandoc 未能生成 Word 验证文档。")
        try:
            with zipfile.ZipFile(output) as docx:
                document_xml = docx.read("word/document.xml")
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise PandocInstallError("Pandoc 生成的 Word 验证文档无效。") from exc
        if b"oMath" not in document_xml:
            raise PandocInstallError("Pandoc 未生成 Word 可编辑公式。")


def _install_pandoc(progress: Callable[[int, str, str | None], None]) -> Path:
    key, asset = _asset_for_current_platform()
    PANDOC_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = str(asset["filename"])
    archive_path: Path | None = None
    errors: list[str] = []

    for source_name, url in _source_urls(filename):
        for attempt in range(1, 3):
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{filename}.", suffix=".partial", dir=PANDOC_DOWNLOAD_DIR
            )
            os.close(fd)
            candidate = Path(temp_name)
            try:
                progress(0, "downloading", source_name)
                _append_install_log(f"download source={source_name} attempt={attempt} url={url}")
                _download(
                    url,
                    candidate,
                    expected_size=int(asset["size"]),
                    progress=lambda value: progress(value, "downloading", source_name),
                )
                if _sha256(candidate) != asset["sha256"]:
                    raise PandocInstallError("Pandoc 下载文件 SHA-256 校验失败。")
                archive_path = candidate
                break
            except (OSError, urllib.error.URLError, ValueError, PandocInstallError) as exc:
                errors.append(f"{source_name} attempt {attempt}: {type(exc).__name__}: {exc}")
                _append_install_log(errors[-1])
                candidate.unlink(missing_ok=True)
        if archive_path:
            break

    if not archive_path:
        raise PandocInstallError("Pandoc 官方与备用下载线路均不可用。")

    final_dir = PANDOC_RUNTIME_DIR / PANDOC_VERSION / key
    staging = PANDOC_RUNTIME_DIR / PANDOC_VERSION / f".{key}.{uuid.uuid4().hex}.staging"
    prepared: Path | None = None
    backup: Path | None = None
    try:
        progress(90, "verifying", None)
        staging.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive)
            archive.extractall(staging, members=members)
        binaries = [path for path in staging.rglob(str(asset["binary"])) if path.is_file()]
        if len(binaries) != 1:
            raise PandocInstallError("Pandoc 压缩包中的可执行文件异常。")
        binary = binaries[0]
        if os.name != "nt":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _smoke_pandoc(binary)
        metadata = {
            "component": "pandoc",
            "version": PANDOC_VERSION,
            "platform": key,
            "sha256": asset["sha256"],
            "binary": asset["binary"],
        }
        (binary.parent / "component.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        extracted_root = binary.parent
        prepared = staging.parent / f".{key}.{uuid.uuid4().hex}.prepared"
        os.replace(extracted_root, prepared)
        if final_dir.exists():
            backup = staging.parent / f".{key}.{uuid.uuid4().hex}.backup"
            os.replace(final_dir, backup)
        try:
            os.replace(prepared, final_dir)
        except Exception:
            if backup and backup.exists() and not final_dir.exists():
                os.replace(backup, final_dir)
            raise
        if backup and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        progress(100, "ready", None)
        _append_install_log(f"installed version={PANDOC_VERSION} platform={key}")
        return final_dir / str(asset["binary"])
    finally:
        archive_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        if prepared:
            shutil.rmtree(prepared, ignore_errors=True)


class PandocInstallManager:
    """One-install-at-a-time background manager with pollable progress."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mathbank-pandoc")
        self._state: dict[str, Any] = {
            "task_id": None,
            "status": "idle",
            "progress": 0,
            "stage": None,
            "source": None,
            "message": None,
            "error": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def ensure(self) -> dict[str, Any]:
        ready = pandoc_status()
        if ready["available"]:
            return {**self.snapshot(), **ready, "status": "ready", "progress": 100}
        with self._lock:
            if self._state["status"] in {"queued", "downloading", "verifying"}:
                return dict(self._state)
            task_id = uuid.uuid4().hex
            self._state = {
                "task_id": task_id,
                "status": "queued",
                "progress": 0,
                "stage": "queued",
                "source": None,
                "message": "正在准备 Word 可编辑公式组件…",
                "error": None,
            }
            self._executor.submit(self._run, task_id)
            return dict(self._state)

    def _progress(self, task_id: str, value: int, stage: str, source: str | None) -> None:
        with self._lock:
            if self._state.get("task_id") != task_id:
                return
            source_message = ""
            if source == "official":
                source_message = "（Pandoc 官方）"
            elif source == "sourceforge":
                source_message = "（备用线路 SourceForge）"
            self._state.update(
                status=stage,
                stage=stage,
                progress=max(0, min(100, int(value))),
                source=source,
                message=(
                    f"正在下载 Word 可编辑公式组件 {source_message}…"
                    if stage == "downloading"
                    else "正在校验并安装 Word 可编辑公式组件…"
                ),
            )

    def _run(self, task_id: str) -> None:
        try:
            binary = _install_pandoc(
                lambda value, stage, source: self._progress(task_id, value, stage, source)
            )
            with self._lock:
                if self._state.get("task_id") == task_id:
                    self._state.update(
                        status="completed",
                        stage="ready",
                        progress=100,
                        path=os.fspath(binary),
                        message="Word 可编辑公式组件已安装完成。",
                        error=None,
                    )
        except Exception as exc:
            _append_install_log(f"install failed: {type(exc).__name__}: {exc}")
            with self._lock:
                if self._state.get("task_id") == task_id:
                    self._state.update(
                        status="error",
                        stage="error",
                        message="Word 可编辑公式组件安装失败。",
                        error=str(exc),
                    )


PANDOC_INSTALL_MANAGER = PandocInstallManager()
