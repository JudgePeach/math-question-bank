"""Machine transcript integrity is separate from native mathematical truth."""
from copy import deepcopy
from pathlib import Path
import hashlib
import pytest
from PIL import Image
from mathbank.pdf_transcription_scopes import (
    begin_pdf_transcription_witness, finish_pdf_transcription_witness,
    finalize_pdf_transcription_snapshot, verify_pdf_transcription_snapshot,
)

def snapshot(tmp_path):
    image=tmp_path/'page.png';Image.new('RGB',(32,48),'white').save(image)
    result={'markdown':'1. 求$x+1$。','layout':{'figures':[],'page_complete':True,'notes':[]}}
    ticket=begin_pdf_transcription_witness(str(image),{'page_index':0})
    witness=finish_pdf_transcription_witness(ticket,result)
    pages=[{'page_number':1,'origin':'joint_vision','markdown':'<!-- MATHBANK_PDF_PAGE:1 -->\n'+result['markdown'],'figures':[]}]
    layout={'pages':[{'page_index':0,'status':'checked','figures':[],'warnings':[]}],'page_texts':[pages[0]['markdown']]}
    source='\n'+result['markdown'];diag={'pdf_layout':{'warnings':[]}}
    evidence=finalize_pdf_transcription_snapshot(source,diag,source_document_sha256='a'*64,
        source_pages=pages,layout_result=layout,witnesses={0:witness},transcript_normalizer=lambda s:s,figure_assets={})
    return source,diag,pages,layout,evidence,image

def verify(values):
    source,diag,pages,layout,evidence,_=values
    return verify_pdf_transcription_snapshot(source,diag,evidence,source_document_sha256='a'*64,
        source_pages=pages,layout_result=layout)

def test_first_pass_preservation_never_certifies_native_math(tmp_path):
    data=snapshot(tmp_path);proof=verify(data)
    assert proof['status']=='ready' and proof['pages'][0]['snapshot_eligible']
    assert proof['source_basis']=='first_pass_transcription' and proof['native_reliable'] is False
    assert proof['pages'][0]['reliable'] is False

@pytest.mark.parametrize('change',['source','page','layout','warning','image','public_flag'])
def test_changed_source_or_model_flag_cannot_reuse_private_evidence(tmp_path,change):
    data=list(snapshot(tmp_path))
    if change=='source':data[0]+=' 改写。'
    elif change=='page':data[2][0]['origin']='native'
    elif change=='layout':data[3]['pages'][0]['page_complete']=True
    elif change=='warning':data[1]['pdf_layout']['warnings']=['未知原式']
    elif change=='image':data[5].write_bytes(b'changed bytes')
    else:data[4]={'status':'ready','native_reliable':True,'page_complete':True}
    assert verify(data)['status']=='uncertain'

def test_image_change_after_input_capture_prevents_witness(tmp_path):
    image=tmp_path/'page.png';Image.new('RGB',(32,48),'white').save(image)
    input_witness=begin_pdf_transcription_witness(str(image),{'page_index':0});image.write_bytes(b'changed')
    assert finish_pdf_transcription_witness(input_witness,{'markdown':'1. 伪稿','layout':{'page_complete':True}}) is None

def test_page_complete_alone_is_not_a_completed_http_witness(tmp_path):
    source,diag,pages,layout,_,_=snapshot(tmp_path)
    evidence=finalize_pdf_transcription_snapshot(source,diag,source_document_sha256='a'*64,
        source_pages=pages,layout_result=layout,witnesses={},transcript_normalizer=lambda s:s,figure_assets={})
    proof=verify_pdf_transcription_snapshot(source,diag,evidence,source_document_sha256='a'*64,
        source_pages=pages,layout_result=layout)
    assert not proof['pages'][0]['snapshot_eligible'] and not proof['native_reliable']

@pytest.mark.parametrize('change_phase', ['before_request', 'after_request', 'after_snapshot'])
def test_changed_actual_detail_input_never_reuses_transcription(tmp_path, change_phase):
    source, diag, pages, layout, _, navigation = snapshot(tmp_path)
    detail = tmp_path / 'detail.png'
    Image.new('RGB', (24, 24), 'white').save(detail)
    descriptor = {'path': str(detail), 'sha256': hashlib.sha256(detail.read_bytes()).hexdigest()}
    if change_phase == 'before_request':
        detail.write_bytes(b'changed')
        with pytest.raises(ValueError, match='detail_changed_before_request'):
            begin_pdf_transcription_witness(navigation, {'page_index': 0}, detail_views=[descriptor])
        return
    ticket = begin_pdf_transcription_witness(navigation, {'page_index': 0}, detail_views=[descriptor])
    result = {'markdown': '1. 求$x+1$。', 'layout': {'figures': [], 'page_complete': True}}
    if change_phase == 'after_request':
        detail.write_bytes(b'changed')
        assert finish_pdf_transcription_witness(ticket, result) is None
        return
    witness = finish_pdf_transcription_witness(ticket, result)
    evidence = finalize_pdf_transcription_snapshot(source, diag, source_document_sha256='a'*64,
        source_pages=pages, layout_result=layout, witnesses={0: witness},
        transcript_normalizer=lambda s: s, figure_assets={})
    detail.write_bytes(b'changed')
    assert verify((source, diag, pages, layout, evidence, navigation))['status'] == 'uncertain'
