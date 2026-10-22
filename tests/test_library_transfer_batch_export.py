"""Sharing exports stream bounded batches from one stable SQLite snapshot."""
import io
import json
import sqlite3
import zipfile

from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from mathbank import library_transfer as transfer
from mathbank.database import (
    Base, KnowledgePoint, KnowledgePointCurriculum, Question, QuestionCurriculum,
    QuestionKnowledgePoint,
)
from mathbank.question_references import sync_question_references, sync_question_source_labels
from mathbank.sync_helper import serialize_exchange_questions


@pytest.fixture
def export_library(tmp_path):
    path = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=14")
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    image = io.BytesIO()
    Image.new("RGB", (32, 20), "red").save(image, format="PNG")
    for name in ("visible.png", "reference.png"):
        (uploads / name).write_bytes(image.getvalue())
    with Session(engine) as db:
        point = KnowledgePoint(code="local:shared", name="共享点", original_name="原始点", source="local")
        point.aliases = ["旧名"]
        point.curriculum_paths = [KnowledgePointCurriculum(
            version_code="A", compulsory="必修一", chapter="第一章", knowledge=f"第{index}节",
            reference_section="原始目录", level="leaf",
        ) for index in range(64)]
        db.add(point)
        for index in range(1, 258):
            content = f"第{index}题 $x^2$。"
            if index == 1:
                content += "参看例1.257。 ![图](/static/uploads/visible.png)"
            elif index == 257:
                content += "参看例1.1。"
            question = Question(
                id=index, content=content, answer_markdown=f"第{index}题答案", review="保留评析",
                source=f"测试讲义 | 例1.{index} | 类别{'偶数' if index % 2 == 0 else '奇数'}",
                category_compulsory="必修一", category_chapter="第一章", category_knowledge="第一节",
            )
            question.curriculum_mappings = [
                QuestionCurriculum(version_code="A", compulsory="必修一", chapter="第一章", knowledge="第一节"),
                QuestionCurriculum(version_code="B", compulsory="B册", chapter="B章", knowledge="B节"),
            ]
            question.curriculum_mappings[0].sub_knowledge = [{
                "compulsory": "旧册", "chapter": "旧章", "knowledge": "保留旧关系",
            }]
            question.knowledge_points = [QuestionKnowledgePoint(
                point=point, role="main" if index % 2 else "auxiliary",
                status="confirmed" if index % 2 else "suggested", position=index % 7,
                evidence=json.dumps({"question": index, "text": f"第{index}题依据"}, ensure_ascii=False),
                origin=json.dumps({"question": index, "nested": ["原始来源", {"keep": True}]}, ensure_ascii=False),
            )]
            if index == 1:
                question.image_paths = ["/static/uploads/visible.png", "/static/uploads/reference.png"]
                question.tikz_reference_image_path = "/static/uploads/reference.png"
            db.add(question)
        db.flush()
        for question in db.query(Question):
            sync_question_source_labels(db, question)
        for qid in (1, 257):
            sync_question_references(db, db.get(Question, qid))
        db.commit()
    try:
        yield engine, path, uploads
    finally:
        engine.dispose()


def _legacy_bytes(engine, ids):
    with Session(engine) as db:
        transfer._library_read_snapshot(db, "A")
        by_id = {question.id: question for question in db.query(Question).filter(Question.id.in_(ids))}
        questions = [by_id[qid] for qid in ids]
        records, _ = serialize_exchange_questions(db, questions, "A")
        for question, record in zip(questions, records):
            record["image_paths"] = list(question.image_paths)
        transfer._validate_records(records)
        return transfer._json(records)


@pytest.mark.parametrize("selected", [False, True])
def test_export_streams_bounded_batches_and_preserves_complete_v1_bytes(export_library, tmp_path, monkeypatch, selected):
    engine, _, uploads = export_library
    ids = list(range(257, 0, -1)) if selected else list(range(1, 258))
    expected = _legacy_bytes(engine, ids)
    batches, full_row_reads = [], []
    original = transfer.serialize_exchange_questions

    def serialize(db, questions, active):
        batches.append([question.id for question in questions])
        assert len(questions) <= 128
        return original(db, questions, active)

    def observe(_connection, _cursor, statement, parameters, _context, _many):
        lowered = statement.lower()
        if "from questions" in lowered and "questions.content as questions_content" in lowered:
            assert "questions.id in" in lowered
            full_row_reads.append(len(parameters))
            assert len(parameters) <= 128

    monkeypatch.setattr(transfer, "serialize_exchange_questions", serialize)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        output = tmp_path / "streamed.zip"
        with Session(engine) as db:
            result = transfer.export_library_zip(db, output, uploads_dir=uploads,
                                                 question_ids=ids if selected else None)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert [len(batch) for batch in batches] == [128, 128, 1]
    assert [qid for batch in batches for qid in batch] == ids
    assert full_row_reads
    assert result == {"question_count": 257, "image_count": 2}
    with zipfile.ZipFile(output) as archive:
        assert archive.read("questions.json") == expected
        records = json.loads(expected)
        by_id = {record["id"]: record for record in records}
        assert by_id[1]["question_references"][0]["target_question_id"] == 257
        assert by_id[257]["question_references"][0]["target_question_id"] == 1
        assert all(by_id[qid]["question_references"][0]["status"] == "resolved" for qid in (1, 257))
    assert transfer.inspect_library_zip(output) == {"question_count": 257, "image_count": 2, "format_version": 1}


def test_source_filtered_export_freezes_ascending_ids_before_streaming(export_library, tmp_path):
    engine, _, uploads = export_library
    expected_ids = list(range(2, 258, 2))
    expected = _legacy_bytes(engine, expected_ids)
    output = tmp_path / "filtered.zip"
    with Session(engine) as db:
        result = transfer.export_library_zip(db, output, uploads_dir=uploads, source_keywords=["类别偶数"])
    assert result == {"question_count": 128, "image_count": 0}
    with zipfile.ZipFile(output) as archive:
        assert archive.read("questions.json") == expected


def test_import_remaps_both_directions_of_cross_batch_references(export_library, tmp_path):
    from mathbank.question_references import reference_payloads

    engine, _, uploads = export_library
    output = tmp_path / "cross-batch.zip"
    with Session(engine) as source:
        transfer.export_library_zip(source, output, uploads_dir=uploads)
    target_engine = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    Base.metadata.create_all(target_engine)
    try:
        with Session(target_engine) as target:
            target.add(Question(content="已有题目"))
            target.commit()
            result = transfer.import_library_zip(target, output, uploads_dir=tmp_path / "target-uploads")
        with Session(target_engine) as readback:
            for source_id, destination_id in ((1, 257), (257, 1)):
                question = readback.get(Question, result["id_map"][source_id])
                reference = reference_payloads(readback, [question])[question.id][0]
                assert reference["status"] == "resolved"
                assert reference["target_question_id"] == result["id_map"][destination_id]
                assert question.content[reference["start"]:reference["end"]] == reference["text"]
            assert readback.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        target_engine.dispose()


def test_later_batches_keep_the_frozen_read_snapshot(export_library, tmp_path):
    engine, path, uploads = export_library
    changed = []

    def update_between_batches(_connection, _cursor, statement, parameters, _context, _many):
        if changed or "questions.content AS questions_content" not in statement or len(parameters) != 128 or 129 not in parameters:
            return
        changed.append(True)
        with sqlite3.connect(path) as writer:
            writer.execute("UPDATE questions SET content='并发编辑的新题干', category_knowledge='新小节' WHERE id=257")
            writer.execute("UPDATE question_curriculums SET knowledge='新小节' WHERE question_id=257 AND version_code='A'")

    output = tmp_path / "snapshot.zip"
    event.listen(engine, "before_cursor_execute", update_between_batches)
    try:
        with Session(engine) as db:
            transfer.export_library_zip(db, output, uploads_dir=uploads)
    finally:
        event.remove(engine, "before_cursor_execute", update_between_batches)
    assert changed
    with zipfile.ZipFile(output) as archive:
        last = json.loads(archive.read("questions.json"))[-1]
        assert last["id"] == 257 and last["content"].startswith("第257题")
        assert last["category_knowledge"] == "第一节"
        assert next(mapping for mapping in last["curriculum_mappings"] if mapping["version_code"] == "A")["knowledge"] == "第一节"
    with Session(engine) as db:
        assert db.get(Question, 257).content == "并发编辑的新题干"


def test_late_batch_failure_keeps_the_previous_zip_and_cleans_temporary_files(export_library, tmp_path, monkeypatch):
    engine, _, uploads = export_library
    output = tmp_path / "existing.zip"
    output.write_bytes(b"previous valid output")
    calls = []
    original = transfer.serialize_exchange_questions

    def fail_second_batch(db, questions, active):
        calls.append(len(questions))
        if len(calls) == 2:
            raise RuntimeError("controlled second batch failure")
        return original(db, questions, active)

    monkeypatch.setattr(transfer, "serialize_exchange_questions", fail_second_batch)
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="second batch"):
            transfer.export_library_zip(db, output, uploads_dir=uploads)
    assert calls == [128, 128]
    assert output.read_bytes() == b"previous valid output"
    assert list(tmp_path.glob(".mathbank-share-*")) == []
    with Session(engine) as db:
        assert db.query(Question).count() == 257


def test_id_count_limit_rejects_before_any_full_question_serialization(export_library, tmp_path, monkeypatch):
    engine, _, uploads = export_library
    monkeypatch.setattr(transfer, "MAX_QUESTIONS", 256)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("oversized export must fail at the lightweight ID snapshot")

    monkeypatch.setattr(transfer, "serialize_exchange_questions", forbidden)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="超过上限"):
            transfer.export_library_zip(db, tmp_path / "oversized.zip", uploads_dir=uploads)
