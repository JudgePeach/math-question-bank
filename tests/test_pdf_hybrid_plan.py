"""PDF planner boundaries, with local verifier/issuer doubles and no HTTP.

The native scope module separately tests physical PDF evidence. These tests use
the real generic source inspector and exercise grouping, ranges and plan HMAC.
"""

from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from mathbank.source_metadata import inspect_source_structure
from mathbank.pdf_hybrid_plan import (
    MAX_GROUPS, PdfHybridPlanError, build_pdf_hybrid_plan,
    build_pdf_snapshot_hybrid_plan, pdf_hybrid_plan_diagnostics, require_pdf_hybrid_plan,
    require_pdf_snapshot_hybrid_plan, pdf_snapshot_hybrid_plan_diagnostics,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class NativeEvidence:
    snapshot: str
    pages: tuple
    global_reasons: tuple = ()


@dataclass(frozen=True)
class GroupCertificate:
    original_source_sha256: str
    proof_sha256: str
    owned_ranges: tuple
    context_ranges: tuple
    original_question_ids: tuple
    task_id: str
    generation: int
    group_id: str
    question_snapshot: str
    source_basis: str = 'native_pdf'
    native_reliable: bool = True


@pytest.fixture
def authorities(monkeypatch):
    def verify(source, diagnostics, evidence, *, source_document_sha256, source_pages, layout_result):
        if type(evidence) is not NativeEvidence or evidence.snapshot != digest(
                [source, diagnostics, source_document_sha256, source_pages, layout_result]):
            return {'status': 'uncertain'}
        return {'status': 'ready', 'proof_sha256': digest(asdict(evidence)),
                'pages': deepcopy(list(evidence.pages)), 'global_reasons': list(evidence.global_reasons)}

    def prepare(source, diagnostics, *, owned_ranges, context_ranges, group_id, task_id, generation,
                source_document_sha256, source_review_evidence, source_pages, layout_result):
        proof = verify(source, diagnostics, source_review_evidence,
            source_document_sha256=source_document_sha256, source_pages=source_pages, layout_result=layout_result)
        assert proof['status'] == 'ready'
        questions = [q for q in inspect_source_structure(source)['questions']
            if q['source_range'] in owned_ranges]
        certificate = GroupCertificate(hashlib.sha256(source.encode()).hexdigest(), proof['proof_sha256'],
            tuple(map(tuple, owned_ranges)), tuple(map(tuple, context_ranges)), tuple(q['id'] for q in questions),
            task_id, generation, group_id, digest(questions))
        return {'questions': deepcopy(questions), '_pdf_source_certificate': certificate,
                'original_source_sha256': certificate.original_source_sha256}

    def require(local, *, group_id, task_id, generation):
        cert = local.get('_pdf_source_certificate') if isinstance(local, dict) else None
        if (type(cert) is not GroupCertificate or cert.group_id != group_id or cert.task_id != task_id
                or cert.generation != generation or cert.question_snapshot != digest(local['questions'])):
            raise ValueError('Unowned or modified native PDF group')

    monkeypatch.setitem(sys.modules, 'mathbank.pdf_source_scopes',
        SimpleNamespace(verify_pdf_source_review_evidence=verify))
    monkeypatch.setitem(sys.modules, 'mathbank.pdf_source_metadata',
        SimpleNamespace(prepare_pdf_source_group=prepare, require_pdf_source_group_certificate=require))


def source_fixture(parts=None, *, origins=None, reliable=None, globals=(), figures=None, layout=True, page_numbers=None):
    parts = parts or ['1. 独立甲题，请完成计算。', '2. 独立乙题，请写出结论。', '3. 独立丙题，请说明理由。']
    source = '\n\n'.join(parts)
    origins = origins or ['native'] * len(parts)
    reliable = reliable or [True] * len(parts)
    page_numbers = page_numbers or list(range(1, len(parts) + 1))
    scopes, public, pages = [], [], []
    start = 0
    for i, text in enumerate(parts):
        number = page_numbers[i]
        end = start + len(text) + (2 if i + 1 < len(parts) else 0)
        scopes.append({'page_index': number - 1, 'page_number': number, 'range': [start, end],
                       'origin': origins[i], 'reliable': reliable[i], 'reasons': []})
        public.append({'page_number': number, 'origin': origins[i], 'markdown': text})
        pages.append({'page_index': number - 1, 'page_number': number, 'status': 'checked',
                      'rotation': 0, 'figures': deepcopy((figures or {}).get(i, [])), 'warnings': []})
        start = end
    diag = {'pdf_native_quality': []}
    result = {'pages': pages} if layout else None
    document_sha = '1' * 64
    evidence = NativeEvidence(digest([source, diag, document_sha, public, result]), tuple(scopes), tuple(globals))
    return {'source': source, 'diagnostics': diag, 'source_pages': public, 'layout_result': result,
            'source_document_sha256': document_sha, 'source_review_evidence': evidence}


def planned(fixture, **kwargs):
    return build_pdf_hybrid_plan(**fixture, task_id='pdf-plan-test', generation=4,
                                 min_metadata_source_characters=0, **kwargs)

def test_low_reuse_mixed_document_stays_original_before_request(authorities):
    fixture = source_fixture(origins=['joint_vision', 'native', 'joint_vision'])
    plan = planned(fixture, min_metadata_source_fraction=0.5)
    assert plan['mode'] == 'original'
    assert plan['request_plan'] == []
    assert 'metadata_reuse_below_mixed_character_fraction_policy' in plan['global_reasons']
    assert 0 < plan['reuse_policy']['metadata_source_fraction'] < 0.5
    assert plan['reuse_policy']['is_token_measurement'] is False

def test_whole_preserved_source_is_not_disabled_by_mixed_fraction_policy(authorities):
    plan = planned(source_fixture(), min_metadata_source_fraction=1.0)
    assert plan['mode'] == 'whole_metadata'
    assert plan['reuse_policy']['metadata_source_fraction'] == 1.0

@pytest.mark.parametrize('value', [True, -0.1, 1.1, float('nan'), float('inf')])
def test_reuse_fraction_policy_rejects_invalid_values(authorities, value):
    with pytest.raises(PdfHybridPlanError):
        planned(source_fixture(), min_metadata_source_fraction=value)


def test_native_vision_native_forms_one_mixed_request_with_exact_owned_ranges(authorities):
    fixture = source_fixture(origins=['native', 'joint_vision', 'native_repaired'])
    old = deepcopy(fixture)
    plan = planned(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert [(g['source_numbers'], g['route']) for g in plan['groups']] == [([1], 'metadata'), ([2], 'split'), ([3], 'metadata')]
    assert fixture == old
    assert len(plan['request_plan']) == 1 and plan['request_plan'][0]['max_posts'] == 1
    assert plan['fallback_policy']['max_additional_posts'] == 1
    assert plan['fallback_policy']['otherwise_failed_groups_only']
    assert not plan['fallback_policy']['repeat_completed_groups']
    require_pdf_hybrid_plan(plan, task_id='pdf-plan-test', generation=4)
    assert '_source_metadata_plan' not in plan['groups'][1]


def test_all_native_groups_aggregate_without_word_certificate_or_diagnostics(authorities):
    plan = planned(source_fixture(layout=False))
    assert plan['mode'] == 'whole_metadata', plan['global_reasons']
    assert len(plan['groups']) == 1 and plan['groups'][0]['source_numbers'] == [1, 2, 3]
    assert '_word_source_certificate' not in plan['groups'][0]['_source_metadata_plan']
    assert 'review_required' not in plan['_source_diagnostics']
    require_pdf_hybrid_plan(plan, task_id='pdf-plan-test', generation=4)


def test_one_complete_question_crosses_safe_and_vision_pages_as_one_risky_question(authorities):
    fixture = source_fixture(['1. 比较数量，第一段说明。', '第二段仍属于第一题。', '2. 独立乙题，请说明理由。'],
        origins=['native', 'ocr', 'native'])
    plan = planned(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    first = plan['groups'][0]
    assert first['source_numbers'] == [1] and first['page_numbers'] == [1, 2] and first['route'] == 'split'
    assert first['owned_ranges'] == [plan['_inspection']['questions'][0]['source_range']]


def test_complete_cross_page_native_question_is_not_artificially_split(authorities):
    fixture = source_fixture(['1. 甲题的前半段说明。', '这里仍是甲题的后半段。', '2. 乙题独立说明。'])
    plan = planned(fixture)
    assert plan['mode'] == 'whole_metadata', plan['global_reasons']
    assert len(plan['_inspection']['questions']) == 2
    assert plan['groups'][0]['page_numbers'] == [1, 2, 3]


def test_native_complete_selected_source_cannot_certify_question_across_unselected_page(authorities):
    fixture = source_fixture(['1. 甲题的前半段。', '这是甲题后半段。', '2. 独立乙题。'], page_numbers=[1, 3, 4])
    plan = planned(fixture)
    assert plan['mode'] == 'original' and 'pdf_question_crosses_unselected_page_gap' in plan['global_reasons']


def test_original_answer_on_vision_page_forces_entire_native_stem_to_split(authorities):
    fixture = source_fixture(['1. 独立甲题，请求出结论。', '【解析】这是第一题完整原解法，必须保留。', '2. 独立乙题，请说明理由。'],
        origins=['native', 'regional_vision', 'native'])
    plan = planned(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    group = plan['groups'][0]
    q = plan['_inspection']['questions'][0]
    assert group['route'] == 'split' and group['source_numbers'] == [1]
    assert q['source_range'] in group['owned_ranges'] and q['answer_source_range'] in group['owned_ranges']
    assert '完整原解法' in fixture['source'][slice(*q['answer_source_range'])]


def test_explicit_cross_question_dependency_closes_noncontiguous_questions(authorities):
    fixture = source_fixture(['1. 独立甲题，请完成计算。', '2. 独立乙题，请说明理由。',
                              '3. 根据第1题中的函数，写出结论。'])
    plan = planned(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert [(g['source_numbers'], g['route']) for g in plan['groups']] == [([1, 3], 'split'), ([2], 'metadata')]


def figure(identifier='p1-f1', url='/static/uploads/tmp/one.png', slot='p1-s1'):
    return {'id': identifier, 'image_path': url, 'slot_id': slot, 'attached': True,
            'page_index': 0, 'bbox': [100, 200, 400, 500], 'review_reasons': []}


def test_shared_native_figure_closes_all_actual_owners_into_one_risk_group(authorities):
    image = '![](/static/uploads/tmp/one.png)'
    fixture = source_fixture(['1. 甲题请看配图。' + image, '2. 乙题请看配图。' + image, '3. 独立丙题。'],
        figures={0: [figure()]})
    plan = planned(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert [(g['source_numbers'], g['route']) for g in plan['groups']] == [([1, 2], 'split'), ([3], 'metadata')]
    assert plan['groups'][0]['figure_ids'] == ['p1-f1']


@pytest.mark.parametrize('fault', ['unattached', 'unknown_slot', 'duplicate_slot', 'duplicate_id',
                                 'duplicate_path', 'unowned', 'missing_layout', 'bad_geometry'])
def test_unknown_or_duplicate_figure_ownership_keeps_whole_original(authorities, fault):
    image = '![](/static/uploads/tmp/one.png)'
    fig = figure(); figs = [fig]
    if fault == 'unattached': fig['attached'] = False
    elif fault == 'unknown_slot': fig['slot_id'] = None
    elif fault == 'duplicate_slot': figs.append(figure('other', '/static/uploads/tmp/two.png'))
    elif fault == 'duplicate_id': figs.append(figure(url='/static/uploads/tmp/two.png', slot='p1-s2'))
    elif fault == 'duplicate_path': figs.append(figure('other', slot='p1-s2'))
    elif fault == 'unowned': image = ''
    elif fault == 'bad_geometry': fig['bbox'][2] = float('inf')
    fixture = source_fixture(['1. 甲题请看配图。' + image, '2. 独立乙题。'],
        figures={0: figs}, layout=fault != 'missing_layout')
    plan = planned(fixture)
    assert plan['mode'] == 'original' and not plan['request_plan']


def test_formula_and_code_references_do_not_invent_dependencies(authorities):
    fixture = source_fixture(['1. 阅读代码`根据第2题中的函数`并比较$第2题$。', '2. 独立乙题。'])
    assert planned(fixture)['mode'] == 'whole_metadata'


@pytest.mark.parametrize('source', [
    ['1. 第一题。', '1. 第二题。'],
    ['1. 第一题。', '2. 根据前面的图完成题目。'],
    ['所有题共用同一条件。', '1. 第一题。', '2. 第二题。'],
])
def test_unknown_structure_or_unresolved_shared_context_never_certifies_clean_subset(authorities, source):
    plan = planned(source_fixture(source))
    assert plan['mode'] == 'original' and not plan['request_plan']


def test_private_page_proof_can_reject_reading_order_for_whole_source(authorities):
    plan = planned(source_fixture(globals=('physical_reading_order_unknown',)))
    assert plan['mode'] == 'original'
    assert 'physical_reading_order_unknown' in plan['global_reasons']


def test_vision_document_context_cannot_authorize_native_metadata_groups(authorities):
    fixture = source_fixture(['一、解答题', '1. 独立甲题。', '2. 独立乙题。'],
        origins=['ocr', 'native', 'native'])
    plan = planned(fixture)
    assert plan['mode'] == 'original' and 'pdf_document_context_not_native' in plan['global_reasons']


def test_reverse_original_answer_section_keeps_all_exact_disjoint_ranges(authorities):
    fixture = source_fixture(['一、解答题\n1. 独立甲题。', '2. 独立乙题。', '3. 独立丙题。',
        '参考答案：\n3. 丙题的原解法。\n1. 甲题的原解法。'])
    plan = planned(fixture)
    assert plan['mode'] == 'whole_metadata', plan['global_reasons']
    questions = plan['_inspection']['questions']
    for question in questions:
        assert question['source_range'] in plan['groups'][0]['owned_ranges']
        if question['answer_source_range'] is not None:
            assert question['answer_source_range'] in plan['groups'][0]['owned_ranges']
            assert fixture['source'][slice(*question['answer_source_range'])] == question['raw_answer']
    assert [q['source_number'] for q in questions] == [1, 2, 3]


@pytest.mark.parametrize('fake', [True, {'status': 'ready', 'pages': []}, None])
def test_json_page_complete_flags_or_word_evidence_cannot_certify_pdf(authorities, fake):
    fixture = source_fixture()
    fixture['source_review_evidence'] = fake
    assert planned(fixture)['mode'] == 'original'


def test_all_vision_and_unreliable_native_sources_keep_old_route(authorities):
    assert planned(source_fixture(origins=['ocr'] * 3))['mode'] == 'original'
    assert planned(source_fixture(reliable=[False] * 3))['mode'] == 'original'


@pytest.mark.parametrize('mutation', ['source', 'page', 'layout', 'route', 'owned', 'context', 'qid', 'budget'])
def test_hmac_binds_pages_layout_task_source_and_group_budget(authorities, mutation):
    plan = planned(source_fixture(origins=['native', 'ocr', 'native']))
    if mutation == 'source': plan['source'] += '来源变化'
    elif mutation == 'page': plan['_source_pages'][0]['origin'] = 'ocr'
    elif mutation == 'layout': plan['_layout_result']['pages'][0]['rotation'] = 90
    elif mutation == 'route': plan['groups'][0]['route'] = 'split'
    elif mutation == 'owned': plan['groups'][0]['owned_ranges'][0][0] += 1
    elif mutation == 'context': plan['groups'][0]['context_ranges'] = [[0, 1]]
    elif mutation == 'qid': plan['groups'][0]['question_ids'][0] = 'known-but-other-id'
    else: plan['fallback_policy']['max_additional_posts'] = 3
    with pytest.raises(PdfHybridPlanError):
        require_pdf_hybrid_plan(plan, task_id='pdf-plan-test', generation=4)


def test_public_certificate_wrong_task_generation_and_modified_projection_cannot_replay(authorities):
    plan = planned(source_fixture())
    with pytest.raises(PdfHybridPlanError): require_pdf_hybrid_plan(plan, task_id='other-task', generation=4)
    with pytest.raises(PdfHybridPlanError): require_pdf_hybrid_plan(plan, task_id='pdf-plan-test', generation=5)
    plan['groups'][0]['_source_metadata_plan']['questions'][0]['content'] += '被修改'
    with pytest.raises(ValueError): require_pdf_hybrid_plan(plan, task_id='pdf-plan-test', generation=4)
    plan = planned(source_fixture())
    plan['_pdf_hybrid_plan_certificate'] = asdict(plan['_pdf_hybrid_plan_certificate'])
    with pytest.raises(PdfHybridPlanError): require_pdf_hybrid_plan(plan, task_id='pdf-plan-test', generation=4)


def test_group_limit_character_policy_and_content_free_diagnostics(authorities):
    parts = [f'{i}. 独立数学问题，请说明理由。' for i in range(1, MAX_GROUPS + 3)]
    origins = ['native' if i % 2 else 'ocr' for i in range(1, len(parts) + 1)]
    assert planned(source_fixture(parts, origins=origins))['mode'] == 'original'
    fixture = source_fixture(origins=['native', 'ocr', 'native'])
    plan = build_pdf_hybrid_plan(**fixture, task_id='pdf-plan-test', generation=4)
    assert plan['mode'] == 'original' and 'native_reuse_below_character_policy' in plan['global_reasons']
    plan = planned(fixture)
    public = pdf_hybrid_plan_diagnostics(plan)
    assert fixture['source'] not in json.dumps(public, ensure_ascii=False)
    assert public['primary_posts'] == 1 and public['max_additional_posts'] == 1
    with pytest.raises(TypeError): json.dumps(plan)


def machine_snapshot(tmp_path, parts=None, *, warning_pages=(), missing_witness=(), complete=True, page_numbers=None):
    """Synthetic returned drafts use real private snapshot/issuer, zero HTTP.

    Input PNGs are integrity fixtures, not a visual oracle for the returned text.
    These fixtures therefore cannot support claims about OCR accuracy.
    """
    from PIL import Image
    from mathbank.pdf_transcription_scopes import (
        begin_pdf_transcription_witness, finish_pdf_transcription_witness,
        finalize_pdf_transcription_snapshot,
    )
    parts = parts or ['1. 独立甲题，请完成计算。', '2. 独立乙题，请写出结论。', '3. 独立丙题，请说明理由。']
    page_numbers = page_numbers or list(range(1, len(parts) + 1))
    public, pages, witnesses = [], [], {}
    for i, text in enumerate(parts):
        number = page_numbers[i]
        path = tmp_path / f'input-page-{i}.png'
        Image.new('RGB', (32, 32), (240, 240, i * 20)).save(path)
        info = {'page_index': number - 1, 'width': 600, 'height': 800, 'candidates': [], 'figure_slots': []}
        first = begin_pdf_transcription_witness(path, info)
        returned = {'markdown': text, 'layout': {'page_complete': complete, 'figures': [], 'notes': []}}
        if i not in missing_witness:
            witnesses[number - 1] = finish_pdf_transcription_witness(first, returned)
        public.append({'page_number': number, 'origin': 'joint_vision',
                       'markdown': f'<!-- MATHBANK_PDF_PAGE:{number} -->\n' + text})
        pages.append({'page_index': number - 1, 'page_number': number, 'figures': [], 'rotation': 0,
                      'status': 'checked', 'warnings': ['局部已有核对提示。'] if i in warning_pages else []})
    source = '\n\n'.join('\n' + text for text in parts)
    diag = {'pdf_native_quality': [{'page_number': 1, 'reasons': ['此前原生字形有疑点。']}],
            'pdf_layout': {'review_pages': len(warning_pages)}}
    layout = {'pages': pages}
    evidence = finalize_pdf_transcription_snapshot(source, diag, source_document_sha256='2' * 64,
        source_pages=public, layout_result=layout, witnesses=witnesses,
        transcript_normalizer=lambda text: text, figure_assets={})
    assert evidence is not None
    return {'source': source, 'diagnostics': diag, 'source_pages': public, 'layout_result': layout,
            'source_document_sha256': '2' * 64, 'source_review_evidence': evidence}


def snapshot_plan(fixture):
    return build_pdf_snapshot_hybrid_plan(**fixture, task_id='snapshot-test', generation=2,
                                          min_metadata_source_characters=0)


def test_real_private_snapshot_and_pdf_issuer_preserve_machine_draft_without_native_claim(tmp_path):
    fixture = machine_snapshot(tmp_path)
    old = deepcopy(fixture['diagnostics'])
    plan = snapshot_plan(fixture)
    assert plan['mode'] == 'whole_metadata', plan['global_reasons']
    assert plan['source_basis'] == 'first_pass_transcription' and plan['native_reliable'] is False
    assert fixture['diagnostics'] == old and fixture['diagnostics']['pdf_native_quality']
    require_pdf_snapshot_hybrid_plan(plan, task_id='snapshot-test', generation=2)
    for group in plan['groups']:
        assert group['route'] == 'metadata' and group['native_reliable'] is False
        local = group['_source_metadata_plan']
        assert local['source_basis'] == 'first_pass_transcription'
        assert local['native_reliable'] is False and '_word_source_certificate' not in local
        for q in local['questions']:
            assert q['raw_content'] == fixture['source'][slice(*q['original_source_range'])]
    public = pdf_snapshot_hybrid_plan_diagnostics(plan)
    assert public['source_basis'] == 'first_pass_transcription' and public['native_reliable'] is False
    with pytest.raises(PdfHybridPlanError):
        require_pdf_hybrid_plan(plan, task_id='snapshot-test', generation=2)


def test_first_pass_page_warning_stays_risky_and_diagnostics_are_not_cleared(tmp_path):
    fixture = machine_snapshot(tmp_path, warning_pages=(1,))
    old = deepcopy(fixture['diagnostics'])
    plan = snapshot_plan(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert [(g['source_numbers'], g['route']) for g in plan['groups']] == [([1], 'metadata'), ([2], 'split'), ([3], 'metadata')]
    assert fixture['diagnostics'] == old and fixture['diagnostics']['pdf_layout']['review_pages'] == 1
    assert plan['fallback_policy']['max_additional_posts'] == 1


def test_first_pass_marked_unknown_formula_only_routes_its_complete_question_to_split(tmp_path):
    fixture = machine_snapshot(tmp_path, parts=['1. 独立甲题。\n\n2. 本题包含[公式待核对]，请核对。\n\n3. 独立丙题。'])
    plan = snapshot_plan(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert [(g['source_numbers'], g['route']) for g in plan['groups']] == [([1], 'metadata'), ([2], 'split'), ([3], 'metadata')]
    assert 'pdf_first_pass_source_marker' in plan['groups'][1]['reasons']


def test_machine_question_spanning_warned_page_keeps_its_whole_draft_old(tmp_path):
    fixture = machine_snapshot(tmp_path, parts=['1. 甲题的前半段。', '这是甲题的后半段。', '2. 独立乙题。'],
        warning_pages=(1,))
    plan = snapshot_plan(fixture)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert plan['groups'][0]['source_numbers'] == [1] and plan['groups'][0]['route'] == 'split'
    assert plan['groups'][0]['page_numbers'] == [1, 2]


def test_retained_machine_source_cannot_certify_a_question_across_unselected_page(tmp_path):
    fixture = machine_snapshot(tmp_path, parts=['1. 甲题前半段。', '这是甲题后半段。', '2. 独立乙题。'],
        page_numbers=[1, 3, 4])
    plan = snapshot_plan(fixture)
    assert plan['mode'] == 'original' and 'pdf_question_crosses_unselected_page_gap' in plan['global_reasons']


def test_public_complete_flag_without_private_witness_cannot_keep_snapshot_context(tmp_path):
    fixture = machine_snapshot(tmp_path, parts=['一、解答题', '1. 独立甲题。', '2. 独立乙题。'], missing_witness=(0,))
    plan = snapshot_plan(fixture)
    assert plan['mode'] == 'original' and 'pdf_document_context_snapshot_unproved' in plan['global_reasons']


@pytest.mark.parametrize('field', ['source_basis', 'native_reliable', 'page_source', 'group_source_basis', 'warnings'])
def test_snapshot_confidence_origin_warnings_and_bytes_cannot_be_upgraded_after_plan(tmp_path, field):
    plan = snapshot_plan(machine_snapshot(tmp_path))
    if field == 'source_basis': plan['source_basis'] = 'native_pdf'
    elif field == 'native_reliable': plan['native_reliable'] = True
    elif field == 'page_source': plan['_source_pages'][0]['origin'] = 'native_repaired'
    elif field == 'group_source_basis': plan['groups'][0]['source_basis'] = 'native_pdf'
    else: plan['_source_diagnostics']['pdf_native_quality'] = []
    with pytest.raises(PdfHybridPlanError):
        require_pdf_snapshot_hybrid_plan(plan, task_id='snapshot-test', generation=2)


def test_snapshot_input_asset_changed_after_plan_is_hard_stop(tmp_path):
    plan = snapshot_plan(machine_snapshot(tmp_path))
    (tmp_path / 'input-page-0.png').write_bytes(b'Input image changed after response')
    with pytest.raises(PdfHybridPlanError):
        require_pdf_snapshot_hybrid_plan(plan, task_id='snapshot-test', generation=2)


def test_transcript_without_full_page_report_and_json_private_snapshot_cannot_authorize_metadata(tmp_path):
    fixture = machine_snapshot(tmp_path, complete=False)
    assert snapshot_plan(fixture)['mode'] == 'original'
    fixture = machine_snapshot(tmp_path)
    fixture['source_review_evidence'] = asdict(fixture['source_review_evidence'])
    assert snapshot_plan(fixture)['mode'] == 'original'


def test_native_font_cache_cannot_reuse_previous_pages_same_named_different_font_resource(monkeypatch):
    """Fault injection proves per-page font-ID consistency, not glyph meaning."""
    import pymupdf as fitz
    from mathbank import pdf_source_scopes
    box = (50, 48, 60, 65)
    raw = {'blocks': [{'type': 0, 'lines': [{'dir': (1, 0), 'spans': [
        {'font': 'SameFontName', 'alpha': 255,
         'chars': [{'c': '1', 'origin': (50, 60), 'bbox': box}]}]}]}]}

    def page(xref, actual_font_gid):
        return SimpleNamespace(rect=fitz.Rect(0, 0, 600, 800),
            get_text=lambda _kind: deepcopy(raw),
            get_fonts=lambda **_kwargs: [(xref, 'ttf', 'TrueType', 'SameFontName')],
            get_texttrace=lambda: [{'font': 'SameFontName', 'type': 0, 'opacity': 1,
                'chars': [(49, 7, (50, 60), box)]}],
            parent=SimpleNamespace(extract_font=lambda _xref: ('', '', '', bytes([actual_font_gid]))))

    monkeypatch.setattr(pdf_source_scopes.fitz, 'Font',
        lambda *, fontbuffer: SimpleNamespace(has_glyph=lambda cp, **_kw: fontbuffer[0] if cp == 49 else 0))
    cache = {}
    first, _, _ = pdf_source_scopes._plain_text_proof(page(10, 7), '1', cache)
    assert first is not None
    second, reason, _ = pdf_source_scopes._plain_text_proof(page(20, 8), '1', cache)
    assert second is None and reason == 'native_font_glyph_identity_unproved'
