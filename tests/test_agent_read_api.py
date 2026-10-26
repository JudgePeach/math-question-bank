"""Real HTTP contracts in a source-only copy, with disposable data and assets."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r'''
import hashlib
import json
import sqlite3
import pytest
import requests

def no_network(*args, **kwargs):
    raise AssertionError('Agent read tests must not call external services')
requests.sessions.Session.request = no_network

import main
from fastapi.testclient import TestClient
from mathbank.database import Question, QuestionKnowledgePoint, SessionLocal
from PIL import Image

client = TestClient(main.app)
db = SessionLocal()
question = Question(id=7, content='已知 $x_1=1$，求证此函数满足条件。',
                    question_type='detailed_answer', difficulty='normal',
                    source='教师讲义 100%_', answer_markdown='完整解析',
                    review='【原资料核对说明】保留原书错印，请独立复核。')
db.add(question)
db.commit()
db.add(QuestionKnowledgePoint(question_id=7, point_code='1136', role='main',
    status='confirmed', evidence=json.dumps({'excerpt':'原依据'}),
    origin=json.dumps({'large_trace':'x' * 100_000})))
db.commit()

def get(path, **params):
    result = client.get(path, params=params)
    assert result.status_code == 200, result.text
    return result.json()

def fingerprint():
    with sqlite3.connect(main.DATABASE_PATH) as connection:
        return hashlib.sha256('\n'.join(connection.iterdump()).encode()).hexdigest()
'''


@pytest.fixture
def isolated_app(tmp_path):
    shutil.copy2(ROOT / "main.py", tmp_path / "main.py")
    (tmp_path / "scripts").mkdir()
    for filename in ("__init__.py", "search_questions.py"):
        shutil.copy2(ROOT / "scripts" / filename, tmp_path / "scripts" / filename)
    for directory in ("mathbank", "templates", "static"):
        shutil.copytree(
            ROOT / directory, tmp_path / directory,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "uploads", "test_uploads",
                "*.db", "*.db-*", ".env", ".system_generated",
            ),
        )
    assert not (tmp_path / "math_question_bank.db").exists()
    assert not (tmp_path / ".env").exists()
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)

    def run(source):
        result = subprocess.run(
            [sys.executable, "-B", "-c", PRELUDE + "\n" + textwrap.dedent(source)],
            cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return run


def test_http_and_cli_share_one_read_only_teaching_workflow(isolated_app):
    isolated_app(r'''
        import subprocess, sys
        from pathlib import Path
        from mathbank.paths import DATA_BACKUP_DIR, UPLOADS_DIR

        # The test app normally uses test_uploads/custom_metadata_test.json;
        # point both adapters at the same disposable production-shaped paths.
        metadata = Path(main.METADATA_FILE).read_bytes()
        main.METADATA_FILE = str(DATA_BACKUP_DIR / 'custom_metadata.json')
        Path(main.METADATA_FILE).write_bytes(metadata)
        main.UPLOAD_DIR = str(UPLOADS_DIR)
        main.UPLOAD_DIR_REL = 'static/uploads'
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

        image = Path(main.UPLOAD_DIR) / 'workflow.png'
        Image.new('RGB', (24, 16), 'white').save(image)
        question.content += ' ![](/static/uploads/workflow.png)'
        db.commit()
        before = fingerprint()

        def cli(*arguments):
            result = subprocess.run([sys.executable, '-B', '-m', 'scripts.search_questions',
                *arguments, '--format', 'json'], text=True, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            return json.loads(result.stdout)

        def public(value):
            if isinstance(value, dict):
                return {key: public(item) for key, item in value.items() if key != 'local_path'}
            if isinstance(value, list):
                return [public(item) for item in value]
            return value

        assert cli('--catalog', '--knowledge-query', '单调性') == get(
            '/api/agent/knowledge-points', q='单调性')
        assert cli('--knowledge-point', '1136') == get(
            '/api/agent/questions', knowledge_point_codes=['1136'])
        detail = cli('--ids', '7,999', '--profile', 'teaching')
        assert detail['missing_ids'] == [999]
        assert detail['items'][0]['content'] == question.content
        assert detail['items'][0]['answer_markdown'] == question.answer_markdown
        assert detail['items'][0]['review'] == question.review
        assert public(detail) == get('/api/agent/questions/batch', ids='7,999', profile='teaching')
        assets = cli('--assets', '--ids', '7,999')
        assert assets['missing_ids'] == [999]
        assert Path(assets['items'][0]['assets'][0]['local_path']).resolve() == image.resolve()
        assert public(assets) == get('/api/agent/assets', ids='7,999')
        assert fingerprint() == before
    ''')


def test_agent_profiles_and_knowledge_search_preserve_legacy_and_data(isolated_app):
    isolated_app(r'''
        old_detail = get('/api/questions/7')
        old_list = get('/api/questions', page=1)
        before = fingerprint()
        capabilities = get('/api/agent/capabilities')
        assert 'normal' in json.dumps(capabilities)
        catalog = get('/api/agent/knowledge-points', q='单调性')
        assert any(point['code'] == '1136' for point in catalog['items'])
        search = get('/api/agent/questions', knowledge_query='单调性', source='100%_')
        assert search['total'] == 1
        item = search['items'][0]
        assert item['id'] == 7
        assert 'answer_markdown' not in item and 'review' not in item
        assert 'large_trace' not in json.dumps(item)
        detail = get('/api/agent/questions/7')
        assert detail['content'] == question.content
        assert detail['answer_markdown'] == '完整解析'
        assert detail['review'] == question.review
        assert 'large_trace' not in json.dumps(detail)
        selected = get('/api/agent/questions', profile='teaching')['items'][0]
        assert selected['answer_markdown'] == '完整解析'
        without_answer = get('/api/agent/questions/7', include_answers=False)
        assert 'answer_markdown' not in without_answer and without_answer['review'] == question.review
        evidence = get('/api/agent/questions/7', profile='evidence')
        assert 'large_trace' in json.dumps(evidence)
        assert get('/api/questions/7') == old_detail
        assert get('/api/questions', page=1) == old_list
        assert fingerprint() == before
    ''')


def test_agent_missing_ids_validation_and_bounded_requests(isolated_app):
    isolated_app(r'''
        before = fingerprint()
        batch = get('/api/agent/questions/batch', ids='7,99999')
        assert [item['id'] for item in batch['items']] == [7]
        assert batch['missing_ids'] == [99999]
        assert client.get('/api/agent/questions/99999').status_code == 404
        assert client.get('/api/agent/questions/batch', params={'ids':'7,bad'}).status_code == 400
        assert client.get('/api/agent/questions/batch', params={'ids':'7,,8'}).status_code == 400
        assert client.get('/api/agent/questions/batch', params={'ids':'9'*5000}).status_code == 400
        assert client.get('/api/agent/questions', params={'knowledge_point_codes':''}).status_code == 400
        assert client.get('/api/agent/questions', params={'page_size':101}).status_code == 422
        assert client.get('/api/agent/questions/7', params={'profile':'unknown'}).status_code == 422
        assert client.get('/api/agent/knowledge-points', params={'limit':101}).status_code == 422
        assert get('/api/agent/questions', q='%_')['total'] == 1
        assert get('/api/agent/questions', q='%_missing')['total'] == 0
        assert fingerprint() == before
    ''')


def test_detail_version_parameter_reads_mirror_without_switching_global_version(isolated_app):
    isolated_app(r'''
        from mathbank.database import QuestionCurriculum
        db.add(QuestionCurriculum(question_id=7, version_code='B', compulsory='B册',
                                  chapter='B章', knowledge='B节'))
        db.commit()
        before = fingerprint()
        active = main.get_active_version_code()
        single = get('/api/agent/questions/7', version_code='B')
        batch = get('/api/agent/questions/batch', ids='7', version_code='B')
        assert single['category_chapter'] == batch['items'][0]['category_chapter'] == 'B章'
        assert single['version_code'] == batch['version_code'] == 'B'
        assert main.get_active_version_code() == active
        assert fingerprint() == before
    ''')


def test_agent_assets_expose_safe_urls_without_local_paths(isolated_app):
    isolated_app(r'''
        from pathlib import Path
        root = Path(main.UPLOAD_DIR)
        root.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (24, 16), 'white').save(root / 'agent-body.png')
        question.content += '\n![图](/static/test_uploads/agent-body.png)'
        question.review += '\n![缺图](/static/test_uploads/agent-missing.png)'
        question.image_paths = ['/static/test_uploads/agent-body.png',
                                '/static/test_uploads/agent-missing.png']
        db.commit()
        before = fingerprint()
        result = get('/api/agent/assets', ids='7,99999')
        assert result['missing_ids'] == [99999]
        serialized = json.dumps(result)
        assert 'agent-body.png' in serialized and 'agent-missing.png' in serialized
        assert str(Path.cwd()) not in serialized and 'local_path' not in serialized
        assert 'review' in serialized
        assert get('/api/agent/questions/7/assets')['missing_ids'] == []
        assert fingerprint() == before
    ''')


def test_unavailable_database_has_safe_error_and_is_not_created(isolated_app):
    isolated_app(r'''
        from pathlib import Path
        absent = Path.cwd() / 'private-unavailable' / 'database.db'
        main.DATABASE_PATH = str(absent)
        response = client.get('/api/agent/questions')
        assert response.status_code == 503
        assert str(Path.cwd()) not in response.text
        assert not absent.exists() and not absent.parent.exists()
    ''')
