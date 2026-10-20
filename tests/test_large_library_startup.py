"""Maintenance updates counters without hydrating question/evidence graphs."""

from sqlalchemy import event

from mathbank.database import Paper, PaperQuestion, Question


def test_usage_calibration_is_atomic_sql_and_preserves_question_data(db_session):
    import main

    questions = [
        Question(content="计数甲", usage_count=99),
        Question(content="计数乙", usage_count=1),
        Question(content="计数丙", usage_count=0),
    ]
    paper = Paper(title="隔离计数专项")
    db_session.add_all([*questions, paper])
    db_session.flush()
    ids = [question.id for question in questions]
    db_session.add_all([
        PaperQuestion(paper_id=paper.id, question_id=ids[0], order_index=0),
        PaperQuestion(paper_id=paper.id, question_id=ids[0], order_index=1),
    ])
    db_session.commit()
    statements = []

    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement.lower())

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        main.recalibrate_usage_counts()
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    db_session.expire_all()
    assert db_session.query(Question.id, Question.content, Question.usage_count).order_by(Question.id).all() == [
        (ids[0], "计数甲", 2), (ids[1], "计数乙", 0), (ids[2], "计数丙", 0),
    ]
    assert any(statement.lstrip().startswith("update questions") for statement in statements)
    assert not any("knowledge_point" in statement for statement in statements)
    assert not any(statement.lstrip().startswith("select") for statement in statements)
