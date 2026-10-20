"""Figure-review progress is optional evidence, separate from saved teacher edits."""

import json

import pytest

from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


@pytest.mark.parametrize('scenario', [
    'local_geometry_is_not_a_visual_pass',
    'unknown_counts_are_not_zero_or_a_pass',
    'safe_reason_codes_have_chinese_messages_without_raw_responses',
    'progress_preserves_save_state_and_pending_controls',
    'pending_figures_locate_physical_pages_and_proven_questions',
    'unbound_or_ambiguous_figures_require_explicit_question_selection',
    'stale_pending_actions_do_not_open_a_different_question',
    'teacher_edits_keep_the_automatic_record_as_history',
    'pending_or_unavailable_figures_do_not_block_text_import',
    'clear_removes_the_previous_projects_figure_record',
])
def test_pdf_figure_review_frontend(project_script, scenario):
    script = project_script + r"""
function figureReport(overrides={}) {
 return {pdf_local_draft:{persisted:true,question_count:2},pdf_figure_review:{
  status:'completed',total:4,checked:4,verified:2,corrected:1,pending:2,reused:1,calls:3,
  items:[{id:'f3',page_number:3,slot_id:'p3-s1',status:'verified',reason:''},
   {id:'f7',page_number:7,slot_id:'p7-s1',status:'pending',reason:'crop_not_accepted',source_number:20}],...overrides}};
}
function withPages() {
 window.pdfPageImages=['/static/uploads/tmp/page-three.png','/static/uploads/tmp/page-seven.png'];
 window.pdfPageNumbers=[3,7];
}
function panel(){return document.getElementById('pdfFigureReviewPanel');}
const figureScenarios={
 async local_geometry_is_not_a_visual_pass(){
  const text=pdfLayoutReportSummary({pages_checked:9,figures_extracted:4,figures_attached:4,visual_calls:0});
  assert.ok(text.includes('本地图框检查 9 页'));assert.ok(!text.includes('已核对 9 页'));
  assert.ok(text.includes('不代表已通过视觉核验'));
  const summary=pdfFigureReviewSummary(figureReport());
  assert.ok(summary.includes('配图通过 2/4'));assert.ok(summary.includes('已取得核验结论 4/4'));
  assert.ok(summary.includes('重裁 1'));assert.ok(summary.includes('待补 2'));
  assert.ok(!summary.includes('配图通过 4/4'));
 },
 async unknown_counts_are_not_zero_or_a_pass(){
  const summary=pdfFigureReviewSummary({pdf_figure_review:{status:'running',checked:4}});
  assert.ok(summary.includes('通过进度暂未提供'));assert.ok(summary.includes('待补数量暂未提供'));
  assert.ok(!summary.includes('0/0'));assert.ok(!summary.includes('待补 0'));
  const invalid=pdfFigureReviewSummary(figureReport({verified:5,total:4}));
  assert.ok(invalid.includes('通过进度暂未提供'));assert.ok(!invalid.includes('5/4'));
 },
 async safe_reason_codes_have_chinese_messages_without_raw_responses(){
  const expected={not_reviewed:'等待',provider_unavailable:'未配置',unknown_request_outcome:'不会自动重复',
   budget_limit:'额度已用完',crop_not_accepted:'未通过',recrop_not_accepted:'仍未通过',
   anchor_unresolved:'无法唯一对应',reviewed_asset_changed:'图片发生变化',candidate_overlaps_neighbor:'重叠'};
  for(const [code,text] of Object.entries(expected)){
   const reason=pdfFigureReviewReason(code);assert.ok(reason.message.includes(text));assert.equal(reason.code,code);
  }
  for(const code of ['http_error','http_503','invalid_json','protocol_error','PdfFigureReviewError','JSONDecodeError','ReadTimeout']){
   const reason=pdfFigureReviewReason(code);assert.ok(reason.message.includes('请求或回复未能完成检查'));assert.equal(reason.code,code);
  }
  await open();withPages();
  const report=figureReport({items:[{page_number:7,slot_id:'p7-s1',status:'pending',reason:'unknown_request_outcome'}]});
  const before=JSON.stringify(report);renderSourceIntegrityReport(report);
  const line=panel().querySelector('.pdf-figure-review-reason');
  assert.ok(line.textContent.includes('上次配图请求的响应尚未确定'));assert.ok(!line.textContent.includes('unknown_request_outcome'));
  assert.equal(line.title,'原因代码：unknown_request_outcome');assert.equal(JSON.stringify(report),before,'checked and all original counts stay unchanged');
  report.pdf_figure_review.items[0].reason='raw response {"private":"do not display"} <script>bad()</script>';
  renderSourceIntegrityReport(report);
  const unknown=panel().querySelector('.pdf-figure-review-reason');
  assert.ok(!unknown.textContent.includes('private'));assert.ok(!unknown.textContent.includes('script'));
  assert.equal(unknown.title,undefined);assert.equal(unknown.querySelector('script'),null);
 },
 async progress_preserves_save_state_and_pending_controls(){
  await open();withPages();
  const report=figureReport();session.update({phase:'layout_analysis',diagnostics:report,log:'核验开始'});
  const status=document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status');
  renderSourceIntegrityReport(report);
  const originalPanel=panel(),row=panel().querySelector('.pdf-figure-review-item');
  const select=panel().querySelector('.pdf-figure-review-question');
  select.value='0';select.dispatchEvent(new Event('change'));
  for(let i=0;i<3;i++){
   const progress=figureReport({checked:i,calls:i});
   session.update({phase:'layout_analysis',diagnostics:progress,log:'核验进度'+i});
   renderPdfFigureReviewPanel(progress);
   assert.equal(panel(),originalPanel);assert.equal(panel().querySelector('.pdf-figure-review-item'),row);
   assert.equal(panel().querySelector('.pdf-figure-review-question'),select);assert.equal(select.value,'0');
  }
  await new Promise(resolve=>setTimeout(resolve,750));
  assert.equal(requests.length,0,'progress must not PUT a draft or call a model');
  assert.equal(stored().projectLocalDirty,false);
  assert.equal(document.getElementById('documentImportRecoveryPanel').querySelector('.document-project-save-status'),status);
  assert.equal(panel().querySelector('.pdf-figure-review-receipt').textContent,'首次正文稿已保存。');
 },
 async pending_figures_locate_physical_pages_and_proven_questions(){
  const qs=[{...question('原题10'),project_card_id:'q10',source_number:10},
   {...question('原题20'),project_card_id:'q20',source_number:20}];
  await open(details('figure-pages',1,{questions:qs,baselineQuestions:qs}));withPages();
  renderSourceIntegrityReport(figureReport());
  const row=panel().querySelector('.pdf-figure-review-item');
  assert.equal(row.dataset.pageNumber,'7');assert.equal(row.querySelector('a').href,'/static/uploads/tmp/page-seven.png');
  assert.equal(row.querySelector('select'),null);assert.equal(row.querySelector('script'),null);
  assert.ok(row.querySelector('p').textContent.includes('原配图尚未通过核验'));
  assert.equal(row.querySelector('p').title,'原因代码：crop_not_accepted');
  const calls=[];openPdfCropModalForQuestion=(index,options)=>calls.push({index,options});
  row.querySelector('button').dispatchEvent(new Event('click'));
  assert.deepEqual(calls,[{index:1,options:{pageNumber:7,slotId:'p7-s1'}}]);
  assert.equal(pdfFigureReviewPageIndex(7),1);assert.equal(pdfFigureReviewPageIndex(2),-1);
 },
 async unbound_or_ambiguous_figures_require_explicit_question_selection(){
  const qs=[{...question('甲'),source_number:20,project_card_id:'a'},
   {...question('乙'),source_number:20,project_card_id:'b'}];
  await open(details('ambiguous-figure',1,{questions:qs,baselineQuestions:qs}));withPages();
  renderSourceIntegrityReport(figureReport());
  const row=panel().querySelector('.pdf-figure-review-item'),select=row.querySelector('select'),button=row.querySelector('button');
  assert.ok(select);assert.equal(button.disabled,true,'never guess the first card');
  const calls=[];openPdfCropModalForQuestion=(index,options)=>calls.push({index,options});
  select.value='1';select.dispatchEvent(new Event('change'));assert.equal(button.disabled,false);
  button.dispatchEvent(new Event('click'));assert.equal(calls[0].index,1);
  assert.equal(requests.length,0,'choosing a crop target does not call a model');
 },
 async stale_pending_actions_do_not_open_a_different_question(){
  await open();withPages();renderSourceIntegrityReport(figureReport());
  const select=panel().querySelector('select'),button=panel().querySelector('button');
  select.value='0';select.dispatchEvent(new Event('change'));
  const calls=[];openPdfCropModalForQuestion=(...args)=>calls.push(args);
  parsedQuestionsGeneration+=1;
  button.dispatchEvent(new Event('click'));assert.deepEqual(calls,[]);
 },
 async teacher_edits_keep_the_automatic_record_as_history(){
  const card=await open();withPages();const report=figureReport();const before=JSON.stringify(report);
  renderSourceIntegrityReport(report);const summary=panel().querySelector('.pdf-figure-review-summary').textContent;
  edit(card,'.card-content-textarea','教师刚修正的正文与手动图片。');
  assert.ok(panel().querySelector('.pdf-figure-review-edited').textContent.includes('后续已手动调整'));
  assert.equal(panel().querySelector('.pdf-figure-review-edited').hidden,false);
  assert.equal(panel().querySelector('.pdf-figure-review-summary').textContent,summary,'manual edits never grant an AI pass');
  assert.equal(JSON.stringify(report),before);assert.equal(card.querySelector('.card-content-textarea').value,'教师刚修正的正文与手动图片。');
 },
 async pending_or_unavailable_figures_do_not_block_text_import(){
  const card=await open();withPages();
  const report=figureReport({status:'disabled',verified:0,pending:4,checked:0,calls:0,
   items:[{id:'missing',page_number:2,slot_id:'p2-s1',status:'unavailable',reason:'原页未取得'}]});
  renderSourceIntegrityReport(report);
  assert.ok(panel().querySelector('.pdf-figure-review-summary').textContent.includes('本次未启用'));
  assert.equal(panel().querySelector('button').disabled,true);
  assert.equal(panel().querySelectorAll('input').length,0,'no mandatory review checkbox');
  assert.equal(validateParsedQuestionBeforeImport(0,{notify:false,focus:false}),true);
  assert.equal(card.querySelector('.card-select-checkbox').disabled,false);
 },
 async clear_removes_the_previous_projects_figure_record(){
  await open();withPages();renderSourceIntegrityReport(figureReport());assert.ok(panel());
  replaceParsedQuestions([],{saveDraft:false});assert.equal(panel(),null);assert.equal(parsedFigureReviewReport,null);
 }
};
"""
    run_node(script + '\nfigureScenarios[' + json.dumps(scenario) + ']().catch(error=>{console.error(error);process.exitCode=1});')
