"""Real settings round trips in the source-only browser fixture; no AI or questions written."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


def question_data_snapshot(root):
    path = root / 'math_question_bank.db'
    assert root.resolve() != Path(__file__).resolve().parents[1]
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        data = [(name, connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall())
                for name in ('questions', 'question_curriculums', 'question_knowledge_points') if name in tables]
    return hashlib.sha256(repr(data).encode()).hexdigest()


@pytest.mark.parametrize('width,height', [(1440, 1000), (375, 812)])
def test_classification_quality_save_readback_and_unsaved_editor_survive(browser, tmp_path, width, height):
    browser.command('reload')
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('wait', '--fn', "typeof window.openNewQuestionEditor==='function' && !!window.MathBankLimits")
    before = question_data_snapshot(browser.root)
    browser.evaluate(r"""
      window.__policyOriginalFetch=window.fetch.bind(window);window.__policyForbiddenRequests=[];window.__policySavedModes=[];
      window.fetch=(url,options={})=>{
        const address=String(url),method=String(options.method||'GET').toUpperCase();
        if(address.startsWith('/api/ai/')||(address.startsWith('/api/questions')&&method!=='GET')){
          window.__policyForbiddenRequests.push({url:address,method});
          return Promise.reject(Error('classification and question writes are disabled in acceptance'));
        }
        if(address==='/api/settings/save')window.__policySavedModes.push(options.body.get('classify_quality'));
        return window.__policyOriginalFetch(url,options);
      };
      selectWorkspace('bank','题库管理');openNewQuestionEditor();true
    """)
    browser.command('wait', '--fn', "!document.getElementById('editorSection').classList.contains('hidden')")
    browser.command('click', '[data-editor-panel-target="classification"]')
    draft = f'分类设置验收 {width}：尚未入库的教师来源修改'
    browser.command('fill', '#editSource', draft)
    assert browser.evaluate('isEditorModified()') is True
    identity = browser.evaluate('({questionId:EditorState.questionId,draftId:EditorState.draftId})')
    current = browser.evaluate("fetch('/api/settings').then(r=>r.json()).then(s=>s.classification_policy.mode)")
    assert current in ('quick', 'precise')
    browser.evaluate('openSettingsModal();true')
    browser.command('wait', '--fn', f"!document.getElementById('settingsModal').classList.contains('hidden') && document.getElementById('classifyQuality').value==='{current}'")
    browser.command('scrollintoview', '#classifyQuality')
    browser.command('snapshot', '-i')
    assert browser.evaluate("[...document.getElementById('classifyQuality').options].map(o=>o.value)") == ['quick', 'precise']
    assert browser.evaluate("(()=>{const r=document.getElementById('classifyQuality').getBoundingClientRect();return r.width>0&&r.height>0&&r.left>=0&&r.right<=innerWidth+1;})()")
    browser.command('select', '#classifyQuality', 'quick')
    browser.command('select', '#classifyQuality', 'precise')
    browser.command('screenshot', str(tmp_path / f'classification-policy-precise-{width}.png'))
    browser.command('click', '#btnSettingsSave')
    browser.command('wait', '--fn', "window.__policySavedModes.length===1 && document.getElementById('settingsModal').classList.contains('hidden')")
    readback = browser.evaluate("fetch('/api/settings').then(r=>r.json()).then(s=>({policy:s.classification_policy,limits:s.processing_limits}))")
    assert readback['policy']['mode'] == 'precise'
    assert readback['policy']['thinking_requested'] is True
    assert browser.evaluate('window.__policySavedModes') == ['precise']
    assert 'MATHBANK_CLASSIFY_MODE=precise' in (browser.root / '.env').read_text()
    assert browser.evaluate("document.getElementById('editSource').value") == draft
    assert browser.evaluate('isEditorModified()') is True
    assert browser.evaluate('({questionId:EditorState.questionId,draftId:EditorState.draftId})') == identity
    browser.evaluate('openSettingsModal();true')
    browser.command('wait', '--fn', "!document.getElementById('settingsModal').classList.contains('hidden') && document.getElementById('classifyQuality').value==='precise'")
    browser.command('click', '#settingsModal button[data-modal-close]')
    browser.command('wait', '--fn', "document.getElementById('settingsModal').classList.contains('hidden')")
    assert browser.evaluate("document.getElementById('editSource').value") == draft
    limits = browser.evaluate('({...window.MathBankLimits})')
    injected = browser.evaluate('window.__processingLimits')
    for key, value in limits.items():
        assert value == injected[key] == readback['limits'][key]
    expected_mb = limits['image_upload_bytes'] / (1024 * 1024)
    assert browser.evaluate("document.getElementById('answerTikzReferenceLimit').textContent") == f'可选 · 最大 {expected_mb:g}MB'
    assert browser.evaluate('window.__policyForbiddenRequests') == []
    assert question_data_snapshot(browser.root) == before
    browser.evaluate('window.fetch=window.__policyOriginalFetch;true')
