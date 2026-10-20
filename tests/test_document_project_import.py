"""File-SQLite receipt/backup regressions; never import the production app."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from mathbank.backup import create_full_backup, verify_full_backup
from mathbank.database import Base, DocumentImportReceipt, Question
from mathbank import db_migrations
from mathbank.document_project_import import (
    import_payload_hash, prepare_project_import, record_project_import, project_import_guard,
)
from mathbank.document_projects import DocumentProjectStore


def _card(**changes):
    value = {"project_card_id": "card-one", "content": "已知 $x=1$，求 $x+1$。",
             "answer_markdown": "$2$", "question_type": "detailed_answer", "difficulty": "normal",
             "category_compulsory": "必修一", "category_chapter": "3. 函数", "category_knowledge": "",
             "source": "隔离回执测试", "review": "保留评析", "image_paths": []}
    value.update(changes)
    return value


@pytest.fixture
def environment(tmp_path):
    path = tmp_path / "questions.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=14")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    factory = sessionmaker(bind=engine)
    store = DocumentProjectStore(tmp_path / "projects")
    project = store.create(b"%PDF-isolated", "本地工程.pdf", {}, {"必修一": {"3. 函数": []}})
    card = _card()
    store.save_draft(project["project_id"], {"questions": [card], "paperTitle": "测试"}, 0)
    yield path, engine, factory, store, project["project_id"], card
    engine.dispose()


def _import(factory, store, project_id, card, *, revision=1, before_commit=None):
    @project_import_guard(lambda: store)
    def execute(*, import_project_id):
        with factory() as db:
            try:
                digest, existing = prepare_project_import(db, store, import_project_id,
                    card["project_card_id"], revision, card)
                if existing is not None:
                    return existing
                question = Question(**{key: value for key, value in card.items()
                                       if key not in {"project_card_id", "image_paths"}})
                question.image_paths = card.get("image_paths", [])
                db.add(question)
                db.flush()
                record_project_import(db, project_id=import_project_id, card_id=card["project_card_id"],
                                      revision=revision, digest=digest, question_id=question.id)
                if before_commit:
                    before_commit(db)
                db.commit()
                return question.id
            except Exception:
                db.rollback()
                raise
    return execute(import_project_id=project_id)


def _counts(factory):
    with factory() as db:
        return db.query(Question).count(), db.query(DocumentImportReceipt).count()


def _question_hash(path):
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT * FROM questions ORDER BY id").fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def test_same_project_card_payload_only_creates_one_question_across_sessions(environment):
    _, _, factory, store, project_id, card = environment
    qid = _import(factory, store, project_id, card)
    assert _import(factory, store, project_id, deepcopy(card)) == qid
    assert _counts(factory) == (1, 1)
    with factory() as db:
        receipt = db.query(DocumentImportReceipt).one()
        assert receipt.payload_sha256 == import_payload_hash(card)
        assert receipt.question_id == qid and receipt.draft_revision == 1


@pytest.mark.parametrize("change", [
    {"content": "改动题干"}, {"answer_markdown": "$3$"},
    {"image_paths": ["/static/uploads/changed.png"]},
    {"tikz_code": r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}"},
    {"tags": "新标签"}, {"figure_align": "center"}, {"figure_size": "large"},
    {"figure_align_custom": True},
    {"image_layouts": {"/static/uploads/a.png": {"align": "left", "size": "small"}}},
    {"answer_tikz_assets": [{"image_path": "/static/uploads/answer.png", "tikz_code": "source"}]},
])
def test_committed_card_changed_payload_conflicts_instead_of_creating_again(environment, change):
    _, _, factory, store, project_id, card = environment
    _import(factory, store, project_id, card)
    with pytest.raises(HTTPException) as error:
        _import(factory, store, project_id, {**card, **change})
    assert error.value.status_code == 409
    assert _counts(factory) == (1, 1)


def test_form_json_and_draft_json_defaults_share_the_same_receipt_hash(environment):
    _, _, factory, store, project_id, card = environment
    qid = _import(factory, store, project_id, card)
    equivalent = {**card, "image_paths": "[]", "image_layouts": "{}", "content_tikz_assets": "[]",
                  "answer_tikz_assets": "[]", "sub_knowledge": "[]", "knowledge_points": "[]",
                  "figure_align": "right", "figure_size": "auto", "figure_align_custom": "false"}
    assert _import(factory, store, project_id, equivalent) == qid
    assert _counts(factory) == (1, 1)


def test_missing_project_card_and_unsaved_or_changed_draft_are_rejected(environment):
    _, _, factory, store, project_id, card = environment
    with pytest.raises(HTTPException) as error:
        _import(factory, store, project_id, {**card, "project_card_id": "missing"})
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        _import(factory, store, project_id, card, revision=2)
    assert error.value.status_code == 409
    store.save_draft(project_id, {"questions": [{**card, "content": "新工作稿"}]}, 1)
    with pytest.raises(HTTPException) as error:
        _import(factory, store, project_id, card, revision=1)
    assert error.value.status_code == 409
    assert _counts(factory) == (0, 0)
    store.delete(project_id)
    with pytest.raises(HTTPException) as error:
        _import(factory, store, project_id, card)
    assert error.value.status_code == 409


def test_question_and_receipt_roll_back_in_the_same_real_file_transaction(environment):
    _, _, factory, store, project_id, card = environment
    def reject(db):
        db.flush()
        assert db.query(Question).count() == db.query(DocumentImportReceipt).count() == 1
        raise RuntimeError("simulated failure after receipt flush")
    with pytest.raises(RuntimeError, match="receipt flush"):
        _import(factory, store, project_id, card, before_commit=reject)
    # A genuinely independent connection sees neither uncommitted object.
    assert _counts(factory) == (0, 0)
    assert _import(factory, store, project_id, card) > 0
    assert _counts(factory) == (1, 1)


def test_database_commit_without_project_mirror_write_replays_after_restart(environment):
    path, engine, factory, store, project_id, card = environment
    qid = _import(factory, store, project_id, card)
    # Simulate process death immediately after the SQLite commit: no imported
    # marker was written to the project JSON and no in-memory session survives.
    saved = store.get_draft(project_id)
    assert saved["payload"]["questions"][0] == card
    engine.dispose()
    fresh_engine = create_engine(f"sqlite:///{path}")
    fresh_store = DocumentProjectStore(store.root)
    try:
        assert _import(sessionmaker(bind=fresh_engine), fresh_store, project_id, card) == qid
        assert _counts(sessionmaker(bind=fresh_engine)) == (1, 1)
    finally:
        fresh_engine.dispose()


def test_deleting_imported_question_sets_receipt_null_then_returns_410(environment):
    _, _, factory, store, project_id, card = environment
    qid = _import(factory, store, project_id, card)
    with factory() as db:
        db.execute(text("DELETE FROM questions WHERE id=:qid"), {"qid": qid})
        db.commit()
    with factory() as db:
        assert db.query(DocumentImportReceipt).one().question_id is None
    with pytest.raises(HTTPException) as error:
        _import(factory, store, project_id, card)
    assert error.value.status_code == 410
    assert _counts(factory) == (0, 1)


def test_database_unique_card_constraint_is_real_not_only_python_check(environment):
    _, _, factory, store, project_id, card = environment
    qid = _import(factory, store, project_id, card)
    with factory() as db:
        record_project_import(db, project_id=project_id, card_id=card["project_card_id"], revision=1,
                              digest=import_payload_hash(card), question_id=qid)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    assert _counts(factory) == (1, 1)


def test_v13_to_v14_backs_up_before_ddl_and_preserves_all_question_fields(environment, tmp_path, monkeypatch):
    path, engine, factory, _, _, card = environment
    with factory() as db:
        question = Question(content=card["content"], answer_markdown=card["answer_markdown"],
                            tikz_code=r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}",
                            figure_align="bottom_right", figure_size="large", tags="证据,保留")
        question.image_paths = ["/static/uploads/原图.png"]
        question.image_layouts = {"/static/uploads/原图.png": {"align": "center", "size": "small"}}
        db.add(question); db.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE document_import_receipts")
        connection.exec_driver_sql("PRAGMA user_version=13")
    before = _question_hash(path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    original_backup = db_migrations.create_pre_migration_backup
    original_ensure = db_migrations._ensure_document_import_receipts
    observed = []
    def backup(*args, **kwargs):
        assert "document_import_receipts" not in inspect(engine).get_table_names()
        result = original_backup(*args, **kwargs)
        assert result.is_file() and result.with_suffix('.db.sha256').is_file()
        with sqlite3.connect(result) as snapshot:
            assert snapshot.execute("PRAGMA user_version").fetchone()[0] == 13
            assert not snapshot.execute("SELECT name FROM sqlite_master WHERE name='document_import_receipts'").fetchall()
        observed.append(result)
        return result
    def ensure(connection):
        assert observed, "receipt DDL preceded durable migration backup"
        return original_ensure(connection)
    monkeypatch.setattr(db_migrations, "create_pre_migration_backup", backup)
    monkeypatch.setattr(db_migrations, "_ensure_document_import_receipts", ensure)
    result = db_migrations.migrate_database(engine)
    assert result['from_version'] == 13 and result['to_version'] == 14
    assert db_migrations.schema_version(engine) == 14
    assert 'document_import_receipts' in inspect(engine).get_table_names()
    assert _question_hash(path) == before
    assert _question_hash(observed[0]) == before


def test_failed_pre_migration_backup_leaves_v13_schema_and_question_bytes_unchanged(environment, monkeypatch):
    path, engine, factory, _, _, card = environment
    with factory() as db:
        db.add(Question(content=card['content'], answer_markdown=card['answer_markdown'])); db.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE document_import_receipts")
        connection.exec_driver_sql("PRAGMA user_version=13")
    before = _question_hash(path)
    def reject(*_a, **_kw):
        raise OSError('backup failed')
    monkeypatch.setattr(db_migrations, 'create_pre_migration_backup', reject)
    with pytest.raises(OSError, match='backup failed'):
        db_migrations.migrate_database(engine)
    assert db_migrations.schema_version(engine) == 13
    assert 'document_import_receipts' not in inspect(engine).get_table_names()
    assert _question_hash(path) == before


def test_full_backup_manifest_and_archived_database_include_import_receipts(environment, tmp_path):
    path, _, factory, store, project_id, card = environment
    qid = _import(factory, store, project_id, card)
    uploads = tmp_path / 'uploads'; uploads.mkdir()
    archive = create_full_backup(database_path=path, uploads_dir=uploads,
        metadata_path=tmp_path/'custom_metadata.json', output_dir=tmp_path/'full-backups', retention=None)
    manifest = verify_full_backup(archive)
    assert manifest['database']['schema_version'] == 14
    assert manifest['database']['row_counts']['document_import_receipts'] == 1
    assert manifest['database']['row_counts']['questions'] == 1
    import zipfile
    with zipfile.ZipFile(archive) as zipped:
        filename = next(name for name in zipped.namelist() if name.startswith('database/'))
        restored = tmp_path/'restored.db'; restored.write_bytes(zipped.read(filename))
    with sqlite3.connect(restored) as connection:
        assert connection.execute('SELECT project_id,card_id,question_id FROM document_import_receipts').fetchone() == (project_id,card['project_card_id'],qid)
