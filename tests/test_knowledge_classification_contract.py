"""AI/import classifications accept only a real subsection of the selected chapter."""
import pytest

from main import validate_import_knowledge
from mathbank.prompts import build_classification_system_prompt

TREE = {"必修二": {"8. 立体几何初步": ["8.1 基本立体图形", "8.2 立体图形的直观图"]}}


def test_import_knowledge_accepts_real_leaf_or_explicit_whole_chapter():
    validate_import_knowledge([{"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步",
                               "category_knowledge": "8.1 基本立体图形"}], TREE)
    validate_import_knowledge([{"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步",
                               "category_knowledge": ""}], TREE)


def test_classification_uses_actual_book_then_chapter_catalog_order():
    prompt = build_classification_system_prompt({
        "必修一": {"9. 后一册章节": ["9.1 后一考点"]},
        "必修二": {"1. 前编号但后册章节": ["1.1 后一册考点"]},
    })
    assert "先比较教材册次在目录中的排列" in prompt
    assert "同一册内再比较章节在目录中的排列" in prompt
    assert "册次和章节先后只能按当前教材大纲中展示的JSON插入顺序比较" in prompt
    assert prompt.index("必修一") < prompt.index("必修二")
    assert "裸章节编号" in prompt


@pytest.mark.parametrize("row", [
    {"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": "8. 立体几何初步"},
    {"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": "1.1 Other-version leaf"},
    {"category_compulsory": "选择性必修一", "category_chapter": "空间向量", "category_knowledge": "8.1 基本立体图形"},
    {"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": None},
])
def test_import_knowledge_rejects_chapter_labels_wrong_version_and_malformed_values(row):
    with pytest.raises(ValueError):
        validate_import_knowledge([row], TREE)
