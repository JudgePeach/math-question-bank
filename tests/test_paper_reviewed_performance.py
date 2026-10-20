"""Synthetic executable checks for fixed page coordinates and bounded paging work."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'static/js/paper.js').read_text()


def section(start, end):
    a = SOURCE.index(start)
    return SOURCE[a:SOURCE.index(end, a)]


def node(script):
    executable = shutil.which('node')
    assert executable
    result = subprocess.run([executable, '-'], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_fixed_a4_capacity_and_block_heights_ignore_preview_scale():
    helpers = section('function getPaperBlockOuterHeight(', 'function createMeasuredA4Page(')
    helpers += section('function getA4PageHeightLimits(', 'function rebalanceA4PaperPages(')
    node("const A4_PAGE={width:794};global.window={getComputedStyle:x=>x.style};\n" + helpers + r'''
const assert=require('node:assert/strict');
let scale=1, pageTop=100, expanded=false;
const page={offsetWidth:794,style:{minHeight:'1123px',paddingTop:'96px',paddingBottom:'96px',borderTopWidth:'1px',borderBottomWidth:'1px'},
 closest:()=>page,getBoundingClientRect:()=>({top:pageTop,width:794*scale,height:(expanded?1900:1123)*scale}),querySelector:()=>expanded?notice:null};
const notice={closest:()=>page,style:{marginTop:'0px',marginBottom:'10px'},getBoundingClientRect:()=>({height:30*scale})};
const content={getBoundingClientRect:()=>({top:pageTop+(350+(expanded?40:0))*scale})};
const footer={style:{bottom:'20px'},getBoundingClientRect:()=>({height:16*scale})};
const block={closest:()=>page,style:{marginTop:'3px',marginBottom:'7px'},getBoundingClientRect:()=>({height:300*scale})};
for(const factor of [1,.75,.42,1]){
 scale=factor;
 assert.deepEqual(getA4PageHeightLimits(page,content,footer),{firstPageLimit:676,laterPageLimit:929});
 assert.equal(getPaperBlockOuterHeight(block),310);
 expanded=true;pageTop-=150;
 assert.deepEqual(getA4PageHeightLimits(page,content,footer),{firstPageLimit:676,laterPageLimit:929});
 expanded=false;
}
const sheet={clientWidth:397,style:{setProperty:(key,value)=>{sheet[key]=value}},dataset:{}};
applyPaperPreviewZoom(sheet);assert.equal(Number(sheet['--paper-preview-zoom']),.5);
sheet.clientWidth=1500;applyPaperPreviewZoom(sheet);assert.equal(Number(sheet['--paper-preview-zoom']),1);
''')


def test_non_cart_cache_is_bounded_and_protects_visible_pending_and_selected_questions():
    helpers = section('// Bound complete off-cart', 'window.applyPaperPreviewZoom =')
    node(r'''
const assert=require('node:assert/strict');
const PAPER_QUESTION_CACHE_LIMIT=120,paperQuestionCacheAccess=new Map();
const qids=[...Array.from({length:145},(_,i)=>i+1),...Array.from({length:200},(_,i)=>i+1000)];
const store={questionsMap:Object.fromEntries(qids.map(id=>[id,{id}])),
 cart:qids.filter(id=>id>=1000).map(id=>({id})),bankQuestions:Array.from({length:15},(_,i)=>({id:i+2})),
 answerLoadingIds:new Set([1]),answerCache:Object.fromEntries(qids.map(id=>[id,'answer'])),
 answerErrors:Object.fromEntries(qids.map(id=>[id,'error'])),expandedAnswerIds:new Set(qids)};
global.window={PaperStore:store,hasPendingFigureLayoutWrite:id=>id===17};
''' + helpers + r'''
window.prunePaperQuestionCache();
assert.equal(Object.keys(store.questionsMap).length,320);
for(const id of [1,2,16,17,1000,1199]) assert.ok(store.questionsMap[id]);
assert.equal(store.questionsMap[18],undefined);
assert.equal(store.answerCache[18],undefined);assert.equal(store.answerErrors[18],undefined);
assert.equal(store.expandedAnswerIds.has(18),false);
store.bankQuestions=[{id:50}];window.prunePaperQuestionCache();
for(let id=2000;id<2050;id++)store.questionsMap[id]={id};
window.prunePaperQuestionCache();
assert.ok(store.questionsMap[50], 'revisited visible question is retained');
assert.equal(Object.keys(store.questionsMap).length,320);
assert.equal(store.cart.length,200);
''')


def test_frame_rendering_caps_work_and_ignores_replaced_or_switched_workspace():
    body = section("if (savedScrollTop > 0 || displayList.length <= 6", '\n    // Render Part 4:')
    # The extracted suffix includes the renderer's closing brace.
    node(r'''
const assert=require('node:assert/strict');
let paperStreamLayoutRevision=1,paperStreamLayoutFrame=null;
const frames=[],done=[];
const container={isConnected:true,scrollTop:0,setAttribute:(k,v)=>{container[k]=v}};
global.document={getElementById:id=>id==='paperQuestionStream'?container:null};
global.window={PaperStore:{activeWorkspace:'paper'}};
function requestAnimationFrame(fn){frames.push(fn);return frames.length;}
function initializeAutoFigureSizing(){}
function run(savedScrollTop=0){
 const layoutRevision=paperStreamLayoutRevision,activeWorkspace='paper';
 const displayList=Array.from({length:15},(_,i)=>({id:i}));
 const layoutQuestion=q=>done.push(q.id);
''' + body + r'''
run();assert.equal(done.length,6);assert.equal(frames.length,1);
frames.shift()();assert.equal(done.length,12);
frames.shift()();assert.equal(done.length,15);assert.equal(container['aria-busy'],'false');
done.length=0;run();assert.equal(done.length,6);
window.PaperStore.activeWorkspace='bank';frames.shift()();assert.equal(done.length,6);
window.PaperStore.activeWorkspace='paper';done.length=0;run();paperStreamLayoutRevision++;
frames.shift()();assert.equal(done.length,6);
done.length=0;run(40);assert.equal(done.length,15);assert.equal(container.scrollTop,40);
''')


def test_preflight_history_is_bounded_and_accepts_only_local_report_ids():
    helpers = section("const PAPER_PREFLIGHT_HISTORY_KEY =", 'function renderPaperExportPreflightHistory(')
    node(r'''
const assert=require('node:assert/strict');
const values=new Map();global.localStorage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};
function renderPaperExportPreflightHistory(){}
''' + helpers + r'''
assert.equal(rememberPaperExportPreflight(null,{preflight:{id:'javascript:alert(1)'}}),'');
for(let n=1;n<=8;n++)rememberPaperExportPreflight(null,{preflight:{id:String(n).padStart(32,'0'),status:'blocked'}});
assert.equal(readPaperPreflightHistory().length,5);
assert.equal(readPaperPreflightHistory()[0].id,'8'.padStart(32,'0'));
assert.equal(rememberPaperExportPreflight({headers:{get:()=> '9'.padStart(32,'0')}}),'9'.padStart(32,'0'));
assert.equal(readPaperPreflightHistory().length,5);
''')


def test_detached_figure_reservations_use_fixed_a4_body_width():
    geometry = next(line for line in SOURCE.splitlines() if 'const A4_PAGE =' in line)
    helpers = section('function normalizeFigureSize(', 'function figureImageStyle(')
    node(geometry + "\nconst FIGURE_SIZE_VALUES=['auto','small','medium','large'];\n" + r'''
const assert=require('node:assert/strict');
global.window={ImageLayoutTools:{split:raw=>({tail:raw}),sizeDimensions:(profile,size)=>{
 const cm={small:[5,4],medium:[8,6],large:[11,8]}[size];
 return cm?{maxWidth:cm[0]*96/2.54,maxHeight:cm[1]*96/2.54}:null;
}}};
function shouldPreserveInlinePaperImages(){return false;}
''' + helpers + r'''
const pair='![](/static/uploads/one.png)\n\n![](/static/uploads/two.png)';
const medium=getDetachedFigureMetrics(pair,'center','medium');
assert.equal(medium.count,2);
assert.equal(medium.blockHeight,2*medium.maxHeight+8+30,'two medium figures need two conservative rows in a 600px body');
const wide=getDetachedFigureMetrics('![](/static/uploads/wide.png)','bottom_right','auto');
assert.equal(wide.maxWidth,420);assert.equal(wide.maxHeight,300);
assert.equal(wide.blockHeight,330,'a single auto wide figure reserves one row');
''')
