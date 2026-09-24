"""Native math repair routing, with no paid requests and no user data writes."""
from types import SimpleNamespace
import os

import pymupdf as fitz
import pytest

from mathbank import pdf_inspector_helper as helper


def pdf_bytes(count=2):
    with fitz.open() as document:
        for _ in range(count):
            document.new_page()
        return document.tobytes()


@pytest.fixture
def repair(monkeypatch):
    from mathbank import pdf_native_math
    calls = []
    def run(page, inspector_items=None):
        calls.append(page.number)
        return {'status': 'repaired', 'markdown': '题目 1：已知 $x_i^2=1$，求值。',
                'notes': ['已按字号及位置恢复上下标。'], 'stats': {'glyphs_consumed': 20}}
    monkeypatch.setattr(pdf_native_math, 'repair_native_page', run)
    return calls


def test_repair_runs_before_page_is_routed_to_vision_preserving_original(repair, monkeypatch):
    broken = '题目 1：公式\uf8f1待恢复'
    monkeypatch.setattr(helper, '_PDF_INSPECTOR_AVAILABLE', True)
    monkeypatch.setattr(helper, 'pdf_inspector', SimpleNamespace(extract_pages_markdown=lambda *a, **k:
        SimpleNamespace(pages=[SimpleNamespace(page=0, markdown=broken, needs_ocr=False)])))
    result = helper.inspect_and_extract_pdf(pdf_bytes())
    assert repair == [0] and result['pages_needing_ocr'] == []
    page = result['pages'][0]
    assert page['source'] == 'native-math-repaired'
    assert page['original_markdown'] == broken
    assert page['quality_reasons'] == []
    assert page['native_repair']['original_quality_reasons']
    assert page['native_repair']['status'] == 'repaired'
    assert '$x_i^2=1$' in result['markdown']


def test_reliable_native_page_is_not_rewritten(repair):
    pages = [{'page_index': 1, 'markdown': '已可靠提取的原文。', 'needs_ocr': False}]
    assert helper._repair_native_pages(pdf_bytes(), pages) == pages
    assert not repair and 'native_repair' not in pages[0]


def test_selected_page_order_and_unselected_pages_are_preserved(repair):
    pages = [{'page_index': i, 'markdown': '\uf8f1', 'needs_ocr': True, 'quality_reasons': ['bad']} for i in (2, 0)]
    output = helper._repair_native_pages(pdf_bytes(3), pages)
    assert repair == [2, 0]
    assert [page['page_index'] for page in output] == [2, 0]


@pytest.mark.parametrize('status,markdown', [('unsupported', 'looks complete'), ('repaired', ''),
                                          ('repaired', '残留\uf8f1'), ('repaired', 'A. B. C. D.')])
def test_incomplete_repair_keeps_its_original_visual_route(monkeypatch, status, markdown):
    from mathbank import pdf_native_math
    monkeypatch.setattr(pdf_native_math, 'repair_native_page', lambda *a: {'status': status, 'markdown': markdown})
    pages = [{'page_index': 0, 'markdown': 'original unsafe text', 'needs_ocr': True, 'quality_reasons': ['bad']}]
    result = helper._repair_native_pages(pdf_bytes(), pages)[0]
    assert result['needs_ocr'] is True
    assert result['markdown'] == 'original unsafe text'
    assert result['quality_reasons'] == ['bad']
    assert result['native_repair']['status'] == 'fallback'


def test_repair_exception_is_not_success_or_extraction_failure(monkeypatch):
    from mathbank import pdf_native_math
    def fail(*a): raise ValueError('a local unsupported shape')
    monkeypatch.setattr(pdf_native_math, 'repair_native_page', fail)
    pages = [{'page_index': 0, 'markdown': 'raw', 'needs_ocr': True}]
    result = helper._repair_native_pages(pdf_bytes(), pages)[0]
    assert result['needs_ocr'] and result['markdown'] == 'raw'
    assert result['native_repair']['status'] == 'fallback'


def test_unreadable_pdf_does_not_use_a_fabricated_repair(repair):
    pages = [{'page_index': 0, 'markdown': 'raw', 'needs_ocr': True}]
    assert helper._repair_native_pages(b'not a pdf', pages) == pages
    assert not repair and 'native_repair' not in pages[0]


def test_legacy_aggregate_is_not_replaced_by_only_its_first_page(repair, monkeypatch):
    monkeypatch.setattr(helper, '_PDF_INSPECTOR_AVAILABLE', True)
    monkeypatch.setattr(helper, 'pdf_inspector', SimpleNamespace(process_pdf=lambda *a, **k:
        SimpleNamespace(pdf_type='text_based', markdown='原文\uf8f1' + '两页均须保留' * 20,
                        has_encoding_issues=False, pages_needing_ocr=[])))
    result = helper.inspect_and_extract_pdf(pdf_bytes())
    assert not repair and result['pages'][0]['needs_ocr']


def test_pymupdf_fallback_can_attempt_the_same_local_repair(repair, monkeypatch):
    monkeypatch.setattr(helper, '_PDF_INSPECTOR_AVAILABLE', False)
    monkeypatch.setattr(helper, '_extract_fitz_pages', lambda *a: [
        {'page_index': 0, 'markdown': '\uf8f1', 'needs_ocr': True, 'quality_reasons': ['bad']}])
    result = helper.inspect_and_extract_pdf(pdf_bytes())
    assert repair == [0] and not result['pages_needing_ocr']
    assert result['available'] is False and result['is_text_based']


@pytest.mark.parametrize('strategy', ['layout_aware', 'native_preferred'])
def test_repaired_native_page_skips_all_visual_calls_and_shares_one_split(monkeypatch, strategy):
    import uuid
    import main
    from mathbank import pdf_native_regions, pdf_page_vision, pdf_region_vision

    source = '题目 1：已知 $x_i^2=1$，求 $x_i$。'
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('No model network'))
    monkeypatch.setattr(main, 'inspect_and_extract_pdf', lambda *a, **k: {
        'pdf_type': 'text_based', 'pages': [{'page_index': 0, 'markdown': source, 'needs_ocr': False,
        'quality_reasons': [], 'native_repair': {'status': 'repaired', 'notes': ['本地恢复2处上下标。']}}]})
    def forbidden(*a, **k): pytest.fail('Repaired native page must not request visual recognition')
    monkeypatch.setattr(pdf_native_regions, 'plan_pdf_regions', forbidden)
    monkeypatch.setattr(pdf_page_vision, 'request_pdf_page', forbidden)
    monkeypatch.setattr(pdf_region_vision, 'request_pdf_regions', forbidden)
    monkeypatch.setattr(main, 'ocr_pdf_page_image', forbidden)
    split_inputs = []
    def split(text, *a, **k):
        split_inputs.append(text)
        return [{'content': source, 'answer_markdown': ''}]
    monkeypatch.setattr(main, 'parse_paper_text_internal', split)
    task_id = 'native-math-flow-' + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type='pdf', temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(1), 'test.pdf', pdf_strategy=strategy)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task['status'] == 'completed', task.get('error')
        assert len(split_inputs) == 1
        report = task['diagnostics']
        assert report['pdf_extraction']['native_pages'] == report['pdf_extraction']['repaired_pages'] == 1
        assert report['pdf_extraction']['regional_pages'] == report['pdf_extraction']['full_vision_pages'] == 0
        assert report['pdf_native_quality'] == []
        assert report['pdf_native_repair'][0]['status'] == 'repaired'
        assert task['pdf_source_pages'][0]['origin'] == 'native_repaired'
        if strategy == 'layout_aware':
            assert report['pdf_layout']['joint_visual_calls'] == report['pdf_layout']['visual_calls'] == 0
    finally:
        removed = main.DOCUMENT_TASKS.remove(task_id)
        if removed:
            main._delete_task_temp_assets(removed.get('temp_assets', []))


@pytest.mark.skipif(os.environ.get('MATHBANK_LIVE_NATIVE_REPAIR') != '1',
                    reason='Explicit opt-in: one configured text-model request in an isolated database')
def test_real_repaired_pdf_splits_once_without_vision(db_session, monkeypatch):
    import hashlib
    import json
    import time
    import uuid
    from pathlib import Path
    import main
    from mathbank import pdf_page_vision, pdf_region_vision, pdf_source_verify
    from mathbank.paths import SYSTEM_GENERATED_DIR

    assert str(main.engine.url) == 'sqlite:///:memory:', 'Live audit must use the test database'
    path = Path(os.environ['MATHBANK_NATIVE_REPAIR_PDF'])
    data = path.read_bytes()
    original_hash = hashlib.sha256(data).hexdigest()
    output = SYSTEM_GENERATED_DIR / 'pdf-structure-repair'
    output.mkdir(exist_ok=True)
    calls = []
    post = main.post_chat_completion
    def observe(provider, payload, **kwargs):
        assert not calls, 'No paid retries or second model call in this audit'
        calls.append(provider.model_name)
        kwargs['retry_connection'] = False
        return post(provider, payload, **kwargs)
    monkeypatch.setattr(main, 'post_chat_completion', observe)
    def forbid(*a, **k): pytest.fail('The repaired page must not request vision')
    monkeypatch.setattr(pdf_page_vision, 'request_pdf_page', forbid)
    monkeypatch.setattr(pdf_region_vision, 'request_pdf_regions', forbid)
    monkeypatch.setattr(main, 'ocr_pdf_page_image', forbid)
    monkeypatch.setattr(pdf_source_verify, 'verify_pdf_source_suspicions', forbid)
    task_id = 'native-repair-audit-' + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type='pdf', temp_assets=[])
    started = time.monotonic()
    try:
        main.run_pdf_parsing_task(task_id, data, path.name, pdf_strategy='layout_aware', pdf_verify_suspicions=True)
        result = main.DOCUMENT_TASKS.snapshot(task_id)
        result['audit'] = {'text_requests': len(calls), 'models': calls, 'seconds': round(time.monotonic() - started, 2),
                           'database': 'isolated in-memory database', 'database_imported': False,
                           'pdf_sha256': original_hash}
        (output / 'live-task.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        assert result['status'] == 'completed', result.get('error')
        assert len(calls) == 1 and len(result['data']) == 4
        report = result['diagnostics']
        assert report['pdf_extraction']['repaired_pages'] == report['pdf_extraction']['native_pages'] == 1
        assert report['pdf_extraction']['full_vision_pages'] == report['pdf_extraction']['regional_pages'] == 0
        assert report['pdf_layout']['joint_visual_calls'] == report['pdf_layout']['visual_calls'] == 0
        assert report['source_review_count'] == 0 and not report['unmatched_source']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    finally:
        removed = main.DOCUMENT_TASKS.remove(task_id)
        if removed:
            main._delete_task_temp_assets(removed.get('temp_assets', []))
