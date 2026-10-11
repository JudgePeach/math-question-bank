"""Single OCR UI with deterministic AI replies and real isolated uploads/saves."""

import json
import re

from PIL import Image, ImageDraw

from test_preview_browser import browser, pytestmark  # noqa: F401


def _source(browser, name):
    folder = browser.root / "static" / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ".png")
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 50, 320, 250), fill="navy")
    draw.rectangle((460, 280, 720, 450), fill="green")
    image.save(path)
    return "/static/uploads/" + path.name


def _editor(browser, name):
    browser.evaluate("""(() => {
        const comp=Object.keys(categoryTree)[0], chapter=Object.keys(categoryTree[comp])[0];
        selectWorkspace('bank','题库管理');
        selectDraft({id:%s,isDraft:true,content:'',answer_markdown:'',question_type:'single_choice',
          category_compulsory:comp,category_chapter:chapter,difficulty:'medium',image_paths:[],
          content_tikz_assets:[],answer_tikz_assets:[]});
        openQuestionEditorModal('content');
        return true;
    })()""" % json.dumps(name))
    browser.command("wait", "--fn", "!questionDetailLoading && !document.getElementById('editorSection').classList.contains('hidden')")


def _ocr(browser, response, *, skip=False, delay=False):
    browser.evaluate("""(async () => {
        window.__singleOcrOriginalFetch=window.fetch;
        window.__singleOcrMock=%s;
        window.fetch=async (input,options) => {
            if(String(input)==='/api/ocr') {
                window.__singleOcrSkip=options.body.get('skip_tikz');
                %s
                return new Response(JSON.stringify(window.__singleOcrMock),{status:200,headers:{'Content-Type':'application/json'}});
            }
            return window.__singleOcrOriginalFetch(input,options);
        };
        const source=await window.__singleOcrOriginalFetch(window.__singleOcrMock.image_path).then(r=>r.blob());
        document.getElementById('contentOcrSkipTikz').checked=%s;
        runContentOcr(new File([source],'question.png',{type:'image/png'}));
        return true;
    })()""" % (json.dumps(response),
               "await new Promise(resolve=>window.__singleOcrRelease=resolve);" if delay else "",
               "true" if skip else "false"))
    browser.command("wait", "--fn", "typeof window.__singleOcrSkip==='string'")
    if not delay:
        browser.command("wait", "--fn", "!document.getElementById('contentOcrOutputBox').classList.contains('hidden')")


def _restore_fetch(browser):
    browser.evaluate("window.fetch=window.__singleOcrOriginalFetch; delete window.__singleOcrSkip; true")


def _click(browser, selector):
    # The editor scrolls inside its modal. Explicitly bring controls into view
    # before Chromium pointer clicks; the CLI does not scroll that container.
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)


def _verified_review(*, corrected=False, evidence="实际裁片保留完整图形和标注，图位一致。"):
    return {"status": "verified", "corrected": corrected, "evidence": evidence,
            "checks": {"same_figure": True, "full_graphic": True, "labels_complete": True,
                       "no_foreign_content": True, "correct_anchor": True}}


def _save_with_duplicate_review(browser):
    # Related fixture questions share a database; preserve the real save gate.
    browser.evaluate("""(() => {
        window.__singleOcrSaveOutcome={finished:false};
        Promise.resolve(saveQuestion(true)).then(value=>{
            window.__singleOcrSaveOutcome={finished:true,value};
        },error=>{
            window.__singleOcrSaveOutcome={finished:true,error:String(error)};
        });
        return true;
    })()""")
    browser.command('wait', '--fn', "window.__singleOcrSaveOutcome.finished || !document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')")
    if browser.evaluate("!document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')"):
        _click(browser, '#duplicateReviewIndependentBtn')
    browser.command('wait', '--fn', 'window.__singleOcrSaveOutcome.finished')
    outcome = browser.evaluate('window.__singleOcrSaveOutcome')
    assert 'error' not in outcome, outcome
    return outcome.get('value')


def test_four_ocr_drawings_keep_options_private_references_and_saved_layouts(browser):
    source = _source(browser, "single-ocr-source")
    assets = []
    for label in "ABCD":
        path = "/static/uploads/ocr-option-" + label + ".png"
        reference = "/static/uploads/ocr-reference-" + label + ".png"
        for url in (path, reference):
            Image.new("RGB", (600, 300), "navy").save(browser.root / url.lstrip("/"))
        assets.append({"id": "ocr-" + label, "image_path": path,
                       "reference_image_path": reference,
                       "tikz_code": r"\begin{tikzpicture}\draw (0,0)--(2,1);\end{tikzpicture}"})
    text = r"选择正确图象。\begin{choices}" + "\n".join(
        r"\item ![图" + str(i + 1) + "](" + asset["image_path"] + ")"
        for i, asset in enumerate(assets)) + r"\end{choices}"
    reply = {"status": "success", "latex": text, "image_path": source, "confidence": .99,
             "tikz_assets": assets, "image_layouts": {asset["image_path"]: {"size": "small", "align": "left"} for asset in assets},
             "illustrations": [{"slot": "图" + str(i + 1), "placeholder": "[插图待补: 图" + str(i + 1) + "]",
                                "status": "tikz", "image_path": asset["image_path"],
                                "review": _verified_review(),
                                "preview_review": _verified_review(evidence="编译预览的图形、标注和原图一致。")}
                               for i, asset in enumerate(assets)]}
    _editor(browser, "single-ocr-four-options")
    try:
        _ocr(browser, reply)
        browser.command("wait", "--fn", "document.querySelectorAll('#contentPreview .choices-content img').length===4")
        for width, height in ((1600, 1100), (375, 812)):
            browser.command("set", "viewport", str(width), str(height))
            browser.settle()
            state = browser.evaluate("""(() => ({
                assets:TikzState.contentAssets.map(a=>({path:a.image_path,reference:a.reference_image_path})),
                visible:uploadedImages,
                reviewLabels:[...document.querySelectorAll('#contentOcrFigures > div > span')].map(label=>label.textContent),
                options:[...document.querySelectorAll('#contentPreview .choices-item')].map(item=>({
                  path:item.querySelector('img').getAttribute('src'),
                  key:item.querySelector('img').dataset.editorImageKey,
                  fits:item.querySelector('img').getBoundingClientRect().right<=item.getBoundingClientRect().right+1
                }))
            }))()""")
            assert [item["path"] for item in state["options"]] == [asset["image_path"] for asset in assets]
            assert all(item["key"] and item["fits"] for item in state["options"]), state
            assert len(state["assets"]) == 4
            assert len(state['reviewLabels']) == 4
            assert all(label.endswith('绘图已视觉核对') for label in state['reviewLabels'])
            assert not any("reference" in url or "source" in url for url in state["visible"])
        browser.command("set", "viewport", "1600", "1100")
        _click(browser, "#contentPreview .choices-item:first-child img")
        _click(browser, "#editorFigureLayoutPopover button[onclick*=\"'large'\"]")
        _click(browser, "#contentPreview .choices-item:first-child img")
        _click(browser, "#editorFigureLayoutPopover button[aria-label='插图位置：当前位置居右']")
        assert browser.evaluate("FigureLayoutState.imageLayouts['ocr-option-A.png']") == {"align": "right", "size": "large"}
        assert browser.evaluate("saveQuestion(true)") is True
        stored = browser.evaluate("fetch('/api/questions/'+EditorState.questionId).then(r=>r.json())")
        assert stored["image_layouts"]["ocr-option-A.png"] == {"align": "right", "size": "large"}
        assert len(stored["content_tikz_assets"]) == 4
        assert all(asset["reference_image_path"] for asset in stored["content_tikz_assets"])
        assert not any("reference" in url for url in stored["image_paths"])
        browser.evaluate("selectQuestion(%s,{silent:true}); openQuestionEditorModal('content'); true" % json.dumps(stored))
        browser.command("wait", "--fn", "!questionDetailLoading && TikzState.contentAssets.length===4")
        assert browser.evaluate("FigureLayoutState.imageLayouts['ocr-option-A.png'].size") == "large"
    finally:
        _restore_fetch(browser)
        browser.command("set", "viewport", "1600", "1100")


def test_disabled_tikz_crop_replaces_only_selected_option_and_keeps_table_slot(browser):
    source = _source(browser, "single-ocr-manual-source")
    text = (r"选出图象。\begin{choices}\item [插图待补: 图1]\item $2$\end{choices}"
            r"\begin{tabular}{cc}[插图待补: 图2] & $x=1$\end{tabular}")
    reply = {"status": "success", "latex": text, "image_path": source,
             "illustrations": [{"slot": "图1", "placeholder": "[插图待补: 图1]", "status": "pending"},
                               {"slot": "图2", "placeholder": "[插图待补: 图2]", "status": "pending"}]}
    _editor(browser, "single-ocr-manual")
    try:
        _ocr(browser, reply, skip=True)
        assert browser.evaluate("window.__singleOcrSkip") == "true"
        browser.command("set", "viewport", "375", "812")
        _click(browser, "#contentOcrFigures > div:first-of-type button")
        browser.command("wait", "--fn", "document.getElementById('singleOcrCropCanvas').width===800")
        assert browser.evaluate("document.getElementById('singleOcrCropModal').scrollWidth<=window.innerWidth")
        # Exercise the canvas pointer handlers in its real scaled viewport.
        browser.evaluate("""(() => {
            const canvas=document.getElementById('singleOcrCropCanvas'),rect=canvas.getBoundingClientRect();
            const event=(type,x,y)=>new PointerEvent(type,{pointerId:1,clientX:rect.left+x*rect.width,clientY:rect.top+y*rect.height,bubbles:true});
            // Synthetic pointer events cannot own browser pointer capture.
            const capture=canvas.setPointerCapture; canvas.setPointerCapture=()=>{};
            canvas.dispatchEvent(event('pointerdown',.1,.1));
            canvas.dispatchEvent(event('pointermove',.4,.5));
            canvas.dispatchEvent(event('pointerup',.4,.5));
            canvas.setPointerCapture=capture;
            return true;
        })()""")
        _click(browser, "#singleOcrCropSave")
        browser.command("wait", "--fn", "!document.getElementById('singleOcrCropModal')")
        state = browser.evaluate("({text:document.getElementById('editContent').value,paths:uploadedImages,assets:TikzState.contentAssets})")
        assert "[插图待补: 图1]" not in state["text"]
        assert "[插图待补: 图2]" in state["text"]
        assert len(state["paths"]) == 1 and state["assets"] == []
        path = state["paths"][0]
        assert state["text"].index(path) < state["text"].index(r"\end{choices}")
        with Image.open(browser.root / path.lstrip("/")) as crop:
            assert crop.size == (240, 200)
            assert crop.getpixel((120, 100))[:3] == (0, 0, 128)
        assert browser.evaluate("document.querySelector('#contentPreview .choices-content img').dataset.editorImageKey")
    finally:
        _restore_fetch(browser)
        browser.command("set", "viewport", "1600", "1100")


def test_edits_while_ocr_runs_remain_until_explicit_load(browser):
    source = _source(browser, "single-ocr-delayed-source")
    reply = {"status": "success", "latex": "识别正文 [插图待补: 图1]", "image_path": source,
             "illustrations": [{"slot": "图1", "placeholder": "[插图待补: 图1]", "status": "unresolved",
                                "review": {"status": "unavailable", "corrected": False}}],
             "illustration_review": {"status": "unavailable", "verified": 0, "total": 1}}
    _editor(browser, "single-ocr-late-response")
    try:
        _ocr(browser, reply, delay=True)
        browser.command("fill", "#editContent", "请求期间的新编辑")
        browser.evaluate("window.__singleOcrRelease(); true")
        browser.command("wait", "--fn", "!document.getElementById('contentOcrOutputBox').classList.contains('hidden')")
        assert browser.evaluate("document.getElementById('editContent').value") == "请求期间的新编辑"
        assert "载入识别结果" in browser.evaluate("document.getElementById('contentOcrFigures').textContent")
        assert "已视觉核对" not in browser.evaluate("document.getElementById('contentOcrFigures').textContent")
        assert browser.evaluate("TikzState.contentAssets.length") == 0
    finally:
        _restore_fetch(browser)


def test_disabled_tikz_automatic_crops_keep_four_options_and_table_layouts_after_save(browser):
    """Deterministic OCR output; browser rendering and save/reload use the app."""
    folder = browser.root / 'static' / 'uploads'
    source_name = 'single-ocr-auto-crop-source.png'
    image = Image.new('RGB', (900, 600), 'white')
    draw = ImageDraw.Draw(image)
    rectangles = [(50, 50, 250, 200), (350, 50, 550, 200), (650, 50, 850, 200),
                  (50, 300, 250, 450), (350, 300, 550, 450)]
    colors = ['navy', 'green', 'maroon', 'purple', 'teal']
    paths = []
    for index, (rectangle, color) in enumerate(zip(rectangles, colors), 1):
        draw.rectangle(rectangle, fill=color)
        name = 'ocr-auto-crop-' + str(index) + '.png'
        image.crop(rectangle).save(folder / name)
        paths.append('/static/uploads/' + name)
    image.save(folder / source_name)
    text = r'选择对应图象。\begin{choices}' + '\n'.join(
        r'\item ![图' + str(index + 1) + '](' + path + ')' for index, path in enumerate(paths[:4])) + r'\end{choices}'
    text += r'\begin{tabular}{cc} ![图5](' + paths[4] + r') & $x=1$\end{tabular}'
    reply = {'status': 'success', 'latex': text, 'image_path': '/static/uploads/' + source_name,
             'tikz_assets': [], 'image_layouts': {path: {'align': 'left', 'size': 'small'} for path in paths},
             'illustrations': [{'slot': '图' + str(index + 1), 'placeholder': '[插图待补: 图' + str(index + 1) + ']',
                               'status': 'cropped', 'image_path': path, 'reference_image_path': '',
                               'review': _verified_review()}
                              for index, path in enumerate(paths)]}
    _editor(browser, 'single-ocr-auto-crop')
    try:
        _ocr(browser, reply, skip=True)
        assert browser.evaluate('window.__singleOcrSkip') == 'true'
        browser.command('wait', '--fn', "document.querySelectorAll('#contentPreview .choices-content img').length===4 && document.querySelectorAll('#contentPreview table img').length===1")
        for width, height in ((1600, 1100), (375, 812)):
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            state = browser.evaluate("""(() => ({
                text:document.getElementById('editContent').value,
                assets:TikzState.contentAssets,
                references:TikzState.referencePaths(),
                visible:uploadedImages,
                pictures:[...document.querySelectorAll('#contentPreview .choices-content img, #contentPreview table img')].map(image=>({
                  path:image.getAttribute('src'),key:image.dataset.editorImageKey,
                  fits:image.getBoundingClientRect().right<=image.closest('.choices-item,td').getBoundingClientRect().right+1
                })),
                pendingButtons:[...document.querySelectorAll('#contentOcrFigures button')].filter(button=>button.textContent==='截图补入此处').length,
                statuses:document.getElementById('contentOcrFigures').textContent
            }))()""")
            assert state['text'] == text
            assert state['assets'] == [] and state['references'] == []
            assert state['visible'] == paths
            assert [picture['path'] for picture in state['pictures']] == paths
            assert all(picture['key'] and picture['fits'] for picture in state['pictures']), state
            assert state['pendingButtons'] == 0
            assert state['statuses'].count(' · 原图截图') == 5
            assert state['statuses'].count('原图裁片已视觉核对') == 5
        browser.command('set', 'viewport', '1600', '1100')
        assert _save_with_duplicate_review(browser) is True
        stored = browser.evaluate("fetch('/api/questions/'+EditorState.questionId).then(r=>r.json())")
        assert re.findall(r'!\[图\d\]\(([^)]+)\)', stored['content']) == paths
        choice_start, choice_end = stored['content'].index(r'\begin{choices}'), stored['content'].index(r'\end{choices}')
        assert all(choice_start < stored['content'].index(path) < choice_end for path in paths[:4])
        assert stored['content'].index(paths[4]) > stored['content'].index(r'\begin{tabular}')
        assert stored['content_tikz_assets'] == []
        assert stored['image_paths'] == paths
        assert stored['image_layouts'] == {path.split('/')[-1]: {'align': 'left', 'size': 'small'} for path in paths}
        browser.evaluate('selectQuestion(%s,{silent:true}); openQuestionEditorModal(\'content\'); true' % json.dumps(stored))
        browser.command('wait', '--fn', '!questionDetailLoading && document.querySelectorAll("#contentPreview img").length===5')
        assert browser.evaluate('TikzState.contentAssets.length') == 0
        assert browser.evaluate('uploadedImages') == paths
        assert browser.evaluate('FigureLayoutState.imageLayouts') == stored['image_layouts']
    finally:
        _restore_fetch(browser)
        browser.command('set', 'viewport', '1600', '1100')


def test_visual_review_proof_and_uncertain_slots_remain_distinct_on_desktop_and_mobile(browser):
    source = _source(browser, 'single-ocr-review-source')
    folder = browser.root / 'static' / 'uploads'
    paths = ['/static/uploads/ocr-review-verified.png', '/static/uploads/ocr-review-corrected.png']
    with Image.open(folder / source.split('/')[-1]) as original:
        original.crop((80, 50, 320, 250)).save(folder / paths[0].split('/')[-1])
        original.crop((460, 280, 720, 450)).save(folder / paths[1].split('/')[-1])
    text = (r'核对图象。\begin{choices}\item ![图1](' + paths[0] +
            r')\item ![图2](' + paths[1] +
            r')\item [插图待补: 图3]\item [插图待补: 图4]\end{choices}')
    reply = {'status': 'success', 'latex': text, 'image_path': source, 'tikz_assets': [],
             'image_layouts': {path: {'align': 'left', 'size': 'small'} for path in paths},
             'illustration_review': {'status': 'partial', 'calls': 2, 'verified': 2, 'total': 4, 'corrected': 1},
             'illustrations': [
                 {'slot': '图1', 'placeholder': '[插图待补: 图1]', 'status': 'cropped', 'image_path': paths[0],
                  'review': _verified_review()},
                 {'slot': '图2', 'placeholder': '[插图待补: 图2]', 'status': 'cropped', 'image_path': paths[1],
                  'review': _verified_review(corrected=True)},
                 {'slot': '图3', 'placeholder': '[插图待补: 图3]', 'status': 'unresolved',
                  'bbox': {'left': 100, 'top': 100, 'right': 400, 'bottom': 500},
                  'review': {'status': 'unverified', 'corrected': False, 'evidence': '标注未完整保留。'},
                  'message': '裁图核对未通过，请从原图手动截图。'},
                 {'slot': '图4', 'placeholder': '[插图待补: 图4]', 'status': 'unresolved',
                  'review': {'status': 'unavailable', 'corrected': False},
                  'message': '视觉核对暂不可用，请手动截图。'}],
             'warnings': ['图3 的裁图核对未通过，图4 无法完成核对；已保留对应待补位置。']}
    _editor(browser, 'single-ocr-reviewed-and-uncertain')
    try:
        _ocr(browser, reply, skip=True)
        browser.command('wait', '--fn', 'document.querySelectorAll("#contentPreview .choices-content img").length===2')
        for width, height in ((1600, 1100), (375, 812)):
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            state = browser.evaluate("""(() => ({
                text:document.getElementById('editContent').value,
                labels:[...document.querySelectorAll('#contentOcrFigures > div > span')].map(label=>label.textContent),
                cropButtons:[...document.querySelectorAll('#contentOcrFigures > div button')].map(button=>button.textContent),
                visible:uploadedImages,
                assets:TikzState.contentAssets,
                fits:[...document.querySelectorAll('#contentOcrFigures button')].every(button=>
                  button.getBoundingClientRect().right<=document.getElementById('contentOcrFigures').getBoundingClientRect().right+1)
            }))()""")
            assert state['text'] == text
            assert state['labels'] == ['图1 · 原图截图 · 原图裁片已视觉核对',
                                       '图2 · 原图截图 · 已修正裁图并核对',
                                       '图3 · 待截图 · 视觉核对未通过',
                                       '图4 · 待截图 · 视觉核对暂不可用']
            assert state['cropButtons'] == ['截图补入此处', '截图补入此处']
            assert state['visible'] == paths and state['assets'] == []
            assert state['fits']
        _click(browser, '#contentOcrFigures > div:nth-of-type(3) button')
        browser.command('wait', '--fn', 'document.getElementById("singleOcrCropCanvas").width===800')
        assert browser.evaluate('document.getElementById("singleOcrCropModal").scrollWidth<=window.innerWidth')
        browser.command('press', 'Escape')
        browser.command('wait', '--fn', '!document.getElementById("singleOcrCropModal")')
        assert browser.evaluate('document.getElementById("editContent").value') == text
    finally:
        _restore_fetch(browser)
        browser.command('set', 'viewport', '1600', '1100')


def test_unverified_wide_image_bbox_starts_blank_and_manual_crop_maps_retina_pixels(browser):
    """Regression for shifted B/C proposals on a short 1009×157 source."""
    folder = browser.root / 'static' / 'uploads'
    source_name = 'single-ocr-short-retina-source.png'
    source = Image.new('RGB', (1009, 157), 'white')
    draw = ImageDraw.Draw(source)
    draw.line([(40, 138), (91, 74), (208, 74), (156, 138), (40, 138)], fill='black', width=3)
    draw.rectangle((321, 70, 474, 137), outline='black', width=3)
    draw.line([(591, 137), (712, 66), (712, 137), (591, 137)], fill='black', width=3)
    draw.line([(872, 63), (990, 63), (931, 145), (872, 63)], fill='black', width=3)
    source.save(folder / source_name)
    cases = [('B', {'left': 320, 'top': 385, 'right': 490, 'bottom': 775}, (317, 66, 478, 142)),
             ('C', {'left': 603, 'top': 333, 'right': 752, 'bottom': 776}, (587, 62, 716, 142))]
    try:
        for viewport_width, viewport_height in ((1600, 1100), (375, 812)):
            for label, bbox, region in cases:
                _editor(browser, 'single-ocr-blank-' + label + '-' + str(viewport_width))
                _ocr(browser, {'status': 'success', 'latex': '完整题干 [插图待补: 图1]',
                               'image_path': '/static/uploads/' + source_name,
                               'illustrations': [{'slot': '图1', 'placeholder': '[插图待补: 图1]', 'status': 'unresolved',
                                                 'bbox': bbox, 'review': {'status': 'unverified', 'corrected': False}}]}, skip=True)
                browser.command('set', 'viewport', str(viewport_width), str(viewport_height), '2')
                _click(browser, '#contentOcrFigures > div:first-of-type button')
                browser.command('wait', '--fn', 'document.getElementById("singleOcrCropCanvas").width===1009')
                state = browser.evaluate("""(() => {
                    const c=document.getElementById('singleOcrCropCanvas'),r=c.getBoundingClientRect(),pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                    let hasBlue=false;
                    for(let i=0;i<pixels.length;i+=4) if(pixels[i]===99&&pixels[i+1]===102&&pixels[i+2]===241) hasBlue=true;
                    return {hasBlue,disabled:document.getElementById('singleOcrCropSave').disabled,
                            status:document.getElementById('singleOcrCropStatus').textContent,
                            canvas:[c.width,c.height],rect:r.toJSON(),dpr:devicePixelRatio};
                })()""")
                assert state['canvas'] == [1009, 157] and state['dpr'] == 2
                assert not state['hasBlue'] and state['disabled']
                assert state['status'] == '原图框未确认，请手动框选完整插图'
                # The CLI mouse accepts integral CSS coordinates. PointerEvents
                # retain the exact source-pixel endpoints at fractional scaling.
                browser.evaluate("""(() => {
                    const region=%s,c=document.getElementById('singleOcrCropCanvas'),r=c.getBoundingClientRect();
                    const event=(type,x,y)=>new PointerEvent(type,{pointerId:1,
                        clientX:r.left+x*r.width/1009,clientY:r.top+y*r.height/157,bubbles:true});
                    const capture=c.setPointerCapture;c.setPointerCapture=()=>{};
                    c.dispatchEvent(event('pointerdown',region[0],region[1]));
                    c.dispatchEvent(event('pointermove',region[2],region[3]));
                    c.dispatchEvent(event('pointerup',region[2],region[3]));
                    c.setPointerCapture=capture;
                    return true;
                })()""" % json.dumps(region))
                assert browser.evaluate('document.getElementById("singleOcrCropSave").disabled') is False
                _click(browser, '#singleOcrCropSave')
                browser.command('wait', '--fn', '!document.getElementById("singleOcrCropModal")')
                path = browser.evaluate('uploadedImages[0]')
                with Image.open(folder / path.split('/')[-1]) as uploaded:
                    expected = source.crop(region)
                    assert uploaded.size == expected.size
                    assert uploaded.convert('RGB').tobytes() == expected.tobytes()
                _restore_fetch(browser)
    finally:
        if browser.evaluate('typeof window.__singleOcrOriginalFetch === "function"'):
            _restore_fetch(browser)
        browser.command('set', 'viewport', '1600', '1100', '1')
