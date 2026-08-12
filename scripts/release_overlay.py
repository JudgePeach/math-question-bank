"""Safely reconcile a Release extracted over an existing installation."""

import argparse
import errno
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


PLATFORMS = {"macos", "windows-x64"}
MANIFEST_NAME = "RELEASE-MANIFEST.json"
STATE_DIRECTORY = ".system_generated"
INSTALLED_MANIFEST_NAME = "installed-release-manifest.json"
INSTALLED_DIGEST_NAME = "installed-release-manifest.sha256"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HARD_PROTECTED_PATHS = frozenset(map(PurePosixPath, (
    ".env", ".system_generated", "data_backup", "math_question_bank.db",
    "math_question_bank.db-wal", "math_question_bank.db-shm",
    "static/uploads", "venv",
)))
HISTORICAL_TOMBSTONES = frozenset({
    ".DS_Store", "build_release.py", "database.py", "migrate_choice_parentheses.py",
    "migrate_fillin.py", "paper_helper.py", "search_questions.py", "sync_helper.py",
    "static/.DS_Store",
    "static/test_uploads/tmp/pdf_page_test-force-ocr-task_0.png",
})
REQUIRED_FILES = {
    "macos": {"main.py", "scripts/release_overlay.py", "启动题库系统.command"},
    "windows-x64": {
        "main.py", "scripts/release_overlay.py", "启动题库系统.bat", "python/python.exe",
    },
}
DATABASE_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite-wal", ".sqlite-shm",
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
)


class ReleaseOverlayError(RuntimeError):
    pass


def _require(condition, message):
    if not condition:
        raise ReleaseOverlayError(message)


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value, label):
    valid = isinstance(value, str) and value and "\\" not in value and "\0" not in value
    _require(valid, f"{label} contains an invalid path")
    relative = PurePosixPath(value)
    safe = not relative.is_absolute() and relative.as_posix() == value
    safe = safe and not any(
        part in {"", ".", ".."} or ":" in part for part in relative.parts
    )
    _require(safe, f"{label} contains an unsafe path: {value!r}")
    return relative


def _path_set(values, label):
    _require(isinstance(values, list) and values, f"{label} is invalid")
    paths = {_relative_path(value, label) for value in values}
    _require(
        len({path.as_posix().casefold() for path in paths}) == len(values),
        f"{label} contains a duplicate path",
    )
    return frozenset(paths)


def _under(relative, roots):
    return any(relative == root or root in relative.parents for root in roots)


def _same_location(path, resolved):
    return os.path.normcase(os.fspath(path.absolute())) == os.path.normcase(
        os.fspath(resolved)
    )


def _has_local_parent(root, target):
    try:
        resolved = target.parent.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return _same_location(target.parent, resolved)


def _is_protected(relative, extra=()):
    if len(relative.parts) == 1 and relative.name.lower().endswith(DATABASE_SUFFIXES):
        return True
    folded = PurePosixPath(*(part.casefold() for part in relative.parts))
    roots = {
        PurePosixPath(*(part.casefold() for part in path.parts))
        for path in (*HARD_PROTECTED_PATHS, *extra)
    }
    return _under(folded, roots)


def _read_regular(path, label):
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseOverlayError(f"{label} is missing: {path}") from exc
    _require(
        stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
        f"{label} is not a regular file",
    )
    _require(metadata.st_size <= MAX_MANIFEST_BYTES, f"{label} is unexpectedly large")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseOverlayError(f"cannot read {label}") from exc


def _load_manifest(path, platform, label):
    content = _read_regular(path, label)
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseOverlayError(f"{label} is not valid UTF-8 JSON") from exc
    _require(
        isinstance(manifest, dict) and manifest.get("schema_version") == 1,
        f"{label} schema is unsupported",
    )
    _require(manifest.get("platform") == platform, f"{label} platform does not match")
    _require(
        isinstance(manifest.get("app_version"), str) and manifest["app_version"],
        f"{label} has no valid app version",
    )
    overlay = manifest.get("overlay")
    _require(
        isinstance(overlay, dict) and overlay.get("schema_version") == 1,
        f"{label} overlay policy is unsupported",
    )
    managed = _path_set(overlay.get("managed_roots"), f"{label} managed_roots")
    protected = _path_set(overlay.get("protected_paths"), f"{label} protected_paths")
    declared = {path.as_posix().casefold() for path in protected}
    required = {path.as_posix().casefold() for path in HARD_PROTECTED_PATHS}
    _require(required.issubset(declared), f"{label} omits a required protected path")
    managed_names = {path.as_posix() for path in managed}
    required_roots = {"scripts"}
    if platform == "windows-x64":
        required_roots.add("python")
    _require(required_roots.issubset(managed_names), f"{label} omits a managed root")

    raw_entries = manifest.get("files")
    _require(
        isinstance(raw_entries, list) and len(raw_entries) <= 100_000,
        f"{label} file list is invalid",
    )
    entries = {}
    folded_names = set()
    for raw in raw_entries:
        _require(isinstance(raw, dict), f"{label} contains an invalid file entry")
        relative = _relative_path(raw.get("path"), label)
        name = relative.as_posix()
        size, expected_hash = raw.get("size"), raw.get("sha256")
        _require(
            name != MANIFEST_NAME and _under(relative, managed),
            f"{label} contains an unmanaged path: {name}",
        )
        _require(
            not _is_protected(relative, protected) or name == "static/uploads/.gitkeep",
            f"{label} lists a protected path: {name}",
        )
        _require(
            isinstance(size, int) and not isinstance(size, bool) and size >= 0,
            f"{label} has an invalid size for {name}",
        )
        _require(
            isinstance(expected_hash, str) and SHA256_PATTERN.fullmatch(expected_hash),
            f"{label} has an invalid SHA-256 for {name}",
        )
        _require(name.casefold() not in folded_names, f"{label} has a duplicate path")
        folded_names.add(name.casefold())
        entries[name] = (size, expected_hash)
    _require(
        REQUIRED_FILES[platform].issubset(entries),
        f"{label} omits a required application file",
    )
    return content, entries, protected


def _verify_files(root, entries, names=None):
    for name in entries if names is None else names:
        expected_size, expected_hash = entries[name]
        target = root.joinpath(*PurePosixPath(name).parts)
        _require(_has_local_parent(root, target), f"{name} has a linked or unsafe parent")
        try:
            metadata = target.lstat()
        except OSError as exc:
            raise ReleaseOverlayError(f"release file is missing: {name}") from exc
        _require(
            stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
            f"release file is not regular: {name}",
        )
        _require(metadata.st_size == expected_size, f"release file size mismatch: {name}")
        _require(
            hmac.compare_digest(_sha256_file(target), expected_hash),
            f"release file SHA-256 mismatch: {name}",
        )


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _change_lock(handle, unlock=False):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl

        mode = fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(handle.fileno(), mode)


@contextmanager
def _runtime_lock(root):
    state = root / STATE_DIRECTORY
    _require(
        not state.is_symlink() and (not state.exists() or state.is_dir()),
        "release state path is not a regular directory",
    )
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseOverlayError("cannot create the release state directory") from exc
    _require(_same_location(state, state.resolve()), "release state path is linked")
    lock_path = state / "runtime.lock"
    try:
        metadata = lock_path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as exc:
        raise ReleaseOverlayError("cannot inspect the runtime lock") from exc
    if metadata is not None:
        _require(
            stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
            "runtime lock path is not a regular file",
        )
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ReleaseOverlayError("cannot open the runtime lock") from exc
    try:
        try:
            os.chmod(lock_path, stat.S_IRUSR | stat.S_IWUSR)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            _change_lock(handle)
        except OSError as exc:
            raise ReleaseOverlayError(
                "题库服务仍在运行；请完全关闭服务后再执行覆盖升级。"
            ) from exc
        yield state
    finally:
        try:
            _change_lock(handle, unlock=True)
        except OSError:
            pass
        handle.close()


def _read_digest(state):
    path = state / INSTALLED_DIGEST_NAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = _read_regular(path, "installed release digest").decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ReleaseOverlayError("installed release digest is not ASCII") from exc
    return value if SHA256_PATTERN.fullmatch(value) else None


def _delete_owned(root, relative, expected, protected):
    if _is_protected(relative, protected):
        return False
    target = root.joinpath(*relative.parts)
    if not _has_local_parent(root, target):
        return False
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ReleaseOverlayError(f"cannot inspect obsolete file: {relative}") from exc
    if stat.S_ISDIR(metadata.st_mode):
        return False
    if expected is not None:
        if not stat.S_ISREG(metadata.st_mode):
            return False
        size, expected_hash = expected
        if metadata.st_size != size or not hmac.compare_digest(
            _sha256_file(target), expected_hash
        ):
            return False
    try:
        target.unlink()
    except OSError as exc:
        raise ReleaseOverlayError(f"cannot delete obsolete file: {relative}") from exc
    return True


def _reconcile_old(root, previous, current_entries, current_protected, platform):
    if previous is None:
        candidates = ((name, None) for name in HISTORICAL_TOMBSTONES - current_entries.keys())
        protected = current_protected
    else:
        old_entries, old_protected = previous
        normalize = str.casefold if platform == "windows-x64" else str
        current_names = {normalize(name) for name in current_entries}
        candidates = (
            (name, old_entries[name])
            for name in old_entries
            if normalize(name) not in current_names
        )
        protected = old_protected | current_protected
    deleted = []
    for name, expected in sorted(candidates):
        if _delete_owned(root, _relative_path(name, "obsolete path"), expected, protected):
            deleted.append(name)
    return deleted, protected


def _reconcile_windows_python(root, current_entries, protected):
    python_root = root / "python"
    _require(
        python_root.is_dir() and not python_root.is_symlink(),
        "windows python runtime is not a regular directory",
    )
    resolved_root = python_root.resolve()
    _require(_same_location(python_root, resolved_root), "windows python runtime is linked")
    expected = {
        name.casefold() for name in current_entries if name.startswith("python/")
    }
    deleted, directories = [], []

    def fail_walk(exc):
        raise ReleaseOverlayError("cannot inspect the windows python runtime") from exc

    for directory, subdirectories, filenames in os.walk(
        python_root, topdown=True, followlinks=False, onerror=fail_walk
    ):
        directory = Path(directory)
        try:
            directory.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ReleaseOverlayError("windows python directory escapes runtime") from exc
        _require(_same_location(directory, directory.resolve()), "windows runtime is linked")
        for name in list(subdirectories):
            child = directory / name
            relative = PurePosixPath(child.relative_to(root).as_posix())
            if child.is_symlink():
                subdirectories.remove(name)
                try:
                    child.unlink()
                except OSError as exc:
                    raise ReleaseOverlayError(f"cannot delete runtime link: {relative}") from exc
                deleted.append(relative.as_posix())
            else:
                _require(_same_location(child, child.resolve()), "windows runtime is linked")
                directories.append((child, relative))
        for name in filenames:
            child = directory / name
            relative = PurePosixPath(child.relative_to(root).as_posix())
            if relative.as_posix().casefold() in expected or _is_protected(relative, protected):
                continue
            try:
                child.unlink()
            except OSError as exc:
                raise ReleaseOverlayError(f"cannot delete runtime file: {relative}") from exc
            deleted.append(relative.as_posix())
    for child, relative in reversed(directories):
        try:
            child.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise ReleaseOverlayError(f"cannot remove runtime directory: {relative}") from exc
    return deleted


def _apply_locked(root, platform, state):
    manifest_content, entries, protected = _load_manifest(
        root / MANIFEST_NAME, platform, MANIFEST_NAME
    )
    digest = hashlib.sha256(manifest_content).hexdigest()
    previous = None
    installed_path = state / INSTALLED_MANIFEST_NAME
    if installed_path.exists() or installed_path.is_symlink():
        installed_content = _read_regular(installed_path, "installed release manifest")
        installed_digest = hashlib.sha256(installed_content).hexdigest()
        if hmac.compare_digest(installed_digest, digest):
            _verify_files(root, entries, REQUIRED_FILES[platform])
            if _read_digest(state) != digest:
                try:
                    _atomic_write(
                        state / INSTALLED_DIGEST_NAME, (digest + "\n").encode("ascii")
                    )
                except OSError as exc:
                    raise ReleaseOverlayError("cannot repair release digest") from exc
            return {"status": "unchanged", "deleted": [], "digest": digest}
        if _read_digest(state) != installed_digest:
            raise ReleaseOverlayError("installed release manifest digest does not match")
        _, old_entries, old_protected = _load_manifest(
            installed_path, platform, "installed release manifest"
        )
        previous = (old_entries, old_protected)

    _verify_files(root, entries)
    deleted, protections = _reconcile_old(
        root, previous, entries, protected, platform
    )
    if platform == "windows-x64":
        deleted.extend(_reconcile_windows_python(root, entries, protections))
    try:
        _atomic_write(state / INSTALLED_MANIFEST_NAME, manifest_content)
        _atomic_write(state / INSTALLED_DIGEST_NAME, (digest + "\n").encode("ascii"))
    except OSError as exc:
        raise ReleaseOverlayError("cannot save installed release manifest") from exc
    return {"status": "updated", "deleted": sorted(set(deleted)), "digest": digest}


def apply_release_overlay(root, platform):
    _require(platform in PLATFORMS, f"unsupported release platform: {platform}")
    root = Path(root).resolve()
    _require(root.is_dir(), f"application root is missing: {root}")
    with _runtime_lock(root) as state:
        return _apply_locked(root, platform, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reconcile a MathBank Release overlay")
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    args = parser.parse_args(argv)
    try:
        result = apply_release_overlay(Path(__file__).resolve().parents[1], args.platform)
    except ReleaseOverlayError as exc:
        parser.exit(1, f"Release overlay failed: {exc}\n")
    if result["status"] == "unchanged":
        print("Release overlay already reconciled.")
    else:
        print(f"Release overlay verified; removed {len(result['deleted'])} obsolete files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
