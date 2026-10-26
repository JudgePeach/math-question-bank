"""Actual Chromium acceptance; fixture uses only a disposable source copy and DB."""
import json
import os

import pytest

from test_preview_browser import browser, complete_import_fixture_categories  # noqa: F401
from test_document_import_resilience_browser import click_stable, show_questions


pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1', reason='opt-in isolated browser')


def approve_fixture_duplicates_if_present(browser, success_condition):
    """The disposable DB may retain near matches from another viewport run."""
    visible = '!!document.getElementById("parsedDuplicateReviewModal") && !document.getElementById("parsedDuplicateReviewModal").classList.contains("hidden")'
    browser.command('wait', '--fn', success_condition + ' || (' + visible + ')')
    if not browser.evaluate(visible):
        return
    if browser.evaluate('document.querySelectorAll(".duplicate-keep-checkbox").length>0'):
        indices = browser.evaluate('[...document.querySelectorAll(".duplicate-keep-checkbox")].map(node=>node.dataset.questionIndex)')
        for index in indices:
            browser.command('check', f'.duplicate-keep-checkbox[data-question-index="{index}"]')
        click_stable(browser, '#duplicateReviewIndependentBtn')
    elif browser.evaluate(visible):
        click_stable(browser, '#duplicateReviewIndependentBtn')


@pytest.mark.parametrize('width', [1440, 375])
def test_batch_saves_qualified_rows_keeps_errors_and_reports_plain_text(browser, tmp_path, width):
    browser.command('set', 'viewport', str(width), '1000')
    show_questions(browser, [
        {'content': f'隔离批次可保存 {width}：计算 $8127+54$。', 'question_type': 'detailed_answer'},
        {'content': f'隔离批次失败 {width}：求 $x+2=9$ 的解。', 'question_type': 'detailed_answer'},
        {'content': f'隔离批次待分类 {width}：求函数定义域。', 'question_type': 'detailed_answer',
         'category_compulsory': '', 'category_chapter': '',
         'classification_review': {'required': True, 'reason_codes': ['chapter_not_found']}}
    ])
    for index in [0, 1]:
        complete_import_fixture_categories(browser, index)
    browser.evaluate("document.querySelector('#parsed-card-2 .card-select-checkbox').checked=true; MathBankDocumentImportSession.begin({documentType:'tex',phase:'completed'}); true")
    before = browser.evaluate("fetch('/api/stats').then(r=>r.json()).then(d=>d.total_count)")
    browser.evaluate(r"""
      window.__resilienceFetch=window.fetch;window.__questionPosts=[];
      window.fetch=(url,options={})=>{
        if(String(url)==='/api/questions'&&options.method==='POST'){
          window.__questionPosts.push(options.body.get('content'));
          if(options.body.get('content').includes('批次失败'))return Promise.resolve(new Response(JSON.stringify({detail:'模拟服务错误 <img src=x onerror=bad()>'}),{status:503,headers:{'Content-Type':'application/json'}}));
        }
        if(String(url).startsWith('/api/ai/'))return Promise.reject(Error('AI disabled in acceptance'));
        return window.__resilienceFetch(url,options);
      };true
    """)
    try:
        click_stable(browser, '#saveAllParsedBtn')
        approve_fixture_duplicates_if_present(browser, 'parsedQuestionsData[0].saved===true')
        browser.command('wait', '--fn', 'parsedQuestionsData[0].saved===true && !parsedBatchSaveInFlight')
        assert browser.evaluate('parsedQuestionsData.map(q=>q.import_outcome.status)') == ['success', 'failed', 'skipped']
        assert browser.evaluate('parsedQuestionsData.map(q=>!!q.saved)') == [True, False, False]
        assert browser.evaluate('window.__questionPosts.length') == 2
        assert browser.evaluate("document.getElementById('parsedBatchImportReport').textContent") .startswith('入库处理记录：成功 1，失败 1，跳过 1')
        assert browser.evaluate("document.querySelectorAll('#parsedBatchImportReport img').length") == 0
        after = browser.evaluate("fetch('/api/stats').then(r=>r.json()).then(d=>d.total_count)")
        assert after - before == 1
        browser.command('scrollintoview', '#parsedBatchImportReport')
        browser.command('screenshot', str(tmp_path / f'batch-partial-{width}.png'))
        assert browser.evaluate("(()=>{const r=document.getElementById('parsedBatchImportReport').getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1;})()")
        browser.command('reload')
        browser.command('wait', '--fn', 'parsedQuestionsData.length===3 && !!document.getElementById("parsedBatchImportReport")')
        assert browser.evaluate('parsedQuestionsData.map(q=>q.import_outcome.status)') == ['success', 'failed', 'skipped']
    finally:
        browser.evaluate('if(window.__resilienceFetch)window.fetch=window.__resilienceFetch;MathBankDocumentImportSession.clear();true')


@pytest.mark.parametrize('width', [1440, 375])
def test_pdf_explicit_failed_page_resume_preserves_edits_saved_card_and_same_task(browser, tmp_path, width):
    browser.command('set', 'viewport', str(width), '1000')
    machine = [
        {'content': f'补做保留人工修改 {width}：计算 $438+1$。', 'question_type': 'detailed_answer', 'pdf_source_card_id': 'source-1'},
        {'content': f'补做保留入库回执 {width}：求圆的面积。', 'question_type': 'detailed_answer', 'pdf_source_card_id': 'source-2'}
    ]
    show_questions(browser, machine)
    browser.evaluate("MathBankDocumentImportSession.clear();MathBankDocumentImportSession.begin({taskId:'partial-browser-task',documentType:'pdf',phase:'running'});true")
    for index in range(2):
        complete_import_fixture_categories(browser, index)
    click_stable(browser, '#parsed-card-1 .card-save-btn')
    approve_fixture_duplicates_if_present(browser, 'parsedQuestionsData[1].saved===true')
    browser.command('wait', '--fn', 'parsedQuestionsData[1].saved===true')
    saved_id = browser.evaluate('parsedQuestionsData[1].saved_id')
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '教师手工修改，补做不得覆盖。')
    browser.command('uncheck', '#parsed-card-0 .card-select-checkbox')
    diagnostic = {'pdf_partial_checkpoint': {'version': 1, 'task_id': 'partial-browser-task', 'failed_pages': [2],
                  'quarantined_ranges': [], 'resume_action': 'retry_failed_pages', 'preserve_edits': True}}
    completed = {'status': 'completed', 'task_id': 'partial-browser-task', 'document_type': 'pdf', 'can_resume': False,
                 'diagnostics': diagnostic, 'data': machine + [{'content': '补做新增完整题', 'question_type': '', 'difficulty': None,
                   'category_compulsory': '', 'category_chapter': '', 'classification_review': {'required': True},
                   'pdf_source_card_id': 'source-3'}]}
    browser.evaluate('MathBankDocumentImportSession.update({phase:"completed",canResume:true,diagnostics:' + json.dumps(diagnostic) + '});true')
    browser.evaluate(r"""
      window.__resilienceFetch=window.fetch;window.__resumeRequests=[];window.__checkpointQuestionPosts=[];
      window.fetch=(url,options={})=>{
        if(String(url)==='/api/questions'&&options.method==='POST')window.__checkpointQuestionPosts.push(options.body.get('content'));
        if(String(url)==='/api/tasks/partial-browser-task/resume'){
          window.__resumeRequests.push({url,method:options.method});
          return new Promise(resolve=>window.__resumeResolve=resolve);
        }
        if(String(url)==='/api/tasks/partial-browser-task/status')return Promise.resolve(new Response(JSON.stringify(COMPLETED),{headers:{'Content-Type':'application/json'}}));
        if(String(url).startsWith('/api/ai/'))return Promise.reject(Error('AI disabled in acceptance'));
        return window.__resilienceFetch(url,options);
      };true
    """.replace('COMPLETED', json.dumps(completed, ensure_ascii=False)))
    try:
        assert browser.evaluate("document.getElementById('documentImportRecoveryPanel').textContent.includes('补做失败页')")
        click_stable(browser, '#documentImportRecoveryPanel button:last-child')
        browser.evaluate('MathBankDocumentImportSession.resume();true')
        assert browser.evaluate('window.__resumeRequests') == [{'url': '/api/tasks/partial-browser-task/resume', 'method': 'POST'}]
        browser.evaluate("window.__resumeResolve(new Response(JSON.stringify({status:'success',task_id:'partial-browser-task'}),{headers:{'Content-Type':'application/json'}}));true")
        browser.command('wait', '--fn', 'parsedQuestionsData.length===3 && !activeDocumentPoll')
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '教师手工修改，补做不得覆盖。'
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-select-checkbox').checked") is False
        assert browser.evaluate('parsedQuestionsData[1].saved') is True
        assert browser.evaluate('parsedQuestionsData[1].saved_id') == saved_id
        assert browser.evaluate("document.querySelector('#parsed-card-1 .card-select-checkbox').disabled") is True
        assert browser.evaluate('new Set(parsedQuestionsData.map(q=>q.pdf_source_card_id)).size') == 3
        assert browser.evaluate('window.__resumeRequests.length') == 1
        assert browser.evaluate('MathBankDocumentImportSession.isProject()') is False
        assert browser.evaluate("!localStorage.getItem('mathbank_document_import_session_v1')") is True
        assert browser.evaluate("document.querySelector('#parsed-card-2 .card-qtype').value") == ''
        assert browser.evaluate("document.querySelector('#parsed-card-2 .card-difficulty').value") == ''
        assert browser.evaluate('window.__checkpointQuestionPosts.length') == 0
        browser.command('select', '#parsed-card-2 .card-qtype', 'detailed_answer')
        browser.command('select', '#parsed-card-2 .card-difficulty', 'normal')
        complete_import_fixture_categories(browser, 2)
        click_stable(browser, '#parsed-card-2 .card-save-btn')
        approve_fixture_duplicates_if_present(browser, 'parsedQuestionsData[2].saved===true')
        browser.command('wait', '--fn', 'parsedQuestionsData[2].saved===true')
        assert browser.evaluate('window.__checkpointQuestionPosts.length') == 1
        assert browser.evaluate('parsedQuestionsData[1].saved_id') == saved_id
        browser.command('scrollintoview', '#documentImportRecoveryPanel')
        browser.command('screenshot', str(tmp_path / f'pdf-same-task-merged-{width}.png'))
    finally:
        browser.evaluate('window.fetch=window.__resilienceFetch;beginDocumentImportTask({skipCancellation:true});MathBankDocumentImportSession.clear();true')


def test_status_network_failures_keep_polling_original_task_without_posts(browser, tmp_path):
    browser.command('set', 'viewport', '1440', '1000')
    show_questions(browser, [{'content': '断线期间保留的人工校对题', 'question_type': 'detailed_answer'}])
    browser.evaluate(r"""
      MathBankDocumentImportSession.begin({taskId:'poll-resilience-task',documentType:'docx',phase:'running'});
      window.__resilienceFetch=window.fetch;window.__nativeNow=Date.now;window.__pollClockOffset=0;
      Date.now=()=>window.__nativeNow()+window.__pollClockOffset;window.__statusRequests=[];
      window.fetch=(url,options={})=>{
        if(String(url)==='/api/tasks/poll-resilience-task/status'){
          window.__statusRequests.push({url,method:options.method||'GET'});
          return Promise.resolve(new Response(JSON.stringify(window.__statusRequests.length<=4
            ? {detail:'模拟断线'} : {status:'ocr_extraction',document_type:'docx',progress:30,log:'恢复读取同一任务'}),
            {status:window.__statusRequests.length<=4?503:200,headers:{'Content-Type':'application/json'}}));
        }
        if(String(url).startsWith('/api/ai/')||String(url).startsWith('/api/tasks/'))throw Error('Unexpected task write in acceptance');
        return window.__resilienceFetch(url,options);
      };
      document.getElementById('importLoadingState').classList.remove('hidden');
      document.getElementById('runParseBtn').disabled=true;
      const generation=beginDocumentImportTask({skipCancellation:true});pollPdfTaskStatus('poll-resilience-task',generation);true
    """)
    try:
        for count in range(1, 5):
            browser.command('wait', '--fn', f'window.__statusRequests.length>={count} && activeDocumentPoll?.pending===false')
            assert browser.evaluate('!!activeDocumentPoll')
            assert browser.evaluate("document.getElementById('runParseBtn').disabled")
            browser.evaluate('window.__pollClockOffset+=16000;true')
        browser.command('screenshot', str(tmp_path / 'poll-disconnected-reconnecting.png'))
        browser.command('wait', '--fn', 'activeDocumentPoll?.failures===0 && window.__statusRequests.length>=5')
        assert browser.evaluate('window.__statusRequests.every(r=>r.url==="/api/tasks/poll-resilience-task/status"&&r.method==="GET")')
        assert browser.evaluate('parsedQuestionsData.length') == 1
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '断线期间保留的人工校对题'
        assert browser.evaluate("document.getElementById('importLoadingText').textContent") == '恢复读取同一任务'
    finally:
        browser.evaluate('window.fetch=window.__resilienceFetch;Date.now=window.__nativeNow;beginDocumentImportTask({skipCancellation:true});MathBankDocumentImportSession.clear();true')
