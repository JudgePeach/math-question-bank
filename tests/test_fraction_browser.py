"""Opt-in fraction formatting checks through the real editor and KaTeX.

The shared browser fixture runs a source-only temporary application with its
own SQLite database; these checks never open the user's running application.
"""

import json

from test_preview_browser import browser, pytestmark  # noqa: F401


CONTENT = (
    r"分式规范回归：主体 $\eu^x-\frac{x+1}{x-1}$；"
    r"指数 $x^{\dfrac{1}{2}}$；下标 $a_{\dfrac{m}{n}}$；"
    r"嵌套 $\frac{1+\dfrac{x}{y}}{\dfrac{a}{b}}$。"
)
NORMALIZED_CONTENT = (
    r"分式规范回归：主体 $\eu^x-\dfrac{x+1}{x-1}$；"
    r"指数 $x^{\frac{1}{2}}$；下标 $a_{\frac{m}{n}}$；"
    r"嵌套 $\dfrac{1+\frac{x}{y}}{\frac{a}{b}}$。"
)
ANSWER = (
    r"解析：主体 $\upe-\frac{2x}{x+3}$；"
    r"指数 $2^{\dfrac{p}{q}}$；下标 $b_{\dfrac{1}{3}}$；"
    r"嵌套 $\frac{\dfrac{u}{v}+1}{2}$。"
)
NORMALIZED_ANSWER = (
    r"解析：主体 $\upe-\dfrac{2x}{x+3}$；"
    r"指数 $2^{\frac{p}{q}}$；下标 $b_{\frac{1}{3}}$；"
    r"嵌套 $\dfrac{\frac{u}{v}+1}{2}$。"
)


def _preview_metrics(browser, preview_id):
    return browser.evaluate("""
    (() => {
        const host = document.getElementById(%s);
        return {
            errors: host.querySelectorAll('.katex-error').length,
            formulas: [...host.querySelectorAll('.katex')].map(node => ({
                tex: node.querySelector('annotation').textContent,
                // The inline katex-html box has a fixed line height. Its base
                // struts encode the actual formula's ascent plus descent.
                height: Math.max(...[...node.querySelectorAll('.katex-html > .base > .strut')]
                    .map(strut => strut.getBoundingClientRect().height)),
            })),
        };
    })()
    """ % json.dumps(preview_id))


def test_fraction_buttons_preserve_compact_contexts_and_unsaved_state(browser, tmp_path):
    question_id = browser.evaluate("""
    (async () => {
        selectWorkspace('bank', '题库研讨工作台');
        const form = new FormData();
        form.set('content', %s);
        form.set('answer_markdown', %s);
        form.set('question_type', 'detailed_answer');
        form.set('difficulty', 'medium');
        const response = await fetch('/api/questions', {method: 'POST', body: form});
        const data = await response.json();
        if (!response.ok || !data.question) throw new Error(JSON.stringify(data));
        loadQuestions();
        return data.question.id;
    })()
    """ % (json.dumps(CONTENT), json.dumps(ANSWER)))

    def persisted_question():
        return browser.evaluate(
            f"fetch('/api/questions/{question_id}').then(response => response.json())"
        )

    original = persisted_question()
    assert original["content"] == CONTENT
    assert original["answer_markdown"] == ANSWER
    browser.command("wait", "--fn", f"!!document.querySelector('#questionsList [data-id=\"{question_id}\"]')")
    browser.command("click", f'#questionsList [data-id="{question_id}"]')
    browser.command("wait", "--fn", (
        f"EditorState.questionId === {question_id} && !questionDetailLoading && !isEditorModified()"
    ))
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="content"]')
    browser.command("wait", "--fn", "document.querySelectorAll('#contentPreview .katex').length === 4")
    browser.settle()
    before = _preview_metrics(browser, "contentPreview")
    assert not before["errors"]

    browser.command("scrollintoview", "#normalizeContentFractionsBtn")
    browser.settle()
    browser.command("click", "#normalizeContentFractionsBtn")
    browser.command("wait", "--fn", (
        "!document.getElementById('normalizeContentFractionsBtn').disabled && "
        "document.getElementById('editContent').value === " + json.dumps(NORMALIZED_CONTENT)
    ))
    browser.command("wait", "--fn", (
        "document.querySelector('#contentPreview annotation').textContent === "
        + json.dumps(r"\eu^x-\dfrac{x+1}{x-1}")
    ))
    browser.settle()
    after = _preview_metrics(browser, "contentPreview")
    assert not after["errors"]
    assert [formula["tex"] for formula in after["formulas"]] == [
        r"\eu^x-\dfrac{x+1}{x-1}", r"x^{\frac{1}{2}}", r"a_{\frac{m}{n}}",
        r"\dfrac{1+\frac{x}{y}}{\frac{a}{b}}",
    ]
    assert after["formulas"][0]["height"] > before["formulas"][0]["height"] + 3
    assert after["formulas"][1]["height"] < before["formulas"][1]["height"]
    assert after["formulas"][2]["height"] < before["formulas"][2]["height"]
    assert browser.evaluate("isEditorModified()")
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == ANSWER
    assert persisted_question() == original
    content_screenshot = tmp_path / "fraction-content-normalized.png"
    browser.command("screenshot", str(content_screenshot))

    browser.command("click", '[data-editor-panel-target="answer"]')
    browser.command("scrollintoview", "#normalizeAnswerFractionsBtn")
    browser.settle()
    browser.command("click", "#normalizeAnswerFractionsBtn")
    browser.command("wait", "--fn", (
        "!document.getElementById('normalizeAnswerFractionsBtn').disabled && "
        "document.getElementById('editAnswerMarkdown').value === " + json.dumps(NORMALIZED_ANSWER)
    ))
    browser.command("wait", "--fn", (
        "document.querySelector('#answerPreview annotation').textContent === "
        + json.dumps(r"\upe-\dfrac{2x}{x+3}")
    ))
    browser.settle()
    answer = _preview_metrics(browser, "answerPreview")
    assert not answer["errors"]
    assert [formula["tex"] for formula in answer["formulas"]] == [
        r"\upe-\dfrac{2x}{x+3}", r"2^{\frac{p}{q}}", r"b_{\frac{1}{3}}",
        r"\dfrac{\frac{u}{v}+1}{2}",
    ]
    assert browser.evaluate("isEditorModified()")
    assert browser.evaluate("document.getElementById('editContent').value") == NORMALIZED_CONTENT
    assert persisted_question() == original
    answer_screenshot = tmp_path / "fraction-answer-normalized.png"
    browser.command("screenshot", str(answer_screenshot))

    # A second real button click is idempotent and cannot mark edits as saved.
    browser.command("click", "#normalizeAnswerFractionsBtn")
    browser.command("wait", "--fn", "!document.getElementById('normalizeAnswerFractionsBtn').disabled")
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == NORMALIZED_ANSWER
    assert browser.evaluate("isEditorModified()")
    assert persisted_question() == original
    print(f"Fraction visual QA: {content_screenshot}\nFraction visual QA: {answer_screenshot}")
