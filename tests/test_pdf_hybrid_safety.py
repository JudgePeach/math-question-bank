"""Real private PDF transcript witnesses: integrity is never math truth."""
from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
import json,re

from PIL import Image
import pytest

from mathbank.ai_providers import resolve_text_provider
from mathbank.pdf_layout import apply_figure_anchors
from mathbank.pdf_figures import clear_resolved_figure_placeholders
from mathbank.pdf_transcription_scopes import (
    begin_pdf_transcription_witness, finish_pdf_transcription_witness,
    finalize_pdf_transcription_snapshot, verify_pdf_transcription_snapshot,
)
from mathbank.pdf_hybrid_plan import (
    PdfHybridPlanError, build_pdf_snapshot_hybrid_plan,
    require_pdf_hybrid_plan, require_pdf_snapshot_hybrid_plan,
)
from mathbank.pdf_hybrid_request import request_pdf_hybrid
from mathbank.source_metadata import SourceMetadataContractError
from mathbank.task_manager import TaskCancelled

CURRICULUM={'必修一':{'3. 函数的概念与性质':[]}}
FIELDS={'question_type':'detailed_answer','category_compulsory':'必修一',
        'category_chapter':'3. 函数的概念与性质','difficulty':'normal'}
PROVIDER=resolve_text_provider('SILICONFLOW/Qwen/test-model',{'SILICONFLOW_API_KEY':'fake-key-no-network'})


def raster(path,color='red'):
    data=BytesIO();Image.new('RGB',(8,8),color).save(data,format='PNG');path.write_bytes(data.getvalue())


def prepared(tmp_path,*,risk=True,with_figure=False,shared=False):
    # These are deliberate test machine transcripts of tiny color rasters.
    # No fixture claims raster/transcript mathematical equivalence.
    texts=['一、解答题\n1. 甲题如图，已知$x=99$。\n【答案】$99$。原始代码`[EXTRACTED_ORIGINAL]`。',
           '2. 乙题已知$y=2$，求$y+y$。\n【解析】原乙题答案$4$。',
           '3. 丙题已知$z=3$，求$z+z$。\n【解析】原丙题答案$6$。']
    if shared:texts[2]=texts[2].replace('丙题已知','根据第1题中的函数，丙题已知')
    pages=[];layout=[];witnesses={};image_paths=[];figures={};figure_url='/static/uploads/tmp/source_figure.png'
    for index,text in enumerate(texts):
        image=tmp_path/f'page-{index}.png';raster(image);image_paths.append(image)
        info={'page_index':index,'width':8,'height':8}
        before=begin_pdf_transcription_witness(image,info)
        result={'markdown':text,'layout':{'page_complete':not(risk and index==1),
                                        'native_reliable':True,'source_math_verified':True}}
        state={'page_index':index,'page_number':index+1,'status':'checked','warnings':[], 'figures':[]}
        if with_figure and index==0:
            figure=tmp_path/'figure.png';raster(figure,'blue');figures[figure_url]=str(figure)
            state['figures']=[{'id':'local-F1','slot_id':'slot-F1','page_index':index,'bbox':[100,100,400,400],
                'image_path':figure_url,'attached':True,'review_reasons':[],'review_required':False,
                'anchor_before':'已知$x=99$。','anchor_after':'【答案】$99$。'}]
        result['layout']['figures'] = deepcopy(state['figures'])
        witnesses[index]=finish_pdf_transcription_witness(before,result)
        marked=f'<!-- MATHBANK_PDF_PAGE:{index+1} -->\n'+text
        attached=apply_figure_anchors(marked,state['figures'])
        marked=clear_resolved_figure_placeholders(attached['markdown'],state['figures'])
        pages.append({'page_number':index+1,'origin':'joint_vision','markdown':marked})
        layout.append(state)
    source='\n\n'.join(re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->','',page['markdown']) for page in pages)
    diagnostics={'pdf_extraction':'joint_vision','pdf_native_quality':[{'reason':'unverified native glyphs remain unverified'}]}
    evidence=finalize_pdf_transcription_snapshot(source,diagnostics,source_document_sha256='a'*64,
        source_pages=pages,layout_result={'pages':layout},witnesses=witnesses,
        transcript_normalizer=lambda x:x,figure_assets=figures)
    assert evidence is not None
    plan=build_pdf_snapshot_hybrid_plan(source,diagnostics,source_pages=pages,layout_result={'pages':layout},
        task_id='pdf-integrity-test',generation=2,source_document_sha256='a'*64,
        source_review_evidence=evidence,min_metadata_source_characters=0)
    assert plan['mode'] in {'hybrid','whole_metadata'},plan['global_reasons']
    return plan,image_paths,figures


def response_for(plan,payload):
    data=json.loads(payload['messages'][1]['content']);original={q['id']:q for q in plan['_inspection']['questions']}
    splits=[]
    for group in data['split_groups']:
        rows=[]
        for qid in group['source_ids']:
            q=original[qid];rows.append({'source_id':qid,**FIELDS,'content':q['content'],
                'answer_markdown':'[EXTRACTED_ORIGINAL]'+q['answer_markdown'] if q['answer_markdown'] else '',
                'source':'','referenced_images':[]})
        splits.append({'group_id':group['group_id'],'questions':rows})
    return {'metadata':{'items':[{'id':item['id'],**FIELDS} for item in data['metadata_items']]},'splits':splits}


class Response:
    status_code=200
    def __init__(self,content):self.content=content
    def json(self):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.content,ensure_ascii=False)}}],
        'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}


def run(plan,post,*,fallback=lambda:pytest.fail('Unexpected whole retry'),cancel=lambda:None):
    diagnostics={};result=request_pdf_hybrid(plan,CURRICULUM,provider=PROVIDER,post=post,diagnostics=diagnostics,
        normalize_fillin=lambda x:x,full_source_fallback=fallback,task_id='pdf-integrity-test',generation=2,check_cancelled=cancel)
    return result,diagnostics


def test_snapshot_self_reported_native_math_flags_never_become_native_certificate(tmp_path):
    plan,_,_=prepared(tmp_path,risk=False)
    assert plan['source_basis']=='first_pass_transcription' and plan['native_reliable'] is False
    require_pdf_snapshot_hybrid_plan(plan,task_id='pdf-integrity-test',generation=2)
    with pytest.raises(PdfHybridPlanError):require_pdf_hybrid_plan(plan,task_id='pdf-integrity-test',generation=2)
    for group in plan['groups']:
        private=group['_source_metadata_plan']
        assert private['source_basis']=='first_pass_transcription' and private['native_reliable'] is False
        assert '_word_source_certificate' not in private
    result,diag=run(plan,lambda _p,payload,**kw:Response(response_for(plan,payload)))
    assert '$x=99$' in result.questions[0]['content']
    assert diag['pdf_hybrid']['native_reliable'] is False
    assert '`[EXTRACTED_ORIGINAL]`' in result.questions[0]['answer_markdown']


@pytest.mark.parametrize('change',['source','page','layout','generation','native_basis','input_image','figure_image','private_math'])
def test_source_page_layout_identity_and_assets_change_after_post_are_hard_stops(tmp_path,change):
    plan,images,figures=prepared(tmp_path,with_figure=True);posts=[];fallbacks=[]
    def post(_provider,payload,**options):
        posts.append(options);reply=response_for(plan,payload)
        if change=='source':plan['source']+='Changed condition'
        elif change=='page':plan['_source_pages'][0]['markdown']+='Changed page'
        elif change=='layout':plan['_layout_result']['pages'][0]['figures'][0]['bbox']=[200,200,400,400]
        elif change=='generation':plan['generation']+=1
        elif change=='native_basis':plan['source_basis']='native_pdf';plan['native_reliable']=True
        elif change=='input_image':images[0].write_bytes(b'changed original page raster')
        elif change=='figure_image':__import__('pathlib').Path(next(iter(figures.values()))).write_bytes(b'changed crop raster')
        else:plan['groups'][0]['_source_metadata_plan']['questions'][0]['content']+='Changed local math'
        return Response(reply)
    with pytest.raises((PdfHybridPlanError,SourceMetadataContractError)):
        run(plan,post,fallback=lambda:fallbacks.append(True) or [])
    assert len(posts)==1 and not fallbacks
    assert posts[0]['retry_connection'] is False


@pytest.mark.parametrize('change',['unknown_meta','duplicate_meta','unknown_group','unknown_question','duplicate_question'])
def test_unknown_or_duplicate_reply_identity_rejects_contract_with_one_old_source_fallback(tmp_path,change):
    plan,_,_=prepared(tmp_path,risk=False,shared=True);posts=[];fallbacks=[]
    def post(_provider,payload,**options):
        posts.append(True);reply=response_for(plan,payload)
        if change=='unknown_meta':reply['metadata']['items'][0]['id']='unknown'
        elif change=='duplicate_meta':reply['metadata']['items'].append(deepcopy(reply['metadata']['items'][0]))
        elif change=='unknown_group':reply['splits'][0]['group_id']='unknown'
        elif change=='unknown_question':reply['splits'][0]['questions'][0]['source_id']='unknown'
        else:reply['splits'][0]['questions'][1]['source_id']=reply['splits'][0]['questions'][0]['source_id']
        return Response(reply)
    old=[{'content':'Original source fallback',**FIELDS}]
    result,diag=run(plan,post,fallback=lambda:fallbacks.append(True) or old)
    assert len(posts)==1 and len(fallbacks)==1 and diag['pdf_hybrid']['calls']==2
    assert result.questions==old and not result.preserved_indices


def test_original_answer_exchange_in_noncontiguous_closed_risk_group_keeps_other_source_without_third_post(tmp_path):
    plan,_,_=prepared(tmp_path,risk=False,shared=True);posts=[]
    def post(_provider,payload,**options):
        posts.append(payload);reply=response_for(plan,payload);rows=reply['splits'][0]['questions']
        rows[0]['answer_markdown'],rows[1]['answer_markdown']=rows[1]['answer_markdown'],rows[0]['answer_markdown']
        return Response(reply)
    result,diag=run(plan,post)
    assert len(posts)==2 and result.partial and len(result.questions)==1
    assert '乙题' in result.questions[0]['content'] and result.preserved_indices==frozenset({0})
    assert diag['pdf_hybrid']['status']=='partial'


def test_noncontiguous_source_reply_reordering_is_local_and_never_promotes_snapshot_to_native(tmp_path):
    plan,_,_=prepared(tmp_path,risk=False,shared=True);posts=[]
    def post(_provider,payload,**options):
        posts.append(True);reply=response_for(plan,payload);reply['splits'][0]['questions'].reverse();return Response(reply)
    result,diag=run(plan,post)
    assert len(posts)==1 and not result.partial
    assert ['甲题' in result.questions[0]['content'],'乙题' in result.questions[1]['content'],'丙题' in result.questions[2]['content']]==[True,True,True]
    assert result.preserved_indices==frozenset({1})
    assert diag['pdf_hybrid']['native_reliable'] is False


def test_cancel_then_connection_error_never_adds_text_stage_retry(tmp_path):
    plan,_,_=prepared(tmp_path);cancelled=False;posts=[]
    def cancel():
        if cancelled:raise TaskCancelled('cancelled fixture')
    def post(_provider,payload,**options):
        nonlocal cancelled
        posts.append(True);cancelled=True;raise ConnectionError('cancel wins')
    with pytest.raises(TaskCancelled):run(plan,post,cancel=cancel)
    assert len(posts)==1


def test_public_snapshot_json_cannot_be_replayed_as_private_evidence(tmp_path):
    plan,_,_=prepared(tmp_path,risk=False);plan['_source_review_evidence']=asdict(plan['_source_review_evidence'])
    with pytest.raises(PdfHybridPlanError):require_pdf_snapshot_hybrid_plan(plan,task_id='pdf-integrity-test',generation=2)


def test_real_plain_native_proof_and_visual_transcript_proof_cannot_exchange_domains(tmp_path):
    import hashlib
    import pymupdf
    from mathbank.pdf_source_scopes import _capture_native_source_evidence, finalize_pdf_source_review_evidence
    from mathbank.pdf_hybrid_plan import build_pdf_hybrid_plan
    document=pymupdf.open();page=document.new_page()
    page.insert_text((70,70),'1. Plain first question.\n2. Independent second question.',fontname='helv',fontsize=12)
    raw=document.tobytes();markdown=page.get_text('text').strip();document.close()
    rows=[{'page_index':0,'markdown':markdown,'source':'pymupdf','needs_ocr':False,'quality_reasons':[]}]
    physical=_capture_native_source_evidence(raw,rows)
    diagnostics={};pages=[{'page_number':1,'origin':'native','markdown':markdown}]
    layout={'pages':[{'page_index':0,'page_number':1,'status':'checked','figures':[],'warnings':[]}]}
    docsha=hashlib.sha256(raw).hexdigest()
    evidence=finalize_pdf_source_review_evidence(markdown,diagnostics,source_document_sha256=docsha,
        source_pages=pages,layout_result=layout,native_evidence=physical,asset_paths={})
    native=build_pdf_hybrid_plan(markdown,diagnostics,source_pages=pages,layout_result=layout,
        task_id='native-domain',generation=0,source_document_sha256=docsha,source_review_evidence=evidence,
        min_metadata_source_characters=0)
    # A real Helvetica-only physical witness can conservatively remain
    # original; this test verifies certificate domain, never math reliability.
    assert native['mode'] in {'original','whole_metadata'}
    require_pdf_hybrid_plan(native,task_id='native-domain',generation=0)
    with pytest.raises(PdfHybridPlanError):require_pdf_snapshot_hybrid_plan(native,task_id='native-domain',generation=0)
    snapshot,_,_=prepared(tmp_path)
    original_native=native['_source_review_evidence'];original_snapshot=snapshot['_source_review_evidence']
    from mathbank.pdf_source_scopes import verify_pdf_source_review_evidence
    assert verify_pdf_transcription_snapshot(markdown,diagnostics,original_native,
        source_document_sha256=docsha,source_pages=pages,layout_result=layout)['status']=='uncertain'
    assert verify_pdf_source_review_evidence(markdown,diagnostics,original_snapshot,
        source_document_sha256=docsha,source_pages=pages,layout_result=layout)['status']=='uncertain'
    snapshot['_source_review_evidence']=original_native
    with pytest.raises(PdfHybridPlanError):require_pdf_snapshot_hybrid_plan(snapshot,task_id='pdf-integrity-test',generation=2)


@pytest.mark.parametrize('partial',[False,True])
def test_pdf_task_completion_executed_tail_preserves_partial_flag_and_notice(partial):
    """Execute the actual PDF completion tail without importing app/main/DB.

    The HTTP/planner tests above exercise the real request. This isolates the
    final task/UI contract directly from main's current source statements.
    """
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_pdf_parsing_task')
    assigns={target.id:node for node in ast.walk(function) if isinstance(node,ast.Assign)
             for target in node.targets if isinstance(target,ast.Name) and target.id in {'partial','completion_log'}}
    completion=next(node for node in ast.walk(function) if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute) and node.func.attr=='complete'
        and isinstance(node.func.value,ast.Name) and node.func.value.id=='DOCUMENT_TASKS')
    actual_ast=ast.Module(body=[assigns['partial'],assigns['completion_log'],ast.Expr(value=completion)],type_ignores=[])
    ast.fix_missing_locations(actual_ast)
    captured=[]
    tasks=SimpleNamespace(complete=lambda task_id,**kwargs:captured.append((task_id,kwargs)) or True)
    data=[{'content':'One retained complete question'}]
    diagnostics={'pdf_hybrid':{'partial':partial,'failed_group_ids':['failed-source-group'] if partial else []}}
    namespace={'DOCUMENT_TASKS':tasks,'diagnostics':diagnostics,'task_id':'pdf-ui-completion','final_questions':data,
               'generate_answers':False,'page_urls':['test-page'],'target_page_indices':[0],'temp_assets':[]}
    exec(compile(actual_ast,'actual-pdf-completion-tail','exec'),namespace)
    assert len(captured)==1
    kwargs=captured[0][1]
    assert kwargs['partial'] is partial and kwargs['data'] is data and kwargs['diagnostics'] is diagnostics
    assert ('部分疑点题组未完成' in kwargs['log']) is partial
    assert kwargs['document_type']=='pdf' and kwargs['page_numbers']==[1]
