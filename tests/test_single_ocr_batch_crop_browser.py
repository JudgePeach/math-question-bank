"""Real isolated Canvas cropping and safe uploads; OCR is a deterministic local stub."""
import re

from PIL import Image, ImageDraw

from test_preview_browser import browser, pytestmark  # noqa: F401
from test_single_ocr_browser import _click, _editor, _ocr, _restore_fetch


def test_three_batch_presets_keep_native_pixels_order_and_source_text_at_narrow_width(browser, tmp_path):
    colors = [(255, 0, 0), (0, 128, 0), (0, 0, 255), (255, 165, 0)]
    for mode, rows, columns in [('2x2', 2, 2), ('1x4', 1, 4), ('4x1', 4, 1)]:
        source = '/static/uploads/batch-grid-' + mode + '.png'
        image = Image.new('RGB', (2000, 1000), 'white')
        draw = ImageDraw.Draw(image)
        for index, color in enumerate(colors):
            row, column = divmod(index, columns)
            draw.rectangle((column * 2000 // columns, row * 1000 // rows,
                            (column + 1) * 2000 // columns - 1, (row + 1) * 1000 // rows - 1), fill=color)
        image.save(browser.root / source.lstrip('/'))
        _editor(browser, 'batch-pixels-' + mode)
        original = r'已有多选题正文\begin{choices}\item $1$\item $2$\end{choices}'
        try:
            _ocr(browser, {'status': 'success', 'latex': original, 'image_path': source})
            browser.command('set', 'viewport', '375', '812')
            browser.evaluate("""(() => {
                const textarea=document.getElementById('editContent');
                textarea.setSelectionRange(0,5);
                document.getElementById('editQType').value='multi_choice';
                [...document.querySelectorAll('#contentOcrFigures button')].find(b=>b.textContent==='从原图追加截图').click();
                return true;
            })()""")
            browser.command('wait', '--fn', "document.getElementById('singleOcrCropCanvas').width===1600")
            browser.command('select', '#singleOcrCropMode', mode)
            assert browser.evaluate("document.getElementById('singleOcrCropSave').disabled")
            browser.evaluate("""(() => {
                const canvas=document.getElementById('singleOcrCropCanvas'),rect=canvas.getBoundingClientRect();
                const event=(type,x,y)=>new PointerEvent(type,{pointerId:1,clientX:rect.left+x*rect.width,clientY:rect.top+y*rect.height,bubbles:true});
                const capture=canvas.setPointerCapture;canvas.setPointerCapture=()=>{};
                canvas.dispatchEvent(event('pointerdown',0,0));
                canvas.dispatchEvent(event('pointermove',1,1));
                canvas.dispatchEvent(event('pointerup',1,1));
                canvas.setPointerCapture=capture;
                return true;
            })()""")
            assert browser.evaluate("!document.getElementById('singleOcrCropSave').disabled")
            assert browser.evaluate("document.getElementById('singleOcrCropModal').scrollWidth<=window.innerWidth")
            browser.command('screenshot', str(tmp_path / ('batch-grid-' + mode + '-375px.png')))
            _click(browser, '#singleOcrCropSave')
            browser.command('wait', '--fn', "!document.getElementById('singleOcrCropModal')")
            result = browser.evaluate("({text:document.getElementById('editContent').value,type:document.getElementById('editQType').value,layouts:FigureLayoutState.imageLayouts})")
            paths = re.findall(r'!\[([ABCD])\]\(([^)]+)\)', result['text'])
            assert [label for label, _ in paths] == list('ABCD'), result
            assert result['text'].endswith(original), result
            assert result['type'] == 'multi_choice'
            for index, (_, path) in enumerate(paths):
                with Image.open(browser.root / path.lstrip('/')) as crop:
                    assert crop.size == (2000 // columns, 1000 // rows)
                    assert crop.getpixel((crop.width // 2, crop.height // 2))[:3] == colors[index]
                assert result['layouts'][path.rsplit('/',1)[-1]]['size'] == 'small'
        finally:
            _restore_fetch(browser)
            browser.command('set', 'viewport', '1600', '1100')
