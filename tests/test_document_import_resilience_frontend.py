"""Execute document cancellation, recovery, and crop ownership without services or AI."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_pdf_layout_frontend import pdf_script  # noqa: F401
from test_import_source_review import review_script  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    result = subprocess.run([shutil.which('node'), '-e', "eval(require('node:fs').readFileSync(0,'utf8'))"],
                            input=script, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


def base_script(pdf_script):
    return pdf_script.rsplit('pdfScenarios[process.argv[1]]', 1)[0]


@pytest.mark.parametrize('scenario', ['cancel_creation', 'cancel_failure', 'cancel_completed_legacy', 'serial_poll_and_404', 'poll_timeout', 'poll_backoff'])
def test_document_request_faults(pdf_script, scenario):
    script = base_script(pdf_script) + r"""
const resilience = {
  async cancel_creation() {
    window.currentDocxFile=new Blob(['docx']); runAIPaperParse();
    const upload=requests[0]; const id=upload.options.body.get('client_request_id');
    assert.match(id,/^[a-f0-9-]{36}$/);assert.equal(upload.options.body.get('paper_title'),'测试卷');
    const cancelled=cancelCurrentImportTask();
    assert.equal(requests[1].url,'/api/tasks/cancel-by-request');
    assert.equal(JSON.parse(requests[1].options.body).client_request_id,id);
    requests[1].resolve({ok:true,json:async()=>({status:'success',cancellation_pending:true})});
    await cancelled;
    upload.resolve({ok:true,json:async()=>({status:'success',task_id:'late-task'})});await flush();
    assert.equal(requests.at(-1).url,'/api/tasks/late-task/cancel');
    requests.at(-1).resolve({ok:true,json:async()=>({status:'success',cancelled:true})});await flush();
    assert.equal(window.currentPdfTaskId,null);assert.equal(intervals.size,0);
    assert.ok(toasts.some(text=>text.includes('待确认')));
  },
  async cancel_failure() {
    window.currentPdfTaskId='task';
    const work=cancelCurrentImportTask();
    requests[0].resolve({ok:false,status:503,json:async()=>({detail:'服务暂不可用'})});await work;
    assert.ok(toasts.at(-1).includes('后台取消尚未确认'));
    assert.ok(!toasts.some(text=>text.includes('安全中止')));
  },
  async cancel_completed_legacy() {
    window.currentPdfTaskId='completed-task';
    const work=cancelCurrentImportTask();
    requests[0].resolve({ok:false,status:409,json:async()=>({status:'error',task_status:'completed',message:'已结束'})});await work;
    assert.equal(window.currentPdfTaskId,'completed-task');assert.ok(activeDocumentPoll);
    assert.ok(toasts.at(-1).includes('读取已完成'));
    assert.ok(!toasts.some(text=>text.includes('已确认取消')));
    stopCurrentDocumentPoll();
  },
  async serial_poll_and_404() {
    const generation=beginDocumentImportTask();pollPdfTaskStatus('task',generation);
    const tick=[...intervals.values()][0];tick();tick();tick();
    assert.equal(requests.length,1,'slow status requests cannot overlap');
    requests[0].resolve({ok:false,status:404,json:async()=>({detail:'找不到任务'})});await flush();
    assert.equal(activeDocumentPoll,null);assert.equal(intervals.size,0);
    assert.ok(toasts.at(-1).includes('重新连接'));
    assert.equal(document.getElementById('runParseBtn').disabled,false);
  },
  async poll_timeout() {
    const work=fetchDocumentImportJson('/api/tasks/task/status',{},1);
    const keepAlive=new Promise(resolve=>setTimeout(resolve,10));
    await assert.rejects(work,error=>error.isConnectionTimeout===true);
    await keepAlive;
    assert.equal(requests[0].options.signal.aborted,true);
  },
  async poll_backoff() {
    let now=10000;Date.now=()=>now;
    const generation=beginDocumentImportTask();pollPdfTaskStatus('task',generation);
    const tick=[...intervals.values()][0];tick();
    requests[0].resolve({ok:false,status:503,json:async()=>({detail:'掉线'})});await flush();
    tick();assert.equal(requests.length,1,'backoff prevents immediate repeated failures');
    now+=3001;tick();requests[1].resolve({ok:false,status:503,json:async()=>({})});await flush();
    now+=6001;tick();requests[2].resolve({ok:false,status:503,json:async()=>({})});await flush();
    assert.equal(activeDocumentPoll,null);assert.equal(intervals.size,0);
  }
};
resilience[SCENARIO]().catch(error=>{console.error(error);process.exitCode=1});
"""
    run_node(script.replace('SCENARIO', json.dumps(scenario)))


def test_crop_close_and_open_other_question_rejects_late_result(pdf_script):
    source = (ROOT / 'static/js/import.js').read_text()
    start = source.index('function openPdfCropModalForQuestion(')
    modal_functions = source[start:source.index('function renderPdfPagesThumbnails()', start)]
    script = base_script(pdf_script) + modal_functions + r"""
window.MathBankModal={open(){},close(){}};
const modal=create('pdfCropModal');modal.appendChild(new Element('div'));
renderPdfPagesThumbnails=()=>{};setupPdfCropDrawListeners=()=>{};loadPdfCropPage=()=>{};
let zoomFactor=1,baseWidth=0,baseHeight=0;
const qA={content:'题A'},qB={content:'题B'};show([qA,qB]);window.currentPdfTaskId='task';
openPdfCropModalForQuestion(0);submitPdfCropCoordinates();
const request=requests.at(-1);
closePdfCropModal();openPdfCropModalForQuestion(1);
request.resolve({ok:true,json:async()=>({status:'success',image_path:'/static/uploads/tmp/A.png'})});
flush().then(()=>{
 assert.deepEqual(qA.image_paths,[]);assert.deepEqual(qB.image_paths,[]);
 assert.equal(document.getElementById('parsed-card-1').querySelector('.card-content-textarea').value,'题B');
 assert.equal(window.activePdfCropSession.question,qB);assert.equal(window.activePdfCropSession.closed,false);
}).catch(error=>{console.error(error);process.exitCode=1});
"""
    run_node(script)


def session_harness(pdf_script, initial=None):
    setup = r"""
Element.prototype.setAttribute=function(name,value){this[name]=value;};
Element.prototype.replaceChildren=function(){this.children=[];};
Element.prototype.closest=function(selectors){
 let target=this;while(target){if(selectors.split(',').some(selector=>selector.trim()==='#'+target.id))return target;target=target.parentElement;}return null;
};
const storage=new Map(INITIAL ? [['mathbank_document_import_session_v1',JSON.stringify(INITIAL)]] : []);
localStorage.getItem=key=>storage.get(key)||null;
localStorage.setItem=(key,value)=>storage.set(key,value);
localStorage.removeItem=key=>storage.delete(key);
const listeners={};document.readyState='loading';
document.addEventListener=(name,callback)=>listeners[name]=callback;
window.addEventListener=(name,callback)=>listeners[name]=callback;
""".replace('INITIAL', json.dumps(initial, ensure_ascii=False))
    return base_script(pdf_script) + setup + (ROOT / 'static/js/document-import-session.js').read_text()


def test_session_restores_corrections_selection_and_revoked_verification_without_paid_requests(pdf_script):
    first = session_harness(pdf_script) + r"""
listeners.DOMContentLoaded();
const q=verifiedQuestion();show([q]);
window.MathBankDocumentImportSession.begin({taskId:'task',clientRequestId:'request',documentType:'docx',phase:'completed'});
edit(document.getElementById('parsed-card-0'),'.card-content-textarea','人工修改后的题干');
document.getElementById('parsed-card-0').querySelector('.card-select-checkbox').checked=false;
window.MathBankDocumentImportSession.flush();
console.log(storage.get('mathbank_document_import_session_v1'));
"""
    saved = json.loads(run_node(first).splitlines()[-1])
    assert saved['questions'][0]['content'] == '人工修改后的题干'
    assert saved['questions'][0]['document_import_source_edited'] is True
    second = session_harness(pdf_script, saved) + r"""
listeners.DOMContentLoaded();
assert.equal(parsedQuestionsData[0].content,'人工修改后的题干');
assert.equal(document.getElementById('parsed-card-0').querySelector('.card-select-checkbox').checked,false);
assert.equal(parsedQuestionNeedsSourceReview(0),true,'refresh cannot revive old visual confirmation');
assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/tasks/task/status');
requests[0].resolve({ok:true,json:async()=>({task_id:'task',status:'completed',data:[verifiedQuestion()]})});
flush().then(()=>{
 assert.equal(parsedQuestionsData[0].content,'人工修改后的题干','server completion cannot overwrite restored corrections');
 assert.ok(requests.every(request=>request.url.endsWith('/status')),'refresh cannot invoke AI or resume');
 window.MathBankDocumentImportSession.clear();assert.equal(storage.size,0);
}).catch(error=>{console.error(error);process.exitCode=1});
"""
    run_node(second)


def test_session_completion_merges_stable_source_ids_and_preserves_unmatched_corrections(pdf_script):
    saved = {'version': 1, 'phase': 'interrupted', 'questions': [
        {'content': '人工修改题A', 'import_item_id': 'A', 'answer_markdown': ''},
        {'content': '无法定位的人工题', 'answer_markdown': ''}], 'baselineQuestions': []}
    script = session_harness(pdf_script, saved) + r"""
listeners.DOMContentLoaded();
const result=window.MathBankDocumentImportSession.mergeCompletion([
 {content:'模型原题A',import_item_id:'A'}, {content:'新恢复题B',import_item_id:'B'}]);
assert.deepEqual(result.map(item=>item.content),['人工修改题A','新恢复题B','无法定位的人工题']);
assert.ok(toasts.some(text=>text.includes('无法唯一对应')));
window.MathBankDocumentImportSession.clear();
"""
    run_node(script)


def test_restored_classification_and_metadata_survive_empty_then_late_catalogs(pdf_script):
    question = {'content': '恢复分类题', 'question_type': 'detailed_answer', 'difficulty': 'challenge',
                'source': '教师修订的来源', 'category_compulsory': '必修一', 'category_chapter': '集合',
                'category_knowledge': '集合的概念'}
    saved = {'version': 1, 'phase': 'completed', 'questions': [question], 'baselineQuestions': [question]}
    script = session_harness(pdf_script, saved) + r"""
window.categoryTree={};window.systemMetadata={question_types:[],difficulties:[]};
listeners.DOMContentLoaded();
const card=document.getElementById('parsed-card-0');
assert.equal(card.querySelector('.card-compulsory').value,'必修一');
assert.equal(card.querySelector('.card-chapter').value,'集合');
assert.equal(card.querySelector('.card-knowledge').value,'集合的概念');
assert.equal(card.querySelector('.card-difficulty').value,'challenge');
assert.equal(card.querySelector('.card-qtype').value,'detailed_answer');
window.MathBankDocumentImportSession.flush();
let saved=JSON.parse(storage.get('mathbank_document_import_session_v1')).questions[0];
assert.equal(saved.category_chapter,'集合');assert.equal(saved.category_knowledge,'集合的概念');
card.querySelector('.card-source').value='目录等待期间的新来源';
window.categoryTree={'必修一':{'集合':['集合的概念','集合的运算']}};
window.systemMetadata={question_types:[{value:'single_choice',label:'单选'}],difficulties:[{value:'normal',label:'常规'}]};
window.MathBankDocumentImportSession.refreshCatalogs();window.MathBankDocumentImportSession.flush();
saved=JSON.parse(storage.get('mathbank_document_import_session_v1')).questions[0];
assert.equal(saved.category_compulsory,'必修一');assert.equal(saved.category_chapter,'集合');
assert.equal(saved.category_knowledge,'集合的概念');assert.equal(saved.difficulty,'challenge');
assert.equal(saved.question_type,'detailed_answer');assert.equal(saved.source,'目录等待期间的新来源');
window.MathBankDocumentImportSession.clear();
"""
    run_node(script)
