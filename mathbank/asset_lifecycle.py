"""Recoverable upload retirement shared by writers, cleanup and image reads.

Retained files have no automatic expiry.  The registry is configured on every
startup; relative paths and integrity metadata persist below the private store.
"""

from __future__ import annotations

import hashlib
import functools
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

import anyio
from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles


ASSET_LIFECYCLE_LOCK = threading.RLock()
_STORES: dict[Path, tuple[Path, Path]] = {}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class AssetLifecycleError(ValueError):
    """A retention operation could not be completed without risking data."""


def serialize_asset_lifecycle(function):
    """Keep synchronous question writes and file lifecycle operations atomic."""

    @functools.wraps(function)
    def serialized(*args, **kwargs):
        with ASSET_LIFECYCLE_LOCK:
            return function(*args, **kwargs)

    return serialized


def _reject_links_below(anchor: Path, path: Path) -> None:
    candidate = anchor
    if candidate.is_symlink():
        raise AssetLifecycleError("图片保留目录不能经过符号链接。")
    for part in path.relative_to(anchor).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise AssetLifecycleError("图片保留目录不能经过符号链接。")


def register_asset_store(uploads_dir, recovery_dir) -> None:
    """Register trusted, disjoint roots without touching any upload file."""

    uploads = Path(uploads_dir).absolute()
    recovery = Path(recovery_dir).absolute()
    anchor = Path(os.path.commonpath([uploads, recovery]))
    _reject_links_below(anchor, uploads)
    _reject_links_below(anchor, recovery)
    uploads = uploads.resolve()
    recovery = recovery.resolve()
    if uploads == recovery or uploads in recovery.parents or recovery in uploads.parents:
        raise AssetLifecycleError("图片保留目录必须独立于公开上传目录。")
    with ASSET_LIFECYCLE_LOCK:
        _STORES[uploads] = (recovery, anchor.resolve())


def _contained(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or not relative.parts or any(
        not _SAFE_COMPONENT.fullmatch(part) or part in {".", ".."}
        for part in relative.parts
    ):
        raise AssetLifecycleError("图片保留路径无效。")
    if root.is_symlink():
        raise AssetLifecycleError("图片保留目录不能经过符号链接。")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise AssetLifecycleError("图片保留路径不能经过符号链接。")
    return candidate


def _store_paths(root: Path, relative: Path):
    registration = _STORES.get(root)
    if registration is None:
        return None
    store, anchor = registration
    _reject_links_below(anchor, root)
    _reject_links_below(anchor, store)
    retained = _contained(store, Path("files") / relative)
    metadata = _contained(store, Path("metadata") / Path(str(relative) + ".json"))
    return retained, metadata


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _sync_directory(path: Path) -> None:
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_metadata(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".retaining-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _exclusive_move(source: Path, destination: Path) -> None:
    """Move with an exclusive destination and no temporary byte duplication.

    If hard links are unsupported, the original remains untouched.  A crash
    between link/unlink leaves two names for the same bytes, which is safe.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, destination, follow_symlinks=False)
    _sync_directory(destination.parent)
    try:
        source.unlink()
    except OSError:
        # Keep both names: deleting either is unnecessary for data safety.
        raise


def quarantine_asset(reference, *, uploads_dir, url_prefix, reason) -> bool:
    """Retire an image while keeping its original URL recoverable indefinitely."""

    from mathbank.asset_security import resolve_upload_asset

    with ASSET_LIFECYCLE_LOCK:
        root = Path(uploads_dir).resolve()
        candidate = resolve_upload_asset(
            reference, uploads_dir=root, url_prefix=url_prefix, require_file=False
        )
        relative = candidate.relative_to(root)
        paths = _store_paths(root, relative)
        if paths is None:
            raise AssetLifecycleError("图片保留目录尚未配置，已保留原图。")
        retained, metadata = paths
        if not candidate.is_file():
            return False
        checksum = _digest(candidate)
        size = candidate.stat().st_size
        if retained.exists():
            if not retained.is_file() or retained.stat().st_size != size or _digest(retained) != checksum:
                raise AssetLifecycleError("图片保留路径已存在不同内容，已保留原图。")
        values = {
            "schema_version": 1,
            "relative_path": relative.as_posix(),
            "original_url": "/" + str(url_prefix).strip("/") + "/" + relative.as_posix(),
            "reason": str(reason)[:256],
            "retained_at": time.time(),
            "size": size,
            "sha256": checksum,
        }
        try:
            # Metadata is durable before the original name can disappear.
            _write_metadata(metadata, values)
            if retained.exists():
                candidate.unlink()  # Identical bytes already retained above.
            else:
                _exclusive_move(candidate, retained)
        except OSError as exc:
            raise AssetLifecycleError("图片保留未完成，已有图片副本未删除。") from exc
        return True


def restore_asset(candidate_path, *, uploads_dir) -> bool:
    """Restore only a registered, intact image to its original contained path."""

    from mathbank.asset_security import SAFE_IMAGE_EXTENSIONS

    with ASSET_LIFECYCLE_LOCK:
        root = Path(uploads_dir).resolve()
        candidate = Path(candidate_path)
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise AssetLifecycleError("待恢复图片越出了上传目录。") from exc
        candidate = _contained(root, relative)
        if candidate.suffix.lower() not in SAFE_IMAGE_EXTENSIONS:
            raise AssetLifecycleError("待恢复文件不是安全图片类型。")
        if candidate.exists():
            if not candidate.is_file():
                raise AssetLifecycleError("待恢复图片路径不是普通文件。")
            return False
        paths = _store_paths(root, relative)
        if paths is None:
            return False
        retained, metadata = paths
        if not retained.exists():
            return False
        if not retained.is_file() or not metadata.is_file() or metadata.stat().st_size > 16384:
            raise AssetLifecycleError("保留图片或校验记录无效。")
        try:
            values = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AssetLifecycleError("保留图片校验记录无法读取。") from exc
        if not isinstance(values, dict) or (
            values.get("schema_version") != 1
            or values.get("relative_path") != relative.as_posix()
            or values.get("size") != retained.stat().st_size
            or values.get("sha256") != _digest(retained)
        ):
            raise AssetLifecycleError("保留图片完整性校验失败，未覆盖任何文件。")
        try:
            _exclusive_move(retained, candidate)
        except OSError as exc:
            raise AssetLifecycleError("图片恢复未完成，已有图片副本未删除。") from exc
        try:
            os.utime(candidate, None)
        except OSError:
            pass  # Restored bytes are already safe; mtime is only a grace period.
        return True


class RetainedUploadStaticFiles(StaticFiles):
    """Serve upload bytes under the lifecycle lock, restoring retired URLs."""

    def __init__(self, *args, uploads_dir, url_prefix, **kwargs):
        super().__init__(*args, **kwargs)
        self.uploads_dir = Path(uploads_dir).resolve()
        self.url_prefix = str(url_prefix).strip("/")
        self.upload_static_prefix = self.url_prefix.partition("/")[2] + "/"

    def _upload_response(self, path, scope):
        from mathbank.asset_security import AssetSecurityError, resolve_upload_asset
        from starlette.exceptions import HTTPException

        if scope["method"] not in {"GET", "HEAD"}:
            raise HTTPException(status_code=405)
        reference = "/" + self.url_prefix + "/" + path[len(self.upload_static_prefix):]
        with ASSET_LIFECYCLE_LOCK:
            try:
                candidate = resolve_upload_asset(
                    reference, uploads_dir=self.uploads_dir, url_prefix=self.url_prefix
                )
                with candidate.open("rb") as stream:
                    stat_result = os.fstat(stream.fileno())
                    content = stream.read() if scope["method"] == "GET" else b""
            except (AssetSecurityError, AssetLifecycleError, OSError):
                raise HTTPException(status_code=404)
            file_response = FileResponse(candidate, stat_result=stat_result)
            if self.is_not_modified(file_response.headers, Headers(scope=scope)):
                return NotModifiedResponse(file_response.headers)
            return Response(content, media_type=file_response.media_type, headers=dict(file_response.headers))

    async def get_response(self, path, scope):
        if path.startswith(self.upload_static_prefix):
            return await anyio.to_thread.run_sync(self._upload_response, path, scope)
        return await super().get_response(path, scope)
