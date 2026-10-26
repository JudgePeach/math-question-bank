"""Explicit per-question refinement keeps quick suggestions until valid success."""
import json
import pytest

from test_ai_knowledge_points_actions_frontend import run


PROFILES = r"""
const quick={...result,placement_valid:true,version_code:'A',classification_diagnostics:{policy:{mode:'quick'}}};
const precise={...quick,chapter:'精细章',version_code:'A',question_form:'choice',
  classification_diagnostics:{policy:{mode:'precise'}},knowledge_points:[
    {code:'1859',name:'精细新点',role:'main',status:'confirmed',evidence_source:'content',evidence:'求数列的和',reason:'直接目标'}]};
async function quickPreview(data=quick){openClassifyModal();const work=runAIClassify();reply(requests.at(-1),data);await work;}
"""


def test_refinement_is_explicit_same_source_and_single_request_without_apply_or_save():
    run(r"""
      await quickPreview();
      assert.equal(requests.length,1);assert.equal(requests[0].options.body.get('quality'),null);
      assert.equal(document.getElementById('classifyRefineButton').disabled,false);
      const before=temporaryClassifyData;
      const work=refineAIClassify(),request=requests.at(-1);
      assert.equal(requests.length,2);assert.equal(request.options.body.get('quality'),'precise');
      for(const field of ['content','answer_markdown','review_markdown','expected_version_code'])
        assert.equal(request.options.body.get(field),requests[0].options.body.get(field));
      assert.equal(await refineAIClassify(),false);assert.equal(await runAIClassify(),false);
      assert.equal(requests.length,2);assert.equal(temporaryClassifyData,before);
      assert.ok(document.getElementById('classifyResultMode').textContent.includes('快速建议'));
      assert.equal(document.getElementById('classifyRefineButton')['aria-busy'],'true');
      assert.equal(document.getElementById('classifyApplyButton').disabled,true);
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,true);
      applyClassifyKnowledgePointsOnly();applyClassifyRecommendation();
      assert.equal(KnowledgePointState.snapshot().length,0);assert.equal(saves.length,0);
      reply(request,precise);await work;
      assert.equal(temporaryClassifyData.classification_diagnostics.policy.mode,'precise');
      assert.equal(document.getElementById('classifyResultMode').textContent,'本次结果：精细');
      assert.equal(document.getElementById('classifyRefineButton').disabled,true);
      assert.equal(document.getElementById('classifyRefineButton')['aria-busy'],'false');
      assert.equal(document.getElementById('classifyApplyButton').disabled,true,'choice still needs manual type');
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,false);
      selectClassifiedChoiceType('multi_choice');
      assert.equal(document.getElementById('classifyApplyButton').disabled,false);
      assert.equal(saves.length,0);assert.equal(requests.every(r=>r.url==='/api/ai/classify'),true);
    """, PROFILES)


@pytest.mark.parametrize('outcome', ['network','error_json','http_error','quick_mode','missing_mode',
                                     'missing_version','missing_points','invalid_question_form',
                                     'missing_placement_flag','invalid_placement_flag','abort'])
def test_failed_refinement_retains_quick_data_and_selections(outcome):
    finish={
        'network': "request.reject(new Error('offline'));",
        'error_json': "reply(request,{status:'error',message:'模型失败'});",
        'http_error': "request.resolve({ok:false,status:500,json:async()=>precise});",
        'quick_mode': "reply(request,{...precise,classification_diagnostics:{policy:{mode:'quick'}}});",
        'missing_mode': "reply(request,{...precise,classification_diagnostics:{}});",
        'missing_version': "reply(request,{...precise,version_code:undefined});",
        'missing_points': "reply(request,{...precise,knowledge_points:undefined});",
        'invalid_question_form': "reply(request,{...precise,question_form:'single_choice'});",
        'missing_placement_flag': "reply(request,{...precise,placement_valid:undefined});",
        'invalid_placement_flag': "reply(request,{...precise,placement_valid:'false'});",
        'abort': "request.reject(Object.assign(new Error('cancelled'),{name:'AbortError'}));",
    }[outcome]
    run(r"""
      await quickPreview({...quick,question_form:'choice'});selectClassifiedChoiceType('single_choice');
      temporaryClassifyKnowledgePoints[0].classification_selected=false;
      temporaryClassifyKnowledgePoints[0].role='main';
      const before=temporaryClassifyData,points=JSON.stringify(temporaryClassifyKnowledgePoints);
      const work=refineAIClassify(),request=requests.at(-1);
    """+finish+r"""
      await work;
      assert.equal(temporaryClassifyData,before);assert.equal(JSON.stringify(temporaryClassifyKnowledgePoints),points);
      assert.equal(temporaryClassifyQuestionType,'single_choice');
      assert.equal(document.getElementById('classifyResultMode').textContent,'本次结果：快速');
      assert.equal(document.getElementById('classifyRefineButton').disabled,false);
      assert.equal(document.getElementById('classifyApplyButton').disabled,false);
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,true);
      assert.equal(requests.length,2);assert.equal(saves.length,0);assert.equal(KnowledgePointState.snapshot().length,0);
    """,PROFILES)


def test_cancel_keeps_quick_preview_and_late_response_cannot_replace_fresh_refinement():
    run(r"""
      await quickPreview();const before=temporaryClassifyData;
      const oldWork=refineAIClassify(),old=requests.at(-1);
      cancelAIClassifyRefinement();
      assert.equal(old.options.signal.aborted,true);assert.equal(temporaryClassifyData,before);
      assert.equal(classifyRefineInFlight,false);
      assert.equal(document.getElementById('classifyResultMode').textContent,'本次结果：快速');
      const freshWork=refineAIClassify(),fresh=requests.at(-1);
      reply(old,{...precise,chapter:'迟到旧结果'});await oldWork;
      assert.equal(temporaryClassifyData,before);assert.equal(classifyRefineInFlight,true);
      reply(fresh,precise);await freshWork;
      assert.equal(temporaryClassifyData.chapter,'精细章');assert.equal(requests.length,3);
      assert.equal(saves.length,0);
    """,PROFILES)


def test_render_failure_rolls_back_complete_quick_state_without_false_precise_success():
    run(r"""
      await quickPreview({...quick,question_form:'choice'});selectClassifiedChoiceType('single_choice');
      temporaryClassifyKnowledgePoints[0].classification_selected=false;
      temporaryClassifyKnowledgePoints[0].role='main';
      const before=temporaryClassifyData,points=temporaryClassifyKnowledgePoints;
      const work=refineAIClassify(),request=requests.at(-1);
      const render=renderClassifyKnowledgePoints;
      renderClassifyKnowledgePoints=()=>{temporaryClassifyKnowledgePoints=[{code:'broken'}];throw new Error('render broke');};
      reply(request,precise);await work;renderClassifyKnowledgePoints=render;
      assert.equal(temporaryClassifyData,before);assert.equal(temporaryClassifyKnowledgePoints,points);
      assert.equal(temporaryClassifyQuestionType,'single_choice');
      assert.equal(document.getElementById('classifyResultMode').textContent,'本次结果：快速');
      assert.equal(document.getElementById('classifyRefineButton').disabled,false);
      assert.equal(document.getElementById('classifyApplyButton').disabled,false);
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,true);
      assert.equal(saves.length,0);
    """,PROFILES)


def test_server_version_conflict_invalidates_old_quick_even_before_local_version_updates():
    run(r"""
      await quickPreview();const before=temporaryClassifyData,points=KnowledgePointState.snapshot();
      const work=refineAIClassify(),request=requests.at(-1);
      request.resolve({ok:false,status:409,json:async()=>({status:'error',message:'教材版本变化'})});await work;
      assert.equal(window.activeCurriculumVersion,'A','another window changed the server, not this client');
      assert.equal(temporaryClassifyData,before);assert.equal(isAIClassifyContextCurrent(temporaryClassifyContext),false);
      assert.ok(document.getElementById('classifyResultMode').textContent.includes('教材版本已变化'));
      assert.equal(document.getElementById('classifyRefineButton').disabled,true);
      assert.equal(document.getElementById('classifyApplyButton').disabled,true);
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,true);
      selectClassifiedChoiceType('multi_choice');assert.equal(document.getElementById('classifyApplyButton').disabled,true);
      applyClassifyKnowledgePointsOnly();applyClassifyRecommendation();
      assert.equal(temporaryClassifyData,before);assert.deepEqual(KnowledgePointState.snapshot(),points);
      assert.equal(await refineAIClassify(),false);assert.equal(await runAIClassify(),false);
      assert.equal(requests.length,2);assert.equal(saves.length,0);
      window.activeCurriculumVersion='B';const fresh=runAIClassify();
      assert.equal(requests.at(-1).options.body.get('expected_version_code'),'B');
      reply(requests.at(-1),{...quick,version_code:'B'});await fresh;
      assert.equal(temporaryClassifyData.version_code,'B');assert.equal(document.getElementById('classifyRefineButton').disabled,false);
    """,PROFILES)


def test_cancelled_late_409_cannot_invalidate_the_restored_quick_context():
    run(r"""
      await quickPreview();const before=temporaryClassifyData;
      const work=refineAIClassify(),request=requests.at(-1);cancelAIClassifyRefinement();
      request.resolve({ok:false,status:409,json:async()=>({status:'error'})});await work;
      assert.equal(temporaryClassifyData,before);
      assert.equal(isAIClassifyContextCurrent(temporaryClassifyContext),true);
      assert.equal(document.getElementById('classifyResultMode').textContent,'本次结果：快速');
      assert.equal(document.getElementById('classifyRefineButton').disabled,false);
      assert.equal(requests.length,2);assert.equal(saves.length,0);
    """,PROFILES)


@pytest.mark.parametrize('end',['failure','cancel','success'])
def test_pending_preview_is_readonly_and_restores_its_original_disabled_flags(end):
    finish={'failure':"request.reject(new Error('offline'));await work;",
            'cancel':"cancelAIClassifyRefinement();reply(request,precise);await work;",
            'success':"reply(request,precise);await work;"}[end]
    run(r"""
      await quickPreview({...quick,question_form:'choice'});selectClassifiedChoiceType('single_choice');
      const input=document.getElementById('classifyKnowledgePoints').children[0].children[0].children[0];
      const role=document.getElementById('classifyKnowledgePoints').children[0].children[0].children[2];
      input.disabled=true;role.disabled=false;
      document.getElementById('classifyKnowledgePoints').querySelectorAll=()=>[input,role];
      const work=refineAIClassify(),request=requests.at(-1);
      assert.equal(input.disabled,true);assert.equal(role.disabled,true);
      assert.equal(document.getElementById('classifySingleChoiceBtn').disabled,true);
      assert.equal(document.getElementById('classifyMultiChoiceBtn').disabled,true);
      selectClassifiedChoiceType('multi_choice');assert.equal(temporaryClassifyQuestionType,'single_choice');
      input.checked=false;input.events.change();role.value='main';role.events.change();
      assert.equal(input.checked,true);assert.equal(role.value,'auxiliary');
      assert.equal(temporaryClassifyKnowledgePoints[0].classification_selected,undefined);
      assert.equal(temporaryClassifyKnowledgePoints[0].role,'auxiliary');
    """+finish+r"""
      assert.equal(input.disabled,true);assert.equal(role.disabled,false);
      assert.equal(document.getElementById('classifySingleChoiceBtn').disabled,false);
      assert.equal(document.getElementById('classifyMultiChoiceBtn').disabled,false);
      assert.equal(classifyRefineLockedControls.length,0);
    """,PROFILES)


@pytest.mark.parametrize('mutation',[
    "document.getElementById('editContent').value+='改'",
    "document.getElementById('editAnswerMarkdown').value+='改'",
    "document.getElementById('editReview').value+='改'",
    "document.getElementById('editQType').value='multi_choice'",
    "document.getElementById('editChapter').value='人工章'",
    "KnowledgePointState.add({code:'1796',name:'人工点'})",
    "window.activeCurriculumVersion='B'",
    "EditorState.useQuestion({id:99})",
])
def test_refinement_never_adopts_after_source_version_or_point_revision_changes(mutation):
    run(r"""
      await quickPreview();const before=temporaryClassifyData;
      const work=refineAIClassify(),request=requests.at(-1);
    """+mutation+r""";
      const fields=['editContent','editAnswerMarkdown','editReview','editQType','editChapter']
        .map(id=>document.getElementById(id).value),points=KnowledgePointState.snapshot();
      reply(request,precise);await work;
      assert.equal(temporaryClassifyData,before);assert.notEqual(temporaryClassifyData.chapter,'精细章');
      assert.equal(document.getElementById('classifyRefineButton').disabled,true);
      assert.ok(document.getElementById('classifyResultMode').textContent.includes('已失效'));
      applyClassifyKnowledgePointsOnly();applyClassifyRecommendation();
      assert.deepEqual(KnowledgePointState.snapshot(),points);
      assert.deepEqual(['editContent','editAnswerMarkdown','editReview','editQType','editChapter']
        .map(id=>document.getElementById(id).value),fields);
      assert.equal(saves.length,0);
    """,PROFILES)


def test_closing_reopening_or_new_analysis_rejects_old_refine_response():
    run(r"""
      await quickPreview();const oldWork=refineAIClassify(),old=requests.at(-1);
      closeClassifyModal();openClassifyModal();const freshWork=runAIClassify(),fresh=requests.at(-1);
      reply(old,precise);await oldWork;assert.equal(temporaryClassifyData,null);
      reply(fresh,{...quick,chapter:'新快速结果'});await freshWork;
      assert.equal(temporaryClassifyData.chapter,'新快速结果');
      assert.equal(document.getElementById('classifyResultMode').textContent,'本次结果：快速');
      assert.equal(saves.length,0);
    """,PROFILES)


@pytest.mark.parametrize('mode',[None,'precise','unknown'])
def test_refine_is_unavailable_without_an_explicit_quick_result(mode):
    diagnostic={} if mode is None else {'policy':{'mode':mode}}
    run(r"""
      openClassifyModal();assert.equal(await refineAIClassify(),false);assert.equal(requests.length,0);
      const work=runAIClassify();
      reply(requests.at(-1),{...result,classification_diagnostics:"""+json.dumps(diagnostic)+r"""});await work;
      assert.equal(await refineAIClassify(),false);assert.equal(requests.length,1);
      assert.equal(document.getElementById('classifyRefineButton').disabled,true);
    """,PROFILES)


def test_precise_points_only_preserves_existing_labels_and_does_not_save():
    run(r"""
      const original={code:'1859',name:'原人工名称',role:'auxiliary',status:'suggested',
        evidence:'已有依据',reason:'人工原理由',origin:{kind:'manual'}};
      KnowledgePointState.hydrate([original]);const before=KnowledgePointState.snapshot();
      await quickPreview();const work=refineAIClassify();
      reply(requests.at(-1),{...precise,knowledge_points:[...precise.knowledge_points,
        {...result.knowledge_points[0],code:'1803'}]});await work;
      applyClassifyKnowledgePointsOnly();
      assert.deepEqual(KnowledgePointState.snapshot()[0],before[0]);
      assert.equal(KnowledgePointState.snapshot()[1].code,'1803');
      timers.forEach(fn=>fn());assert.equal(saves.length,0);
    """,PROFILES)


@pytest.mark.parametrize('question_form',['choice','unknown'])
def test_valid_partial_precise_placement_still_allows_points_only(question_form):
    run(r"""
      document.getElementById('editCompulsory').value='原册';
      document.getElementById('editChapter').value='原章';
      document.getElementById('editQType').value='single_choice';
      await quickPreview();const work=refineAIClassify();
      reply(requests.at(-1),{...precise,placement_valid:false,compulsory:'',chapter:'',knowledge:'',
        question_form:"""+json.dumps(question_form)+r"""});await work;
      assert.equal(temporaryClassifyData.classification_diagnostics.policy.mode,'precise');
      assert.equal(temporaryClassifyQuestionType,null);
      assert.equal(document.getElementById('classifyApplyButton').disabled,true);
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,false);
      applyClassifyKnowledgePointsOnly();
      assert.equal(KnowledgePointState.snapshot()[0].code,'1859');
      assert.deepEqual(['editCompulsory','editChapter','editQType'].map(id=>document.getElementById(id).value),
        ['原册','原章','single_choice']);
      timers.forEach(fn=>fn());assert.equal(saves.length,0);
    """,PROFILES)
