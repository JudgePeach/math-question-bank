"""Paragraph-owned question figures must not enter a preceding answer."""
from io import BytesIO
from pathlib import Path
import hashlib
import zipfile

from PIL import Image
import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.content_locks import _source_parts,lock_visible_math

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WP='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
M='http://schemas.openxmlformats.org/officeDocument/2006/math'
PIC='http://schemas.openxmlformats.org/drawingml/2006/picture'


def raster():
    data=BytesIO();Image.new('RGB',(8,8),'red').save(data,format='PNG');return data.getvalue()


def picture(*,vertical='paragraph',y='400',horizontal='column',x='700',height='1600',behind='0',carrier='anchor',other=''):
    return ('<w:r><w:rPr/>'
      f'<w:drawing><wp:{carrier} simplePos="0" behindDoc="{behind}">'
      f'<wp:positionH relativeFrom="{horizontal}"><wp:posOffset>{x}</wp:posOffset></wp:positionH>'
      f'<wp:positionV relativeFrom="{vertical}"><wp:posOffset>{y}</wp:posOffset></wp:positionV>'
      f'<wp:extent cx="1600" cy="{height}"/><wp:docPr id="1" name="Picture 1"/>'
      f'<a:graphic><a:graphicData uri="{PIC}"><pic:pic><pic:blipFill><a:blip r:embed="img"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>'
      f'{other}</wp:{carrier}></w:drawing></w:r>')


def docx(body):
    data=BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/></Types>')
        z.writestr('word/document.xml',f'<w:document xmlns:w="{W}" xmlns:wp="{WP}" xmlns:r="{R}" xmlns:a="{A}" xmlns:m="{M}" xmlns:pic="{PIC}"><w:body>{body}</w:body></w:document>')
        z.writestr('word/_rels/document.xml.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="img" Type="{R}/image" Target="media/figure.png"/></Relationships>')
        z.writestr('word/media/figure.png',raster())
    return data.getvalue()


def extract(tmp_path,figure=None,body_extra='',heading='14．如图，已知x=2。'):
    first='<w:p><w:r><w:t>13．已知x=1。</w:t></w:r></w:p><w:p><w:r><w:t>【答案】1</w:t></w:r></w:p>'
    current='<w:p>'+(figure or picture())+f'<w:r><w:t>{heading}</w:t></w:r>'+body_extra+'<w:r><w:t>（本题图注）</w:t></w:r></w:p>'
    return extract_docx_markdown(docx(first+current),output_dir=tmp_path,url_prefix='/static/test_uploads/heading-figures')


@pytest.mark.parametrize('horizontal',['column','margin'])
@pytest.mark.parametrize('y',['0','400'])
def test_verified_question_anchor_moves_only_ordinal_not_image_stem_or_caption(tmp_path,horizontal,y):
    result=extract(tmp_path,picture(horizontal=horizontal,y=y))
    assert result['success'] and result['diagnostics']['review_required']==0
    url=result['image_paths'][0]
    assert f'14．\n\n![]({url})\n\n如图，已知x=2。（本题图注）' in result['markdown']
    _,locks=lock_visible_math(result['markdown'],'heading')
    parts=_source_parts(result['markdown'],locks)
    q14=next(p for p in parts if p.field=='content' and p.number==14)
    answer13=next(p for p in parts if p.field=='answer_markdown' and p.number==13)
    assert url in q14.text and url not in answer13.text
    assert hashlib.sha256((tmp_path/Path(url).name).read_bytes()).hexdigest()==hashlib.sha256(raster()).hexdigest()
    assert result['diagnostics']['heading_image_ownership'][0]['status']=='same_paragraph_heading_order_corrected'


def test_spread_runs_keep_heading_identity_and_exact_native_formula(tmp_path):
    body='<w:p>'+picture()+'<w:r><w:t>14</w:t></w:r><w:r><w:t>．如图，已知</w:t></w:r><m:oMath><m:r><m:t>x+2=3</m:t></m:r></m:oMath><w:r><w:t>求值。（图注）</w:t></w:r></w:p>'
    result=extract_docx_markdown(docx(body),output_dir=tmp_path,url_prefix='/static/test_uploads/heading-figures')
    assert result['markdown'].startswith('14．\n\n![](')
    assert '$x+2=3$' in result['markdown']
    assert result['markdown'].endswith('求值。（图注）')
    assert result['diagnostics']['omml_converted']==1 and result['diagnostics']['review_required']==0


@pytest.mark.parametrize('score',['（本小题满分13分）','(本小题满分13分)','（本题满分13分）','（满分13分）','（13分）'])
def test_complete_administrative_score_shell_preserves_original_text_and_picture_role(tmp_path,score):
    result=extract(tmp_path,heading='14.'+score+'如图，已知x=2。')
    assert result['diagnostics']['review_required']==0
    url=result['image_paths'][0]
    assert f'14.\n\n![]({url})\n\n'+score+'如图，已知x=2。（本题图注）' in result['markdown']
    parts=_source_parts(result['markdown'],[])
    assert url in next(p.text for p in parts if p.field=='content' and p.number==14)
    assert url not in next(p.text for p in parts if p.field=='answer_markdown' and p.number==13)


@pytest.mark.parametrize('score',[
    '（本小题满分13分，且x&gt;0）','（本小题满分13分，共用上题条件）',
    '（本小题满分N分）','（本小题满分13+2分）','（本小题满分13分说明）',
    '（本小题满分13分','（本小题满分13分]','（本小题满分13分）未知条件，',
])
def test_unknown_or_mathematical_score_parenthesis_does_not_advertise_a_verified_heading(tmp_path,score):
    result=extract(tmp_path,heading='14.'+score+'如图，已知x=2。')
    url=result['image_paths'][0]
    assert result['markdown'].index(url)<result['markdown'].index('14.')
    assert not result['diagnostics'].get('heading_image_ownership')


def test_administrative_score_shell_cannot_override_an_unknown_anchor(tmp_path):
    result=extract(tmp_path,picture(vertical='page'),heading='14.（本小题满分13分）如图，已知x=2。')
    assert result['diagnostics']['review_required']==1
    assert result['diagnostics']['heading_image_ownership'][0]['reason']=='vertical_reference_not_paragraph'


@pytest.mark.parametrize('kwargs',[
    {'y':'-1'},{'x':'-1'},{'vertical':'page'},{'horizontal':'page'},
    {'y':'1601'},{'height':'0'},{'height':'invalid'},{'y':'invalid'},
    {'y':'2147483648'},{'behind':'1'},{'carrier':'inline'},
    {'other':'<w:txbxContent><w:p><w:r><w:t>上题说明</w:t></w:r></w:p></w:txbxContent>'},
    {'other':'<a:chart/>'},{'other':'<a:svgBlip/>'},
])
def test_uncertain_heading_anchor_is_not_reassigned_and_blocks_certification(tmp_path,kwargs):
    result=extract(tmp_path,picture(**kwargs))
    assert result['success'] and result['diagnostics']['review_required']==1
    url=result['image_paths'][0]
    assert result['markdown'].index(url)<result['markdown'].index('14．')
    review=result['diagnostics']['heading_image_ownership'][0]
    assert review['source_number']==14 and review['image_path']==url and review['status']=='ownership_uncertain'
    assert len(result['diagnostics']['warnings'])==1
    assert url in result['diagnostics']['warnings'][0]


@pytest.mark.parametrize('mutation',[
    lambda p:p.replace('<wp:positionV relativeFrom="paragraph"><wp:posOffset>400</wp:posOffset></wp:positionV>',''),
    lambda p:p.replace('<wp:extent cx="1600" cy="1600"/>',''),
    lambda p:p.replace('<wp:posOffset>400</wp:posOffset>','<wp:align>center</wp:align>'),
    lambda p:p.replace('simplePos="0"','simplePos="1"'),
    lambda p:p.replace('name="Picture 1"','name="Picture 1" descr="上一题配图"'),
    lambda p:p.replace('name="Picture 1"','name="Picture 1" descr="第13题配图"'),
])
def test_incomplete_or_conflicting_anchor_metadata_remains_local_review(tmp_path,mutation):
    result=extract(tmp_path,mutation(picture()))
    assert result['diagnostics']['review_required']==1
    assert result['diagnostics']['heading_image_ownership'][0]['status']=='ownership_uncertain'


def test_two_leading_unknown_figures_are_not_silently_merged_into_this_question(tmp_path):
    result=extract(tmp_path,picture()+picture())
    assert result['diagnostics']['review_required']==1
    assert result['markdown'].index(result['image_paths'][0])<result['markdown'].index('14．')


@pytest.mark.parametrize('wrapper',['sdt','customXml'])
def test_second_visible_vml_picture_in_structured_wrapper_is_not_certified(tmp_path,wrapper):
    pict='<w:r><w:pict xmlns:v="urn:schemas-microsoft-com:vml"><v:shape><v:imagedata r:id="img"/></v:shape></w:pict></w:r>'
    extra=('<w:sdt><w:sdtContent>'+pict+'</w:sdtContent></w:sdt>' if wrapper=='sdt'
           else '<w:customXml>'+pict+'</w:customXml>')
    result=extract(tmp_path,body_extra=extra)
    assert result['markdown'].count('![](')==2
    assert result['diagnostics']['review_required']==1
    assert result['diagnostics']['heading_image_ownership'][0]['reason']=='multiple_visible_image_outputs'
    assert result['markdown'].index(result['image_paths'][0])<result['markdown'].index('14．')


def test_second_visible_question_boundary_vetoes_local_reassignment(tmp_path):
    result=extract(tmp_path,heading='14．如图求值。15．已知另一条件。')
    assert result['diagnostics']['review_required']==1
    assert result['diagnostics']['heading_image_ownership'][0]['reason']=='another_visible_numbered_boundary_in_paragraph'


def test_ordinary_template_heading_and_no_picture_paragraph_are_not_scanned(tmp_path):
    result=extract(tmp_path,heading='14．参考答案')
    assert result['diagnostics']['review_required']==0
    assert 'heading_image_ownership' not in result['diagnostics']


@pytest.mark.parametrize('label',['答案','解析','详解'])
@pytest.mark.parametrize('bold',[False,True])
def test_complete_same_paragraph_answer_label_precedes_its_verified_picture(tmp_path,label,bold):
    fig=picture(horizontal='margin').replace('<wp:posOffset>700</wp:posOffset>','<wp:align>right</wp:align>')
    style='<w:rPr><w:b/></w:rPr>' if bold else ''
    body=('<w:p><w:r><w:t>17．已知条件，求值。</w:t></w:r></w:p>'
          '<w:p>'+fig+f'<w:r>{style}<w:t>【{label}】</w:t></w:r>'
          '<w:r><w:t>（解图说明）</w:t></w:r><m:oMath><m:r><m:t>x+2=3</m:t></m:r></m:oMath></w:p>')
    result=extract_docx_markdown(docx(body),output_dir=tmp_path)
    token=f'\\textbf{{【{label}】}}' if bold else f'【{label}】'
    url=result['image_paths'][0]
    assert token+'\n\n![]('+url+')\n\n（解图说明） $x+2=3$' in result['markdown']
    assert result['diagnostics']['review_required']==0
    assert result['diagnostics']['heading_image_ownership'][0]['status']=='same_paragraph_answer_label_order_corrected'
    _,locks=lock_visible_math(result['markdown'],'answer_picture')
    parts=_source_parts(result['markdown'],locks)
    assert url in next(p.text for p in parts if p.number==17 and p.field=='answer_markdown')
    assert url not in next(p.text for p in parts if p.number==17 and p.field=='content')
    assert hashlib.sha256((tmp_path/Path(url).name).read_bytes()).hexdigest()==hashlib.sha256(raster()).hexdigest()


@pytest.mark.parametrize('figure,label,reason',[
    (picture(y='-1'),'【解析】','negative_or_distant_paragraph_anchor'),
    (picture(vertical='page'),'【解析】','vertical_reference_not_paragraph'),
    (picture()+picture(),'【解析】','multiple_or_unknown_picture_carriers'),
    (picture().replace('name="Picture 1"','name="Picture 1" descr="第16题配图"'),'【解析】','conflicting_question_description'),
    (picture(horizontal='margin').replace('<wp:posOffset>700</wp:posOffset>','<wp:align>center</wp:align>'),'【解析】','anchor_position_or_extent_incomplete'),
    (picture(),'【解析】正文【答案】','multiple_answer_labels_in_paragraph'),
])
def test_uncertain_answer_picture_keeps_order_and_reports_one_local_review(tmp_path,figure,label,reason):
    body='<w:p><w:r><w:t>17．已知条件。</w:t></w:r></w:p><w:p>'+figure+f'<w:r><w:t>{label}</w:t></w:r></w:p>'
    result=extract_docx_markdown(docx(body),output_dir=tmp_path)
    assert result['diagnostics']['review_required']==1
    assert result['markdown'].index(result['image_paths'][0])<result['markdown'].index('【解析】')
    record=result['diagnostics']['heading_image_ownership'][0]
    assert record['field']=='answer_markdown' and record['reason']==reason and record['status']=='ownership_uncertain'
    assert len(result['diagnostics']['warnings'])==1


def test_incompletely_bold_wrapped_answer_label_is_not_split_into_invalid_tex(tmp_path):
    body='<w:p>'+picture()+'<w:r><w:rPr><w:b/></w:rPr><w:t>【解析】仍同一加粗段</w:t></w:r></w:p>'
    result=extract_docx_markdown(docx(body),output_dir=tmp_path)
    assert result['diagnostics']['review_required']==1
    assert result['diagnostics']['heading_image_ownership'][0]['reason']=='rendered_heading_not_safely_separable'
    assert '\\textbf{【解析】仍同一加粗段}' in result['markdown']


@pytest.mark.parametrize('next_has_picture,current_refers_picture,review',[
    (False,False,1),(True,False,0),(False,True,0),
])
def test_adjacent_unplaced_figure_reference_vetoes_guessing_across_paragraphs(tmp_path,next_has_picture,current_refers_picture,review):
    heading='13．在数列中求值。'+('见图。' if current_refers_picture else '')
    body=('<w:p>'+picture()+f'<w:r><w:t>{heading}</w:t></w:r></w:p>'
          '<w:p><w:r><w:t>14．如图，已知条件。</w:t></w:r>'+ (picture() if next_has_picture else '')+'</w:p>')
    result=extract_docx_markdown(docx(body),output_dir=tmp_path)
    assert result['diagnostics']['review_required']==review
    record=result['diagnostics']['heading_image_ownership'][0]
    if review:
        assert record['status']=='ownership_uncertain' and record['reason']=='following_question_refers_unplaced_figure'
        assert record['possible_adjacent_source_number']==14
        assert result['markdown'].index(result['image_paths'][0])<result['markdown'].index('13．')
    else:
        assert record['status']=='same_paragraph_heading_order_corrected'
    # No condition moves this paragraph's original picture into the next one.
    assert result['markdown'].index(result['image_paths'][0])<result['markdown'].index('14．')


@pytest.mark.parametrize('figure,reason',[
    (picture(vertical='page'),'vertical_reference_not_paragraph'),
    (picture(y='-1'),'negative_or_distant_paragraph_anchor'),
    (picture().replace('<wp:extent cx="1600" cy="1600"/>',''),'anchor_position_or_extent_incomplete'),
])
def test_adjacent_question_hint_does_not_turn_an_unknown_anchor_into_bounded_ownership(tmp_path,figure,reason):
    from mathbank.docx_source_scopes import verify_source_review_evidence
    body=('<w:p>'+figure+'<w:r><w:t>13．在数列中求值。</w:t></w:r></w:p>'
          '<w:p><w:r><w:t>14．如图，已知条件。</w:t></w:r></w:p>')
    blob=docx(body)
    result=extract_docx_markdown(blob,output_dir=tmp_path,include_source_review_evidence=True)
    record=result['diagnostics']['heading_image_ownership'][0]
    assert result['diagnostics']['review_required']==1
    assert record['reason']==reason and record['reason']!='following_question_refers_unplaced_figure'
    assert record['possible_adjacent_source_number']==14
    assert result['markdown'].index(result['image_paths'][0])<result['markdown'].index('13．')
    scope=verify_source_review_evidence(result['markdown'],result['diagnostics'],result['_source_review_evidence'])
    assert scope['status']=='ready' and scope['blocks'][0]['has_risk']
    assert scope['blocks'][0]['heading_image_ownership'][0]['reason']==reason
    assert scope['blocks'][0]['risk_related_numbers']==[13,14]
    result=extract_docx_markdown(docx('<w:p><w:r><w:t>14．如图，已知x=2。</w:t></w:r></w:p>'),output_dir=tmp_path)
    assert result['diagnostics']['review_required']==0
    assert 'heading_image_ownership' not in result['diagnostics']
