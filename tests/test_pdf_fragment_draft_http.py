"""Actual fresh v2 upload and legacy resume in an isolated app/empty SQLite."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from pdf_feature_contract import experimental_pdf_only


def _fixture():
    path=Path(__file__).with_name("test_pdf_project_repair_flow.py")
    spec=importlib.util.spec_from_file_location("fragment_draft_http_fixture",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


SCENARIO=r'''
from mathbank import pdf_page_vision, pdf_figures, pdf_source_verify
from mathbank.pdf_page_fragments import SCHEMA as FRAGMENTS_SCHEMA
from mathbank.pdf_fragment_draft import RULES_VERSION as FRAGMENT_RULES_VERSION
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_figure_review import CHECKS

# Use the normal asset namespace inside this disposable source-only root. The
# canonical PNG verifier intentionally validates mathbank.paths.UPLOADS_DIR;
# no production directory is used or copied by this fixture.
main.UPLOAD_DIR=str(ROOT/"static"/"uploads")
main.UPLOAD_DIR_REL="static/uploads"
main.TMP_UPLOAD_DIR=str(ROOT/"static"/"uploads"/"tmp")
Path(main.TMP_UPLOAD_DIR).mkdir(parents=True,exist_ok=True)

if CASE=="v1_resume":
    source="一、解答题\n1. 旧版原题$x=1$。\n2. 旧版原题$y=2$。\n数学参考答案\n1. 原答2。\n2. 原答3。"
    pid,old_id,original_state,actual_source,assets=seed_project(source,with_layout=True)
    original=deepcopy(original_state)
    def forbidden(*_a,**_kw):raise AssertionError("A complete v1 checkpoint must not upgrade or send OCR")
    main.ocr_pdf_page_image=forbidden;pdf_page_vision.request_pdf_page=forbidden
    main.parse_paper_text_internal=forbidden
    with TestClient(main.app)as client:
        client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
        response=client.post(f"/api/document-projects/{pid}/resume")
        assert response.status_code==200,response.text
        task=terminal(client,response.json()["task_id"])
        assert task["status"]=="completed",task.get("error")
        assert len(task["data"])==2 and not ocr_calls and len(structure_calls)==1
        current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
        assert "transcription_protocol" not in current
        assert current["prepared"]==original["prepared"]
        assert not current["pages"]["0"].get("joint")
        assert task["diagnostics"]["pdf_local_draft"].get("assembly")!="first_pass_blocks"
        assert original_state==original
    main.DOCUMENT_TASKS._executor.shutdown(wait=True)
    print("FRAGMENT_DRAFT_HTTP="+json.dumps({"case":CASE,"questions":2,"ocr_posts":0,"full_body_posts":0}))
    raise SystemExit(0)

has_slot=CASE=="fresh_verified_slot"
has_blank=CASE=="fresh_blank"
delayed_page7=CASE in {"delayed_p7","delayed_p7_failure","delayed_p7_unedited","delayed_p7_resume_missing"}
page7_entered=threading.Event();release_page7=threading.Event()
early_raw_snapshot=None;early_baseline=None
page_count=9 if has_blank else 8
with fitz.open()as pdf:
    for number in range(1,page_count+1):
        page=pdf.new_page(width=300,height=400)
        if has_blank and number==6:continue
        page.insert_text((20,30),f"isolated original physical page {number}")
        if number==2 and has_slot:
            page.draw_line((190,220),(225,135));page.draw_line((225,135),(265,220));page.draw_line((265,220),(190,220))
            page.insert_text((220,130),"P");page.insert_text((180,232),"A");page.insert_text((265,232),"B")
    raw=pdf.tobytes()
bodies={n:f"第{n}道原题$x_{{{n}}}={n}$，保留  两个空格。" for n in range(1,20)}
for n in range(1,12):
    bodies[n]+="\n\\begin{choices}\n"+"\n".join(f"\\item ${n}+{k}$" for k in range(4))+"\n\\end{choices}"
bodies[13]="原题偶函数$f(x+a)=x^2+2x+3$，则$a=$\\fillin $$ ."
if has_slot:bodies[10]+="\n[插图待补: 图1]"
answer_bodies={n:f"原版第{n}题解答$x_{{{n}}}+1={n+1}$。" for n in range(1,20)}
answer_bodies[19]="原版第19题解答$\\begin{cases}y^2=4x\\\\x=my+a$。保留未闭环境，不能吞后块。"
def frag(text,role="stem",number=None,edge="none"):
    return {"role":role,"number":number,"edge":edge,"text":text}
def numbered(n,role="stem",edge="none",text=None):
    return frag(str(n)+". "+(text if text is not None else bodies[n] if role=="stem" else answer_bodies[n])+"\n\n",role,n,edge)
pages=[[],[],[],[],[],[],[],[]]
pages[0]=[frag("一、单项选择题：本题共8小题，每题只有一个正确选项。\n\n","context")]+[numbered(n)for n in range(1,7)]
pages[1]=[numbered(7),numbered(8),frag("二、多项选择题：本题共3小题。\n\n","context")]+[numbered(n)for n in range(9,12)]+[frag("三、填空题：本题共3小题。\n\n","context")]+[numbered(n)for n in range(12,15)]
pages[2]=[frag("四、解答题：本题共5小题。\n\n","context")]+[numbered(n)for n in range(15,18)]
pages[3]=[numbered(18),numbered(19)]
pages[4]=[frag("数学参考答案与解析\n\n","context")]+[numbered(n,"answer")for n in range(1,10)]+[numbered(10,"answer","to_next",answer_bodies[10]+"原解前半。")]
pages[5]=[frag("第10题后半，故选项B错误。\n\n","answer",None,"from_previous")]+[numbered(n,"answer")for n in range(11,15)]+[numbered(15,"answer","to_next",answer_bodies[15]+"原解前半。")]
pages[6]=[frag("第15题后半，$S_3>7$或$S_3<-9$。\n\n","answer",None,"from_previous"),numbered(16,"answer"),numbered(17,"answer"),numbered(18,"answer","to_next",answer_bodies[18]+"①原解前半。")]
pages[7]=[frag("②第18题后半，有且仅有一个实根。\n\n","answer",None,"from_previous"),numbered(19,"answer")]
if has_blank:pages.insert(5,[])
packets=[]
for index,blocks in enumerate(pages):
    figures=[]
    if index==1 and has_slot:
        figures=[{"slot":"图1","bbox":{"left":590,"top":300,"right":940,"bottom":610},
            "candidate_ids":[],"review_required":True,"review_reason":"独立核验完整顶点"}]
    packets.append({"schema":FRAGMENTS_SCHEMA,"blocks":blocks,"figures":figures,
        "ignored_candidates":[],"page_complete":True,"warnings":[]})
raw_texts=["".join(b["text"]for b in packet["blocks"])for packet in packets]
main.inspect_and_extract_pdf=lambda *_a,**_kw:{"pdf_type":"scanned","pages":[
    {"page_index":index,"needs_ocr":not(has_blank and index==5),"markdown":"",
     "confirmed_blank":bool(has_blank and index==5)}for index in range(page_count)]}
vision_provider=resolve_ocr_provider("siliconflow",{"SILICONFLOW_API_KEY":"offline-fixture-only",
    "SILICONFLOW_OCR_MODEL":"Qwen/Qwen3.8-27B"})
pdf_page_vision.resolve_ocr_provider=lambda *_a,**_kw:vision_provider
pdf_figures.resolve_ocr_provider=lambda *_a,**_kw:vision_provider
main.resolve_ocr_provider=lambda *_a,**_kw:vision_provider
vision_posts=[];metadata_posts=[];figure_posts=[];preview_observations=[];saved_teacher=None
unexpected_calls=[]
def forbidden_rewrite(*_a,**_kw):
    unexpected_calls.append("unexpected_body_or_layout_request")
    raise AssertionError("C cannot invoke full-body, extra layout OCR or old local question scanner")
main.parse_paper_text_internal=forbidden_rewrite
main.ocr_pdf_page_image=forbidden_rewrite
pdf_figures.post_chat_completion=forbidden_rewrite
regional_planner_calls=[]
if CASE=="fresh_regional_candidate":
    from mathbank import pdf_native_regions, pdf_region_vision
    def regional_candidate(page,page_info):
        regional_planner_calls.append(page_info["page_index"])
        return {"kind":"mixed","pieces":[{"text":"可靠原生片段"},{"region_id":"r1"}],
            "regions":[{"id":"r1","bbox":[100,100,900,900],
                "page_info":{"page_index":page_info["page_index"],"candidates":[]}}],
            "native_characters":6,"area_ratio":0.64}
    pdf_native_regions.plan_pdf_regions=regional_candidate
    pdf_region_vision.request_pdf_regions=forbidden_rewrite
from mathbank import pdf_local_draft
pdf_local_draft.prepare_pdf_local_draft=forbidden_rewrite
# Optional textual suspicion checking is outside this structural entry test;
# this read-only stub certifies nothing and never changes source or cards.
pdf_source_verify.verify_pdf_source_suspicions=lambda *_a,**_kw:{"status":"fixture_read_only_skipped",
    "calls":0,"checked":0,"confirmed":0,"pending":0}
def vision_post(config,payload,**kwargs):
    text=next(p["text"]for p in payload["messages"][-1]["content"]if p["type"]=="text")
    assert FRAGMENTS_SCHEMA in text and '"markdown":' not in text
    number=int(re.findall(r'"page_number"\s*:\s*(\d+)',text)[-1])
    vision_posts.append((number,deepcopy(payload)))
    assert kwargs.get("retry_connection")is False and payload.get("enable_thinking")is False
    if delayed_page7 and number==7:
        page7_entered.set()
        assert release_page7.wait(20),"Delayed-page fixture was never released"
        if CASE in {"delayed_p7_failure","delayed_p7_resume_missing"} and sum(n==7 for n,p in vision_posts)==1:
            # A known nonretryable reply makes the page finally fail without
            # purchasing another model request or changing production budgets.
            return type("FailedPage",(),{"status_code":400,"headers":{},"json":lambda _self:{}})()
    return Reply(packets[number-1])
pdf_page_vision.post_chat_completion=vision_post

def preview_before_optional_request():
    global saved_teacher
    pid=main.DOCUMENT_PROJECTS.list()[0]["project_id"]
    active=main.DOCUMENT_PROJECTS.get(pid)["task_id"]
    task=main.DOCUMENT_TASKS.snapshot(active)
    assert len(task["data"])==19 and sum(bool(q.get("answer_markdown"))for q in task["data"])==19
    assert task["diagnostics"]["pdf_local_draft"]["persisted"]is True
    manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
    previews=[(name,entry)for name,entry in manifest["stages"].items()
              if name.startswith("figure-preview-" if has_slot else "local-preview-")]
    assert previews,"Editable source must be durable before optional paid stage"
    preview_observations.append({"questions":len(task["data"]),"status":task["status"]})
    if CASE=="delayed_p7_unedited":return pid
    if saved_teacher is None:
        detail=project_detail(api_client,pid);draft=deepcopy(detail["draft"])
        assert len(draft["questions"])==19
        draft["questions"][0]["content"]+="\n教师先行校对题干。"
        draft["questions"][0]["answer_markdown"]+="\n教师先行校对原答。"
        saved=api_client.put(f"/api/document-projects/{pid}/draft",
            json={"expected_revision":detail["revision"],"draft":draft})
        assert saved.status_code==200,saved.text
        saved_teacher=deepcopy(saved.json()["draft"])
        stale=api_client.put(f"/api/document-projects/{pid}/draft",
            json={"expected_revision":detail["revision"],"draft":draft})
        assert stale.status_code==409,"Actual CAS conflict must protect teacher source"
    return pid

def optional_post(config,payload,**kwargs):
    body=payload["messages"][-1]["content"]
    if isinstance(body,list):
        text=next(p["text"]for p in body if p["type"]=="text")
        assert "输入资料JSON：\n" in text,"Only image-verdict requests are permitted"
        metadata=json.loads(text.split("输入资料JSON：\n",1)[1])
        preview_before_optional_request()
        figure_posts.append(deepcopy(metadata))
        assert metadata["stage"]=="first","A verified fixture crop must not require retries"
        return Reply({"frame":metadata["frame"],"source_sha256":metadata["source_sha256"],
            "figures":[{"id":f["id"],"slot_id":f["slot_id"],"crop_sha256":f["crop_sha256"],
                "decision":"accepted","checks":{key:True for key in CHECKS},"evidence":"isolated fixture verdict"}
                for f in metadata["figures"]]})
    value=json.loads(body)
    assert set(value)=={"input_sha256","document_sha256","items"},"Never send/rewrite the complete exam body"
    assert all("answer_markdown"not in q for q in value["items"])
    assert payload.get("max_tokens",payload.get("max_completion_tokens"))<=6000
    preview_before_optional_request();metadata_posts.append(deepcopy(payload))
    return post(config,payload,**kwargs)
main.post_chat_completion=optional_post

with TestClient(main.app)as api_client:
    api_client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    def upload_request():
        return api_client.post("/api/upload/pdf-task",files={"file":("first-fragments.pdf",raw,"application/pdf")},
            data={"client_request_id":str(uuid.uuid4()),"pdf_strategy":"layout_aware",
                  "pdf_verify_suspicions":"true"if has_slot else "false"})
    if delayed_page7:
        with ThreadPoolExecutor(max_workers=1)as sender:
            response=sender.submit(upload_request).result(timeout=5)
    else:response=upload_request()
    assert response.status_code==200,response.text
    upload=response.json();pid=upload["project_id"]
    if delayed_page7:
        try:
            assert page7_entered.wait(5),"The slow page must really be in flight"
            deadline=time.monotonic()+8
            early=None
            while time.monotonic()<deadline:
                status=api_client.get(f"/api/tasks/{upload['task_id']}/status")
                assert status.status_code==200,status.text
                candidate=status.json()
                if len(candidate.get("data")or[])==19:
                    early=candidate;break
                assert candidate.get("status")not in {"completed","error","cancelled"},candidate
                time.sleep(.01)
            assert early is not None,"All 19 stems must be editable before slow answer-page completion"
            assert not release_page7.is_set() and not metadata_posts and not figure_posts
            assert early["partial"]is True and early["diagnostics"]["pdf_local_draft"]["persisted"]is True
            assert 7 in early["diagnostics"]["pdf_local_draft"]["pending_page_numbers"]
            assert all(q.get("classification_review",{}).get("required")for q in early["data"])
            detail=project_detail(api_client,pid);draft=deepcopy(detail["draft"])
            assert len(draft["questions"])==19
            assert sum(bool(q.get("answer_markdown"))for q in draft["questions"])<19
            early_baseline=deepcopy(main.DOCUMENT_PROJECTS.get_baseline(pid))
            before=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
            assert "6"not in before["pages"]
            early_raw_snapshot={index:{"markdown":row["markdown"],
                "joint_markdown":row["joint"]["markdown"],"fragments":deepcopy(row["joint"]["fragments"])}
                for index,row in before["pages"].items()}
            if CASE!="delayed_p7_unedited":
                draft["questions"][0]["content"]+="\n教师在慢页返回前校对题干。"
                draft["questions"][0]["answer_markdown"]+="\n教师在慢页返回前校对原答。"
                saved=api_client.put(f"/api/document-projects/{pid}/draft",
                    json={"expected_revision":detail["revision"],"draft":draft})
                assert saved.status_code==200,saved.text
                saved_teacher=deepcopy(saved.json()["draft"])
                stale=api_client.put(f"/api/document-projects/{pid}/draft",
                    json={"expected_revision":detail["revision"],"draft":draft})
                assert stale.status_code==409
            else:
                assert detail["revision"]==0 and not detail["has_draft"]
        finally:
            release_page7.set()
    first=terminal(api_client,upload["task_id"])
    if CASE in {"delayed_p7_failure","delayed_p7_resume_missing"}:
        assert first["status"]=="error"and first["partial"]is True,first
        assert len(first["data"])==19 and len(vision_posts)==8
        assert sum(n==7 for n,p in vision_posts)==1
        assert not metadata_posts and not figure_posts and not unexpected_calls
        assert any(row["page_number"]==7 for row in first["diagnostics"]["pdf_failed_pages"])
        assert first["diagnostics"]["pdf_local_draft"]["status"]=="partial"
        assert 7 in first["diagnostics"]["pdf_local_draft"]["pending_page_numbers"]
        detail=project_detail(api_client,pid)
        assert detail["draft"]==saved_teacher
        assert main.DOCUMENT_PROJECTS.get_baseline(pid)==early_baseline
        state=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
        assert "6"not in state["pages"]
        for index,original in early_raw_snapshot.items():
            row=state["pages"][index]
            assert row["markdown"]==original["markdown"]and row["joint"]["markdown"]==original["joint_markdown"]
            assert row["joint"]["fragments"]==original["fragments"]
        if CASE=="delayed_p7_resume_missing":
            before_pages=deepcopy(state["pages"])
            resumed=api_client.post(f"/api/document-projects/{pid}/resume")
            assert resumed.status_code==200,resumed.text
            restored=terminal(api_client,resumed.json()["task_id"])
            assert restored["status"]=="completed",restored.get("error")
            assert len(vision_posts)==9 and sum(n==7 for n,p in vision_posts)==2
            assert sorted(n for n,p in vision_posts if n!=7)==[1,2,3,4,5,6,8]
            assert len(metadata_posts)==1 and not figure_posts and not unexpected_calls
            assert len(restored["data"])==19 and sum(bool(q["answer_markdown"].strip())for q in restored["data"])==19
            assert "pdf_draft_restore"not in restored["diagnostics"],"A real source continuation is machine output, not a working-copy ACK"
            current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
            assert "6"in current["pages"]
            for index,original in before_pages.items():assert current["pages"][index]==original
            detail=project_detail(api_client,pid)
            assert detail["draft"]==saved_teacher
            assert len(detail["latest_result"])==19 and sum(bool(q["answer_markdown"].strip())for q in detail["latest_result"])==19
            assert detail["latest_result"][0]["content"]!=saved_teacher["questions"][0]["content"]
            assert main.DOCUMENT_PROJECTS.get_baseline(pid)==early_baseline
            with SessionLocal()as db:assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
            print("FRAGMENT_DRAFT_HTTP="+json.dumps({"case":CASE,"questions":19,"answers":19,
                "new_resume_ocr_posts":1,"full_body_posts":0,"teacher_CAS_preserved":True,"raw7_preserved":True}))
            main.DOCUMENT_TASKS._executor.shutdown(wait=True)
            raise SystemExit(0)
        print("FRAGMENT_DRAFT_HTTP="+json.dumps({"case":CASE,"questions":19,"partial":True,
            "ocr_posts":8,"full_body_posts":0,"teacher_CAS_preserved":True,"page7_known_failure":True}))
        main.DOCUMENT_TASKS._executor.shutdown(wait=True)
        raise SystemExit(0)
    assert first["status"]=="completed",first.get("error")
    expected_ocr=[number for number in range(1,page_count+1)if not(has_blank and number==6)]
    assert len(vision_posts)==8 and sorted(n for n,p in vision_posts)==expected_ocr
    assert not unexpected_calls
    if CASE=="fresh_regional_candidate":assert regional_planner_calls==[]
    assert len(metadata_posts)==1 and len(figure_posts)==(1 if has_slot else 0)
    assert len(first["data"])==19 and [q["source_number"]for q in first["data"]]==list(range(1,20))
    assert sum(q["content"].count("\\item")for q in first["data"][:11])==44
    assert sum(bool(q["answer_markdown"].strip())for q in first["data"])==19
    assert first["diagnostics"]["pdf_local_draft"]["assembly"]=="first_pass_blocks"
    assert first["diagnostics"]["pdf_local_draft"]["new_body_generation_calls"]==0
    assert first["data"][12]["content"].strip()==bodies[13]
    assert "15."not in first["data"][13]["content"] and len(first["data"][13]["content"])<300
    for n,tail in [(10,"第10题后半"),(15,"第15题后半"),(18,"第18题后半")]:
        assert first["data"][n-1]["answer_markdown"].count(tail)==1
        assert answer_bodies[n]in first["data"][n-1]["answer_markdown"]
    assert answer_bodies[19]in first["data"][18]["answer_markdown"]
    state=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
    assert state["transcription_protocol"]==FRAGMENTS_SCHEMA
    for index,text in enumerate(raw_texts):
        if has_blank and index==5:
            assert str(index)not in state["pages"]
            empty=first["pdf_source_pages"][index]
            assert empty["markdown"]=="" and empty["fragments"]["blocks"]==[]
            continue
        cached=state["pages"][str(index)]
        assert cached["joint"]["markdown"]==text
        assert cached["markdown"]==f"<!-- MATHBANK_PDF_PAGE:{index+1} -->\n"+text
        assert "fragments"in cached["joint"]
    if delayed_page7:
        assert main.DOCUMENT_PROJECTS.get_baseline(pid)==early_baseline
        for index,original in early_raw_snapshot.items():
            row=state["pages"][index]
            assert row["markdown"]==original["markdown"]and row["joint"]["markdown"]==original["joint_markdown"]
            assert row["joint"]["fragments"]==original["fragments"]
        assert sum(n==7 for n,p in vision_posts)==1
    # The assembly plan has executable, exact field ranges in its current
    # canonical source; neither bad math nor crop URL length changes break it.
    manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
    plans=[main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"])
        for name,entry in manifest["stages"].items()if name.startswith("local-draft-")]
    plan=next(p for p in plans if p.get("workflow")=="page-fragments.v2"
        and p.get("rules_version")==FRAGMENT_RULES_VERSION)
    for item in plan["items"]:
        assert "".join(plan["source"][a:b]for a,b in item["stem_ranges"])==item["content"]
        assert "".join(plan["source"][a:b]for a,b in item["answer_ranges"])==item["answer_markdown"]
    if has_slot:
        assert preview_observations[0]["questions"]==19
        assert "![插图]("in first["data"][9]["content"]
        assert "[插图待补"not in first["data"][9]["content"]
        assert "[插图待补: 图1]"in state["pages"]["1"]["joint"]["markdown"]
        source_page=first["pdf_source_pages"][1]
        assert any(r.get("replacements")for r in source_page["field_sources"])
    detail=project_detail(api_client,pid)
    if CASE=="delayed_p7_unedited":
        assert detail["revision"]>0 and detail["has_draft"]
        assert len(detail["draft"]["questions"])==19
        assert sum(bool(q.get("answer_markdown"))for q in detail["draft"]["questions"])==19
        assert main.DOCUMENT_PROJECTS.get_baseline(pid)==early_baseline
        saved_teacher=deepcopy(detail["draft"])
    assert detail["draft"]==saved_teacher,"Final machine output must not overwrite the editable early draft"
    if delayed_page7:
        assert len(detail["latest_result"])==19
        assert sum(bool(q.get("answer_markdown"))for q in detail["latest_result"])==19
        if CASE!="delayed_p7_unedited":
            assert detail["latest_result"][0]["content"]!=saved_teacher["questions"][0]["content"]
    fast_partial_case=CASE in {"fast_resume_partial_draft","fast_resume_partial_diagnostics","fast_resume_explicit_complete"}
    if fast_partial_case:
        fast_baseline=deepcopy(main.DOCUMENT_PROJECTS.get_baseline(pid))
        draft=deepcopy(saved_teacher)
        draft["questions"][0]["project_incoming_candidate"]=deepcopy(first["data"][0])
        draft["diagnostics"]=deepcopy(draft.get("diagnostics")or first["diagnostics"])
        draft["diagnostics"]["pdf_local_draft"].update(status="partial",unresolved_ranges=[{"reason":"offline pending range"}])
        draft["diagnostics"]["pdf_project_partial"]={"pending":True}
        if CASE=="fast_resume_partial_diagnostics":draft.pop("partial",None)
        else:draft["partial"]=CASE!="fast_resume_explicit_complete"
        revision_before_save=detail["revision"]
        saved=api_client.put(f"/api/document-projects/{pid}/draft",
            json={"expected_revision":revision_before_save,"draft":draft})
        assert saved.status_code==200,saved.text
        saved_teacher=deepcopy(saved.json()["draft"])
        saved_revision=saved.json()["revision"]
        previous=main.DOCUMENT_PROJECTS.load_task(pid)
        previous["partial"]=(None if CASE=="fast_resume_partial_diagnostics" else CASE=="fast_resume_explicit_complete")
        previous["diagnostics"]=deepcopy(draft["diagnostics"])
        main.DOCUMENT_PROJECTS.sync_task(pid,previous)
        assert main.DOCUMENT_PROJECTS.load_task(pid)["partial"] is previous["partial"]
        if CASE=="fast_resume_partial_diagnostics":
            assert "partial"not in saved_teacher and previous["partial"]is None
        def forbidden_worker(*_a,**_kw):raise AssertionError("A saved draft resume must take the synchronous zero-model fast path")
        main.DOCUMENT_TASKS.submit=forbidden_worker
        main.run_pdf_parsing_task=forbidden_worker
    post_counts=(len(vision_posts),len(metadata_posts),len(figure_posts))
    assert api_client.post(f"/api/document-projects/{pid}/open").status_code==200
    resumed=api_client.post(f"/api/document-projects/{pid}/resume")
    assert resumed.status_code==200,resumed.text
    restored=terminal(api_client,resumed.json()["task_id"])
    assert restored["status"]=="completed"
    stable=lambda row:{key:value for key,value in row.items()if key!="import_item_id"}
    assert [stable(row)for row in restored["data"]]==[stable(row)for row in saved_teacher["questions"]]
    if fast_partial_case:
        expected_partial=CASE!="fast_resume_explicit_complete"
        assert restored["partial"]is expected_partial
        detail=project_detail(api_client,pid)
        assert detail["partial"]is expected_partial and detail["draft"]==saved_teacher
        assert detail["revision"]==saved_revision
        assert restored["data"][0]["project_incoming_candidate"]==saved_teacher["questions"][0]["project_incoming_candidate"]
        assert main.DOCUMENT_PROJECTS.get_baseline(pid)==fast_baseline
        assert main.DOCUMENT_PROJECTS.load_task(pid)["partial"]is expected_partial
        assert (len(vision_posts),len(metadata_posts),len(figure_posts))==post_counts and not unexpected_calls
        stale=api_client.put(f"/api/document-projects/{pid}/draft",
            json={"expected_revision":revision_before_save,"draft":saved_teacher})
        assert stale.status_code==409 and project_detail(api_client,pid)["draft"]==saved_teacher
        with SessionLocal()as db:assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
        print("FRAGMENT_DRAFT_HTTP="+json.dumps({"case":CASE,"questions":19,"partial":expected_partial,
            "full_body_posts":0,"warm_new_posts":0,"teacher_candidate_CAS_preserved":True}))
        main.DOCUMENT_TASKS._executor.shutdown(wait=True)
        raise SystemExit(0)
    if CASE=="fresh_unchanged_page_edits":
        detail=project_detail(api_client,pid);draft=deepcopy(detail["draft"])
        draft["page_edits"]=[{"page_number":row["page_number"],"markdown":row["markdown"]}
            for row in first["pdf_source_pages"]]
        saved=api_client.put(f"/api/document-projects/{pid}/draft",
            json={"expected_revision":detail["revision"],"draft":draft})
        assert saved.status_code==200,saved.text
        saved_teacher=deepcopy(saved.json()["draft"])
    if CASE in {"fresh_native_upgrade","fresh_native_marks_accepted_blank"}:
        main.inspect_and_extract_pdf=lambda *_a,**_kw:{"pdf_type":"native","pages":[
            {"page_index":index,"needs_ocr":False,"markdown":"新原生输出不能覆盖原v2文字",
             "confirmed_blank":bool(CASE=="fresh_native_marks_accepted_blank"and index==1)}
            for index in range(page_count)]}
    if CASE=="fresh_rules_upgrade":
        # Keep all immutable first-pass packets and the actual teacher draft.
        # Only make the synthetic accepted machine result represent an old
        # assembler version; re-analysis must rebuild from local blocks.
        from mathbank.document_project_parse import _decode,_encode
        old_rules="pdf-fragment-draft.rules.v0"
        manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
        changed=0
        for name,entry in manifest["stages"].items():
            if not name.startswith("legacy-accepted-"):continue
            cached=main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"])
            historical=_decode(cached["payload"])
            assert historical["workflow"]=="page-fragments.v2"
            historical["diagnostics"]["pdf_local_draft"]["rules_version"]=old_rules
            historical["questions"][-1]["content"]="旧机器缓存不应继续冒充当前题文。"
            cached["payload"]=_encode(historical)
            main.DOCUMENT_PROJECTS.save_stage(pid,name,cached,entry["input_fingerprint"],complete=True)
            changed+=1
        assert changed==1
        baseline_before_rebuild=deepcopy(main.DOCUMENT_PROJECTS.get_baseline(pid))
    reanalyzed=api_client.post(f"/api/document-projects/{pid}/analyze")
    assert reanalyzed.status_code==200,reanalyzed.text
    again=terminal(api_client,reanalyzed.json()["task_id"])
    assert again["status"]=="completed",again.get("error")
    assert (len(vision_posts),len(metadata_posts),len(figure_posts))==post_counts
    assert not unexpected_calls
    assert [row["markdown"]for row in again["pdf_source_pages"]]==[row["markdown"]for row in first["pdf_source_pages"]]
    assert project_detail(api_client,pid)["draft"]==saved_teacher
    assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["pages"]==state["pages"]
    if CASE=="fresh_rules_upgrade":
        recheck=again["diagnostics"]["pdf_local_cache_recheck"]
        assert recheck["status"]=="fragment_rules_changed"and recheck["previous_rules"]==old_rules
        assert again["diagnostics"]["pdf_project_legacy_parse"]["accepted_cache_rejected"]is True
        assert again["diagnostics"]["pdf_project_legacy_parse"]["executor_calls"]==1
        assert again["data"][-1]["content"].strip()==bodies[19]
        assert main.DOCUMENT_PROJECTS.get_baseline(pid)==baseline_before_rebuild
        detail=project_detail(api_client,pid)
        assert detail["draft"]==saved_teacher
        assert detail["latest_result"][0]["content"]!=saved_teacher["questions"][0]["content"]
    with SessionLocal()as db:assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
    print("FRAGMENT_DRAFT_HTTP="+json.dumps({"case":CASE,"questions":19,"answers":19,"choices":44,
        "ocr_posts":len(vision_posts),"metadata_posts":len(metadata_posts),"figure_posts":len(figure_posts),
        "full_body_posts":0,"warm_new_posts":0,"teacher_CAS_preserved":True}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@pytest.mark.parametrize("case",["fresh_no_figure","fresh_verified_slot","fresh_blank","fresh_native_upgrade",
    "fresh_unchanged_page_edits","fresh_native_marks_accepted_blank","fresh_regional_candidate",
    "fresh_rules_upgrade","v1_resume","delayed_p7","delayed_p7_failure","delayed_p7_unedited",
    "fast_resume_partial_draft","fast_resume_partial_diagnostics","fast_resume_explicit_complete",
    "delayed_p7_resume_missing"])
@experimental_pdf_only
def test_first_pass_fragments_actual_upload_preview_cache_and_legacy_resume(tmp_path,case):
    root=Path(__file__).resolve().parents[1];workspace=tmp_path/"source-only-workspace";workspace.mkdir()
    shutil.copy2(root/"main.py",workspace/"main.py")
    shutil.copytree(root/"mathbank",workspace/"mathbank",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    if(root/"templates").is_dir():shutil.copytree(root/"templates",workspace/"templates")
    (workspace/"static").mkdir()
    base=_fixture();script=workspace/"fragment_draft_http.py"
    script.write_text(base._base()+"\n"+base.HELPERS+"\nCASE="+repr(case)+"\n"+SCENARIO,encoding="utf-8")
    result=subprocess.run([sys.executable,"-B",str(script)],cwd=workspace,
        env=dict(os.environ,PYTHONPATH=str(workspace)),capture_output=True,text=True,timeout=65)
    assert result.returncode==0,result.stdout[-8000:]+"\n"+result.stderr[-8000:]
    assert '"full_body_posts": 0'in result.stdout
