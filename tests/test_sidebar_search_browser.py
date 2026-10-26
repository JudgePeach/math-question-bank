"""Opt-in full-page search scheduling with an isolated, synthetic-only app."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import pytest

from test_preview_browser import Browser


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1",
                               reason="opt-in actual browser regression")


def _processes():
    result = subprocess.run(["ps", "-axo", "pid=,ppid="], text=True, capture_output=True, check=True)
    return [tuple(map(int, line.split())) for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def search_browser(tmp_path_factory):
    executable = shutil.which("agent-browser")
    assert executable, "Install optional agent-browser and Chromium for this regression"
    root = tmp_path_factory.mktemp("sidebar-search-browser")
    shutil.copy2(ROOT / "main.py", root / "main.py")
    for directory in ("mathbank", "templates", "static"):
        shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "uploads", "test_uploads", ".env*", "*.db*", "*.sqlite*"))
    assert not (root / ".env").exists()
    assert not (root / "math_question_bank.db").exists()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, MATHBANK_LAUNCH_ID=uuid.uuid4().hex)
    env.pop("PYTHONPATH", None)
    browser = Browser(executable, "mathbank-search-" + uuid.uuid4().hex[:12])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    owned_pids = set()
    with (root / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-u", "-m", "uvicorn", "main:app",
                                   "--host", "127.0.0.1", "--port", str(port)],
                                  cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                try:
                    with opener.open(url + "/healthz", timeout=1) as response:
                        if json.load(response).get("ready"):
                            break
                except (OSError, ValueError):
                    pass
                if server.poll() is not None:
                    pytest.fail((root / "server.log").read_text())
                time.sleep(0.1)
            else:
                pytest.fail((root / "server.log").read_text())
            browser.command("open", url)
            browser.command("set", "viewport", "1600", "1100")
            browser.command("wait", "--fn", "typeof setupSidebarSearch === 'function' && typeof selectWorkspace === 'function'")
            pid_file = Path.home() / ".agent-browser" / f"{browser.session}.pid"
            owned_pids.add(int(pid_file.read_text().strip()))
            processes = _processes()
            while True:
                expanded = owned_pids | {pid for pid, parent in processes if parent in owned_pids}
                if expanded == owned_pids:
                    break
                owned_pids = expanded
            yield browser
        finally:
            try:
                subprocess.run([executable, "--session", browser.session, "close"],
                               capture_output=True, timeout=20, check=False)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                remaining = owned_pids & {pid for pid, _ in _processes()}
                sidecars = list((Path.home() / ".agent-browser").glob(f"{browser.session}.*"))
                if not remaining and not sidecars:
                    break
                time.sleep(0.1)
            with socket.socket() as sock:
                sock.settimeout(0.2)
                listening = sock.connect_ex(("127.0.0.1", port)) == 0
            cleanup = {"session": browser.session, "owned_pids": sorted(owned_pids),
                       "remaining_pids": sorted(remaining), "sidecars": [str(p) for p in sidecars],
                       "port_listening": listening, "server_exit_code": server.returncode}
            (root / "cleanup.json").write_text(json.dumps(cleanup, indent=2))
            assert owned_pids and not remaining and not sidecars and not listening, cleanup


def test_real_sidebar_search_debounce_ime_actions_and_unsaved_editor(search_browser, tmp_path):
    browser = search_browser
    seeded = browser.evaluate(r"""
    (async()=>{
      selectWorkspace('bank');
      const ids=[];
      const prefix='SIDEBAR_SCHED_'+Date.now();
      for(let index=0;index<25;index++){
        const form=new FormData();
        form.set('content',prefix+' 合成第 '+index+' 题，求 $x+1$。');
        form.set('question_type','detailed_answer');form.set('difficulty','medium');
        const response=await fetch('/api/questions',{method:'POST',body:form});
        const payload=await response.json();
        if(!response.ok || !payload.question)throw Error(JSON.stringify(payload));
        ids.push(payload.question.id);
      }
      window.__sidebarPrefix=prefix;window.__sidebarCalls=[];
      window.__sidebarOriginalFetch=window.fetch;
      window.fetch=function(url,options={}){
        const target=new URL(typeof url==='string'?url:url.url,location.href);
        if(target.pathname==='/api/questions' && (!options.method || options.method==='GET')){
          const record={query:target.searchParams.get('q')||'',page:target.searchParams.get('page'),
            type:target.searchParams.get('qtype'),signal:options.signal};
          window.__sidebarCalls.push(record);
          if(record.query==='obsolete-response'){
            return window.__sidebarOriginalFetch(url,{...options,signal:undefined}).then(response=>
              new Promise(resolve=>{window.__releaseSidebarOld=()=>resolve(response);}));
          }
        }
        return window.__sidebarOriginalFetch(url,options);
      };
      const input=document.getElementById('searchInput');
      input.value=prefix;input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
      return {ids,prefix};
    })()
    """)
    browser.command("wait", "--fn", "document.querySelectorAll('#questionsList [data-id]').length===20 && !document.getElementById('questionsList').hasAttribute('aria-busy')")
    browser.evaluate("selectQuestion({id:" + str(seeded["ids"][0]) + "},{silent:true}); true")
    browser.command("wait", "--fn", f"EditorState.questionId==={seeded['ids'][0]} && !questionDetailLoading")
    assert browser.evaluate(r"""(()=>{
      const edit=document.getElementById('editContent');edit.value+=' 未保存编辑';
      edit.dispatchEvent(new Event('input',{bubbles:true}));
      window.__sidebarUnsaved=edit.value;window.__sidebarCart=JSON.stringify(PaperStore.cart);
      window.__sidebarCalls=[];return isEditorModified();
    })()""")

    result = browser.evaluate(r"""
    (async()=>{
      const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      const waitFor=async(test)=>{const deadline=performance.now()+8000;while(!test()){
        if(performance.now()>deadline)throw Error('sidebar wait timed out');await sleep(20);}};
      const input=document.getElementById('searchInput');
      const emit=(value,composing=false)=>{input.value=value;input.dispatchEvent(new InputEvent('input',
        {bubbles:true,isComposing:composing,inputType:'insertText',data:value}));};
      const enter=(composing=false)=>input.dispatchEvent(new KeyboardEvent('keydown',
        {key:'Enter',bubbles:true,cancelable:true,isComposing:composing}));
      const settled=()=>!document.getElementById('questionsList').hasAttribute('aria-busy');
      const counts={};
      for(let index=1;index<=8;index++){emit(__sidebarPrefix.slice(0,index));await sleep(25);}
      emit(__sidebarPrefix);
      await waitFor(()=>__sidebarCalls.length===1 && settled());
      counts.typing=__sidebarCalls.length;
      await sleep(350);if(__sidebarCalls.length!==1)throw Error('duplicate typing request');

      __sidebarCalls=[];
      input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
      for(const value of ['d','dan','单','单调']){emit(value,true);await sleep(40);}
      await sleep(350);enter(true);counts.duringComposition=__sidebarCalls.length;
      if(counts.duringComposition!==0)throw Error('partial IME query');
      input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'单调'}));
      emit(__sidebarPrefix);await waitFor(()=>__sidebarCalls.length===1 && settled());
      await sleep(350);counts.afterComposition=__sidebarCalls.length;
      if(counts.afterComposition!==1)throw Error('duplicate compositionend/trailing-input query');

      for(const kind of ['clear','enter','filter','page']){
        __sidebarCalls=[];emit(__sidebarPrefix);
        if(kind==='clear')emit('');
        if(kind==='enter')enter();
        if(kind==='filter'){const select=document.getElementById('filterType');select.value='detailed_answer';
          select.dispatchEvent(new Event('change',{bubbles:true}));}
        if(kind==='page')goToSidebarPage(2,'bank');
        if(__sidebarCalls.length!==1)throw Error(kind+' was not immediate');
        await waitFor(settled);await sleep(350);
        if(__sidebarCalls.length!==1)throw Error(kind+' left a duplicate timer');
        counts[kind]={requests:__sidebarCalls.length,page:__sidebarCalls[0].page};
      }
      if(counts.page.page!=='2')throw Error('paging reset to first page');

      // Ignore transport cancellation deliberately; test the real loader's sequence guard.
      __sidebarCalls=[];emit('obsolete-response');enter();
      await waitFor(()=>typeof __releaseSidebarOld==='function');
      const old=__sidebarCalls[0];const before=document.querySelectorAll('#questionsList [data-id]').length;
      emit(__sidebarPrefix);
      if(!old.signal.aborted)throw Error('new input did not immediately abort old request');
      __releaseSidebarOld();await sleep(100);
      if(document.querySelectorAll('#questionsList [data-id]').length!==before)throw Error('old response rendered while new input waits');
      await waitFor(()=>__sidebarCalls.length===2 && settled());
      if(document.querySelectorAll('#questionsList [data-id]').length!==20)throw Error('final search results missing');
      counts.staleResponseIgnored=true;

      __sidebarCalls=[];emit('pending-before-drafts');switchSidebarTab('drafts');
      await sleep(350);if(__sidebarCalls.length)throw Error('tab change left a delayed bank query');
      emit('local-draft-search');await sleep(350);
      if(__sidebarCalls.length)throw Error('draft search requested database');
      counts.draftDatabaseRequests=__sidebarCalls.length;
      switchSidebarTab('bank');await waitFor(settled);
      counts.unsavedPreserved=document.getElementById('editContent').value===__sidebarUnsaved && isEditorModified();
      counts.cartPreserved=JSON.stringify(PaperStore.cart)===__sidebarCart;
      if(!counts.unsavedPreserved || !counts.cartPreserved)throw Error('search changed editor or cart');
      window.fetch=window.__sidebarOriginalFetch;
      return counts;
    })()
    """)
    (tmp_path / "sidebar-search-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    browser.command("screenshot", str(tmp_path / "sidebar-search-complete.png"))
    assert result["typing"] == result["afterComposition"] == 1
    assert result["duringComposition"] == result["draftDatabaseRequests"] == 0
    assert result["staleResponseIgnored"] and result["unsavedPreserved"] and result["cartPreserved"]
