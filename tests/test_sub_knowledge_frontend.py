"""The historical relation data is retained but is no longer a user classification UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_single_knowledge_controls_are_the_only_visible_classification_surface():
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    editor = (ROOT / "static/js/editor.js").read_text(encoding="utf-8")
    importer = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
    paper = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
    assert 'id="editKnowledge"' in index and 'id="filterKnowledge"' in index
    for obsolete_id in ("editSubKnowledgePicker", "filterSubKnowledgePicker", "paperSubKnowledgePicker",
                        "editSubKnowledgeOptions", "filterSubKnowledgeOptions"):
        assert obsolete_id not in index and obsolete_id not in paper
    assert "function matchesQuestionCurriculumFilters(item, filters)" in editor
    matcher = editor[editor.index("function matchesQuestionCurriculumFilters"):editor.index("function renderSubKnowledgeBadges")]
    assert "item.category_knowledge" in matcher and "sub_knowledge" not in matcher
    assert "formData.append('sub_knowledge'" not in importer
    assert "temporaryClassifyData.knowledge || ''" in importer
    assert "整章定位（待细分）" in importer
