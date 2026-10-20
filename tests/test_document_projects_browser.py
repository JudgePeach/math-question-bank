"""Opt-in real DOM project editing with deterministic local-only HTTP replies."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_preview_browser import browser  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.system_generated/document-projects-20261018/browser'
pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1', reason='opt-in isolated browser')


MOCK = r"""
(()=>{
 const key='mathbank_project_browser_server',oldFetch=window.__projectBrowserOriginalFetch||window.fetch;
 window.__projectBrowserOriginalFetch=oldFetch;
 const clone=value=>JSON.parse(JSON.stringify(value));
 const now=()=>Date.now()/1000;
 const save=store=>localStorage.setItem(key,JSON.stringify(store));
 const respond=(data,status=200)=>Promise.resolve(new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}}));
 window.__projectBrowserActions=[];
 window.fetch=(input,options={})=>{
  const path=new URL(String(input?.url||input),location.href).pathname,method=String(options.method||'GET').toUpperCase();
  if(path.includes('/api/ai/')||path.includes('/api/upload/'))throw Error('No model or uploads in project UI replay');
  if(path.startsWith('/api/tasks/mock-analyze'))return respond({task_id:'mock-analyze',status:'error',error:'仅验证显式工作稿分析入口',can_resume:false,data:[]});
  if(!path.startsWith('/api/document-projects'))return oldFetch(input,options);
  const store=JSON.parse(localStorage.getItem(key)||'{}');window.__projectBrowserActions.push({path,method});
  if(path==='/api/document-projects')return respond({projects:Object.values(store).map(({draft,source_pages,...metadata})=>metadata)});
  const parts=path.split('/'),id=parts[3],action=parts[4]||'',project=store[id];
  if(!project)return respond({detail:'not found'},404);
  if(action==='open'){project.last_used_at=now();project.expires_at=now()+7*86400;save(store);return respond(project);}
  if(action==='draft'){
   const body=JSON.parse(options.body);
   if(body.expected_revision!==project.revision)return respond({detail:'工程工作稿已被更新',current_revision:project.revision},409);
   project.draft=clone(body.draft);project.revision++;project.last_used_at=now();project.expires_at=now()+7*86400;
   project.question_count=project.draft.questions.length;save(store);return respond(project);
  }
  if(action==='fork'){
   const fork=clone(project),newId=id+'-copy';fork.project_id=newId;fork.revision=1;fork.title=project.title+' 副本';
   fork.draft=JSON.parse(options.body).draft;store[newId]=fork;save(store);return respond(fork);
  }
  if(action==='analyze')return respond({status:'success',task_id:'mock-analyze',project_id:id});
  if(method==='DELETE'){delete store[id];save(store);return respond({status:'success'});}
  return respond(project);
 };
 return true;
})()
"""


def _click(browser, selector):
    browser.command('scrollintoview', selector)
    browser.settle()
    browser.command('wait', '--fn', "(()=>{const el=document.querySelector(" + json.dumps(selector) +
                    "),r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return el===hit||el.contains(hit)})()")
    browser.command('click', selector)


@pytest.mark.parametrize('width,height', [(1600, 1100), (375, 812)])
def test_project_autosave_conflict_copy_restore_and_page_workflow(browser, width, height):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('network', 'route', '**/api/ai/**', '--abort')
    browser.evaluate("window.__projectBrowserViewport=" + str(width))
    browser.evaluate(r"""
    (()=>{
      window.MathBankDocumentImportSession.clear();
      const id='browser-project-'+window.__projectBrowserViewport,now=Date.now()/1000;
      const q={project_card_id:'card-a',content:'工程原题：已知 $x+1=2$，求 $x$。',answer_markdown:'原版答案 $x=1$。',
        question_type:'single_choice',difficulty:'normal',category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'',
        source_review:{required:true,blocking:false,disposition:'advisory',reasons:['保留来源比较说明。']},
        classification_review:{required:false,original:{category_compulsory:'必修二',category_chapter:'统计',category_knowledge:''}}};
      const common={revision:1,status:'completed',can_resume:false,last_used_at:now,expires_at:now+7*86400,total_bytes:51200,task_id:null};
      const store={};
      store[id]={...common,project_id:id,title:'持久校对回归 '+window.__projectBrowserViewport,question_count:1,
        draft:{questions:[q],baselineQuestions:[q],paperTitle:'持久校对回归',documentType:'pdf'},source_pages:[]};
      store[id+'-pages']={...common,project_id:id+'-pages',title:'逐页工作稿回归',question_count:0,status:'error',
        draft:{questions:[],paperTitle:'逐页工作稿'},source_pages:[{page_number:1,markdown:'原页 $a+b$ <script>按原文保留</script>'}]};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify(store));
      window.__projectBrowserId=id;selectWorkspace('import','导入中心');return true;
    })()
    """)
    browser.evaluate(MOCK)
    browser.evaluate("document.getElementById('documentProjectsPanel').open=false;true")
    _click(browser, '#documentProjectsPanel > summary')
    browser.command('wait', '--fn', "!!document.querySelector('#documentProjectsList [data-project-id=\"'+window.__projectBrowserId+'\"]')")
    browser.command('snapshot', '-i', '-s', '#documentProjectsPanel')
    assert browser.evaluate("document.getElementById('documentProjectsPanel').textContent.includes('保留 7 天')")
    project_id = browser.evaluate('window.__projectBrowserId')
    selector = f'#documentProjectsList [data-project-id="{project_id}"] button:first-child'
    _click(browser, selector)
    browser.command('wait', '--fn', "parsedQuestionsData.length===1&&document.getElementById('documentImportRecoveryPanel')?.textContent.includes('已保存到本地服务')")
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '人工校对：$x^2+1$ 保留原公式与中文。')
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__projectBrowserId].draft.questions[0].content==='人工校对：$x^2+1$ 保留原公式与中文。'")
    assert browser.evaluate("document.getElementById('documentImportRecoveryPanel').textContent.includes('已保存到本地服务')")
    browser.command('scrollintoview', '#documentImportRecoveryPanel')
    browser.settle()
    browser.command('screenshot', str(EVIDENCE / f'autosaved-{width}.png'))

    browser.evaluate(r"""(()=>{
        const original=window.fetch;
        window.fetch=(input,options={})=>{
            if(String(input?.url||input).endsWith('/api/upload/pdf-task')){
                window.__projectReusedUploadSeen=true;
                return Promise.resolve(new Response(JSON.stringify({status:'success',task_id:'old-completed-project-task',
                    project_id:window.__projectBrowserId,reused_project:true,project_revision:1}),
                    {status:200,headers:{'Content-Type':'application/json'}}));
            }
            return original(input,options);
        };
        window.currentDocxFile=null;window.currentPdfFile=new File(['local fixture'],'same-project.pdf',{type:'application/pdf'});
        runAIPaperParse();return true;
    })()""")
    browser.command('wait', '--fn', "window.__projectReusedUploadSeen&&window.MathBankDocumentImportSession.isProject()&&parsedQuestionsData[0]?.content==='人工校对：$x^2+1$ 保留原公式与中文。'")
    assert browser.evaluate('activeDocumentPoll===null')
    # Native navigation restores the browser draft; initialization only reads
    # this project's metadata and touches its seven-day expiry, never AI.
    project = browser.evaluate("JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__projectBrowserId]")
    browser.command('network', 'route', '**/api/document-projects/' + project_id, '--body', json.dumps(project, ensure_ascii=False))
    browser.command('network', 'route', '**/api/document-projects/' + project_id + '/open', '--body', json.dumps(project, ensure_ascii=False))
    browser.command('open', browser.evaluate('location.href'))
    browser.command('wait', '--fn', "typeof window.MathBankDocumentImportSession?.openProject==='function'&&parsedQuestionsData.length===1")
    browser.evaluate(MOCK)
    browser.evaluate('window.__projectBrowserId=' + json.dumps(project_id) + ";selectWorkspace('import','导入中心');true")
    browser.settle()
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '人工校对：$x^2+1$ 保留原公式与中文。'
    assert browser.evaluate("parsedQuestionsData[0].classification_review.original.category_chapter") == '统计'
    assert browser.evaluate('parsedQuestionNeedsSourceReview(0)')

    browser.evaluate(r"""(()=>{const store=JSON.parse(localStorage.getItem('mathbank_project_browser_server'));
        store[window.__projectBrowserId].revision++;localStorage.setItem('mathbank_project_browser_server',JSON.stringify(store));return true})()""")
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '409冲突时保留的本机稿 $x^3$。')
    browser.command('wait', '--fn', "document.querySelector('.document-project-save-status')?.textContent.includes('保存冲突')")
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '409冲突时保留的本机稿 $x^3$。'
    browser.command('scrollintoview', '#documentImportRecoveryPanel')
    browser.settle()
    browser.command('screenshot', str(EVIDENCE / f'conflict-{width}.png'))
    assert browser.evaluate("[...document.querySelectorAll('#documentImportRecoveryPanel button')].some(el=>el.textContent==='另存为工程副本')")
    browser.evaluate("document.querySelector('#documentImportRecoveryPanel button:last-child').id='projectBrowserFork';true")
    _click(browser, '#projectBrowserFork')
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_document_import_session_v1'))?.projectId.endsWith('-copy')")
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '409冲突时保留的本机稿 $x^3$。'
    assert browser.evaluate("JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__projectBrowserId+'-copy'].draft.questions[0].content") == '409冲突时保留的本机稿 $x^3$。'

    browser.evaluate(r"""(()=>{
        const incoming={...parsedQuestionsData[0],content:'本次新分析得到的候选题干 $x+4$。'};
        delete incoming.project_incoming_candidate;
        const merged=window.MathBankDocumentImportSession.mergeCompletion([incoming]);
        replaceParsedQuestions(merged);renderParsedQuestionsList(parsedQuestionsData);
        window.MathBankDocumentImportSession.completed({status:'completed',data:[incoming]},merged);return true;
    })()""")
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '409冲突时保留的本机稿 $x^3$。'
    assert browser.evaluate("!!document.querySelector('.document-project-candidate')&&!document.querySelector('.document-project-candidate').open")
    _click(browser, '.document-project-candidate > summary')
    assert browser.evaluate("document.querySelector('.document-project-candidate pre').textContent.includes('本次新分析得到的候选题干')")
    _click(browser, '.document-project-candidate > button')
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '本次新分析得到的候选题干 $x+4$。'
    assert browser.evaluate("document.querySelector('.document-project-candidate')===null")
    browser.evaluate('window.MathBankDocumentImportSession.openProject(' + json.dumps(project_id + '-pages') + ')')
    browser.command('wait', '--fn', "!!document.getElementById('documentProjectPageEditor')&&!document.getElementById('documentProjectPageEditor').hidden")
    _click(browser, '#documentProjectPageEditor > summary')
    assert browser.evaluate("document.querySelector('.document-project-page-edit').value.includes('<script>')")
    assert browser.evaluate("document.querySelector('#documentProjectPageEditor script')===null")
    browser.command('fill', '.document-project-page-edit', '逐页校对后的原文 $a+b=c$，不重新OCR。')
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__projectBrowserId+'-pages'].draft.page_edits?.[0].markdown==='逐页校对后的原文 $a+b=c$，不重新OCR。'")
    browser.command('snapshot', '-i', '-s', '#documentProjectPageEditor')
    _click(browser, '#documentProjectPageEditor > button')
    browser.command('wait', '--fn', "window.__projectBrowserActions.some(row=>row.path.endsWith('/analyze'))")
    browser.evaluate('stopCurrentDocumentPoll();true')
    browser.command('scrollintoview', '#documentProjectPageEditor')
    browser.settle()
    browser.command('screenshot', str(EVIDENCE / f'page-work-{width}.png'))
    actions = browser.evaluate('window.__projectBrowserActions')
    assert sum(row['path'].endswith('/analyze') for row in actions) == 1
    assert not any('/api/ai/' in row['path'] or '/api/upload/' in row['path'] for row in actions)
    (EVIDENCE / f'evidence-{width}.json').write_text(json.dumps({'viewport': [width, height], 'actions': actions,
        'browser_root': str(browser.root), 'model_calls': 0, 'project_http': 'deterministic in-browser replies'}, ensure_ascii=False, indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')


def test_real_isolated_project_http_save_and_question_receipt(browser):
    """Seed a disposable project locally, then use the real FastAPI routes."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command('set', 'viewport', '1600', '1100')
    browser.evaluate("if(window.__projectBrowserOriginalFetch)window.fetch=window.__projectBrowserOriginalFetch;window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');true")
    curriculum = browser.evaluate('categoryTree')
    seed = r"""
import json,sys
import fitz
from mathbank.paths import DOCUMENT_PROJECTS_DIR
from mathbank.document_projects import DocumentProjectStore
from mathbank.document_project_service import project_question_ids
store=DocumentProjectStore(DOCUMENT_PROJECTS_DIR)
doc=fitz.open();page=doc.new_page();page.insert_text((72,72),'Local-only project browser fixture')
source=doc.tobytes();doc.close()
meta=store.create(source,'real-project-browser.pdf',{'generate_answers':False,'pdf_verify_suspicions':False},json.load(sys.stdin))
identifier=meta['project_id']
q=project_question_ids(identifier,[{'content':'给定样本 $1,2,3$，求样本平均数。','answer_markdown':'原版答案：$2$。',
 'question_type':'detailed_answer','difficulty':'normal','category_compulsory':'必修二','category_chapter':'9. 统计',
 'category_knowledge':'','source_number':1,'image_paths':[],
 'source_review':{'required':True,'blocking':False,'disposition':'advisory','reasons':['浏览器合成来源说明。']},
 'classification_review':{'required':False,'original':{'category_compulsory':'必修二','category_chapter':'统计','category_knowledge':''}}}])[0]
draft={'questions':[q],'baselineQuestions':[q],'paperTitle':'真实本地工程接口回归','pageImages':[],'pageNumbers':[],'diagnostics':{}}
store.save_draft(identifier,draft,0)
store.sync_task(identifier,{'task_id':'real-project-browser-task','status':'completed','document_type':'pdf','project_id':identifier,'data':[q]})
print(json.dumps({'project_id':identifier}))
"""
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-B', '-c', seed], cwd=browser.root, env=env,
                            input=json.dumps(curriculum, ensure_ascii=False), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    project_id = json.loads(result.stdout.splitlines()[-1])['project_id']
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(' + json.dumps(project_id) + ')')
    browser.command('wait', '--fn', 'parsedQuestionsData.length===1')
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '真实工程校对：样本 $2,4,6$ 的平均数为多少？')
    browser.command('fill', '#parsed-card-0 .card-answer-textarea', '原版答案：$4$。')
    browser.command('wait', '--fn', "(()=>{const s=JSON.parse(localStorage.getItem('mathbank_document_import_session_v1'));return s?.questions[0]?.content==='真实工程校对：样本 $2,4,6$ 的平均数为多少？'&&s?.projectLocalDirty===false})()")
    detail = browser.evaluate("fetch('/api/document-projects/" + project_id + "').then(r=>r.json())")
    assert detail['draft']['questions'][0]['content'] == '真实工程校对：样本 $2,4,6$ 的平均数为多少？'
    assert detail['draft']['questions'][0]['answer_markdown'] == '原版答案：$4$。'
    _click(browser, '#parsed-card-0 .card-save-btn')
    browser.command('wait', '--fn', 'parsedQuestionsData[0].saved===true&&Number(parsedQuestionsData[0].saved_id)>0')
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_document_import_session_v1'))?.projectLocalDirty===false")
    saved_id = browser.evaluate('parsedQuestionsData[0].saved_id')
    detail = browser.evaluate("fetch('/api/document-projects/" + project_id + "').then(r=>r.json())")
    assert detail['draft']['questions'][0]['saved_id'] == saved_id
    assert detail['draft']['questions'][0]['saved'] is True
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(' + json.dumps(project_id) + ')')
    assert browser.evaluate('parsedQuestionsData[0].saved')
    assert browser.evaluate('parsedQuestionsData[0].saved_id') == saved_id
    browser.command('scrollintoview', '#parsed-card-0')
    browser.settle()
    browser.command('screenshot', str(EVIDENCE / 'real-http-saved-and-restored.png'))
    (EVIDENCE / 'real-http-evidence.json').write_text(json.dumps({'project_id':project_id,'saved_id':saved_id,
        'revision':detail['revision'],'browser_root':str(browser.root),'project_http':'real isolated FastAPI endpoints',
        'model_calls':0}, ensure_ascii=False, indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')


@pytest.mark.parametrize('width,height', [(1600,1100),(375,812)])
def test_progress_and_manual_zero_result_keep_save_status_stable(browser,width,height):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    browser.command('set','viewport',str(width),str(height))
    browser.command('network','route','**/api/ai/**','--abort')
    browser.evaluate('window.__manualViewport='+str(width))
    browser.evaluate(r"""(()=>{
      if(window.__projectBrowserOriginalFetch)window.fetch=window.__projectBrowserOriginalFetch;
      window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      const id='manual-progress-'+window.__manualViewport,now=Date.now()/1000;
      window.__manualProjectId=id;window.__manualPolls=0;
      const project={project_id:id,revision:1,title:'零题人工校对回归',status:'completed',task_id:null,can_resume:false,
        last_used_at:now,expires_at:now+7*86400,total_bytes:50000,source_pages:[],
        draft:{questions:[],baselineQuestions:[],paperTitle:'人工校对回归'}};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify({[id]:project}));return true;
    })()""")
    browser.evaluate(MOCK)
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(window.__manualProjectId)')
    browser.evaluate(r"""(()=>{
      const store=JSON.parse(localStorage.getItem('mathbank_project_browser_server'));
      store[window.__manualProjectId].source_pages=Array.from({length:9},(_,i)=>({page_number:i+1,markdown:'第'+(i+1)+'页识别原文 $x+1$'}));
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify(store));
      const original=window.fetch;
      window.fetch=(input,options={})=>{
        if(String(input?.url||input).includes('/api/tasks/mock-progress-manual/status')){
          const count=++window.__manualPolls,done=count>=4;
          return Promise.resolve(new Response(JSON.stringify({task_id:'mock-progress-manual',document_type:'pdf',
            project_id:window.__manualProjectId,project_revision:1,status:done?'completed':['ocr_extraction','ai_splitting','source_verification'][count-1],partial:done,data:[],
            log:done?'首次识别稿已保留，题界尚未可靠确定':'相同进度轮询 '+count,progress:done?100:40,
            diagnostics:done?{pdf_project_partial:true,pdf_local_structure:{status:'manual_review',calls:0,
              manual_reasons:['nested_table','unclosed_environment'],page_issues:[
                {page_number:2,reason_codes:['nested_table'],blocked:true},{page_number:5,reason_codes:['unclosed_environment'],blocked:true}]}}
              :{pdf_local_structure:{status:'preparing',calls:0,elapsed:count}}}),{status:200,headers:{'Content-Type':'application/json'}}));
        }
        return original(input,options);
      };
      const generation=beginDocumentImportTask({skipCancellation:true});pollPdfTaskStatus('mock-progress-manual',generation);return true;
    })()""")
    browser.command('wait','--fn','window.__manualPolls>=1')
    browser.evaluate("window.__stableManualStatus=document.querySelector('.document-project-save-status');true")
    browser.command('wait','--fn','window.__manualPolls>=3')
    assert browser.evaluate("document.querySelector('.document-project-save-status')===window.__stableManualStatus")
    assert browser.evaluate("window.__projectBrowserActions.filter(row=>row.path.endsWith('/draft')).length") == 0
    browser.command('wait','--fn',"window.__manualPolls>=4&&activeDocumentPoll===null&&document.querySelectorAll('.document-project-page-edit').length===9")
    text=browser.evaluate("document.getElementById('documentImportRecoveryPanel').textContent")
    assert '拆题尚未完成' in text and '第 2 页' in text and '嵌套' in text and '第 5 页' in text and '未闭合' in text
    assert browser.evaluate("!!document.querySelector('.document-project-manual-notice')")
    assert not browser.evaluate("document.getElementById('parsedCardsContainer').textContent.includes('AI 未能拆解出任何有效')")
    assert not browser.evaluate("/拆解成功！共分析出 0/.test(document.getElementById('importLogsConsole').textContent)")
    assert browser.evaluate("window.__projectBrowserActions.filter(row=>row.path.endsWith('/draft')).length") == 0
    browser.command('scrollintoview','#documentImportRecoveryPanel');browser.settle()
    browser.command('screenshot',str(EVIDENCE/f'manual-zero-reasons-{width}.png'))
    if not browser.evaluate("document.getElementById('documentProjectPageEditor').open"):
        _click(browser,'#documentProjectPageEditor > summary')
    browser.command('fill','.document-project-page-edit[data-page-number="1"]','教师实际校对的第1页 $x+2$。')
    browser.command('wait','--fn',"JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__manualProjectId].draft.page_edits?.[0].markdown==='教师实际校对的第1页 $x+2$。'")
    assert browser.evaluate("window.__projectBrowserActions.filter(row=>row.path.endsWith('/draft')).length") == 1
    assert browser.evaluate('activeDocumentPoll===null')
    (EVIDENCE/f'progress-manual-evidence-{width}.json').write_text(json.dumps({'viewport':[width,height],
      'status_polls':browser.evaluate('window.__manualPolls'),'put_before_edit':0,'put_after_edit':1,
      'source_pages':9,'model_calls':0,'project_http':'deterministic isolated replies'},ensure_ascii=False,indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')
