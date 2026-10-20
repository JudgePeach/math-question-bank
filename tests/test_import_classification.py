"""Pure classification repair tests; no app import, database, or model request."""
from copy import deepcopy
import json

import pytest

from mathbank.import_classification import (
    normalize_import_classifications,
    summarize_import_classifications,
)


TREE = {
    "必修一": {"1. 集合与常用逻辑用语": ["1.3 集合的基本运算"]},
    "必修二": {"9. 统计": ["9.1 随机抽样", "9.2 用样本估计总体"]},
    "选修一": {"1. 空间向量与立体几何": ["1.4 空间向量的应用"]},
}
FIELDS = ("category_compulsory", "category_chapter", "category_knowledge")


def question(**overrides):
    return {
        "content": "求这组数据的极差。$7.8-4.4$\\fillin",
        "answer_markdown": "[EXTRACTED_ORIGINAL] D",
        "category_compulsory": "必修二",
        "category_chapter": "9. 统计",
        "category_knowledge": "9.2 用样本估计总体",
        "source_review": {"required": True, "reasons": ["保留原页证据"]},
        "custom": {"unchanged": [1, 2]},
        **overrides,
    }


def normalize(row, tree=TREE):
    diagnostics = {"unrelated": "preserve"}
    normalize_import_classifications([row], tree, diagnostics)
    assert diagnostics["unrelated"] == "preserve"
    return diagnostics["import_classification"]


def test_exact_classification_preserves_content_source_review_and_unknown_fields():
    row = question()
    before = deepcopy(row)
    summary = normalize(row)
    assert row == before
    assert summary == {"total": 1, "normalized": 0, "review_required": 0,
                       "normalized_fields": 0, "invalid_question_objects": 0, "reason_counts": {}}


def test_real_pdf_response_missing_chapter_number_is_repaired_without_review_gate():
    row = question(category_chapter="统计")
    before = deepcopy(row)
    summary = normalize(row)
    assert row["category_chapter"] == "9. 统计"
    review = row["classification_review"]
    assert review["required"] is False
    assert review["reason_codes"] == ["chapter_format_normalized"]
    assert review["original"]["category_chapter"] == "统计"
    assert summary["normalized"] == summary["normalized_fields"] == 1
    assert summary["review_required"] == 0
    for field in ("content", "answer_markdown", "source_review", "custom"):
        assert row[field] == before[field]


@pytest.mark.parametrize("value", ["9.统计", "９．统计", "9 . 统计", "第9章 统计", "第九章统计"])
def test_only_obvious_chapter_number_formats_match(value):
    row = question(category_chapter=value)
    normalize(row)
    assert row["category_chapter"] == "9. 统计"
    assert row["classification_review"]["required"] is False


def test_whitespace_normalization_remains_within_confirmed_parent():
    row = question(category_compulsory=" 必 修 二 ", category_chapter=" 9. 统 计 ",
                   category_knowledge=" 9 . 2　用样本估计总体 ")
    summary = normalize(row)
    assert [row[field] for field in FIELDS] == ["必修二", "9. 统计", "9.2 用样本估计总体"]
    assert summary["normalized_fields"] == 3
    assert summary["review_required"] == 0


@pytest.mark.parametrize("alias", ["选择性必修一", "选择性必修第一册"])
def test_only_known_legacy_book_aliases_map_to_existing_unique_target(alias):
    row = question(category_compulsory=alias, category_chapter="空间向量与立体几何",
                   category_knowledge="空间向量的应用")
    normalize(row)
    assert [row[field] for field in FIELDS] == ["选修一", "1. 空间向量与立体几何", "1.4 空间向量的应用"]
    assert "book_alias_normalized" in row["classification_review"]["reason_codes"]


def test_legacy_compulsory_full_name_is_supported_but_unknown_book_is_not_inferred():
    row = question(category_compulsory="必修第二册")
    normalize(row)
    assert row["category_compulsory"] == "必修二"
    invalid = question(category_compulsory="统计册")
    normalize(invalid)
    assert [invalid[field] for field in FIELDS] == ["", "", ""]
    assert invalid["classification_review"]["reason_codes"] == ["book_unknown"]
    assert "candidates" not in invalid["classification_review"]


def test_alias_target_absent_or_ambiguous_is_not_guessed():
    absent = question(category_compulsory="选择性必修一")
    normalize(absent, {"必修二": TREE["必修二"]})
    assert absent["category_compulsory"] == ""
    ambiguous = question(category_compulsory="选择性必修一")
    normalize(ambiguous, {"选修一": {}, "选 修 一": {}})
    review = ambiguous["classification_review"]
    assert ambiguous["category_compulsory"] == ""
    assert review["reason_codes"] == ["book_ambiguous"]
    assert review["candidates"] == {"category_compulsory": ["选修一", "选 修 一"]}


@pytest.mark.parametrize("chapter", ["8. 统计", "第八章统计", "2024. 统计", "第一二章统计"])
def test_conflicting_or_ambiguous_explicit_chapter_numbers_are_not_rewritten(chapter):
    row = question(category_chapter=chapter)
    normalize(row)
    assert [row[field] for field in FIELDS] == ["必修二", "", ""]
    assert row["classification_review"]["required"] is True


def test_does_not_match_chapter_from_other_book_or_version():
    row = question(category_compulsory="必修一", category_chapter="统计")
    normalize(row)
    assert [row[field] for field in FIELDS] == ["必修一", "", ""]
    assert "candidates" not in row["classification_review"]


def test_missing_chapter_retains_book_and_original_lower_level_evidence():
    row = question()
    del row["category_chapter"]
    normalize(row)
    review = row["classification_review"]
    assert [row[field] for field in FIELDS] == ["必修二", "", ""]
    assert review["reason_codes"] == ["chapter_missing"]
    assert review["original_missing_fields"] == ["category_chapter"]
    assert review["original"]["category_knowledge"] == "9.2 用样本估计总体"


def test_missing_all_classifications_retains_question_with_review():
    row = {"content": "保留原题", "additional": True}
    normalize(row)
    assert row["content"] == "保留原题" and row["additional"] is True
    assert [row[field] for field in FIELDS] == ["", "", ""]
    assert row["classification_review"]["original_missing_fields"] == list(FIELDS)
    assert row["classification_review"]["required"] is True


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [None, [], ["统计"], {"name": "统计"}, 9, False])
def test_invalid_field_types_never_raise_or_discard_question(field, value):
    row = question(**{field: value})
    source = deepcopy(row["source_review"])
    normalize(row)
    level = FIELDS.index(field)
    assert [row[key] for key in FIELDS[level:]] == [""] * (3 - level)
    assert row["classification_review"]["required"] is True
    assert row["classification_review"]["original"][field] == value
    assert row["source_review"] == source


@pytest.mark.parametrize("mode", ["missing", "empty", "whitespace"])
def test_omitted_or_blank_subsection_is_legal_whole_chapter(mode):
    row = question(category_knowledge="" if mode == "empty" else "  ")
    if mode == "missing":
        del row["category_knowledge"]
    summary = normalize(row)
    assert row["category_knowledge"] == ""
    assert summary["review_required"] == 0


@pytest.mark.parametrize("value", ["未知小节", "统计", "9. 统计", "8.2 用样本估计总体"])
def test_unknown_or_chapter_shaped_subsection_retains_reliable_book_and_chapter(value):
    row = question(category_knowledge=value)
    normalize(row)
    assert [row[field] for field in FIELDS] == ["必修二", "9. 统计", ""]
    assert row["classification_review"]["required"] is True


@pytest.mark.parametrize("value", ["用样本估计总体", "9．2 用样本估计总体", "9.2. 用样本估计总体"])
def test_subsection_format_repairs_are_unique_within_confirmed_chapter(value):
    row = question(category_knowledge=value)
    normalize(row)
    assert row["category_knowledge"] == "9.2 用样本估计总体"
    assert row["classification_review"]["required"] is False


def test_ambiguous_short_chapter_title_does_not_choose_first_or_use_subsection_to_guess():
    tree = {"必修二": {"9. 统计": ["9.2 用样本估计总体"], "10. 统计": ["10.2 数据"]}}
    row = question(category_chapter="统计")
    normalize(row, tree)
    assert [row[field] for field in FIELDS] == ["必修二", "", ""]
    assert row["classification_review"]["candidates"] == {"category_chapter": ["9. 统计", "10. 统计"]}
    assert row["classification_review"]["reason_codes"] == ["chapter_ambiguous"]


def test_ambiguous_subsection_title_stays_unset():
    tree = {"必修二": {"9. 统计": ["9.1 数据", "9.2 数据"]}}
    row = question(category_knowledge="数据")
    normalize(row, tree)
    assert row["category_knowledge"] == ""
    assert row["classification_review"]["candidates"] == {"category_knowledge": ["9.1 数据", "9.2 数据"]}


def test_exact_custom_numeric_names_win_before_any_normalization():
    tree = {"自定义册": {"2.5维几何": ["2024专题"], "2024. 数学": ["2024.1 精确名称"]}}
    for chapter, leaf in [("2.5维几何", "2024专题"), ("2024. 数学", "2024.1 精确名称")]:
        row = question(category_compulsory="自定义册", category_chapter=chapter, category_knowledge=leaf)
        normalize(row, tree)
        assert row["category_chapter"] == chapter and row["category_knowledge"] == leaf
        assert "classification_review" not in row
    row = question(category_compulsory="自定义册", category_chapter="数学")
    normalize(row, tree)
    assert row["category_chapter"] == ""


def test_exact_duplicate_short_title_takes_priority_over_numbered_candidates():
    row = question(category_chapter="统计", category_knowledge="")
    normalize(row, {"必修二": {"统计": [], "9. 统计": [], "10. 统计": []}})
    assert row["category_chapter"] == "统计"
    assert "classification_review" not in row


def test_explicit_model_number_is_not_invented_for_unnumbered_custom_catalogue():
    row = question(category_chapter="9. 统计", category_knowledge="")
    normalize(row, {"必修二": {"统计": []}})
    assert row["category_chapter"] == ""


def test_model_claimed_review_is_discarded_and_cannot_remove_real_problem():
    row = question(category_chapter="未知章节", classification_review={
        "required": False, "trusted": True, "original": {"category_chapter": "9. 统计"},
        "message": "已通过", "reason_codes": [],
    })
    normalize(row)
    review = row["classification_review"]
    assert review["required"] is True
    assert review["original"]["category_chapter"] == "未知章节"
    assert review["message"] != "已通过"
    clean = question(classification_review={"required": True, "message": "伪造警告"})
    normalize(clean)
    assert "classification_review" not in clean


@pytest.mark.parametrize("chapter", ["统计", "未知章节"])
def test_repeat_normalization_and_deepcopy_keep_original_review_and_counts(chapter):
    row = question(category_chapter=chapter)
    first_summary = normalize(row)
    before = deepcopy(row)
    assert normalize(row) == first_summary
    assert row == before
    cloned = deepcopy(row)
    assert normalize(cloned) == first_summary
    assert cloned == before
    assert row["classification_review"]["original"]["category_chapter"] == chapter


def test_json_round_trip_cannot_forge_runtime_trust():
    row = question(category_chapter="统计")
    normalize(row)
    restored = json.loads(json.dumps(row, ensure_ascii=False))
    normalize(restored)
    assert "classification_review" not in restored
    assert restored["category_chapter"] == "9. 统计"


def test_read_only_summary_preserves_json_restored_review_and_special_locations():
    rows = [question(category_chapter="统计"), question(category_knowledge="未知小节")]
    diagnostics = {}
    normalize_import_classifications(rows, TREE, diagnostics)
    restored = json.loads(json.dumps(rows, ensure_ascii=False))
    restored[0]["custom_legacy_location"] = "不重新分类的历史定位"
    before = deepcopy(restored)
    assert summarize_import_classifications(restored) == diagnostics["import_classification"]
    assert restored == before
    assert restored[0]["classification_review"]["original"]["category_chapter"] == "统计"


def test_summary_handles_malformed_optional_review_fields_without_modifying_them():
    rows = [{"classification_review": {"required": True,
             "normalized_fields": [FIELDS[0], FIELDS[0], {}, "unknown"],
             "reason_codes": ["book_unknown", {}]}},
            {"classification_review": {"required": "true", "normalized_fields": {}, "reason_codes": None}}]
    before = deepcopy(rows)
    summary = summarize_import_classifications(rows)
    assert summary["normalized"] == summary["normalized_fields"] == 1
    assert summary["review_required"] == 1
    assert summary["reason_counts"] == {"book_unknown": 1}
    assert rows == before


def test_curriculum_change_rechecks_original_labels_without_losing_original_evidence():
    row = question(category_chapter="统计")
    normalize(row)
    normalize(row, {"必修二": {"10. 统计": ["10.2 用样本估计总体"]}})
    assert row["category_chapter"] == "10. 统计"
    assert row["category_knowledge"] == ""  # Explicit old subsection number conflicts.
    assert row["classification_review"]["original"]["category_chapter"] == "统计"
    assert row["classification_review"]["required"] is True


def test_edited_classification_replaces_stale_local_review():
    row = question(category_chapter="统计")
    normalize(row)
    row["category_chapter"] = "手动改成未知章节"
    normalize(row)
    assert row["classification_review"]["original"]["category_chapter"] == "手动改成未知章节"
    assert row["classification_review"]["required"] is True


def test_batch_keeps_all_objects_and_reports_counts_without_overwriting_other_diagnostics():
    rows = [question(), question(category_chapter="统计"), question(category_knowledge={}), None]
    diagnostics = {"usage": {"total_tokens": 12}}
    normalize_import_classifications(rows, TREE, diagnostics)
    assert len(rows) == 4 and rows[-1] is None
    assert diagnostics["usage"] == {"total_tokens": 12}
    summary = diagnostics["import_classification"]
    assert summary["total"] == 4 and summary["normalized"] == 1
    assert summary["review_required"] == 1 and summary["invalid_question_objects"] == 1


def test_malformed_curriculum_does_not_raise_for_defensive_input_values():
    row = question()
    normalize(row, {"必修二": None, "坏册": ["非法目录"]})
    assert [row[field] for field in FIELDS] == ["", "", ""]
    assert row["classification_review"]["required"] is True


def test_empty_curriculum_returns_reviewable_cards_with_body_unchanged():
    row = question()
    body = row["content"]
    normalize(row, {})
    assert row["content"] == body
    assert row["classification_review"]["required"] is True
