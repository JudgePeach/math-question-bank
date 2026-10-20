"""Fake-only tests for the shared PDF request budget and reference transport."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import requests

from mathbank.ai_providers import resolve_text_provider
from mathbank.pdf_local_structure import prepare_pdf_local_structure
from mathbank.pdf_local_structure import adopt_pdf_local_structure_response, build_pdf_local_structure_messages
from mathbank.pdf_local_request import request_pdf_local_structure, validate_cached_pdf_local_result
from mathbank.pdf_vision_request import PDFVisionRequestError
from mathbank.task_manager import TaskCancelled

TREE = {"必修一": {"3. 函数": []}}
NORMAL = resolve_text_provider('deepseek-flash', {'DEEPSEEK_API_KEY':'fake-no-network'})
STREAM = resolve_text_provider('SILICONFLOW/Qwen/Qwen3.8-27B', {'SILICONFLOW_API_KEY':'fake-no-network'})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *_a, **_kw: pytest.fail('No real API permitted'))
    monkeypatch.setattr('mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS', 0)


class Response:
    headers = {}
    def __init__(self, value=None, *, status=200, finish='stop', done=True):
        self.status_code, self.value, self.finish, self.done = status, value, finish, done
    def json(self):
        return {'choices':[{'finish_reason':self.finish,'message':{'content':json.dumps(self.value,ensure_ascii=False)}}],
                'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
    def iter_content(self, **_kw):
        raw=json.dumps(self.value,ensure_ascii=False)
        yield ('data: '+json.dumps({'choices':[{'index':0,'delta':{'content':raw},'finish_reason':None}]},ensure_ascii=False)+'\n\n').encode()
        if self.finish:
            yield ('data: '+json.dumps({'choices':[{'index':0,'delta':{},'finish_reason':self.finish}],
                'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})+'\n\n').encode()
        if self.done:
            yield b'data: [DONE]\n\n'
    def close(self):
        pass


def response_for(payload, *, bad_number=None, all_unknown=False):
    data=json.loads(payload['messages'][1]['content'])
    groups={}
    for block in data['blocks']:
        text=block['text'].strip()
        if text and text[0].isdigit() and '. ' in text:
            number=int(text.split('.',1)[0]);groups.setdefault(number,[]).append(block['id'])
    rows=[]
    for number, refs in groups.items():
        rows.append({'id':f'q{number}','source_number':number,'stem':refs,'answer':[],'answer_cells':[],
            'question_type':'detailed_answer','difficulty':'wrong' if number==bad_number else 'normal',
            'category_compulsory':'必修一','category_chapter':'3. 函数','category_knowledge':''})
    return {'input_sha256':data['input_sha256'], 'questions':[] if all_unknown else rows,
            'roles':[{'role':'unknown','blocks':[b['id'] for b in data['blocks']]}] if all_unknown else []}


def run(post, *, provider=NORMAL, source='1. 甲题\n2. 乙题', **kwargs):
    return request_pdf_local_structure(source,[{'page_number':1,'markdown':source}],TREE,
        provider=provider,post=post,**kwargs)


def test_complete_19_questions_exact_local_assembly_callbacks_and_controls():
    source='\n'.join(f'{i}. 原题${i}+1$。' for i in range(1,20))
    calls=[];plans=[];results=[];diagnostics={}
    def post(provider,payload,**kwargs):
        calls.append((deepcopy(payload),kwargs))
        return Response(response_for(payload))
    result=run(post,source=source,on_plan=plans.append,on_result=results.append,diagnostics=diagnostics)
    assert len(calls)==1 and len(result['questions'])==19
    assert [q['content'] for q in result['questions']]==[f'原题${i}+1$。' for i in range(1,20)]
    assert calls[0][0]['max_tokens']==6000 and calls[0][1]['timeout']==600
    assert calls[0][1]['retry_connection'] is False and calls[0][1]['allow_redirects'] is False
    assert len(plans)==1 and results[-1]['question_ids']==[f'q{i}' for i in range(1,20)]
    assert diagnostics['pdf_local_structure']['calls']==1


def test_partial_group_repair_only_remaining_scope_and_reindexes_source_matches():
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(deepcopy(payload))
        return Response(response_for(payload,bad_number=1 if len(calls)==1 else None))
    result=run(post)
    assert len(calls)==2 and result['question_ids']==['q1','q2']
    assert not result['failed_groups'] and not result['unassigned']
    assert '甲题' in calls[1]['messages'][1]['content'] and '乙题' not in calls[1]['messages'][1]['content']
    assert json.loads(calls[1]['messages'][1]['content'])['retry_question_ids']==['q1']
    assert [(m['question_index'],m['source_number']) for m in result['source_matches']]==[(0,1),(1,2)]


def test_first_transport_retry_consumes_budget_no_third_post_for_bad_group():
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(payload)
        if len(calls)==1:
            raise requests.ReadTimeout('private payload and credential')
        return Response(response_for(payload,bad_number=2))
    result=run(post)
    assert len(calls)==2 and result['question_ids']==['q1'] and result['partial']
    assert result['diagnostics']['transport']['calls']==2


@pytest.mark.parametrize('status',[400,401,403,404])
def test_permanent_http_never_retries(status):
    calls=[]
    def post(*args,**kwargs):
        calls.append(1);return Response(status=status)
    with pytest.raises(PDFVisionRequestError):
        run(post)
    assert len(calls)==1


def test_cancellation_has_priority_and_does_not_retry():
    calls=[];cancelled=[False]
    def check():
        if cancelled[0]:raise TaskCancelled('stop')
    def post(*args,**kwargs):
        calls.append(1);cancelled[0]=True;return Response(status=503)
    with pytest.raises(TaskCancelled):run(post,check_cancelled=check)
    assert len(calls)==1


@pytest.mark.parametrize('done,finish',[(False,'stop'),(True,None),(True,'length')])
def test_stream_requires_stop_and_done_without_accepting_partial_objects(done,finish):
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(deepcopy(payload));assert kwargs['stream'] is True
        return Response(response_for(payload),done=done,finish=finish)
    with pytest.raises(PDFVisionRequestError):run(post,provider=STREAM)
    assert len(calls)==2


def test_complete_stream_and_one_scoped_repair_share_two_post_budget():
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(deepcopy(payload))
        assert payload['enable_thinking'] is False and payload['stream'] is True
        return Response(response_for(payload,bad_number=2 if len(calls)==1 else None))
    result=run(post,provider=STREAM)
    assert len(calls)==2 and result['question_ids']==['q1','q2']
    assert result['diagnostics']['transport']['calls']==2


def test_zero_questions_full_unknown_is_manual_partial_without_extra_post():
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(1);return Response(response_for(payload,all_unknown=True))
    result=run(post)
    assert len(calls)==1 and result['partial'] and not result['questions'] and result['unassigned']


def test_invalid_json_retries_but_never_adopts_complete_prefix_objects():
    calls=[]
    class Truncated(Response):
        def json(self):
            value=super().json();value['choices'][0]['message']['content']=json.dumps(self.value)[:-1];return value
    def post(provider,payload,**kwargs):
        calls.append(1);return Truncated(response_for(payload))
    with pytest.raises(PDFVisionRequestError):run(post)
    assert len(calls)==2


def test_bad_scoped_reply_never_replaces_good_group_or_triggers_third_post():
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(deepcopy(payload))
        if len(calls)==1:return Response(response_for(payload,bad_number=2))
        value=response_for(payload);value['questions'][0]['id']='q1';value['questions'][0]['source_number']=1
        return Response(value)
    result=run(post)
    assert len(calls)==2 and result['question_ids']==['q1']
    assert result['manual_reasons']==['scoped_structure_retry_incomplete']


def test_unsafe_plan_and_stale_prepared_plan_never_start_paid_post():
    def post(*args,**kwargs):pytest.fail('No paid request for unsafe source')
    result=run(post,source='1. $unfinished')
    assert result['partial'] and result['manual_reasons']
    source='1. 甲题\n2. 乙题'
    plan=prepare_pdf_local_structure(source,[{'page_number':1,'markdown':source}])
    with pytest.raises(ValueError):run(post,source=source+'改',prepared_plan=plan)


def partial_seed():
    source='1. 甲题\n2. 乙题'
    plan=prepare_pdf_local_structure(source,[{'page_number':1,'markdown':source}])
    payload={'messages':build_pdf_local_structure_messages(plan,TREE)}
    return plan,adopt_pdf_local_structure_response(response_for(payload,bad_number=2),plan,TREE)


def test_explicit_resume_reuses_cached_good_question_and_only_sends_bad_scope():
    plan,seed=partial_seed();calls=[];saved=[]
    def post(provider,payload,**kwargs):
        calls.append(deepcopy(payload));return Response(response_for(payload))
    result=run(post,prepared_plan=plan,seed_result=seed,on_result=saved.append)
    assert len(calls)==1
    assert '甲题' not in calls[0]['messages'][1]['content'] and '乙题' in calls[0]['messages'][1]['content']
    assert result['question_ids']==['q1','q2'] and not result['partial']
    assert result['questions'][0]==seed['questions'][0]
    assert result['diagnostics']['transport']['resumed_from_cached_result']
    assert [m['question_index'] for m in result['source_matches']]==[0,1]
    assert saved[-1]['question_ids']==['q1','q2']


def test_resume_cannot_expand_budget_when_first_scoped_transport_retry_is_consumed():
    _,seed=partial_seed();calls=[]
    def post(provider,payload,**kwargs):
        calls.append(deepcopy(payload))
        if len(calls)==1:raise requests.ReadTimeout('not printed')
        return Response(response_for(payload,bad_number=2))
    result=run(post,seed_result=seed)
    assert len(calls)==2 and result['question_ids']==['q1'] and result['partial']
    assert all('甲题' not in call['messages'][1]['content'] for call in calls)
    assert result['retry_question_ids']==['q2']


def test_resume_complete_bad_scoped_reply_may_use_only_one_remaining_retry():
    _,seed=partial_seed();calls=[]
    def post(provider,payload,**kwargs):
        calls.append(payload)
        return Response(response_for(payload,bad_number=2 if len(calls)==1 else None))
    result=run(post,seed_result=seed)
    assert len(calls)==2 and result['question_ids']==['q1','q2']


@pytest.mark.parametrize('mutation',['wrong_input','wrong_question_hash','body_changed','scope_contains_good_body'])
def test_stale_or_modified_resume_seed_never_sends_a_primary_request(mutation):
    plan,seed=partial_seed()
    if mutation=='wrong_input':seed['diagnostics']['input_sha256']='0'*64
    elif mutation=='wrong_question_hash':seed['questions'][0]['pdf_local_source']['input_sha256']='0'*64
    elif mutation=='body_changed':seed['questions'][0]['content']='altered cached body'
    else:seed['retry_scopes']['q2'].insert(0,plan['blocks'][0]['id'])
    def post(*_a,**_kw):pytest.fail('No paid request for invalid seed')
    if mutation=='scope_contains_good_body':
        result=run(post,seed_result=seed)
        assert result['question_ids']==['q1'] and result['partial'] and not result['retry_question_ids']
        assert result['manual_reasons']==['cached_partial_has_no_reliable_retry_scope']
    else:
        with pytest.raises(ValueError):run(post,seed_result=seed)


def test_cached_partial_without_reliable_scope_is_returned_for_manual_review_without_post():
    _,seed=partial_seed();seed['retry_scopes']={}
    def post(*_a,**_kw):pytest.fail('No whole document restart')
    result=run(post,seed_result=seed)
    assert result['partial'] and result['question_ids']==['q1'] and result['diagnostics']['transport']['calls']==0


def test_public_cache_validator_retains_classification_review_and_audit_fields():
    plan,seed=partial_seed()
    seed['questions'][0]['classification_review']={'required':True,'original':{'category_chapter':['raw-invalid']}}
    seed['questions'][0]['local_audit']={'receipt':'kept'}
    result=validate_cached_pdf_local_result(seed,plan,TREE)
    assert result['questions'][0]['classification_review']==seed['questions'][0]['classification_review']
    assert result['questions'][0]['local_audit']=={'receipt':'kept'}


def test_bad_classification_type_is_not_a_structure_retry():
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(1);value=response_for(payload)
        value['questions'][0]['category_chapter']=None
        value['questions'][1]['category_compulsory']=['not a string']
        return Response(value)
    result=run(post)
    assert len(calls)==1 and len(result['questions'])==2 and not result['failed_groups']
    assert all(q['classification_review']['required'] for q in result['questions'])
