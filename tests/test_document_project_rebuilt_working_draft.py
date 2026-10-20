"""A proved local-rule correction cannot overwrite a teacher working draft."""
from copy import deepcopy
import threading

import pytest

from mathbank.document_project_service import DocumentProjectService
from mathbank.document_projects import DocumentProjectStore


class Tasks:
    def __init__(self):
        self._lock = threading.RLock()
        self._records = {}

    def snapshot(self, _task_id):
        return None


@pytest.fixture
def setup(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    pid = store.create(b"%PDF-isolated", "source.pdf", {}, {})["project_id"]
    old = [{"source_number": n, "content": f"old {n}", "answer_markdown": ""} for n in range(1, 14)]
    old[-1]["content"] = "incorrectly swallowed source " * 100
    baseline = {"questions": old, "baselineQuestions": deepcopy(old), "paperTitle": "source"}
    store.save_baseline(pid, baseline)
    rows = [{"source_number": n, "content": f"source {n}", "answer_markdown": f"answer {n}"}
            for n in range(1, 20)]
    task = {"project_id": pid, "status": "completed", "document_type": "pdf", "data": rows,
            "diagnostics": {"pdf_local_cache_recheck": {"status": "structure_changed"}}}
    return DocumentProjectService(store, Tasks()), pid, baseline, task


def test_rebuilt_source_materializes_a_working_draft_but_keeps_the_immutable_baseline(setup):
    service, pid, baseline, task = setup
    service.sync("rebuilt-job", task)
    assert service.store.get_baseline(pid) == baseline
    saved = service.store.get_draft(pid)
    assert saved["revision"] == 1 and saved["metadata"]["has_draft"]
    assert len(saved["payload"]["questions"]) == 19
    assert saved["payload"]["questions"][12]["content"] == "source 13"
    assert all(row["answer_markdown"] for row in saved["payload"]["questions"])


def test_existing_teacher_draft_is_preserved_and_rebuild_is_a_separate_latest_result(setup):
    service, pid, baseline, task = setup
    manual = deepcopy(baseline)
    manual["questions"][0]["content"] = "teacher correction"
    service.save_draft(pid, manual, 0)
    before = service.store.get_draft(pid)
    service.sync("rebuilt-job", task)
    after = service.store.get_draft(pid)
    assert after["revision"] == before["revision"] and after["payload"] == before["payload"]
    assert len(service.detail(pid)["latest_result"]) == 19


def test_teacher_edit_winning_the_compare_and_swap_is_never_overwritten(setup, monkeypatch):
    service, pid, baseline, task = setup
    original = service.save_draft
    manual = deepcopy(baseline)
    manual["questions"][0]["content"] = "concurrent teacher correction"

    def racing_save(project_id, payload, expected_revision):
        service.store.save_draft(project_id, manual, expected_revision)
        return original(project_id, payload, expected_revision)

    monkeypatch.setattr(service, "save_draft", racing_save)
    service.sync("rebuilt-job", task)
    assert service.store.get_draft(pid)["payload"] == manual
    assert service.store.get_draft(pid)["revision"] == 1


@pytest.mark.parametrize("status", ["not_needed", "compatible"])
def test_ordinary_metadata_completion_does_not_create_a_machine_working_copy(setup, status):
    service, pid, baseline, task = setup
    task["diagnostics"]["pdf_local_cache_recheck"]["status"] = status
    service.sync("ordinary-job", task)
    assert not service.store.get(pid)["has_draft"]
    assert service.store.get_draft(pid)["payload"] == baseline
