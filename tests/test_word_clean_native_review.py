"""Clean display must preserve privately proven native extraction risk."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import struct
from types import SimpleNamespace
import zipfile

from PIL import Image, ImageDraw
import pytest

from mathbank.docx_helper import extract_docx_markdown
from mathbank.docx_source_scopes import verify_source_review_evidence
from mathbank.content_locks import _source_parts
from mathbank.import_review import prepare_word_extraction_reviews, finalize_word_extraction_reviews
from mathbank import docx_source_verify as verifier
from mathbank.ai_providers import resolve_ocr_provider

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


@pytest.fixture(autouse=True)
def no_requests(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *_a, **_k: pytest.fail('No live model requests'))


def package(body, *, members=None, rels=''):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"><w:body>{body}</w:body></w:document>')
        archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + '</Relationships>')
        for name, data in (members or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def text(value):
    return '<w:r><w:t>' + value + '</w:t></w:r>'


def missing_package():
    return package('<w:p>' + text('1. 已知') + '<m:oMath><m:r><m:t>x\uef0ay</m:t></m:r></m:oMath>' + text('，求值。') + '</w:p>'
                   + '<w:p>' + text('2. 已知x=1，求x+1。') + '</w:p>')


def native_state(tmp_path, *, blob=None, seed_false_flag=False):
    blob = blob or missing_package()
    result = extract_docx_markdown(blob, output_dir=tmp_path / 'uploads' / 'tmp', url_prefix='/static/uploads/tmp', include_source_review_evidence=True)
    assert result['success']
    native = deepcopy(result['diagnostics'])
    parts = [part for part in _source_parts(result['markdown'], []) if part.field == 'content']
    questions = [{'content': part.text, 'answer_markdown': ''} for part in parts]
    if seed_false_flag:
        for q in questions:
            q['source_review'] = {'native_missing_glyphs': False}
    diagnostics = deepcopy(native)
    diagnostics['word_extraction_warnings'] = deepcopy(native['warnings'])
    diagnostics['source_matches'] = [{'question_index': index, 'field': 'content', 'source_start': part.source_start,
        'source_end': part.source_end, 'source_excerpt': part.text, 'source_number': part.number} for index, part in enumerate(parts)]
    kwargs = {'source_review_evidence': result['_source_review_evidence'], 'extraction_diagnostics': native,
              'source_document_sha256': hashlib.sha256(blob).hexdigest()}
    proof = verify_source_review_evidence(result['markdown'], native, kwargs['source_review_evidence'], source_document_sha256=kwargs['source_document_sha256'])
    assert proof['status'] == 'ready'
    return SimpleNamespace(source=result['markdown'], result=result, native=native, questions=questions,
        diagnostics=diagnostics, kwargs=kwargs, blob=blob, proof=proof)


def test_signed_native_missing_glyph_maps_to_its_question_without_display_markers(tmp_path):
    state = native_state(tmp_path, seed_false_flag=True)
    assert '?' not in state.source and '待核对' not in state.source
    assert state.native['native_missing_glyphs'] == 1
    prepare_word_extraction_reviews(state.questions, state.diagnostics, state.source, **state.kwargs)
    assert state.questions[0]['source_review']['required']
    assert state.questions[0]['source_review']['native_missing_glyphs'] is True
    assert state.questions[0]['source_review']['native_extraction_risks'][0]['risk_counters']['native_missing_glyphs'] == 1
    assert not state.questions[1].get('source_review', {}).get('required')
    assert state.diagnostics['source_review_count'] == 1


@pytest.mark.parametrize('change', ['source', 'diagnostics', 'document', 'public_copy'])
def test_unverified_public_native_risk_cannot_be_used_as_a_source_certificate(tmp_path, change):
    state = native_state(tmp_path)
    if change == 'source': state.source += '另有正文'
    if change == 'diagnostics': state.kwargs['extraction_diagnostics']['omml_converted'] += 1
    if change == 'document': state.kwargs['source_document_sha256'] = '0' * 64
    if change == 'public_copy': state.kwargs['source_review_evidence'] = state.proof
    prepare_word_extraction_reviews(state.questions, state.diagnostics, state.source, **state.kwargs)
    assert not any(q.get('source_review', {}).get('native_extraction_risks') for q in state.questions)
    assert state.diagnostics['word_native_missing_glyphs_unlocated'] == 1


def attach_fake_vision(state, tmp_path, monkeypatch, *, decisions='equivalent'):
    root = tmp_path / 'uploads'
    root.mkdir(exist_ok=True)
    page = root / 'tmp' / 'docx_page_native.png'
    page.parent.mkdir(exist_ok=True)
    Image.new('RGB', (80, 80), 'white').save(page)
    monkeypatch.setattr(verifier, 'UPLOADS_DIR', root)
    provider = resolve_ocr_provider('siliconflow', {'SILICONFLOW_API_KEY': 'test-only'})
    monkeypatch.setattr(verifier, 'resolve_ocr_provider', lambda _: provider)
    state.evidence = {'status': 'ready', 'evidence_kind': 'original_docx_render', 'evidence_version': 2,
        'source_sha256': hashlib.sha256(state.blob).hexdigest(), 'pages': [{'page_number': 1,
        'image_path': '/static/uploads/tmp/docx_page_native.png', 'image_sha256': hashlib.sha256(page.read_bytes()).hexdigest(),
        'text': state.source}]}
    calls = []
    def post(_provider, payload, **_kwargs):
        calls.append(payload)
        verdicts = [{'id': f'word_{index + 1:03d}', 'source_number': index + 1,
            'decision': decisions, 'evidence': '本地桩：图文相同声明，不能证明未保留的未知字形已恢复。',
            'checks': {key: True for key in verifier._CHECKS}}
            for index, q in enumerate(state.questions) if q.get('source_review', {}).get('required')]
        body = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': verdicts}, ensure_ascii=False)}}]}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(verifier, 'post_chat_completion', post)
    return calls


def test_clean_partial_formula_cannot_be_confirmed_by_model_equivalent_or_image_cleanup(tmp_path, monkeypatch):
    state = native_state(tmp_path)
    prepare_word_extraction_reviews(state.questions, state.diagnostics, state.source, **state.kwargs)
    original = deepcopy(state.questions[0])
    calls = attach_fake_vision(state, tmp_path, monkeypatch)
    report = verifier.verify_docx_source_suspicions(state.questions, state.diagnostics, state.source, state.evidence)
    assert len(calls) == 1 and report['confirmed'] == 0 and report['pending'] == 1
    assert report['items'][0]['decision'] == 'uncertain'
    assert report['items'][0]['checks']['complete_content'] is False
    assert state.questions[0]['content'] == original['content']
    assert state.questions[0]['source_review']['native_missing_glyphs']
    assert state.questions[0]['source_review']['verification_attempt']['model_decision'] == 'equivalent'
    finalize_word_extraction_reviews(state.questions, state.diagnostics, state.source, **state.kwargs)
    assert state.diagnostics['word_extraction_review_count'] >= 1
    assert state.diagnostics['word_formula_extraction_items'][0]['verified'] is False


def preview_package():
    def char(code, style=3): return bytes((2, 0, style + 128)) + struct.pack('<H', code)
    binary = b'\x05\x01\x00\x07\x00DSMT7\x00\x00\x01\x00' + char(ord('x')) + char(0xEF0A, 11) + char(ord('y')) + b'\x00\x00'
    image = Image.new('RGB', (90, 70), 'white')
    ImageDraw.Draw(image).line((5, 5, 85, 65), fill='black', width=4)
    buffer = BytesIO(); image.save(buffer, format='PNG')
    obj = '<w:r><w:object><v:shape><v:imagedata r:id="preview"/></v:shape><o:OLEObject ProgID="Equation.DSMT4" r:id="ole"/></w:object></w:r>'
    return package('<w:p>' + text('1. 已知原公式') + obj + text('，求值。') + '</w:p>',
        members={'word/embeddings/equation.bin': binary, 'word/media/preview.png': buffer.getvalue()},
        rels=f'<Relationship Id="ole" Type="{R}/oleObject" Target="embeddings/equation.bin"/><Relationship Id="preview" Type="{R}/image" Target="media/preview.png"/>')


def test_full_native_formula_preview_is_risk_but_not_omitted_glyphs(tmp_path, monkeypatch):
    state = native_state(tmp_path, blob=preview_package())
    assert state.native['native_missing_glyphs'] == 0
    assert state.native['mtef_fallback_images'] == 1 and state.result['image_paths']
    prepare_word_extraction_reviews(state.questions, state.diagnostics, state.source, **state.kwargs)
    review = state.questions[0]['source_review']
    assert review['required'] and not review.get('native_missing_glyphs')
    calls = attach_fake_vision(state, tmp_path, monkeypatch)
    report = verifier.verify_docx_source_suspicions(state.questions, state.diagnostics, state.source, state.evidence,
        candidate_image_paths=state.result['image_paths'])
    assert report['confirmed'] == 1 and report['pending'] == 0 and len(calls) == 1
    finalize_word_extraction_reviews(state.questions, state.diagnostics, state.source, **state.kwargs)
    assert state.diagnostics['word_formula_extraction_items'][0]['verified'] is True
