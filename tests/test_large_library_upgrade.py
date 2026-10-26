"""Portable-source and overwrite-upgrade regressions using synthetic old data.

The application copy is produced by the real release allowlist, without
building an archive or downloading a runtime. Production lifespan validation
runs only on the current host; the Windows case checks its source-copy rules.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from scripts import build_release, release_overlay
from mathbank.db_migrations import LATEST_SCHEMA_VERSION


NEW_RUNTIME_MODULES = (
    "mathbank/question_reads.py",
    "mathbank/background_export.py",
    "mathbank/question_queries.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_child(root, source):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(source)],
        cwd=root, env=environment, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def portable_source_trees(tmp_path_factory):
    root = tmp_path_factory.mktemp("mathbank-portable-source")
    trees = {}
    for platform, launcher in (
        ("macos", "启动题库系统.command"),
        ("windows-x64", "启动题库系统.bat"),
    ):
        tree = root / platform
        build_release.copy_app_files(tree, launcher)
        trees[platform] = tree
    return trees


@pytest.mark.parametrize("platform", ["macos", "windows-x64"])
def test_actual_portable_copy_includes_both_new_runtime_modules(portable_source_trees, platform):
    tree = portable_source_trees[platform]
    for relative in NEW_RUNTIME_MODULES:
        assert (tree / relative).is_file()
        assert digest(tree / relative) == digest(Path(build_release.BASE_DIR) / relative)
    assert not (tree / "scripts/build_release.py").exists()
    assert not (tree / ".env").exists()
    assert not (tree / "math_question_bank.db").exists()
    assert not (tree / "data_backup").exists()
    assert not (tree / "tests").exists()
    assert {p.name for p in (tree / "static/uploads").iterdir()} == {".gitkeep"}
    run_child(tree, """
        from mathbank.question_reads import find_association_candidates, question_summary_load_options
        from mathbank.background_export import DatabaseExportQueue
        from mathbank.question_queries import QuestionReadSnapshot, page_questions
        from mathbank.paths import DATABASE_FILE
        assert callable(find_association_candidates)
        assert callable(question_summary_load_options)
        assert callable(QuestionReadSnapshot) and callable(page_questions)
        assert not DATABASE_FILE.exists()
    """)


SEED_OLD_DATA = r'''
import base64
import json
import sqlite3
from pathlib import Path

from mathbank import database
from mathbank.database import (
    Question, QuestionCurriculum, QuestionKnowledgePoint, KnowledgePoint, SessionLocal,
)
from mathbank.curriculums import build_default_metadata

database.init_db()
image_url = '/static/uploads/old-user-diagram.png'
image = Path('static/uploads/old-user-diagram.png')
image.write_bytes(base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c5c8AAAAASUVORK5CYII='))
metadata = build_default_metadata('A')
metadata['curriculum']['自编教材'] = {'用户历史章节': ['历史自定义小节']}
metadata['upgrade_regression_marker'] = '保留用户自定义配置'
Path('data_backup/custom_metadata.json').write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
Path('.env').write_text('DEEPSEEK_API_KEY=synthetic-upgrade-test-only\nMATHBANK_QA_ENABLED=1\n', encoding='utf-8')

audit = {'scope': ['(1)'], 'evidence_source': '旧题人工核验',
         'evidence': {'excerpt': '保留原证据'}, 'reason': '人工分类依据',
         'origin': {'review': 'old-package-evidence-' + 'x' * 50_000}}
with SessionLocal() as db:
    point = KnowledgePoint(code='local:upgrade-proof', name='升级保真知识点',
        original_name='用户知识点原名', source='local', aliases=['用户旧别名'])
    db.add(point)
    for qid, text in ((2, '旧题甲'), (19, '旧题乙')):
        question = Question(id=qid, content=text + r' $a_n=2^n$ ![原图](' + image_url + ') 然后继续正文。',
            question_type='detailed_answer', difficulty='normal',
            category_compulsory='自编教材', category_chapter='用户历史章节',
            category_knowledge='历史自定义小节', source='旧用户题源', tags='自定义旧标签',
            answer_markdown='原解析 $S_n=2^{n+1}-2$。', review='用户原点评')
        question.image_paths = [image_url]
        question.image_layouts = {image_url: {'align': 'left', 'size': 'small'}}
        mirror = QuestionCurriculum(question=question, version_code='A',
            compulsory=question.category_compulsory, chapter=question.category_chapter,
            knowledge=question.category_knowledge)
        mirror.sub_knowledge = [{'compulsory': '自编教材', 'chapter': '用户历史章节',
                                 'knowledge': '旧多选关系证据'}]
        db.add(question)
        db.flush()
        db.add(QuestionKnowledgePoint(question_id=qid, point_code=point.code,
            role='main', status='confirmed',
            **{key: json.dumps(value, ensure_ascii=False) for key, value in audit.items()}))
    db.commit()
database.engine.dispose()
'''


def logical_old_rows(database_path):
    """Compare original user records independently of newly added schema tables."""
    with sqlite3.connect(database_path) as connection:
        return {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
            for table in (
                "questions", "question_curriculums", "question_sub_knowledge",
                "question_knowledge_points",
            )
        }


@pytest.mark.parametrize("old_schema", [12, 13])
def test_overwrite_upgrade_starts_with_old_data_and_keeps_user_assets(
    tmp_path, portable_source_trees, old_schema,
):
    # A source fixture models the old installation; the schema is explicitly
    # downgraded to the documented v12 layout where its two v13 tables do not
    # yet exist. It is not presented as a downloaded historical release.
    source = portable_source_trees["macos"]
    installed = tmp_path / "旧用户 题库安装目录"
    shutil.copytree(source, installed)
    for relative in NEW_RUNTIME_MODULES:
        (installed / relative).unlink()
    (installed / "main.py").write_text("# Synthetic previous application entrypoint\n")
    (installed / "mathbank/obsolete_upgrade_fixture.py").write_text("# previous owned file\n")
    build_release.write_release_manifest(installed, "macos")
    release_overlay.apply_release_overlay(installed, "macos")

    run_child(installed, SEED_OLD_DATA)
    database_path = installed / "math_question_bank.db"
    with sqlite3.connect(database_path) as connection:
        if old_schema == 12:
            connection.execute("DROP TABLE question_references")
            connection.execute("DROP TABLE question_source_labels")
        connection.execute(f"PRAGMA user_version={old_schema}")
        connection.commit()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    old_rows = logical_old_rows(database_path)
    protected = (
        ".env", "data_backup/custom_metadata.json", "static/uploads/old-user-diagram.png",
    )
    original_hashes = {relative: digest(installed / relative) for relative in protected}
    old_database_hash = digest(database_path)

    # Merge incoming application files just as the overwrite instructions say.
    shutil.copytree(source, installed, dirs_exist_ok=True)
    build_release.write_release_manifest(source, "macos")
    shutil.copy2(source / release_overlay.MANIFEST_NAME, installed / release_overlay.MANIFEST_NAME)
    result = release_overlay.apply_release_overlay(installed, "macos")
    assert result["status"] == "updated"
    assert "mathbank/obsolete_upgrade_fixture.py" in result["deleted"]
    assert digest(database_path) == old_database_hash
    assert {relative: digest(installed / relative) for relative in protected} == original_hashes

    run_child(installed, r'''
        import json
        import threading
        import requests
        from pathlib import Path

        def no_external_requests(*args, **kwargs):
            raise AssertionError('Upgrade regression cannot contact AI providers')
        requests.sessions.Session.request = no_external_requests

        import main
        from fastapi.testclient import TestClient
        from mathbank import __version__
        from mathbank.db_migrations import LATEST_SCHEMA_VERSION
        assert not main.IS_TESTING, 'Exercise the production lifespan and actual metadata path'
        finished = threading.Event()
        cleanup = main.start_startup_cleanup
        def observed_cleanup():
            try:
                cleanup()
            finally:
                finished.set()
        main.start_startup_cleanup = observed_cleanup

        with TestClient(main.app) as client:
            health = client.get('/healthz')
            assert health.status_code == 200, health.text
            assert health.json()['ready'] and health.json()['checks']['schema_version'] == LATEST_SCHEMA_VERSION
            assert client.get('/api/version').json()['current_version'] == __version__
            detail = client.get('/api/questions/19').json()
            assert detail['content'].startswith('旧题乙') and detail['seq_num'] == 2
            assert detail['category_knowledge'] == '历史自定义小节'
            assert detail['tags'] == '自定义旧标签'
            assert detail['answer_markdown'] == '原解析 $S_n=2^{n+1}-2$。'
            assert detail['review'] == '用户原点评'
            assert detail['knowledge_points'][0]['evidence'] == {'excerpt': '保留原证据'}
            assert detail['knowledge_points'][0]['origin']['review'].startswith('old-package-evidence-')
            assert detail['image_layouts']['old-user-diagram.png'] == {'align': 'left', 'size': 'small'}
            image = client.get('/static/uploads/old-user-diagram.png')
            assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
            page = client.get('/api/questions', params={'page': 1}).json()
            assert page['total'] == 2 and len(page['items']) == 2
            assert set(page['items'][0]['knowledge_points'][0]) == {'code', 'name', 'role', 'status'}
            candidates = client.get('/api/questions/association-candidates', params={'seq_num': 2}).json()
            assert len(candidates['items']) == 1 and candidates['items'][0]['id'] == 19
            legacy = client.get('/api/questions').json()
            assert isinstance(legacy, list) and len(legacy) == 2
            assert legacy[0]['knowledge_points'] == detail['knowledge_points']
            metadata = client.get('/api/config/metadata').json()
            assert metadata['upgrade_regression_marker'] == '保留用户自定义配置'
            assert metadata['curriculum']['自编教材']['用户历史章节'] == ['历史自定义小节']
            assert finished.wait(20), 'Production post-startup maintenance did not finish'
            assert client.get('/healthz').json()['ready']
            assert client.get('/api/questions/19').json()['knowledge_points'] == detail['knowledge_points']
    ''')
    assert logical_old_rows(database_path) == old_rows
    assert {relative: digest(installed / relative) for relative in protected} == original_hashes
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    snapshots = list((installed / "data_backup/schema_snapshots").glob(
        f"*.schema-v{old_schema}-to-v{LATEST_SCHEMA_VERSION}.*.db"))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == old_schema
    assert logical_old_rows(snapshots[0]) == old_rows
