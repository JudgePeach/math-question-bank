"""Execute shipped sidebar scheduling and list loaders with synthetic IO only."""

from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _frontend_source():
    editor = (ROOT / "static/js/editor.js").read_text(encoding="utf-8")
    imports = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
    helpers = editor[:editor.index("// A subdivision always carries")]
    load_start = editor.index("        function loadQuestions(retryCount = 0)")
    load_end = editor.index("        // ==========================================", load_start)
    draft_start = editor.index("        function loadDrafts()")
    draft_end = editor.index("        function deleteDraft(", draft_start)
    page_start = editor.index("        function goToSidebarPage(")
    page_end = editor.index("        // Expose to global scope", page_start)
    setup_start = imports.index("        function setupSidebarSearch()")
    setup_end = imports.index("        // App Initialization", setup_start)
    tab_start = imports.index("        function switchSidebarTab(")
    tab_end = imports.index("        // Toast Helper", tab_start)
    return "\n".join((helpers, editor[load_start:load_end], editor[draft_start:draft_end],
                      editor[page_start:page_end], imports[setup_start:setup_end],
                      imports[tab_start:tab_end]))


SETUP = r"""
const assert = require('node:assert/strict');
const window = {};
const console = {error() {}, warn() {}};
let activeSidebarTab = 'bank';
const timers = new Map(); let nextTimer = 0;
const setTimeout = (fn, ms) => {timers.set(++nextTimer, {fn, ms}); return nextTimer;};
const clearTimeout = id => timers.delete(id);
const tick = () => {
  const pending = [...timers];
  for (const [id, timer] of pending) if (timers.delete(id)) timer.fn();
};
const flush = () => new Promise(resolve => setImmediate(resolve));
class Element {
  constructor(value='') {this.value=value; this.listeners={}; this.attributes={}; this.innerHTML='';
    this.classList={add() {}, remove() {}};}
  addEventListener(name, handler) {(this.listeners[name] ||= []).push(handler);}
  emit(name, properties={}) {
    const event={type:name, preventDefault() {this.prevented=true;}, ...properties};
    for (const handler of this.listeners[name] || []) handler(event);
    return event;
  }
  setAttribute(name, value) {this.attributes[name]=value;}
  removeAttribute(name) {delete this.attributes[name];}
  scrollTo() {}
}
const ids=['searchInput','filterType','filterDifficulty','filterCompulsory','filterChapter',
  'filterKnowledge','filterSource','filterSort','questionsList','sidebarTab-bank','sidebarTab-drafts'];
const nodes=Object.fromEntries(ids.map(id=>[id,new Element()]));
nodes.filterSort.value='desc';
const document={getElementById:id=>nodes[id] || null};
const KnowledgePointState={filter:()=>({}), appendParams() {}, matches:()=>true};
const EditorState={questionId:42,draftId:null};
const originalEditor={content:'unsaved $x^2$',draft:'keep',cart:[7,9]};
const originalEditorSnapshot=JSON.stringify(originalEditor);
const isEditorModified=()=>true;
const getLocalStorageDrafts=()=>[];
const updateBankFilterSummary=()=>{};
const applied=[];
function renderSidebarPagination(total, page, tab) {applied.push({total,page,tab});}
const requests=[];
function fetch(url, options={}) {
  return new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));
}
const respond=(request,total=1)=>request.resolve({ok:true,json:async()=>({items:[],total,
  total_pages:4,page:Number(new URL(request.url,'http://local').searchParams.get('page'))})});
function input(value, properties={}) {nodes.searchInput.value=value;return nodes.searchInput.emit('input',properties);}
function enter(properties={}) {return nodes.searchInput.emit('keydown',{key:'Enter',...properties});}
function parameters(request=requests.at(-1)) {return new URL(request.url,'http://local').searchParams;}
"""


def _run(assertions):
    node = shutil.which("node")
    assert node, "Node.js is required for the executable frontend regression"
    script = SETUP + _frontend_source() + "\nsetupSidebarSearch();\n(async()=>{\n" + assertions + r"""
assert.equal(JSON.stringify(originalEditor),originalEditorSnapshot,'search changed unsaved editor/cart');
})().catch(error=>{process.stderr.write(String(error.stack));process.exitCode=1;});
"""
    result = subprocess.run([node, "-"], input=script, cwd=ROOT, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_typing_ime_and_final_trailing_input_issue_one_bounded_search():
    _run(r"""
currentBankPage=4;currentDraftPage=3;
for(const value of ['f','fu','fun','function']) input(value);
assert.equal(requests.length,0);
assert.equal(timers.size,1);
assert.equal([...timers.values()][0].ms,300);
assert.equal(currentBankPage,1);assert.equal(currentDraftPage,1);
tick();assert.equal(requests.length,1);assert.equal(parameters().get('q'),'function');
assert.equal(parameters().get('page_size'),'20');
respond(requests.at(-1));await flush();
nodes.searchInput.emit('compositionstart');
for(const value of ['d','dan','单','单调']) input(value,{isComposing:true});
tick();assert.equal(requests.length,1,'IME partial text was searched');
assert.equal(enter({isComposing:true}).prevented,undefined);
assert.equal(enter({keyCode:229}).prevented,undefined);
assert.equal(requests.length,1,'IME candidate Enter submitted search');
nodes.searchInput.emit('compositionend');input('单调');
assert.equal(timers.size,1);tick();
assert.equal(requests.length,2);assert.equal(parameters().get('q'),'单调');
respond(requests.at(-1));await flush();
// Some browsers expose isComposing on input before a compositionstart callback.
input('pending-before-ime');input('partial',{isComposing:true});
tick();assert.equal(requests.length,2);
""")


def test_clear_enter_filters_paging_and_tab_changes_cancel_pending_keyword():
    _run(r"""
input('pending');input('');
assert.equal(requests.length,1);assert.equal(parameters().has('q'),false);
tick();assert.equal(requests.length,1);respond(requests.at(-1));await flush();
input('pending-enter');assert.equal(enter().prevented,true);
assert.equal(requests.length,2);tick();assert.equal(requests.length,2);
respond(requests.at(-1));await flush();
// All existing explicit filter/sort paths call the same shipped list loader.
for(const [id,value,param] of [['filterType','single_choice','qtype'],
  ['filterDifficulty','medium','difficulty'],['filterSort','asc','sort'],
  ['filterCompulsory','必修一','compulsory'],['filterChapter','函数','chapter'],
  ['filterKnowledge','单调性','knowledge'],['filterSource','合成来源','source']]) {
  const count=requests.length;input('pending-'+id);nodes[id].value=value;loadQuestions();
  assert.equal(requests.length,count+1);assert.equal(parameters().get(param),value);
  tick();assert.equal(requests.length,count+1);respond(requests.at(-1));await flush();
}
input('pending-page');goToSidebarPage(3,'bank');
const pagingCount=requests.length;assert.equal(parameters().get('page'),'3');
tick();assert.equal(requests.length,pagingCount);respond(requests.at(-1));await flush();
input('pending-drafts');switchSidebarTab('drafts');tick();
assert.equal(requests.length,pagingCount);assert.equal(applied.at(-1).tab,'drafts');
input('draft-search');assert.equal(timers.size,1);tick();
assert.equal(requests.length,pagingCount,'draft search made a database request');
assert.equal(applied.at(-1).tab,'drafts');
input('pending-bank');switchSidebarTab('bank');const tabCount=requests.length;
tick();assert.equal(requests.length,tabCount);respond(requests.at(-1));await flush();
""")


def test_input_invalidates_inflight_results_and_retries_before_debounce_dispatch():
    _run(r"""
input('old');enter();const old=requests.at(-1);
const sequence=bankQuestionsLoadSequence;
input('new-awaiting');
assert.equal(old.options.signal.aborted,true);
assert(bankQuestionsLoadSequence>sequence);
assert.equal(requests.length,1,'new request ignored debounce');
assert.equal(nodes.questionsList.attributes['aria-busy'],undefined);
respond(old,101);await flush();assert.equal(applied.length,0,'stale result rendered during wait');
tick();const fresh=requests.at(-1);respond(fresh,202);await flush();
assert.deepEqual(applied.map(item=>item.total),[202]);
input('old-2');enter();const old2=requests.at(-1);
input('new-2');enter();respond(requests.at(-1),303);await flush();
respond(old2,404);await flush();assert.equal(applied.at(-1).total,303);
// A genuine current failure queues retry; new input cancels that work immediately.
input('will-fail');enter();requests.at(-1).reject(Error('synthetic outage'));await flush();
assert([...timers.values()].some(timer=>timer.ms===1500));
input('recovered');assert.deepEqual([...timers.values()].map(timer=>timer.ms),[300]);
tick();const count=requests.length;respond(requests.at(-1),505);await flush();
tick();assert.equal(requests.length,count);assert.equal(applied.at(-1).total,505);
// Switching to local drafts invalidates any ignored transport abort response too.
input('late-bank');enter();const late=requests.at(-1);switchSidebarTab('drafts');
const appliedCount=applied.length;respond(late,606);await flush();
assert.equal(applied.length,appliedCount);assert.equal(applied.at(-1).tab,'drafts');
""")
