"""Version-specific relations survive; public filtering uses one stored label."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from mathbank import db_migrations
from mathbank.curriculums import CURRICULUM_NAMES, build_default_metadata
from mathbank.database import Base, Question, QuestionCurriculum, QuestionSubKnowledge
from mathbank.sub_knowledge import normalize_sub_knowledge, resolve_active_version


CONDITIONAL = {"compulsory": "选修三", "chapter": "7. 随机变量及其分布", "knowledge": "7.1 条件概率与全概率公式"}
BINOMIAL = {"compulsory": "选修三", "chapter": "7. 随机变量及其分布", "knowledge": "7.4 二项分布与超几何分布"}


@pytest.fixture
def isolated_metadata(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "METADATA_CACHE", build_default_metadata("A"))
    monkeypatch.setattr(main, "METADATA_FILE", str(tmp_path / "metadata.json"))
    monkeypatch.setattr(main, "schedule_database_export", lambda *args, **kwargs: None)
    return main


def payload(content="有条件概率的题目"):
    return {"content": content, "question_type": "single_choice", "difficulty": "normal",
            "category_compulsory": "必修二", "category_chapter": "10. 概率",
            "category_knowledge": "10. 概率", "tags": "历史标签，不改变", "image_paths": "[]"}


def headers(main):
    return {"X-Local-Token": main.LOCAL_TOKEN}


def create(client, main, **extra):
    data = payload()
    data.update(extra)
    response = client.post("/api/questions", data=data, headers=headers(main))
    assert response.status_code == 200, response.text
    return response.json()["question"]["id"]


def test_full_path_validation_and_stable_deduplication():
    assert normalize_sub_knowledge(json.dumps([CONDITIONAL, CONDITIONAL, BINOMIAL])) == [CONDITIONAL, BINOMIAL]
    stripped = {field: f"  {value}  " for field, value in CONDITIONAL.items()}
    assert normalize_sub_knowledge([stripped]) == [CONDITIONAL]
    for value in (None, {}, ["条件概率"], [dict(CONDITIONAL, extra="猜测")],
                  [dict(CONDITIONAL, chapter="")], [dict(CONDITIONAL, knowledge=7)],
                  [dict(CONDITIONAL, knowledge="a\x00b")], [CONDITIONAL] * 65):
        with pytest.raises(ValueError):
            normalize_sub_knowledge(value)


def test_create_edit_legacy_omission_explicit_clear_and_atomic_rejection(client, db_session, isolated_metadata):
    main = isolated_metadata
    qid = create(client, main, sub_knowledge=json.dumps([CONDITIONAL, CONDITIONAL, BINOMIAL]))
    mapping = db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one()
    assert mapping.sub_knowledge == [CONDITIONAL, BINOMIAL]
    assert db_session.query(QuestionSubKnowledge).count() == 2
    data = payload("旧客户端更新内容")
    response = client.put(f"/api/questions/{qid}", data=data, headers=headers(main))
    assert response.status_code == 200
    assert response.json()["question"]["sub_knowledge"] == [CONDITIONAL, BINOMIAL]
    bad = dict(data, content="不应落库", sub_knowledge='["条件概率"]')
    assert client.put(f"/api/questions/{qid}", data=bad, headers=headers(main)).status_code == 400
    result = client.get(f"/api/questions/{qid}").json()
    assert result["content"] == "旧客户端更新内容"
    assert result["category_knowledge"] == "10. 概率"
    assert result["tags"] == "历史标签，不改变"
    assert result["sub_knowledge"] == [CONDITIONAL, BINOMIAL]
    assert result["active_version_code"] == "A"
    assert result["curriculum_mappings"][0]["sub_knowledge"] == [CONDITIONAL, BINOMIAL]
    clear = dict(data, sub_knowledge="[]")
    assert client.put(f"/api/questions/{qid}", data=clear, headers=headers(main)).status_code == 200
    assert client.get(f"/api/questions/{qid}").json()["sub_knowledge"] == []
    assert db_session.query(QuestionSubKnowledge).count() == 0


def test_blank_knowledge_stays_whole_chapter_and_does_not_rewrite_other_version(client, db_session, isolated_metadata):
    main = isolated_metadata
    qid = create(client, main, category_knowledge="")
    question = db_session.query(Question).filter_by(id=qid).one()
    a_mirror = db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one()
    b_mirror = QuestionCurriculum(question_id=qid, version_code="B", compulsory="旧版册",
                                  chapter="历史章", knowledge="历史整章标签")
    db_session.add(b_mirror); db_session.commit()
    assert question.category_knowledge == a_mirror.knowledge == ""
    update = payload("仍为整章定位")
    update["category_knowledge"] = ""
    assert client.put(f"/api/questions/{qid}", data=update, headers=headers(main)).status_code == 200
    db_session.expire_all()
    assert question.category_knowledge == a_mirror.knowledge == ""
    assert b_mirror.knowledge == "历史整章标签"


def test_single_value_filters_do_not_expand_from_relations(client, isolated_metadata):
    main = isolated_metadata
    create(client, main, content="仅关系匹配题", sub_knowledge=json.dumps([CONDITIONAL, BINOMIAL]))
    matched = create(client, main, content="唯一单值匹配题", category_compulsory=CONDITIONAL["compulsory"],
                     category_chapter=CONDITIONAL["chapter"], category_knowledge=CONDITIONAL["knowledge"])
    create(client, main, content="不同册的同名小节", category_compulsory="必修二",
           category_chapter=CONDITIONAL["chapter"], category_knowledge=CONDITIONAL["knowledge"])
    create(client, main, content="仅历史标签题", tags="条件概率")
    for params in (CONDITIONAL, dict(CONDITIONAL, include_sub_knowledge="false")):
        response = client.get("/api/questions", params=params)
        assert response.status_code == 200, response.text
        assert [q["id"] for q in response.json()] == [matched]
    paginated = client.get("/api/questions", params=dict(CONDITIONAL, page=1, page_size=1)).json()
    assert paginated["total"] == 1 and [q["id"] for q in paginated["items"]] == [matched]
    assert [q["id"] for q in client.get("/api/questions", params=dict(CONDITIONAL, q="唯一")).json()] == [matched]
    assert client.get("/api/questions", params=dict(CONDITIONAL, q="仅关系")).json() == []


@pytest.mark.parametrize("params", [
    {"include_sub_knowledge": "true"},
    {"include_sub_knowledge": "true", **CONDITIONAL},
    {"sub_knowledge": json.dumps(CONDITIONAL), "sub_knowledge_mode": "any"},
    {"sub_knowledge": json.dumps(CONDITIONAL), "sub_knowledge_mode": "all"},
])
def test_question_list_rejects_relation_filter_parameters(client, isolated_metadata, params):
    main = isolated_metadata
    create(client, main, sub_knowledge=json.dumps([CONDITIONAL]))
    response = client.get("/api/questions", params=params)
    assert response.status_code == 400, response.text
    assert "原单值小节" in response.json()["detail"]


def test_relation_catalog_remains_read_only_and_preserves_saved_evidence(client, db_session, isolated_metadata):
    main = isolated_metadata
    qid = create(client, main, sub_knowledge=json.dumps([CONDITIONAL]))
    before = db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one().to_dict()
    assert client.get("/api/questions", params={"sub_knowledge": "条件概率"}).status_code == 400
    assert client.get("/api/questions", params={"sub_knowledge_mode": "xor"}).status_code == 400
    catalog = client.get("/api/config/sub-knowledge", params={"compulsory": "选修三"}).json()
    assert catalog["version_code"] == "A" and CONDITIONAL in catalog["sub_knowledge"]
    assert CONDITIONAL in catalog["existing_sub_knowledge"]
    db_session.expire_all()
    assert db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one().to_dict() == before


def test_all_versions_independent_new_mirrors_empty_existing_empty_mirror_preserved(client, db_session, isolated_metadata):
    main = isolated_metadata
    qid = create(client, main, sub_knowledge=json.dumps([CONDITIONAL]))
    original = db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one().to_dict()
    for version in ("B", "S", "H", "N", "X"):
        response = client.post("/api/config/metadata", json=build_default_metadata(version), headers=headers(main))
        assert response.status_code == 200, response.text
        detail = client.get(f"/api/questions/{qid}").json()
        assert detail["active_version_code"] == version and detail["sub_knowledge"] == []
        mirror = db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code=version).one()
        mirror.sub_knowledge = [{"compulsory": f"{version}自定义册", "chapter": "历史自定义章", "knowledge": "历史自定义小节"}]
        # Existing empty old categories are meaningful and must not be routed again.
        mirror.compulsory = mirror.chapter = mirror.knowledge = ""
        db_session.commit()
    for version in (*CURRICULUM_NAMES, "A"):
        assert client.post("/api/config/metadata", json=build_default_metadata(version), headers=headers(main)).status_code == 200
        detail = client.get(f"/api/questions/{qid}").json()
        if version == "A":
            assert detail["sub_knowledge"] == [CONDITIONAL]
            assert detail["category_knowledge"] == "10. 概率"
        else:
            assert detail["sub_knowledge"][0]["compulsory"] == f"{version}自定义册"
            assert detail["category_compulsory"] == detail["category_chapter"] == detail["category_knowledge"] == ""
    assert db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one().to_dict() == original


def test_stale_version_save_and_ai_selection_rejected_without_changes(client, db_session, isolated_metadata):
    main = isolated_metadata
    qid = create(client, main, sub_knowledge=json.dumps([CONDITIONAL]))
    assert client.post("/api/config/metadata", json=build_default_metadata("B"), headers=headers(main)).status_code == 200
    data = dict(payload("过期的A题干"), expected_version_code="A", sub_knowledge=json.dumps([BINOMIAL]))
    assert client.put(f"/api/questions/{qid}", data=data, headers=headers(main)).status_code == 409
    assert client.post("/api/questions", data=data, headers=headers(main)).status_code == 409
    assert client.post("/api/paper/ai-select", json={"expected_version_code": "A"}, headers=headers(main)).status_code == 409
    assert db_session.query(Question).count() == 1
    assert db_session.query(Question).one().content == "有条件概率的题目"
    assert db_session.query(QuestionCurriculum).filter_by(question_id=qid, version_code="A").one().sub_knowledge == [CONDITIONAL]


def test_ai_fallback_respects_single_value_filters(client, monkeypatch, isolated_metadata):
    main = isolated_metadata
    create(client, main, content="仅关系匹配的概率题", sub_knowledge=json.dumps([CONDITIONAL]))
    qid = create(client, main, content="单值匹配的概率题", category_compulsory=CONDITIONAL["compulsory"],
                 category_chapter=CONDITIONAL["chapter"], category_knowledge=CONDITIONAL["knowledge"],
                 sub_knowledge=json.dumps([BINOMIAL]))
    create(client, main, content="不在筛选池里的概率题", category_compulsory="另一册")
    monkeypatch.setattr(main, "resolve_text_provider", lambda _: SimpleNamespace(
        api_key="", api_base="", model_name="test", provider_label="本地", credential_label="测试"))
    data = {**CONDITIONAL, "prompt": "概率", "limit": 10,
            "include_sub_knowledge": False, "sub_knowledge": [], "sub_knowledge_mode": "all"}
    response = client.post("/api/paper/ai-select", json=data, headers=headers(main))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["fallback"] is True
    assert [q["id"] for q in result["data"]] == [qid]
    data["compulsory"] = "必修二"
    response = client.post("/api/paper/ai-select", json=data, headers=headers(main))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["data"] == []


@pytest.mark.parametrize("params", [
    {"include_sub_knowledge": True},
    {"include_sub_knowledge": "true"},
    {"sub_knowledge": [CONDITIONAL], "sub_knowledge_mode": "any"},
    {"sub_knowledge": [CONDITIONAL], "sub_knowledge_mode": "all"},
    {"sub_knowledge": "条件概率"},
    {"sub_knowledge_mode": "xor"},
])
def test_ai_selection_rejects_relation_filters_before_provider_call(client, monkeypatch, isolated_metadata, params):
    main = isolated_metadata
    create(client, main, sub_knowledge=json.dumps([CONDITIONAL]))
    monkeypatch.setattr(main, "resolve_text_provider", lambda _: pytest.fail("Invalid filters must not invoke an AI provider"))
    response = client.post("/api/paper/ai-select", json={"prompt": "概率", **params}, headers=headers(main))
    assert response.status_code == 400, response.text


def test_v10_migration_backup_unchanged_old_fields_empty_relation_idempotent_fk(tmp_path, monkeypatch):
    path = tmp_path / "v10.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE question_sub_knowledge")
            connection.exec_driver_sql("PRAGMA user_version=10")
            connection.exec_driver_sql("INSERT INTO questions(id,content,category_knowledge,tags) VALUES(1,'原题','8. 立体几何初步','旧标签')")
            connection.exec_driver_sql("INSERT INTO question_curriculums(id,question_id,version_code,knowledge) VALUES(1,1,'A','8. 立体几何初步'),(2,1,'B','历史B标签')")
        monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
        result = db_migrations.migrate_database(engine)
        from mathbank.db_migrations import LATEST_SCHEMA_VERSION
        assert result["from_version"] == 10 and result["to_version"] == LATEST_SCHEMA_VERSION and result["backup"]
        with sqlite3.connect(result["backup"]) as original:
            assert original.execute("PRAGMA user_version").fetchone()[0] == 10
            assert original.execute("SELECT category_knowledge,tags FROM questions").fetchone() == ("8. 立体几何初步", "旧标签")
        with engine.begin() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM question_sub_knowledge").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT category_knowledge,tags FROM questions").one() == ("8. 立体几何初步", "旧标签")
            assert connection.exec_driver_sql("SELECT knowledge FROM question_curriculums ORDER BY id").all() == [("8. 立体几何初步",), ("历史B标签",)]
            connection.exec_driver_sql("INSERT INTO question_sub_knowledge(curriculum_id,compulsory,chapter,knowledge) VALUES(1,'册','章','节')")
        again = db_migrations.migrate_database(engine)
        assert again["backup"] is None
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            connection.exec_driver_sql("DELETE FROM question_curriculums WHERE id=1")
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM question_sub_knowledge").scalar_one() == 0
    finally:
        engine.dispose()


def test_metadata_resolver_requires_explicit_valid_source_in_strict_mode(tmp_path):
    path = tmp_path / "metadata.json"
    with pytest.raises(ValueError):
        resolve_active_version(path, strict=True)
    for version in CURRICULUM_NAMES:
        path.write_text(json.dumps(build_default_metadata(version)), encoding="utf-8")
        assert resolve_active_version(path, strict=True) == version
    path.write_text('{"curriculum": {"未知": {"自编章": []}}}', encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_active_version(path, strict=True)
