"""Click-to-recrop uses the real import UI with synthetic images and no AI."""

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from test_preview_browser import browser, complete_import_fixture_categories  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".system_generated/pdf-image-replacement-browser-20261022"
OLD = "/static/uploads/tmp/replacement-old.png"
NEW = "/static/uploads/tmp/replacement-new.png"
OTHER = "/static/uploads/tmp/replacement-other.png"
pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in isolated browser"
)


def _prepare(browser, content, answer, paths=None, extra=None):
    assets = browser.root / "static/uploads/tmp"
    assets.mkdir(parents=True, exist_ok=True)
    for name, size, color in [
        ("replacement-source.png", (420, 560), "black"),
        ("replacement-old.png", (160, 110), "red"),
        ("replacement-new.png", (180, 140), "green"),
        ("replacement-other.png", (140, 100), "blue"),
    ]:
        path = assets / name
        if not path.exists():
            image = Image.new("RGB", size, "white")
            ImageDraw.Draw(image).line(
                [(15, size[1] - 15), (size[0] // 2, 15),
                 (size[0] - 15, size[1] - 15), (15, size[1] - 15)],
                fill=color, width=4,
            )
            image.save(path)
    question = {
        "project_card_id": "replacement-card", "source_number": 16,
        "content": content, "answer_markdown": answer,
        "question_type": "detailed_answer", "difficulty": "normal",
        "image_paths": paths if paths is not None else [OLD],
        **(extra or {}),
    }
    browser.command("network", "route", "**/api/ai/**", "--abort")
    browser.evaluate(r"""(()=>{
      if(window.__replacementRealFetch)window.fetch=window.__replacementRealFetch;
      if(window.__replacementRealOpen)window.open=window.__replacementRealOpen;
      if(window.activePdfCropSession)closePdfCropModal();
      MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      window.pdfPageImages=['/static/uploads/tmp/replacement-source.png'];window.pdfPageNumbers=[7];
      window.currentPdfTaskId='00000000-0000-4000-8000-000000000007';
      window.__replacementRequests=[];window.__replacementFailures=[];window.__replacementOpened=[];
      window.__replacementAllowQuestionWrites=false;window.__replacementSavedQuestions=[];
      window.__replacementFail=false;window.__replacementHold=false;window.__resolveReplacement=null;
      window.__replacementRealFetch=window.fetch;window.__replacementRealOpen=window.open;
      window.open=(...args)=>{window.__replacementOpened.push(args);return null;};
      window.fetch=(input,options={})=>{
        const url=String(input?.url||input),method=String(options.method||'GET').toUpperCase();
        if(url==='/api/ai/manual-crop-pdf'){
          window.__replacementRequests.push(JSON.parse(options.body));
          const response=()=>new Response(JSON.stringify(window.__replacementFail
            ?{status:'error',message:'合成裁图失败'}
            :{status:'success',image_path:'/static/uploads/tmp/replacement-new.png'}),
            {status:window.__replacementFail?500:200,headers:{'Content-Type':'application/json'}});
          if(window.__replacementHold)return new Promise(resolve=>{window.__resolveReplacement=()=>resolve(response());});
          return Promise.resolve(response());
        }
        if(url.includes('/api/ai/')||(method!=='GET'&&url.includes('/api/questions')&&!window.__replacementAllowQuestionWrites)){
          window.__replacementFailures.push(url);throw Error('No AI or question writes in crop fixture');
        }
        if(url==='/api/questions'&&method==='POST')return window.__replacementRealFetch(input,options).then(async response=>{
          window.__replacementSavedQuestions.push(await response.clone().json());return response;
        });
        return window.__replacementRealFetch(input,options);
      };return true;
    })()""")
    browser.evaluate(
        "(()=>{replaceParsedQuestions([" + json.dumps(question, ensure_ascii=False) + r"""]);
          renderParsedQuestionsList(parsedQuestionsData);
          document.getElementById('importPlaceholder').classList.add('hidden');
          document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
          return true;})()"""
    )
    browser.command("wait", "--fn", "!!document.querySelector('#parsed-card-0 img[data-pdf-crop-replace]')")


def _open_replacement(browser, selector, keyboard=None):
    browser.command("scrollintoview", selector)
    if keyboard:
        browser.command("focus", selector)
        browser.command("press", keyboard)
    else:
        browser.command("click", selector)
    browser.command("wait", "--fn", (
        "!!window.activePdfCropSession?.replacement"
        "&&!document.getElementById('pdfCropModal').classList.contains('opacity-0')"
        "&&document.getElementById('pdfCropActiveImage').naturalWidth===420"
    ))
    controls = browser.evaluate(r"""(()=>{
      const old=document.querySelector('#pdfCropReplacementPreview img');
      return {field:document.getElementById('pdfCropTargetField').disabled,
        text:document.getElementById('pdfCropTargetText').disabled,
        button:document.getElementById('pdfCropConfirmBtn').textContent,
        original:old?.getAttribute('src'),replacement:window.activePdfCropSession.replacement,
        opened:window.__replacementOpened.length,overflow:document.documentElement.scrollWidth>innerWidth+1};
    })()""")
    assert controls["field"] and controls["text"], controls
    assert "确认替换此图" in controls["button"], controls
    assert controls["original"] == OLD, controls
    assert controls["opened"] == 0 and not controls["overflow"], controls
    return controls["replacement"]


def _draw_and_submit(browser):
    bounds = browser.evaluate(
        "(()=>{const r=document.getElementById('pdfCropImageContainer').getBoundingClientRect();"
        "return {x:r.x,y:r.y,w:r.width,h:r.height}})()"
    )
    browser.command("mouse", "move", str(round(bounds["x"] + bounds["w"] * .2)),
                    str(round(bounds["y"] + bounds["h"] * .2)))
    browser.command("mouse", "down", "left")
    browser.command("mouse", "move", str(round(bounds["x"] + bounds["w"] * .8)),
                    str(round(bounds["y"] + bounds["h"] * .8)))
    browser.command("mouse", "up", "left")
    browser.command("wait", "--fn", "!document.getElementById('pdfCropConfirmBtn').disabled")
    browser.command("click", "#pdfCropConfirmBtn")


def _state(browser):
    return browser.evaluate(r"""(()=>{
      const card=document.getElementById('parsed-card-0'),q=parsedQuestionsData[0];
      return {content:card.querySelector('.card-content-textarea').value,
        answer:card.querySelector('.card-answer-textarea').value,paths:q.image_paths,
        requests:window.__replacementRequests,blocked:window.__replacementFailures,
        sourceEdited:q.document_import_source_edited===true,
        errors:card.querySelectorAll('.katex-error').length,
        overflow:document.documentElement.scrollWidth>innerWidth+1};
    })()""")


def _cleanup(browser):
    browser.evaluate(r"""(()=>{
      window.fetch=window.__replacementRealFetch;window.open=window.__replacementRealOpen;
      delete window.__replacementRealFetch;delete window.__replacementRealOpen;
      closePdfCropModal();return true;
    })()""")
    browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")


def _import_current_question(browser):
    """Save through the real card and inspect the disposable fixture database API."""
    complete_import_fixture_categories(browser, 0)
    browser.evaluate("window.__replacementAllowQuestionWrites=true;true")
    browser.command("scrollintoview", "#parsed-card-0 .card-save-btn")
    browser.settle()
    browser.command("click", "#parsed-card-0 .card-save-btn")
    browser.command("wait", "--fn", "window.__replacementSavedQuestions.length===1")
    response = browser.evaluate("window.__replacementSavedQuestions[0]")
    assert response.get("status") == "success", response
    browser.command("wait", "--fn", "parsedQuestionsData[0].saved===true")
    return browser.evaluate(r"""(async()=>{
      const response=window.__replacementSavedQuestions[0],id=response.question.id,q=parsedQuestionsData[0];
      const stored=await fetch('/api/questions/'+id).then(result=>result.json());
      return {response,stored,snapshot:q.savedSnapshot,
        paths:q.image_paths,reference:q.tikz_reference_image_path,contentAssets:q.content_tikz_assets,
        manualCrops:q.document_import_manual_crops,
        blocked:window.__replacementFailures,visible:[...document.querySelectorAll('#parsed-card-0 .card-content-preview img')].map(image=>image.getAttribute('src'))};
    })()""")


@pytest.mark.parametrize("width,height", [(1600, 1100), (390, 844)])
def test_clicked_occurrence_stays_in_its_choice_and_answer_table(browser, width, height):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command("set", "viewport", str(width), str(height))
    content = ("题干，选择符合条件的图。\n\n\\begin{choices}\n"
               f"\\item ![第一选项]({OLD})\n\\item ![第二选项]({OLD})\n"
               "\\item $2$\n\\item $3$\n\\end{choices}")
    answer = ("第一步，比较图形。\n\n| 图形 | 结论 |\n| --- | --- |\n"
              f"| ![答案表图]({OLD}) | $x=1$ |\n\n第二步，完成计算。")
    _prepare(browser, content, answer)
    try:
        # The shared choices renderer owns option markup; use source-order image
        # indexing so this regression does not depend on the option class name.
        browser.evaluate("document.querySelectorAll('#parsed-card-0 .card-content-preview img[data-pdf-crop-replace]')[1].id='replace-second-choice';true")
        replacement = _open_replacement(browser, "#replace-second-choice")
        assert replacement["field"] == "content"
        assert replacement["start"] == content.rindex(OLD)
        assert replacement["end"] == content.rindex(OLD) + len(OLD)
        browser.command("screenshot", str(EVIDENCE / f"replace-dialog-{width}.png"))
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        state = _state(browser)
        expected = content[:content.rindex(OLD)] + NEW + content[content.rindex(OLD) + len(OLD):]
        assert state["content"] == expected and state["answer"] == answer, state
        assert state["paths"] == [OLD, NEW], state
        assert state["requests"][0]["page_index"] == 6, state
        assert state["sourceEdited"] and not state["blocked"], state
        assert browser.evaluate("document.querySelectorAll('#parsed-card-0 .card-content-preview .choices-grid img').length") == 2
        replacement = _open_replacement(browser, '#parsed-card-0 .card-answer-preview td img[data-pdf-crop-replace]', keyboard="Enter")
        assert replacement["field"] == "answer_markdown"
        assert replacement["start"] == answer.index(OLD)
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        state = _state(browser)
        assert state["content"] == expected and state["answer"] == answer.replace(OLD, NEW), state
        assert state["paths"] == [OLD, NEW] and len(state["requests"]) == 2, state
        assert state["errors"] == 0 and not state["overflow"], state
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-answer-preview td img').getAttribute('src')") == NEW
        browser.command("scrollintoview", "#parsed-card-0 .card-answer-preview")
        browser.command("screenshot", str(EVIDENCE / f"replacement-result-{width}.png"))
        (EVIDENCE / f"replacement-result-{width}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    finally:
        _cleanup(browser)


@pytest.mark.parametrize("width,height", [(1600, 1100), (390, 844)])
def test_attachment_replacement_keeps_the_original_list_position(browser, width, height):
    browser.command("set", "viewport", str(width), str(height))
    _prepare(browser, "附件配图题干，正文不含图片语法。", "原答案保持完整。", [OTHER, OLD])
    try:
        browser.evaluate("[...document.querySelectorAll('#parsed-card-0 .card-content-preview img[data-pdf-crop-replace]')].find(img=>img.getAttribute('src')===" + json.dumps(OLD) + ").id='replace-attachment';true")
        replacement = _open_replacement(browser, "#replace-attachment", keyboard="Space")
        assert replacement["attachment"] is True, replacement
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        state = _state(browser)
        assert state["content"] == "附件配图题干，正文不含图片语法。", state
        assert state["answer"] == "原答案保持完整。" and state["paths"] == [OTHER, NEW], state
        assert state["sourceEdited"] and not state["blocked"] and not state["overflow"], state
    finally:
        _cleanup(browser)


def test_cancel_failure_and_late_crop_keep_existing_content(browser):
    browser.command("set", "viewport", "1600", "1100")
    content = f"前文。\n\n![原图说明]({OLD})\n\n后文继续。"
    answer = "完整的原答案。"
    _prepare(browser, content, answer)
    selector = '#parsed-card-0 .card-content-preview img[data-pdf-crop-replace]'
    try:
        _open_replacement(browser, selector)
        browser.command("click", '#pdfCropModal button[onclick="closePdfCropModal()"]')
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        state = _state(browser)
        assert state["content"] == content and state["answer"] == answer and state["paths"] == [OLD]
        assert state["requests"] == []
        _open_replacement(browser, selector)
        browser.evaluate("window.__replacementFail=true;true")
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "!window.activePdfCropSession.submitting")
        state = _state(browser)
        assert state["content"] == content and state["answer"] == answer and state["paths"] == [OLD]
        assert not browser.evaluate("document.getElementById('pdfCropModal').classList.contains('hidden')")
        assert browser.evaluate("document.getElementById('pdfCropTargetField').disabled&&document.getElementById('pdfCropTargetText').disabled")
        browser.evaluate("closePdfCropModal();window.__replacementFail=false;window.__replacementHold=true;true")
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        _open_replacement(browser, selector)
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "typeof window.__resolveReplacement==='function'")
        browser.evaluate(r"""(()=>{
          const input=document.querySelector('#parsed-card-0 .card-answer-textarea');
          input.value+='教师在等待期间的修改。';input.dispatchEvent(new Event('input',{bubbles:true}));
          window.__resolveReplacement();return true;
        })()""")
        browser.command("wait", "--fn", "!window.activePdfCropSession.submitting")
        state = _state(browser)
        assert state["content"] == content and state["answer"] == answer + "教师在等待期间的修改。", state
        assert state["paths"] == [OLD] and not state["blocked"], state
    finally:
        _cleanup(browser)


@pytest.mark.parametrize("modifier", ["ctrlKey", "metaKey"])
def test_modified_image_click_keeps_original_image_viewing(browser, modifier):
    browser.command("set", "viewport", "1600", "1100")
    content = f"![插图]({OLD})"
    _prepare(browser, content, "答案。")
    try:
        browser.evaluate("document.querySelector('#parsed-card-0 img[data-pdf-crop-replace]').dispatchEvent(new MouseEvent('click',{bubbles:true," + modifier + ":true}));true")
        assert browser.evaluate("window.__replacementOpened.map(args=>args[0])") == [OLD]
        assert browser.evaluate("document.getElementById('pdfCropModal').classList.contains('hidden')")
        assert _state(browser)["requests"] == []
    finally:
        _cleanup(browser)


def test_private_reference_does_not_reappear_as_an_unanchored_old_figure(browser):
    browser.command("set", "viewport", "1600", "1100")
    content = f"普通插图前文。\n\n![本题图]({OLD})\n\n插图后文。"
    _prepare(browser, content, "答案。", extra={"tikz_reference_image_path": OLD})
    try:
        _open_replacement(browser, '#parsed-card-0 .card-content-preview img[data-pdf-crop-replace]')
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        result = browser.evaluate(r"""(()=>{
          const q=parsedQuestionsData[0],card=document.getElementById('parsed-card-0');
          return {reference:q.tikz_reference_image_path,paths:q.image_paths,content:q.content,
            images:[...card.querySelectorAll('.card-content-preview img')].map(image=>image.getAttribute('src'))};
        })()""")
        assert result["reference"] == OLD, result
        assert result["content"] == content.replace(OLD, NEW), result
        assert result["paths"] == [NEW] and result["images"] == [NEW], result
    finally:
        _cleanup(browser)


@pytest.mark.parametrize("field", ["content", "answer_markdown"])
@pytest.mark.parametrize("occurrences", [1, 2])
def test_drawing_binding_is_removed_only_after_its_last_visible_occurrence(browser, field, occurrences):
    browser.command("set", "viewport", "1600", "1100")
    old_asset = {"id": "old-drawing", "image_path": OLD, "reference_image_path": OTHER,
                 "tikz_code": r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}"}
    retained_asset = {"id": "retained-drawing", "image_path": OTHER, "reference_image_path": OTHER,
                      "tikz_code": r"\begin{tikzpicture}\draw(0,0)circle(1);\end{tikzpicture}"}
    images = "\n\n".join(f"![旧图{number}]({OLD})" for number in range(occurrences))
    target = f"正文开始。\n\n{images}\n\n![保留图]({OTHER})\n\n正文结束。"
    extra = {
        "content_tikz_assets": [old_asset, retained_asset] if field == "content" else [retained_asset],
        "answer_tikz_assets": [old_asset, retained_asset] if field == "answer_markdown" else [],
        "tikz_code": old_asset["tikz_code"] if field == "content" else retained_asset["tikz_code"],
        "tikz_reference_image_path": OTHER,
    }
    content, answer = (target, "答案。") if field == "content" else (f"题干。\n\n![题干保留图]({OTHER})", target)
    _prepare(browser, content, answer, [OLD, OTHER], extra)
    try:
        preview = ".card-content-preview" if field == "content" else ".card-answer-preview"
        browser.evaluate("document.querySelector('#parsed-card-0 " + preview + " img[data-pdf-crop-replace]').id='replace-old-drawing';true")
        _open_replacement(browser, "#replace-old-drawing")
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        result = browser.evaluate(r"""(()=>{
          const q=parsedQuestionsData[0],card=document.getElementById('parsed-card-0');
          return {content:q.content,answer:q.answer_markdown,contentAssets:q.content_tikz_assets,
            answerAssets:q.answer_tikz_assets,legacy:q.tikz_code,reference:q.tikz_reference_image_path,
            contentImages:[...card.querySelectorAll('.card-content-preview img')].map(image=>image.getAttribute('src')),
            answerImages:[...card.querySelectorAll('.card-answer-preview img')].map(image=>image.getAttribute('src'))};
        })()""")
        assets = result["contentAssets"] if field == "content" else result["answerAssets"]
        assert assets == ([old_asset, retained_asset] if occurrences == 2 else [retained_asset]), result
        assert (result["content"] if field == "content" else result["answer"]) == target.replace(OLD, NEW, 1), result
        shown = result["contentImages"] if field == "content" else result["answerImages"]
        assert shown.count(OLD) == occurrences - 1 and shown.count(NEW) == 1, result
        expected_legacy = old_asset if field == "content" and occurrences == 2 else retained_asset
        assert result["legacy"] == expected_legacy["tikz_code"] and result["reference"] == OTHER, result
    finally:
        _cleanup(browser)


def test_replacement_layout_survives_actual_import_and_asset_promotion(browser):
    browser.command("set", "viewport", "1600", "1100")
    content = f"替换布局正式入库回归：先核对坐标图。\n\n![布局图]({OLD})\n\n再计算 $x^2+y^2$。"
    layout = {"align": "left", "size": "small"}
    _prepare(browser, content, "布局回归答案：$x^2+y^2=1$。", extra={
        "image_layouts": {"tmp/replacement-old.png": layout},
        "figure_align": "bottom_left", "figure_size": "small", "figure_align_custom": True,
    })
    try:
        _open_replacement(browser, '#parsed-card-0 .card-content-preview img[data-pdf-crop-replace]')
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        assert browser.evaluate("parsedQuestionsData[0].image_layouts") == {"tmp/replacement-new.png": layout}
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-content-preview img').classList.contains('mb-inline-image-size-small')")
        assert browser.evaluate("!!document.querySelector('#parsed-card-0 .card-content-preview .mb-inline-image-align-left')")
        result = _import_current_question(browser)
        response, stored = result["response"], result["stored"]
        promoted = response["asset_path_map"][NEW]
        promoted_key = promoted.removeprefix("/static/uploads/")
        assert promoted != NEW and "/tmp/" not in promoted, result
        assert stored["content"].replace("\r\n", "\n") == content.replace(OLD, promoted), result
        assert stored["image_layouts"] == {promoted_key: layout}, result
        assert result["snapshot"]["image_layouts"] == {promoted_key: layout}, result
        assert stored["figure_align"] == "bottom_left" and stored["figure_size"] == "small", result
        assert stored["figure_align_custom"] is True, result
        assert result["visible"] == [promoted] and result["blocked"] == [], result
        assert result["manualCrops"][0]["image_path"] == promoted, result
        assert browser.evaluate("pdfCropReplacementPage(parsedQuestionsData[0]," + json.dumps(promoted) + ")") == 7
    finally:
        _cleanup(browser)


def test_saved_private_reference_remains_registered_without_visible_old_image(browser):
    browser.command("set", "viewport", "1600", "1100")
    retained = {"id": "retained-private-reference", "image_path": OTHER, "reference_image_path": OLD,
                "tikz_code": r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}"}
    content = (f"私有参考图正式入库回归：替换以下原图。\n\n![待替换图]({OLD})"
               f"\n\n保留独立绘图。\n\n![保留绘图]({OTHER})\n\n以上为两处插图。")
    _prepare(browser, content, "原答案保持完整。", [OLD, OTHER], {
        "content_tikz_assets": [retained], "tikz_code": retained["tikz_code"],
        "tikz_reference_image_path": OLD,
    })
    try:
        _open_replacement(browser, '#parsed-card-0 .card-content-preview img[data-pdf-crop-replace]')
        _draw_and_submit(browser)
        browser.command("wait", "--fn", "document.getElementById('pdfCropModal').classList.contains('hidden')")
        result = _import_current_question(browser)
        mapping, stored = result["response"]["asset_path_map"], result["stored"]
        new_path, reference_path, drawing_path = mapping[NEW], mapping[OLD], mapping[OTHER]
        expected = content.replace(OLD, new_path).replace(OTHER, drawing_path)
        assert stored["content"].replace("\r\n", "\n") == expected, result
        assert stored["content_tikz_assets"][0]["reference_image_path"] == reference_path, result
        assert result["reference"] == reference_path, result
        assert result["visible"] == [new_path, drawing_path] and reference_path not in result["paths"], result
        assert result["manualCrops"][0]["image_path"] == new_path, result
        assert result["blocked"] == [], result
        browser.command("wait", "--fn", "[...document.querySelectorAll('#parsed-card-0 .card-content-preview img')].every(image=>image.complete&&image.naturalWidth>0)")
        assert browser.evaluate("fetch(" + json.dumps(reference_path) + ").then(response=>response.ok)")
    finally:
        _cleanup(browser)
