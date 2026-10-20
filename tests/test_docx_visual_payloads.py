"""Unconsumed Word visuals cannot silently acquire a native-source certificate."""
from copy import deepcopy
import hashlib
from io import BytesIO
import zipfile

from PIL import Image
import pytest

from mathbank.content_locks import _source_parts
from mathbank.docx_helper import extract_docx_markdown
from mathbank.docx_source_scopes import verify_source_review_evidence
from mathbank.import_review import prepare_word_extraction_reviews, WORD_DRAWING_EXTRACTION_REASON
from mathbank.source_metadata import prepare_word_source_metadata
from mathbank.word_hybrid_plan import build_word_hybrid_plan

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'
WPG = 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup'


def text(value):
    return '<w:r><w:t>' + value + '</w:t></w:r>'


def paragraph(value):
    return '<w:p>' + text(value) + '</w:p>'


def drawing(payload, uri=WPS):
    return (f'<w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/>'
            f'<wp:docPr id="1" name="Original visual"/><a:graphic><a:graphicData uri="{uri}">'
            + payload + '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')


TRIANGLE = ('<wps:wsp><wps:cNvSpPr/><wps:spPr><a:prstGeom prst="triangle"><a:avLst/>'
            '</a:prstGeom><a:ln w="12700"><a:solidFill><a:srgbClr val="000000"/>'
            '</a:solidFill></a:ln></wps:spPr><wps:bodyPr/></wps:wsp>')


def package(body, pictures=()):
    data = BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}"'
            f' xmlns:wp="{WP}" xmlns:wps="{WPS}" xmlns:wpg="{WPG}"><w:body>{body}</w:body></w:document>')
        relations = []
        for name, color in pictures:
            png = BytesIO(); Image.new('RGB', (20, 20), color).save(png, format='PNG')
            archive.writestr(f'word/media/{name}.png', png.getvalue())
            relations.append(f'<Relationship Id="{name}" Type="{R}/image" Target="media/{name}.png"/>')
        archive.writestr('word/_rels/document.xml.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join(relations) + '</Relationships>')
    return data.getvalue()


def extract(tmp_path, blob):
    result = extract_docx_markdown(blob, output_dir=tmp_path, url_prefix='/static/test_uploads/visuals',
        include_source_asset_evidence=True, include_source_review_evidence=True)
    assert result['success'], result['error']
    return result


def assert_not_whole_reliable(blob, result):
    plan = prepare_word_source_metadata(result['markdown'], result['diagnostics'],
                                        asset_evidence=result['_source_asset_evidence'])
    assert not plan['eligible']
    hybrid = build_word_hybrid_plan(result['markdown'], result['diagnostics'], task_id='visual-audit',
        generation=0, source_document_sha256=hashlib.sha256(blob).hexdigest(),
        asset_evidence=result['_source_asset_evidence'], source_review_evidence=result['_source_review_evidence'])
    assert hybrid['mode'] != 'whole_metadata'


def test_native_triangle_is_explicit_source_risk_without_body_placeholder(tmp_path):
    blob = package('<w:p>' + text('1. 计算下面几何图形的面积。') + drawing(TRIANGLE) + '</w:p>'
                   + paragraph('【解析】面积为2。') + paragraph('2. 求1+1。'))
    result = extract(tmp_path, blob)
    assert result['image_count'] == 0
    assert result['diagnostics']['review_required'] == 1
    assert result['diagnostics']['images_unavailable'] == 1
    assert '待核对' not in result['markdown']
    assert result['diagnostics']['drawing_review_details'][0]['reasons'] == ['native_shape_not_converted']
    assert_not_whole_reliable(blob, result)
    parts = [part for part in _source_parts(result['markdown'], []) if part.field == 'content']
    questions = [{'content': part.text, 'answer_markdown': ''} for part in parts]
    diagnostics = deepcopy(result['diagnostics'])
    diagnostics['source_matches'] = [{'question_index': index, 'field': part.field,
        'source_start': part.source_start, 'source_end': part.source_end,
        'source_excerpt': part.text} for index, part in enumerate(parts)]
    prepare_word_extraction_reviews(questions, diagnostics, result['markdown'],
        source_review_evidence=result['_source_review_evidence'], extraction_diagnostics=result['diagnostics'],
        source_document_sha256=hashlib.sha256(blob).hexdigest())
    assert questions[0]['source_review']['reasons'] == [WORD_DRAWING_EXTRACTION_REASON]
    assert questions[0]['source_review']['native_document_risks'][0]['drawing_review_details']
    assert not questions[1].get('source_review', {}).get('required')
    assert diagnostics['word_document_extraction_unlocated'] == 0


def test_group_keeps_every_bitmap_occurrence_and_does_not_certify_composition(tmp_path):
    group = '<wpg:wgp><a:blip r:embed="first"/><a:blip r:embed="second"/></wpg:wgp>'
    blob = package('<w:p>' + text('1. 计算结果。') + drawing(group, WPG) + '</w:p>',
                   [('first', 'red'), ('second', 'blue')])
    result = extract(tmp_path, blob)
    assert result['image_count'] == 2
    assert len(result['image_paths']) == 2
    assert result['markdown'].index(result['image_paths'][0]) < result['markdown'].index(result['image_paths'][1])
    colors = [Image.open(tmp_path / path.rsplit('/', 1)[-1]).getpixel((0, 0)) for path in result['image_paths']]
    assert colors == [(255, 0, 0), (0, 0, 255)]
    detail = result['diagnostics']['drawing_review_details'][0]
    assert detail['image_occurrences'] == result['image_paths']
    assert not detail['layout_preserved']
    assert_not_whole_reliable(blob, result)


def test_unknown_graphic_and_missing_relationship_never_silently_disappear(tmp_path):
    for payload, uri in [('<a:unknownVisual/>', 'urn:unknown-visual'), ('<a:blip r:embed="missing"/>', WPG)]:
        result = extract(tmp_path, package('<w:p>' + text('1. 求值。') + drawing(payload, uri) + '</w:p>'))
        assert result['diagnostics']['review_required'] > 0
        assert result['diagnostics']['drawing_review_details']


def test_plain_textbox_does_not_create_false_diagram_warning_or_duplicate_nested_picture(tmp_path):
    textbox = ('<wps:wsp><wps:spPr><a:prstGeom prst="rect"/></wps:spPr><wps:txbx><w:txbxContent>'
               + paragraph('1. 已知x=1。') + '</w:txbxContent></wps:txbx></wps:wsp>')
    result = extract(tmp_path, package('<w:p>' + drawing(textbox) + '</w:p>'))
    assert result['markdown'] == '1. 已知x=1。'
    assert result['diagnostics']['review_required'] == 0


def test_empty_unowned_shape_stays_document_warning_instead_of_guessing_a_question(tmp_path):
    blob = package(paragraph('1. 求值。') + '<w:p>' + drawing(TRIANGLE) + '</w:p>' + paragraph('2. 求值。'))
    result = extract(tmp_path, blob)
    proof = verify_source_review_evidence(result['markdown'], result['diagnostics'], result['_source_review_evidence'])
    assert proof['status'] == 'uncertain'
    assert 'empty_output_review_origin_unlocated' in proof['unlocated_reasons']
    assert_not_whole_reliable(blob, result)


def test_public_or_changed_drawing_evidence_does_not_assign_card_owners(tmp_path):
    blob = package('<w:p>' + text('1. 求值。') + drawing(TRIANGLE) + '</w:p>')
    result = extract(tmp_path, blob)
    native = deepcopy(result['diagnostics'])
    native['drawing_review_details'][0]['reasons'] = []
    questions = [{'content': result['markdown'], 'answer_markdown': ''}]
    diagnostics = deepcopy(native)
    prepare_word_extraction_reviews(questions, diagnostics, result['markdown'], extraction_diagnostics=native,
                                   source_review_evidence=result['_source_review_evidence'])
    assert not questions[0].get('source_review', {}).get('native_document_risks')
    assert diagnostics['word_document_extraction_unlocated'] == 1
