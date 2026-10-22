"""Exchange exports reuse catalog trees while preserving every relation value."""
from datetime import datetime
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mathbank.database import (
    Base, KnowledgePoint, KnowledgePointCurriculum, Question,
    QuestionCurriculum, QuestionKnowledgePoint,
)
from mathbank.question_references import (
    serialize_question_reference_data, sync_question_references, sync_question_source_labels,
)
from mathbank.sync_helper import serialize_exchange_questions


@pytest.fixture
def catalog_library():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=13")
    try:
        with sessionmaker(bind=engine)() as db:
            point = KnowledgePoint(code="local:shared", name="共享点", original_name="原始点", source="local")
            point.aliases = ["旧名", "别名"]
            point.curriculum_paths = [KnowledgePointCurriculum(
                version_code="A", compulsory="必修一", chapter="第一章", knowledge=f"第{index}节",
                reference_section="原始目录", level="leaf",
            ) for index in range(64)]
            other = KnowledgePoint(code="1015", name="另一点", original_name="另一原始点", source="zhixue")
            other.aliases = []
            db.add_all([point, other])
            for index in range(3):
                question = Question(
                    content=f"第{index + 1}题 $x^2$，参见例1.2。", source=f"测试讲义 | 例1.{index + 1}",
                    answer_markdown=f"答案{index + 1}", review="保留评析", created_at=datetime(2025, 8, 1, 12, 30),
                    category_compulsory="必修一", category_chapter="第一章", category_knowledge="第一节",
                    association_group_id="source-group", figure_align="center", figure_size="large",
                    figure_align_custom=True, usage_count=9, tags="保留标签", difficulty="hard",
                )
                question.curriculum_mappings = [
                    QuestionCurriculum(version_code="A", compulsory="必修一", chapter="第一章", knowledge="第一节"),
                    QuestionCurriculum(version_code="B", compulsory="B册", chapter="B章", knowledge="B节"),
                ]
                question.curriculum_mappings[0].sub_knowledge = [{
                    "compulsory": "旧册", "chapter": "旧章", "knowledge": "保留旧关系",
                }]
                question.knowledge_points = [QuestionKnowledgePoint(
                    point=point, role="main" if index == 0 else "auxiliary",
                    status="confirmed" if index == 0 else "suggested", position=index + 3,
                    scope=json.dumps({"question": index, "range": [1, 2]}),
                    evidence_source=json.dumps(["目录", {"index": index}], ensure_ascii=False),
                    evidence=json.dumps({"text": f"第{index + 1}题证据"}, ensure_ascii=False),
                    reason=json.dumps("旧字符串依据", ensure_ascii=False), origin="null",
                ), QuestionKnowledgePoint(point=other, role="auxiliary", status="confirmed", position=8)]
                db.add(question)
            db.flush()
            questions = db.query(Question).order_by(Question.id).all()
            for question in questions:
                sync_question_source_labels(db, question)
                sync_question_references(db, question)
            db.commit()
            yield db, db.query(Question).order_by(Question.id).all(), point
    finally:
        engine.dispose()


def test_catalog_caching_preserves_the_complete_legacy_exchange_values(catalog_library):
    db, questions, _ = catalog_library
    actual, mappings = serialize_exchange_questions(db, questions, "A")
    references = serialize_question_reference_data(db, [question.id for question in questions])
    expected = []
    for question in questions:
        mirrors = mappings[question.id]
        active = next(mapping for mapping in mirrors if mapping["version_code"] == "A")
        payload = question.to_dict(version_code="A", sub_knowledge=active["sub_knowledge"])
        # Frozen pre-optimization expression: each call creates its own catalog.
        payload["knowledge_points"] = [
            {**row.point.to_dict(), **row.to_dict(), "position": row.position}
            for row in question.knowledge_points
        ]
        payload["active_version_code"] = "A"
        payload["curriculum_mappings"] = mirrors
        payload["sub_knowledge"] = active["sub_knowledge"]
        payload.update(references[question.id])
        expected.append(payload)
    assert actual == expected
    # Also freeze JSON key ordering, not just Python's order-independent equality.
    assert json.dumps(actual, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


def test_export_catalog_is_serialized_once_and_metadata_remains_per_question(catalog_library, monkeypatch):
    db, questions, point = catalog_library
    calls = []
    original = KnowledgePoint.to_dict

    def observe(self):
        calls.append(self.code)
        return original(self)

    monkeypatch.setattr(KnowledgePoint, "to_dict", observe)
    exported, _ = serialize_exchange_questions(db, questions, "A")
    assert calls == ["local:shared", "1015"]
    left, right = (exported[index]["knowledge_points"][0] for index in (0, 1))
    assert left is not right
    assert left["curriculum_paths"] is right["curriculum_paths"]
    assert left["aliases"] is right["aliases"]
    assert left["evidence"] is not right["evidence"] and left["scope"] is not right["scope"]
    assert (left["role"], left["status"], left["position"]) == ("main", "confirmed", 3)
    assert (right["role"], right["status"], right["position"]) == ("auxiliary", "suggested", 4)
    left["evidence"]["text"] = "只改导出副本第一题"
    assert right["evidence"]["text"] == "第2题证据"
    point.name = "下次导出的新名称"
    again, _ = serialize_exchange_questions(db, questions, "A")
    assert calls == ["local:shared", "1015", "local:shared", "1015"]
    second_run = again[0]["knowledge_points"][0]
    assert second_run["name"] == "下次导出的新名称"
    assert second_run["curriculum_paths"] is not left["curriculum_paths"]
    assert second_run["evidence"]["text"] == "第1题证据"


def test_shared_catalog_survives_the_real_json_and_markdown_export(catalog_library, tmp_path, monkeypatch):
    from mathbank import sync_helper

    db, questions, _ = catalog_library
    expected, _ = serialize_exchange_questions(db, questions, "A")
    monkeypatch.setattr(sync_helper, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(tmp_path / "questions.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(tmp_path / "questions.md"))
    monkeypatch.setattr(sync_helper, "resolve_active_version", lambda: "A")
    report = sync_helper.export_database_to_files(db, quiet=True)
    assert report["question_count"] == 3
    assert json.loads((tmp_path / "questions.json").read_text(encoding="utf-8")) == expected
    markdown = (tmp_path / "questions.md").read_text(encoding="utf-8")
    assert markdown.count("共享点 (`local:shared`)") == 3
    assert all(f"第{index}题 $x^2$" in markdown for index in (1, 2, 3))
    assert db.query(Question).count() == 3
