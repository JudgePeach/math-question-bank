"""Startup, bounded association search and old drafts in the actual browser."""

import json
import os

import pytest

from test_preview_browser import browser


pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1",
    reason="opt-in isolated large-library browser regression",
)


def test_startup_and_question_switches_never_download_the_full_bank(browser):
    ids = browser.evaluate(r"""(async () => {
      const ids=[];
      for(let index=1;index<=45;index++){
        const body=new FormData();
        body.set('content',`大题库专项第 ${index} 题：计算 $x+1$。`);
        body.set('question_type','detailed_answer');body.set('difficulty','normal');
        const response=await fetch('/api/questions',{method:'POST',body});
        const data=await response.json();if(!response.ok)throw new Error(JSON.stringify(data));
        ids.push(data.question.id);
      }
      return ids;
    })()""")
    browser.scale_ids = ids
    url = browser.evaluate("location.origin")
    browser.command("open", url)
    browser.command("wait", "--fn", (
        "typeof EditorState !== 'undefined' && EditorState.questionId && !questionDetailLoading"
        " && document.getElementById('apiStatusIndicator').textContent.includes('题库就绪')"
    ))
    requests = browser.evaluate(r"""performance.getEntriesByType('resource').map(entry=>{
      const url=new URL(entry.name);return {path:url.pathname,query:url.search};
    }).filter(entry=>entry.path==='/healthz'||entry.path.startsWith('/api/questions'))""")
    assert any(entry["path"] == "/healthz" for entry in requests)
    assert not any(entry["path"] == "/api/questions" and not entry["query"] for entry in requests)
    assert not any(entry["path"].endswith("association-candidates") for entry in requests)
    assert browser.evaluate("document.getElementById('editRelatedQuestion').options.length") == 1
    browser.evaluate(r"""(() => {
      window.scaleRequests=[];const original=window.fetch;
      window.fetch=function(url,...args){scaleRequests.push(String(url));return original.call(this,url,...args)};
      selectWorkspace('bank','题库研讨工作台');return true;
    })()""")
    browser.evaluate(f"selectQuestion({{id:{ids[0]}}});selectQuestion({{id:{ids[1]}}});selectQuestion({{id:{ids[2]}}});true")
    browser.command("wait", "--fn", f"EditorState.questionId === {ids[2]} && !questionDetailLoading")
    assert browser.evaluate("document.getElementById('editContent').value") == "大题库专项第 3 题：计算 $x+1$。"
    assert not browser.evaluate("scaleRequests.some(url=>url==='/api/questions'||url.includes('association-candidates'))")


def test_association_pages_preserve_selection_and_number_lookup_is_exact(browser):
    ids = browser.scale_ids
    browser.evaluate(f"selectQuestion({{id:{ids[0]}}});true")
    browser.command("wait", "--fn", f"EditorState.questionId === {ids[0]} && !questionDetailLoading")
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="answer"]')
    browser.evaluate("document.getElementById('relatedQuestionSearchPanel').open=true;true")
    browser.command("wait", "--fn", "relatedCandidatePage === 1 && relatedCandidateItems.length === 20")
    browser.command("select", "#editRelatedQuestion", str(ids[-1]))
    browser.command("scrollintoview", "#relatedQuestionNextPage")
    browser.settle()
    browser.command("click", "#relatedQuestionNextPage")
    state = browser.evaluate(r"""(async()=>{
      const deadline=performance.now()+6000;
      while(relatedCandidatePage!==2&&performance.now()<deadline)await new Promise(resolve=>setTimeout(resolve,30));
      return {page:relatedCandidatePage,pages:relatedCandidatePages,
        status:document.getElementById('relatedQuestionPageStatus').textContent,
        panel:document.getElementById('relatedQuestionSearchPanel').open,
        nextDisabled:document.getElementById('relatedQuestionNextPage').disabled,
        requests:scaleRequests.slice(-12)};
    })()""")
    assert state["page"] == 2, state
    assert browser.evaluate("document.getElementById('editRelatedQuestion').value") == str(ids[-1])
    assert browser.evaluate("document.getElementById('editRelatedQuestion').options.length") <= 22
    browser.command("fill", "#editRelatedQuestionNum", "2")
    browser.command("wait", "--fn", f"document.getElementById('editRelatedQuestion').value === '{ids[1]}'")
    assert browser.evaluate("document.getElementById('editRelatedQuestionNum').value") == "2"


def test_old_draft_target_and_local_storage_survive_picker_hydration(browser):
    target = browser.scale_ids[-1]
    draft = {"id": "legacy-scale-draft", "content": "旧版草稿 $x=1$。", "related_question_id": str(target)}
    browser.evaluate(f"""(() => {{
      const drafts=[{json.dumps(draft,ensure_ascii=False)}];
      localStorage.setItem('mathbank_local_drafts',JSON.stringify(drafts));
      window.scaleDraftBefore=localStorage.getItem('mathbank_local_drafts');
      selectDraft(drafts[0]);return true;
    }})()""")
    browser.command("wait", "--fn", f"document.getElementById('editRelatedQuestion').value === '{target}' && document.getElementById('editRelatedQuestionNum').value === '45'")
    assert not browser.evaluate("isEditorModified()")
    assert browser.evaluate("localStorage.getItem('mathbank_local_drafts') === scaleDraftBefore")
    assert browser.evaluate("document.getElementById('editContent').value") == draft["content"]
