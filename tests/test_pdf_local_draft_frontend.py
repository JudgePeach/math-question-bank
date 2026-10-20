"""Local OCR draft preview and metadata adoption, without services/models."""
import json

import pytest

from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401
from test_import_classification_frontend import linkage_script


@pytest.mark.parametrize('scenario', [
    'local_draft_summary_never_describes_classification_as_body_regeneration',
    'local_draft_preview_is_once_per_task_and_progress_does_not_put',
    'first_poll_during_figure_review_shows_saved_cards_and_keeps_teacher_edits',
    'metadata_fills_unchanged_fields_and_preserves_teacher_body_answer',
    'manual_classification_candidate_can_only_apply_metadata',
    'metadata_failure_keeps_nineteen_cards_unselected_and_manual_classification_importable',
    'manual_zero_draft_keeps_page_editor_and_specific_reasons',
    'fresh_zero_draft_distinguishes_saved_source_from_missing_card_draft',
    'zero_draft_source_receipt_does_not_mask_unsaved_edits_or_conflict',
    'specific_local_blockers_precede_generic_legacy_partial',
    'rebuilding_thirteen_machine_cards_to_nineteen_replaces_unedited_body_and_preserves_teacher',
    'source_changes_create_full_candidate_and_can_be_adopted_in_local_draft_mode',
    'source_formula_advisory_does_not_mark_restored_structure_incomplete',
    'generic_image_verification_diagnostic_keeps_formula_and_page_specific_source_reasons',
])
def test_local_pdf_draft_frontend(project_script, scenario):
    script = project_script + linkage_script() + r"""
function localQuestion(n=1){return {...question('首次识图第'+n+'题 $x+1$。'),project_card_id:'local-card-'+n,source_number:n,
 answer_markdown:'首次原版答案 $2$。',difficulty:'normal',category_compulsory:'',category_chapter:'',category_knowledge:'',
 classification_review:{required:true,reason_codes:['pdf_local_draft_metadata_unavailable'],original:{category_compulsory:'',category_chapter:'',category_knowledge:''}}};}
function localReport(count=19,status='ready',metadata={status:'preparing',calls:0}){return {
 pdf_local_draft:{status,persisted:true,source_basis:'first_pass_transcription',source_preserved:true,question_count:count,unresolved_ranges:[],page_issues:[]},
 pdf_draft_metadata:metadata};}
async function preview(count=1){
 await open(details('local-project',1,{questions:[],baselineQuestions:[]}));
 const report=localReport(count);session.update({projectId:'local-project',taskId:'local-task',phase:'local_draft',diagnostics:report});
 const data=Array.from({length:count},(_,i)=>localQuestion(i+1));
 const task={project_id:'local-project',task_id:'local-task',status:'local_draft',diagnostics:report,data};
 assert.equal(session.previewLocalDraft(task),true);
 // The shared lightweight DOM does not implement HTML selected-option state.
 data.forEach((row,index)=>{const card=document.getElementById('parsed-card-'+index);
  card.querySelector('.card-qtype').value=row.question_type;card.querySelector('.card-difficulty').value=row.difficulty;});
 return {task,data};
}
const localScenarios={
 async first_poll_during_figure_review_shows_saved_cards_and_keeps_teacher_edits(){
  await open(details('early-project',0,{questions:[],baselineQuestions:[]}));
  const generation=beginDocumentImportTask({skipCancellation:true});
  session.update({projectId:'early-project',taskId:'early-task',phase:'running'});
  pollPdfTaskStatus('early-task',generation);
  const tick=[...intervals.values()][0];
  const report=localReport(19);report.pdf_figure_review={status:'running',total:4,verified:0,pending:4,calls:4};
  const task={project_id:'early-project',task_id:'early-task',status:'source_verification',
   document_type:'pdf',project_revision:0,diagnostics:report,data:Array.from({length:19},(_,i)=>localQuestion(i+1))};
  tick();next('/api/tasks/early-task/status',task);await flush();
  assert.equal(parsedQuestionsData.length,19,'first poll can miss the brief local_draft state');
  assert.equal(document.querySelectorAll('#parsedCardsContainer .glass-card').length,19);
  parsedQuestionsData[0].content='教师已经开始校对';
  document.getElementById('parsed-card-0').querySelector('.card-content-textarea').value='教师已经开始校对';
  tick();next('/api/tasks/early-task/status',{...task,data:[localQuestion(99)]});await flush();
  assert.equal(parsedQuestionsData.length,19);assert.equal(parsedQuestionsData[0].content,'教师已经开始校对');
  await new Promise(resolve=>setTimeout(resolve,750));
  assert.equal(requests.length,0,'figure-review polling must not save or replace the working draft');
  stopCurrentDocumentPoll();session.clear();
 },
 async local_draft_summary_never_describes_classification_as_body_regeneration(){
  const report=localReport(19,'ready',{status:'metadata_failed',calls:1,metadata_failed:true});
  report.pdf_project_legacy_parse={reused:false,new_calls:8};report.pdf_splitting={calls:8};
  const text=pdfProjectParseReportSummary(report);
  assert.ok(text.includes('首次识图稿已保存，本地题稿可直接校对入库'));
  assert.ok(text.includes('本次新增分类请求 1 次'));assert.ok(text.includes('可手动选择分类'));
  assert.ok(!text.includes('拆题请求'));assert.ok(!text.includes('8 次'));
  const review=pdfStructureReviewState(report,{partial:false});assert.equal(review.localDraft,true);
  assert.equal(review.incomplete,false);assert.equal(review.classificationPending,true);
  report.pdf_project_legacy_parse.reused=true;report.pdf_draft_metadata={status:'completed',new_calls:1,calls:1};
  const hit=pdfProjectParseReportSummary(report);assert.ok(hit.includes('本次新增分类请求 0 次'));
  assert.ok(!hit.includes('新增分类请求 1 次'),'outer cache history is not a new metadata request');
 },
 async local_draft_preview_is_once_per_task_and_progress_does_not_put(){
  const {task}=await preview(19);assert.equal(parsedQuestionsData.length,19);
  assert.equal(document.querySelectorAll('#parsedCardsContainer .glass-card').length,19);
  assert.deepEqual(getCheckedUnsavedIndices(),[]);
  session.update({phase:'ai_metadata',diagnostics:task.diagnostics});
  const status=document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status');
  for(let i=0;i<3;i++){
   session.update({phase:'ai_metadata',log:'分类进度'+i,diagnostics:task.diagnostics});
   assert.equal(session.previewLocalDraft({...task,data:[localQuestion(99)]}),false);
  }
  await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0,'preview and repeated metadata progress are not edits');
  assert.equal(parsedQuestionsData.length,19);assert.equal(stored().projectLocalDirty,false);
  assert.equal(document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status'),status);
 },
 async metadata_fills_unchanged_fields_and_preserves_teacher_body_answer(){
  const {data}=await preview();const card=document.getElementById('parsed-card-0');
  editProject(card,'教师修正题干 $x^2$。');edit(card,'.card-answer-textarea','教师修正原版答案 $3$。');
  const report=localReport(1,'ready',{status:'used',calls:1});session.update({phase:'completed',diagnostics:report});
  const incoming={...data[0],category_compulsory:'高中',category_chapter:'函数',difficulty:'hard'};delete incoming.classification_review;
  const merged=session.mergeCompletion([incoming]);
  assert.equal(merged[0].content,'教师修正题干 $x^2$。');assert.equal(merged[0].answer_markdown,'教师修正原版答案 $3$。');
  assert.equal(merged[0].category_chapter,'函数');assert.equal(merged[0].difficulty,'hard');
  assert.equal(merged[0].classification_review.required,false,'valid metadata clears the preview pending state');
  assert.deepEqual(merged[0].classification_review.original,data[0].classification_review.original);
  assert.equal(merged[0].project_card_id,'local-card-1');assert.equal(merged[0].project_incoming_candidate,undefined);
  session.clear();
 },
 async manual_classification_candidate_can_only_apply_metadata(){
  const {data}=await preview();const card=document.getElementById('parsed-card-0');
  editProject(card,'教师正文必须保留');edit(card,'.card-answer-textarea','教师答案必须保留');
  edit(card,'.card-difficulty','normal');
  parsedQuestionsData[0].classification_review.confirmed_by='user';
  const report=localReport(1,'ready',{status:'used',calls:1});session.update({phase:'completed',diagnostics:report});
  const incoming={...data[0],category_compulsory:'高中',category_chapter:'函数'};
  delete incoming.classification_review;
  const merged=session.mergeCompletion([incoming]);assert.equal(merged[0].project_incoming_candidate.metadata_only,true);
  assert.equal(merged[0].project_incoming_candidate.content,undefined);
  show(merged);const panel=document.getElementById('parsed-card-0').querySelector('.document-project-candidate');
  assert.equal(panel.querySelector('button').textContent,'采用分类建议');panel.querySelector('button').dispatchEvent(new Event('click'));
  assert.equal(parsedQuestionsData[0].content,'教师正文必须保留');assert.equal(parsedQuestionsData[0].answer_markdown,'教师答案必须保留');
  assert.equal(parsedQuestionsData[0].category_chapter,'函数');assert.equal(parsedQuestionsData[0].classification_review.required,false);
  assert.equal(parsedQuestionsData[0].project_card_id,'local-card-1');session.clear();
 },
 async metadata_failure_keeps_nineteen_cards_unselected_and_manual_classification_importable(){
  const {data}=await preview(19);const report=localReport(19,'ready',{status:'metadata_failed',calls:1,metadata_failed:true});
  session.update({phase:'completed',diagnostics:report});const merged=session.mergeCompletion(data);show(merged);
  assert.equal(parsedQuestionsData.length,19);assert.deepEqual(getCheckedUnsavedIndices(),[]);
  const card=document.getElementById('parsed-card-0');
  card.querySelector('.card-qtype').value='single_choice';card.querySelector('.card-difficulty').value='normal';
  for(const [selector,value] of [['.card-compulsory','高中'],['.card-chapter','函数']]){
   const select=card.querySelector(selector);select.value=value;select.dispatchEvent(new Event('change',{bubbles:true}));
  }
  assert.equal(parsedQuestionsData[0].classification_review.required,false);
  assert.equal(validateParsedQuestionBeforeImport(0,{notify:false,focus:false}),true);
  assert.equal(requests.length,0,'manual classification does not call models');session.clear();
 },
 async manual_zero_draft_keeps_page_editor_and_specific_reasons(){
  const report=localReport(0,'manual',{status:'classification_pending',new_calls:0});
  report.pdf_local_draft.unresolved_ranges=[{page_numbers:[2,3],reason:'question_numbers_unproved'}];
  const detail=details('manual-local',1,{questions:[],baselineQuestions:[]});detail.status='completed';detail.partial=true;detail.diagnostics=report;
  detail.source_pages=[{page_number:2,markdown:'首次识图原文 $x+1$'},{page_number:3,markdown:'跨页续文'}];await open(detail);
  renderParsedQuestionsList([],{diagnostics:report,partial:true});
  const notice=document.getElementById('parsedCardsContainer').querySelector('.document-project-manual-notice').textContent;
  assert.ok(notice.includes('第 2、3 页'));assert.ok(notice.includes('原题号'));assert.ok(!notice.includes('AI 未能'));
  assert.equal(document.getElementById('documentProjectPageEditor').querySelectorAll('textarea').length,2);
  assert.equal(requests.length,0,'opening manual source never starts a model request');
 },
 async fresh_zero_draft_distinguishes_saved_source_from_missing_card_draft(){
  const report=localReport(0,'partial');report.pdf_local_draft.manual_reasons=['answer_section_ambiguous'];
  report.pdf_local_draft.page_issues=[{page_number:7,reason_codes:['answer_section_ambiguous']}];
  report.pdf_project_legacy_parse={status:'partial',partial:true,new_calls:0};
  const data=details('fresh-zero-project',0,null);Object.assign(data,{status:'completed',partial:true,
   has_draft:false,has_baseline:false,stage_count:14,question_count:0,diagnostics:report,task_id:'fresh-zero-task',
   source_pages:Array.from({length:9},(_,i)=>({page_number:i+1,markdown:'首次识图第'+(i+1)+'页原文'}))});
  session.begin({documentType:'pdf',phase:'creating',clientRequestId:'fresh-upload'});
  session.update({projectId:data.project_id,projectRevision:0,taskId:data.task_id,phase:'running'});
  const task={project_id:data.project_id,task_id:data.task_id,status:'completed',partial:true,
   project_revision:0,source_available:true,diagnostics:report,data:[]};
  session.completed(task,[]);next('/api/document-projects/'+data.project_id,data);await flush();
  renderParsedQuestionsList([],{diagnostics:report,partial:true});
  const panel=document.getElementById('documentImportRecoveryPanel');
  assert.ok(panel.children[0].textContent.includes('原文件和9页识图稿已保存；题卡尚未整理'));
  assert.ok(!panel.querySelector('.document-project-save-status').textContent.includes('等待保存'));
  assert.ok(panel.querySelector('.document-project-manual-reasons').textContent.includes('第 7 页'));
  assert.ok(panel.querySelector('.document-project-manual-reasons').textContent.includes('原版答案分区不唯一'));
  assert.equal(document.getElementById('documentProjectPageEditor').querySelectorAll('textarea').length,9);
  await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0,'zero-card completion must not create an empty baseline or a draft PUT');
  const listing=session.refreshProjects();next('/api/document-projects',{projects:[data]});await listing;
  const label=document.getElementById('documentProjectsList').children[1].children[1].textContent;
  assert.ok(label.includes('题卡尚未整理'));assert.ok(!label.includes('可继续校对'));
  await open(data);assert.equal(stored().projectRevision,0);assert.equal(stored().projectLocalDirty,false);
  assert.ok(panel.children[0].textContent.includes('原文件和9页识图稿已保存；题卡尚未整理'));
  await new Promise(resolve=>setTimeout(resolve,750));assert.equal(requests.length,0,'reopen only reads retained source');
 },
 async zero_draft_source_receipt_does_not_mask_unsaved_edits_or_conflict(){
  const report=localReport(0,'manual');report.pdf_local_draft.manual_reasons=['answer_section_ambiguous'];
  const data=details('zero-conflict-project',0,null);data.diagnostics=report;data.source_pages=[{page_number:1,markdown:'原识图稿'}];
  await open(data);const input=document.getElementById('documentProjectPageEditor').querySelector('textarea');
  input.value='教师的逐页修正';input.dispatchEvent(new Event('input'));
  await new Promise(resolve=>setTimeout(resolve,750));
  assert.ok(document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status').textContent.includes('正在保存'));
  const req=next('/api/document-projects/'+data.project_id+'/draft',{detail:'隔离模拟保存冲突'},409);await flush();
  assert.equal(JSON.parse(req.options.body).expected_revision,0);
  assert.equal(JSON.parse(req.options.body).draft.page_edits[0].markdown,'教师的逐页修正');
  const status=document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status').textContent;
  assert.ok(status.includes('保存冲突'));assert.ok(!status.includes('已保存到本地服务'));
  assert.equal(stored().page_edits[0].markdown,'教师的逐页修正');session.clear();
 },
 async specific_local_blockers_precede_generic_legacy_partial(){
  const report={pdf_project_legacy_parse:{status:'partial',partial:true,new_calls:0},
   pdf_local_structure:{status:'manual_review',manual_reasons:['answer_section_ambiguous'],
    page_issues:[{page_number:5,blocked:true,reason_codes:['answer_section_ambiguous']}]}};
  const review=pdfStructureReviewState(report,{partial:true});
  assert.equal(review.incomplete,true);assert.ok(review.lines.some(line=>line.includes('第 5 页')&&line.includes('原版答案分区不唯一')));
  assert.ok(!review.lines.some(line=>line.includes('部分题目或原文范围尚未处理完成')));
  const region=localReport(0,'partial');region.pdf_local_draft.manual_reasons=['question_region_unproved'];
  const regionReview=pdfStructureReviewState(region,{partial:true});
  assert.ok(regionReview.lines.some(line=>line.includes('答案标题之前尚未找到可确认的题干区域')));
 },
 async rebuilding_thirteen_machine_cards_to_nineteen_replaces_unedited_body_and_preserves_teacher(){
  const original=Array.from({length:13},(_,i)=>({...localQuestion(i+1),answer_markdown:'',
   source:'原PDF',image_paths:[],referenced_images:[]}));
  original[12].content='第13题旧机器稿错误吞入14至19题和整卷答案：'+ '原文'.repeat(7500);
  const data=details('rules-rebuilt-project',0,{questions:original,baselineQuestions:original});
  data.has_draft=false;data.has_baseline=true;data.diagnostics=localReport(13,'ready',{status:'completed',new_calls:1});
  await open(data);original.forEach((row,index)=>{const card=document.getElementById('parsed-card-'+index);
   card.querySelector('.card-qtype').value=row.question_type;card.querySelector('.card-difficulty').value=row.difficulty;});
  editProject(document.getElementById('parsed-card-0'),'教师已修正的第1题');
  const rebuilt=Array.from({length:19},(_,i)=>({...localQuestion(i+1),content:'规则v3恢复的第'+(i+1)+'题',
   answer_markdown:'第'+(i+1)+'题原版答案',source:'原PDF',image_paths:[],referenced_images:[]}));
  session.update({phase:'completed',diagnostics:localReport(19,'ready',{status:'completed',new_calls:1})});
  const merged=session.mergeCompletion(rebuilt);
  assert.equal(merged.length,19);assert.ok(merged[12].content===rebuilt[12].content,'unedited swallowed machine body is replaced by rebuilt source');
  assert.equal(merged[12].answer_markdown,'第13题原版答案');assert.equal(merged[12].project_incoming_candidate,undefined);
  assert.equal(merged[0].content,'教师已修正的第1题');assert.equal(merged[0].project_incoming_candidate.content,rebuilt[0].content);
  assert.equal(merged[0].project_incoming_candidate.answer_markdown,'第1题原版答案');
  assert.notEqual(merged[0].project_incoming_candidate.metadata_only,true);
  assert.deepEqual(merged.map(row=>row.source_number),Array.from({length:19},(_,i)=>i+1));session.clear();
 },
 async source_changes_create_full_candidate_and_can_be_adopted_in_local_draft_mode(){
  const {data}=await preview();
  session.update({phase:'completed',diagnostics:localReport(1,'ready',{status:'completed',new_calls:1})});
  for(const patch of [{answer_markdown:'新机器原版答案'}, {source:'新的来源文字'},
    {image_paths:['/static/uploads/tmp/source-new.png']}, {referenced_images:['new-original-figure']}]){
   const fresh=session.mergeCompletion([{...data[0],...patch}]);
   Object.entries(patch).forEach(([field,value])=>assert.deepEqual(fresh[0][field],value,'source field changes must not be treated as metadata-only: '+field));
  }
  editProject(document.getElementById('parsed-card-0'),'教师正文仍需保留');
  const changed={...data[0],content:'本地结构规则恢复的完整正文',answer_markdown:'本地恢复的原版答案',
   source:'修正后的原文来源',image_paths:['/static/uploads/tmp/rebuilt.png'],referenced_images:['image-1']};
  session.update({phase:'completed',diagnostics:localReport(1,'ready',{status:'completed',new_calls:1})});
  const merged=session.mergeCompletion([changed]);assert.equal(merged[0].content,'教师正文仍需保留');
  assert.equal(merged[0].project_incoming_candidate.content,changed.content);assert.notEqual(merged[0].project_incoming_candidate.metadata_only,true);
  show(merged);const panel=document.getElementById('parsed-card-0').querySelector('.document-project-candidate');
  assert.equal(panel.querySelector('button').textContent,'采用本次候选');
  assert.ok(panel.querySelector('pre').textContent.includes('本地恢复的原版答案'));
  panel.querySelector('button').dispatchEvent(new Event('click'));
  assert.equal(parsedQuestionsData[0].content,changed.content);assert.equal(parsedQuestionsData[0].answer_markdown,changed.answer_markdown);
  assert.deepEqual(parsedQuestionsData[0].image_paths,changed.image_paths);assert.equal(parsedQuestionsData[0].source,changed.source);
  assert.equal(parsedQuestionsData[0].project_card_id,'local-card-1');session.clear();
 },
 async source_formula_advisory_does_not_mark_restored_structure_incomplete(){
  const report=localReport(19,'ready',{status:'completed',new_calls:1});
  report.pdf_local_draft.structure_census={declared_count:19,final_numbers:Array.from({length:19},(_,i)=>i+1),independent_boundaries_proved:true};
  report.pdf_local_draft.page_issues=[{page_number:2,source_number:13,reason_codes:['source_math_delimiter_requires_review'],blocked:false}];
  const review=pdfStructureReviewState(report,{partial:false});assert.equal(review.incomplete,false);
  assert.ok(review.lines.some(line=>line.includes('第 2 页')&&line.includes('公式定界符需核对')));
  const q=localQuestion(13);q.source_review={required:true,blocking:false,reasons:['source_math_delimiter_requires_review']};
  show([q]);assert.ok(document.getElementById('parsed-card-0').querySelector('.card-source-review-reasons').textContent.includes('请对照原页检查该题公式'));
  report.pdf_local_draft.status='partial';report.pdf_local_draft.manual_reasons=['source_structure_census_requires_review'];
  const incomplete=pdfStructureReviewState(report,{partial:true});assert.equal(incomplete.incomplete,true);
  assert.ok(incomplete.lines.some(line=>line.includes('题目数量或边界尚未完成独立核对')));
 },
 async generic_image_verification_diagnostic_keeps_formula_and_page_specific_source_reasons(){
  const q=localQuestion(13);q.source_review={required:true,blocking:false,reasons:[
   'source_math_delimiter_requires_review','PDF 第2页：配图位置需要核对','PDF 第6页：图片裁剪范围需要核对',
   '存在配图引用、位置或裁剪风险']};
  const protectedFields=()=>JSON.stringify([q.content,q.answer_markdown,q.question_type,q.difficulty,q.source_review]);
  const frozen=protectedFields();show([q]);const report=localReport(19,'ready',{status:'completed',new_calls:0});
  report.pdf_source_verification={status:'no_candidates',calls:0,checked:0,pending:1,
   skipped_reasons:[{question_index:0,reason:'存在配图引用、位置或裁剪风险'}]};
  for(let pass=0;pass<2;pass++){
   renderSourceIntegrityReport(report);
   const text=document.getElementById('parsed-card-0').querySelector('.card-source-review-reasons').textContent;
   assert.ok(text.includes('请对照原页检查该题公式'));
   assert.ok(text.includes('PDF 第2页：配图位置需要核对'));assert.ok(text.includes('PDF 第6页：图片裁剪范围需要核对'));
   assert.equal(text.split('存在配图引用、位置或裁剪风险').length-1,1,'generic and original duplicate reasons are shown once');
   assert.equal(pdfStructureReviewState(report,{partial:false}).incomplete,false);
  }
  assert.equal(protectedFields(),frozen,'source explanations are rendered without rewriting the question or raw review reasons');
  assert.equal(requests.length,0,'mixed source evidence never dispatches a model');
 }
};
"""
    run_node(script + '\nlocalScenarios[' + json.dumps(scenario) + "]().catch(error=>{console.error(error);process.exitCode=1});")
