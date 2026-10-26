"""Real save-to-classifier hand-off in a source-only disposable app."""
import os

import pytest

from test_preview_browser import browser  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1",
                                reason="opt-in isolated browser regression")


def click(browser, selector):
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)


@pytest.mark.parametrize("width,height", [(1600, 1100), (375, 812)])
def test_missing_placement_save_hands_off_and_points_only_stays_unsaved(browser, width, height, tmp_path):
    browser.command("reload")
    browser.command("set", "viewport", str(width), str(height))
    browser.command("wait", "--fn", "typeof window.runAIClassify==='function'")
    browser.command("network", "route", "**/api/ai/**", "--abort")
    assert browser.evaluate(r"""(async()=>{
      await loadCategories();await loadKnowledgePointCatalog();selectWorkspace('bank','题库管理');
      openNewQuestionEditor();
      document.getElementById('editContent').value='集合A、B含元素1、2、3，求交集。';
      document.getElementById('editContent').dispatchEvent(new Event('input',{bubbles:true}));
      document.getElementById('editAnswerMarkdown').value='公共元素为2。';
      document.getElementById('editReview').value='本地已有评析';
      document.getElementById('editCompulsory').value='';
      document.getElementById('editCompulsory').dispatchEvent(new Event('change',{bubbles:true}));
      document.getElementById('editQType').value='single_choice';
      KnowledgePointState.hydrate([{code:'1015',name:'集合的含义',role:'auxiliary',status:'suggested',
        origin:{kind:'manual'},evidence:'已有依据',evidence_source:'content',reason:'人工理由',scope:['(1)']}]);
      window.__classifyBeforePoints=KnowledgePointState.snapshot();
      window.__classifyBeforeFields=['editCompulsory','editChapter','editKnowledge','editQType','editAnswerMarkdown','editReview']
        .map(id=>document.getElementById(id).value);
      window.__classifyRequests=[];window.__questionWrites=[];
      window.__classifyOriginalFetch=window.fetch;
      window.fetch=(url,options={})=>{
        if(String(url).endsWith('/api/ai/classify')){
          window.__classifyRequests.push({body:Object.fromEntries(options.body),saving:isQuestionSaveInFlight()});
          return Promise.resolve(new Response(JSON.stringify({status:'success',placement_valid:false,
            compulsory:'',chapter:'',knowledge:'',question_form:'choice',classification_review:{required:true},
            knowledge_points:[{code:'1015',name:'AI不同名称',role:'main',status:'confirmed',evidence:'AI不同依据'},
              {code:'1016',name:'集合的表示方法',role:'main',status:'confirmed',origin:'ai_classification',
                evidence_source:'content',evidence:'集合A、B',reason:'使用集合表示。'}],knowledge_point_warnings:[]
          }),{status:200,headers:{'Content-Type':'application/json'}}));
        }
        if(String(url).startsWith('/api/questions')&&['POST','PUT'].includes(options.method)){
          window.__questionWrites.push(String(url));return Promise.reject(new Error('Points-only must not save'));
        }
        return window.__classifyOriginalFetch(url,options);
      };
      return !document.getElementById('editCompulsory').value;
    })()""")
    click(browser, "#saveQuestionBtn")
    browser.command("wait", "#autoSaveClassifyBtn")
    assert browser.evaluate("isQuestionSaveInFlight()")
    click(browser, "#autoSaveClassifyBtn")
    browser.command("wait", "--fn", "!document.getElementById('classifyResult').classList.contains('hidden')")
    assert browser.evaluate("window.__classifyRequests.length") == 1
    assert browser.evaluate("window.__classifyRequests[0].saving") is False
    assert browser.evaluate("window.__classifyRequests[0].body.answer_markdown") == "公共元素为2。"
    assert browser.evaluate("document.getElementById('classifyApplyButton').disabled") is True
    assert browser.evaluate("document.getElementById('classifyPointsApplyButton').disabled") is False
    assert browser.evaluate("temporaryClassifyQuestionType===null")
    assert browser.evaluate("document.getElementById('choiceTypeConfirm').classList.contains('hidden')")
    assert browser.evaluate("document.getElementById('recKnowledge').textContent") == "未可靠定位"
    assert browser.evaluate("document.getElementById('classifyExistingPointDifferences').textContent.includes('已有标注全部保留')")
    assert browser.evaluate("document.getElementById('classifyPlacementNotice').textContent.includes('只应用')")
    assert browser.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert browser.evaluate("document.getElementById('classifyPointsApplyButton').getBoundingClientRect().height>=44")
    browser.command("screenshot", str(tmp_path / f"points-only-{width}.png"))
    click(browser, "#classifyPointsApplyButton")
    browser.command("wait", "--fn", "!document.getElementById('aiClassifyModal').dataset.classifyOpen")
    result = browser.evaluate(r"""(()=>({
      points:KnowledgePointState.snapshot(),before:window.__classifyBeforePoints,
      fields:['editCompulsory','editChapter','editKnowledge','editQType','editAnswerMarkdown','editReview']
        .map(id=>document.getElementById(id).value),beforeFields:window.__classifyBeforeFields,
      writes:window.__questionWrites,questionId:EditorState.questionId,dirty:isEditorModified()
    }))()""")
    assert result["points"][:1] == result["before"]
    assert result["points"][1]["code"] == "1016"
    assert result["fields"] == result["beforeFields"]
    assert result["writes"] == []
    assert result["questionId"] is None
    assert result["dirty"] is True
    browser.evaluate("window.fetch=window.__classifyOriginalFetch;true")
