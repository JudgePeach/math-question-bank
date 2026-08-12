import json
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import event

from mathbank.database import Paper, PaperQuestion, Question, QuestionCurriculum


def _payload(**overrides):
    payload = {
        "content": "事务测试题",
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "第一章",
        "category_knowledge": "集合",
        "difficulty": "medium",
        "source": "测试",
        "answer_markdown": "答案",
        "review": "",
        "tikz_code": "",
        "tags": "",
        "related_question_id": "",
        "image_paths": "[]",
    }
    payload.update(overrides)
    return payload


def _headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _write_png(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), "white").save(path, format="PNG")


def test_question_and_curriculum_are_one_transaction(client, db_session):
    def reject_curriculum(_session, _flush_context, _instances):
        if any(isinstance(item, QuestionCurriculum) for item in db_session.new):
            raise RuntimeError("simulated curriculum failure")

    event.listen(db_session, "before_flush", reject_curriculum)
    try:
        response = client.post("/api/questions", data=_payload(), headers=_headers())
    finally:
        event.remove(db_session, "before_flush", reject_curriculum)
        db_session.rollback()

    assert response.status_code == 400
    assert db_session.query(Question).count() == 0


def test_failed_create_moves_promoted_temp_image_back(client, db_session):
    import main

    temp_image = Path(main.TMP_UPLOAD_DIR) / "transaction-promotion.png"
    permanent_image = Path(main.UPLOAD_DIR) / temp_image.name
    permanent_image.unlink(missing_ok=True)
    _write_png(temp_image)
    reference = f"/{main.UPLOAD_DIR_REL}/tmp/{temp_image.name}"

    def reject_curriculum(_session, _flush_context, _instances):
        if any(isinstance(item, QuestionCurriculum) for item in db_session.new):
            raise RuntimeError("simulated curriculum failure")

    event.listen(db_session, "before_flush", reject_curriculum)
    try:
        response = client.post(
            "/api/questions",
            data=_payload(image_paths=json.dumps([reference])),
            headers=_headers(),
        )
    finally:
        event.remove(db_session, "before_flush", reject_curriculum)

    try:
        assert response.status_code == 400
        assert temp_image.is_file()
        assert not permanent_image.exists()
        assert db_session.query(Question).count() == 0
    finally:
        temp_image.unlink(missing_ok=True)
        permanent_image.unlink(missing_ok=True)


def test_failed_update_keeps_old_image(client, db_session, monkeypatch):
    import main

    image = Path(main.UPLOAD_DIR) / "transaction-update.png"
    _write_png(image)
    reference = f"/{main.UPLOAD_DIR_REL}/{image.name}"
    question = Question(content="原题", question_type="single_choice")
    question.image_paths = [reference]
    db_session.add(question)
    db_session.commit()

    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated commit failure")),
    )
    response = client.put(
        f"/api/questions/{question.id}",
        data=_payload(content="更新内容", image_paths="[]"),
        headers=_headers(),
    )

    try:
        assert response.status_code == 400
        assert image.is_file()
    finally:
        image.unlink(missing_ok=True)


def test_delete_only_removes_image_after_commit_and_last_reference(
    client, db_session, monkeypatch
):
    import main

    image = Path(main.UPLOAD_DIR) / "transaction-shared.png"
    _write_png(image)
    reference = f"/{main.UPLOAD_DIR_REL}/{image.name}"
    first = Question(content="题目一", question_type="single_choice")
    second = Question(content="题目二", question_type="single_choice")
    first.image_paths = [reference]
    second.image_paths = [reference]
    db_session.add_all([first, second])
    db_session.commit()
    first_id, second_id = first.id, second.id

    response = client.delete(f"/api/questions/{first_id}", headers=_headers())
    assert response.status_code == 200
    assert image.is_file()

    original_commit = db_session.commit
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated commit failure")),
    )
    failed = client.delete(f"/api/questions/{second_id}", headers=_headers())
    assert failed.status_code == 400
    assert image.is_file()

    monkeypatch.setattr(db_session, "commit", original_commit)
    succeeded = client.delete(f"/api/questions/{second_id}", headers=_headers())
    try:
        assert succeeded.status_code == 200
        assert not image.exists()
    finally:
        image.unlink(missing_ok=True)


def test_post_commit_cleanup_failure_does_not_report_update_failure(
    client, db_session, monkeypatch
):
    import main

    question = Question(content="原题", question_type="single_choice")
    db_session.add(question)
    db_session.commit()
    question_id = question.id

    monkeypatch.setattr(
        main,
        "delete_unreferenced_question_assets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")),
    )
    response = client.put(
        f"/api/questions/{question_id}",
        data=_payload(content="已成功更新"),
        headers=_headers(),
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Question, question_id).content == "已成功更新"


def test_post_commit_cleanup_failure_does_not_report_delete_failure(
    client, db_session, monkeypatch
):
    import main

    question = Question(content="待删除", question_type="single_choice")
    db_session.add(question)
    db_session.commit()
    question_id = question.id

    monkeypatch.setattr(
        main,
        "delete_unreferenced_question_assets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")),
    )
    response = client.delete(
        f"/api/questions/{question_id}",
        headers=_headers(),
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Question, question_id) is None


def test_create_succeeds_when_post_commit_export_scheduling_fails(
    client, db_session, monkeypatch
):
    import main

    def reject_background_task(_self, _function, *args, **kwargs):
        raise RuntimeError("simulated scheduler failure")

    monkeypatch.setattr(main.BackgroundTasks, "add_task", reject_background_task)

    response = client.post(
        "/api/questions",
        data=_payload(content="创建已提交"),
        headers=_headers(),
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.query(Question).filter_by(content="创建已提交").count() == 1


def test_update_succeeds_when_post_commit_export_scheduling_fails(
    client, db_session, monkeypatch
):
    import main

    question = Question(content="待更新", question_type="single_choice")
    db_session.add(question)
    db_session.commit()
    question_id = question.id

    def reject_background_task(_self, _function, *args, **kwargs):
        raise RuntimeError("simulated scheduler failure")

    monkeypatch.setattr(main.BackgroundTasks, "add_task", reject_background_task)

    response = client.put(
        f"/api/questions/{question_id}",
        data=_payload(content="更新已提交"),
        headers=_headers(),
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Question, question_id).content == "更新已提交"


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure_stage", ["refresh", "sequence", "serialize"])
def test_committed_question_write_returns_minimal_success_when_response_fails(
    client, db_session, monkeypatch, operation, failure_stage
):
    import main

    question_id = None
    if operation == "update":
        question = Question(content="原题", question_type="single_choice")
        db_session.add(question)
        db_session.commit()
        question_id = question.id

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"simulated post-commit {failure_stage} failure")

    if failure_stage == "refresh":
        monkeypatch.setattr(db_session, "refresh", fail)
    elif failure_stage == "sequence":
        monkeypatch.setattr(main, "get_seq_mapping", fail)
    else:
        monkeypatch.setattr(Question, "to_dict", fail)

    if operation == "create":
        response = client.post(
            "/api/questions",
            data=_payload(content="已提交新题"),
            headers=_headers(),
        )
    else:
        response = client.put(
            f"/api/questions/{question_id}",
            data=_payload(content="已提交更新"),
            headers=_headers(),
        )

    assert response.status_code == 200
    committed_id = response.json()["question"]["id"]
    assert response.json() == {
        "status": "success",
        "question": {"id": committed_id},
    }
    db_session.expire_all()
    stored = db_session.get(Question, committed_id)
    assert stored is not None
    assert stored.content == ("已提交新题" if operation == "create" else "已提交更新")


def test_asset_cleanup_loads_question_references_only_once(
    db_session, monkeypatch, tmp_path
):
    import main

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    first = upload_dir / "first.png"
    second = upload_dir / "second.png"
    _write_png(first)
    _write_png(second)
    calls = 0
    original = main._referenced_question_assets

    def count_reference_load(session):
        nonlocal calls
        calls += 1
        return original(session)

    monkeypatch.setattr(main, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(main, "UPLOAD_DIR_REL", "static/uploads")
    monkeypatch.setattr(main, "_referenced_question_assets", count_reference_load)

    removed = main.delete_unreferenced_question_assets(
        db_session,
        [
            "/static/uploads/first.png",
            "/static/uploads/second.png",
        ],
    )

    assert removed == 2
    assert calls == 1


def test_question_delete_cascades_paper_link_and_recalculates_total(
    client, db_session
):
    first = Question(content="试卷题一", question_type="single_choice")
    second = Question(content="试卷题二", question_type="single_choice")
    paper = Paper(title="关系约束测试", total_score=12)
    db_session.add_all([first, second, paper])
    db_session.flush()
    db_session.add_all(
        [
            PaperQuestion(
                paper_id=paper.id,
                question_id=first.id,
                order_index=1,
                score=5,
            ),
            PaperQuestion(
                paper_id=paper.id,
                question_id=second.id,
                order_index=2,
                score=7,
            ),
        ]
    )
    db_session.commit()
    first_id, paper_id = first.id, paper.id

    response = client.delete(
        f"/api/questions/{first_id}", headers=_headers()
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.query(PaperQuestion).filter_by(paper_id=paper_id).count() == 1
    assert db_session.get(Paper, paper_id).total_score == 7


def test_metadata_file_and_cache_are_restored_when_db_commit_fails(
    client, db_session, monkeypatch, tmp_path
):
    import main

    metadata_path = tmp_path / "custom_metadata.json"
    old_metadata = {
        "question_types": [{"value": "single_choice", "label": "单选题"}],
        "difficulties": [{"value": "medium", "label": "中等"}],
        "curriculum": {"必修一": {"1. 集合与常用逻辑用语": ["集合"]}},
    }
    old_text = json.dumps(old_metadata, ensure_ascii=False, indent=2)
    metadata_path.write_text(old_text, encoding="utf-8")
    monkeypatch.setattr(main, "METADATA_FILE", str(metadata_path))
    monkeypatch.setattr(main, "METADATA_CACHE", old_metadata)
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated commit failure")),
    )
    new_metadata = {
        **old_metadata,
        "question_types": [{"value": "detailed_answer", "label": "解答题"}],
    }

    response = client.post(
        "/api/config/metadata",
        json=new_metadata,
        headers=_headers(),
    )

    assert response.status_code == 500
    assert metadata_path.read_text(encoding="utf-8") == old_text
    assert main.METADATA_CACHE == old_metadata


def test_metadata_save_succeeds_when_post_commit_export_scheduling_fails(
    client, db_session, monkeypatch, tmp_path
):
    import main

    metadata_path = tmp_path / "custom_metadata.json"
    old_metadata = {
        "question_types": [{"value": "single_choice", "label": "单选题"}],
        "difficulties": [{"value": "medium", "label": "中等"}],
        "curriculum": {"必修一": {"第一章": ["集合"]}},
    }
    metadata_path.write_text(
        json.dumps(old_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(main, "METADATA_FILE", str(metadata_path))
    monkeypatch.setattr(main, "METADATA_CACHE", old_metadata)

    def reject_background_task(_self, _function, *args, **kwargs):
        raise RuntimeError("simulated scheduler failure")

    monkeypatch.setattr(main.BackgroundTasks, "add_task", reject_background_task)
    new_metadata = {
        **old_metadata,
        "question_types": [{"value": "detailed_answer", "label": "解答题"}],
    }

    response = client.post(
        "/api/config/metadata",
        json=new_metadata,
        headers=_headers(),
    )

    assert response.status_code == 200
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == new_metadata
    assert main.METADATA_CACHE == new_metadata
