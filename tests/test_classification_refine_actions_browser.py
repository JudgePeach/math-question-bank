"""Explicit refine actions in a source-only app; every model call is mocked."""
import hashlib
import json
import os
import sqlite3

import pytest

from mathbank.paths import DATABASE_PATH
from test_preview_browser import browser  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1',
                                reason='opt-in isolated browser regression')


def click(browser, selector):
    browser.command('scrollintoview', selector)
    browser.settle()
    browser.command('click', selector)


def frozen_questions(browser):
    result={}
    with sqlite3.connect((browser.root/DATABASE_PATH.name).as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        for table in ['questions','question_knowledge_points','question_curriculums']:
            rows=db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
            result[table]=hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()
    env=browser.root/'.env'
    result['environment']=env.read_bytes() if env.exists() else None
    return result


SETUP=r"""(async()=>{
  await loadCategories();await loadKnowledgePointCatalog();selectWorkspace('bank','题库管理');openNewQuestionEditor();
  document.getElementById('editContent').value='集合A、B含参数a，求交集。';
  document.getElementById('editContent').dispatchEvent(new Event('input',{bubbles:true}));
  document.getElementById('editAnswerMarkdown').value='原解逐项讨论参数a，取两个集合的共同元素。';
  document.getElementById('editReview').value='保留人工评析。';
  document.getElementById('editQType').value='single_choice';
  KnowledgePointState.hydrate([{code:'1015',name:'原人工集合点',role:'auxiliary',status:'suggested',
    evidence:'人工旧依据',reason:'人工原理由',origin:{kind:'manual'},scope:['原范围']}]);
  window.__refineBefore=KnowledgePointState.snapshot();window.__refineFields=['editContent','editAnswerMarkdown','editReview','editQType']
    .map(id=>document.getElementById(id).value);
  const book=Object.keys(categoryTree)[0],chapter=Object.keys(categoryTree[book])[0];
  window.__quickReply={status:'success',placement_valid:true,compulsory:book,chapter,
    knowledge:categoryTree[book][chapter][0]||'',question_form:'choice',version_code:window.activeCurriculumVersion,
    classification_diagnostics:{policy:{mode:'quick'}},knowledge_point_warnings:[],
    knowledge_points:[{code:'1039',name:'交集运算',role:'main',status:'confirmed',
      evidence_source:'content',evidence:'求交集',reason:'快速建议'}]};
  window.__preciseReply={...window.__quickReply,classification_diagnostics:{policy:{mode:'precise'}},
    knowledge_points:[{code:'1040',name:'含参数的交集运算问题',role:'main',status:'confirmed',
      evidence_source:'content',evidence:'含参数a，求交集',reason:'精细建议'},
      {code:'1015',name:'AI不同名称',role:'main',status:'confirmed',evidence:'不同依据',reason:'不得覆盖人工'}]};
  window.__refineRequests=[];window.__refineWrites=[];window.__refineFetch=window.fetch;
  window.fetch=(url,options={})=>{
    if(String(url).endsWith('/api/ai/classify'))return new Promise((resolve,reject)=>{
      window.__refineRequests.push({url:String(url),body:Object.fromEntries(options.body),signal:options.signal,resolve,reject});
    });
    if((String(url).startsWith('/api/questions')&&['POST','PUT','DELETE'].includes(options.method))
        ||String(url).endsWith('/api/settings/save')){
      window.__refineWrites.push(String(url));return Promise.reject(new Error('No question or setting writes'));
    }
    return window.__refineFetch(url,options);
  };
  openClassifyModal();return true;
})()"""


def quick_preview(browser, width, height):
    browser.command('reload');browser.command('set','viewport',str(width),str(height))
    browser.command('wait','--fn',"typeof window.runAIClassify==='function'")
    browser.command('network','route','**/api/ai/**','--abort')
    assert browser.evaluate(SETUP)
    click(browser,'#classifyAIButton')
    assert browser.evaluate("window.__refineRequests.length===1&&window.__refineRequests[0].body.quality===undefined")
    assert browser.evaluate("__refineRequests[0].resolve(new Response(JSON.stringify(__quickReply),{status:200,headers:{'Content-Type':'application/json'}}));true")
    browser.command('wait','--fn',"document.getElementById('classifyResultMode').textContent==='本次结果：快速'")


@pytest.mark.parametrize('width,height',[(1440,1000),(375,812)])
def test_explicit_precise_refine_keeps_quick_until_valid_reply_and_points_only_stays_unsaved(browser,width,height,tmp_path):
    before=frozen_questions(browser)
    quick_preview(browser,width,height)
    assert browser.evaluate("!document.getElementById('classifyRefineButton').disabled")
    assert browser.evaluate("document.getElementById('classifyRefineButton').getAttribute('data-tooltip').includes('全局默认不变')")
    assert browser.evaluate("document.getElementById('classifyRefineButton').getBoundingClientRect().height>=44")
    click(browser,'#classifyRefineButton')
    pending=browser.evaluate(r"""(()=>{
      refineAIClassify();runAIClassify();applyClassifyKnowledgePointsOnly();applyClassifyRecommendation();
      return {requests:__refineRequests.map(r=>r.body),points:KnowledgePointState.snapshot(),before:__refineBefore,
        mode:temporaryClassifyData.classification_diagnostics.policy.mode,busy:classifyRefineInFlight,
        previewReadonly:[...document.querySelectorAll('#classifyKnowledgePoints input, #classifyKnowledgePoints select, #choiceTypeConfirm button')]
          .every(node=>node.disabled),
        disabled:[document.getElementById('classifyApplyButton').disabled,document.getElementById('classifyPointsApplyButton').disabled]};
    })()""")
    assert len(pending['requests'])==2 and pending['requests'][1]['quality']=='precise'
    for key in ['content','answer_markdown','review_markdown','expected_version_code']:
        assert pending['requests'][0].get(key)==pending['requests'][1].get(key)
    assert pending['points']==pending['before'] and pending['mode']=='quick' and pending['busy']
    assert pending['disabled']==[True,True]
    assert pending['previewReadonly'] is True
    browser.settle()
    browser.command('screenshot',str(tmp_path/f'refine-pending-{width}.png'))
    assert browser.evaluate("__refineRequests[1].resolve(new Response(JSON.stringify(__preciseReply),{status:200,headers:{'Content-Type':'application/json'}}));true")
    browser.command('wait','--fn',"document.getElementById('classifyResultMode').textContent==='本次结果：精细'")
    assert browser.evaluate("document.getElementById('classifyRefineButton').classList.contains('hidden')")
    assert browser.evaluate("document.getElementById('classifyApplyButton').disabled&&temporaryClassifyQuestionType===null")
    assert browser.evaluate("!document.getElementById('classifyPointsApplyButton').disabled")
    assert browser.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert browser.evaluate("document.getElementById('classifyResult').scrollWidth<=document.getElementById('classifyResult').clientWidth+1")
    browser.settle()
    browser.command('screenshot',str(tmp_path/f'refine-ready-{width}.png'))
    click(browser,'#classifyPointsApplyButton')
    browser.command('wait','--fn',"!document.getElementById('aiClassifyModal').dataset.classifyOpen")
    actual=browser.evaluate(r"""(()=>({points:KnowledgePointState.snapshot(),before:__refineBefore,
      fields:['editContent','editAnswerMarkdown','editReview','editQType'].map(id=>document.getElementById(id).value),
      beforeFields:__refineFields,writes:__refineWrites,dirty:isEditorModified()}))()""")
    assert actual['points'][:1]==actual['before'] and actual['points'][1]['code']=='1040'
    assert actual['fields']==actual['beforeFields'] and actual['writes']==[] and actual['dirty'] is True
    assert frozen_questions(browser)==before
    browser.evaluate('window.fetch=window.__refineFetch;true')


def test_failure_cancel_and_source_edit_keep_original_quick_and_never_write(browser,tmp_path):
    before=frozen_questions(browser)
    quick_preview(browser,375,812)
    click(browser,'#classifyRefineButton')
    assert browser.evaluate("__refineRequests[1].resolve(new Response(JSON.stringify(__preciseReply),{status:500,headers:{'Content-Type':'application/json'}}));true")
    browser.command('wait','--fn','!classifyRefineInFlight')
    assert browser.evaluate("temporaryClassifyData.classification_diagnostics.policy.mode==='quick'&&!document.getElementById('classifyRefineButton').disabled")
    click(browser,'#classifySingleChoiceBtn')
    assert browser.evaluate(r"""(()=>{
      const input=document.querySelector('#classifyKnowledgePoints input');input.checked=false;input.dispatchEvent(new Event('change'));
      const role=document.querySelector('#classifyKnowledgePoints select');role.value='auxiliary';role.dispatchEvent(new Event('change'));
      window.__quickSelections=JSON.stringify(temporaryClassifyKnowledgePoints);
      window.__quickChapter=document.getElementById('recChapter').textContent;
      window.__refineRenderer=renderClassifyKnowledgePoints;
      window.renderClassifyKnowledgePoints=data=>{__refineRenderer(data);document.getElementById('recChapter').textContent='渲染中断';throw new Error('synthetic render failure');};
      return true;
    })()""")
    click(browser,'#classifyRefineButton')
    assert browser.evaluate("[...document.querySelectorAll('#classifyKnowledgePoints input, #classifyKnowledgePoints select, #choiceTypeConfirm button')].every(node=>node.disabled)")
    assert browser.evaluate("__refineRequests.at(-1).resolve(new Response(JSON.stringify(__preciseReply),{status:200,headers:{'Content-Type':'application/json'}}));true")
    browser.command('wait','--fn','!classifyRefineInFlight')
    assert browser.evaluate(r"""(()=>{
      window.renderClassifyKnowledgePoints=window.__refineRenderer;
      return temporaryClassifyData.classification_diagnostics.policy.mode==='quick'
        &&JSON.stringify(temporaryClassifyKnowledgePoints)===__quickSelections
        &&document.getElementById('recChapter').textContent===__quickChapter
        &&document.getElementById('classifySingleChoiceBtn').getAttribute('aria-checked')==='true'
        &&!document.querySelector('#classifyKnowledgePoints input').checked
        &&document.querySelector('#classifyKnowledgePoints select').value==='auxiliary'
        &&!document.querySelector('#classifyKnowledgePoints select').disabled;
    })()""")
    # The restored actual nodes retain their change handlers, not dead clones.
    assert browser.evaluate(r"""(()=>{
      const input=document.querySelector('#classifyKnowledgePoints input');input.checked=true;input.dispatchEvent(new Event('change'));
      const role=document.querySelector('#classifyKnowledgePoints select');role.value='main';role.dispatchEvent(new Event('change'));
      return temporaryClassifyKnowledgePoints[0].classification_selected===true
        &&temporaryClassifyKnowledgePoints[0].role==='main'&&!document.getElementById('classifyPointsApplyButton').disabled;
    })()""")
    click(browser,'#classifyRefineButton');click(browser,'#classifyRefineCancelButton')
    assert browser.evaluate("__refineRequests.at(-1).signal.aborted&&!classifyRefineInFlight&&temporaryClassifyData.classification_diagnostics.policy.mode==='quick'")
    assert browser.evaluate("__refineRequests.at(-1).resolve(new Response(JSON.stringify({status:'error'}),{status:409,headers:{'Content-Type':'application/json'}}));true")
    browser.settle()
    assert browser.evaluate("temporaryClassifyData.classification_diagnostics.policy.mode==='quick'&&isAIClassifyContextCurrent(temporaryClassifyContext)")
    click(browser,'#classifyRefineButton')
    assert browser.evaluate(r"""(()=>{
      document.getElementById('editContent').value+='人工新修改';
      document.getElementById('editContent').dispatchEvent(new Event('input',{bubbles:true}));
      __refineRequests.at(-1).resolve(new Response(JSON.stringify(__preciseReply),{status:200,headers:{'Content-Type':'application/json'}}));
      return true;
    })()""")
    browser.command('wait','--fn','!classifyRefineInFlight')
    assert browser.evaluate(r"""(()=>{
      applyClassifyKnowledgePointsOnly();applyClassifyRecommendation();
      return document.getElementById('editContent').value.endsWith('人工新修改')
        &&document.getElementById('classifyRefineButton').disabled&&__refineWrites.length===0
        &&JSON.stringify(KnowledgePointState.snapshot())===JSON.stringify(__refineBefore);
    })()""")
    assert browser.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert browser.evaluate("document.getElementById('classifyResult').scrollWidth<=document.getElementById('classifyResult').clientWidth+1")
    browser.settle()
    browser.command('screenshot',str(tmp_path/'refine-stale-mobile.png'))
    assert frozen_questions(browser)==before
    browser.evaluate('window.fetch=window.__refineFetch;true')


def test_server_409_keeps_quick_for_reference_but_disables_old_curriculum_actions(browser,tmp_path):
    before=frozen_questions(browser)
    quick_preview(browser,375,812)
    click(browser,'#classifyRefineButton')
    assert browser.evaluate("window.__versionBeforeConflict=window.activeCurriculumVersion;__refineRequests.at(-1).resolve(new Response(JSON.stringify({status:'error',message:'教材版本已变化'}),{status:409,headers:{'Content-Type':'application/json'}}));true")
    browser.command('wait','--fn','!classifyRefineInFlight')
    assert browser.evaluate(r"""(()=>{
      applyClassifyKnowledgePointsOnly();applyClassifyRecommendation();selectClassifiedChoiceType('single_choice');
      refineAIClassify();runAIClassify();
      return temporaryClassifyData.classification_diagnostics.policy.mode==='quick'
        &&window.activeCurriculumVersion===__versionBeforeConflict
        &&!isAIClassifyContextCurrent(temporaryClassifyContext)
        &&document.getElementById('classifyResultMode').textContent.includes('教材版本已变化')
        &&document.getElementById('classifyApplyButton').disabled
        &&document.getElementById('classifyPointsApplyButton').disabled
        &&document.getElementById('classifyRefineButton').disabled
        &&__refineRequests.length===2&&__refineWrites.length===0;
    })()""")
    browser.settle();browser.command('screenshot',str(tmp_path/'refine-version-conflict-mobile.png'))
    assert frozen_questions(browser)==before
    browser.evaluate('window.fetch=window.__refineFetch;true')
