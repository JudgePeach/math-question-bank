"""Real browser regressions for compatible, versioned curriculum refinements.

The imported fixture starts a source-only disposable app: no real database,
uploads, token, or user settings. Run with MATHBANK_TEST_BROWSER=1 and
--noconftest. Evidence goes to MATHBANK_QA_ARTIFACTS or ../artifacts/qa.
"""

import json
import os
from pathlib import Path

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


GEOMETRY = {
    "compulsory": "必修二", "chapter": "8. 立体几何初步",
    "knowledge": "8.1 基本立体图形",
}
PROBABILITY = {
    "compulsory": "选修三", "chapter": "7. 随机变量及其分布",
    "knowledge": "7.1 条件概率与全概率公式",
}
OLD_KNOWLEDGE = "8. 立体几何初步"
OLD_TAGS = "旧标签,高掌"
EVIDENCE = Path(os.environ.get(
    "MATHBANK_QA_ARTIFACTS", str(Path(__file__).resolve().parents[2] / "artifacts" / "qa")
))


def capture(browser, name, state=None):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(EVIDENCE / (name + ".png")))
    if state is not None:
        (EVIDENCE / (name + ".json")).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def reset_bank(browser, width=1600):
    browser.command("reload")
    browser.command("set", "viewport", str(width), "1100")
    browser.command("wait", "--fn", "window.categoryTree && Object.keys(categoryTree).length > 0 && typeof loadQuestions === 'function' && window.activeCurriculumVersion === 'A'")
    browser.evaluate("selectWorkspace('bank', '题库管理'); true")
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy')")


def seed(browser, marker, paths=None, legacy_knowledge=OLD_KNOWLEDGE):
    payload = {
        "content": marker + "：计算 $\\frac{1}{2}+\\frac{1}{3}$。",
        "question_type": "detailed_answer", "difficulty": "normal",
        "category_compulsory": GEOMETRY["compulsory"],
        "category_chapter": GEOMETRY["chapter"],
        "category_knowledge": legacy_knowledge, "tags": OLD_TAGS,
        "sub_knowledge": json.dumps(paths or [], ensure_ascii=False),
    }
    return browser.evaluate("""(async () => {
        const form = new FormData();
        Object.entries(%s).forEach(([key,value]) => form.set(key,value));
        const response = await fetch('/api/questions', {method:'POST',body:form});
        const result = await response.json();
        if (!response.ok || !result.question) throw Error(JSON.stringify(result));
        await loadQuestions();
        return result.question;
    })()""" % json.dumps(payload, ensure_ascii=False))


def question(browser, qid):
    return browser.evaluate(f"fetch('/api/questions/{qid}').then(r=>r.json())")


def checkbox(browser, host, path, checked=True):
    summary_id = browser.evaluate("""(() => {
        const picker=document.querySelector(%s)?.closest('details');
        return picker && !picker.open ? picker.querySelector('summary').id : null;
    })()""" % json.dumps(host))
    if summary_id:
        browser.command("click", "#" + summary_id)
    value = browser.evaluate("""(() => {
        const wanted = %s;
        const input = [...document.querySelectorAll(%s)].find(el => {
            const path=JSON.parse(el.value);
            return ['compulsory','chapter','knowledge'].every(key => path[key] === wanted[key]);
        });
        if (!input) throw Error('missing curriculum path '+JSON.stringify(wanted));
        return input.value;
    })()""" % (json.dumps(path, ensure_ascii=False), json.dumps(host + ' input[type="checkbox"]')))
    selector = host + " input[value=" + json.dumps(value, ensure_ascii=False) + "]"
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("check" if checked else "uncheck", selector)


def open_editor(browser, qid):
    if browser.evaluate("innerWidth <= 960 && document.getElementById('bankWorkspaceSection').classList.contains('bank-detail-open')"):
        browser.command("click", ".bank-detail-back")
    browser.command("wait", f'#questionsList [data-id="{qid}"]')
    browser.command("click", f'#questionsList [data-id="{qid}"]')
    browser.command("wait", "--fn", f"EditorState.questionId === {qid} && !questionDetailLoading && !isEditorModified()")
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="classification"]')


def wait_bank_ids(browser, ids):
    ids = sorted(str(qid) for qid in ids)
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy') && "
                    "JSON.stringify([...document.querySelectorAll('#questionsList .question-card')].map(el=>el.dataset.id).sort()) === "
                    + json.dumps(json.dumps(ids, separators=(",", ":"))))


def get_selected(browser, host):
    return browser.evaluate("[...document.querySelectorAll(%s)].map(el=>JSON.parse(el.value))"
                            % json.dumps(host + ' input[type="checkbox"]:checked'))


def save_editor(browser):
    browser.command("click", "#saveQuestionBtn")
    review_open = "!!document.getElementById('parsedDuplicateReviewModal') && !document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')"
    browser.command("wait", "--fn", "!isQuestionSaveInFlight() || (" + review_open + ")")
    # Fixtures intentionally share mathematical structure. Respect the normal
    # duplicate review rather than allowing it to stall a metadata-only save.
    if browser.evaluate(review_open):
        browser.command("click", "#duplicateReviewIndependentBtn")
    browser.command("wait", "--fn", "!isQuestionSaveInFlight() && !isEditorModified()")


def switch_metadata(browser, metadata):
    browser.evaluate("""(async () => {
        const response=await fetch('/api/config/metadata',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(%s)});
        if(!response.ok)throw Error(await response.text());
        await loadCategories({reloadCurrentQuestion:true}); return true;
    })()""" % json.dumps(metadata, ensure_ascii=False))


def open_bank_filters(browser):
    if browser.evaluate("document.querySelector('.bank-filter-toggle').getAttribute('aria-expanded') !== 'true'"):
        browser.command("click", ".bank-filter-toggle")


def open_picker(browser, summary_id):
    if browser.evaluate("!document.getElementById(%s).closest('details').open" % json.dumps(summary_id)):
        browser.command("click", "#" + summary_id)


def assert_picker_layout(browser, host):
    layout = browser.evaluate("""(() => {
        const label=document.querySelector(%s+' label');
        const input=label.querySelector('input');
        return {direction:getComputedStyle(label).flexDirection,
            labelHeight:label.getBoundingClientRect().height,
            input:input.getBoundingClientRect().toJSON(), viewport:innerWidth};
    })()""" % json.dumps(host))
    assert layout["direction"] == "row", layout
    assert layout["labelHeight"] >= 44, layout
    assert 12 <= layout["input"]["width"] <= 20, layout
    assert 12 <= layout["input"]["height"] <= 20, layout
    return layout


@pytest.mark.parametrize("width", [1600, 375])
def test_real_single_knowledge_edit_filter_and_cancel(browser, width):
    reset_bank(browser, width)
    stored = seed(browser, "单值小节回归" + str(width), paths=[GEOMETRY, PROBABILITY])
    qid = stored["id"]
    open_editor(browser, qid)
    assert browser.evaluate("document.getElementById('editKnowledge').value") == OLD_KNOWLEDGE
    assert browser.evaluate("!document.getElementById('editSubKnowledgePicker') && !document.getElementById('filterSubKnowledgePicker')")
    assert browser.evaluate("document.getElementById('editTags').value") == OLD_TAGS

    browser.evaluate("""(() => {
      const select=document.getElementById('editKnowledge');
      select.value=%s;select.dispatchEvent(new Event('change',{bubbles:true}));return true;
    })()""" % json.dumps(GEOMETRY["knowledge"], ensure_ascii=False))
    assert browser.evaluate("isEditorModified()")
    save_editor(browser)
    persisted = question(browser, qid)
    assert persisted["category_knowledge"] == GEOMETRY["knowledge"]
    assert persisted["tags"] == OLD_TAGS
    assert persisted["sub_knowledge"] == [GEOMETRY, PROBABILITY]
    browser.command("click", "#editorSection .question-editor-close")
    browser.command("wait", "--fn", "document.getElementById('editorSection').classList.contains('hidden')")
    open_editor(browser, qid)
    assert browser.evaluate("document.getElementById('editKnowledge').value") == GEOMETRY["knowledge"]

    browser.evaluate("""(() => {
      const select=document.getElementById('editKnowledge');
      const next=[...select.options].find(option=>option.value && option.value !== %s);
      if(!next) throw Error('expected a second subsection option');
      select.value=next.value;select.dispatchEvent(new Event('change',{bubbles:true}));return next.value;
    })()""" % json.dumps(GEOMETRY["knowledge"], ensure_ascii=False))
    assert browser.evaluate("isEditorModified()")
    browser.command("click", "#editorSection .question-editor-close")
    browser.command("wait", "#unsavedChangesModalTitle")
    browser.command("click", "#cancelBtn")
    assert browser.evaluate("isEditorModified() && !document.getElementById('editorSection').classList.contains('hidden')")
    assert question(browser, qid)["category_knowledge"] == GEOMETRY["knowledge"]

    open_bank_filters(browser)
    browser.evaluate("""(() => {
      const select=document.getElementById('filterKnowledge');
      select.value=%s;select.dispatchEvent(new Event('change',{bubbles:true}));return true;
    })()""" % json.dumps(GEOMETRY["knowledge"], ensure_ascii=False))
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy')")
    assert browser.evaluate("document.querySelectorAll('#questionsList [data-id]').length >= 1")
    assert question(browser, qid)["sub_knowledge"] == [GEOMETRY, PROBABILITY]
