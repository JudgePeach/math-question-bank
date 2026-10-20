"""Real cart-click scroll regressions in a disposable source-only app.

Run with MATHBANK_TEST_BROWSER=1 python3 -m pytest --noconftest
tests/test_paper_cart_scroll_browser.py. The shared fixture owns its own
database, server, uploads directory, and Chromium session.
"""

import json

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


@pytest.fixture(scope="module")
def cart_scroll_questions(browser):
    # Persist only synthetic questions in the fixture's disposable database so
    # real server pagination, summary loading, and cart hydration remain active.
    return browser.evaluate(r"""
    (async () => {
        const questions = [];
        for (let index = 1; index <= 30; index++) {
            const type = ['single_choice', 'fill_in_blank', 'detailed_answer'][(index - 1) % 3];
            const fraction = String.raw`\dfrac{1+\dfrac{x^2+1}{x+1}}{\sqrt{1+\dfrac{x^4+1}{x^2+1}}}`;
            let content = `滚动回归第 ${index} 题：设函数 $f(x)=${fraction}$。` +
                '\n\n' + `已知 $a_${index}=${fraction}$，求满足条件的实数范围。` +
                '\n\n' + `请比较 $${fraction}$ 与 $${fraction}$ 的大小。`;
            if (type === 'single_choice') {
                content += String.raw`\begin{choices}\item $` + fraction +
                    String.raw`$\item $1$\item $2$\item $3$\end{choices}`;
            } else if (type === 'fill_in_blank') {
                content += String.raw`结果为\fillin。`;
            } else {
                content += '\n\n(1) 讨论定义域；(2) 说明结论的理由。';
            }
            const form = new FormData();
            Object.entries({content, question_type:type, difficulty:'normal',
                answer_markdown:'参考答案：$x=1$。'}).forEach(([key,value]) => form.set(key,value));
            const response = await fetch('/api/questions', {method:'POST', body:form});
            const payload = await response.json();
            if (!response.ok || !payload.question) throw new Error(JSON.stringify(payload));
            questions.push(payload.question);
        }
        return questions;
    })()
    """)


def _prepare(browser, questions, viewport):
    browser.command('set', 'viewport', *map(str, viewport))
    browser.evaluate("""
    (async () => {
        const questions = %s;
        PaperStore.cart = questions.slice(0, 15).map(q => ({id:q.id,score:5,solution_space:0}));
        PaperStore.questionsMap = Object.fromEntries(questions.map(q => [q.id,q]));
        PaperStore.filters.tab = 'all';
        Object.assign(PaperStore.meta, {paper_type:'exam', title:'组卷滚动回归',
            show_notice:false, show_secret:false, solution_space_default:'0'});
        Object.assign(PaperStore.streamPagination.all, {page:1});
        PaperStore.streamPagination.selected.page = 1;
        selectWorkspace('paper', '智能组卷');
        await renderPaperWorkspace();
        setPaperMobilePane('questions');
        return true;
    })()
    """ % json.dumps(questions))
    browser.command('wait', '--fn',
        '!PaperStore.cartQuestionLoad.loading && !PaperStore.streamPagination.all.loading'
        ' && document.querySelectorAll("#paperQuestionStream .paper-resource-card").length === 15')
    browser.settle()
    assert browser.evaluate("document.querySelectorAll('#paperQuestionStream .katex-error').length") == 0
    assert browser.evaluate("document.querySelectorAll('#paperQuestionStream .mfrac').length") >= 45


def _measure(browser, question_id):
    return browser.evaluate("""
    (() => {
        const stream = document.getElementById('paperQuestionStream');
        const sheet = document.getElementById('a4PaperPreviewSheet');
        const action = document.querySelector('[data-paper-question-id="%s"] .paper-cart-action');
        const rect = action.getBoundingClientRect();
        const bounds = stream.getBoundingClientRect();
        return {left:stream.scrollTop, right:sheet.scrollTop,
            outer:document.scrollingElement.scrollTop,
            cardTop:action.closest('.paper-resource-card').getBoundingClientRect().top - bounds.top,
            actionTop:rect.top - bounds.top,
            actionVisible:rect.top >= bounds.top && rect.bottom <= bounds.bottom,
            selected:action.classList.contains('is-selected'),
            page:PaperStore.streamPagination.all.page,
            mathErrors:stream.querySelectorAll('.katex-error').length};
    })()
    """ % question_id)


@pytest.mark.parametrize('viewport', [(1600, 1000), (375, 812)])
def test_cart_clicks_keep_math_question_reading_position(browser, cart_scroll_questions, viewport, tmp_path):
    _prepare(browser, cart_scroll_questions, viewport)
    targets = browser.evaluate("PaperStore.bankQuestions.slice(9, 11).map(q=>q.id)")
    evidence = []
    for question_id in targets:
        # Put a real lower-page button in the reading viewport before clicking;
        # subsequent remove/re-add clicks happen without another scroll command.
        browser.evaluate("""
        (() => {
            const stream=document.getElementById('paperQuestionStream');
            const action=document.querySelector('[data-paper-question-id="%s"] .paper-cart-action');
            stream.scrollTop += action.getBoundingClientRect().top - stream.getBoundingClientRect().top - 70;
            document.getElementById('a4PaperPreviewSheet').scrollTop = 160;
            return true;
        })()
        """ % question_id)
        browser.settle()
        for selected in (True, False, True):
            before = _measure(browser, question_id)
            assert before['left'] > 500 and before['actionVisible'], before
            browser.command('click', f'[data-paper-question-id="{question_id}"] .paper-cart-action')
            browser.command('wait', '--fn', f'isInCart({question_id}) === {str(selected).lower()}')
            browser.settle()
            after = _measure(browser, question_id)
            evidence.append({'question':question_id, 'selected':selected, 'before':before, 'after':after})
            (tmp_path / f'cart-scroll-{viewport[0]}.json').write_text(json.dumps(evidence, indent=2))
            assert abs(after['left'] - before['left']) <= 2, evidence[-1]
            # The added score input may wrap mobile controls onto another row;
            # the question card itself must retain its viewport anchor.
            assert after['actionVisible'] and abs(after['cardTop'] - before['cardTop']) <= 2, evidence[-1]
            assert after['right'] == before['right'] and after['outer'] == before['outer'], evidence[-1]
            assert after['selected'] == selected and after['page'] == 1 and not after['mathErrors'], evidence[-1]


@pytest.mark.parametrize('viewport', [(1600, 1000), (375, 812)])
def test_page_and_tab_navigation_still_reset_only_question_list(browser, cart_scroll_questions, viewport):
    _prepare(browser, cart_scroll_questions, viewport)
    browser.evaluate("document.getElementById('a4PaperPreviewSheet').scrollTop=160; true")
    right = browser.evaluate("document.getElementById('a4PaperPreviewSheet').scrollTop")
    outer = browser.evaluate("document.scrollingElement.scrollTop")
    next_page = '#paperQuestionStream nav button:last-child[onclick="window.changePaperStreamPage(\'all\', 2)"]'
    browser.command('scrollintoview', next_page)
    browser.settle()
    assert browser.evaluate("document.getElementById('paperQuestionStream').scrollTop") > 500
    browser.command('click', next_page)
    browser.command('wait', '--fn', '!PaperStore.streamPagination.all.loading && PaperStore.streamPagination.all.page === 2')
    browser.settle()
    assert browser.evaluate("document.getElementById('paperQuestionStream').scrollTop") == 0
    assert browser.evaluate("document.getElementById('a4PaperPreviewSheet').scrollTop") == right
    assert browser.evaluate("document.scrollingElement.scrollTop") == outer

    # Focus the visible tab first, then scroll down using real keyboard input.
    # Enter activates that same focused tab while its header is off screen.
    tab = '#paperQuestionStream button[onclick="switchPaperStreamTab(\'selected\')"]'
    browser.command('focus', tab)
    browser.evaluate("document.getElementById('paperQuestionStream').scrollTop=1200; true")
    assert browser.evaluate("document.getElementById('paperQuestionStream').scrollTop") > 500
    browser.command('press', 'Enter')
    browser.command('wait', '--fn', "PaperStore.filters.tab === 'selected' && !PaperStore.cartQuestionLoad.loading")
    browser.settle()
    assert browser.evaluate("document.getElementById('paperQuestionStream').scrollTop") == 0
    assert browser.evaluate("document.getElementById('a4PaperPreviewSheet').scrollTop") == right
    assert browser.evaluate("document.scrollingElement.scrollTop") == outer
