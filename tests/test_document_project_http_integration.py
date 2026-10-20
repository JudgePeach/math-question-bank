"""Production-route integration in a source-only subprocess, never the real DB.

The child imports its own copied app and empty file SQLite. All OCR/model calls
are stubs; ASGI HTTP exercises the real upload/project/import/crop routes.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

from pdf_feature_contract import experimental_pdf_only

SCENARIO = r'''
from copy import deepcopy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest  # Enables the app's normal isolated-test startup behavior.
import requests
requests.sessions.Session.request = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No live HTTP/model calls allowed"))
import pymupdf as fitz
from fastapi.testclient import TestClient
from mathbank.document_projects import DocumentProjectStore
from mathbank.asset_lifecycle import quarantine_asset
from mathbank.pdf_task_resume import seal_pdf_checkpoint
from mathbank.ai_providers import resolve_text_provider
import main
from mathbank.database import SessionLocal, Question, DocumentImportReceipt, engine

ROOT = Path.cwd().resolve()
assert Path(main.__file__).resolve().parent == ROOT
assert Path(engine.url.database).resolve() == ROOT / "math_question_bank.db"
assert not (ROOT / ".env").exists()
now = [time.time()]
main.DOCUMENT_PROJECTS = DocumentProjectStore(ROOT / ".system_generated" / "document_projects", clock=lambda: now[0])
main.schedule_database_export = lambda *_a, **_k: None
main.METADATA_CACHE = {"curriculum_version":"A", "curriculum":{"必修二":{"9. 统计":["9.2 用样本估计总体"]}}}
provider = resolve_text_provider("deepseek-flash", {"DEEPSEEK_API_KEY":"fake-offline-only"})
main.resolve_text_provider = lambda *_a, **_k: provider
source = "一、解答题\n1. 已知数据$1,2,3$，求极差。\n2. 计算$2+3$。"
ocr_calls = []; structure_calls = []
main.inspect_and_extract_pdf = lambda *_a, **_k: {
    "pdf_type":"scanned", "pages":[{"page_index":0,"needs_ocr":True,"markdown":""}]}
def ocr(path, **kwargs):
    ocr_calls.append(str(path)); return source
main.ocr_pdf_page_image = ocr
class Reply:
    status_code = 200
    headers = {}
    def __init__(self, value): self.value = value
    def json(self):
        return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(self.value,ensure_ascii=False)}}],
                "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}
def post(provider, payload, **kwargs):
    structure_calls.append(deepcopy(payload))
    data = json.loads(payload["messages"][1]["content"])
    assert set(data)=={"input_sha256","document_sha256","items"}
    assert payload.get("max_tokens",payload.get("max_completion_tokens"))<=6000
    rows=[]
    for item in data["items"]:
        section=json.dumps(item.get("section_context",""),ensure_ascii=False)
        kind=("multi_choice" if "多项选择题" in section else "single_choice" if "选择题" in section
              else "fill_in_blank" if "填空" in section else "detailed_answer")
        rows.append({"id":item["id"],"question_type":kind,"difficulty":"normal",
            "category_compulsory":"必修二","category_chapter":"统计","category_knowledge":"9.2 用样本估计总体"})
    return Reply({"items":rows})
main.post_chat_completion = post
with fitz.open() as pdf:
    page = pdf.new_page(width=300,height=400)
    page.insert_text((30,40), "isolated PDF fixture")
    raw = pdf.tobytes()

def settled_task(client, task_id):
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        response = client.get(f"/api/tasks/{task_id}/status")
        if response.status_code == 200:
            state = response.json()
            if state.get("status") in {"completed","error","cancelled"}:
                assert state["status"] == "completed", state
                # The task's project callback and future finalizer can finish
                # after publishing its terminal in-memory status.
                with main.DOCUMENT_TASKS._lock:
                    record = main.DOCUMENT_TASKS._records.get(task_id)
                    finished = record is not None and record.future is None
                if finished:
                    return state
        time.sleep(.02)
    raise AssertionError("isolated task did not settle")

def project_detail(client, project_id):
    for _ in range(100):
        response = client.get(f"/api/document-projects/{project_id}")
        if response.status_code == 200:
            return response.json()
        assert response.status_code == 409, response.text
        time.sleep(.01)
    raise AssertionError("project metadata remained busy")

with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    response = client.post("/api/upload/pdf-task", files={"file":("fixture.pdf",raw,"application/pdf")},
        data={"client_request_id":str(uuid.uuid4()),"pdf_strategy":"force_ocr","pdf_verify_suspicions":"false"})
    assert response.status_code == 200, response.text
    uploaded = response.json(); project_id = uploaded["project_id"]; task_id = uploaded["task_id"]
    task = settled_task(client,task_id)
    assert len(ocr_calls) == len(structure_calls) == 1, (ocr_calls, len(structure_calls))
    assert len(task["data"]) == 2 and task["diagnostics"]["pdf_project_partial"] is False
    detail = project_detail(client,project_id)
    assert detail["has_baseline"] and detail["source_pages"]
    assert main.DOCUMENT_PROJECTS.load_task(project_id)["pdf_resume_state"]["prepared"]
    assert detail["latest_result"][0]["category_chapter"] == "9. 统计"
    # The first local preview is durable before optional metadata. Simulate
    # the browser's metadata-only merge by stable card id and ordinary CAS;
    # body and answer bytes remain the local source draft.
    labeled=deepcopy(detail["draft"])
    incoming={row["project_card_id"]:row for row in detail["latest_result"]}
    for row in labeled["questions"]:
        latest=incoming[row["project_card_id"]]
        assert row["content"]==latest["content"] and row["answer_markdown"]==latest["answer_markdown"]
        for key in ("question_type","difficulty","category_compulsory","category_chapter","category_knowledge","classification_review"):
            if key in latest:row[key]=deepcopy(latest[key])
    labeled_response=client.put(f"/api/document-projects/{project_id}/draft",
        json={"expected_revision":detail["revision"],"draft":labeled})
    assert labeled_response.status_code==200,labeled_response.text
    detail=project_detail(client,project_id)

    # A migrated legacy task can have all its original page/figure pixels in
    # the registered retained store. Represent all 31 assets with one real page
    # and 30 byte-identical auxiliary crops/details, keeping this test tiny.
    previous = main.DOCUMENT_PROJECTS.load_task(project_id)
    legacy_state = deepcopy(previous["pdf_resume_state"])
    page_url = previous["page_images"][0]
    page_path = main.resolve_upload_asset(page_url,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL)
    fixture_assets = list(legacy_state["assets"])
    for index in range(30):
        prefix = "pdf_crop_legacy_" if index % 2 else "pdf_detail_legacy_"
        path = Path(main.TMP_UPLOAD_DIR) / (prefix+str(index)+".png")
        path.write_bytes(page_path.read_bytes())
        fixture_assets.append(f"/{main.UPLOAD_DIR_REL}/tmp/{path.name}")
    fixture_assets = list(dict.fromkeys(fixture_assets))
    assert len(fixture_assets) == 31
    legacy_state = seal_pdf_checkpoint(legacy_state,assets=fixture_assets,
        resolve_asset=main._document_project_service().asset_resolver)
    previous.update(pdf_resume_state=legacy_state,temp_assets=fixture_assets)
    main.DOCUMENT_PROJECTS.sync_task(project_id,previous)
    def retire_legacy_assets():
        for value in fixture_assets:
            assert quarantine_asset(value,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL,
                                    reason="isolated-legacy-task-ttl")
            assert not main.resolve_upload_asset(value,uploads_dir=main.UPLOAD_DIR,
                url_prefix=main.UPLOAD_DIR_REL,require_file=False).exists()
    def assert_legacy_assets_restored():
        for value in fixture_assets:
            path = main.resolve_upload_asset(value,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == legacy_state["assets"][value]["sha256"]
    retire_legacy_assets()
    assert client.get("/api/document-projects").status_code == 200
    assert client.post(f"/api/document-projects/{project_id}/open").status_code == 200
    assert all(not main.resolve_upload_asset(value,uploads_dir=main.UPLOAD_DIR,
        url_prefix=main.UPLOAD_DIR_REL,require_file=False).exists() for value in fixture_assets), "listing/open must not restore assets"

    # Save a working card through the actual CAS endpoint, then explicitly resume
    # that draft; no OCR or structure call should run merely to restore it.
    working = deepcopy(detail["draft"])
    working["questions"][0]["content"] += "教师已校对。"
    saved_response = client.put(f"/api/document-projects/{project_id}/draft",
        json={"expected_revision":detail["revision"],"draft":working})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json(); revision = saved["revision"]; working = saved["draft"]
    opened = client.post(f"/api/document-projects/{project_id}/open")
    assert opened.status_code == 200 and opened.json()["draft"]["questions"][0]["content"].endswith("教师已校对。")
    resumed = client.post(f"/api/document-projects/{project_id}/resume")
    assert resumed.status_code == 200, resumed.text
    restored = settled_task(client,resumed.json()["task_id"])
    assert len(ocr_calls) == len(structure_calls) == 1
    persisted = main.DOCUMENT_PROJECTS.load_task(project_id)
    assert persisted.get("pdf_resume_state",{}).get("prepared"), "resume of a working draft must retain first OCR"
    assert persisted.get("pdf_source_pages"), "resume must retain source page evidence"
    assert_legacy_assets_restored()

    # A repeated identical card POST is acknowledged by its transaction receipt,
    # without a second Question row or an extra model request.
    card = working["questions"][0]
    form = {key:str(card.get(key) or "") for key in ("content","answer_markdown","question_type","difficulty",
        "source","category_compulsory","category_chapter","category_knowledge")}
    form.update(import_project_id=project_id,import_card_id=card["project_card_id"],
        import_draft_revision=str(revision),expected_version_code="A",image_paths="[]")
    first = client.post("/api/questions",data=form)
    assert first.status_code == 200, first.text
    second = client.post("/api/questions",data=form)
    assert second.status_code == 200, second.text
    assert first.json()["question"]["id"] == second.json()["question"]["id"]
    with SessionLocal() as db:
        assert db.query(Question).count() == db.query(DocumentImportReceipt).count() == 1

    # Page editing and Analyze reuse first OCR even after working-draft Resume.
    detail = project_detail(client,project_id)
    working = deepcopy(detail["draft"])
    working["page_edits"] = [{"page_number":1,"markdown":source.replace("乙题", "乙题").replace("计算", "求和计算")}]
    updated = client.put(f"/api/document-projects/{project_id}/draft",
        json={"expected_revision":detail["revision"],"draft":working})
    assert updated.status_code == 200, updated.text
    retire_legacy_assets()
    analyzed = client.post(f"/api/document-projects/{project_id}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    analyzed_task = settled_task(client,analyzed.json()["task_id"])
    assert len(ocr_calls) == 1 and len(structure_calls) == 2
    assert analyzed_task["diagnostics"]["local_working_copy"]["edited_pages"] == [1]
    assert "求和计算" in analyzed_task["data"][1]["content"]
    assert_legacy_assets_restored()
    after_analysis = project_detail(client,project_id)
    assert after_analysis["draft"]["questions"][0]["content"].endswith("教师已校对。")

    # Two simultaneous Analyze requests may schedule only one job. Events hold
    # that job active until both HTTP requests have made their dispatch decision.
    original_worker = main.run_pdf_parsing_task
    started = threading.Event(); release = threading.Event(); barrier = threading.Barrier(2)
    blocked_jobs = []
    def blocked_worker(job_id, *_args, **_kwargs):
        blocked_jobs.append(job_id); started.set()
        assert release.wait(5)
        main.DOCUMENT_TASKS.complete(job_id,data=deepcopy(analyzed_task["data"]),
            page_images=analyzed_task["page_images"],page_numbers=analyzed_task["page_numbers"],
            diagnostics=deepcopy(analyzed_task["diagnostics"]))
    main.run_pdf_parsing_task = blocked_worker
    def concurrent_analyze():
        barrier.wait(timeout=3)
        return client.post(f"/api/document-projects/{project_id}/analyze")
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(concurrent_analyze); two = pool.submit(concurrent_analyze)
        try:
            assert started.wait(3)
            responses = [one.result(3),two.result(3)]
            assert sorted(result.status_code for result in responses) == [200,409]
            assert len(blocked_jobs) == 1
        finally:
            release.set()
    accepted = next(result.json() for result in responses if result.status_code == 200)
    settled_task(client,accepted["task_id"])
    main.run_pdf_parsing_task = original_worker

    # Runtime records can expire while a seven-day project and original file
    # remain. Crop reconstructs missing pixels locally from the retained PDF.
    old_ttl = main.DOCUMENT_TASKS._terminal_ttl
    main.DOCUMENT_TASKS._terminal_ttl = dt.timedelta(seconds=0)
    main.DOCUMENT_TASKS.cleanup()
    main.DOCUMENT_TASKS._terminal_ttl = old_ttl
    assert main.DOCUMENT_TASKS.snapshot(analyzed.json()["task_id"]) is None
    for path in Path(main.TMP_UPLOAD_DIR).glob("pdf_page_*.png"):
        path.unlink()
    cropped = client.post("/api/ai/manual-crop-pdf",json={"project_id":project_id,
        "task_id":analyzed.json()["task_id"],"page_index":0,"xmin":5,"ymin":5,"xmax":60,"ymax":60})
    assert cropped.status_code == 200, cropped.text
    crop_url = cropped.json()["image_path"]
    crop_path = main.resolve_upload_asset(crop_url,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL)
    assert crop_path.is_file() and crop_url in main.DOCUMENT_PROJECTS.asset_urls(project_id)
    assert len(ocr_calls) == 1 and len(structure_calls) == 2

    # Formal question assets survive project expiration. The image is promoted
    # by the actual create-question route, rather than a handcrafted DB record.
    detail = project_detail(client,project_id); working = deepcopy(detail["draft"])
    second_card = working["questions"][1]
    second_card["content"] += "\n![]("+crop_url+")"; second_card["image_paths"] = [crop_url]
    saved = client.put(f"/api/document-projects/{project_id}/draft",
        json={"expected_revision":detail["revision"],"draft":working})
    assert saved.status_code == 200, saved.text
    row = saved.json()["draft"]["questions"][1]
    form2 = {key:str(row.get(key) or "") for key in ("content","answer_markdown","question_type","difficulty",
        "source","category_compulsory","category_chapter","category_knowledge")}
    form2.update(import_project_id=project_id,import_card_id=row["project_card_id"],
        import_draft_revision=str(saved.json()["revision"]),expected_version_code="A",image_paths=json.dumps([crop_url]))
    imported = client.post("/api/questions",data=form2)
    assert imported.status_code == 200, imported.text
    persisted_images = imported.json()["question"]["image_paths"]
    assert persisted_images
    image_paths = [main.resolve_upload_asset(value,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL)
                   for value in persisted_images]
    # The seven-day project purge should reclaim page/detail render caches,
    # while preserving the separately promoted images used by formal questions.
    detail_cache = Path(main.TMP_UPLOAD_DIR) / ("pdf_detail_"+project_id+"_isolated.png")
    detail_cache.write_bytes(crop_path.read_bytes())
    main.DOCUMENT_PROJECTS.register_assets(project_id,[f"/{main.UPLOAD_DIR_REL}/tmp/{detail_cache.name}"])
    page_caches = list(Path(main.TMP_UPLOAD_DIR).glob("pdf_page_*.png"))
    assert page_caches and detail_cache.is_file()
    now[0] += 8*86400
    receipt = main.DOCUMENT_PROJECTS.cleanup(active_task_ids=main._document_project_service().active_task_ids())
    assert project_id in receipt["deleted_projects"], receipt
    main._release_document_project_assets(receipt["temp_assets"])
    assert all(path.is_file() for path in image_paths)
    assert all(not path.exists() for path in page_caches) and not detail_cache.exists()
    with SessionLocal() as db:
        assert db.query(Question).count() == db.query(DocumentImportReceipt).count() == 2
    assert len(ocr_calls) == 1 and len(structure_calls) == 2
    print("PROJECT_HTTP_EVIDENCE="+json.dumps({"ocr_calls":len(ocr_calls),"structure_calls":len(structure_calls),
        "questions":2,"receipts":2,"runtime_expired_crop":True,"saved_images_survive_project_cleanup":True,
        "expired_page_and_detail_caches_removed":True,"concurrent_analyze_jobs":len(blocked_jobs),
        "legacy_retained_assets_restored":len(fixture_assets)}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@experimental_pdf_only
def test_project_routes_preserve_first_ocr_working_draft_receipts_and_assets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-workspace"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    runner = workspace / "isolated_project_http.py"
    runner.write_text(SCENARIO, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(workspace))
    result = subprocess.run([sys.executable, str(runner)], cwd=workspace, env=environment,
                            capture_output=True, text=True, timeout=100)
    assert result.returncode == 0, result.stdout[-7000:] + "\n" + result.stderr[-7000:]
    assert '"runtime_expired_crop": true' in result.stdout
    assert '"saved_images_survive_project_cleanup": true' in result.stdout
    assert '"expired_page_and_detail_caches_removed": true' in result.stdout
    assert '"legacy_retained_assets_restored": 31' in result.stdout


def test_slow_progress_callback_cannot_overwrite_completed_project_snapshot(tmp_path):
    """Force the terminal update to wait behind an older publishing callback."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import uuid
    from mathbank.document_project_service import DocumentProjectService
    from mathbank.document_projects import DocumentProjectStore
    from mathbank.document_task_store import DurableDocumentTasks

    store = DocumentProjectStore(tmp_path / "projects")
    project_id = store.create(b"%PDF-local-only", "fixture.pdf", {}, {})["project_id"]
    progress_entered = threading.Event(); release_progress = threading.Event()
    terminal_waiting = threading.Event()
    tasks = None; service = None
    def publish(task_id, snapshot):
        if snapshot.get("status") == "running":
            progress_entered.set()
            assert release_progress.wait(3)
        service.sync(task_id, snapshot)
    tasks = DurableDocumentTasks(store=None, on_snapshot=publish, max_workers=1, max_queue=0)
    service = DocumentProjectService(store, tasks)
    original_lock = tasks._publish_lock
    class ObservedPublishLock:
        def __enter__(self):
            if (tasks.snapshot(task_id) or {}).get("status") == "completed":
                terminal_waiting.set()
            original_lock.acquire()
            return self
        def __exit__(self, *_):
            original_lock.release()
    tasks._publish_lock = ObservedPublishLock()
    task_id = str(uuid.uuid4())
    tasks.create(task_id, status="pending", project_id=project_id, document_type="pdf")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            progress = pool.submit(tasks.update, task_id, status="running", progress=20)
            assert progress_entered.wait(3)
            terminal = pool.submit(tasks.complete, task_id, data=[{"content": "完整结果", "source_number": 1}])
            try:
                assert terminal_waiting.wait(3)
            finally:
                release_progress.set()
            assert progress.result(3) and terminal.result(3)
        saved = store.load_task(project_id)
        assert saved["status"] == "completed"
        assert saved["data"][0]["content"] == "完整结果"
        assert store.get(project_id)["has_baseline"] is True
    finally:
        release_progress.set()
        tasks._executor.shutdown(wait=True)
