"""PDF cloud sessions keep editable cards in memory without durable project drafts."""
import json

import pytest

from test_document_import_resilience_frontend import run_node, session_harness
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


KEY = "mathbank_document_import_session_v1"


def cloud_script(pdf_script, saved=None):
    return session_harness(pdf_script, saved) + r"""
window.__pdfCloudOnly=true;
const writes=[],removals=[];
const oldSet=localStorage.setItem,oldRemove=localStorage.removeItem;
localStorage.setItem=(key,value)=>{writes.push(key);oldSet(key,value);};
localStorage.removeItem=key=>{removals.push(key);oldRemove(key);};
for(const id of ['documentProjectsPanel','documentProjectsRefresh','documentProjectsList',
 'documentImportRecoveryPanel','documentProjectPageEditor'])create(id);
const session=window.MathBankDocumentImportSession;
listeners.DOMContentLoaded();
"""


def test_pdf_session_never_writes_drafts_but_keeps_editable_cards_and_import_selection(pdf_script):
    script = cloud_script(pdf_script) + r"""
(async()=>{
 const q=verifiedQuestion();show([q]);
 session.begin({taskId:'cloud-task',documentType:'pdf',phase:'running',projectId:'ignored-project'});
 session.update({phase:'completed',projectId:'ignored-project',projectRevision:1,
  canResume:true,page_edits:[{page_number:1,markdown:'旧逐页识别稿'}]});
 session.completed({task_id:'cloud-task',status:'completed',project_id:'ignored-project',
  can_resume:true,data:[q]},[q]);
 const card=document.getElementById('parsed-card-0');
 edit(card,'.card-content-textarea','本页人工修订的题干');
 edit(card,'.card-answer-textarea','本页人工修订答案及插图 ![](/static/uploads/tmp/answer.png)');
 card.querySelector('.card-select-checkbox').checked=false;
 session.saveSoon();session.flush();listeners.pagehide();
 assert.equal(await session.saveServerNow(),true);
 assert.equal(await session.prepareQuestionImport(q,0),null,'normal question import has no project receipt');
 await new Promise(resolve=>setTimeout(resolve,750));
 assert.equal(card.querySelector('.card-content-textarea').value,'本页人工修订的题干');
 assert.ok(card.querySelector('.card-answer-textarea').value.includes('answer.png'));
 assert.equal(card.querySelector('.card-select-checkbox').checked,false);
 assert.equal(parsedQuestionsData.length,1);assert.equal(session.getProjectId(),null);
 assert.equal(session.isProject(),false);assert.equal(session.isRestored(),false);
 assert.deepEqual(writes,[]);assert.deepEqual(removals,[]);assert.equal(storage.size,0);
 assert.equal(requests.length,0,'no draft PUT or project request');
 assert.equal(document.getElementById('documentProjectsPanel').hidden,true);
 assert.equal(document.getElementById('documentImportRecoveryPanel').hidden,true);
 assert.equal(document.getElementById('documentProjectPageEditor').hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    run_node(script)


@pytest.mark.parametrize("document_type", ["pdf", None])
def test_old_pdf_archive_is_not_restored_or_deleted_and_no_project_actions_dispatch(pdf_script, document_type):
    saved = {"version": 1, "projectId": "old-project", "taskId": "old-task", "phase": "completed",
             "questions": [{"content": "旧PDF本机人工校对稿"}], "baselineQuestions": []}
    if document_type is not None:
        saved["documentType"] = document_type
    script = cloud_script(pdf_script, saved) + r"""
(async()=>{
 const key='mathbank_document_import_session_v1',archive=storage.get(key);
 assert.equal(parsedQuestionsData.length,0,'archived PDF cards are not auto restored');
 assert.equal(session.isRestored(),false);assert.equal(window.currentPdfTaskId||null,null);
 assert.equal(await session.refreshProjects(),false);
 assert.equal(await session.openProject('old-project'),false);
 assert.equal(await session.deleteProject('old-project'),false);
 assert.equal(await session.forkProject(),false);assert.equal(await session.analyzeProject(),false);
 session.clear();assert.equal(storage.get(key),archive,'clear does not delete the old PDF archive');
 show([verifiedQuestion()]);session.begin({documentType:'pdf',taskId:'new-task',phase:'running'});
 session.saveSoon();session.flush();session.clear();
 await new Promise(resolve=>setTimeout(resolve,750));
 assert.equal(storage.get(key),archive);assert.deepEqual(writes,[]);assert.deepEqual(removals,[]);
 assert.equal(requests.length,0,'no old PDF status restore or engineering endpoint');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    run_node(script)


@pytest.mark.parametrize("document_type", ["docx", "tex"])
def test_other_document_corrections_still_persist_with_pdf_cloud_flag(pdf_script, document_type):
    script = cloud_script(pdf_script) + r"""
(async()=>{
 const q=verifiedQuestion();show([q]);
 session.begin({documentType:TYPE,phase:'completed'});
 const card=document.getElementById('parsed-card-0');
 edit(card,'.card-content-textarea','其他文档人工校对');
 session.saveSoon();session.flush();
 const saved=JSON.parse(storage.get('mathbank_document_import_session_v1'));
 assert.equal(saved.documentType,TYPE);assert.equal(saved.questions[0].content,'其他文档人工校对');
 assert.ok(writes.length>0,'Word and TeX correction persistence stays enabled');
 assert.equal(requests.length,0);session.clear();
 assert.equal(storage.has('mathbank_document_import_session_v1'),false);
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace("TYPE", json.dumps(document_type))
    run_node(script)


def test_word_archive_still_restores_with_pdf_cloud_flag(pdf_script):
    saved = {"version": 1, "documentType": "docx", "phase": "completed",
             "questions": [{"content": "Word原有人工稿", "answer_markdown": "Word答案"}],
             "baselineQuestions": []}
    script = cloud_script(pdf_script, saved) + r"""
assert.equal(parsedQuestionsData.length,1);
assert.equal(parsedQuestionsData[0].content,'Word原有人工稿');
assert.equal(session.isRestored(),true);assert.equal(requests.length,0);
session.clear();
"""
    run_node(script)
