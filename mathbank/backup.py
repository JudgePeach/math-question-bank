"""Verified full backups and restores for MathBank persistent data."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from mathbank import __version__
from mathbank.asset_security import SAFE_IMAGE_EXTENSIONS, harden_private_path
from mathbank.paths import (
    DATA_BACKUP_DIR,
    DATABASE_FILE,
    FULL_BACKUP_DIR,
    PRE_RESTORE_BACKUP_DIR,
    SYSTEM_GENERATED_DIR,
    UPLOADS_DIR,
)


BACKUP_FORMAT_VERSION = 2
SUPPORTED_BACKUP_FORMAT_VERSIONS = frozenset({1, BACKUP_FORMAT_VERSION})
DEFAULT_RETENTION = 10
MAX_BACKUP_UNCOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024
MAX_BACKUP_MEMBERS = 100_000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MEMBER_NAME_BYTES = 4096
_automatic_backup_lock = threading.Lock()
_UPLOAD_URL_PATTERN = re.compile(
    r"/static/uploads/[A-Za-z0-9][A-Za-z0-9_.\-/]*"
)
_SAFE_UPLOAD_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RuntimeLock:
    """A held cross-platform advisory lock released by :meth:`close`."""

    def __init__(self, handle, platform_name: str):
        self._handle = handle
        self._platform_name = platform_name

    def close(self) -> None:
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            _unlock_runtime_file(handle, self._platform_name)
        except OSError:
            # Closing the descriptor releases both flock and msvcrt locks; a
            # late unlock error must not misreport a completed restore.
            pass
        finally:
            handle.close()

    def __enter__(self) -> "RuntimeLock":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class RuntimeLockError(RuntimeError):
    """Raised when another MathBank server owns the runtime lock."""


def _lock_runtime_file(handle, platform_name: str) -> None:
    handle.seek(0)
    if platform_name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_runtime_file(handle, platform_name: str) -> None:
    handle.seek(0)
    if platform_name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_runtime_lock(
    lock_path: Path | None = None, *, platform_name: str | None = None
) -> RuntimeLock:
    """Acquire the lock shared by the server and destructive restore CLI."""

    target = Path(lock_path or (SYSTEM_GENERATED_DIR / "runtime.lock"))
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    harden_private_path(target)
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        handle.close()
        raise
    selected_platform = platform_name or os.name
    try:
        _lock_runtime_file(handle, selected_platform)
    except OSError as exc:
        handle.close()
        raise RuntimeLockError(
            "题库服务仍在运行；请完全关闭服务后再执行恢复。"
        ) from exc
    return RuntimeLock(handle, selected_platform)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(source_path: Path, target_path: Path) -> dict[str, Any]:
    if not source_path.is_file():
        raise FileNotFoundError(f"数据库文件不存在: {source_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path)) as source, closing(
        sqlite3.connect(target_path)
    ) as target:
        source.backup(target)
        # A source in WAL mode can copy that persistent journal setting to the
        # snapshot.  Convert the target back to a standalone database so no
        # unarchived -wal/-shm sidecar is required for recovery.
        journal_mode = str(target.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode.lower() != "delete":
            raise RuntimeError(
                f"数据库快照无法转换为独立日志模式: {journal_mode}"
            )
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"数据库快照完整性检查失败: {integrity}")
        foreign_key_errors = target.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"数据库快照包含外键异常: {foreign_key_errors[:5]}")
        schema_version = int(target.execute("PRAGMA user_version").fetchone()[0])
        table_names = {
            row[0]
            for row in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        counts = {}
        for table_name in (
            "questions",
            "question_curriculums",
            "papers",
            "paper_questions",
        ):
            if table_name in table_names:
                counts[table_name] = int(
                    target.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                )
        referenced_uploads = sorted(_database_upload_references(target))
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{target_path}{suffix}")
        if suffix == "-wal" and sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError(f"数据库快照仍依赖未归档 WAL: {sidecar}")
        sidecar.unlink(missing_ok=True)
    return {
        "schema_version": schema_version,
        "row_counts": counts,
        "referenced_upload_count": len(referenced_uploads),
        "referenced_uploads": referenced_uploads,
    }


def _upload_archive_name(reference: str, *, url_prefix: str) -> str:
    if not isinstance(reference, str) or not reference.strip():
        raise RuntimeError("插图引用为空")
    normalized = reference.strip()
    prefix = "/" + url_prefix.strip("/") + "/"
    if (
        not normalized.startswith(prefix)
        or "\\" in normalized
        or "\x00" in normalized
        or "?" in normalized
        or "#" in normalized
    ):
        raise RuntimeError(f"非法的上传插图引用: {reference}")
    relative = normalized[len(prefix) :]
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or PurePosixPath(relative).as_posix() != relative
        or any(
            part in {"", ".", "..", "tmp"}
            or part.startswith(".")
            or not _SAFE_UPLOAD_COMPONENT.fullmatch(part)
            for part in parts
        )
        or PurePosixPath(relative).suffix.lower() not in SAFE_IMAGE_EXTENSIONS
    ):
        raise RuntimeError(f"非法的上传插图引用: {reference}")
    member_name = f"uploads/{relative}"
    _validate_member_name(member_name)
    return member_name


def _database_upload_references(
    connection: sqlite3.Connection, *, url_prefix: str = "/static/uploads"
) -> set[str]:
    """Return safe raster members referenced by structured or legacy fields."""

    columns = {
        row[1]
        for row in connection.execute('PRAGMA table_info("questions")').fetchall()
    }
    if not columns:
        return set()

    references: set[str] = set()
    selected_columns = [
        name for name in ("image_paths", "content", "answer_markdown") if name in columns
    ]
    if not selected_columns:
        return references
    query_columns = ", ".join(f'"{name}"' for name in selected_columns)
    for row in connection.execute(
        f'SELECT id, {query_columns} FROM "questions"'
    ).fetchall():
        question_id = row[0]
        values_by_column = dict(zip(selected_columns, row[1:]))
        if "image_paths" in values_by_column:
            raw_value = values_by_column["image_paths"]
            try:
                image_paths = json.loads(raw_value or "[]")
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"题目 {question_id} 的 image_paths JSON 已损坏"
                ) from exc
            if not isinstance(image_paths, list):
                raise RuntimeError(f"题目 {question_id} 的 image_paths 不是数组")
            for value in image_paths:
                try:
                    references.add(
                        _upload_archive_name(value, url_prefix=url_prefix)
                    )
                except RuntimeError as exc:
                    raise RuntimeError(
                        f"题目 {question_id} 包含无效插图引用"
                    ) from exc
        for field_name in ("content", "answer_markdown"):
            text = values_by_column.get(field_name)
            if not isinstance(text, str):
                continue
            search_prefix = "/" + url_prefix.strip("/") + "/"
            pattern = (
                _UPLOAD_URL_PATTERN
                if search_prefix == "/static/uploads/"
                else re.compile(re.escape(search_prefix) + r"[A-Za-z0-9][A-Za-z0-9_.\-/]*")
            )
            for match in pattern.finditer(text):
                references.add(
                    _upload_archive_name(match.group(0), url_prefix=url_prefix)
                )
    return references


def _copy_all_files(source_dir: Path, target_dir: Path) -> int:
    """Copy a raw tree for disaster evidence, including unreferenced files."""

    target_dir.mkdir(parents=True, exist_ok=True)
    if not source_dir.exists():
        return 0
    count = 0
    for source in sorted(source_dir.rglob("*")):
        if source.is_symlink():
            raise RuntimeError(f"上传目录含符号链接，已拒绝备份: {source}")
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir)
        target = target_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    return count


def _copy_referenced_uploads(
    source_dir: Path, target_dir: Path, references: set[str]
) -> int:
    """Copy only the safe production raster files named by the database."""

    source_root = source_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    for member_name in sorted(references):
        relative = PurePosixPath(member_name).relative_to("uploads")
        source = source_root.joinpath(*relative.parts)
        current = source_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeError(f"插图路径不能经过符号链接: {source}")
        try:
            source.resolve(strict=False).relative_to(source_root)
        except ValueError as exc:
            raise RuntimeError(f"插图路径越出上传目录: {source}") from exc
        if not source.is_file():
            raise RuntimeError(f"备份缺少数据库引用的上传图片: {member_name}")
        target = target_dir.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return len(references)


def _manifest_files(payload_dir: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(payload_dir.rglob("*")):
        if not path.is_file():
            continue
        archive_name = path.relative_to(payload_dir).as_posix()
        files[archive_name] = {
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    return files


def _write_zip_atomic(
    payload_dir: Path,
    index_name: str,
    index: dict[str, Any],
    output_path: Path,
) -> None:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                index_name,
                json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for path in sorted(payload_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(payload_dir).as_posix())
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, output_path)
        output_path.chmod(0o600)
        _fsync_directory(output_path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _prune_backups(output_dir: Path, retention: int | None) -> None:
    if retention is None or retention <= 0:
        return
    backups = sorted(
        output_dir.glob("mathbank-backup-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[retention:]:
        old_backup.unlink(missing_ok=True)


def create_full_backup(
    *,
    output_dir: Path | None = None,
    database_path: Path | None = None,
    uploads_dir: Path | None = None,
    metadata_path: Path | None = None,
    retention: int | None = DEFAULT_RETENTION,
) -> Path:
    """Create an atomic, verified archive without including API secrets."""

    output_dir = (output_dir or FULL_BACKUP_DIR).resolve()
    database_path = (database_path or DATABASE_FILE).resolve()
    uploads_dir = (uploads_dir or UPLOADS_DIR).resolve()
    metadata_path = (metadata_path or (DATA_BACKUP_DIR / "custom_metadata.json")).resolve()
    if output_dir.is_relative_to(uploads_dir):
        raise RuntimeError("完整备份目录不能位于上传目录内部")
    if metadata_path.is_file() and metadata_path.name != "custom_metadata.json":
        raise RuntimeError("完整备份只允许归档 custom_metadata.json 元数据")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)

    now = dt.datetime.now(dt.timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    output_path = output_dir / f"mathbank-backup-{timestamp}.zip"

    with tempfile.TemporaryDirectory(prefix=".mathbank-backup-", dir=output_dir) as temp:
        payload_dir = Path(temp) / "payload"
        payload_dir.mkdir()
        database_info = _snapshot_database(
            database_path,
            payload_dir / "database" / DATABASE_FILE.name,
        )
        referenced_uploads = set(database_info["referenced_uploads"])
        upload_count = _copy_referenced_uploads(
            uploads_dir, payload_dir / "uploads", referenced_uploads
        )
        metadata_included = metadata_path.is_file()
        if metadata_included:
            metadata_target = payload_dir / "metadata" / metadata_path.name
            metadata_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(metadata_path, metadata_target)

        manifest = {
            "format": "mathbank-full-backup",
            "format_version": BACKUP_FORMAT_VERSION,
            "app_version": __version__,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "database": database_info,
            "upload_file_count": upload_count,
            "metadata_included": metadata_included,
            "secrets_included": False,
            "files": _manifest_files(payload_dir),
        }
        _write_zip_atomic(payload_dir, "manifest.json", manifest, output_path)

    try:
        verify_full_backup(output_path)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    _prune_backups(output_dir, retention)
    return output_path


def create_full_backup_if_due(
    *,
    minimum_interval_seconds: int = 24 * 60 * 60,
    output_dir: Path | None = None,
    retention: int = DEFAULT_RETENTION,
) -> Path | None:
    """Create at most one automatic full backup per interval."""

    output_dir = (output_dir or FULL_BACKUP_DIR).resolve()
    with _automatic_backup_lock:
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = []
        for path in output_dir.glob("mathbank-backup-*.zip"):
            try:
                existing.append((path.stat().st_mtime, path))
            except OSError:
                continue
        existing.sort(key=lambda item: item[0], reverse=True)
        now_timestamp = dt.datetime.now().timestamp()
        for modified_at, candidate in existing:
            age = now_timestamp - modified_at
            if age >= minimum_interval_seconds:
                break
            try:
                verify_full_backup(candidate)
            except Exception:
                # Never delete a user's archive here, but an invalid file must
                # not suppress creation of the next verified automatic backup.
                continue
            return None
        return create_full_backup(output_dir=output_dir, retention=retention)


def _validate_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise RuntimeError(f"备份包含不安全路径: {name}")
    if len(name.encode("utf-8")) > MAX_MEMBER_NAME_BYTES:
        raise RuntimeError("备份成员路径过长")
    canonical = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(canonical)
    parts = canonical.split("/")
    if (
        not canonical
        or path.is_absolute()
        or path.as_posix() != canonical
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        raise RuntimeError(f"备份包含不安全路径: {name}")
    return canonical


def _validated_archive_files(
    archive: zipfile.ZipFile,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_BACKUP_MEMBERS:
        raise RuntimeError("备份文件数量超过安全上限")

    total_size = 0
    collision_keys: set[str] = set()
    file_infos: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        canonical = _validate_member_name(info.filename)
        collision_key = unicodedata.normalize("NFC", canonical).casefold()
        if collision_key in collision_keys:
            raise RuntimeError(f"备份中存在重复或跨平台冲突路径: {info.filename}")
        collision_keys.add(collision_key)

        unix_mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(unix_mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise RuntimeError(f"备份包含不支持的特殊文件: {info.filename}")
        if info.flag_bits & 0x1:
            raise RuntimeError(f"备份包含加密成员，无法安全验证: {info.filename}")
        if info.file_size < 0:
            raise RuntimeError(f"备份成员大小无效: {info.filename}")
        total_size += info.file_size
        if total_size > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise RuntimeError("备份解压后体积超过安全上限")
        if info.is_dir():
            continue
        file_infos[canonical] = info

    file_names = set(file_infos)
    for name in file_names:
        parts = name.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in file_names:
                raise RuntimeError(f"备份路径同时被声明为文件和目录: {name}")
    return file_infos


def _read_member_limited(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int
) -> bytes:
    if info.file_size > limit:
        raise RuntimeError(f"备份成员超过读取上限: {info.filename}")
    with archive.open(info, "r") as source:
        data = source.read(limit + 1)
        if len(data) > limit or source.read(1):
            raise RuntimeError(f"备份成员超过读取上限: {info.filename}")
    return data


def _hash_archive_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(info, "r") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            if size > info.file_size:
                raise RuntimeError(f"备份成员实际大小异常: {info.filename}")
            digest.update(chunk)
    return size, digest.hexdigest()


def _validated_manifest(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
    file_infos = _validated_archive_files(archive)
    manifest_info = file_infos.get("manifest.json")
    if manifest_info is None:
        raise RuntimeError("备份缺少 manifest.json")
    try:
        manifest = json.loads(
            _read_member_limited(archive, manifest_info, MAX_MANIFEST_BYTES).decode(
                "utf-8"
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("备份 manifest.json 无效") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("备份清单必须是 JSON 对象")
    if manifest.get("format") != "mathbank-full-backup":
        raise RuntimeError("不是 MathBank 完整备份")
    format_version = manifest.get("format_version")
    if (
        type(format_version) is not int
        or format_version not in SUPPORTED_BACKUP_FORMAT_VERSIONS
    ):
        raise RuntimeError("备份格式版本不受支持")
    if manifest.get("secrets_included") is not False:
        raise RuntimeError("备份清单未明确排除敏感密钥")

    files = manifest.get("files")
    if not isinstance(files, dict) or len(files) > MAX_BACKUP_MEMBERS - 1:
        raise RuntimeError("备份文件清单无效")
    expected_names = set(files) | {"manifest.json"}
    if set(file_infos) != expected_names:
        raise RuntimeError("备份内容与文件清单不一致")

    database_name = f"database/{DATABASE_FILE.name}"
    if database_name not in files:
        raise RuntimeError("备份缺少数据库快照")
    upload_names = []
    metadata_names = []
    for name, expected in files.items():
        _validate_member_name(name)
        if name.startswith("uploads/"):
            upload_names.append(name)
        elif name.startswith("metadata/"):
            metadata_names.append(name)
        elif name != database_name:
            raise RuntimeError(f"备份包含未知持久化资源: {name}")
        if not isinstance(expected, dict):
            raise RuntimeError(f"备份文件清单项无效: {name}")
        expected_size = expected.get("size")
        expected_hash = expected.get("sha256")
        if (
            type(expected_size) is not int
            or expected_size < 0
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)
        ):
            raise RuntimeError(f"备份文件校验信息无效: {name}")
        if file_infos[name].file_size != expected_size:
            raise RuntimeError(f"备份文件大小不匹配: {name}")

    database_manifest = manifest.get("database")
    strict_upload_manifest = (
        format_version >= 2
        and isinstance(database_manifest, dict)
        and "referenced_uploads" in database_manifest
    )
    for name in upload_names:
        # Early v2 archives were emitted before referenced_uploads became the
        # strict-v2 discriminator and can contain the old directory sentinel.
        if name == "uploads/.gitkeep" and not strict_upload_manifest:
            continue
        relative = name.removeprefix("uploads/")
        parts = PurePosixPath(relative).parts
        if (
            not parts
            or any(
                part == "tmp"
                or part.startswith(".")
                or not _SAFE_UPLOAD_COMPONENT.fullmatch(part)
                for part in parts
            )
            or PurePosixPath(relative).suffix.lower() not in SAFE_IMAGE_EXTENSIONS
        ):
            raise RuntimeError(f"备份包含非法上传资源: {name}")

    upload_count = manifest.get("upload_file_count")
    if type(upload_count) is not int or upload_count != len(upload_names):
        raise RuntimeError("备份上传文件计数不一致")
    metadata_included = manifest.get("metadata_included")
    if type(metadata_included) is not bool or metadata_included != bool(metadata_names):
        raise RuntimeError("备份元数据声明与文件不一致")
    if metadata_names and metadata_names != ["metadata/custom_metadata.json"]:
        raise RuntimeError("备份包含非法元数据文件")
    return manifest, file_infos


def verify_full_backup(archive_path: Path) -> dict[str, Any]:
    """Verify paths, hashes, sizes and SQLite integrity without restoring."""

    archive_path = Path(archive_path).resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        manifest, file_infos = _validated_manifest(archive)
        files = manifest["files"]
        for name, expected in files.items():
            actual_size, actual_hash = _hash_archive_member(archive, file_infos[name])
            if actual_size != expected["size"]:
                raise RuntimeError(f"备份文件大小不匹配: {name}")
            if not hmac.compare_digest(actual_hash, expected["sha256"]):
                raise RuntimeError(f"备份文件校验失败: {name}")

        database_name = f"database/{DATABASE_FILE.name}"
        with tempfile.TemporaryDirectory(prefix="mathbank-verify-") as temp:
            database_copy = Path(temp) / DATABASE_FILE.name
            with archive.open(file_infos[database_name], "r") as source, database_copy.open(
                "xb"
            ) as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            with closing(sqlite3.connect(database_copy)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError(f"备份数据库损坏: {integrity}")
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"备份数据库关系异常: {violations[:5]}")
                schema_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                database_manifest = manifest.get("database")
                if not isinstance(database_manifest, dict):
                    raise RuntimeError("备份数据库清单无效")
                if database_manifest.get("schema_version") != schema_version:
                    raise RuntimeError("备份数据库版本与清单不一致")
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                row_counts = database_manifest.get("row_counts")
                required_tables = {
                    "questions",
                    "question_curriculums",
                    "papers",
                    "paper_questions",
                }
                if not isinstance(row_counts, dict) or set(row_counts) != required_tables:
                    raise RuntimeError("备份数据库行数清单无效")
                if not required_tables.issubset(table_names):
                    raise RuntimeError("备份数据库缺少必要数据表")
                for table_name, expected_count in row_counts.items():
                    if type(expected_count) is not int or expected_count < 0:
                        raise RuntimeError("备份数据库行数清单无效")
                    actual_count = int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0]
                    )
                    if actual_count != expected_count:
                        raise RuntimeError(f"备份数据库行数不匹配: {table_name}")
                referenced_uploads = _database_upload_references(connection)
                missing_uploads = referenced_uploads - set(file_infos)
                if missing_uploads:
                    preview = ", ".join(sorted(missing_uploads)[:5])
                    raise RuntimeError(f"备份缺少数据库引用的上传图片: {preview}")
                expected_reference_count = database_manifest.get(
                    "referenced_upload_count", len(referenced_uploads)
                )
                if (
                    type(expected_reference_count) is not int
                    or expected_reference_count < 0
                ):
                    raise RuntimeError("备份插图引用计数无效")
                if expected_reference_count != len(referenced_uploads):
                    raise RuntimeError("备份插图引用计数与数据库不一致")
                declared_references = database_manifest.get("referenced_uploads")
                strict_upload_manifest = (
                    manifest["format_version"] >= 2
                    and "referenced_uploads" in database_manifest
                )
                if strict_upload_manifest:
                    if (
                        not isinstance(declared_references, list)
                        or declared_references != sorted(referenced_uploads)
                    ):
                        raise RuntimeError("备份插图引用清单与数据库不一致")
                    archived_uploads = {
                        name for name in file_infos if name.startswith("uploads/")
                    }
                    if archived_uploads != referenced_uploads:
                        raise RuntimeError("备份包含未被数据库引用的上传资源")
                manifest["_verified_referenced_uploads"] = sorted(referenced_uploads)
    return manifest


def _safe_extract(
    archive: zipfile.ZipFile,
    destination: Path,
    expected_files: dict[str, dict[str, Any]],
    selected_names: set[str] | None = None,
) -> None:
    file_infos = _validated_archive_files(archive)
    if set(file_infos) != set(expected_files) | {"manifest.json"}:
        raise RuntimeError("恢复期间备份内容发生变化")
    destination.mkdir(parents=True, exist_ok=False)
    destination_root = destination.resolve()
    selected_names = selected_names or set(expected_files)
    if not selected_names.issubset(expected_files):
        raise RuntimeError("恢复文件选择超出已验证清单")
    for name in sorted(selected_names):
        expected = expected_files[name]
        info = file_infos[name]
        target = destination.joinpath(*PurePosixPath(name).parts)
        try:
            target.resolve(strict=False).relative_to(destination_root)
        except ValueError as exc:
            raise RuntimeError(f"备份解压路径越界: {name}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        try:
            with archive.open(info, "r") as source, target.open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    written += len(chunk)
                    if written > expected["size"]:
                        raise RuntimeError(f"恢复文件大小异常: {name}")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if written != expected["size"] or not hmac.compare_digest(
            digest.hexdigest(), expected["sha256"]
        ):
            target.unlink(missing_ok=True)
            raise RuntimeError(f"恢复文件校验失败: {name}")


def _absolute_path(path: Path) -> Path:
    """Return an absolute path without hiding a symlink at the final component."""

    return Path(os.path.abspath(os.fspath(path)))


def _remove_restore_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _validate_restore_target(path: Path, *, directory: bool) -> None:
    if path.is_symlink():
        raise RuntimeError(f"恢复目标不能是符号链接: {path}")
    if not path.exists():
        return
    if directory and not path.is_dir():
        raise RuntimeError(f"恢复目录目标类型异常: {path}")
    if not directory and not path.is_file():
        raise RuntimeError(f"恢复文件目标类型异常: {path}")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _check_database_can_be_replaced(database_path: Path) -> None:
    """Fail quickly when another process currently owns a write transaction."""

    try:
        with closing(sqlite3.connect(database_path, timeout=0)) as connection:
            connection.execute("PRAGMA busy_timeout=0")
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        error_code = getattr(exc, "sqlite_errorcode", None)
        if error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or "locked" in str(
            exc
        ).lower():
            raise RuntimeError(
                "数据库当前正被占用；请完全关闭题库服务后再执行恢复。"
            ) from exc
        # A malformed database is precisely when disaster recovery is needed.
    except sqlite3.DatabaseError:
        pass


def _create_raw_recovery_archive(
    *,
    output_dir: Path,
    database_path: Path,
    uploads_dir: Path,
    metadata_path: Path,
) -> Path:
    """Capture raw resources before any SQLite API can mutate WAL sidecars."""

    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_path = output_dir / f"mathbank-raw-pre-restore-{timestamp}.zip"
    with tempfile.TemporaryDirectory(prefix=".mathbank-raw-", dir=output_dir) as temp:
        payload = Path(temp) / "payload"
        payload.mkdir()
        if database_path.exists():
            target = payload / "database" / database_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(database_path, target)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database_path}{suffix}")
            _validate_restore_target(sidecar, directory=False)
            if sidecar.exists():
                target = payload / "database" / f"{database_path.name}{suffix}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sidecar, target)
        _copy_all_files(uploads_dir, payload / "uploads")
        if metadata_path.is_file():
            target = payload / "metadata" / metadata_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(metadata_path, target)
        recovery_manifest = {
            "format": "mathbank-raw-pre-restore",
            "created_at": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "capture_stage": "before-current-database-sqlite-probe",
            "files": _manifest_files(payload),
        }
        _write_zip_atomic(payload, "recovery.json", recovery_manifest, output_path)
    return output_path


def _restore_full_backup_unlocked(
    archive_path: Path,
    *,
    database_path: Path | None = None,
    uploads_dir: Path | None = None,
    metadata_path: Path | None = None,
    safety_backup_dir: Path | None = None,
) -> Path:
    """Restore a verified backup; callers must ensure the app is stopped."""

    archive_path = Path(archive_path).resolve()
    database_path = _absolute_path(database_path or DATABASE_FILE)
    uploads_dir = _absolute_path(uploads_dir or UPLOADS_DIR)
    metadata_path = _absolute_path(
        metadata_path or (DATA_BACKUP_DIR / "custom_metadata.json")
    )
    safety_backup_dir = _absolute_path(safety_backup_dir or PRE_RESTORE_BACKUP_DIR)

    _validate_restore_target(database_path, directory=False)
    _validate_restore_target(uploads_dir, directory=True)
    _validate_restore_target(metadata_path, directory=False)
    if database_path.is_relative_to(uploads_dir) or metadata_path.is_relative_to(
        uploads_dir
    ):
        raise RuntimeError("数据库或元数据文件不能位于待替换的上传目录内")
    if safety_backup_dir.is_relative_to(uploads_dir):
        raise RuntimeError("恢复前安全备份目录不能位于待替换的上传目录内")
    if archive_path.is_relative_to(uploads_dir):
        raise RuntimeError("待恢复压缩包不能位于即将被替换的上传目录内")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    uploads_dir.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    # SQLite may delete or rewrite -wal/-shm merely by opening a damaged main
    # database.  Capture byte-for-byte recovery evidence before the lock probe
    # or the verified online snapshot opens the current database in any way.
    raw_candidate = _create_raw_recovery_archive(
        output_dir=safety_backup_dir,
        database_path=database_path,
        uploads_dir=uploads_dir,
        metadata_path=metadata_path,
    )
    try:
        manifest = verify_full_backup(archive_path)
    except Exception:
        try:
            raw_candidate.unlink()
            _fsync_directory(raw_candidate.parent)
        except OSError as cleanup_error:
            print(
                "[Restore Warning] 恢复包验证失败，且候选原始恢复包清理失败 "
                f"(type={type(cleanup_error).__name__}): {raw_candidate}"
            )
        raise
    try:
        if database_path.exists():
            _check_database_can_be_replaced(database_path)
    except Exception as probe_error:
        print(
            "[Restore Warning] 当前数据库探测失败，恢复已停止；"
            f"候选原始恢复包已保留: {raw_candidate} "
            f"(type={type(probe_error).__name__})"
        )
        raise

    try:
        safety_backup = create_full_backup(
            output_dir=safety_backup_dir,
            database_path=database_path,
            uploads_dir=uploads_dir,
            metadata_path=metadata_path,
            retention=None,
        )
    except Exception as safety_error:
        safety_backup = raw_candidate
        print(
            "[Restore Warning] 当前数据库无法生成已验证安全备份；"
            f"已改为保留原始灾难恢复包: {safety_backup} "
            f"(type={type(safety_error).__name__})"
        )
    else:
        try:
            raw_candidate.unlink()
            _fsync_directory(raw_candidate.parent)
        except OSError as cleanup_error:
            print(
                "[Restore Warning] 已创建标准安全备份，但候选原始恢复包清理失败 "
                f"(type={type(cleanup_error).__name__}): {raw_candidate}"
            )
    with tempfile.TemporaryDirectory(prefix=".mathbank-restore-", dir=database_path.parent) as temp:
        extracted = Path(temp) / "extracted"
        restore_names = set(manifest["files"])
        database_manifest = manifest["database"]
        strict_upload_manifest = (
            manifest["format_version"] >= 2
            and "referenced_uploads" in database_manifest
        )
        if not strict_upload_manifest:
            restore_names = {
                name for name in restore_names if not name.startswith("uploads/")
            }
            restore_names.update(manifest["_verified_referenced_uploads"])
        with zipfile.ZipFile(archive_path, "r") as archive:
            _safe_extract(
                archive,
                extracted,
                manifest["files"],
                restore_names,
            )

        staged_database = extracted / "database" / DATABASE_FILE.name
        staged_uploads = extracted / "uploads"
        metadata_names = [
            name for name in manifest["files"] if name.startswith("metadata/")
        ]
        staged_metadata = extracted / metadata_names[0] if metadata_names else None
        token = uuid.uuid4().hex
        database_temp = database_path.with_name(
            f".{database_path.name}.restore-new-{token}"
        )
        uploads_temp = uploads_dir.with_name(
            f".{uploads_dir.name}.restore-new-{token}"
        )
        metadata_temp = metadata_path.with_name(
            f".{metadata_path.name}.restore-new-{token}"
        )
        shutil.copy2(staged_database, database_temp)
        database_temp.chmod(0o600)
        uploads_temp.mkdir(mode=0o700)
        if staged_uploads.is_dir():
            shutil.copytree(staged_uploads, uploads_temp, dirs_exist_ok=True)
        if manifest.get("metadata_included"):
            if staged_metadata is None or not staged_metadata.is_file():
                raise RuntimeError("备份声明包含元数据，但暂存文件不存在")
            shutil.copy2(staged_metadata, metadata_temp)
            metadata_temp.chmod(0o600)

        old_database = database_path.with_name(
            f".{database_path.name}.restore-old-{token}"
        )
        old_uploads = uploads_dir.with_name(f".{uploads_dir.name}.restore-old-{token}")
        old_metadata = metadata_path.with_name(
            f".{metadata_path.name}.restore-old-{token}"
        )
        wal_path = Path(f"{database_path}-wal")
        shm_path = Path(f"{database_path}-shm")
        old_wal = Path(f"{old_database}-wal")
        old_shm = Path(f"{old_database}-shm")
        for sidecar in (wal_path, shm_path):
            _validate_restore_target(sidecar, directory=False)

        originals = [
            (database_path, old_database),
            (wal_path, old_wal),
            (shm_path, old_shm),
            (uploads_dir, old_uploads),
            (metadata_path, old_metadata),
        ]
        moved_originals: list[tuple[Path, Path]] = []
        installed: list[Path] = []
        committed = False
        try:
            for current, old in originals:
                if current.exists():
                    os.replace(current, old)
                    moved_originals.append((current, old))
            os.replace(database_temp, database_path)
            installed.append(database_path)
            os.replace(uploads_temp, uploads_dir)
            installed.append(uploads_dir)
            if manifest.get("metadata_included"):
                os.replace(metadata_temp, metadata_path)
                installed.append(metadata_path)
            for parent in {
                database_path.parent,
                uploads_dir.parent,
                metadata_path.parent,
            }:
                _fsync_directory(parent)
            committed = True
        except Exception as restore_error:
            rollback_errors = []
            for target in reversed(installed):
                try:
                    _remove_restore_path(target)
                except OSError as exc:
                    rollback_errors.append(f"移除新目标 {target}: {exc}")
            for current, old in reversed(moved_originals):
                if old.exists():
                    try:
                        os.replace(old, current)
                    except OSError as exc:
                        rollback_errors.append(f"恢复旧目标 {current}: {exc}")
            if rollback_errors:
                details = "; ".join(rollback_errors)
                raise RuntimeError(
                    f"恢复失败且自动回滚未完整完成；旧数据副本已保留。{details}"
                ) from restore_error
            raise
        finally:
            if committed:
                for _current, old in moved_originals:
                    try:
                        _remove_restore_path(old)
                    except OSError:
                        # A leftover hidden old path is safer than deleting live data.
                        pass
            for staged in (database_temp, uploads_temp, metadata_temp):
                try:
                    _remove_restore_path(staged)
                except OSError:
                    pass

    return safety_backup


def restore_full_backup(
    archive_path: Path,
    *,
    database_path: Path | None = None,
    uploads_dir: Path | None = None,
    metadata_path: Path | None = None,
    safety_backup_dir: Path | None = None,
    runtime_lock_path: Path | None = None,
) -> Path:
    """Restore while holding the same process lock as every server instance."""

    with acquire_runtime_lock(runtime_lock_path):
        return _restore_full_backup_unlocked(
            archive_path,
            database_path=database_path,
            uploads_dir=uploads_dir,
            metadata_path=metadata_path,
            safety_backup_dir=safety_backup_dir,
        )
