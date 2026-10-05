"""Image lifecycle regressions; run in an isolated source checkout like other API tests.

The production ``main`` import initializes runtime storage, so these tests must not
be run against the user's live checkout/database. Every image fixture is unique.
"""

import io
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from mathbank.database import Question, QuestionCurriculum, get_db
from mathbank.task_manager import TaskManager


def _payload(**overrides):
    payload = {
        "content": "图片生命周期测试题",
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "第一章",
        "category_knowledge": "集合",
        "difficulty": "medium",
        "source": "测试",
        "answer_markdown": "答案",
        "image_paths": "[]",
    }
    payload.update(overrides)
    return payload


def _png_bytes(color="white"):
    data = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(data, format="PNG")
    return data.getvalue()


@pytest.fixture
def assets(client):
    import main

    paths = set()
    prefix = f"lifecycle-{uuid.uuid4().hex}"

    def write(name="figure", *, temporary=False, color="white"):
        directory = Path(main.TMP_UPLOAD_DIR if temporary else main.UPLOAD_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{prefix}-{name}.png"
        path = directory / filename
        path.write_bytes(_png_bytes(color))
        paths.add(path)
        paths.add(Path(main.UPLOAD_DIR) / filename)
        suffix = f"tmp/{filename}" if temporary else filename
        return path, f"/{main.UPLOAD_DIR_REL}/{suffix}"

    yield write
    for path in paths:
        path.unlink(missing_ok=True)


@pytest.fixture
def headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _age(path):
    old = time.time() - 7200
    os.utime(path, (old, old))


def _created(response):
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "success"
    return data


def test_uploaded_permanent_body_and_answer_images_are_registered(client, headers):
    import main

    paths = []
    try:
        urls = []
        for color in ("red", "blue"):
            uploaded = client.post(
                "/api/upload",
                files={"file": (f"{color}.png", _png_bytes(color), "image/png")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            url = uploaded.json()["file_path"]
            urls.append(url)
            paths.append(Path(main.UPLOAD_DIR) / Path(url).name)
        data = _created(client.post(
            "/api/questions",
            data=_payload(
                content=f"正文配图 ![题图]({urls[0]})",
                answer_markdown=f"解答配图 ![解析]({urls[1]})",
                image_paths="[]",
            ),
            headers=headers,
        ))
        assert set(data["question"]["image_paths"]) == set(urls)
        for url in urls:
            assert client.get(url).status_code == 200
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def test_missing_visible_permanent_image_rejected_but_code_literal_preserved(client, headers):
    import main

    missing = f"/{main.UPLOAD_DIR_REL}/lifecycle-missing-{uuid.uuid4().hex}.png"
    response = client.post(
        "/api/questions", data=_payload(content=f"题目 ![图]({missing})"), headers=headers,
    )
    assert response.status_code == 400
    literal = f"代码示例 `![图]({missing})`\n\n```text\n![图]({missing})\n```"
    data = _created(client.post(
        "/api/questions", data=_payload(content=literal), headers=headers,
    ))
    assert data["question"]["content"] == literal
    assert data["question"]["image_paths"] == []


@pytest.mark.parametrize("field", ["content", "answer_markdown"])
def test_startup_cleanup_preserves_legacy_body_only_references(client, db_session, assets, field):
    import main

    path, url = assets("legacy")
    question = Question(content="旧题", question_type="single_choice")
    setattr(question, field, f"保留正文插图 ![图]({url})")
    question.image_paths = []
    db_session.add(question)
    db_session.commit()
    original = path.read_bytes()
    _age(path)

    main.clean_orphaned_images()

    assert path.is_file()
    assert path.read_bytes() == original


@pytest.mark.parametrize("recover_with", ["get", "save"])
def test_old_unsaved_draft_image_recovers_after_startup_cleanup(client, headers, assets, recover_with):
    import main

    path, url = assets("draft")
    original = path.read_bytes()
    _age(path)
    main.clean_orphaned_images()
    assert not path.exists()

    if recover_with == "get":
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == original
    else:
        data = _created(client.post(
            "/api/questions", data=_payload(content=f"恢复草稿 ![图]({url})"), headers=headers,
        ))
        assert url in data["question"]["image_paths"]
    assert path.read_bytes() == original


def test_deleted_question_image_can_be_recovered_at_its_original_url(client, headers, assets):
    path, url = assets("deleted")
    original = path.read_bytes()
    data = _created(client.post(
        "/api/questions", data=_payload(content=f"待删题 ![图]({url})"), headers=headers,
    ))
    response = client.delete(f"/api/questions/{data['question']['id']}", headers=headers)
    assert response.status_code == 200
    assert not path.exists()
    restored = client.get(url)
    assert restored.status_code == 200
    assert restored.content == original
    assert path.read_bytes() == original


@pytest.mark.parametrize("recover_with", ["get", "save"])
def test_expired_task_temporary_image_remains_recoverable(client, headers, assets, recover_with):
    import main

    path, url = assets("expired-task", temporary=True)
    original = path.read_bytes()
    manager = TaskManager(
        max_workers=1, max_queue=0, terminal_ttl_seconds=0,
        temp_asset_cleanup=main._delete_task_temp_assets,
    )
    try:
        manager.create("expired", temp_assets=[url])
        manager.complete("expired")
        assert manager.cleanup() == 1
        assert not path.exists()
        if recover_with == "get":
            restored = client.get(url)
            assert restored.status_code == 200
            assert restored.content == original
        else:
            data = _created(client.post(
                "/api/questions", data=_payload(content=f"过期拆卷题 ![图]({url})"), headers=headers,
            ))
            permanent = data["asset_path_map"][url]
            assert permanent in data["question"]["image_paths"]
            assert client.get(permanent).content == original
        assert path.read_bytes() == original
    finally:
        manager.shutdown()


def test_explicit_discard_of_temp_crop_remains_recoverable(client, headers, assets):
    path, url = assets("discarded-crop", temporary=True)
    original = path.read_bytes()
    response = client.post("/api/ai/clear-temp-crops", json={"paths": [url]}, headers=headers)
    assert response.status_code == 200
    assert not path.exists()
    restored = client.get(url)
    assert restored.status_code == 200
    assert restored.content == original


def test_task_cleanup_keeps_legacy_saved_tmp_image_in_place_for_backup(
    client, db_session, headers, assets,
):
    path, url = assets("legacy-saved-temp", temporary=True)
    original = path.read_bytes()
    # Older records may reference tmp directly and omit the image-path array.
    question = Question(content=f"旧题正文 ![图]({url})", question_type="single_choice")
    question.image_paths = []
    db_session.add(question)
    db_session.commit()

    response = client.post("/api/ai/clear-temp-crops", json={"paths": [url]}, headers=headers)

    assert response.status_code == 200
    # Check before any HTTP image read can restore an incorrectly retired file:
    # full backup reads the persisted path directly and needs it still present.
    assert path.is_file()
    assert path.read_bytes() == original
    db_session.expire_all()
    assert url in db_session.get(Question, question.id).content


def test_shared_temporary_image_can_be_saved_by_two_cards_and_reimported(client, headers, assets):
    path, url = assets("shared", temporary=True)
    original = path.read_bytes()
    saved = []
    for title in ("第一题", "第二题", "第一题"):
        data = _created(client.post(
            "/api/questions",
            data=_payload(content=f"{title} ![共用图]({url})", image_paths=json.dumps([url])),
            headers=headers,
        ))
        permanent = data["asset_path_map"][url]
        assert "/tmp/" not in permanent
        assert permanent in data["question"]["content"]
        assert permanent in data["question"]["image_paths"]
        assert client.get(permanent).content == original
        assert path.read_bytes() == original
        saved.append(data["question"]["id"])
    assert len(set(saved)) == 3


@pytest.mark.parametrize("already_saved", [False, True])
def test_failed_copy_save_keeps_source_and_does_not_remove_a_reused_target(
    client, db_session, headers, assets, already_saved,
):
    import main

    path, url = assets("rollback", temporary=True)
    permanent = Path(main.UPLOAD_DIR) / path.name
    original = path.read_bytes()
    if already_saved:
        _created(client.post(
            "/api/questions", data=_payload(content=f"已保存 ![图]({url})"), headers=headers,
        ))
        assert permanent.read_bytes() == original

    def reject_curriculum(session, _flush_context, _instances):
        if any(isinstance(item, QuestionCurriculum) for item in session.new):
            raise RuntimeError("simulated curriculum write failure")

    event.listen(db_session, "before_flush", reject_curriculum)
    try:
        response = client.post(
            "/api/questions", data=_payload(content=f"失败题 ![图]({url})"), headers=headers,
        )
    finally:
        event.remove(db_session, "before_flush", reject_curriculum)
    assert response.status_code == 400
    assert path.read_bytes() == original
    if already_saved:
        assert permanent.read_bytes() == original
    else:
        assert not permanent.exists()
    assert db_session.query(Question).count() == int(already_saved)


def test_same_filename_with_different_content_never_overwrites_destination(client, headers, assets):
    path, url = assets("conflict", temporary=True, color="red")
    destination, _ = assets("conflict", color="blue")
    source_bytes, destination_bytes = path.read_bytes(), destination.read_bytes()
    response = client.post(
        "/api/questions", data=_payload(content=f"冲突图片 ![图]({url})"), headers=headers,
    )
    assert response.status_code == 400
    assert path.read_bytes() == source_bytes
    assert destination.read_bytes() == destination_bytes


def test_concurrent_delete_and_create_preserve_the_new_questions_shared_image(
    client, db_session, headers, assets,
):
    import main

    path, url = assets("concurrent")
    original = path.read_bytes()
    first = _created(client.post(
        "/api/questions", data=_payload(content=f"即将删除 ![图]({url})"), headers=headers,
    ))
    first_id = first["question"]["id"]
    sessions = sessionmaker(bind=db_session.get_bind())

    def independent_session():
        with sessions() as session:
            yield session

    previous_override = main.app.dependency_overrides[get_db]
    main.app.dependency_overrides[get_db] = independent_session
    barrier = threading.Barrier(2)

    def remove_old():
        barrier.wait(timeout=3)
        return client.delete(f"/api/questions/{first_id}", headers=headers)

    def create_new():
        barrier.wait(timeout=3)
        return client.post(
            "/api/questions", data=_payload(content=f"同时新建 ![图]({url})"), headers=headers,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            deletion, creation = pool.submit(remove_old), pool.submit(create_new)
            assert deletion.result(timeout=10).status_code == 200
            new = _created(creation.result(timeout=10))
    finally:
        main.app.dependency_overrides[get_db] = previous_override
    db_session.expire_all()
    # SQLite may reuse the deleted highest row ID when deletion wins the race.
    assert db_session.query(Question).filter(Question.content.contains("即将删除")).count() == 0
    stored = db_session.get(Question, new["question"]["id"])
    assert stored is not None
    assert url in stored.image_paths
    assert client.get(url).content == original


def test_retain_endpoint_does_not_modify_completed_evidence(client, headers, monkeypatch):
    import main

    manager = TaskManager(max_workers=1, max_queue=0)
    monkeypatch.setattr(main, "DOCUMENT_TASKS", manager)
    try:
        task_id = str(uuid.uuid4())
        manager.create(task_id, document_type="pdf")
        manager.complete(task_id, data=[{"content": "题目", "source_review": {"proof": "keep"}}])
        before = manager.snapshot(task_id)
        response = client.post(f"/api/tasks/{task_id}/retain", headers=headers)
        assert response.status_code == 200, response.text
        assert manager.snapshot(task_id) == before
        cancelled = str(uuid.uuid4())
        manager.create(cancelled)
        manager.cancel(cancelled)
        ended = manager.snapshot(cancelled)
        rejected = client.post(f"/api/tasks/{cancelled}/retain", headers=headers)
        assert rejected.status_code >= 400
        assert manager.snapshot(cancelled) == ended
        missing = client.post(f"/api/tasks/{uuid.uuid4()}/retain", headers=headers)
        assert missing.status_code >= 400
    finally:
        manager.shutdown()


def test_tikz_assets_references_and_inline_layouts_follow_promoted_urls(client, db_session, headers, assets):
    urls = {}
    for name in ("content-render", "content-reference", "answer-render", "answer-reference", "inline"):
        _path, urls[name] = assets(name, temporary=True)
    code = r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"
    content_asset = {
        "id": "content_test", "image_path": urls["content-render"], "tikz_code": code,
        "reference_image_path": urls["content-reference"], "instruction": "题干绘图",
    }
    answer_asset = {
        "id": "answer_test", "image_path": urls["answer-render"], "tikz_code": code,
        "reference_image_path": urls["answer-reference"], "instruction": "解析绘图",
    }
    data = _created(client.post(
        "/api/questions",
        data=_payload(
            content=f"![正文]({urls['inline']})\n\n正文后续文字\n\n![TikZ]({urls['content-render']})",
            answer_markdown=f"解析 ![TikZ]({urls['answer-render']})",
            image_paths=json.dumps(list(urls.values())),
            content_tikz_assets=json.dumps([content_asset]),
            answer_tikz_assets=json.dumps([answer_asset]),
            image_layouts=json.dumps({urls["inline"]: {"align": "left", "size": "small"}}),
        ),
        headers=headers,
    ))
    mapping, question = data["asset_path_map"], data["question"]
    for old in urls.values():
        assert mapping[old] != old
        assert client.get(mapping[old]).status_code == 200
    assert question["content_tikz_assets"] == [{
        **content_asset,
        "image_path": mapping[urls["content-render"]],
        "reference_image_path": mapping[urls["content-reference"]],
    }]
    assert question["answer_tikz_assets"] == [{
        **answer_asset,
        "image_path": mapping[urls["answer-render"]],
        "reference_image_path": mapping[urls["answer-reference"]],
    }]
    assert question["tikz_reference_image_path"] == mapping[urls["content-reference"]]
    assert question["image_layouts"] == {mapping[urls["inline"]]: {"align": "left", "size": "small"}}
    stored = db_session.get(Question, question["id"])
    assert set(stored.image_paths) == {mapping[path] for path in urls.values()}
    assert mapping[urls["content-reference"]] not in question["image_paths"]
    assert mapping[urls["answer-reference"]] not in question["image_paths"]
