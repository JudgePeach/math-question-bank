"""Opt-in fraction row/ruled-table geometry in the real offline KaTeX app.

Run with MATHBANK_TEST_BROWSER=1 python3 -m pytest --noconftest
tests/test_fraction_spacing_browser.py. The reused fixture owns an isolated
temporary application, database, server, and Chromium session.
"""

import json

from test_preview_browser import browser, pytestmark  # noqa: F401


PROBABILITY_ROWS = (
    r"P(X_1=0)=\dfrac{1}{3}\times\dfrac{2}{3}=\dfrac{2}{9}\\"
    r"P(X_1=1)=\dfrac{1}{3}\times\dfrac{1}{3}+\dfrac{2}{3}\times\dfrac{2}{3}=\dfrac{5}{9}\\"
    r"P(X_1=2)=\dfrac{2}{3}\times\dfrac{1}{3}=\dfrac{2}{9}"
)
PROBABILITY_ARRAY = r"\begin{array}{l}" + PROBABILITY_ROWS + r"\end{array}"
DISTRIBUTION_ARRAY = (
    r"\begin{array}{c|c|c|c}X_1&0&1&2\\\hline "
    r"P&\dfrac{2}{9}&\dfrac{5}{9}&\dfrac{2}{9}\end{array}"
)
PREVIEW_SOURCE = (
    "由相互独立事件概率乘法公式可知，\n\n"
    r"\[" + PROBABILITY_ARRAY + r"\]"
    "\n\n故随机变量 $X_1$ 的分布列为 $" + DISTRIBUTION_ARRAY + "$。"
)
UNNORMALIZED_SOURCE = PREVIEW_SOURCE.replace(r"\dfrac", r"\frac")


# Array row containers and text Ranges include font descent whitespace.
# Recover each leaf's baseline from its Range and actual loaded font, then
# compare Canvas ink ascent/descent, including nested fraction digits.
INSTALL_MEASUREMENTS = r"""
(() => {
    const context=document.createElement('canvas').getContext('2d');
    const ink = root => [...root.querySelectorAll('span')].filter(node =>
        !node.children.length && node.textContent.replace(/[\s\u200b]/g,'')
    ).map(node => {
        const range=document.createRange();range.selectNodeContents(node);
        const r=range.getBoundingClientRect();context.font=getComputedStyle(node).font;
        const metric=context.measureText(node.textContent);
        const baseline=r.top+metric.fontBoundingBoxAscent;
        return {text:node.textContent,left:r.left,right:r.right,
            top:baseline-metric.actualBoundingBoxAscent,
            bottom:baseline+metric.actualBoundingBoxDescent};
    }).filter(r => r.right>r.left && r.bottom>r.top);
    const bounds = leaves => leaves.length ? {
        left:Math.min(...leaves.map(r=>r.left)),right:Math.max(...leaves.map(r=>r.right)),
        top:Math.min(...leaves.map(r=>r.top)),bottom:Math.max(...leaves.map(r=>r.bottom)),
    } : null;
    window.__fractionSpacingMeasure = selector => {
        const host=document.querySelector(selector);
        if (!host) throw new Error('Missing fraction preview: '+selector);
        return {selector,width:host.clientWidth,errors:host.querySelectorAll('.katex-error').length,
            tables:[...host.querySelectorAll('.katex-html')].flatMap(html => {
                const table=html.querySelector('.mtable');
                if (!table) return [];
                const columns=[...table.children].filter(node=>/col-align-/.test(node.className));
                const rows=[];
                for (const column of columns) {
                    const vlist=column.querySelector(':scope > .vlist-t > .vlist-r > .vlist');
                    if (!vlist) continue;
                    [...vlist.children].forEach((row,index)=>{
                        rows[index]??=[];rows[index].push(...ink(row));
                    });
                }
                const rowBounds=rows.map(bounds).filter(Boolean);
                const lines=[...html.querySelectorAll('.hline')].map(node=>{
                    const r=node.getBoundingClientRect();
                    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};
                });
                const display=html.closest('.katex-display');
                return [{rows:rowBounds,gaps:rowBounds.slice(1).map((r,i)=>r.top-rowBounds[i].bottom),
                    lines,lineGaps:lines.map(line=>({
                        above:Math.min(...rowBounds.filter(r=>r.top<line.top).map(r=>line.top-r.bottom)),
                        below:Math.min(...rowBounds.filter(r=>r.bottom>line.bottom).map(r=>r.top-line.bottom)),
                    })),fontSize:parseFloat(getComputedStyle(html).fontSize),
                    source:html.closest('.katex').querySelector('annotation').textContent,
                    displayHeight:display?.getBoundingClientRect().height ?? null}];
            })};
    };
    return true;
})()
"""


def _metrics(browser, selector):
    return browser.evaluate("__fractionSpacingMeasure(" + json.dumps(selector) + ")")


def _assert_spacing(metrics, row_counts, *, ruled=False):
    assert metrics["width"] > 0, metrics
    assert not metrics["errors"], metrics
    assert [len(table["rows"]) for table in metrics["tables"]] == row_counts, metrics
    for table in metrics["tables"]:
        assert all(gap >= 1 for gap in table["gaps"]), table
        if ruled:
            assert table["lines"], table
            assert all(gap["above"] >= 1 and gap["below"] >= 1 for gap in table["lineGaps"]), table


def _reload_clean_ui(browser):
    browser.command("set", "viewport", "1600", "1100")
    browser.command("reload")
    browser.command("wait", "--fn", "typeof window.renderPaperCanvas === 'function'")
    browser.evaluate("selectWorkspace('paper','智能组卷'); true")
    browser.command("wait", "--fn", "!PaperStore.cartQuestionLoad.loading")


def _install_panel(browser, cases):
    browser.evaluate(INSTALL_MEASUREMENTS)
    browser.evaluate(r"""
    (() => {
        const panel=document.createElement('section');panel.id='fraction-spacing-fixtures';
        panel.className='preview-box bg-white text-slate-900';
        Object.assign(panel.style,{position:'fixed',zIndex:'99999',top:'16px',left:'16px',
            width:'min(1250px, calc(100vw - 32px))',maxHeight:'calc(100vh - 32px)',
            overflowY:'auto',padding:'16px',fontSize:'16px'});
        document.body.append(panel);
        for (const item of %s) {
            const host=document.createElement('div');host.id='fraction-spacing-'+item.name;
            panel.append(host);renderQuestionPreviewContent(host,'\\['+item.source+'\\]');
        }
        return true;
    })()
    """ % json.dumps(cases))
    browser.settle()


def test_display_fraction_rows_have_clear_ink_gaps_in_multiline_environments(browser, tmp_path):
    row = r"\dfrac{1+\frac{x}{y}}{2+\frac{a}{b}}"
    cases = [
        {"name": "probability", "source": PROBABILITY_ARRAY, "rows": 3},
        {"name": "compact", "source": PROBABILITY_ARRAY.replace(r"\dfrac", r"\frac"), "rows": 3},
        {"name": "nested", "source": r"\begin{array}{l}A="+row+r"\\B="+row+r"\end{array}", "rows": 2},
        {"name": "tagged", "source": r"\begin{align}A&="+row+r"\tag{first}\\B&="+row+r"\tag{second}\end{align}", "rows": 2},
        {"name": "explicit", "source": PROBABILITY_ARRAY.replace(r"\\", r"\\[1em]", 1), "rows": 3},
        {"name": "aligned", "source": r"\begin{aligned}A&="+row+r"\\B&="+row+r"\end{aligned}", "rows": 2},
        {"name": "gathered", "source": r"\begin{gathered}A="+row+r"\\B="+row+r"\end{gathered}", "rows": 2},
        {"name": "matrix", "source": r"\begin{pmatrix}"+row+r"&1\\2&"+row+r"\end{pmatrix}", "rows": 2},
        {"name": "cases", "source": r"f(x)=\begin{cases}"+row+r"&x>0\\"+row+r"&x\le0\end{cases}", "rows": 2},
    ]
    try:
        _install_panel(browser, cases)
        for width, font_size in [(1600, 16), (375, 16), (375, 32), (1600, 32)]:
            browser.command("set", "viewport", str(width), "1100")
            browser.evaluate(f"document.getElementById('fraction-spacing-fixtures').style.fontSize='{font_size}px'; true")
            browser.settle()
            results = {}
            for case in cases:
                result = _metrics(browser, "#fraction-spacing-"+case["name"])
                _assert_spacing(result, [case["rows"]])
                results[case["name"]] = result
            tags = browser.evaluate(r"""
            (() => {
                const html=document.querySelector('#fraction-spacing-tagged .katex-html');
                const context=document.createElement('canvas').getContext('2d');
                const baseline=node=>{
                    const range=document.createRange();range.selectNodeContents(node);
                    context.font=getComputedStyle(node).font;
                    return range.getBoundingClientRect().top+context.measureText(node.textContent).fontBoundingBoxAscent;
                };
                return ['first','second'].map((label,index)=>{
                    const tag=[...html.querySelectorAll('.tag span')].find(n=>n.textContent===label&&!n.children.length);
                    const variable=[...html.querySelectorAll('span')].find(n=>n.textContent===['A','B'][index]&&!n.children.length);
                    return {label,tagBaseline:baseline(tag),variableBaseline:baseline(variable)};
                });
            })()
            """)
            assert len(tags) == 2 and all(abs(tag["tagBaseline"]-tag["variableBaseline"]) <= 1 for tag in tags), tags
            normal_gap = results["probability"]["tables"][0]["gaps"][0]
            explicit_gap = results["explicit"]["tables"][0]["gaps"][0]
            # Array's optional row spacing is a minimum depth, not an
            # unconditional added 1em when the cell is already tall.
            assert explicit_gap >= normal_gap + font_size * 0.2, results
            assert r"\\[1em]" in results["explicit"]["tables"][0]["source"], results
            (tmp_path / f"fraction-row-bounds-{width}-{font_size}.json").write_text(json.dumps(results, indent=2))
        browser.command("screenshot", str(tmp_path / "fraction-rows-wide-large-font.png"))
    finally:
        browser.evaluate("document.getElementById('fraction-spacing-fixtures')?.remove(); delete window.__fractionSpacingMeasure; true")
        _reload_clean_ui(browser)


def test_distribution_rule_keeps_clearance_above_and_below_fraction_glyphs(browser, tmp_path):
    try:
        _install_panel(browser, [{"name": "distribution", "source": DISTRIBUTION_ARRAY}])
        for width, font_size in [(1600, 16), (375, 16), (375, 32)]:
            browser.command("set", "viewport", str(width), "1100")
            browser.evaluate(f"document.getElementById('fraction-spacing-fixtures').style.fontSize='{font_size}px'; true")
            browser.settle()
            result = _metrics(browser, "#fraction-spacing-distribution")
            _assert_spacing(result, [2], ruled=True)
            (tmp_path / f"fraction-rule-bounds-{width}-{font_size}.json").write_text(json.dumps(result, indent=2))
        browser.command("screenshot", str(tmp_path / "distribution-rule-narrow-large-font.png"))
        browser.command("set", "viewport", "1200", "900")
        browser.evaluate("""
        (() => {
            const panel=document.getElementById('fraction-spacing-fixtures');
            panel.innerHTML='';panel.style.fontSize='28px';
            const host=document.createElement('div');host.id='fraction-user-example';panel.append(host);
            renderQuestionPreviewContent(host,%s);return true;
        })()
        """ % json.dumps(PREVIEW_SOURCE))
        browser.settle()
        _assert_spacing(_metrics(browser, "#fraction-user-example"), [3, 2])
        browser.command("screenshot", str(tmp_path / "fraction-user-array-and-distribution.png"))
    finally:
        browser.evaluate("document.getElementById('fraction-spacing-fixtures')?.remove(); delete window.__fractionSpacingMeasure; true")
        _reload_clean_ui(browser)


def test_fraction_normalization_and_preview_entries_preserve_editor_and_database_state(browser, tmp_path):
    question_id = None
    original_cart = browser.evaluate("localStorage.getItem('mathbank_paper_cart')")
    try:
        browser.evaluate(INSTALL_MEASUREMENTS)
        question_id = browser.evaluate("""
        (async () => {
            selectWorkspace('bank','题库管理');const form=new FormData();
            Object.entries({content:%s,answer_markdown:%s,review:%s,
                question_type:'detailed_answer',difficulty:'medium'})
                .forEach(([key,value])=>form.set(key,value));
            const response=await fetch('/api/questions',{method:'POST',body:form});
            const result=await response.json();
            if (!response.ok || !result.question) throw new Error(JSON.stringify(result));
            await loadQuestions();return result.question.id;
        })()
        """ % (json.dumps(PREVIEW_SOURCE), json.dumps(UNNORMALIZED_SOURCE), json.dumps(PREVIEW_SOURCE)))
        original = browser.evaluate(f"fetch('/api/questions/{question_id}').then(r=>r.json())")
        browser.command("wait", "--fn", f"!!document.querySelector('#questionsList [data-id=\"{question_id}\"]')")
        browser.command("click", f'#questionsList [data-id="{question_id}"]')
        browser.command("wait", "--fn", f"EditorState.questionId==={question_id} && !questionDetailLoading && !isEditorModified()")
        for width in [1600, 375]:
            browser.command("set", "viewport", str(width), "1100")
            browser.evaluate("showBankDetail(); true")
            browser.settle()
            for selector in ["#paperContent", "#paperAnalysisContent", "#paperReviewContent"]:
                _assert_spacing(_metrics(browser, selector), [3, 2])
        assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == UNNORMALIZED_SOURCE
        assert not browser.evaluate("isEditorModified()")
        browser.command("set", "viewport", "1600", "1100")
        browser.command("click", "#editQuestionFromPreviewBtn")
        browser.command("click", '[data-editor-panel-target="answer"]')
        browser.command("scrollintoview", "#normalizeAnswerFractionsBtn")
        browser.settle()
        browser.command("click", "#normalizeAnswerFractionsBtn")
        browser.command("wait", "--fn", (
            "!document.getElementById('normalizeAnswerFractionsBtn').disabled && "
            "document.getElementById('editAnswerMarkdown').value==="+json.dumps(PREVIEW_SOURCE)
        ))
        for width in [1600, 375]:
            browser.command("set", "viewport", str(width), "1100")
            browser.settle()
            _assert_spacing(_metrics(browser, "#answerPreview"), [3, 2])
            assert browser.evaluate("isEditorModified()")
        assert browser.evaluate("document.getElementById('editContent').value") == PREVIEW_SOURCE
        assert browser.evaluate(f"fetch('/api/questions/{question_id}').then(r=>r.json())") == original
        browser.command("screenshot", str(tmp_path / "fraction-normalized-answer-narrow.png"))
        browser.command("fill", "#editAnswerMarkdown", UNNORMALIZED_SOURCE)
        browser.command("click", "#editorSection .question-editor-close")
        browser.evaluate("""
        (() => {
            selectWorkspace('import','导入中心');
            document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
            document.getElementById('importPlaceholder').classList.add('hidden');
            replaceParsedQuestions([{content:%s,answer_markdown:%s,
                question_type:'detailed_answer',difficulty:'medium',image_paths:[]}]);
            renderParsedQuestionsList(parsedQuestionsData);return true;
        })()
        """ % (json.dumps(PREVIEW_SOURCE), json.dumps(PREVIEW_SOURCE)))
        for width in [1600, 375]:
            browser.command("set", "viewport", str(width), "1100")
            browser.settle()
            for selector in ["#parsed-card-0 .card-content-preview", "#parsed-card-0 .card-answer-preview"]:
                _assert_spacing(_metrics(browser, selector), [3, 2])
        browser.evaluate(f"""
        (async () => {{
            const question=await fetch('/api/questions/{question_id}').then(r=>r.json());
            PaperStore.cart=[];PaperStore.questionsMap[question.id]=question;
            addToCart(question.id,5);selectWorkspace('paper','智能组卷');return true;
        }})()
        """)
        browser.command("set", "viewport", "1600", "1100")
        browser.command("wait", "--fn", "!!document.querySelector('#a4PaperPreviewSheet .mtable')")
        browser.settle()
        _assert_spacing(_metrics(browser, "#a4PaperPreviewSheet"), [3, 2])
        assert browser.evaluate(f"fetch('/api/questions/{question_id}').then(r=>r.json())") == original
        print(f"Fraction-spacing visual QA: {tmp_path}")
    finally:
        browser.evaluate("""
        (async () => {
            closeQuestionEditorModal();replaceParsedQuestions([]);PaperStore.cart=[];
            if (%s!==null) await fetch('/api/questions/'+%s,{method:'DELETE'});
            if (%s===null) localStorage.removeItem('mathbank_paper_cart');
            else localStorage.setItem('mathbank_paper_cart',%s);
            delete window.__fractionSpacingMeasure;return true;
        })()
        """ % (json.dumps(question_id), json.dumps(question_id), json.dumps(original_cart), json.dumps(original_cart)))
        _reload_clean_ui(browser)
