"""Replay old/new project result identities with no models, services, or files."""
import json

import pytest

from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401
from mathbank.document_project_service import project_question_ids


@pytest.mark.parametrize('scenario', ['nineteen_results_keep_teacher_edit', 'out_of_order_progress_cannot_reorder_cards', 'restoring_full_draft_never_reanalyzes','new_path_ignores_stale_block_manual_and_cache_history','partial_groups_keep_numbered_pending_scopes',
    'full_context_schema_without_outer_cache_keeps_pending_numbers',
    'current_run_reused_ocr_does_not_count_previous_runs',
    'current_run_counts_only_current_ocr_attempts_and_retries',
    'unknown_ocr_attempt_run_never_claims_zero',
    'cached_verification_and_layout_calls_are_explicit_history',
    'source_report_and_log_do_not_duplicate_current_ocr_summary'])
def test_project_result_compatibility(project_script, scenario):
    script = project_script + r"""
function numbered(number,content='原题'+number){return {...question(content),source_number:number,project_card_id:'number-'+number};}
const resultScenarios={
 async nineteen_results_keep_teacher_edit(){
  const old=[17,18,19].map(n=>numbered(n));
  await open(details('old-project',4,{questions:old,baselineQuestions:old}));
  editProject(document.getElementById('parsed-card-1'),'教师已校对的第18题 $x+1$');
  const incoming=Array.from({length:19},(_,i)=>numbered(i+1,'重新拆出的第'+(i+1)+'题'));
  const merged=session.mergeCompletion(incoming);
  assert.equal(merged.length,19);assert.deepEqual(merged.map(q=>q.source_number),incoming.map(q=>q.source_number));
  assert.equal(merged[17].content,'教师已校对的第18题 $x+1$');
  assert.equal(merged[17].project_incoming_candidate.content,'重新拆出的第18题');
  assert.equal(merged[16].content,'重新拆出的第17题');assert.equal(merged[18].content,'重新拆出的第19题');
 },
 async out_of_order_progress_cannot_reorder_cards(){
  const old=[17,18,19].map(n=>numbered(n));await open(details('parallel-project',4,{questions:old,baselineQuestions:old}));
  for(const group of [3,1,2])session.update({phase:'ai_splitting',diagnostics:{pdf_parallel_split:{last_completed_group:group}}});
  assert.deepEqual(parsedQuestionsData.map(q=>q.source_number),[17,18,19]);
  const incoming=Array.from({length:19},(_,i)=>numbered(i+1));
  const merged=session.mergeCompletion(incoming);assert.deepEqual(merged.map(q=>q.source_number),Array.from({length:19},(_,i)=>i+1));
  assert.equal(requests.length,0,'progress packets are not analysis or draft-write requests');
 },
 async new_path_ignores_stale_block_manual_and_cache_history(){
  const diag={pdf_project_legacy_parse:{mode:'legacy_executor_cache',reused:true,status:'reused',new_calls:0,executor_calls:0},
   pdf_parallel_split:{schema:'mathbank.pdf-parallel-split.v1',used_parallel:true,status:'used',calls:9,group_count:2,completed_groups:2,reused_groups:0},
   pdf_local_structure:{status:'manual_review',manual_reasons:['nested_table','unclosed_environment']},
   pdf_project_partial:true,pdf_splitting:{calls:9}};
  const review=pdfStructureReviewState(diag,{partial:false});assert.equal(review.incomplete,false);assert.equal(review.manual,false);
  const text=pdfProjectParseReportSummary(diag);assert.ok(text.includes('新增拆题请求 0 次'));assert.ok(text.includes('此前题组结果'));
  assert.ok(!text.includes('请求 9 次'),'cached historic calls are never described as new model requests');
  renderSourceIntegrityReport(diag);assert.ok(document.getElementById('parsedSourceIntegrityReport').textContent!==undefined);
 },
 async partial_groups_keep_numbered_pending_scopes(){
  const diag={pdf_project_legacy_parse:{mode:'legacy_executor_cache',reused:false,status:'partial',new_calls:2,executor_calls:1},
   pdf_parallel_split:{schema:'mathbank.pdf-parallel-split.v1',used_parallel:true,status:'partial',calls:2,group_count:3,completed_groups:1,reused_groups:0,
    groups:[{id:'a',source_numbers:[1,2],status:'completed'},{id:'b',source_numbers:[3,4],status:'failed'},{id:'c',source_numbers:[5,6],status:'pending'}]},
   pdf_local_structure:{status:'manual_review',manual_reasons:['nested_table']}};
  const review=pdfStructureReviewState(diag,{partial:true});assert.equal(review.incomplete,true);assert.equal(review.manual,false);
  assert.ok(review.lines.some(line=>line.includes('3、4')));assert.ok(review.lines.some(line=>line.includes('5、6')));
  assert.ok(!review.lines.join('').includes('嵌套'));assert.ok(pdfProjectParseReportSummary(diag).includes('已完成 1/3 组'));
 },
 async full_context_schema_without_outer_cache_keeps_pending_numbers(){
  const diag={pdf_parallel_split:{schema:'mathbank.pdf-full-context-split.v1',used_parallel:true,status:'partial',
   calls:2,group_count:2,completed_groups:1,reused_groups:0,
   groups:[{id:'one',source_numbers:[1,2,3],status:'completed'},{id:'two',source_numbers:[12,13,14,15,16,17,18,19],status:'failed'}]},
   pdf_local_structure:{status:'manual_review',manual_reasons:['nested_table']}};
  const review=pdfStructureReviewState(diag);assert.equal(review.incomplete,true);assert.equal(review.manual,false);
  assert.ok(review.lines.some(line=>line.includes('12、13、14、15、16、17、18、19')));
  assert.ok(!review.lines.join('').includes('嵌套'));
  assert.ok(review.summary.includes('已完成 1/2 组'));assert.ok(review.summary.includes('本次拆题请求 2 次'));
 },
 async current_run_reused_ocr_does_not_count_previous_runs(){
  const diag={pdf_project_legacy_parse:{reused:true,new_calls:0,status:'reused'},pdf_resume:{run:3,reused_visual_pages:7},
   pdf_vision:{calls:9,retries:2,attempts:[{run:0,attempt:1},{run:0,attempt:2},{run:1,attempt:1}]}};
  const text=pdfProjectParseReportSummary(diag);assert.ok(text.includes('复用 7 页识图缓存；本次新增识图 0 次'));
  assert.ok(text.includes('本次新增拆题请求 0 次'));assert.ok(!text.includes('识图 9 次'));
  const extracted=pdfExtractionReportSummary({full_vision_pages:7},diag.pdf_vision,diag.pdf_resume);
  assert.ok(extracted.includes('本次新增识图 0 次'));assert.ok(!extracted.includes('识别请求 9 次'));
 },
 async current_run_counts_only_current_ocr_attempts_and_retries(){
  const text=pdfCurrentOcrReportSummary({calls:20,retries:8,attempts:[{run:0,attempt:1},{run:1,attempt:2},
    {run:3,attempt:1},{run:3,attempt:2},{run:3,attempt:1,http_started:false}]},{run:3,reused_visual_pages:5});
  assert.ok(text.includes('复用 5 页识图缓存；本次新增识图 2 次，其中失败后重试 1 次'));
  assert.ok(!text.includes('20'));assert.ok(!text.includes('重试 8 次'));
 },
 async unknown_ocr_attempt_run_never_claims_zero(){
  for(const vision of [{calls:7},{calls:7,attempts:[{attempt:1}]},{calls:7,attempts:[{run:'3',attempt:1}]}]){
   const text=pdfCurrentOcrReportSummary(vision,{run:3,reused_visual_pages:7});
   assert.ok(text.includes('复用 7 页识图缓存'));assert.ok(text.includes('次数暂未提供'));
   assert.ok(!text.includes('新增识图 0 次'));assert.ok(!text.includes('新增识图 7 次'));
  }
  assert.equal(pdfCurrentOcrReportSummary(undefined,undefined),'');
 },
 async cached_verification_and_layout_calls_are_explicit_history(){
  const diag={pdf_project_legacy_parse:{reused:true,status:'reused',new_calls:0},pdf_resume:{run:3,reused_split:true},
   pdf_source_verification:{status:'completed',calls:4,checked:19,confirmed:18,pending:1},
   pdf_layout:{pages_checked:7,joint_visual_calls:7,visual_calls:2}};
  renderSourceIntegrityReport(diag);
  const panel=document.getElementById('parsedSourceIntegrityReport');
  const verification=panel.querySelector('.pdf-source-verification-summary').textContent;
  assert.ok(verification.startsWith('此前核验记录：'));assert.ok(verification.includes('当时调用 4 次'));
  assert.ok(!verification.includes('额外调用'));
  assert.ok(pdfLayoutReportSummary(diag.pdf_layout,diag).startsWith('此前图文核对记录：'));
 },
 async source_report_and_log_do_not_duplicate_current_ocr_summary(){
  const diag={pdf_project_legacy_parse:{reused:true,status:'reused',new_calls:0},pdf_resume:{run:3,reused_visual_pages:7},
   pdf_extraction:{native_pages:0,full_vision_pages:7},pdf_vision:{calls:7,attempts:[{run:0,attempt:1}]}};
  renderSourceIntegrityReport(diag);const panel=document.getElementById('parsedSourceIntegrityReport');
  assert.ok(panel.querySelector('.pdf-extraction-summary').textContent.includes('本次新增识图 0 次'));
  assert.ok(!panel.querySelector('.pdf-splitting-summary').textContent.includes('新增识图'));
  logs.length=0;appendSourceIntegrityLog(diag);
  assert.equal(logs.filter(text=>text.includes('本次新增识图')).length,1);
 },
 async restoring_full_draft_never_reanalyzes(){
  const original=Array.from({length:19},(_,i)=>numbered(i+1));
  const draft=JSON.parse(JSON.stringify(original));draft[17].content='已保存的第18题人工稿';draft[17].project_incoming_candidate=original[17];
  const data=details('cached-project',8,{questions:draft,baselineQuestions:original});
  data.latest_result=original;data.latest_result_id='already-finished';await open(data);
  assert.equal(parsedQuestionsData.length,19);assert.equal(parsedQuestionsData[17].content,'已保存的第18题人工稿');
  assert.equal(document.querySelectorAll('#parsedCardsContainer .glass-card').length,19);
  assert.equal(requests.length,0,'opening saved/cache results never resumes or pays for another model request');
 }
};
"""
    run_node(script + '\nresultScenarios[' + json.dumps(scenario) + "]().catch(error=>{console.error(error);process.exitCode=1});")


def test_legacy_position_ids_map_before_frontend_teacher_merge(project_script):
    project_id = '306e1124-1fec-4313-86a3-051fce4d67a7'
    old = [{'source_number': number, 'content': f'原第{number}题', 'answer_markdown': '原答案',
            'project_card_id': f'legacy-position-{index}'} for index, number in enumerate((17, 18, 19))]
    incoming = project_question_ids(project_id,
        [{'source_number': number, 'content': f'新第{number}题', 'answer_markdown': '新答案'}
         for number in range(1, 20)], previous_questions=old)
    script = project_script + '\nconst legacyOld=' + json.dumps(old, ensure_ascii=False) + ';\n'
    script += 'const legacyIncoming=' + json.dumps(incoming, ensure_ascii=False) + ';\n'
    script += '(async()=>{await open(details(' + json.dumps(project_id) + r''',4,
      {questions:legacyOld.map(row=>({...question(row.content),...row})),
       baselineQuestions:legacyOld.map(row=>({...question(row.content),...row}))}));
      editProject(document.getElementById('parsed-card-1'),'第18题人工校对稿');
      const merged=session.mergeCompletion(legacyIncoming.map(row=>({...question(row.content),...row})));
      assert.equal(merged.length,19);assert.equal(merged[17].content,'第18题人工校对稿');
      assert.equal(merged[17].project_card_id,'legacy-position-1');
      assert.equal(merged[17].project_incoming_candidate.source_number,18);
      assert.equal(merged[0].content,'新第1题');assert.notEqual(merged[0].project_card_id,'legacy-position-0');
    })().catch(error=>{console.error(error);process.exitCode=1});'''
    run_node(script)
