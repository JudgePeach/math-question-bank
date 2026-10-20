"""The original full-body algorithm stays identical behind the project cache."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from mathbank import pdf_legacy_parse as module
from mathbank.content_locks import lock_visible_math
from mathbank.document_projects import DocumentProjectStore
from mathbank.document_project_parse import run_project_legacy_parse

_spec=importlib.util.spec_from_file_location('legacy_parse_flow_helpers',Path(__file__).with_name('test_pdf_legacy_parse.py'))
_helpers=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_helpers)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *_a,**_kw:pytest.fail('No real model API'))
    monkeypatch.setattr('mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS',0)
    monkeypatch.setattr(module,'prepare_pdf_task_plan',lambda *_a,**_kw:{'mode':'original','groups':[]})
    raw=b'%PDF-legacy-cache-flow';sha=hashlib.sha256(raw).hexdigest()
    source='1. 已知$x=1$，如图求$x+x$。\n![](/static/uploads/tmp/source.png)\n【答案】$2$。'
    store=DocumentProjectStore(tmp_path/'projects')
    pid=store.create(raw,'旧算法缓存验证.pdf',{},_helpers.CURRICULUM)['project_id']
    questions=[{'content':'已知$x=1$，如图求$x+x$。\n![](/static/uploads/tmp/source.png)',
        'answer_markdown':'[EXTRACTED_ORIGINAL]$2$。',**_helpers._fixture.FIELDS,
        'category_chapter':'函数的概念与性质'}]
    packet=_helpers.response(questions);calls=[]
    def post(provider,payload,**kwargs):
        calls.append((deepcopy(payload),deepcopy(kwargs)))
        return SimpleNamespace(status_code=200,json=lambda:deepcopy(packet))
    return SimpleNamespace(store=store,pid=pid,source=source,sha=sha,post=post,packet=packet,calls=calls,
        pages=[{'page_number':1,'markdown':source}],namespace=pid.replace('-','')[:16])


def finalizer(questions,preserved):
    # Same deterministic finalizer is supplied to the pre-project reference and
    # executor. It illustrates a product-owned final stage, not a new parser.
    for question in questions:
        question['image_paths']=list(question.get('referenced_images') or [])
    return questions


def executor(setup, *, diagnostics=None):
    report=diagnostics if diagnostics is not None else {}
    def execute(cache):
        return module.execute_legacy_pdf_parse(setup.source,source_namespace=setup.namespace,
            curriculum=_helpers.CURRICULUM,source_pages=setup.pages,layout_result={'pages':[]},
            task_id='a-new-runtime-task',source_document_sha256=setup.sha,diagnostics=report,
            provider=_helpers.PROVIDER,post=setup.post,parse_full=_helpers.parser(report),
            normalize_fillin=lambda value:value,cache=cache,parallel=False,finalize=finalizer)
    return execute


def run(setup, **kwargs):
    return run_project_legacy_parse(setup.store,setup.pid,setup.source,setup.pages,_helpers.CURRICULUM,
        source_document_sha256=setup.sha,executor=executor(setup),**kwargs)


def test_same_model_packet_original_vs_cache_miss_vs_hit_question_semantics_and_result_exact(setup):
    locked,locks=lock_visible_math(setup.source,setup.namespace)
    old=_helpers.parser({})(locked,request_post=setup.post)
    # Source namespace is part of the original lock protocol. Reconcile with
    # those exact locks, then use the unchanged original answer/finalizer rules.
    from mathbank.content_locks import reconcile_visible_math
    from mathbank.paper_parse import finalize_source_answers
    reconcile_visible_math(old,locks,setup.source)
    finalize_source_answers(old,setup.source)
    expected=finalizer(old,frozenset())
    first=run(setup)
    assert _helpers.semantics(first['questions'])==_helpers.semantics(expected)
    assert first['questions'][0]['image_paths']==expected[0]['image_paths']
    assert setup.calls[0][0]==setup.calls[1][0]
    count=len(setup.calls);diag={}
    second=run(setup,diagnostics=diag)
    assert second==first and len(setup.calls)==count
    assert diag['pdf_project_legacy_parse']['reused'] is True
    assert diag['pdf_project_legacy_parse']['new_calls']==0
    # Original diagnostics are historical result evidence, not rewritten as if
    # another call happened. The separate cache report supplies current usage.
    assert second['diagnostics']==first['diagnostics']


def test_validation_update_reparses_complete_model_packet_locally_with_zero_new_http(setup):
    first=run(setup);assert len(setup.calls)==1
    diag={}
    second=run(setup,validation_version='legacy-validation.v2',diagnostics=diag)
    assert len(setup.calls)==1
    assert _helpers.semantics(first['questions'])==_helpers.semantics(second['questions'])
    assert diag['pdf_project_legacy_parse']['executor_calls']==1
    assert 'legacy-full-split' in diag['pdf_project_legacy_parse']['reused_raw_stages']
    assert second['diagnostics']['pdf_legacy_requests']['new_calls']==0


def test_old_block_protocol_packets_remain_archived_and_are_never_legacy_cache_hits(setup):
    old={'questions':[{'content':'不同块引用算法的旧卡片，不能冒充原算法结果'}], 'partial':True}
    setup.store.save_stage(setup.pid,'pdf_local_structure_result',old,'previous-block-binding')
    value=run(setup)
    assert len(setup.calls)==1 and value['questions'][0]['content']!=old['questions'][0]['content']
    assert setup.store.load_stage(setup.pid,'pdf_local_structure_result','previous-block-binding')==old


def test_finalizer_runs_inside_cached_executor_once_and_not_again_on_cache_hit(setup):
    calls=[]
    def execute(cache):
        value=executor(setup)(cache)
        calls.append(1);value['questions'][0]['final_review']={'required':True,'message':'保留现有来源状态'}
        return value
    def invoke():
        return run_project_legacy_parse(setup.store,setup.pid,setup.source,setup.pages,_helpers.CURRICULUM,
            source_document_sha256=setup.sha,executor=execute)
    first=invoke();second=invoke()
    assert first==second and calls==[1] and len(setup.calls)==1
    assert second['questions'][0]['final_review']['required'] is True


def test_raw_recording_disk_failure_cannot_trigger_another_physical_request(setup,monkeypatch):
    original=setup.store.save_stage
    def save(project_id,name,*args,**kwargs):
        if name.startswith(('legacy-raw-','legacy-structured-')):
            raise OSError('simulated response recording failure')
        return original(project_id,name,*args,**kwargs)
    monkeypatch.setattr(setup.store,'save_stage',save)
    result=run(setup)
    assert len(setup.calls)==1 and result['questions']


def test_current_run_does_not_replay_its_own_semantically_invalid_response_instead_of_original_retry(setup):
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(1)
        body=_helpers.response([]) if len(calls)==1 else setup.packet
        return SimpleNamespace(status_code=200,json=lambda:deepcopy(body))
    setup.post=post
    value=run(setup)
    assert value['questions'] and calls==[1,1]
