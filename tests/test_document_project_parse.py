"""An unchanged legacy executor with private stage caching; no model/app/DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json

import pytest
import requests

from mathbank.document_projects import DocumentProjectStore, ProjectConflict, ProjectCorrupt
from mathbank.document_project_parse import run_project_legacy_parse
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *_a,**_kw:pytest.fail('No model API in cache regressions'))


@pytest.fixture
def setup(tmp_path):
    raw=b'%PDF-unchanged-legacy-source'
    sha=hashlib.sha256(raw).hexdigest()
    store=DocumentProjectStore(tmp_path/'projects')
    curriculum={'必修一':{'3. 函数':['3.1 概念']},'必修二':{'9. 统计':[]}}
    pid=store.create(raw,'原算法试卷.pdf',{},curriculum)['project_id']
    source='1. 已知 $x=1$，求 $x+x$。\n![](/static/uploads/tmp/source.png)'
    return store,pid,source,[{'page_number':1,'markdown':source,'revision':0}],curriculum,sha


def result(*,partial=False,questions=None):
    return {'questions':questions if questions is not None else [
        {'content':'已知 $x=1$，求 $x+x$。\n![](/static/uploads/tmp/source.png)',
         'answer_markdown':'[EXTRACTED_ORIGINAL]$2$。','category_compulsory':'必修一',
         'category_chapter':'3. 函数','classification_review':{'required':True,'original':{'category_chapter':['保留']}},
         'source_review':{'required':True,'reasons':['原有具体来源疑点，不改状态']},'extra_evidence':{'keep':True}}],
        'preserved_indices':frozenset({0}), 'partial':partial,
        'diagnostics':{'historical_calls':7,'range_tuple':(0,10),'set_value':{'A','B'}}}


def run(setup,executor,**kwargs):
    store,pid,source,pages,curriculum,sha=setup
    return run_project_legacy_parse(store,pid,source,pages,curriculum,
        source_document_sha256=sha,executor=executor,**kwargs)


def test_first_result_and_cache_hit_are_exact_legacy_dicts_without_algorithm_changes(setup):
    calls=[];original=result();before=deepcopy(original);diag={}
    def executor(cache):calls.append(1);return original
    first=run(setup,executor,diagnostics=diag)
    assert first==before and original==before and len(calls)==1
    assert diag['pdf_project_legacy_parse']['executor_calls']==1
    cached=run(setup,lambda _cache:pytest.fail('A cache hit cannot invoke the legacy algorithm'),diagnostics=diag)
    assert cached==first and cached is not first
    assert type(cached['preserved_indices']) is frozenset
    assert type(cached['diagnostics']['range_tuple']) is tuple
    assert type(cached['diagnostics']['set_value']) is set
    report=diag['pdf_project_legacy_parse']
    assert report['reused'] and report['new_calls']==0 and report['status']=='reused'
    assert cached['diagnostics']['historical_calls']==7


def test_raw_model_packet_structured_stage_and_final_result_are_separate(setup):
    raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'questions':[{'content':'完整原回包'}]})}}],
         'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
    def executor(cache):
        assert cache.on_response('paper_split',raw,input_binding={'provider_model':'configured-model'})
        assert cache.save_structured('before_postprocess',{'questions':[{'content':'完整原回包'}]})
        assert cache.load_structured('before_postprocess')=={'questions':[{'content':'完整原回包'}]}
        return result()
    run(setup,executor)
    store,pid,*_=setup
    manifest=json.loads((store.root/pid/'manifest.json').read_text())
    names=list(manifest['stages'])
    assert any(v.startswith('legacy-raw-')for v in names)
    assert any(v.startswith('legacy-structured-')for v in names)
    assert any(v.startswith('legacy-accepted-')for v in names)


@pytest.mark.parametrize('change',['source','pages','curriculum_order','assets','algorithm','prompt','validation'])
def test_changed_input_or_validation_version_does_not_borrow_cached_results(setup,change):
    calls=[]
    def executor(cache):calls.append(1);return result()
    run(setup,executor,asset_receipts={'source.png':{'sha256':'a'*64,'size':10}})
    store,pid,source,pages,curriculum,sha=setup
    source,pages,curriculum=source,deepcopy(pages),deepcopy(curriculum)
    options={'asset_receipts':{'source.png':{'sha256':'a'*64,'size':10}}}
    if change=='source':source+='改';pages[0]['markdown']=source
    elif change=='pages':pages[0]['revision']=1
    elif change=='curriculum_order':curriculum=dict(reversed(list(curriculum.items())))
    elif change=='assets':options['asset_receipts']['source.png']['sha256']='b'*64
    elif change=='algorithm':options['algorithm_version']='legacy.v2'
    elif change=='prompt':options['prompt_version']='prompts.v2'
    else:options['validation_version']='validation.v2'
    run_project_legacy_parse(store,pid,source,pages,curriculum,source_document_sha256=sha,executor=executor,**options)
    assert len(calls)==2


def test_same_project_after_restart_has_no_task_id_binding(setup):
    expected=result();run(setup,lambda cache:expected)
    store,pid,source,pages,curriculum,sha=setup
    restarted=DocumentProjectStore(store.root)
    cached=run_project_legacy_parse(restarted,pid,source,pages,curriculum,
        source_document_sha256=sha,executor=lambda _cache:pytest.fail('New runtime task must reuse same project input'))
    assert cached==expected


def test_other_pdf_identity_cannot_execute_or_borrow_this_projects_result(setup):
    run(setup,lambda _cache:result())
    store,pid,source,pages,curriculum,_=setup
    with pytest.raises(ProjectConflict):
        run_project_legacy_parse(store,pid,source,pages,curriculum,source_document_sha256='0'*64,
            executor=lambda _cache:pytest.fail('A different PDF cannot enter this project executor'))


def test_legacy_cache_never_uses_or_deletes_old_block_protocol_stages(setup):
    store,pid,*_=setup
    old={'questions':[{'content':'旧块协议题卡应保留'}]}
    store.save_stage(pid,'pdf_local_structure_result',old,'old-block-input')
    calls=[]
    output=run(setup,lambda cache:(calls.append(1) or result()))
    assert len(calls)==1 and output==result()
    assert store.load_stage(pid,'pdf_local_structure_result','old-block-input')==old


def test_draft_cas_changes_during_execution_reject_final_result_without_another_executor_call(setup):
    store,pid,*_=setup;calls=[]
    def executor(cache):
        calls.append(1);store.save_draft(pid,{'questions':[],'paperTitle':'教师正在修改'},0)
        return result()
    with pytest.raises(ProjectConflict):run(setup,executor,expected_revision=0)
    assert len(calls)==1
    with pytest.raises(ProjectConflict):run(setup,lambda _cache:pytest.fail('Stale CAS before paid work'),expected_revision=0)


def test_saving_question_cards_does_not_invalidate_unchanged_effective_source_cache(setup):
    original=run(setup,lambda _cache:result())
    store,pid,*_=setup
    manual={'questions':[{'content':'教师修改的题卡，不是逐页OCR源稿'}],'paperTitle':'人工标题'}
    store.save_draft(pid,manual,0)
    diagnostics={}
    cached=run(setup,lambda _cache:pytest.fail('Card-only revision must not repeat parsing'),diagnostics=diagnostics)
    assert cached==original
    assert diagnostics['pdf_project_legacy_parse']['new_calls']==0
    assert store.get_draft(pid)['payload']==manual


def test_card_only_save_during_execution_keeps_candidate_and_never_overwrites_manual_draft(setup):
    store,pid,*_=setup;calls=[]
    manual={'questions':[{'content':'并行人工题卡'}]}
    def executor(cache):
        calls.append(1);store.save_draft(pid,manual,0);return result()
    candidate=run(setup,executor)
    assert candidate==result() and calls==[1]
    assert store.get_draft(pid)['payload']==manual
    assert run(setup,lambda _cache:pytest.fail('Completed candidate should be cached'))==candidate


def test_raw_response_replays_after_local_algorithm_or_validation_upgrade_without_new_model(setup):
    request_binding={'payload_sha256':'a'*64,'provider':'siliconflow','model':'Qwen/Qwen3.8-27B'}
    raw={'choices':[{'finish_reason':'stop','message':{'content':'{"questions":[{"content":"原模型题文"}]}'}}],
         'model':'Qwen/Qwen3.8-27B'}
    model_calls=[]
    def executor(cache):
        packet=cache.load_raw_response('paper_split',input_binding=request_binding)
        if packet is None:
            model_calls.append(1);assert cache.on_response('paper_split',raw,input_binding=request_binding)
            packet=raw
        assert packet==raw
        return result()
    run(setup,executor)
    diagnostics={}
    run(setup,executor,algorithm_version='legacy-postprocessing.v2',validation_version='validator.v2',diagnostics=diagnostics)
    assert model_calls==[1]
    assert diagnostics['pdf_project_legacy_parse']['reused_raw_stages']==['paper_split']


def test_raw_latest_index_keeps_all_complete_histories_and_does_not_use_an_unbound_or_different_request(setup):
    binding={'payload_sha256':'a'*64,'provider':'siliconflow','model':'actual-model'}
    packets=[{'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'value':value})}}]} for value in [1,2]]
    def executor(cache):
        assert cache.on_response('split',packets[0],input_binding=binding)
        assert cache.on_response('split',packets[1],input_binding=binding)
        assert cache.load_raw_response('split',input_binding=binding)==packets[1]
        assert cache.load_raw_response('split') is None
        assert cache.load_raw_response('split',input_binding={**binding,'model':'other-model'}) is None
        assert cache.load_raw_response('split',input_binding={**binding,'payload_sha256':'b'*64}) is None
        invalid={'choices':[{'finish_reason':'length','message':{'content':'{"value":3}'}}]}
        assert not cache.on_response('split',invalid,input_binding=binding)
        assert cache.load_raw_response('split',input_binding=binding)==packets[1]
        return result()
    run(setup,executor)
    store,pid,*_=setup
    manifest=json.loads((store.root/pid/'manifest.json').read_text())
    assert len([name for name in manifest['stages'] if name.startswith('legacy-raw-') and not name.startswith('legacy-raw-index-')])==2
    assert len([name for name in manifest['stages'] if name.startswith('legacy-raw-index-')])==1


def test_prompt_version_change_cannot_replay_raw_packet(setup):
    binding={'payload_sha256':'a'*64,'model':'actual-model'}
    raw={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
    run(setup,lambda cache:(cache.on_response('split',raw,input_binding=binding) and result()))
    def executor(cache):
        assert cache.load_raw_response('split',input_binding=binding) is None
        return result()
    run(setup,executor,prompt_version='changed-actual-prompt.v2')


def test_cancellation_before_hit_or_after_executor_never_saves_or_retries(setup):
    cancelled=[False];calls=[]
    def check():
        if cancelled[0]:raise TaskCancelled('stop')
    def executor(cache):calls.append(1);cancelled[0]=True;return result()
    with pytest.raises(TaskCancelled):run(setup,executor,check_cancelled=check)
    assert len(calls)==1
    with pytest.raises(TaskCancelled):run(setup,lambda _c:pytest.fail('No retry on cancellation'),check_cancelled=check)


def test_disk_write_failure_does_not_cause_additional_paid_execution(setup,monkeypatch):
    store,*_=setup;calls=[];diag={}
    monkeypatch.setattr(store,'save_stage',lambda *_a,**_kw:(_ for _ in ()).throw(OSError('disk full')))
    def executor(cache):
        calls.append(1)
        cache.on_response('split',{'choices':[{'finish_reason':'stop','message':{'content':'{"questions":[]}'}}]})
        cache.save_structured('parsed',{'questions':[]})
        return result()
    assert run(setup,executor,diagnostics=diag)==result()
    assert len(calls)==1 and len(diag['pdf_project_legacy_parse']['cache_errors'])==3


def test_corrupt_cached_packet_fails_locally_before_an_executor_or_model(setup):
    run(setup,lambda _cache:result())
    store,pid,*_=setup
    manifest=json.loads((store.root/pid/'manifest.json').read_text())
    receipt=next(value for key,value in manifest['stages'].items()if key.startswith('legacy-accepted-'))
    (store.root/pid/receipt['file']).write_bytes(b'changed cache bytes')
    with pytest.raises(ProjectCorrupt):run(setup,lambda _cache:pytest.fail('Corrupt cache must not start model repair'))


def test_partial_result_default_is_manual_and_reliable_scheduler_can_resume_only_missing_group(setup):
    calls={'executor':0,'job_one':0,'job_two':0};first=True
    q1={'content':'完整第一题','source_review':{'required':True,'reason':'保留'}}
    q2={'content':'完整第二题','answer_markdown':'原答案'}
    def executor(cache):
        calls['executor']+=1
        one=cache.load_partial('job_one',input_binding='source-one-curriculum-model')
        if one is None:
            calls['job_one']+=1;one={'questions':[q1],'source_numbers':[1],'route':'metadata'}
            cache.save_partial('job_one',one,input_binding='source-one-curriculum-model')
        if calls['executor']==1:return result(partial=True,questions=one['questions'])
        calls['job_two']+=1
        return result(questions=one['questions']+[q2])
    partial=run(setup,executor)
    assert partial['partial'] and calls=={'executor':1,'job_one':1,'job_two':0}
    assert run(setup,executor)==partial and calls['executor']==1
    complete=run(setup,executor,resume_partial=True)
    assert complete['questions']==[q1,q2]
    assert calls=={'executor':2,'job_one':1,'job_two':1}


def test_concurrent_group_cache_callbacks_are_serialized_without_losing_successful_groups(setup):
    def executor(cache):
        def group(index):
            assert cache.save_partial(f'job_{index}',{'questions':[{'content':f'题{index}'}]},input_binding=f'input{index}')
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(group,[1,2]))
        assert cache.load_partial('job_1',input_binding='input1')=={'questions':[{'content':'题1'}]}
        assert cache.load_partial('job_2',input_binding='input2')=={'questions':[{'content':'题2'}]}
        assert cache.load_partial('job_1',input_binding='different-input') is None
        return result()
    run(setup,executor)


def test_incomplete_raw_packet_and_credential_fields_are_not_saved_or_retried(setup):
    diagnostics={};calls=[]
    def executor(cache):
        calls.append(1)
        for value in [
            {'choices':[{'finish_reason':'length','message':{'content':'{"questions":[]}'}}]},
            {'choices':[{'finish_reason':'stop','message':{'content':'{"questions":['}}]},
            {'choices':[{'finish_reason':'stop','message':{'content':'{"questions":[]}'}}],'api_key':'never-save-this'},
        ]:assert cache.on_response('split',value) is False
        return result()
    assert run(setup,executor,diagnostics=diagnostics)==result()
    assert calls==[1]
    for path in setup[0].root.rglob('*.json'):
        assert 'never-save-this' not in path.read_text()


def local_result(count, *, rules='pdf-local-draft.rules.v2', answers=False, partial=False):
    packet=result(partial=partial,questions=[{'source_number':n,'content':f'原第{n}题',
        'answer_markdown':f'原第{n}题答案' if answers else ''} for n in range(1,count+1)])
    packet['workflow']='local-draft.v1'
    packet['diagnostics']['pdf_local_draft']={'rules_version':rules,'question_count':count}
    return packet


def test_rejected_complete_13_card_cache_reuses_raw_scoped_stages_without_changing_fingerprint_or_manual_draft(setup):
    request={'payload_sha256':'a'*64,'model':'unchanged-metadata-model'}
    raw={'choices':[{'finish_reason':'stop','message':{'content':'{"items":[]}'}}]}
    scope={'questions':[{'source_number':1,'content':'保留原题','answer_markdown':'保留原答'}]}
    before={}
    def seed(cache):
        assert cache.on_response('metadata',raw,input_binding=request)
        assert cache.save_partial('known-card',scope,input_binding='same-card-source')
        assert cache.save_structured('source-plan',{'original':'首次OCR逐字原文'})
        return local_result(13)
    stale=run(setup,seed,diagnostics=before)
    store,pid,*_=setup
    manual={'questions':[{'content':'教师已经校对的题卡','answer_markdown':'教师原答微调'}],
            'paperTitle':'人工标题','page_edits':[{'page_number':1,'markdown':'保留人工逐页稿'}]}
    store.save_draft(pid,manual,0)
    manifest=json.loads((store.root/pid/'manifest.json').read_text())
    protected={name:receipt for name,receipt in manifest['stages'].items()
               if not name.startswith('legacy-accepted-')}
    validations=[];calls=[];diagnostics={}
    def accept(packet):
        validations.append(deepcopy(packet))
        return len(packet['questions'])==19 and all(row['answer_markdown'] for row in packet['questions'])
    def repair(cache):
        calls.append(1)
        assert cache.load_raw_response('metadata',input_binding=request)==raw
        assert cache.load_partial('known-card',input_binding='same-card-source')==scope
        assert cache.load_structured('source-plan')=={'original':'首次OCR逐字原文'}
        return local_result(19,rules='pdf-local-draft.rules.v3',answers=True)
    fixed=run(setup,repair,accept_cached_result=accept,diagnostics=diagnostics)
    assert validations==[stale] and calls==[1]
    assert len(fixed['questions'])==19 and all(row['answer_markdown'] for row in fixed['questions'])
    report=diagnostics['pdf_project_legacy_parse']
    assert report['fingerprint']==before['pdf_project_legacy_parse']['fingerprint']
    assert report['accepted_cache_checked'] and report['accepted_cache_rejected'] and not report['reused']
    assert report['executor_calls']==1 and report['reused_raw_stages']==['metadata']
    assert report['reused_partial_scopes']==['known-card']
    after=json.loads((store.root/pid/'manifest.json').read_text())
    assert all(after['stages'][name]==receipt for name,receipt in protected.items())
    assert store.get_draft(pid)['payload']==manual


@pytest.mark.parametrize('kind',['current_v3','complete_v2','historical_legacy'])
def test_current_or_proven_complete_cached_result_keeps_manual_work_without_executor(setup,kind):
    packet=(result() if kind=='historical_legacy' else local_result(19,
        rules='pdf-local-draft.rules.v3' if kind=='current_v3' else 'pdf-local-draft.rules.v2',answers=True))
    first=run(setup,lambda _cache:packet)
    store,pid,*_=setup
    manual={'questions':[{'content':'人工修订保持不变','answer_markdown':'人工答案'}]}
    store.save_draft(pid,manual,0)
    seen=[];diagnostics={}
    def accept(candidate):
        seen.append(deepcopy(candidate))
        candidate['questions'][0]['content']='校验副本改动不能传播'
        return True
    cached=run(setup,lambda _cache:pytest.fail('Accepted cache must not execute or pay'),
        accept_cached_result=accept,diagnostics=diagnostics)
    assert seen==[first] and cached==first
    report=diagnostics['pdf_project_legacy_parse']
    assert report['accepted_cache_checked'] and not report['accepted_cache_rejected']
    assert report['reused'] and report['executor_calls']==report['new_calls']==0
    assert store.get_draft(pid)['payload']==manual
    assert run(setup,lambda _cache:pytest.fail('The validator cannot mutate accepted disk data'))==first


@pytest.mark.parametrize('error_type',[ValueError,RuntimeError,TaskCancelled])
def test_acceptance_callback_error_keeps_disk_state_and_stops_before_executor_or_paid_work(setup,error_type):
    first=run(setup,lambda _cache:local_result(13))
    store,pid,*_=setup
    before={path.name:path.read_bytes() for path in (store.root/pid).iterdir() if path.is_file()}
    diagnostics={}
    def invalid(_packet):raise error_type('local-only validator failed')
    with pytest.raises(error_type,match='local-only validator failed'):
        run(setup,lambda _cache:pytest.fail('Validation failure must not buy another request'),
            accept_cached_result=invalid,diagnostics=diagnostics)
    report=diagnostics['pdf_project_legacy_parse']
    assert report['accepted_cache_checked'] and report['executor_calls']==report['new_calls']==0
    assert report['status']==('cancelled' if error_type is TaskCancelled else 'cached_acceptance_error')
    assert before=={path.name:path.read_bytes() for path in (store.root/pid).iterdir() if path.is_file()}
    assert run(setup,lambda _cache:pytest.fail('Failed validator must not destroy the old receipt'))==first


@pytest.mark.parametrize('decision',[None,0,1,'true',{},[]])
def test_non_bool_acceptance_result_is_local_failure_without_executor(setup,decision):
    run(setup,lambda _cache:local_result(13))
    diagnostics={}
    with pytest.raises(ValueError,match='必须返回布尔值'):
        run(setup,lambda _cache:pytest.fail('Invalid validator return cannot start paid work'),
            accept_cached_result=lambda _packet:decision,diagnostics=diagnostics)
    assert diagnostics['pdf_project_legacy_parse']['executor_calls']==0


def test_cache_miss_force_and_default_none_do_not_invoke_acceptance_callback(setup):
    calls=[]
    def unused(_packet):pytest.fail('No accepted packet was loaded')
    first=run(setup,lambda _cache:(calls.append(1) or result()),accept_cached_result=unused)
    assert run(setup,lambda _cache:pytest.fail('Default None must retain the old cache protocol'))==first
    assert run(setup,lambda _cache:(calls.append(1) or result()),force=True,
        accept_cached_result=unused)==first
    assert calls==[1,1]


def test_rejecting_cached_partial_can_reassemble_and_cancelled_validator_cannot_start_executor(setup):
    run(setup,lambda _cache:local_result(13,partial=True))
    completed=run(setup,lambda _cache:local_result(19,rules='pdf-local-draft.rules.v3',answers=True),
        accept_cached_result=lambda _packet:False)
    assert len(completed['questions'])==19 and not completed['partial']
    cancelled=[False]
    def check():
        if cancelled[0]:raise TaskCancelled('cancelled after validation')
    def accept(_packet):cancelled[0]=True;return False
    with pytest.raises(TaskCancelled,match='cancelled after validation'):
        run(setup,lambda _cache:pytest.fail('Late cancellation must stop before execution'),
            accept_cached_result=accept,check_cancelled=check)


def test_invalid_acceptance_callback_is_rejected_before_the_executor(setup):
    with pytest.raises(ValueError,match='原拆卷工程输入无效'):
        run(setup,lambda _cache:pytest.fail('Invalid callback must not execute'),accept_cached_result=False)
