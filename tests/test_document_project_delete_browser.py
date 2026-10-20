"""Opt-in deletion feedback in a source-only browser, with synthetic projects."""
import os

import pytest

from test_preview_browser import browser  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in isolated browser")


def test_delete_progress_immediate_removal_and_late_list_does_not_restore_row(browser):
    browser.command("network", "route", "**/api/ai/**", "--abort")
    assert browser.evaluate(r"""(()=>{
        window.MathBankDocumentImportSession.clear();
        const original=window.fetch;
        const id='delete-browser-only',now=Date.now()/1000;
        const row={project_id:id,title:'删除回归临时工程',status:'completed',question_count:1,
            expires_at:now+604800,total_bytes:12345};
        const response=data=>new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
        window.__deleteBrowser={row,phase:'seed',confirm:0,deletes:0};
        window.confirm=()=>{window.__deleteBrowser.confirm++;return true};
        window.fetch=(input,options={})=>{
            const path=new URL(String(input?.url||input),location.href).pathname;
            const method=String(options.method||'GET').toUpperCase(),test=window.__deleteBrowser;
            if(path==='/api/document-projects'){
                if(test.phase==='seed')return Promise.resolve(response({projects:[row]}));
                return new Promise(resolve=>{test.resolveList=()=>resolve(response({projects:[row]}))});
            }
            if(path==='/api/document-projects/'+id&&method==='DELETE'){
                test.deletes++;
                return new Promise(resolve=>{test.resolveDelete=()=>resolve(response({status:'success'}))});
            }
            if(path.startsWith('/api/ai/')||path.startsWith('/api/upload/'))throw Error('No model requests in deletion test');
            return original(input,options);
        };
        selectWorkspace('import','导入中心');document.getElementById('documentProjectsPanel').open=true;
        window.MathBankDocumentImportSession.refreshProjects();return true;
    })()""")
    browser.command("wait", "--fn", "!!document.querySelector('#documentProjectsList [data-project-id=\"delete-browser-only\"]')")
    # Keep a stale list GET pending while the DELETE is submitted and confirmed.
    browser.evaluate("window.__deleteBrowser.phase='late';window.MathBankDocumentImportSession.refreshProjects();true")
    browser.command("wait", "--fn", "typeof window.__deleteBrowser.resolveList==='function'")
    browser.evaluate("document.querySelector('#documentProjectsList [data-project-id=\"delete-browser-only\"] button:last-child').click();true")
    browser.command("wait", "--fn", "window.__deleteBrowser.deletes===1")
    assert browser.evaluate(r"""(()=>{const row=document.querySelector('#documentProjectsList [data-project-id="delete-browser-only"]');
        return row.textContent.includes('正在删除')&&[...row.querySelectorAll('button')].every(button=>button.disabled)})()""")
    browser.evaluate("window.__deleteBrowser.resolveDelete();true")
    browser.command("wait", "--fn", "!document.querySelector('#documentProjectsList [data-project-id=\"delete-browser-only\"]')")
    browser.evaluate("window.__deleteBrowser.resolveList();true")
    browser.command("wait", "--fn", "!document.getElementById('documentProjectsList').textContent.includes('正在读取')")
    assert browser.evaluate("!document.querySelector('#documentProjectsList [data-project-id=\"delete-browser-only\"]')")
    assert browser.evaluate("window.__deleteBrowser.confirm===1&&window.__deleteBrowser.deletes===1")
