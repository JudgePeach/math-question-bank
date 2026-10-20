"""Polling a partial C failure updates machine answers while protecting edits."""
import json

import pytest

from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401
from test_import_classification_frontend import linkage_script


@pytest.mark.parametrize("teacher_edit", [False, True])
def test_fragment_error_merges_later_original_answers_without_poll_autosave(project_script, teacher_edit):
    script=project_script+linkage_script()+r'''
function fragmentRows(answerCount){return Array.from({length:19},(_,i)=>({
 ...question('首次原题第'+(i+1)+'题 $x=1$。'),project_card_id:'fragment-card-'+(i+1),source_number:i+1,
 answer_markdown:i<answerCount?'首次原版第'+(i+1)+'题答案。':'',difficulty:'normal',
 category_compulsory:'',category_chapter:'',category_knowledge:'',
 classification_review:{required:true,reason_codes:['pdf_local_draft_metadata_unavailable'],
 original:{category_compulsory:'',category_chapter:'',category_knowledge:''}}}));}
function fragmentReport(answers){return {pdf_local_draft:{assembly:'first_pass_blocks',status:'partial',
 persisted:true,source_preserved:true,source_basis:'first_pass_transcription',question_count:19,
 pending_page_numbers:[7],manual_reasons:['fragment_pages_pending'],
 page_issues:[{page_number:7,blocked:true,reason_codes:['fragment_pages_pending']}]},
 pdf_fragment_draft:{questions:19,answers},pdf_draft_metadata:{status:'preparing',calls:0}};}
function normalizeHarnessControls(){parsedQuestionsData.forEach((row,index)=>{
 const card=document.getElementById('parsed-card-'+index);
 card.querySelector('.card-qtype').value=row.question_type;
 card.querySelector('.card-difficulty').value=row.difficulty;
});}
async function check(){
 const initial=details('partial-C-project',0,{questions:[],baselineQuestions:[]});
 initial.source_pages=[{page_number:1,markdown:'已保存原页'}];
 await open(initial);
 const generation=beginDocumentImportTask({skipCancellation:true});
 session.update({projectId:'partial-C-project',taskId:'partial-C-task',phase:'running'});
 pollPdfTaskStatus('partial-C-task',generation);
 const tick=[...intervals.values()][0];
 const early={project_id:'partial-C-project',task_id:'partial-C-task',document_type:'pdf',
  project_revision:0,status:'local_draft',partial:true,diagnostics:fragmentReport(11),data:fragmentRows(11)};
 tick();next('/api/tasks/partial-C-task/status',early);await flush();normalizeHarnessControls();
 assert.equal(parsedQuestionsData.length,19);
 assert.equal(parsedQuestionsData.filter(q=>Boolean(q.answer_markdown)).length,11);
 assert.equal(document.querySelectorAll('#parsedCardsContainer .glass-card').length,19);
 const earlySource=JSON.stringify(parsedQuestionsData.map(q=>[q.content,q.answer_markdown]));
 for(let i=0;i<3;i++){
  tick();next('/api/tasks/partial-C-task/status',{...early,log:'等待第7页'+i,
   diagnostics:{...fragmentReport(11),pdf_vision:{calls:9,retries:1}}});await flush();
 }
 await new Promise(resolve=>setTimeout(resolve,750));
 assert.equal(requests.length,0,'Progress-only polls must not PUT or dispatch a model');
 assert.equal(JSON.stringify(parsedQuestionsData.map(q=>[q.content,q.answer_markdown])),earlySource);
 if(TEACHER_EDIT){
  const card=document.getElementById('parsed-card-11');
  editProject(card,'教师校对的第12题题干，必须全文保留。');
  edit(card,'.card-answer-textarea','教师填写的第12题原答，必须全文保留。');session.flush();
  const saving=session.saveServerNow();await flush();
  assert.equal(requests.length,1);assert.equal(requests[0].options.method,'PUT');
  const outgoing=JSON.parse(requests[0].options.body);
  assert.equal(outgoing.expected_revision,0);
  assert.equal(outgoing.draft.questions[11].content,'教师校对的第12题题干，必须全文保留。');
  assert.equal(outgoing.draft.questions[11].answer_markdown,'教师填写的第12题原答，必须全文保留。');
  next('/api/document-projects/partial-C-project/draft',{project_id:'partial-C-project',revision:1});
  assert.equal(await saving,true);assert.equal(requests.length,0);
 }
 const final={...early,status:'error',can_resume:true,error:'第7页识图失败，完整原包与成功页面已保留。',
  project_revision:TEACHER_EDIT?1:0,diagnostics:fragmentReport(15),data:fragmentRows(15)};
 tick();next('/api/tasks/partial-C-task/status',final);await flush();
 assert.equal(parsedQuestionsData.length,19);
 assert.equal(parsedQuestionsData.filter(q=>Boolean(q.answer_markdown)).length,15);
 for(const index of [12,13,14]){
  assert.equal(parsedQuestionsData[index].answer_markdown,'首次原版第'+(index+1)+'题答案。');
  assert.equal(document.getElementById('parsed-card-'+index).querySelector('.card-answer-textarea').value,
   '首次原版第'+(index+1)+'题答案。');
 }
 assert.equal(parsedQuestionsData[18].answer_markdown,'','Missing answer must not be invented');
 assert.equal(intervals.size,0,'Terminal failure ends polling');
 assert.equal(stored().phase,'error');assert.equal(stored().canResume,true);
 if(TEACHER_EDIT){
  const q=parsedQuestionsData[11];
  assert.equal(q.content,'教师校对的第12题题干，必须全文保留。');
  assert.equal(q.answer_markdown,'教师填写的第12题原答，必须全文保留。');
  assert.equal(q.project_incoming_candidate.content,final.data[11].content);
  assert.equal(q.project_incoming_candidate.answer_markdown,final.data[11].answer_markdown);
  assert.notEqual(q.project_incoming_candidate.metadata_only,true);
  const panel=document.getElementById('parsed-card-11').querySelector('.document-project-candidate');
  assert.ok(panel);assert.equal(panel.querySelector('button').textContent,'采用本次候选');
 }
 // Completion/error can save the newly merged draft. Only the earlier
 // progress-only section is required to generate no PUT.
 session.clear();stopCurrentDocumentPoll();
}
check().catch(error=>{console.error(error);process.exitCode=1;});
'''
    run_node(script.replace('TEACHER_EDIT', json.dumps(teacher_edit)))
