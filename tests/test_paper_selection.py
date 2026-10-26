"""Read-only paper planning against isolated databases and synthetic sources."""

from collections import Counter

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mathbank.database import Base, KnowledgePoint, Question, QuestionKnowledgePoint
from mathbank.paper_selection import (
    MAX_CANDIDATES, build_reference_context, collect_candidates, list_reference_exams,
    parse_gaokao_source, parse_selection_request, select_local_candidates,
)


@pytest.fixture
def paper_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("prompt,count", [("抽取19道高考题", 19), ("十九题", 19),
    ("抽取二十二道题", 22), ("五道函数单选题", 5), ("抽取30题", 30)])
def test_counts_preserve_both_digits(prompt, count):
    assert parse_selection_request({"prompt": prompt})["limit"] == count


@pytest.mark.parametrize("payload", [{"limit": True}, {"limit": "5"}, {"limit": 0},
    {"limit": 31}, {"prompt": "抽取100题"}, {"exclude_ids": [True]}, {"exclude_ids": [-1]},
    {"type_counts": {"single_choice": True}}, {"type_counts": {"single_choice": 31}}])
def test_invalid_limits_and_identifiers_are_rejected(payload):
    with pytest.raises(ValueError):
        parse_selection_request(payload)


def test_mixed_types_and_generic_choices_do_not_become_single_choice():
    request = parse_selection_request({"prompt": "8道单选、3道多选、3道填空、5道解答"})
    assert request["type_counts"] == {"single_choice": 8, "multi_choice": 3, "fill_in_blank": 3, "detailed_answer": 5}
    assert request["limit"] == 19 and request["question_type"] == ""
    request = parse_selection_request({"prompt": "5道函数选择题"})
    assert request["question_type"] == "" and request["question_types"] == ["single_choice", "multi_choice"]
    assert parse_selection_request({"prompt": "5道单选题"})["question_type"] == "single_choice"


def test_revision_is_not_repeated_question_intent_and_negation_is_not_positive_topic():
    assert parse_selection_request({"prompt": "高三复习，函数专题"})["review"] is False
    assert parse_selection_request({"prompt": "重做五道旧题"})["review"] is True
    assert parse_selection_request({"prompt": "函数专题，不要导数"})["topics"] == ["函数"]
    assert parse_selection_request({"prompt": "三角函数专项"})["topics"] == ["三角函数"]


@pytest.mark.parametrize("source,key,number", [
    ("2024年新课标一卷 第19题", "2024:national_1", 19),
    ("2026年全国二卷 第1题", "2026:national_2", 1),
    ("2026新高考一卷", "2026:national_1", None),
    ("2023·全国甲卷·第1题", None, None),
    ("2025·浙江卷·第22题", "2025:浙江", 22),
])
def test_source_recognition_is_compact_and_groups_numbered_variants(source, key, number):
    result = parse_gaokao_source(source)
    assert (result["key"] if result else None) == key
    if result:
        assert result["number"] == number


@pytest.mark.parametrize("source", ["2026年全国一卷模拟 第1题", "2025新高考一卷改编第2题",
    "2026讲义专题 高考链接 2025年全国一卷第1题", "2026年全国一卷预测第1题",
    "2026年全国一卷 第1题 (改编)", "2026年第1题", "2025北京期末卷第1题"])
def test_simulated_or_embedded_sources_are_never_reference_papers(source):
    assert parse_gaokao_source(source) is None


def _add_exam(db, year, volume="一", counts=(8, 3, 3, 5), prefix="全国"):
    number = 0
    for kind, count in zip(("single_choice", "multi_choice", "fill_in_blank", "detailed_answer"), counts):
        for _ in range(count):
            number += 1
            db.add(Question(content=f"{year} {volume} 第{number}题 函数$f(x)$", question_type=kind,
                source=f"{year}年{prefix}{volume}卷 第{number}题", difficulty="challenge"))
    db.commit()


def test_references_use_latest_collected_years_and_keep_series_separate(paper_db):
    for year in (2022, 2023, 2024, 2025):
        _add_exam(paper_db, year)
    _add_exam(paper_db, 2025, "二", counts=(8, 4, 4, 6))
    paper_db.add(Question(content="散题", source="2025新高考一卷", question_type="single_choice"))
    paper_db.commit()
    profiles = list_reference_exams(paper_db)
    assert {item["year"] for item in profiles} == {2023, 2024, 2025}
    first = profiles[0]
    assert first["key"] == "2025:national_1" and first["question_count"] == 19 and first["complete"]
    assert first["type_counts"] == {"single_choice": 8, "multi_choice": 3, "fill_in_blank": 3, "detailed_answer": 5}
    assert [slot["number"] for slot in first["slots"]] == list(range(1, 20))
    assert [slot["question_type"] for slot in first["slots"]] == (["single_choice"]*8 + ["multi_choice"]*3 + ["fill_in_blank"]*3 + ["detailed_answer"]*5)
    assert first["slots"][0]["difficulty_label"] == "challenge"
    before = paper_db.query(Question).count()
    context = build_reference_context(paper_db)
    assert {item["series"] for item in context["references"]} == {"national_1"}
    assert [item["year"] for item in context["references"]] == [2025, 2024, 2023]
    assert all(len(item["samples"]) <= 5 for item in context["references"])
    assert paper_db.query(Question).count() == before and not paper_db.new and not paper_db.dirty
    with pytest.raises(ValueError):
        build_reference_context(paper_db, "missing")
    assert build_reference_context(paper_db, "none")["enabled"] is False


def test_partial_papers_are_labeled_and_only_main_points_are_counted(paper_db):
    paper_db.add_all([KnowledgePoint(code="main", name="函数", original_name="函数", source="test"), KnowledgePoint(code="aux", name="数形结合", original_name="数形结合", source="test")])
    question = Question(content="函数题", source="2026新高考一卷", question_type="single_choice", difficulty="challenge")
    paper_db.add(question)
    paper_db.flush()
    paper_db.add_all([QuestionKnowledgePoint(question_id=question.id, point_code="main", role="main"),
                      QuestionKnowledgePoint(question_id=question.id, point_code="aux", role="auxiliary")])
    paper_db.commit()
    profile = list_reference_exams(paper_db)[0]
    assert not profile["complete"] and profile["difficulty_labels"] == {"challenge": 1}
    assert profile["main_knowledge_counts"] == [{"code": "main", "name": "函数", "count": 1}]
    assert profile["slots"] == [{"number": None, "question_type": "single_choice",
        "difficulty_label": "challenge", "main_knowledge_points": [{"code": "main", "name": "函数"}]}]
    assert build_reference_context(paper_db)["warnings"]


def test_empty_reference_database_is_explicit(paper_db):
    assert list_reference_exams(paper_db) == []
    assert build_reference_context(paper_db)["warnings"]


def test_candidates_retrieve_topic_before_newest_bound_and_preserve_filters(paper_db):
    # The requested topic is deliberately older than 35 unrelated questions.
    relevant = Question(content="函数专题", source="旧", question_type="single_choice", category_knowledge="函数")
    paper_db.add(relevant)
    paper_db.flush()
    rid = relevant.id
    for number in range(150):
        paper_db.add(Question(content=f"无关题{number}", question_type="single_choice"))
    paper_db.add(Question(content="函数不在本次题型范围", question_type="detailed_answer"))
    paper_db.commit()
    query = paper_db.query(Question).filter(Question.question_type == "single_choice")
    candidates = collect_candidates(query, topics=["函数"])
    assert rid in {question.id for question in candidates}
    assert len(candidates) <= MAX_CANDIDATES
    assert all(question.question_type == "single_choice" for question in candidates)
    candidates = collect_candidates(query, excluded_ids=[rid], topics=["函数"])
    assert rid not in {question.id for question in candidates}


def test_candidate_hydration_is_stratified_and_bounded(paper_db):
    for kind in ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer"):
        for number in range(80):
            paper_db.add(Question(content=f"{kind}-{number}", question_type=kind))
    paper_db.commit()
    candidates = collect_candidates(paper_db.query(Question))
    assert len(candidates) == MAX_CANDIDATES
    assert set(Counter(question.question_type for question in candidates).values()) == {30}


def test_local_selection_deduplicates_and_honors_quotas(paper_db):
    for content, kind in [("函数题", "single_choice"), ("函数题", "single_choice"),
        ("函数填空", "fill_in_blank"), ("另一道函数", "single_choice"), ("函数解答", "detailed_answer")]:
        paper_db.add(Question(content=content, question_type=kind))
    paper_db.commit()
    candidates = collect_candidates(paper_db.query(Question))
    chosen = select_local_candidates(candidates, 4, topics=["函数"],
        type_counts={"single_choice": 2, "fill_in_blank": 2})
    assert len(chosen) == 3
    assert Counter(question.question_type for question in chosen) == {"single_choice": 2, "fill_in_blank": 1}
    assert len({question.content for question in chosen}) == 3


def test_reference_question_number_is_not_a_requested_total():
    assert parse_selection_request({"prompt": "参考第19题的风格", "limit": 5})["limit"] == 5


def test_different_paper_formats_do_not_share_a_reference_context(paper_db):
    _add_exam(paper_db, 2026)
    _add_exam(paper_db, 2025)
    _add_exam(paper_db, 2024, counts=(8, 4, 4, 6))
    context = build_reference_context(paper_db)
    assert [item["year"] for item in context["references"]] == [2026, 2025]


def test_missing_topic_never_fills_from_unrelated_questions(paper_db):
    paper_db.add(Question(content="集合题", question_type="single_choice"))
    paper_db.commit()
    assert collect_candidates(paper_db.query(Question), topics=["函数"]) == []


def test_separate_legacy_diagrams_distinguish_identically_worded_questions(paper_db):
    first = Question(content="如图求面积", question_type="single_choice")
    second = Question(content="如图求面积", question_type="single_choice")
    first.image_paths = ["/static/uploads/first.png"]
    second.image_paths = ["/static/uploads/second.png"]
    paper_db.add_all([first, second])
    paper_db.commit()
    candidates = collect_candidates(paper_db.query(Question))
    assert len(select_local_candidates(candidates, 2)) == 2


def test_reference_slots_bound_each_main_knowledge_list(paper_db):
    question = Question(content="综合题", source="2026年全国一卷 第19题", question_type="detailed_answer")
    paper_db.add(question)
    paper_db.flush()
    for index in range(15):
        code = f"point{index}"
        paper_db.add(KnowledgePoint(code=code, name=code, original_name=code, source="test"))
        paper_db.flush()
        paper_db.add(QuestionKnowledgePoint(question_id=question.id, point_code=code, role="main", position=index))
    paper_db.commit()
    slots = build_reference_context(paper_db)["references"][0]["slots"]
    assert slots[0]["number"] == 19 and len(slots[0]["main_knowledge_points"]) == 12


@pytest.mark.parametrize("prompt,positive,excluded", [
    ("选5道单选题，不要解答题", "single_choice", ["detailed_answer"]),
    ("选5道题，不选做过的，不要解答题", "", ["detailed_answer"]),
    ("选5道题，排除填空题", "", ["fill_in_blank"]),
    ("选5道选择题，不含多选题", "single_choice", ["multi_choice"]),
    ("选5道题，不考单选和多选", "", ["single_choice", "multi_choice"]),
    ("选5道题，不要选择题", "", ["single_choice", "multi_choice"]),
    ("选5道单项选择题", "single_choice", []),
    ("不选题型为解答题", "detailed_answer", []),
])
def test_explicit_negative_types_are_not_positive_type_requests(prompt, positive, excluded):
    request = parse_selection_request({"prompt": prompt})
    assert request["question_type"] == positive
    assert request["excluded_question_types"] == excluded


@pytest.mark.parametrize("prompt", ["不要旧题", "不选做过的题", "排除已用过的", "不含考过的题", "不考旧题", "不要再重做旧题", "不要已经做过的题"])
def test_explicit_negative_review_never_prioritizes_used_questions(prompt):
    assert parse_selection_request({"prompt": prompt})["review"] is False


def test_independent_function_and_trigonometry_requests_both_survive():
    assert parse_selection_request({"prompt": "函数与三角函数专项"})["topics"] == ["函数", "三角函数"]
    assert parse_selection_request({"prompt": "函数专题，不要三角函数"})["topics"] == ["函数"]
    assert parse_selection_request({"prompt": "不要函数和导数"})["topics"] == []
    assert parse_selection_request({"prompt": "不要三角函数"})["topics"] == []


def test_negative_per_type_count_does_not_override_positive_total():
    request = parse_selection_request({"prompt": "选5道单选题，不要3道多选题"})
    assert request["limit"] == 5 and not request["type_counts"]


def test_weighted_candidate_budgets_cover_large_custom_type_quota(paper_db):
    counts = {"major": 19, **{f"custom_{index}": 1 for index in range(11)}}
    for kind in counts:
        for number in range(85):
            paper_db.add(Question(content=f"{kind}-{number}", question_type=kind))
    paper_db.commit()
    candidates = collect_candidates(paper_db.query(Question), type_counts=counts)
    actual = Counter(question.question_type for question in candidates)
    assert len(candidates) <= MAX_CANDIDATES
    assert all(actual[kind] >= required for kind, required in counts.items())
    chosen = select_local_candidates(candidates, 30, type_counts=counts)
    assert Counter(question.question_type for question in chosen) == counts
