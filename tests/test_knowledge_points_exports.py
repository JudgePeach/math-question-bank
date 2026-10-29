"""Point metadata exchanges and full backups use only isolated test databases."""

import json
import re
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from mathbank import sync_helper
from mathbank.backup import create_full_backup, restore_full_backup, verify_full_backup
from mathbank.database import (
    Base, KnowledgePoint, KnowledgePointCurriculum, Question,
    QuestionCurriculum, QuestionKnowledgePoint, QuestionSubKnowledge,
)


@pytest.fixture
def knowledge_session(tmp_path):
    path = tmp_path / "math_question_bank.db"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=12")
    with Session(engine) as session:
        yield session, path
    engine.dispose()


def _seed_points(session, question_count=1):
    points = [
        KnowledgePoint(code="10001", name="等比数列的通项公式", original_name="等比数列的通项公式", source="zhixue", aliases=["等比通项"]),
        KnowledgePoint(code="local:sequence-proof", name="自定义数列证明方法", original_name="自定义数列证明方法", source="local", aliases=["我的证明方法"]),
        KnowledgePoint(code="10003", name="尚待确认的导数应用", original_name="尚待确认的导数应用", source="zhixue", aliases=[]),
    ]
    session.add_all(points)
    for version in ("A", "B"):
        points[0].curriculum_paths.append(KnowledgePointCurriculum(
            version_code=version, compulsory="选择性必修第二册", chapter="第四章 数列",
            knowledge="4.3 等比数列", reference_section="等比数列的概念", level="leaf",
        ))
    for index in range(question_count):
        question = Question(
            content=f"第{index + 1}道数列题：已知 $a_n=2^n$。", question_type="detailed_answer",
            category_compulsory="选择性必修第二册", category_chapter="第四章 数列",
            category_knowledge="历史自定义小节", tags="高一期中,待讲", answer_markdown="秘密答案：32",
        )
        mapping = QuestionCurriculum(
            version_code="A", compulsory=question.category_compulsory,
            chapter=question.category_chapter, knowledge=question.category_knowledge,
        )
        mapping.sub_knowledge_rows.append(QuestionSubKnowledge(
            compulsory=question.category_compulsory, chapter=question.category_chapter,
            knowledge="历史关系证据", position=0,
        ))
        question.curriculum_mappings.append(mapping)
        for position, (point, role, status) in enumerate(zip(
            points, ("main", "auxiliary", "main"), ("confirmed", "confirmed", "suggested"),
        )):
            question.knowledge_points.append(QuestionKnowledgePoint(
                point=point, role=role, status=status, position=position,
                scope=json.dumps(["(1)", "(2)"]), evidence_source=json.dumps("solution", ensure_ascii=False),
                evidence=json.dumps({"quote": "秘密答案：32", "line": position}, ensure_ascii=False),
                reason=json.dumps("原文依据", ensure_ascii=False),
                origin=json.dumps({"proposal": "research-20261012", "model": "人工审查"}, ensure_ascii=False),
            ))
        session.add(question)
    session.commit()
    session.expunge_all()


def test_json_exchange_preserves_point_catalog_and_all_relation_metadata(knowledge_session, tmp_path, monkeypatch):
    session, _ = knowledge_session
    _seed_points(session)
    monkeypatch.setattr(sync_helper, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(tmp_path / "questions_backup.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(tmp_path / "questions_library.md"))
    monkeypatch.setattr(sync_helper, "resolve_active_version", lambda: "A")
    sync_helper.export_database_to_files(session, quiet=True)
    text = (tmp_path / "questions_backup.json").read_text(encoding="utf-8")
    payload = json.loads(text)[0]
    assert "等比数列" in text and "\\u7b49" not in text
    assert payload["category_knowledge"] == "历史自定义小节"
    assert payload["tags"] == "高一期中,待讲"
    assert payload["curriculum_mappings"][0]["sub_knowledge"][0]["knowledge"] == "历史关系证据"
    confirmed, custom, suggested = payload["knowledge_points"]
    assert confirmed["code"] == "10001" and confirmed["aliases"] == ["等比通项"]
    assert {path["version_code"] for path in confirmed["curriculum_paths"]} == {"A", "B"}
    assert custom["source"] == "local" and custom["original_name"] == "自定义数列证明方法"
    assert custom["role"] == "auxiliary" and custom["position"] == 1
    assert suggested["status"] == "suggested"
    assert suggested["scope"] == ["(1)", "(2)"]
    assert suggested["evidence"] == {"quote": "秘密答案：32", "line": 2}
    assert suggested["origin"] == {"proposal": "research-20261012", "model": "人工审查"}
    markdown = (tmp_path / "questions_library.md").read_text(encoding="utf-8")
    assert "**主要考查知识点**：等比数列的通项公式 (`10001`)" in markdown
    assert "**辅助涉及知识点**：自定义数列证明方法 (`local:sequence-proof`)" in markdown
    assert "尚待确认的导数应用 (`10003`)" in markdown
    assert "秘密答案" not in markdown
    assert "历史自定义小节" in markdown and "高一期中,待讲" in markdown


@pytest.mark.parametrize("active_version", ("A", "B", "S", "H", "N", "X"))
def test_builtin_versions_json_export_preserves_mirrors_points_and_evidence(knowledge_session, tmp_path, monkeypatch, active_version):
    session, _ = knowledge_session
    _seed_points(session)
    question = session.query(Question).one()
    point = session.get(KnowledgePoint, "10001")
    for version in ("B", "S", "H", "N", "X"):
        # Legacy/custom labels remain portable even when presets later change.
        mapping = QuestionCurriculum(question=question, version_code=version,
            compulsory=f"{version}历史册", chapter=f"{version}历史章", knowledge=f"{version}历史小节")
        mapping.sub_knowledge = [{"compulsory": f"{version}历史册", "chapter": f"{version}历史章", "knowledge": f"{version}历史关系"}]
        session.add(mapping)
        if version != "B":
            point.curriculum_paths.append(KnowledgePointCurriculum(version_code=version,
                compulsory=f"{version}历史册", chapter=f"{version}历史章", knowledge=f"{version}历史小节",
                reference_section=f"{version}来源末节", level="leaf"))
    active = next(mapping for mapping in question.curriculum_mappings if mapping.version_code == active_version)
    for field in ("compulsory", "chapter", "knowledge"):
        setattr(question, "category_" + field, getattr(active, field))
    session.commit()
    tables = ("questions", "question_curriculums", "question_sub_knowledge", "knowledge_points",
              "knowledge_point_curriculums", "question_knowledge_points")
    before = {table: session.connection().exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1').all() for table in tables}
    monkeypatch.setattr(sync_helper, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(tmp_path / "questions_backup.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(tmp_path / "questions_library.md"))
    monkeypatch.setattr(sync_helper, "resolve_active_version", lambda: active_version)
    sync_helper.export_database_to_files(session, quiet=True)
    payload = json.loads((tmp_path / "questions_backup.json").read_text(encoding="utf-8"))[0]
    assert payload["active_version_code"] == active_version
    assert {mapping["version_code"] for mapping in payload["curriculum_mappings"]} == {"A", "B", "S", "H", "N", "X"}
    assert payload["sub_knowledge"] == active.sub_knowledge
    assert payload["category_knowledge"] == active.knowledge and payload["tags"] == "高一期中,待讲"
    exported_point = payload["knowledge_points"][0]
    assert {path["version_code"] for path in exported_point["curriculum_paths"]} == {"A", "B", "S", "H", "N", "X"}
    assert exported_point["name"] == exported_point["original_name"] == "等比数列的通项公式"
    assert exported_point["aliases"] == ["等比通项"]
    assert exported_point["evidence"] == {"quote": "秘密答案：32", "line": 0}
    assert {table: session.connection().exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1').all() for table in tables} == before


def test_exchange_queries_are_bounded_as_question_count_grows(knowledge_session):
    session, _ = knowledge_session
    _seed_points(session, question_count=20)
    counts = []
    for limit in (1, 20):
        session.expunge_all()
        selects = []

        def track(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(session.bind, "before_cursor_execute", track)
        try:
            questions = session.query(Question).order_by(Question.id).limit(limit).all()
            payload, _ = sync_helper.serialize_exchange_questions(session, questions, "A")
            assert len(payload) == limit
            assert all(len(row["knowledge_points"]) == 3 for row in payload)
            counts.append(len(selects))
        finally:
            event.remove(session.bind, "before_cursor_execute", track)
    assert counts[1] <= counts[0] + 2
    assert counts[1] <= 10


def _backup_test_database(tmp_path, database):
    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    return create_full_backup(
        output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "custom_metadata.json", retention=None,
    )


def test_schema12_full_backup_restore_keeps_catalog_paths_and_evidence(knowledge_session, tmp_path):
    session, database = knowledge_session
    _seed_points(session)
    archive = _backup_test_database(tmp_path, database)
    manifest = verify_full_backup(archive)
    assert manifest["database"]["row_counts"]["knowledge_points"] == 3
    assert manifest["database"]["row_counts"]["knowledge_point_curriculums"] == 2
    assert manifest["database"]["row_counts"]["question_knowledge_points"] == 3
    session.close()
    with closing(sqlite3.connect(database)) as connection:
        expected = {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall() for table in (
            "knowledge_points", "knowledge_point_curriculums", "question_knowledge_points", "question_sub_knowledge",
        )}
        connection.execute("DELETE FROM question_knowledge_points")
        connection.commit()
    # Session.close returns connections to the pool; Windows needs handles closed.
    session.get_bind().dispose()
    safety = restore_full_backup(
        archive, database_path=database, uploads_dir=tmp_path / "uploads",
        metadata_path=tmp_path / "custom_metadata.json", safety_backup_dir=tmp_path / "safety",
        runtime_lock_path=tmp_path / "runtime.lock",
    )
    assert verify_full_backup(safety)["database"]["row_counts"]["question_knowledge_points"] == 0
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table, rows in expected.items():
            assert connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall() == rows


@pytest.mark.parametrize("table", ("knowledge_points", "knowledge_point_curriculums", "question_knowledge_points"))
def test_schema12_backup_rejects_missing_point_tables(knowledge_session, tmp_path, table):
    session, database = knowledge_session
    session.close()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(f'DROP TABLE "{table}"')
    with pytest.raises(RuntimeError, match=table):
        _backup_test_database(tmp_path, database)


def test_schema12_backup_rejects_dangling_point_relation(knowledge_session, tmp_path):
    session, database = knowledge_session
    _seed_points(session)
    session.close()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE question_knowledge_points SET point_code='missing' WHERE id=1")
        connection.commit()
    with pytest.raises(RuntimeError, match="外键异常"):
        _backup_test_database(tmp_path, database)


@pytest.mark.parametrize("constraint", (
    "uq_question_knowledge_point", "ck_question_knowledge_point_role",
))
def test_schema12_backup_rejects_missing_point_constraints(knowledge_session, tmp_path, constraint):
    session, database = knowledge_session
    session.close()
    with closing(sqlite3.connect(database)) as connection:
        table = "question_knowledge_points"
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        if constraint.startswith("uq_"):
            damaged_sql = re.sub(r"CONSTRAINT " + constraint + r" UNIQUE \(question_id, point_code\),", "", sql)
        else:
            damaged_sql = re.sub(r"CONSTRAINT " + constraint + r" CHECK \(role IN \('main', 'auxiliary'\)\),", "", sql)
        assert damaged_sql != sql
        indexes = [row[0] for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,),
        )]
        connection.execute(f'DROP TABLE "{table}"')
        connection.execute(damaged_sql)
        for index_sql in indexes:
            connection.execute(index_sql)
        connection.commit()
    with pytest.raises(RuntimeError, match="唯一约束|CHECK|检查约束|值域约束|结构异常"):
        _backup_test_database(tmp_path, database)


def test_updated_code_can_back_up_and_restore_schema11_without_new_tables(knowledge_session, tmp_path):
    session, database = knowledge_session
    _seed_points(session)
    session.close()
    with closing(sqlite3.connect(database)) as connection:
        for table in ("question_knowledge_points", "knowledge_point_curriculums", "knowledge_points"):
            connection.execute(f'DROP TABLE "{table}"')
        connection.execute("PRAGMA user_version=11")
        connection.commit()
    archive = _backup_test_database(tmp_path, database)
    counts = verify_full_backup(archive)["database"]["row_counts"]
    assert "knowledge_points" not in counts and counts["question_sub_knowledge"] == 1
    # Match the stopped-service restore contract, not a pooled open SQLite file.
    session.get_bind().dispose()
    restore_full_backup(
        archive, database_path=database, uploads_dir=tmp_path / "uploads",
        metadata_path=tmp_path / "custom_metadata.json", safety_backup_dir=tmp_path / "safety",
        runtime_lock_path=tmp_path / "runtime.lock",
    )
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 11
        assert connection.execute("SELECT category_knowledge, tags FROM questions").fetchone() == (
            "历史自定义小节", "高一期中,待讲",
        )
