"""Executable AI paper UI races and reference handling, without a live library."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
START = SOURCE.index("    // Reference metadata stays outside")
END = SOURCE.index("    window.clearAiAnalysis =", START)
AI_SOURCE = SOURCE[START:END]


def run_ui(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the executable frontend regression")
    harness = r"""
const assert = require('assert');
const toasts = [];
function element(value='') {
  return {value, disabled:false, hidden:false, textContent:'', children:[], attributes:{},
    replaceChildren(){this.children=[];}, appendChild(child){this.children.push(child);},
    setAttribute(key,value){this.attributes[key]=value;}};
}
const nodes = Object.fromEntries(['paperAiSelectBtn','paperAiReferenceKey','paperAiReferenceStatus','paperAiReferenceRetryBtn'].map(id=>[id,element()]));
nodes.paperAiPromptInput=element('函数专题，兼顾基础和综合');
const document = {getElementById:id=>nodes[id]||null,createElement:()=>element()};
const window = {activeCurriculumVersion:'rja',PaperStore:{
  cart:[{id:1,score:5}],meta:{paper_type:'exam',title:'test',section_order:[]},
  filters:{compulsory:'必修一',chapter:'函数',knowledge:'函数的概念',question_type:'',difficulty:'',keyword:'测试',tab:'all'},
  aiSelectionLimit:2,aiSelectionPrompt:nodes.paperAiPromptInput.value,aiReferenceMode:'recent_gaokao',aiReferenceKey:'',
  aiSelectionBusy:false,aiReferenceExams:[],aiReferenceLoaded:false,aiReferenceLoading:false,aiReferenceError:'',
  questionsMap:{},activeWorkspace:'paper'
},showToast:(message,type)=>toasts.push({message,type}),renderPaperCanvas(){}};
window.isInCart=id=>window.PaperStore.cart.some(item=>Number(item.id)===id);
const KnowledgePointState={filter:()=>({knowledge_point_codes:['kp1'],knowledge_point_mode:'any',knowledge_point_role:'main',knowledge_point_status:'all'})};
let savedMeta=0,savedCart=0,rendered=0;
function saveMetaToStorage(){savedMeta++;}
function saveCartToStorage(){savedCart++;}
function renderPart3QuestionStream(){rendered++;}
function getPaperCartSignature(){return JSON.stringify(window.PaperStore.cart);}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const success=data=>({ok:true,status:200,json:async()=>data});
"""
    result = subprocess.run([node, "-e", harness + AI_SOURCE + "\n" + script], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_selection_is_single_flight_sends_current_scope_and_reports_shortfall():
    run_ui(r"""
(async()=>{
  const pending=deferred();let calls=0,request;
  global.fetch=async(url,options)=>{calls++;assert.equal(url,'/api/paper/ai-select');request=JSON.parse(options.body);return pending.promise;};
  const first=window.triggerAiPaperSelect();await window.triggerAiPaperSelect();
  assert.equal(calls,1);assert(nodes.paperAiSelectBtn.disabled);assert(window.PaperStore.aiSelectionBusy);
  assert.deepEqual(request.exclude_ids,[1]);assert.equal(request.keyword,'测试');assert.equal(request.limit,2);
  assert.equal(request.paper_type,'exam');assert.equal(request.reference_mode,'recent_gaokao');
  assert.equal(request.expected_version_code,'rja');assert.deepEqual(request.knowledge_point_codes,['kp1']);
  pending.resolve(success({status:'success',data:[{id:1},{id:2,question_type:'single_choice'},{id:2}],
    ai_analysis:'库内函数题覆盖',fallback:true,warnings:['参考卷不完整'],selection_summary:{selected:1},reference_profile:{title:'2025年卷'}}));
  await first;
  assert.deepEqual(window.PaperStore.cart.map(item=>item.id),[1,2]);
  assert(window.PaperStore.meta.ai_analysis.includes('新增 1 / 2'));
  assert(window.PaperStore.meta.ai_analysis.includes('参考卷不完整'));
  assert.equal(window.PaperStore.meta.ai_model_used,'本地规则选题');
  assert.equal(toasts.at(-1).type,'warning');assert(!nodes.paperAiSelectBtn.disabled);
  assert.equal(savedCart,1);assert.equal(savedMeta,1);
  // State survives a second invocation; disabling the reference suppresses its key.
  window.PaperStore.aiReferenceMode='none';window.PaperStore.aiReferenceKey='old';
  global.fetch=async(url,options)=>{request=JSON.parse(options.body);return success({status:'success',data:[{id:3},{id:4}]});};
  await window.triggerAiPaperSelect();assert.equal(request.reference_key,'');assert.equal(toasts.at(-1).type,'success');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_late_selection_cannot_change_another_paper_or_changed_requirements():
    run_ui(r"""
(async()=>{
  const changes=[
    ()=>window.PaperStore.cart.push({id:10,score:5}),
    ()=>window.PaperStore.cart=window.PaperStore.cart.map(item=>({...item})),
    ()=>window.PaperStore.meta={...window.PaperStore.meta},
    ()=>window.PaperStore.meta.title+='改',
    ()=>window.PaperStore.filters.keyword+='改',
    ()=>window.activeCurriculumVersion+='改',
    ()=>nodes.paperAiPromptInput.value+='改',
    ()=>window.PaperStore.aiSelectionLimit++,
    ()=>window.PaperStore.aiReferenceKey='2024',
    ()=>window.PaperStore.aiReferenceMode='none',
    ()=>window.PaperStore.activeWorkspace='bank'
  ];
  for(const change of changes){
    const pending=deferred();global.fetch=()=>pending.promise;
    const started=window.triggerAiPaperSelect();change();
    const cartBefore=JSON.stringify(window.PaperStore.cart),metaBefore=JSON.stringify(window.PaperStore.meta);
    pending.resolve(success({status:'success',data:[{id:50,question_type:'detailed_answer'}],ai_analysis:'迟到'}));
    await started;
    assert.equal(JSON.stringify(window.PaperStore.cart),cartBefore);
    assert.equal(JSON.stringify(window.PaperStore.meta),metaBefore);
    assert.equal(window.PaperStore.questionsMap[50],undefined);
    assert(toasts.at(-1).message.includes('本次结果未加入'));
    assert(!window.PaperStore.aiSelectionBusy);
  }
  assert.equal(savedMeta,0);assert.equal(savedCart,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_explicit_nineteen_question_plan_overrides_default_count_without_truncation():
    run_ui(r"""
(async()=>{
  window.PaperStore.aiSelectionLimit=5;
  nodes.paperAiPromptInput.value='组一份19题高考卷：8道单选、3道多选、3道填空、5道解答';
  const kinds=[...Array(8).fill('single_choice'),...Array(3).fill('multi_choice'),
    ...Array(3).fill('fill_in_blank'),...Array(5).fill('detailed_answer')];
  global.fetch=async(url,options)=>{
    assert.equal(JSON.parse(options.body).limit,5);
    return success({status:'success',data:kinds.map((question_type,index)=>({id:index+2,question_type})),
      warnings:[],selection_summary:{requested:19,selected:19,shortfall:0,
        requested_type_counts:{single_choice:8,multi_choice:3,fill_in_blank:3,detailed_answer:5}},ai_analysis:'19题混合配额'});
  };
  await window.triggerAiPaperSelect();
  assert.equal(window.PaperStore.cart.length,20);
  assert.equal(window.PaperStore.cart.filter(item=>window.PaperStore.questionsMap[item.id]?.question_type==='detailed_answer').length,5);
  assert(window.PaperStore.meta.ai_analysis.includes('新增 19 / 19'));
  assert(toasts.at(-1).message.includes('19 / 19'));assert.equal(toasts.at(-1).type,'success');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_errors_release_the_current_button_and_validate_count_before_calling():
    run_ui(r"""
(async()=>{
  let calls=0;global.fetch=async()=>{calls++;return {ok:false,status:409,json:async()=>({detail:'教材已切换，请重试'})};};
  await window.triggerAiPaperSelect();assert.equal(toasts.at(-1).message,'教材已切换，请重试');assert.equal(toasts.at(-1).type,'error');
  assert.equal(window.PaperStore.cart.length,1);assert(!window.PaperStore.aiSelectionBusy);
  for(const count of [0,31,1.2,'not a number']){window.PaperStore.aiSelectionLimit=count;await window.triggerAiPaperSelect();}
  assert.equal(calls,1);window.PaperStore.aiSelectionLimit=2;
  const pending=deferred();global.fetch=()=>pending.promise;const started=window.triggerAiPaperSelect();
  nodes.paperAiSelectBtn=element();pending.resolve({ok:false,status:500,json:async()=>{throw Error('invalid JSON');}});await started;
  assert(!nodes.paperAiSelectBtn.disabled);assert.equal(nodes.paperAiSelectBtn.textContent,'开始选题');
  assert.equal(window.PaperStore.cart.length,1);
  global.fetch=async()=>success({status:'success',data:[]});await window.triggerAiPaperSelect();
  assert.equal(toasts.at(-1).type,'warning');assert(toasts.at(-1).message.includes('0 / 2'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_reference_exams_are_lazy_retryable_and_rendered_as_text():
    run_ui(r"""
(async()=>{
  let calls=0;const pending=deferred();global.fetch=url=>{assert.equal(url,'/api/paper/reference-exams');calls++;return pending.promise;};
  window.PaperStore.aiReferenceMode='none';await window.loadPaperReferenceExams();assert.equal(calls,0);
  window.PaperStore.aiReferenceMode='recent_gaokao';const first=window.loadPaperReferenceExams();await window.loadPaperReferenceExams();assert.equal(calls,1);
  pending.resolve({ok:false,status:503,json:async()=>({detail:'暂时不可读取'})});await first;
  assert.equal(nodes.paperAiReferenceStatus.textContent,'暂时不可读取');assert(!nodes.paperAiReferenceRetryBtn.hidden);
  global.fetch=async()=>{calls++;return success({status:'success',data:[{key:'2025',title:'<img src=x onerror=alert(1)>',question_count:19,completeness_note:'有待核对'}]});};
  await window.loadPaperReferenceExams(true);await window.loadPaperReferenceExams();assert.equal(calls,2);
  const option=nodes.paperAiReferenceKey.children[1];assert(option.textContent.includes('<img'));assert.equal(option.innerHTML,undefined);
  assert(nodes.paperAiReferenceRetryBtn.hidden);assert(!nodes.paperAiReferenceKey.disabled);
  window.updatePaperAiReferenceMode('none');assert(nodes.paperAiReferenceKey.disabled);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
