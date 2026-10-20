"""Actual offline DOM replay for partial parallel results and cached reopening."""
import json
import os
from pathlib import Path

import pytest

from test_preview_browser import browser  # noqa: F401
from test_document_projects_browser import MOCK, _click

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'.system_generated/pdf-legacy-cache-20261018/browser'
pytestmark=pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER')!='1',reason='opt-in isolated browser')


@pytest.mark.parametrize('width,height',[(1600,1100),(375,812)])
def test_parallel_partial_nineteen_results_keep_teacher_edit_and_cache_reopen_is_read_only(browser,width,height):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    browser.command('set','viewport',str(width),str(height))
    browser.command('network','route','**/api/ai/**','--abort')
    browser.evaluate('window.__resultWidth='+str(width))
    browser.evaluate(r"""(()=>{
      window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      const make=n=>({project_card_id:'number-'+n,source_number:n,content:'原第'+n+'题：求 $x+'+n+'$。',answer_markdown:'原版答案。',
        question_type:'detailed_answer',difficulty:'normal',category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'',image_paths:[],
        source_review:{required:true,blocking:false,disposition:'advisory',reasons:['原文对照保留。']}});
      window.__makeResult=make;window.__resultProject='parallel-result-'+window.__resultWidth;
      const old=[17,18,19].map(make),now=Date.now()/1000;
      const project={project_id:window.__resultProject,revision:1,status:'completed',partial:true,can_resume:false,task_id:null,
        title:'新旧拆卷结果回放',last_used_at:now,expires_at:now+7*86400,total_bytes:10000,source_pages:[],
        draft:{questions:old,baselineQuestions:old,paperTitle:'新旧拆卷结果回放'}};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify({[project.project_id]:project}));return true;
    })()""")
    browser.evaluate(MOCK)
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(window.__resultProject)')
    browser.command('fill','#parsed-card-1 .card-content-textarea','教师保留的第18题人工稿 $x^2$。')
    browser.command('wait','--fn',"JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__resultProject].draft.questions[1].content==='教师保留的第18题人工稿 $x^2$。'")
    report=browser.evaluate(r"""(()=>{
      const groups=[{id:'g1',source_numbers:[1,2,3,4,5,6,7],status:'pending'},
        {id:'g2',source_numbers:[8,9,10,11,12,13],status:'pending'},{id:'g3',source_numbers:[14,15,16,17,18,19],status:'pending'}];
      window.__resultOrder=[];
      for(const index of [2,0,1]){
        groups[index].status='completed';window.__resultOrder.push(index+1);
        window.MathBankDocumentImportSession.update({phase:'ai_splitting',diagnostics:{
          pdf_project_legacy_parse:{mode:'legacy_executor_cache',status:'executing',reused:false,new_calls:window.__resultOrder.length,executor_calls:1},pdf_parallel_split:{schema:'mathbank.pdf-parallel-split.v1',status:'running',used_parallel:true,
          calls:window.__resultOrder.length,group_count:3,completed_groups:window.__resultOrder.length,reused_groups:0,groups}}});
      }
      const incoming=Array.from({length:19},(_,i)=>({...window.__makeResult(i+1),content:'重新拆出的第'+(i+1)+'题 $x+'+(i+1)+'$。'}));
      const diag={pdf_project_legacy_parse:{mode:'legacy_executor_cache',status:'partial',reused:false,new_calls:3,executor_calls:1},
        pdf_parallel_split:{schema:'mathbank.pdf-parallel-split.v1',status:'used',used_parallel:true,calls:3,group_count:3,completed_groups:3,reused_groups:0,groups},
        pdf_local_structure:{status:'manual_review',manual_reasons:['nested_table']},pdf_project_partial:true};
      const review=pdfStructureReviewState(diag,{partial:true});
      const merged=window.MathBankDocumentImportSession.mergeCompletion(incoming);
      replaceParsedQuestions(merged);window.MathBankDocumentImportSession.completed({status:'completed',partial:true,diagnostics:diag,data:incoming},merged);
      renderParsedQuestionsList(parsedQuestionsData,{diagnostics:diag,partial:true});renderSourceIntegrityReport(diag);
      window.__resultIncoming=incoming;window.__resultDiagnostic=diag;
      return {count:merged.length,numbers:merged.map(q=>q.source_number),manual:review.manual};
    })()""")
    assert report['count']==19 and report['numbers']==list(range(1,20)) and report['manual'] is False
    assert browser.evaluate("parsedQuestionsData[17].content")=='教师保留的第18题人工稿 $x^2$。'
    assert browser.evaluate('parsedQuestionsData[17].project_incoming_candidate.source_number')==18
    assert browser.evaluate("document.querySelectorAll('#parsedCardsContainer > .glass-card').length")==19
    assert not browser.evaluate("document.querySelector('.document-project-manual-reasons')?.textContent.includes('嵌套')||false")
    browser.command('wait','--fn',"JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__resultProject].draft.questions.length===19")
    browser.evaluate(r"""(()=>{
      const store=JSON.parse(localStorage.getItem('mathbank_project_browser_server')),p=store[window.__resultProject];
      p.partial=true;p.latest_result=window.__resultIncoming;p.latest_result_id='cached-full-results';
      p.diagnostics={...window.__resultDiagnostic,pdf_project_legacy_parse:{mode:'legacy_executor_cache',status:'partial_manual_review',reused:true,new_calls:0,executor_calls:0},
        pdf_parallel_split:{...window.__resultDiagnostic.pdf_parallel_split,calls:0,reused_groups:3,
          groups:window.__resultDiagnostic.pdf_parallel_split.groups.map(g=>({...g,status:'reused'}))}};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify(store));window.__projectBrowserActions=[];return true;
    })()""")
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(window.__resultProject)')
    assert browser.evaluate('parsedQuestionsData.length')==19
    assert browser.evaluate('parsedQuestionsData[17].content')=='教师保留的第18题人工稿 $x^2$。'
    assert browser.evaluate("document.querySelector('.document-project-parse-summary').textContent.includes('本次新增拆题请求 0 次')")
    assert not browser.evaluate("window.__projectBrowserActions.some(row=>/\\/(?:analyze|resume)$/.test(row.path))")
    browser.command('scrollintoview','#documentImportRecoveryPanel');browser.settle()
    browser.command('screenshot',str(EVIDENCE/f'cached-parallel-nineteen-{width}.png'))
    (EVIDENCE/f'evidence-{width}.json').write_text(json.dumps({'viewport':[width,height],'source_numbers':report['numbers'],
      'completion_order':[3,1,2],'teacher_edit_preserved':True,'model_calls':0,'cache_new_calls':0,
      'project_http':'deterministic browser replies'},ensure_ascii=False,indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')
