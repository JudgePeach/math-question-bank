"""Knowledge classification UI against a disposable app; model replies are fake."""
import os
from pathlib import Path

import pytest

from test_preview_browser import browser  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1",
                                reason="opt-in isolated browser regression")
EVIDENCE = Path(__file__).resolve().parents[1] / ".system_generated" / "ai-knowledge-browser"


def click(browser, selector):
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)


@pytest.mark.parametrize("width,height", [(1600, 1100), (375, 812)])
def test_same_classification_request_merges_and_saves_points_without_confirmation(browser, width, height):
    browser.command("reload")
    browser.command("set", "viewport", str(width), str(height))
    browser.command("wait", "--fn", "typeof window.renderParsedQuestionsList==='function'")
    browser.command("network", "route", "**/api/ai/**", "--abort")
    question = browser.evaluate(r"""(async()=>{
      await loadCategories();await loadKnowledgePointCatalog();selectWorkspace('bank','题库管理');
      const form=new FormData();Object.entries({content:'集合A、B含元素1、2、3。求交集。 视口'+innerWidth,
        answer_markdown:'公共元素为2。',question_type:'detailed_answer',difficulty:'normal',
        category_compulsory:'必修一',category_chapter:'1. 集合与常用逻辑用语',category_knowledge:'1.1 集合的概念',
        knowledge_points:JSON.stringify([{code:'1015',role:'auxiliary',status:'suggested',origin:'historical'}])
      }).forEach(([key,value])=>form.set(key,value));
      const response=await fetch('/api/questions',{method:'POST',body:form});const body=await response.json();
      if(!response.ok)throw Error(JSON.stringify(body));await loadQuestions();return body.question;
    })()""")
    qid = question["id"]
    browser.command("wait", f'#questionsList [data-id="{qid}"]')
    browser.command("click", f'#questionsList [data-id="{qid}"]')
    browser.command("wait", "--fn", f"EditorState.questionId==={qid}&&!questionDetailLoading&&!isEditorModified()")
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="classification"]')
    assert browser.evaluate("document.querySelectorAll('#editKnowledgePointPicker .mb-kp-chip').length") or browser.evaluate(
        "document.getElementById('editKnowledgePointPicker').textContent.includes('集合')")
    assert "待确认" not in browser.evaluate("document.getElementById('editKnowledgePointPicker').textContent")
    browser.evaluate(r"""(()=>{
      window.__knowledgeOriginalFetch=window.fetch;window.__knowledgeRequests=[];
      window.fetch=(url,options={})=>{
        if(String(url).endsWith('/api/ai/classify')){
          window.__knowledgeRequests.push(Object.fromEntries(options.body));
          return Promise.resolve(new Response(JSON.stringify({status:'success',compulsory:'必修一',
            chapter:'1. 集合与常用逻辑用语',knowledge:'1.1 集合的概念',question_form:'detailed_answer',
            knowledge_points:[{code:'1015',name:'集合的含义',role:'main',status:'confirmed',origin:'ai_classification'},
              {code:'1016',name:'集合的表示方法',role:'main',status:'confirmed',origin:'ai_classification',
                evidence_source:'content',evidence:'集合A、B',reason:'识别集合的表示与元素。'}],knowledge_point_warnings:[]
          }),{status:200,headers:{'Content-Type':'application/json'}}));
        }return window.__knowledgeOriginalFetch(url,options);
      };return true;
    })()""")
    click(browser, "#editKnowledgePointPicker .mb-kp-header button:nth-of-type(2)")
    browser.command("wait", "--fn", "!document.getElementById('classifyResult').classList.contains('hidden')")
    assert browser.evaluate("window.__knowledgeRequests.length") == 1
    assert browser.evaluate("window.__knowledgeRequests[0].answer_markdown") == "公共元素为2。"
    assert browser.evaluate("document.getElementById('classifyKnowledgePoints').textContent.includes('集合的表示方法')")
    assert browser.evaluate("document.getElementById('classifyAcceptKnowledgePoints')===null")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(EVIDENCE / f"classification-{width}.png"))
    assert browser.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    click(browser, "#classifyApplyButton")
    duplicate = "document.getElementById('parsedDuplicateReviewModal')&&!document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')"
    browser.command("wait", "--fn", "(!isQuestionSaveInFlight()&&!isEditorModified())||(" + duplicate + ")")
    if browser.evaluate(duplicate):
        click(browser, "#duplicateReviewIndependentBtn")
        browser.command("wait", "--fn", "!isQuestionSaveInFlight()&&!isEditorModified()")
    saved = browser.evaluate(f"fetch('/api/questions/{qid}').then(r=>r.json())")
    points = {point["code"]: point for point in saved["knowledge_points"]}
    assert set(points) == {"1015", "1016"}
    assert (points["1015"]["role"], points["1015"]["status"]) == ("auxiliary", "suggested")
    assert (points["1016"]["role"], points["1016"]["status"]) == ("main", "confirmed")
    assert points["1016"]["origin"] == "ai_classification"
    assert browser.evaluate("window.__knowledgeRequests.length") == 1

    # The same point array survives a document card and the real save endpoint.
    browser.evaluate(r"""(()=>{
      window.fetch=window.__knowledgeOriginalFetch;closeQuestionEditorModal();selectWorkspace('import','导入中心');
      replaceParsedQuestions([{content:'集合关系导入合成回归，视口'+innerWidth,answer_markdown:'',
        question_type:'detailed_answer',difficulty:'normal',category_compulsory:'必修一',
        category_chapter:'1. 集合与常用逻辑用语',category_knowledge:'1.1 集合的概念',image_paths:[],
        knowledge_points:[{code:'1016',name:'集合的表示方法',role:'main',status:'confirmed',
          origin:'ai_classification',evidence_source:'content',evidence:'集合关系',reason:'分类依据。'}]}]);
      renderParsedQuestionsList(parsedQuestionsData);
      document.getElementById('importPlaceholder').classList.add('hidden');
      document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');return true;
    })()""")
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-knowledge-points').textContent.includes('集合的表示方法')")
    assert "待确认" not in browser.evaluate("document.querySelector('#parsed-card-0 .card-knowledge-points').textContent")
    browser.command("scrollintoview", "#parsed-card-0 .card-knowledge-points")
    browser.settle()
    browser.command("screenshot", str(EVIDENCE / f"import-card-{width}.png"))
    click(browser, "#parsed-card-0 .card-save-btn")
    browser.command("wait", "--fn", "parsedQuestionsData[0].saved===true||(" + duplicate + ")")
    if browser.evaluate(duplicate):
        click(browser, "#duplicateReviewIndependentBtn")
    browser.command("wait", "--fn", "parsedQuestionsData[0].saved===true")
    assert browser.evaluate("parsedQuestionsData[0].knowledge_points[0].code") == "1016"
    assert browser.evaluate("JSON.stringify(parsedQuestionsData[0].savedSnapshot).includes('1016')")


def test_edit_during_classification_rejects_late_points(browser):
    browser.command("reload")
    browser.command("wait", "--fn", "typeof window.runAIClassify==='function'")
    browser.evaluate("selectWorkspace('bank','题库管理');openNewQuestionEditor();true")
    browser.command("wait", "--fn", "!questionDetailLoading&&!isQuestionSaveInFlight()")
    browser.evaluate(r"""(()=>{
      document.getElementById('editContent').value='原始集合题';
      openClassifyModal();window.__lateKnowledgeOriginalFetch=window.fetch;
      window.fetch=(url,options)=>String(url).endsWith('/api/ai/classify')
        ?new Promise(resolve=>window.__resolveKnowledge=resolve):window.__lateKnowledgeOriginalFetch(url,options);
      runAIClassify();document.getElementById('editContent').value='请求期间改过的题干';
      window.__resolveKnowledge(new Response(JSON.stringify({status:'success',compulsory:'必修一',
        chapter:'1. 集合与常用逻辑用语',knowledge:'',question_form:'detailed_answer',
        knowledge_points:[{code:'1016',role:'main',status:'confirmed'}]}),{status:200}));return true;
    })()""")
    browser.command("wait", "--fn", "document.getElementById('classifyLoading').classList.contains('hidden')")
    assert browser.evaluate("KnowledgePointState.snapshot().length") == 0
    assert browser.evaluate("document.getElementById('classifyResult').classList.contains('hidden')")
    browser.evaluate("window.fetch=window.__lateKnowledgeOriginalFetch;closeClassifyModal();true")
