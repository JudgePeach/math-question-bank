from io import BytesIO
import uuid
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('No live model'))


@pytest.mark.parametrize('flag,expected', [(None, True), ('false', False), ('true', True)])
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


@pytest.mark.parametrize('stage', ['prepare', 'verify'])
def test_optional_word_verification_failure_preserves_split_and_images(monkeypatch, stage):
    import main
    from mathbank import docx_source_evidence, docx_source_verify

    image = f'/{main.UPLOAD_DIR_REL}/tmp/word_verification_fixture.png'
    content = f'已知三角形，如图所示，求其面积。 ![图]({image})'
    source = '1. ' + content
    monkeypatch.setattr(main, 'extract_docx_markdown', lambda *a, **k: {
        'success': True, 'markdown': source, 'diagnostics': {},
        'image_paths': [image], 'image_count': 1,
    })
    calls = []

    def parse(*a, **k):
        calls.append('split')
        return [{'content': content, 'answer_markdown': '', 'referenced_images': [image]}]

    def reconcile(questions, *a):
        questions[0]['source_review'] = {'required': True, 'reasons': ['需核对原文']}
        return {'source_review_count': 1}

    def prepare(*a, **k):
        calls.append('prepare')
        if stage == 'prepare':
            raise OSError('fixture renderer failure')
        return {'status': 'ready', 'pages': []}

    def verify(*a, **k):
        calls.append('verify')
        raise ValueError('fixture verification failure')

    monkeypatch.setattr(main, 'parse_paper_text_internal', parse)
    monkeypatch.setattr(main, 'reconcile_visible_math', reconcile)
    monkeypatch.setattr(docx_source_evidence, 'prepare_docx_source_evidence', prepare)
    monkeypatch.setattr(docx_source_verify, 'verify_docx_source_suspicions', verify)
    deleted = []
    monkeypatch.setattr(main, '_delete_task_temp_assets', lambda paths: deleted.extend(paths))
    task_id = str(uuid.uuid4())
    main.DOCUMENT_TASKS.create(task_id, document_type='docx', temp_assets=[])
    try:
        main.run_docx_parsing_task(task_id, b'fixture', '带图.docx', docx_verify_suspicions=True)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task['status'] == 'completed', task.get('error')
        assert calls == (['split', 'prepare'] if stage == 'prepare' else ['split', 'prepare', 'verify'])
        assert task['data'][0]['content'] == content
        assert task['data'][0]['image_paths'] == [image]
        assert task['data'][0]['source_review']['required'] is True
        assert task['data'][0]['source_review']['blocking'] is False
        assert task['diagnostics']['docx_source_verification']['status'] == 'failed'
        assert task['diagnostics']['source_review_count'] == 1
        assert image in task['temp_assets']
        assert deleted == []
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


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


@pytest.mark.parametrize('enabled', [True, False])
def test_native_formula_notice_is_verified_by_default_and_only_final_count_is_reported(monkeypatch, enabled):
    import main
    from mathbank import docx_source_evidence, docx_source_verify
    source = '1. 求 $x+1$ [公式结构待核对] 的值。'
    warning = '部分 Office 公式含暂未完整支持的结构：fixture'
    monkeypatch.setattr(main, 'extract_docx_markdown', lambda *a, **k: {
        'success': True, 'markdown': source, 'image_paths': [],
        'diagnostics': {'omml_converted': 1, 'omml_unsupported': 1,
                        'review_required': 1, 'warnings': [warning]},
    })
    monkeypatch.setattr(main, 'parse_paper_text_internal', lambda *a, **k: [
        {'content': source[3:], 'answer_markdown': '', 'referenced_images': []}])
    calls = []

    def render(*a, **k):
        calls.append('render')
        return {'status': 'ready'}

    def verify(questions, diagnostics, *a, **k):
        calls.append('verify')
        assert diagnostics['source_review_count'] == 1
        assert questions[0]['source_review']['required'] is True
        questions[0]['content'] = questions[0]['content'].replace('[公式结构待核对]', '')
        questions[0]['source_review'].update(required=False, verified_by='vision',
                                            verification={'decision': 'equivalent'})
        diagnostics['source_review_count'] = 0
        return {'status': 'completed', 'calls': 1, 'checked': 1, 'confirmed': 1, 'pending': 0}

    monkeypatch.setattr(docx_source_evidence, 'prepare_docx_source_evidence', render)
    monkeypatch.setattr(docx_source_verify, 'verify_docx_source_suspicions', verify)
    task_id = str(uuid.uuid4())
    main.DOCUMENT_TASKS.create(task_id, document_type='docx', temp_assets=[])
    try:
        # Omitted flag must take the new default; explicit false is still honored.
        kwargs = {} if enabled else {'docx_verify_suspicions': False}
        main.run_docx_parsing_task(task_id, b'fixture', '公式.docx', **kwargs)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task['status'] == 'completed', task.get('error')
        assert calls == (['render', 'verify'] if enabled else [])
        report = task['diagnostics']
        assert report['review_required'] == 1
        assert report['word_extraction_review_count'] == (0 if enabled else 1)
        assert report['source_review_count'] == (0 if enabled else 1)
        assert report['word_extraction_warnings'] == ([] if enabled else [warning])
        assert report['word_extraction_warnings_original'] == [warning]
        assert '$x+1$' in task['data'][0]['content']
    finally:
        main.DOCUMENT_TASKS.remove(task_id)
