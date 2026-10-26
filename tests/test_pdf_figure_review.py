"""PDF crop semantics, full-page relocation and immutable cache receipts (offline)."""

from mathbank.ai_timeouts import DOCUMENT_AI_TIMEOUT_SECONDS
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest
import requests

from mathbank.ai_providers import resolve_ocr_provider
from mathbank.pdf_figure_review import CHECKS, FRAME, PdfFigureReviewError, review_pdf_figures
from mathbank.task_manager import TaskCancelled


def png(path, size=(100, 80), label="source"):
    image=Image.new("RGB",size,"white")
    draw=ImageDraw.Draw(image)
    draw.rectangle((5,5,size[0]-6,size[1]-6),outline="black",width=2)
    draw.text((10,10),label,fill="black")
    image.save(path)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session,"request",lambda *_a,**_kw:pytest.fail("No live API in figure tests"))


@pytest.fixture
def setup(tmp_path):
    source=tmp_path/"page.png"
    source_sha=png(source,(500,700))
    crop=tmp_path/"crop.png"
    png(crop,label="initial")
    provider=resolve_ocr_provider("zhongzhan_claude",{
        "ZHONGZHAN_CLAUDE_API_KEY":"test-only","ZHONGZHAN_CLAUDE_BASE_URL":"https://example.test/v1",
        "ZHONGZHAN_CLAUDE_OCR_MODEL":"gemini-3.8-flash-high"})
    page={"page_number":1,"page_index":0,"source_path":str(source),"source_sha256":source_sha,
        "figures":[{"id":"p1-f1","slot_id":"slot-1","bbox":[100,100,200,200],
                    "crop_path":str(crop),"image_path":"/static/uploads/tmp/original.png","context":"第1题图1"}]}
    return tmp_path,provider,page


def metadata(payload):
    user=next(message for message in payload["messages"] if message["role"]=="user")
    return json.loads(user["content"][0]["text"].split("\n输入资料JSON：\n",1)[1])


def accepted(record):
    return {"id":record["id"],"slot_id":record["slot_id"],"crop_sha256":record["crop_sha256"],
            "decision":"accepted","checks":{key:True for key in CHECKS},"evidence":"原页与实际裁片相符，无邻文水印。"}


def rejection(record):
    return {**accepted(record),"decision":"rejected","checks":{key:key!="full_graphic" for key in CHECKS},
            "evidence":"实际裁片不完整，需要从整页重新定位。"}


def located(record,box=(600,450,900,800)):
    return {"id":record["id"],"slot_id":record["slot_id"],"decision":"located",
        "bbox":dict(zip(("left","top","right","bottom"),box)),"evidence":"根据整页的原题图位重新定位。"}


def post_for(callback=None):
    calls=[]
    def post(_provider,payload,**kwargs):
        assert kwargs=={"timeout":DOCUMENT_AI_TIMEOUT_SECONDS,"check_status":False,"retry_connection":False,"allow_redirects":False}
        data=metadata(payload)
        calls.append((data,deepcopy(payload),threading.get_ident()))
        entries=callback(data) if callback else [accepted(record) for record in data["figures"]]
        body={"choices":[{"finish_reason":"stop","message":{"content":json.dumps({
            "frame":FRAME,"source_sha256":data["source_sha256"],"figures":entries},ensure_ascii=False)}}],
            "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}
        return SimpleNamespace(status_code=200,json=lambda:body,close=lambda:None)
    return post,calls


def execute(setup,*,post,cache=None,recrop=None,**kwargs):
    _root,provider,page=setup
    return review_pdf_figures([page],provider=provider,post=post,cache=cache,
        recrop=recrop or (lambda *_a:pytest.fail("No unexpected original-PDF crop")),**kwargs)


def test_every_crop_including_native_box_needs_actual_pixel_review_and_keeps_input(setup):
    root,_provider,page=setup
    page["figures"][0]["native_box"]=True
    before=deepcopy(page)
    post,calls=post_for()
    snapshots=[]
    result=execute(setup,post=post,progress=lambda value:snapshots.append(value))
    assert page==before and len(calls)==1
    assert calls[0][0]["figures"][0]["crop_sha256"]==hashlib.sha256((root/"crop.png").read_bytes()).hexdigest()
    assert calls[0][0]["frame"]==FRAME
    assert result["figures"][0]["status"]=="accepted"
    assert result["figures"][0]["image_path"]==before["figures"][0]["image_path"]
    assert result["summary"]["verified"]==1 and result["summary"]["checked"]==1
    assert snapshots[-1]["items"][0]["slot_id"]=="slot-1"
    snapshots[-1]["items"][0]["status"]="external mutation"
    assert result["figures"][0]["status"]=="accepted"


def test_wrong_tiny_initial_box_can_relocate_across_page_but_actual_recrop_must_pass(setup):
    root,_provider,page=setup
    original=deepcopy(page)
    main_thread=threading.get_ident();recrops=[]
    def respond(data):
        return [rejection(record) if data["stage"]=="first" else located(record)
                if data["stage"]=="relocate" else accepted(record) for record in data["figures"]]
    def recrop(page_number,identifier,box):
        recrops.append((threading.get_ident(),page_number,identifier,box))
        path=root/"corrected.png";png(path,(180,160),"new actual pixels")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/corrected.png"}
    post,calls=post_for(respond)
    cache={}
    result=execute(setup,post=post,recrop=recrop,cache=cache)
    assert [call[0]["stage"] for call in calls]==["first","relocate","recheck"]
    assert all(call[2]!=main_thread for call in calls)
    expected=[596.,450.-2000/700,904.,800.+2000/700]
    assert recrops[0][:3]==(main_thread,1,"p1-f1") and recrops[0][3]==pytest.approx(expected)
    assert calls[-1][0]["figures"][0]["crop_sha256"]==hashlib.sha256((root/"corrected.png").read_bytes()).hexdigest()
    assert result["figures"][0]["status"]=="repaired" and result["summary"]["corrected"]==1
    assert result["figures"][0]["bbox"]==pytest.approx(expected)
    assert calls[-1][0]["figures"][0]["bbox"]==pytest.approx(expected)
    assert result["figures"][0]["review"]["recrop_padding"]["navigation_pixels"]==2
    assert page==original
    cached=execute(setup,cache=cache,post=lambda *_a,**_kw:pytest.fail("Verified crop repeated"))
    assert cached["summary"]["calls"]==0 and cached["summary"]["reused"]==1


def test_rejected_recrop_never_attaches_any_bad_asset(setup):
    root,_provider,_page=setup
    def respond(data):
        return [located(record) if data["stage"]=="relocate" else rejection(record) for record in data["figures"]]
    def recrop(*_a):
        path=root/"candidate.png";png(path,label="candidate")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/candidate.png"}
    post,calls=post_for(respond)
    result=execute(setup,post=post,recrop=recrop)
    assert len(calls)==3 and result["figures"][0]["status"]=="pending"
    assert result["figures"][0]["image_path"] is None
    assert result["figures"][0]["candidate_image_path"].endswith("candidate.png")
    assert result["figures"][0]["review"]["checks"]["full_graphic"] is False


def test_older_repaired_receipt_without_padding_metadata_still_reuses_verified_pixels(setup):
    root,_provider,_page=setup
    def respond(data):
        return [rejection(record) if data["stage"]=="first" else located(record)
                if data["stage"]=="relocate" else accepted(record) for record in data["figures"]]
    def recrop(*_a):
        path=root/"old_verified.png";png(path,label="historically verified pixels")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/old_verified.png"}
    post,_calls=post_for(respond);cache={}
    first=execute(setup,post=post,recrop=recrop,cache=cache)
    assert first["summary"]["repaired"]==1
    for key,packet in cache.items():
        if key.startswith("figure:") and packet.get("status")=="repaired":
            packet["review"].pop("recrop_padding",None)
    second=execute(setup,cache=cache,post=lambda *_a,**_kw:pytest.fail("Historical passed crop re-requested"))
    assert second["summary"]["calls"]==0 and second["summary"]["reused"]==1
    assert second["figures"][0]["status"]=="repaired"


@pytest.mark.parametrize("bad",["source","frame","duplicate","unknown_id","wrong_crop","false_accepted","body_rewrite"])
def test_response_identity_and_strict_fields_fail_closed(setup,bad):
    post,calls=post_for()
    def invalid(provider,payload,**kwargs):
        response=post(provider,payload,**kwargs)
        body=response.json();parsed=json.loads(body["choices"][0]["message"]["content"])
        entry=parsed["figures"][0]
        if bad=="source":parsed["source_sha256"]="0"*64
        if bad=="frame":parsed["frame"]="crop_0_1000"
        if bad=="duplicate":parsed["figures"].append(deepcopy(entry))
        if bad=="unknown_id":entry["id"]="another-figure"
        if bad=="wrong_crop":entry["crop_sha256"]="0"*64
        if bad=="false_accepted":entry["checks"]["full_graphic"]=False
        if bad=="body_rewrite":entry["content"]="AI rewrote question"
        body["choices"][0]["message"]["content"]=json.dumps(parsed)
        return response
    result=execute(setup,post=invalid)
    assert len(calls)==1 and result["summary"]["pending"]==1
    assert result["figures"][0]["image_path"] is None


def test_relocation_is_blocked_by_confirmed_neighbor_or_prose(setup):
    _root,_provider,page=setup
    page["protected_boxes"]=[[650,500,700,600]]
    post,calls=post_for(lambda data:[located(record) if data["stage"]=="relocate" else rejection(record)
                                   for record in data["figures"]])
    result=execute(setup,post=post)
    assert len(calls)==2 and result["figures"][0]["review"]["reason"]=="candidate_overlaps_neighbor"


def test_new_frame_padding_yields_to_verified_neighbor_and_rechecks_actual_unpadded_crop(setup):
    root,_provider,page=setup
    neighbor=root/"neighbor.png";png(neighbor,label="neighbor")
    page["figures"].append({"id":"p1-f2","slot_id":"slot-2","bbox":[902,450,980,800],
        "crop_path":str(neighbor),"image_path":"/static/uploads/tmp/neighbor.png","context":"邻图"})
    def respond(data):
        return [located(record) if data["stage"]=="relocate" else rejection(record)
                if data["stage"]=="first" and record["id"]=="p1-f1" else accepted(record)
                for record in data["figures"]]
    boxes=[]
    def recrop(_page,_id,box):
        boxes.append(box)
        path=root/"new.png";png(path,label="actual unpadded crop")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/new.png"}
    post,calls=post_for(respond)
    result=execute(setup,post=post,recrop=recrop)
    repaired=next(row for row in result["figures"] if row["id"]=="p1-f1")
    assert boxes==[[600.,450.,900.,800.]]
    assert repaired["bbox"]==boxes[0]
    assert repaired["review"]["recrop_padding"]["applied"] is False
    assert repaired["review"]["recrop_padding"]["reason"]=="neighbor"
    assert calls[-1][0]["stage"]=="recheck" and calls[-1][0]["figures"][0]["bbox"]==boxes[0]


def test_new_frame_padding_preserves_protected_prose(setup):
    root,_provider,page=setup
    page["protected_boxes"]=[[602,448,850,449]]
    post,calls=post_for(lambda data:[located(record) if data["stage"]=="relocate" else rejection(record)
        if data["stage"]=="first" else accepted(record) for record in data["figures"]])
    boxes=[]
    def recrop(_page,_id,box):
        boxes.append(box);path=root/"new.png";png(path,label="new")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/new.png"}
    result=execute(setup,post=post,recrop=recrop)
    assert boxes==[[600.,450.,900.,800.]]
    assert result["figures"][0]["review"]["recrop_padding"]["reason"]=="protected_prose"


def test_new_frame_padding_clamps_to_visible_page_bounds(setup):
    root,_provider,_page=setup
    def respond(data):
        return [located(record,(0,450,900,1000)) if data["stage"]=="relocate" else rejection(record)
                if data["stage"]=="first" else accepted(record) for record in data["figures"]]
    boxes=[]
    def recrop(_page,_id,box):
        boxes.append(box);path=root/"clamped.png";png(path,label="actual clamped PDF crop")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/clamped.png"}
    post,_calls=post_for(respond)
    result=execute(setup,post=post,recrop=recrop)
    assert boxes[0]==pytest.approx([0,450-2000/700,904,1000])
    assert result["figures"][0]["bbox"]==boxes[0]


def test_missing_crop_goes_to_full_page_relocation_without_fake_first_pass(setup):
    root,_provider,page=setup
    page["figures"][0].update(crop_path=None,image_path=None,bbox=None)
    post,calls=post_for(lambda data:[located(record) if data["stage"]=="relocate" else accepted(record)
                                   for record in data["figures"]])
    def recrop(*_a):
        path=root/"candidate.png";png(path,label="actual recrop")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/candidate.png"}
    result=execute(setup,post=post,recrop=recrop)
    assert [call[0]["stage"] for call in calls]==["relocate","recheck"]
    assert result["summary"]["repaired"]==1


def test_success_receipt_reuses_only_unchanged_figure_bytes(setup):
    root,_provider,page=setup
    path=root/"crop2.png";png(path,label="second")
    page["figures"].append({"id":"p1-f2","slot_id":"slot-2","bbox":[300,100,400,200],
        "crop_path":str(path),"image_path":"/static/uploads/tmp/second.png","context":"第二幅图"})
    cache={};post,calls=post_for()
    execute(setup,post=post,cache=cache)
    assert len(calls)==1 and len(calls[0][0]["figures"])==2
    png(path,label="changed only second")
    post2,calls2=post_for()
    result=execute(setup,post=post2,cache=cache)
    assert len(calls2)==1 and [row["id"] for row in calls2[0][0]["figures"]]==["p1-f2"]
    assert result["summary"]["reused"]==1 and result["summary"]["verified"]==2


def test_unknown_request_is_sticky_even_on_explicit_retry(setup):
    cache={};calls=[]
    def timeout(*_a,**_kw):calls.append(1);raise requests.ReadTimeout()
    first=execute(setup,post=timeout,cache=cache)
    assert first["summary"]["calls"]==1 and first["figures"][0]["image_path"] is None
    second=execute(setup,cache=cache,retry_pending=True,post=lambda *_a,**_kw:pytest.fail("Unknown request resent"))
    assert second["summary"]["calls"]==0 and calls==[1]
    assert second["figures"][0]["review"]["reason"]=="unknown_request_outcome"


def test_known_failed_figures_can_be_explicitly_retried(setup):
    cache={}
    def respond(data):
        if data["stage"]=="first":return [rejection(record) for record in data["figures"]]
        return [{"id":record["id"],"slot_id":record["slot_id"],"decision":"uncertain","bbox":None,"evidence":"本轮无法确认。"}
                for record in data["figures"]]
    post,calls=post_for(respond)
    initial=execute(setup,post=post,cache=cache)
    assert initial["summary"]["calls"]==2
    good,calls2=post_for()
    result=execute(setup,post=good,cache=cache,retry_pending=True)
    assert len(calls2)==1 and result["summary"]["verified"]==1
    assert any(":attempt:2" in key for key in cache)


@pytest.mark.parametrize("failure",["http","non_json","bad_inner"])
def test_known_http_or_json_failure_is_not_resent_without_explicit_retry(setup,failure):
    cache={};calls=[]
    raw=b"<html>private gateway failure</html>"
    good,_good_calls=post_for()
    def failed(provider,payload,**kwargs):
        calls.append(1)
        if failure=="http":
            return SimpleNamespace(status_code=503,content=raw,json=lambda:{"error":"busy"},close=lambda:None)
        if failure=="non_json":
            return SimpleNamespace(status_code=200,content=raw,
                json=lambda:(_ for _ in ()).throw(ValueError("invalid JSON")),close=lambda:None)
        result=good(provider,payload,**kwargs)
        body=result.json();body["choices"][0]["message"]["content"]="broken inner JSON"
        return result
    execute(setup,post=failed,cache=cache)
    repeated=execute(setup,cache=cache,post=lambda *_a,**_kw:pytest.fail("Failed HTTP implicitly resent"))
    assert calls==[1] and repeated["summary"]["calls"]==0
    retried=execute(setup,post=good,cache=cache,retry_pending=True)
    assert retried["summary"]["calls"]==1 and retried["summary"]["verified"]==1
    if failure in {"http","non_json"}:
        attempts=[packet for key,packet in cache.items() if ":attempt:1" in key]
        assert attempts[0]["raw_response_text"]==raw.decode()
        assert attempts[0]["raw_response_bytes"]==len(raw)
        assert attempts[0]["raw_response_sha256"]==hashlib.sha256(raw).hexdigest()


def test_budget_is_persistent_across_explicit_reanalysis(setup):
    root,_provider,_page=setup
    post,calls=post_for(lambda data:[located(record) if data["stage"]=="relocate" else rejection(record)
                                   for record in data["figures"]])
    def recrop(*_a):
        path=root/"new.png";png(path,label="new")
        return {"crop_path":str(path),"image_path":"/static/uploads/tmp/new.png"}
    cache={}
    first=execute(setup,post=post,cache=cache,recrop=recrop,max_calls=2,document_sha256="a"*64)
    assert first["summary"]["calls"]==2 and len(calls)==2
    again=execute(setup,cache=cache,recrop=recrop,max_calls=2,document_sha256="a"*64,retry_pending=True,
        post=lambda *_a,**_kw:pytest.fail("Document budget reset"))
    assert again["summary"]["calls"]==0 and again["summary"]["budget_used"]==2
    assert again["summary"]["reason"]=="budget_limit" and "未追加模型请求" in again["summary"]["notes"][0]


def test_same_document_cache_has_independent_explicit_provider_budget(setup):
    _root,provider,page=setup
    cache={}
    rejected,calls=post_for(lambda data:[rejection(record) for record in data["figures"]])
    first=execute(setup,post=rejected,cache=cache,max_calls=1,document_sha256="a"*64)
    assert first["summary"]["calls"]==1 and first["summary"]["pending"]==1
    again=execute(setup,cache=cache,max_calls=1,document_sha256="a"*64,retry_pending=True,
                  post=lambda *_a,**_kw:pytest.fail("Same-provider document budget reset"))
    assert again["summary"]["calls"]==0 and again["summary"]["historical_calls"]==1
    other=replace(provider,provider_code="zhongzhan_gpt",model_name="gemini-3.8-flash-high")
    good,other_calls=post_for()
    second=review_pdf_figures([page],provider=other,post=good,cache=cache,
        recrop=lambda *_a:pytest.fail("Unexpected recrop"),max_calls=1,document_sha256="a"*64)
    assert len(other_calls)==1 and second["summary"]["historical_calls"]==0
    assert second["summary"]["calls"]==1 and second["summary"]["verified"]==1
    budgets=[packet for key,packet in cache.items() if key.startswith("budget:")]
    assert len(budgets)==2 and all(packet["spent"]==1 for packet in budgets)


def test_failed_required_cache_save_does_not_buy_another_model_call(setup):
    class BrokenCache:
        def get(self,_key):return None
        def put(self,_key,_packet):raise OSError("disk full")
    result=execute(setup,cache=BrokenCache(),post=lambda *_a,**_kw:pytest.fail("Paid without durable dispatch"))
    assert result["summary"]["calls"]==0 and result["summary"]["pending"]==1


def test_source_change_invalidates_results_before_delivery(setup):
    root,_provider,_page=setup
    good,_calls=post_for()
    def changing(provider,payload,**kwargs):
        response=good(provider,payload,**kwargs)
        png(root/"page.png",(500,700),"source changed")
        return response
    with pytest.raises(PdfFigureReviewError,match="source_or_crop_changed"):
        execute(setup,post=changing)


def test_four_page_http_parallelism_and_global_semaphore(setup):
    _root,provider,page=setup
    pages=[]
    for n in range(1,5):
        value=deepcopy(page);value.update(page_number=n,page_index=n-1)
        value["figures"][0].update(id=f"p{n}-f1",slot_id=f"slot-{n}")
        pages.append(value)
    barrier=threading.Barrier(4);lock=threading.Lock();active=peak=0
    good,_calls=post_for()
    def post(config,payload,**kwargs):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        barrier.wait(timeout=3)
        try:return good(config,payload,**kwargs)
        finally:
            with lock:active-=1
    result=review_pdf_figures(pages,provider=provider,post=post,semaphore=threading.BoundedSemaphore(4),
                             recrop=lambda *_a:pytest.fail("Unexpected recrop"))
    assert peak==4 and result["summary"]["verified"]==4 and result["summary"]["calls"]==4


def test_cancel_does_not_wait_for_a_blocked_http(setup):
    cancelled=threading.Event();release=threading.Event()
    def post(*_a,**_kw):
        cancelled.set();release.wait(2)
        raise requests.ReadTimeout()
    def check():
        if cancelled.is_set():raise TaskCancelled("cancelled")
    started=time.monotonic()
    try:
        with pytest.raises(TaskCancelled):execute(setup,post=post,check_cancelled=check)
        assert time.monotonic()-started<.6
    finally:release.set()


def test_relocation_receives_rejected_crop_feedback_with_full_page_as_only_coordinate_frame(setup):
    root, _provider, page = setup

    def respond(data):
        return [located(record) if data['stage']=='relocate' else rejection(record)
                for record in data['figures']]

    def recrop(*_args):
        path=root/'feedback-candidate.png'
        png(path,(180,160),'candidate')
        return {'crop_path':str(path),'image_path':'/static/uploads/tmp/feedback.png'}

    post,calls=post_for(respond)
    result=execute(setup,post=post,recrop=recrop)
    data,payload,_=next(call for call in calls if call[0]['stage']=='relocate')
    record=data['figures'][0]
    assert record['failed_crop_review']['decision']=='rejected'
    assert record['failed_crop_review']['checks']['full_graphic']is False
    assert record['failed_crop_review']['evidence']=='实际裁片不完整，需要从整页重新定位。'
    user=payload['messages'][-1]['content']
    images=[part['image_url']['url']for part in user if part['type']=='image_url']
    import base64
    assert len(images)==2
    assert base64.b64decode(images[0].split(',',1)[1])==(root/'page.png').read_bytes()
    assert base64.b64decode(images[1].split(',',1)[1])==(root/'crop.png').read_bytes()
    assert data['frame']==FRAME and record['frame']==FRAME
    assert any('负面证据'in part.get('text','')for part in user)
    assert result['summary']['calls']==3 and result['summary']['verified']==0
    assert result['figures'][0]['status']=='pending','Feedback cannot authorize a rejected crop'
