"""Real resume routes recover rejected OCR archives in source-only workspaces."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from pdf_feature_contract import experimental_pdf_only


def _fixture():
    path = Path(__file__).with_name("test_pdf_project_repair_flow.py")
    spec = importlib.util.spec_from_file_location("protocol_recovery_http_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCENARIO = r'''
from mathbank.pdf_navigation_cache import bind_pdf_navigation
from mathbank.pdf_ocr_archive import retain_pdf_ocr_response
from mathbank import pdf_ocr_protocol_recovery, pdf_page_vision, pdf_region_vision

# A real, tiny two-page PDF and server-rendered navigation PNGs; no personal
# exam, credentials, production assets or database are copied into this test.
with fitz.open() as pdf:
    for number in (1,2):
        page=pdf.new_page(width=300,height=400)
        page.insert_text((30,40),f"isolated original physical page {number}")
    raw=pdf.tobytes()
chunks=["一、解答题：本题共2小题\n1. 第一道原题$x=1$，求$x+1$。\n\n2. 第二道原题$y=2$，求$y+1$。",
        "数学参考答案\n1. 原答$x+1=2$。\n\n2. 原答$y+1=3$。"]
OPTIONS={**OPTIONS,"pdf_strategy":"layout_aware"}
project=main.DOCUMENT_PROJECTS.create(raw,"archived-protocol.pdf",OPTIONS,deepcopy(main.get_current_curriculum()))
pid=project["project_id"];old_id=str(uuid.uuid4());images=[];paths=[]
with fitz.open(stream=raw,filetype="pdf") as pdf:
    for index in range(2):
        path=Path(main.TMP_UPLOAD_DIR)/f"pdf_page_{old_id}_{index}.png"
        pdf[index].get_pixmap(dpi=150).save(path)
        paths.append(path);images.append(f"/{main.UPLOAD_DIR_REL}/tmp/{path.name}")
state=prepare_pdf_checkpoint(None,task_id=old_id,document_sha256=project["source_sha256"],
    page_indices=[0,1],strategy="layout_aware",verify_suspicions=False,
    resolve_asset=main._document_project_service().asset_resolver)
for index,url in enumerate(images):bind_pdf_navigation(state,index,url)
attempts=[];raw_receipts=[]
def archive(index,attempt,value,finish="stop"):
    content=json.dumps(value,ensure_ascii=False) if isinstance(value,dict) else value
    body={"choices":[{"finish_reason":finish,"message":{"content":content}}]}
    metadata={"stage":"joint_page","page_index":index,"attempt":attempt,
        "http_status":200,"model":"offline-fixture-vision","request_payload_sha256":str(index+1)*64}
    receipt=retain_pdf_ocr_response(main.DOCUMENT_PROJECTS,pid,project["source_sha256"],body,metadata,
        page_number=index+1,image_path=paths[index],run=0)
    raw_receipts.append((deepcopy(receipt),deepcopy(body)))
    attempts.append({"stage":"joint_page","page_index":index,"page_number":index+1,
        "attempt":attempt,"run":0,"status":"failed","http_status":200,
        "finish_reason":finish,"response_retained":True,
        "response_sha256":hashlib.sha256(content.encode()).hexdigest(),
        "error":"原协议校验失败，不能改成识图成功","elapsed_seconds":1.25})
archive(0,1,{"math_markdown_text":"不允许通过别名恢复","figures":[],"ignored_candidates":[]})
archive(0,2,{"markdown":chunks[0],"figures":[],"ignored_candidates":[],
    "page_complete":False,"warnings":["本页仍需核对"],"WARN":"完整回包的附加提示"})
archive(1,1,'{"markdown":"第一次内容被截断',finish="length")
archive(1,2,{"markdown":chunks[1],"figures":[],"ignored_candidates":[],
    "page_complete":True,"warnings":[],"review_required":"false"})
current_run=1 if CASE in {"project_resume_no_top_diagnostics","project_resume_historical_unknown",
    "project_resume_missing_failure_proof"} else 0
state["run"]=current_run
missing_proof=CASE=="project_resume_missing_failure_proof"
state["diagnostics"]={} if missing_proof else {
    "pdf_vision":{"attempts":deepcopy(attempts),"calls":4,"retries":2}}
state=seal_pdf_checkpoint(state,assets=images,resolve_asset=main._document_project_service().asset_resolver)
original_state=deepcopy(state);original_attempts=deepcopy(attempts)
expected_attempts=deepcopy(original_attempts)
if CASE=="project_resume_historical_unknown":
    # Only the most recent top-level diagnostics know these new paid requests
    # started. Their outcome is unknown and must survive another task rebind.
    expected_attempts.extend({"stage":"joint_page","page_index":index,"page_number":index+1,
        "attempt":1,"run":1,"status":"running","usage":{}} for index in range(2))
initial_top_diagnostics=({} if CASE in {"project_resume_no_top_diagnostics",
    "project_resume_missing_failure_proof"} else {"pdf_vision":{"attempts":deepcopy(expected_attempts)}})
input_data=main._register_document_input(old_id,raw,"pdf","archived-protocol.pdf",**OPTIONS)
main.DOCUMENT_TASKS.create(old_id,status="pending",project_id=pid,document_type="pdf",
    document_input=input_data,temp_assets=images)
main.DOCUMENT_TASKS.fail(old_id,"此前两页协议被拒收",pdf_resume_state=state,
    page_images=images,page_numbers=[1,2],temp_assets=images,diagnostics=initial_top_diagnostics)
before_manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
archive_files={receipt["stage"]:{"receipt":deepcopy(before_manifest["stages"][receipt["stage"]]),
    "sha256":hashlib.sha256((main.DOCUMENT_PROJECTS.root/pid/before_manifest["stages"][receipt["stage"]]["file"]).read_bytes()).hexdigest()}
    for receipt,body in raw_receipts}

main.inspect_and_extract_pdf=lambda *_a,**_kw:{"pdf_type":"scanned","pages":[
    {"page_index":index,"needs_ocr":True,"markdown":""} for index in range(2)]}
def forbidden_ocr(*_a,**_kw):
    ocr_calls.append("forbidden")
    raise AssertionError("Archived protocol resume must not make another OCR request")
main.ocr_pdf_page_image=forbidden_ocr
pdf_page_vision.request_pdf_page=forbidden_ocr
pdf_region_vision.request_pdf_regions=forbidden_ocr
main.parse_paper_text_internal=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("No full-body regeneration"))
metadata_calls=[]
def metadata_only(config,payload,**kwargs):
    data=json.loads(payload["messages"][1]["content"])
    assert set(data)=={"input_sha256","document_sha256","items"}
    assert all("answer_markdown" not in row for row in data["items"])
    metadata_calls.append(deepcopy(payload))
    return post(config,payload,**kwargs)
main.post_chat_completion=metadata_only

# Observe the actual destructive rebinding, rather than seeding a diagnostics
# state that bypasses the route. The recovery hook must republish the sealed
# old failures after bind_task erased the former task_parts.
bind_calls=[];original_bind=main.DOCUMENT_PROJECTS.bind_task
def observe_bind(project_id,task_id):
    assert main.DOCUMENT_PROJECTS.load_task(project_id).get("diagnostics",{})==initial_top_diagnostics
    result=original_bind(project_id,task_id)
    cleared=main.DOCUMENT_PROJECTS.load_task(project_id)
    assert cleared=={},cleared
    bind_calls.append(task_id)
    return result
if CASE.startswith("project_resume"):main.DOCUMENT_PROJECTS.bind_task=observe_bind
hook_calls=[];original_restore=pdf_ocr_protocol_recovery.restore_pdf_protocol_pages
def observe_restore(store,project_id,checkpoint,page_urls,page_infos,**kwargs):
    persisted=store.load_task(project_id)
    carried=(persisted.get("diagnostics") or {}).get("pdf_vision",{}).get("attempts",[])
    assert carried==([] if missing_proof else expected_attempts)
    assert checkpoint["pages"]=={} and checkpoint["run"]==current_run
    assert set(page_infos)=={0,1}
    pages,report=original_restore(store,project_id,checkpoint,page_urls,page_infos,**kwargs)
    assert report["has_compatible_archive_pages"]==[1,2]
    if missing_proof:
        assert pages=={} and len(report["pending_pages"])==2,report
    else:
        assert set(pages)=={0,1},report
        assert not report["pending_pages"] and len(report["rejected_archives"])==2
    hook_calls.append(deepcopy(report))
    return pages,report
pdf_ocr_protocol_recovery.restore_pdf_protocol_pages=observe_restore

with TestClient(main.app)as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    endpoint=(f"/api/document-projects/{pid}/resume" if CASE.startswith("project_resume")
              else f"/api/tasks/{old_id}/resume")
    response=client.post(endpoint)
    assert response.status_code==200,response.text
    task_id=response.json()["task_id"]
    if CASE.startswith("project_resume"):assert task_id!=old_id and bind_calls==[task_id]
    else:assert task_id==old_id and not bind_calls
    task=terminal(client,task_id)
    if missing_proof:
        assert task["status"]=="error",task
        assert not ocr_calls and not metadata_calls and len(hook_calls)==1
        latest=main.DOCUMENT_PROJECTS.load_task(pid)
        assert latest["diagnostics"]["pdf_ocr_protocol_recovery"]["has_compatible_archive_pages"]==[1,2]
        for receipt,body in raw_receipts:
            record=main.DOCUMENT_PROJECTS.load_stage(pid,receipt["stage"],receipt["input_fingerprint"])
            assert record["body"]==body
        print("PROTOCOL_RECOVERY_HTTP="+json.dumps({"case":CASE,"recovered_pages":0,"questions":0,
            "ocr_calls":0,"metadata_calls":0,"raw_archives_unchanged":4,"paid_fallback_blocked":True}))
        main.DOCUMENT_TASKS._executor.shutdown(wait=True)
        raise SystemExit(0)
    assert task["status"]=="completed",task.get("error")
    assert not ocr_calls and len(metadata_calls)==1 and len(hook_calls)==1
    assert len(task["data"])==2
    assert [row["content"] for row in task["data"]]==[
        "第一道原题$x=1$，求$x+1$。","第二道原题$y=2$，求$y+1$。"]
    assert [row["answer_markdown"] for row in task["data"]]==["原答$x+1=2$。","原答$y+1=3$。"]
    diagnostics=task["diagnostics"]
    assert diagnostics["pdf_vision"]["attempts"]==expected_attempts
    report=diagnostics["pdf_ocr_protocol_recovery"]
    assert report["recovered"]==2 and report["new_calls"]==report["ai_calls"]==0
    assert diagnostics["pdf_local_draft"]["new_body_generation_calls"]==0
    saved=main.DOCUMENT_PROJECTS.load_task(pid)
    current=saved["pdf_resume_state"]
    assert current["run"]==current_run+1 and set(current["pages"])=={"0","1"}
    for index in range(2):
        page=current["pages"][str(index)]
        assert page["joint"]["markdown"]==chunks[index]
        assert page["joint"]["ocr_succeeded"] is False
        assert page["joint"]["visual_calls"]==0
        assert "_transcription_witness" not in page["joint"]
        assert page["joint"]["attempts"]==[a for a in original_attempts if a["page_index"]==index]
        assert page["joint"]["protocol_recovery"]["source_run"]==0
        assert page["joint"]["protocol_recovery"]["working_run"]==current_run
        if CASE=="project_resume_historical_unknown":
            later=page["joint"]["protocol_recovery"]["later_attempts"]
            assert len(later)==1 and later[0]["run"]==1 and later[0]["status"]=="running"
    assert current["pages"]["0"]["joint"]["layout"]["page_complete"] is False
    assert "待核对：本页仍需核对" in current["pages"]["0"]["joint"]["layout"]["notes"]
    assert current["pages"]["1"]["joint"]["layout"]["page_complete"] is True
    assert any("格式不规范" in note for note in current["pages"]["1"]["joint"]["layout"]["notes"])
    assert [page["origin"] for page in task["pdf_source_pages"]]==["cached_protocol_recovery"]*2
    assert state==original_state and original_state["pages"]=={}
    after_manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
    for receipt,body in raw_receipts:
        name=receipt["stage"]
        assert after_manifest["stages"][name]==archive_files[name]["receipt"]
        path=main.DOCUMENT_PROJECTS.root/pid/after_manifest["stages"][name]["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest()==archive_files[name]["sha256"]
        record=main.DOCUMENT_PROJECTS.load_stage(pid,name,receipt["input_fingerprint"])
        assert record["body"]==body and record["accepted_source"] is False and record["original_verified"] is False
    assert len([name for name in after_manifest["stages"] if name.startswith("ocr-response-")])==4
    assert any(name.startswith("ocr-protocol-recovery-") for name in after_manifest["stages"])
    with SessionLocal()as db:
        assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
    print("PROTOCOL_RECOVERY_HTTP="+json.dumps({"case":CASE,"recovered_pages":2,"questions":2,
        "ocr_calls":0,"metadata_calls":len(metadata_calls),"raw_archives_unchanged":4,
        "original_failures_unchanged":4,"task_rebound":task_id!=old_id}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@pytest.mark.parametrize("case", ["project_resume", "task_resume", "project_resume_no_top_diagnostics",
    "project_resume_historical_unknown", "project_resume_missing_failure_proof"])
@experimental_pdf_only
def test_public_resume_recovers_archived_diagnostics_before_any_new_ocr(tmp_path, case):
    root=Path(__file__).resolve().parents[1]
    workspace=tmp_path/"source-only-workspace";workspace.mkdir()
    shutil.copy2(root/"main.py",workspace/"main.py")
    shutil.copytree(root/"mathbank",workspace/"mathbank",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    if(root/"templates").is_dir():shutil.copytree(root/"templates",workspace/"templates")
    (workspace/"static").mkdir()
    base=_fixture();script=workspace/"protocol_recovery_http.py"
    script.write_text(base._base()+"\n"+base.HELPERS+"\nCASE="+repr(case)+"\n"+SCENARIO,encoding="utf-8")
    result=subprocess.run([sys.executable,"-B",str(script)],cwd=workspace,
        env=dict(os.environ,PYTHONPATH=str(workspace)),capture_output=True,text=True,timeout=65)
    assert result.returncode==0,result.stdout[-7000:]+"\n"+result.stderr[-7000:]
    assert '"ocr_calls": 0' in result.stdout and '"raw_archives_unchanged": 4' in result.stdout
