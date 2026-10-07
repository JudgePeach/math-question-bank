"""Independent mixed-Word contract checks: real native evidence, no paid HTTP."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from xml.sax.saxutils import escape
import zipfile

from PIL import Image

import pytest

from mathbank.ai_providers import resolve_text_provider
from mathbank.content_locks import _formulas
from mathbank.docx_helper import extract_docx_markdown
from mathbank.source_metadata import SourceMetadataContractError
from mathbank.question_assets import embedded_question_assets
from mathbank.task_manager import TaskCancelled
from mathbank.word_hybrid_plan import WordHybridPlanError, build_word_hybrid_plan
from mathbank.word_hybrid_request import request_word_hybrid

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
CURRICULUM = {"必修一": {"1. 集合与常用逻辑用语": [], "3. 函数的概念与性质": []}}
FIELDS = {"question_type": "detailed_answer", "category_compulsory": "必修一",
          "category_chapter": "3. 函数的概念与性质", "difficulty": "normal"}
PROVIDER = resolve_text_provider("SILICONFLOW/Qwen/test-model", {"SILICONFLOW_API_KEY": "isolated-fake-key"})


def paragraph(value):
    return '<w:p><w:r><w:t>' + escape(value) + '</w:t></w:r></w:p>'


def bad_question(number):
    return (f'<w:p><w:r><w:t>{number}. 比较未知式并求函数值：</w:t></w:r>'
            '<m:oMath><m:unknownSafetyFixture><m:r><m:t>x+1</m:t></m:r>'
            '</m:unknownSafetyFixture></m:oMath></w:p>')


def native_plan(tmp_path, *, two_bad=False, shared=False, with_image=False):
    body = paragraph('一、解答题')
    body += paragraph(r'1. 设$f(x)=x^2$，求$f(3)$。阅读代码`x___`和$\underline{AB}$，两个公式不能省略。')
    if with_image:
        body += '<w:p><w:r><w:pict><v:shape><v:imagedata r:id="img"/></v:shape></w:pict></w:r></w:p>'
    body += paragraph('【解析】$f(3)=9$。原始代码`[EXTRACTED_ORIGINAL]`必须保留。')
    body += bad_question(2)
    body += paragraph('【解析】这是原题二的独立解法；应保留原始未知式，不能生成新解法。')
    body += paragraph(r'3. 设集合$A=\{1,2,3\}$，求其元素个数。')
    body += paragraph('【解析】$3$，这是原题三的原答案。')
    if two_bad:
        body += bad_question(4) + paragraph('【解析】这是原题四的原答案。')
        body += paragraph('5. 已知$x=5$，求$x+x$。') + paragraph('【解析】$10$。')
    if shared:
        body = body.replace('3. 设集合', '3. 根据第2题中的函数，设集合')
    data = BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>{body}</w:body></w:document>')
        if with_image:
            pixels = BytesIO(); Image.new('RGB', (8,8), 'red').save(pixels, format='PNG')
            archive.writestr('word/media/figure.png', pixels.getvalue())
            archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="img" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/figure.png"/></Relationships>')
    blob = data.getvalue()
    native = extract_docx_markdown(blob, output_dir=tmp_path, include_source_asset_evidence=True,
                                   include_source_review_evidence=True)
    assert native['success']
    plan = build_word_hybrid_plan(native['markdown'], native['diagnostics'], task_id='independent-hybrid',
        generation=3, source_document_sha256=hashlib.sha256(blob).hexdigest(),
        source_review_evidence=native['_source_review_evidence'], asset_evidence=native['_source_asset_evidence'],
        min_metadata_source_characters=0)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    return plan


def reply_for(plan, payload):
    """A fixture response includes original bodies only for requested risky IDs."""
    envelope = json.loads(payload['messages'][1]['content'])
    by_id = {q['id']: q for q in plan['_inspection']['questions']}
    meta = [{'id': item['id'], **FIELDS} for item in envelope['metadata_items']]
    splits = []
    for group in envelope['split_groups']:
        rows = []
        for qid in group['source_ids']:
            source = by_id[qid]
            rows.append({'source_id': qid, **FIELDS, 'content': source['content'],
                'answer_markdown': '[EXTRACTED_ORIGINAL]' + source['answer_markdown'] if source['answer_markdown'] else '',
                'source': '', 'referenced_images': embedded_question_assets(source['content'], source['answer_markdown'])})
        splits.append({'group_id': group['group_id'], 'questions': rows})
    return {'metadata': {'items': meta}, 'splits': splits}


class Response:
    status_code = 200

    def __init__(self, parsed, *, status_code=200):
        self.parsed = parsed
        self.status_code = status_code

    def json(self):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.parsed, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}


def call(plan, post, *, fallback=lambda: pytest.fail('Unexpected whole-source fallback'), check_cancelled=lambda: None):
    diagnostics = {}
    result = request_word_hybrid(plan, CURRICULUM, provider=PROVIDER, post=post, diagnostics=diagnostics,
        normalize_fillin=lambda value: value, full_source_fallback=fallback,
        task_id='independent-hybrid', generation=3, check_cancelled=check_cancelled)
    return result, diagnostics


def test_one_post_reorders_all_groups_and_preserves_safe_source_original_answer_and_literal_bytes(tmp_path):
    plan = native_plan(tmp_path, two_bad=True)
    calls = []
    before = deepcopy(plan['_inspection']['questions'])
    def post(provider, payload, **options):
        assert provider is PROVIDER
        assert options['retry_connection'] is False and options['allow_redirects'] is False
        calls.append(deepcopy(payload))
        response = reply_for(plan, payload)
        response['metadata']['items'].reverse()
        response['splits'].reverse()
        for group in response['splits']:
            group['questions'].reverse()
        return Response(response)
    result, diagnostics = call(plan, post)
    assert len(calls) == 1 and diagnostics['word_hybrid']['calls'] == 1
    assert not result.partial and result.preserved_indices == frozenset({0, 2, 4})
    assert [q['content'] for q in result.questions] == [q['content'] for q in before]
    assert '`x___`' in result.questions[0]['content'] and r'$\underline{AB}$' in result.questions[0]['content']
    assert '`[EXTRACTED_ORIGINAL]`' in result.questions[0]['answer_markdown']
    assert plan['_inspection']['questions'] == before
    assert all('source_id' not in q for q in result.questions)


@pytest.mark.parametrize('mutation', ['unknown_metadata', 'duplicate_metadata', 'unknown_group', 'duplicate_group', 'outer_field'])
def test_global_contract_failure_has_one_whole_fallback_and_no_scoped_repeats(tmp_path, mutation):
    plan = native_plan(tmp_path)
    calls, fallbacks = [], []
    original = [{'content': 'Old-path whole result', **FIELDS, 'answer_markdown': ''}]
    def post(_provider, payload, **options):
        calls.append(deepcopy(payload)); parsed = reply_for(plan, payload)
        if mutation == 'unknown_metadata': parsed['metadata']['items'][0]['id'] = 'unknown'
        elif mutation == 'duplicate_metadata': parsed['metadata']['items'].append(deepcopy(parsed['metadata']['items'][0]))
        elif mutation == 'unknown_group': parsed['splits'][0]['group_id'] = 'unknown'
        elif mutation == 'duplicate_group': parsed['splits'].append(deepcopy(parsed['splits'][0]))
        else: parsed['page_complete'] = True
        return Response(parsed)
    def fallback(): fallbacks.append(True); return original
    result, diagnostics = call(plan, post, fallback=fallback)
    assert len(calls) == 1 and len(fallbacks) == 1
    assert diagnostics['word_hybrid']['calls'] == 2
    assert result.questions == original and not result.preserved_indices and not result.partial


@pytest.mark.parametrize('mutation', ['missing_metadata', 'changed_content', 'bad_category', 'wrong_type', 'missing_split'])
def test_only_failed_group_is_sent_once_more_without_repeating_accepted_ids(tmp_path, mutation):
    plan = native_plan(tmp_path, two_bad=True)
    calls = []
    def post(_provider, payload, **options):
        envelope = json.loads(payload['messages'][1]['content']); calls.append(envelope)
        parsed = reply_for(plan, payload)
        if len(calls) == 1:
            if mutation == 'missing_metadata': parsed['metadata']['items'].pop(0)
            elif mutation == 'changed_content': parsed['metadata']['items'][0]['content'] = 'Rewrite forbidden'
            elif mutation == 'bad_category': parsed['metadata']['items'][0]['category_chapter'] = 'Unknown chapter'
            elif mutation == 'wrong_type': parsed['metadata']['items'][0]['question_type'] = 'single_choice'
            else: parsed['splits'].pop(0)
        return Response(parsed)
    result, diagnostics = call(plan, post)
    assert len(calls) == 2 and diagnostics['word_hybrid']['calls'] == 2 and not result.partial
    first_meta = {q['id'] for q in calls[0]['metadata_items']}
    second_meta = {q['id'] for q in calls[1]['metadata_items']}
    first_split = {g['group_id'] for g in calls[0]['split_groups']}
    second_split = {g['group_id'] for g in calls[1]['split_groups']}
    assert len(second_meta) + len(second_split) == 1
    assert second_meta <= first_meta and second_split <= first_split
    assert len(result.questions) == 5


def test_second_failure_retains_successful_groups_and_exposes_partial_without_third_post(tmp_path):
    plan = native_plan(tmp_path, two_bad=True)
    calls = []
    failed_group = next(g['id'] for g in plan['groups'] if g['source_numbers'] == [2])
    def post(_provider, payload, **options):
        calls.append(deepcopy(payload)); parsed = reply_for(plan, payload)
        parsed['splits'] = [g for g in parsed['splits'] if g['group_id'] != failed_group]
        return Response(parsed)
    result, diagnostics = call(plan, post)
    assert len(calls) == 2 and result.partial
    assert [q['content'] for q in result.questions] == [q['content'] for q in plan['_inspection']['questions'] if q['source_number'] != 2]
    assert result.preserved_indices == frozenset({0, 1, 3})
    assert diagnostics['word_hybrid']['failed_group_ids'] == [failed_group]
    assert diagnostics['word_hybrid']['status'] == 'partial'


@pytest.mark.parametrize('stage', ['before', 'during', 'connection_error'])
def test_cancellation_never_pays_for_fallback(tmp_path, stage):
    plan = native_plan(tmp_path)
    cancelled = stage == 'before'; posts = []
    def check():
        if cancelled: raise TaskCancelled('fixture cancellation')
    def post(_provider, payload, **options):
        nonlocal cancelled
        posts.append(True); cancelled = True
        if stage == 'connection_error': raise ConnectionError('cancel wins')
        return Response(reply_for(plan, payload))
    with pytest.raises(TaskCancelled): call(plan, post, check_cancelled=check)
    assert len(posts) == (0 if stage == 'before' else 1)


@pytest.mark.parametrize('change', ['source', 'identity', 'private_body'])
def test_changed_source_or_identity_after_post_is_a_hard_stop_without_any_fallback(tmp_path, change):
    plan = native_plan(tmp_path)
    posts, fallbacks = [], []
    def post(_provider, payload, **options):
        posts.append(True); response = Response(reply_for(plan, payload))
        if change == 'source': plan['source'] += '\nChanged original condition.'
        elif change == 'identity': plan['generation'] += 1
        else: plan['groups'][0]['_source_metadata_plan']['questions'][0]['content'] += 'altered'
        return response
    def fallback(): fallbacks.append(True); return []
    with pytest.raises((WordHybridPlanError, SourceMetadataContractError)):
        call(plan, post, fallback=fallback)
    assert len(posts) == 1 and fallbacks == []


@pytest.mark.parametrize('mutation', ['unknown', 'duplicate', 'swapped_content', 'swapped_answer'])
def test_risky_source_identity_and_answer_binding_cannot_be_certified_by_self_report(tmp_path, mutation):
    plan = native_plan(tmp_path, shared=True)
    calls, fallbacks = [], []
    def post(_provider, payload, **options):
        calls.append(True); parsed = reply_for(plan, payload)
        group = parsed['splits'][0]; rows = group['questions']; assert len(rows) == 2
        if mutation == 'unknown': rows[0]['source_id'] = 'unknown'
        elif mutation == 'duplicate': rows[1]['source_id'] = rows[0]['source_id']
        elif mutation == 'swapped_content': rows[0]['content'], rows[1]['content'] = rows[1]['content'], rows[0]['content']
        else: rows[0]['answer_markdown'], rows[1]['answer_markdown'] = rows[1]['answer_markdown'], rows[0]['answer_markdown']
        return Response(parsed)
    result, diagnostics = call(plan, post, fallback=lambda: fallbacks.append(True) or [{'content': 'Old fallback', **FIELDS}])
    if mutation in {'unknown', 'duplicate'}:
        assert len(calls) == 1 and len(fallbacks) == 1
        assert diagnostics['word_hybrid']['status'] == 'full_source_fallback'
    else:
        assert len(calls) == 2 and result.partial and not fallbacks
        assert len(result.questions) == 1
        assert result.preserved_indices == frozenset({0})


def test_shared_risky_question_replies_can_reorder_only_with_matching_original_ids(tmp_path):
    plan = native_plan(tmp_path, shared=True)
    calls = []
    def post(_provider, payload, **options):
        calls.append(True); parsed = reply_for(plan, payload)
        parsed['splits'][0]['questions'].reverse()
        return Response(parsed)
    result, diagnostics = call(plan, post)
    assert len(calls) == 1 and not result.partial
    assert [q['content'] for q in result.questions] == [q['content'] for q in plan['_inspection']['questions']]
    assert '原题二的独立解法' in result.questions[1]['answer_markdown']
    assert '原题三的原答案' in result.questions[2]['answer_markdown']
    assert result.preserved_indices == frozenset({0})


@pytest.mark.parametrize('stage', ['before', 'during'])
def test_changed_original_raster_bytes_revoke_native_evidence_without_paid_fallback(tmp_path, stage):
    plan = native_plan(tmp_path, with_image=True)
    asset = next(tmp_path.glob('*.png'))
    posts, fallbacks = [], []
    if stage == 'before': asset.write_bytes(b'changed source image bytes')
    def post(_provider, payload, **options):
        posts.append(True); response = Response(reply_for(plan, payload))
        asset.write_bytes(b'changed source image bytes'); return response
    with pytest.raises((WordHybridPlanError, SourceMetadataContractError)):
        call(plan, post, fallback=lambda: fallbacks.append(True) or [])
    assert len(posts) == (0 if stage == 'before' else 1) and fallbacks == []


def test_source_change_during_scoped_retry_cannot_return_stale_accepted_groups(tmp_path):
    plan = native_plan(tmp_path, two_bad=True)
    calls, fallbacks = [], []
    def post(_provider, payload, **options):
        calls.append(True); parsed = reply_for(plan, payload)
        if len(calls) == 1: parsed['splits'].pop(0)
        else: plan['source'] += '\nChanged shared original source.'
        return Response(parsed)
    with pytest.raises((WordHybridPlanError, SourceMetadataContractError)):
        call(plan, post, fallback=lambda: fallbacks.append(True) or [])
    assert len(calls) == 2 and fallbacks == []


def test_provider_error_uses_one_whole_fallback_without_client_retry_or_third_post(tmp_path):
    plan = native_plan(tmp_path); posts, fallbacks = [], []
    def post(_provider, payload, **options):
        posts.append(options); return Response({}, status_code=500)
    original = [{'content': 'Old original source result', **FIELDS}]
    result, diagnostics = call(plan, post, fallback=lambda: fallbacks.append(True) or original)
    assert len(posts) == 1 and len(fallbacks) == 1 and diagnostics['word_hybrid']['calls'] == 2
    assert posts[0]['retry_connection'] is False and result.questions == original


def test_second_transport_error_keeps_valid_first_groups_without_whole_source_repetition(tmp_path):
    plan = native_plan(tmp_path, two_bad=True); calls = []
    def post(_provider, payload, **options):
        calls.append(True)
        if len(calls) == 2: raise ConnectionError('bounded scoped failure')
        parsed = reply_for(plan, payload); parsed['splits'].pop(0); return Response(parsed)
    result, diagnostics = call(plan, post)
    assert len(calls) == 2 and result.partial and len(result.questions) == 4
    assert diagnostics['word_hybrid']['status'] == 'partial'


def test_safe_complete_body_original_answer_and_formula_occurrences_are_all_in_metadata_context(tmp_path):
    plan = native_plan(tmp_path, with_image=True); calls = []
    def post(_provider, payload, **options):
        envelope = json.loads(payload['messages'][1]['content']); calls.append(envelope)
        first = next(item for item in envelope['metadata_items'] if item['id'] == plan['_inspection']['questions'][0]['id'])
        original = plan['_inspection']['questions'][0]
        assert first['content'] == original['content']
        assert first['original_answer_context'] == original['answer_markdown']
        assert [f.formula for f in _formulas(first['content'])] == [f.formula for f in _formulas(original['content'])]
        assert '`x___`' in first['content'] and '`[EXTRACTED_ORIGINAL]`' in first['original_answer_context']
        for group in envelope['split_groups']:
            assert '[公式结构待核对]' not in group['source_markdown']
            assert '比较未知式并求函数值' in group['source_markdown']
            assert '$x+1$' in group['source_markdown']
        return Response(reply_for(plan, payload))
    result, diagnostics = call(plan, post)
    assert len(calls) == 1 and not result.partial
    assert result.questions[0]['referenced_images']


def test_no_adoptable_groups_uses_one_original_fallback_instead_of_repeating_full_mixed_payload(tmp_path):
    plan = native_plan(tmp_path)
    posts, fallbacks = [], []
    def post(_provider, payload, **options):
        posts.append(deepcopy(payload)); return Response({'metadata': {'items': []}, 'splits': []})
    original = [{'content': 'Original fallback result', **FIELDS}]
    result, diagnostics = call(plan, post, fallback=lambda: fallbacks.append(True) or original)
    assert len(posts) == 1 and len(fallbacks) == 1
    assert diagnostics['word_hybrid']['calls'] == 2
    assert result.questions == original and not result.preserved_indices and not result.partial
