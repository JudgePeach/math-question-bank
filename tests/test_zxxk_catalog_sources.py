"""Catalogue source identities stay namespaced and provenance-bound."""

from copy import deepcopy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mathbank import database, knowledge_points
from mathbank.curriculums import load_curriculum
from mathbank.database import KnowledgePoint


NEW_CODE = "zxxk:catalog:999990001"
ALIAS_CODE = "zxxk:catalog:999990002"


def source_resource():
    resource = json.loads(knowledge_points.CATALOG_FILE.read_text(encoding="utf-8"))
    source = resource.setdefault("source_catalogs", {}).get("zxxk_hujiao")
    if source:
        # Each test validates its own small source capture, not a production
        # path, while retaining the shipped compatibility identities.
        shipped_codes = {point["code"] for point in resource["points"] if point["source"] == "zxxk"}
        resource["points"] = [point for point in resource["points"] if point["code"] not in shipped_codes]
        resource["source_code_aliases"] = {code: canonical for code, canonical in resource["source_code_aliases"].items()
                                           if not knowledge_points.is_zxxk_catalog_code(code)}
        resource["source_catalogs"] = {}
    tree = load_curriculum("H")
    book = next(iter(tree))
    chapter = next(iter(tree[book]))
    section = tree[book][chapter][0]
    path = {"version_code": "H", "compulsory": book, "chapter": chapter, "knowledge": section,
            "reference_section": section, "level": "leaf"}
    old = next(point for point in resource["points"] if point["code"] == "1015")
    old_path = {**path, "reference_section": "集合的含义"}
    new_path = {**path, "reference_section": "独立目录范围"}
    if old_path not in old["curriculum_paths"]:
        old["curriculum_paths"].append(old_path)
    resource["points"].append({"code": NEW_CODE, "name": "测试独立目录范围", "original_name": "独立目录范围",
                               "source": "zxxk", "aliases": [], "curriculum_paths": [new_path]})
    resource["source_code_aliases"][ALIAS_CODE] = "1015"
    source_book = "沪教版必修 第一册"

    def mapping(code, name, canonical):
        return {"source_code": code, "source_name": name, "source_path": [source_book, chapter, name],
                "source_url": f"https://sx.zxxk.com/h/books-catalog{code.split(':')[-1]}/",
                "canonical_code": canonical, "curriculum_path": {**path, "reference_section": name}}

    resource["source_catalogs"] = {"zxxk_hujiao": {
        "source": "zxxk", "version_code": "H", "source_url": "https://sx.zxxk.com/h/kpoints/",
        "textbook_version_id": 837, "captured_at": "2026-10-19T04:46:57.069Z", "capture_sha256": "a" * 64,
        "books": [{"id": 5294, "name": source_book}],
        "mappings": [mapping(NEW_CODE, "独立目录范围", NEW_CODE), mapping(ALIAS_CODE, "集合的含义", "1015")],
        "unmapped": [{"source_id": 999990003, "source_name": "尚未审阅范围",
                      "source_path": [source_book, chapter, "尚未审阅范围"], "reason": "保留待审阅，不猜测对应知识点"}],
    }}
    resource["counts"]["points"] = len(resource["points"])
    resource["counts"]["platform_points"] = sum(point["source"] in {"zhixue", "zxxk"} for point in resource["points"])
    resource["counts"]["local_points"] = sum(point["source"] == "local" for point in resource["points"])
    resource["counts"]["source_code_aliases"] = len(resource["source_code_aliases"])
    paths = {}
    for point in resource["points"]:
        for row in point["curriculum_paths"]:
            paths[row["version_code"]] = paths.get(row["version_code"], 0) + 1
    resource["counts"]["paths_by_version"] = paths
    return resource


@pytest.fixture
def catalog_resource(tmp_path, monkeypatch):
    original = knowledge_points.CATALOG_FILE

    def load(resource):
        path = tmp_path / "knowledge_points.json"
        path.write_text(json.dumps(resource, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(knowledge_points, "CATALOG_FILE", path)
        knowledge_points._resource_points.cache_clear()
        return knowledge_points._resource_catalog()

    yield load
    monkeypatch.setattr(knowledge_points, "CATALOG_FILE", original)
    knowledge_points._resource_points.cache_clear()


def test_namespaced_catalogue_seed_search_and_preserved_stable_identity(catalog_resource, tmp_path):
    resource = source_resource()
    catalog = catalog_resource(resource)
    assert catalog["source_code_aliases"][ALIAS_CODE] == "1015"
    assert catalog["source_catalogs"] == resource["source_catalogs"]
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    database.Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine)() as db:
            knowledge_points.seed_catalog(db)
            db.commit()
            old = db.get(KnowledgePoint, "1015")
            old.name = "教师自己的集合名称"
            old.aliases = ["教师自己的别名"]
            db.commit()
            assert knowledge_points.seed_catalog(db) == {"added_points": 0, "added_paths": 0}
            assert old.source == "zhixue" and old.name == "教师自己的集合名称"
            assert old.aliases == ["教师自己的别名"]
            new = knowledge_points.list_catalog(db, version_code="H", q="#" + NEW_CODE)
            assert new["total"] == 1 and new["items"][0]["source_codes"] == [NEW_CODE]
            reused = knowledge_points.list_catalog(db, q="#" + ALIAS_CODE)
            assert reused["total"] == 1 and reused["items"][0]["code"] == "1015"
            assert ALIAS_CODE in reused["items"][0]["source_codes"]
            assert knowledge_points.list_catalog(db, q="#999990002")["total"] == 0
            assert knowledge_points.list_catalog(db, q="#zxxk:catalog:999")["total"] == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("problem", [
    "wrong_source", "raw_numeric_id", "zero_id", "leading_zero_id", "missing_provenance",
    "unknown_catalog", "wrong_version", "wrong_textbook_version", "boolean_book_id", "duplicate_book_id",
    "bad_capture_hash", "missing_timezone", "mismatched_url_id", "foreign_host", "unknown_canonical",
    "missing_alias", "wrong_alias_target", "unknown_h_section", "wrong_path_version", "path_not_owned",
    "extra_path_field", "wrong_source_name", "wrong_source_book", "duplicate_mapping", "unmapped_overlap",
    "wrong_original_name", "wrong_reference_name",
])
def test_resource_rejects_source_identity_and_provenance_conflicts(catalog_resource, problem):
    resource = source_resource()
    source = resource["source_catalogs"]["zxxk_hujiao"]
    mapping = source["mappings"][0]
    point = resource["points"][-1]
    if problem == "wrong_source":
        point["source"] = "zhixue"
    elif problem == "raw_numeric_id":
        point["code"] = "999990001"
    elif problem in {"zero_id", "leading_zero_id"}:
        point["code"] = "zxxk:catalog:" + ("0" if problem == "zero_id" else "01")
    elif problem == "missing_provenance":
        del resource["source_catalogs"]
    elif problem == "unknown_catalog":
        resource["source_catalogs"]["unknown"] = resource["source_catalogs"].pop("zxxk_hujiao")
    elif problem == "wrong_version":
        source["version_code"] = "A"
    elif problem == "wrong_textbook_version":
        source["textbook_version_id"] = 838
    elif problem == "boolean_book_id":
        source["books"][0]["id"] = True
    elif problem == "duplicate_book_id":
        source["books"].append(deepcopy(source["books"][0]))
    elif problem == "bad_capture_hash":
        source["capture_sha256"] = "not-a-hash"
    elif problem == "missing_timezone":
        source["captured_at"] = "2026-10-19T04:46:57"
    elif problem == "mismatched_url_id":
        mapping["source_url"] = "https://sx.zxxk.com/h/books-catalog999990004/"
    elif problem == "foreign_host":
        mapping["source_url"] = "https://other.example/h/books-catalog999990001/"
    elif problem == "unknown_canonical":
        mapping["canonical_code"] = "not-a-canonical-code"
    elif problem == "missing_alias":
        del resource["source_code_aliases"][ALIAS_CODE]
        resource["counts"]["source_code_aliases"] -= 1
    elif problem == "wrong_alias_target":
        resource["source_code_aliases"][ALIAS_CODE] = "1016"
    elif problem == "unknown_h_section":
        mapping["curriculum_path"]["knowledge"] = "不存在的真实小节"
    elif problem == "wrong_path_version":
        mapping["curriculum_path"] = deepcopy(resource["points"][0]["curriculum_paths"][0])
    elif problem == "path_not_owned":
        mapping["curriculum_path"]["reference_section"] += "（另一个来源）"
    elif problem == "extra_path_field":
        mapping["curriculum_path"]["source_node_id"] = 999990001
    elif problem == "wrong_source_name":
        mapping["source_name"] = "与路径不符"
    elif problem == "wrong_source_book":
        mapping["source_path"][0] = "未声明教材"
    elif problem == "duplicate_mapping":
        source["mappings"].append(deepcopy(mapping))
    elif problem == "unmapped_overlap":
        source["unmapped"][0].update(source_id=999990001)
    elif problem == "wrong_original_name":
        point["original_name"] = "改写后的原名"
    elif problem == "wrong_reference_name":
        mapping["curriculum_path"]["reference_section"] = "与来源叶名不同"
        point["curriculum_paths"] = [deepcopy(mapping["curriculum_path"])]
    with pytest.raises(ValueError):
        catalog_resource(resource)


def test_alias_namespace_never_collides_with_zhixue_or_forms_a_chain():
    aliases = {"999990001": "1015", ALIAS_CODE: "1016"}
    assert knowledge_points._normalize_source_code_aliases(aliases, {"1015", "1016"}) == aliases
    for invalid in ({NEW_CODE: "1015"}, {ALIAS_CODE: "unknown"}, {ALIAS_CODE: "999990001", "999990001": "1015"}):
        with pytest.raises(ValueError):
            knowledge_points._normalize_source_code_aliases(invalid, {"1015", "1016", NEW_CODE})


def test_sharing_accepts_namespaced_platform_definition_and_rejects_spoofed_codes():
    from mathbank.library_transfer import _validate_point
    point = {"code": NEW_CODE, "name": "目录点", "original_name": "目录点", "source": "zxxk",
             "aliases": [], "curriculum_paths": [], "role": "main", "status": "confirmed", "position": 0}
    _validate_point(point)
    for code in ("1015", "zxxk:catalog:0", "zxxk:catalog:01", "zxxk:knowledge:123", "zxxk:catalog:１２３"):
        with pytest.raises(ValueError, match="来源或编码"):
            _validate_point({**point, "code": code})
