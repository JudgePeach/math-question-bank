"""A saved working-copy acknowledgement must never become a machine baseline."""
from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401
from test_import_classification_frontend import linkage_script


def test_teacher_edit_fast_resume_reopen_then_real_analysis_keeps_machine_baseline(project_script):
    run_node(project_script + linkage_script() + r'''
const copy=value=>JSON.parse(JSON.stringify(value));
const machine=[{...question('机器首轮题干'),difficulty:'normal',image_paths:[],image_layouts:{}},
 {...question('另一道未修改题'),project_card_id:'card-b',difficulty:'normal',image_paths:[]}];
const diag={pdf_local_draft:{assembly:'first_pass_blocks',source_preserved:true,status:'partial',persisted:true},
 pdf_draft_metadata:{status:'completed'}};
function normalize(){parsedQuestionsData.forEach((row,i)=>{
 const card=document.getElementById('parsed-card-'+i);
 card.querySelector('.card-qtype').value=row.question_type;
 card.querySelector('.card-difficulty').value=row.difficulty;
});}
async function check(){
 const initial=details('working-copy-project',4,{questions:copy(machine),baselineQuestions:copy(machine),diagnostics:diag});
 initial.source_pages=[{page_number:1,markdown:'首次原页'}];
 let card=await open(initial);normalize();
 editProject(card,'教师校对正文 ![人工补图](/static/uploads/tmp/teacher-manual.png)');
 edit(card,'.card-answer-textarea','教师校对原答');edit(card,'.card-difficulty','hard');
 parsedQuestionsData[0].image_paths=['/static/uploads/tmp/teacher-manual.png'];
 parsedQuestionsData[0].image_layouts={'/static/uploads/tmp/teacher-manual.png':{align:'center',size:'large'}};
 parsedQuestionsData[0].document_import_source_edited=true;
 parsedQuestionsData[0].project_incoming_candidate={...copy(machine[0]),content:'先前真实机器候选'};
 session.saveSoon();session.flush();const saving=session.saveServerNow();await flush();
 const saved=JSON.parse(requests[0].options.body).draft;
 next('/api/document-projects/working-copy-project/draft',{revision:5});assert.equal(await saving,true);
 const teacher=copy(saved.questions[0]);assert.equal(teacher.difficulty,'hard');
 const restoredDiag={...copy(diag),pdf_draft_restore:{working_copy:true}};
 const generation=beginDocumentImportTask({skipCancellation:true});
 session.update({projectId:'working-copy-project',taskId:'restore-working',phase:'running'});
 pollPdfTaskStatus('restore-working',generation);const tick=[...intervals.values()][0];tick();
 next('/api/tasks/restore-working/status',{status:'completed',project_id:'working-copy-project',task_id:'restore-working',
  document_type:'pdf',project_revision:5,data:copy(saved.questions),diagnostics:restoredDiag,partial:true});await flush();
 assert.deepEqual(stored().baselineQuestions,machine,'A working-copy completion cannot replace the machine baseline');
 assert.equal(parsedQuestionsData[0].content,teacher.content);assert.equal(parsedQuestionsData[0].difficulty,'hard');
 assert.deepEqual(parsedQuestionsData[0].project_incoming_candidate,teacher.project_incoming_candidate);
 await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0,'A working-copy ACK cannot PUT');
 const opened=details('working-copy-project',5,copy(saved));
 opened.task_id='restore-working';opened.task={status:'completed',diagnostics:restoredDiag};opened.diagnostics=restoredDiag;
 opened.latest_result=copy(saved.questions);opened.latest_result_id='restore-working';
 opened.source_pages=[{page_number:1,markdown:'首次原页'}];
 await open(opened);normalize();
 assert.deepEqual(stored().baselineQuestions,machine,'Opening the saved working result must not promote it to machine evidence');
 assert.equal(stored().latestResultId,'restore-working');
 assert.deepEqual(parsedQuestionsData[0].project_incoming_candidate,teacher.project_incoming_candidate);
 await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0,'Opening ACK cannot PUT');
 const analyzing=session.analyzeProject();await flush();
 assert.equal(requests.length,1);next('/api/document-projects/working-copy-project/analyze',
  {status:'success',task_id:'real-machine-analysis'});await analyzing;
 const incoming=[{...copy(machine[0]),content:'本地新规则整理的机器题干',answer_markdown:'后来的机器原答',difficulty:'easy'},
  {...copy(machine[1]),content:'另一道题后来的正确机器结果',difficulty:'hard'}];
 const incomingEvidence=copy(incoming);
 const analysisTick=[...intervals.values()][0];analysisTick();
 next('/api/tasks/real-machine-analysis/status',{status:'completed',task_id:'real-machine-analysis',
  project_id:'working-copy-project',project_revision:5,document_type:'pdf',data:incoming,diagnostics:diag});await flush();
 assert.equal(parsedQuestionsData[0].content,teacher.content);assert.equal(parsedQuestionsData[0].answer_markdown,teacher.answer_markdown);
 assert.equal(parsedQuestionsData[0].difficulty,'hard');assert.deepEqual(parsedQuestionsData[0].image_layouts,teacher.image_layouts);
 assert.deepEqual(parsedQuestionsData[0].image_paths,teacher.image_paths);
 assert.equal(parsedQuestionsData[0].project_incoming_candidate.content,incoming[0].content);
 assert.equal(parsedQuestionsData[0].project_incoming_candidate.difficulty,'easy');
 assert.equal(parsedQuestionsData[1].content,incoming[1].content);assert.equal(parsedQuestionsData[1].difficulty,'hard');
 assert.deepEqual(stored().baselineQuestions,incomingEvidence,'A genuine new machine result still advances the machine baseline');
 normalize(); // The lightweight DOM harness does not apply HTML selected attributes.
 const commit=session.saveServerNow();await flush();
 const outgoing=JSON.parse(requests[0].options.body);assert.equal(outgoing.expected_revision,5);
 assert.equal(outgoing.draft.questions[0].content,teacher.content);assert.equal(outgoing.draft.questions[0].difficulty,'hard');
 assert.ok(!outgoing.draft.diagnostics.pdf_draft_restore);
 next('/api/document-projects/working-copy-project/draft',{revision:6});await commit;
 session.clear();stopCurrentDocumentPoll();
}
check().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_persistent_source_edit_flag_protects_previously_eroded_working_baseline(project_script):
    run_node(project_script + r'''
async function check(){
 const teacher={...question('已经保存的教师正文和人工图'),difficulty:'hard',document_import_source_edited:true,
  image_paths:['/static/uploads/tmp/teacher.png']};
 await open(details('already-eroded',4,{questions:[teacher],baselineQuestions:[teacher]}));
 const card=document.getElementById('parsed-card-0');card.querySelector('.card-qtype').value=teacher.question_type;
 card.querySelector('.card-difficulty').value='hard';
 const incoming={...question('真实机器题干'),difficulty:'normal',image_paths:[]};
 const merged=session.mergeCompletion([incoming]);
 assert.equal(merged[0].content,teacher.content);assert.equal(merged[0].difficulty,'hard');
 assert.deepEqual(merged[0].image_paths,teacher.image_paths);
 assert.equal(merged[0].project_incoming_candidate.content,incoming.content);
 session.clear();
}
check().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_working_copy_reports_zero_new_calls_without_erasing_history(project_script):
    run_node(project_script + r'''
const report={pdf_draft_restore:{working_copy:true},pdf_local_draft:{question_count:19},
 pdf_extraction:{native_pages:0,regional_pages:0,full_vision_pages:8},
 pdf_resume:{run:0,reused_visual_pages:0},pdf_vision:{calls:8,retries:0,
  attempts:Array.from({length:8},(_,i)=>({run:0,attempt:1,page_number:i+1,http_started:true}))},
 pdf_draft_metadata:{status:'completed',new_calls:1,calls:1}};
const before=JSON.stringify(report);
assert.ok(pdfCurrentOcrReportSummary(report.pdf_vision,report.pdf_resume,report).includes('新增识图 0 次'));
const summary=pdfProjectParseReportSummary(report);
assert.ok(summary.includes('新增识图 0 次'));assert.ok(summary.includes('新增分类请求 0 次'));
assert.ok(summary.includes('新增拆题请求 0 次'));assert.ok(summary.includes('未调用模型'));
renderSourceIntegrityReport(report);
const panel=document.getElementById('parsedSourceIntegrityReport');
assert.ok(panel.querySelector('.pdf-extraction-summary').textContent.includes('新增识图 0 次'));
assert.ok(panel.querySelector('.pdf-splitting-summary').textContent.includes('新增分类请求 0 次'));
assert.ok(!panel.querySelector('.pdf-extraction-summary').textContent.includes('8 次'));
appendSourceIntegrityLog(report);
assert.ok(logs.some(line=>line.includes('新增识图 0 次')));
assert.ok(!logs.some(line=>line.includes('新增识图 8 次')||line.includes('新增分类请求 1 次')));
assert.equal(JSON.stringify(report),before,'Historical diagnostic counts stay unchanged');
const actual=JSON.parse(before);delete actual.pdf_draft_restore;
assert.ok(pdfProjectParseReportSummary(actual).includes('新增识图 8 次'));
assert.ok(pdfProjectParseReportSummary(actual).includes('新增分类请求 1 次'));
assert.ok(pdfExtractionReportSummary(actual.pdf_extraction,actual.pdf_vision,actual.pdf_resume,actual).includes('新增识图 8 次'));
''')
