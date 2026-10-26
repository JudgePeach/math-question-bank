"""Stable identities and independent sequence numbers on disposable SQLite."""
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mathbank.database import Base, Question
from mathbank.question_ordering import sequence_order, sequence_mapping, library_list_order
from mathbank.question_reads import find_association_candidates
from mathbank.question_references import sync_question_source_labels, sync_question_references, reference_payloads


@pytest.fixture
def ordered_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ordering.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([Question(id=3, content="other"),
                    Question(id=8, content="teacher-old", source="排序教材 | 例1.1"),
                    Question(id=15, content="olympiad"),
                    Question(id=21, content="teacher-new", source="排序教材 | 例1.2", answer_markdown="见例1.1。")])
        db.commit()
        yield db
    engine.dispose()


def reorder(db):
    for number, qid in enumerate([3, 8, 21, 15], 1):
        db.get(Question, qid).display_order = number
    db.commit()


def test_reorder_sequence_search_references_and_future_append_keep_ids(ordered_db):
    db = ordered_db
    assert sequence_mapping(db) == {3: 1, 8: 2, 15: 3, 21: 4}
    for qid in (8, 21):
        sync_question_source_labels(db, db.get(Question, qid))
    sync_question_references(db, db.get(Question, 21))
    db.commit()
    reorder(db)
    assert sequence_mapping(db) == {3: 1, 8: 2, 21: 3, 15: 4}
    assert sequence_mapping(db, [15, 21]) == {21: 3, 15: 4}
    assert find_association_candidates(db, seq_num=3)["items"][0]["id"] == 21
    assert [x.id for x in db.query(Question).order_by(*library_list_order(db))] == [15, 21, 8, 3]
    refs = reference_payloads(db, [db.get(Question, 21)])[21]
    assert refs[0]["target_question_id"] == 8 and refs[0]["seq_num"] == 2
    db.add(Question(content="later"));db.commit()
    assert [x.id for x in db.query(Question).order_by(*sequence_order())] == [3, 8, 21, 15, 22]
    db.delete(db.get(Question, 3));db.commit()
    assert sequence_mapping(db) == {8: 1, 21: 2, 15: 3, 22: 4}
    assert reference_payloads(db, [db.get(Question, 21)])[21][0]["seq_num"] == 1


def test_no_custom_order_retains_legacy_date_sort_and_rejects_invalid_value(ordered_db):
    db = ordered_db
    assert "created_at" in str(library_list_order(db)[0])
    db.get(Question, 8).display_order = 0
    with pytest.raises(Exception):db.commit()
    db.rollback()
    assert sequence_mapping(db, []) == {}


def test_later_insert_after_many_high_id_deletions_still_appends(ordered_db):
    db=ordered_db
    db.get(Question, 8).display_order=1000
    db.commit()
    q=Question(content='new after old high-rank row')
    db.add(q);db.commit()
    assert q.display_order==1001
    assert [x.id for x in db.query(Question).order_by(*sequence_order())][-1]==q.id


def test_api_lists_detail_and_sequence_lookup_use_same_custom_order(client, db_session):
    db_session.add_all([Question(id=4, content="alpha", display_order=1),
                        Question(id=9, content="beta", display_order=3),
                        Question(id=16, content="gamma", display_order=2)])
    db_session.commit()
    rows=client.get('/api/questions',params={'page':1,'page_size':10,'sort':'asc'}).json()['items']
    assert [(x['id'],x['seq_num'])for x in rows]==[(4,1),(16,2),(9,3)]
    assert client.get('/api/questions/16').json()['seq_num']==2
    found=client.get('/api/questions',params={'q':'#2','page':1}).json()['items']
    assert [x['id']for x in found]==[16]
    from mathbank.library_transfer import preview_library_questions
    preview=preview_library_questions(db_session,question_ids=[9,16],active_version='A')
    assert [(x['id'],x['seq_num'])for x in preview['items']]==[(9,3),(16,2)]


def test_sync_export_keeps_display_order_within_batches(ordered_db, tmp_path, monkeypatch):
    import json
    from mathbank import sync_helper
    db=ordered_db;reorder(db)
    monkeypatch.setattr(sync_helper,'BACKUP_DIR',str(tmp_path))
    monkeypatch.setattr(sync_helper,'JSON_BACKUP_PATH',str(tmp_path/'questions.json'))
    monkeypatch.setattr(sync_helper,'MD_BACKUP_PATH',str(tmp_path/'questions.md'))
    sync_helper.export_database_to_files(db,quiet=True)
    payload=json.loads((tmp_path/'questions.json').read_text())
    assert [q['id']for q in payload]==[3,8,21,15]
    text=(tmp_path/'questions.md').read_text()
    assert '题目 #3 (数据库 ID: 21)'in text
    assert '题目 #4 (数据库 ID: 15)'in text


def test_schema14_upgrade_preserves_rows_and_is_idempotent(tmp_path, monkeypatch):
    from mathbank import db_migrations as migrations
    path=tmp_path/'old.db';engine=create_engine(f"sqlite:///{path}")
    from sqlalchemy.schema import CreateTable
    sql = str(CreateTable(Question.__table__).compile(engine))
    sql = sql.replace("\tdisplay_order INTEGER, \n", "").replace(
        ", \n\tCONSTRAINT ck_questions_display_order_positive CHECK (display_order IS NULL OR display_order > 0)", "")
    with engine.begin() as c:
        c.exec_driver_sql(sql)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO questions(id,content)VALUES(8,'original')")
        c.exec_driver_sql("PRAGMA user_version=14")
    monkeypatch.setattr(migrations,'SCHEMA_SNAPSHOT_DIR',tmp_path/'snapshots')
    result=migrations.migrate_database(engine)
    assert result['from_version']==14 and result['to_version']==15
    assert result['added_question_display_order_columns']==1
    with engine.connect() as c:
        assert c.exec_driver_sql('SELECT id,content,display_order FROM questions').fetchall()==[(8,'original',None)]
    assert migrations.migrate_database(engine)['backup']is None
    with sqlite3.connect(result['backup'])as c:
        assert 'display_order'not in {x[1]for x in c.execute('PRAGMA table_info(questions)')}
    engine.dispose()
