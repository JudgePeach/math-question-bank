"""Opt-in layout checks against the shipped importer and renderer, without AI."""

import json
import os
from pathlib import Path

import pytest

from test_preview_browser import browser  # source-only temporary app, no user data
from test_math_preview_regressions import run_preview_script


def test_paragraph_separators_keep_hard_breaks_math_and_block_anchors():
    run_preview_script(r'''
const paragraphs=html=>(html.match(/mb-preview-paragraph-break/g)||[]).length;
assert.equal(preprocessFormulaForKaTeX('甲\n乙'),'甲 乙');
assert.equal(paragraphs(preprocessFormulaForKaTeX('甲\n\n乙\n\n\n丙')),2);
assert.equal(preprocessFormulaForKaTeX(String.raw`甲\\乙`),'甲<br>乙');
assert.equal(preprocessFormulaForKaTeX(String.raw`甲\\\\乙`),'甲<br><br>乙');
const subquestions=preprocessFormulaForKaTeX('解题过程\r\n\r\n(1) 求 $x$。\r\n\r\n(2) 说明理由。');
assert.equal(paragraphs(subquestions),2);
assert.equal((subquestions.match(/<br>/g)||[]).length,0);
assert.deepEqual(parsedFormulas(subquestions),['x']);
const mixedBreak=preprocessFormulaForKaTeX(String.raw`甲\\`+'\n\n乙');
assert.equal(paragraphs(mixedBreak),1); assert.equal(mixedBreak.includes('<br>'),false);
for(const math of ['$$x=1$$',String.raw`\[x=1\]`,String.raw`\begin{equation}x=1\end{equation}`]) {
  const html=preprocessFormulaForKaTeX('前段\n\n'+math+'\n\n后段');
  assert.equal(paragraphs(html),0,html);
  assert.deepEqual(parsedFormulas(html),['x=1']);
}
for(const block of [
  String.raw`\begin{choices}\item $1$\item $2$\end{choices}`,
  String.raw`\begin{tabular}{cc}甲 & $x_1$\\乙 & $x_2$\end{tabular}`,
  '![](/static/uploads/paragraph.png)'
]) {
  const html=preprocessFormulaForKaTeX('前段\n\n'+block+'\n\n后段');
  assert.equal(paragraphs(html),0,html);
  assert.ok(html.indexOf('前段')<html.indexOf('<div'));
  assert.ok(html.lastIndexOf('</div>')<html.indexOf('后段'));
  parsedFormulas(html);
}
const multiline='$x+\n\ny=1$';
assert.equal(preprocessFormulaForKaTeX(multiline),multiline);
assert.equal(paragraphs(preprocessFormulaForKaTeX(multiline)),0);
''')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in real paragraph layout")
def test_word_answer_paragraph_spacing(browser, tmp_path):
    capture = os.environ.get("MATHBANK_TEST_WORD_SPACING_TASK")
    if capture:
        question = json.loads(Path(capture).read_text())["data"][0]
    else:
        question = {"content": "根据条件选择正确结论。", "question_type": "single_choice",
                    "answer_markdown": "C\n\n【星空解析】∵集合 $A=\\{1,2\\}$，$B=\\{2,3\\}$。\n\n∴交集为 $\\{2\\}$。"}
    browser.evaluate("""
    (() => {
        selectWorkspace('import', '导入中心');
        window.__spacingRequests=[];
        window.__spacingOriginalFetch=window.fetch;
        window.fetch=(input,options={})=>{
            const url=typeof input==='string' ? input : input.url;
            if (url.includes('/api/ai/') || (String(options.method || 'GET').toUpperCase()!=='GET' && /\\/api\\/questions(?:\\/|$)/.test(url))) {
                window.__spacingRequests.push(url);
                return Promise.reject(Error('Rendering tests must not call models or save questions'));
            }
            return window.__spacingOriginalFetch(input,options);
        };
        replaceParsedQuestions([%s]);
        renderParsedQuestionsList(parsedQuestionsData);
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        return true;
    })()
    """ % json.dumps(question))
    try:
        browser.command('set', 'viewport', '1600', '1100')
        browser.settle()
        browser.command('scrollintoview', '#parsed-card-0 .card-answer-preview')
        metrics = browser.evaluate(r"""
        (() => {
            const host=document.querySelector('#parsed-card-0 .card-answer-preview');
            function box(needle) {
                const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);
                let node;
                while ((node=walker.nextNode())) {
                    if (node.parentElement.closest('.katex')) continue;
                    const start=node.textContent.indexOf(needle);
                    if (start<0) continue;
                    const range=document.createRange();range.setStart(node,start);range.setEnd(node,start+needle.length);
                    const rect=range.getBoundingClientRect();return {top:rect.top,bottom:rect.bottom,height:rect.height};
                }
                throw Error('Missing visible paragraph text: '+needle);
            }
            const first=box('C'), second=box('【星空解析】');
            return {gap:second.top-first.bottom,lineHeight:parseFloat(getComputedStyle(host).lineHeight),
                breaks:host.querySelectorAll('br').length,paragraphs:host.querySelectorAll('p').length,
                displays:host.querySelectorAll('.katex-display').length,height:host.getBoundingClientRect().height,
                prepared:parseMarkdownWithMath(document.querySelector('#parsed-card-0 .card-answer-textarea').value),
                errorCount:host.querySelectorAll('.katex-error').length};
        })()
        """)
        browser.evaluate("(() => {const host=document.querySelector('#parsed-card-0 .card-answer-preview');host.parentElement.scrollTop += host.getBoundingClientRect().top-host.parentElement.getBoundingClientRect().top-12;return true;})()")
        browser.command('screenshot', str(tmp_path / 'word-answer-spacing.png'))
        (tmp_path / 'word-answer-spacing.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        print('Word answer spacing: ' + json.dumps(metrics, ensure_ascii=False))
        assert metrics['errorCount'] == 0
        assert 0 < metrics['gap'] < metrics['lineHeight'] * 0.8, metrics
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-answer-textarea').value") == question['answer_markdown']
        assert browser.evaluate('window.__spacingRequests') == []
    finally:
        browser.evaluate('window.fetch=window.__spacingOriginalFetch; true')
    print(f'Word answer visual QA: {tmp_path}')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in real paragraph layout")
def test_paragraph_boundaries_preserve_display_tables_choices_and_images(browser, tmp_path):
    from PIL import Image

    asset = browser.root / 'static/uploads/paragraph-fixture.png'
    asset.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (80, 40), '#64748b').save(asset)
    result = browser.evaluate(r"""
    (() => {
        selectWorkspace('import','导入中心');
        const cases={
            paragraphs:'前段正文。\n\n(1) 求 $x$。\n\n(2) 证明 $x>0$。',
            display:'前段正文。\n\n$$x^2+1=2$$\n\n后段正文。',
            table:String.raw`前段正文。\begin{tabular}{cc}甲 & $x_1$\\乙 & ![](/static/uploads/paragraph-fixture.png)\end{tabular}后段正文。`,
            choices:String.raw`前段正文。\begin{choices}\item $x=1$\item ![](/static/uploads/paragraph-fixture.png)\end{choices}后段正文。`,
            image:'前段正文。\n\n![](/static/uploads/paragraph-fixture.png)\n\n后段正文。',
            hard_break:String.raw`前段正文。\\后段正文。`
        };
        const wrapper=document.getElementById('parsedQuestionsWrapper');
        wrapper.classList.remove('hidden'); document.getElementById('importPlaceholder').classList.add('hidden');
        const old=document.getElementById('paragraphSpacingFixtures');if(old)old.remove();
        const panel=document.createElement('section');panel.id='paragraphSpacingFixtures';
        panel.className='space-y-4 p-4 bg-white text-xs font-serif leading-relaxed';
        wrapper.insertBefore(panel,wrapper.firstChild);
        const result={};
        for(const [name,text] of Object.entries(cases)) {
            const host=document.createElement('div');host.id='spacing-'+name;panel.appendChild(host);
            renderQuestionPreviewContent(host,text);
            const img=host.querySelector('img');
            const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);let node,after=null;
            while((node=walker.nextNode()))if(node.textContent.includes('后段正文'))after=node;
            result[name]={separators:host.querySelectorAll('.mb-preview-paragraph-break').length,
                breaks:host.querySelectorAll('br').length,errors:host.querySelectorAll('.katex-error').length,
                displays:host.querySelectorAll('.katex-display').length,rows:host.querySelectorAll('tr').length,
                cells:host.querySelectorAll('td').length,labels:[...host.querySelectorAll('.choices-label')].map(n=>n.textContent),
                imageInCell:!!img?.closest('td'),imageInOption:!!img?.closest('.choices-item'),
                imageBeforeFollowingText:!!(img&&after&&(img.compareDocumentPosition(after)&Node.DOCUMENT_POSITION_FOLLOWING))};
        }
        return result;
    })()
    """)
    browser.command('wait', '--fn', "[...document.querySelectorAll('#paragraphSpacingFixtures img')].every(img=>img.complete && img.naturalWidth===80)")
    browser.settle()
    assert all(item['errors'] == 0 for item in result.values()), result
    assert result['paragraphs']['separators'] == 2 and result['paragraphs']['breaks'] == 0
    assert result['display']['displays'] == 1 and result['display']['separators'] == 0
    assert result['table']['rows'] == 2 and result['table']['cells'] == 4 and result['table']['imageInCell']
    assert result['choices']['labels'] == ['A.', 'B.'] and result['choices']['imageInOption']
    assert result['image']['imageBeforeFollowingText'] and result['image']['separators'] == 0
    assert result['hard_break']['breaks'] == 1 and result['hard_break']['separators'] == 0
    browser.command('scrollintoview', '#paragraphSpacingFixtures')
    browser.command('screenshot', str(tmp_path / 'paragraph-block-boundaries.png'))
    (tmp_path / 'paragraph-block-boundaries.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
