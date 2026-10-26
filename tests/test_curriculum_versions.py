"""Explicit preset identity and conservative routing for six curricula."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import threading
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from mathbank.curriculums import (CURRICULUM_NAMES, build_default_metadata, get_curriculum_preset,
                                 build_curriculum_routing_index, route_curriculum_location)
from mathbank.database import (Question, QuestionCurriculum, KnowledgePoint, KnowledgePointCurriculum,
                               QuestionKnowledgePoint)
from mathbank.sub_knowledge import detect_curriculum_version, resolve_active_version


VERSIONS = ("A", "B", "S", "H", "N", "X")


@pytest.mark.parametrize("version", VERSIONS)
def test_each_preset_keeps_explicit_identity(version):
    preset = get_curriculum_preset(version)
    assert preset["name"] == CURRICULUM_NAMES[version]
    assert preset["metadata"]["curriculum_version"] == version
    assert detect_curriculum_version(preset["metadata"], strict=True) == version
    edited = deepcopy(preset["metadata"])
    edited["curriculum"] = {"教师自编册": {"修改后的章节": []}}
    assert detect_curriculum_version(edited, strict=True) == version


@pytest.mark.parametrize("version", VERSIONS)
def test_explicit_identity_wins_over_ambiguous_chapter_shape(version, tmp_path):
    metadata = {"curriculum_version": version.lower(), "curriculum": {"册": {"第一章 集合": []}}}
    assert detect_curriculum_version(metadata) == version
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    assert resolve_active_version(path, strict=True) == version


@pytest.mark.parametrize("version", ("A", "B", "S", "H"))
def test_legacy_metadata_without_identity_remains_compatible(version):
    metadata = build_default_metadata(version)
    metadata.pop("curriculum_version")
    assert detect_curriculum_version(metadata, strict=True) == version


@pytest.mark.parametrize("value", (None, "", "   ", "unknown", 1, False, [], {}))
def test_invalid_explicit_identity_never_falls_back_to_a(value, tmp_path):
    metadata = {"curriculum_version": value, "curriculum": {"必修一": {"1. 集合": []}}}
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    for strict in (False, True):
        with pytest.raises(ValueError):
            detect_curriculum_version(metadata, strict=strict)
        with pytest.raises(ValueError):
            resolve_active_version(path, strict=strict)


@pytest.fixture
def metadata_scope(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "METADATA_CACHE", build_default_metadata("A"))
    monkeypatch.setattr(main, "METADATA_FILE", str(tmp_path / "metadata.json"))
    monkeypatch.setattr(main, "schedule_database_export", lambda *args, **kwargs: None)
    return main


def test_metadata_api_rejects_invalid_identity_before_writing(client, db_session, metadata_scope):
    main = metadata_scope
    original = deepcopy(main.METADATA_CACHE)
    question = Question(content="原始题文", category_compulsory="原册", category_chapter="原章", category_knowledge="原节")
    db_session.add(question)
    db_session.commit()
    before = (question.category_compulsory, question.category_chapter, question.category_knowledge)
    response = client.post("/api/config/metadata", json={**original, "curriculum_version": "unknown"},
                           headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 400
    assert main.METADATA_CACHE == original
    assert not Path(main.METADATA_FILE).exists()
    assert db_session.query(QuestionCurriculum).count() == 0
    db_session.refresh(question)
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == before


def test_same_version_directory_edit_preserves_existing_mirrors(client, db_session, metadata_scope):
    main = metadata_scope
    question = Question(content="原始题文", category_compulsory="历史册", category_chapter="历史章", category_knowledge="历史小节")
    question.curriculum_mappings.append(QuestionCurriculum(version_code="A", compulsory="历史册", chapter="历史章", knowledge="历史小节"))
    db_session.add(question)
    db_session.commit()
    original = question.curriculum_mappings[0].to_dict()
    metadata = build_default_metadata("A")
    metadata["curriculum"] = {"教师修改册": {"修改后的章节名": ["修改后的小节名"]}}
    response = client.post("/api/config/metadata", json=metadata,
                           headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200, response.text
    db_session.refresh(question)
    assert question.curriculum_mappings[0].to_dict() == original
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == ("历史册", "历史章", "历史小节")
    assert json.loads(Path(main.METADATA_FILE).read_text(encoding="utf-8"))["curriculum_version"] == "A"


def test_same_version_save_does_not_clear_main_only_legacy_position(client, db_session, metadata_scope):
    main = metadata_scope
    question = Question(content="历史题文", category_compulsory="历史册", category_chapter="历史章", category_knowledge="历史节")
    db_session.add(question)
    db_session.commit()
    response = client.post("/api/config/metadata", json=build_default_metadata("A"), headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200
    db_session.refresh(question)
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == ("历史册", "历史章", "历史节")


@pytest.mark.parametrize("version", ("N", "X"))
def test_new_routes_require_exact_paths_and_leave_unknowns_empty(version, metadata_scope):
    main = metadata_scope
    target = {"对应册": {"完整章节名称": ["完整小节名称"]}}
    assert main.route_chapter("对应册", "完整章节名称", "完整小节名称", version, target_curriculum=target) == ("对应册", "完整章节名称", "完整小节名称")
    assert main.route_chapter("旧册", "完整章节名称", "完整小节名称", version, target_curriculum=target) == ("对应册", "完整章节名称", "完整小节名称")
    assert main.route_chapter("旧册", "指数与对数不等式", "导数方法", version, target_curriculum=target) == ("", "", "")
    ambiguous = {**target, "另一册": {"完整章节名称": ["完整小节名称"]}}
    assert main.route_chapter("旧册", "完整章节名称", "完整小节名称", version, target_curriculum=ambiguous) == ("", "", "")


@pytest.mark.parametrize("version", ("A", "B", "S", "H"))
def test_unknown_legacy_routes_do_not_default_to_set_theory(version, metadata_scope):
    main = metadata_scope
    assert main.route_chapter("历史册", "待核对的专门专题", "", version) == ("", "", "")


ROUTING_TREE = {"目标册": {"第10章 先修内容": ["甲小节", "乙小节"], "第2章 后修内容": ["丙小节", "丁小节"]}}


def routing_paths():
    def path(code, version, chapter, knowledge):
        return {"point_code": code, "version_code": version, "compulsory": "原册" if version == "A" else "目标册",
                "chapter": chapter, "knowledge": knowledge}
    return [path("one", "A", "原甲章", "原甲节"), path("one", "N", "第10章 先修内容", "甲小节"),
            path("two", "A", "原乙章", "原乙节"), path("two", "N", "第2章 后修内容", "丙小节"),
            path("multi", "N", "第2章 后修内容", "丙小节"), path("multi", "N", "第2章 后修内容", "丁小节"),
            path("alternative", "N", "第10章 先修内容", "乙小节"), path("alternative", "N", "第2章 后修内容", "丁小节")]


def test_source_crosswalk_and_main_points_keep_ambiguity_and_directory_order():
    index = build_curriculum_routing_index(routing_paths(), "A", "N", ROUTING_TREE)
    assert route_curriculum_location("", "", "", index, point_codes=["one"]) == ("目标册", "第10章 先修内容", "甲小节")
    assert route_curriculum_location("", "", "", index, point_codes=["one", "two"]) == ("目标册", "第2章 后修内容", "丙小节")
    assert route_curriculum_location("", "", "", index, point_codes=["multi"]) == ("目标册", "第2章 后修内容", "")
    assert route_curriculum_location("", "", "", index, point_codes=["alternative"]) == ("", "", "")
    assert route_curriculum_location("", "", "", index, point_codes=["one", "alternative"]) == ("", "", "")
    assert route_curriculum_location("", "", "", index, point_codes=["two", "alternative"]) == ("目标册", "第2章 后修内容", "")
    assert route_curriculum_location("原册", "原甲章", "原甲节", index) == ("目标册", "第10章 先修内容", "甲小节")
    assert route_curriculum_location("原册", "完全未知章", "", index) == ("", "", "")
    invalid = routing_paths() + [{"point_code": "bad", "version_code": "N", "compulsory": "目标册",
                                 "chapter": "第10章 先修内容", "knowledge": "伪造小节"}]
    invalid_index = build_curriculum_routing_index(invalid, "A", "N", ROUTING_TREE)
    assert route_curriculum_location("", "", "", invalid_index, point_codes=["bad"]) == ("", "", "")


def test_one_point_with_multiple_uses_cannot_push_source_section_to_later_chapter():
    paths = routing_paths() + [{"point_code": "alternative", "version_code": "A", "compulsory": "原册",
                                "chapter": "跨用途原章", "knowledge": "同一个源小节"}]
    index = build_curriculum_routing_index(paths, "A", "N", ROUTING_TREE)
    for codes in ([], ["alternative"], ["alternative", "alternative"]):
        assert route_curriculum_location("原册", "跨用途原章", "同一个源小节", index, point_codes=codes) == ("", "", "")
    assert route_curriculum_location("原册", "跨用途原章", "", index) == ("", "", "")


def test_unmapped_source_section_never_borrows_neighbouring_target_leaf():
    paths = routing_paths() + [{"point_code": "unmapped", "version_code": "A", "compulsory": "原册",
                                "chapter": "原甲章", "knowledge": "已知未映射小节"}]
    index = build_curriculum_routing_index(paths, "A", "N", ROUTING_TREE)
    assert route_curriculum_location("原册", "原甲章", "已知未映射小节", index) == ("", "", "")
    # Coarse or historical custom source values can support a chapter at most.
    assert route_curriculum_location("原册", "原甲章", "", index) == ("目标册", "第10章 先修内容", "")
    assert route_curriculum_location("原册", "原甲章", "历史自定义小节", index) == ("目标册", "第10章 先修内容", "")
    # A real confirmed main point remains stronger evidence for a precise leaf.
    assert route_curriculum_location("原册", "原甲章", "历史自定义小节", index, point_codes=["one"]) == ("目标册", "第10章 先修内容", "甲小节")


def test_parent_paths_support_a_chapter_and_only_proven_ancestors_are_redundant():
    tree = {"目标册": {"目标章": ["父节", "细小节", "另一父节"]}}
    def path(level, section, reference=None):
        return {"point_code": "same", "version_code": "N", "compulsory": "目标册", "chapter": "目标章",
                "knowledge": section, "reference_section": reference or section or "目标章", "level": level}
    def route(paths, **kwargs):
        index = build_curriculum_routing_index(paths, "A", "N", tree, **kwargs)
        return route_curriculum_location("", "", "", index, point_codes=["same"])
    parent, leaf = path("parent", "父节"), path("leaf", "细小节")
    assert route([parent]) == ("目标册", "目标章", "")
    assert route([path("chapter", "")]) == ("目标册", "目标章", "")
    assert route([leaf]) == ("目标册", "目标章", "细小节")
    assert route([parent, leaf]) == ("目标册", "目标章", "")
    assert route([path("chapter", ""), leaf]) == ("目标册", "目标章", "细小节")
    ancestors = {("目标册", "目标章", "细小节"): ["父节"]}
    assert route([parent, leaf], target_ancestors=ancestors) == ("目标册", "目标章", "细小节")
    assert route([path("parent", "另一父节"), leaf], target_ancestors=ancestors) == ("目标册", "目标章", "")
    assert route([parent, {**leaf, "ancestors": ["父节"]}]) == ("目标册", "目标章", "细小节")
    other_tree = {**tree, "另一册": {"后续章": ["后续叶节"]}}
    other_leaf = {**leaf, "compulsory": "另一册", "chapter": "后续章", "knowledge": "后续叶节", "reference_section": "后续叶节"}
    index = build_curriculum_routing_index([path("chapter", ""), other_leaf], "A", "N", other_tree)
    assert route_curriculum_location("", "", "", index, point_codes=["same"]) == ("", "", "")


def test_source_crosswalk_cannot_turn_parent_recommendation_into_precise_leaf():
    paths = [{"point_code": "same", "version_code": "A", "compulsory": "原册", "chapter": "原章",
              "knowledge": "原末节", "level": "leaf"},
             {"point_code": "same", "version_code": "N", "compulsory": "目标册", "chapter": "目标章",
              "knowledge": "父节", "reference_section": "父节", "level": "parent"}]
    index = build_curriculum_routing_index(paths, "A", "N", {"目标册": {"目标章": ["父节", "细小节"]}})
    assert route_curriculum_location("原册", "原章", "原末节", index) == ("目标册", "目标章", "")
    assert route_curriculum_location("原册", "原章", "原末节", index, point_codes=["same"]) == ("目标册", "目标章", "")


def test_equal_local_paths_remove_source_parent_redundancy_without_guessed_hierarchy():
    tree = build_default_metadata("A")["curriculum"]
    location = {"point_code": "same", "version_code": "A", "compulsory": "选修二",
                "chapter": "5. 一元函数的导数及其应用", "knowledge": "5.3 导数在研究函数中的应用"}
    paths = [{**location, "reference_section": "5.3 导数在研究函数中的应用", "level": "parent"},
             {**location, "reference_section": "5.3.1 函数的单调性", "level": "leaf"}]
    index = build_curriculum_routing_index(paths, "N", "A", tree)
    assert route_curriculum_location("", "", "", index, point_codes=["same"]) == (
        "选修二", "5. 一元函数的导数及其应用", "5.3 导数在研究函数中的应用")
    # A lone parent still does not prove any of its finer source uses.
    index = build_curriculum_routing_index(paths[:1], "N", "A", tree)
    assert route_curriculum_location("", "", "", index, point_codes=["same"]) == (
        "选修二", "5. 一元函数的导数及其应用", "")


def test_ancestry_uses_raw_source_references_instead_of_display_whitespace():
    tree = {"目标册": {"目标章": ["1.1 父节", "1.1.1 子节"]}}
    paths = [{"point_code": "same", "version_code": "N", "compulsory": "目标册", "chapter": "目标章",
              "knowledge": "1.1 父节", "reference_section": "1.1父节  ", "level": "parent"},
             {"point_code": "same", "version_code": "N", "compulsory": "目标册", "chapter": "目标章",
              "knowledge": "1.1.1 子节", "reference_section": "1.1.1 子节    ", "level": "leaf"}]
    registry = {("目标册", "目标章", "1.1.1 子节    "): ["1.1父节  "]}
    index = build_curriculum_routing_index(paths, "A", "N", tree, target_ancestors=registry)
    assert route_curriculum_location("", "", "", index, point_codes=["same"]) == ("目标册", "目标章", "1.1.1 子节")


def test_verified_parent_section_cannot_be_reported_as_terminal_by_a_leaf_flag_or_exact_fallback():
    tree = {"目标册": {"目标章": ["1.1 父节", "1.1.1 子节"]}}
    registry = {("目标册", "目标章", "1.1.1子节 "): ["1.1父节  "]}
    # Historical/user metadata can call a path "leaf".  The verified source
    # hierarchy remains the authority about whether it is really terminal.
    path = {"point_code": "same", "version_code": "N", "compulsory": "目标册", "chapter": "目标章",
            "knowledge": "1.1 父节", "reference_section": "1.1父节  ", "level": "leaf"}
    index = build_curriculum_routing_index([path], "A", "N", tree, target_ancestors=registry)
    assert route_curriculum_location("", "", "", index, point_codes=["same"]) == ("目标册", "目标章", "")
    index = build_curriculum_routing_index([], "A", "N", tree, target_ancestors=registry)
    assert route_curriculum_location("目标册", "目标章", "1.1 父节", index) == ("目标册", "目标章", "")
    assert route_curriculum_location("旧册", "目标章", "1.1 父节", index) == ("目标册", "目标章", "")
    assert route_curriculum_location("目标册", "目标章", "1.1.1 子节", index) == ("目标册", "目标章", "1.1.1 子节")


def test_switch_uses_all_main_points_and_round_trip_preserves_data(client, db_session, metadata_scope):
    main = metadata_scope
    for code in ("one", "two", "multi", "alternative"):
        point = KnowledgePoint(code=code, name=code, original_name=code, source="local")
        point.aliases = []
        db_session.add(point)
    db_session.flush()
    for path in routing_paths():
        db_session.add(KnowledgePointCurriculum(**path, reference_section=path["knowledge"], level="leaf"))
    questions = []
    cases = [("unique", ["one"], ("原册", "原甲章", "原甲节")),
             ("cross_section", ["multi"], ("自定义册", "未知章", "未知节")),
             ("combined", ["one", "two"], ("自定义册", "未知章", "未知节")),
             ("ambiguous", ["alternative"], ("自定义册", "未知章", "未知节")),
             ("unlabeled", [], ("原册", "原甲章", "原甲节")),
             ("unknown", [], ("自定义册", "未知章", "未知节")),
             ("suggested", ["one"], ("原册", "原甲章", "原甲节")),
             ("auxiliary", ["one"], ("原册", "原甲章", "原甲节"))]
    for name, codes, location in cases:
        question = Question(content=f"原题文 {name}", answer_markdown="原解析", tags="原标签",
                            category_compulsory=location[0], category_chapter=location[1], category_knowledge=location[2])
        question.curriculum_mappings.append(QuestionCurriculum(version_code="A", compulsory=location[0], chapter=location[1], knowledge=location[2]))
        db_session.add(question)
        db_session.flush()
        for position, code in enumerate(codes):
            db_session.add(QuestionKnowledgePoint(question_id=question.id, point_code=code, role="main", status="confirmed",
                                                position=position, evidence='{"保留":"原依据"}', reason='"原理由"'))
        if name in {"suggested", "auxiliary"}:
            db_session.add(QuestionKnowledgePoint(question_id=question.id, point_code="two", role="auxiliary" if name == "auxiliary" else "main",
                                                status="confirmed" if name == "auxiliary" else "suggested", position=1))
        questions.append(question)
    db_session.commit()
    original_a = [row.to_dict() for row in db_session.query(QuestionCurriculum).filter_by(version_code="A").order_by(QuestionCurriculum.id)]
    original_labels = [tuple(getattr(row, field) for field in ("id", "question_id", "point_code", "role", "status", "position", "scope", "evidence_source", "evidence", "reason", "origin"))
                       for row in db_session.query(QuestionKnowledgePoint).order_by(QuestionKnowledgePoint.id)]
    original_text = [(q.content, q.answer_markdown, q.tags) for q in questions]
    metadata = {"question_types": [], "difficulties": [], "curriculum_version": "N", "curriculum": ROUTING_TREE}
    response = client.post("/api/config/metadata", json=metadata, headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200, response.text
    summary = response.json()["classification_switch"]
    assert summary["created"] == 8 and summary["section"] == 5 and summary["chapter"] == 1 and summary["unclassified"] == 2
    positions = [(q.category_compulsory, q.category_chapter, q.category_knowledge) for q in questions]
    assert positions[0] == positions[4] == positions[7] == ("目标册", "第10章 先修内容", "甲小节")
    assert positions[1] == ("目标册", "第2章 后修内容", "")
    # Historical suggested main points participate in the same routing as
    # confirmed main points; auxiliary methods still cannot move placement.
    assert positions[2] == positions[6] == ("目标册", "第2章 后修内容", "丙小节")
    assert positions[3] == positions[5] == ("", "", "")
    # A manually edited N mirror wins on a later return, even if it is outside
    # the now-selected directory.  Its evidence is user data.
    n_mirror = db_session.query(QuestionCurriculum).filter_by(question_id=questions[0].id, version_code="N").one()
    n_mirror.compulsory, n_mirror.chapter, n_mirror.knowledge = "N自定义册", "N历史章", "N历史节"
    db_session.commit()
    assert client.post("/api/config/metadata", json=build_default_metadata("A"), headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 200
    assert [row.to_dict() for row in db_session.query(QuestionCurriculum).filter_by(version_code="A").order_by(QuestionCurriculum.id)] == original_a
    assert [(q.category_compulsory, q.category_chapter, q.category_knowledge) for q in questions] == [location for _, _, location in cases]
    assert client.post("/api/config/metadata", json=metadata, headers={"X-Local-Token": main.LOCAL_TOKEN}).json()["classification_switch"]["created"] == 0
    assert (questions[0].category_compulsory, questions[0].category_chapter, questions[0].category_knowledge) == ("N自定义册", "N历史章", "N历史节")
    assert [(q.content, q.answer_markdown, q.tags) for q in questions] == original_text
    assert [tuple(getattr(row, field) for field in ("id", "question_id", "point_code", "role", "status", "position", "scope", "evidence_source", "evidence", "reason", "origin"))
            for row in db_session.query(QuestionKnowledgePoint).order_by(QuestionKnowledgePoint.id)] == original_labels


def test_switch_preserves_22_b_and_21_s_existing_mirrors_and_main_only_legacy_source(client, db_session, metadata_scope):
    main = metadata_scope
    for number in range(24):
        question = Question(content=f"保留题文 {number}", category_compulsory="A原册", category_chapter="A原章", category_knowledge=f"A原节{number}")
        db_session.add(question)
        db_session.flush()
        if number < 22:
            db_session.add(QuestionCurriculum(question_id=question.id, version_code="B", compulsory="B历史册", chapter="B历史章", knowledge=f"B原节{number}"))
        if number < 21:
            db_session.add(QuestionCurriculum(question_id=question.id, version_code="S", compulsory="S历史册", chapter="S历史章", knowledge=f"S原节{number}"))
    db_session.commit()
    originals = [row.to_dict() for row in db_session.query(QuestionCurriculum).order_by(QuestionCurriculum.id)]
    for version, count in (("B", 22), ("S", 21), ("A", 24)):
        response = client.post("/api/config/metadata", json=build_default_metadata(version), headers={"X-Local-Token": main.LOCAL_TOKEN})
        assert response.status_code == 200, response.text
        assert response.json()["classification_switch"]["restored_existing"] == count
    assert [row.to_dict() for row in db_session.query(QuestionCurriculum).filter(QuestionCurriculum.id.in_([item["id"] for item in originals])).order_by(QuestionCurriculum.id)] == originals
    assert [(q.category_compulsory, q.category_chapter, q.category_knowledge)
            for q in db_session.query(Question).order_by(Question.id)] == [("A原册", "A原章", f"A原节{number}") for number in range(24)]


def test_first_switch_keeps_legacy_null_and_uses_bounded_selects(client, db_session, metadata_scope):
    main = metadata_scope
    db_session.add_all(Question(content=f"原文 {number}", category_compulsory="历史册", category_chapter="历史章", category_knowledge="")
                       for number in range(120))
    db_session.commit()
    db_session.execute(text("UPDATE questions SET category_knowledge=NULL WHERE id=(SELECT MIN(id) FROM questions)"))
    db_session.commit()
    selects = []
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)
    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        metadata = {"question_types": [], "difficulties": [], "curriculum_version": "N", "curriculum": ROUTING_TREE}
        response = client.post("/api/config/metadata", json=metadata, headers={"X-Local-Token": main.LOCAL_TOKEN})
        assert response.status_code == 200, response.text
        assert response.json()["classification_switch"]["preserved_source_mirrors"] == 120
        assert len(selects) <= 8  # Independent of the number of questions.
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert client.post("/api/config/metadata", json=build_default_metadata("A"), headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 200
    first = db_session.query(Question).order_by(Question.id).first()
    db_session.refresh(first)
    assert first.category_knowledge is None
    assert (first.category_compulsory, first.category_chapter) == ("历史册", "历史章")


def test_existing_target_mirror_preserves_mixed_null_and_empty_values(client, db_session, metadata_scope):
    main = metadata_scope
    question = Question(content="原题文", category_compulsory="A原册", category_chapter="A原章", category_knowledge="A原节")
    question.curriculum_mappings.extend([
        QuestionCurriculum(version_code="A", compulsory="A原册", chapter="A原章", knowledge="A原节"),
        QuestionCurriculum(version_code="B", compulsory="", chapter="", knowledge=""),
    ])
    db_session.add(question)
    db_session.commit()
    db_session.execute(text("UPDATE question_curriculums SET compulsory=NULL,knowledge=NULL WHERE version_code='B'"))
    db_session.commit()
    response = client.post("/api/config/metadata", json=build_default_metadata("B"), headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200, response.text
    summary = response.json()["classification_switch"]
    assert summary["restored_existing"] == 1 and summary["created"] == 0
    db_session.refresh(question)
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == (None, "", None)
    assert client.post("/api/config/metadata", json=build_default_metadata("A"), headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 200
    db_session.refresh(question)
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == ("A原册", "A原章", "A原节")
    mirror = db_session.query(QuestionCurriculum).filter_by(version_code="B").one()
    db_session.refresh(mirror)
    assert (mirror.compulsory, mirror.chapter, mirror.knowledge) == (None, "", None)


@pytest.mark.parametrize("failure", ("commit", "metadata_file"))
def test_failed_first_switch_rolls_back_new_mirrors_main_fields_cache_and_metadata(client, db_session, metadata_scope, monkeypatch, failure):
    main = metadata_scope
    question = Question(content="原文保留", answer_markdown="原解析", category_compulsory="A原册", category_chapter="A原章", category_knowledge="A原节")
    db_session.add(question)  # No source mirror: both source snapshot and target are new writes.
    db_session.commit()
    original_cache = deepcopy(main.METADATA_CACHE)
    original_file = json.dumps(original_cache, ensure_ascii=False, indent=3) + "\n"
    Path(main.METADATA_FILE).write_text(original_file, encoding="utf-8")
    if failure == "commit":
        monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("injected commit failure")))
    else:
        monkeypatch.setattr(main, "write_private_text_atomic", lambda *args: (_ for _ in ()).throw(OSError("injected write failure")))
    response = client.post("/api/config/metadata", json=build_default_metadata("N"), headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 500
    assert main.METADATA_CACHE == original_cache
    assert Path(main.METADATA_FILE).read_text(encoding="utf-8") == original_file
    assert db_session.query(QuestionCurriculum).count() == 0
    db_session.refresh(question)
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == ("A原册", "A原章", "A原节")
    assert (question.content, question.answer_markdown) == ("原文保留", "原解析")


def test_concurrent_switches_serialize_file_cache_and_mirrors(db_session, metadata_scope, monkeypatch):
    main = metadata_scope
    question = Question(content="原文不变", category_compulsory="A原册", category_chapter="A原章", category_knowledge="A原节")
    question.curriculum_mappings.append(QuestionCurriculum(version_code="A", compulsory="A原册", chapter="A原章", knowledge="A原节"))
    db_session.add(question)
    db_session.commit()
    original_save = main._save_metadata_config
    guard = threading.Lock()
    active, peak = 0, 0
    def tracked_save(*args):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)  # Ensure overlapping requests reach the guard.
            return original_save(*args)
        finally:
            with guard:
                active -= 1
    monkeypatch.setattr(main, "_save_metadata_config", tracked_save)
    barrier = threading.Barrier(2)
    session_factory = sessionmaker(bind=db_session.get_bind())
    def switch(version):
        with session_factory() as session:
            barrier.wait()
            return main.save_metadata_config(
                {"question_types": [], "difficulties": [], "curriculum_version": version, "curriculum": ROUTING_TREE},
                main.BackgroundTasks(), session,
            )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(switch, version) for version in ("N", "X")]
        assert all(future.result()["status"] == "success" for future in futures)
    assert peak == 1
    assert json.loads(Path(main.METADATA_FILE).read_text(encoding="utf-8")) == main.METADATA_CACHE
    db_session.expire_all()
    assert {row.version_code for row in db_session.query(QuestionCurriculum)} == {"A", "N", "X"}
    assert db_session.query(Question).one().content == "原文不变"


@pytest.mark.parametrize("method", ("post", "put"))
@pytest.mark.parametrize("first", ("write", "switch"))
def test_old_version_question_write_and_switch_cannot_cross_versions(client, db_session, metadata_scope, monkeypatch, method, first):
    main = metadata_scope
    source = build_default_metadata("A")["curriculum"]
    book, chapter, section = next((book, chapter, sections[0]) for book, chapters in source.items()
                                  for chapter, sections in chapters.items() if sections)
    form = {"content": "原编辑题文", "question_type": "single_choice", "difficulty": "normal",
            "category_compulsory": book, "category_chapter": chapter, "category_knowledge": section,
            "expected_version_code": "A", "image_paths": "[]"}
    headers = {"X-Local-Token": main.LOCAL_TOKEN}
    question_id = None
    if method == "put":
        response = client.post("/api/questions", data=form, headers=headers)
        assert response.status_code == 200, response.text
        question_id = response.json()["question"]["id"]
    form = {**form, "content": "本次写入题文"}
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    if first == "write":
        original = main.require_expected_curriculum_version
        def pause(expected):
            original(expected)
            entered.set()
            assert release.wait(5)
        monkeypatch.setattr(main, "require_expected_curriculum_version", pause)
    else:
        original = main._save_metadata_config
        def pause(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(main, "_save_metadata_config", pause)
    def write():
        return getattr(client, method)("/api/questions" if method == "post" else f"/api/questions/{question_id}", data=form, headers=headers)
    def switch():
        return client.post("/api/config/metadata", json=build_default_metadata("N"), headers=headers)
    first_action, second_action = (write, switch) if first == "write" else (switch, write)
    def second():
        second_started.set()
        return second_action()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_action)
        assert entered.wait(5)
        second_future = executor.submit(second)
        try:
            assert second_started.wait(5)
            time.sleep(0.03)
            assert not second_future.done()
        finally:
            release.set()
        results = [first_future.result(timeout=10), second_future.result(timeout=10)]
    write_result, switch_result = results if first == "write" else results[::-1]
    assert switch_result.status_code == 200, switch_result.text
    assert write_result.status_code == (200 if first == "write" else 409), write_result.text
    db_session.expire_all()
    question = db_session.query(Question).first()
    if method == "post" and first == "switch":
        assert question is None
    else:
        assert question.content == ("本次写入题文" if first == "write" else "原编辑题文")
        a = db_session.query(QuestionCurriculum).filter_by(question_id=question.id, version_code="A").one()
        n = db_session.query(QuestionCurriculum).filter_by(question_id=question.id, version_code="N").one()
        assert (a.compulsory, a.chapter, a.knowledge) == (book, chapter, section)
        assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == (n.compulsory, n.chapter, n.knowledge)


@pytest.mark.parametrize("pause_stage", ("inspect", "backup"))
def test_zip_import_preparation_can_overlap_switch_but_backup_and_commit_bind_one_version(client, db_session, metadata_scope, monkeypatch, tmp_path, pause_stage):
    main = metadata_scope
    entered, release, switch_started = threading.Event(), threading.Event(), threading.Event()
    captures = []
    monkeypatch.setattr(main, "SYSTEM_GENERATED_DIR", tmp_path / "runtime")
    monkeypatch.setattr(main, "DATA_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(main, "DATABASE_PATH", str(tmp_path / "isolated.db"))
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))
    prepared_package = SimpleNamespace(summary={"image_count": 0})
    @contextmanager
    def prepare(_archive, *, workspace_dir=None):
        if pause_stage == "inspect":
            entered.set()
            assert release.wait(5)
        yield prepared_package
    def backup(**kwargs):
        captures.append(("backup", main.get_active_version_code()))
        if pause_stage == "backup":
            entered.set()
            assert release.wait(5)
        return tmp_path / "isolated-backup.zip"
    def importer(db, _archive, *, uploads_dir, active_version, prepared):
        assert prepared is prepared_package
        captures.append(("import", active_version))
        assert active_version == main.get_active_version_code()
        question = Question(content="隔离导入题文", category_compulsory=active_version + "册", category_chapter="原章", category_knowledge="原节")
        question.curriculum_mappings.append(QuestionCurriculum(version_code=active_version, compulsory=active_version + "册", chapter="原章", knowledge="原节"))
        db.add(question)
        db.commit()
        return {"imported_count": 1, "image_count": 0}
    monkeypatch.setattr(main, "prepare_library_import", prepare)
    monkeypatch.setattr(main, "create_full_backup", backup)
    monkeypatch.setattr(main, "backup_asset_warnings", lambda _archive: [])
    monkeypatch.setattr(main, "import_library_zip", importer)
    headers = {"X-Local-Token": main.LOCAL_TOKEN}
    def transfer():
        return client.post("/api/library-transfer/import", files={"file": ("share.zip", b"isolated test package", "application/zip")}, headers=headers)
    def switch():
        switch_started.set()
        return client.post("/api/config/metadata", json=build_default_metadata("N"), headers=headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        import_future = executor.submit(transfer)
        assert entered.wait(5)
        switch_future = executor.submit(switch)
        try:
            assert switch_started.wait(5)
            if pause_stage == "inspect":
                assert switch_future.result(timeout=5).status_code == 200
            else:
                time.sleep(0.03)
                assert not switch_future.done()
        finally:
            release.set()
        import_result, switch_result = import_future.result(timeout=10), switch_future.result(timeout=10)
    assert import_result.status_code == switch_result.status_code == 200
    version = "N" if pause_stage == "inspect" else "A"
    assert captures == [("backup", version), ("import", version)]
    assert import_result.json()["active_version_code"] == version
    db_session.expire_all()
    question = db_session.query(Question).one()
    n = db_session.query(QuestionCurriculum).filter_by(question_id=question.id, version_code="N").one()
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == (n.compulsory, n.chapter, n.knowledge)


def test_metadata_switch_uses_bundled_source_ancestry_without_touching_point_evidence(client, db_session, metadata_scope, monkeypatch):
    import mathbank.knowledge_points as catalog
    main = metadata_scope
    target = build_default_metadata("B")
    book, chapter = "必修一", "第一章 集合与常用逻辑用语"
    parent, leaf = "1.1 集合", "1.1.1 集合及其表示方法"
    assert parent in target["curriculum"][book][chapter] and leaf in target["curriculum"][book][chapter]
    point = KnowledgePoint(code="local:ancestry-test", name="本题考点", original_name="原考点", source="local")
    point.aliases = []
    for section, level in ((parent, "parent"), (leaf, "leaf")):
        point.curriculum_paths.append(KnowledgePointCurriculum(version_code="B", compulsory=book, chapter=chapter,
                                                              knowledge=section, reference_section=section + "  ", level=level))
    question = Question(content="原题文", answer_markdown="原解析", category_compulsory="A原册", category_chapter="A原章", category_knowledge="A原节")
    question.curriculum_mappings.append(QuestionCurriculum(version_code="A", compulsory="A原册", chapter="A原章", knowledge="A原节"))
    db_session.add_all([point, question])
    db_session.flush()
    label = QuestionKnowledgePoint(question_id=question.id, point_code=point.code, role="main", status="confirmed", position=0,
                                   evidence='{"原依据":"逐字保留"}', reason='"原理由"')
    db_session.add(label)
    db_session.commit()
    before = (label.point_code, label.role, label.status, label.evidence, label.reason)
    calls = []
    def ancestry(version):
        calls.append(version)
        return {(book, chapter, leaf + "  "): [parent + "  "]}
    monkeypatch.setattr(catalog, "get_curriculum_ancestors", ancestry)
    response = client.post("/api/config/metadata", json=target, headers={"X-Local-Token": main.LOCAL_TOKEN})
    assert response.status_code == 200, response.text
    assert response.json()["classification_switch"]["section"] == 1
    assert calls == ["B"]
    db_session.refresh(question)
    assert (question.category_compulsory, question.category_chapter, question.category_knowledge) == (book, chapter, leaf)
    db_session.refresh(label)
    assert (label.point_code, label.role, label.status, label.evidence, label.reason) == before
    assert (question.content, question.answer_markdown) == ("原题文", "原解析")
    # Restoring existing mirrors needs no reinterpretation from the hierarchy.
    assert client.post("/api/config/metadata", json=build_default_metadata("A"), headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 200
    assert client.post("/api/config/metadata", json=target, headers={"X-Local-Token": main.LOCAL_TOKEN}).status_code == 200
    assert calls == ["B"]


def test_frontend_preset_names_identity_and_deferred_save_are_executable():
    root = Path(__file__).resolve().parents[1]
    source = (root / "static/js/api.js").read_text(encoding="utf-8")
    start = source.index("window.resetMetadataToDefault = function")
    end = source.index("// ----------------- App Version", start)
    script = "const assert=require('node:assert/strict');\n" + """
const window={},element={value:''},toasts=[],requests=[],confirms=[];
function confirm(text){confirms.push(text);return true;}
function showToast(...args){toasts.push(args);}
const document={getElementById(id){assert.equal(id,'settingsMetadataJson');return element;}};
async function fetch(url){requests.push(url);const code=url.split('/').pop();return {ok:true,json:async()=>({name:code==='N'?'北师大版':'湘教版',metadata:{curriculum_version:code,curriculum:{册:{章:[]}}}})};}
""" + source[start:end] + """
(async()=>{
  for(const [code,name] of [['N','北师大版'],['X','湘教版']]){
    window.resetMetadataToDefault(code);
    await new Promise(resolve=>setImmediate(resolve));
    assert.ok(confirms.at(-1).includes(name));
    assert.equal(JSON.parse(element.value).curriculum_version,code);
    assert.equal(requests.at(-1),'/api/config/curriculum-presets/'+code);
  }
  assert.equal(requests.length,2);
  window.resetMetadataToDefault('unknown');
  assert.equal(requests.length,2);
  assert.equal(toasts.at(-1)[0],'不支持的教材大纲版本');
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    html = (root / "static/index.html").read_text(encoding="utf-8")
    for code in VERSIONS:
        assert f"resetMetadataToDefault('{code}')" in html


def test_frontend_refreshes_committed_curriculum_even_if_api_settings_save_fails():
    source = (Path(__file__).resolve().parents[1] / "static/js/api.js").read_text(encoding="utf-8")
    start = source.index("function saveSettings(e)")
    end = source.index("// Load Categories from Database", start)
    script = "const assert=require('node:assert/strict');\n" + """
const activeSettingsTab='metadata',events=[],toasts=[];
const document={getElementById(id){return {value:id==='settingsMetadataJson'
  ? JSON.stringify({curriculum_version:'N',question_types:[],difficulties:[],curriculum:{}})
  : 'deepseek'};}};
class FormData{append(){}}
function showToast(...args){toasts.push(args);}
function closeSettingsModal(){throw new Error('settings should stay open on API failure');}
function fetchConfigStatus(){}
async function loadCategories(options){assert.equal(options.reloadCurrentQuestion,true);events.push('refresh');}
async function fetch(url,options){
  events.push(url);
  if(url==='/api/config/metadata') return {ok:true,json:async()=>({status:'success',classification_switch:{created:4,restored_existing:2,chapter:1,unclassified:1}})};
  throw new Error('API setting write failed');
}
""" + source[start:end] + """
(async()=>{
  saveSettings({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(events,['/api/config/metadata','refresh','/api/settings/save']);
  assert.ok(toasts.at(-1)[0].includes('维度配置已保存；接口设置保存失败'));
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
