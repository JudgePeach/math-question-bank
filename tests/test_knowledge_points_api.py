"""Independent knowledge-point API contracts on the isolated test database."""
import json

import pytest

from mathbank import knowledge_points
from mathbank.database import KnowledgePoint, KnowledgePointCurriculum, Question
from mathbank.knowledge_points import CATALOG_FILE, seed_catalog, normalize_question_points, replace_question_points


def form(content="知识点测试题", **extra):
    return {"content": content, "question_type": "detailed_answer", "difficulty": "easy_error",
            "category_compulsory": "选修二", "category_chapter": "4. 数列",
            "category_knowledge": "4.3 等比数列", "tags": "原标签", **extra}


@pytest.fixture
def catalog(db_session):
    seed_catalog(db_session)
    db_session.commit()
    return db_session


@pytest.fixture(autouse=True)
def authenticated_client(client):
    from main import LOCAL_TOKEN
    client.headers.update({"X-Local-Token": LOCAL_TOKEN})


def test_catalog_preserves_names_and_recommends_exact_section(client, catalog):
    data = client.get("/api/knowledge-points").json()
    assert data["total"] == json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["counts"]["points"]
    assert data["offset"] == 0 and data["limit"] == 700
    result = client.get("/api/knowledge-points", params={"version_code": "A", "compulsory": "选修二",
                        "chapter": "4. 数列", "knowledge": "4.3 等比数列"}).json()
    assert result["total"] == 15
    assert {row["code"] for row in result["items"]} >= {"1796", "1803"}
    other = client.get("/api/knowledge-points", params={"version_code": "B", "compulsory": "选修二"}).json()
    assert all(any(path["version_code"] == "B" and path["compulsory"] == "选修二"
                   for path in point["curriculum_paths"]) for point in other["items"])


def test_catalog_offset_pages_cover_large_catalogue_and_preserve_legacy_limit(client, catalog):
    catalog.add_all(KnowledgePoint(code=f"local:api-page-{index:04d}", name="API分页点", original_name="API分页点", source="local")
                    for index in range(500))
    catalog.commit()
    first = client.get("/api/knowledge-points", params={"limit": 1000}).json()
    second = client.get("/api/knowledge-points", params={"limit": 1000, "offset": 1000}).json()
    assert len(first["items"]) == 1000 and second["items"]
    assert first["offset"] == 0 and second["offset"] == 1000
    assert "hierarchy" in first and "hierarchy" not in second
    assert first["total"] == second["total"]
    codes = [point["code"] for point in first["items"] + second["items"]]
    assert len(codes) == len(set(codes)) == first["total"]
    assert len(client.get("/api/knowledge-points", params={"limit": 3}).json()["items"]) == 3
    assert client.get("/api/knowledge-points", params={"offset": -1}).status_code == 400
    assert client.get("/api/knowledge-points", params={"offset": 2**63}).status_code == 400
    assert client.get("/api/knowledge-points", params={"limit": 1001}).status_code == 400


def test_catalog_api_returns_explicit_hierarchy_and_parent_descendants(client, catalog, monkeypatch):
    resource = knowledge_points._resource_catalog()
    book, chapter = "必修一", "1. 集合与常用逻辑用语"
    parent, child = "1.1 集合的概念", "1.2 集合间的基本关系"
    hierarchy = [{"compulsory": book, "chapter": chapter, "knowledge": parent, "ancestors": []},
                 {"compulsory": book, "chapter": chapter, "knowledge": child, "ancestors": [parent]}]
    point = KnowledgePoint(code="local:api-hierarchy", name="API层级回归", original_name="API层级回归", source="local")
    point.curriculum_paths = [KnowledgePointCurriculum(version_code="A", compulsory=book, chapter=chapter,
                                                     knowledge=child, reference_section=child, level="leaf")]
    catalog.add(point)
    catalog.commit()
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {**resource, "hierarchies": {"A": hierarchy}})
    response = client.get("/api/knowledge-points", params={"version_code": "A", "q": "API层级回归",
                          "compulsory": book, "chapter": chapter, "knowledge": parent})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["hierarchy"] == hierarchy and data["items"][0]["code"] == point.code
    assert data["items"][0]["curriculum_paths"][0]["knowledge"] == child
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {**resource, "hierarchies": {}})
    assert client.get("/api/knowledge-points", params={"version_code": "A", "q": "API层级回归",
                      "compulsory": book, "chapter": chapter, "knowledge": parent}).json()["total"] == 0


def test_source_code_alias_api_search_is_exact_and_never_rekeys_a_saved_label(client, catalog, monkeypatch):
    resource = knowledge_points._resource_catalog()
    monkeypatch.setattr(knowledge_points, "_resource_catalog", lambda: {**resource, "source_code_aliases": {"9990001": "1015"}})
    data = client.get("/api/knowledge-points", params={"q": "#9990001"}).json()
    assert data["total"] == 1 and data["items"][0]["code"] == "1015"
    assert data["items"][0]["source_codes"] == ["1015", "9990001"]
    assert client.get("/api/knowledge-points", params={"q": "#999"}).json()["total"] == 0
    response = client.post("/api/questions", data=form(knowledge_points=json.dumps([{"code": "9990001"}])))
    assert response.status_code == 400
    assert catalog.query(Question).count() == 0


def test_local_point_creation_and_no_implicit_question_classification(client, catalog):
    response = client.post("/api/knowledge-points", json={"name": "我的分层练习考点", "aliases": ["分层考点"],
                           "curriculum_paths": [{"version_code": "A", "compulsory": "选修二", "chapter": "4. 数列",
                                                 "knowledge": "4.3 等比数列", "reference_section": "", "level": "leaf"}]})
    assert response.status_code == 200, response.text
    point = response.json()
    assert point["code"].startswith("local:") and point["source"] == "local"
    assert catalog.query(Question).count() == 0
    same = client.post("/api/knowledge-points", json={"name": "我的分层练习考点"})
    assert same.status_code == 400
    assert client.get("/api/knowledge-points", params={"q": "我的分层练习考点"}).json()["total"] == 1
    assert client.post("/api/knowledge-points", json={"name": ""}).status_code == 400


def test_save_explicit_points_omission_preserves_and_empty_clears(client, catalog):
    labels = [{"code": "1796", "role": "main", "status": "confirmed", "scope": ["第Ⅰ问"],
               "origin": {"kind": "reviewed_research_candidate"}},
              {"code": "1803", "role": "auxiliary", "status": "suggested"}]
    response = client.post("/api/questions", data=form(knowledge_points=json.dumps(labels)))
    assert response.status_code == 200, response.text
    qid = response.json()["question"]["id"]
    initial = client.get(f"/api/questions/{qid}").json()["knowledge_points"]
    assert len(initial) == 2 and initial[0]["origin"] == labels[0]["origin"]
    response = client.put(f"/api/questions/{qid}", data=form(content="只修改正文"))
    assert response.status_code == 200, response.text
    after = client.get(f"/api/questions/{qid}").json()
    assert after["knowledge_points"] == initial and after["tags"] == "原标签"
    assert after["category_knowledge"] == "4.3 等比数列"
    assert client.put(f"/api/questions/{qid}", data=form(knowledge_points="[]")).status_code == 200
    assert client.get(f"/api/questions/{qid}").json()["knowledge_points"] == []


@pytest.mark.parametrize("value", ["broken-json", "{}", '[{"code":"not-present"}]',
                                  '[{"code":"1796","role":"wrong"}]'])
def test_invalid_points_never_partially_create_question(client, catalog, value):
    before = catalog.query(Question).count()
    response = client.post("/api/questions", data=form(knowledge_points=value))
    assert response.status_code == 400
    assert catalog.query(Question).count() == before


def test_query_is_exact_combined_and_status_aware(client, catalog):
    examples = [
        ("甲", "4. 数列", [{"code": "1796", "role": "main"}, {"code": "1803", "role": "main"}]),
        ("乙", "4. 数列", [{"code": "1796", "role": "main"}, {"code": "1803", "role": "auxiliary"}]),
        ("丙", "5. 一元函数的导数及其应用", [{"code": "1796", "role": "main", "status": "suggested"}]),
        ("丁", "4. 数列", []),
    ]
    ids = []
    for content, chapter, labels in examples:
        q = Question(content=content, question_type="detailed_answer", category_compulsory="选修二", category_chapter=chapter)
        catalog.add(q)
        replace_question_points(q, normalize_question_points(labels, catalog), catalog)
        catalog.flush()
        ids.append(q.id)
    catalog.commit()
    def query(**kwargs):
        pairs = [("knowledge_point_codes", "1796"), ("knowledge_point_codes", "1803"), ("page", 1)]
        pairs += list(kwargs.items())
        response = client.get("/api/questions", params=pairs)
        assert response.status_code == 200, response.text
        return {row["id"] for row in response.json()["items"]}
    assert query() == set(ids[:3])
    assert query(knowledge_point_status="confirmed") == {ids[0], ids[1]}
    assert query(knowledge_point_mode="all") == {ids[0]}
    assert query(knowledge_point_mode="all", knowledge_point_role="all") == {ids[0], ids[1]}
    assert query(knowledge_point_status="all") == set(ids[:3])
    assert query(chapter="5. 一元函数的导数及其应用") == {ids[2]}
    unmarked = client.get("/api/questions", params={"knowledge_point_unlabeled": True, "page": 1}).json()
    assert {row["id"] for row in unmarked["items"]} == {ids[3]}
    assert client.get("/api/questions", params={"knowledge_point_mode": "bad"}).status_code == 400
    assert client.get("/api/questions", params=[("knowledge_point_codes", "1796"),
               ("knowledge_point_unlabeled", "true")]).status_code == 400


def test_ai_local_fallback_keeps_point_filter_when_pool_is_empty(client, catalog):
    catalog.add(Question(content="没有关联的题目", question_type="detailed_answer"))
    catalog.commit()
    response = client.post("/api/paper/ai-select", json={"prompt": "", "knowledge_point_codes": ["1796"], "limit": 5})
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    invalid = client.post("/api/paper/ai-select", json={"prompt": "", "knowledge_point_mode": "bad"})
    assert invalid.status_code == 400
