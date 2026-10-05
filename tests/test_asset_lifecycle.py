"""Retention tests use only pytest temporary roots; never the real uploads."""

import inspect
import json
import os
from pathlib import Path
import threading

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mathbank import asset_lifecycle as lifecycle
from mathbank.asset_security import AssetSecurityError, resolve_upload_asset


@pytest.fixture
def store(tmp_path):
    uploads = tmp_path / "static" / "uploads"
    uploads.mkdir(parents=True)
    recovery = tmp_path / ".system_generated" / "retained_uploads"
    lifecycle.register_asset_store(uploads, recovery)
    yield uploads, recovery
    lifecycle._STORES.pop(uploads.resolve(), None)


def write_image(uploads, relative="example.png", content=b"original image bytes"):
    image = uploads / relative
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(content)
    return image, "/static/uploads/" + relative


def retire(store, reference, reason="startup-unused"):
    return lifecycle.quarantine_asset(
        reference, uploads_dir=store[0], url_prefix="static/uploads", reason=reason
    )


@pytest.mark.parametrize("relative", ["example.png", "tmp/example.png"])
def test_retained_image_recovers_with_same_url_after_registry_restart(store, relative):
    uploads, recovery = store
    image, reference = write_image(uploads, relative)
    os.utime(image, (1, 1))
    assert retire(store, reference)
    assert not image.exists()
    retained = recovery / "files" / relative
    assert retained.read_bytes() == b"original image bytes"
    metadata = json.loads((recovery / "metadata" / (relative + ".json")).read_text())
    assert metadata["original_url"] == reference
    assert metadata["reason"] == "startup-unused"
    assert str(uploads) not in json.dumps(metadata)
    assert retire(store, reference) is False

    # Only the process registry disappears; files alone retain all recovery data.
    lifecycle._STORES.pop(uploads.resolve())
    lifecycle.register_asset_store(uploads, recovery)
    resolved = resolve_upload_asset(reference, uploads_dir=uploads, url_prefix="static/uploads")
    assert resolved == image
    assert image.read_bytes() == b"original image bytes"
    assert image.stat().st_mtime > 1
    assert not retained.exists()
    assert lifecycle.restore_asset(image, uploads_dir=uploads) is False


def test_missing_file_probe_does_not_restore_archived_image(store):
    uploads, recovery = store
    image, reference = write_image(uploads)
    retire(store, reference)
    assert resolve_upload_asset(
        reference, uploads_dir=uploads, url_prefix="static/uploads", require_file=False
    ) == image
    assert not image.exists()
    assert (recovery / "files/example.png").is_file()


def test_retention_never_overwrites_different_bytes(store):
    uploads, recovery = store
    image, reference = write_image(uploads)
    retire(store, reference)
    image.write_bytes(b"newer different contents")
    with pytest.raises(lifecycle.AssetLifecycleError, match="不同内容"):
        retire(store, reference)
    assert image.read_bytes() == b"newer different contents"
    assert (recovery / "files/example.png").read_bytes() == b"original image bytes"
    assert lifecycle.restore_asset(image, uploads_dir=uploads) is False


def test_retention_deduplicates_identical_already_retained_bytes(store):
    uploads, recovery = store
    image, reference = write_image(uploads)
    retire(store, reference)
    image.write_bytes(b"original image bytes")
    assert retire(store, reference)
    assert not image.exists()
    assert (recovery / "files/example.png").read_bytes() == b"original image bytes"


@pytest.mark.parametrize("failure_stage", ["metadata", "hardlink", "unlink"])
def test_failed_retirement_keeps_original_image(store, monkeypatch, failure_stage):
    uploads, recovery = store
    image, reference = write_image(uploads)

    def fail(*_args, **_kwargs):
        raise OSError("simulated unavailable filesystem operation")

    if failure_stage == "metadata":
        monkeypatch.setattr(lifecycle, "_write_metadata", fail)
    elif failure_stage == "hardlink":
        monkeypatch.setattr(lifecycle.os, "link", fail)
    else:
        original = Path.unlink

        def fail_source(path, *args, **kwargs):
            if path == image:
                fail()
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_source)
    with pytest.raises(lifecycle.AssetLifecycleError):
        retire(store, reference)
    assert image.read_bytes() == b"original image bytes"


def test_failed_restore_keeps_recovery_copy(store, monkeypatch):
    uploads, recovery = store
    image, reference = write_image(uploads)
    retire(store, reference)
    monkeypatch.setattr(lifecycle.os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(AssetSecurityError):
        resolve_upload_asset(reference, uploads_dir=uploads, url_prefix="static/uploads")
    assert (recovery / "files/example.png").read_bytes() == b"original image bytes"
    assert not image.exists()


@pytest.mark.parametrize("corruption", ["data", "metadata", "missing_metadata"])
def test_corrupt_recovery_data_is_retained_but_never_restored(store, corruption):
    uploads, recovery = store
    image, reference = write_image(uploads)
    retire(store, reference)
    if corruption == "data":
        (recovery / "files/example.png").write_bytes(b"changed")
    elif corruption == "metadata":
        (recovery / "metadata/example.png.json").write_text("[]")
    else:
        (recovery / "metadata/example.png.json").unlink()
    with pytest.raises(AssetSecurityError):
        resolve_upload_asset(reference, uploads_dir=uploads, url_prefix="static/uploads")
    assert not image.exists()
    assert (recovery / "files/example.png").is_file()


@pytest.mark.parametrize("reference", ["/static/uploads/../escape.png", "/etc/file.png", "https://example.test/a.png", "/static/uploads/unsafe.svg"])
def test_invalid_reference_cannot_reach_recovery(store, reference):
    with pytest.raises(AssetSecurityError):
        retire(store, reference)
    assert not store[1].exists()


@pytest.mark.parametrize("location", ["upload", "retained", "metadata"])
def test_symlink_is_rejected_without_touching_target(store, tmp_path, location):
    uploads, recovery = store
    image, reference = write_image(uploads)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"do not touch")
    if location == "upload":
        image.unlink()
        image.symlink_to(outside)
        with pytest.raises(AssetSecurityError):
            retire(store, reference)
    else:
        retire(store, reference)
        link = recovery / ("files/example.png" if location == "retained" else "metadata/example.png.json")
        link.unlink()
        link.symlink_to(outside)
        with pytest.raises(AssetSecurityError):
            resolve_upload_asset(reference, uploads_dir=uploads, url_prefix="static/uploads")
    assert outside.read_bytes() == b"do not touch"


def test_unregistered_root_never_discards_image(tmp_path):
    uploads = tmp_path / "uploads"
    image, reference = write_image(uploads)
    with pytest.raises(lifecycle.AssetLifecycleError, match="尚未配置"):
        lifecycle.quarantine_asset(reference, uploads_dir=uploads, url_prefix="static/uploads", reason="test")
    assert image.exists()


def test_recovery_ancestor_symlink_is_rejected_at_registration(tmp_path):
    uploads = tmp_path / "static/uploads"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / ".system_generated").symlink_to(outside, target_is_directory=True)
    with pytest.raises(lifecycle.AssetLifecycleError, match="符号链接"):
        lifecycle.register_asset_store(uploads, tmp_path / ".system_generated/retained_uploads/uploads")


def test_recovery_ancestor_replaced_by_symlink_after_registration_is_rejected(store, tmp_path):
    uploads, recovery = store
    image, reference = write_image(uploads)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    recovery.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(lifecycle.AssetLifecycleError, match="符号链接"):
        retire(store, reference)
    assert image.exists()
    assert list(outside.iterdir()) == []


def test_static_image_get_head_cache_and_other_static_files(store):
    uploads, recovery = store
    image, reference = write_image(uploads)
    css = uploads.parent / "app.css"
    css.write_text("body { color: red; }")
    static = lifecycle.RetainedUploadStaticFiles(
        directory=uploads.parent, uploads_dir=uploads, url_prefix="static/uploads"
    )
    app = Starlette(routes=[Mount("/static", app=static)])
    with TestClient(app) as client:
        retire(store, reference)
        response = client.get(reference)
        assert response.status_code == 200
        assert response.content == b"original image bytes"
        assert response.headers["content-type"] == "image/png"
        assert image.exists()
        assert not (recovery / "files/example.png").exists()
        cached = client.get(reference, headers={"If-None-Match": response.headers["etag"]})
        assert cached.status_code == 304
        head = client.head(reference)
        assert head.status_code == 200 and head.content == b""
        assert head.headers["content-length"] == str(len(response.content))
        assert client.post(reference).status_code == 405
        assert client.get("/static/app.css").text == css.read_text()
        assert client.get("/static/.system_generated/retained_uploads/files/example.png").status_code == 404


def test_serialization_decorator_preserves_signature_and_blocks_other_threads():
    entered = threading.Event()

    @lifecycle.serialize_asset_lifecycle
    def operation(question_id: int):
        entered.set()
        return question_id

    assert list(inspect.signature(operation).parameters) == ["question_id"]
    with lifecycle.ASSET_LIFECYCLE_LOCK:
        worker = threading.Thread(target=operation, args=(4,))
        worker.start()
        assert not entered.wait(0.05)
    worker.join(timeout=1)
    assert entered.is_set() and not worker.is_alive()
