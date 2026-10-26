"""Execute real request waits with virtual time; never wait or use services."""
from pathlib import Path
import shutil
import subprocess

import pytest

from frontend_timeout_helpers import FRONTEND_TIMEOUTS


ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
IMPORT = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
PAPER = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")


def section(source, start, end):
    left = source.index(start)
    return source[left:source.index(end, left)]


CLOCK = r"""
const assert=require('node:assert/strict');
let now=0,serial=0;
Date.now=()=>now;
const timers=new Map();
const setTimeout=(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms,due:now+ms});return id;};
const clearTimeout=id=>timers.delete(id);
const advance=ms=>{now+=ms;for(const [id,timer] of [...timers])if(timer.due<=now){timers.delete(id);timer.fn();}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const requests=[];
function pendingFetch(url,options={}){
  return new Promise((resolve,reject)=>{
    requests.push({url,options,resolve,reject});
    options.signal?.addEventListener('abort',()=>{const error=Error('aborted');error.name='AbortError';reject(error);});
  });
}
const window={showToast(){}};
"""


def run(source, checks):
    script = FRONTEND_TIMEOUTS + CLOCK + source
    script += "\n(async()=>{\n" + checks + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    result = subprocess.run([shutil.which("node") or "node", "-"], input=script, text=True,
                            capture_output=True, cwd=ROOT, timeout=15)
    assert result.returncode == 0, result.stderr + result.stdout


def test_policy_is_frozen_and_document_status_waits_for_sixty_seconds():
    assert API.index("const MathBankTimeouts =") < API.index("const MathBankSafe ="), "policy must be shared across cascade modules"
    source = "const fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));\n"
    source += section(IMPORT, "async function fetchDocumentImportJson(", "async function requestDocumentTaskCancellation(")
    run(source, r"""
      assert.ok(Object.isFrozen(MathBankTimeouts));
      assert.deepEqual(Object.values(MathBankTimeouts),[30000,60000,600000,1800000]);
      const task=fetchDocumentImportJson('/api/tasks/original/status');
      const failed=assert.rejects(task,error=>error.isConnectionTimeout===true);
      advance(59999);await flush();assert.equal(requests[0].options.signal.aborted,false);
      advance(1);await failed;
      assert.equal(requests[0].options.signal.aborted,true);
      assert.deepEqual(requests.map(item=>item.url),['/api/tasks/original/status']);
      assert.equal(timers.size,0);
    """)


def test_duplicate_wait_and_message_match_the_sixty_second_policy():
    source = "const fetch=pendingFetch;const localStorage={getItem:()=>''};\n"
    source += section(IMPORT, "async function requestQuestionDuplicateCheck(items)", "function duplicateBatchMatchCount")
    run(source, r"""
      const task=requestQuestionDuplicateCheck([{content:'synthetic'}]);
      const failed=assert.rejects(task,/查重超时（60 秒）/);
      advance(12000);await flush();assert.equal(requests[0].options.signal.aborted,false);
      advance(48000);await failed;
      assert.equal(requests.length,1);assert.equal(timers.size,0);
    """)


TRANSFER_SETUP = r"""
let libraryTransferTask=null,libraryTransferGeneration=0,libraryTransferAutoDownload='';
const document={getElementById:()=>({checked:true})};
const archiveLibraryTransferTask=()=>{},stopLibraryTransferPolling=()=>{},renderLibraryTransferTask=()=>{};
const newLibraryTransferId=()=> 'original-id',saveLibraryTransferTask=()=>true;
const libraryTransferTaskIsCurrent=(id,generation)=>libraryTransferTask?.task_id===id&&generation===libraryTransferGeneration;
const applyLibraryTransferSnapshot=()=>false;
"""


def test_upload_timeout_queries_original_uuid_without_reposting():
    source = TRANSFER_SETUP + "const fetch=pendingFetch;let reconnects=0;const pollLibraryTransferTask=()=>{reconnects++;};\n"
    source += section(API, "async function submitLibraryTransferTask(", "async function exportLibraryTransfer(")
    run(source, r"""
      const task=submitLibraryTransferTask('export',{question_ids:[1]});
      assert.equal(requests.length,1);assert.equal(requests[0].options.method,'POST');
      assert.equal(JSON.parse(requests[0].options.body).client_request_id,'original-id');
      advance(120000);await flush();assert.equal(requests[0].options.signal.aborted,false);
      advance(480000);await task;
      assert.equal(reconnects,1);assert.equal(requests.length,1);
      assert.equal(libraryTransferTask.task_id,'original-id');assert.equal(libraryTransferTask.disconnected,true);
      assert.equal(timers.size,0);
    """)


def test_transfer_poll_timeout_backoff_only_queries_same_task():
    source = TRANSFER_SETUP + r"""
const fetch=pendingFetch;
let libraryTransferPollPending=false,libraryTransferPollController=null,libraryTransferPollTimer=null;
""" + section(API, "async function pollLibraryTransferTask()", "function reconnectLibraryTransfer()")
    run(source, r"""
      libraryTransferTask={task_id:'original-id',status:'running'};
      const task=pollLibraryTransferTask();advance(15000);await flush();
      assert.equal(requests[0].options.signal.aborted,false);
      advance(45000);await task;
      assert.equal(libraryTransferPollPending,false);
      assert.equal([...timers.values()][0].ms,2000,'existing reconnect backoff is unchanged');
      advance(2000);await flush();
      assert.deepEqual(requests.map(item=>item.url),['/api/library-transfer/tasks/original-id','/api/library-transfer/tasks/original-id']);
      assert.ok(requests.every(item=>!item.options.method));
      requests[1].resolve({ok:true,json:async()=>({status:'completed'})});await flush();
      assert.equal(timers.size,0);
    """)


PANDOC_SETUP = r"""
const elements=new Map(['pandocInstallBtn','pandocCompatibilityBtn','pandocCancelBtn'].map(id=>[id,{}]));
const document={getElementById:id=>elements.get(id)};
const errors=[];
const updatePandocInstallProgress=()=>{},setPandocModalVisible=()=>{};
const showPandocInstallError=(message,reconnect)=>errors.push({message,reconnect});
"""
PANDOC = section(PAPER, "async function pollPandocInstall(taskId)", "async function ensurePandocForWordExport()")


@pytest.mark.parametrize("existing", [False, True])
def test_pandoc_query_timeout_reconnects_instead_of_starting_another_install(existing):
    source = PANDOC_SETUP + r"""
const fetch=(url,options={})=>{
  if(options.method==='POST'){
    requests.push({url,options});
    return Promise.resolve({ok:true,json:async()=>({status:'success',pandoc:{status:'queued',task_id:'original'}})});
  }
  return pendingFetch(url,options);
};
""" + PANDOC
    run(source, r"""
      const task=installPandocAndContinue(EXISTING?'original':null);await flush();
      const first=requests.at(-1);assert.equal(first.url,'/api/runtime/pandoc/install/original');
      advance(60000);await flush();
      assert.equal(errors.at(-1).reconnect,true);
      assert.equal(typeof elements.get('pandocInstallBtn').onclick,'function');
      elements.get('pandocInstallBtn').onclick();await flush();
      const last=requests.at(-1);assert.notEqual(last,first);
      assert.equal(last.url,first.url);
      last.resolve({ok:true,json:async()=>({pandoc:{status:'completed'}})});
      assert.equal(await task,true);
      assert.equal(requests.filter(item=>item.options.method==='POST').length,EXISTING?0:1);
      assert.equal(timers.size,0);
    """.replace("EXISTING", "true" if existing else "false"))


def test_pandoc_total_wait_passes_fifteen_minutes_and_ends_at_thirty_minutes():
    source = PANDOC_SETUP + "const fetch=pendingFetch;\n" + PANDOC
    run(source, r"""
      const task=pollPandocInstall('original');
      const failed=assert.rejects(task,/安装等待超时/);
      requests[0].resolve({ok:true,json:async()=>({pandoc:{status:'downloading'}})});await flush();
      assert.equal([...timers.values()][0].ms,750,'poll cadence remains unchanged');
      now=15*60*1000;advance(750);await flush();
      assert.equal(requests.length,2,'fifteen minutes no longer ends installation polling');
      requests[1].resolve({ok:true,json:async()=>({pandoc:{status:'verifying'}})});await flush();
      now=30*60*1000;advance(750);await failed;
      assert.equal(requests.length,2);assert.equal(timers.size,0);
    """)


def test_upload_and_ai_waits_keep_their_respective_contracts():
    source_upload = section(IMPORT, "const readToken =", "// 旧版独立配图区")
    assert "controller.abort(), MathBankTimeouts.UPLOAD_MS" in source_upload
    for source in ((ROOT / "static/js/ocr.js").read_text(encoding="utf-8"),
                   section(IMPORT, "function runAIClassify()", "function canApplyClassifyRecommendation()")):
        assert "MathBankTimeouts" not in source  # Existing cancellation has no short wall-clock cutoff.
    assert "}, 1500);" in IMPORT
    assert "setTimeout(resolve, 750)" in PAPER
