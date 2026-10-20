"""Archived protocol compatibility never adds a model call or source proof."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from pdf_feature_contract import experimental_pdf_only
import requests

from mathbank.document_projects import DocumentProjectStore
from mathbank.pdf_navigation_cache import bind_pdf_navigation
from mathbank.pdf_ocr_archive import retain_pdf_ocr_response
from mathbank.pdf_ocr_protocol_recovery import restore_pdf_protocol_pages
from mathbank.pdf_task_resume import prepare_pdf_checkpoint, seal_pdf_checkpoint
from mathbank.task_manager import TaskCancelled


TEXT = '16. 已保存的原题 $x=1$。\n17. 原题不能改写。'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request',
        lambda *_a, **_kw: pytest.fail('Protocol recovery must make zero API calls'))


@pytest.fixture
def case(tmp_path):
    source = b'%PDF original source'
    digest = hashlib.sha256(source).hexdigest()
    store = DocumentProjectStore(tmp_path/'projects')
    project = store.create(source, 'exam.pdf', {}, {})['project_id']
    png = tmp_path/'pdf_page_test_6.png'
    Image.new('RGB', (12, 12), 'white').save(png)
    url = '/static/uploads/tmp/' + png.name
    files = {url: png}
    state = prepare_pdf_checkpoint(None, task_id='old-task', document_sha256=digest,
        page_indices=[6], strategy='layout_aware', verify_suspicions=False,
        resolve_asset=files.__getitem__)
    bind_pdf_navigation(state, 6, url)
    checkpoint = seal_pdf_checkpoint(state, assets=[url], resolve_asset=files.__getitem__)
    packet = {'markdown': TEXT, 'figures': [], 'ignored_candidates': [],
              'page_complete': False, 'warnings': ['原有核对提示'], 'WARN': '请核对本页'}
    attempts = []
    def archive(packet=packet, *, raw=None, finish='stop', run=0, attempt=2,
                stage='joint_page', image_path=png):
        raw = json.dumps(packet, ensure_ascii=False) if raw is None else raw
        body = {'choices': [{'finish_reason': finish, 'message': {'content': raw}}]}
        metadata = {'stage': stage, 'page_index': 6, 'attempt': attempt,
            'http_status': 200, 'model': 'fixture-vision',
            'request_payload_sha256': 'a'*64}
        receipt = retain_pdf_ocr_response(store, project, digest, body, metadata,
            page_number=7, image_path=image_path, run=run)
        attempts.append({'stage': stage, 'page_index': 6, 'page_number': 7,
            'run': run, 'attempt': attempt, 'status': 'failed', 'http_status': 200,
            'finish_reason': finish, 'response_retained': True,
            'response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'error': '原失败记录必须保留', 'elapsed_seconds': 7.5})
        sync()
        return receipt
    def sync():
        store.sync_task(project, {'task_id': 'new-working-task', 'status': 'running',
            'pdf_resume_state': checkpoint, 'diagnostics': {'pdf_vision': {'attempts': attempts}}})
    sync()
    def restore(checkpoint_override=None, **kwargs):
        return restore_pdf_protocol_pages(store, project,
            checkpoint if checkpoint_override is None else checkpoint_override,
            [url], {6: {'page_index': 6, 'candidates': []}},
            resolve_asset=files.__getitem__, **kwargs)
    return SimpleNamespace(**locals())


@experimental_pdf_only
def test_warn_packet_recovers_verbatim_without_writes_witness_or_success(case, monkeypatch):
    receipt = case.archive()
    before = deepcopy(case.checkpoint)
    saved = case.store.load_task(case.project)
    original = case.store.load_stage(case.project, receipt['stage'], receipt['input_fingerprint'])
    for method in ('save_stage', 'sync_task', 'register_assets', 'save_draft'):
        monkeypatch.setattr(case.store, method, lambda *_a, **_kw: pytest.fail('No store mutation'))
    pages, report = case.restore()
    assert set(pages) == {6} and pages[6]['markdown'] == TEXT
    joint = pages[6]['joint']
    assert joint['ocr_succeeded'] is False and joint['original_verified'] is False
    assert joint['layout']['page_complete'] is False
    assert joint['layout']['notes'] == ['待核对：原有核对提示', '待核对：请核对本页']
    assert joint['attempts'] == case.attempts and joint['attempts'][0]['status'] == 'failed'
    assert '_transcription_witness' not in joint
    assert joint['visual_calls'] == report['new_calls'] == report['ai_calls'] == 0
    assert joint['protocol_recovery']['checkpoint_task_id'] == 'old-task'
    assert joint['protocol_recovery']['working_task_id'] == 'new-working-task'
    assert case.checkpoint == before and case.store.load_task(case.project) == saved
    assert case.store.load_stage(case.project, receipt['stage'], receipt['input_fingerprint']) == original
    assert '原题' not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize('change', ['without_warn', 'alias', 'missing', 'other_extra',
    'warn_empty', 'warn_list', 'warn_long', 'complete_string', 'unknown_path', 'unknown_figure'])
def test_recovery_is_narrower_than_general_text_retention(case, change):
    packet = deepcopy(case.packet)
    if change == 'without_warn': packet.pop('WARN')
    elif change == 'alias': packet['math_markdown_text'] = packet.pop('markdown')
    elif change == 'missing': packet.pop('page_complete')
    elif change == 'other_extra': packet['extra'] = 'not supported'
    elif change == 'warn_empty': packet['WARN'] = '  '
    elif change == 'warn_list': packet['WARN'] = ['not supported']
    elif change == 'warn_long': packet['WARN'] = 'x'*501
    elif change == 'complete_string': packet['page_complete'] = 'false'
    elif change == 'unknown_path': packet['markdown'] += '\n![](/static/uploads/unknown.png)'
    elif change == 'unknown_figure':
        packet['markdown'] += '\n[插图待补：图1]'
        packet['figures'] = [{'slot': '图1', 'bbox': [100, 100, 200, 200],
            'candidate_ids': ['unknown'], 'review_required': True, 'review_reason': ''}]
    case.archive(packet)
    pages, report = case.restore()
    assert pages == {} and report['pending_pages'][0]['page_number'] == 7
    assert report['new_calls'] == 0


@pytest.mark.parametrize('kind', ['length', 'missing_finish', 'truncated', 'duplicate', 'prose', 'nan'])
def test_incomplete_or_non_strict_json_never_recovers(case, kind):
    raw = json.dumps(case.packet, ensure_ascii=False)
    finish = 'stop'
    if kind == 'length': finish = 'length'
    elif kind == 'missing_finish': finish = None
    elif kind == 'truncated': raw = raw[:-3]
    elif kind == 'duplicate': raw = raw[:-1] + ',"WARN":"other"}'
    elif kind == 'prose': raw += '\nThis may be repaired by another parser.'
    elif kind == 'nan': raw = raw.replace('false', 'NaN')
    case.archive(raw=raw, finish=finish)
    assert case.restore()[0] == {}


@pytest.mark.parametrize('kind', ['seal', 'source', 'pixels', 'future_run', 'no_navigation',
    'wrong_page', 'accepted_page', 'running', 'succeeded', 'raw_sha', 'not_retained'])
def test_source_page_seal_and_actual_failed_attempt_are_required(case, kind):
    case.archive(run=1 if kind == 'future_run' else 0)
    checkpoint = deepcopy(case.checkpoint)
    if kind == 'seal': checkpoint['state_sha256'] = '0'*64
    elif kind == 'source':
        checkpoint['document_sha256'] = '0'*64
        checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    elif kind == 'pixels': Image.new('RGB', (12, 12), 'black').save(case.png)
    elif kind == 'no_navigation':
        checkpoint.pop('navigation_pages')
        checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    elif kind == 'wrong_page':
        checkpoint['navigation_pages']['6']['page_index'] = 5
        checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    elif kind == 'accepted_page':
        checkpoint['pages']['6'] = {'markdown': '已接收正文', 'joint': None}
        checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    else:
        if kind in ('running', 'succeeded'): case.attempts[0]['status'] = kind
        elif kind == 'raw_sha': case.attempts[0]['response_sha256'] = '0'*64
        elif kind == 'not_retained': case.attempts[0]['response_retained'] = False
        case.sync()
    assert case.restore(checkpoint)[0] == {}


@experimental_pdf_only
def test_alias_first_attempt_is_not_repaired_when_second_warn_packet_is_recovered(case):
    case.archive({'math_markdown_text': '不可通过别名恢复', 'figures': [], 'ignored_candidates': []}, attempt=1)
    case.archive()
    pages, report = case.restore()
    assert pages[6]['markdown'] == TEXT and len(pages[6]['joint']['attempts']) == 2
    assert len(report['rejected_archives']) == 1
    assert all(row['status'] == 'failed' for row in case.store.load_task(case.project)['diagnostics']['pdf_vision']['attempts'])


@experimental_pdf_only
def test_multiple_compatible_packets_are_ambiguous_even_if_latest_looks_better(case):
    case.archive(attempt=1)
    packet = deepcopy(case.packet); packet['markdown'] += '\n另一个版本。'
    case.archive(packet, attempt=2)
    pages, report = case.restore()
    assert pages == {} and report['pending_pages'][0]['reason'] == 'ambiguous_archives'


def test_archive_with_changed_body_or_content_address_is_not_reused(case):
    receipt = case.archive()
    record = case.store.load_stage(case.project, receipt['stage'], receipt['input_fingerprint'])
    record['body']['choices'][0]['message']['content'] += 'tamper'
    case.store.save_stage(case.project, receipt['stage'], record, receipt['input_fingerprint'])
    assert case.restore()[0] == {}


@experimental_pdf_only
def test_only_missing_page_info_is_needed_and_confirmed_page_is_untouched(case):
    case.archive()
    checkpoint = deepcopy(case.checkpoint)
    checkpoint['page_indices'] = [0, 6]
    checkpoint['pages']['0'] = {'markdown': '已经接收的页保持不变', 'joint': None}
    checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    pages, _ = restore_pdf_protocol_pages(case.store, case.project, checkpoint,
        ['/static/uploads/tmp/pdf_page_no_read_0.png', case.url],
        {6: {'page_index': 6, 'candidates': []}}, resolve_asset=case.files.__getitem__)
    assert set(pages) == {6}
    assert checkpoint['pages']['0']['markdown'] == '已经接收的页保持不变'


def test_cancellation_propagates_without_recovery(case):
    case.archive()
    def cancel(): raise TaskCancelled('cancelled')
    with pytest.raises(TaskCancelled): case.restore(check_cancelled=cancel)


def test_evidence_change_during_read_discards_derived_page(case, monkeypatch):
    case.archive()
    load = case.store.load_stage
    def changed(*args, **kwargs):
        value = load(*args, **kwargs)
        Image.new('RGB', (12, 12), 'black').save(case.png)
        return value
    monkeypatch.setattr(case.store, 'load_stage', changed)
    assert case.restore()[0] == {}


@pytest.mark.parametrize('with_warn', [False, True])
@pytest.mark.parametrize('review_required', [False, True])
@experimental_pdf_only
def test_known_boolean_diagnostic_alone_or_combined_preserves_warnings(case, with_warn, review_required):
    packet = deepcopy(case.packet)
    if not with_warn: packet.pop('WARN')
    packet['review_required'] = review_required
    packet['page_complete'] = True
    case.archive(packet)
    pages, report = case.restore()
    joint = pages[6]['joint']
    assert joint['markdown'] == packet['markdown']
    assert joint['layout']['page_complete'] is True
    assert joint['ocr_succeeded'] is False
    notes = joint['layout']['notes']
    assert '待核对：原有核对提示' in notes
    assert ('待核对：请核对本页' in notes) is with_warn
    assert any('本页识图结果仍需对照原页核对' in note for note in notes) is review_required
    assert report['new_calls'] == 0


@pytest.mark.parametrize('value', [0, 1, None, 'True', 'False', ' false', 'false ', '', [], {}])
def test_review_required_is_strict_boolean_without_coercion(case, value):
    packet = deepcopy(case.packet)
    packet['review_required'] = value
    case.archive(packet)
    assert case.restore()[0] == {}


@pytest.mark.parametrize('value', ['true', 'false'])
@experimental_pdf_only
def test_exact_string_boolean_is_only_format_warning_and_never_coerced(case, value):
    packet = deepcopy(case.packet)
    packet.pop('WARN'); packet['review_required'] = value
    case.archive(packet)
    pages, _ = case.restore()
    assert pages[6]['joint']['layout']['page_complete'] is False
    assert pages[6]['joint']['ocr_succeeded'] is False
    assert '待核对：原有核对提示' in pages[6]['joint']['layout']['notes']
    assert len(pages[6]['joint']['layout']['notes']) == 2
    assert packet['review_required'] == value


@experimental_pdf_only
def test_length_first_attempt_is_not_adopted_before_complete_diagnostic_second_attempt(case):
    case.archive(raw='{"markdown":"cut off', finish='length', attempt=1)
    packet = deepcopy(case.packet)
    packet.pop('WARN'); packet['review_required'] = True; packet['page_complete'] = True
    case.archive(packet)
    pages, report = case.restore()
    assert pages[6]['markdown'] == TEXT
    assert pages[6]['joint']['attempts'][0]['finish_reason'] == 'length'
    assert pages[6]['joint']['attempts'][0]['status'] == 'failed'
    assert pages[6]['joint']['protocol_recovery']['attempt'] == 2
    assert len(report['rejected_archives']) == 1


@pytest.mark.parametrize('top_diagnostics', [False, True])
@experimental_pdf_only
def test_sealed_historical_failure_recovers_after_new_run_and_missing_top_diagnostics(case, top_diagnostics):
    case.archive()
    checkpoint = deepcopy(case.checkpoint)
    checkpoint['run'] = 1
    checkpoint['diagnostics'] = {'pdf_vision': {'attempts': deepcopy(case.attempts)}}
    checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    unknown = {'stage': 'joint_page', 'page_index': 6, 'page_number': 7,
               'run': 1, 'attempt': 1, 'status': 'running', 'usage': {}}
    diagnostics = {'pdf_vision': {'attempts': [*deepcopy(case.attempts), unknown]}} if top_diagnostics else {}
    case.store.sync_task(case.project, {'task_id': 'new-working-task', 'status': 'cancelled',
        'pdf_resume_state': checkpoint, 'diagnostics': diagnostics})
    before = case.store.load_task(case.project)
    pages, report = case.restore(checkpoint)
    assert pages[6]['markdown'] == TEXT and report['has_compatible_archive_pages'] == [7]
    receipt = pages[6]['joint']['protocol_recovery']
    assert receipt['source_run'] == 0 and receipt['working_run'] == 1
    assert receipt['later_attempts'] == ([{key: unknown.get(key) for key in
        ('run','attempt','status','http_status','finish_reason','response_sha256')}] if top_diagnostics else [])
    assert pages[6]['joint']['attempts'] == case.attempts
    assert case.store.load_task(case.project) == before


@experimental_pdf_only
def test_compatible_archive_is_reported_even_when_no_failed_evidence_is_persisted(case):
    case.archive()
    case.store.sync_task(case.project, {'task_id': 'new-working-task', 'diagnostics': {}})
    pages, report = case.restore()
    assert pages == {} and report['has_compatible_archive_pages'] == [7]
    assert report['pending_pages'] == [{'page_number': 7, 'reason': 'failed_attempt_evidence_missing'}]
    assert report['compatible_archives'][0]['source_run'] == 0
    assert report['new_calls'] == 0


@experimental_pdf_only
def test_unknown_later_request_cannot_stand_in_for_missing_historical_failed_proof(case):
    case.archive()
    checkpoint = deepcopy(case.checkpoint); checkpoint['run'] = 1
    checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    case.store.sync_task(case.project, {'task_id': 'new-working-task', 'diagnostics': {
        'pdf_vision': {'attempts': [{'stage': 'joint_page', 'page_index': 6, 'run': 1,
            'attempt': 2, 'status': 'running'}]}}})
    pages, report = case.restore(checkpoint)
    assert pages == {} and report['has_compatible_archive_pages'] == [7]


@experimental_pdf_only
def test_conflicting_historical_attempt_outcomes_do_not_silently_choose_sealed_version(case):
    case.archive()
    checkpoint = deepcopy(case.checkpoint)
    checkpoint['diagnostics'] = {'pdf_vision': {'attempts': deepcopy(case.attempts)}}
    checkpoint = seal_pdf_checkpoint(checkpoint, assets=[], resolve_asset=case.files.__getitem__)
    case.attempts[0]['response_sha256'] = '0'*64; case.sync()
    pages, report = case.restore(checkpoint)
    assert pages == {} and report['has_compatible_archive_pages'] == [7]


def test_no_archive_is_not_a_compatible_candidate(case):
    pages, report = case.restore()
    assert pages == {} and report['has_compatible_archive_pages'] == []


@experimental_pdf_only
def test_changed_page_after_candidate_scan_remains_reported_for_no_paid_fallback(case, monkeypatch):
    case.archive()
    load = case.store.load_stage
    def changed(*args, **kwargs):
        value = load(*args, **kwargs)
        Image.new('RGB', (12, 12), 'black').save(case.png)
        return value
    monkeypatch.setattr(case.store, 'load_stage', changed)
    pages, report = case.restore()
    assert pages == {} and report['has_compatible_archive_pages'] == [7]
