"""Execute project autosave, conflict, resume, and work-page edits without services."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_document_import_resilience_frontend import session_harness
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    result = subprocess.run([shutil.which('node'), '-e', "eval(require('node:fs').readFileSync(0,'utf8'))"],
                            input=script, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope='module')
def project_script(pdf_script):
    return session_harness(pdf_script) + r"""
for(const id of ['documentProjectsPanel','documentProjectsRefresh','documentProjectsList'])create(id);
globalThis.confirm=()=>true;
listeners.DOMContentLoaded();
window.categoryTree={'高中':{'函数':[]}};
const session=window.MathBankDocumentImportSession;
const stored=()=>JSON.parse(storage.get('mathbank_document_import_session_v1'));
const good=(data)=>({ok:true,status:200,json:async()=>data});
function next(path,data,status=200){
 const req=requests.shift();assert.ok(req,'expected request '+path);assert.equal(req.url,path);
 req.resolve(status===200?good(data):{ok:false,status,json:async()=>data});return req;
}
function question(content='工程原题'){
 return {project_card_id:'card-a',content,answer_markdown:'原版答案',question_type:'single_choice',
   category_compulsory:'高中',category_chapter:'函数',category_knowledge:'',source_review:{required:true,blocking:false},
   classification_review:{required:false,original:{category_chapter:'函 数'}}};
}
function details(id='project-a',revision=4,draft={questions:[question()],baselineQuestions:[question()],paperTitle:'工程标题'}){
 return {project_id:id,revision,title:'原PDF标题',status:'completed',task_id:null,can_resume:false,
   total_bytes:2048,last_used_at:Date.now()/1000,expires_at:Date.now()/1000+7*86400,draft,source_pages:[]};
}
async function open(data=details()){
 const work=session.openProject(data.project_id);await flush();
 next('/api/document-projects/'+data.project_id+'/open',data);
 await work;requests.length=0;return document.getElementById('parsed-card-0');
}
function editProject(card,text){edit(card,'.card-content-textarea',text);session.saveSoon();session.flush();}
const projectScenarios={
 async list_loading_label_clears_after_metadata_get_and_can_refresh_again(){
  const list=document.getElementById('documentProjectsList');
  const loading=session.refreshProjects();
  assert.ok(list.children[0].textContent.includes('正在读取'));
  next('/api/document-projects',{projects:[{project_id:'project-a',title:'已完成工程',question_count:3,
   total_bytes:2048,expires_at:Date.now()/1000+86400}]});
  await loading;assert.ok(!list.children[0].textContent.includes('正在读取'));
  assert.ok(list.children[0].textContent.includes('只在打开工程时载入稿件'));
  assert.ok(list.children[1].children[1].textContent.endsWith('3 题'));
  const retry=session.refreshProjects();
  assert.ok(list.children[0].textContent.includes('正在读取'));
  next('/api/document-projects',{detail:'isolated network failure'},503);await retry;
  assert.ok(list.children[0].textContent.includes('暂不可用'));assert.ok(!list.children[0].textContent.includes('正在读取'));
  const recovered=session.refreshProjects();next('/api/document-projects',{projects:[]});await recovered;
  assert.ok(list.children[0].textContent.includes('暂无本地'));
  assert.equal(requests.length,0,'listing and retry never dispatch paid work');
 },
 async autosave_refreshes_list_count_from_three_to_nineteen_without_refetch_or_draft_retention(){
  const metadata={project_id:'project-a',title:'原三题工程',question_count:3,status:'completed',
   total_bytes:2048,expires_at:Date.now()/1000+86400};
  const listing=session.refreshProjects();next('/api/document-projects',{projects:[metadata]});await listing;
  const original=[17,18,19].map(number=>({...question('原题'+number),source_number:number,project_card_id:'card-'+number}));
  await open(details('project-a',4,{questions:original,baselineQuestions:original}));
  const list=document.getElementById('documentProjectsList');
  assert.ok(list.children[1].children[1].textContent.endsWith('3 题'));
  const complete=Array.from({length:19},(_,i)=>({...question('完整结果'+(i+1)),source_number:i+1,project_card_id:'card-'+(i+1)}));
  replaceParsedQuestions(complete);renderParsedQuestionsList(complete);
  session.completed({status:'completed',data:complete},complete);session.flush();
  await new Promise(resolve=>setTimeout(resolve,550));
  assert.equal(requests.length,1,'one debounced save, no list refetch, no model dispatch');
  const request=requests[0];assert.equal(request.options.method,'PUT');
  assert.equal(JSON.parse(request.options.body).draft.questions.length,19);
  next('/api/document-projects/project-a/draft',{project_id:'project-a',revision:5,question_count:19,
   last_used_at:Date.now()/1000,expires_at:Date.now()/1000+7*86400,total_bytes:8192,status:'completed',
   draft:{questions:complete}});
  await flush();assert.ok(list.children[1].children[1].textContent.endsWith('19 题'));
  assert.equal(metadata.question_count,19);assert.equal(metadata.total_bytes,8192);
  assert.ok(!Object.prototype.hasOwnProperty.call(metadata,'draft'),'metadata list must not retain the whole returned draft');
  assert.equal(parsedQuestionsData.length,19);assert.equal(stored().projectLocalDirty,false);
  assert.equal(requests.length,0,'accepted save updates list in memory without another GET or paid request');
 },
 async metadata_list_is_lazy_and_open_is_model_free(){
  assert.equal(requests.length,0,'initial page does not fetch entire projects or start AI');
  const listing=session.refreshProjects();
  next('/api/document-projects',{projects:[{project_id:'project-a',title:'<script>bad()</script>',question_count:1,total_bytes:2048,expires_at:Date.now()/1000+86400}]});
  await listing;assert.equal(requests.length,0,'listing loads metadata only');
  assert.equal(document.getElementById('documentProjectsList').querySelector('script'),null);
  const card=await open();
  assert.equal(card.querySelector('.card-content-textarea').value,'工程原题');
  assert.equal(card.querySelector('.card-answer-textarea').value,'原版答案');
  assert.equal(requests.length,0,'opening cannot resume or call models');
  assert.ok(document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status').textContent.includes('剩余 7 天'));
 },
 async progress_updates_do_not_save_or_replace_the_recovery_status_node(){
  await open();session.update({phase:'ai_splitting',taskId:'same-task',log:'进度一',projectId:'project-a'});
  const status=document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status');
  for(let i=0;i<4;i++)session.update({phase:['ocr_extraction','ai_splitting','layout_analysis','source_verification'][i],taskId:'same-task',log:'进度'+i,projectId:'project-a',diagnostics:{pdf_splitting:{calls:1,elapsed:i}}});
  await new Promise(resolve=>setTimeout(resolve,750));
  assert.equal(requests.length,0,'unchanged progress must never generate draft PUTs');
  assert.equal(stored().projectLocalDirty,false);assert.equal(stored().projectSaveStatus,'saved');
  assert.equal(document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status'),status,
    'unchanged visible recovery content keeps its DOM identity');
 },
 async progress_during_a_real_save_does_not_create_another_dirty_revision(){
  const card=await open();editProject(card,'确实修改的题干');const saving=session.saveServerNow();
  session.update({phase:'ai_splitting',log:'模型仍在工作',diagnostics:{pdf_splitting:{elapsed:25}},projectId:'project-a'});
  next('/api/document-projects/project-a/draft',{revision:5});await saving;
  assert.equal(stored().projectLocalDirty,false,'runtime-only metadata does not revoke a real save receipt');
  await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0);
 },
 async completed_partial_zero_cards_shows_manual_pages_and_preserves_work_source(){
  await open(details('manual-project',1,{questions:[],baselineQuestions:[]}));
  const diag={pdf_project_partial:true,pdf_local_structure:{status:'manual_review',calls:0,manual_reasons:['nested_table','unclosed_environment'],
    page_issues:[{page_number:2,reason_codes:['nested_table'],blocked:true},{page_number:5,reason_codes:['unclosed_environment'],blocked:true}]}};
  session.update({phase:'completed',partial:true,projectId:'manual-project',diagnostics:diag});
  await flush();const data=details('manual-project',1,{questions:[]});
  data.source_pages=Array.from({length:9},(_,i)=>({page_number:i+1,markdown:'第'+(i+1)+'页原文'}));
  next('/api/document-projects/manual-project',data);await flush();
  assert.equal(stored().page_edits.length,9);assert.equal(requests.length,0,'manual review cannot start AI or autosave from progress');
  const reasons=document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-manual-reasons').textContent;
  assert.ok(reasons.includes('第 2 页'));assert.ok(reasons.includes('嵌套'));assert.ok(reasons.includes('第 5 页'));assert.ok(reasons.includes('未闭合'));
  renderParsedQuestionsList([],{diagnostics:diag,partial:true});
  assert.ok(document.getElementById('parsedCardsContainer').querySelector('.document-project-manual-notice'));
  assert.equal(document.getElementById('documentProjectPageEditor').querySelectorAll('textarea').length,9);
  replaceParsedQuestions([],{saveDraft:false});session.completed({status:'completed',partial:true,diagnostics:diag,data:[]},[]);
  await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0,'empty manual terminal metadata is not an edited draft');
 },
 async autosave_serializes_revision_and_preserves_edits_during_request(){
  const card=await open();editProject(card,'第一轮校对');
  const first=session.saveServerNow();
  const req=requests[0],body=JSON.parse(req.options.body);
  assert.equal(body.expected_revision,4);assert.equal(body.draft.questions[0].content,'第一轮校对');
  assert.deepEqual(body.draft.questions[0].classification_review.original,{category_chapter:'函 数'});
  edit(card,'.card-content-textarea','请求途中继续校对');session.saveSoon();
  next('/api/document-projects/project-a/draft',{revision:5,expires_at:Date.now()/1000+7*86400,draft:body.draft});
  await first;
  assert.equal(card.querySelector('.card-content-textarea').value,'请求途中继续校对');
  assert.equal(stored().projectLocalDirty,true,'late save receipt cannot certify a later edit');
  session.flush();const second=session.saveServerNow();
  assert.equal(JSON.parse(requests[0].options.body).expected_revision,5);
  assert.equal(JSON.parse(requests[0].options.body).draft.questions[0].content,'请求途中继续校对');
  next('/api/document-projects/project-a/draft',{revision:6,expires_at:Date.now()/1000+7*86400});await second;
  assert.equal(stored().projectRevision,6);assert.equal(stored().projectLocalDirty,false);
 },
 async conflict_keeps_local_and_blocks_import_or_reanalysis(){
  const card=await open();editProject(card,'不能被409覆盖的人工稿');
  const saving=session.saveServerNow();next('/api/document-projects/project-a/draft',{detail:'revision conflict'},409);
  assert.equal(await saving,false);assert.equal(stored().questions[0].content,'不能被409覆盖的人工稿');
  assert.equal(stored().projectSaveStatus,'conflict');
  assert.equal(await session.saveServerNow(),false);assert.equal(requests.length,0,'conflict is not automatically retried');
  await assert.rejects(session.prepareQuestionImport(parsedQuestionsData[0],0),/尚未保存/);
  assert.equal(requests.length,0,'an unsaved project card cannot become a question POST');
  assert.ok(document.getElementById('documentImportRecoveryPanel').children.some(el=>el.textContent.includes('保存冲突')));
 },
 async offline_save_keeps_browser_backup_and_retry_uses_same_revision(){
  const card=await open();editProject(card,'离线修改');
  const saving=session.saveServerNow();next('/api/document-projects/project-a/draft',{detail:'offline'},503);await saving;
  assert.equal(stored().questions[0].content,'离线修改');assert.equal(stored().projectSaveStatus,'local');
  const retry=session.saveServerNow();assert.equal(JSON.parse(requests[0].options.body).expected_revision,4);
  next('/api/document-projects/project-a/draft',{revision:5});await retry;
  assert.equal(stored().projectLocalDirty,false);
 },
 async project_import_waits_for_matching_draft_and_keeps_stable_card_id(){
  const card=await open();editProject(card,'导入前最新稿');
  const preparing=session.prepareQuestionImport(parsedQuestionsData[0],0);await flush();
  const req=requests[0];assert.equal(JSON.parse(req.options.body).draft.questions[0].project_card_id,'card-a');
  next('/api/document-projects/project-a/draft',{revision:5});
  const proof=await preparing;assert.deepEqual(proof,{project_id:'project-a',card_id:'card-a',draft_revision:5});
  assert.equal(requests.length,0);
 },
 async old_project_without_draft_recovers_all_nine_original_pages_without_ai(){
  const data=details('migrated-project',0,null);data.status='error';data.can_resume=true;
  data.task_id='old-ocr-task';
  data.page_images=Array.from({length:9},(_,i)=>'/static/uploads/tmp/pdf_page_old_'+(i+1)+'.png');
  data.page_numbers=Array.from({length:9},(_,i)=>i+1);
  data.source_pages=data.page_numbers.map(page_number=>({page_number,markdown:'第 '+page_number+' 页已识别原文'}));
  await open(data);
  assert.deepEqual(window.pdfPageImages,data.page_images);assert.deepEqual(window.pdfPageNumbers,data.page_numbers);
  assert.equal(stored().pageImages.length,9);assert.equal(stored().pageNumbers.length,9);
  assert.equal(stored().page_edits.length,9);assert.equal(parsedQuestionsData.length,0);
  assert.equal(requests.length,0,'opening old OCR/prepared evidence cannot dispatch a model');
  assert.equal(intervals.size,0,'failed old tasks remain paused until a teacher chooses to resume');
 },
 async old_project_with_empty_default_arrays_uses_retained_original_pages(){
  const data=details('empty-page-project',0,{questions:[],paperTitle:'默认空工作稿',pageImages:[],pageNumbers:[]});
  data.status='partial';data.can_resume=true;
  data.page_images=Array.from({length:9},(_,i)=>'/static/uploads/tmp/pdf_page_retained_'+(i+1)+'.png');
  data.page_numbers=Array.from({length:9},(_,i)=>i+1);
  data.source_pages=data.page_numbers.map(page_number=>({page_number,markdown:'既有识别工作稿'}));
  await open(data);
  assert.deepEqual(window.pdfPageImages,data.page_images,'empty default draft arrays must not hide retained original pages');
  assert.deepEqual(window.pdfPageNumbers,data.page_numbers);assert.equal(stored().page_edits.length,9);
  assert.equal(requests.length,0);assert.equal(intervals.size,0);
 },
 async page_work_draft_saves_before_explicit_analyze_without_ocr(){
  const data=details('work-project',2,{questions:[],paperTitle:'待拆卷工作稿'});
  data.status='partial';data.source_pages=[{page_number:3,markdown:'原页 $x+1$ <script>bad()</script>'}];
  await open(data);assert.equal(requests.length,0);
  const panel=document.getElementById('documentProjectPageEditor');assert.equal(panel.open,false);
  const input=panel.querySelector('textarea');assert.ok(input.value.includes('<script>'));assert.equal(panel.querySelector('script'),null);
  input.value='已校对的工作稿 $x+2$';input.dispatchEvent(new Event('input'));session.flush();
  const analyzing=session.analyzeProject();await flush();
  const payload=JSON.parse(requests[0].options.body);assert.equal(payload.draft.page_edits[0].markdown,'已校对的工作稿 $x+2$');
  assert.equal(requests.length,1,'analyze cannot precede work-draft persistence');
  next('/api/document-projects/work-project/draft',{revision:3});await flush();
  next('/api/document-projects/work-project/analyze',{status:'success',task_id:'work-task'});await analyzing;
  assert.equal(window.currentPdfTaskId,'work-task');assert.equal(requests.length,0,'no OCR or model route is invoked by the frontend');
  stopCurrentDocumentPoll();
 },
 async late_task_completion_retains_manual_project_cards(){
  const first=question();first.import_item_id='stable-source';const card=await open(details('project-a',4,{questions:[first],baselineQuestions:[first]}));
  editProject(card,'人工题干不可覆盖');
  const incoming={...question('任务迟到的旧题干'),import_item_id:'stable-source'};
  const merged=session.mergeCompletion([incoming]);assert.equal(merged[0].content,'人工题干不可覆盖');
  assert.equal(merged[0].source_review.required,true);
 },
 async fresh_analysis_replaces_unedited_cards_and_retains_manual_candidate(){
  const base=question();await open(details('project-a',4,{questions:[base],baselineQuestions:[base]}));
  const replacement={...question('工作稿重新分析的新题干')};
  const unchanged=session.mergeCompletion([replacement]);
  assert.equal(unchanged[0].content,'工作稿重新分析的新题干','new analysis must appear for an unedited card');
  const card=document.getElementById('parsed-card-0');editProject(card,'教师人工校对稿');
  const retained=session.mergeCompletion([replacement]);
  assert.equal(retained[0].content,'教师人工校对稿');
  assert.equal(retained[0].project_incoming_candidate.content,'工作稿重新分析的新题干');
  show(retained);assert.equal(document.getElementById('parsed-card-0').querySelector('.document-project-candidate').open,false);
 },
 async fresh_analysis_drops_only_unedited_obsolete_cards_and_empty_result_keeps_draft(){
  const oldA=question('旧结构甲'),oldB={...question('旧结构乙'),project_card_id:'card-b'};
  await open(details('project-a',4,{questions:[oldA,oldB],baselineQuestions:[oldA,oldB]}));
  assert.equal(session.mergeCompletion([]).length,2,'a failed empty analysis cannot erase working cards');
  const replacement=question('新结构甲');
  assert.deepEqual(session.mergeCompletion([replacement]).map(q=>q.project_card_id),['card-a']);
  editProject(document.getElementById('parsed-card-1'),'教师保留的旧结构乙');
  const kept=session.mergeCompletion([replacement]);assert.equal(kept.length,2);
  assert.equal(kept[1].content,'教师保留的旧结构乙');
 },
 async opening_reconciles_results_finished_while_the_browser_was_closed(){
  const base=question(),newResult=question('关闭期间完成的新分析结果');
  const data=details('project-a',4,{questions:[base],baselineQuestions:[base]});
  data.latest_result=[newResult];data.latest_result_id='result-new';
  const card=await open(data);assert.equal(card.querySelector('.card-content-textarea').value,'关闭期间完成的新分析结果');
  assert.equal(stored().baselineQuestions[0].content,'关闭期间完成的新分析结果');
  assert.equal(stored().latestResultId,'result-new');
 },
 async opening_keeps_manual_text_and_attaches_new_result_as_candidate(){
  const base=question(),manual=question('关闭前已保存的人工稿'),newResult=question('关闭期间生成的新候选');
  const data=details('project-a',4,{questions:[manual],baselineQuestions:[base]});
  data.latest_result=[newResult];data.latest_result_id='candidate-new';
  const card=await open(data);assert.equal(card.querySelector('.card-content-textarea').value,'关闭前已保存的人工稿');
  assert.equal(parsedQuestionsData[0].project_incoming_candidate.content,'关闭期间生成的新候选');
  assert.equal(card.querySelectorAll('.document-project-candidate').length,1);
  assert.equal(stored().baselineQuestions[0].content,'关闭期间生成的新候选');
 },
 async import_form_carries_project_receipt_after_draft_is_saved(){
  const card=await open();editProject(card,'工程入库的完整题干');
  const saving=saveParsedQuestion(0);await flush();
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/document-projects/project-a/draft');
  next('/api/document-projects/project-a/draft',{revision:5});await flush();
  const req=requests[0];assert.equal(req.url,'/api/questions');
  assert.equal(req.options.body.get('import_project_id'),'project-a');
  assert.equal(req.options.body.get('import_card_id'),'card-a');
  assert.equal(req.options.body.get('import_draft_revision'),'5');
  next('/api/questions',{status:'success',question:{id:42}});await saving;
  assert.equal(parsedQuestionsData[0].saved_id,42);session.flush();
  assert.equal(stored().questions[0].saved_id,42);assert.equal(stored().questions[0].saved,true);
 },
 async editing_during_project_preparation_rejects_stale_question_post(){
  const card=await open();editProject(card,'准备入库时的旧稿');
  const saving=saveParsedQuestion(0);await flush();
  edit(card,'.card-content-textarea','保存工程时继续输入的新稿');session.saveSoon();
  next('/api/document-projects/project-a/draft',{revision:5});await flush();
  if(requests[0]?.url.endsWith('/draft')){next('/api/document-projects/project-a/draft',{revision:6});await flush();}
  assert.equal(await saving,false);assert.equal(requests.some(req=>req.url==='/api/questions'),false);
  assert.equal(card.querySelector('.card-content-textarea').value,'保存工程时继续输入的新稿');
 },
 async reused_pdf_opens_saved_working_draft_without_polling_old_result(){
  window.currentPdfFile=new Blob(['pdf']);runAIPaperParse();
  next('/api/upload/pdf-task',{status:'success',task_id:'old-completed-task',project_id:'project-a',project_revision:4,reused_project:true});
  await flush();next('/api/document-projects/project-a/open',details('project-a',4,{questions:[question('原工程里已经保存的人工稿')],baselineQuestions:[question('旧任务最初的原文')]}));
  await flush();assert.equal(parsedQuestionsData[0].content,'原工程里已经保存的人工稿');
  assert.equal(requests.length,0);assert.equal(intervals.size,0,'do not poll the old completed task into the working draft');
 },
 async reused_pdf_from_open_project_does_not_replace_its_backup_with_empty_cards(){
  const card=await open();editProject(card,'重复上传前尚未同步的人工稿');
  window.currentPdfFile=new Blob(['pdf']);runAIPaperParse();
  const backup=JSON.parse(storage.get('mathbank_document_import_session_v1:project-a'));
  assert.equal(backup.questions[0].content,'重复上传前尚未同步的人工稿');
  next('/api/upload/pdf-task',{status:'success',task_id:'old-completed-task',project_id:'project-a',project_revision:4,reused_project:true});
  await flush();next('/api/document-projects/project-a/open',details());await flush();
  assert.equal(parsedQuestionsData[0].content,'重复上传前尚未同步的人工稿');
  assert.equal(requests.length,0);assert.equal(intervals.size,0);
 },
 async reused_failed_pdf_does_not_retry_until_teacher_clicks_resume(){
  window.currentPdfFile=new Blob(['pdf']);runAIPaperParse();
  next('/api/upload/pdf-task',{status:'success',task_id:'old-failed-task',project_id:'project-a',project_revision:4,reused_project:true});
  await flush();const data=details();data.status='error';data.task_id='old-failed-task';data.can_resume=true;
  next('/api/document-projects/project-a/open',data);await flush();
  assert.equal(requests.length,0);assert.equal(intervals.size,0);
  assert.ok(document.getElementById('documentImportRecoveryPanel').querySelectorAll('button').some(button=>button.textContent==='继续未完成解析'));
 },
 async project_crop_binds_persistent_source_and_rejects_cross_project_reply(){
  const card=await open();const q=parsedQuestionsData[0];window.currentPdfTaskId=null;
  window.activeCropQuestionIndex=0;window.pdfPageNumbers=[1];
  window.activePdfCropSession={projectId:'project-a',taskId:null,generation:parsedQuestionsGeneration,
    questionIndex:0,question:q,closed:false};
  submitPdfCropCoordinates();const req=requests.shift();assert.equal(req.url,'/api/ai/manual-crop-pdf');
  assert.equal(JSON.parse(req.options.body).project_id,'project-a');
  session.getProjectId=()=> 'different-project';
  req.resolve(good({status:'success',image_path:'/static/uploads/tmp/old-project-crop.png'}));await flush();
  assert.deepEqual(q.image_paths,[]);assert.equal(card.querySelector('.card-content-textarea').value,'工程原题');
 },
 async resetting_workspace_preserves_last_keystrokes_in_project_backup(){
  const card=await open();edit(card,'.card-content-textarea','清理工作区前最后输入的稿件');session.saveSoon();
  session.clear();assert.equal(storage.has('mathbank_document_import_session_v1'),false);
  const backup=JSON.parse(storage.get('mathbank_document_import_session_v1:project-a'));
  assert.equal(backup.questions[0].content,'清理工作区前最后输入的稿件');assert.equal(backup.projectLocalDirty,true);
 },
 async late_open_response_cannot_replace_a_new_import_session(){
  const opening=session.openProject('slow-project');await flush();
  const request=requests.shift();assert.equal(request.url,'/api/document-projects/slow-project/open');
  session.clear();show([question('用户已经开始了另一份试卷')]);session.begin({phase:'creating'});
  request.resolve(good(details('slow-project')));assert.equal(await opening,false);
  assert.equal(parsedQuestionsData[0].content,'用户已经开始了另一份试卷');
  assert.equal(requests.length,0,'superseded open cannot touch or adopt its project');
 },
 async deleting_project_is_explicit_and_does_not_delete_library_questions(){
  await open();const deleting=session.deleteProject('project-a');await flush();
  const req=next('/api/document-projects/project-a',{});assert.equal(req.options.method,'DELETE');await flush();
  next('/api/document-projects',{projects:[]});await deleting;
  assert.equal(parsedQuestionsData.length,0);assert.equal(requests.length,0);
  assert.equal(storage.has('mathbank_document_import_session_v1:project-a'),false);
 }
};
"""


@pytest.mark.parametrize('scenario', [
    'list_loading_label_clears_after_metadata_get_and_can_refresh_again',
    'autosave_refreshes_list_count_from_three_to_nineteen_without_refetch_or_draft_retention',
    'metadata_list_is_lazy_and_open_is_model_free',
    'autosave_serializes_revision_and_preserves_edits_during_request',
    'progress_updates_do_not_save_or_replace_the_recovery_status_node',
    'progress_during_a_real_save_does_not_create_another_dirty_revision',
    'completed_partial_zero_cards_shows_manual_pages_and_preserves_work_source',
    'conflict_keeps_local_and_blocks_import_or_reanalysis',
    'offline_save_keeps_browser_backup_and_retry_uses_same_revision',
    'project_import_waits_for_matching_draft_and_keeps_stable_card_id',
    'page_work_draft_saves_before_explicit_analyze_without_ocr',
    'old_project_without_draft_recovers_all_nine_original_pages_without_ai',
    'old_project_with_empty_default_arrays_uses_retained_original_pages',
    'late_task_completion_retains_manual_project_cards',
    'deleting_project_is_explicit_and_does_not_delete_library_questions',
    'fresh_analysis_replaces_unedited_cards_and_retains_manual_candidate',
    'import_form_carries_project_receipt_after_draft_is_saved',
    'opening_reconciles_results_finished_while_the_browser_was_closed',
    'fresh_analysis_drops_only_unedited_obsolete_cards_and_empty_result_keeps_draft',
    'opening_keeps_manual_text_and_attaches_new_result_as_candidate',
    'editing_during_project_preparation_rejects_stale_question_post',
    'late_open_response_cannot_replace_a_new_import_session',
    'resetting_workspace_preserves_last_keystrokes_in_project_backup',
    'project_crop_binds_persistent_source_and_rejects_cross_project_reply',
    'reused_pdf_opens_saved_working_draft_without_polling_old_result',
    'reused_failed_pdf_does_not_retry_until_teacher_clicks_resume',
    'reused_pdf_from_open_project_does_not_replace_its_backup_with_empty_cards',
])
def test_document_project_frontend(project_script, scenario):
    run_node(project_script + '\nprojectScenarios[' + json.dumps(scenario) + r""" ]()
.catch(error=>{console.error(error);process.exitCode=1});
""")


def test_imported_knowledge_points_are_editable_saved_and_match_the_project_receipt(project_script):
    run_node(project_script + r"""
(async()=>{
 const point={code:'1796',name:'数列通项',role:'main',status:'confirmed',origin:'ai_classification',evidence:'工程原题',reason:'直接考查'};
 const q={...question(),knowledge_points:[point]};
 const card=await open(details('project-a',4,{questions:[q],baselineQuestions:[q]}));
 const host=card.querySelector('.card-knowledge-points');assert.equal(host.hidden,false);
 assert.deepEqual(host.querySelectorAll('button').map(button=>button.textContent),['移除'],'no extra knowledge confirmation');
 const role=host.querySelector('select');role.value='auxiliary';role.dispatchEvent(new Event('change'));
 assert.equal(parsedQuestionsData[0].knowledge_points[0].role,'auxiliary');
 const saving=saveParsedQuestion(0);await flush();
 const projectRequest=requests[0];assert.ok(projectRequest.url.endsWith('/draft'));
 const projectPoints=JSON.parse(projectRequest.options.body).draft.questions[0].knowledge_points;
 next('/api/document-projects/project-a/draft',{revision:5});await flush();
 const req=requests[0];assert.equal(req.url,'/api/questions');
 const submitted=JSON.parse(req.options.body.get('knowledge_points'));
 assert.deepEqual(submitted,projectPoints);assert.equal(submitted[0].evidence,'工程原题');
 assert.equal(submitted[0].status,'confirmed');assert.equal(submitted[0].role,'auxiliary');
 next('/api/questions',{status:'success',question:{id:51}});await saving;
 assert.deepEqual(parsedQuestionsData[0].savedSnapshot.knowledge_points,submitted);
 host.querySelector('button').dispatchEvent(new Event('click'));
 assert.deepEqual(parsedQuestionsData[0].knowledge_points,[]);assert.equal(parsedQuestionsData[0].saved,false);
 session.clear();
})().catch(error=>{console.error(error);process.exitCode=1});
""")


def test_late_pdf_metadata_adds_points_and_keeps_later_teacher_edits_as_a_candidate(project_script):
    run_node(project_script + r"""
(async()=>{
 const original=question();await open(details('project-a',4,{questions:[original],baselineQuestions:[original]}));
 session.update({phase:'ai_metadata',diagnostics:{pdf_local_draft:{source_preserved:true},pdf_draft_metadata:{status:'used'}}});
 const incoming={...original,knowledge_points:[{code:'1796',name:'通项',role:'main',status:'confirmed',origin:'ai_classification'}]};
 let merged=session.mergeCompletion([incoming]);assert.equal(merged[0].knowledge_points[0].code,'1796');
 assert.equal(merged[0].knowledge_points[0].status,'confirmed');assert.equal(merged[0].project_incoming_candidate,undefined);
 parsedQuestionsData[0].knowledge_points=[{code:'1859',name:'人工零点',role:'auxiliary',status:'suggested',origin:'manual'}];
 merged=session.mergeCompletion([incoming]);
 assert.deepEqual(merged[0].knowledge_points.map(point=>point.code),['1859']);
 assert.equal(merged[0].knowledge_points[0].status,'suggested','preserve historical status without a mass rewrite');
 assert.equal(merged[0].project_incoming_candidate.metadata_only,true);
 assert.equal(merged[0].project_incoming_candidate.knowledge_points[0].code,'1796');
 session.clear();
})().catch(error=>{console.error(error);process.exitCode=1});
""")
