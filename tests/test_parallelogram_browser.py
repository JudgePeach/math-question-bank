"""Opt-in real editor and paper checks in the isolated browser fixture."""

import json

from test_preview_browser import browser, pytestmark  # noqa: F401


CONTENT = (
    r"命令 $\parallelogram ABCD$；符号 $▱EFGH$；文本 $\text{▱}IJKL$。"
    "\n\n"
    r"正文 ▱$MNOP$；下标 $S_{▱ABCD}$；分式 $\dfrac{▱}{\parallelogram}$。"
    "\n\n"
    r"\begin{tabular}{cc} $\parallelogram ABCD$ & ▱EFGH \end{tabular}"
)
ANSWER = r"由 $\parallelogram ABCD$ 的性质，$AB=CD$，$AD=BC$。"


def test_parallelogram_editor_answer_shortcut_and_paper(browser, tmp_path):
    question_id = browser.evaluate("""
    (async () => {
        selectWorkspace('bank', '题库管理');
        const form = new FormData();
        Object.entries({content: %s, answer_markdown: %s,
            question_type: 'detailed_answer', difficulty: 'medium'})
            .forEach(([key, value]) => form.set(key, value));
        const response = await fetch('/api/questions', {method: 'POST', body: form});
        const data = await response.json();
        if (!response.ok || !data.question) throw new Error(JSON.stringify(data));
        loadQuestions();
        return data.question.id;
    })()
    """ % (json.dumps(CONTENT), json.dumps(ANSWER)))
    original = browser.evaluate(f"fetch('/api/questions/{question_id}').then(r => r.json())")
    browser.command("wait", "--fn", f"!!document.querySelector('#questionsList [data-id=\"{question_id}\"]')")
    browser.command("click", f'#questionsList [data-id="{question_id}"]')
    browser.command("wait", "--fn", f"EditorState.questionId === {question_id} && !questionDetailLoading && !isEditorModified()")
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="content"]')
    browser.command("wait", "--fn", "document.querySelectorAll('#contentPreview .mb-parallelogram').length === 9")
    browser.settle()
    metrics = browser.evaluate("""
    (() => {
        const host = document.getElementById('contentPreview');
        const symbols = [...host.querySelectorAll('.mb-parallelogram')];
        return {
            errors: host.querySelectorAll('.katex-error').length,
            semanticSymbols: [...host.querySelectorAll('math mo')].filter(n => n.textContent === '▱').length,
            shapes: symbols.map(n => ({width: n.getBoundingClientRect().width,
                height: n.getBoundingClientRect().height, path: !!n.querySelector('svg path')})),
            input: document.getElementById('editContent').value,
            dirty: isEditorModified(),
        };
    })()
    """)
    assert metrics["errors"] == 0
    assert metrics["semanticSymbols"] == 9
    assert metrics["input"] == CONTENT
    assert not metrics["dirty"]
    assert all(s["path"] and s["width"] > 4 and s["height"] > 3 for s in metrics["shapes"])
    assert metrics["shapes"][4]["height"] < metrics["shapes"][0]["height"]  # subscript
    assert browser.evaluate(r"""
    (() => {
        const host = document.createElement('div');
        renderQuestionPreviewContent(host, String.raw`Let ▱ABCD be a parallelogram. \textbf{▱ABCD} \textbf{\parallelogram ABCD}`);
        return host.querySelectorAll('.mb-parallelogram').length === 3
            && host.querySelectorAll('strong .mb-parallelogram').length === 2
            && !host.querySelector('.katex-error')
            && host.firstChild.textContent.startsWith('Let ');
    })()
    """)
    browser.command("screenshot", str(tmp_path / "parallelogram-editor.png"))

    browser.command("click", '[data-editor-panel-target="answer"]')
    browser.command("wait", "--fn", "document.querySelectorAll('#answerPreview .mb-parallelogram').length === 1")
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == ANSWER
    assert browser.evaluate("document.querySelectorAll('#answerPreview .katex-error').length") == 0
    browser.command("click", '[data-editor-panel-target="content"]')
    browser.command("click", "#contentTabBtn-manual")
    browser.evaluate("document.getElementById('contentTabContent-manual').open = true; true")
    browser.evaluate("(() => { const input = document.getElementById('editContent'); input.setSelectionRange(input.value.length, input.value.length); return true; })()")
    browser.command("click", '#contentTabContent-manual button[onclick*="parallelogram"]')
    assert browser.evaluate("document.getElementById('editContent').value.endsWith('\\\\parallelogram ABCD')")
    assert browser.evaluate("isEditorModified()")
    # Restore the untouched fixture value before leaving the editor.
    browser.evaluate("document.getElementById('editContent').value = %s; updateContentPreview(); closeQuestionEditorModal(); true" % json.dumps(CONTENT))

    browser.evaluate("""
    (async () => {
        const question = await fetch('/api/questions/%s').then(r => r.json());
        PaperStore.cart = [];
        PaperStore.questionsMap[question.id] = question;
        addToCart(question.id, 10);
        selectWorkspace('paper', '智能组卷');
        return true;
    })()
    """ % question_id)
    browser.command("wait", "--fn", "document.querySelectorAll('#a4PaperPreviewSheet .mb-parallelogram').length === 9")
    browser.settle()
    assert browser.evaluate("document.querySelectorAll('#a4PaperPreviewSheet .katex-error').length") == 0
    browser.command("screenshot", str(tmp_path / "parallelogram-paper.png"))
    saved = browser.evaluate(f"fetch('/api/questions/{question_id}').then(r => r.json())")
    assert saved == original
    print(f"Parallelogram visual QA: {tmp_path}")
