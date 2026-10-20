"""Opt-in figure-review UI with invented cards/images and no model requests."""

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from test_preview_browser import browser  # noqa: F401
from test_document_projects_browser import MOCK, _click


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.system_generated/pdf-figure-review-ui-20261020/browser'
pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1', reason='opt-in isolated browser')


@pytest.mark.parametrize('width,height', [(1600, 1100), (390, 844)])
def test_figure_review_progress_pending_crop_and_teacher_edits(browser, width, height):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    assets = browser.root / 'static/uploads/tmp'
    assets.mkdir(parents=True, exist_ok=True)
    for number in (3, 7):
        image = Image.new('RGB', (420, 560), 'white')
        draw = ImageDraw.Draw(image)
        draw.text((30, 30), f'DEMO SOURCE PAGE {number}', fill='black')
        draw.line([(80, 420), (80, 180), (310, 420), (80, 420)], fill='black', width=3)
        image.save(assets / f'pdf_page_figure-demo_{number}.png')
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('network', 'route', '**/api/ai/**', '--abort')
    browser.evaluate('window.__figureReviewWidth=' + str(width))
    browser.evaluate(r"""(()=>{
      window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      const id='figure-ui-'+window.__figureReviewWidth,now=Date.now()/1000;
      const q={project_card_id:'figure-card',source_number:16,content:'演示题：求三角形面积。[插图待补: 图1]',
       answer_markdown:'原版答案：$6$。',question_type:'detailed_answer',difficulty:'normal',
       category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'',image_paths:[],
       source_review:{required:true,blocking:false,disposition:'advisory',reasons:['配图尚需对照原页。']},
       classification_review:{required:false}};
      const diagnostics={pdf_local_draft:{persisted:true,status:'ready',question_count:1},pdf_figure_review:{
       status:'running',total:4,checked:2,verified:1,corrected:0,pending:3,reused:0,calls:2,
       items:[{id:'p3-fig1',page_number:3,slot_id:'p3-s1',status:'verified',reason:''},
        {id:'p7-fig1',page_number:7,slot_id:'p7-s1',source_number:16,status:'pending',reason:'crop_not_accepted'}]}};
      const project={project_id:id,revision:1,status:'completed',document_type:'pdf',question_count:1,
       task_id:'00000000-0000-4000-8000-000000000007',can_resume:false,title:'配图核验演示',
       last_used_at:now,expires_at:now+7*86400,total_bytes:2048,diagnostics,
       page_images:['/static/uploads/tmp/pdf_page_figure-demo_3.png','/static/uploads/tmp/pdf_page_figure-demo_7.png'],
       page_numbers:[3,7],draft:{questions:[q],baselineQuestions:[q],paperTitle:'配图核验演示'},source_pages:[]};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify({[id]:project}));
      window.__figureReviewProject=id;window.__figureReviewDiagnostics=diagnostics;return true;
    })()""")
    browser.evaluate(MOCK)
    browser.evaluate('MathBankDocumentImportSession.openProject(window.__figureReviewProject).then(()=>true)')
    browser.command('wait', '--fn', "!!document.getElementById('pdfFigureReviewPanel')")
    assert browser.evaluate("document.querySelector('#pdfFigureReviewPanel .pdf-figure-review-receipt').textContent==='首次正文稿已保存。'")
    assert browser.evaluate("document.querySelector('#pdfFigureReviewPanel .pdf-figure-review-summary').textContent.includes('配图通过 1/4')")
    browser.evaluate(r"""(()=>{
      MathBankDocumentImportSession.update({phase:'layout_analysis',diagnostics:window.__figureReviewDiagnostics,log:'演示核验开始'});
      window.__figurePanel=document.getElementById('pdfFigureReviewPanel');
      window.__figureSaveStatus=document.querySelector('#documentImportRecoveryPanel .document-project-save-status');
      window.__projectBrowserActions=[];
      for(let i=0;i<3;i++){
       const diagnostics=JSON.parse(JSON.stringify(window.__figureReviewDiagnostics));diagnostics.pdf_figure_review.calls=i;
       MathBankDocumentImportSession.update({phase:'layout_analysis',diagnostics,log:'演示核验进度'+i});
       renderPdfFigureReviewPanel(diagnostics);
      }
      return true;
    })()""")
    browser.command('wait', '--fn', "!JSON.parse(localStorage.getItem('mathbank_document_import_session_v1')).projectLocalDirty")
    assert browser.evaluate("document.getElementById('pdfFigureReviewPanel')===window.__figurePanel")
    assert browser.evaluate("document.querySelector('#documentImportRecoveryPanel .document-project-save-status')===window.__figureSaveStatus")
    assert browser.evaluate("window.__projectBrowserActions.every(x=>x.method==='GET')")
    assert browser.evaluate("validateParsedQuestionBeforeImport(0,{notify:false,focus:false})")
    _click(browser, '#pdfFigureReviewPanel .pdf-figure-review-crop')
    browser.command('wait', '--fn', "document.getElementById('pdfCropActiveImage').naturalWidth===420&&!document.getElementById('pdfCropModal').classList.contains('opacity-0')")
    assert browser.evaluate("document.getElementById('pdfCropPageIndicator').textContent.includes('原卷第 7 页')")
    assert browser.evaluate("activePageIndex===1&&window.activePdfCropSession.questionIndex===0&&window.activePdfCropSession.slotId==='p7-s1'")
    browser.command('screenshot', str(EVIDENCE / f'correct-source-page-{width}.png'))
    browser.evaluate('closePdfCropModal();true')
    browser.command('wait', '--fn', "document.getElementById('pdfCropModal').classList.contains('hidden')")
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '教师刚调整的题文与待补图说明。')
    browser.command('wait', '--fn', "document.querySelector('#pdfFigureReviewPanel .pdf-figure-review-edited').hidden===false")
    assert browser.evaluate("document.querySelector('#pdfFigureReviewPanel .pdf-figure-review-summary').textContent.includes('配图通过 1/4')")
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__figureReviewProject].draft.questions[0].content==='教师刚调整的题文与待补图说明。'")
    browser.command('scrollintoview', '#pdfFigureReviewPanel')
    browser.settle()
    assert browser.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    browser.command('screenshot', str(EVIDENCE / f'optional-review-and-saved-edit-{width}.png'))
    result = browser.evaluate("({summary:document.querySelector('#pdfFigureReviewPanel .pdf-figure-review-summary').textContent,manual:document.querySelector('#pdfFigureReviewPanel .pdf-figure-review-edited').textContent,actions:window.__projectBrowserActions})")
    (EVIDENCE / f'result-{width}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
