from io import BytesIO
import uuid
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('No live model'))


@pytest.mark.parametrize('flag,expected', [(None, False), ('false', False), ('true', True)])
def test_upload_word_verification_contract(client, monkeypatch, flag, expected):
    import main
    submitted = []
    monkeypatch.setattr(main.DOCUMENT_TASKS, 'submit', lambda *a, **k: submitted.append(a))
    fields = {} if flag is None else {'docx_verify_suspicions': flag}
    response = client.post('/api/upload/docx-task', data=fields, files={'file': ('test.docx', BytesIO(b'PK\x03\x04fake'), 'application/octet-stream')},
                           headers={'X-Local-Token': main.LOCAL_TOKEN})
    assert response.status_code == 200
    task_id = response.json()['task_id']
    try: assert submitted[0][-1] is expected
    finally: main.DOCUMENT_TASKS.remove(task_id)


@pytest.mark.parametrize('enabled,pending', [(False, True), (True, False), (True, True)])
def test_word_verification_after_local_checks_without_reparse(monkeypatch, enabled, pending):
    import main
    from mathbank import docx_source_evidence, docx_source_verify
    source = '1. 已知函数，求结果。'
    monkeypatch.setattr(main, 'extract_docx_markdown', lambda *a, **k: {'success': True, 'markdown': source, 'diagnostics': {}, 'image_paths': []})
    calls = []
    def parse(*a, **k):
        calls.append('split')
        return [{'content': '给出函数，求结果。', 'answer_markdown': ''}]
    monkeypatch.setattr(main, 'parse_paper_text_internal', parse)
    def reconcile(q, *a):
        if pending: q[0]['source_review'] = {'required': True, 'reasons': ['review']}
        return {'source_review_count': int(pending)}
    monkeypatch.setattr(main, 'reconcile_visible_math', reconcile)
    def render(*a, **k):
        calls.append('render')
        return {'status': 'unavailable', 'notes': ['renderer unavailable']}
    monkeypatch.setattr(docx_source_evidence, 'prepare_docx_source_evidence', render)
    def verify(*a, **k):
        calls.append('verify')
        return {'status': 'unavailable', 'calls': 0, 'pending': 1}
    monkeypatch.setattr(docx_source_verify, 'verify_docx_source_suspicions', verify)
    task_id = str(uuid.uuid4())
    main.DOCUMENT_TASKS.create(task_id, document_type='docx', temp_assets=[])
    try:
        main.run_docx_parsing_task(task_id, b'fake', 'test.docx', docx_verify_suspicions=enabled)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task['status'] == 'completed', task.get('error')
        assert calls == (['split', 'render', 'verify'] if enabled and pending else ['split'])
        assert task['diagnostics']['docx_source_verification']['calls'] == 0
        assert task['docx_source_cache']['source_markdown'] == source
        assert 'source_review' not in task['docx_source_cache']['split_questions'][0]
    finally: main.DOCUMENT_TASKS.remove(task_id)
