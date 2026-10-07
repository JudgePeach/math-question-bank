"""Unknown MTEF glyphs retain original images instead of a question-mark formula.

These tiny synthetic PNGs prove image/position conservation, not mathematical
equivalence to the intentionally unknown binary records.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import re
import struct
import zipfile

from PIL import Image
import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.docx_source_scopes import verify_source_review_evidence
from mathbank.mtef_helper import decode_mtef_formula
from mathbank.omml_helper import normalize_word_formula_latex

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    def rejected(*args, **kwargs):
        raise AssertionError('Preview fallback tests cannot call a provider.')
    monkeypatch.setattr('requests.sessions.Session.request', rejected)


def char(code, style=3):
    return bytes((2, 0, style + 128)) + struct.pack('<H', code)


def equation(records):
    return b'\x05\x01\x00\x07\x00DSMT7\x00\x00\x01\x00' + records + b'\x00\x00'


UNKNOWN_GLYPH = equation(char(ord('a')) + char(0xEF0A, 11) + char(ord('b')))
UNKNOWN_NODE = equation(b'\x14')
SAFE = equation(char(ord('x')) + char(ord('+')) + char(ord('1'), 8))


def png():
    buffer = BytesIO()
    image = Image.new('RGB', (12, 8), 'white')
    image.putpixel((5, 3), (220, 0, 0))
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def object_xml(ole='ole', preview='preview'):
    picture = f'<v:shape><v:imagedata r:id="{preview}"/></v:shape>' if preview else ''
    return '<w:r><w:object>' + picture + f'<o:OLEObject ProgID="Equation.DSMT4" r:id="{ole}"/></w:object></w:r>'


def text(value):
    return '<w:r><w:t>' + value + '</w:t></w:r>'


def package(body, *, payload=UNKNOWN_GLYPH, preview_state='valid', extra_members=None, extra_rels=''):
    members = {'word/embeddings/equation.bin': payload}
    rels = f'<Relationship Id="ole" Type="{R}/oleObject" Target="embeddings/equation.bin"/>'
    if preview_state != 'none':
        rels += f'<Relationship Id="preview" Type="{R}/image" Target="media/equation.png"/>'
        if preview_state != 'missing':
            members['word/media/equation.png'] = png() if preview_state == 'valid' else b'not a PNG'
    members.update(extra_members or {})
    data = BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>{body}</w:body></w:document>')
        archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + extra_rels + '</Relationships>')
        for part, value in members.items():
            archive.writestr(part, value)
    return data.getvalue()


def extract(blob, tmp_path):
    result = extract_docx_markdown(blob, output_dir=tmp_path, url_prefix='/static/uploads/tmp',
        include_source_asset_evidence=True, include_source_review_evidence=True)
    assert result['success']
    return result


def scope(blob, result):
    return verify_source_review_evidence(result['markdown'], result['diagnostics'],
        result['_source_review_evidence'], source_document_sha256=hashlib.sha256(blob).hexdigest())


@pytest.mark.parametrize('payload', [UNKNOWN_GLYPH, UNKNOWN_NODE])
def test_unknown_binary_or_normalized_glyph_keeps_the_original_preview_at_its_source_position(tmp_path, payload):
    if payload == UNKNOWN_GLYPH:
        decoded = decode_mtef_formula(payload)
        assert decoded.success and decoded.confidence == 'structural'
        diagnostics = {}
        normalize_word_formula_latex(decoded.latex, diagnostics)
        assert diagnostics['unsupported_math_tokens'] == ['privateUse:U+EF0A']
    else:
        assert not decode_mtef_formula(payload).success
    blob = package('<w:p>' + text('1. 已知原公式为') + object_xml() + text('，求其定义域。') + '</w:p>', payload=payload)
    result = extract(blob, tmp_path)
    diag = result['diagnostics']
    assert len(result['image_paths']) == 1
    uri = result['image_paths'][0]
    assert result['markdown'] == '1. 已知原公式为\n![](' + uri + ')\n，求其定义域。'
    assert r'\text{?}' not in result['markdown'] and '[公式结构待核对]' not in result['markdown']
    assert (tmp_path / Path(uri).name).read_bytes() == png()
    assert diag['mtef_converted'] == diag['mtef_structural_converted'] == 0
    assert diag['mtef_fallback_images'] == diag['review_required'] == diag['images_extracted'] == 1
    assert diag['mtef_unavailable'] == diag['images_unavailable'] == 0
    proof = scope(blob, result)
    assert proof['status'] == 'ready' and proof['asset_count'] == 1
    assert proof['blocks'][0]['has_risk'] and proof['blocks'][0]['risk_counters']['review_required'] == 1
    assert proof['blocks'][0]['risk_counters']['mtef_fallback_images'] == 1


@pytest.mark.parametrize('preview_state', ['none', 'missing', 'invalid'])
def test_missing_or_bad_preview_keeps_unknown_formula_diagnostics_without_double_review(tmp_path, preview_state):
    blob = package('<w:p>' + text('1. 原公式：') + object_xml(preview=None if preview_state == 'none' else 'preview')
                   + text('；此条件必须保留。') + '</w:p>', preview_state=preview_state)
    result = extract(blob, tmp_path)
    diag = result['diagnostics']
    assert result['image_paths'] == []
    assert '$ab$' in result['markdown'] and '此条件必须保留。' in result['markdown']
    assert '待核对' not in result['markdown'] and diag['native_missing_glyphs'] == 1
    assert r'\text{?}' not in result['markdown']
    assert diag['mtef_converted'] == diag['mtef_fallback_images'] == 0
    assert diag['mtef_unavailable'] == diag['review_required'] == 1
    assert diag['images_unavailable'] == (0 if preview_state == 'none' else 1)
    assert any('privateUse:U+EF0A' in warning for warning in diag['warnings'])
    assert scope(blob, result)['status'] == 'ready'
    assert scope(blob, result)['blocks'][0]['has_risk']


def test_fully_supported_formula_does_not_duplicate_the_original_preview(tmp_path):
    blob = package('<w:p>' + text('1. 求') + object_xml() + text('的值。') + '</w:p>', payload=SAFE)
    result = extract(blob, tmp_path)
    assert '$x+1$' in result['markdown']
    assert not result['image_paths'] and not list(tmp_path.glob('*.png'))
    diag = result['diagnostics']
    assert diag['mtef_converted'] == diag['mtef_structural_converted'] == 1
    assert diag['mtef_fallback_images'] == diag['mtef_unavailable'] == diag['review_required'] == 0
    assert not scope(blob, result)['blocks'][0]['has_risk']


@pytest.mark.parametrize('unknown_first', [True, False])
def test_multiple_formula_objects_in_one_paragraph_keep_each_object_and_one_risk(tmp_path, unknown_first):
    extra = {'word/embeddings/safe.bin': SAFE}
    rels = f'<Relationship Id="safe" Type="{R}/oleObject" Target="embeddings/safe.bin"/>'
    objects = [object_xml(), object_xml('safe')]
    if not unknown_first:
        objects.reverse()
    blob = package('<w:p>' + text('1. 甲式：') + objects[0] + text('；乙式：') + objects[1] + text('；求值。') + '</w:p>',
                   extra_members=extra, extra_rels=rels)
    result = extract(blob, tmp_path)
    diag = result['diagnostics']
    assert diag['mtef_converted'] == diag['mtef_fallback_images'] == diag['review_required'] == 1
    assert result['markdown'].count('![](') == 1 and result['markdown'].count('$x+1$') == 1
    assert result['markdown'].index('![](') < result['markdown'].index('$x+1$') if unknown_first else result['markdown'].index('$x+1$') < result['markdown'].index('![](')
    assert scope(blob, result)['blocks'][0]['risk_counters']['review_required'] == 1


def test_repeated_preview_assets_keep_every_formula_reference_and_each_native_risk_origin(tmp_path):
    body = '<w:p>' + text('1. 第一式') + object_xml() + text('条件。') + '</w:p>'
    body += '<w:p>' + text('2. 第二式') + object_xml() + text('条件。') + '</w:p>'
    body += '<w:p>' + text('3. 独立题已知x=1。') + '</w:p>'
    blob = package(body)
    result = extract(blob, tmp_path)
    diag = result['diagnostics']
    assert len(result['image_paths']) == diag['images_extracted'] == 1
    assert result['markdown'].count(result['image_paths'][0]) == 2
    assert diag['mtef_fallback_images'] == diag['review_required'] == 2
    assert len(diag['warnings']) == 1
    proof = scope(blob, result)
    assert proof['status'] == 'ready' and proof['asset_count'] == 1
    assert [row['has_risk'] for row in proof['blocks']] == [True, True, False]
    assert [row['risk_counters'].get('review_required', 0) for row in proof['blocks']] == [1, 1, 0]
    assert ''.join(result['markdown'][slice(*row['range'])] for row in proof['blocks']) == result['markdown']
    changed = deepcopy(diag)
    changed['review_required'] = changed['mtef_fallback_images'] = 0
    assert verify_source_review_evidence(result['markdown'], changed, result['_source_review_evidence'])['status'] == 'uncertain'


def test_unknown_formula_preview_cannot_become_empty_administrative_or_reliable_word_metadata(tmp_path):
    from mathbank.docx_source_assets import verified_empty_asset_urls
    from mathbank.source_metadata import prepare_word_source_metadata
    body = '<w:p>' + text('1. 已知原公式') + object_xml() + text('求值。') + '</w:p>'
    blob = package(body)
    result = extract(blob, tmp_path)
    assert verified_empty_asset_urls(result['markdown'], result['_source_asset_evidence'], result['image_paths']) == set()
    plan = prepare_word_source_metadata(result['markdown'], result['diagnostics'], asset_evidence=result['_source_asset_evidence'])
    assert not plan['eligible']
    assert 'word_extraction_requires_review' in plan['fallback_reasons']
    assert not plan.get('_word_source_certificate')


@pytest.mark.parametrize('unknown_first', [True, False])
def test_multiple_true_ole_records_in_one_carrier_never_certify_only_the_first_formula(tmp_path, unknown_first):
    records = ['<o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/>', '<o:OLEObject ProgID="Equation.DSMT4" r:id="safe"/>']
    if not unknown_first:
        records.reverse()
    carrier = '<w:r><w:object><v:shape><v:imagedata r:id="preview"/></v:shape>' + ''.join(records) + '</w:object></w:r>'
    blob = package('<w:p>' + text('1. 甲乙两式：') + carrier + text('；求值。') + '</w:p>',
        extra_members={'word/embeddings/safe.bin': SAFE},
        extra_rels=f'<Relationship Id="safe" Type="{R}/oleObject" Target="embeddings/safe.bin"/>')
    result = extract(blob, tmp_path)
    diag = result['diagnostics']
    assert diag['mtef_converted'] == 0 and '$x+1$' not in result['markdown']
    assert diag['mtef_fallback_images'] == diag['review_required'] == 1
    assert len(result['image_paths']) == 1
    assert any('多个嵌入记录' in warning for warning in diag['warnings'])
    proof = scope(blob, result)
    assert proof['status'] == 'ready' and proof['blocks'][0]['has_risk']
    from mathbank.source_metadata import prepare_word_source_metadata
    assert not prepare_word_source_metadata(result['markdown'], diag)['eligible']


@pytest.mark.parametrize('repeated_same_reference', [False, True])
def test_ambiguous_carrier_keeps_all_visible_preview_occurrences_in_source_order(tmp_path, repeated_same_reference):
    second = 'preview' if repeated_same_reference else 'second'
    carrier = ('<w:r><w:object><v:shape><v:imagedata r:id="preview"/></v:shape>'
        f'<v:shape><v:imagedata r:id="{second}"/></v:shape>'
        '<o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/>'
        '<o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/></w:object></w:r>')
    members, rels = {}, ''
    if not repeated_same_reference:
        alternate = BytesIO()
        Image.new('RGB', (12, 8), 'blue').save(alternate, format='PNG')
        members['word/media/second.png'] = alternate.getvalue()
        rels = f'<Relationship Id="second" Type="{R}/image" Target="media/second.png"/>'
    blob = package('<w:p>' + text('1. 两式在同一对象：') + carrier + text('；末条件。') + '</w:p>',
                   extra_members=members, extra_rels=rels)
    result = extract(blob, tmp_path)
    references = re.findall(r'!\[\]\(([^)]*)\)', result['markdown'])
    assert len(references) == 2
    assert len(result['image_paths']) == (1 if repeated_same_reference else 2)
    assert references == [result['image_paths'][0]] * 2 if repeated_same_reference else references == result['image_paths']
    assert result['diagnostics']['review_required'] == result['diagnostics']['mtef_fallback_images'] == 1
    assert scope(blob, result)['status'] == 'ready'


@pytest.mark.parametrize('payload,expected_review', [(SAFE, 0), (UNKNOWN_GLYPH, 1)])
def test_alternate_choice_and_fallback_same_ole_is_selected_before_multiple_record_guard(tmp_path, payload, expected_review):
    entry = '<o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/>'
    carrier = ('<w:r><w:object><v:shape><v:imagedata r:id="preview"/></v:shape>'
        '<mc:AlternateContent><mc:Choice Requires="o">' + entry + '</mc:Choice>'
        '<mc:Fallback>' + entry + '</mc:Fallback></mc:AlternateContent></w:object></w:r>')
    blob = package('<w:p>' + text('1. 公式') + carrier + text('；求值。') + '</w:p>', payload=payload)
    result = extract(blob, tmp_path)
    assert result['diagnostics']['review_required'] == expected_review
    assert result['diagnostics']['mtef_converted'] == 1 - expected_review
    assert result['diagnostics']['mtef_fallback_images'] == expected_review
    assert not any('多个嵌入记录' in warning for warning in result['diagnostics']['warnings'])
    assert scope(blob, result)['status'] == 'ready'


def test_multiple_ole_carrier_without_any_preview_stays_unavailable_and_reviewed(tmp_path):
    carrier = ('<w:r><w:object><o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/>'
               '<o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/></w:object></w:r>')
    blob = package('<w:p>' + text('1. 原两式') + carrier + text('求值。') + '</w:p>', preview_state='none')
    result = extract(blob, tmp_path)
    diag = result['diagnostics']
    assert diag['mtef_converted'] == diag['mtef_fallback_images'] == 0
    assert diag['review_required'] == diag['mtef_unavailable'] == 1
    assert result['markdown'] == '1. 原两式求值。'
    assert diag['native_missing_glyphs'] == 1
    assert scope(blob, result)['status'] == 'ready' and scope(blob, result)['blocks'][0]['has_risk']
