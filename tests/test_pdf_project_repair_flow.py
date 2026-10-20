"""Real main-route repair wiring in fresh source-only subprocesses; fake AI."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from pdf_feature_contract import experimental_pdf_only


def _base():
    path = Path(__file__).with_name("test_document_project_http_integration.py")
    spec = importlib.util.spec_from_file_location("isolated_project_http_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SCENARIO.split("with TestClient(main.app) as client:\n", 1)[0]


HELPERS = r'''
from mathbank.pdf_task_resume import prepare_pdf_checkpoint,seal_pdf_checkpoint
from mathbank.content_locks import lock_visible_math
from mathbank.pdf_inspector_helper import merge_pdf_page_texts

OPTIONS={"generate_answers":False,"page_range":None,"pdf_strategy":"force_ocr",
         "pdf_verify_suspicions":False,"paper_title":"repair fixture"}
def seed_project(text, *, protocol=None, unknown=False, detail=False, with_layout=False):
    project=main.DOCUMENT_PROJECTS.create(raw,"repair.pdf",OPTIONS,deepcopy(main.get_current_curriculum()))
    pid=project["project_id"];job_id=str(uuid.uuid4())
    page=Path(main.TMP_UPLOAD_DIR)/f"pdf_page_{job_id}_0.png"
    with fitz.open(stream=raw,filetype="pdf") as pdf:
        pdf[0].get_pixmap(dpi=150).save(page)
    page_url=f"/{main.UPLOAD_DIR_REL}/tmp/{page.name}"
    text_with_marker="<!-- MATHBANK_PDF_PAGE:1 -->\n"+text
    original_source=re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->","",merge_pdf_page_texts([text_with_marker]))
    state=prepare_pdf_checkpoint(None,task_id=job_id,document_sha256=project["source_sha256"],
        page_indices=[0],strategy="force_ocr",verify_suspicions=False,
        resolve_asset=main._document_project_service().asset_resolver)
    state["pages"]={"0":{"markdown":text_with_marker,"joint":None}}
    state["prepared"]={"transcription_sha256":hashlib.sha256(merge_pdf_page_texts([text_with_marker]).encode()).hexdigest(),
        "page_texts":[text_with_marker],"layout_result":None}
    if with_layout:
        state["prepared"]["layout_result"]={"schema":"mathbank.pdf-layout.v1",
            "page_texts":[text_with_marker],"pages":[{"page_index":0,"page_image":page_url,"figures":[]}],
            "issues":[],"diagnostics":{}}
    assets=[page_url]
    if detail:
        extra=Path(main.TMP_UPLOAD_DIR)/f"pdf_detail_{job_id}_unrelated.png"
        extra.write_bytes(page.read_bytes());assets.append(f"/{main.UPLOAD_DIR_REL}/tmp/{extra.name}")
    if protocol is not None:
        namespace=job_id.replace("-","")[:16]
        locked,_=lock_visible_math(protocol,namespace)
        if unknown:locked="<unknown-wrapper>"+protocol+"</unknown-wrapper>"
        state["source_namespace"]=namespace
        state["split_input"]={"source_markdown":protocol,"model_source":locked,
            "model_source_sha256":hashlib.sha256(locked.encode()).hexdigest()}
    state=seal_pdf_checkpoint(state,assets=assets,resolve_asset=main._document_project_service().asset_resolver)
    input_data=main._register_document_input(job_id,raw,"pdf","repair.pdf",**OPTIONS)
    main.DOCUMENT_TASKS.create(job_id,status="pending",project_id=pid,document_type="pdf",document_input=input_data,temp_assets=assets)
    main.DOCUMENT_TASKS.fail(job_id,"isolated legacy interruption",pdf_resume_state=state,
        page_images=[page_url],page_numbers=[1],pdf_source_pages=[{"page_number":1,"markdown":text_with_marker}],temp_assets=assets)
    return pid,job_id,state,original_source,assets

def terminal(client,job_id):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        response=client.get(f"/api/tasks/{job_id}/status")
        if response.status_code==200:
            result=response.json()
            if result.get("status") in {"completed","error","cancelled"}:
                with main.DOCUMENT_TASKS._lock:
                    record=main.DOCUMENT_TASKS._records.get(job_id)
                    finished=record is not None and record.future is None
                if finished:return result
        time.sleep(.01)
    raise AssertionError("repair job did not settle")

def source_reference_post(provider,payload,**kwargs):
    return post(provider,payload,**kwargs)
main.post_chat_completion=source_reference_post
'''


PROTOCOL = r'''
original_markdown="<!-- MATHBANK_PDF_PAGE:1 -->\n"+source
original_source=re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->","",merge_pdf_page_texts([original_markdown]))
text=source.replace("求极差","计算极差") if CASE=="changed_source" else source
pid,old_id,state,actual_source,assets=seed_project(text,protocol=original_source,unknown=CASE=="unknown_wrapper")
old_input=deepcopy(state["split_input"])
with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    response=client.post(f"/api/document-projects/{pid}/resume")
    assert response.status_code==200,response.text
    task=terminal(client,response.json()["task_id"])
    assert len(ocr_calls)==0,"a complete OCR checkpoint must be reused"
    stages=main.DOCUMENT_PROJECTS.get(pid)["stage_count"]
    manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
    migrations=[name for name in manifest["stages"] if name.startswith("split-protocol-")]
    if CASE=="valid":
        assert task["status"]=="completed",task.get("error")
        assert len(structure_calls)==1 and len(task["data"])==2
        assert task["diagnostics"]["pdf_resume"]["migrated_split_protocol"] is True
        assert len(migrations)==1
        archived=main.DOCUMENT_PROJECTS.load_stage(pid,migrations[0],old_input["model_source_sha256"])
        assert archived["split_input"]==old_input
        current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["split_input"]
        assert current["source_markdown"]==current["model_source"]==actual_source
    else:
        assert task["status"]=="error",task
        assert len(structure_calls)==0 and not migrations
        assert "缓存已变化" in task["error"]
        assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["split_input"]==old_input
    print("REPAIR_FLOW="+json.dumps({"case":CASE,"ocr_calls":len(ocr_calls),"structure_calls":len(structure_calls)}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


RECOVERY = r'''
good=("一、选择题\n1. 已知数据，选择正确数值。\n\\begin{choices}\n\\item 0.8\n\\item 1.4\n\\item 2.9\n\\item 3.4\n\\end{choices}\n2. 计算$2+3$。")
bad=("一、选择题\n1. 已知数据，选择正确数值。\n\\begin{choices}\n\\item \\quad\n\\item \\quad\n\\item \\quad\n\\item \\quad\n\\end{choices}\n2. 计算$2+3$。")
old_pid,old_id,old_state,_,old_assets=seed_project(good,detail=True,with_layout=CASE=="stale_layout_nav")
old_pixel_receipt=deepcopy(old_state["assets"][old_assets[0]])
# The old project and its navigation/detail files disappear, but its bounded
# runtime task still has a sealed source packet usable through extra_candidates.
main.DOCUMENT_PROJECTS.delete(old_pid)
for url in old_assets:
    main.resolve_upload_asset(url,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL).unlink()
pid,old_current_id,state,_,current_assets=seed_project(bad,with_layout=CASE=="stale_layout_nav")
assert state["assets"][current_assets[0]]==old_pixel_receipt
original_bad=deepcopy(state["prepared"]["page_texts"])
if CASE=="stale_layout_nav":
    metadata_nav=f"/{main.UPLOAD_DIR_REL}/tmp/pdf_page_historical_nav_metadata.png"
    unsealed_nav=f"/{main.UPLOAD_DIR_REL}/tmp/pdf_page_history_unsealed.png"
    main.DOCUMENT_PROJECTS.register_assets(pid,[metadata_nav])
    previous=main.DOCUMENT_PROJECTS.load_task(pid)
    previous["temp_assets"].append(unsealed_nav)
    main.DOCUMENT_PROJECTS.sync_task(pid,previous)
    assert metadata_nav in main.DOCUMENT_PROJECTS.asset_urls(pid)
    assert unsealed_nav not in state["assets"]
edited=good.replace("已知数据，选择正确数值", "教师手工校对，请选择数值")
if CASE=="user_edit":
    main.DOCUMENT_PROJECTS.save_draft(pid,{"questions":[],"paperTitle":"教师稿",
        "page_edits":[{"page_number":1,"markdown":edited}]},0)
with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    response=client.post(f"/api/document-projects/{pid}/analyze")
    assert response.status_code==200,response.text
    task=terminal(client,response.json()["task_id"])
    assert task["status"]=="completed",task.get("error")
    assert len(ocr_calls)==0 and len(structure_calls)==1
    assert len(task["data"])==2
    report=task["diagnostics"]["pdf_transcript_recovery"]
    current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
    assert current["prepared"]["page_texts"]==original_bad,"raw first OCR must stay immutable"
    if CASE in {"recover","stale_layout_nav","user_revert_bad"}:
        assert [row["page_number"] for row in report["recovered_pages"]]==[1],report
        assert report["recovered_pages"][0]["source_kind"]=="retained_task"
        assert "已知数据" in task["data"][0]["content"]
        assert task["data"][0]["content"].count("\\item")==4
        if CASE=="stale_layout_nav":
            saved=main.DOCUMENT_PROJECTS.load_task(pid)
            assert metadata_nav not in saved["pdf_resume_state"]["assets"]
            assert unsealed_nav not in saved["temp_assets"]
            assert old_assets[0] not in saved["temp_assets"]
            assert report["recovered_assets"]==[],"only actual question images belong in recovery assets"
            manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
            stage=manifest["stages"]["pdf_transcript_recovery"]
            recovery_packet=main.DOCUMENT_PROJECTS.load_stage(pid,"pdf_transcript_recovery",stage["input_fingerprint"])
            assert recovery_packet["layout_result"]["pages"][0]["page_image"]==task["page_images"][0]
            assert recovery_packet["layout_result"]["pages"][0]["page_image"] != old_assets[0]
            assert metadata_nav in main.DOCUMENT_PROJECTS.asset_urls(pid),"metadata URLs stay recorded without becoming required files"
    else:
        assert report["recovered_pages"]==[]
        assert "教师手工校对" in task["data"][0]["content"]
        assert main.DOCUMENT_PROJECTS.get_draft(pid)["payload"]["page_edits"][0]["markdown"]==edited
    assert not (main.DOCUMENT_PROJECTS.root/old_pid).exists()
    assert all(not main.resolve_upload_asset(url,uploads_dir=main.UPLOAD_DIR,
        url_prefix=main.UPLOAD_DIR_REL,require_file=False).exists() for url in old_assets)
    if CASE=="user_revert_bad":
        # A deliberate edit after seeing the recovered page remains an edit,
        # even when the teacher's new bytes exactly equal the raw bad baseline.
        detail=project_detail(client,pid)
        draft=deepcopy(detail["draft"])
        draft["page_edits"]=[{"page_number":1,"markdown":original_bad[0]}]
        saved=client.put(f"/api/document-projects/{pid}/draft",
            json={"expected_revision":detail["revision"],"draft":draft})
        assert saved.status_code==200,saved.text
        restarted=client.post(f"/api/document-projects/{pid}/analyze")
        assert restarted.status_code==200,restarted.text
        deliberate=terminal(client,restarted.json()["task_id"])
        assert deliberate["status"]=="completed",deliberate.get("error")
        assert len(ocr_calls)==0 and len(structure_calls)==2
        assert deliberate["diagnostics"]["pdf_transcript_recovery"]["recovered_pages"]==[]
        assert deliberate["diagnostics"]["local_working_copy"]["edited_pages"]==[1]
        assert deliberate["pdf_source_pages"][0]["markdown"]==original_bad[0]
        # Restoring the original splitter preserves the deliberately edited
        # bad source as model input; it must not silently restore old options
        # or keep the retired block route's automatic question exclusion.
        assert [row["source_number"] for row in deliberate["data"]]==[1,2]
        assert "\\quad" in deliberate["data"][0]["content"]
        current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
        assert current["prepared"]["page_texts"]==original_bad
        assert main.DOCUMENT_PROJECTS.get_draft(pid)["payload"]["page_edits"][0]["markdown"]==original_bad[0]
    print("REPAIR_FLOW="+json.dumps({"case":CASE,"ocr_calls":len(ocr_calls),"structure_calls":len(structure_calls),
        "old_project_deleted":True,"bad_prepared_unchanged":True}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


STARTUP = r'''
pid=main.DOCUMENT_PROJECTS.create(raw,"startup.pdf",{},deepcopy(main.get_current_curriculum()))["project_id"]
root_image=Path(main.UPLOAD_DIR)/"project-owned-old.png"
tmp_image=Path(main.TMP_UPLOAD_DIR)/"project-owned-old-slice.png"
orphan=Path(main.UPLOAD_DIR)/"unregistered-old-control.png"
for image in [root_image,tmp_image,orphan]:
    main.Image.new("RGB",(4,4),"white").save(image)
    os.utime(image,(time.time()-86400,time.time()-86400))
root_url=f"/{main.UPLOAD_DIR_REL}/{root_image.name}"
tmp_url=f"/{main.UPLOAD_DIR_REL}/tmp/{tmp_image.name}"
before={image:hashlib.sha256(image.read_bytes()).hexdigest() for image in [root_image,tmp_image]}
main.DOCUMENT_PROJECTS.save_draft(pid,{"questions":[{"content":"未入库工程稿 ![]("+root_url+")",
    "image_paths":[root_url,tmp_url]}],"paperTitle":"未入库工程稿"},0)
main.clean_orphaned_images()
assert all(image.is_file() and hashlib.sha256(image.read_bytes()).hexdigest()==digest for image,digest in before.items())
assert not orphan.exists(),"control proves startup cleanup actually ran"
with SessionLocal() as db:
    assert db.query(Question).count()==0 and db.query(DocumentImportReceipt).count()==0
assert len(ocr_calls)==len(structure_calls)==0
print("REPAIR_FLOW="+json.dumps({"case":CASE,"ocr_calls":0,"startup_project_images_protected":2}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


NATIVE_REFRESH = r'''
with fitz.open() as pdf:
    for number in range(1,8):
        page=pdf.new_page(width=300,height=400);page.insert_text((30,40),f"isolated original physical page {number}")
    raw=pdf.tobytes()
OPTIONS={**OPTIONS,"pdf_strategy":"native_preferred"}
project=main.DOCUMENT_PROJECTS.create(raw,"native-upgrade.pdf",OPTIONS,deepcopy(main.get_current_curriculum()))
pid=project["project_id"];old_id=str(uuid.uuid4())
old_texts=[f"<!-- MATHBANK_PDF_PAGE:{number} -->\n"+("一、解答题\n" if number==1 else "")+f"{number}. 原来{'OCR' if number==4 else 'native'}正文$1+1$。"
           for number in range(1,8)]
old_pages=[{"page_number":number,"origin":"ocr" if number==4 else "native","markdown":old_texts[number-1]}
           for number in range(1,8)]
images=[]
with fitz.open(stream=raw,filetype="pdf") as pdf:
    for index in range(7):
        path=Path(main.TMP_UPLOAD_DIR)/f"pdf_page_{old_id}_{index}.png"
        pdf[index].get_pixmap(dpi=150).save(path);images.append(f"/{main.UPLOAD_DIR_REL}/tmp/{path.name}")
state=prepare_pdf_checkpoint(None,task_id=old_id,document_sha256=project["source_sha256"],
    page_indices=list(range(7)),strategy="native_preferred",verify_suspicions=False,
    resolve_asset=main._document_project_service().asset_resolver)
state["pages"]={"3":{"markdown":old_texts[3],"joint":None}}
old_prepared={"transcription_sha256":hashlib.sha256(merge_pdf_page_texts(old_texts).encode()).hexdigest(),
    "page_texts":deepcopy(old_texts),"layout_result":None}
state["prepared"]=deepcopy(old_prepared)
state=seal_pdf_checkpoint(state,assets=images,resolve_asset=main._document_project_service().asset_resolver)
input_data=main._register_document_input(old_id,raw,"pdf","native-upgrade.pdf",**OPTIONS)
main.DOCUMENT_TASKS.create(old_id,status="pending",project_id=pid,document_type="pdf",document_input=input_data,temp_assets=images)
main.DOCUMENT_TASKS.fail(old_id,"isolated pre-upgrade interruption",pdf_resume_state=state,page_images=images,
    page_numbers=list(range(1,8)),pdf_source_pages=old_pages,temp_assets=images)
manual_page=None
if CASE in {"native6_unchanged_all_page_edits","native5_with_real_page_edit"}:
    # Opening/saving the real page editor serializes every visible page, even
    # when the teacher changed nothing. Those snapshots are not manual edits.
    entries=[{"page_number":index+1,"markdown":text} for index,text in enumerate(old_texts)]
    if CASE=="native5_with_real_page_edit":
        manual_page="<!-- MATHBANK_PDF_PAGE:1 -->\n一、解答题\n1. 教师真实逐页校对正文$9+1$。"
        entries[0]["markdown"]=manual_page
    main.DOCUMENT_PROJECTS.save_draft(pid,{"questions":[],"page_edits":entries,"paperTitle":"保存过的逐页稿"},0)
main.inspect_and_extract_pdf=lambda *_a,**_kw:{"pdf_type":"mixed","pages":[
    {"page_index":index,"markdown":"","needs_ocr":True,
     "quality_reasons":["native_formula_missing_after_quality_upgrade"] if index!=3 else []}
    for index in range(7)]}
from mathbank import pdf_native_regions
pdf_native_regions.plan_pdf_regions=lambda *_a,**_kw:None
ocr_calls.clear()
def refresh_ocr(path,**kwargs):
    index=int(Path(path).stem.rsplit("_",1)[1]);ocr_calls.append(index+1)
    assert index!=3,"the completed physical page 4 OCR must be reused"
    return ("一、解答题\n" if index==0 else "")+f"{index+1}. 质量升级后完整识别正文$1+1$。"
main.ocr_pdf_page_image=refresh_ocr
with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    response=client.post(f"/api/document-projects/{pid}/analyze")
    assert response.status_code==200,response.text
    task=terminal(client,response.json()["task_id"])
    assert task["status"]=="completed",task.get("error")
    expected_refresh=[2,3,5,6,7] if manual_page else [1,2,3,5,6,7]
    assert sorted(ocr_calls)==expected_refresh
    assert len(structure_calls)==1 and len(task["data"])==7
    refreshed=task["diagnostics"]["pdf_native_quality_refresh"]
    assert refreshed["page_numbers"]==expected_refresh
    assert refreshed["original_prepared_preserved"] is True
    assert refreshed["reused_successful_ocr_pages"]==1
    current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
    assert current["pages"]["3"]["markdown"]==old_texts[3]
    assert current["prepared"]["page_texts"][3]==old_texts[3]
    assert all("质量升级后" in current["prepared"]["page_texts"][number-1] for number in expected_refresh)
    assert all("质量升级后" in task["pdf_source_pages"][number-1]["markdown"] for number in expected_refresh)
    assert all(task["pdf_source_pages"][number-1]["origin"]!="manual_working_copy" for number in expected_refresh)
    if manual_page:
        assert task["pdf_source_pages"][0]["markdown"]==manual_page
        assert task["pdf_source_pages"][0]["origin"]=="manual_working_copy"
        assert current["prepared"]["page_texts"][0]==old_texts[0],"manual work is separate from the original page baseline"
        assert task["diagnostics"]["local_working_copy"]["edited_pages"]==[1]
        assert "教师真实逐页校对" in task["data"][0]["content"]
    else:
        assert not task["diagnostics"].get("local_working_copy",{}).get("edited_pages")
        assert all(page["origin"]!="manual_working_copy" for page in task["pdf_source_pages"])
    archive=main.DOCUMENT_PROJECTS.load_stage(pid,"native-input-"+old_prepared["transcription_sha256"][:24],
        old_prepared["transcription_sha256"])
    assert archive["prepared"]==old_prepared and archive["source_pages"]==old_pages
    assert old_prepared["page_texts"]==old_texts
    print("REPAIR_FLOW="+json.dumps({"case":CASE,"ocr_calls":len(ocr_calls),"structure_calls":len(structure_calls),
        "reused_physical_page":4,"archived_native_pages":6}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


def _run(tmp_path, scenario, case, expected_ocr=0):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-workspace"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    script = workspace / "isolated_repair.py"
    script.write_text(_base() + "\nimport os\n" + HELPERS + "\nCASE=" + repr(case) + "\n" + scenario, encoding="utf-8")
    result = subprocess.run([sys.executable, str(script)], cwd=workspace,
        env=dict(os.environ, PYTHONPATH=str(workspace)), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-6500:] + "\n" + result.stderr[-6500:]
    assert '"ocr_calls": '+str(expected_ocr) in result.stdout and "REPAIR_FLOW=" in result.stdout


@pytest.mark.parametrize("case", ["valid", "changed_source", "unknown_wrapper"])
@experimental_pdf_only
def test_legacy_formula_lock_protocol_migration_reuses_ocr_and_rejects_wrong_source(tmp_path, case):
    _run(tmp_path, PROTOCOL, case)


@pytest.mark.parametrize("case", ["recover", "user_edit", "stale_layout_nav", "user_revert_bad"])
@experimental_pdf_only
def test_recovery_from_retained_task_without_old_project_or_preview_preserves_user_edits(tmp_path, case):
    _run(tmp_path, RECOVERY, case)


def test_startup_orphan_cleanup_preserves_old_project_only_figures(tmp_path):
    _run(tmp_path, STARTUP, "startup_project_assets")


@pytest.mark.parametrize('case,expected_ocr',[
    ('native6_quality_upgrade',6),('native6_unchanged_all_page_edits',6),('native5_with_real_page_edit',5),
])
@experimental_pdf_only
def test_native_quality_upgrade_refreshes_only_changed_bad_pages_reusing_completed_ocr_and_true_edits(tmp_path,case,expected_ocr):
    _run(tmp_path,NATIVE_REFRESH,case,expected_ocr=expected_ocr)
