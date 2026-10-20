"""Stable receipt identities across partial/full results; isolated stores only."""
from copy import deepcopy
import threading
import uuid

import pytest

from mathbank.document_project_service import DocumentProjectService, project_question_ids
from mathbank.document_projects import DocumentProjectError, DocumentProjectStore


PROJECT = "306e1124-1fec-4313-86a3-051fce4d67a7"


def question(number=None, content=None, **extra):
    row = {"content": content or f"原第{number}题", "answer_markdown": "原答案", "image_paths": []}
    if number is not None:
        row["source_number"] = number
    return {**row, **extra}


def old_id(index):
    return "card-" + uuid.uuid5(uuid.UUID(PROJECT), f"question:{index}").hex


def prior_partial():
    return [question(number, project_card_id=old_id(index)) for index, number in enumerate((17, 18, 19))]


class FakeTasks:
    def __init__(self):
        self._lock = threading.RLock()
        self._records = {}

    def snapshot(self, task_id):
        return None


@pytest.fixture
def service(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project_id = store.create(b"%PDF-local-only", "本地身份测试.pdf", {}, {})["project_id"]
    return DocumentProjectService(store, FakeTasks()), project_id


def test_existing_receipt_id_is_preserved_before_any_new_number_assignment():
    source = [question(18, project_card_id=old_id(1), import_project_id=PROJECT, saved_id=123)]
    original = deepcopy(source)
    rows = project_question_ids(PROJECT, source)
    assert rows[0]["project_card_id"] == old_id(1)
    assert rows[0]["saved_id"] == 123 and source == original


def test_full_result_maps_unique_numbers_to_historical_partial_position_ids():
    old = prior_partial()
    rows = project_question_ids(PROJECT, [question(n, f"新分析第{n}题") for n in range(1, 20)],
                                previous_questions=old)
    assert [row["source_number"] for row in rows] == list(range(1, 20))
    assert [row["project_card_id"] for row in rows[16:]] == [old_id(n) for n in range(3)]
    assert not {row["project_card_id"] for row in rows[:16]} & {old_id(n) for n in range(3)}


def test_unknown_partial_and_full_results_cannot_share_position_fallback():
    old = project_question_ids(PROJECT, [question(content="旧局部甲"), question(content="旧局部乙")])
    incoming = [question(content="新增第一题"), question(content="新增第二题"), question(content="其余完整题")]
    result = project_question_ids(PROJECT, incoming, previous_questions=old)
    assert not {row["project_card_id"] for row in old} & {row["project_card_id"] for row in result}
    assert result == project_question_ids(PROJECT, incoming, previous_questions=old)
    assert old_id(0) not in {row["project_card_id"] for row in result}


def test_unique_exact_baseline_content_can_move_without_changing_old_receipt():
    old = [question(content="唯一原文", project_card_id=old_id(0))]
    rows = project_question_ids(PROJECT, [question(content="新题"), question(17, "唯一原文")],
                                previous_questions=old)
    assert rows[1]["project_card_id"] == old_id(0)
    assert rows[0]["project_card_id"] != old_id(0)


def test_content_evidence_must_be_unique_and_cannot_contradict_known_source_number():
    old = [question(17, "相同题文", project_card_id=old_id(0))]
    duplicated = project_question_ids(PROJECT, [question(content="相同题文"), question(content="相同题文")],
                                      previous_questions=old)
    assert old_id(0) not in {row["project_card_id"] for row in duplicated}
    conflict = project_question_ids(PROJECT, [question(1, "相同题文")], previous_questions=old)
    assert conflict[0]["project_card_id"] != old_id(0)
    ambiguous = project_question_ids(PROJECT, [question(17, "新题文")],
        previous_questions=[*old, question(17, "另一原文", project_card_id=old_id(1))])
    assert ambiguous[0]["project_card_id"] not in {old_id(0), old_id(1)}


def test_repeated_baseline_versions_with_same_id_are_one_mapping_not_ambiguity():
    old = prior_partial()
    rows = project_question_ids(PROJECT, [question(18, "新题文")], previous_questions=[*old, *old])
    assert rows[0]["project_card_id"] == old_id(1)


def test_boolean_source_number_does_not_hide_reliable_integer_one():
    rows = project_question_ids(PROJECT, [question(True), question(1)])
    assert rows[1]["project_card_id"] == "card-" + uuid.uuid5(uuid.UUID(PROJECT), "number:1").hex
    assert rows[0]["project_card_id"] != rows[1]["project_card_id"]


def test_empty_card_is_not_matching_evidence_and_different_answers_change_result_scope():
    old = project_question_ids(PROJECT, [{"content": "", "answer_markdown": "旧答案"}])
    new = project_question_ids(PROJECT, [{"content": "", "answer_markdown": "新答案"}], previous_questions=old)
    assert old[0]["project_card_id"] != new[0]["project_card_id"]


@pytest.mark.parametrize("value", ["", "bad id", "<script>", "x" * 161, 123])
def test_invalid_existing_id_is_replaced_without_altering_question_source(value):
    row = question(17, project_card_id=value)
    result = project_question_ids(PROJECT, [row])[0]
    assert result["project_card_id"].startswith("card-") and result["content"] == row["content"]


def test_duplicate_existing_ids_fail_without_silently_renaming_a_formal_receipt():
    rows = [question(1, project_card_id="same-receipt"), question(2, project_card_id="same-receipt")]
    original = deepcopy(rows)
    with pytest.raises(DocumentProjectError, match="身份重复"):
        project_question_ids(PROJECT, rows)
    assert rows == original


def test_foreign_project_id_is_not_inherited_by_another_project():
    row = question(17, project_card_id=old_id(0), import_project_id=str(uuid.uuid4()))
    result = project_question_ids(PROJECT, [row])[0]
    assert result["project_card_id"] != old_id(0) and result["import_project_id"] == PROJECT


def test_detail_and_sync_map_machine_baseline_without_changing_teacher_draft(service):
    instance, project_id = service
    old = [question(n, project_card_id=f"legacy-position-{i}") for i, n in enumerate((17, 18, 19))]
    instance.store.save_baseline(project_id, {"questions": old, "baselineQuestions": old})
    teacher = deepcopy(old)
    teacher[1].update(content="第18题人工校对稿", saved_id=900, saved=True)
    instance.save_draft(project_id, {"questions": teacher, "baselineQuestions": old}, 0)
    saved = instance.store.get_draft(project_id)
    instance.sync("new-task", {"document_type": "pdf", "project_id": project_id, "status": "completed",
                              "data": [question(n, f"新第{n}题") for n in range(1, 20)]})
    result = instance.detail(project_id)
    assert len(result["latest_result"]) == 19
    assert result["latest_result"][17]["project_card_id"] == "legacy-position-1"
    assert result["draft"] == saved["payload"] and result["revision"] == saved["revision"]
    assert instance.store.load_task(project_id)["data"][17]["project_card_id"] == "legacy-position-1"
    assert instance.store.get_baseline(project_id)["questions"] == old


def test_save_keeps_new_and_existing_card_identities_when_only_the_text_changes(service):
    instance, project_id = service
    first = instance.save_draft(project_id, {"questions": [question(content="教师初稿")]}, 0)
    cards = first["draft"]["questions"]
    cards[0]["content"] = "教师第二稿"
    second = instance.save_draft(project_id, {"questions": cards}, 1)
    assert first["draft"]["questions"][0]["project_card_id"] == second["draft"]["questions"][0]["project_card_id"]


def test_fork_keeps_unknown_edited_card_baseline_and_candidate_correspondence(service):
    instance, project_id = service
    raw = question(content="首次原文", project_card_id="old-receipt", import_project_id=project_id)
    edited = {**raw, "content": "人工改稿", "saved_id": 123, "saved": True,
              "project_incoming_candidate": deepcopy(raw)}
    instance.store.save_baseline(project_id, {"questions": [raw]})
    instance.store.sync_task(project_id, {"status": "completed", "data": [raw]})
    instance.save_draft(project_id, {"questions": [edited], "baselineQuestions": [raw]}, 0)
    copied = instance.fork(project_id)
    card, baseline = copied["draft"]["questions"][0], copied["draft"]["baselineQuestions"][0]
    assert card["project_card_id"] == baseline["project_card_id"] != "old-receipt"
    assert card["project_incoming_candidate"]["project_card_id"] == card["project_card_id"]
    assert copied["latest_result"][0]["project_card_id"] == card["project_card_id"]
    assert "saved_id" not in card and "saved" not in card
    assert instance.store.get_draft(project_id)["payload"]["questions"][0]["saved_id"] == 123


def test_mapping_historical_id_reuses_existing_database_receipt(environment):
    # Reuse the file-SQLite fixture, never the running application/database.
    from mathbank.document_project_import import prepare_project_import
    from test_document_project_import import _import, _counts
    _, _, factory, store, project_id, card = environment
    question_id = _import(factory, store, project_id, card)
    baseline = {**card, "source_number": 18}
    store.save_baseline(project_id, {"questions": [baseline]})
    instance = DocumentProjectService(store, FakeTasks())
    incoming = {key: value for key, value in card.items() if key != "project_card_id"}
    result = instance.question_ids(project_id, [question(1), {**incoming, "source_number": 18}])
    with factory() as db:
        _, existing = prepare_project_import(db, store, project_id, result[1]["project_card_id"], 1, result[1])
    assert existing == question_id and _counts(factory) == (1, 1)


from test_document_project_import import environment  # noqa: E402,F401
