"""Bounded read projections for large libraries, without changing stored data."""

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from mathbank.database import (
    KnowledgePoint,
    Question,
    QuestionCurriculum,
    QuestionKnowledgePoint,
)
from mathbank.source_comments import strip_source_attribution_comment


def question_summary_load_options():
    """Keep list labels cheap before ORM deserialization, including point joins.

    Question image visibility still needs its answer/review and TikZ metadata;
    the large knowledge-point audit fields are neither selected nor parsed.
    Raiseload catches accidental use of those fields by a summary renderer.
    """
    points = selectinload(Question.knowledge_points).load_only(
        QuestionKnowledgePoint.id,
        QuestionKnowledgePoint.question_id,
        QuestionKnowledgePoint.point_code,
        QuestionKnowledgePoint.role,
        QuestionKnowledgePoint.status,
        QuestionKnowledgePoint.position,
        raiseload=True,
    ).joinedload(QuestionKnowledgePoint.point).load_only(
        KnowledgePoint.code, KnowledgePoint.name, raiseload=True,
    ).raiseload(KnowledgePoint.curriculum_paths)
    return (
        selectinload(Question.curriculum_mappings).selectinload(
            QuestionCurriculum.sub_knowledge_rows
        ),
        points,
    )


def find_association_candidates(
    db, *, q="", page=1, page_size=20, exclude_id=None,
    seq_num=None, question_id=None,
):
    """Find at most 20 labels, never loading question/point ORM graphs.

    Explicit sequence lookup uses ascending stable IDs over the entire library
    before applying exclusion or keyword filters. It therefore respects holes
    left by deletes and never mistakes a physical ID for its displayed number.
    """
    page_size = max(1, min(int(page_size), 20))
    query = db.query(
        Question.id,
        Question.question_type,
        func.substr(Question.content, 1, 2048).label("content"),
    )
    if seq_num is not None:
        target = (
            db.query(Question.id).order_by(Question.id.asc())
            .offset(int(seq_num) - 1).limit(1).first()
        )
        query = query.filter(Question.id == (target[0] if target else -1))
    if question_id is not None:
        query = query.filter(Question.id == question_id)
    if exclude_id is not None:
        query = query.filter(Question.id != exclude_id)
    keyword = str(q or "").strip()
    if keyword:
        query = query.filter(or_(
            Question.content.contains(keyword, autoescape=True),
            Question.source.contains(keyword, autoescape=True),
        ))
    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    rows = (
        query.order_by(Question.created_at.desc(), Question.id.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    items = []
    for row in rows:
        content = strip_source_attribution_comment(str(row.content or ""))
        content = " ".join(content.split())
        if len(content) > 180:
            content = content[:179] + "…"
        items.append({
            "id": row.id, "question_type": row.question_type, "content": content,
        })
    return {
        "items": items, "total": total, "page": page,
        "page_size": page_size, "total_pages": total_pages,
    }
