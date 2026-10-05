"""Opt-in currency and hard-break regressions in a source-only app copy."""

import json
import os

import pytest
from PIL import Image

from test_preview_browser import browser


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in real currency preview")
@pytest.mark.parametrize("viewport", [(1600, 1100), (375, 812)])
def test_currency_math_and_spaced_hard_breaks_render_without_rewriting_inputs(browser, tmp_path, viewport):
    browser.command("set", "viewport", *map(str, viewport))
    cases = {
        "formula": "$2 xy+3$",
        "hardline": "第一行" + "\\" * 2 + " 第二行 $2 xy+3$",
        "prices": "Price $5 and $10; compute $2 xy+3$.",
        "url": "See https://example.test/$5 and compute $x^2$.",
        "code": "Example `$5 and $10`, then $x^2$.",
        "link": "[price](/help/$5), then $x^2$.",
        "tex_verb": r"\verb|$5 and $10|, then $x^2$.",
        "detokenize": r"\detokenize{$5 and ${10}}, then $x^2$.",
        "image": "![$5](/static/uploads/tmp/currency-price.png) then $x^2$.",
        "table": r"\begin{tabular}{cc}\verb|$5| & $x^2$\\[price](/help/$5) & 2\end{tabular}",
        "unsafe_code": "`<img src=x onerror=window.__currencyInjected=1>$5</code>` then $x^2$.",
        "html_code": "<CODE class=example>$5 and $10</code> then $x^2$.",
    }
    image = browser.root / "static/uploads/tmp/currency-price.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 10), "white").save(image)
    metrics = browser.evaluate(r"""
    (() => {
        selectWorkspace('import', '导入中心');
        const wrapper = document.getElementById('parsedQuestionsWrapper');
        wrapper.classList.remove('hidden');
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('currencyPreviewFixtures')?.remove();
        const panel = document.createElement('section');
        panel.id = 'currencyPreviewFixtures';
        panel.className = 'space-y-4 p-4 bg-white text-slate-900 text-sm leading-relaxed';
        wrapper.prepend(panel);
        const cases = %s;
        window.__currencyInjected = 0;
        const result = {};
        for (const [name, source] of Object.entries(cases)) {
            const label = document.createElement('p');
            label.textContent = name + ': ' + source;
            panel.append(label);
            const host = document.createElement('div');
            host.id = 'currency-preview-' + name;
            panel.append(host);
            renderQuestionPreviewContent(host, source);
            result[name] = {
                formulas: [...host.querySelectorAll('annotation')].map(node => node.textContent),
                errors: host.querySelectorAll('.katex-error').length,
                breaks: host.querySelectorAll('br').length,
                literalText: [...host.querySelectorAll('code.mb-preview-literal')].map(node => node.textContent),
                literalMath: host.querySelectorAll('code .katex').length,
                imagePaths: [...host.querySelectorAll('img')].map(node => node.getAttribute('src')),
            };
        }
        const content = document.getElementById('editContent');
        const answer = document.getElementById('editAnswerMarkdown');
        content.value = cases.hardline;
        answer.value = cases.hardline;
        updateContentPreview();
        updateAnswerPreview();
        result.inputs = {content: content.value, answer: answer.value};
        result.editorPreviews = ['contentPreview', 'paperContent', 'answerPreview', 'paperAnalysisContent'].map(id => {
            const host = document.getElementById(id);
            return {id, breaks: host.querySelectorAll('br').length,
                formulas: [...host.querySelectorAll('annotation')].map(node => node.textContent),
                errors: host.querySelectorAll('.katex-error').length};
        });
        const literalSource = cases.url + '\\\\ ' + cases.code + ' ' + cases.tex_verb;
        content.value = literalSource;
        answer.value = literalSource;
        updateContentPreview();
        updateAnswerPreview();
        result.literalInputs = {content: content.value, answer: answer.value, expected: literalSource};
        result.literalEditorPreviews = ['contentPreview', 'paperContent', 'answerPreview', 'paperAnalysisContent'].map(id => {
            const host = document.getElementById(id);
            return {id, breaks: host.querySelectorAll('br').length,
                formulas: [...host.querySelectorAll('annotation')].map(node => node.textContent),
                literalText: [...host.querySelectorAll('code.mb-preview-literal')].map(node => node.textContent),
                errors: host.querySelectorAll('.katex-error').length};
        });
        replaceParsedQuestions([{content: literalSource, answer_markdown: literalSource,
            question_type: 'detailed_answer', difficulty: 'normal', image_paths: []}]);
        renderParsedQuestionsList(parsedQuestionsData);
        result.importPreviews = ['.card-content-preview', '.card-answer-preview'].map(selector => {
            const host = document.querySelector('#parsed-card-0 ' + selector);
            return {formulas: [...host.querySelectorAll('annotation')].map(node => node.textContent),
                literalText: [...host.querySelectorAll('code.mb-preview-literal')].map(node => node.textContent),
                errors: host.querySelectorAll('.katex-error').length};
        });
        result.importSourceUnchanged = parsedQuestionsData[0].content === literalSource
            && parsedQuestionsData[0].answer_markdown === literalSource;
        MathBankBrowserChecks.seed(1, 1, true);
        PaperStore.questionsMap[1].content = literalSource;
        PaperStore.questionsMap[1].question_type = 'detailed_answer';
        renderPaperCanvas();
        const paper = document.querySelector('#a4PaperPreviewSheet [data-qid="1"]');
        result.paperPreview = {formulas: [...paper.querySelectorAll('annotation')].map(node => node.textContent),
            literalText: [...paper.querySelectorAll('code.mb-preview-literal')].map(node => node.textContent),
            errors: paper.querySelectorAll('.katex-error').length,
            sourceUnchanged: PaperStore.questionsMap[1].content === literalSource};
        result.injected = window.__currencyInjected;
        return result;
    })()
    """ % json.dumps(cases, ensure_ascii=False))
    assert metrics["formula"]["formulas"] == ["2 xy+3"], metrics
    assert metrics["hardline"]["formulas"] == ["2 xy+3"] and metrics["hardline"]["breaks"] == 1, metrics
    assert metrics["prices"]["formulas"] == [r"\text{\$}", r"\text{\$}", "2 xy+3"], metrics
    assert all(metrics[name]["errors"] == 0 for name in cases), metrics
    assert metrics["inputs"] == {"content": cases["hardline"], "answer": cases["hardline"]}, metrics
    assert all(item["breaks"] == 1 and item["errors"] == 0 and item["formulas"] == ["2 xy+3"]
               for item in metrics["editorPreviews"]), metrics
    for name in ["url", "code", "link", "tex_verb", "detokenize", "image", "table", "unsafe_code", "html_code"]:
        assert metrics[name]["formulas"] == ["x^2"] and metrics[name]["literalMath"] == 0, metrics
    assert metrics["url"]["literalText"] == ["https://example.test/$5"], metrics
    assert metrics["code"]["literalText"] == ["`$5 and $10`"], metrics
    assert metrics["link"]["literalText"] == ["](/help/$5)"], metrics
    assert metrics["image"]["imagePaths"] == ["/static/uploads/tmp/currency-price.png"], metrics
    assert not metrics["unsafe_code"]["imagePaths"] and metrics["injected"] == 0, metrics
    assert metrics["literalInputs"]["content"] == metrics["literalInputs"]["answer"] == metrics["literalInputs"]["expected"], metrics
    for item in metrics["literalEditorPreviews"] + metrics["importPreviews"] + [metrics["paperPreview"]]:
        assert item["formulas"] == ["x^2"] * 3 and item["errors"] == 0, metrics
        assert item["literalText"] == ["https://example.test/$5", "`$5 and $10`", r"\verb|$5 and $10|"], metrics
    assert metrics["importSourceUnchanged"] and metrics["paperPreview"]["sourceUnchanged"], metrics
    browser.command("wait", "--fn", "document.querySelector('#currency-preview-image img').complete && document.querySelector('#currency-preview-image img').naturalWidth === 20")
    browser.command("scrollintoview", "#currencyPreviewFixtures")
    browser.command("screenshot", str(tmp_path / f"currency-preview-{viewport[0]}.png"))


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in real blank boundary preview")
@pytest.mark.parametrize("viewport", [(1600, 1100), (375, 812)])
def test_fillin_next_to_closed_math_keeps_comparisons_and_tables(browser, tmp_path, viewport):
    browser.command("set", "viewport", *map(str, viewport))
    source = (
        r"\begin{tabular}{cc}项目 & $m$\\\end{tabular}"
        "\n\n" + r"则$\bar{x}$\fillin 91（填“$>$”“$=$”或“$<$”）"
        "\n\n" + r"\begin{tabular}{ccc}甲 & 93 & $k$\\\end{tabular}"
    )
    metrics = browser.evaluate(r"""
    (() => {
        selectWorkspace('import', '导入中心');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        document.getElementById('importPlaceholder').classList.add('hidden');
        const source = %s;
        replaceParsedQuestions([{content:source,answer_markdown:source,question_type:'detailed_answer',image_paths:[]}]);
        renderParsedQuestionsList(parsedQuestionsData);
        document.getElementById('editContent').value=source;
        document.getElementById('editAnswerMarkdown').value=source;
        updateContentPreview();updateAnswerPreview();
        const hosts=[
            document.querySelector('#parsed-card-0 .card-content-preview'),
            document.querySelector('#parsed-card-0 .card-answer-preview'),
            ...['contentPreview','paperContent','answerPreview','paperAnalysisContent'].map(id=>document.getElementById(id)),
        ];
        return {sourceUnchanged:parsedQuestionsData[0].content===source && parsedQuestionsData[0].answer_markdown===source,
            inputUnchanged:document.getElementById('editContent').value===source && document.getElementById('editAnswerMarkdown').value===source,
            previews:hosts.map(host=>({errors:host.querySelectorAll('.katex-error').length,tables:host.querySelectorAll('table').length,
                formulas:[...host.querySelectorAll('annotation')].map(node=>node.textContent)}))};
    })()
    """ % json.dumps(source, ensure_ascii=False))
    assert metrics["sourceUnchanged"] and metrics["inputUnchanged"], metrics
    expected = ["m", r"\bar{x}", r"\underline{\hspace{1.5cm}}", r"\gt ", "=", r"\lt ", "k"]
    assert all(item["errors"] == 0 and item["tables"] == 2 and item["formulas"] == expected
               for item in metrics["previews"]), metrics
    browser.command("scrollintoview", "#parsed-card-0")
    browser.settle()
    browser.command("screenshot", str(tmp_path / f"fillin-boundary-{viewport[0]}.png"))
