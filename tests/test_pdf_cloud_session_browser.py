"""No durable PDF corrections in the real source-only cloud-mode UI."""
import os

import pytest

from test_preview_browser import browser  # noqa: F401

pytestmark=pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER')!='1',reason='opt-in isolated browser')


def test_pdf_cloud_edits_stay_in_current_page_and_old_pdf_archive_is_not_restored(browser):
    browser.command('network','route','**/api/ai/**','--abort')
    assert browser.evaluate('window.__pdfCloudOnly===true')
    browser.evaluate(r"""(()=>{
        const q={content:'旧PDF归档，不能自动恢复。',answer_markdown:'旧原答',question_type:'detailed_answer',difficulty:'normal'};
        const key='mathbank_document_import_session_v1';
        window.__oldCloudArchive=JSON.stringify({version:1,documentType:'pdf',questions:[q],phase:'completed'});
        localStorage.setItem(key,window.__oldCloudArchive);return true;
    })()""")
    browser.command('open',browser.evaluate('location.href'))
    browser.command('wait','--fn',"typeof window.MathBankDocumentImportSession?.begin==='function'")
    assert browser.evaluate('parsedQuestionsData.length===0')
    assert browser.evaluate("JSON.parse(localStorage.getItem('mathbank_document_import_session_v1')).questions[0].content==='旧PDF归档，不能自动恢复。'")
    browser.evaluate(r"""(()=>{
        selectWorkspace('import','导入中心');window.__cloudWrites=[];window.__cloudRequests=[];
        window.__oldCloudArchive=localStorage.getItem('mathbank_document_import_session_v1');
        const original=Storage.prototype.setItem;
        Storage.prototype.setItem=function(key,value){if(key.startsWith('mathbank_document_import_session_v1'))window.__cloudWrites.push(key);return original.call(this,key,value)};
        const fetchOriginal=window.fetch;
        window.fetch=(input,options={})=>{const url=String(input?.url||input);
            if(url.includes('/api/document-projects'))window.__cloudRequests.push(url);
            return fetchOriginal(input,options)};
        const q={content:'API完整题干：计算 $1+2$。',answer_markdown:'API原答案：$3$。',question_type:'detailed_answer',difficulty:'normal',image_paths:[]};
        MathBankDocumentImportSession.begin({documentType:'pdf',phase:'ai_splitting',taskId:'cloud-page-demo'});
        replaceParsedQuestions([q]);renderParsedQuestionsList(parsedQuestionsData);
        MathBankDocumentImportSession.completed({task_id:'cloud-page-demo',status:'completed',document_type:'pdf',data:[q]},[q]);
        document.getElementById('importPlaceholder').classList.add('hidden');document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        return true;
    })()""")
    browser.command('fill','#parsed-card-0 .card-content-textarea','当前页校对后的题干 $x=3$。')
    browser.evaluate('new Promise(resolve=>setTimeout(()=>{MathBankDocumentImportSession.flush();resolve(true)},800))')
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value==='当前页校对后的题干 $x=3$。'")
    assert browser.evaluate('window.__cloudWrites.length===0&&window.__cloudRequests.length===0')
    assert browser.evaluate("localStorage.getItem('mathbank_document_import_session_v1')===window.__oldCloudArchive")
    assert browser.evaluate("(!document.getElementById('documentProjectsPanel')||document.getElementById('documentProjectsPanel').hidden)&&(!document.getElementById('documentImportRecoveryPanel')||document.getElementById('documentImportRecoveryPanel').hidden)")
    browser.command('open',browser.evaluate('location.href'))
    browser.command('wait','--fn',"typeof window.MathBankDocumentImportSession?.begin==='function'")
    assert browser.evaluate('parsedQuestionsData.length===0')
