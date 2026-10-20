"""Opt-in equation-number geometry checks in a source-only isolated app.

Run with MATHBANK_TEST_BROWSER=1 python3 -m pytest --noconftest
tests/test_equation_tag_browser.py. The reused fixture owns a disposable
database, server, uploads directory, and Chromium session.
"""

import json
import math

from test_preview_browser import browser, pytestmark  # noqa: F401


LONG_BODY = (
    r"P(A)=P(X\ge n+1)=P(X=n+1)+P(X=n+2)+\cdots+P(X=2n+1)"
)
LONG_FORMULA = LONG_BODY + r"\tag{6.9.5}"
PREVIEW_SOURCE = (
    r"公式编号回归：\[x+y=z\tag{短}\]"
    "\n\n" + r"\[" + LONG_FORMULA + r"\]"
    "\n\n" + r"\[x^2+y^2=1\]"
)


# The bounds are actual visible glyphs, not the .base or .tag containers:
# KaTeX's vlist struts deliberately extend outside those containers.
INSTALL_MEASUREMENTS = r"""
(() => {
    const ink = root => [...root.querySelectorAll('span')].filter(node =>
        !node.children.length && node.textContent.replace(/[\s\u200b]/g, '')
    ).map(node => {
        const range = document.createRange();
        range.selectNodeContents(node);
        const rect = range.getBoundingClientRect();
        return {text:node.textContent, left:rect.left, right:rect.right,
            top:rect.top, bottom:rect.bottom, width:rect.width, height:rect.height,
            tag:Boolean(node.closest('.tag'))};
    }).filter(rect => rect.width > 0 && rect.height > 0);
    const bounds = leaves => leaves.length ? {
        left:Math.min(...leaves.map(rect => rect.left)),
        right:Math.max(...leaves.map(rect => rect.right)),
        top:Math.min(...leaves.map(rect => rect.top)),
        bottom:Math.max(...leaves.map(rect => rect.bottom)),
    } : null;
    window.__equationTagMeasure = selector => {
        const host = document.querySelector(selector);
        if (!host) throw new Error('Missing equation preview: '+selector);
        return {
            selector, width:host.clientWidth,
            errors:host.querySelectorAll('.katex-error').length,
            displays:[...host.querySelectorAll('.katex-display')].map(display => {
                display.scrollLeft = 0;
                const html = display.querySelector('.katex-html');
                const leaves = ink(html);
                const body = leaves.filter(rect => !rect.tag);
                const tags = leaves.filter(rect => rect.tag);
                const rect = display.getBoundingClientRect();
                const intersections = [];
                for (const tag of tags) for (const glyph of body) {
                    const width = Math.min(tag.right,glyph.right)-Math.max(tag.left,glyph.left);
                    const height = Math.min(tag.bottom,glyph.bottom)-Math.max(tag.top,glyph.top);
                    if (width > 0.5 && height > 0.5) intersections.push({tag,glyph,width,height});
                }
                const first = bounds(leaves);
                display.scrollLeft = display.scrollWidth;
                const last = bounds(ink(html));
                const result = {
                    source:display.querySelector('annotation').textContent,
                    width:display.clientWidth, scrollWidth:display.scrollWidth,
                    maxScroll:display.scrollWidth-display.clientWidth,
                    scrollEnd:display.scrollLeft,
                    startLeft:first.left-rect.left,
                    endRight:rect.right-last.right,
                    body:bounds(body), tag:bounds(tags), tags, bodyGlyphs:body,
                    intersections,
                    center:(bounds(body).left+bounds(body).right-rect.left-rect.right)/2,
                    leqno:display.classList.contains('leqno'),
                };
                display.scrollLeft = 0;
                return result;
            }),
        };
    };
    return true;
})()
"""


def _assert_geometry(metrics, *, expected_count, expected_tagged):
    assert metrics["width"] > 0, metrics
    assert metrics["errors"] == 0, metrics
    assert len(metrics["displays"]) == expected_count, metrics
    assert sum(bool(display["tag"]) for display in metrics["displays"]) == expected_tagged, metrics
    for display in metrics["displays"]:
        assert display["width"] <= metrics["width"] + 2, display
        assert not display["intersections"], display
        # Both extremes must be reachable; checking only scrollWidth misses
        # centered overflow whose beginning lies at a negative coordinate.
        assert display["startLeft"] >= -2, display
        assert display["endRight"] >= -2, display
        assert abs(display["scrollEnd"] - display["maxScroll"]) <= 1, display
        if display["tag"]:
            if display["leqno"]:
                gap = display["body"]["left"] - display["tag"]["right"]
            else:
                gap = display["tag"]["left"] - display["body"]["right"]
            assert gap >= 4, display


def _reload_clean_ui(browser):
    browser.command("set", "viewport", "1600", "1100")
    browser.command("reload")
    browser.command("wait", "--fn", "typeof window.renderPaperCanvas === 'function'")
    browser.evaluate("selectWorkspace('paper', '智能组卷'); true")
    browser.command("wait", "--fn", "!PaperStore.cartQuestionLoad.loading")


def test_tagged_displays_keep_glyphs_separate_and_reachable_during_resize(browser, tmp_path):
    cases = [
        {"name": "short", "source": r"x+y=z\tag{1}"},
        {"name": "threshold", "source": LONG_FORMULA},
        {"name": "long_tag", "source": LONG_BODY + r"\tag{123456789012345678901234567890}"},
        {"name": "star", "source": LONG_BODY + r"\tag*{manual-label}"},
        {"name": "align", "source": r"\begin{align}A&=" + LONG_BODY + r"\tag{first}\\B&=x+y\tag{second}\end{align}"},
        {"name": "no_tag", "source": r"x^2+y^2=1"},
        {"name": "fleqn", "source": LONG_FORMULA, "fleqn": True},
        {"name": "leqno", "source": LONG_FORMULA, "leqno": True},
        {"name": "both", "source": LONG_FORMULA, "fleqn": True, "leqno": True},
    ]
    try:
        browser.evaluate(INSTALL_MEASUREMENTS)
        browser.evaluate(r"""
        (() => {
            const panel=document.createElement('section');
            panel.id='equation-tag-fixtures';
            panel.className='preview-box bg-white text-slate-900';
            Object.assign(panel.style, {position:'fixed', zIndex:'99999', top:'20px', left:'20px',
                width:'min(1200px, calc(100vw - 40px))', maxHeight:'calc(100vh - 40px)',
                overflowY:'auto', padding:'16px', fontSize:'16px'});
            document.body.append(panel);
            for (const item of %s) {
                const host=document.createElement('div');
                host.id='equation-tag-'+item.name;
                panel.append(host);
                katex.render(item.source, host, {displayMode:true, throwOnError:false,
                    fleqn:Boolean(item.fleqn), leqno:Boolean(item.leqno)});
            }
            return true;
        })()
        """ % json.dumps(cases))
        browser.settle()
        initial = browser.evaluate("__equationTagMeasure('#equation-tag-threshold')")["displays"][0]
        natural_width = (
            initial["body"]["right"] - initial["body"]["left"]
            + initial["tag"]["right"] - initial["tag"]["left"]
        )
        # This narrow width allows each part on its own, but cannot fit both.
        # It reproduces the user's overlap before either part is individually
        # wider than the display and catches late scrollbar activation.
        threshold_viewport = math.ceil(natural_width + 40 + 32 + 5)
        for width in [1600, threshold_viewport, 375, 1600]:
            browser.command("set", "viewport", str(width), "1100")
            browser.settle()
            result = {}
            for item in cases:
                metrics = browser.evaluate("__equationTagMeasure(" + json.dumps("#equation-tag-"+item["name"]) + ")")
                _assert_geometry(metrics, expected_count=1, expected_tagged=item["name"] != "no_tag")
                display = metrics["displays"][0]
                assert display["source"] == item["source"], display
                if item["name"] == "no_tag":
                    assert abs(display["center"]) <= 2, display
                if item["name"] == "threshold" and width == threshold_viewport:
                    assert display["maxScroll"] > 0, display
                    assert display["body"]["right"]-display["body"]["left"] < display["width"], display
                    assert display["tag"]["right"]-display["tag"]["left"] < display["width"], display
                if item["name"] == "align":
                    # Each row's label keeps its corresponding row baseline.
                    for label, variable in [("first", "A"), ("second", "B")]:
                        tag = next(rect for rect in display["tags"] if rect["text"] == label)
                        row = next(rect for rect in display["bodyGlyphs"] if rect["text"] == variable)
                        assert abs(tag["bottom"]-row["bottom"]) <= 2, display
                result[item["name"]] = metrics
            (tmp_path / f"equation-tag-bounds-{width}.json").write_text(json.dumps(result, indent=2))
        browser.command("screenshot", str(tmp_path / "equation-tags-wide.png"))
        browser.command("set", "viewport", "375", "1100")
        browser.settle()
        browser.command("screenshot", str(tmp_path / "equation-tags-narrow.png"))
    finally:
        browser.evaluate("document.getElementById('equation-tag-fixtures')?.remove(); delete window.__equationTagMeasure; true")
        _reload_clean_ui(browser)


def test_tag_layout_in_real_detail_editor_import_and_paper_previews(browser, tmp_path):
    question_id = None
    original_cart = browser.evaluate("localStorage.getItem('mathbank_paper_cart')")
    try:
        browser.evaluate(INSTALL_MEASUREMENTS)
        question_id = browser.evaluate("""
        (async () => {
            selectWorkspace('bank', '题库管理');
            const form = new FormData();
            Object.entries({content:%s, answer_markdown:%s, review:%s,
                question_type:'detailed_answer', difficulty:'medium'})
                .forEach(([key,value]) => form.set(key,value));
            const response=await fetch('/api/questions',{method:'POST',body:form});
            const result=await response.json();
            if (!response.ok || !result.question) throw new Error(JSON.stringify(result));
            await loadQuestions();
            return result.question.id;
        })()
        """ % ((json.dumps(PREVIEW_SOURCE),) * 3))
        original = browser.evaluate(f"fetch('/api/questions/{question_id}').then(response=>response.json())")
        card = f'#questionsList [data-id="{question_id}"]'
        browser.command("wait", "--fn", "!!document.querySelector(" + json.dumps(card) + ")")
        browser.command("click", card)
        browser.command("wait", "--fn", f"EditorState.questionId === {question_id} && !questionDetailLoading && !isEditorModified()")
        for width in [1600, 920, 375]:
            browser.command("set", "viewport", str(width), "1100")
            browser.evaluate("showBankDetail(); true")
            browser.settle()
            for selector in ["#paperContent", "#paperAnalysisContent", "#paperReviewContent"]:
                _assert_geometry(browser.evaluate("__equationTagMeasure(" + json.dumps(selector) + ")"), expected_count=3, expected_tagged=2)
        browser.command("set", "viewport", "1600", "1100")
        browser.command("click", "#editQuestionFromPreviewBtn")
        for panel, selector in [("content", "#contentPreview"), ("answer", "#answerPreview")]:
            browser.command("click", f'[data-editor-panel-target="{panel}"]')
            for width in [1600, 375]:
                browser.command("set", "viewport", str(width), "1100")
                browser.settle()
                _assert_geometry(browser.evaluate("__equationTagMeasure(" + json.dumps(selector) + ")"), expected_count=3, expected_tagged=2)
        assert not browser.evaluate("isEditorModified()")
        browser.command("click", "#editorSection .question-editor-close")
        browser.evaluate("""
        (() => {
            selectWorkspace('import', '导入中心');
            document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
            document.getElementById('importPlaceholder').classList.add('hidden');
            replaceParsedQuestions([{content:%s, answer_markdown:%s,
                question_type:'detailed_answer', difficulty:'medium', image_paths:[]}]);
            renderParsedQuestionsList(parsedQuestionsData);
            return true;
        })()
        """ % ((json.dumps(PREVIEW_SOURCE),) * 2))
        for width in [1600, 375]:
            browser.command("set", "viewport", str(width), "1100")
            browser.settle()
            for selector in ["#parsed-card-0 .card-content-preview", "#parsed-card-0 .card-answer-preview"]:
                _assert_geometry(browser.evaluate("__equationTagMeasure(" + json.dumps(selector) + ")"), expected_count=3, expected_tagged=2)
        browser.evaluate(f"""
        (async () => {{
            const question=await fetch('/api/questions/{question_id}').then(response=>response.json());
            PaperStore.cart=[];
            PaperStore.questionsMap[question.id]=question;
            addToCart(question.id,5);
            selectWorkspace('paper','智能组卷');
            return true;
        }})()
        """)
        browser.command("set", "viewport", "1600", "1100")
        browser.command("wait", "--fn", "document.querySelectorAll('#a4PaperPreviewSheet .katex-display').length === 3")
        browser.settle()
        _assert_geometry(browser.evaluate("__equationTagMeasure('#a4PaperPreviewSheet')"), expected_count=3, expected_tagged=2)
        browser.command("screenshot", str(tmp_path / "equation-tags-paper.png"))
        saved = browser.evaluate(f"fetch('/api/questions/{question_id}').then(response=>response.json())")
        assert saved == original
        print(f"Equation-tag visual QA: {tmp_path}")
    finally:
        browser.evaluate("""
        (async () => {
            closeQuestionEditorModal();
            replaceParsedQuestions([]);
            PaperStore.cart=[];
            if (%s !== null) await fetch('/api/questions/'+%s,{method:'DELETE'});
            if (%s === null) localStorage.removeItem('mathbank_paper_cart');
            else localStorage.setItem('mathbank_paper_cart',%s);
            delete window.__equationTagMeasure;
            return true;
        })()
        """ % (json.dumps(question_id), json.dumps(question_id), json.dumps(original_cart), json.dumps(original_cart)))
        _reload_clean_ui(browser)
