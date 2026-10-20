"""Actual browser page breaks, manual space entry and stable control anchors."""
import pytest
from test_preview_browser import browser, pytestmark  # noqa: F401


def seed(browser, space):
    browser.evaluate("""MathBankBrowserChecks.seed(2,0,true);
        PaperStore.meta.show_notice=false;
        for(let i=1;i<=2;i++)PaperStore.questionsMap[i].question_type='detailed_answer';
        PaperStore.cart[0].solution_space=%s;
        renderPaperCanvas();true""" % str(space))
    browser.settle()


def test_thirty_cm_space_flows_across_fixed_a4_pages_and_rebalances_without_duplicates(browser):
    seed(browser, 30)
    for _ in range(3):
        result=browser.evaluate("""({pages:MathBankBrowserChecks.measure(),
            height:[...document.querySelectorAll('[data-solution-space-zone-qid="1"],[data-solution-space-continuation-qid="1"]')]
                .reduce((n,x)=>{const page=x.closest('.a4-paper-sheet');
                    const scale=page.getBoundingClientRect().width/page.offsetWidth;
                    return n+x.getBoundingClientRect().height/scale;},0),
            continuations:document.querySelectorAll('.paper-solution-continuation').length})""")
        assert len(result['pages'])==2
        assert not any(p['expanded'] for p in result['pages'])
        assert [q['id'] for p in result['pages'] for q in p['questions']]==[1,2]
        assert all(q['overflow']<=2 for p in result['pages'] for q in p['questions'])
        assert result['height']==pytest.approx(30*96/2.54,abs=1)
        assert result['continuations']==1
        browser.evaluate("rebalanceA4PaperPages(document.getElementById('a4PaperPreviewSheet'),PaperStore.meta,2,10);true")
        browser.settle()


def test_manual_input_accepts_thirty_and_preserves_control_viewport_anchor(browser):
    seed(browser, 7)
    selector='[data-solution-space-input="1"]'
    browser.command('scrollintoview',selector)
    browser.settle()
    before=browser.evaluate('document.querySelector(\'[data-solution-space-controls-qid="1"]\').getBoundingClientRect().top')
    browser.command('fill',selector,'30')
    browser.command('press','Enter')
    browser.settle()
    assert browser.evaluate("PaperStore.cart[0].solution_space")=='30.0'
    after=browser.evaluate('document.querySelector(\'[data-solution-space-controls-qid="1"]\').getBoundingClientRect().top')
    assert abs(after-before)<=2
    browser.evaluate("updateQuestionSolutionSpace(1,1);true")
    browser.settle()
    assert browser.evaluate("PaperStore.cart[0].solution_space")=='30.0'
    browser.evaluate("setQuestionSolutionSpace(1,0);true")
    browser.settle()
    assert browser.evaluate("document.querySelector('[data-solution-space-zone-qid=\"1\"]').getBoundingClientRect().height")==0
    assert browser.evaluate("document.querySelectorAll('.paper-solution-continuation').length")==0


def test_switching_to_exam_19_restores_compact_defaults(browser):
    seed(browser,30)
    browser.evaluate("updatePaperMeta('paper_type','exam_19');true")
    browser.settle()
    assert browser.evaluate("PaperStore.cart.map(q=>q.solution_space)")==['0.0','0.0']
    assert browser.evaluate("document.querySelectorAll('.paper-solution-continuation').length")==0
