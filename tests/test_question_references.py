"""Reference identity, literal protection and schema upgrades stay isolated."""

import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from mathbank import backup, database, db_migrations
from mathbank.database import Question, QuestionReference, QuestionSourceLabel
from mathbank.question_references import (
    export_reference_text, extract_references, extract_source_labels,
    normalize_exchange_reference_data, reference_payloads,
    replace_exchange_reference_data, serialize_question_reference_data,
    sync_question_references, sync_question_source_labels,
)


def test_reference_rows_and_numbers_share_a_real_read_snapshot(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reference-race.db'}")
    database.Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
    sessions = sessionmaker(bind=engine)
    with sessions() as seed:
        source = Question(id=1, content="来源", source="读快照教材 | 例1.1", answer_markdown="见例1.2。")
        target = Question(id=2, content="原绑定的目标题", source="读快照教材 | 例1.2")
        seed.add_all([source, target])
        seed.flush()
        for q in (source, target):
            sync_question_source_labels(seed, q)
        sync_question_references(seed, source)
        seed.commit()
    replaced = []

    def replace_target_before_numbers(_connection, _cursor, statement, _parameters, _context, _many):
        if "row_number()" not in statement.lower() or replaced:
            return
        replaced.append(True)
        with sessions() as writer:
            writer.delete(writer.get(Question, 2))
            writer.flush()
            writer.add(Question(id=2, content="后来复用 ID 的不同题", source="读快照教材 | 例1.2"))
            writer.commit()

    event.listen(engine, "before_cursor_execute", replace_target_before_numbers)
    try:
        with sessions() as reader:
            source = reader.get(Question, 1)
            first = reference_payloads(reader, [source], seq_mapping={2: 999})[1][0]
            assert replaced and first["status"] == "resolved" and first["seq_num"] == 2
            assert reader.get(Question, 2).content == "原绑定的目标题"
            reader.rollback()
            reader.expire_all()
            second = reference_payloads(reader, [reader.get(Question, 1)])[1][0]
            assert second["status"] == "target_deleted" and second["target_question_id"] is None
    finally:
        event.remove(engine, "before_cursor_execute", replace_target_before_numbers)
        engine.dispose()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'references.db'}")
    database.Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _question(db, source, answer="", content="题干", qid=None):
    question = Question(content=content, answer_markdown=answer, source=source)
    if qid is not None:
        question.id = qid
    db.add(question)
    db.flush()
    sync_question_source_labels(db, question)
    sync_question_references(db, question)
    db.commit()
    return question


def test_parser_masks_literals_math_images_attributes_and_keeps_unicode_offsets():
    text = ("😀参考例 2．6及2.7。`例2.8` https://x.test/例2.9 "
            "![例2.10](/static/uploads/例2.11.png) $\\text{例2.12}$ "
            "\\(例2.13\\) \\begin{tikzpicture}例2.14\\end{tikzpicture} "
            '<span title="例2.15">参照变式题 1.2.3</span> '
            "\\url{例2.16} \\begin{equation}例2.17\\end{equation}")
    references = extract_references(text, "  某书  ")
    assert [item["label_key"] for item in references] == ["例2.6", "例2.7", "变式1.2.3"]
    assert references[0]["start"] == text.index("例 2．6")
    assert all(text[item["start"]:item["end"]] == item["text"] for item in references)
    assert all(item["book_key"] == "某书" for item in references)
    assert extract_references("先见例2.6，随后$例2.7")[-1]["label_key"] == "例2.6"


def test_parser_reports_ranges_placeholders_and_incomplete_numbers_without_guessing():
    items = extract_references("见例2.6～2.9、例x和变式1。再见例2.6和2.7", "某书")
    assert items[0]["is_range"] and not items[0]["reason"]
    assert items[0]["candidate_label_keys"] == ["例2.6", "例2.7", "例2.8", "例2.9"]
    assert [item["label_key"] for item in items] == ["例2.6", "例2.7", "例2.8", "例2.9", "", "", "例2.6", "例2.7"]
    assert all(item["reason"] for item in items[4:6])
    unprovable = extract_references("例2.6～3.1", "某书")
    assert len(unprovable) == 1 and unprovable[0]["reason"] == "range_requires_multiple_targets"


def test_parser_allows_actual_ascii_sentence_periods_without_truncating_numbers():
    text = "比如例2.7. 正确的解析见例6.18. 应用见变式3.32.1. 例3.41～例3.45."
    items = extract_references(text, "某书")
    assert [item["label_key"] for item in items[:3]] == ["例2.7", "例6.18", "变式3.32.1"]
    assert all(not item["reason"] and not item["text"].endswith(".") for item in items[:3])
    assert items[3]["is_range"] and not items[3]["reason"]
    assert items[3]["candidate_label_keys"] == [f"例3.{number}" for number in range(41, 46)]
    malformed = extract_references("见例2.6..7", "某书")
    assert len(malformed) == 1 and malformed[0]["label_key"] == ""


def test_source_labels_require_explicit_book_structure_and_keep_merged_numbers():
    assert extract_source_labels("某考试 例2.6") == []
    assert extract_source_labels("某考试 | 2026年统考") == []
    labels = extract_source_labels(" 某书  | 例题 02．06 / 例2.7、例2.6 / 变式题1.1.2")
    assert [(item["book_key"], item["label_key"]) for item in labels] == [
        ("某书", "例2.6"), ("某书", "例2.7"), ("某书", "变式1.1.2")]


def test_identity_survives_sequence_change_target_label_edit_and_source_movement(db):
    first = _question(db, "某书 | 例1.1", qid=3)
    target = _question(db, "某书 | 例2.6 / 例2.7", qid=8)
    source = _question(db, "某书 | 例3.1", "参考例2.6和2.7。", qid=11)
    before = reference_payloads(db, [source])[source.id]
    assert [(item["target_question_id"], item["seq_num"]) for item in before] == [(8, 2), (8, 2)]
    db.delete(first)
    target.source = "另一来源说明"
    sync_question_source_labels(db, target)
    source.answer_markdown = "😀引言。" + source.answer_markdown
    sync_question_references(db, source)
    db.commit()
    after = reference_payloads(db, [source])[source.id]
    assert [(item["target_question_id"], item["seq_num"], item["status"]) for item in after] == [(8, 1, "resolved"), (8, 1, "resolved")]
    assert source.answer_markdown[after[0]["start"]:after[0]["end"]] == "例2.6"


def test_deleted_maximum_id_and_replacement_source_label_cannot_reconnect(db):
    source = _question(db, "某书 | 例1.1", "见例2.6。", qid=1)
    target = _question(db, "某书 | 例2.6", qid=2)
    # Adding the target permanently resolves the prior unresolved occurrence.
    assert reference_payloads(db, [source])[1][0]["status"] == "resolved"
    db.delete(target)
    db.commit()
    replacement = _question(db, "某书 | 例2.6")
    assert replacement.id == 2
    source.answer_markdown = "😀" + source.answer_markdown
    sync_question_references(db, source)
    db.commit()
    item = reference_payloads(db, [source])[1][0]
    assert item["status"] == "target_deleted" and item["target_question_id"] is None
    assert db.query(QuestionReference).one().resolution_state == "resolved"
    db.delete(source)
    db.commit()
    assert db.query(QuestionReference).count() == 0
    assert db.query(QuestionSourceLabel).count() == 1


def test_ambiguous_same_book_is_unresolved_other_book_does_not_collide(db):
    _question(db, "甲书 | 例2.6")
    _question(db, "甲书 | 例2.6")
    target_b = _question(db, "乙书 | 例2.6")
    source_a = _question(db, "甲书 | 例3.1", "见例2.6。")
    source_b = _question(db, "乙书 | 例3.1", "见例2.6。")
    a = reference_payloads(db, [source_a])[source_a.id][0]
    b = reference_payloads(db, [source_b])[source_b.id][0]
    assert a["status"] == "unresolved" and a["reason"] == "multiple_source_targets"
    assert b["target_question_id"] == target_b.id and b["status"] == "resolved"


def test_payload_detects_stale_fields_and_export_changes_copy_only(db):
    target = _question(db, "某书 | 例2.6")
    source = _question(db, "某书 | 例3.1", "由例2.6得证。")
    payload = {"id": source.id, "answer_markdown": source.answer_markdown,
               "question_references": reference_payloads(db, [source])[source.id]}
    exported = export_reference_text(payload)
    assert exported["answer_markdown"] == "由题库第 1 题（原例2.6）得证。"
    assert payload["answer_markdown"] == source.answer_markdown == "由例2.6得证。"
    source.answer_markdown = "后来改文，由例2.6得证。"
    db.commit()  # Deliberately bypass sync, exercising fail-closed reads.
    assert reference_payloads(db, [source])[source.id][0]["status"] == "stale"
    assert target.id == 1


def test_exchange_validates_evidence_rebinds_by_book_label_and_preserves_tombstones(db):
    target = _question(db, "某书 | 例2.6")
    source = _question(db, "某书 | 例3.1", "见例2.6。")
    serialized = serialize_question_reference_data(db, [source.id])[source.id]
    serialized["question_references"][0]["target_question_id"] = 99999
    replace_exchange_reference_data(db, source, serialized)
    db.commit()
    assert reference_payloads(db, [source])[source.id][0]["target_question_id"] == target.id
    assert normalize_exchange_reference_data(source, {}) == {}
    assert normalize_exchange_reference_data(source, {"question_references": []}) == {"question_references": []}
    serialized["question_references"][0]["start"] += 1
    with pytest.raises(ValueError, match="正文证据"):
        normalize_exchange_reference_data(source, serialized)
    deleted = serialize_question_reference_data(db, [source.id])[source.id]
    deleted["question_references"][0].update(status="target_deleted", target_question_id=None)
    replace_exchange_reference_data(db, source, deleted)
    db.commit()
    assert reference_payloads(db, [source])[source.id][0]["status"] == "target_deleted"


def test_range_members_group_for_export_and_individual_deleted_targets_stay_deleted(db):
    targets = [_question(db, f"某书 | 例2.{number}") for number in range(6, 9)]
    source = _question(db, "某书 | 例3.1", "依据例2.6～例2.8。")
    items = reference_payloads(db, [source])[source.id]
    assert [item["target_question_id"] for item in items] == [target.id for target in targets]
    assert len({(item["start"], item["end"], item["text"]) for item in items}) == 1
    payload = {"answer_markdown": source.answer_markdown, "question_references": items}
    assert export_reference_text(payload)["answer_markdown"] == "依据题库第 1、2、3 题（原例2.6～例2.8）。"
    portable = serialize_question_reference_data(db, [source.id])[source.id]
    assert len(normalize_exchange_reference_data(source, portable)["question_references"]) == 3
    db.delete(targets[1])
    db.commit()
    source.answer_markdown = "😀" + source.answer_markdown
    sync_question_references(db, source)
    db.commit()
    refreshed = reference_payloads(db, [source])[source.id]
    assert [item["status"] for item in refreshed] == ["resolved", "target_deleted", "resolved"]
    assert all(item["is_range"] and len(item["candidate_label_keys"]) == 3 for item in refreshed)


def test_v12_upgrade_is_backup_first_and_creates_empty_tables(tmp_path, monkeypatch):
    path = tmp_path / "legacy-v12.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        database.Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE question_references")
            connection.exec_driver_sql("DROP TABLE question_source_labels")
            connection.exec_driver_sql("PRAGMA user_version=12")
            connection.exec_driver_sql("INSERT INTO questions(id,content,source,answer_markdown) VALUES(7,'原题','某书 | 例2.6','见例2.6')")
        monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
        result = db_migrations.migrate_database(engine)
        assert result["from_version"] == 12 and result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
        assert result["added_question_source_labels"] == result["added_question_references"] == 1
        with sqlite3.connect(result["backup"]) as saved:
            assert saved.execute("PRAGMA user_version").fetchone()[0] == 12
            assert saved.execute("SELECT content,source,answer_markdown FROM questions").fetchone() == ("原题", "某书 | 例2.6", "见例2.6")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM question_references").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM question_source_labels").scalar_one() == 0
            db_migrations._validate_question_reference_schema(connection)
        assert db_migrations.migrate_database(engine)["from_version"] == db_migrations.LATEST_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_v13_schema_rejects_missing_index_and_checks_foreign_key_actions(db):
    with db.bind.connect() as connection:
        db_migrations._validate_question_reference_schema(connection)
        keys = connection.exec_driver_sql("PRAGMA foreign_key_list(question_references)").fetchall()
        assert {row[3]: row[6] for row in keys} == {"source_question_id": "CASCADE", "target_question_id": "SET NULL"}
    db.execute(database.text("DROP INDEX idx_question_source_labels_lookup"))
    db.commit()
    with db.bind.connect() as connection:
        with pytest.raises(RuntimeError, match="索引"):
            db_migrations._validate_question_reference_schema(connection)


def test_v13_full_backup_preserves_reference_tables_and_old_counts_remain_compatible(tmp_path):
    path = tmp_path / "archive.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        database.Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=13")
        with sessionmaker(bind=engine)() as session:
            _question(session, "某书 | 例2.6")
            _question(session, "某书 | 例3.1", "见例2.6")
        archive = backup.create_full_backup(output_dir=tmp_path / "archives", database_path=path,
                                           uploads_dir=tmp_path / "uploads", metadata_path=tmp_path / "metadata.json")
        manifest = backup.verify_full_backup(archive)
        assert manifest["database"]["row_counts"]["question_source_labels"] == 2
        assert manifest["database"]["row_counts"]["question_references"] == 1
        assert "question_references" not in backup._backup_count_tables(12)
        assert "question_references" in backup._backup_count_tables(13)
    finally:
        engine.dispose()
