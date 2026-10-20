"""Fresh OCR and a pre-fix zero-card cache exercise real, isolated HTTP routes."""
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
    spec = importlib.util.spec_from_file_location("fresh_pdf_http_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCENARIO = r'''
from mathbank.document_project_parse import run_project_legacy_parse
from mathbank.pdf_legacy_parse import completion_request_binding
from mathbank.ai_providers import apply_model_thinking_policy
from mathbank.document_project_service import project_question_ids

bodies={n:f"第{n}道完整原题$x_{{{n}}}={n}$，请计算。" for n in range(1,20)}
answers={n:f"原卷第{n}题解答$x_{{{n}}}+1={n+1}$。" for n in range(1,20)}
if CASE in {"cached_complete_13_recovery","cached_complete_13_manual_recovery"}:
    # A first-pass OCR delimiter error used to hide 14..19 and the answer
    # section. The current source copy must retain this exact extra dollar.
    bodies[13]+="$"
chunks=["一、解答题\n"+"\n\n".join(f"{n}. {bodies[n]}" for n in range(1,11))+"\n数 学 第1页 共2页",
    "\n\n".join(f"{n}. {bodies[n]}" for n in range(11,20))+"\n数 学 第2页 共2页",
    "## 数学参考答案及多维细目表\n"+"\n\n".join(f"{n}. {answers[n]}" for n in range(1,11))+
        "\n数 学 参 考 答 案\n第1页\n,\n2页",
    "\n\n".join(f"{n}. {answers[n]}" for n in range(11,20))+
        "\n数 学 参 考 答 案\n第2页\n,\n2页"]
texts=[f"<!-- MATHBANK_PDF_PAGE:{index+1} -->\n{text}" for index,text in enumerate(chunks)]
expected_source=re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->","",merge_pdf_page_texts(texts))
source_pages=[{"page_number":index+1,"origin":"ocr","markdown":text,"figures":[]}
              for index,text in enumerate(texts)]
with fitz.open() as pdf:
    for number in range(1,5):
        page=pdf.new_page(width=300,height=400)
        page.insert_text((30,40),f"isolated physical page {number}")
    raw=pdf.tobytes()
main.inspect_and_extract_pdf=lambda *_a,**_kw:{"pdf_type":"scanned","pages":[
    {"page_index":index,"needs_ocr":True,"markdown":""} for index in range(4)]}
def fresh_ocr(path,**kwargs):
    index=int(Path(path).stem.rsplit("_",1)[1]);ocr_calls.append(index+1)
    return chunks[index]
main.ocr_pdf_page_image=fresh_ocr
metadata_calls=[];prepared_at_metadata=[]
pid=None
def metadata_post(config,payload,**kwargs):
    data=json.loads(payload["messages"][1]["content"])
    assert set(data)=={"input_sha256","document_sha256","items"}
    assert all("answer_markdown" not in row for row in data["items"])
    metadata_calls.append(deepcopy(payload))
    # The source-only draft must already exist before the optional model call.
    current_pid=pid
    if current_pid is None:
        current_pid=main.DOCUMENT_PROJECTS.list()[0]["project_id"]
    manifest=json.loads((main.DOCUMENT_PROJECTS.root/current_pid/"manifest.json").read_text())
    local=[]
    for name,entry in manifest["stages"].items():
        if name.startswith("local-draft-"):
            local.append(main.DOCUMENT_PROJECTS.load_stage(current_pid,name,entry["input_fingerprint"]))
    assert any(plan["source"]==expected_source and len(plan["questions"])==19 for plan in local)
    prepared_at_metadata.append(deepcopy(main.DOCUMENT_PROJECTS.load_task(current_pid)["pdf_resume_state"]["prepared"]))
    return Reply({"items":[{"id":row["id"],"question_type":"detailed_answer","difficulty":"normal",
        "category_compulsory":"必修二","category_chapter":"统计","category_knowledge":"9.2 用样本估计总体"}
        for row in reversed(data["items"])]})
main.post_chat_completion=metadata_post
main.parse_paper_text_internal=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("Never regenerate bodies"))

if CASE in {"cached_zero_recovery","cached_complete_13_recovery",
            "cached_complete_13_manual_recovery","cached_complete_19_compatible"}:
    project=main.DOCUMENT_PROJECTS.create(raw,"fresh-footer.pdf",OPTIONS,deepcopy(main.get_current_curriculum()))
    pid=project["project_id"];old_id=str(uuid.uuid4());images=[]
    with fitz.open(stream=raw,filetype="pdf") as pdf:
        for index in range(4):
            path=Path(main.TMP_UPLOAD_DIR)/f"pdf_page_{old_id}_{index}.png"
            pdf[index].get_pixmap(dpi=150).save(path)
            images.append(f"/{main.UPLOAD_DIR_REL}/tmp/{path.name}")
    state=prepare_pdf_checkpoint(None,task_id=old_id,document_sha256=project["source_sha256"],
        page_indices=list(range(4)),strategy="force_ocr",verify_suspicions=False,
        resolve_asset=main._document_project_service().asset_resolver)
    state["pages"]={str(index):{"markdown":text,"joint":None} for index,text in enumerate(texts)}
    state["prepared"]={"transcription_sha256":hashlib.sha256(merge_pdf_page_texts(texts).encode()).hexdigest(),
                       "page_texts":deepcopy(texts),"layout_result":None}
    original_prepared=deepcopy(state["prepared"])
    state=seal_pdf_checkpoint(state,assets=images,resolve_asset=main._document_project_service().asset_resolver)
    input_data=main._register_document_input(old_id,raw,"pdf","fresh-footer.pdf",**OPTIONS)
    main.DOCUMENT_TASKS.create(old_id,status="pending",project_id=pid,document_type="pdf",document_input=input_data,temp_assets=images)
    old_count=0 if CASE=="cached_zero_recovery" else 13 if CASE.startswith("cached_complete_13_") else 19
    old_partial=CASE=="cached_zero_recovery"
    old_rows=[{"source_number":n,"content":bodies[n],
        "answer_markdown":answers[n] if old_count==19 else "","image_paths":[],
        "question_type":"detailed_answer","difficulty":"normal","category_compulsory":"必修二",
        "category_chapter":"9. 统计","category_knowledge":"9.2 用样本估计总体"}
        for n in range(1,old_count+1)]
    if CASE.startswith("cached_complete_13_"):
        old_rows[-1]["content"]+="\n"+"\n".join(chunks[1:])
    elif CASE=="cached_complete_19_compatible":
        old_rows[0]["content"]+="历史已接受题文，复用时不重新抄写。"
    old_rows=project_question_ids(pid,old_rows)
    old_diagnostics={"pdf_local_draft":{"rules_version":"pdf-local-draft.rules.v2",
        "status":"partial" if old_partial else "ready","question_count":old_count,"persisted":True,
        "manual_reasons":["answer_section_ambiguous"] if old_partial else []},
        "pdf_draft_metadata":{"status":"completed","calls":1,"new_calls":1},
        "pdf_legacy_requests":{"new_calls":0,"body_generation_disabled":True}}
    main.DOCUMENT_TASKS.complete(old_id,data=old_rows,partial=old_partial,diagnostics=old_diagnostics,
        pdf_resume_state=state,page_images=images,page_numbers=list(range(1,5)),
        pdf_source_pages=source_pages,temp_assets=images)
    profile=completion_request_binding(provider,apply_model_thinking_policy(
        {"model":provider.model_name,"reasoning_effort":provider.reasoning_effort},provider=provider,task="parse"))
    seeded={}
    old_packet={"workflow":"local-draft.v1","questions":old_rows,"preserved_indices":frozenset(range(old_count)),
                "partial":old_partial,"diagnostics":old_diagnostics,"result_assets":{}}
    cached=run_project_legacy_parse(main.DOCUMENT_PROJECTS,pid,expected_source,source_pages,
        main.DOCUMENT_PROJECTS.load_curriculum(pid),source_document_sha256=project["source_sha256"],
        executor=lambda _cache:deepcopy(old_packet),
        asset_receipts={"page:"+str(index+1):state["assets"][image] for index,image in enumerate(images)},
        prompt_version="legacy-source-identities.v1:"+hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest(),
        diagnostics=seeded)
    assert cached==old_packet and not metadata_calls
    old_fingerprint=seeded["pdf_project_legacy_parse"]["fingerprint"]
    original_baseline=deepcopy(main.DOCUMENT_PROJECTS.get_baseline(pid))
    if CASE=="cached_complete_13_manual_recovery":
        manual_before_repair=deepcopy(original_baseline)
        manual_before_repair["questions"][0]["content"]+="教师已经人工校对，不得自动覆盖。"
        saved_manual=main.DOCUMENT_PROJECTS.save_draft(pid,manual_before_repair,0)

with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    if CASE=="fresh_ocr":
        response=client.post("/api/upload/pdf-task",files={"file":("fresh-footer.pdf",raw,"application/pdf")},
            data={"client_request_id":str(uuid.uuid4()),"pdf_strategy":"force_ocr","pdf_verify_suspicions":"false"})
    else:
        response=client.post(f"/api/document-projects/{pid}/analyze")
    assert response.status_code==200,response.text
    dispatched=response.json();pid=dispatched["project_id"]
    first=terminal(client,dispatched["task_id"])
    assert first["status"]=="completed",first.get("error")
    assert not first["partial"] and len(first["data"])==19
    assert len(ocr_calls)==(4 if CASE=="fresh_ocr" else 0)
    assert len(metadata_calls)==(0 if CASE=="cached_complete_19_compatible" else 1)
    assert [q["source_number"] for q in first["data"]]==list(range(1,20))
    assert [q["content"] for q in first["data"]]==([row["content"] for row in old_rows]
        if CASE=="cached_complete_19_compatible" else [bodies[n] for n in range(1,20)])
    assert [q["answer_markdown"] for q in first["data"]]==[answers[n] for n in range(1,20)]
    assert first["diagnostics"]["pdf_legacy_requests"]["new_calls"]==0
    assert first["pdf_source_pages"]==source_pages
    if CASE in {"cached_zero_recovery","cached_complete_13_recovery","cached_complete_13_manual_recovery"}:
        cache=first["diagnostics"]["pdf_project_legacy_parse"]
        assert cache["fingerprint"]==old_fingerprint and not cache["reused"] and cache["executor_calls"]==1
        assert cache["accepted_cache_rejected"]
        assert first["diagnostics"]["pdf_local_cache_recheck"]["status"]=="structure_changed"
        assert first["diagnostics"]["pdf_local_draft"]["rules_version"]=="pdf-local-draft.rules.v3"
        if CASE.startswith("cached_complete_13_"):
            recheck=first["diagnostics"]["pdf_local_cache_recheck"]
            assert recheck["previous_question_count"]==13 and recheck["current_question_count"]==19
            assert recheck["missing_original_answers"]
            census=first["diagnostics"]["pdf_local_draft"]["structure_census"]
            assert census["independent_boundaries_proved"] and census["recovered_boundaries"]
            assert census["final_numbers"]==list(range(1,20))
    elif CASE=="cached_complete_19_compatible":
        cache=first["diagnostics"]["pdf_project_legacy_parse"]
        assert cache["fingerprint"]==old_fingerprint and cache["reused"] and cache["executor_calls"]==0
        assert cache["accepted_cache_checked"] and not cache["accepted_cache_rejected"]
        assert first["diagnostics"]["pdf_local_cache_recheck"]["status"]=="compatible"
    else:
        original_prepared=deepcopy(prepared_at_metadata[0])
    assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["prepared"]==original_prepared
    manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
    plans=[main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"])
           for name,entry in manifest["stages"].items() if name.startswith("local-draft-")]
    if CASE!="cached_complete_19_compatible":
        plan=next(packet for packet in plans if len(packet["questions"])==19)
        assert plan["source"]==expected_source
        for item in plan["items"]:
            n=item["source_number"]
            assert "".join(expected_source[a:b] for a,b in item["stem_ranges"])==bodies[n]
            assert "".join(expected_source[a:b] for a,b in item["answer_ranges"])==answers[n]
            for removed in item["removals"]:
                assert removed["text"]==expected_source[removed["start"]:removed["end"]]

    detail=project_detail(client,pid);working=deepcopy(detail["draft"])
    if CASE.startswith("cached_complete_13_"):
        assert main.DOCUMENT_PROJECTS.get_baseline(pid)==original_baseline
        assert len(detail["latest_result"])==19
        if CASE=="cached_complete_13_manual_recovery":
            assert working==manual_before_repair and len(working["questions"])==13
            # Explicitly review/adopt the rebuilt candidate through normal CAS,
            # retaining the teacher's independent edit. The server must not
            # take this decision for a project with an actual working draft.
            adopted=deepcopy(working)
            adopted["questions"]=deepcopy(detail["latest_result"])
            adopted["questions"][0]["content"]=working["questions"][0]["content"]
            reviewed=client.put(f"/api/document-projects/{pid}/draft",
                json={"expected_revision":detail["revision"],"draft":adopted})
            assert reviewed.status_code==200,reviewed.text
            detail=project_detail(client,pid);working=deepcopy(detail["draft"])
            assert "教师已经人工校对，不得自动覆盖。" in working["questions"][0]["content"]
    assert len(working["questions"])==19
    working["questions"][0]["content"]+="\n教师本地微调。"
    working["questions"][0]["answer_markdown"]+="\n教师校对原答。"
    saved=client.put(f"/api/document-projects/{pid}/draft",json={"expected_revision":detail["revision"],"draft":working})
    assert saved.status_code==200,saved.text
    opened=client.post(f"/api/document-projects/{pid}/open")
    assert opened.status_code==200 and opened.json()["draft"]==saved.json()["draft"]
    first_calls=len(metadata_calls);first_ocr=len(ocr_calls)
    main.post_chat_completion=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("Complete cache cannot repeat paid HTTP"))
    main.ocr_pdf_page_image=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("Complete first OCR cannot rerun"))
    response=client.post(f"/api/document-projects/{pid}/analyze")
    assert response.status_code==200,response.text
    second=terminal(client,response.json()["task_id"])
    assert second["status"]=="completed" and second["data"]==first["data"]
    assert second["diagnostics"]["pdf_project_legacy_parse"]["reused"]
    assert second["diagnostics"]["pdf_draft_metadata"]["new_calls"]==0
    assert len(metadata_calls)==first_calls and len(ocr_calls)==first_ocr
    assert project_detail(client,pid)["draft"]==saved.json()["draft"]
    assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["prepared"]==original_prepared
    with SessionLocal() as db:
        assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
    print("FRESH_RECOVERY_HTTP="+json.dumps({"case":CASE,"questions":19,"original_source_unchanged":True,
        "ocr_calls":len(ocr_calls),"metadata_calls":len(metadata_calls),"replay_new_calls":0,"local_edits_preserved":True}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@pytest.mark.parametrize("case", ["fresh_ocr", "cached_zero_recovery",
    "cached_complete_13_recovery","cached_complete_13_manual_recovery","cached_complete_19_compatible"])
@experimental_pdf_only
def test_fresh_split_footer_and_old_zero_cache_recover_without_rewriting_source(tmp_path, case):
    root=Path(__file__).resolve().parents[1]
    workspace=tmp_path/"source-only-workspace";workspace.mkdir()
    shutil.copy2(root/"main.py",workspace/"main.py")
    shutil.copytree(root/"mathbank",workspace/"mathbank",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    if(root/"templates").is_dir():shutil.copytree(root/"templates",workspace/"templates")
    (workspace/"static").mkdir()
    base=_fixture();script=workspace/"fresh_recovery_api.py"
    script.write_text(base._base()+"\n"+base.HELPERS+"\nCASE="+repr(case)+"\n"+SCENARIO,encoding="utf-8")
    result=subprocess.run([sys.executable,str(script)],cwd=workspace,
        env=dict(os.environ,PYTHONPATH=str(workspace)),capture_output=True,text=True,timeout=65)
    assert result.returncode==0,result.stdout[-7000:]+"\n"+result.stderr[-7000:]
    assert '"questions": 19' in result.stdout and '"replay_new_calls": 0' in result.stdout
