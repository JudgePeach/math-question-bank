"""Original bytes/glyphs authorize native reuse; public flags never do."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from types import SimpleNamespace

from PIL import Image
import pymupdf as fitz
import pytest

from mathbank.pdf_inspector_helper import inspect_and_extract_pdf, merge_pdf_page_texts
from mathbank.pdf_source_scopes import (
    finalize_pdf_source_review_evidence, verify_pdf_source_review_evidence,
    _capture_native_source_evidence, _open, _NativePdfEvidence,
)


TEXT='普通中文提问：请根据原文简述这一次学习过程，列举生活中的实例并给出文字解释。'


def make_pdf(*,pages=1,rotation=0,math=False,picture=False,columns=False,covered=False):
    font=fitz.Font(fontname='china-s')
    with fitz.open() as document:
        for i in range(pages):
            page=document.new_page()
            page.insert_font(fontname='prose',fontbuffer=font.buffer)
            text=f'{i+1}. '+TEXT
            if math:text=f'{i+1}. 已知变量x满足x=2，求x+1，并给出过程。'
            page.insert_text((40,80),text,fontname='prose',fontsize=10)
            if columns:page.insert_text((350,80),'另一栏中文内容',fontname='prose',fontsize=10)
            if picture:
                png=BytesIO();Image.new('RGB',(8,8),'red').save(png,format='PNG')
                page.insert_image(fitz.Rect(50,100,100,150),stream=png.getvalue())
            if covered:page.draw_rect(fitz.Rect(38,66,460,85),fill=(1,1,1),color=(1,1,1),overlay=True)
            if rotation:page.set_rotation(rotation)
        return document.tobytes(deflate=True)


@pytest.fixture(scope='module')
def positive_pdf():return make_pdf()


def extract(blob):
    return inspect_and_extract_pdf(blob,include_source_review_evidence=True)


def source_state(result,*,origins=None):
    pages=[{'page_number':p['page_index']+1,'origin':(origins or {}).get(p['page_index'],'native'),
            'markdown':f"<!-- MATHBANK_PDF_PAGE:{p['page_index']+1} -->\n"+p['markdown'],'figures':[]} for p in result['pages']]
    # This is the exact current production operation, retaining the newline
    # left by a stripped page comment, including three newlines between pages.
    source=re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->','',merge_pdf_page_texts([p['markdown'] for p in pages]))
    return source,pages


def bind(blob,result,*,origins=None,source=None,pages=None,layout=None,asset_paths=None):
    original,records=source_state(result,origins=origins)
    source=original if source is None else source;pages=records if pages is None else pages
    digest=hashlib.sha256(blob).hexdigest()
    evidence=finalize_pdf_source_review_evidence(source,{},source_document_sha256=digest,source_pages=pages,
        layout_result=layout,native_evidence=result['_native_source_review_evidence'],asset_paths=asset_paths)
    proof=verify_pdf_source_review_evidence(source,{},evidence,source_document_sha256=digest,source_pages=pages,layout_result=layout)
    return source,pages,evidence,proof


def test_real_chinese_original_glyph_proof_and_opt_in_do_not_change_native_output(positive_pdf):
    result=extract(positive_pdf);default=inspect_and_extract_pdf(positive_pdf)
    assert {k:v for k,v in result.items() if not k.startswith('_')}==default
    assert '_native_source_review_evidence' not in default
    source,pages,evidence,proof=bind(positive_pdf,result)
    assert source.startswith('\n1.') and proof['status']=='ready'
    page=proof['pages'][0]
    assert page['reliable'] and page['range']==[0,len(source)]
    assert page['glyph_summary']['total']==page['glyph_summary']['consumed']
    assert page['reading_order']['status']==page['figure_ownership']['status']=='proved'
    assert proof['producer_analysis']['pdf_opens']==1
    assert proof['verification_analysis']=={'pdf_opens':0,'pages_reanalyzed':0}


def test_exact_mark_stripping_and_page_offsets_keep_production_whitespace():
    blob=make_pdf(pages=2);result=extract(blob)
    source,pages,evidence,proof=bind(blob,result)
    assert proof['status']=='ready' and all(p['reliable'] for p in proof['pages'])
    first,second=proof['pages']
    assert source[first['range'][1]:second['range'][0]]=='\n\n'
    assert source[slice(*second['range'])].startswith('\n2.')
    stripped=source.strip()
    assert bind(blob,result,source=stripped)[3]['status']=='uncertain'


@pytest.mark.parametrize('origin',['ocr','joint_vision','regional_vision','vision'])
def test_same_correct_text_from_a_visual_origin_does_not_receive_native_trust(positive_pdf,origin):
    result=extract(positive_pdf)
    source,pages=source_state(result,origins={0:origin})
    pages[0].update(page_complete=True,needs_ocr=False,native_trustworthy=True)
    _,_,_,proof=bind(positive_pdf,result,source=source,pages=pages)
    assert proof['status']=='ready' and not proof['pages'][0]['reliable']
    assert proof['pages'][0]['reasons']==['visual_origin_not_native_certified']


def test_public_complete_flags_cannot_certify_unrepaired_math():
    blob=make_pdf(math=True);result=extract(blob)
    result['pages'][0].update(needs_ocr=False,page_complete=True)
    _,_,_,proof=bind(blob,result)
    assert not any(p.get('reliable') for p in proof.get('pages',[]))


@pytest.mark.parametrize('kwargs,reason',[
    ({'rotation':90},'native_rotation_unsupported'),
    ({'picture':True},'native_bitmap_content_or_figure_ownership_unproved'),
    ({'columns':True},'native_multicolumn_or_nonmonotonic_order'),
    ({'covered':True},'native_paint_visibility_unproved'),
])
def test_physical_order_visibility_and_figure_uncertainty_are_not_erased(kwargs,reason):
    blob=make_pdf(**kwargs);result=extract(blob)
    packet=_open(_NativePdfEvidence,result['_native_source_review_evidence'])
    assert packet and not packet['pages'][0]['reliable']
    assert reason in packet['pages'][0]['global_reasons']


def test_independent_font_glyph_identity_failure_keeps_native_untrusted(positive_pdf,monkeypatch):
    import mathbank.pdf_source_scopes as scopes
    result=inspect_and_extract_pdf(positive_pdf)
    class WrongFont:
        def has_glyph(self,*a,**kw):return 0
    monkeypatch.setattr(scopes.fitz,'Font',lambda *a,**kw:WrongFont())
    packet=_open(_NativePdfEvidence,_capture_native_source_evidence(positive_pdf,result['pages']))
    assert not packet['pages'][0]['reliable']
    assert packet['pages'][0]['reasons']==['native_font_glyph_identity_unproved']


def test_native_producer_missing_or_wrong_pdf_hash_cannot_bind_a_native_certificate(positive_pdf):
    result=extract(positive_pdf);source,pages=source_state(result)
    for packet in [None,asdict(result['_native_source_review_evidence'])]:
        evidence=finalize_pdf_source_review_evidence(source,{},source_document_sha256=hashlib.sha256(positive_pdf).hexdigest(),
            source_pages=pages,layout_result=None,native_evidence=packet)
        assert verify_pdf_source_review_evidence(source,{},evidence,source_document_sha256=hashlib.sha256(positive_pdf).hexdigest(),
            source_pages=pages,layout_result=None)['status']=='uncertain'


@pytest.mark.parametrize('field',['source','pages','layout','native_diagnostic','pdf_sha'])
def test_late_source_page_layout_and_native_diagnostic_mutation_rejects_private_proof(positive_pdf,field):
    result=extract(positive_pdf);source,pages,evidence,_=bind(positive_pdf,result)
    digest=hashlib.sha256(positive_pdf).hexdigest();diag={};layout=None
    if field=='source':source+='改字'
    elif field=='pages':pages=deepcopy(pages);pages[0]['origin']='ocr'
    elif field=='layout':layout={'pages':[],'invented_owner':True}
    elif field=='native_diagnostic':diag={'pdf_native_repair':{'guessed_complete':True}}
    else:digest='0'*64
    proof=verify_pdf_source_review_evidence(source,diag,evidence,source_document_sha256=digest,source_pages=pages,layout_result=layout)
    assert proof['status']=='uncertain' and not proof['pages']


def test_task_usage_fields_do_not_change_native_snapshot_and_verify_does_not_reopen_pdf(positive_pdf,monkeypatch):
    import mathbank.pdf_source_scopes as scopes
    result=extract(positive_pdf);source,pages,evidence,_=bind(positive_pdf,result)
    monkeypatch.setattr(scopes.fitz,'open',lambda *a,**kw:pytest.fail('Late verifier must not reanalyze original PDF'))
    proof=verify_pdf_source_review_evidence(source,{'pdf_hybrid_usage':{'tokens':100}},evidence,
        source_document_sha256=hashlib.sha256(positive_pdf).hexdigest(),source_pages=pages,layout_result=None)
    assert proof['status']=='ready' and proof['verification_analysis']['pdf_opens']==0


@pytest.mark.parametrize('mutation',['replace','delete','symlink'])
def test_bound_visual_crop_asset_is_rechecked_even_though_visual_page_is_not_native(positive_pdf,tmp_path,mutation):
    result=extract(positive_pdf);source,pages=source_state(result,origins={0:'joint_vision'})
    image=tmp_path/'crop.png';png=BytesIO();Image.new('RGB',(8,8),'red').save(png,format='PNG');image.write_bytes(png.getvalue())
    url='/static/test_uploads/research/crop.png';pages[0]['markdown']+='\n![插图]('+url+')';pages[0]['figures']=[{'id':'f1','image_path':url,'bbox':[1,2,3,4]}]
    source=re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->','',pages[0]['markdown'].strip())
    source,pages,evidence,proof=bind(positive_pdf,result,source=source,pages=pages,asset_paths={url:image})
    assert proof['status']=='ready' and proof['asset_count']==1 and not proof['pages'][0]['reliable']
    if mutation=='replace':image.write_bytes(b'changed')
    elif mutation=='delete':image.unlink()
    else:
        replacement=tmp_path/'replacement.png';replacement.write_bytes(image.read_bytes());image.unlink();image.symlink_to(replacement)
    assert verify_pdf_source_review_evidence(source,{},evidence,source_document_sha256=hashlib.sha256(positive_pdf).hexdigest(),source_pages=pages,layout_result=None)['status']=='uncertain'


def test_public_json_or_changed_private_signature_does_not_authorize_native_trust(positive_pdf):
    result=extract(positive_pdf);source,pages,evidence,_=bind(positive_pdf,result)
    with pytest.raises(TypeError):json.dumps(evidence)
    payload=json.loads(evidence.payload);payload['pages'][0]['reliable']=False
    for forged in [asdict(evidence),replace(evidence,signature='0'*64),replace(evidence,payload=json.dumps(payload))]:
        assert verify_pdf_source_review_evidence(source,{},forged,source_document_sha256=hashlib.sha256(positive_pdf).hexdigest(),source_pages=pages,layout_result=None)['status']=='uncertain'


def test_missing_selected_page_and_foreign_page_marker_are_global_uncertainty():
    blob=make_pdf(pages=2);result=extract(blob);source,pages=source_state(result)
    bad=deepcopy(pages);bad[0]['markdown']=bad[0]['markdown'].replace('PAGE:1','PAGE:2')
    wrong_source=re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->','',merge_pdf_page_texts([r['markdown'] for r in bad]))
    proof=bind(blob,result,source=wrong_source,pages=bad)[3]
    assert proof['status']=='uncertain' and 'source_page_marker_identity_unproved' in proof['global_reasons']
    short_source=re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->','',pages[0]['markdown'].strip())
    assert bind(blob,result,source=short_source,pages=pages[:1])[3]['status']=='uncertain'


def test_optional_native_producer_failure_preserves_original_extraction(positive_pdf,monkeypatch):
    import mathbank.pdf_source_scopes as scopes
    baseline=inspect_and_extract_pdf(positive_pdf)
    monkeypatch.setattr(scopes,'_capture_native_source_evidence',lambda *a,**kw:(_ for _ in ()).throw(ValueError('fixture')))
    result=extract(positive_pdf)
    assert {k:v for k,v in result.items() if not k.startswith('_')}==baseline
    assert result['_native_source_review_evidence'] is None


def test_real_cm_script_repair_requires_reproduced_glyph_consumption_not_reported_complete(monkeypatch):
    import mathbank.pdf_inspector_helper as helper
    blob=(Path(__file__).parent/'fixtures/pdf_source_scopes/supported_cm_script.pdf').read_bytes()
    # A backend rejection only triggers the existing repair. The proof itself
    # must read the real embedded CM glyph geometry and reproduce all output.
    monkeypatch.setattr(helper,'_PDF_INSPECTOR_AVAILABLE',True)
    monkeypatch.setattr(helper.pdf_inspector,'extract_pages_markdown',lambda *a,**kw:SimpleNamespace(pages=[
        SimpleNamespace(page=0,markdown='unreliable pre-repair text',needs_ocr=True)]))
    result=extract(blob)
    assert result['pages'][0]['source']=='native-math-repaired'
    assert '$x^{2}=1$' in result['pages'][0]['markdown']
    source,pages,evidence,proof=bind(blob,result,origins={0:'native_repaired'})
    assert proof['status']=='ready' and proof['pages'][0]['reliable']
    stats=proof['pages'][0]['glyph_summary']
    assert stats['kind']=='completed_native_math_repair' and stats['glyphs_total']==stats['glyphs_consumed']
    assert stats['superscripts']==1


def test_false_native_repair_status_on_plain_page_cannot_mint_supported_math(positive_pdf,monkeypatch):
    import mathbank.pdf_inspector_helper as helper
    result=inspect_and_extract_pdf(positive_pdf)
    row=deepcopy(result['pages'][0]);row.update(source='native-math-repaired',native_repair={'status':'repaired','glyphs_consumed':100})
    packet=_open(_NativePdfEvidence,_capture_native_source_evidence(positive_pdf,[row]))
    assert not packet['pages'][0]['reliable'] and packet['pages'][0]['reasons']==['native_repair_not_reproduced_completely']


def test_same_font_name_on_another_page_cannot_reuse_the_previous_resource_mapping(monkeypatch):
    import mathbank.pdf_source_scopes as scopes
    class Font:
        def __init__(self,data):self.data=data
        def has_glyph(self,*a,**kw):return 7 if self.data==b'first-font' else 8
    monkeypatch.setattr(scopes.fitz,'Font',lambda *,fontbuffer:Font(fontbuffer))
    def page(xref):
        raw={'blocks':[{'type':0,'lines':[{'dir':(1,0),'spans':[{'font':'SharedFont','chars':[
            {'c':'1','origin':(10,20),'bbox':(10,10,20,22)}]}]}]}]}
        return SimpleNamespace(rect=fitz.Rect(0,0,600,800),get_text=lambda kind:deepcopy(raw),
          get_fonts=lambda **kw:[(xref,'ttf','Type0','SharedFont','font','Identity-H',0)],
          parent=SimpleNamespace(extract_font=lambda ref:('','','',b'first-font' if ref==11 else b'second-font')),
          get_texttrace=lambda:[{'font':'SharedFont','type':0,'opacity':1,'chars':[(49,7,(10,20),(10,10,20,22))]}])
    cache={}
    assert scopes._plain_text_proof(page(11),'1',cache)[0] is not None
    proof,reason,_=scopes._plain_text_proof(page(22),'1',cache)
    assert proof is None and reason=='native_font_glyph_identity_unproved'
    assert len(cache)==2


def test_malformed_late_native_diagnostics_reject_instead_of_crashing(positive_pdf):
    result=extract(positive_pdf);source,pages,evidence,_=bind(positive_pdf,result)
    assert verify_pdf_source_review_evidence(source,None,evidence,source_document_sha256=hashlib.sha256(positive_pdf).hexdigest(),source_pages=pages,layout_result=None)['status']=='uncertain'
