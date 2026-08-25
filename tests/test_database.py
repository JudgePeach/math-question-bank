from mathbank.database import Paper, Question

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
        tikz_reference_image_path="/static/uploads/original.png",
        association_group_id="group_123"
    )
    # Set image paths
    q.image_paths = [
        "/static/uploads/test_img.png",
        "/static/uploads/original.png",
    ]
    
    db_session.add(q)
    db_session.commit()
    db_session.refresh(q)
    
    assert q.id is not None
    assert q.question_type == "single_choice"
    assert q.category_compulsory == "必修一"
    assert q.image_paths == [
        "/static/uploads/test_img.png",
        "/static/uploads/original.png",
    ]
    
    # Test dictionary formats
    d = q.to_dict()
    assert d["id"] == q.id
    assert d["content"] == q.content
    assert "answer_markdown" in d
    assert d["answer_markdown"] == "$\\{1, 2, 3\\}$"
    assert d["has_answer"] is True
    assert d["review"] == "这是一道基础的集合并集题目"
    assert d["tikz_reference_image_path"] == "/static/uploads/original.png"
    assert d["content_tikz_assets"] == []
    assert d["image_paths"] == ["/static/uploads/test_img.png"]
    assert d["association_group_id"] == "group_123"
    
    s = q.to_summary_dict()
    assert s["id"] == q.id
    assert "answer_markdown" not in s  # Summary should not leak answers
    assert "answer_tikz_assets" not in s  # Editable answer sources stay detail-only
    assert "content_tikz_assets" not in s  # Editable content sources stay detail-only
    assert "tikz_reference_image_path" not in s  # OCR reference stays detail-only
    assert s["image_paths"] == ["/static/uploads/test_img.png"]
    assert s["has_answer"] is True
    
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


def test_json_model_fields_fail_closed_on_wrong_json_shapes():
    question = Question(content="shape test")
    for malformed_shape in ('{}', '"/static/uploads/a.png"', "null", "123"):
        question._image_paths = malformed_shape
        assert question.image_paths == []
        question._answer_tikz_assets = malformed_shape
        assert question.answer_tikz_assets == []
        question._content_tikz_assets = malformed_shape
        assert question.content_tikz_assets == []

    paper = Paper(title="shape test", metadata_json="[]")
    payload = paper.to_dict()
    assert payload["show_secret"] is True
    assert payload["show_notice"] is True

    paper.metadata_json = '"not-an-object"'
    payload = paper.to_dict()
    assert payload["show_secret"] is True
    assert payload["show_notice"] is True
