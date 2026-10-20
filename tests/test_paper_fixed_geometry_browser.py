"""Actual Chromium geometry on a disposable app, including scaled drag targets."""
from test_preview_browser import browser, pytestmark  # noqa: F401


def geometry(browser):
    return browser.evaluate('''(() => {
        const sheet=document.getElementById('a4PaperPreviewSheet');
        return {available:sheet.clientWidth, zoom:Number(sheet.dataset.paperPreviewZoom),
            pages:[...sheet.querySelectorAll(':scope > .a4-paper-sheet')].map(page=>{
                const style=getComputedStyle(page),q=page.querySelector('.paper-q-item');
                return {width:page.offsetWidth, height:page.offsetHeight,
                    visualWidth:page.getBoundingClientRect().width,
                    padding:[style.paddingTop,style.paddingRight,style.paddingBottom,style.paddingLeft],
                    font:q?getComputedStyle(q).fontSize:null,
                    columns:[...page.querySelectorAll('.choices-grid')].map(grid=>grid.dataset.choiceColumns),
                    ids:[...page.querySelectorAll('.paper-q-item')].map(q=>Number(q.dataset.qid))};
            })};
    })()''')


def test_fixed_page_wraps_identically_while_zooming_and_scaled_drag_keeps_cart_order(browser):
    browser.command('set','viewport','1600','1100')
    browser.evaluate("setPaperMobilePane('preview');MathBankBrowserChecks.seed(15,2);true")
    browser.settle()
    wide=geometry(browser)
    assert len(wide['pages']) >= 2
    original=[page['ids'] for page in wide['pages']]
    columns=[page['columns'] for page in wide['pages']]
    for viewport in [('1100','900'),('375','812')]:
        browser.command('set','viewport',*viewport)
        browser.settle()
        narrow=geometry(browser)
        assert 0 < narrow['zoom'] <= 1
        assert [page['ids'] for page in narrow['pages']] == original
        assert [page['columns'] for page in narrow['pages']] == columns
        assert all(page['width']==794 and page['height']==1123 for page in narrow['pages'])
        assert all(page['padding']==['96px']*4 and page['font']=='14px' for page in narrow['pages'])
        assert all(page['visualWidth']<=narrow['available']+1 for page in narrow['pages'])
    browser.command('screenshot',str(browser.root/'a4-fixed-narrow.png'))
    browser.evaluate("document.documentElement.classList.add('dark');true")
    browser.settle()
    dark=geometry(browser)
    assert [page['ids'] for page in dark['pages']] == original
    assert all(page['padding']==['96px']*4 for page in dark['pages'])
    result=browser.evaluate('MathBankBrowserChecks.drag(1,15)')
    assert result == list(range(2,16))+[1]
    browser.settle()
    assert browser.evaluate('MathBankBrowserChecks.drag(2,1,false,true)') == result
    browser.settle()
    browser.evaluate("document.documentElement.classList.remove('dark');true")
    browser.command('set','viewport','1600','1100')
    browser.settle()
    assert all(page['width']==794 for page in geometry(browser)['pages'])
    browser.command('screenshot',str(browser.root/'a4-fixed-desktop.png'))
