"""Isolated storage lifecycle tests; never touch real library/media/config."""
import hashlib
import json
from pathlib import Path
import sys
import threading

import pytest

from mathbank import tiered_storage as ts


pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="External volume storage requires POSIX directory file descriptors"
)


UUID = "00000000-0000-4000-8000-000000000001"  # Synthetic test volume.


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / "static/uploads").mkdir(parents=True)
    old = root / "static/uploads/historical.png"
    old.write_bytes(b"historical")
    state = root / ".system_generated/tiered_storage"
    storage = ts.initialize(root=root, state_dir=state, config_path=state / "config.json",
                            volume_uuid=UUID)
    volume = tmp_path / "external"
    volume.mkdir()
    online = [True]
    now = [__import__('time').time() + 200]
    storage.clock = lambda: now[0]
    storage.volume_provider = lambda expected: volume if online[0] and expected == UUID else None
    monkeypatch.setattr(ts, "_STORAGE", storage)
    monkeypatch.setattr(ts, "_CONFIG_CHECKED", True)
    storage.stage_seconds = 0
    return storage, root, volume, online, now


def add_file(root, name="new.png", data=b"new image"):
    path = root / "static/uploads" / name
    path.write_bytes(data)
    return path


def evict(storage, now):
    storage.cycle()
    now[0] += 3601
    storage.cycle()


def test_staging_reconnect_verified_evict_hydrate_and_baseline(store):
    storage, root, volume, online, now = store
    path = add_file(root)
    online[0] = False
    storage.cycle()
    assert path.read_bytes() == b"new image"
    assert not (volume / "业务资料").exists()
    online[0] = True
    evict(storage, now)
    assert not path.exists()
    assert (root / "static/uploads/historical.png").read_bytes() == b"historical"
    assert storage.hydrate(path)
    assert path.read_bytes() == b"new image"
    assert list(volume.rglob("*.json"))  # Durable, reconstructable remote receipts.


def test_offline_cache_survives_and_cold_asset_has_clear_error(store):
    storage, root, _, online, now = store
    path = add_file(root)
    evict(storage, now)
    online[0] = False
    with pytest.raises(ts.TieredStorageError, match="连接数据盘"):
        storage.hydrate(path)
    online[0] = True
    storage.hydrate(path)
    online[0] = False
    assert not storage.hydrate(path)
    assert path.read_bytes() == b"new image"


def test_corrupt_remote_never_evicts_or_hydrates_wrong_bytes(store):
    storage, root, volume, _, now = store
    path = add_file(root)
    storage.cycle()
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    target = volume / "业务资料/MathBank/objects" / checksum[:2] / checksum
    target.write_bytes(b"corrupt")
    now[0] += 3601
    storage.cycle()
    assert path.read_bytes() == b"new image"
    path.unlink()  # Simulated missing local cache, not production data.
    with pytest.raises(ts.TieredStorageError, match="校验失败"):
        storage.hydrate(path)
    assert not path.exists()


def test_active_response_and_busy_worker_prevent_eviction(store):
    storage, root, _, _, now = store
    path = add_file(root)
    storage.cycle()
    now[0] += 3601
    storage.active_requests = 1
    storage.cycle()
    assert path.exists()
    storage.active_requests = 0
    storage.cycle(allow_evict=False)
    assert path.exists()
    storage.cycle()
    assert not path.exists()


def test_readonly_external_view_and_local_replacement_priority(store):
    storage, root, _, _, now = store
    path = add_file(root)
    evict(storage, now)
    before = storage.index.read_bytes()
    external = storage.readable_path(path)
    assert external.read_bytes() == b"new image"
    assert not path.exists()
    assert storage.index.read_bytes() == before
    path.write_bytes(b"new version")
    assert storage.readable_path(path) == path
    assert storage.readable_path(path).read_bytes() == b"new version"


def test_retire_and_restore_root_never_resurrects_old_assets(store):
    storage, root, _, _, now = store
    path = add_file(root)
    evict(storage, now)
    storage.forget(path)
    assert not storage.hydrate(path)
    path = add_file(root, "second.png")
    evict(storage, now)
    uploads = path.parent
    uploads.rename(uploads.with_name("previous-uploads"))
    uploads.mkdir()
    assert not storage.hydrate(path)
    assert not path.exists()


def test_symlinks_rejected_and_wrong_volume_stages(store):
    storage, root, volume, online, now = store
    path = add_file(root)
    storage.volume_provider = lambda expected: None  # Same name, UUID mismatch.
    storage.cycle()
    assert path.exists() and not (volume / "业务资料").exists()
    outside = root.parent / "private"
    outside.write_bytes(b"secret")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ts.TieredStorageError, match="符号链接"):
        storage.hydrate(path)


def test_received_pdf_is_local_offline_then_migrates_and_drains(store):
    storage, root, _, online, now = store
    online[0] = False
    storage.save_received(b"%PDF-test", "课堂资料.pdf", "pdf")
    path = next((root / ".system_generated/received_documents").glob("*.pdf"))
    assert path.read_bytes() == b"%PDF-test"
    online[0] = True
    evict(storage, now)
    assert not path.exists()
    online[0] = False
    with pytest.raises(ts.TieredStorageError):
        storage.restore_local()
    # Uploading the same source again while offline still creates local staging.
    storage.save_received(b"%PDF-test", "再次上传.pdf", "pdf")
    assert path.exists()
    assert storage.restore_local() == 1


def test_large_copy_does_not_hold_activity_or_catalog_locks(store, monkeypatch):
    storage, root, _, _, _ = store
    add_file(root)
    original = storage._copy_verified
    def check(source, target, checksum):
        finished = threading.Event()
        def inspect():
            with storage.activity_lock:
                pass
            with storage._db() as db:
                db.execute("SELECT COUNT(*) FROM entries").fetchone()
            finished.set()
        reader = threading.Thread(target=inspect)
        reader.start()
        assert finished.wait(2), "Background I/O blocked requests/catalog access"
        reader.join()
        return original(source, target, checksum)
    monkeypatch.setattr(storage, "_copy_verified", check)
    storage.cycle()


def test_corrupt_index_does_not_reinitialize_baseline(store):
    storage, root, _, _, _ = store
    storage.index.write_bytes(b"broken index")
    with pytest.raises(Exception):
        ts.TieredStorage(root=root, state_dir=storage.state_dir, config=storage.config)
    assert storage.index.read_bytes() == b"broken index"


def test_original_url_serving_and_backup_word_read_cold_assets(store):
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient
    from mathbank.asset_lifecycle import RetainedUploadStaticFiles, register_asset_store
    from mathbank.backup import _copy_referenced_uploads
    from mathbank.word_export_helper import _resolve_image_paths
    storage, root, _, online, now = store
    path = add_file(root)
    uploads = path.parent
    register_asset_store(uploads, root / ".system_generated/retained_uploads")
    app = Starlette(routes=[Mount('/static', RetainedUploadStaticFiles(
        directory=root/'static', uploads_dir=uploads, url_prefix='static/uploads'))])
    evict(storage, now)
    with TestClient(app) as client:
        response = client.get('/static/uploads/new.png')
        assert response.status_code == 200 and response.content == b"new image"
    now[0] += 3601
    storage.cycle()
    assert not path.exists()
    target = root / "backup-images"
    count, missing = _copy_referenced_uploads(uploads, target, {'uploads/new.png'})
    assert count == 1 and not missing
    assert (target/'new.png').read_bytes() == b"new image"
    now[0] += 3601
    storage.cycle()
    assert _resolve_image_paths(['/static/uploads/new.png'], uploads) == [path]
    now[0] += 3601
    storage.cycle()
    online[0] = False
    with pytest.raises(ts.TieredStorageError):
        _copy_referenced_uploads(uploads, root/'missing-backup', {'uploads/new.png'}, allow_missing_uploads=True)


def test_cold_quarantine_offline_is_recoverable_but_readonly_does_not_restore(store):
    from mathbank.asset_lifecycle import quarantine_asset, register_asset_store
    from mathbank.asset_security import resolve_upload_asset
    storage, root, _, online, now = store
    path = add_file(root)
    register_asset_store(path.parent, root/'.system_generated/retained_uploads')
    evict(storage, now)
    online[0] = False
    assert quarantine_asset('/static/uploads/new.png', uploads_dir=path.parent,
        url_prefix='static/uploads', reason='question-reference-removed')
    assert storage.readable_path(path) == path and not path.exists()
    online[0] = True
    result = resolve_upload_asset('/static/uploads/new.png', uploads_dir=path.parent,
                                  url_prefix='static/uploads')
    assert result.read_bytes() == b"new image"


def test_document_sources_keep_existing_store_contracts(store):
    from mathbank.document_projects import DocumentProjectStore
    from mathbank.document_task_store import DocumentTaskStore
    storage, root, _, online, now = store
    projects = DocumentProjectStore(root/'.system_generated/document_projects')
    row = projects.create(b'%PDF-source', 'source.pdf', {}, {})
    task = DocumentTaskStore(root/'.system_generated/document_tasks')
    task.source('task-a', b'word-source')
    evict(storage, now)
    source = projects.root/row['project_id']/'source.bin'
    assert not source.exists()
    assert projects.get(row['project_id'])['title'] == 'source'
    assert projects.load_source(row['project_id']) == b'%PDF-source'
    assert task.has_source('task-a')  # No hydration required for availability.
    assert task.source('task-a') == b'word-source'
    now[0] += 3601
    storage.cycle()
    task.remove('task-a')
    assert not task.has_source('task-a')
    assert not storage.hydrate(task.root/'task-a.source')


def test_local_read_still_works_after_index_failure(store):
    storage, root, _, _, _ = store
    path = add_file(root)
    storage.cycle()
    storage.index.write_bytes(b'corrupt')
    assert not storage.hydrate(path)
    assert path.read_bytes() == b'new image'
    assert storage.status()['error']


def test_interrupted_historical_records_do_not_pin_cache():
    from concurrent.futures import Future
    from types import SimpleNamespace
    records = {'interrupted': SimpleNamespace(future=None)}
    assert not ts.document_workers_busy(records)
    future = Future()
    records['running'] = SimpleNamespace(future=future)
    assert ts.document_workers_busy(records)
    future.set_result(None)
    assert not ts.document_workers_busy(records)


def test_unplug_between_probe_and_directory_creation_never_creates_mount_label(store, monkeypatch):
    storage, root, volume, _, _ = store
    path = add_file(root)
    original = storage._external
    def unplug():
        external = original()
        volume.rename(volume.with_name('detached-volume'))
        return external
    monkeypatch.setattr(storage, '_external', unplug)
    storage.cycle()
    assert path.read_bytes() == b'new image'
    assert not volume.exists()


def test_replaced_mount_directory_is_rejected_before_mkdir(store):
    storage, _, volume, _, _ = store
    external = storage._external()
    volume.rename(volume.with_name('detached-volume'))
    volume.mkdir()  # A stale same-name ordinary directory is not the volume.
    with pytest.raises(ts.TieredStorageError, match='挂载状态'):
        storage._mkdir_external(external)
    assert list(volume.iterdir()) == []
