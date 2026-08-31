import json
from pathlib import Path
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from PIL import Image

from mathbank.database import Base, Question, QuestionFingerprint
from mathbank.question_duplicate_service import (
    find_indexed_candidates,
    recall_question_ids,
    select_visible_question_images,
    tikz_signatures,
    upsert_question_fingerprint,
    visible_image_signatures,
    backfill_missing_fingerprints,
)
from mathbank.question_duplicates import build_question_fingerprint


def _headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _question_payload(content: str, **overrides):
    payload = {
        "content": content,
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "第一章",
        "category_knowledge": "集合",
        "difficulty": "normal",
        "source": "查重测试",
        "answer_markdown": "答案",
        "review": "",
        "image_paths": "[]",
    }
    payload.update(overrides)
    return payload


def _create(client, content: str, **overrides):
    return client.post(
        "/api/questions",
        data=_question_payload(content, **overrides),
        headers=_headers(),
    )


def _check(client, items):
    return client.post(
        "/api/questions/check-duplicates",
        json={"items": items, "max_candidates": 5},
        headers=_headers(),
    )


def test_question_write_persists_rebuildable_fingerprint(client, db_session):
    created = _create(client, "设 $x=1$，求 $x+1$。")

    assert created.status_code == 200
    question_id = created.json()["question"]["id"]
    stored = db_session.query(QuestionFingerprint).filter_by(
        question_id=question_id
    ).one()
    assert len(stored.exact_hash) == 64
    assert len(stored.simhash_hex) == 32
    assert stored.status == "ready"


def test_check_finds_safe_latex_normalization_without_scanning_answers(
    client, db_session
):
    created = _create(
        client,
        r"已知 $\dfrac{x}{2}\leqslant 1$，求 $x$ 的取值范围。",
        answer_markdown="原解析",
    )
    assert created.status_code == 200

    checked = _check(
        client,
        [
            {
                "client_key": "latex-equivalent",
                "content": r"已知 $\frac{x}{2}\le 1$ ，求 $x$ 的取值范围。",
                "answer_markdown": "另一份解析",
                "question_type": "single_choice",
                "image_paths": [],
                "content_tikz_assets": [],
            }
        ],
    )

    assert checked.status_code == 200
    result = checked.json()["items"][0]
    assert result["level"] == "exact"
    assert result["candidates"][0]["id"] == created.json()["question"]["id"]
    assert "SAFE_NORMALIZATION_ONLY" in result["candidates"][0]["reasons"]
    assert "ANSWER_DIFF" in result["candidates"][0]["reasons"]
    assert checked.json()["index"]["ready"] is True


def test_batch_check_finds_batch_local_duplicate_only_when_requested(client):
    assert _create(client, "题库中的无关题 $a=9$。").status_code == 200

    checked = _check(
        client,
        [
            {
                "client_key": "paper-1",
                "content": "同一试卷题 $x^2=4$。",
                "question_type": "fill_in_blank",
            },
            {
                "client_key": "paper-2",
                "content": "同一试卷题  $x^2=4$ 。",
                "question_type": "fill_in_blank",
            },
            {
                "client_key": "paper-3",
                "content": "同一试卷题 $x^2=9$。",
                "question_type": "fill_in_blank",
            },
        ],
    )

    assert checked.status_code == 200
    by_key = {item["client_key"]: item for item in checked.json()["items"]}
    assert by_key["paper-1"]["batch_matches"]
    assert by_key["paper-2"]["batch_matches"]
    assert by_key["paper-1"]["batch_matches"][0]["other_client_key"] == "paper-2"
    assert by_key["paper-3"]["level"] in {"possible_variant", "none"}


def test_save_time_recheck_requires_explicit_independent_override(client, db_session):
    content = "并发复查题 $0<x<1$。"
    assert _create(client, content).status_code == 200
    checked = _check(
        client,
        [
            {
                "client_key": "candidate",
                "content": content,
                "answer_markdown": "答案",
                "question_type": "single_choice",
            }
        ],
    )
    snapshot = checked.json()["items"][0]["snapshot_hash"]

    blocked = _create(client, content, duplicate_snapshot_hash=snapshot)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "duplicate_review_required"
    assert db_session.query(Question).count() == 1

    allowed = _create(
        client,
        content,
        duplicate_snapshot_hash=snapshot,
        duplicate_override="independent",
    )
    assert allowed.status_code == 200
    assert db_session.query(Question).count() == 2
    assert db_session.query(QuestionFingerprint).count() == 2


def test_stale_duplicate_snapshot_never_saves_changed_content(client, db_session):
    original = "快照原题 $x=1$。"
    checked = _check(
        client,
        [{"client_key": "stale", "content": original, "question_type": "single_choice"}],
    )
    snapshot = checked.json()["items"][0]["snapshot_hash"]

    response = _create(
        client,
        "快照已修改 $x=2$。",
        duplicate_snapshot_hash=snapshot,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_snapshot_stale"
    assert db_session.query(Question).count() == 0


def test_missing_precheck_snapshot_keeps_legacy_and_degraded_saves_available(
    client, db_session
):
    content = "旧客户端兼容题 $y=3$。"

    assert _create(client, content).status_code == 200
    assert _create(client, content).status_code == 200
    assert db_session.query(Question).count() == 2


def test_fingerprint_generation_failure_does_not_block_question_save(
    client, db_session, monkeypatch
):
    import main

    monkeypatch.setattr(
        main,
        "build_prepared_question_fingerprint",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated")),
    )

    response = _create(client, "指纹故障仍应保存 $x=7$。")

    assert response.status_code == 200
    assert "warning" in response.json()
    assert db_session.query(Question).count() == 1
    assert db_session.query(QuestionFingerprint).count() == 1


def test_edit_check_excludes_the_current_question(client):
    created = _create(client, "编辑自身排除题 $z=5$。")
    question_id = created.json()["question"]["id"]

    checked = _check(
        client,
        [
            {
                "client_key": "editor",
                "content": "编辑自身排除题 $z=5$。",
                "question_type": "single_choice",
                "exclude_id": question_id,
            }
        ],
    )

    assert checked.status_code == 200
    assert checked.json()["items"][0]["level"] == "none"
    assert checked.json()["items"][0]["candidates"] == []


def test_lsh_recall_uses_eight_independent_composite_indexes(db_session):
    fingerprint = build_question_fingerprint(
        {"content": "索引计划题 $a^2+b^2=c^2$", "question_type": "detailed_answer"}
    )
    captured = []

    def capture_statement(_conn, _cursor, statement, parameters, _context, _many):
        if "UNION ALL" in statement and "question_fingerprints" in statement:
            captured.append((statement, parameters))

    event.listen(db_session.bind, "before_cursor_execute", capture_statement)
    try:
        recall_question_ids(db_session, fingerprint)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture_statement)

    assert len(captured) == 1
    statement, parameters = captured[0]
    assert statement.count("UNION ALL") == 7
    raw_connection = db_session.connection().connection
    plan = raw_connection.execute(
        "EXPLAIN QUERY PLAN " + statement,
        parameters,
    ).fetchall()
    details = "\n".join(str(row[3]) for row in plan)
    for band_no in range(8):
        assert f"idx_question_fingerprints_band{band_no}" in details


def test_visible_image_signature_ignores_png_compression_and_filename(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    image = Image.new("RGB", (8, 8), "white")
    image.putpixel((3, 4), (10, 20, 30))
    image.save(first, format="PNG", compress_level=0)
    image.save(second, format="PNG", compress_level=9)

    signatures = visible_image_signatures(
        ["/static/uploads/first.png", "/static/uploads/second.png"],
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
    )

    assert len(signatures) == 2
    assert signatures[0] == signatures[1]


def test_duplicate_snapshot_survives_temp_image_promotion(client):
    import main

    temp_image = Path(main.TMP_UPLOAD_DIR) / "duplicate-snapshot-promotion.png"
    permanent_image = Path(main.UPLOAD_DIR) / temp_image.name
    permanent_image.unlink(missing_ok=True)
    temp_image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (6, 6), "white").save(temp_image, format="PNG")
    temp_reference = f"/{main.UPLOAD_DIR_REL}/tmp/{temp_image.name}"
    content = f"配图快照题\n![配图]({temp_reference})"
    try:
        checked = _check(
            client,
            [
                {
                    "client_key": "temp-image",
                    "content": content,
                    "answer_markdown": "答案",
                    "question_type": "detailed_answer",
                    "image_paths": [temp_reference],
                }
            ],
        )
        snapshot = checked.json()["items"][0]["snapshot_hash"]
        saved = _create(
            client,
            content,
            question_type="detailed_answer",
            image_paths=json.dumps([temp_reference]),
            duplicate_snapshot_hash=snapshot,
        )

        assert saved.status_code == 200
        assert "/tmp/" not in saved.json()["question"]["content"]
        assert permanent_image.is_file()
    finally:
        temp_image.unlink(missing_ok=True)
        permanent_image.unlink(missing_ok=True)


def test_tikz_signature_preserves_escaped_percent_content():
    first = tikz_signatures(
        [{"tikz_code": r"\node {50\%}; \draw (0,0)--(1,1); % comment"}]
    )
    second = tikz_signatures(
        [{"tikz_code": r"\node {50\%}; \draw (0,0)--(9,9); % comment"}]
    )

    assert first != second


def test_duplicate_snapshot_survives_temp_answer_image_promotion(client):
    import main

    temp_image = Path(main.TMP_UPLOAD_DIR) / "duplicate-answer-promotion.png"
    permanent_image = Path(main.UPLOAD_DIR) / temp_image.name
    permanent_image.unlink(missing_ok=True)
    temp_image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (6, 6), "blue").save(temp_image, format="PNG")
    temp_reference = f"/{main.UPLOAD_DIR_REL}/tmp/{temp_image.name}"
    answer = f"解析配图\n![答案图]({temp_reference})"
    try:
        checked = _check(
            client,
            [
                {
                    "client_key": "temp-answer-image",
                    "content": "解析图片快照题 $x=1$",
                    "answer_markdown": answer,
                    "question_type": "detailed_answer",
                    "image_paths": [temp_reference],
                }
            ],
        )
        snapshot = checked.json()["items"][0]["snapshot_hash"]
        saved = _create(
            client,
            "解析图片快照题 $x=1$",
            question_type="detailed_answer",
            answer_markdown=answer,
            image_paths=json.dumps([temp_reference]),
            duplicate_snapshot_hash=snapshot,
        )

        assert saved.status_code == 200
        assert "/tmp/" not in saved.json()["question"]["answer_markdown"]
        assert permanent_image.is_file()
    finally:
        temp_image.unlink(missing_ok=True)
        permanent_image.unlink(missing_ok=True)


def test_atomic_fingerprint_upsert_is_idempotent(db_session):
    question = Question(content="原子 upsert $x=1$", question_type="detailed_answer")
    db_session.add(question)
    db_session.flush()
    fingerprint = build_question_fingerprint(
        {"content": question.content, "question_type": question.question_type}
    )
    db_session.add(
        QuestionFingerprint(
            question_id=question.id,
            fingerprint_version=1,
            exact_hash="legacy",
        )
    )
    db_session.flush()

    upsert_question_fingerprint(db_session, question, fingerprint)
    upsert_question_fingerprint(db_session, question, fingerprint)
    db_session.commit()

    assert db_session.query(QuestionFingerprint).count() == 1
    assert (
        db_session.query(QuestionFingerprint).one().fingerprint_version
        == fingerprint.fingerprint_version
    )


def test_candidate_refinement_is_bounded_before_expensive_rebuild(
    db_session, tmp_path, monkeypatch
):
    import mathbank.question_duplicate_service as service

    questions = [
        Question(content="密集重复题 $x=1$", question_type="detailed_answer")
        for _ in range(100)
    ]
    db_session.add_all(questions)
    db_session.flush()
    fingerprint = build_question_fingerprint(
        {"content": "密集重复题 $x=1$", "question_type": "detailed_answer"}
    )
    for question in questions:
        upsert_question_fingerprint(db_session, question, fingerprint)
    db_session.commit()

    original = service.fingerprint_for_question
    calls = {"count": 0}

    def counted(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "fingerprint_for_question", counted)
    candidates = find_indexed_candidates(
        db_session,
        fingerprint,
        uploads_dir=tmp_path,
        url_prefix="static/uploads",
        limit=5,
    )

    assert len(candidates) == 5
    assert calls["count"] <= 40


def test_wide_lsh_bucket_reports_incomplete_recall(db_session):
    query_fingerprint = build_question_fingerprint(
        {"content": "宽分桶查询 $x=1$", "question_type": "detailed_answer"}
    )
    questions = [
        Question(content=f"宽分桶候选 {index}", question_type="detailed_answer")
        for index in range(60)
    ]
    db_session.add_all(questions)
    db_session.flush()
    for index, question in enumerate(questions):
        db_session.add(
            QuestionFingerprint(
                question_id=question.id,
                fingerprint_version=query_fingerprint.fingerprint_version,
                exact_hash=f"{index:064x}",
                simhash_hex=query_fingerprint.simhash_hex,
                token_count=query_fingerprint.token_count,
                **{
                    f"band{band_no}": band_value
                    for band_no, band_value in enumerate(query_fingerprint.bands)
                },
            )
        )
    db_session.commit()
    diagnostics = {}

    recalled = recall_question_ids(
        db_session,
        query_fingerprint,
        limit=40,
        diagnostics=diagnostics,
    )

    assert len(recalled) == 40
    assert diagnostics["index_complete"] is False
    assert diagnostics["truncated_band_count"] == 8


def test_visible_selection_keeps_ordinary_image_alongside_tikz_render(tmp_path):
    for name, color in (("ordinary.png", "red"), ("tikz.png", "white")):
        Image.new("RGB", (5, 5), color).save(tmp_path / name, format="PNG")
    ordinary = "/static/uploads/ordinary.png"
    tikz = "/static/uploads/tikz.png"
    assets = [
        {
            "id": "tikz_1",
            "image_path": tikz,
            "tikz_code": r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}",
        }
    ]

    selected = select_visible_question_images(
        f"配图题\n![TikZ]({tikz})",
        "",
        [ordinary, tikz],
        assets,
    )

    assert selected == [ordinary, tikz]


def test_word_private_character_migration_invalidates_derived_fingerprint(
    db_session, monkeypatch
):
    from mathbank import database
    from scripts import migrate_word_math_private_chars as migration

    question = Question(
        content="迁移题 \uec07x\uec08",
        question_type="detailed_answer",
    )
    db_session.add(question)
    db_session.flush()
    fingerprint = build_question_fingerprint(
        {"content": question.content, "question_type": question.question_type}
    )
    upsert_question_fingerprint(db_session, question, fingerprint)
    db_session.commit()
    monkeypatch.setattr(migration, "SessionLocal", database.SessionLocal)
    monkeypatch.setattr(migration, "export_database_to_files", lambda *_args: None)

    result = migration.migrate_database_word_math_private_chars()

    assert result["changed_questions"] == [question.id]
    assert db_session.query(QuestionFingerprint).count() == 0


def test_concurrent_backfill_uses_atomic_upsert_without_primary_key_failure(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    seed = session_factory()
    seed.add_all(
        [
            Question(content=f"并发回填题 {index}", question_type="detailed_answer")
            for index in range(30)
        ]
    )
    seed.commit()
    seed.close()
    barrier = threading.Barrier(2)
    errors = []

    def worker():
        session = session_factory()
        try:
            barrier.wait()
            backfill_missing_fingerprints(
                session,
                uploads_dir=tmp_path,
                url_prefix="static/uploads",
                limit=30,
            )
        except Exception as exc:  # pragma: no cover - asserted through errors
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    verify = session_factory()
    try:
        assert errors == []
        assert verify.query(QuestionFingerprint).count() == 30
    finally:
        verify.close()
        engine.dispose()
