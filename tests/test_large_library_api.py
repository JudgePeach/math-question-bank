"""Large-library read contracts in source-only disposable app subprocesses.

Run with ``python3 -m pytest --noconftest tests/test_large_library_api.py``.
No application/database imports occur in the pytest process; each test gets
its own file SQLite, and neither live data nor credentials are copied.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRELUDE = """
import json
import pytest
import requests
from sqlalchemy import event

def no_network(*args, **kwargs):
    raise AssertionError('Isolated API regression cannot call external services')
requests.sessions.Session.request = no_network

import main
from fastapi.testclient import TestClient
from mathbank.database import (
    Question, QuestionCurriculum, QuestionKnowledgePoint, SessionLocal, engine,
)

client = TestClient(main.app)
client.headers.update({'X-Local-Token': main.LOCAL_TOKEN})
# Exercise actual save paths without invoking the unrelated full-file export.
main.schedule_database_export = lambda *args, **kwargs: None
db = SessionLocal()
statements = []
event.listen(engine, 'before_cursor_execute',
             lambda conn, cursor, statement, params, context, many: statements.append(statement))

def get(path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()

def seed_question(qid, **extra):
    question = Question(id=qid, content='候选题 ' + str(qid),
                        question_type='detailed_answer', **extra)
    db.add(question)
    db.commit()
    return question

AUDIT = {'scope': ['(1)'], 'evidence_source': '题干',
         'evidence': {'excerpt': '巨型证据'}, 'reason': '原审核说明',
         'origin': {'trace': 'large-library-private-evidence-' + 'x' * 1_000_000}}

def attach_audit(question):
    db.add(QuestionKnowledgePoint(question_id=question.id, point_code='1015',
        role='main', status='confirmed',
        **{key: json.dumps(value, ensure_ascii=False) for key, value in AUDIT.items()}))
    db.commit()
"""


@pytest.fixture
def isolated_app(tmp_path):
    shutil.copy2(ROOT / "main.py", tmp_path / "main.py")
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


def test_paginated_labels_avoid_audit_sql_and_detail_stays_lossless(isolated_app):
    isolated_app(r'''
        question = seed_question(7, answer_markdown='原解析', review='原评析')
        attach_audit(question)
        statements.clear()
        page = get('/api/questions', page=1)
        point = page['items'][0]['knowledge_points'][0]
        assert set(point) == {'code', 'name', 'role', 'status'}
        assert point['code'] == '1015' and point['name'] == '集合的含义'
        assert len(json.dumps(page)) < 6000
        selects = [sql.lower() for sql in statements if sql.lstrip().lower().startswith('select')]
        assert selects
        assert not any('question_knowledge_points.' + field in sql
                       for sql in selects for field in AUDIT), selects
        assert not any('knowledge_point_curriculums' in sql for sql in selects), selects
        detail = get('/api/questions/7')
        for key, value in AUDIT.items():
            assert detail['knowledge_points'][0][key] == value
        legacy = get('/api/questions')
        assert legacy[0]['knowledge_points'] == detail['knowledge_points']
        response = client.put('/api/questions/7', data={
            'content': '仅修改正文', 'question_type': 'detailed_answer',
            'difficulty': 'normal', 'answer_markdown': '原解析', 'review': '原评析',
        })
        assert response.status_code == 200, response.text
        assert get('/api/questions/7')['knowledge_points'] == detail['knowledge_points']
    ''')


def test_summary_keeps_image_visibility_and_per_image_layout(isolated_app):
    isolated_app(r'''
        question = seed_question(5, answer_markdown='原解析',
                                 review='仅评析图 ![核验](/static/uploads/review.png)',
                                 tikz_reference_image_path='/static/uploads/original.png')
        question.image_paths = ['/static/uploads/body.png', '/static/uploads/review.png',
                                '/static/uploads/original.png', '/static/uploads/answer-ref.png']
        question.answer_tikz_assets = [{
            'image_path': '/static/uploads/answer.png',
            'reference_image_path': '/static/uploads/answer-ref.png',
            'tikz_code': r'draw(0,0)--(1,1);',
        }]
        question.content = '正文 ![配图](/static/uploads/body.png) 然后继续正文'
        question.image_layouts = {'/static/uploads/body.png': {'align': 'left', 'size': 'small'}}
        db.commit()
        listed = get('/api/questions', page=1)['items'][0]
        detail = get('/api/questions/5')
        assert listed['image_paths'] == detail['image_paths'] == ['/static/uploads/body.png']
        assert listed['image_layouts'] == detail['image_layouts']
        assert listed['has_answer'] is True
        assert 'answer_markdown' not in listed and 'review' not in listed
    ''')


def test_candidates_bound_page_and_project_only_label_columns(isolated_app):
    isolated_app(r'''
        for qid in range(1, 26):
            question = seed_question(qid)
        question.content = '短题干开始 ' + '长正文' * 5000
        question.answer_markdown = 'private-answer-' + 'y' * 100_000
        db.commit()
        attach_audit(question)
        statements.clear()
        first = get('/api/questions/association-candidates', page_size=999)
        assert first['total'] == 25 and first['total_pages'] == 2
        assert first['page_size'] == 20 and len(first['items']) == 20
        assert first['items'][0]['id'] == 25
        assert len(first['items'][0]['content']) <= 180
        assert len(json.dumps(first)) < 6000
        for item in first['items']:
            assert set(item) == {'id', 'seq_num', 'question_type', 'content'}
            assert item['seq_num'] == item['id']
        selects = [sql.lower() for sql in statements if sql.lstrip().lower().startswith('select')]
        assert not any('knowledge_point' in sql for sql in selects), selects
        assert not any('questions.answer_markdown' in sql or 'questions.review' in sql
                       or 'questions.tikz_code' in sql for sql in selects), selects
        assert any('substr(questions.content' in sql for sql in selects), selects
        second = get('/api/questions/association-candidates', page=2)
        assert len(second['items']) == 5
        assert {item['id'] for item in first['items']}.isdisjoint(
            {item['id'] for item in second['items']})
    ''')


def test_candidates_exact_numbers_literal_search_and_exclusion(isolated_app):
    isolated_app(r'''
        first = seed_question(2, source='特殊来源 100%_')
        gap = seed_question(7)
        last = seed_question(31)
        first.content = '含字面 %_ 符号的题'
        db.delete(gap)
        db.commit()
        assert get('/api/questions/association-candidates', seq_num=2)['items'][0]['id'] == 31
        assert get('/api/questions/association-candidates', question_id=2)['items'][0]['seq_num'] == 1
        assert get('/api/questions/association-candidates', question_id=7)['items'] == []
        assert get('/api/questions/association-candidates', seq_num=7)['total'] == 0
        assert get('/api/questions/association-candidates', seq_num=1, exclude_id=2)['items'] == []
        assert get('/api/questions/association-candidates', seq_num=1, question_id=31)['items'] == []
        assert get('/api/questions/association-candidates', q='%_')['total'] == 1
        assert get('/api/questions/association-candidates', q='100%_')['items'][0]['id'] == 2
        assert get('/api/questions/association-candidates', q='%_', exclude_id=2)['total'] == 0
        assert get('/api/questions/association-candidates', q='找不到的关键字')['items'] == []
        assert client.get('/api/questions/association-candidates', params={'seq_num': 0}).status_code == 422
    ''')


def test_associated_reads_keep_labels_without_audit_evidence(isolated_app):
    isolated_app(r'''
        first = seed_question(3, association_group_id='regression-group')
        second = seed_question(11, association_group_id='regression-group')
        attach_audit(second)
        statements.clear()
        associated = get('/api/questions/3/associated')
        assert len(associated) == 1 and associated[0]['id'] == 11
        assert associated[0]['seq_num'] == 2
        assert set(associated[0]['knowledge_points'][0]) == {'code', 'name', 'role', 'status'}
        assert len(json.dumps(associated)) < 6000
        assert not any('question_knowledge_points.' + field in sql.lower()
                       for sql in statements for field in AUDIT), statements
        assert get('/api/questions/11')['knowledge_points'][0]['origin'] == AUDIT['origin']
    ''')
