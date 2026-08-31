import hashlib
import http.client
import io
import stat
import urllib.error
import zipfile
from pathlib import Path

import pytest

from mathbank import runtime_components


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
    monkeypatch.setattr(runtime_components, "_smoke_pandoc", lambda _binary: None)
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

    with pytest.raises(runtime_components.PandocInstallError, match="下载线路均不可用"):
        runtime_components._install_pandoc(lambda *_args: None)

    assert not list(runtime_root.rglob("*.partial"))
