"""Knowledge-point storage and retrieval use isolated temporary databases."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from mathbank import database, db_migrations, knowledge_points
from mathbank.database import KnowledgePoint, KnowledgePointCurriculum, Question, QuestionCurriculum, QuestionKnowledgePoint
from mathbank.knowledge_points import (
    CATALOG_FILE, apply_knowledge_point_filters, create_local_point, list_catalog,
    normalize_question_points, replace_question_points, seed_catalog,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'points.db'}")
    database.Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        seed_catalog(session)
        session.commit()
        yield session
    engine.dispose()


def test_catalog_resource_preserves_codes_names_sources_and_real_paths(db):
    resource = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    assert len(knowledge_points._BASE_PLATFORM_CODES) == 630
    assert len(knowledge_points._BASE_LOCAL_CODES) == 2
    assert db.query(KnowledgePoint).count() == resource["counts"]["points"]
    assert db.query(KnowledgePoint).filter(KnowledgePoint.source.in_(["zhixue", "zxxk"])).count() == resource["counts"]["platform_points"]
    assert db.query(KnowledgePoint).filter_by(source="local").count() == resource["counts"]["local_points"]
    assert knowledge_points._BASE_PLATFORM_CODES.issubset({row.code for row in db.query(KnowledgePoint)})
    assert knowledge_points._BASE_PLATFORM_CODES.issubset({row.code for row in db.query(KnowledgePoint).filter_by(source="zhixue")})
    assert {row.version_code for row in db.query(KnowledgePointCurriculum)} == {
        path["version_code"] for point in resource["points"] for path in point["curriculum_paths"]
    }
    for definition in resource["points"]:
        actual = db.get(KnowledgePoint, definition["code"])
        assert actual.name == definition["name"]
        assert actual.original_name == definition["original_name"]
        assert actual.to_dict()["curriculum_paths"] == definition["curriculum_paths"]
    assert db.query(KnowledgePointCurriculum).filter_by(reference_section="4.4* 数学归纳法").count() > 0
    # Preserve empty original Zhixue captures. Reviewed associations from a
    # separate source may legitimately refer to the same real textbook leaf;
    # they must retain their own source identity rather than becoming Zhixue.
    for section in ("9.3 统计案例 公司员工的体重指数调查分析", "6.2.3 组合", "8.2.1 一元线性回归模型", "8.3.1  分类变量与列联表"):
        assert db.query(KnowledgePointCurriculum).join(KnowledgePoint).filter(
            KnowledgePointCurriculum.version_code == "A",
            KnowledgePointCurriculum.reference_section == section,
            KnowledgePointCurriculum.level == "leaf", KnowledgePoint.source == "zhixue",
        ).count() == 0


def test_reviewed_cross_textbook_references_keep_source_identity_and_real_A_location(db):
    cases = [
        ("必修二", "9. 统计", "9.3 统计案例", "zxxk:catalog:225851"),
        ("选修三", "8. 成对数据的统计分析", "8.2 一元线性回归模型及其应用", "zxxk:catalog:206261"),
    ]
    for book, chapter, section, code in cases:
        result = list_catalog(db, "A", compulsory=book, chapter=chapter, knowledge=section)
        points = {point["code"]: point for point in result["items"]}
        assert code in points and points[code]["source"] == "zxxk"
        assert any(path["version_code"] == "A" and path["compulsory"] == book
                   and path["chapter"] == chapter and path["knowledge"] == section
                   and path["level"] == "leaf" for path in points[code]["curriculum_paths"])


def test_every_shipped_catalogue_definition_can_be_carried_by_a_sharing_point_record():
    from mathbank.library_transfer import _validate_point

    for point in knowledge_points._resource_points():
        _validate_point({**point, "role": "main", "status": "confirmed", "position": 0})


def test_resource_accepts_additional_platform_codes_without_replacing_the_compatibility_baseline(tmp_path, monkeypatch):
    resource = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    extra = {**resource["points"][0], "code": "999999999", "name": "新增教材平台点", "original_name": "新增教材平台点"}
    resource["points"].append(extra)
    resource["counts"]["points"] += 1
    resource["counts"]["platform_points"] += 1
    if "paths_by_version" in resource["counts"]:
        for path in extra["curriculum_paths"]:
            resource["counts"]["paths_by_version"][path["version_code"]] += 1
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(resource, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(knowledge_points, "CATALOG_FILE", target)
    knowledge_points._resource_points.cache_clear()
    try:
        definitions = knowledge_points._resource_points()
        assert len(definitions) == resource["counts"]["points"]
        assert definitions[-1]["code"] == extra["code"]
        assert knowledge_points._BASE_PLATFORM_CODES.issubset({row["code"] for row in definitions})
    finally:
        knowledge_points._resource_points.cache_clear()


@pytest.mark.parametrize("problem", ["count", "boolean_count", "alias_count", "version_path_count", "missing_base", "duplicate_code", "duplicate_path",
                                      "unknown_section", "extra_path_field", "wrong_source"])
def test_resource_rejects_inconsistent_counts_identities_and_paths(tmp_path, monkeypatch, problem):
    resource = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    first = resource["points"][0]
    if problem == "count":
        resource["counts"]["points"] += 1
    elif problem == "boolean_count":
        resource["counts"]["points"] = True
    elif problem == "alias_count":
        resource["counts"]["source_code_aliases"] = len(resource.get("source_code_aliases", {})) + 1
    elif problem == "version_path_count":
        resource["counts"]["paths_by_version"] = {"A": 1}
    elif problem == "missing_base":
        resource["points"] = [point for point in resource["points"] if point["code"] != "1015"]
        resource["counts"]["points"] -= 1
        resource["counts"]["platform_points"] -= 1
    elif problem == "duplicate_code":
        resource["points"].append(first)
        resource["counts"]["points"] += 1
        resource["counts"]["platform_points"] += 1
    elif problem == "duplicate_path":
        first["curriculum_paths"].append(dict(first["curriculum_paths"][0]))
    elif problem == "unknown_section":
        first["curriculum_paths"][0].update(knowledge="不存在的小节", level="leaf")
    elif problem == "extra_path_field":
        first["curriculum_paths"][0]["invented"] = True
    elif problem == "wrong_source":
        first["source"] = "local"
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(resource, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(knowledge_points, "CATALOG_FILE", target)
    knowledge_points._resource_points.cache_clear()
    try:
        with pytest.raises(ValueError):
            knowledge_points._resource_points()
    finally:
        knowledge_points._resource_points.cache_clear()


def test_resource_rejects_duplicate_json_keys(tmp_path, monkeypatch):
    target = tmp_path / "catalog.json"
    target.write_text('{"schema_version":"mathbank-knowledge-points-v1","points":[],"points":[]}', encoding="utf-8")
    monkeypatch.setattr(knowledge_points, "CATALOG_FILE", target)
    knowledge_points._resource_points.cache_clear()
    try:
        with pytest.raises(ValueError, match="字段重复"):
            knowledge_points._resource_points()
    finally:
        knowledge_points._resource_points.cache_clear()


def test_explicit_curriculum_hierarchy_validates_real_sections_and_complete_parent_chains(monkeypatch):
    tree = {"测试册": {"测试章": ["1 集合", "1.1 集合的概念", "1.1.1 元素", "2 函数"]}}
    monkeypatch.setattr(knowledge_points, "load_curriculum", lambda version: tree)
    rows = [
        {"compulsory": "测试册", "chapter": "测试章", "knowledge": "1 集合", "ancestors": []},
        {"compulsory": "测试册", "chapter": "测试章", "knowledge": "1.1 集合的概念", "ancestors": ["1 集合"]},
        {"compulsory": "测试册", "chapter": "测试章", "knowledge": "1.1.1 元素", "ancestors": ["1 集合", "1.1 集合的概念"]},
        {"compulsory": "测试册", "chapter": "测试章", "knowledge": "2 函数", "ancestors": []},
    ]
    assert knowledge_points._normalize_curriculum_hierarchies({"A": rows}) == {"A": rows}
    invalid = [
        rows + [rows[0]],
        [{**rows[0], "knowledge": "不存在"}],
        [{**rows[0], "ancestors": ["不存在"]}],
        [{**rows[0], "ancestors": ["1 集合"]}],
        [rows[0], {**rows[1], "ancestors": ["1 集合", "1 集合"]}],
        [rows[0], rows[2]],
        [rows[0], rows[1], {**rows[2], "ancestors": ["1.1 集合的概念"]}],
    ]
    for entries in invalid:
        with pytest.raises(ValueError):
            knowledge_points._normalize_curriculum_hierarchies({"A": entries})


def test_source_code_aliases_only_accept_new_platform_codes_with_real_canonical_targets():
    canonical = {"1015", "local:function-graph-symmetry"}
    approved = {"9990001": "1015", "9990002": "local:function-graph-symmetry"}
    assert knowledge_points._normalize_source_code_aliases(approved, canonical) == approved
    for aliases in ([], {"1015": "1015"}, {"1228": "1015"}, {"0": "1015"}, {"１２３": "1015"},
                    {"new-code": "1015"}, {" 9990001": "1015"}, {"9990001": "missing"},
                    {"9990001": "9990002", "9990002": "1015"}, {"9990001": "9990002", "9990002": "9990001"}):
        with pytest.raises(ValueError):
            knowledge_points._normalize_source_code_aliases(aliases, canonical)


def test_raw_hierarchy_reference_names_are_preserved_and_only_whitespace_may_differ(monkeypatch):
    tree = {"测试册": {"测试章": ["1 集合", "1.1 集合的概念"]}}
    monkeypatch.setattr(knowledge_points, "load_curriculum", lambda version: tree)
    rows = [
        {"compulsory": "测试册", "chapter": "测试章", "knowledge": "1 集合", "ancestors": [],
         "reference_section": " 1\u00a0 集合 ", "reference_ancestors": []},
        {"compulsory": "测试册", "chapter": "测试章", "knowledge": "1.1 集合的概念", "ancestors": ["1 集合"],
         "reference_section": "1.1   集合的概念", "reference_ancestors": [" 1\u00a0 集合 "]},
    ]
    normalized = knowledge_points._normalize_curriculum_hierarchies({"A": rows})
    assert normalized == {"A": rows}
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {"hierarchies": normalized})
    assert knowledge_points.get_curriculum_ancestors("A") == {
        ("测试册", "测试章", " 1\u00a0 集合 "): [],
        ("测试册", "测试章", "1.1   集合的概念"): [" 1\u00a0 集合 "],
    }
    assert knowledge_points.get_curriculum_ancestors("H") == {}
    invalid = [
        [{key: value for key, value in rows[0].items() if key != "reference_ancestors"}],
        [{**rows[0], "reference_section": "1 函数"}],
        [rows[0], {**rows[1], "reference_ancestors": ["1 函数"]}],
        [rows[0], {**rows[1], "reference_ancestors": []}],
    ]
    for entries in invalid:
        with pytest.raises(ValueError):
            knowledge_points._normalize_curriculum_hierarchies({"A": entries})


def test_source_code_lookup_returns_existing_canonical_without_rewriting_labels(db, monkeypatch):
    resource = knowledge_points._resource_catalog()
    local = create_local_point(db, {"name": "本机已审阅对称性点"})
    question = Question(content="既有题文")
    db.add(question)
    replace_question_points(question, normalize_question_points([{"code": "1015", "reason": "既有证据"}], db), db)
    db.commit()
    before = question.knowledge_points[0].to_dict()
    aliases = {"9990001": "1015", "9990002": local["code"]}
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {**resource, "source_code_aliases": aliases})
    result = list_catalog(db, q="#9990001")
    assert result["total"] == 1 and result["items"][0]["code"] == "1015"
    assert result["items"][0]["source_codes"] == ["1015", "9990001"]
    assert list_catalog(db, q="#9990002")["items"][0]["source_codes"] == ["9990002"]
    assert list_catalog(db, q="#999")["total"] == 0
    assert list_catalog(db, q="#1015")["total"] == 1
    with pytest.raises(ValueError, match="未知知识点"):
        normalize_question_points([{"code": "9990001"}], db)
    assert question.knowledge_points[0].to_dict() == before


def test_parent_section_recommendations_include_only_declared_descendant_paths(db, monkeypatch):
    catalog = knowledge_points._resource_catalog()
    book, chapter = "必修一", "1. 集合与常用逻辑用语"
    sections = ["1.1 集合的概念", "1.2 集合间的基本关系", "1.3 集合的基本运算"]
    hierarchy = [{"compulsory": book, "chapter": chapter, "knowledge": section,
                  "ancestors": [sections[0]] if index == 1 else []} for index, section in enumerate(sections)]
    for index, section in enumerate(sections):
        point = KnowledgePoint(code=f"local:hierarchy-{index}", name=f"层级回归{index}", original_name=f"层级回归{index}", source="local")
        point.curriculum_paths = [KnowledgePointCurriculum(version_code="A", compulsory=book, chapter=chapter,
                                                         knowledge=section, reference_section=section, level="parent" if index == 0 else "leaf")]
        db.add(point)
    db.commit()
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {**catalog, "hierarchies": {"A": hierarchy}})
    query = {"version_code": "A", "q": "层级回归", "compulsory": book, "chapter": chapter}
    parent = list_catalog(db, **query, knowledge=sections[0])
    assert {point["code"] for point in parent["items"]} == {"local:hierarchy-0", "local:hierarchy-1"}
    assert parent["hierarchy"] == hierarchy
    assert {point["code"] for point in list_catalog(db, **query, knowledge=sections[1])["items"]} == {"local:hierarchy-1"}
    assert "hierarchy" not in list_catalog(db, **query, offset=1)
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {**catalog, "hierarchies": {}})
    assert {point["code"] for point in list_catalog(db, **query, knowledge=sections[0])["items"]} == {"local:hierarchy-0"}


def test_catalog_pages_cover_more_than_one_thousand_points_without_duplicates(db):
    db.add_all(KnowledgePoint(code=f"local:pagination-{index:04d}", name="分页点", original_name="分页点", source="local")
               for index in range(500))
    db.commit()
    expected = [row[0] for row in db.query(KnowledgePoint.code).order_by(KnowledgePoint.name, KnowledgePoint.code)]
    assert len(expected) > 1000
    actual = []
    for offset in range(0, len(expected), 137):
        page = list_catalog(db, limit=137, offset=offset)
        assert page["offset"] == offset and page["limit"] == 137 and page["total"] == len(expected)
        actual.extend(point["code"] for point in page["items"])
    assert actual == expected
    assert len(set(actual)) == len(actual)
    assert list_catalog(db, offset=len(expected))["items"] == []
    for offset in (-1, True, 1.5, "1", 2**63):
        with pytest.raises(ValueError, match="起始位置"):
            list_catalog(db, offset=offset)


def test_seed_is_idempotent_and_preserves_user_names_aliases_and_local_extensions(db):
    point = db.get(KnowledgePoint, "1015")
    point.name = "我的集合含义"
    point.aliases = ["集合定义"]
    local = create_local_point(db, {"name": "我的新知识点", "aliases": ["我的别名"]})
    db.commit()
    assert seed_catalog(db) == {"added_points": 0, "added_paths": 0}
    db.commit()
    assert point.name == "我的集合含义" and point.aliases == ["集合定义"]
    assert db.get(KnowledgePoint, local["code"]).name == "我的新知识点"


def test_seed_adds_new_catalogue_paths_without_rewriting_prior_point_or_question_data(db, monkeypatch):
    definitions = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["points"]
    definition = next(point for point in definitions if point["code"] == "1015")
    point = db.get(KnowledgePoint, "1015")
    point.name, point.original_name, point.aliases = "用户简名", "用户保存的原名", ["用户别名"]
    question = Question(content="保留题文", category_chapter="用户章节", tags="用户标签")
    db.add(question)
    replace_question_points(question, normalize_question_points([{"code": "1015", "role": "auxiliary", "reason": "既有依据"}], db), db)
    db.commit()
    original_paths = [row.to_dict() for row in point.curriculum_paths]
    new_path = {**definition["curriculum_paths"][0], "reference_section": "冻结的新增目录来源证据"}
    definition["curriculum_paths"].append(new_path)
    monkeypatch.setattr(knowledge_points, "_resource_points", lambda: definitions)
    assert seed_catalog(db) == {"added_points": 0, "added_paths": 1}
    db.commit()
    assert point.name == "用户简名" and point.original_name == "用户保存的原名" and point.aliases == ["用户别名"]
    assert [row.to_dict() for row in point.curriculum_paths] == original_paths + [new_path]
    assert question.content == "保留题文" and question.category_chapter == "用户章节" and question.tags == "用户标签"
    assert len(question.knowledge_points) == 1 and question.knowledge_points[0].role == "auxiliary"
    assert json.loads(question.knowledge_points[0].reason) == "既有依据"
    assert seed_catalog(db) == {"added_points": 0, "added_paths": 0}


def test_catalog_search_supports_code_name_alias_and_escaped_wildcards(db):
    point = db.get(KnowledgePoint, "1015")
    point.aliases = ["集合定义", "test_100%"]
    db.commit()
    assert list_catalog(db, q="1015")["items"][0]["code"] == "1015"
    assert list_catalog(db, q="集合定义")["items"][0]["name"] == "集合的含义"
    assert list_catalog(db, q="test_100%")["total"] == 1
    assert list_catalog(db, q="test_101%")["total"] == 0
    assert list_catalog(db, version_code="B", compulsory="必修一", chapter="1. 集合与常用逻辑用语")["total"] == 0
    assert list_catalog(db, version_code="B", q="集合的含义")["total"] == 1
    recommended = list_catalog(db, compulsory="选修二", chapter="4. 数列", knowledge="4.3 等比数列")["items"]
    assert recommended
    assert all(any(row["compulsory"] == "选修二" and row["chapter"] == "4. 数列" and row["knowledge"] == "4.3 等比数列" for row in item["curriculum_paths"]) for item in recommended)


def test_create_local_point_validates_real_curriculum_and_deduplicates_aliases(db):
    path = {"version_code": "A", "compulsory": "选修二", "chapter": "4. 数列", "knowledge": "4.3 等比数列"}
    point = create_local_point(db, {"name": "我的数列方法", "aliases": ["我的数列简称"], "paths": [path]})
    db.commit()
    assert point["code"].startswith("local:")
    assert point["source"] == "local" and point["curriculum_paths"][0]["knowledge"] == "4.3 等比数列"
    for payload in ({"name": "集合的含义"}, {"name": "我的数列简称"}, {"name": "新方法", "aliases": ["我的数列简称"]}, {"name": "*数学归纳法"}):
        with pytest.raises(ValueError, match="已有同名或同义"):
            create_local_point(db, payload)
    with pytest.raises(ValueError, match="真实小节"):
        create_local_point(db, {"name": "另一方法", "paths": [{**path, "knowledge": "9.9 不存在"}]})


@pytest.mark.parametrize("payloads", [
    ({"name": "并发新知识点"}, {"name": "并发新知识点"}),
    ({"name": "并发主名甲", "aliases": ["并发共同别名"]}, {"name": "并发主名乙", "aliases": ["并发共同别名"]}),
    ({"name": "并发主名甲", "aliases": ["并发共同别名"]}, {"name": "并发共同别名"}),
])
def test_concurrent_local_creation_reserves_writer_before_name_or_alias_check(db, payloads):
    # Use two independent file-backed engines/connections, not a shared-memory
    # pool.  Creator one holds its uncommitted insert while creator two starts.
    first_ready, second_attempt = Event(), Event()
    url = str(db.bind.url)
    initial_count = db.query(KnowledgePoint).count()

    def creator_one():
        engine = create_engine(url)
        try:
            with sessionmaker(bind=engine)() as session:
                point = create_local_point(session, payloads[0])
                first_ready.set()
                assert second_attempt.wait(timeout=5)
                session.commit()
                return ("created", point["code"])
        finally:
            engine.dispose()

    def creator_two():
        assert first_ready.wait(timeout=5)
        engine = create_engine(url)
        # Signal before the new writer reservation can block.  The SELECT hook
        # also lets this test expose the original check-then-insert race if the
        # reservation is removed: the old SELECT snapshots the missing point
        # before creator one commits, then inserts a second identity afterward.
        @event.listens_for(engine, "before_cursor_execute")
        def before_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.strip().upper() == "BEGIN IMMEDIATE":
                second_attempt.set()

        @event.listens_for(engine, "after_cursor_execute")
        def after_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT") and "FROM knowledge_points" in statement:
                second_attempt.set()

        try:
            with sessionmaker(bind=engine)() as session:
                try:
                    point = create_local_point(session, payloads[1])
                    session.commit()
                    return ("created", point["code"])
                except ValueError as exc:
                    session.rollback()
                    return ("duplicate", str(exc))
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(creator_one), workers.submit(creator_two)
        outcomes = [first.result(timeout=10), second.result(timeout=10)]
    assert outcomes[0][0] == "created"
    assert outcomes[1][0] == "duplicate" and "已有同名或同义" in outcomes[1][1]
    db.expire_all()
    assert db.query(KnowledgePoint).count() == initial_count + 1
    assert seed_catalog(db) == {"added_points": 0, "added_paths": 0}
    db.commit()
    # Caller rollback released creator two's reservation for later requests.
    another = create_local_point(db, {"name": "并发之后的下一知识点"})
    db.commit()
    assert db.get(KnowledgePoint, another["code"]).name == "并发之后的下一知识点"


def test_local_creation_keeps_existing_write_transaction_and_caller_rollback(db):
    question = Question(content="同事务未提交的题目")
    db.add(question)
    db.flush()
    assert db.connection().connection.dbapi_connection.in_transaction
    point = create_local_point(db, {"name": "同事务未提交的知识点"})
    assert db.connection().connection.dbapi_connection.in_transaction
    # The helper did not commit the caller's pre-existing write transaction.
    db.rollback()
    assert db.query(Question).count() == 0
    assert db.get(KnowledgePoint, point["code"]) is None
    assert seed_catalog(db) == {"added_points": 0, "added_paths": 0}
    db.commit()


@pytest.mark.parametrize("value", [None, "1015", ["1015"], [{"code": "missing"}], [{"code": "1015"}, {"code": "1015"}], [{"code": "1015", "role": "other"}], [{"code": "1015", "role": []}], [{"code": "1015", "status": "other"}], [{"code": "1015", "status": {}}], [{"code": "1015", "evidence": float("nan")}], [{"code": "1015", "evidence": "x" * 32769}], [{}], [True], [{}] * 65])
def test_invalid_selections_are_rejected(db, value):
    with pytest.raises(ValueError):
        normalize_question_points(value, db)


def test_question_storage_roundtrip_transaction_and_omitted_update_preserve_old_fields(db):
    question = Question(content="原题", category_compulsory="必修一", category_chapter="1. 集合与常用逻辑用语", category_knowledge="1.1 集合的概念", tags="期中,待讲")
    mapping = QuestionCurriculum(question=question, version_code="A", compulsory="必修一", chapter="1. 集合与常用逻辑用语", knowledge="1.1 集合的概念")
    mapping.sub_knowledge = [{"compulsory": "必修一", "chapter": "1. 集合与常用逻辑用语", "knowledge": "1.2 集合间的基本关系"}]
    db.add(question)
    db.flush()
    metadata = {"scope": ["(1)", "(2)"], "evidence_source": "题干", "evidence": {"excerpt": "集合A"}, "reason": "直接考查", "origin": "teacher"}
    points = normalize_question_points([{"code": "1015", **metadata}, {"code": "1016", "role": "auxiliary", "status": "suggested"}], db)
    replace_question_points(question, points, db)
    db.commit()
    db.expire_all()
    question = db.get(Question, question.id)
    assert question.to_dict()["knowledge_points"][0] == {"code": "1015", "name": "集合的含义", "role": "main", "status": "confirmed", **metadata}
    assert question.to_summary_dict()["knowledge_points"] == question.to_dict()["knowledge_points"]
    assert question.to_dict(sub_knowledge=[])["sub_knowledge"] == []
    question.content = "只更新正文，省略知识点"
    db.commit()
    assert [row.point_code for row in question.knowledge_points] == ["1015", "1016"]
    assert question.tags == "期中,待讲" and mapping.knowledge == "1.1 集合的概念"
    assert mapping.sub_knowledge[0]["knowledge"] == "1.2 集合间的基本关系"
    replace_question_points(question, normalize_question_points([{"code": "1016"}, {"code": "1015"}], db), db)
    db.commit()
    assert [row.point_code for row in question.knowledge_points] == ["1016", "1015"]
    replace_question_points(question, [], db)
    question.content = "回滚编辑"
    db.rollback()
    assert question.content == "只更新正文，省略知识点"
    assert len(question.knowledge_points) == 2
    replace_question_points(question, [], db)
    db.commit()
    assert question.knowledge_points == []


def _add_question(db, content, points, *, chapter="chapter"):
    question = Question(content=content, category_chapter=chapter)
    db.add(question)
    replace_question_points(question, normalize_question_points(points, db), db)
    db.flush()
    return question


def test_sql_any_all_role_status_filters_and_chapter_conditions(db):
    q1 = _add_question(db, "双主已确认", [{"code": "1015"}, {"code": "1016"}])
    q2 = _add_question(db, "主辅已确认", [{"code": "1015"}, {"code": "1016", "role": "auxiliary"}])
    q3 = _add_question(db, "主已确认及建议", [{"code": "1015"}, {"code": "1016", "status": "suggested"}])
    q4 = _add_question(db, "只有辅助", [{"code": "1015", "role": "auxiliary"}])
    q5 = _add_question(db, "只有建议", [{"code": "1015", "status": "suggested"}])
    q6 = _add_question(db, "没有知识点", [])
    q7 = _add_question(db, "另一章节", [{"code": "1015"}, {"code": "1016"}], chapter="other")
    db.commit()
    query = db.query(Question).filter(Question.category_chapter == "chapter")
    ids = lambda **kw: {q.id for q in apply_knowledge_point_filters(query, kw.pop("codes", ["1015", "1016"]), **kw)}
    assert ids() == {q1.id, q2.id, q3.id, q5.id}
    assert ids(match_mode="all") == {q1.id, q3.id}
    assert ids(match_mode="all", role_scope="all") == {q1.id, q2.id, q3.id}
    assert ids(status_scope="confirmed") == {q1.id, q2.id, q3.id}
    assert ids(match_mode="all", status_scope="confirmed") == {q1.id}
    assert ids(match_mode="all", status_scope="all") == {q1.id, q3.id}
    assert ids(role_scope="all", status_scope="all") == {q1.id, q2.id, q3.id, q4.id, q5.id}
    assert ids(codes=[]) == {q1.id, q2.id, q3.id, q4.id, q5.id, q6.id}
    assert ids(codes=[], unlabeled=True) == {q6.id}
    assert ids(codes=[], unlabeled=True, status_scope="confirmed") == {q5.id, q6.id}
    assert ids(codes=[], unlabeled=True, status_scope="all") == {q6.id}
    assert ids(codes=["missing"]) == set()
    assert q7.id not in ids(role_scope="all", status_scope="all")
    sql = str(apply_knowledge_point_filters(query, ["1015", "1016"], "all").statement)
    assert "GROUP BY" in sql and "HAVING" in sql and "question_knowledge_points" in sql
    with pytest.raises(ValueError, match="不能同时"):
        ids(unlabeled=True)


def test_database_constraints_reject_duplicate_unknown_invalid_and_delete_cascades(db):
    question = _add_question(db, "test", [{"code": "1015"}])
    db.commit()
    for values in ({"point_code": "1015"}, {"point_code": "unknown"}, {"point_code": "1016", "role": "invalid"}, {"point_code": "1016", "status": "invalid"}, {"point_code": "1016", "position": -1}):
        db.add(QuestionKnowledgePoint(question_id=question.id, **values))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    db.delete(question)
    db.commit()
    assert db.query(QuestionKnowledgePoint).count() == 0


def _v11_database(tmp_path):
    path = tmp_path / "v11.db"
    engine = create_engine(f"sqlite:///{path}")
    database.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table in (QuestionKnowledgePoint.__table__, KnowledgePointCurriculum.__table__, KnowledgePoint.__table__):
            table.drop(connection)
        connection.exec_driver_sql("PRAGMA user_version=11")
        connection.exec_driver_sql("INSERT INTO questions (id, content, category_knowledge, tags) VALUES (1, '原题内容', '历史小节', '待讲')")
        connection.exec_driver_sql("INSERT INTO question_curriculums (id, question_id, version_code, compulsory, chapter, knowledge) VALUES (1, 1, 'A', '必修一', '1. 集合与常用逻辑用语', '历史小节')")
        connection.exec_driver_sql("INSERT INTO question_sub_knowledge (curriculum_id, compulsory, chapter, knowledge) VALUES (1, '必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念')")
    return path, engine


def test_v11_migration_backs_up_before_adding_empty_relations_and_preserves_old_evidence(tmp_path, monkeypatch):
    path, engine = _v11_database(tmp_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    with engine.connect() as connection:
        before = {table: connection.exec_driver_sql(f"SELECT * FROM {table}").fetchall() for table in ("questions", "question_curriculums", "question_sub_knowledge")}
    result = db_migrations.migrate_database(engine)
    assert result["from_version"] == 11 and result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["backup"] and len(list((tmp_path / "snapshots").glob("*.sha256"))) == 1
    with sqlite3.connect(result["backup"]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 11
        assert backup.execute("SELECT content, category_knowledge, tags FROM questions").fetchone() == ("原题内容", "历史小节", "待讲")
    with engine.connect() as connection:
        for table, rows in before.items():
            assert connection.exec_driver_sql(f"SELECT * FROM {table}").fetchall() == rows
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM question_knowledge_points").scalar_one() == 0
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    assert db_migrations.migrate_database(engine)["backup"] is None
    engine.dispose()


def test_v11_migration_rolls_back_all_ddl_on_invalid_preexisting_point_table(tmp_path, monkeypatch):
    _, engine = _v11_database(tmp_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE question_knowledge_points (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="核心字段"):
        db_migrations.migrate_database(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 11
        names = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "knowledge_points" not in names and "knowledge_point_curriculums" not in names
        assert connection.exec_driver_sql("SELECT content FROM questions").scalar_one() == "原题内容"
    assert len(list((tmp_path / "snapshots").glob("*.db"))) == 1
    engine.dispose()


def test_fresh_database_init_seeds_only_catalogue_using_the_selected_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots")
    database.init_db()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == db_migrations.LATEST_SCHEMA_VERSION
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM knowledge_points").scalar_one() == len(knowledge_points._resource_points())
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM question_knowledge_points").scalar_one() == 0
    engine.dispose()
