"""Real main project endpoints, copied sources/fresh SQLite, fake metadata."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from pdf_feature_contract import experimental_pdf_only


def fixture():
    path = Path(__file__).with_name("test_pdf_project_repair_flow.py")
    spec = importlib.util.spec_from_file_location("local_draft_api_fixture", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


SCENARIO = r'''
bodies={n:f"原题{n}完整条件$x_{{{n}}}={n}$。" for n in range(1,20)}
bodies[1]="第一题原文独立填空宏$\\fillin$及分式$\\frac{1}{2}$必须原样保留。"
answers={n:f"第{n}题原版解答$x_{{{n}}}+1={n+1}$。" for n in range(1,20)}
source="一、解答题\n"+"\n\n".join(f"{n}. {bodies[n]}" for n in range(1,20))+"\n# 参考答案与解析\n"+"\n\n".join(f"{n}. {answers[n]}" for n in range(1,20))
pid,old_id,original_state,actual_source,assets=seed_project(source,with_layout=True)
original_prepared=deepcopy(original_state["prepared"])
metadata_calls=[];seen_previews=[]
providers=None;current=None
if CASE.startswith("provider_"):
    from dataclasses import replace
    providers={name:replace(provider,model_name=name) for name in ["snapshot-A","snapshot-B"]}
    captured="snapshot-A" if CASE=="provider_A_to_B" else "snapshot-B"
    changed="snapshot-B" if captured=="snapshot-A" else "snapshot-A"
    current=[captured]
    main.resolve_text_provider=lambda *_a,**_kw:providers[current[0]]
    original_save=main.DOCUMENT_PROJECTS.save_stage
    def save_after_provider_capture(project_id,stage,*args,**kwargs):
        result=original_save(project_id,stage,*args,**kwargs)
        if stage.startswith("local-preview-"):
            current[0]=changed
            main.METADATA_CACHE={"curriculum_version":"A","curriculum":{"必修一":{"1. 新全局目录":[]}}}
        return result
    main.DOCUMENT_PROJECTS.save_stage=save_after_provider_capture

def metadata_post(config,payload,**kwargs):
    data=json.loads(payload["messages"][1]["content"])
    assert set(data)=={"input_sha256","document_sha256","items"}
    assert "<mathbank-math" not in payload["messages"][1]["content"]
    assert all("answer_markdown" not in row for row in data["items"])
    assert payload.get("max_tokens",payload.get("max_completion_tokens"))<=6000
    manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
    raw=[];preview=[]
    for name,entry in manifest["stages"].items():
        if name.startswith("local-draft-"):
            raw.append(main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"]))
        if name.startswith("local-preview-"):
            preview.append(main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"]))
    assert raw and preview,"first local source and preview must be durable before metadata HTTP"
    assert any(packet["source"]==actual_source for packet in raw) or CASE=="page_edit"
    active=main.DOCUMENT_PROJECTS.get(pid)["task_id"]
    task=main.DOCUMENT_TASKS.snapshot(active)
    assert task["status"]=="ai_metadata" and len(task["data"])==19,task
    if not metadata_calls:
        assert [q["content"] for q in task["data"]]==[bodies[n] for n in range(1,20)]
        assert [q["answer_markdown"] for q in task["data"]]==[answers[n] for n in range(1,20)]
    seen_previews.append(deepcopy(task["data"]))
    metadata_calls.append((deepcopy(payload),config.model_name))
    if providers:
        assert config.model_name==captured and payload["model"]==captured
        assert current[0]==changed,"settings really changed after the project snapshot"
    if CASE=="metadata_503_import":
        response=Reply({});response.status_code=503;return response
    return Reply({"items":[{"id":row["id"],"question_type":"detailed_answer","difficulty":"normal",
        "category_compulsory":"必修二","category_chapter":"统计","category_knowledge":"9.2 用样本估计总体"}
        for row in reversed(data["items"])]})
main.post_chat_completion=metadata_post
main.parse_paper_text_internal=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("No full-body parser in project default"))
with TestClient(main.app)as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    response=client.post(f"/api/document-projects/{pid}/analyze")
    assert response.status_code==200,response.text
    first=terminal(client,response.json()["task_id"])
    assert first["status"]=="completed",first.get("error")
    assert len(first["data"])==19 and not first["partial"] and not ocr_calls
    assert [q["content"] for q in first["data"]]==[bodies[n] for n in range(1,20)]
    assert [q["answer_markdown"] for q in first["data"]]==[answers[n] for n in range(1,20)]
    assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["prepared"]==original_prepared
    assert first["diagnostics"]["pdf_local_draft"]["new_body_generation_calls"]==0
    assert first["diagnostics"]["pdf_legacy_requests"]["body_generation_disabled"]
    assert first["diagnostics"]["pdf_navigation_cache"]=={"reused_pages":1,"rendered_pages":0}
    assert first["page_images"]==[assets[0]]
    assert len(metadata_calls)==(2 if CASE=="metadata_503_import" else 1)
    if CASE=="metadata_503_import":
        assert all(q["classification_review"]["required"] for q in first["data"])
        assert first["diagnostics"]["pdf_draft_metadata"]["metadata_failed"]

    if CASE in {"import_edit","metadata_503_import"}:
        detail=project_detail(client,pid);draft=deepcopy(detail["draft"])
        card=draft["questions"][0]
        card["content"]+="\n教师逐字校对补正。"
        card["answer_markdown"]+="\n教师确认原答。"
        card.update(category_compulsory="必修二",category_chapter="9. 统计",category_knowledge="9.2 用样本估计总体")
        card.pop("classification_review",None)
        saved=client.put(f"/api/document-projects/{pid}/draft",json={"expected_revision":detail["revision"],"draft":draft})
        assert saved.status_code==200,saved.text
        saved=saved.json();card=saved["draft"]["questions"][0]
        form={key:str(card.get(key)or"")for key in ("content","answer_markdown","question_type","difficulty","source",
             "category_compulsory","category_chapter","category_knowledge")}
        form.update(import_project_id=pid,import_card_id=card["project_card_id"],import_draft_revision=str(saved["revision"]),
                    expected_version_code="A",image_paths="[]")
        imported=client.post("/api/questions",data=form)
        assert imported.status_code==200,imported.text
        again=client.post("/api/questions",data=form)
        assert again.status_code==200 and again.json()["question"]["id"]==imported.json()["question"]["id"]
        with SessionLocal()as db:
            row=db.query(Question).one()
            assert row.content==card["content"] and row.answer_markdown==card["answer_markdown"]
            assert db.query(DocumentImportReceipt).count()==1
        assert len(metadata_calls)==(2 if CASE=="metadata_503_import" else 1)
    elif CASE=="page_edit":
        detail=project_detail(client,pid);draft=deepcopy(detail["draft"])
        changed_body=bodies[2]+"教师更正第二题条件。"
        draft["page_edits"]=[{"page_number":1,"markdown":source.replace(bodies[2],changed_body)}]
        saved=client.put(f"/api/document-projects/{pid}/draft",json={"expected_revision":detail["revision"],"draft":draft})
        assert saved.status_code==200,saved.text
        response=client.post(f"/api/document-projects/{pid}/analyze")
        second=terminal(client,response.json()["task_id"])
        assert second["status"]=="completed",second.get("error")
        assert not ocr_calls and len(metadata_calls)==2
        sent=json.loads(metadata_calls[-1][0]["messages"][1]["content"])["items"]
        assert len(sent)==1 and sent[0]["content"]==changed_body
        assert second["data"][1]["content"]==changed_body
        assert all(second["data"][n-1]["content"]==bodies[n]for n in range(1,20)if n!=2)
        assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["prepared"]==original_prepared
    else:
        expected=deepcopy(first["data"])
        if CASE=="old_accepted":
            from mathbank.document_project_parse import _decode,_encode
            manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
            for name,entry in manifest["stages"].items():
                if name.startswith("legacy-accepted-"):
                    packet=main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"])
                    historical=_decode(packet["payload"]);historical.pop("workflow",None)
                    historical["questions"][0]["content"]="历史已接受题文$z^2$，必须完整沿用。"
                    historical["questions"][0]["answer_markdown"]="历史已接受原答案。"
                    historical["diagnostics"].pop("pdf_local_draft",None)
                    historical["diagnostics"].pop("pdf_draft_metadata",None)
                    packet["payload"]=_encode(historical)
                    main.DOCUMENT_PROJECTS.save_stage(pid,name,packet,entry["input_fingerprint"],complete=True)
                    expected=deepcopy(historical["questions"])
                    for row in expected:row.setdefault("import_item_id",row["project_card_id"])
        if providers:current[0]=captured
        main.post_chat_completion=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("Accepted draft replay must not POST"))
        response=client.post(f"/api/document-projects/{pid}/analyze")
        second=terminal(client,response.json()["task_id"])
        assert second["status"]=="completed",second.get("error")
        assert second["data"]==expected and len(metadata_calls)==1 and not ocr_calls
        assert second["diagnostics"]["pdf_project_legacy_parse"]["new_calls"]==0
        if CASE!="old_accepted":
            assert second["diagnostics"]["pdf_draft_metadata"]["new_calls"]==0
            assert second["diagnostics"]["pdf_draft_metadata"]["calls"]==0
    print("LOCAL_DRAFT_HTTP="+json.dumps({"case":CASE,"questions":len(first["data"]),
        "page_ocr_calls":len(ocr_calls),"metadata_calls":len(metadata_calls),"body_generation_calls":0}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@pytest.mark.parametrize("case", ["success", "import_edit", "metadata_503_import", "page_edit", "old_accepted",
                                  "provider_A_to_B", "provider_B_to_A"])
@experimental_pdf_only
def test_local_draft_project_api_preserves_source_before_optional_metadata_and_import(tmp_path, case):
    root=Path(__file__).resolve().parents[1];workspace=tmp_path/"source-only-workspace";workspace.mkdir()
    shutil.copy2(root/"main.py",workspace/"main.py")
    shutil.copytree(root/"mathbank",workspace/"mathbank",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    if(root/"templates").is_dir():shutil.copytree(root/"templates",workspace/"templates")
    (workspace/"static").mkdir()
    base=fixture();script=workspace/"local_draft_api.py"
    script.write_text(base._base()+"\n"+base.HELPERS+"\nCASE="+repr(case)+"\n"+SCENARIO,encoding="utf-8")
    result=subprocess.run([sys.executable,str(script)],cwd=workspace,env=dict(os.environ,PYTHONPATH=str(workspace)),
                          capture_output=True,text=True,timeout=65)
    assert result.returncode==0,result.stdout[-7000:]+"\n"+result.stderr[-7000:]
    assert '"questions": 19' in result.stdout and '"body_generation_calls": 0' in result.stdout
