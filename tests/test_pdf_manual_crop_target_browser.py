"""Opt-in real UI crop anchoring with synthetic pixels and mocked crop HTTP."""

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from test_preview_browser import browser  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".system_generated/pdf-manual-crop-target-20261020"
pytestmark = pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in isolated browser")


@pytest.mark.parametrize("width,height", [(1600, 1100), (390, 844)])
def test_answer_crop_stays_between_original_solution_steps(browser, width, height):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    assets = browser.root / "static/uploads/tmp"
    assets.mkdir(parents=True, exist_ok=True)
    for name, size in [("answer-source-demo.png", (420, 560)), ("answer-crop-demo.png", (180, 140))]:
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        draw.line([(20, size[1]-20), (20, 30), (size[0]-20, size[1]-20), (20, size[1]-20)], fill="black", width=3)
        image.save(assets / name)
    browser.command("set", "viewport", str(width), str(height))
    browser.command("network", "route", "**/api/ai/**", "--abort")
    browser.evaluate(r"""(()=>{
      MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      window.pdfPageImages=['/static/uploads/tmp/answer-source-demo.png'];window.pdfPageNumbers=[7];
      window.currentPdfTaskId='00000000-0000-4000-8000-000000000007';window.__cropRequests=[];
      const question={project_card_id:'answer-demo',source_number:16,content:'演示题干，原题不含图片。',
       answer_markdown:'第一步，建立直角坐标系。\n\n[插图待补：答案几何图]\n\n第二步，由 $x^2+y^2=1$ 继续计算。',
       question_type:'detailed_answer',difficulty:'normal',image_paths:[]};
      replaceParsedQuestions([question]);renderParsedQuestionsList(parsedQuestionsData);
      document.getElementById('importPlaceholder').classList.add('hidden');document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
      renderSourceIntegrityReport({pdf_figure_review:{status:'completed',total:1,verified:0,pending:1,checked:1,calls:3,items:[
       {page_number:7,slot_id:'p7-s1',source_number:16,target_field:'answer_markdown',slot_label:'[插图待补：答案几何图]',status:'pending',reason:'recrop_not_accepted'}]}});
      window.__realCropFetch=window.fetch;window.fetch=(input,options={})=>{
       const url=String(input?.url||input);
       if(url==='/api/ai/manual-crop-pdf'){
        window.__cropRequests.push(JSON.parse(options.body));
        return Promise.resolve(new Response(JSON.stringify({status:'success',image_path:'/static/uploads/tmp/answer-crop-demo.png'}),{status:200,headers:{'Content-Type':'application/json'}}));
       }
       if(url.includes('/api/ai/')||(String(options.method||'GET').toUpperCase()!=='GET'&&url.includes('/api/questions')))throw Error('No paid API or question writes in UI fixture');
       return window.__realCropFetch(input,options);
      };return true;
    })()""")
    try:
        browser.command("scrollintoview", "#pdfFigureReviewPanel .pdf-figure-review-crop")
        browser.command("click", "#pdfFigureReviewPanel .pdf-figure-review-crop")
        browser.command("wait", "--fn", "document.getElementById('pdfCropActiveImage').naturalWidth===420&&!document.getElementById('pdfCropModal').classList.contains('opacity-0')")
        assert browser.evaluate("document.getElementById('pdfCropTargetField').value") == "answer_markdown"
        assert browser.evaluate("(()=>{const el=document.getElementById('pdfCropTargetText');return el.value.slice(el.selectionStart,el.selectionEnd)})()") == "[插图待补：答案几何图]"
        browser.command("screenshot", str(EVIDENCE / f"answer-target-{width}.png"))
        bounds = browser.evaluate("(()=>{const r=document.getElementById('pdfCropImageContainer').getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
        browser.command("mouse", "move", str(round(bounds["x"]+bounds["w"]*.2)), str(round(bounds["y"]+bounds["h"]*.2)))
        browser.command("mouse", "down", "left")
        browser.command("mouse", "move", str(round(bounds["x"]+bounds["w"]*.8)), str(round(bounds["y"]+bounds["h"]*.8)))
        browser.command("mouse", "up", "left")
        browser.command("wait", "--fn", "!document.getElementById('pdfCropConfirmBtn').disabled")
        browser.command("click", "#pdfCropConfirmBtn")
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        browser.command("scrollintoview", "#parsed-card-0 .card-answer-preview img")
        browser.command("wait", "--fn", "document.querySelector('#parsed-card-0 .card-answer-preview img')?.naturalWidth===180")
        result = browser.evaluate(r"""(()=>{
          const card=document.getElementById('parsed-card-0'),preview=card.querySelector('.card-answer-preview'),image=preview.querySelector('img');
          const walker=document.createTreeWalker(preview,NodeFilter.SHOW_TEXT);let next,node;
          while(node=walker.nextNode()){if(node.textContent.includes('第二步')){next=node;break;}}
          return {requests:window.__cropRequests,content:card.querySelector('.card-content-textarea').value,answer:card.querySelector('.card-answer-textarea').value,
           promptImages:card.querySelectorAll('.card-content-preview img').length,answerImages:preview.querySelectorAll('img').length,
           imageBeforeNextStep:!!(image.compareDocumentPosition(next)&Node.DOCUMENT_POSITION_FOLLOWING),
           katexErrors:card.querySelectorAll('.katex-error').length,summary:document.querySelector('.pdf-figure-review-summary').textContent,
           receipt:parsedQuestionsData[0].document_import_manual_crops[0],overflow:document.documentElement.scrollWidth>innerWidth+1};
        })()""")
        assert len(result["requests"]) == 1 and result["requests"][0]["page_index"] == 6
        assert result["content"] == "演示题干，原题不含图片。"
        assert result["promptImages"] == 0 and result["answerImages"] == 1
        assert result["imageBeforeNextStep"] and result["katexErrors"] == 0
        assert result["receipt"]["target_field"] == "answer_markdown" and result["receipt"]["ai_verified"] is False
        assert "配图通过 0/1" in result["summary"]
        assert not result["overflow"]
        browser.command("screenshot", str(EVIDENCE / f"answer-preview-{width}.png"))
        (EVIDENCE / f"result-{width}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        browser.evaluate("window.fetch=window.__realCropFetch;closePdfCropModal();true")
