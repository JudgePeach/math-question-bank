"""Exercise the actual CLI in disposable source copies, never the live library."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path):
    for directory in ("scripts", "mathbank"):
        shutil.copytree(
            ROOT / directory, tmp_path / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", ".env", "resources" if directory == "scripts" else "never-match"),
        )
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)

    def python(source):
        result = subprocess.run(
            [sys.executable, "-B", "-c", textwrap.dedent(source)],
            cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    python(r'''
        import base64, json
        from pathlib import Path
        from mathbank.database import Base, engine, SessionLocal, Question, KnowledgePoint, QuestionKnowledgePoint
        Base.metadata.create_all(engine)
        db = SessionLocal()
        point = KnowledgePoint(code='1136', name='判断或证明函数的单调性', original_name='单调性证明', source='custom')
        point.aliases = ['递增函数证明']
        db.add(point)
        first = Question(id=1, content='完整题干 $f(x)=x^2$，证明给定区间的性质。',
            category_compulsory='必修一', category_chapter='第三章', category_knowledge='3.2',
            question_type='detailed_answer', difficulty='normal', source='甲讲义｜教师版',
            answer_markdown='独立解析甲', review='原书错印说明甲', association_group_id='linked',
            tags='自定义标签甲')
        first.image_paths = ['/static/uploads/standalone.png']
        first.answer_tikz_assets = [{'image_path':'/static/uploads/teacher-only.png', 'tikz_code':'draw(0,0)--(1,1);'}]
        second = Question(id=2, content='完整题干乙 ' + '条件不应截断。' * 40 + '平行四边形▱',
            question_type='detailed_answer', difficulty='challenge', source='乙讲义',
            answer_markdown='独立解析乙', review='评析乙', association_group_id='linked')
        db.add_all([first, second]); db.flush()
        db.add(QuestionKnowledgePoint(question_id=1, point_code='1136', role='main',
            status='confirmed', evidence=json.dumps({'excerpt':'给定区间'}),
            origin=json.dumps({'trace':'审计依据仅按需'})))
        db.commit(); db.close()
        path = Path('static/uploads'); path.mkdir(parents=True)
        (path / 'standalone.png').write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ZkAAAAASUVORK5CYII='))
    ''')

    def run(*arguments, expected=0):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.search_questions", *arguments],
            cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=30,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        return result

    run.python = python
    run.root = tmp_path
    return run


def test_legacy_markdown_keeps_full_stem_and_answer_opt_in(cli):
    output = cli("-q", "完整题干乙", "-n", "1").stdout
    assert "条件不应截断。" * 40 in output
    assert "独立解析乙" not in output and "评析乙" not in output
    output = cli("-d", "normal", "-a").stdout
    assert "独立解析甲" in output and "原书错印说明甲" in output
    assert "常规题" in output and "ID: #1" in output
    assert "teacher-only.png" not in cli("-d", "normal").stdout
    assert "teacher-only.png" in output


def test_json_candidates_and_total_do_not_dump_teacher_content(cli):
    result = json.loads(cli("--format", "json", "-n", "1").stdout)
    assert result["total"] == 2 and len(result["items"]) == 1
    assert result["next_page"] == 2
    candidate = result["items"][0]
    assert "content_preview" in candidate
    assert not {"content", "answer_markdown", "review"}.intersection(candidate)
    markdown = cli("-n", "1").stdout
    assert "总命中 2，本次返回 1" in markdown


def test_knowledge_alias_retrieval_catalog_and_source_filters(cli):
    result = json.loads(cli("--knowledge-query", "递增函数证明", "--source", "甲讲义", "--format", "json").stdout)
    assert result["total"] == 1 and result["items"][0]["id"] == 1
    result = json.loads(cli("--knowledge-point", "1136", "--format", "json").stdout)
    assert [item["id"] for item in result["items"]] == [1]
    catalog = json.loads(cli("--catalog", "--knowledge-query", "递增函数证明", "--format", "json").stdout)
    assert catalog["items"][0]["code"] == "1136"


def test_selected_teaching_detail_keeps_review_and_reports_missing(cli):
    result = json.loads(cli("--ids", "1,999", "--format", "json").stdout)
    assert result["missing_ids"] == [999]
    detail = result["items"][0]
    assert detail["content"].startswith("完整题干")
    assert detail["answer_markdown"] == "独立解析甲"
    assert detail["review"] == "原书错印说明甲"
    assert "origin" not in detail["knowledge_points"][0]
    evidence = json.loads(cli("--ids", "1", "--profile", "evidence", "--format", "json").stdout)
    assert evidence["items"][0]["knowledge_points"][0]["origin"] == {"trace": "审计依据仅按需"}
    markdown = cli("--ids", "1").stdout
    assert "原书错印说明甲" in markdown


def test_assets_include_independent_figure_and_local_readable_path(cli):
    result = json.loads(cli("--assets", "--ids", "1", "--format", "json").stdout)
    asset = next(asset for asset in result["items"][0]["assets"]
                 if str(asset.get("url", "")).endswith("standalone.png"))
    assert asset["exists"] is True
    assert Path(asset["local_path"]).is_file()
    assert "standalone.png" in cli("--assets", "--ids", "1").stdout


@pytest.mark.parametrize("arguments", [
    ("--ids", "1,bad"), ("--ids", "0"), ("--assets",),
    ("--page", "0"), ("--ids", "1", "--source", "甲"),
    ("--profile", "candidate", "-a"),
    ("--page", "bad"), ("--profile", "bad"),
    ("--ids", "1", "--page", "2"),
    ("--assets", "--ids", "1", "--source", "甲"),
    ("--capabilities", "-q", "甲"),
    ("--catalog", "--source", "甲"),
])
def test_json_validation_is_machine_readable(cli, arguments):
    result = cli(*arguments, "--format", "json", expected=2)
    error = json.loads(result.stdout)
    assert error["status"] == "error" and error["error"]["message"]
    assert not result.stderr


def test_legacy_python_tuple_group_and_unlimited_are_preserved(cli):
    result = json.loads(cli.python('''
        import json
        from scripts.search_questions import search_questions, get_association_group
        rows = search_questions(limit=-1)
        assert len(rows) == 2 and all(isinstance(row, tuple) and len(row) == 12 for row in rows)
        assert rows[0][8] == '独立解析乙'
        assert search_questions(limit=0) == []
        assert get_association_group(1) == 'linked'
        assert get_association_group(999) is None
        related = search_questions(associated_to_id=1)
        assert [row[0] for row in related] == [2]
        print(json.dumps({'count':len(rows)}))
    '''))
    assert result["count"] == 2
    result = json.loads(cli("-n", "-1", "--format", "json").stdout)
    assert result["total"] == len(result["items"]) == 2


def test_cli_never_mutates_database_and_empty_search_is_success(cli):
    database = cli.root / "math_question_bank.db"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    for arguments in (("--capabilities",), ("--catalog",), ("--ids", "1"),
                      ("--assets", "--ids", "1"), ("-q", "不存在内容")):
        result = json.loads(cli(*arguments, "--format", "json").stdout)
    assert result["total"] == 0 and result["items"] == []
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_large_legacy_limits_and_pages_preserve_order(cli):
    cli.python('''
        from mathbank.database import Question, SessionLocal
        db = SessionLocal()
        db.add_all([Question(id=i, content='分页题'+str(i)) for i in range(3, 244)])
        db.commit(); db.close()
    ''')
    result = json.loads(cli("-n", "120", "--page", "2", "--format", "json").stdout)
    assert result["total"] == 243 and result["page"] == 2
    assert [item["id"] for item in result["items"]] == list(range(123, 3, -1))
    result = json.loads(cli("-n", "-1", "--format", "json").stdout)
    assert [item["id"] for item in result["items"]] == list(range(243, 0, -1))


def test_redirected_cp936_environment_still_emits_lossless_utf8(cli):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="cp936")
    environment.pop("PYTHONPATH", None)
    for arguments in (("--ids", "2", "--format", "json"), ("--ids", "2")):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.search_questions", *arguments],
            cwd=cli.root, env=environment, capture_output=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8")
        output = result.stdout.decode("utf-8")
        assert "平行四边形▱" in output and "�" not in output
        if arguments[-1] == "json":
            assert "▱" in json.loads(output)["items"][0]["content"]
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.search_questions", "--page", "bad", "--format", "json"],
        cwd=cli.root, env=environment, capture_output=True, timeout=30,
    )
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout.decode("utf-8"))["status"] == "error"
