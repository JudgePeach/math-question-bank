import pytest
from database import Question

def test_question_crud_operations(db_session):
    # 1. Create Question
    q = Question(
        content="设集合 $A = \\{1, 2\\}$, $B = \\{2, 3\\}$，则 $A \\cup B = $",
        question_type="single_choice",
        category_compulsory="必修一",
        category_chapter="第一章 集合与常用逻辑用语",
        category_knowledge="集合的并集",
        difficulty="easy",
        source="2024高考真题",
        answer_markdown="$\\{1, 2, 3\\}$",
        review="这是一道基础的集合并集题目",
        association_group_id="group_123"
    )
    # Set image paths
    q.image_paths = ["/static/uploads/test_img.png"]
    
    db_session.add(q)
    db_session.commit()
    db_session.refresh(q)
    
    assert q.id is not None
    assert q.question_type == "single_choice"
    assert q.category_compulsory == "必修一"
    assert q.image_paths == ["/static/uploads/test_img.png"]
    
    # Test dictionary formats
    d = q.to_dict()
    assert d["id"] == q.id
    assert d["content"] == q.content
    assert "answer_markdown" in d
    assert d["answer_markdown"] == "$\\{1, 2, 3\\}$"
    assert d["review"] == "这是一道基础的集合并集题目"
    assert d["association_group_id"] == "group_123"
    
    s = q.to_summary_dict()
    assert s["id"] == q.id
    assert "answer_markdown" not in s  # Summary should not leak answers
    
    # 2. Read / Query Question
    retrieved = db_session.query(Question).filter_by(id=q.id).first()
    assert retrieved is not None
    assert retrieved.source == "2024高考真题"
    
    # 3. Update Question
    retrieved.difficulty = "medium"
    db_session.commit()
    
    updated = db_session.query(Question).filter_by(id=q.id).first()
    assert updated.difficulty == "medium"
    
    # 4. Delete Question
    db_session.delete(updated)
    db_session.commit()
    
    deleted = db_session.query(Question).filter_by(id=q.id).first()
    assert deleted is None
