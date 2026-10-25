"""Derived exports retain exact formats without retaining all relation evidence."""
from datetime import datetime
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session

from mathbank import sync_helper
from mathbank.content_sources import CONTENT_SOURCES_NOTICE_NAME, content_source_export_files
from mathbank.database import (
    Base, KnowledgePoint, KnowledgePointCurriculum, Question, QuestionCurriculum,
    QuestionKnowledgePoint,
)
from mathbank.question_references import sync_question_references, sync_question_source_labels


@pytest.fixture
def export_database(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA user_version=14")
    with Session(engine) as db:
        point = KnowledgePoint(code="local:shared", name="共享知识点", original_name="旧知识点", source="local")
        point.aliases = ["旧名"]
        point.curriculum_paths = [KnowledgePointCurriculum(
            version_code="A", compulsory="必修一", chapter="第一章", knowledge="第一节", level="leaf",
        )]
        db.add(point)
        for index in range(1, 258):
            question = Question(
                id=index, content=f"第{index}题 $x=1$。" + ("参见例1.257。" if index == 1 else ""),
                source=f"批次测试 | 例1.{index}", answer_markdown="答案\n\\解答 \"中文\"😀",
                review="保留评析", question_type="single_choice" if index % 2 else "fill_in_blank",
                difficulty="hard" if index % 2 else "easy", tags="原标签", association_group_id="旧关联组",
                category_compulsory="必修一", category_chapter="第一章", category_knowledge="第一节",
                created_at=datetime(2025, 1, 2, 3, 4),
            )
            question.curriculum_mappings = [
                QuestionCurriculum(version_code="A", compulsory="必修一", chapter="第一章", knowledge="第一节"),
                QuestionCurriculum(version_code="B", compulsory="B册", chapter="B章", knowledge="B节"),
            ]
            question.curriculum_mappings[0].sub_knowledge = [{
                "compulsory": "旧册", "chapter": "旧章", "knowledge": "保留旧关系",
            }]
            question.knowledge_points = [QuestionKnowledgePoint(
                point=point, role="main" if index % 2 else "auxiliary", status="suggested", position=3,
                scope=json.dumps([index, {"值": "原值"}], ensure_ascii=False),
                evidence_source=json.dumps({"source": index}), evidence=json.dumps({"text": "逐题依据" * 100}),
                reason=json.dumps("完整原因"), origin=json.dumps({"index": index, "large": "保留来源" * 1000}),
            )]
            if index == 1:
                question.image_paths = ["/static/uploads/visible.png", "/static/uploads/hidden.png"]
                question.tikz_reference_image_path = "/static/uploads/hidden.png"
                question.content_tikz_assets = [{"tikz_code": "\\draw (0,0)--(1,1);"}]
            db.add(question)
        db.flush()
        for question in db.query(Question):
            sync_question_source_labels(db, question)
        sync_question_references(db, db.get(Question, 1))
        db.commit()
    monkeypatch.setattr(sync_helper, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(tmp_path / "questions.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(tmp_path / "questions.md"))
    monkeypatch.setattr(sync_helper, "resolve_active_version", lambda: "A")
    monkeypatch.setattr(sync_helper, "datetime", SimpleNamespace(datetime=SimpleNamespace(
        now=lambda: datetime(2026, 10, 22, 12, 30),
    )))
    try:
        yield engine, path, tmp_path
    finally:
        engine.dispose()


def test_batch_exports_exact_legacy_json_and_markdown_without_full_evidence_views(export_database, monkeypatch):
    engine, _, directory = export_database
    with Session(engine) as db:
        questions = db.query(Question).order_by(Question.id).all()
        payloads, mappings = sync_helper.serialize_exchange_questions(db, questions, "A")
        # Match the legacy text writer's platform newline convention as well as
        # its JSON bytes; Windows text output uses CRLF.
        expected_json_path = directory / "legacy.json"
        with open(expected_json_path, "w", encoding="utf-8") as handle:
            json.dump(payloads, handle, ensure_ascii=False, indent=2)
        expected_json = expected_json_path.read_bytes()
        expected_markdown = directory / "legacy.md"
        sync_helper.generate_markdown_library(
            questions, expected_markdown, curriculum_mappings=mappings, active_version_code="A",
            knowledge_points={item["id"]: item["knowledge_points"] for item in payloads},
            source_notice=content_source_export_files()[CONTENT_SOURCES_NOTICE_NAME].decode("utf-8"),
        )
    batches, identities = [], []
    original_serialize = sync_helper.serialize_exchange_questions
    original_markdown = sync_helper.generate_markdown_library

    def serialize(db, questions, active_version):
        batches.append([question.id for question in questions])
        identities.append(sum(isinstance(item, Question) for item in db.identity_map.values()))
        assert len(questions) <= 128
        return original_serialize(db, questions, active_version)

    def markdown(questions, filepath, **kwargs):
        assert len(questions) == 257
        assert all(not hasattr(question, "answer_markdown") for question in questions)
        assert all(set(point) == {"name", "code", "role"}
                   for points in kwargs["knowledge_points"].values() for point in points)
        return original_markdown(questions, filepath, **kwargs)

    monkeypatch.setattr(sync_helper, "serialize_exchange_questions", serialize)
    monkeypatch.setattr(sync_helper, "generate_markdown_library", markdown)
    with Session(engine) as db:
        held = db.get(Question, 1)
        report = sync_helper.export_database_to_files(db, quiet=True)
        assert inspect(held).persistent
        assert db.get(Question, 1) is held
    assert report["question_count"] == 257
    assert [len(batch) for batch in batches] == [128, 128, 1]
    assert max(identities) <= 129
    # Avoid pytest building a multi-megabyte byte diff if this strict comparison
    # fails; repetitive evidence strings make that diagnostic very expensive.
    if (directory / "questions.json").read_bytes() != expected_json:
        pytest.fail("Batch JSON export differs from the legacy JSON file.", pytrace=False)
    assert (directory / "questions.md").read_bytes() == expected_markdown.read_bytes()
    first = json.loads(expected_json)[0]
    assert first["question_references"][0]["target_question_id"] == 257
    assert first["knowledge_points"][0]["origin"]["large"] == "保留来源" * 1000


def test_later_batches_share_one_real_sqlite_read_snapshot(export_database):
    engine, path, directory = export_database
    changed = []

    def edit_between_batches(_connection, _cursor, statement, parameters, _context, _many):
        if changed or "questions.content AS questions_content" not in statement or len(parameters) != 128 or 129 not in parameters:
            return
        changed.append(True)
        with sqlite3.connect(path) as writer:
            writer.execute("UPDATE questions SET content='并发编辑', category_knowledge='新小节' WHERE id=257")
            writer.execute("UPDATE question_curriculums SET knowledge='新小节' WHERE question_id=257 AND version_code='A'")

    event.listen(engine, "before_cursor_execute", edit_between_batches)
    try:
        with Session(engine) as db:
            sync_helper.export_database_to_files(db, quiet=True)
    finally:
        event.remove(engine, "before_cursor_execute", edit_between_batches)
    assert changed
    last = json.loads((directory / "questions.json").read_text(encoding="utf-8"))[-1]
    assert last["content"].startswith("第257题") and last["category_knowledge"] == "第一节"
    assert "并发编辑" not in (directory / "questions.md").read_text(encoding="utf-8")
    with Session(engine) as db:
        assert db.get(Question, 257).content == "并发编辑"


def test_late_batch_failure_keeps_all_previous_output_files(export_database, monkeypatch):
    engine, _, directory = export_database
    paths = [directory / name for name in (
        "questions.json", "questions.md", "content_sources.json", "CONTENT_SOURCES.txt",
    )]
    for path in paths:
        path.write_bytes(b"previous complete export")
    calls = []
    original = sync_helper.serialize_exchange_questions

    def fail_second(db, questions, active_version):
        calls.append(True)
        if len(calls) == 2:
            raise OSError("controlled second batch failure")
        return original(db, questions, active_version)

    monkeypatch.setattr(sync_helper, "serialize_exchange_questions", fail_second)
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="second batch"):
            sync_helper.export_database_to_files(db, quiet=True)
    assert len(calls) == 2
    assert all(path.read_bytes() == b"previous complete export" for path in paths)
    assert not list(directory.glob(".mathbank-export-*"))


def test_empty_database_preserves_the_empty_array(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(tmp_path / "questions.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(tmp_path / "questions.md"))
    try:
        with Session(engine) as db:
            result = sync_helper.export_database_to_files(db, quiet=True)
        assert result["question_count"] == 0
        assert (tmp_path / "questions.json").read_bytes() == b"[]"
        assert "暂无题目数据" in (tmp_path / "questions.md").read_text(encoding="utf-8")
    finally:
        engine.dispose()


def test_export_batches_reference_queries_below_old_sqlite_limit(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old-sqlite.db'}")

    @event.listens_for(engine, "connect")
    def old_variable_limit(connection, _):
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=14")
    with Session(engine) as db:
        db.add_all([Question(content=f"旧SQLite变量限制 第{index}题") for index in range(1025)])
        db.commit()
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(tmp_path / "questions.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(tmp_path / "questions.md"))
    try:
        with Session(engine) as db:
            result = sync_helper.export_database_to_files(db, quiet=True)
        records = json.loads((tmp_path / "questions.json").read_text(encoding="utf-8"))
        assert result["question_count"] == len(records) == 1025
        assert records[-1]["id"] == 1025
    finally:
        engine.dispose()
