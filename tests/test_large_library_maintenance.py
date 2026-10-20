"""Large annotation evidence must not enter startup fingerprint batches."""

import json

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from mathbank.database import (
    Base,
    KnowledgePoint,
    Question,
    QuestionFingerprint,
    QuestionKnowledgePoint,
)
from mathbank.question_duplicate_service import backfill_missing_fingerprints
from mathbank.question_duplicates import QuestionDuplicateInput, build_question_fingerprint


def test_fingerprint_backfill_skips_annotation_evidence_and_preserves_hashes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'maintenance.db'}")
    Base.metadata.create_all(engine)
    content = r"已知数列 $a_n=2^n$，求 $a_4$。"
    answer = "16"
    origin = json.dumps({"audit": "审阅证据" * 100_000}, ensure_ascii=False)
    expected = build_question_fingerprint(QuestionDuplicateInput(
        content=content, answer_markdown=answer, question_type="fill_in_blank",
    ))
    selects = []

    def track_select(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement.lower())

    try:
        with Session(engine) as session:
            question = Question(
                content=content, answer_markdown=answer,
                question_type="fill_in_blank",
            )
            question.knowledge_points.append(QuestionKnowledgePoint(
                point=KnowledgePoint(
                    code="local:backfill", name="等比数列", original_name="等比数列",
                    source="local", aliases=[],
                ),
                role="main", status="confirmed", position=0,
                evidence=json.dumps({"quote": content}, ensure_ascii=False),
                origin=origin,
            ))
            session.add(question)
            session.commit()
            question_id = question.id
            session.expunge_all()

            event.listen(engine, "before_cursor_execute", track_select)
            try:
                result = backfill_missing_fingerprints(
                    session, uploads_dir=tmp_path / "uploads", url_prefix="static/uploads",
                    limit=250, return_cursor=True,
                )
            finally:
                event.remove(engine, "before_cursor_execute", track_select)

            assert result == (1, question_id)
            assert selects
            assert all("question_knowledge_points" not in sql for sql in selects)
            assert all("knowledge_points" not in sql for sql in selects)
            stored = session.get(QuestionFingerprint, (question_id, expected.fingerprint_version))
            assert stored is not None
            assert stored.content_revision_hash == expected.content_revision_hash
            assert stored.exact_hash == expected.exact_hash
            assert stored.critical_math_hash == expected.critical_math_hash
            assert stored.answer_hash == expected.answer_hash
            assert stored.simhash_hex == expected.simhash_hex
            assert stored.status == "ready"
            assert session.scalar(select(QuestionKnowledgePoint.origin)) == origin
    finally:
        engine.dispose()
