"""Real PDF recrops and mocked review: only verified, safely anchored assets attach."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pymupdf as fitz
from PIL import Image
import pytest

from mathbank.ai_providers import resolve_ocr_provider
from mathbank import pdf_figure_pipeline as pipeline
from mathbank.pdf_figure_review import CHECKS, FRAME, review_pdf_figures as actual_engine
from mathbank.pdf_figures import describe_figure_slots
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request",lambda *_a,**_kw:pytest.fail("No paid API in pipeline tests"))


def make_pdf():
    with fitz.open() as document:
        page=document.new_page(width=400,height=500)
        page.insert_text((25,30),"1. Original question")
        page.draw_rect(fitz.Rect(240,225,360,400),color=(0,0,0),width=2)
        page.insert_text((260,250),"A")
        return document.tobytes()


@pytest.fixture
def setup(tmp_path):
    pdf=make_pdf();out=tmp_path/"assets";out.mkdir()
    source_path=out/"source.png"
    with fitz.open(stream=pdf,filetype="pdf") as document:document[0].get_pixmap().save(source_path)
    crop=out/"initial.png";Image.new("RGB",(50,50),"white").save(crop)
    source="<!-- MATHBANK_PDF_PAGE:1 -->\n一、解答题\n1. 原题 $x+1$，保留  两个空格。\n[插图待补: 图1]\n原答案不由模型改写。"
    slot=describe_figure_slots(source,0)[0]
    figure={"id":"p1-f1","slot_id":slot["id"],"bbox":[100,100,200,200],
        "image_path":"/static/uploads/tmp/initial.png","page_index":0,
        "anchor_before":source[:slot["end"]],"anchor_after":""}
    layout={"schema":"test-layout","pages":[{"page_index":0,"figures":[figure],"status":"review","warnings":[]}],
            "page_texts":["old derived text must not become the raw OCR source"],"diagnostics":{}}
    provider=resolve_ocr_provider("zhongzhan_claude",{"ZHONGZHAN_CLAUDE_API_KEY":"test-only",
        "ZHONGZHAN_CLAUDE_BASE_URL":"https://example.test/v1","ZHONGZHAN_CLAUDE_OCR_MODEL":"gemini-3.8-flash-high"})
    def resolve(url):
        assert isinstance(url,str) and url.startswith("/static/uploads/tmp/")
        assert Path(url).name==url.rsplit("/",1)[1]
        return out/Path(url).name
    return {"pdf":pdf,"out":out,"source_path":source_path,"source":source,"layout":layout,
            "provider":provider,"resolve":resolve,"registered":[],"progress":[]}


def run(setup,**kwargs):
    values={"file_bytes":setup["pdf"],"output_dir":setup["out"],"url_prefix":"/static/uploads/tmp",
        "task_id":"figure-pipeline","provider":setup["provider"],"resolve_asset":setup["resolve"],
        "register_asset":setup["registered"].append,"progress":setup["progress"].append}
    values.update(kwargs)
    return pipeline.review_pdf_layout_figures(setup["layout"],[setup["source"]],[0],[str(setup["source_path"])],**values)


def fake_engine(mode="accepted",*,change_asset=False):
    def review(pages,**kwargs):
        results=[]
        for page in pages:
            for figure in page["figures"]:
                crop=Path(figure["crop_path"])
                if change_asset:crop.write_bytes(b"changed after reviewed receipt")
                results.append({"id":figure["id"],"slot_id":figure["slot_id"],"page_number":page["page_number"],
                    "status":mode,"bbox":figure["bbox"],"frame":FRAME,"image_path":figure["image_path"] if mode in {"accepted","repaired"} else None,
                    "crop_sha256":hashlib.sha256(crop.read_bytes()).hexdigest() if not change_asset else "0"*64,
                    "review":{"reason":"not accepted" if mode=="pending" else ""}})
        kwargs["progress"]({"calls":1,"items":[{key:row[key] for key in ("id","slot_id","page_number","status","review")} for row in results]})
        return {"figures":results,"summary":{"status":"completed","calls":1}}
    return review


def test_raw_ocr_and_layout_not_modified_only_exact_slot_gets_verified_asset(setup,monkeypatch):
    before=deepcopy(setup["layout"]);source=setup["source"]
    monkeypatch.setattr(pipeline,"review_pdf_figures",fake_engine())
    result=run(setup)
    assert setup["source"]==source and setup["layout"]==before
    assert result["figure_review"]["verified"]==1 and result["figure_review"]["pending"]==0
    derived=result["page_texts"][0]
    left,right=source.split("[插图待补: 图1]")
    assert derived.startswith(left) and derived.endswith(right)
    assert "![插图](/static/uploads/tmp/initial.png)" in derived
    assert "$x+1$，保留  两个空格。" in derived
    assert result["pages"][0]["figures"][0]["attached"] is True


def test_unverified_or_disabled_never_attaches_old_or_new_bad_crop(setup,monkeypatch):
    monkeypatch.setattr(pipeline,"review_pdf_figures",fake_engine("pending"))
    result=run(setup)
    assert result["page_texts"][0]==setup["source"]
    assert "![" not in result["page_texts"][0]
    assert result["page_texts"][0].count("[插图待补: 图1]")==1
    assert result["figure_review"]["pending"]==1 and result["pages"][0]["figures"]==[]
    monkeypatch.setattr(pipeline,"review_pdf_figures",actual_engine)
    disabled=run(setup,enabled=False,post=lambda *_a,**_kw:pytest.fail("Disabled review sent paid HTTP"))
    assert disabled["figure_review"]["calls"]==0 and disabled["figure_review"]["pending"]==1
    assert disabled["page_texts"][0]==setup["source"]


@pytest.mark.parametrize("kind",["missing","ambiguous","math","code"])
def test_native_without_safe_slot_anchor_stays_pending_even_if_pixels_pass(setup,monkeypatch,kind):
    figure=setup["layout"]["pages"][0]["figures"][0]
    figure.update(slot_id="",native_box=True)
    if kind=="missing":
        setup["source"]="1. 无可证明图位的原题。";figure.update(anchor_before="",anchor_after="")
    elif kind=="ambiguous":
        setup["source"]="1. 重复。\n重复。";figure.update(anchor_before="重复。",anchor_after="")
    elif kind=="math":
        setup["source"]="1. 原公式 $x+2$。";figure.update(anchor_before="x+",anchor_after="")
    else:
        setup["source"]="1. 示例代码\n```text\ninside-code\n```\n";figure.update(anchor_before="inside-code",anchor_after="")
    monkeypatch.setattr(pipeline,"review_pdf_figures",fake_engine())
    result=run(setup)
    assert result["page_texts"][0]==setup["source"]
    assert result["figure_review"]["verified"]==0 and result["figure_review"]["pending"]==1
    assert result["pages"][0]["figures"]==[]


def test_markdown_table_slot_remains_in_its_original_cell(setup,monkeypatch):
    setup["source"]="|题号|选项|\n|---|---|\n|1|[插图待补: 图1]|\n"
    slot=describe_figure_slots(setup["source"],0)[0]
    setup["layout"]["pages"][0]["figures"][0].update(slot_id=slot["id"],anchor_before=setup["source"][:slot["end"]],anchor_after="")
    monkeypatch.setattr(pipeline,"review_pdf_figures",fake_engine())
    result=run(setup)
    rows=result["page_texts"][0].splitlines()
    assert len(rows)==3 and "![插图]" in rows[2] and rows[2].startswith("|1|") and rows[2].endswith("|")


def test_asset_changed_after_review_becomes_only_pending(setup,monkeypatch):
    monkeypatch.setattr(pipeline,"review_pdf_figures",fake_engine(change_asset=True))
    result=run(setup)
    assert result["figure_review"]["pending"]==1 and result["figure_review"]["verified"]==0
    assert "![" not in result["page_texts"][0]


def test_missing_asset_resolver_does_not_lose_raw_text(setup,monkeypatch):
    observed=[]
    def engine(pages,**_kwargs):
        observed.extend(pages)
        return {"figures":[],"summary":{"calls":0,"status":"partial"}}
    monkeypatch.setattr(pipeline,"review_pdf_figures",engine)
    def missing(_url):raise FileNotFoundError("retained file unavailable")
    result=run(setup,resolve_asset=missing)
    assert observed[0]["figures"][0]["crop_path"] is None
    assert result["page_texts"][0]==setup["source"] and result["figure_review"]["pending"]==1


def test_progress_uses_frontend_status_names(setup,monkeypatch):
    monkeypatch.setattr(pipeline,"review_pdf_figures",fake_engine("repaired"))
    result=run(setup)
    assert result["figure_review"]["corrected"]==1
    assert all(item["status"] not in {"accepted","repaired"} for summary in setup["progress"] for item in summary.get("items",[]))
    assert setup["progress"][-1]["items"][0]["status"]=="corrected"


class FakeStore:
    def __init__(self):self.packets={}
    def save_stage(self,pid,name,value,fingerprint,complete=True):self.packets[(pid,name,fingerprint)]=deepcopy(value)
    def load_stage(self,pid,name,fingerprint):return deepcopy(self.packets.get((pid,name,fingerprint)))


def test_project_cache_restores_actual_retained_png_without_changing_saved_receipt(setup):
    store=FakeStore();restored=setup["out"]/"restored.png";Image.new("RGB",(50,50),"white").save(restored)
    cache=pipeline.ProjectFigureReviewCache(store,"project","a"*64,lambda _url:restored)
    original={"image_path":"/static/uploads/tmp/retained.png","crop_path":"/expired/old.png","status":"accepted"}
    cache.put("figure-key",original)
    assert cache.get("figure-key")["crop_path"]==str(restored)
    assert next(iter(store.packets.values()))["crop_path"]=="/expired/old.png"


def test_cache_restore_missing_asset_is_a_miss_not_a_document_failure(setup):
    store=FakeStore()
    def missing(_url):raise FileNotFoundError()
    cache=pipeline.ProjectFigureReviewCache(store,"project","a"*64,missing)
    cache.put("figure-key",{"image_path":"/static/uploads/tmp/missing.png","crop_path":"/expired/old.png"})
    packet=cache.get("figure-key")
    assert packet is None or packet.get("crop_path") in (None,"","/expired/old.png")


def test_real_original_pdf_recrop_receipt_and_retry_cache(setup,monkeypatch):
    calls=[];crop_threads=[];caller=threading.get_ident()
    actual_crop=pipeline.crop_pdf_figure
    def crop(*args,**kwargs):crop_threads.append(threading.get_ident());return actual_crop(*args,**kwargs)
    monkeypatch.setattr(pipeline,"crop_pdf_figure",crop)
    def post(_provider,payload,**kwargs):
        assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
        user=next(item for item in payload["messages"] if item["role"]=="user")
        data=json.loads(user["content"][0]["text"].split("\n输入资料JSON：\n",1)[1]);calls.append(data)
        entries=[]
        for record in data["figures"]:
            if data["stage"]=="relocate":
                entries.append({"id":record["id"],"slot_id":record["slot_id"],"decision":"located",
                    "bbox":{"left":600,"top":450,"right":900,"bottom":800},"evidence":"整页右侧找到完整图形。"})
            else:
                entries.append({"id":record["id"],"slot_id":record["slot_id"],"crop_sha256":record["crop_sha256"],
                    "decision":"rejected" if data["stage"]=="first" else "accepted",
                    "checks":{key:data["stage"]!="first" or key!="full_graphic" for key in CHECKS},"evidence":"原图与实际裁片已比较。"})
        body={"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"frame":FRAME,
            "source_sha256":data["source_sha256"],"figures":entries})}}]}
        return SimpleNamespace(status_code=200,json=lambda:body,close=lambda:None)
    cache={}
    result=run(setup,post=post,cache=cache)
    assert [call["stage"] for call in calls]==["first","relocate","recheck"] and crop_threads==[caller]
    figure=result["pages"][0]["figures"][0]
    actual=setup["resolve"](figure["image_path"])
    assert hashlib.sha256(actual.read_bytes()).hexdigest()==figure["crop_review"]["crop_sha256"]
    assert result["figure_review"]["corrected"]==1 and "initial.png" not in result["page_texts"][0]
    replay=run(setup,cache=cache,post=lambda *_a,**_kw:pytest.fail("Verified pipeline repeated model"))
    assert replay["figure_review"]["calls"]==0 and replay["figure_review"]["reused"]==1


def test_task_cancel_propagates_and_does_not_become_optional_failure(setup,monkeypatch):
    def cancelled(*_a,**_kw):raise TaskCancelled("cancelled")
    monkeypatch.setattr(pipeline,"review_pdf_figures",cancelled)
    with pytest.raises(TaskCancelled):run(setup)


def checkpoint_for(source):
    return {"pages":{"0":{"markdown":"untouched first OCR page"}},"prepared":{"page_texts":["untouched prepared"]},
        "split_input":{"source_markdown":source,"model_source":source,
            "model_source_sha256":hashlib.sha256(source.encode()).hexdigest()},
        "accepted_split":{"questions":[{"content":"preserved old accepted question"}]},"run":3}


@pytest.mark.parametrize("old,new",[
    ("1. 原题 $x+1$。\n![插图](/static/uploads/tmp/old.png)\n原答案。",
     "1. 原题 $x+1$。\n![插图](/static/uploads/tmp/new.png)\n原答案。"),
    ("1. 原题。\n[插图待补: 图1]\n后文。","1. 原题。\n\n![插图](/static/uploads/tmp/new.png)\n\n后文。"),
    ("1. 原题。\n后文。","1. 原题。\n[插图待补: 图1]\n后文。"),
])
def test_figure_only_derivation_archives_then_invalidates_without_changing_OCR_pages(old,new):
    checkpoint=checkpoint_for(old);before=deepcopy(checkpoint);archives=[]
    result=pipeline.invalidate_figure_derivation(checkpoint,old,new,lambda packet,sha:archives.append((packet,sha)))
    assert result["changed"] is True and len(archives)==1
    assert archives[0][0]["split_input"]==before["split_input"]
    assert archives[0][0]["accepted_split"]==before["accepted_split"]
    assert archives[0][1]==result["archive_sha256"]
    assert "split_input" not in checkpoint and "accepted_split" not in checkpoint
    assert checkpoint["pages"]==before["pages"] and checkpoint["prepared"]==before["prepared"]


@pytest.mark.parametrize("case",["hash","old_source","prose","formula","far_whitespace","code","math_literal","escaped_image","no_figure"])
def test_unproved_or_body_source_change_never_invalidates_checkpoint(case):
    old="1. 原题  保留空格 $x+1$。\n![插图](/static/uploads/tmp/old.png)\n后文。"
    new=old.replace("old.png","new.png")
    checkpoint=checkpoint_for(old)
    if case=="hash":checkpoint["split_input"]["model_source_sha256"]="0"*64
    if case=="old_source":checkpoint["split_input"]["source_markdown"]="other text"
    if case=="prose":new=new.replace("后文","被改正文")
    if case=="formula":new=new.replace("x+1","x-1")
    if case=="far_whitespace":new=new.replace("原题  保留","原题 保留")
    if case in {"code","math_literal"}:
        opening,closing=("```text\n","\n```") if case=="code" else ("$$","$$")
        old=opening+"![插图](/static/uploads/tmp/old.png)"+closing
        new=old.replace("old.png","new.png");checkpoint=checkpoint_for(old)
    if case=="escaped_image":
        old=r"显示字面图片语法：\![插图](/static/uploads/tmp/old.png)"
        new=old.replace("old.png","new.png");checkpoint=checkpoint_for(old)
    if case=="no_figure":old="1. 原题。";new="1. 改题。";checkpoint=checkpoint_for(old)
    before=deepcopy(checkpoint)
    with pytest.raises(ValueError):pipeline.invalidate_figure_derivation(checkpoint,old,new,
        lambda *_a:pytest.fail("Unproved source was archived as accepted transition"))
    assert checkpoint==before


def test_derivation_archive_failure_keeps_old_inputs_and_accepted_results():
    old="1. 原题[插图待补: 图1]。";new="1. 原题![插图](/static/uploads/tmp/new.png)。"
    checkpoint=checkpoint_for(old);before=deepcopy(checkpoint)
    def failed(*_a):raise OSError("archive failed")
    with pytest.raises(OSError):pipeline.invalidate_figure_derivation(checkpoint,old,new,failed)
    assert checkpoint==before


def test_figure_derivation_cannot_silently_move_an_existing_anchor_between_questions():
    old="第一题。![插图](/static/uploads/tmp/old.png)第二题。"
    new="第一题。第二题。![插图](/static/uploads/tmp/new.png)"
    checkpoint=checkpoint_for(old);before=deepcopy(checkpoint)
    with pytest.raises(ValueError):pipeline.invalidate_figure_derivation(checkpoint,old,new)
    assert checkpoint==before
