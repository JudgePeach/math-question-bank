"""Original split semantics versus the extracted executor, using fake replies."""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from mathbank import pdf_legacy_parse as module
from mathbank.ai_providers import apply_model_thinking_policy
from mathbank.content_locks import lock_visible_math, reconcile_visible_math
from mathbank.import_classification import normalize_import_classifications
from mathbank.paper_parse import request_pdf_paper_completion, parse_paper_completion, finalize_source_answers
from mathbank.pdf_hybrid_request import request_pdf_hybrid
from mathbank.prompts import build_pdf_parse_system_prompt

_spec=importlib.util.spec_from_file_location('legacy_pdf_private_fixtures',Path(__file__).with_name('test_pdf_hybrid_safety.py'))
_fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixture)
CURRICULUM,PROVIDER=_fixture.CURRICULUM,_fixture.PROVIDER


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *_a,**_kw:pytest.fail('No real model calls'))
    monkeypatch.setattr('mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS',0)


def response(questions):
    body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'questions':questions},ensure_ascii=False)}}],
          'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}}
    return body


def parser(diagnostics, *, cache=None, curriculum=CURRICULUM):
    """Pure equivalent of the existing model/JSON/classification parse stage."""
    def parse(source, *,pdf_retry=True,connection_retry=True,request_post=None,completion_cache=None):
        payload=apply_model_thinking_policy({'model':PROVIDER.model_name,'messages':[
            {'role':'system','content':build_pdf_parse_system_prompt(curriculum,False)},
            {'role':'user','content':source}], 'response_format':{'type':'json_object'},
            'temperature':0.2,'max_tokens':65536},provider=PROVIDER,task='parse')
        active_cache=completion_cache or cache
        binding=module.completion_request_binding(PROVIDER,payload)
        cached=active_cache.load_raw_response('legacy-full-split',input_binding=binding) if active_cache else None
        if cached is not None:
            questions=parse_paper_completion(cached,raw_markdown='')
        else:
            complete=(lambda body:active_cache.on_response('legacy-full-split',body,input_binding=binding)) if active_cache else None
            if pdf_retry:
                questions=request_pdf_paper_completion(PROVIDER,payload,post=request_post,
                    diagnostics=diagnostics,raw_markdown='',on_completion=complete)
            else:
                packet=request_post(PROVIDER,payload,timeout=600,retry_connection=connection_retry,allow_redirects=False).json()
                questions=parse_paper_completion(packet,raw_markdown='')
                if complete:complete(packet)
        normalize_import_classifications(questions,curriculum)
        return questions
    return parse


def original_source_checks(questions, source, preserved=frozenset()):
    """The same pre-project source reconciliation/final-answer sequence."""
    _,locks=lock_visible_math(source,'fixed-project-namespace')
    staged=deepcopy(questions) if preserved else questions
    report=reconcile_visible_math(staged,locks,source)
    if preserved:
        for index,candidate in enumerate(staged):
            if index not in preserved:questions[index]=candidate
            elif candidate.get('source_review'):questions[index]['source_review']=candidate['source_review']
    finalize_source_answers(questions,source)
    return questions,report


def semantics(questions):
    keys=('content','answer_markdown','referenced_images','question_type','difficulty',
          'category_compulsory','category_chapter','category_knowledge','source','source_review','classification_review')
    return [{key:deepcopy(question.get(key)) for key in keys}for question in questions]


def execute(source,layout,diagnostics,post,**kwargs):
    return module.execute_legacy_pdf_parse(source,source_namespace='fixed-project-namespace',curriculum=CURRICULUM,
        source_pages=kwargs.pop('source_pages',[]),layout_result=layout,task_id='pdf-integrity-test',
        source_document_sha256='a'*64,diagnostics=diagnostics,provider=PROVIDER,post=post,
        parse_full=parser(diagnostics),normalize_fillin=lambda value:value,**kwargs)


def test_original_complete_model_reply_keeps_same_questions_answers_figures_classification_and_reviews(monkeypatch):
    source='1. 已知$x=1$，如图求$x+x$。\n![](/static/uploads/tmp/source.png)\n【答案】$2$。'
    questions=[{'content':'已知$x=1$，如图求$x+x$。\n![](/static/uploads/tmp/source.png)',
                'answer_markdown':'[EXTRACTED_ORIGINAL]$2$。',**_fixture.FIELDS,
                'category_chapter':'函数的概念与性质','source_review':{'required':False,'verified_by':'model'}}]
    packet=response(questions);sent=[]
    def post(_provider,payload,**kwargs):
        sent.append(deepcopy(payload));return SimpleNamespace(status_code=200,json=lambda:deepcopy(packet))
    locked,_=lock_visible_math(source,'fixed-project-namespace')
    old=parser({})(locked,request_post=post)
    expected,_=original_source_checks(old,source)
    monkeypatch.setattr(module,'prepare_pdf_task_plan',lambda *_a,**_kw:{'mode':'original','groups':[]})
    result=execute(source,{'pages':[]},{},post)
    assert semantics(result['questions'])==semantics(expected)
    assert sent[0]==sent[1]
    assert '原版答案或空字符串' in sent[0]['messages'][0]['content'] or 'answer_markdown' in sent[0]['messages'][0]['content']
    assert result['preserved_indices']==frozenset() and not result['partial']
    assert not result['questions'][0].get('source_review',{}).get('verified_by')


@pytest.mark.parametrize('risk',[False,True])
@pytest.mark.parametrize('parallel',[False,True])
def test_proved_metadata_or_hybrid_keeps_original_routes_and_exact_adoption(tmp_path,monkeypatch,risk,parallel):
    plan,_,_=_fixture.prepared(tmp_path,risk=risk,with_figure=True)
    source=plan['source'];calls=[]
    def post(_provider,payload,**kwargs):
        calls.append(json.loads(payload['messages'][1]['content']))
        return _fixture.Response(_fixture.response_for(plan,payload))
    original=request_pdf_hybrid(plan,CURRICULUM,provider=PROVIDER,post=post,diagnostics={},
        normalize_fillin=lambda value:value,task_id='pdf-integrity-test',generation=2,
        full_source_fallback=lambda:pytest.fail('Valid original reply cannot cause a paid full-paper fallback'))
    expected,_=original_source_checks(original.questions,source,original.preserved_indices)
    # Only the executor's planning handoff is replaced with a real private plan;
    # its validation, request prompt and adopter remain the original functions.
    monkeypatch.setattr(module,'prepare_pdf_task_plan',lambda *_a,**_kw:plan)
    monkeypatch.setattr(module,'request_pdf_hybrid',lambda p,curriculum,**kw:request_pdf_hybrid(p,curriculum,**{**kw,'generation':2}))
    original_parallel=module.request_pdf_parallel_split
    monkeypatch.setattr(module,'request_pdf_parallel_split',lambda p,curriculum,**kw:original_parallel(p,curriculum,**{**kw,'generation':2}))
    result=execute(source,plan['_layout_result'],{},post,parallel=parallel,source_pages=plan['_source_pages'])
    assert semantics(result['questions'])==semantics(expected)
    assert result['preserved_indices']==original.preserved_indices
    assert calls[0]==calls[1]
    if not risk:
        assert plan['mode']=='whole_metadata' and calls[-1]['split_groups']==[]
        assert result['preserved_indices']==frozenset(range(3))
    assert not result['partial']


def test_unproved_whole_paper_parse_uses_the_same_shared_request_gate(monkeypatch):
    entered=[]
    @contextmanager
    def gate(provider,**kwargs):
        entered.append(provider);yield
    monkeypatch.setattr('mathbank.pdf_split_parallel.pdf_split_request_slot',gate)
    monkeypatch.setattr(module,'prepare_pdf_task_plan',lambda *_a,**_kw:{'mode':'original','groups':[]})
    packet=response([{'content':'完整题干',**_fixture.FIELDS}])
    result=execute('1. 完整题干',{'pages':[]},{},lambda *_a,**_kw:SimpleNamespace(status_code=200,json=lambda:deepcopy(packet)))
    assert entered==[PROVIDER] and len(result['questions'])==1
    assert result['diagnostics']['pdf_parallel_split']['reason']=='unproven_original_number_census'


class MemoryRawCache:
    def __init__(self):
        self.latest = {}
        self.history = []
        self.reads = []

    @staticmethod
    def key(stage,binding):
        return stage,json.dumps(binding,sort_keys=True)

    def load_raw_response(self,stage,input_binding=None):
        self.reads.append(self.key(stage,input_binding))
        return deepcopy(self.latest.get(self.key(stage,input_binding)))

    def on_response(self,stage,body,input_binding=None):
        self.history.append(deepcopy(body))
        self.latest[self.key(stage,input_binding)] = deepcopy(body)


def test_invalid_first_complete_reply_is_audited_but_real_second_post_can_succeed():
    cache = MemoryRawCache()
    physical = []
    def post(*args,**kwargs):
        physical.append(1)
        body = response([] if len(physical)==1 else [{'content':'第二次完整题干',**_fixture.FIELDS}])
        return SimpleNamespace(status_code=200,json=lambda:deepcopy(body))
    recorder = module.LegacyResponseRecorder(post,cache,lambda:None)
    questions = request_pdf_paper_completion(PROVIDER,{'model':PROVIDER.model_name},post=recorder,max_attempts=2)
    assert questions[0]['content']=='第二次完整题干'
    assert len(physical)==recorder.calls==2 and recorder.replayed==0
    assert len(cache.reads)==1 and len(cache.history)==2
    assert json.loads(cache.history[0]['choices'][0]['message']['content'])=={'questions':[]}


def test_existing_bad_historical_packet_is_read_once_then_real_retry_succeeds():
    cache = MemoryRawCache()
    payload = {'model':PROVIDER.model_name}
    binding = module.completion_request_binding(PROVIDER,payload)
    cache.on_response('legacy-http',response([]),input_binding=binding)
    physical = []
    def post(*a,**kw):
        physical.append(1)
        return SimpleNamespace(status_code=200,json=lambda:response([{'content':'实际新回复',**_fixture.FIELDS}]))
    recorder = module.LegacyResponseRecorder(post,cache,lambda:None)
    result = request_pdf_paper_completion(PROVIDER,payload,post=recorder,max_attempts=2)
    assert result[0]['content']=='实际新回复' and len(physical)==1 and recorder.replayed==1
    assert len(cache.reads)==1 and len(cache.history)==2


def test_rejected_outer_raw_cache_can_skip_http_cache_and_keep_two_physical_attempts():
    cache = MemoryRawCache()
    payload = {'model':PROVIDER.model_name}
    binding = module.completion_request_binding(PROVIDER,payload)
    cache.on_response('legacy-http',response([]),input_binding=binding)
    physical = []
    def post(*a,**kw):
        physical.append(1)
        body = response([] if len(physical)==1 else [{'content':'第二次真实请求',**_fixture.FIELDS}])
        return SimpleNamespace(status_code=200,json=lambda:deepcopy(body))
    recorder = module.LegacyResponseRecorder(post,cache,lambda:None)
    recorder.skip_cached_binding(PROVIDER,payload)
    result = request_pdf_paper_completion(PROVIDER,payload,post=recorder,max_attempts=2)
    assert result[0]['content']=='第二次真实请求'
    assert len(physical)==2 and not cache.reads and recorder.replayed==0
    assert len(cache.history)==3


def test_same_run_new_payload_has_its_own_single_history_read():
    cache = MemoryRawCache()
    recorder = module.LegacyResponseRecorder(lambda *_a,**_kw:SimpleNamespace(status_code=200,
        json=lambda:response([{'content':'完整原题',**_fixture.FIELDS}])),cache,lambda:None)
    recorder(PROVIDER,{'different_payload':1})
    recorder(PROVIDER,{'different_payload':1})
    recorder(PROVIDER,{'different_payload':2})
    assert len(cache.reads)==2 and recorder.calls==3 and not recorder.replayed
