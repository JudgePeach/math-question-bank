"""Preserve reliable native content through both PDF import strategies."""

from types import SimpleNamespace
import uuid

import pymupdf as fitz
import pytest

from mathbank import pdf_inspector_helper as helper


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('No live models'))


def pdf_bytes():
    with fitz.open() as document:
        document.new_page()
        return document.tobytes()


@pytest.mark.parametrize('strategy', ['native_preferred', 'layout_aware'])
@pytest.mark.parametrize('source', [
    '1. 已知 $x=1$，下文列出外文字母 ô、î，请按题意计算。',
    '1. 已知 $A∪B={1,2}$，且 $x≤2$、$x≠1$，求集合。',
])
def test_complete_native_source_does_not_call_any_vision_stage(monkeypatch, strategy, source):
    import main
    from mathbank import pdf_native_regions, pdf_page_vision, pdf_region_vision, pdf_source_verify

    monkeypatch.setattr(helper, '_PDF_INSPECTOR_AVAILABLE', True)
    monkeypatch.setattr(helper, 'pdf_inspector', SimpleNamespace(extract_pages_markdown=lambda *a, **k:
        SimpleNamespace(pages=[SimpleNamespace(page=0, markdown=source, needs_ocr=False)])))
    monkeypatch.setattr(main, 'inspect_and_extract_pdf', helper.inspect_and_extract_pdf)

    def forbidden(*a, **k):
        pytest.fail('Reliable native content must not be retranscribed by a visual model')

    monkeypatch.setattr(pdf_native_regions, 'plan_pdf_regions', forbidden)
    monkeypatch.setattr(pdf_page_vision, 'request_pdf_page', forbidden)
    monkeypatch.setattr(pdf_region_vision, 'request_pdf_regions', forbidden)
    monkeypatch.setattr(pdf_source_verify, 'verify_pdf_source_suspicions', forbidden)
    monkeypatch.setattr(main, 'ocr_pdf_page_image', forbidden)
    split_inputs = []

    def split(text, *a, **k):
        split_inputs.append(text)
        return [{'content': source, 'answer_markdown': ''}]

    monkeypatch.setattr(main, 'parse_paper_text_internal', split)
    task_id = 'native-preference-' + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type='pdf', temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(), 'native.pdf', pdf_strategy=strategy)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task['status'] == 'completed', task.get('error')
        assert len(split_inputs) == 1
        assert task['data'][0]['content'] == source
        assert task['pdf_source_pages'][0]['markdown'].endswith(source)
        report = task['diagnostics']['pdf_extraction']
        assert report['native_pages'] == 1
        assert report['regional_pages'] == report['full_vision_pages'] == 0
        assert task['diagnostics']['pdf_native_quality'] == []
    finally:
        removed = main.DOCUMENT_TASKS.remove(task_id)
        if removed:
            main._delete_task_temp_assets(removed.get('temp_assets', []))
