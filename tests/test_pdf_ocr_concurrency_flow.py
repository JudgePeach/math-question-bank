"""Bounded 9/12-page OCR through copied real app routes, with offline HTTP.

The child has source only, an empty SQLite file and no live credentials. Its
page request/JSON/archive/cache paths stay real; only provider HTTP is faked.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from pdf_feature_contract import experimental_pdf_only


NETWORK_GUARD = '''
import ipaddress,socket
_connect=socket.socket.connect
_connect_ex=socket.socket.connect_ex
def _check(address):
    if not isinstance(address,tuple):return
    host=address[0]
    if host in ("localhost","testserver"):return
    try:local=ipaddress.ip_address(host).is_loopback
    except (TypeError,ValueError):local=False
    if not local:raise AssertionError("Source-only test forbids external network")
def connect(self,address):
    _check(address);return _connect(self,address)
def connect_ex(self,address):
    _check(address);return _connect_ex(self,address)
socket.socket.connect=connect
socket.socket.connect_ex=connect_ex
'''


def _base():
    path = Path(__file__).with_name("test_document_project_http_integration.py")
    spec = importlib.util.spec_from_file_location("ocr_capacity_http_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SCENARIO.split("with TestClient(main.app) as client:\n", 1)[0].replace(
        "main.ocr_pdf_page_image = ocr", "# Keep the actual first-page OCR entry point.")


SCENARIO = r'''
import base64
from mathbank import pdf_page_vision, pdf_native_regions
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_inspector_helper import merge_pdf_page_texts

assert main.PDF_OCR_CAPACITY.total==LIMIT
assert main.PDF_OCR_SEMAPHORE is main.PDF_OCR_CAPACITY.auxiliary
assert main.PDF_OCR_CAPACITY.auxiliary_limit==4
ocr_provider=resolve_ocr_provider("siliconflow",{
    "SILICONFLOW_API_KEY":"fake-offline-only",
    "SILICONFLOW_OCR_MODEL":"Qwen/Qwen3.8-27B"})
main.resolve_ocr_provider=lambda *_a,**_kw:ocr_provider
pdf_page_vision.resolve_ocr_provider=lambda *_a,**_kw:ocr_provider
pdf_native_regions.plan_pdf_regions=lambda *_a,**_kw:None
bodies={n:f"第{n}道完整原题$x_{{{n}}}={n}$，请计算。" for n in range(1,20)}
answers={n:f"原卷第{n}题解答$x_{{{n}}}+1={n+1}$。" for n in range(1,20)}
chunks=[]
for count,values,heading in [(PAGES//2,bodies,"一、解答题"),
                             (PAGES-PAGES//2,answers,"数学参考答案及多维细目表")]:
    for page in range(count):
        start=page*19//count+1;stop=(page+1)*19//count+1
        chunks.append((heading+"\n" if page==0 else "")+
            "\n\n".join(f"{n}. {values[n]}" for n in range(start,stop)))
texts=[f"<!-- MATHBANK_PDF_PAGE:{index+1} -->\n{text}" for index,text in enumerate(chunks)]
expected_source=re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->","",merge_pdf_page_texts(texts))
with fitz.open() as pdf:
    for number in range(PAGES):
        page=pdf.new_page(width=300,height=400)
        page.insert_text((30,40),f"isolated physical page {number+1}")
    raw=pdf.tobytes()
main.inspect_and_extract_pdf=lambda *_a,**_kw:{"pdf_type":"scanned","pages":[
    {"page_index":index,"needs_ocr":True,"markdown":""} for index in range(PAGES)]}

lock=threading.Lock();release=threading.Event();full=threading.Event()
active=0;peak=0;http_pages=[];image_pages={};metadata_calls=[]
original_page=main.ocr_pdf_page_image
original_joint=pdf_page_vision.request_pdf_page
def register_image(path,index):
    # This is reached before the real base64 encoder, after the real permit.
    state=main.PDF_OCR_CAPACITY.snapshot()
    assert state["page_active"]>=1 and state["page_input_bytes"]>0
    with lock:image_pages[hashlib.sha256(Path(path).read_bytes()).hexdigest()]=index
def page(path,**kwargs):
    register_image(path,kwargs["page_index"])
    return original_page(path,**kwargs)
def joint(path,info,**kwargs):
    register_image(path,info["page_index"])
    return original_joint(path,info,**kwargs)
main.ocr_pdf_page_image=page
pdf_page_vision.request_pdf_page=joint

class PageReply:
    status_code=200;headers={}
    def __init__(self,value):
        self.body={"choices":[{"finish_reason":"stop","message":{"content":value}}],
            "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}
    def json(self):return deepcopy(self.body)

def offline_http(config,payload,**kwargs):
    global active,peak
    content=payload["messages"][-1]["content"]
    if isinstance(content,str):
        metadata_calls.append(deepcopy(payload))
        return post(config,payload,**kwargs)
    assert payload["enable_thinking"] is False
    assert kwargs["timeout"]==600 and kwargs["retry_connection"] is False
    images=[item["image_url"]["url"] for item in content if item.get("type")=="image_url"]
    png=base64.b64decode(images[0].split(",",1)[1],validate=True)
    with lock:
        index=image_pages[hashlib.sha256(png).hexdigest()]
        active+=1;peak=max(peak,active);http_pages.append(index)
        assert active<=LIMIT
        if active==LIMIT:full.set()
    try:
        assert release.wait(15),"configured concurrency never reached the fake HTTP barrier"
        if MODE=="layout_aware":
            assert len(images)==3,"real high-detail inputs must share the single page POST"
            value=json.dumps({"markdown":chunks[index],"figures":[],"ignored_candidates":[],
                "page_complete":True,"warnings":[]},ensure_ascii=False)
        else:
            assert len(images)==1
            value=chunks[index]
        return PageReply(value)
    finally:
        with lock:active-=1
main.post_chat_completion=offline_http
pdf_page_vision.post_chat_completion=offline_http
main.parse_paper_text_internal=lambda *_a,**_kw:(_ for _ in()).throw(AssertionError("No body regeneration"))

with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    dispatched=[]
    try:
        for number in range(PROJECTS):
            with fitz.open(stream=raw,filetype="pdf") as pdf:
                # Different source hashes make these independent tasks; the
                # ordinary identical-upload deduplication must remain enabled.
                pdf.set_metadata({"title":f"isolated concurrent project {number}"})
                upload_raw=pdf.tobytes()
            response=client.post("/api/upload/pdf-task",files={"file":(f"capacity-{number}.pdf",upload_raw,"application/pdf")},
                data={"client_request_id":str(uuid.uuid4()),"pdf_strategy":MODE,"pdf_verify_suspicions":"false"})
            assert response.status_code==200,response.text
            dispatched.append(response.json())
        assert full.wait(15),main.PDF_OCR_CAPACITY.snapshot()
        if PROJECTS>1:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                states=[client.get(f"/api/tasks/{entry['task_id']}/status").json() for entry in dispatched]
                if (all(state["status"]=="ocr_extraction" for state in states)
                        and main.PDF_OCR_CAPACITY.snapshot()["page_waiting"]>0):break
                time.sleep(.01)
            else:raise AssertionError("second independent OCR task did not reach the shared gate")
        assert len(http_pages)==LIMIT and peak==LIMIT
        state=main.PDF_OCR_CAPACITY.snapshot()
        assert state["page_active"]==state["active"]==LIMIT
        assert state["peak_page_input_bytes"]<=state["page_input_budget_bytes"]
        assert not main.PDF_OCR_SEMAPHORE.acquire(blocking=False),"auxiliary work must share the total gate"
    finally:
        release.set()
    for dispatched_task in dispatched:
        task=settled_task(client,dispatched_task["task_id"])
        pid=dispatched_task["project_id"]
        assert len(task["data"])==19 and task["partial"] is False
        assert [q["source_number"] for q in task["data"]]==list(range(1,20))
        assert [q["content"] for q in task["data"]]==[bodies[n] for n in range(1,20)]
        assert [q["answer_markdown"] for q in task["data"]]==[answers[n] for n in range(1,20)]
        assert task["diagnostics"]["pdf_legacy_requests"]["new_calls"]==0
        pipeline=task["diagnostics"]["pdf_request_pipeline"]
        assert pipeline["workers"]==min(PAGES,LIMIT)
        assert pipeline["submitted"]==pipeline["completed"]==PAGES
        saved=main.DOCUMENT_PROJECTS.load_task(pid)
        assert saved["pdf_resume_state"]["prepared"]["page_texts"]==texts
        assert set(saved["pdf_resume_state"]["pages"])=={str(i) for i in range(PAGES)}
        assert [row["markdown"] for row in saved["pdf_source_pages"]]==texts
        manifest=json.loads((main.DOCUMENT_PROJECTS.root/pid/"manifest.json").read_text())
        archives=[main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"])
            for name,entry in manifest["stages"].items() if name.startswith("ocr-response-")]
        assert len(archives)==PAGES,"simultaneous raw-response saves must not lose manifest entries"
        assert sorted(row["page_number"] for row in archives)==list(range(1,PAGES+1))
        assert all(row["metadata"]["enable_thinking"] is False and row["accepted_source"] is False
                   for row in archives)
        plans=[main.DOCUMENT_PROJECTS.load_stage(pid,name,entry["input_fingerprint"])
            for name,entry in manifest["stages"].items() if name.startswith("local-draft-")]
        assert any(plan["source"]==expected_source and len(plan["questions"])==19 for plan in plans)
    assert len(http_pages)==PAGES*PROJECTS and len(metadata_calls)==PROJECTS
    assert peak==LIMIT and active==0
    assert main.PDF_OCR_CAPACITY.snapshot()["active"]==0
    assert main.PDF_OCR_CAPACITY.snapshot()["page_input_bytes"]==0
    before=len(http_pages),len(metadata_calls)
    response=client.post(f"/api/document-projects/{dispatched[0]['project_id']}/analyze")
    assert response.status_code==200,response.text
    replay=settled_task(client,response.json()["task_id"])
    assert len(replay["data"])==19 and replay["diagnostics"]["pdf_draft_metadata"]["new_calls"]==0
    assert (len(http_pages),len(metadata_calls))==before
    with SessionLocal() as db:
        assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
    print("OCR_CONCURRENCY_FLOW="+json.dumps({"limit":LIMIT,"pages":PAGES,"projects":PROJECTS,
        "peak":peak,"page_http_calls":len(http_pages),"questions":19,"replay_calls":0,
        "formal_rows":0,"raw_responses_per_project":PAGES}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@pytest.mark.parametrize("limit,pages,projects,mode", [
    (8, 9, 2, "force_ocr"),
    (12, 12, 1, "layout_aware"),
])
@experimental_pdf_only
def test_real_routes_bound_fresh_ocr_and_keep_every_page(
        tmp_path, limit, pages, projects, mode):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-workspace"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    guard = workspace / "network-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    script = workspace / "ocr_concurrency_api.py"
    script.write_text(_base() + f"\nLIMIT={limit}\nPAGES={pages}\nPROJECTS={projects}\nMODE={mode!r}\n" + SCENARIO,
                      encoding="utf-8")
    # Product credentials cannot enter the source-only child through its env.
    environment = {key: value for key, value in os.environ.items()
                   if not any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET"))}
    environment.update(PYTHONPATH=os.pathsep.join((str(guard), str(workspace))),
                       PDF_OCR_CONCURRENCY=str(limit))
    result = subprocess.run([sys.executable, str(script)], cwd=workspace,
                            env=environment, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout[-9000:] + "\n" + result.stderr[-9000:]
    assert '"peak": ' + str(limit) in result.stdout
    assert '"formal_rows": 0' in result.stdout and '"replay_calls": 0' in result.stdout
