"""Complete choice contents in the actual sidebar, editor and paper preview."""

import json

from test_preview_browser import browser, pytestmark  # noqa: F401


def test_complete_choice_cards_and_formula_ink_bounds(browser, tmp_path):
    browser.command('set', 'viewport', '1600', '1100')
    question_id = browser.evaluate(r'''
    (async () => {
        const canvas = document.createElement('canvas');
        canvas.width = 240; canvas.height = 160;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'white'; ctx.fillRect(0,0,240,160);
        ctx.strokeStyle = 'black'; ctx.lineWidth = 3; ctx.strokeRect(4,4,232,152);
        ctx.font = '24px sans-serif'; ctx.fillStyle = 'black'; ctx.fillText('OPTION IMAGE', 18,85);
        const image = new FormData();
        image.set('file', await new Promise(resolve => canvas.toBlob(resolve, 'image/png')), 'choice.png');
        const uploaded = await fetch('/api/upload', {method:'POST',body:image}).then(r=>r.json());
        uploaded.url = uploaded.file_path;
        if (!uploaded.url) throw new Error(JSON.stringify(uploaded));
        const source = String.raw`选项完整显示回归。长题干应完整显示，不吞掉最后的选项。\begin{choices}
\item $\dfrac{1+\dfrac{x^2}{\sqrt{1+y^2}}}{\sqrt{\dfrac{a}{b}+c_{n+1}^{2}}}$
\item 较长文字：` + '文字包含高字母 ÅÉgjpq 和完整边缘；'.repeat(12) + String.raw`\\第二行：$\sqrt{x^{\dfrac{a}{b}}+y_{n+1}}$，结束。
\item ![](` + uploaded.url + String.raw`)
\item $\dfrac{` + 'x^2+'.repeat(60) + String.raw`1}{y_{n+1}}$\end{choices}`;
        const form = new FormData();
        form.set('content', source); form.set('question_type','single_choice'); form.set('difficulty','medium');
        form.set('answer_markdown', source); form.set('review', source);
        form.set('image_paths', JSON.stringify([uploaded.url]));
        const response = await fetch('/api/questions',{method:'POST',body:form});
        const result = await response.json();
        if(!response.ok || !result.question) throw new Error(JSON.stringify(result));
        window.__completeChoiceFixture = result.question;
        selectWorkspace('bank'); loadQuestions();
        return result.question.id;
    })()
    ''')
    selector = f'#questionsList [data-id="{question_id}"]'
    browser.command('wait', '--fn', '!!document.querySelector(' + json.dumps(selector) + ')')
    browser.command('click', f'#questionsList [data-id="{question_id}"]')
    browser.settle()
    browser.command('wait', '--fn', "document.querySelectorAll('#paperContent .choices-content').length===4")

    measurement = r'''
    (() => {
        const result = [];
        for (const selector of window.__choiceHosts) {
            const host = document.querySelector(selector);
            if (!host) throw new Error('missing host '+selector);
            const hostBounds = host.getBoundingClientRect();
            const items = [...host.querySelectorAll('.choices-content')];
            result.push({selector, count:items.length,
                clamp:getComputedStyle(host).webkitLineClamp,
                height:host.clientHeight, scrollHeight:host.scrollHeight,
                overflowY:getComputedStyle(host).overflowY,
                items:items.map(content=>{
                    const r = content.getBoundingClientRect();
                    const formulae=[...content.querySelectorAll('.katex')].map(math=>{
                        const mr=math.getBoundingClientRect();
                        const leaves=[...math.querySelectorAll('.katex-html span')].filter(e=>
                            !e.children.length && e.textContent.trim() && !e.classList.contains('strut')
                        ).map(e=>e.getBoundingClientRect()).filter(b=>b.width && b.height);
                        return {scroll:math.classList.contains('choices-math-scroll'),
                            top:Math.min(...leaves.map(b=>b.top))-mr.top,
                            bottom:mr.bottom-Math.max(...leaves.map(b=>b.bottom)),
                        overflowY:getComputedStyle(math).overflowY,
                        scrollHeight:math.scrollHeight, height:math.clientHeight,
                            width:mr.width, available:content.clientWidth,
                            scrollWidth:math.scrollWidth};
                    });
                        const label = content.parentElement.querySelector('.choices-label').getBoundingClientRect();
                        return {top:r.top-hostBounds.top,bottom:hostBounds.bottom-r.bottom,
                            labelOffset:label.top-r.top,
                        overflowY:getComputedStyle(content).overflowY,
                        height:content.clientHeight,scrollHeight:content.scrollHeight,
                        width:content.clientWidth, scrollWidth:content.scrollWidth,
                        formulae,images:[...content.querySelectorAll('img')].map(i=>({
                            complete:i.complete,natural:i.naturalWidth,width:i.getBoundingClientRect().width,
                            height:i.getBoundingClientRect().height
                        }))};
                })});
        }
        return result;
    })()
    '''
    browser.evaluate('window.__choiceHosts = '+json.dumps([
        f'#questionsList [data-id="{question_id}"] .card-formula-render', '#paperContent',
        '#paperAnalysisContent', '#paperReviewContent'
    ]))
    for width in [1600, 920, 375]:
        browser.command('set','viewport',str(width),'1100')
        browser.settle()
        browser.command('wait','--fn',"[...document.querySelectorAll('#questionsList .choices-content img')].every(i=>i.complete&&i.naturalWidth)")
        results = []
        if width <= 960:
            # Narrow layouts show list and detail as separate real user views.
            browser.evaluate('closeBankDetail(); window.__choiceHosts=[' + json.dumps(f'{selector} .card-formula-render') + ']; true')
            browser.settle()
            results.extend(browser.evaluate(measurement))
            browser.command('screenshot',str(tmp_path/f'complete-choices-list-{width}.png'))
            browser.evaluate('showBankDetail(); window.__choiceHosts=["#paperContent","#paperAnalysisContent","#paperReviewContent"]; true')
            browser.settle()
            results.extend(browser.evaluate(measurement))
        else:
            results = browser.evaluate(measurement)
        (tmp_path/f'choice-bounds-{width}.json').write_text(json.dumps(results,indent=2))
        browser.command('screenshot',str(tmp_path/f'complete-choices-{width}.png'))
        for host in results:
            assert host['count'] == 4, host
            assert host['overflowY'] != 'hidden', host
            assert host['height'] >= host['scrollHeight']-1, host
            assert -3 <= host['items'][1]['labelOffset'] <= 8, host['items'][1]
            for option in host['items']:
                assert option['top'] >= -1 and option['bottom'] >= -1, host
                assert option['overflowY'] == 'visible', option
                assert option['height'] >= option['scrollHeight']-1, option
                assert option['scrollWidth'] <= option['width']+1, option
                for formula in option['formulae']:
                    # KaTeX struts can enlarge scrollHeight without visible ink.
                    # Check glyph bounds against the clip with antialiasing room.
                    assert formula['top'] >= 1 and formula['bottom'] >= 1, formula
                    if formula['scroll']:
                        assert formula['width'] <= formula['available']+1, formula
                        assert formula['scrollWidth'] > formula['width'], formula
                for image in option['images']:
                    assert image['complete'] and image['natural'] and image['height']>0, image
                    assert image['width'] <= option['width']+1, image

    # Exercise the scroll rather than only accepting an overflow CSS class:
    # the centered denominator and the rightmost numerator must be reachable.
    middle = browser.evaluate('''(() => {
        const math=document.querySelector('#paperContent .choices-math-scroll');
        math.scrollLeft=(math.scrollWidth-math.clientWidth)/2;
        math.scrollIntoView({block:'center'});
        return {position:math.scrollLeft,max:math.scrollWidth-math.clientWidth};
    })()''')
    assert 0 < middle['position'] < middle['max'], middle
    browser.command('screenshot',str(tmp_path/'long-formula-middle.png'))
    end = browser.evaluate('''(() => {
        const math=document.querySelector('#paperContent .choices-math-scroll');
        math.scrollLeft=math.scrollWidth;
        return {position:math.scrollLeft,max:math.scrollWidth-math.clientWidth};
    })()''')
    assert abs(end['position']-end['max'])<=1, end
    browser.command('screenshot',str(tmp_path/'long-formula-end.png'))

    browser.command('set','viewport','1600','1100')
    browser.evaluate('selectWorkspace("paper"); MathBankBrowserChecks.seed(1,0,true); Object.assign(PaperStore.questionsMap[1],window.__completeChoiceFixture); renderPaperCanvas(); true')
    browser.settle()
    browser.evaluate('window.__choiceHosts=["#a4PaperPreviewSheet .paper-choice-options-row"]')
    results=browser.evaluate(measurement)
    assert results[0]['count']==4,results
    for option in results[0]['items']:
        assert option['height']>=option['scrollHeight']-1,option
    browser.command('screenshot',str(tmp_path/'complete-choices-paper.png'))
