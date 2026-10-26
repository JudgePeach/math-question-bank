"""Manual PDF crops preserve the chosen answer anchor and concurrent edits."""

import json
from pathlib import Path

import pytest

from test_document_projects_frontend import run_node
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def crop_script(pdf_script):
    source = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
    begin = source.index("function openPdfCropModalForQuestion(")
    opening = source[begin:source.index("function renderPdfPagesThumbnails(", begin)]
    api = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
    assets = api[api.index("window.MathBankImageAssets ="):api.index("// Image references remain")]
    return pdf_script.split("const pdfScenarios =", 1)[0] + assets + opening + r"""
window.ImageLayoutTools={key:path=>path.replace(/^\//,''),normalize:layouts=>JSON.parse(JSON.stringify(layouts))};
Element.prototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end;};
window.MathBankModal={open(){},close(){}};
window.MathBankDocumentImportSession={getProjectId:()=>window.__projectId,saveSoon:()=>{window.__saves+=1;}};
window.__projectId='project-one';window.__saves=0;window.currentPdfTaskId='task-one';
window.tempCroppedPathsThisSession=[];
for(const id of ['pdfCropTargetField','pdfCropTargetText','pdfCropTargetHint','pdfCropModal','pdfCropClearBtn'])create(id);
document.getElementById('pdfCropModal').appendChild(new Element('div'));
function renderPdfPagesThumbnails(){}
function setupPdfCropDrawListeners(){}
function ready(content='题干正文',answer='第一步。\n\n[插图待补：答案几何图]\n\n第二步，继续解答。'){
 window.pdfPageImages=['/static/uploads/tmp/page-three.png','/static/uploads/tmp/page-seven.png'];
 window.pdfPageNumbers=[3,7];window.activePdfCropSession=null;
 const q={project_card_id:'card-a',content,answer_markdown:answer,image_paths:[],source_number:16,
  source_review:{required:true,reasons:['image_pending']}};
 const card=show([q]);return {q,card,content:card.querySelector('.card-content-textarea'),answer:card.querySelector('.card-answer-textarea')};
}
function openAnswer(options={}){openPdfCropModalForQuestion(0,{targetField:'answer_markdown',pageNumber:7,slotId:'p7-s1',slotLabel:'[插图待补：答案几何图]',...options});}
async function resolveCrop(path='/static/uploads/tmp/manual-answer.png'){
 const req=requests.at(-1);assert.equal(req.url,'/api/ai/manual-crop-pdf');
 req.resolve({ok:true,json:async()=>({status:'success',image_path:path})});await flush();await flush();return req;
}
"""


@pytest.mark.parametrize("scenario", [
    "answer_placeholder_is_replaced_at_its_original_position",
    "answer_caret_without_placeholder_preserves_following_solution",
    "explicit_answer_button_preserves_the_previous_caret",
    "field_switch_does_not_move_answer_image_to_stem",
    "duplicate_placeholder_never_guesses_which_occurrence",
    "arbitrary_selected_solution_is_not_deleted",
    "crop_response_cannot_overwrite_edits_to_either_field",
    "crop_response_cannot_cross_card_id_or_project_or_modal_session",
    "duplicate_submit_is_one_request_and_one_reference",
    "recropping_the_same_slot_replaces_the_previous_manual_image",
    "discarded_response_is_registered_for_session_cleanup",
    "selecting_another_page_while_request_runs_is_blocked",
    "figure_report_passes_a_proved_answer_target",
    "clicked_image_url_span_preserves_literal_alt_title_and_other_occurrences",
    "replacement_selection_remains_locked_and_preserves_layout",
    "stale_replacement_span_is_rejected_before_open",
    "replaced_private_reference_is_not_a_visible_attachment",
    "replaced_drawing_detaches_only_after_its_last_field_reference",
    "literal_reference_cannot_resurrect_the_replaced_image",
])
def test_pdf_manual_crop_target_frontend(crop_script, scenario):
    checks = r"""
const cropScenarios={
 async literal_reference_cannot_resurrect_the_replaced_image(){
  const old='/static/uploads/tmp/old.png',next='/static/uploads/tmp/new.png';
  const literal='`![字面图片]('+old+')`';const body=literal+'\n![原图]('+old+')';
  const {q,content}=ready(body,'原答案');q.image_paths=[old];
  const span=window.MathBankImageAssets.spans(body)[0];
  openPdfCropModalForQuestion(0,{replacement:{field:'content',...span}});
  submitPdfCropCoordinates();await resolveCrop(next);
  assert.equal(content.value,literal+'\n![原图]('+next+')');assert.deepEqual(q.image_paths,[next]);
 },
 async clicked_image_url_span_preserves_literal_alt_title_and_other_occurrences(){
  const old='/static/uploads/tmp/old.png',next='/static/uploads/tmp/new.png';
  const literal='`![代码]('+old+')`';
  const body='😀前文'+literal+'\n![图一]('+old+' "原说明")\n![图二](<'+old+'>)\n后文';
  const {q,content,answer}=ready(body,'原答案');q.image_paths=[old];
  const spans=window.MathBankImageAssets.spans(body);assert.equal(spans.length,2);
  openPdfCropModalForQuestion(0,{replacement:{field:'content',...spans[1]}});
  submitPdfCropCoordinates();await resolveCrop(next);
  assert.equal(content.value,body.slice(0,spans[1].start)+next+body.slice(spans[1].end));
  assert.equal(answer.value,'原答案');assert.deepEqual(q.image_paths,[old,next]);
 },
 async replacement_selection_remains_locked_and_preserves_layout(){
  const old='/static/uploads/tmp/old.png',next='/static/uploads/tmp/new.png';
  const body='第一步。\n![原说明]('+old+')\n第二步。';
  const {q,answer}=ready('题干',body);q.image_paths=[old];
  q.image_layouts={[window.ImageLayoutTools.key(old)]:{align:'left',size:'large'}};
  const span=window.MathBankImageAssets.spans(body)[0];
  openPdfCropModalForQuestion(0,{replacement:{field:'answer_markdown',...span}});
  configurePdfCropTarget(window.activePdfCropSession,'content');
  document.getElementById('pdfCropTargetText').setSelectionRange(0,0);
  assert.equal(window.activePdfCropSession.targetField,'answer_markdown');
  assert.equal(document.getElementById('pdfCropTargetField').disabled,true);
  submitPdfCropCoordinates();await resolveCrop(next);
  assert.equal(answer.value,body.replace(old,next));assert.deepEqual(q.image_paths,[next]);
  assert.deepEqual(q.image_layouts,{[window.ImageLayoutTools.key(next)]:{align:'left',size:'large'}});
  assert.equal(q.document_import_manual_crops[0].replaced_image_path,old);
  assert.equal(q.document_import_manual_crops[0].ai_verified,false);
 },
 async stale_replacement_span_is_rejected_before_open(){
  const old='/static/uploads/tmp/old.png';const {q}=ready('![原图]('+old+')','答案');q.image_paths=[old];
  const span=window.MathBankImageAssets.spans(q.content)[0];span.start+=1;
  openPdfCropModalForQuestion(0,{replacement:{field:'content',...span}});
  assert.equal(window.activePdfCropSession,null);assert.ok(toasts.at(-1).includes('插图位置已变化'));
 },
 async replaced_private_reference_is_not_a_visible_attachment(){
  const old='/static/uploads/tmp/old.png',next='/static/uploads/tmp/new.png';
  const {q,content}=ready('![原图]('+old+')','答案');q.image_paths=[old];q.tikz_reference_image_path=old;
  const span=window.MathBankImageAssets.spans(content.value)[0];
  openPdfCropModalForQuestion(0,{replacement:{field:'content',...span}});
  submitPdfCropCoordinates();await resolveCrop(next);
  assert.deepEqual(q.image_paths,[next]);assert.equal(q.tikz_reference_image_path,old);
 },
 async replaced_drawing_detaches_only_after_its_last_field_reference(){
  const old='/static/uploads/tmp/old.png',next='/static/uploads/tmp/new.png';
  for(const repeated of [false,true]){
   const mark='![原图]('+old+')';const {q,content}=ready(mark+(repeated?'\n'+mark:''),'答案');q.image_paths=[old];
   q.content_tikz_assets=[{image_path:old,tikz_code:'原绘图源码',reference_image_path:old}];q.tikz_code='原绘图源码';
   const span=window.MathBankImageAssets.spans(content.value)[0];
   openPdfCropModalForQuestion(0,{replacement:{field:'content',...span}});
   submitPdfCropCoordinates();await resolveCrop(next);
   assert.equal(q.content_tikz_assets.length,repeated?1:0);assert.equal(q.tikz_code,repeated?'原绘图源码':'');
   assert.deepEqual(q.image_paths,repeated?[old,next]:[next]);
  }
 },
 async answer_placeholder_is_replaced_at_its_original_position(){
  const {q,content,answer}=ready();const before=answer.value,report={pdf_figure_review:{total:1,verified:0,pending:1,checked:1,calls:2,items:[]}};
  renderSourceIntegrityReport(report);const record=JSON.stringify(report);openAnswer();
  const position=document.getElementById('pdfCropTargetText');assert.equal(position.value.slice(position.selectionStart,position.selectionEnd),'[插图待补：答案几何图]');
  submitPdfCropCoordinates();const req=await resolveCrop();assert.equal(JSON.parse(req.options.body).page_index,6);
  assert.equal(content.value,'题干正文');assert.equal(answer.value,before.replace('[插图待补：答案几何图]','![插图](/static/uploads/tmp/manual-answer.png)'));
  assert.equal(q.answer_markdown,answer.value);assert.deepEqual(q.image_paths,['/static/uploads/tmp/manual-answer.png']);
  assert.equal(q.document_import_manual_crops[0].target_field,'answer_markdown');assert.equal(q.document_import_manual_crops[0].page_number,7);
  assert.equal(q.document_import_source_edited,true);assert.equal(JSON.stringify(report),record,'manual insert never grants a model approval');
 },
 async answer_caret_without_placeholder_preserves_following_solution(){
  const {content,answer}=ready('题干。','第一步。\n\n第二步。\n\n最后答案。');
  answer.setSelectionRange(6,6);answer.dispatchEvent(new Event('click'));openPdfCropModalForQuestion(0);
  assert.equal(document.getElementById('pdfCropTargetField').value,'answer_markdown');
  submitPdfCropCoordinates();await resolveCrop();assert.equal(content.value,'题干。');
  assert.ok(answer.value.indexOf('![插图]')<answer.value.indexOf('第二步。'));assert.ok(answer.value.endsWith('最后答案。'));
 },
 async explicit_answer_button_preserves_the_previous_caret(){
  const {answer}=ready('题干。','甲步骤。乙步骤。');answer.setSelectionRange(4,4);answer.dispatchEvent(new Event('click'));
  openPdfCropModalForQuestion(0,{targetField:'answer_markdown'});submitPdfCropCoordinates();await resolveCrop();
  assert.ok(answer.value.startsWith('甲步骤。\n\n![插图]'));assert.ok(answer.value.endsWith('乙步骤。'));
 },
 async field_switch_does_not_move_answer_image_to_stem(){
  const {content,answer}=ready('题干。','解答A。解答B。');openPdfCropModalForQuestion(0,{targetField:'content'});
  const select=document.getElementById('pdfCropTargetField');select.value='answer_markdown';select.onchange();
  document.getElementById('pdfCropTargetText').setSelectionRange(4,4);submitPdfCropCoordinates();await resolveCrop();
  assert.equal(content.value,'题干。');assert.ok(answer.value.startsWith('解答A。\n\n![插图]'));assert.ok(answer.value.endsWith('解答B。'));
 },
 async duplicate_placeholder_never_guesses_which_occurrence(){
  const marker='[插图待补：答案几何图]';const {answer}=ready('题干',marker+'\n中间'+marker);openAnswer();
  const position=document.getElementById('pdfCropTargetText');assert.equal(position.selectionStart,answer.value.length);assert.equal(position.selectionEnd,answer.value.length);
  position.setSelectionRange(0,marker.length);submitPdfCropCoordinates();await resolveCrop();
  assert.equal((answer.value.match(/\[插图待补/g)||[]).length,1);assert.ok(answer.value.startsWith('![插图]'));assert.ok(answer.value.endsWith(marker));
 },
 async arbitrary_selected_solution_is_not_deleted(){
  const {answer}=ready('题干','甲步骤。重要公式与文字。乙步骤。');openAnswer();
  document.getElementById('pdfCropTargetText').setSelectionRange(4,12);submitPdfCropCoordinates();await resolveCrop();
  assert.ok(answer.value.includes('重要公式与文字。乙步骤。'));
 },
 async crop_response_cannot_overwrite_edits_to_either_field(){
  for(const target of ['content','answer']){
   const state=ready();openAnswer();submitPdfCropCoordinates();state[target].value+='教师临时修改';state[target].dispatchEvent(new Event('input'));
   const before=[state.content.value,state.answer.value];await resolveCrop();
   assert.deepEqual([state.content.value,state.answer.value],before);assert.deepEqual(state.q.image_paths,[]);
   assert.ok(toasts.some(text=>text.includes('已保留当前编辑')));
  }
 },
 async crop_response_cannot_cross_card_id_or_project_or_modal_session(){
  for(const kind of ['card','project','modal']){
   const state=ready();openAnswer();submitPdfCropCoordinates();
   if(kind==='card')state.q.project_card_id='new-card';
   if(kind==='project')window.__projectId='other-project';
   if(kind==='modal')openAnswer();
   const before=state.answer.value;await resolveCrop();assert.equal(state.answer.value,before);assert.deepEqual(state.q.image_paths,[]);
   window.__projectId='project-one';
  }
 },
 async duplicate_submit_is_one_request_and_one_reference(){
  const {answer}=ready();openAnswer();submitPdfCropCoordinates();submitPdfCropCoordinates();assert.equal(requests.length,1);
  await resolveCrop();assert.equal((answer.value.match(/manual-answer.png/g)||[]).length,1);
 },
 async recropping_the_same_slot_replaces_the_previous_manual_image(){
  const {q,answer}=ready();openAnswer();submitPdfCropCoordinates();await resolveCrop();
  openAnswer();submitPdfCropCoordinates();await resolveCrop('/static/uploads/tmp/manual-new.png');
  assert.ok(!answer.value.includes('manual-answer.png'));assert.equal((answer.value.match(/manual-new.png/g)||[]).length,1);
  assert.deepEqual(q.image_paths,['/static/uploads/tmp/manual-new.png']);assert.equal(q.document_import_manual_crops.length,1);
 },
 async discarded_response_is_registered_for_session_cleanup(){
  ready();openAnswer();submitPdfCropCoordinates();closePdfCropModal();await resolveCrop('/static/uploads/tmp/discarded.png');
  assert.ok(window.tempCroppedPathsThisSession.includes('/static/uploads/tmp/discarded.png'));assert.deepEqual(parsedQuestionsData[0].image_paths,[]);
 },
 async selecting_another_page_while_request_runs_is_blocked(){
  ready();openAnswer();submitPdfCropCoordinates();loadPdfCropPage(0);assert.equal(activePageIndex,1);await resolveCrop();
 },
 async figure_report_passes_a_proved_answer_target(){
  const {q}=ready();renderSourceIntegrityReport({pdf_figure_review:{total:1,verified:0,pending:1,checked:1,calls:2,
   items:[{page_number:7,slot_id:'p7-s1',status:'pending',source_number:16,target_field:'answer_markdown',slot_label:'[插图待补：答案几何图]'}]}});
  document.getElementById('pdfFigureReviewPanel').querySelector('button').dispatchEvent(new Event('click'));
  assert.equal(window.activePdfCropSession.targetField,'answer_markdown');assert.equal(window.activePdfCropSession.slotLabel,'[插图待补：答案几何图]');
  assert.equal(window.activePdfCropSession.question,q);
 }
};
"""
    run_node(crop_script + checks + "\ncropScenarios[" + json.dumps(scenario)
             + "]().catch(error=>{console.error(error);process.exitCode=1});")
