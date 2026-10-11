"""Opt-in exam-zh upright-constant checks in a source-only isolated app."""

import json

from test_preview_browser import browser, pytestmark  # noqa: F401


CONSTANT_FORMULAS = [r"\iu", r"\upi", r"\eu", r"\upe", "i", "e"]
CONTENT_FORMULAS = CONSTANT_FORMULAS + [
    r"\dfrac{\eu^{\iu\pi}+1}{\upe-\upi}",
    r"\iu_1+\eu^2",
    r"\iu", r"\eu", r"\upi", r"\upe",
    r"\iu+i", r"\upi-i", r"\eu+e", r"\upe-e",
]
CONTENT = (
    r"正体常数 $\iu$、$\upi$、$\eu$、$\upe$；普通变量 $i$、$e$。"
    "\n\n"
    r"复合公式 $\dfrac{\eu^{\iu\pi}+1}{\upe-\upi}$；上下标 $\iu_1+\eu^2$。"
    "\n\n"
    r"\begin{tabular}{cc}虚数 $\iu$ & 自然底数 $\eu$ \\ "
    r"别名 $\upi$ & 别名 $\upe$ \end{tabular}"
    "\n\n"
    "\\begin{choices}\n"
    "\\item $\\iu+i$\n"
    "\\item $\\upi-i$\n"
    "\\item $\\eu+e$\n"
    "\\item $\\upe-e$\n"
    r"\end{choices}"
)
ANSWER_FORMULAS = CONSTANT_FORMULAS + [r"\eu^{\iu\pi}+1=0"]
ANSWER = (
    r"解析正体 $\iu$、$\upi$、$\eu$、$\upe$；变量 $i$、$e$；"
    r"由 $\eu^{\iu\pi}+1=0$ 得结论。"
)


def _metrics(browser, selector):
    return browser.evaluate("""
    (() => {
        const host = document.querySelector(%s);
        return {
            errors: host.querySelectorAll('.katex-error').length,
            tables: host.querySelectorAll('table').length,
            options: host.querySelectorAll('.choices-content').length,
            formulas: [...host.querySelectorAll('.katex')].map(math => {
                const html = math.querySelector('.katex-html');
                const leaf = [...html.querySelectorAll('span')]
                    .find(span => !span.children.length && /^[ie]$/.test(span.textContent));
                const style = leaf && getComputedStyle(leaf);
                return {
                    source: math.querySelector('annotation').textContent,
                    text: html.textContent,
                    font: style && style.fontFamily,
                    fontStyle: style && style.fontStyle,
                };
            }),
        };
    })()
    """ % json.dumps(selector))


def _assert_constants(metrics, formulas, *, content=False):
    assert metrics["errors"] == 0, metrics
    assert [item["source"] for item in metrics["formulas"]] == formulas, metrics
    assert all("\\" not in item["text"] for item in metrics["formulas"]), metrics
    constants = metrics["formulas"][:6]
    assert [item["text"] for item in constants] == ["i", "i", "e", "e", "i", "e"]
    # The constants use KaTeX's upright glyphs; ordinary variables retain
    # their separate italic mathematical glyphs in the actual browser.
    assert all("KaTeX_Main" in item["font"] for item in constants[:4]), constants
    assert all(item["fontStyle"] == "normal" for item in constants[:4]), constants
    assert all("KaTeX_Math" in item["font"] for item in constants[4:]), constants
    assert all(item["fontStyle"] == "italic" for item in constants[4:]), constants
    if content:
        assert metrics["tables"] == 1 and metrics["options"] == 4, metrics


def test_examzh_constants_in_editor_import_and_paper_keep_sources(browser, tmp_path):
    question_id = browser.evaluate("""
    (async () => {
        selectWorkspace('bank', '题库管理');
        const form = new FormData();
        Object.entries({content: %s, answer_markdown: %s,
            question_type: 'single_choice', difficulty: 'medium'})
            .forEach(([key, value]) => form.set(key, value));
        const response = await fetch('/api/questions', {method: 'POST', body: form});
        const data = await response.json();
        if (!response.ok || !data.question) throw new Error(JSON.stringify(data));
        loadQuestions();
        return data.question.id;
    })()
    """ % (json.dumps(CONTENT), json.dumps(ANSWER)))
    original = browser.evaluate(f"fetch('/api/questions/{question_id}').then(r => r.json())")
    # Multipart FormData uses CRLF on the wire; textarea values use LF.
    assert original["content"].replace("\r\n", "\n") == CONTENT
    assert original["answer_markdown"] == ANSWER
    browser.command("wait", "--fn", f"!!document.querySelector('#questionsList [data-id=\"{question_id}\"]')")
    browser.command("click", f'#questionsList [data-id="{question_id}"]')
    browser.command("wait", "--fn", f"EditorState.questionId === {question_id} && !questionDetailLoading && !isEditorModified()")
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="content"]')
    browser.command("wait", "--fn", f"document.querySelectorAll('#contentPreview .katex').length === {len(CONTENT_FORMULAS)}")
    browser.settle()
    _assert_constants(_metrics(browser, "#contentPreview"), CONTENT_FORMULAS, content=True)
    _assert_constants(_metrics(browser, "#paperContent"), CONTENT_FORMULAS, content=True)
    assert browser.evaluate("document.getElementById('editContent').value") == CONTENT
    assert not browser.evaluate("isEditorModified()")
    browser.command("scrollintoview", "#contentPreview")
    browser.settle()
    browser.command("screenshot", str(tmp_path / "examzh-constants-editor.png"))

    browser.command("click", '[data-editor-panel-target="answer"]')
    browser.command("wait", "--fn", f"document.querySelectorAll('#answerPreview .katex').length === {len(ANSWER_FORMULAS)}")
    browser.settle()
    _assert_constants(_metrics(browser, "#answerPreview"), ANSWER_FORMULAS)
    _assert_constants(_metrics(browser, "#paperAnalysisContent"), ANSWER_FORMULAS)
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == ANSWER
    assert not browser.evaluate("isEditorModified()")
    browser.command("scrollintoview", "#answerPreview")
    browser.settle()
    browser.command("screenshot", str(tmp_path / "examzh-constants-answer.png"))
    browser.command("click", "#editorSection .question-editor-close")
    assert not browser.evaluate("!!document.getElementById('unsavedChangesModalTitle')")

    browser.evaluate("""
    (() => {
        selectWorkspace('import', '导入中心');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        document.getElementById('importPlaceholder').classList.add('hidden');
        replaceParsedQuestions([{content: %s, answer_markdown: %s,
            question_type: 'single_choice', difficulty: 'medium', image_paths: []}]);
        renderParsedQuestionsList(parsedQuestionsData);
        return true;
    })()
    """ % (json.dumps(CONTENT), json.dumps(ANSWER)))
    browser.command("wait", "--fn", f"document.querySelectorAll('#parsed-card-0 .card-content-preview .katex').length === {len(CONTENT_FORMULAS)}")
    browser.settle()
    _assert_constants(_metrics(browser, "#parsed-card-0 .card-content-preview"), CONTENT_FORMULAS, content=True)
    _assert_constants(_metrics(browser, "#parsed-card-0 .card-answer-preview"), ANSWER_FORMULAS)
    assert browser.evaluate("parsedQuestionsData[0].content") == CONTENT
    assert browser.evaluate("parsedQuestionsData[0].answer_markdown") == ANSWER
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == CONTENT
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-answer-textarea').value") == ANSWER
    browser.command("scrollintoview", "#parsed-card-0")
    browser.settle()
    browser.command("screenshot", str(tmp_path / "examzh-constants-import.png"))

    browser.evaluate("""
    (async () => {
        const question = await fetch('/api/questions/%s').then(r => r.json());
        PaperStore.cart = [];
        PaperStore.questionsMap[question.id] = question;
        addToCart(question.id, 5);
        selectWorkspace('paper', '智能组卷');
        return true;
    })()
    """ % question_id)
    browser.command("wait", "--fn", f"document.querySelectorAll('#a4PaperPreviewSheet .katex').length === {len(CONTENT_FORMULAS)}")
    browser.settle()
    _assert_constants(_metrics(browser, "#a4PaperPreviewSheet"), CONTENT_FORMULAS, content=True)
    assert browser.evaluate(f"PaperStore.questionsMap[{question_id}].content") == original["content"]
    assert browser.evaluate(f"PaperStore.questionsMap[{question_id}].answer_markdown") == ANSWER
    browser.command("screenshot", str(tmp_path / "examzh-constants-paper.png"))
    saved = browser.evaluate(f"fetch('/api/questions/{question_id}').then(r => r.json())")
    assert saved == original
    print(f"Exam-zh constant visual QA: {tmp_path}")
