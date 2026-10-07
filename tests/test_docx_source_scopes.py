"""A private native journal must account for every risk without rewriting source."""
from dataclasses import asdict, replace
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import zipfile

from PIL import Image

import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.docx_source_scopes import verify_source_review_evidence

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M='http://schemas.openxmlformats.org/officeDocument/2006/math'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
A='http://schemas.openxmlformats.org/drawingml/2006/main'


def package(body, *, relationships='', image=None):
    data=BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr('word/document.xml',f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>{body}</w:body></w:document>')
        z.writestr('word/_rels/document.xml.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>')
        if image is not None:z.writestr('word/media/figure.png',image)
    return data.getvalue()


def paragraph(text):
    return '<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'


BAD='<w:p><w:r><w:t>2. 求值：</w:t></w:r><m:oMath><m:unsupportedFixture><m:r><m:t>x+1</m:t></m:r></m:unsupportedFixture></m:oMath></w:p>'


def native(tmp_path,body=None,**kwargs):
    blob=package(body or paragraph('1. 已知x=2。')+BAD+paragraph('3. 求y=3。'))
    result=extract_docx_markdown(blob,output_dir=tmp_path,include_source_review_evidence=True,**kwargs)
    return blob,result


def verified(blob,result):
    return verify_source_review_evidence(result['markdown'],result['diagnostics'],result.get('_source_review_evidence'),source_document_sha256=hashlib.sha256(blob).hexdigest())


def test_optional_native_journal_does_not_change_source_or_diagnostics(tmp_path):
    blob,result=native(tmp_path)
    baseline=extract_docx_markdown(blob,output_dir=tmp_path)
    assert '_source_review_evidence' not in baseline
    assert {k:v for k,v in result.items() if not k.startswith('_')}==baseline
    assert verified(blob,result)['status']=='ready'
    assert result['diagnostics']['review_required']==1


def test_all_source_characters_and_global_risk_tally_have_original_block_owners(tmp_path):
    blob,result=native(tmp_path)
    proof=verified(blob,result)
    blocks=proof['blocks']
    assert [b['body_child_index'] for b in blocks]==[0,1,2]
    assert [b['has_risk'] for b in blocks]==[False,True,False]
    assert blocks[0]['range'][0]==0 and blocks[-1]['range'][1]==len(result['markdown'])
    assert all(a['range'][1]==b['range'][0] for a,b in zip(blocks,blocks[1:]))
    assert ''.join(result['markdown'][slice(*b['range'])] for b in blocks)==result['markdown']
    assert blocks[1]['unsupported_omml_tags']==['unsupportedFixture']
    assert blocks[1]['derived_aggregate_warning'] in result['diagnostics']['warnings']
    for b in blocks:assert hashlib.sha256(result['markdown'][slice(*b['range'])].encode()).hexdigest()==b['source_sha256']
    for counter in ('review_required','omml_unsupported'):
        assert sum(b['risk_counters'].get(counter,0) for b in blocks)==result['diagnostics'][counter]


def test_adjacent_figure_risk_binds_both_question_numbers_to_the_private_scope(tmp_path):
    picture=('<w:r><w:drawing><wp:anchor simplePos="0" behindDoc="0">'
        '<wp:positionH relativeFrom="column"><wp:posOffset>700</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>400</wp:posOffset></wp:positionV>'
        '<wp:extent cx="1600" cy="1600"/><wp:docPr id="1" name="Picture 1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:blipFill><a:blip r:embed="img"/></pic:blipFill></pic:pic>'
        '</a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>')
    png=BytesIO();Image.new('RGB',(8,8),'red').save(png,format='PNG')
    body='<w:p>'+picture+'<w:r><w:t>13．在数列中求值。</w:t></w:r></w:p>'+paragraph('14．如图，求长度。')
    blob=package(body,relationships=f'<Relationship Id="img" Type="{R}/image" Target="media/figure.png"/>',image=png.getvalue())
    result=extract_docx_markdown(blob,output_dir=tmp_path,include_source_review_evidence=True)
    proof=verified(blob,result)
    assert proof['status']=='ready' and result['diagnostics']['review_required']==1
    assert proof['risk_related_numbers']==[13,14]
    assert proof['blocks'][0]['risk_related_numbers']==[13,14]
    assert proof['blocks'][0]['has_risk'] and not proof['blocks'][1]['has_risk']
    assert proof['blocks'][0]['heading_image_ownership'][0]['possible_adjacent_source_number']==14


def test_native_snapshot_ignores_only_additional_task_fields(tmp_path):
    blob,result=native(tmp_path)
    diag=deepcopy(result['diagnostics']);diag.update(math_locks_created=50,word_extraction_warnings=['task copy'],source_review_count=1)
    assert verify_source_review_evidence(result['markdown'],diag,result['_source_review_evidence'])['status']=='ready'


def test_fixed_snapshot_covers_every_native_diagnostic_key():
    from mathbank.docx_helper import _new_diagnostics
    from mathbank.docx_source_scopes import _NATIVE_DIAGNOSTIC_KEYS
    assert set(_new_diagnostics())<=set(_NATIVE_DIAGNOSTIC_KEYS)


@pytest.mark.parametrize('mutation',['replace','delete','symlink'])
def test_every_source_raster_is_revalidated_not_just_empty_header_images(tmp_path,mutation):
    png=BytesIO();Image.new('RGB',(8,8),'red').save(png,format='PNG')
    blob=package(paragraph('1. 如图求值。')+'<w:p><w:r><w:drawing><a:blip r:embed="img"/></w:drawing></w:r></w:p>',
        relationships=f'<Relationship Id="img" Type="{R}/image" Target="media/figure.png"/>',image=png.getvalue())
    result=extract_docx_markdown(blob,output_dir=tmp_path,include_source_review_evidence=True)
    proof=verified(blob,result)
    assert proof['status']=='ready' and proof['asset_count']==1
    assert 'absolute_path' not in json.dumps(proof)
    saved=tmp_path/result['image_paths'][0].rsplit('/',1)[-1]
    if mutation=='replace':saved.write_bytes(b'different source asset')
    elif mutation=='delete':saved.unlink()
    else:
        alternate=tmp_path/'alternate.png';alternate.write_bytes(saved.read_bytes());saved.unlink();saved.symlink_to(alternate)
    assert verified(blob,result)['status']=='uncertain'


def test_repeated_deduplicated_warning_tags_still_mark_each_bad_paragraph(tmp_path):
    blob,result=native(tmp_path,BAD+BAD+paragraph('3. 独立题。'))
    proof=verified(blob,result)
    assert proof['status']=='ready'
    assert [b['has_risk'] for b in proof['blocks']]==[True,True,False]
    assert sum(b['risk_counters'].get('review_required',0) for b in proof['blocks'])==2
    assert len(result['diagnostics']['warnings'])==1


def test_body_content_control_remains_a_conservative_atomic_risk_owner(tmp_path):
    body=paragraph('1. 第一题。')+'<w:sdt><w:sdtContent>'+paragraph('2. 第二题。')+BAD+'</w:sdtContent></w:sdt>'+paragraph('3. 第三题。')
    blob,result=native(tmp_path,body)
    proof=verified(blob,result)
    assert proof['status']=='ready' and proof['blocks'][1]['has_risk']
    assert proof['blocks'][1]['output_block_indices']==[1,3]
    assert '第二题' in result['markdown'][slice(*proof['blocks'][1]['range'])]


def test_path_input_is_bound_to_its_original_bytes_and_preserved(tmp_path):
    source=tmp_path/'原卷.docx';blob=package(paragraph('1. 求值。'));source.write_bytes(blob)
    result=extract_docx_markdown(source,output_dir=tmp_path/'assets',include_source_review_evidence=True)
    assert verified(blob,result)['status']=='ready' and source.read_bytes()==blob


def test_pre_body_numbering_or_relationship_warning_cannot_sign_local_cleanliness(tmp_path,monkeypatch):
    from mathbank import docx_helper
    original=docx_helper._extract_numbering
    def numbered(z,diag):
        result=original(z,diag);diag['numbering_unavailable']+=1;diag['warnings'].append('未知编号定义');return result
    monkeypatch.setattr(docx_helper,'_extract_numbering',numbered)
    blob,result=native(tmp_path,paragraph('1. 求值。'))
    proof=verified(blob,result)
    assert result['success'] and result['diagnostics']['numbering_unavailable']==1
    assert proof['status']=='uncertain' and not proof['blocks']
    assert 'pre_body_review_origin_unlocated' in proof['unlocated_reasons']


def test_missing_image_in_an_empty_block_cannot_be_located_by_neighbor_text(tmp_path):
    image='<w:p><w:r><w:drawing><a:blip r:embed="missing"/></w:drawing></w:r></w:p>'
    blob=package(image+paragraph('1. 求值。'),relationships=f'<Relationship Id="missing" Type="{R}/image" Target="media/missing.png"/>')
    result=extract_docx_markdown(blob,output_dir=tmp_path,include_source_review_evidence=True)
    assert result['success'] and result['diagnostics']['images_unavailable']
    proof=verified(blob,result)
    assert proof['status']=='uncertain' and not proof['blocks']
    assert 'empty_output_review_origin_unlocated' in proof['unlocated_reasons']


def test_unexpected_after_loop_warning_keeps_the_whole_source_uncertain(tmp_path,monkeypatch):
    from mathbank import docx_source_assets
    original=docx_source_assets.finalize_word_asset_evidence
    def changed(source,diag):
        diag['warnings'].append('无法归属的最终风险');return original(source,diag)
    monkeypatch.setattr(docx_source_assets,'finalize_word_asset_evidence',changed)
    blob,result=native(tmp_path,paragraph('1. 求值。'))
    assert verified(blob,result)['status']=='uncertain'
    assert 'final_review_warning_not_accounted' in verified(blob,result)['unlocated_reasons']


def test_nonlocal_normalization_changes_do_not_create_approximate_ranges(tmp_path,monkeypatch):
    from mathbank import ai_json
    original=ai_json.normalize_subquestions_double_newlines
    monkeypatch.setattr(ai_json,'normalize_subquestions_double_newlines',lambda s:original(s).replace('甲\n\n乙\n\n丙','甲乙丙'))
    blob,result=native(tmp_path,paragraph('甲')+paragraph('乙')+paragraph('丙'))
    assert result['markdown']=='甲乙丙'
    proof=verified(blob,result)
    assert proof['status']=='uncertain' and not proof['blocks']
    assert 'per_block_normalization_not_equal_to_source' in proof['unlocated_reasons']


def test_proven_cross_paragraph_subquestion_normalization_has_one_atomic_origin_window(tmp_path):
    body=paragraph('1. 已知条件。')+paragraph('（1）求第一问；')+paragraph('（2）求第二问。')+paragraph('2. 独立题。')
    blob,result=native(tmp_path,body)
    baseline=extract_docx_markdown(blob,output_dir=tmp_path)
    assert result['markdown']==baseline['markdown']
    proof=verified(blob,result)
    assert proof['status']=='ready' and proof['blocks'][0]['body_child_indices']==[0,1,2]
    assert [o['body_child_index'] for o in proof['blocks'][0]['origins']]==[0,1,2]
    assert result['markdown'][slice(*proof['blocks'][0]['range'])].endswith('（2） 求第二问。\n\n')
    assert proof['blocks'][1]['body_child_index']==3


def test_floating_word_table_is_a_scope_risk_without_rewriting_default_diagnostics(tmp_path):
    table=('<w:tbl><w:tblPr><w:tblpPr w:horzAnchor="page" w:vertAnchor="page" w:tblpX="0" w:tblpY="0"/></w:tblPr>'
           '<w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:tc>'+paragraph('数据')+'</w:tc></w:tr></w:tbl>')
    blob,result=native(tmp_path,paragraph('1. 统计数据如下：')+table+paragraph('根据上表求值。'))
    baseline=extract_docx_markdown(blob,output_dir=tmp_path)
    assert {k:v for k,v in result.items() if not k.startswith('_')}==baseline
    assert result['diagnostics']['review_required']==0
    proof=verified(blob,result)
    assert proof['status']=='ready' and proof['blocks'][1]['has_risk']
    assert proof['blocks'][1]['structural_risks']==['floating_table_anchor_ownership']
    assert proof['blocks'][1]['origins'][0]['structural_risks']==['floating_table_anchor_ownership']


@pytest.mark.parametrize('field',['review_required','images_extracted','warnings','unsupported_omml_tags','heading_image_ownership'])
def test_changed_diagnostic_snapshot_cannot_reuse_the_private_proof(tmp_path,field):
    blob,result=native(tmp_path)
    diag=deepcopy(result['diagnostics'])
    diag[field]=(diag.get(field,[])+['changed']) if field in ['warnings','unsupported_omml_tags','heading_image_ownership'] else diag[field]+1
    proof=verify_source_review_evidence(result['markdown'],diag,result['_source_review_evidence'])
    assert proof['status']=='uncertain' and not proof['blocks']


def test_changed_source_original_digest_and_public_dictionary_are_rejected(tmp_path):
    blob,result=native(tmp_path)
    evidence=result['_source_review_evidence']
    for source,cap,digest in [(result['markdown']+'x',evidence,None),(result['markdown'],asdict(evidence),None),(result['markdown'],evidence,'0'*64)]:
        proof=verify_source_review_evidence(source,result['diagnostics'],cap,source_document_sha256=digest)
        assert proof['status']=='uncertain' and not proof['blocks']
    with pytest.raises(TypeError):json.dumps(evidence)


def test_private_fields_or_scope_ranges_cannot_be_forged_with_an_old_signature(tmp_path):
    blob,result=native(tmp_path)
    evidence=result['_source_review_evidence'];payload=json.loads(evidence.payload)
    payload['blocks'][1]['has_risk']=False;payload['blocks'][1]['risk_counters']={}
    for cap in [replace(evidence,payload=json.dumps(payload)),replace(evidence,signature='0'*64),replace(evidence,payload=None)]:
        proof=verify_source_review_evidence(result['markdown'],result['diagnostics'],cap)
        assert proof['status']=='uncertain' and not proof['blocks']


def test_private_proof_from_another_runtime_is_not_portable(tmp_path,monkeypatch):
    from mathbank import docx_source_scopes
    blob,result=native(tmp_path)
    monkeypatch.setattr(docx_source_scopes,'_SECRET',b'another-runtime')
    assert verified(blob,result)['status']=='uncertain'


@pytest.mark.parametrize('function',['_review_snapshot','_finalize_source_review_evidence'])
def test_optional_journal_failure_preserves_the_normal_native_result(tmp_path,monkeypatch,function):
    from mathbank import docx_source_scopes
    blob=package(paragraph('1. 求值。'));baseline=extract_docx_markdown(blob,output_dir=tmp_path)
    def broken(*args,**kwargs):raise ValueError('fixture proof failure')
    monkeypatch.setattr(docx_source_scopes,function,broken)
    result=extract_docx_markdown(blob,output_dir=tmp_path,include_source_review_evidence=True)
    assert {k:v for k,v in result.items() if not k.startswith('_')}==baseline
    assert verified(blob,result)['status']=='uncertain'
