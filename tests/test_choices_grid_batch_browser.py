"""Actual option geometry, using the existing isolated offline app fixture."""

import os
import pytest

from test_preview_browser import browser

pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1",
    reason="opt-in actual browser regression",
)


def test_actual_choices_width_and_scaled_formula_scroll(browser, tmp_path):
    browser.evaluate(r'''
    (() => {
        document.getElementById('choicesBatchWidthFixture')?.remove();
        const host=document.createElement('section');
        host.id='choicesBatchWidthFixture';
        host.style.cssText='position:fixed;left:0;top:0;width:740px;font:16px Arial;background:white;z-index:99999';
        document.body.appendChild(host);
        renderQuestionPreviewContent(host, String.raw`\begin{choices}\item abcdefghijkl\item abcdefghijkl\item abcdefghijkl\item abcdefghijkl\end{choices}`);
        return true;
    })()
    ''')
    try:
        for width, expected in [(740, '4'), (360, '2'), (180, '1'), (740, '4')]:
            browser.evaluate(f"document.getElementById('choicesBatchWidthFixture').style.width='{width}px'")
            browser.settle()
            result=browser.evaluate(r'''
            (() => {
                const host=document.getElementById('choicesBatchWidthFixture');
                const grid=host.querySelector('.choices-grid');
                return {columns:grid.dataset.choiceColumns, items:[...grid.querySelectorAll('.choices-content')].map(content=>({
                    overflow:content.scrollWidth>content.clientWidth+1,
                    fontSize:getComputedStyle(content).fontSize,
                }))};
            })()
            ''')
            assert result['columns']==expected,result
            assert not any(item['overflow'] for item in result['items']),result
            assert all(item['fontSize']=='16px' for item in result['items']),result
            if width in (740,180):
                browser.command('screenshot',str(tmp_path / f'choices-{width}px.png'))
        result=browser.evaluate(r'''
        (() => {
            const host=document.getElementById('choicesBatchWidthFixture');
            host.style.width='360px';host.style.zoom='0.5';
            renderQuestionPreviewContent(host,String.raw`\begin{choices}[columns=1]\item $\frac{11111111111111111111111111111111111111111111111111111111111111}{22222222222222222222222222222222222222222222222222222222222222}$\item $1$\end{choices}`);
            adaptChoicesGridLayout(host);
            const grid=host.querySelector('.choices-grid');
            const math=grid.querySelector('.choices-math-scroll');
            return {columns:grid.dataset.choiceColumns, items:grid.children.length,
                scroll:Boolean(math&&math.scrollWidth>math.clientWidth),
                font:getComputedStyle(grid.querySelector('.choices-content')).fontSize};
        })()
        ''')
        assert result=={'columns':'1','items':2,'scroll':True,'font':'16px'},result
        browser.command('screenshot',str(tmp_path / 'choices-scaled-formula-scroll.png'))
    finally:
        browser.evaluate("document.getElementById('choicesBatchWidthFixture')?.remove(); true")
