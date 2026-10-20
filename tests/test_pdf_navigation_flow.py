"""Resume a missing OCR page with old navigation pixels and real detail checks.

The production main route runs only in a source-only child and fresh SQLite.
HTTP/model replies are stubs; the actual request_pdf_page image protocol runs.
"""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pdf_feature_contract import experimental_pdf_only


def _base():
    path = Path(__file__).with_name("test_document_project_http_integration.py")
    spec = importlib.util.spec_from_file_location("navigation_isolated_http_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SCENARIO.split("with TestClient(main.app) as client:\n", 1)[0]


SCENARIO = r'''
from mathbank import pdf_page_vision, pdf_vision_details, pdf_native_regions
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_navigation_cache import bind_pdf_navigation
from mathbank.pdf_task_resume import prepare_pdf_checkpoint

OPTIONS={"generate_answers":False,"page_range":None,"pdf_strategy":"layout_aware",
         "pdf_verify_suspicions":False,"paper_title":"navigation identity fixture"}
with fitz.open() as pdf:
    for index in range(2):
        page=pdf.new_page(width=595,height=842)
        page.insert_text((30,40),f"original physical page {index+1}")
    raw=pdf.tobytes()
project=main.DOCUMENT_PROJECTS.create(raw,"navigation.pdf",OPTIONS,deepcopy(main.get_current_curriculum()))
pid=project["project_id"];old_id=str(uuid.uuid4())
assets=[];pixel_hashes={}
with fitz.open(stream=raw,filetype="pdf") as pdf:
    for index,page in enumerate(pdf):
        path=Path(main.TMP_UPLOAD_DIR)/f"pdf_page_{old_id}_{index}.png"
        page.get_pixmap(dpi=150).save(path)
        url=f"/{main.UPLOAD_DIR_REL}/tmp/{path.name}"
        assets.append(url);pixel_hashes[url]=hashlib.sha256(path.read_bytes()).hexdigest()
state=prepare_pdf_checkpoint(None,task_id=old_id,document_sha256=project["source_sha256"],
    page_indices=[0,1],strategy="layout_aware",verify_suspicions=False,
    resolve_asset=main._document_project_service().asset_resolver)
first_page="<!-- MATHBANK_PDF_PAGE:1 -->\n一、解答题\n1. 已知数据$1,2,3$，求极差。"
state["pages"]={"0":{"markdown":first_page,"joint":{"markdown":first_page,
    "layout":{"figures":[],"page_complete":True,"ignored_candidates":[],"notes":[]},
    "usage":{},"visual_calls":1}}}
if CASE=="explicit_binding":
    for index,url in enumerate(assets):bind_pdf_navigation(state,index,url)
state=seal_pdf_checkpoint(state,assets=assets,resolve_asset=main._document_project_service().asset_resolver)
source_input=main._register_document_input(old_id,raw,"pdf","navigation.pdf",**OPTIONS)
main.DOCUMENT_TASKS.create(old_id,status="pending",project_id=pid,document_type="pdf",
    document_input=source_input,temp_assets=assets)
main.DOCUMENT_TASKS.fail(old_id,"isolated page 2 interruption",pdf_resume_state=state,
    page_images=assets,page_numbers=[1,2],temp_assets=assets,
    pdf_source_pages=[{"page_number":1,"markdown":first_page,"origin":"joint_vision"}])
assert main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]["pages"].keys()=={"0"}

main.inspect_and_extract_pdf=lambda *_a,**_k:{"pdf_type":"scanned","pages":[
    {"page_index":index,"needs_ocr":True,"markdown":""} for index in range(2)]}
pdf_native_regions.plan_pdf_regions=lambda *_a,**_k:None
vision_provider=resolve_ocr_provider("siliconflow",{
    "SILICONFLOW_API_KEY":"fake-no-live-network", "SILICONFLOW_OCR_MODEL":"Qwen/Qwen3.8-27B"})
pdf_page_vision.resolve_ocr_provider=lambda *_a:vision_provider
request_threads=[];render_threads=[];details_seen=[];vision_posts=[]
original_render=pdf_vision_details.render_pdf_detail_views
def render(page,**kwargs):
    assert page.number==1,"completed page 1 must not need more detail preparation"
    assert kwargs["asset_prefix"]==f"pdf_detail_{old_id}_1"
    render_threads.append(threading.get_ident())
    result=original_render(page,**kwargs)
    assert result["status"]=="prepared" and len(result["views"])==2
    return result
pdf_vision_details.render_pdf_detail_views=render
original_pixmap=fitz.Page.get_pixmap
def no_new_navigation(page,*args,**kwargs):
    assert kwargs.get("dpi")!=150,"validated old navigation must not be rendered again"
    return original_pixmap(page,*args,**kwargs)
fitz.Page.get_pixmap=no_new_navigation
def vision_post(provider,payload,**kwargs):
    assert provider is vision_provider
    assert kwargs["timeout"]==600 and kwargs["retry_connection"] is False
    assert kwargs["allow_redirects"] is False
    assert payload["enable_thinking"] is False
    images=[part for message in payload["messages"] if isinstance(message.get("content"),list)
            for part in message["content"] if part.get("type")=="image_url"]
    assert len(images)==3,"actual request must include navigation and both validated high-DPI details"
    vision_posts.append(deepcopy(payload))
    return Reply({"markdown":"2. 计算$2+3$。","figures":[],"ignored_candidates":[],
                  "page_complete":True,"warnings":[]})
pdf_page_vision.post_chat_completion=vision_post
original_request=pdf_page_vision.request_pdf_page
def actual_request(image,info,**kwargs):
    assert info["page_index"]==1
    assert Path(image).name==f"pdf_page_{old_id}_1.png"
    request_threads.append(threading.get_ident())
    result=original_request(image,info,**kwargs)
    assert result["detail_input"]["status"]=="included",result["detail_input"]
    assert result["detail_input"]["detail_count"]==2
    details_seen.append(deepcopy(result["detail_input"]))
    return result
pdf_page_vision.request_pdf_page=actual_request

with TestClient(main.app) as client:
    client.headers.update({"X-Local-Token":main.LOCAL_TOKEN})
    response=client.post(f"/api/document-projects/{pid}/resume")
    assert response.status_code==200,response.text
    new_id=response.json()["task_id"]
    assert new_id!=old_id,"exercise actual new-task project dispatch"
    task=settled_task(client,new_id)
    assert len(task["data"])==2
    assert task["page_images"]==assets
    assert len(vision_posts)==len(details_seen)==1
    assert len(ocr_calls)==0,"must use actual joint request instead of generic OCR stub"
    assert len(structure_calls)==1
    assert task["diagnostics"]["pdf_navigation_cache"]=={"reused_pages":2,"rendered_pages":0}
    assert task["diagnostics"]["pdf_resume"]["reused_visual_pages"]==1
    assert task["diagnostics"]["pdf_request_pipeline"]["submitted"]==1
    assert task["diagnostics"]["pdf_detail_views"]["successful_input_images"]==2
    assert render_threads and request_threads and set(render_threads).isdisjoint(request_threads)
    current=main.DOCUMENT_PROJECTS.load_task(pid)["pdf_resume_state"]
    assert current["task_id"]==new_id and current["pages"]["0"]["markdown"]==first_page
    assert set(current["pages"])=={"0","1"}
    detail_assets=[url for url in current["assets"] if Path(url).name.startswith(f"pdf_detail_{old_id}_1_")]
    assert len(detail_assets)==2 and all(url in task["temp_assets"] for url in detail_assets)
    for url in assets:
        path=main.resolve_upload_asset(url,uploads_dir=main.UPLOAD_DIR,url_prefix=main.UPLOAD_DIR_REL)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==pixel_hashes[url]
    with SessionLocal() as db:
        assert db.query(Question).count()==db.query(DocumentImportReceipt).count()==0
    print("NAVIGATION_FLOW="+json.dumps({"case":CASE,"old_task":old_id,"new_task":new_id,
        "navigation_reused":2,"new_ocr_calls":1,"included_details":2,"questions":2}))
main.DOCUMENT_TASKS._executor.shutdown(wait=True)
'''


@pytest.mark.parametrize("case", ["explicit_binding", "legacy_unique_name"])
@experimental_pdf_only
def test_project_resume_keeps_cached_navigation_identity_for_pending_page_detail_protocol(tmp_path, case):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-workspace"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    script = workspace / "isolated_navigation.py"
    script.write_text(_base() + "\nCASE=" + repr(case) + "\n" + SCENARIO, encoding="utf-8")
    result = subprocess.run([sys.executable, str(script)], cwd=workspace,
        env=dict(os.environ, PYTHONPATH=str(workspace)), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-7000:] + "\n" + result.stderr[-7000:]
    assert "NAVIGATION_FLOW=" in result.stdout and '"included_details": 2' in result.stdout
