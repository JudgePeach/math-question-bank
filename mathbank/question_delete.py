"""Question removal shared by the editor and reviewed Agent transactions."""
from sqlalchemy import func

from .database import Paper, PaperQuestion, Question, QuestionSourceLabel
from .question_references import refresh_reference_targets_for_labels
from .question_assets import embedded_question_assets, structured_question_assets


def delete_questions(db, question_ids):
    """Delete exact targets and repair paper totals/references; caller commits."""
    ids = set(question_ids)
    questions = db.query(Question).filter(Question.id.in_(ids)).all()
    if {question.id for question in questions} != ids:
        raise ValueError("题目已被删除，请重新核对。")
    assets = [path for question in questions for path in question.image_paths]
    for question in questions:
        assets.extend(embedded_question_assets(question.content, question.answer_markdown, question.review))
        assets.extend(structured_question_assets(question.content_tikz_assets, question.answer_tikz_assets,
                                                 legacy_reference=question.tikz_reference_image_path))
    labels = set(db.query(QuestionSourceLabel.book_key, QuestionSourceLabel.label_key)
                 .filter(QuestionSourceLabel.question_id.in_(ids)).all())
    paper_ids = [row[0] for row in db.query(PaperQuestion.paper_id)
                 .filter(PaperQuestion.question_id.in_(ids)).distinct()]
    if paper_ids:
        totals = dict(db.query(PaperQuestion.paper_id, func.coalesce(func.sum(PaperQuestion.score), 0))
                      .filter(PaperQuestion.paper_id.in_(paper_ids), ~PaperQuestion.question_id.in_(ids))
                      .group_by(PaperQuestion.paper_id).all())
        for paper in db.query(Paper).filter(Paper.id.in_(paper_ids)):
            paper.total_score = int(totals.get(paper.id, 0))
    for question in questions:
        db.delete(question)
    db.flush()
    refresh_reference_targets_for_labels(db, labels)
    return assets
