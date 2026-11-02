"""PDF card-level tolerance through real HTTP in a source-only workspace."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest


SCENARIO = r'''
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid

import requests
requests.sessions.Session.request = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No live network"))
import pymupdf as fitz
from fastapi.testclient import TestClient
from mathbank.ai_providers import resolve_text_provider
from mathbank.database import SessionLocal, Question, engine
import main

root = Path.cwd().resolve()
assert Path(main.__file__).resolve().parent == root
assert Path(engine.url.database).resolve() == root / 'math_question_bank.db'
assert not (root / '.env').exists()
assert main.PDF_CLOUD_ONLY is True
strategy = os.environ['MATHBANK_ISOLATED_PDF_STRATEGY']
main.METADATA_CACHE = {'curriculum_version': 'A', 'curriculum': {'必修一': {'函数': ['函数值']}}}
main.schedule_database_export = lambda *_a, **_k: None
provider = resolve_text_provider('deepseek-flash', {'DEEPSEEK_API_KEY': 'fake-offline-only'})
main.resolve_text_provider = lambda *_a, **_k: provider
source = '一、解答题\n1. 第一题完整原文，请计算一加一。\n2. 第二题完整原文，请计算二加二。'
main.inspect_and_extract_pdf = lambda *_a, **_k: {'pdf_type': 'scanned',
    'pages': [{'page_index': 0, 'needs_ocr': True, 'markdown': ''}]}
ocr_calls, split_calls = [], []
def ocr(*_a, **_k):
    ocr_calls.append(1)
    return source
main.ocr_pdf_page_image = ocr
import mathbank.pdf_page_vision as page_vision
import mathbank.pdf_native_regions as regional
regional.plan_pdf_regions = lambda *_a, **_k: None
def joint_ocr(path, info, **kwargs):
    ocr_calls.append(1)
    return page_vision._validated_page({'markdown': source, 'figures': [],
        'ignored_candidates': [], 'page_complete': True, 'warnings': []}, info)
page_vision.request_pdf_page = joint_ocr
good = [{'content': '第一题完整原文，请计算一加一。', 'answer_markdown': '[EXTRACTED_ORIGINAL]原答案：二。',
         'question_type': 'detailed_answer', 'difficulty': 'normal', 'category_compulsory': '必修一',
         'category_chapter': '函数', 'category_knowledge': '函数值'},
        {'content': '第二题完整原文，请计算二加二。', 'answer_markdown': '[EXTRACTED_ORIGINAL]原答案：四。',
         'question_type': 'detailed_answer', 'difficulty': 'normal', 'category_compulsory': '必修一',
         'category_chapter': '函数', 'category_knowledge': '函数值'}]
rows = [deepcopy(good[0]), None, {'content': ''},
        {'content': 'private rejected question', 'answer_markdown': {'private': 'invalid answer'}},
        deepcopy(good[1])]
class Reply:
    status_code = 200
    headers = {}
    def json(self):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'questions': rows})}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
def post(config, payload, **kwargs):
    assert config is provider and kwargs['retry_connection'] is False
    split_calls.append(1)
    return Reply()
main.post_chat_completion = post
with fitz.open() as pdf:
    page = pdf.new_page(width=300, height=400)
    page.insert_text((30, 40), 'Isolated PDF fixture')
    raw = pdf.tobytes()
with TestClient(main.app) as client:
    client.headers.update({'X-Local-Token': main.LOCAL_TOKEN})
    reply = client.post('/api/upload/pdf-task', files={'file': ('mixed.pdf', raw, 'application/pdf')},
        data={'client_request_id': str(uuid.uuid4()), 'pdf_strategy': strategy,
              'pdf_verify_suspicions': 'false', 'paper_title': 'partial-question-fixture'})
    assert reply.status_code == 200, reply.text
    task_id = reply.json()['task_id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        task = client.get(f'/api/tasks/{task_id}/status').json()
        if task['status'] in {'completed', 'error', 'cancelled'}:
            with main.DOCUMENT_TASKS._lock:
                if main.DOCUMENT_TASKS._records[task_id].future is None:
                    break
        time.sleep(.01)
    else:
        raise AssertionError('Isolated task did not settle')
    assert task['status'] == 'completed' and task['partial'] is True, task
    assert ocr_calls == split_calls == [1]
    assert [q['content'] for q in task['data']] == [q['content'] for q in good]
    assert [q['answer_markdown'] for q in task['data']] == ['原答案：二。', '原答案：四。']
    report = task['diagnostics']['pdf_question_validation']
    assert report == {'received_count': 5, 'accepted_count': 2, 'rejected_count': 3, 'partial': True,
        'rejected_items': [{'index': 2, 'reason': 'invalid_question'},
                           {'index': 3, 'reason': 'missing_content'},
                           {'index': 4, 'reason': 'invalid_answer'}]}
    assert 'private' not in str(task['diagnostics'])
    assert task['diagnostics']['pdf_splitting']['calls'] == 1
    assert task['diagnostics']['pdf_splitting']['retries'] == 0
    assert '3 项' in task['log'] and '原 PDF 页面' in task['log'], task['log']
    assert task['source_available'] and task['page_images']
    assert client.get(f'/api/tasks/{task_id}/source').content == raw
    assert len(main.DOCUMENT_TASKS.snapshot(task_id)['pdf_source_pages']) == 1
    with SessionLocal() as db:
        assert db.query(Question).count() == 0
    print('PARTIAL_QUESTION_EVIDENCE=' + json.dumps({'strategy': strategy,
        'accepted': 2, 'rejected': 3, 'split_calls': len(split_calls), 'partial': task['partial']}))
main.DOCUMENT_TASKS.shutdown()
'''


@pytest.mark.parametrize("strategy", ["force_ocr", "layout_aware"])
def test_pdf_upload_retains_valid_cards_and_reports_rejected_entries(tmp_path, strategy):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-partial-question-workspace"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    runner = workspace / "isolated_partial_question.py"
    runner.write_text(SCENARIO, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(workspace), MATHBANK_ISOLATED_PDF_STRATEGY=strategy)
    result = subprocess.run([sys.executable, str(runner)], cwd=workspace, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-7000:] + "\n" + result.stderr[-7000:]
    assert '"accepted": 2' in result.stdout and '"rejected": 3' in result.stdout
    assert '"split_calls": 1' in result.stdout and '"partial": true' in result.stdout
