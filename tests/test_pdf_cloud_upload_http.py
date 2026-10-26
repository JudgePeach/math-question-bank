"""Original upstream PDF uploads without durable drafts, isolated from real data/network."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest


SCENARIO = r'''
from mathbank.ai_timeouts import DOCUMENT_AI_TIMEOUT_SECONDS
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import pytest
import requests
requests.sessions.Session.request = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No live network"))
import pymupdf as fitz
from fastapi.testclient import TestClient
from mathbank.document_projects import DocumentProjectStore
from mathbank.document_task_store import DocumentTaskStore, DurableDocumentTasks
from mathbank.ai_providers import resolve_text_provider
import main
from mathbank.database import SessionLocal, Question, engine

ROOT = Path.cwd().resolve()
assert Path(main.__file__).resolve().parent == ROOT
assert Path(engine.url.database).resolve() == ROOT / "math_question_bank.db"
assert not (ROOT / ".env").exists()
assert main.PDF_CLOUD_ONLY is True
STRATEGY = os.environ["MATHBANK_ISOLATED_PDF_STRATEGY"]
CURRICULUM = {"必修二": {"9. 统计": ["9.2 用样本估计总体"]}}
main.METADATA_CACHE = {"curriculum_version": "A", "curriculum": CURRICULUM}
main.schedule_database_export = lambda *_a, **_k: None
provider = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY": "fake-offline-only"})
main.resolve_text_provider = lambda *_a, **_k: provider
main.DOCUMENT_TASKS.shutdown()
task_store = DocumentTaskStore(ROOT / ".system_generated" / "document_tasks")
main.DOCUMENT_TASK_STORE = task_store
main.DOCUMENT_TASKS = DurableDocumentTasks(store=task_store, transient_document_types={"pdf"},
    on_snapshot=main._sync_document_project, max_workers=1, max_queue=1)
main.DOCUMENT_TASK_INPUTS = {}
projects = DocumentProjectStore(ROOT / ".system_generated" / "document_projects")
main.DOCUMENT_PROJECTS = projects

def forbidden(*_a, **_k):
    raise AssertionError("Original cloud upload entered new local assembly/cache/persistence")

import mathbank.document_project_draft as local_draft
import mathbank.pdf_fragment_draft as fragments
import mathbank.pdf_local_draft as local_rules
import mathbank.pdf_hybrid_request as hybrid
import mathbank.pdf_legacy_parse as legacy
import mathbank.pdf_split_parallel as hybrid_parallel
import mathbank.document_project_parse as parse_cache
local_draft.execute_pdf_local_draft = forbidden
fragments.prepare_pdf_fragment_draft = forbidden
local_rules.prepare_pdf_local_draft = forbidden
# The original upstream flow is allowed to plan native/transcription hybrid
# requests. This source has no reliability witness, so it must retain the
# original full-body API route rather than invent a reliable local card.
hybrid.request_pdf_hybrid = forbidden
legacy.prepare_pdf_task_plan = forbidden
legacy.request_pdf_hybrid = forbidden
legacy.request_pdf_parallel_split = forbidden
hybrid_parallel.request_pdf_parallel_split = forbidden
parse_cache.run_project_legacy_parse = forbidden

source = "一、解答题\n1. 识图原文甲：请计算一加一。\n2. 识图原文乙：请计算二加二。"
main.inspect_and_extract_pdf = lambda *_a, **_k: {
    "pdf_type": "scanned", "pages": [{"page_index": 0, "needs_ocr": True, "markdown": ""}]}
ocr_calls = []
def ocr(path, **kwargs):
    ocr_calls.append({"strategy": "force_ocr", "path": str(path)})
    return source
main.ocr_pdf_page_image = ocr
import mathbank.pdf_page_vision as page_vision
def joint_ocr(path, info, **kwargs):
    assert kwargs.get("structured_fragments", False) is False
    assert "on_response" not in kwargs, "No persistent OCR archive without a project"
    ocr_calls.append({"strategy": "layout_aware", "path": str(path)})
    return page_vision._validated_page({"markdown": source, "figures": [], "ignored_candidates": [],
        "page_complete": True, "warnings": []}, info)
page_vision.request_pdf_page = joint_ocr
# The fixture has no regional candidate; force the full-page v1 protocol.
import mathbank.pdf_native_regions as regional
regional.plan_pdf_regions = lambda *_a, **_k: None

cloud_questions = [
    {"source_number": 1, "content": "API完整题干甲：请计算一加一。", "answer_markdown": "[EXTRACTED_ORIGINAL]API原答案甲：二。",
     "question_type": "detailed_answer", "difficulty": "normal", "category_compulsory": "必修二",
     "category_chapter": "9. 统计", "category_knowledge": "9.2 用样本估计总体"},
    {"source_number": 2, "content": "API完整题干乙：请计算二加二。", "answer_markdown": "[EXTRACTED_ORIGINAL]API原答案乙：四。",
     "question_type": "detailed_answer", "difficulty": "normal", "category_compulsory": "必修二",
     "category_chapter": "9. 统计", "category_knowledge": "9.2 用样本估计总体"},
]
class Reply:
    status_code = 200
    headers = {}
    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"questions": cloud_questions}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

api_calls = []
def post(config, payload, **kwargs):
    assert config is provider
    expected_source = source if STRATEGY == "layout_aware" else "<!-- MATHBANK_PDF_PAGE:1 -->\n" + source
    assert payload["messages"][1]["content"].strip() == expected_source
    assert "content" in payload["messages"][0]["content"]
    assert "answer_markdown" in payload["messages"][0]["content"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.2 and payload["max_tokens"] == 65536
    assert kwargs["timeout"] == DOCUMENT_AI_TIMEOUT_SECONDS
    assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
    assert not payload.get("stream")
    api_calls.append(deepcopy(payload))
    return Reply()
main.post_chat_completion = post

with fitz.open() as pdf:
    page = pdf.new_page(width=300, height=400)
    page.insert_text((30, 40), "isolated PDF fixture")
    raw = pdf.tobytes()

options = {"generate_answers": False, "page_range": None, "pdf_strategy": STRATEGY,
    "pdf_verify_suspicions": False, "paper_title": "cloud-only-fixture"}
old = projects.create(raw, "fixture.pdf", options, CURRICULUM, "old-local-task")
old_id = old["project_id"]
old_draft = {"questions": [{"content": "旧工程人工题干，不应作为新API结果", "answer_markdown": "旧稿答案"}],
    "paperTitle": "old-work", "pageImages": [], "pageNumbers": [], "diagnostics": {}}
projects.save_baseline(old_id, old_draft)
projects.save_draft(old_id, old_draft, 0)
projects.sync_task(old_id, {"task_id": "old-local-task", "document_type": "pdf", "status": "completed",
    "data": old_draft["questions"], "pdf_resume_state": {"transcription_protocol": "mathbank.pdf-page-fragments.v2"}})
assert projects.find_existing(hashlib.sha256(raw).hexdigest())[0]["options"] == options
old_tree = {str(path.relative_to(projects.root)): path.read_bytes()
            for path in projects.root.rglob("*") if path.is_file()}
# If the new upload tries reuse or any new project write, fail immediately.
projects.find_existing = forbidden
projects.create = forbidden
projects.save_draft = forbidden
projects.sync_task = forbidden

def settled(client, task_id):
    until = time.monotonic() + 20
    while time.monotonic() < until:
        reply = client.get(f"/api/tasks/{task_id}/status")
        assert reply.status_code == 200, reply.text
        state = reply.json()
        if state.get("status") in {"completed", "error", "cancelled"}:
            assert state["status"] == "completed", state
            with main.DOCUMENT_TASKS._lock:
                record = main.DOCUMENT_TASKS._records[task_id]
                if record.future is None:
                    return state
        time.sleep(.01)
    raise AssertionError("isolated task did not settle")

with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token": main.LOCAL_TOKEN})
    request_id = str(uuid.uuid4())
    upload = client.post("/api/upload/pdf-task", files={"file": ("fixture.pdf", raw, "application/pdf")},
        data={"client_request_id": request_id, "pdf_strategy": STRATEGY,
              "pdf_verify_suspicions": "false", "paper_title": "cloud-only-fixture"})
    assert upload.status_code == 200, upload.text
    assert "project_id" not in upload.json() and not upload.json().get("reused_project")
    task_id = upload.json()["task_id"]
    task = settled(client, task_id)
    assert len(ocr_calls) == len(api_calls) == 1, (ocr_calls, api_calls)
    assert ocr_calls[0]["strategy"] == STRATEGY
    assert len(task["data"]) == 2
    assert [row["content"] for row in task["data"]] == [row["content"] for row in cloud_questions]
    assert all("API原答案" in row["answer_markdown"] for row in task["data"]), task["data"]
    assert all("project_card_id" not in row and "import_project_id" not in row for row in task["data"])
    assert not set(task["diagnostics"]) & {"pdf_cloud_api", "pdf_local_draft", "pdf_draft_metadata",
        "pdf_project_legacy_parse", "pdf_legacy_requests", "pdf_parallel_split", "pdf_figure_review"}
    assert task["diagnostics"]["pdf_splitting"]["calls"] == 1
    assert task["diagnostics"]["pdf_splitting"]["max_attempts"] == 2
    if STRATEGY == "layout_aware":
        assert task["diagnostics"]["pdf_hybrid"]["mode"] == "original"
        assert task["diagnostics"]["pdf_source_verification"]["status"] == "disabled"
    else:
        assert "pdf_hybrid" not in task["diagnostics"]
    assert all("[EXTRACTED_ORIGINAL]" not in row["answer_markdown"] for row in task["data"])
    assert task["source_available"] is True and task["page_images"]
    assert "project_id" not in main.DOCUMENT_TASKS.snapshot(task_id)
    assert main.DOCUMENT_TASK_INPUTS[task_id] == raw
    download = client.get(f"/api/tasks/{task_id}/source")
    assert download.status_code == 200 and download.content == raw
    assert client.get("/api/document-projects").json() == {"projects": []}
    assert client.post(f"/api/document-projects/{old_id}/open").status_code == 404
    assert {str(path.relative_to(projects.root)): path.read_bytes()
            for path in projects.root.rglob("*") if path.is_file()} == old_tree
    assert list(task_store.root.iterdir()) == [], "PDF task/source/result must never be persisted"
    current = deepcopy(task["data"])
    current[0]["content"] += "教师在本页编辑。"
    # Client-side working edits stay in the active page. Reading immutable
    # task output is still allowed and must not write a local working draft.
    assert client.get(f"/api/tasks/{task_id}/status").json()["data"][0]["content"] == cloud_questions[0]["content"]
    assert current[0]["content"].endswith("教师在本页编辑。")
    assert list(task_store.root.iterdir()) == []
    recovered = DurableDocumentTasks(store=task_store, transient_document_types={"pdf"}, max_workers=1, max_queue=0)
    try:
        assert recovered.snapshot(task_id) is None
    finally:
        recovered.shutdown()
    assert main.DOCUMENT_TASKS.snapshot(task_id)["data"][0]["content"] == cloud_questions[0]["content"]
    with SessionLocal() as db:
        assert db.query(Question).count() == 0
    print("CLOUD_UPLOAD_EVIDENCE=" + json.dumps({"strategy": STRATEGY, "ocr_calls": len(ocr_calls),
        "api_full_body_calls": len(api_calls), "questions": len(task["data"]), "project_created": False,
        "persistent_task_files": 0, "old_project_unchanged": True, "pdf_restart_recovery": False}))
main.DOCUMENT_TASKS.shutdown()
'''


@pytest.mark.parametrize("strategy", ["force_ocr", "layout_aware"])
def test_original_cloud_pdf_upload_uses_api_bodies_without_local_draft(tmp_path, strategy):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-cloud-workspace"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    runner = workspace / "isolated_cloud_upload.py"
    runner.write_text(SCENARIO, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(workspace), MATHBANK_ISOLATED_PDF_STRATEGY=strategy)
    result = subprocess.run([sys.executable, str(runner)], cwd=workspace, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-7000:] + "\n" + result.stderr[-7000:]
    assert '"api_full_body_calls": 1' in result.stdout
    assert '"persistent_task_files": 0' in result.stdout
    assert '"old_project_unchanged": true' in result.stdout
