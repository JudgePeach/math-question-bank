"""Real editor/import checks in a source-only disposable application."""

import json
import subprocess
import sys

from PIL import Image

from test_preview_browser import browser, complete_import_fixture_categories, pytestmark  # noqa: F401


def _image(browser, name, temporary=False):
    relative = ("tmp/" if temporary else "") + name + ".png"
    path = browser.root / "static" / "uploads" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), "cornflowerblue").save(path)
    return path, "/static/uploads/" + relative


def _draft(browser, content):
    browser.evaluate("""(() => {
        selectWorkspace('bank', '题库管理');
        const comp = Object.keys(categoryTree)[0], chapter = Object.keys(categoryTree[comp])[0];
        selectDraft({id:'image-browser-draft', content:%s, answer_markdown:'', question_type:'detailed_answer',
            category_compulsory:comp,category_chapter:chapter,category_knowledge:'',difficulty:'medium',
            image_paths:[],content_tikz_assets:[],answer_tikz_assets:[],isDraft:true});
        openQuestionEditorModal('content');
        return true;
    })()""" % json.dumps(content))
    browser.command("wait", "--fn", "!questionDetailLoading && !document.getElementById('editorSection').classList.contains('hidden')")


def test_cut_restore_draft_reload_and_recover_original_image(browser):
    path, url = _image(browser, "draft-undo-browser")
    content = f"撤销图片回归：已知示意图。\n![图]({url})"
    _draft(browser, content)
    browser.command("fill", "#editContent", "剪切后的临时文字")
    browser.command("fill", "#editContent", content)
    state = browser.evaluate("""(() => {
        saveCurrentToDrafts();
        const draft = getLocalStorageDrafts().find(item=>item.id === EditorState.draftId);
        return {paths: draft.image_paths, content: draft.content, dirty: isEditorModified()};
    })()""")
    assert state == {"paths": [url], "content": content, "dirty": False}
    # Use another interpreter to exercise the durable recovery metadata. No
    # application import, real data, or shared runtime state is involved.
    subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from mathbank.asset_lifecycle import register_asset_store, quarantine_asset
root=Path.cwd()
register_asset_store(root/'static/uploads', root/'.system_generated/retained_uploads/uploads')
assert quarantine_asset(sys.argv[1], uploads_dir=root/'static/uploads',url_prefix='static/uploads',reason='browser-draft-regression')
""", url], cwd=browser.root, check=True, capture_output=True, text=True)
    assert not path.exists()
    browser.command("reload")
    browser.command("wait", "--fn", "typeof selectDraft === 'function'")
    browser.evaluate("selectWorkspace('bank','题库管理'); selectDraft(getLocalStorageDrafts().find(d=>d.id==='image-browser-draft')); openQuestionEditorModal('content'); true")
    browser.command("wait", "--fn", "[...document.querySelectorAll('#contentPreview img')].some(i=>i.complete && i.naturalWidth===40)")
    assert path.is_file()
    assert browser.evaluate("document.getElementById('editContent').value") == content
    assert browser.evaluate("saveQuestion(true)") is True
    stored = browser.evaluate("fetch('/api/questions/'+EditorState.questionId).then(r=>r.json())")
    assert stored["image_paths"] == [url]


def test_promoted_draft_preserves_edits_typed_while_saving(browser):
    path, url = _image(browser, "draft-inflight-browser", temporary=True)
    content = f"暂存图入库回归：求 $23+11$。\n![图]({url})"
    _draft(browser, content)
    browser.evaluate("""(() => {
        window.__imageAuditFetch = window.fetch;
        window.fetch = async (input, options) => {
            const response = await window.__imageAuditFetch(input, options);
            if (String(input)==='/api/questions' && options?.method==='POST') {
                window.__imageAuditResponseReady = true;
                await new Promise(resolve=>{window.__imageAuditRelease=resolve;});
            }
            return response;
        };
        window.__imageAuditSaving=saveQuestion(true);
        return true;
    })()""")
    try:
        browser.command("wait", "--fn", "window.__imageAuditResponseReady === true")
        browser.command("fill", "#editContent", content + "\n保存期间补充说明。")
        assert browser.evaluate("__imageAuditRelease(); __imageAuditSaving") is True
    finally:
        browser.evaluate("if(window.__imageAuditRelease)__imageAuditRelease(); window.fetch=window.__imageAuditFetch; true")
    permanent = url.replace("/tmp/", "/")
    state = browser.evaluate("({text:document.getElementById('editContent').value,dirty:isEditorModified(),paths:uploadedImages})")
    assert state["text"] == content.replace(url, permanent) + "\n保存期间补充说明。"
    assert state["dirty"] is True
    assert state["paths"] == [permanent]
    assert path.is_file() and (path.parent.parent / path.name).is_file()
    stored = browser.evaluate("fetch('/api/questions/'+EditorState.questionId).then(r=>r.json())")
    assert [line.rstrip() for line in stored["content"].splitlines()] == content.replace(url, permanent).splitlines()
    assert browser.evaluate("saveQuestion(true)") is True
    assert browser.evaluate("isEditorModified()") is False


def test_import_shared_image_keeps_both_cards_and_saved_previews(browser):
    path, url = _image(browser, "shared-import-browser", temporary=True)
    browser.evaluate("""(() => {
        closeQuestionEditorModal(); selectWorkspace('import','导入中心');
        replaceParsedQuestions([31,47].map(n=>({content:'共享插图回归：计算 $'+n+'+3$。\\n![图]('+%s+')',
            question_type:'detailed_answer',difficulty:'medium',answer_markdown:'',image_paths:[%s]})));
        renderParsedQuestionsList(parsedQuestionsData);
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        return true;
    })()""" % (json.dumps(url), json.dumps(url)))
    for index in (0, 1):
        complete_import_fixture_categories(browser, index)
        browser.evaluate(f"window.__imageImportResult=null; saveParsedQuestion({index}).then(r=>window.__imageImportResult=r); true")
        browser.command("wait", "--fn", "window.__imageImportResult !== null || !document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')")
        if browser.evaluate("!document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')"):
            browser.command("click", "#duplicateReviewIndependentBtn")
        browser.command("wait", "--fn", "window.__imageImportResult !== null")
        assert browser.evaluate("window.__imageImportResult") is True
    permanent = url.replace("/tmp/", "/")
    result = browser.evaluate("parsedQuestionsData.map(q=>({saved:q.saved,content:q.content,paths:q.image_paths}))")
    assert all(item["saved"] and permanent in item["content"] and item["paths"] == [permanent] for item in result)
    browser.evaluate("renderParsedQuestionsList(parsedQuestionsData); true")
    browser.command("wait", "--fn", "[...document.querySelectorAll('#parsedQuestionsWrapper img')].filter(i=>i.complete && i.naturalWidth===40).length >= 2")
    assert path.is_file()


def test_import_new_edits_during_save_remain_pending(browser):
    _, url = _image(browser, "pending-import-browser", temporary=True)
    browser.evaluate("""(() => {
        selectWorkspace('import','导入中心');
        replaceParsedQuestions([{content:'正在保存的导入题：求三角形面积。\\n![图]('+%s+')',
            question_type:'detailed_answer',difficulty:'medium',answer_markdown:'',image_paths:[%s]}]);
        renderParsedQuestionsList(parsedQuestionsData);
        window.__pendingImportOriginalFetch=window.fetch;
        window.__pendingImportReady=false;
        window.fetch=async(input,options)=>{
            const response=await __pendingImportOriginalFetch(input,options);
            if(String(input)==='/api/questions' && options?.method==='POST'){
                window.__pendingImportReady=true;
                await new Promise(resolve=>window.__pendingImportRelease=resolve);
            }
            return response;
        };
        return true;
    })()""" % (json.dumps(url), json.dumps(url)))
    complete_import_fixture_categories(browser, 0)
    browser.evaluate("window.__pendingImportResult=null; saveParsedQuestion(0).then(r=>window.__pendingImportResult=r); true")
    try:
        browser.command("wait", "--fn", "window.__pendingImportReady || !document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')")
        if browser.evaluate("!document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')"):
            browser.command("click", "#duplicateReviewIndependentBtn")
        browser.command("wait", "--fn", "window.__pendingImportReady")
        browser.command("fill", "#parsed-card-0 .card-answer-textarea", "请求期间补充的解答。")
        browser.evaluate("__pendingImportRelease(); true")
        browser.command("wait", "--fn", "window.__pendingImportResult !== null")
    finally:
        browser.evaluate("if(window.__pendingImportRelease)__pendingImportRelease(); window.fetch=window.__pendingImportOriginalFetch; true")
    state = browser.evaluate("""({saved:parsedQuestionsData[0].saved,
        answer:document.querySelector('#parsed-card-0 .card-answer-textarea').value,
        label:document.querySelector('#parsed-card-0 .card-status-badge').textContent,
        disabled:document.querySelector('#parsed-card-0 .card-select-checkbox').disabled})""")
    assert state["saved"] is False and state["disabled"] is False
    assert state["answer"] == "请求期间补充的解答。"
    assert "待导入" in state["label"]
