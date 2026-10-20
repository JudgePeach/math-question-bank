"""Opt-in ZIP sharing checks in a source-only, disposable app and browser.

MATHBANK_TEST_BROWSER=1 python3 -m pytest --noconftest
tests/test_library_transfer_browser.py
"""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import uuid
import zipfile

from PIL import Image
import pytest

from test_preview_browser import browser


pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1",
    reason="opt-in actual library-transfer browser regression",
)


def _open_sharing(browser):
    if browser.evaluate("document.getElementById('settingsModal').classList.contains('hidden')"):
        browser.evaluate("openSettingsModal(); true")
    browser.command("wait", "--fn", "!document.getElementById('settingsModal').classList.contains('opacity-0')")
    browser.command("scrollintoview", "#btn-settings-transfer")
    browser.command("click", "#btn-settings-transfer")
    browser.command("wait", "--fn", "!document.getElementById('settings-tab-transfer').classList.contains('hidden')")
    _open_export(browser)
    browser.settle()


def _close_settings(browser):
    browser.command("press", "Escape")
    browser.command("wait", "--fn", "document.getElementById('settingsModal').classList.contains('hidden')")


def _open_export(browser):
    browser.command("click", "#libraryTransferExportTab")
    browser.command("wait", "--fn", "!document.getElementById('libraryExportPanel').hidden")


def _open_import(browser):
    browser.command("click", "#libraryTransferImportTab")
    browser.command("wait", "--fn", "!document.getElementById('libraryImportPanel').hidden")


def _click(browser, selector):
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)


def _check(browser, selector):
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("check", selector)


def _click_import(browser):
    _click(browser, "#libraryImportBtn")


def _wait_export_list(browser, total):
    expected = json.dumps(f"命中 {total} 题")
    browser.command("wait", "--fn", (
        "document.getElementById('libraryExportList').getAttribute('aria-busy')==='false'"
        f" && document.getElementById('libraryExportCount').textContent.includes({expected})"
    ))


def _filter_export_source(browser, source, total):
    browser.command("fill", "#libraryExportSource", source)
    _wait_export_list(browser, total)


def _reset_export_selection(browser):
    _open_export(browser)
    browser.evaluate("""(() => {
        clearLibraryExportSelection();
        for(const chip of [...document.querySelectorAll('#libraryExportPointsSelected button')])chip.click();
        for(const id of ['libraryExportSource','libraryExportSearch','libraryExportCompulsory',
                'libraryExportChapter','libraryExportKnowledge','libraryExportPointSearch']){
            document.getElementById(id).value='';
        }
        document.getElementById('libraryExportPointMode').value='any';
        document.getElementById('libraryExportMoreFilters').open=false;
        document.getElementById('libraryExportPointPicker').open=false;
        changeLibraryExportFilters();return true;
    })()""")


def _download_export_from_ui(browser):
    _open_export(browser)
    browser.evaluate("""(() => {
        window.__sharingExport={originalFetch:window.fetch,body:null,base64:null};
        window.fetch=async function(url,init){
            const response=await __sharingExport.originalFetch.call(this,url,init);
            if(String(url)==='/api/library-transfer/export'&&response.ok){
                __sharingExport.body=JSON.parse(init.body);
                const bytes=new Uint8Array(await response.clone().arrayBuffer());
                let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
                __sharingExport.base64=btoa(binary);
            }
            return response;
        };return true;
    })()""")
    try:
        browser.command("scrollintoview", "#libraryExportBtn")
        browser.settle()
        browser.command("click", "#libraryExportBtn")
        browser.command("wait", "--fn", "document.getElementById('libraryTransferStatus').dataset.state==='success' && !!window.__sharingExport.base64")
        return browser.evaluate("({body:__sharingExport.body,base64:__sharingExport.base64})")
    finally:
        browser.evaluate("window.fetch=__sharingExport.originalFetch; true")


def _source_questions(browser, source):
    return browser.evaluate("""(async () => {
        const params=new URLSearchParams({source:%s,page:'1',page_size:'100'});
        const response=await fetch('/api/questions?'+params);
        if(!response.ok)throw new Error(await response.text());
        const page=await response.json();
        return Promise.all(page.items.map(q=>fetch('/api/questions/'+q.id).then(r=>r.json())));
    })()""" % json.dumps(source))


def _sharing_screenshot(browser, filename):
    destination = Path(__file__).resolve().parents[1] / ".system_generated" / "library_transfer-qa" / "layout-formulas"
    destination.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(destination / filename))


@pytest.fixture(scope="module")
def sharing_package(browser, tmp_path_factory):
    """Export a real fixture through the UI; never read the user's database."""
    folder = tmp_path_factory.mktemp("library-transfer-files")
    source = "分享浏览器专项-" + uuid.uuid4().hex
    image_path = browser.root / "static" / "uploads" / "sharing-fixture.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (42, 30), (48, 100, 180)).save(image_path)
    image_url = "/static/uploads/" + image_path.name
    question = browser.evaluate(r"""(async () => {
        const values={
            content:'分享包原文：计算 $1+2$。\n\n![题图](%s)',
            answer_markdown:'答案为 $3$，保留完整解析。',review:'保留教师评析。',
            source:%s,question_type:'detailed_answer',difficulty:'normal',
            figure_align:'bottom_right',figure_size:'small',figure_align_custom:'true',
            image_paths:JSON.stringify([%s]),tags:'分享测试'
        };
        const body=new FormData();for(const [key,value] of Object.entries(values))body.set(key,value);
        const response=await fetch('/api/questions',{method:'POST',body});
        const data=await response.json();if(!response.ok||!data.question)throw new Error(JSON.stringify(data));
        return data.question;
    })()""" % (image_url, json.dumps(source), json.dumps(image_url)))
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, source, 1)
    browser.command("click", "#libraryExportSelectAllBtn")
    browser.command("wait", "--fn", "document.getElementById('libraryExportSelectedViewBtn').textContent==='已选 (1)'")
    payload = _download_export_from_ui(browser)
    assert payload["body"] == {"question_ids": [question["id"]]}
    package_path = folder / "MathBank-分享包.zip"
    package_path.write_bytes(base64.b64decode(payload["base64"]))
    assert zipfile.is_zipfile(package_path)
    with zipfile.ZipFile(package_path) as archive:
        assert archive.namelist(), "The UI must download a populated ZIP"
    return {
        "path": package_path, "source": source, "question": question,
        "image_url": image_url, "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize("viewport", [(1440, 1000), (375, 812)])
def test_sharing_settings_fit_and_preserve_unsaved_settings(browser, tmp_path, viewport):
    browser.command("set", "viewport", str(viewport[0]), str(viewport[1]))
    _open_sharing(browser)
    browser.command("scrollintoview", "#btn-settings-api")
    browser.command("click", "#btn-settings-api")
    browser.command("wait", "--fn", "activeSettingsTab==='api'")
    browser.command("wait", "--fn", "document.getElementById('settingsMetadataJson').value.trim().startsWith('{')")
    original_key = browser.evaluate("document.getElementById('settingsDeepseekKey').value")
    original_metadata = browser.evaluate("document.getElementById('settingsMetadataJson').value")
    browser.command("fill", "#settingsDeepseekKey", "未保存的接口设置")
    assert browser.evaluate("document.getElementById('settingsDeepseekKey').value") == "未保存的接口设置"
    browser.command("scrollintoview", "#btn-settings-metadata")
    browser.command("click", "#btn-settings-metadata")
    metadata = '{"unsaved_sharing_test": true}'
    browser.command("fill", "#settingsMetadataJson", metadata)
    browser.command("scrollintoview", "#btn-settings-transfer")
    browser.command("click", "#btn-settings-transfer")
    browser.settle()
    _open_export(browser)
    export_heights = browser.evaluate("""Object.fromEntries(
        ['libraryExportSource','libraryExportSearch','libraryExportBtn']
        .map(id=>[id,document.getElementById(id).getBoundingClientRect().height]))""")
    _open_import(browser)
    state = browser.evaluate("""(() => {
        const modal=document.querySelector('#settingsModal > .glass-modal');
        const panel=document.getElementById('settings-tab-transfer');
        const rect=modal.getBoundingClientRect();
        const ids=['btn-settings-transfer','libraryTransferExportTab','libraryTransferImportTab','libraryImportFile','libraryImportBtn'];
        return {left:rect.left,right:rect.right,width:innerWidth,
            overflow:panel.scrollWidth-panel.clientWidth,
            heights:Object.fromEntries(ids.map(id=>[id,document.getElementById(id).getBoundingClientRect().height])),
            focused:document.getElementById('settingsModal').contains(document.activeElement),
            saveHidden:document.getElementById('btnSettingsSave').classList.contains('hidden')};
    })()""")
    assert state["left"] >= -1 and state["right"] <= state["width"] + 1, state
    assert state["overflow"] <= 1, state
    assert all(height >= 44 for height in {**export_heights, **state["heights"]}.values()), state
    assert state["focused"] and state["saveHidden"], state
    browser.command("screenshot", str(tmp_path / f"sharing-settings-{viewport[0]}.png"))
    browser.command("scrollintoview", "#btn-settings-metadata")
    browser.command("click", "#btn-settings-metadata")
    assert browser.evaluate("document.getElementById('settingsMetadataJson').value") == metadata
    browser.command("scrollintoview", "#btn-settings-api")
    browser.command("click", "#btn-settings-api")
    assert browser.evaluate("document.getElementById('settingsDeepseekKey').value") == "未保存的接口设置"
    browser.evaluate("document.getElementById('settingsDeepseekKey').value=%s;document.getElementById('settingsMetadataJson').value=%s;true" % (
        json.dumps(original_key), json.dumps(original_metadata),
    ))
    _close_settings(browser)


def test_real_export_import_appends_twice_and_preserves_editor(browser, sharing_package, tmp_path):
    browser.command("set", "viewport", "1440", "1000")
    before = _source_questions(browser, sharing_package["source"])
    assert len(before) == 1
    original_id = before[0]["id"]
    browser.evaluate("""(() => {
        const draft={id:'sharing-local-draft',content:'本地草稿保留',question_type:'detailed_answer',
            difficulty:'normal',source:'保留中的草稿',image_paths:[],answer_markdown:'草稿解析'};
        setLocalStorageDrafts([draft]);selectDraft(draft);
        const input=document.getElementById('editContent');input.value='分享导入期间未保存的题干';
        input.dispatchEvent(new Event('input',{bubbles:true}));
        PaperStore.cart=[{id:%d,score:7,solution_space:3}];
        window.__sharingPreserved={editor:EditorState.snapshot(),
            draft:localStorage.getItem('mathbank_local_drafts'),cart:JSON.stringify(PaperStore.cart)};
        return true;
    })()""" % original_id)
    assert browser.evaluate("EditorState.draftId==='sharing-local-draft' && isEditorModified()")
    _open_sharing(browser)
    _open_import(browser)
    ids = {original_id}
    for expected_count in (2, 3):
        browser.command("upload", "#libraryImportFile", str(sharing_package["path"]))
        _click_import(browser)
        browser.command("wait", "--fn", "document.getElementById('libraryTransferStatus').dataset.state==='success' && !document.getElementById('libraryImportFile').files.length")
        questions = _source_questions(browser, sharing_package["source"])
        assert len(questions) == expected_count
        new_ids = {q["id"] for q in questions}
        assert ids < new_ids
        ids = new_ids
        for question in questions:
            for field in ("source", "answer_markdown", "review", "tags", "figure_align", "figure_size", "figure_align_custom"):
                assert question[field] == before[0][field], (field, question)
            assert "分享包原文：计算 $1+2$。" in question["content"]
            assert len(question["image_paths"]) == 1
            image_url = question["image_paths"][0]
            assert image_url in question["content"]
            image = browser.evaluate("""(async () => {
                const url=%s;const response=await fetch(url);
                const bytes=await response.arrayBuffer();
                const digest=await crypto.subtle.digest('SHA-256',bytes);
                const sha=[...new Uint8Array(digest)].map(n=>n.toString(16).padStart(2,'0')).join('');
                const image=new Image();image.src=url;await image.decode();
                return {ok:response.ok,sha,width:image.naturalWidth,height:image.naturalHeight};
            })()""" % json.dumps(image_url))
            assert image == {"ok": True, "sha": sharing_package["image_sha256"], "width": 42, "height": 30}
        assert browser.evaluate("""document.getElementById('editContent').value==='分享导入期间未保存的题干'
            && isEditorModified() && EditorState.draftId==='sharing-local-draft'
            && JSON.stringify(EditorState.snapshot())===JSON.stringify(__sharingPreserved.editor)
            && localStorage.getItem('mathbank_local_drafts')===__sharingPreserved.draft
            && JSON.stringify(PaperStore.cart)===__sharingPreserved.cart""")
    assert sharing_package["image_sha256"] == hashlib.sha256(
        (browser.root / sharing_package["image_url"].lstrip("/")).read_bytes()
    ).hexdigest(), "Import must never overwrite the preexisting image"
    browser.command("screenshot", str(tmp_path / "sharing-import-success.png"))
    _close_settings(browser)


def test_arbitrary_zip_is_rejected_without_appending(browser, tmp_path):
    invalid = tmp_path / "ordinary.zip"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("notes.txt", "This is not a MathBank sharing package")
    count_before = browser.evaluate("fetch('/api/questions?page=1&page_size=1').then(r=>r.json()).then(p=>p.total)")
    _open_sharing(browser)
    _open_import(browser)
    browser.command("upload", "#libraryImportFile", str(invalid))
    _click_import(browser)
    browser.command("wait", "--fn", "document.getElementById('libraryTransferStatus').dataset.state==='error' && !document.getElementById('libraryImportBtn').disabled")
    message = browser.evaluate("document.getElementById('libraryTransferMessage').textContent")
    assert "导入失败" in message and len(message) > len("导入失败："), message
    assert not browser.evaluate("!!document.querySelector('#libraryTransferMessage script,#libraryTransferMessage img')")
    assert browser.evaluate("fetch('/api/questions?page=1&page_size=1').then(r=>r.json()).then(p=>p.total)") == count_before
    browser.command("screenshot", str(tmp_path / "sharing-invalid-zip.png"))
    _close_settings(browser)


def test_import_busy_guard_survives_close_and_reopen(browser, sharing_package):
    _open_sharing(browser)
    _open_import(browser)
    browser.command("upload", "#libraryImportFile", str(sharing_package["path"]))
    browser.evaluate("""(() => {
        window.__sharingBusy={originalFetch:window.fetch,requests:0,release:null};
        window.fetch=function(url,init){
            if(String(url)!=='/api/library-transfer/import')return __sharingBusy.originalFetch.call(this,url,init);
            __sharingBusy.requests++;
            return new Promise(resolve=>{__sharingBusy.release=()=>resolve(__sharingBusy.originalFetch.call(this,url,init));});
        };return true;
    })()""")
    try:
        _click_import(browser)
        browser.command("wait", "--fn", "!!__sharingBusy.release")
        browser.evaluate("importLibraryTransfer(); exportLibraryTransfer(); true")
        assert browser.evaluate("__sharingBusy.requests") == 1
        assert browser.evaluate("""['libraryExportSource','libraryExportBtn','libraryImportFile','libraryImportBtn']
            .every(id=>document.getElementById(id).matches(':disabled'))""")
        # Switching the two visible top-level actions must not reset the
        # in-flight operation or allow a second request.
        _open_export(browser)
        browser.evaluate("exportLibraryTransfer();true")
        _open_import(browser)
        browser.evaluate("importLibraryTransfer();true")
        assert browser.evaluate("__sharingBusy.requests") == 1
        _close_settings(browser)
        browser.evaluate("openSettingsModal(); true")
        browser.command("wait", "--fn", "!document.getElementById('settingsModal').classList.contains('opacity-0')")
        assert browser.evaluate("""!document.getElementById('settings-tab-transfer').classList.contains('hidden')
            && document.getElementById('libraryTransferStatus').dataset.state==='busy'
            && document.getElementById('libraryImportFile').disabled""")
        browser.evaluate("__sharingBusy.release(); true")
        browser.command("wait", "--fn", "document.getElementById('libraryTransferStatus').dataset.state==='success' && !document.getElementById('libraryImportFile').disabled")
        assert browser.evaluate("__sharingBusy.requests") == 1
        assert browser.evaluate("document.getElementById('libraryImportBtn').disabled")
    finally:
        browser.evaluate("window.fetch=__sharingBusy.originalFetch; true")
    _close_settings(browser)


@pytest.fixture(scope="module")
def selection_questions(browser):
    suffix = uuid.uuid4().hex[:10]
    source = "分页勾选专项-" + suffix
    other_source = "另一来源专项-" + suffix
    return browser.evaluate(r"""(async () => {
        const source=%s,otherSource=%s,names=[%s,%s],points=[];
        await prepareLibraryExportFilters(false);
        for(const name of names){
            const response=await fetch('/api/knowledge-points',{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})
            });
            const point=await response.json();if(!response.ok)throw new Error(JSON.stringify(point));
            points.push(point);
        }
        const ids=[];
        for(let index=0;index<13;index++){
            const labels=[];
            if(index<12&&index%%2===0)labels.push({code:points[0].code,role:'main',status:'confirmed'});
            if(index<4)labels.push({code:points[1].code,role:'main',status:'confirmed'});
            const form=new FormData();
            for(const [key,value] of Object.entries({
                content:'勾选浏览器题'+index+'：计算 $1+2$。',question_type:'detailed_answer',
                difficulty:'normal',source:index<12?source:otherSource,
                knowledge_points:JSON.stringify(labels)
            }))form.set(key,value);
            const response=await fetch('/api/questions',{method:'POST',body:form});
            const data=await response.json();if(!response.ok)throw new Error(JSON.stringify(data));
            ids.push(data.question.id);
        }
        // Fixture API writes intentionally skip the editor's local-point create
        // callback, so refresh its existing shared catalog cache explicitly.
        await window.loadKnowledgePointCatalog(true);
        await prepareLibraryExportFilters(false);
        return {ids,source,other_source:otherSource,points};
    })()""" % (
        json.dumps(source), json.dumps(other_source),
        json.dumps("分享筛选考点甲-" + suffix), json.dumps("分享筛选考点乙-" + suffix),
    ))


def test_export_checkboxes_survive_pages_and_filters_and_download_exact_selection(browser, selection_questions, tmp_path):
    fixture = selection_questions
    browser.command("set", "viewport", "1440", "1000")
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, fixture["source"], 12)
    assert browser.evaluate("document.querySelectorAll('#libraryExportList article').length") == 10
    assert not browser.evaluate("document.getElementById('libraryExportMoreFilters').open")
    first, second, third = fixture["ids"][0], fixture["ids"][10], fixture["ids"][12]
    _check(browser, f'#libraryExportList input[data-question-id="{first}"]')
    _click(browser, "#libraryExportNextBtn")
    browser.command("wait", "--fn", "document.getElementById('libraryExportPageLabel').textContent==='2 / 2' && document.querySelectorAll('#libraryExportList article').length===2")
    _check(browser, f'#libraryExportList input[data-question-id="{second}"]')
    _filter_export_source(browser, fixture["other_source"], 1)
    assert browser.evaluate("document.getElementById('libraryExportSelectedViewBtn').textContent") == "已选 (2)"
    _check(browser, f'#libraryExportList input[data-question-id="{third}"]')
    _click(browser, "#libraryExportSelectedViewBtn")
    browser.command("wait", "--fn", "document.querySelectorAll('#libraryExportList article').length===3 && document.getElementById('libraryExportSelectedViewBtn').getAttribute('aria-pressed')==='true'")
    assert browser.evaluate("[...document.querySelectorAll('#libraryExportList article')].map(row=>Number(row.dataset.questionId))") == [first, second, third]
    _click(browser, f'#libraryExportList article[data-question-id="{first}"] summary')
    browser.command("wait", "--fn", "!!document.querySelector('#libraryExportList .mb-transfer-question-preview .katex')")
    browser.command("set", "viewport", "375", "812")
    browser.command("scrollintoview", "#libraryExportList")
    browser.settle()
    assert browser.evaluate("document.getElementById('settings-tab-transfer').scrollWidth-document.getElementById('settings-tab-transfer').clientWidth") <= 1
    browser.command("screenshot", str(tmp_path / "sharing-selected-375.png"))
    browser.command("set", "viewport", "1440", "1000")
    payload = _download_export_from_ui(browser)
    assert payload["body"] == {"question_ids": [first, second, third]}
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["base64"]))) as package:
        assert [row["id"] for row in json.loads(package.read("questions.json"))] == [first, second, third]
    browser.command("screenshot", str(tmp_path / "sharing-selected-across-filters.png"))
    _close_settings(browser)


def test_export_knowledge_point_filter_any_all_and_review_selected(browser, selection_questions, tmp_path):
    fixture = selection_questions
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, fixture["source"], 12)
    _click(browser, "#libraryExportMoreFilters > summary")
    _click(browser, "#libraryExportPointPicker > summary")
    for index, expected_total in ((0, 6), (1, 8)):
        browser.command("fill", "#libraryExportPointSearch", fixture["points"][index]["name"])
        browser.command("wait", "--fn", "document.querySelectorAll('#libraryExportPointOptions button').length===1 && document.getElementById('libraryExportPointInfo').textContent.includes('1 项')")
        _click(browser, "#libraryExportPointOptions button")
        _wait_export_list(browser, expected_total)
    browser.command("fill", "#libraryExportPointSearch", "")
    browser.command("scrollintoview", "#libraryExportPointPicker")
    browser.settle()
    assert browser.evaluate("document.getElementById('libraryExportPointOptions').getBoundingClientRect().height") <= 156
    browser.command("screenshot", str(tmp_path / "sharing-knowledge-picker-expanded.png"))
    browser.command("set", "viewport", "375", "812")
    browser.command("scrollintoview", "#libraryExportPointPicker")
    browser.settle()
    assert browser.evaluate("document.getElementById('settings-tab-transfer').scrollWidth-document.getElementById('settings-tab-transfer').clientWidth") <= 1
    assert browser.evaluate("document.getElementById('libraryExportPointOptions').getBoundingClientRect().height") <= 156
    browser.command("screenshot", str(tmp_path / "sharing-knowledge-picker-375.png"))
    browser.command("set", "viewport", "1440", "1000")
    browser.command("select", "#libraryExportPointMode", "all")
    _wait_export_list(browser, 2)
    expected_ids = [fixture["ids"][0], fixture["ids"][2]]
    assert browser.evaluate("[...document.querySelectorAll('#libraryExportList article')].map(row=>Number(row.dataset.questionId))") == expected_ids
    # An ordinary click outside the filter popover closes it before continuing
    # with the result toolbar, without requiring a second summary toggle.
    _click(browser, "#libraryExportSource")
    _wait_export_list(browser, 2)
    assert not browser.evaluate("document.getElementById('libraryExportMoreFilters').open")
    _click(browser, "#libraryExportSelectPageBtn")
    _click(browser, "#libraryExportSelectedViewBtn")
    browser.command("wait", "--fn", "document.getElementById('libraryExportSelectedViewBtn').getAttribute('aria-pressed')==='true' && document.getElementById('libraryExportList').getAttribute('aria-busy')==='false'")
    assert browser.evaluate("document.querySelectorAll('#libraryExportList input:checked').length") == 2
    # Removing a selected row replaces its DOM node immediately. A native click
    # avoids the CLI's post-uncheck lookup of the intentionally removed input.
    _click(browser, f'#libraryExportList input[data-question-id="{expected_ids[0]}"]')
    browser.command("wait", "--fn", "document.querySelectorAll('#libraryExportList article').length===1")
    assert browser.evaluate("document.getElementById('libraryExportSelectedViewBtn').textContent") == "已选 (1)"
    browser.command("screenshot", str(tmp_path / "sharing-knowledge-filter.png"))
    _close_settings(browser)


def test_export_ignores_late_preview_and_late_select_all_after_clear(browser, selection_questions):
    fixture = selection_questions
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, fixture["source"], 12)
    browser.evaluate("""(() => {
        window.__sharingRace={originalFetch:window.fetch,releasePreview:null,releaseSelect:null};
        window.fetch=function(url,init){
            if(String(url)==='/api/library-transfer/preview'&&JSON.parse(init.body).filters?.search==='延迟响应'){
                return new Promise(resolve=>{__sharingRace.releasePreview=()=>resolve(new Response(JSON.stringify({
                    items:[{id:%d,seq_num:1,content:'过期请求不应显示',source:'过期来源',knowledge_points:[]}],
                    total:1,page:1,page_size:10,total_pages:1,missing_ids:[]
                }),{status:200,headers:{'Content-Type':'application/json'}}));});
            }
            if(String(url)==='/api/library-transfer/select-ids'){
                return new Promise(resolve=>{__sharingRace.releaseSelect=()=>resolve(new Response(JSON.stringify({
                    ids:%s,total:12
                }),{status:200,headers:{'Content-Type':'application/json'}}));});
            }
            return __sharingRace.originalFetch.call(this,url,init);
        };return true;
    })()""" % (fixture["ids"][0], json.dumps(fixture["ids"][:12])))
    try:
        browser.command("fill", "#libraryExportSearch", "延迟响应")
        browser.command("wait", "--fn", "!!__sharingRace.releasePreview")
        browser.command("fill", "#libraryExportSearch", "")
        _wait_export_list(browser, 12)
        browser.evaluate("__sharingRace.releasePreview();true")
        browser.settle()
        assert "过期请求不应显示" not in browser.evaluate("document.getElementById('libraryExportList').textContent")
        _check(browser, f'#libraryExportList input[data-question-id="{fixture["ids"][0]}"]')
        _click(browser, "#libraryExportSelectAllBtn")
        browser.command("wait", "--fn", "!!__sharingRace.releaseSelect")
        _click(browser, "#libraryExportClearBtn")
        browser.evaluate("__sharingRace.releaseSelect();true")
        browser.command("wait", "--fn", "!document.getElementById('libraryExportSelectAllBtn').disabled")
        assert browser.evaluate("document.getElementById('libraryExportSelectedViewBtn').textContent") == "已选 (0)"
        assert browser.evaluate("document.getElementById('libraryExportBtn').disabled")
    finally:
        browser.evaluate("window.fetch=__sharingRace.originalFetch;true")
    _close_settings(browser)


def test_deleted_selected_question_blocks_export_until_removed(browser, selection_questions):
    fixture = selection_questions
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, fixture["other_source"], 1)
    qid = fixture["ids"][12]
    _check(browser, f'#libraryExportList input[data-question-id="{qid}"]')
    assert browser.evaluate("""fetch('/api/questions/%d',{method:'DELETE'}).then(r=>r.ok)""" % qid)
    browser.evaluate("""(() => {
        window.__sharingDeleted={originalFetch:window.fetch,exports:0};
        window.fetch=function(url,init){
            if(String(url)==='/api/library-transfer/export')__sharingDeleted.exports++;
            return __sharingDeleted.originalFetch.call(this,url,init);
        };return true;
    })()""")
    try:
        _click(browser, "#libraryExportBtn")
        browser.command("wait", "--fn", "document.getElementById('libraryTransferStatus').dataset.state==='error' && !document.getElementById('libraryExportMissing').hidden")
        assert browser.evaluate("__sharingDeleted.exports") == 0
        assert browser.evaluate("document.getElementById('libraryExportBtn').disabled")
        _click(browser, "#libraryExportMissing button")
        _wait_export_list(browser, 0)
        assert browser.evaluate("document.getElementById('libraryExportSelectedViewBtn').textContent") == "已选 (0)"
        assert browser.evaluate("document.getElementById('libraryExportMissing').hidden")
    finally:
        browser.evaluate("window.fetch=__sharingDeleted.originalFetch;true")
    _close_settings(browser)


def test_export_summaries_render_shared_math_and_preserve_complete_package(browser, sharing_package):
    source = "数学摘要专项-" + uuid.uuid4().hex
    samples = [
        r"计算 $\dfrac{x_1^2+1}{\sqrt{a_2}}$，已知 $0<x_1<2$。",
        r"复数 $z=1+\iu$，常数 $\eu^x$，弧长 $\wideparen{ABC}$。",
        r"选择正确结果。\begin{choices}\item $\frac{1}{2}$\item $x_1^2$\item $\sqrt{3}$\item $\iu$\end{choices}",
        "<!-- MathBank source attribution:\n隐藏来源不应进入摘要\n-->\n\n"
        + r"完整长题：$\dfrac{1}{x_1^2+1}$。"
        + "保留完整题干与后续说明。" * 45
        + r"末尾公式 $\wideparen{AB}+\eu^{x_2}$。"
        + "\n\n![配图](" + sharing_package["image_url"] + ")",
    ]
    questions = browser.evaluate("""(async () => {
        const source=%s,samples=%s,imageUrl=%s,questions=[];
        for(const [index,content] of samples.entries()){
            const values={content,source,difficulty:'normal',
                question_type:index===2?'single_choice':'detailed_answer',
                image_paths:JSON.stringify(index===3?[imageUrl]:[])};
            const form=new FormData();for(const [key,value] of Object.entries(values))form.set(key,value);
            const response=await fetch('/api/questions',{method:'POST',body:form});
            const data=await response.json();if(!response.ok||!data.question)throw new Error(JSON.stringify(data));
            questions.push(data.question);
        }return questions;
    })()""" % (json.dumps(source), json.dumps(samples), json.dumps(sharing_package["image_url"])))
    browser.command("set", "viewport", "1440", "900")
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, source, 4)
    browser.settle()
    rendering = browser.evaluate(r"""(() => {
        const questions=%s;
        function signature(host){
            const clone=host.cloneNode(true);clone.querySelectorAll('img').forEach(node=>node.remove());
            const annotations=[...clone.querySelectorAll('annotation')].map(node=>node.textContent);
            clone.querySelectorAll('.katex-mathml').forEach(node=>node.remove());
            const visible=clone.textContent.replace(/\s+/g,'').replace(/\[配图\]/g,'');
            clone.querySelectorAll('.katex').forEach(node=>node.remove());
            return {annotations,visible,literal:clone.textContent,errors:host.querySelectorAll('.katex-error').length};
        }
        return questions.map(question=>{
            const excerpt=document.querySelector('#libraryExportList article[data-question-id="'+question.id+'"] .mb-transfer-question-excerpt');
            const expected=document.createElement('div');
            renderQuestionPreviewContent(expected,question.content);
            return {actual:signature(excerpt),expected:signature(expected),images:excerpt.querySelectorAll('img').length,
                height:excerpt.getBoundingClientRect().height,expanded:!!excerpt.closest('article').querySelector('details[open]')};
        });
    })()""" % json.dumps(questions))
    for result in rendering:
        assert result["actual"]["annotations"] == result["expected"]["annotations"], result
        assert result["actual"]["visible"] == result["expected"]["visible"], result
        assert not result["actual"]["errors"] and not result["images"] and not result["expanded"], result
        assert "$" not in result["actual"]["literal"] and "\\" not in result["actual"]["literal"], result
        assert "隐藏来源不应进入摘要" not in result["actual"]["literal"], result
        assert result["height"] <= 120, result
    footer = browser.evaluate("""(() => {
        const body=document.getElementById('settingsScrollableBody').getBoundingClientRect();
        const button=document.getElementById('libraryExportBtn').getBoundingClientRect();
        return {top:button.top,bottom:button.bottom,bodyTop:body.top,bodyBottom:body.bottom};
    })()""")
    assert footer["top"] >= footer["bodyTop"] and footer["bottom"] <= footer["bodyBottom"], footer
    _sharing_screenshot(browser, "sharing-formula-summaries-1440.png")
    last = questions[-1]
    _click(browser, f'#libraryExportList article[data-question-id="{last["id"]}"] summary')
    browser.command("wait", "--fn", "document.querySelectorAll('#libraryExportList .mb-transfer-question-preview .katex').length===2")
    assert "末尾公式" in browser.evaluate("document.querySelector('#libraryExportList .mb-transfer-question-preview').textContent")
    assert not browser.evaluate("!!document.querySelector('#libraryExportList .mb-transfer-question-preview img')")
    assert "[配图]" in browser.evaluate("document.querySelector('#libraryExportList .mb-transfer-question-preview').textContent")
    _click(browser, "#libraryExportSelectAllBtn")
    browser.command("wait", "--fn", "document.getElementById('libraryExportSelectedViewBtn').textContent==='已选 (4)'")
    payload = _download_export_from_ui(browser)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["base64"]))) as package:
        records = json.loads(package.read("questions.json"))
        assert [row["content"] for row in records] == [question["content"] for question in questions]
        assert any(name.startswith("assets/") for name in package.namelist())
        assert sharing_package["image_sha256"] in {
            hashlib.sha256(package.read(name)).hexdigest() for name in package.namelist() if name.startswith("assets/")
        }
    assert {row["id"]: row["content"] for row in _source_questions(browser, source)} == {
        question["id"]: question["content"] for question in questions
    }
    _close_settings(browser)


@pytest.mark.parametrize("viewport", [(1440, 900), (375, 812)])
def test_transfer_tabs_remain_visible_and_preserve_export_selection(browser, selection_questions, viewport):
    browser.command("set", "viewport", str(viewport[0]), str(viewport[1]))
    _open_sharing(browser)
    _reset_export_selection(browser)
    _filter_export_source(browser, selection_questions["source"], 12)
    _check(browser, f'#libraryExportList input[data-question-id="{selection_questions["ids"][0]}"]')
    _click(browser, "#libraryExportNextBtn")
    browser.command("wait", "--fn", "document.getElementById('libraryExportPageLabel').textContent==='2 / 2'")

    def tab_positions():
        return browser.evaluate("""(() => {
            return ['libraryTransferExportTab','libraryTransferImportTab'].map(id=>{
                const button=document.getElementById(id),rect=button.getBoundingClientRect();
                const hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
                return {top:rect.top,bottom:rect.bottom,left:rect.left,right:rect.right,
                    visible:rect.top>=0&&rect.bottom<=innerHeight&&rect.left>=0&&rect.right<=innerWidth,
                    clickable:button.contains(hit)};
            });
        })()""")

    initial = tab_positions()
    assert all(tab["visible"] and tab["clickable"] for tab in initial), initial
    browser.evaluate("""(() => {
        const body=document.getElementById('settingsScrollableBody');body.scrollTop=body.scrollHeight;
        const list=document.getElementById('libraryExportList');list.scrollTop=list.scrollHeight;
        return true;
    })()""")
    browser.settle()
    after_scroll = tab_positions()
    assert all(tab["visible"] and tab["clickable"] for tab in after_scroll), after_scroll
    _sharing_screenshot(browser, f"sharing-export-bottom-{viewport[0]}.png")
    _open_import(browser)
    assert browser.evaluate("""(() => {
        const status=document.getElementById('libraryTransferStatus');
        return !status.textContent.includes('ZIP 分享包已生成') || status.getBoundingClientRect().height===0;
    })()"""), "A completed export must not leave its download status in the import pane"
    controls = browser.evaluate("""(() => {
        return ['libraryImportFile','libraryImportBtn'].map(id=>{
            const rect=document.getElementById(id).getBoundingClientRect();
            return {visible:rect.width>0&&rect.height>0&&rect.top>=0&&rect.bottom<=innerHeight};
        });
    })()""")
    assert all(control["visible"] for control in controls), controls
    _sharing_screenshot(browser, f"sharing-import-tab-{viewport[0]}.png")
    _open_export(browser)
    assert browser.evaluate("document.getElementById('libraryExportSource').value") == selection_questions["source"]
    assert browser.evaluate("document.getElementById('libraryExportPageLabel').textContent") == "2 / 2"
    assert browser.evaluate("document.getElementById('libraryExportSelectedViewBtn').textContent") == "已选 (1)"
    _close_settings(browser)
