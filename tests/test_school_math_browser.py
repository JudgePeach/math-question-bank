"""Opt-in real-browser school-symbol checks using an isolated offline app."""

import json

from test_preview_browser import browser, pytestmark  # noqa: F401


CONTENT = (
    r"短弧 $\wideparen{AB}$，长弧 $\wideparen{ABCDEFGHIJ}$；弧前甲、弧后乙。"
    "\n\n"
    r"嵌套 $\wideparen{\wideparen{AB}+\wideparen{CD}}$；"
    r"上下标 $S_{\wideparen{AB}}+\wideparen{CD}^{2}$；"
    r"分式 $\dfrac{\overparen{AB}}{\overarc[1]{CD}}$。"
    "\n\n"
    r"千分号 $3\perthousand+4\permil+5\textperthousand+6‰$；正文 7‰，"
    r"温度 $25\celsius$、$26℃$；角度 $\ang{30;15;20}$；斜分式 $\sfrac{1}{2}$。"
    "\n\n"
    r"\begin{tabular}{cc} 圆弧 $\widearc{EFG}$ & 温度 $-5\celsius$ \\ "
    r"千分率 $2‰$ & 角度 $\ang{45}$ \end{tabular}"
)
ANSWER = r"由 $\wideparen{AB}=\overarc{CD}$ 得 $\ang{30}$；答案保留 $5‰$ 与 $20℃$。"


def _metrics(browser, selector):
    return browser.evaluate("""
    (() => {
        const host = document.querySelector(%s);
        const arcs = [...host.querySelectorAll('.mb-arc-accent')];
        const rates = [...host.querySelectorAll('.mb-perthousand')];
        return {
            errors: host.querySelectorAll('.katex-error').length,
            text: host.textContent,
            renderedMath: [...host.querySelectorAll('.katex-html')].map(n => n.textContent).join(' '),
            arcs: arcs.map(arc => {
                const rect = arc.getBoundingClientRect();
                const vlist = arc.closest('.vlist');
                const base = vlist.firstElementChild.lastElementChild.getBoundingClientRect();
                const vrect = vlist.getBoundingClientRect();
                const svg = arc.querySelector('svg');
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                    vlistWidth: vrect.width, baseTop: base.top, baseBottom: base.bottom,
                    baseHeight: base.height, path: !!svg.querySelector('path'),
                    stretch: svg.getAttribute('preserveAspectRatio'),
                    glyphText: arc.textContent};
            }),
            rates: rates.map(rate => {
                const rect = rate.getBoundingClientRect();
                return {width: rect.width, height: rect.height,
                    path: !!rate.querySelector('svg path'),
                    glyphText: rate.textContent};
            }),
            semanticArcs: [...host.querySelectorAll('math mo')].filter(n => n.textContent === '⏜').length,
            semanticRates: [...host.querySelectorAll('math mo')].filter(n => n.textContent === '‰').length,
        };
    })()
    """ % json.dumps(selector))


def _assert_shapes(metrics, arc_count, rate_count):
    assert metrics["errors"] == 0
    assert "\\" not in metrics["renderedMath"]
    assert len(metrics["arcs"]) == metrics["semanticArcs"] == arc_count
    assert len(metrics["rates"]) == metrics["semanticRates"] == rate_count
    for arc in metrics["arcs"]:
        assert arc["path"] and arc["stretch"] == "none"
        assert arc["glyphText"] == ""  # glyph is local SVG, not font fallback
        assert arc["width"] > 4 and arc["height"] > 1
        assert abs(arc["width"] - arc["vlistWidth"]) < 0.1
        # The arc stays above the base's lower half, including nested bases.
        assert arc["y"] + arc["height"] < arc["baseBottom"] - arc["baseHeight"] * 0.3
    for rate in metrics["rates"]:
        assert rate["width"] > 4 and rate["height"] > 3
        assert rate["glyphText"] == ""
        assert rate["path"]  # visually reviewed as three hollow rings and one diagonal


def test_school_math_editor_answer_shortcuts_and_paper(browser, tmp_path):
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
    browser.command("wait", "--fn", "document.querySelectorAll('#contentPreview .mb-arc-accent').length === 10")
    browser.settle()
    editor = _metrics(browser, "#contentPreview")
    _assert_shapes(editor, 10, 6)
    assert editor["arcs"][1]["width"] > editor["arcs"][0]["width"] * 3
    assert "弧前甲、弧后乙" in editor["text"]
    assert "千分率" in editor["text"] and "温度" in editor["text"]
    assert browser.evaluate("document.querySelectorAll('#contentPreview table .mb-arc-accent').length") == 1
    assert browser.evaluate("document.querySelectorAll('#contentPreview table .mb-perthousand').length") == 1
    assert browser.evaluate("document.getElementById('editContent').value") == CONTENT
    assert not browser.evaluate("isEditorModified()")
    browser.command("scrollintoview", "#contentPreview")
    browser.settle()
    browser.command("screenshot", str(tmp_path / "school-math-editor.png"))

    browser.command("click", '[data-editor-panel-target="answer"]')
    browser.command("wait", "--fn", "document.querySelectorAll('#answerPreview .mb-arc-accent').length === 2")
    browser.settle()
    _assert_shapes(_metrics(browser, "#answerPreview"), 2, 1)
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == ANSWER
    browser.command("scrollintoview", "#answerPreview")
    browser.settle()
    browser.command("screenshot", str(tmp_path / "school-math-answer.png"))

    browser.command("click", '[data-editor-panel-target="content"]')
    browser.command("click", "#contentTabBtn-manual")
    browser.evaluate("document.getElementById('contentTabContent-manual').open = true; true")
    for command in [r"\wideparen{AB}", r"\ang{30;15;20}", r"\perthousand", r"\celsius"]:
        browser.evaluate("(() => { const input = document.getElementById('editContent'); input.value = ''; input.setSelectionRange(0, 0); return true; })()")
        name = command.split("{")[0].removeprefix("\\")
        if name == "ang":
            name = "ang{30"
        browser.command("click", f'#contentTabContent-manual button[onclick*="{name}"]')
        assert browser.evaluate("document.getElementById('editContent').value") == command
        assert browser.evaluate("isEditorModified()")
        browser.command("wait", "--fn", (
            "document.querySelector('#contentPreview annotation')?.textContent === " + json.dumps(command)
        ))
        assert "\\" not in _metrics(browser, "#contentPreview")["renderedMath"]
    # Restore the saved fixture without persisting the temporary shortcut input.
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
    browser.command("wait", "--fn", "document.querySelectorAll('#a4PaperPreviewSheet .mb-arc-accent').length === 10")
    browser.settle()
    _assert_shapes(_metrics(browser, "#a4PaperPreviewSheet"), 10, 6)
    browser.command("screenshot", str(tmp_path / "school-math-paper.png"))
    saved = browser.evaluate(f"fetch('/api/questions/{question_id}').then(r => r.json())")
    assert saved == original
    print(f"School math visual QA: {tmp_path}")
