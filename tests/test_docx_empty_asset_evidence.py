"""Task-owned empty-preamble evidence, never a generic white-image whitelist."""
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image, PngImagePlugin
import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.docx_source_assets import (
    _uniform_empty_png, _declared_image_description, verified_empty_asset_urls,
)

RESOURCE = "学科网(www.zxxk.com)--教育资源门户，提供试卷、教案、课件、论文、素材以及各类教学资源下载，还有大量而丰富的教学相关资讯！"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def png(pixels=None, mode="RGB", size=(3,3), **kwargs):
    image = Image.new(mode, size, (255,255,255) if mode=="RGB" else (255,255,255,255))
    if pixels is not None:
        image.putdata(pixels)
    result = BytesIO();image.save(result,format="PNG",**kwargs)
    return result.getvalue()


def white_noise(channel=2,delta=1,size=(3,3)):
    colors=[]
    for i in range(size[0]*size[1]):
        color=[255,255,255]
        if i%3:color[channel]-=delta
        colors.append(tuple(color))
    return png(colors,size=size)


def drawing(description="",name="图片 100001",behind=False,extras=""):
    carrier='anchor' if behind else 'inline'
    return (f'<w:r><w:drawing><wp:{carrier} behindDoc="{int(behind)}">'
            f'<wp:docPr id="100001" name="{name}" descr="{description}"/>'
            f'<a:graphic><a:graphicData uri="{PIC}"><pic:pic><pic:blipFill>'
            '<a:blip r:embed="img"/></pic:blipFill><pic:spPr>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>' + extras +
            f'</pic:spPr></pic:pic></a:graphicData></a:graphic></wp:{carrier}></w:drawing></w:r>')


def package(body,image):
    result=BytesIO()
    with zipfile.ZipFile(result,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/></Types>')
        z.writestr("word/document.xml",f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:pic="{PIC}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels",f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="img" Type="{R}/image" Target="media/payload.png"/></Relationships>')
        z.writestr("word/media/payload.png",image)
    return result.getvalue()


def extract(tmp_path,*,description="",name="图片 100001",image=None,behind=False,extras="",placement="preamble"):
    carrier=drawing(description,name,behind,extras)
    title='<w:p>'+carrier+'<w:r><w:t>2025年高一数学期中试卷</w:t></w:r></w:p>'
    question='<w:p><w:r><w:t>1. 已知x=2，求值。</w:t></w:r></w:p>'
    if placement=="body":body=question+'<w:p>'+carrier+'</w:p>'
    elif placement=="repeated":body=title+question+'<w:p>'+carrier+'</w:p>'
    elif placement=="named_reference":body=title+'<w:p><w:r><w:t>1. 依据图片100001求值。</w:t></w:r></w:p>'
    elif placement=="description_reference":body=title+f'<w:p><w:r><w:t>1. 依据{description}求值。</w:t></w:r></w:p>'
    else:body=title+question
    result=extract_docx_markdown(package(body,image or white_noise()),output_dir=tmp_path,
        url_prefix='/static/test_uploads/empty-evidence',include_source_asset_evidence=True)
    assert result['success']
    return result


def permitted(result):
    return verified_empty_asset_urls(result['markdown'],result['_source_asset_evidence'],result['image_paths'])


@pytest.mark.parametrize("channel",[0,1,2])
def test_only_one_channel_one_level_white_noise_has_no_visible_foreground(channel):
    assert _uniform_empty_png(white_noise(channel))


@pytest.mark.parametrize("variant",['two_channels','two_levels','gray','black','glyph','alpha','large','icc','gamma','text'])
def test_nonwhite_nonopaque_modified_or_metadata_bearing_png_is_not_empty(variant):
    if variant=='two_channels':data=png([(255,255,255),(254,254,255)]*4+[(255,255,255)])
    elif variant=='two_levels':data=white_noise(delta=2)
    elif variant=='gray':data=png([(230,230,230)]*9)
    elif variant=='black':data=png([(0,0,0)]*9)
    elif variant=='glyph':data=png([(255,255,255)]*8+[(0,0,0)])
    elif variant=='alpha':data=png([(255,255,255,0)]*9,mode='RGBA')
    elif variant=='large':data=white_noise(size=(65,3))
    elif variant=='icc':data=png(icc_profile=b'nonstandard color profile')
    else:
        info=PngImagePlugin.PngInfo()
        if variant=='text':info.add_text('Comment','x>0')
        else:info.add(b'gAMA',(100000).to_bytes(4,'big'))
        data=png(pnginfo=info)
    assert not _uniform_empty_png(data)


@pytest.mark.parametrize("description",["",RESOURCE,"教育资源网(example.org)--教育资源门户，提供教案、课件以及各类教学资源下载，还有教学相关资讯。","图片 100001","Picture 2"])
def test_declared_empty_preamble_keeps_source_image_and_description_proof(tmp_path,description):
    result=extract(tmp_path,description=description)
    assert permitted(result)==set(result['image_paths'])
    assert result['image_paths'][0] in result['markdown']
    records=result['_source_asset_evidence'].empty_rasters
    assert len(records)==1
    if description:assert any(entry[2]==description for entry in records[0].descriptions)


@pytest.mark.parametrize("description",["定义域x>0",RESOURCE+"，各题共用x>0", "学科网(www.zxxk.com)","根据图1回答下列各题", "图片1，红色表示x"])
def test_unknown_or_mathematical_alt_is_not_discardable_even_with_white_pixels(tmp_path,description):
    result=extract(tmp_path,description=description)
    assert permitted(result)==set()


@pytest.mark.parametrize("placement",['body','repeated','named_reference'])
def test_nonleading_reused_or_named_question_reference_is_not_header_metadata(tmp_path,placement):
    assert permitted(extract(tmp_path,placement=placement))==set()


def test_description_picture_number_referenced_by_question_is_not_ignored(tmp_path):
    assert permitted(extract(tmp_path,description="图片1",placement="description_reference"))==set()


@pytest.mark.parametrize("extras",['<a:ln/>','<a:effectLst><a:outerShdw/></a:effectLst>','<a:solidFill/>','<a:txBody/>'])
def test_vector_frame_effect_or_text_box_around_white_raster_is_not_empty(tmp_path,extras):
    assert permitted(extract(tmp_path,extras=extras))==set()


def test_behind_document_background_is_not_header_metadata(tmp_path):
    assert permitted(extract(tmp_path,behind=True))==set()


def test_pending_source_asset_evidence_cannot_be_forged_or_survive_mutation(tmp_path):
    result=extract(tmp_path)
    assert not verified_empty_asset_urls(result['markdown'],{},result['image_paths'])
    assert not verified_empty_asset_urls(result['markdown']+' extra',result['_source_asset_evidence'],result['image_paths'])
    assert not verified_empty_asset_urls(result['markdown'],result['_source_asset_evidence'],[])
    record=result['_source_asset_evidence'].empty_rasters[0]
    Path(record.path).write_bytes(png([(0,0,0)]*9))
    assert permitted(result)==set()
