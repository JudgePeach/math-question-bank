"""Real Chromium acceptance in a disposable source-only app, with no model calls."""
import json
import os

import pytest

from test_preview_browser import browser, complete_import_fixture_categories  # noqa: F401


pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1', reason='opt-in isolated browser')


def click_stable(browser, selector):
    browser.command('scrollintoview', selector)
    browser.settle()
    browser.command('click', selector)


def show_questions(browser, questions):
    browser.evaluate("window.MathBankDocumentImportSession.clear(); selectWorkspace('import','导入中心'); "
                     "replaceParsedQuestions(" + json.dumps(questions, ensure_ascii=False) + "); "
                     "renderParsedQuestionsList(parsedQuestionsData); "
                     "document.getElementById('importPlaceholder').classList.add('hidden'); "
                     "document.getElementById('parsedQuestionsWrapper').classList.remove('hidden'); true")
    browser.settle()


def test_refresh_restores_real_input_and_selection_without_reviving_verification(browser, tmp_path):
    show_questions(browser, [{'content': '计算 $1+1$。', 'answer_markdown': '',
        'question_type': 'detailed_answer', 'difficulty': 'challenge', 'source': '原始来源', 'source_review': {
        'required': False, 'verified_by': 'vision', 'reasons': ['原记录'],
        'verification': {'document_type': 'docx', 'evidence': '原页一致'}}}])
    browser.evaluate("MathBankDocumentImportSession.begin({taskId:'refresh-fixture',documentType:'docx',phase:'completed'}); true")
    complete_import_fixture_categories(browser, 0)
    browser.evaluate("(()=>{const select=document.querySelector('#parsed-card-0 .card-knowledge');const value=[...select.options].find(option=>option.value);if(value){select.value=value.value;select.dispatchEvent(new Event('change',{bubbles:true}));}return true;})()")
    browser.command('fill', '#parsed-card-0 .card-source', '教师校对后的来源')
    expected = browser.evaluate("Object.fromEntries(['qtype','difficulty','source','compulsory','chapter','knowledge'].map(name=>[name,document.querySelector('#parsed-card-0 .card-'+name).value]))")
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '刷新前人工校对：计算 $2+2$。')
    browser.command('uncheck', '#parsed-card-0 .card-select-checkbox')
    browser.command('reload')
    browser.command('wait', '--fn', "typeof window.MathBankDocumentImportSession === 'object' && parsedQuestionsData.length === 1")
    assert browser.evaluate("document.getElementById('dashboardWorkspaceSection').classList.contains('hidden')") is False
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '刷新前人工校对：计算 $2+2$。'
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-select-checkbox').checked") is False
    assert browser.evaluate("Object.fromEntries(['qtype','difficulty','source','compulsory','chapter','knowledge'].map(name=>[name,document.querySelector('#parsed-card-0 .card-'+name).value]))") == expected
    browser.command('wait', '--fn', 'Object.keys(categoryTree).length > 0 && systemMetadata.question_types.length > 0')
    assert browser.evaluate("Object.fromEntries(['qtype','difficulty','source','compulsory','chapter','knowledge'].map(name=>[name,document.querySelector('#parsed-card-0 .card-'+name).value]))") == expected
    assert browser.evaluate('parsedQuestionNeedsSourceReview(0)') is True
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-status').textContent").startswith('内容已修改')
    browser.evaluate("selectWorkspace('import','导入中心'); true")
    browser.command('wait', '--fn', "!!document.getElementById('documentImportRecoveryPanel') && !document.getElementById('documentImportRecoveryPanel').hidden")
    browser.command('screenshot', str(tmp_path / 'restored-document-corrections.png'))
    browser.evaluate('MathBankDocumentImportSession.clear(); true')


def test_crop_pending_close_and_other_card_cannot_receive_old_image(browser, tmp_path):
    from PIL import Image, ImageDraw
    path = browser.root / 'static/uploads/tmp/crop-session-page.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    page = Image.new('RGB', (600, 800), 'white')
    ImageDraw.Draw(page).rectangle((60, 100, 260, 300), outline='black', width=5)
    page.save(path)
    browser.evaluate("window.pdfPageImages=['/static/uploads/tmp/crop-session-page.png']; window.pdfPageNumbers=[1]; true")
    show_questions(browser, [{'content': '裁图甲题', 'answer_markdown': ''}, {'content': '裁图乙题', 'answer_markdown': ''}])
    browser.evaluate(r"""
      window.currentPdfTaskId='crop-fixture';window.__cropFetch=window.fetch;
      window.fetch=(url,options)=>String(url).includes('/api/ai/')
        ? (String(url).includes('/manual-crop-pdf') ? new Promise(resolve=>window.__cropResolve=resolve) : Promise.reject(Error('AI disabled')))
        : window.__cropFetch(url,options);true
    """)
    try:
        browser.command('click', '#parsed-card-0 button[onclick="openPdfCropModalForQuestion(0)"]')
        browser.command('wait', '--fn', "document.getElementById('pdfCropActiveImage').naturalWidth > 0 && !document.getElementById('pdfCropModal').classList.contains('opacity-0')")
        browser.settle()
        bounds = browser.evaluate("(()=>{const r=document.getElementById('pdfCropImageContainer').getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height};})()")
        browser.command('mouse', 'move', str(round(bounds['x'] + bounds['w'] * .1)), str(round(bounds['y'] + bounds['h'] * .1)))
        browser.command('mouse', 'down', 'left')
        browser.command('mouse', 'move', str(round(bounds['x'] + bounds['w'] * .5)), str(round(bounds['y'] + bounds['h'] * .5)))
        browser.command('mouse', 'up', 'left')
        browser.command('wait', '--fn', '!document.getElementById("pdfCropConfirmBtn").disabled')
        browser.command('click', '#pdfCropConfirmBtn')
        browser.command('wait', '--fn', 'typeof window.__cropResolve === "function"')
        browser.command('click', '#pdfCropModal [data-modal-close]')
        browser.command('wait', '--fn', 'document.getElementById("pdfCropModal").classList.contains("hidden")')
        browser.command('scrollintoview', '#parsed-card-1 button[onclick="openPdfCropModalForQuestion(1)"]')
        browser.settle()
        browser.command('click', '#parsed-card-1 button[onclick="openPdfCropModalForQuestion(1)"]')
        browser.command('wait', '--fn', '!document.getElementById("pdfCropModal").classList.contains("opacity-0")')
        browser.evaluate("window.__cropResolve(new Response(JSON.stringify({status:'success',image_path:'/static/uploads/tmp/crop-session-page.png'}),{status:200,headers:{'Content-Type':'application/json'}})); true")
        browser.settle()
        assert browser.evaluate('parsedQuestionsData.every(q=>!q.image_paths.length)')
        assert browser.evaluate("document.querySelector('#parsed-card-1 .card-content-textarea').value") == '裁图乙题'
        assert browser.evaluate('window.activePdfCropSession.closed') is False
        assert browser.evaluate('document.getElementById("pdfCropModal").classList.contains("hidden")') is False
        browser.command('screenshot', str(tmp_path / 'late-crop-keeps-second-card.png'))
    finally:
        browser.evaluate('window.fetch=window.__cropFetch; closePdfCropModal(); MathBankDocumentImportSession.clear(); true')
        browser.command('wait', '--fn', 'document.getElementById("pdfCropModal").classList.contains("hidden")')


def test_saved_card_edit_remains_selected_and_survives_other_batch_success(browser):
    browser.evaluate('window.pdfPageImages=[]; true')
    show_questions(browser, [{'content': '入库后再编辑验收甲：计算 $1543+12$。', 'question_type': 'detailed_answer', 'answer_markdown': '1555'},
                             {'content': '空间几何独立验收：证明正方体的体对角线垂直于所给平面，并写出空间向量的坐标。', 'question_type': 'detailed_answer', 'answer_markdown': '取正交基证明。'}])
    complete_import_fixture_categories(browser, 0)
    complete_import_fixture_categories(browser, 1)
    click_stable(browser, '#parsed-card-0 .card-save-btn')
    browser.command('wait', '--fn', 'parsedQuestionsData[0].saved === true')
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '入库成功之后保留的人工修订：计算 $1543+13$。')
    assert browser.evaluate('parsedQuestionsData[0].saved') is False
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-select-checkbox').disabled") is False
    assert browser.evaluate('getCheckedUnsavedIndices()') == [0, 1]
    browser.command('uncheck', '#parsed-card-0 .card-select-checkbox')
    click_stable(browser, '#saveAllParsedBtn')
    browser.command('wait', '--fn', 'parsedQuestionsData.length === 2 && parsedQuestionsData[1].saved === true && !parsedBatchSaveInFlight')
    browser.evaluate('window.__clearCheckAfter=Date.now()+1700; true')
    browser.command('wait', '--fn', 'Date.now()>window.__clearCheckAfter')
    assert browser.evaluate('parsedQuestionsData.length') == 2
    assert browser.evaluate('parsedQuestionsData[0].saved') is False
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-textarea').value") == '入库成功之后保留的人工修订：计算 $1543+13$。'
    browser.evaluate('MathBankDocumentImportSession.clear(); true')


def test_duplicate_review_has_per_question_choices(browser, tmp_path):
    show_questions(browser, [{'content': '保留候选甲 $x+1$'}, {'content': '保留候选乙 $x+2$'}])
    browser.evaluate("window.__duplicateDecision=null; openParsedDuplicateReviewModal([{index:0,label:'第1题',result:{candidates:[],batch_matches:['fixture-1']}},{index:1,label:'第2题',result:{candidates:[],batch_matches:['fixture-0']}}],'batch').then(result=>window.__duplicateDecision=result); true")
    browser.command('wait', '--fn', 'document.querySelectorAll(".duplicate-keep-checkbox").length === 2')
    browser.command('check', '.duplicate-keep-checkbox[data-question-index="1"]')
    assert browser.evaluate("[...document.querySelectorAll('.duplicate-keep-checkbox')].map(input=>input.checked)") == [False, True]
    browser.command('screenshot', str(tmp_path / 'per-question-duplicate-choice.png'))
    click_stable(browser, '#duplicateReviewIndependentBtn')
    browser.command('wait', '--fn', 'window.__duplicateDecision !== null')
    assert browser.evaluate('window.__duplicateDecision') == {'action': 'selected', 'indices': [1]}
    browser.evaluate('MathBankDocumentImportSession.clear(); true')


def test_batch_import_only_writes_chosen_duplicate_and_unique_rows(browser):
    questions = [
        {'content': '函数逐题选择甲：设 $f(x)=x^3-x$，求 $f(2)$ 的值。', 'question_type': 'detailed_answer', 'difficulty': 'normal', 'answer_markdown': '6'},
        {'content': '平面几何逐题选择乙：在直角三角形中，斜边为十厘米、一个锐角为三十度，求其对边长度。', 'question_type': 'detailed_answer', 'difficulty': 'normal', 'answer_markdown': '五厘米'},
        {'content': '概率独立题：盒中有三个标号不同的小球，等可能抽取一个，求抽到指定球的概率。', 'question_type': 'detailed_answer', 'difficulty': 'normal', 'answer_markdown': '$1/3$'}
    ]
    browser.evaluate("""(async()=>{
      for(const question of """ + json.dumps(questions[:2], ensure_ascii=False) + """){
        const form=new FormData();for(const [key,value]of Object.entries(question))form.append(key,value);
        const response=await fetch('/api/questions',{method:'POST',body:form});
        if(!response.ok)throw Error(JSON.stringify(await response.json()));
      }return true;
    })()""")
    show_questions(browser, questions)
    for index in range(3):
        complete_import_fixture_categories(browser, index)
    before = browser.evaluate("fetch('/api/stats').then(response=>response.json()).then(data=>data.total_count)")
    click_stable(browser, '#saveAllParsedBtn')
    browser.command('wait', '--fn', 'document.querySelectorAll(".duplicate-keep-checkbox").length === 2')
    browser.command('check', '.duplicate-keep-checkbox[data-question-index="1"]')
    click_stable(browser, '#duplicateReviewIndependentBtn')
    browser.command('wait', '--fn', 'parsedQuestionsData.length===3 && parsedQuestionsData[1].saved===true && parsedQuestionsData[2].saved===true && !parsedBatchSaveInFlight')
    assert browser.evaluate('parsedQuestionsData.map(question=>!!question.saved)') == [False, True, True]
    after = browser.evaluate("fetch('/api/stats').then(response=>response.json()).then(data=>data.total_count)")
    assert after - before == 2
    assert browser.evaluate('getCheckedUnsavedIndices()') == [0]
    browser.evaluate('MathBankDocumentImportSession.clear(); true')


def test_formula_notice_precedes_content_only_for_specific_unresolved_risks(browser, tmp_path):
    show_questions(browser, [
        {'content': '具体公式风险题 $x+1$', 'source_review': {'required': True, 'formula_notice_required': True,
            'native_extraction_risks': [{'kind': 'formula'}], 'reasons': ['未知公式字形']}},
        {'content': '只有结构核对提示 $x+2$', 'source_review': {'required': True, 'reasons': ['题号归属需核对']}},
        {'content': '已核验公式题 $x+3$', 'source_review': {'required': False, 'verified_by': 'vision',
            'formula_notice_required': True, 'verification': {'evidence': '原页一致'}}}
    ])
    result = browser.evaluate("parsedQuestionsData.map((q,i)=>{const c=document.getElementById('parsed-card-'+i),n=c.querySelector('.card-formula-review-notice'),p=c.querySelector('.card-content-preview');return {visible:!!n&&!n.hidden,before:!!n&&!!(n.compareDocumentPosition(p)&Node.DOCUMENT_POSITION_FOLLOWING),color:n?getComputedStyle(n).color:''};})")
    assert result[0]['visible'] and result[0]['before']
    assert result[0]['color'] != 'rgb(0, 0, 0)'
    assert not result[1]['visible'] and not result[2]['visible']
    browser.command('screenshot', str(tmp_path / 'specific-formula-review-notice.png'))
    browser.evaluate('MathBankDocumentImportSession.clear(); true')
