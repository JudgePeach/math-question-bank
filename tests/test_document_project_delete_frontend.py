"""Exercise project deletion interleavings with deferred HTTP responses, without services."""

import json

import pytest

from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


SCENARIOS = r"""
const rowFor=id=>document.getElementById('documentProjectsList').children.find(row=>row.dataset.projectId===id);
async function listProjects(ids=['project-a']){
 const listing=session.refreshProjects();
 next('/api/document-projects',{projects:ids.map(id=>({project_id:id,title:id,question_count:1,status:'completed'}))});
 await listing;
}
const deleteScenarios={
 async waits_for_inflight_save_and_removes_before_list_refresh(){
  await listProjects();const card=await open();editProject(card,'等待中的人工稿');
  const saving=session.saveServerNow();assert.equal(requests.length,1);
  const deleting=session.deleteProject('project-a');await flush();
  assert.equal(requests.length,1,'DELETE waits for the existing PUT to release its project lease');
  const row=rowFor('project-a');assert.ok(row);assert.equal(row.children[2].children[1].textContent,'正在删除…');
  assert.equal(row.children[2].children[0].disabled,true);assert.equal(row.children[2].children[1].disabled,true);
  await session.deleteProject('project-a');assert.equal(requests.length,1,'duplicate deletion is ignored');
  next('/api/document-projects/project-a/draft',{revision:5});await saving;await flush();
  const deletion=next('/api/document-projects/project-a',{});assert.equal(deletion.options.method,'DELETE');
  await deleting;
  assert.equal(rowFor('project-a'),undefined,'confirmed deletion hides the row before a list GET completes');
  assert.equal(parsedQuestionsData.length,0);
  assert.equal(storage.has('mathbank_document_import_session_v1:project-a'),false);
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/document-projects');
  next('/api/document-projects',{detail:'list refresh unavailable'},503);await flush();
  assert.equal(rowFor('project-a'),undefined);assert.ok(!toasts.some(text=>text.includes('删除工程失败')));
  await new Promise(resolve=>setTimeout(resolve,550));assert.equal(requests.length,0,'clear after deletion must not write another draft');
 },
 async pauses_debounced_save_and_restores_it_after_delete_failure(){
  await listProjects();const card=await open();editProject(card,'删除失败仍要保存的人工稿');
  const deleting=session.deleteProject('project-a');await flush();
  assert.equal(requests.length,1);assert.equal(requests[0].options.method,'DELETE');
  await new Promise(resolve=>setTimeout(resolve,550));assert.equal(requests.length,1,'no autosave starts during deletion');
  next('/api/document-projects/project-a',{detail:'工程仍有活跃解析任务，不能删除。'},409);await deleting;
  assert.ok(rowFor('project-a'));assert.equal(rowFor('project-a').children[2].children[1].disabled,false);
  assert.equal(card.querySelector('.card-content-textarea').value,'删除失败仍要保存的人工稿');
  assert.equal(stored().questions[0].content,'删除失败仍要保存的人工稿');
  await new Promise(resolve=>setTimeout(resolve,550));
  const save=next('/api/document-projects/project-a/draft',{revision:5});
  assert.equal(save.options.method,'PUT');assert.equal(JSON.parse(save.options.body).draft.questions[0].content,'删除失败仍要保存的人工稿');
  await flush();assert.equal(requests.length,0);
 },
 async old_list_response_cannot_resurrect_confirmed_deletion(){
  await listProjects();const listing=session.refreshProjects();
  const oldList=requests.shift();assert.equal(oldList.url,'/api/document-projects');
  const deleting=session.deleteProject('project-a');await flush();next('/api/document-projects/project-a',{});await deleting;
  assert.equal(rowFor('project-a'),undefined);assert.equal(requests.length,0,'an in-flight list refresh is reused');
  oldList.resolve(good({projects:[{project_id:'project-a',title:'迟到的旧条目'}]}));await listing;
  assert.equal(rowFor('project-a'),undefined,'late metadata is filtered by the confirmed deletion receipt');
  assert.ok(document.getElementById('documentProjectsList').children[0].textContent.includes('暂无本地'));
 },
 async deleting_old_project_does_not_clear_newly_opened_project(){
  await listProjects(['project-a','project-b']);await open();
  const deleting=session.deleteProject('project-a');await flush();
  const deletion=requests.shift();assert.equal(deletion.options.method,'DELETE');
  const opening=session.openProject('project-b');await flush();next('/api/document-projects/project-b/open',details('project-b'));
  assert.equal(await opening,true);assert.equal(session.getProjectId(),'project-b');
  deletion.resolve(good({}));await deleting;
  assert.equal(session.getProjectId(),'project-b');assert.equal(parsedQuestionsData[0].content,'工程原题');
  assert.equal(stored().projectId,'project-b');assert.ok(storage.has('mathbank_document_import_session_v1:project-b'));
  next('/api/document-projects',{projects:[{project_id:'project-b',title:'新工程'}]});await flush();
  assert.equal(rowFor('project-a'),undefined);assert.ok(rowFor('project-b'));session.clear();
 },
 async waits_for_pending_open_and_never_adopts_deleting_project(){
  await listProjects();const opening=session.openProject('project-a');await flush();
  const request=requests.shift();assert.equal(request.url,'/api/document-projects/project-a/open');
  const deleting=session.deleteProject('project-a');await flush();assert.equal(requests.length,0);
  request.resolve(good(details()));assert.equal(await opening,false);await flush();
  const deletion=next('/api/document-projects/project-a',{});assert.equal(deletion.options.method,'DELETE');await deleting;
  assert.equal(session.getProjectId(),null);assert.equal(parsedQuestionsData.length,0);
  next('/api/document-projects',{projects:[]});await flush();
 },
 async dirty_current_draft_is_not_saved_again_after_confirmed_deletion(){
  await listProjects();const card=await open();editProject(card,'待删除的本机稿');
  const deleting=session.deleteProject('project-a');await flush();
  assert.equal(requests.length,1);assert.equal(requests[0].options.method,'DELETE');
  assert.equal(await session.saveServerNow(),false,'explicit saves are paused for a deleting project');
  assert.equal(await session.openProject('project-a'),false);
  next('/api/document-projects/project-a',{});await deleting;
  next('/api/document-projects',{projects:[]});await flush();
  await new Promise(resolve=>setTimeout(resolve,550));
  assert.equal(requests.length,0);assert.equal(storage.has('mathbank_document_import_session_v1'),false);
  assert.equal(storage.has('mathbank_document_import_session_v1:project-a'),false);
  assert.equal(storage.has('mathbank_document_import_session_v1:unsynced:project-a'),false);
 },
 async failure_keeps_row_and_manual_edits_without_optimistic_removal(){
  await listProjects();const card=await open();editProject(card,'删除请求中的最后人工修改');
  const deleting=session.deleteProject('project-a');await flush();assert.ok(rowFor('project-a'));
  edit(card,'.card-content-textarea','请求期间继续输入的文字');session.saveSoon();session.flush();
  next('/api/document-projects/project-a',{detail:'工程正在使用，请稍后重试。'},409);await deleting;
  assert.ok(rowFor('project-a'));assert.equal(stored().questions[0].content,'请求期间继续输入的文字');
  assert.equal(card.querySelector('.card-content-textarea').value,'请求期间继续输入的文字');
  assert.ok(toasts.at(-1).includes('删除工程失败'));
  const saving=session.saveServerNow();next('/api/document-projects/project-a/draft',{revision:5});await saving;
  session.clear();assert.equal(requests.length,0);
 },
 async failed_existing_save_does_not_prevent_explicit_deletion(){
  await listProjects();const card=await open();editProject(card,'用户明确要删的未同步稿');
  const saving=session.saveServerNow(),deleting=session.deleteProject('project-a');await flush();
  assert.equal(requests.length,1);next('/api/document-projects/project-a/draft',{detail:'save temporarily unavailable'},503);
  assert.equal(await saving,false);await flush();next('/api/document-projects/project-a',{});await deleting;
  next('/api/document-projects',{projects:[]});await flush();assert.equal(requests.length,0);assert.equal(rowFor('project-a'),undefined);
 },
 async storage_cleanup_failure_does_not_report_committed_delete_as_failed(){
  await listProjects();await open();
  const deleting=session.deleteProject('project-a');await flush();
  localStorage.removeItem=()=>{throw new Error('browser storage blocked');};
  next('/api/document-projects/project-a',{});await deleting;
  assert.equal(rowFor('project-a'),undefined);assert.equal(session.getProjectId(),null);
  next('/api/document-projects',{projects:[]});await flush();
  assert.ok(!toasts.some(text=>text.includes('删除工程失败')));
 },
 async deleting_current_project_blocks_new_project_actions_and_import_receipts(){
  await listProjects();await open();session.update({canResume:true,taskId:'resume-task',phase:'interrupted'});
  const deleting=session.deleteProject('project-a');await flush();
  const recovery=document.getElementById('documentImportRecoveryPanel');
  assert.ok(recovery.querySelectorAll('button').every(button=>button.disabled));
  await session.forkProject();await session.reconnect();await session.resume();await session.analyzeProject();
  await assert.rejects(session.prepareQuestionImport(parsedQuestionsData[0],0),error=>error.message.includes('工程正在删除'));
  assert.equal(requests.length,1,'deletion does not dispatch fork/open/resume/analyze/import work');
  next('/api/document-projects/project-a',{detail:'temporary busy'},409);await deleting;
  assert.ok(recovery.querySelectorAll('button').some(button=>!button.disabled),'failed deletion restores normal actions');
  assert.equal(parsedQuestionsData.length,1);session.clear();assert.equal(requests.length,0);
 },
 async receipt_preparation_started_before_delete_cannot_authorize_a_late_import(){
  await listProjects();await open();
  const preparing=session.prepareQuestionImport(parsedQuestionsData[0],0);
  const rejected=assert.rejects(preparing,error=>error.message.includes('工程正在删除'));
  await flush();assert.equal(requests[0].options.method,'PUT');
  const deleting=session.deleteProject('project-a');await flush();assert.equal(requests.length,1);
  next('/api/document-projects/project-a/draft',{revision:5});await rejected;await flush();
  next('/api/document-projects/project-a',{});await deleting;
  next('/api/document-projects',{projects:[]});await flush();assert.equal(requests.length,0);
 }
};
"""


@pytest.mark.parametrize('scenario', [
    'waits_for_inflight_save_and_removes_before_list_refresh',
    'pauses_debounced_save_and_restores_it_after_delete_failure',
    'old_list_response_cannot_resurrect_confirmed_deletion',
    'deleting_old_project_does_not_clear_newly_opened_project',
    'waits_for_pending_open_and_never_adopts_deleting_project',
    'dirty_current_draft_is_not_saved_again_after_confirmed_deletion',
    'failure_keeps_row_and_manual_edits_without_optimistic_removal',
    'failed_existing_save_does_not_prevent_explicit_deletion',
    'storage_cleanup_failure_does_not_report_committed_delete_as_failed',
    'deleting_current_project_blocks_new_project_actions_and_import_receipts',
    'receipt_preparation_started_before_delete_cannot_authorize_a_late_import',
])
def test_document_project_delete_interleavings(project_script, scenario):
    run_node(project_script + SCENARIOS + '\ndeleteScenarios[' + json.dumps(scenario) + r''']()
.catch(error=>{console.error(error);process.exitCode=1});
''')
