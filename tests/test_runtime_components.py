import hashlib
import http.client
import io
import os
import stat
import subprocess
import urllib.error
import zipfile
from pathlib import Path

import pytest

from mathbank import runtime_components
from mathbank.processing_timeouts import (
    RUNTIME_DOWNLOAD_TIMEOUT_SECONDS, RUNTIME_PROBE_TIMEOUT_SECONDS, WORD_CONVERSION_TIMEOUT_SECONDS,
)


def _pandoc_archive(binary_name: str = "pandoc") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"pandoc-test/{binary_name}", b"fake-pandoc")
        archive.writestr("pandoc-test/COPYRIGHT.txt", b"Pandoc test notice")
    return output.getvalue()


def test_source_order_prefers_official_then_sourceforge():
    sources = runtime_components._source_urls("pandoc-test.zip")
    assert [item[0] for item in sources] == ["official", "sourceforge"]
    assert sources[0][1].startswith("https://github.com/jgm/pandoc/releases/download/")
    assert sources[1][1].startswith("https://sourceforge.net/projects/pandoc.mirror/")


def test_safe_members_rejects_path_traversal():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../pandoc", b"unsafe")
    payload.seek(0)
    with zipfile.ZipFile(payload) as archive:
        with pytest.raises(runtime_components.PandocInstallError, match="不安全路径"):
            runtime_components._safe_members(archive)


def test_safe_members_omits_optional_official_macos_symlinks():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("pandoc/bin/pandoc", b"binary")
        link = zipfile.ZipInfo("pandoc/bin/pandoc-server")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o755) << 16
        archive.writestr(link, b"pandoc")
    payload.seek(0)

    with zipfile.ZipFile(payload) as archive:
        safe = runtime_components._safe_members(archive)

    assert [member.filename for member in safe] == ["pandoc/bin/pandoc"]


def test_find_usable_pandoc_honors_explicit_path(monkeypatch, tmp_path):
    binary = tmp_path / "pandoc"
    binary.write_bytes(b"pandoc")
    monkeypatch.setenv("MATHBANK_PANDOC_PATH", str(binary))
    monkeypatch.setattr(runtime_components, "_probe_pandoc", lambda path: "pandoc 3.test" if path == binary else None)

    resolved = runtime_components.find_usable_pandoc()

    assert resolved is not None
    assert resolved[0] == binary.resolve()
    assert resolved[1:] == ("configured", "pandoc 3.test")


def test_download_resumes_after_incomplete_connection(monkeypatch, tmp_path):
    class FakeResponse:
        def __init__(self, chunks, status, length):
            self._chunks = iter(chunks)
            self.status = status
            self.headers = {"Content-Length": str(length)}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

        def read(self, _size):
            value = next(self._chunks)
            if isinstance(value, Exception):
                raise value
            return value

    requests = []
    responses = iter(
        (
            FakeResponse([b"abc", http.client.IncompleteRead(b"", 3)], 200, 6),
            FakeResponse([b"def", b""], 206, 3),
        )
    )

    def fake_urlopen(request, timeout):
        requests.append((request.headers, timeout))
        return next(responses)

    monkeypatch.setattr(runtime_components.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "pandoc.zip"

    runtime_components._download(
        "https://example.invalid/pandoc.zip",
        destination,
        expected_size=6,
        progress=lambda _value: None,
    )

    assert destination.read_bytes() == b"abcdef"
    assert requests[0][0].get("Range") is None
    assert requests[1][0]["Range"] == "bytes=3-"
    assert [request[1] for request in requests] == [RUNTIME_DOWNLOAD_TIMEOUT_SECONDS] * 2


def test_install_falls_back_to_sourceforge_and_keeps_verified_component(monkeypatch, tmp_path):
    payload = _pandoc_archive()
    digest = hashlib.sha256(payload).hexdigest()
    runtime_root = tmp_path / "runtime" / "pandoc"
    download_root = runtime_root / "downloads"
    install_log = runtime_root / "install.log"
    asset = {
        "filename": "pandoc-test.zip",
        "sha256": digest,
        "size": len(payload),
        "binary": "pandoc",
    }
    calls = []

    monkeypatch.setattr(runtime_components, "PANDOC_RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(runtime_components, "PANDOC_DOWNLOAD_DIR", download_root)
    monkeypatch.setattr(runtime_components, "PANDOC_INSTALL_LOG", install_log)
    monkeypatch.setattr(runtime_components, "PANDOC_VERSION", "test-version")
    monkeypatch.setattr(runtime_components, "_asset_for_current_platform", lambda: ("macos-arm64", asset))
    monkeypatch.setattr(
        runtime_components,
        "_source_urls",
        lambda _filename: (("official", "https://official.invalid/pandoc.zip"), ("sourceforge", "https://mirror.invalid/pandoc.zip")),
    )

    def fake_download(url, destination, *, expected_size, progress):
        calls.append(url)
        if "official" in url:
            raise urllib.error.URLError("official unavailable")
        destination.write_bytes(payload)
        progress(88)

    monkeypatch.setattr(runtime_components, "_download", fake_download)
    monkeypatch.setattr(runtime_components, "_smoke_pandoc", lambda _binary, **_kwargs: None)
    monkeypatch.setattr(runtime_components, "_probe_pandoc", lambda _binary, **_kwargs: "pandoc test")
    states = []

    binary = runtime_components._install_pandoc(
        lambda progress, stage, source: states.append((progress, stage, source))
    )

    assert calls == [
        "https://official.invalid/pandoc.zip",
        "https://official.invalid/pandoc.zip",
        "https://mirror.invalid/pandoc.zip",
    ]
    assert binary.read_bytes() == b"fake-pandoc"
    assert (binary.parent / "component.json").is_file()
    assert any(source == "sourceforge" for _, _, source in states)
    assert states[-1][:2] == (100, "ready")


def test_install_rejects_wrong_sha256(monkeypatch, tmp_path):
    payload = _pandoc_archive()
    runtime_root = tmp_path / "runtime" / "pandoc"
    asset = {
        "filename": "pandoc-test.zip",
        "sha256": "0" * 64,
        "size": len(payload),
        "binary": "pandoc",
    }
    monkeypatch.setattr(runtime_components, "PANDOC_RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(runtime_components, "PANDOC_DOWNLOAD_DIR", runtime_root / "downloads")
    monkeypatch.setattr(runtime_components, "PANDOC_INSTALL_LOG", runtime_root / "install.log")
    monkeypatch.setattr(runtime_components, "_asset_for_current_platform", lambda: ("macos-arm64", asset))
    monkeypatch.setattr(runtime_components, "_source_urls", lambda _filename: (("official", "https://official.invalid/pandoc.zip"),))
    monkeypatch.setattr(
        runtime_components,
        "_download",
        lambda _url, destination, **_kwargs: destination.write_bytes(payload),
    )

    with pytest.raises(runtime_components.PandocInstallError, match="SHA-256 校验失败"):
        runtime_components._install_pandoc(lambda *_args: None)

    assert not list(runtime_root.rglob("*.partial"))


@pytest.fixture
def isolated_installer(monkeypatch, tmp_path):
    root = tmp_path / "中文 空格" / "runtime"
    payload = _pandoc_archive()
    asset = {"filename": "pandoc-test.zip", "sha256": hashlib.sha256(payload).hexdigest(),
             "size": len(payload), "binary": "pandoc"}
    monkeypatch.setattr(runtime_components, "PANDOC_RUNTIME_DIR", root)
    monkeypatch.setattr(runtime_components, "PANDOC_DOWNLOAD_DIR", root / "downloads")
    monkeypatch.setattr(runtime_components, "PANDOC_INSTALL_LOG", root / "install.log")
    monkeypatch.setattr(runtime_components, "_asset_for_current_platform", lambda: ("test-platform", asset))
    monkeypatch.setattr(runtime_components, "_source_urls", lambda _: (("official", "https://example.invalid"),))
    monkeypatch.setattr(runtime_components, "_download", lambda _url, dest, **_kw: dest.write_bytes(payload))
    monkeypatch.setattr(runtime_components, "_smoke_pandoc", lambda _binary, **_kw: None)
    monkeypatch.setattr(runtime_components, "_probe_pandoc", lambda _binary, **_kw: "pandoc test")
    return root, asset, payload


def test_validation_failure_retains_verified_archive_for_offline_retry(isolated_installer, monkeypatch):
    root, asset, payload = isolated_installer
    def fail_smoke(_binary, **_kwargs):
        raise runtime_components.PandocInstallError("Word verification failed")
    monkeypatch.setattr(runtime_components, "_smoke_pandoc", fail_smoke)
    with pytest.raises(runtime_components.PandocInstallError, match="Word verification"):
        runtime_components._install_pandoc(lambda *_: None)
    assert (root / "downloads" / asset["filename"]).read_bytes() == payload
    assert not (root / runtime_components.PANDOC_VERSION / "test-platform").exists()
    def no_network(*_args, **_kwargs):
        pytest.fail("A verified cached archive must work without network access")
    monkeypatch.setattr(runtime_components, "_download", no_network)
    monkeypatch.setattr(runtime_components, "_smoke_pandoc", lambda _binary, **_kwargs: None)
    binary = runtime_components._install_pandoc(lambda *_: None)
    assert binary.read_bytes() == b"fake-pandoc"


@pytest.mark.parametrize("corruption", ["size", "hash"])
def test_cached_archive_is_reverified_before_reuse(isolated_installer, monkeypatch, corruption):
    root, asset, payload = isolated_installer
    cache = runtime_components._verified_archive(asset, lambda *_: None)
    cache.write_bytes(b"x" if corruption == "size" else b"x" * len(payload))
    downloads = []
    def download(_url, dest, **_kwargs):
        downloads.append(dest)
        dest.write_bytes(payload)
    monkeypatch.setattr(runtime_components, "_download", download)
    binary = runtime_components._install_pandoc(lambda *_: None)
    assert len(downloads) == 1
    assert cache.read_bytes() == payload
    assert binary.read_bytes() == b"fake-pandoc"


def test_cleanup_failure_does_not_reverse_success(isolated_installer, monkeypatch):
    root, _, _ = isolated_installer
    real_rmtree = runtime_components.shutil.rmtree
    def locked_staging(path, *args, **kwargs):
        if str(path).endswith(".staging"):
            raise PermissionError("simulated Windows sharing violation")
        return real_rmtree(path, *args, **kwargs)
    monkeypatch.setattr(runtime_components.shutil, "rmtree", locked_staging)
    manager = runtime_components.PandocInstallManager()
    manager._state.update(task_id="test", status="queued")
    try:
        manager._run("test")
        state = manager.snapshot()
        assert state["status"] == "completed"
        assert Path(state["path"]).read_bytes() == b"fake-pandoc"
        assert "cleanup deferred" in (root / "install.log").read_text(encoding="utf-8")
    finally:
        manager._executor.shutdown(wait=True)


@pytest.mark.parametrize("fail_install", [False, True])
def test_unwritable_log_cannot_change_terminal_state(isolated_installer, monkeypatch, fail_install):
    real_open = Path.open
    def open_except_log(path, *args, **kwargs):
        if path == runtime_components.PANDOC_INSTALL_LOG:
            raise PermissionError("log unavailable")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_except_log)
    if fail_install:
        def fail_smoke(_binary, *, progress):
            progress(96, "checking_word", None)
            raise runtime_components.PandocInstallError("specific conversion error")
        monkeypatch.setattr(runtime_components, "_smoke_pandoc", fail_smoke)
    manager = runtime_components.PandocInstallManager()
    manager._state.update(task_id="test", status="queued")
    try:
        manager._run("test")
        state = manager.snapshot()
        assert state["status"] == ("error" if fail_install else "completed")
        if fail_install:
            assert state["stage"] == "checking_word"
            assert "specific conversion error" in state["error"]
            assert "Word" in state["error"]
    finally:
        manager._executor.shutdown(wait=True)


@pytest.mark.parametrize("failure", ["publish", "final_probe"])
def test_failed_publish_or_final_probe_restores_previous_component(isolated_installer, monkeypatch, failure):
    root, asset, _ = isolated_installer
    final = root / runtime_components.PANDOC_VERSION / "test-platform"
    final.mkdir(parents=True)
    (final / "pandoc").write_bytes(b"previous component")
    real_replace = os.replace
    def replace(source, destination):
        if failure == "publish" and str(source).endswith(".prepared") and destination == final:
            raise PermissionError("publish locked")
        return real_replace(source, destination)
    def probe(path, **_kwargs):
        if failure == "final_probe" and path.parent == final:
            raise runtime_components.PandocInstallError("final probe failed")
        return "pandoc test"
    monkeypatch.setattr(runtime_components.os, "replace", replace)
    monkeypatch.setattr(runtime_components, "_probe_pandoc", probe)
    with pytest.raises((PermissionError, runtime_components.PandocInstallError)):
        runtime_components._install_pandoc(lambda *_: None)
    assert (final / "pandoc").read_bytes() == b"previous component"
    assert (root / "downloads" / asset["filename"]).is_file()
    assert not list(final.parent.glob("*.backup"))


def test_failed_rollback_preserves_previous_component_backup(isolated_installer, monkeypatch):
    root, _, _ = isolated_installer
    final = root / runtime_components.PANDOC_VERSION / "test-platform"
    final.mkdir(parents=True)
    (final / "pandoc").write_bytes(b"previous component")
    real_replace = os.replace
    def replace(source, destination):
        if destination == final:
            raise PermissionError("destination locked during publish and rollback")
        return real_replace(source, destination)
    monkeypatch.setattr(runtime_components.os, "replace", replace)
    with pytest.raises(runtime_components.PandocInstallError, match="恢复旧组件失败"):
        runtime_components._install_pandoc(lambda *_: None)
    backups = list(final.parent.glob("*.backup"))
    assert len(backups) == 1
    assert (backups[0] / "pandoc").read_bytes() == b"previous component"


def test_manager_does_not_report_ready_or_start_another_task_during_finalization(monkeypatch):
    manager = runtime_components.PandocInstallManager()
    manager._state.update(task_id="test", status="queued")
    def install(progress):
        progress(100, "ready", None)
        assert manager.snapshot()["status"] == "verifying"
        assert manager.snapshot()["progress"] == 99
        assert manager.ensure()["task_id"] == "test"
        return Path("/test/pandoc")
    monkeypatch.setattr(runtime_components, "_install_pandoc", install)
    monkeypatch.setattr(runtime_components, "_append_install_log", lambda _: None)
    def must_not_probe():
        pytest.fail("Active installation must be joined before probing the published directory")
    monkeypatch.setattr(runtime_components, "pandoc_status", must_not_probe)
    try:
        manager._run("test")
        assert manager.snapshot()["status"] == "completed"
    finally:
        manager._executor.shutdown(wait=True)


@pytest.mark.parametrize("failure", ["exit", "permission", "timeout"])
def test_strict_probe_preserves_startup_diagnostics(monkeypatch, tmp_path, failure):
    binary = tmp_path / "pandoc"
    binary.write_bytes(b"placeholder")
    def run(*_args, **_kwargs):
        assert _kwargs["timeout"] == RUNTIME_PROBE_TIMEOUT_SECONDS
        if failure == "permission":
            raise PermissionError("access denied")
        if failure == "timeout":
            raise subprocess.TimeoutExpired([str(binary)], RUNTIME_PROBE_TIMEOUT_SECONDS, output=b"startup stalled")
        return subprocess.CompletedProcess([], 23, stdout=b"runtime missing")
    monkeypatch.setattr(runtime_components.subprocess, "run", run)
    assert runtime_components._probe_pandoc(binary) is None
    expected = {"exit": "23.*runtime missing", "permission": "access denied", "timeout": "startup stalled"}
    with pytest.raises(runtime_components.PandocInstallError, match=expected[failure]):
        runtime_components._probe_pandoc(binary, strict=True)


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing_omml", "cleanup"])
def test_word_smoke_reports_conversion_errors_and_tolerates_cleanup(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(runtime_components, "_probe_pandoc", lambda _binary, **_kwargs: "pandoc test")
    temp = tmp_path / "smoke"
    temp.mkdir()
    monkeypatch.setattr(runtime_components.tempfile, "mkdtemp", lambda **_kwargs: str(temp))
    monkeypatch.setattr(runtime_components, "_append_install_log", lambda _: None)
    def run(args, **_kwargs):
        assert _kwargs["timeout"] == WORD_CONVERSION_TIMEOUT_SECONDS
        if failure == "exit":
            return subprocess.CompletedProcess(args, 42, stdout=b"", stderr=b"cannot write docx")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, WORD_CONVERSION_TIMEOUT_SECONDS, stderr=b"conversion stalled")
        with zipfile.ZipFile(args[-1], "w") as docx:
            docx.writestr("word/document.xml", b"<oMath/>" if failure == "cleanup" else b"<document/>")
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(runtime_components.subprocess, "run", run)
    if failure == "cleanup":
        def locked(*_args, **_kwargs):
            raise PermissionError("locked validation document")
        monkeypatch.setattr(runtime_components.shutil, "rmtree", locked)
        runtime_components._smoke_pandoc(Path("/test/pandoc"))
    else:
        expected = {"exit": "42.*cannot write docx", "timeout": "conversion stalled", "missing_omml": "未生成 Word 可编辑公式"}
        with pytest.raises(runtime_components.PandocInstallError, match=expected[failure]):
            runtime_components._smoke_pandoc(Path("/test/pandoc"))
        assert not temp.exists()


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_PANDOC_NATIVE") != "1", reason="Opt-in real platform download and OMML smoke")
def test_native_pandoc_install_in_unicode_path(monkeypatch, tmp_path):
    assert runtime_components._platform_key() is not None, "Native test needs Windows x64 or macOS"
    root = tmp_path / "中文 空格" / "pandoc"
    monkeypatch.setattr(runtime_components, "PANDOC_RUNTIME_DIR", root)
    monkeypatch.setattr(runtime_components, "PANDOC_DOWNLOAD_DIR", root / "downloads")
    monkeypatch.setattr(runtime_components, "PANDOC_INSTALL_LOG", root / "install.log")
    stages = []
    binary = runtime_components._install_pandoc(lambda _value, stage, _source: stages.append(stage))
    assert runtime_components._managed_binary() == binary
    assert runtime_components.PANDOC_VERSION in runtime_components._probe_pandoc(binary, strict=True)
    runtime_components._smoke_pandoc(binary)
    assert {"extracting", "checking_binary", "checking_word", "installing", "checking_install", "ready"} <= set(stages)
    def no_network(*_args, **_kwargs):
        pytest.fail("Reinstallation must reuse the verified archive")
    monkeypatch.setattr(runtime_components, "_download", no_network)
    assert runtime_components._install_pandoc(lambda *_: None) == binary
    runtime_components._smoke_pandoc(binary)
