"""Real local export blocking and persisted preflight history, without model calls."""
import json
from pathlib import Path

from PIL import Image
from test_preview_browser import browser, pytestmark  # noqa: F401


def test_missing_image_blocks_bundle_and_report_survives_workspace_redraw(browser, tmp_path):
    path = browser.root / 'static' / 'uploads' / 'preflight-browser.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (120, 80), 'white').save(path)
    question = browser.evaluate(r"""(async () => {
        const form = new FormData();
        form.set('content', '预检浏览器合成题。\n\n![合成图](/static/uploads/preflight-browser.png)');
        form.set('question_type', 'detailed_answer');
        form.set('difficulty', 'normal');
        const response = await fetch('/api/questions', {method:'POST', body:form});
        const data=await response.json();
        if(!response.ok)throw new Error(JSON.stringify(data));
        return data.question;
    })()""")
    path.unlink()  # Only this disposable fixture asset is removed.
    browser.evaluate("""(() => {
        const q=%s;
        PaperStore.cart=[{id:q.id,score:5,solution_space:0}];
        PaperStore.questionsMap[q.id]=q;
        Object.assign(PaperStore.meta,{paper_type:'exam',show_secret:false,show_notice:false});
        localStorage.removeItem('mathbank_paper_export_preflights');
        renderPaperCanvas();
        return true;
    })()""" % json.dumps(question))
    browser.settle()
    browser.evaluate('exportPaperBundle().then(()=>true)')
    result = browser.evaluate("""(() => {
        const history=JSON.parse(localStorage.getItem('mathbank_paper_export_preflights')||'[]');
        return {history,text:document.getElementById('paperExportPreflightStatus')?.textContent,
          cart:PaperStore.cart.map(q=>q.id)};
    })()""")
    assert len(result['history']) == 1 and result['history'][0]['status'] == 'blocked'
    assert result['history'][0]['id'] in result['text']
    assert result['cart'] == [question['id']]
    browser.evaluate('renderPaperCanvas();true')
    browser.settle()
    selector = '#paperExportPreflightStatus button'
    browser.command('scrollintoview', selector)
    browser.settle()
    browser.command('click', selector)
    browser.command('wait', '--fn', "document.querySelector('#paperExportPreflightStatus pre')?.textContent.trim().startsWith('{')")
    report = json.loads(browser.evaluate("document.querySelector('#paperExportPreflightStatus pre').textContent"))
    assert report['id'] == result['history'][0]['id']
    assert report['errors'][0]['question_id'] == question['id']
    assert report['errors'][0]['field'] == 'content'
    assert report['status'] == 'blocked' and report['counts']['errors'] >= 1
    assert report['errors'][0]['reference'].endswith('preflight-browser.png')
    assert not any('.pdf' in str(p) for p in browser.root.glob('*.pdf'))
    (tmp_path / 'preflight-browser-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    browser.command('screenshot', str(tmp_path / 'preflight-browser-report.png'))
