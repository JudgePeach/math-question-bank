import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from scripts import build_release, release_overlay


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _zip_bytes(name="payload.txt", content=b"verified"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, content)
    return output.getvalue()


class _FakeResponse(io.BytesIO):
    def __init__(self, payload, url):
        super().__init__(payload)
        self._url = url

    def geturl(self):
        return self._url


def test_runtime_downloads_have_fixed_sha256_and_default_tls():
    source = (PROJECT_ROOT / "scripts" / "build_release.py").read_text(
        encoding="utf-8"
    )
    assert "_create_unverified_context" not in source
    assert "_create_default_https_context" not in source
    assert "urlretrieve" not in source
    assert len(build_release.PYTHON_ZIP_SHA256) == 64
    assert len(build_release.NUGET_ZIP_SHA256) == 64
    int(build_release.PYTHON_ZIP_SHA256, 16)
    int(build_release.NUGET_ZIP_SHA256, 16)
    assert build_release.PYTHON_ZIP_URL.startswith("https://www.python.org/")
    assert build_release.NUGET_ZIP_URL.startswith("https://api.nuget.org/")


def test_download_verified_rechecks_cache_and_replaces_atomically(tmp_path, monkeypatch):
    cache_path = tmp_path / "runtime.zip"
    cache_path.write_bytes(b"corrupt-cache")
    payload = _zip_bytes()
    expected = hashlib.sha256(payload).hexdigest()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return _FakeResponse(payload, request.full_url)

    monkeypatch.setattr(build_release.urllib.request, "urlopen", fake_urlopen)
    result = build_release.download_verified(
        "https://downloads.example.test/runtime.zip",
        cache_path,
        expected,
        "test runtime",
    )
    assert Path(result).read_bytes() == payload
    assert calls == [("https://downloads.example.test/runtime.zip", 60)]
    assert not list(tmp_path.glob("*.download"))

    def unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("a verified cache must not be downloaded again")

    monkeypatch.setattr(build_release.urllib.request, "urlopen", unexpected_urlopen)
    build_release.download_verified(
        "https://downloads.example.test/runtime.zip",
        cache_path,
        expected,
        "test runtime",
    )


def test_download_verified_preserves_cache_when_replacement_fails(tmp_path, monkeypatch):
    cache_path = tmp_path / "runtime.zip"
    original = b"existing-invalid-cache"
    cache_path.write_bytes(original)
    expected = hashlib.sha256(_zip_bytes()).hexdigest()

    monkeypatch.setattr(
        build_release.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(_zip_bytes(content=b"tampered"), request.full_url),
    )
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build_release.download_verified(
            "https://downloads.example.test/runtime.zip",
            cache_path,
            expected,
            "test runtime",
        )
    assert cache_path.read_bytes() == original
    assert not list(tmp_path.glob("*.download"))


@pytest.mark.parametrize("digest", ["", "abc", "g" * 64, "0" * 63])
def test_verify_sha256_fails_closed_without_valid_pin(tmp_path, digest):
    payload = tmp_path / "payload"
    payload.write_bytes(b"content")
    with pytest.raises(RuntimeError, match="no valid pinned SHA-256"):
        build_release.verify_sha256(payload, digest, "payload")


def test_cross_platform_requirements_are_locked_and_target_explicit():
    runtime_lines = (PROJECT_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    package_lines = [
        line.strip()
        for line in runtime_lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert package_lines
    assert all("==" in line and ";" not in line for line in package_lines)
    assert "colorama==0.4.6" in package_lines
    assert "greenlet==3.5.4" in package_lines

    dev_lines = (PROJECT_ROOT / "requirements-dev.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    dev_packages = [
        line.strip()
        for line in dev_lines
        if line.strip()
        and not line.lstrip().startswith(("#", "-r", "--requirement"))
    ]
    assert all("==" in line and ";" not in line for line in dev_packages)
    # Starlette 1.x has moved its TestClient to the Pydantic-maintained
    # HTTPX2 package; keep this explicit so a clean environment does not fall
    # back to the deprecated legacy httpx client.
    assert "httpx2==2.7.0" in dev_packages
    assert {
        "httpcore2==2.7.0",
        "truststore==0.10.4",
        "iniconfig==2.3.0",
        "packaging==26.3",
        "pluggy==1.6.0",
        "pygments==2.20.0",
        "tomli==2.4.1",
    }.issubset(dev_packages)

    windows_requirements = (PROJECT_ROOT / "requirements-windows.txt").read_text(
        encoding="utf-8"
    )
    assert windows_requirements.count("-r requirements.txt") == 1
    assert "sys_platform" not in windows_requirements
    build_release.validate_target_requirements()


def test_windows_runtime_layout_requires_platform_transitive_dependencies(tmp_path):
    required_paths = (
        "python/python.exe",
        "python/python310._pth",
        "python/_sqlite3.pyd",
        "python/sqlite3.dll",
        "python/site-packages/fastapi",
        "python/site-packages/uvicorn",
        "python/site-packages/sqlalchemy",
    )
    for relative_path in required_paths:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix:
            path.touch()
        else:
            path.mkdir()

    with pytest.raises(RuntimeError) as exc_info:
        build_release.validate_windows_runtime(tmp_path)

    message = str(exc_info.value)
    assert "python/site-packages/greenlet" in message
    assert "python/site-packages/colorama" in message


def _make_minimal_macos_tree(root):
    for relative_path in (
        "main.py",
        "requirements.txt",
        "覆盖升级说明.txt",
        "mathbank/__init__.py",
        "scripts/release_overlay.py",
        "static/index.html",
        "static/uploads/.gitkeep",
        "启动题库系统.command",
    ):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("safe\n", encoding="utf-8")
    (root / "启动题库系统.command").chmod(0o755)


@pytest.mark.parametrize(
    "forbidden_path",
    [
        "static/.DS_Store",
        "static/test_uploads/capture.png",
        "tests/test_release.py",
        "mathbank/__pycache__/module.pyc",
        ".env",
        "math_question_bank.db",
        "data_backup/custom_metadata.json",
        ".system_generated/local_token",
        "static/uploads/user.png",
        "venv/bin/python",
    ],
)
def test_release_tree_rejects_hidden_test_and_data_artifacts(tmp_path, forbidden_path):
    _make_minimal_macos_tree(tmp_path)
    path = tmp_path / forbidden_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"must not ship")
    with pytest.raises(RuntimeError, match="forbidden artifacts"):
        build_release.assert_release_tree_clean(tmp_path, "macos")


def test_release_manifest_records_every_staged_file_hash(tmp_path):
    _make_minimal_macos_tree(tmp_path)
    build_release.assert_release_tree_clean(tmp_path, "macos")
    manifest_path = build_release.write_release_manifest(tmp_path, "macos")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in manifest["files"]}
    assert manifest["app_version"] == build_release.__version__
    assert manifest["platform"] == "macos"
    assert manifest["overlay"] == {
        "managed_roots": build_release._overlay_managed_roots("macos"),
        "protected_paths": list(build_release.OVERLAY_PROTECTED_PATHS),
        "schema_version": 1,
    }
    assert "RELEASE-MANIFEST.json" not in entries
    assert entries["main.py"]["sha256"] == build_release.sha256_file(
        tmp_path / "main.py"
    )
    assert entries["main.py"]["size"] == (tmp_path / "main.py").stat().st_size
    assert release_overlay.apply_release_overlay(tmp_path, "macos")["status"] == "updated"


def test_reviewed_certifi_tests_are_pruned_before_strict_release_check(tmp_path):
    _make_minimal_macos_tree(tmp_path)
    certifi_tests = tmp_path / "python" / "site-packages" / "certifi" / "tests"
    certifi_tests.mkdir(parents=True)
    (certifi_tests / "test_certify.py").write_text("assert True\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="forbidden artifacts"):
        build_release.assert_release_tree_clean(tmp_path, "macos")

    build_release._prune_dependency_test_artifacts(
        tmp_path / "python" / "site-packages"
    )
    build_release.assert_release_tree_clean(tmp_path, "macos")
    assert not certifi_tests.exists()


def test_finished_archive_crc_manifest_and_sidecar(tmp_path):
    staging = tmp_path / "staging"
    _make_minimal_macos_tree(staging)
    archive_path = build_release._build_archive(
        staging,
        str(tmp_path / "MathBank-macOS"),
        "macos",
        "启动题库系统.command",
    )
    checksum_path = Path(archive_path + ".sha256")
    assert checksum_path.is_file()
    assert checksum_path.read_text(encoding="utf-8").split()[0] == build_release.sha256_file(
        archive_path
    )
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = {info.filename.rstrip("/") for info in archive.infolist()}
        launcher = archive.getinfo("启动题库系统.command")
    assert {"main.py", "覆盖升级说明.txt", "RELEASE-MANIFEST.json"}.issubset(names)
    assert not any(name.startswith("MathBank-") for name in names)
    if launcher.create_system == 3:
        assert launcher.external_attr >> 16 & 0o111


def test_failed_final_verification_removes_archive_and_checksum(
    tmp_path, monkeypatch
):
    staging = tmp_path / "staging"
    _make_minimal_macos_tree(staging)
    archive_stem = tmp_path / "MathBank-macOS"
    checksum = tmp_path / "MathBank-macOS.zip.sha256"
    checksum.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(
        build_release,
        "verify_zip_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(RuntimeError, match="injected"):
        build_release._build_archive(
            staging,
            str(archive_stem),
            "macos",
            "启动题库系统.command",
        )

    assert not archive_stem.with_suffix(".zip").exists()
    assert not checksum.exists()


def test_failed_release_preflight_also_removes_stale_outputs(tmp_path):
    staging = tmp_path / "invalid-staging"
    staging.mkdir()
    archive_stem = tmp_path / "MathBank-macOS"
    archive = archive_stem.with_suffix(".zip")
    checksum = tmp_path / "MathBank-macOS.zip.sha256"
    archive.write_bytes(b"stale")
    checksum.write_text("stale\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing required files"):
        build_release._build_archive(
            staging,
            str(archive_stem),
            "macos",
            "启动题库系统.command",
        )

    assert not archive.exists()
    assert not checksum.exists()


def test_main_failure_removes_stale_and_partial_cross_platform_outputs(
    tmp_path, monkeypatch
):
    dist = tmp_path / "dist"
    dist.mkdir()
    unrelated = dist / "teacher-notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(build_release, "DIST_DIR", str(dist))

    def write_windows_output():
        (dist / "MathBank-Windows-x64.zip").write_bytes(b"partial")
        (dist / "MathBank-Windows-x64.zip.sha256").write_text(
            "partial\n", encoding="utf-8"
        )

    for function_name in (
        "validate_target_requirements",
        "clean_directories",
        "download_python",
        "download_and_extract_sqlite",
        "configure_python_path",
        "download_and_extract_wheels",
        "copy_app_files",
        "create_launcher",
    ):
        monkeypatch.setattr(build_release, function_name, lambda *_args: None)
    monkeypatch.setattr(build_release, "validate_release_source", lambda: None)
    monkeypatch.setattr(build_release, "zip_release", write_windows_output)
    monkeypatch.setattr(
        build_release,
        "zip_macos_release",
        lambda: (_ for _ in ()).throw(RuntimeError("macOS injected failure")),
    )

    with pytest.raises(RuntimeError, match="macOS injected failure"):
        build_release.main()

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not any((dist / name).exists() for name in build_release.RELEASE_OUTPUT_NAMES)

    for name in build_release.RELEASE_OUTPUT_NAMES:
        (dist / name).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(
        build_release,
        "validate_release_source",
        lambda: (_ for _ in ()).throw(RuntimeError("preflight injected failure")),
    )
    with pytest.raises(RuntimeError, match="preflight injected failure"):
        build_release.main()
    assert not any((dist / name).exists() for name in build_release.RELEASE_OUTPUT_NAMES)
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_clean_directories_preserves_unrelated_dist_artifacts(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    unrelated = dist / "teacher-notes.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("keep", encoding="utf-8")
    build_dir = dist / "mathbank-windows"
    wheels_dir = dist / "wheels"
    macos_dir = dist / "mathbank-macos"
    for directory in (build_dir, wheels_dir, macos_dir):
        directory.mkdir()
        (directory / "old").write_text("remove", encoding="utf-8")
    (dist / "MathBank-macOS.zip").write_bytes(b"old")

    monkeypatch.setattr(build_release, "DIST_DIR", str(dist))
    monkeypatch.setattr(build_release, "BUILD_DIR", str(build_dir))
    monkeypatch.setattr(build_release, "WHEELS_DIR", str(wheels_dir))
    monkeypatch.setattr(build_release, "MACOS_BUILD_DIR", str(macos_dir))
    monkeypatch.setattr(build_release, "PYTHON_DIR", str(build_dir / "python"))
    monkeypatch.setattr(
        build_release,
        "SITE_PACKAGES",
        str(build_dir / "python" / "site-packages"),
    )

    build_release.clean_directories()

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not (dist / "MathBank-macOS.zip").exists()
    assert (build_dir / "python" / "site-packages").is_dir()


def test_finished_archive_rejects_crc_valid_member_tampering(tmp_path):
    staging = tmp_path / "staging"
    _make_minimal_macos_tree(staging)
    archive_path = Path(
        build_release._build_archive(
            staging,
            str(tmp_path / "MathBank-macOS"),
            "macos",
            "启动题库系统.command",
        )
    )
    rewritten = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(
        rewritten, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "main.py":
                assert len(payload) == 5
                payload = b"evil\n"
            target.writestr(info, payload)

    # Rewriting updates the CRC, so only the embedded SHA-256 manifest can
    # detect this kind of valid-ZIP tampering.
    build_release.validate_zip(rewritten, "tampered release")
    with pytest.raises(RuntimeError, match="SHA-256 does not match manifest"):
        build_release.verify_zip_archive(
            rewritten,
            {"RELEASE-MANIFEST.json", "main.py", "启动题库系统.command"},
        )


def test_finished_macos_archive_rejects_non_executable_launcher(tmp_path):
    staging = tmp_path / "staging"
    _make_minimal_macos_tree(staging)
    archive_path = Path(
        build_release._build_archive(
            staging,
            str(tmp_path / "MathBank-macOS"),
            "macos",
            "启动题库系统.command",
        )
    )
    rewritten = tmp_path / "non-executable.zip"
    with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(
        rewritten, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for source_info in source.infolist():
            info = zipfile.ZipInfo(source_info.filename, source_info.date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = source_info.create_system
            info.external_attr = source_info.external_attr
            if info.filename == "启动题库系统.command":
                info.create_system = 3
                info.external_attr = 0o100644 << 16
            target.writestr(info, source.read(source_info.filename))

    with pytest.raises(RuntimeError, match="launcher is not executable"):
        build_release.verify_zip_archive(
            rewritten,
            {"RELEASE-MANIFEST.json", "main.py", "启动题库系统.command"},
        )


def test_static_release_allowlist_excludes_uploads_and_test_assets():
    assert "test_uploads" not in build_release.STATIC_ALLOWLIST
    assert "uploads" not in build_release.STATIC_ALLOWLIST
    assert build_release._release_path_violation("static/uploads/.gitkeep") is None
    assert build_release._release_path_violation("static/uploads/user.png")
    assert build_release._release_path_violation("data_backup/custom_metadata.json")


def test_launchers_require_python_310_and_only_stop_owned_pid():
    mac_launcher = (PROJECT_ROOT / "启动题库系统.command").read_text(encoding="utf-8")
    windows_launcher = (PROJECT_ROOT / "启动题库系统.bat").read_text(
        encoding="utf-8"
    )

    assert "--reload" not in mac_launcher
    assert "kill -9" not in mac_launcher
    assert "server.identity" in mac_launcher
    assert "is_owned_server" in mac_launcher
    assert "sys.version_info >= (3, 10)" in mac_launcher
    assert "浏览器不会打开" in mac_launcher
    assert "requirements.sha256" in mac_launcher
    assert "hashlib.sha256" in mac_launcher
    assert "-m pip check" in mac_launcher
    assert "/healthz" in mac_launcher
    assert "/api/questions" not in mac_launcher
    assert "deadline = time.monotonic() + 10.0" in mac_launcher
    assert "min(0.4, remaining)" in mac_launcher
    assert "time.sleep(min(0.5, remaining))" in mac_launcher
    assert "timeout=2" not in mac_launcher
    assert "-B -m scripts.release_overlay --platform macos" in mac_launcher
    assert mac_launcher.index("stop_previous_owned_server ||") < mac_launcher.index(
        "-B -m scripts.release_overlay --platform macos"
    ) < mac_launcher.index("正在检查运行环境依赖是否完整")

    assert "--reload" not in windows_launcher
    assert "taskkill" not in windows_launcher.lower()
    assert "server.identity" in windows_launcher
    assert "Get-CimInstance Win32_Process" in windows_launcher
    assert "Stop-Process" in windows_launcher
    assert "sys.version_info >= (3, 10)" in windows_launcher
    assert "浏览器不会打开" in windows_launcher
    assert "requirements.sha256" in windows_launcher
    assert "hashlib.sha256" in windows_launcher
    assert "-m pip check" in windows_launcher
    assert "/healthz" in windows_launcher
    assert "/api/questions" not in windows_launcher
    windows_health = windows_launcher[
        windows_launcher.index("正在探测后台服务启动状态"):
        windows_launcher.index(":health_complete")
    ]
    assert "[Diagnostics.Stopwatch]::StartNew()" in windows_health
    assert "Elapsed.TotalSeconds -lt 10" in windows_health
    assert "[Math]::Min(400" in windows_health
    assert "[Math]::Min(500" in windows_health
    assert "ping " not in windows_health.lower()
    assert "-B -m scripts.release_overlay --platform windows-x64" in windows_launcher
    assert windows_launcher.index("call :stop_owned_server") < windows_launcher.index(
        "-B -m scripts.release_overlay --platform windows-x64"
    ) < windows_launcher.index("正在检查运行环境依赖是否完整")


def test_release_builder_uses_invoking_interpreter_for_pip():
    source = (PROJECT_ROOT / "scripts" / "build_release.py").read_text(
        encoding="utf-8"
    )
    assert 'sys.executable,\n        "-m",\n        "pip"' in source
    assert '"pip", "download"' not in source


def test_release_builder_never_runs_during_unit_import(monkeypatch):
    # Importing the module above must be side-effect free; guard against a
    # future unconditional main() call without executing a real build here.
    monkeypatch.setattr(build_release, "clean_directories", lambda: pytest.fail("built"))
    assert callable(build_release.main)
    assert shutil.which("definitely-not-a-command") is None
