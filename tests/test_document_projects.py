"""Private, isolated project-store tests; no application/database/API imports."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import pytest

import mathbank.document_projects as module
from mathbank.document_projects import (
    DocumentProjectError, DocumentProjectStore, ProjectConflict, ProjectCorrupt,
    ProjectNotFound, ProjectUnsafePath,
)


@pytest.fixture
def setup(tmp_path):
    now = [1_000_000.0]
    store = DocumentProjectStore(tmp_path / "projects", clock=lambda: now[0])
    project = store.create(b"%PDF-isolated-source", "数学试卷.pdf", {"pdf_strategy": "layout_aware"},
                           {"必修二": {"9. 统计": ["9.2 用样本估计总体"]}})
    return store, project["project_id"], now


def draft(text="工作稿", **extra):
    return {"questions": [{"content": text, "answer_markdown": "D"}], "paperTitle": "校对试卷",
            "pageImages": [], "pageNumbers": [1], "diagnostics": {}, **extra}


def manifest(store, project_id):
    return json.loads((store.root / project_id / "manifest.json").read_text())


def test_create_source_metadata_curriculum_and_restart_are_durable(setup):
    store, project_id, now = setup
    metadata = store.get(project_id)
    assert str(uuid.UUID(project_id)) == project_id
    assert metadata["revision"] == 0 and metadata["title"] == "数学试卷"
    assert metadata["expires_at"] - metadata["last_used"] == 7 * 86400
    assert metadata["last_used_at"] == metadata["last_used"]
    assert store.load_source(project_id) == b"%PDF-isolated-source"
    assert store.load_curriculum(project_id)["必修二"]["9. 统计"] == ["9.2 用样本估计总体"]
    restarted = DocumentProjectStore(store.root, clock=lambda: now[0])
    assert restarted.get(project_id) == metadata
    assert restarted.load_source(project_id) == store.load_source(project_id)


def test_get_list_source_reads_do_not_extend_project_but_touch_does(setup):
    store, project_id, now = setup
    old = store.get(project_id)
    now[0] += 100
    store.list(); store.load_source(project_id); store.get_draft(project_id)
    assert store.get(project_id)["last_used"] == old["last_used"]
    touched = store.touch(project_id)
    assert touched["last_used"] == now[0]
    assert touched["expires_at"] == now[0] + 7 * 86400


def test_same_hash_search_returns_existing_projects_without_overwriting_edits(setup):
    store, project_id, _ = setup
    store.save_draft(project_id, draft("人工改动"), 0)
    second = store.create(store.load_source(project_id), "第二份.pdf", {}, {})
    digest = hashlib.sha256(store.load_source(project_id)).hexdigest()
    assert {row["project_id"] for row in store.find_existing(digest)} == {project_id, second["project_id"]}
    assert store.get_draft(project_id)["payload"]["questions"][0]["content"] == "人工改动"


def test_stage_reuse_requires_matching_fingerprint_and_complete_marker(setup):
    store, project_id, _ = setup
    payload = {"markdown": "第1页", "joint": {"figures": []}}
    store.save_stage(project_id, "ocr.page.1", payload, "first", complete=False)
    assert store.load_stage(project_id, "ocr.page.1", "first") is None
    store.save_stage(project_id, "ocr.page.1", payload, "first", complete=True)
    assert store.load_stage(project_id, "ocr.page.1", "other") is None
    assert store.load_stage(project_id, "ocr.page.1", "first") == payload
    assert store.load_stage(project_id, "absent", "first") is None
    assert store.load_stage(project_id, "ocr.page.1", "first") == payload


def test_baseline_is_independent_immutable_and_draft_cas_is_durable(setup):
    store, project_id, now = setup
    baseline = draft("AI原始结果")
    store.save_baseline(project_id, baseline, "split-input")
    assert store.get_draft(project_id)["payload"] == baseline
    result = store.save_draft(project_id, draft("教师改稿"), expected_revision=0)
    assert result["revision"] == 1
    assert store.get_baseline(project_id) == baseline
    with pytest.raises(ProjectConflict) as conflict:
        store.save_draft(project_id, draft("迟到覆盖"), expected_revision=0)
    assert conflict.value.current_revision == 1
    assert store.get_draft(project_id)["payload"]["questions"][0]["content"] == "教师改稿"
    with pytest.raises(ProjectConflict):
        store.save_baseline(project_id, draft("替换原稿"))
    restarted = DocumentProjectStore(store.root, clock=lambda: now[0])
    assert restarted.get_draft(project_id)["revision"] == 1
    assert restarted.get_baseline(project_id) == baseline


def test_task_updates_deduplicate_large_checkpoints_and_never_overwrite_draft(setup):
    store, project_id, now = setup
    store.bind_task(project_id, "task-one")
    store.save_draft(project_id, draft("教师改稿"), 0)
    snapshot = {"task_id": "task-one", "status": "running", "progress": 10,
                "pdf_resume_state": {"pages": {"0": {"markdown": "首轮OCR"}}},
                "data": [{"content": "迟到AI输出"}]}
    store.sync_task(project_id, snapshot)
    before = manifest(store, project_id)
    now[0] += 10
    store.sync_task(project_id, {**snapshot, "progress": 50})
    after = manifest(store, project_id)
    assert before["task_parts"]["pdf_resume_state"] == after["task_parts"]["pdf_resume_state"]
    assert store.load_task(project_id)["progress"] == 50
    assert store.load_task(project_id)["pdf_resume_state"] == snapshot["pdf_resume_state"]
    assert store.get_draft(project_id)["payload"]["questions"][0]["content"] == "教师改稿"
    assert store.get(project_id)["last_used"] == now[0]
    assert store.get(project_id)["question_count"] == 1


def test_explicit_task_rebind_is_allowed_but_old_callback_is_rejected(setup):
    store, project_id, _ = setup
    store.bind_task(project_id, "old-task")
    store.sync_task(project_id, {"task_id": "old-task", "status": "completed", "data": [{"content": "旧"}]})
    store.save_stage(project_id, "accepted_split", [{"content": "复用"}], "same-input")
    store.bind_task(project_id, "new-task")
    assert store.load_task(project_id) == {}
    with pytest.raises(ProjectConflict):
        store.sync_task(project_id, {"task_id": "old-task", "status": "completed", "data": []})
    assert store.load_stage(project_id, "accepted_split", "same-input") == [{"content": "复用"}]


def test_expired_incomplete_projects_are_cleaned_but_active_tasks_are_preserved(setup):
    store, project_id, now = setup
    store.bind_task(project_id, "active-task")
    now[0] += 7 * 86400 + 1
    assert store.list() == []
    assert store.list(include_expired=True)[0]["project_id"] == project_id
    result = store.cleanup(active_task_ids=["active-task"])
    assert result["deleted_projects"] == [] and project_id in result["busy_projects"]
    assert store.root.joinpath(project_id).exists()
    result = store.cleanup()
    assert result["deleted_projects"] == [project_id]
    with pytest.raises(ProjectNotFound):
        store.get(project_id)


def test_operation_lease_blocks_deletion_even_in_another_store_instance(setup):
    store, project_id, now = setup
    other = DocumentProjectStore(store.root, clock=lambda: now[0])
    with store.operation(project_id):
        store.touch(project_id)
        with pytest.raises(ProjectConflict):
            other.delete(project_id)
        now[0] += 8 * 86400
        assert project_id in other.cleanup()["busy_projects"]
    assert other.delete(project_id)["deleted_projects"] == [project_id]


def test_cleanup_returns_urls_but_never_deletes_shared_or_external_images(setup, tmp_path):
    store, project_id, now = setup
    image = tmp_path / "external-image.png"
    image.write_bytes(b"do-not-delete")
    shared = "/static/uploads/tmp/shared.png"
    only_first = "/static/uploads/tmp/first.png"
    second = store.create(b"different-source", "另一份.pdf", {}, {})["project_id"]
    store.register_assets(project_id, [shared, only_first])
    store.save_draft(second, draft(f"图片 ![]({shared})"), 0)
    assert store.referenced_assets() == {shared, only_first}
    assert store.referenced_assets(exclude_project_id=project_id) == {shared}
    receipt = store.delete(project_id)
    assert set(receipt["temp_assets"]) == {shared, only_first}
    assert store.referenced_assets() == {shared}
    assert image.read_bytes() == b"do-not-delete"


def test_refs_include_task_and_stage_images_without_loading_every_project_in_memory(setup):
    store, project_id, _ = setup
    a = "/static/uploads/tmp/page.png"; b = "/static/uploads/tmp/figure.png"
    store.sync_task(project_id, {"task_id": "task", "temp_assets": [a]})
    store.save_stage(project_id, "prepared", {"image": b}, "input")
    assert store.referenced_assets() == {a, b}


def test_asset_urls_reads_manifest_only_and_task_snapshot_can_omit_task_id(setup, monkeypatch):
    store, project_id, _ = setup
    asset = "/static/uploads/tmp/page.png"
    store.sync_task(project_id, {"status": "interrupted", "page_images": [asset]})
    assert store.get(project_id)["task_id"] is None
    monkeypatch.setattr(store, "_blob", lambda *_: (_ for _ in ()).throw(AssertionError("must not load blobs")))
    assert store.asset_urls(project_id) == {asset}


def test_corrupt_manifest_is_preserved_and_blocks_safe_reference_cleanup(setup):
    store, project_id, now = setup
    (store.root / project_id / "manifest.json").write_text("broken")
    assert store.list()[0]["status"] == "corrupt"
    with pytest.raises(ProjectCorrupt):
        store.referenced_assets()
    now[0] += 8 * 86400
    result = store.cleanup()
    assert result["retained_corrupt_projects"] == [project_id]
    assert (store.root / project_id).is_dir()


def test_missing_required_source_receipt_is_corrupt_and_never_deleted(setup):
    store, project_id, now = setup
    data = manifest(store, project_id); data.pop("source")
    (store.root / project_id / "manifest.json").write_text(json.dumps(data))
    now[0] += 8 * 86400
    assert store.cleanup()["retained_corrupt_projects"] == [project_id]


def test_corrupt_stage_bytes_are_detected_in_load_and_cleanup(setup):
    store, project_id, now = setup
    receipt = store.save_stage(project_id, "ocr.page.1", {"markdown": "old"}, "x")
    (store.root / project_id / receipt["file"]).write_bytes(b"damaged")
    with pytest.raises(ProjectCorrupt):
        store.load_stage(project_id, "ocr.page.1", "x")
    with pytest.raises(ProjectCorrupt):
        store.referenced_assets()
    now[0] += 8 * 86400
    assert project_id in store.cleanup()["retained_corrupt_projects"]


def test_failed_manifest_replace_keeps_previous_draft_and_revision(setup, monkeypatch):
    store, project_id, _ = setup
    store.save_draft(project_id, draft("之前"), 0)
    original_replace = module.os.replace
    def fail_manifest(src, dst, **kwargs):
        if str(dst).endswith("manifest.json"):
            raise OSError("simulated crash before manifest commit")
        return original_replace(src, dst, **kwargs)
    monkeypatch.setattr(module.os, "replace", fail_manifest)
    with pytest.raises(OSError):
        store.save_draft(project_id, draft("未提交"), 1)
    current = store.get_draft(project_id)
    assert current["revision"] == 1
    assert current["payload"]["questions"][0]["content"] == "之前"
    assert not list((store.root / project_id).glob(".*.tmp"))


def test_failed_project_removal_preserves_project_and_does_not_release_assets(setup, monkeypatch):
    store, project_id, now = setup
    store.register_assets(project_id, ["/static/uploads/tmp/preserve.png"])
    monkeypatch.setattr(module.shutil, "rmtree", lambda *_: (_ for _ in ()).throw(OSError("simulated cleanup failure")))
    now[0] += 8 * 86400
    result = store.cleanup()
    assert result["deleted_projects"] == result["temp_assets"] == []
    assert result["retained_corrupt_projects"] == [project_id]
    assert store.referenced_assets() == {"/static/uploads/tmp/preserve.png"}


def test_abandoned_trash_blocks_asset_release(setup):
    store, _, _ = setup
    (store.root / ".trash" / "incomplete-removal").mkdir()
    with pytest.raises(ProjectCorrupt):
        store.referenced_assets()


@pytest.mark.parametrize("project_id", ["../other", "/tmp/other", "..", "abc", "a/b", ""])
def test_project_paths_cannot_escape_root(setup, project_id):
    store, _, _ = setup
    with pytest.raises(ProjectUnsafePath):
        store.get(project_id)


@pytest.mark.parametrize("stage", ["../ocr", "/tmp/ocr", "x/y", "", ".."])
def test_stage_names_cannot_escape_project(setup, stage):
    store, project_id, _ = setup
    with pytest.raises(ProjectUnsafePath):
        store.save_stage(project_id, stage, {}, "x")


def test_project_and_blob_symlinks_never_read_or_delete_targets(setup, tmp_path):
    store, project_id, _ = setup
    external = tmp_path / "outside"; external.mkdir()
    link_id = str(uuid.uuid4()); (store.root / link_id).symlink_to(external, target_is_directory=True)
    with pytest.raises(ProjectUnsafePath):
        store.get(link_id)
    assert link_id in store.cleanup()["retained_corrupt_projects"]
    source = store.root / project_id / "source.bin"
    source.unlink(); target = external / "private.pdf"; target.write_bytes(b"secret")
    source.symlink_to(target)
    with pytest.raises(ProjectCorrupt):
        store.load_source(project_id)
    with pytest.raises(ProjectCorrupt):
        store.delete(project_id)
    assert target.read_bytes() == b"secret"


def test_symlinked_root_or_internal_lock_directory_is_rejected(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    alias = tmp_path / "alias"; alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ProjectUnsafePath):
        DocumentProjectStore(alias)
    root = tmp_path / "projects"; root.mkdir(); (root / ".locks").symlink_to(real, target_is_directory=True)
    with pytest.raises(ProjectUnsafePath):
        DocumentProjectStore(root)


@pytest.mark.parametrize("asset", ["/etc/passwd", "https://evil.test/a.png", "/static/uploads/../secret", "/static/uploads/a%2fb.png"])
def test_registered_assets_must_be_passive_same_origin_upload_paths(setup, asset):
    store, project_id, _ = setup
    with pytest.raises(DocumentProjectError):
        store.register_assets(project_id, [asset])


@pytest.mark.parametrize("payload", [draft(api_key="secret"), draft(diagnostics={"Authorization": "Bearer secret"}),
                                     {"questions": "bad"}, draft(paperTitle="x" * 1001)])
def test_draft_limits_and_credentials_are_rejected_before_commit(setup, payload):
    store, project_id, _ = setup
    with pytest.raises(DocumentProjectError):
        store.save_draft(project_id, payload, 0)
    assert store.get(project_id)["revision"] == 0


def test_json_total_size_and_field_limits_are_bounded(setup, monkeypatch):
    store, project_id, _ = setup
    monkeypatch.setattr(store, "MAX_FIELD_CHARACTERS", 20)
    with pytest.raises(DocumentProjectError):
        store.save_stage(project_id, "ocr", {"markdown": "x" * 21}, "input")
    monkeypatch.setattr(store, "MAX_DRAFT_BYTES", 30)
    with pytest.raises(DocumentProjectError):
        store.save_draft(project_id, draft(), 0)


def test_file_modes_are_private_on_posix(setup):
    if os.name == "nt":
        pytest.skip("POSIX permission-bit verification")
    store, project_id, _ = setup
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / project_id).stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / project_id / "manifest.json").stat().st_mode) == 0o600


def test_windows_io_path_does_not_require_directory_fds_or_no_follow(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_WINDOWS", True)
    store = DocumentProjectStore(tmp_path / "windows-path", clock=lambda: 1000)
    project_id = store.create(b"source", "数学.pdf", {}, {})["project_id"]
    store.save_draft(project_id, draft(), 0)
    assert store.load_source(project_id) == b"source"
    assert store.get_draft(project_id)["revision"] == 1


def test_windows_lock_fallback_is_exercised_without_unconditional_fcntl(setup, monkeypatch):
    store, project_id, _ = setup
    observed = []
    fake_msvcrt = SimpleNamespace(LK_NBRLCK=10, LK_NBLCK=20,
                                 locking=lambda fd, mode, size: observed.append((mode, size)))
    monkeypatch.setattr(module, "fcntl", None)
    monkeypatch.setattr(module, "msvcrt", fake_msvcrt)
    assert store.get(project_id)["project_id"] == project_id
    assert (10, 1) in observed and (20, 1) in observed


def test_missing_platform_file_lock_fails_closed(setup, monkeypatch):
    store, project_id, _ = setup
    monkeypatch.setattr(module, "fcntl", None)
    monkeypatch.setattr(module, "msvcrt", None)
    with pytest.raises(DocumentProjectError):
        store.get(project_id)


@pytest.mark.parametrize("same_instance", [True, False])
def test_short_io_read_waits_for_write_instead_of_losing_project_callback(setup, monkeypatch, same_instance):
    store, project_id, now = setup
    other = store if same_instance else DocumentProjectStore(store.root, clock=lambda: now[0])
    entered = threading.Event(); release = threading.Event(); reading = threading.Event()
    original_write = store._write
    def held_write(directory, filename, content):
        if filename == "manifest.json":
            entered.set()
            assert release.wait(3), "test write release not received"
        return original_write(directory, filename, content)
    monkeypatch.setattr(store, "_write", held_write)
    def read():
        reading.set()
        return other.get(project_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(store.save_draft, project_id, draft("已保存"), 0)
        assert entered.wait(3)
        reader = pool.submit(read)
        assert reading.wait(3)
        # No read/write holds a lease across the simulated slow external task;
        # this event pauses only a short atomic-file commit for the regression.
        release.set()
        assert writer.result(3)["revision"] == 1
        assert reader.result(3)["revision"] == 1


def test_concurrent_identical_baseline_creation_writes_one_immutable_baseline(setup):
    store, project_id, _ = setup
    barrier = threading.Barrier(2)
    def save():
        barrier.wait(timeout=3)
        return store.save_baseline(project_id, draft("初始结果"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(save), pool.submit(save)
        assert one.result(3) == two.result(3)
    assert len(list((store.root / project_id).glob("baseline-*.json"))) == 1
