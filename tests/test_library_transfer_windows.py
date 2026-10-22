"""Native sharing round trip, including Windows write-through and recovery.

Run only in a source-only checkout. CI runs this in a Chinese/space project
directory on Windows, with its real filesystem and CRT rather than shims.
"""

import hashlib
import json
from pathlib import Path
import uuid
import zipfile

from mathbank.backup import verify_full_backup
from mathbank.database import Question
from mathbank.library_transfer import inspect_library_zip
from test_library_transfer_api import _export, _question_count, _seed, transfer_api  # noqa: F401
from test_library_transfer_tasks_api import _start_import, _wait, tasks_api  # noqa: F401


def test_native_sharing_backup_images_download_and_restart_receipt(tasks_api, tmp_path):
    api = tasks_api
    original_id = _seed(api, source="原创示例 | Windows 中文与空格")
    original_bytes = (api.uploads / "figure.png").read_bytes()
    archive = _export(api)
    task_id = str(uuid.uuid4())
    accepted = api.client.post(
        "/api/library-transfer/tasks/import", headers=api.headers,
        data={"client_request_id": task_id},
        files={"file": ("题库 分享 测试.zip", archive, "application/zip")},
    )
    assert accepted.status_code == 202, accepted.text
    completed = _wait(api, task_id)
    assert completed["status"] == "completed", completed
    assert completed["result"]["imported_count"] == 1
    assert _question_count(api) == 2

    backup = verify_full_backup(Path(completed["result"]["backup_path"]))
    assert backup["database"]["row_counts"]["questions"] == 1
    with api.sessions() as session:
        original = session.get(Question, original_id)
        imported = session.query(Question).filter(Question.id != original_id).one()
        assert imported.source == original.source
        assert imported.answer_markdown == original.answer_markdown
        assert imported.review == original.review
        assert imported.content == original.content.replace(original.image_paths[0], imported.image_paths[0])
        assert imported.image_layouts == {Path(imported.image_paths[0]).name: {"align": "left", "size": "small"}}
        assert (api.uploads / Path(imported.image_paths[0]).name).read_bytes() == original_bytes
        assert not session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        assert session.connection().exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"

    # A clean manager restart reads the persisted result without appending.
    api.main._LIBRARY_TRANSFER_TASKS.close()
    api.main._LIBRARY_TRANSFER_TASKS = None
    recovered = api.client.get(f"/api/library-transfer/tasks/{task_id}").json()
    assert recovered["status"] == "completed"
    assert recovered["result"] == completed["result"]
    assert _start_import(api, archive, task_id).status_code == 200
    assert _question_count(api) == 2

    export_id = str(uuid.uuid4())
    response = api.client.post(
        "/api/library-transfer/tasks/export", headers=api.headers,
        json={"client_request_id": export_id, "source_keywords": []},
    )
    assert response.status_code == 202, response.text
    exported = _wait(api, export_id)
    assert exported["status"] == "completed", exported
    download = api.client.get(exported["result"]["download_url"])
    assert download.status_code == 200
    destination = tmp_path / "中文 空格 导出" / "题库 分享包.zip"
    destination.parent.mkdir()
    destination.write_bytes(download.content)
    assert inspect_library_zip(destination) == {"question_count": 2, "image_count": 2, "format_version": 1}
    with zipfile.ZipFile(destination) as package:
        manifest = json.loads(package.read("manifest.json"))
        for item in manifest["files"]:
            data = package.read(item["name"])
            assert len(data) == item["size"]
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert not any(".env" in name or "local_token" in name for name in package.namelist())
