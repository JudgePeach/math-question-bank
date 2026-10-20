"""Opt-in classification recovery in an isolated source-only app and Chromium."""

import json
import os
from pathlib import Path

import pytest

from test_preview_browser import browser  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / '.system_generated/pdf-classification-update-20261018/browser'
pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1',
                                reason='opt-in isolated actual-browser regression')


def _visible_card(browser, index):
    browser.command('scrollintoview', f'#parsed-card-{index}')
    browser.settle()


def _click_visible(browser, selector):
    browser.command('scrollintoview', selector)
    browser.settle()
    browser.command('wait', '--fn', "(()=>{const el=document.querySelector(" + json.dumps(selector) +
                    "),r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);"
                    "return el===hit||el.contains(hit)})()")
    browser.command('click', selector)


@pytest.mark.parametrize('width,height', [(1600, 1100), (375, 812)])
def test_document_classification_recovery_selection_save_and_restore(browser, width, height):
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('network', 'route', '**/api/ai/**', '--abort')
    browser.evaluate('window.__classificationBrowserViewport=' + str(width))
    browser.evaluate(r"""
    (async()=>{
        if(window.__classificationBrowserOriginalFetch)window.fetch=window.__classificationBrowserOriginalFetch;
        await loadCategories();
        selectWorkspace('import','导入中心');stopCurrentDocumentPoll();
        window.currentPdfTaskId=null;window.currentDocumentClientRequestId=null;
        window.MathBankDocumentImportSession.clear();
        window.__classificationBrowserOriginalFetch=window.fetch;
        window.__classificationBrowserBlocked=[];window.__classificationBrowserRequests=[];
        window.fetch=(input,options={})=>{
            const url=String(input?.url||input);
            window.__classificationBrowserRequests.push({url,method:options.method||'GET'});
            if(url.includes('/api/ai/')||url.includes('/api/upload/')){
                window.__classificationBrowserBlocked.push(url);return Promise.reject(Error('No model or upload in classification replay'));
            }
            return window.__classificationBrowserOriginalFetch(input,options);
        };
        const normalized={required:false,reason_codes:[],message:'已恢复目录中唯一对应的章节名称。',
            normalized_fields:['category_chapter'],original:{category_compulsory:'必修二',category_chapter:'统计',
                category_knowledge:'9.2 用样本估计总体'}};
        const pending={required:true,reason_codes:['chapter_not_found'],message:'章节无法匹配当前教材目录，请选择所属章节。',
            original:{category_compulsory:'必修二',category_chapter:'无法归属的统计章节',category_knowledge:''}};
        const subsection={required:true,reason_codes:['knowledge_not_found'],message:'小节无法匹配当前章节，请选择小节或按整章保留。',
            original:{category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'模型自造的小节'}};
        const common={question_type:'single_choice',difficulty:'normal',source:'分类浏览器合成回归',
            answer_markdown:'',category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'',image_paths:[]};
        const questions=[
            {...common,content:'一组样本数据的平均数为 $75$，样本容量为 $20$，求样本数据的总和。',
                category_knowledge:'9.2 用样本估计总体',classification_review:normalized},
            {...common,content:window.__classificationBrowserViewport===375
                    ?'抽样所得的五个数据为 $2,3,5,7,8$，求这一组样本数据的中位数。'
                    :'某校从 $1200$ 名学生中随机抽取 $60$ 人调查，求每名学生被抽中的概率。',
                category_chapter:'',classification_review:pending},
            {...common,content:'某班同学每日阅读时间的数据已经从小到大排列，说明如何确定样本中位数。',
                classification_review:subsection,source_review:{required:true,blocking:false,disposition:'advisory',
                    reasons:['保留独立的来源比较说明。'],source_excerpt:'原文题干及公式保持不变。'}}
        ];
        if(!categoryTree['必修二']?.['9. 统计']?.includes('9.2 用样本估计总体'))throw Error('Fixture expects the real A curriculum directory');
        replaceParsedQuestions(questions);renderParsedQuestionsList(parsedQuestionsData);
        renderSourceIntegrityReport({source_review_count:1,unmatched_source:[]});
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        window.__classificationBrowserOriginalReviews=JSON.stringify(questions.map(q=>q.classification_review.original));
        window.__classificationBrowserSourceReview=JSON.stringify(questions[2].source_review);
        return true;
    })()
    """)
    browser.settle()
    browser.command('snapshot', '-i', '-s', '#parsedQuestionsWrapper')
    initial = browser.evaluate(r"""parsedQuestionsData.map((q,i)=>{
        const card=document.getElementById(`parsed-card-${i}`);
        return {compulsory:card.querySelector('.card-compulsory').value,
            chapter:card.querySelector('.card-chapter').value,knowledge:card.querySelector('.card-knowledge').value,
            selected:card.querySelector('.card-select-checkbox').checked,required:q.classification_review.required};
    })""")
    assert initial[0] == {'compulsory': '必修二', 'chapter': '9. 统计', 'knowledge': '9.2 用样本估计总体',
                          'selected': True, 'required': False}
    assert initial[1] == {'compulsory': '必修二', 'chapter': '', 'knowledge': '', 'selected': False, 'required': True}
    assert initial[2]['selected'] is False and initial[2]['required'] is True
    browser.evaluate('toggleSelectAllParsed(false);true')
    browser.command('check', '#selectAllCheckbox')
    assert browser.evaluate('getCheckedUnsavedIndices()') == [0], 'Select-all must skip unresolved classifications'
    assert not browser.evaluate('validateParsedQuestionBeforeImport(1,{notify:false,focus:false})')
    _visible_card(browser, 1)
    browser.command('screenshot', str(SCREENSHOTS / f'pending-classification-{width}.png'))
    assert browser.evaluate(r"""(()=>{
        const card=document.getElementById('parsed-card-1'),r=card.getBoundingClientRect();
        return r.left>=-1&&r.right<=innerWidth+1&&[...card.querySelectorAll('select')]
            .every(input=>input.getBoundingClientRect().right<=r.right+1);
    })()"""), 'Classification controls must fit the card at both viewport sizes'

    # A real select event confirms an exact path; the real save writes only to
    # the disposable fixture's database, never the user's database or an AI API.
    browser.command('select', '#parsed-card-1 .card-chapter', '9. 统计')
    assert browser.evaluate("!parsedQuestionsData[1].classification_review.required&&parsedQuestionsData[1].classification_review.confirmed_by==='user'")
    assert browser.evaluate('validateParsedQuestionBeforeImport(1,{notify:false,focus:false})')
    _click_visible(browser, '#parsed-card-1 .card-save-btn')
    try:
        browser.command('wait', '--fn', 'parsedQuestionsData[1].saved===true')
    except (AssertionError, TimeoutError):
        browser.command('screenshot', str(SCREENSHOTS / f'save-failure-{width}.png'))
        raise AssertionError(browser.evaluate("JSON.stringify({question:parsedQuestionsData[1],requests:window.__classificationBrowserRequests,text:document.body.innerText.slice(-10000)})"))
    assert browser.evaluate("document.querySelector('#parsed-card-1 .card-status-badge').textContent") == '已导入'

    _visible_card(browser, 2)
    browser.command('snapshot', '-i', '-s', '#parsed-card-2')
    _click_visible(browser, '#parsed-card-2 .card-classification-confirm')
    assert browser.evaluate("!parsedQuestionsData[2].classification_review.required&&parsedQuestionsData[2].category_knowledge===''")
    assert browser.evaluate('validateParsedQuestionBeforeImport(2,{notify:false,focus:false})')
    assert browser.evaluate('parsedQuestionNeedsSourceReview(2)')
    assert browser.evaluate('JSON.stringify(parsedQuestionsData[2].source_review)===window.__classificationBrowserSourceReview')
    assert browser.evaluate("document.querySelectorAll('.card-source-review-confirm').length") == 0
    browser.command('screenshot', str(SCREENSHOTS / f'confirmed-classification-{width}.png'))
    originals = browser.evaluate('JSON.stringify(parsedQuestionsData.map(q=>q.classification_review.original))')
    assert browser.evaluate('window.__classificationBrowserBlocked') == []
    browser.evaluate('window.MathBankDocumentImportSession.flush();true')
    location = browser.evaluate('location.href')
    browser.command('open', location)
    browser.command('wait', '--fn', "typeof window.refreshParsedClassificationReviews==='function'&&parsedQuestionsData.length===3&&Object.keys(categoryTree).length>0")
    browser.settle()
    browser.evaluate("selectWorkspace('import','导入中心');true")
    browser.settle()
    assert browser.evaluate('JSON.stringify(parsedQuestionsData.map(q=>q.classification_review.original))') == originals
    assert browser.evaluate("parsedQuestionsData[1].classification_review.confirmed_by==='user'&&!parsedQuestionsData[1].classification_review.required")
    assert browser.evaluate("parsedQuestionsData[2].classification_review.confirmed_by==='user'&&!parsedQuestionsData[2].classification_review.required")
    assert browser.evaluate('parsedQuestionNeedsSourceReview(2)')

    # Change only the GET result in this disposable browser. Calling the normal
    # catalog loader must refresh the warnings immediately, without a focus,
    # checkbox, or save action triggering validation as a side effect.
    refreshed = browser.evaluate(r"""
    (async()=>{
        const originalFetch=window.fetch,tree=structuredClone(categoryTree);
        tree['浏览器回归目录']={'仅用于确认目录变化':[]};
        window.fetch=(input,options={})=>{
            const url=String(input?.url||input);
            if(url.includes('/api/ai/')||url.includes('/api/upload/'))throw Error('No models or uploads');
            if(url.endsWith('/api/categories'))return Promise.resolve(new Response(JSON.stringify(tree),
                {status:200,headers:{'Content-Type':'application/json'}}));
            return originalFetch(input,options);
        };
        await loadCategories();
        const result={required:parsedQuestionsData.map(q=>q.classification_review.required),
            labels:[...document.querySelectorAll('.card-classification-status')].map(el=>el.textContent),
            original:JSON.stringify(parsedQuestionsData.map(q=>q.classification_review.original)),
            sourceAdvisory:parsedQuestionsData[2].source_review.required===true&&parsedQuestionsData[2].source_review.blocking===false};
        window.fetch=originalFetch;
        return result;
    })()
    """)
    assert refreshed['required'] == [True, True, True]
    assert refreshed['labels'] == ['分类待确认'] * 3
    assert refreshed['original'] == originals and refreshed['sourceAdvisory']
    _visible_card(browser, 2)
    browser.command('screenshot', str(SCREENSHOTS / f'catalog-refreshed-{width}.png'))
    # This card has never been restored, so the session's restoredCards path
    # cannot supply its new options. Exercise the classification-only refresher.
    fresh_options = browser.evaluate(r"""
    (async()=>{
        const q={content:'新增教材目录选项回归题。',answer_markdown:'',question_type:'single_choice',difficulty:'normal',
            category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'9.2 用样本估计总体',
            classification_review:{required:false,original:{category_compulsory:'必修二',category_chapter:'统计',
                category_knowledge:'9.2 用样本估计总体'},normalized_fields:['category_chapter']}};
        replaceParsedQuestions([q]);renderParsedQuestionsList(parsedQuestionsData);
        window.__freshClassificationOriginal=JSON.stringify(q.classification_review.original);
        const originalFetch=window.fetch,tree=structuredClone(categoryTree);
        delete tree['必修二']['9. 统计'];
        tree['必修三']={'1. 统计研究':['1.1 数据分析']};
        window.fetch=(input,options={})=>{
            const url=String(input?.url||input);
            if(url.includes('/api/ai/')||url.includes('/api/upload/'))throw Error('No models or uploads');
            if(url.endsWith('/api/categories'))return Promise.resolve(new Response(JSON.stringify(tree),
                {status:200,headers:{'Content-Type':'application/json'}}));
            return originalFetch(input,options);
        };
        await loadCategories();window.fetch=originalFetch;
        const card=document.getElementById('parsed-card-0');
        return {newBook:[...card.querySelector('.card-compulsory').options].some(option=>option.value==='必修三'),
            preservedChapter:card.querySelector('.card-chapter').value,preservedKnowledge:card.querySelector('.card-knowledge').value,
            outsideLabel:[...card.querySelector('.card-chapter').options].find(option=>option.value==='9. 统计')?.textContent,
            required:q.classification_review.required,original:JSON.stringify(q.classification_review.original)};
    })()
    """)
    assert fresh_options['newBook'] and fresh_options['required']
    assert fresh_options['preservedChapter'] == '9. 统计' and fresh_options['preservedKnowledge'] == '9.2 用样本估计总体'
    assert '不在当前目录' in fresh_options['outsideLabel']
    _visible_card(browser, 0)
    browser.command('select', '#parsed-card-0 .card-compulsory', '必修三')
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-chapter').value===''&&parsedQuestionsData[0].classification_review.required")
    browser.command('select', '#parsed-card-0 .card-chapter', '1. 统计研究')
    browser.command('select', '#parsed-card-0 .card-knowledge', '1.1 数据分析')
    assert browser.evaluate("!parsedQuestionsData[0].classification_review.required&&parsedQuestionsData[0].classification_review.confirmed_by==='user'")
    assert browser.evaluate('validateParsedQuestionBeforeImport(0,{notify:false,focus:false})')
    assert browser.evaluate('JSON.stringify(parsedQuestionsData[0].classification_review.original)===window.__freshClassificationOriginal')
    browser.command('screenshot', str(SCREENSHOTS / f'catalog-new-options-{width}.png'))
    (SCREENSHOTS / f'evidence-{width}.json').write_text(json.dumps({
        'viewport': [width, height], 'initial': initial, 'catalog_refresh': refreshed, 'fresh_option_refresh': fresh_options,
        'isolated_root': str(browser.root), 'actual_save_in_fixture': True,
    }, ensure_ascii=False, indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')
