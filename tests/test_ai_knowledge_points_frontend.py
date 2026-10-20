"""Execute the real classifier modal and point state without services or AI calls."""
from pathlib import Path

from test_knowledge_points_frontend import run_js, section


ROOT = Path(__file__).resolve().parents[1]
IMPORT = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/editor.js").read_text(encoding="utf-8")
MODAL = section(IMPORT, "let temporaryClassifyData =", "// Delete Question")
NODES = section(EDITOR, "function knowledgePointNode", "function currentKnowledgePointLocation")
SETUP = r"""
class Element {
 constructor(){this.value='';this.children=[];this.dataset={};this.events={};this.checked=false;
   this.classList={add(){},remove(){},toggle(){}};this.onchange=()=>{};}
 setAttribute(key,value){this[key]=value;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 addEventListener(name,callback){this.events[name]=callback;}
 dispatchEvent(event){this.events[event.type]?.(event);return true;}
 querySelector(){return this;}
}
const elements=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
 createElement:()=>new Element()};
let questionDetailLoading=false,saveQuestionInFlight=null;
const requests=[],toasts=[],timers=[],saves=[];
const fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));
const showToast=value=>toasts.push(value);
const setTimeout=callback=>{timers.push(callback);};
const categoryTree={};
function populateCategoryDropdowns(){}
function saveQuestion(skip){saves.push({skip,points:KnowledgePointState.snapshot()});}
window.MathBankModal={open(){},close(){}};
window.activeCurriculumVersion='A';
document.getElementById('editContent').value='求数列的和';
document.getElementById('editAnswerMarkdown').value='裂项求和';
document.getElementById('editReview').value='关注通项';
const result={status:'success',compulsory:'册',chapter:'章',knowledge:'节',question_form:'fill_in_blank',
 knowledge_points:[{code:'1803',name:'数列求和',role:'auxiliary',status:'suggested',origin:'ai_classification',evidence:'求数列的和'}]};
const reply=(request,data=result)=>request.resolve({json:async()=>data});
"""


def test_classifier_discards_responses_after_text_points_curriculum_or_session_change():
    run_js(r"""
      const mutations=[()=>document.getElementById('editContent').value+='改',
        ()=>document.getElementById('editAnswerMarkdown').value+='改',
        ()=>document.getElementById('editReview').value+='改',
        ()=>document.getElementById('editChapter').value='人工章',
        ()=>KnowledgePointState.add({code:'1796',name:'人工点'}),
        ()=>window.activeCurriculumVersion='B',()=>EditorState.useQuestion({id:9})];
      for(const mutate of mutations){
        window.activeCurriculumVersion='A';openClassifyModal();
        const work=runAIClassify(),request=requests.at(-1);
        assert.equal(request.options.body.get('answer_markdown'),document.getElementById('editAnswerMarkdown').value);
        assert.equal(request.options.body.get('review_markdown'),document.getElementById('editReview').value);
        mutate();reply(request);await work;
        assert.equal(temporaryClassifyData,null);assert.ok(toasts.at(-1).includes('未应用'));
      }
      assert.equal(saves.length,0);
    """, SETUP + NODES + MODAL)


def test_reopened_classifier_ignores_previous_request_and_applies_points_without_confirmation():
    run_js(r"""
      KnowledgePointState.hydrate([{code:'1796',name:'人工通项',role:'auxiliary',status:'suggested',origin:'manual'}]);
      openClassifyModal();const oldWork=runAIClassify(),old=requests.at(-1);
      closeClassifyModal();openClassifyModal();const freshWork=runAIClassify(),fresh=requests.at(-1);
      reply(old,{...result,chapter:'旧章'});await oldWork;assert.equal(temporaryClassifyData,null);
      reply(fresh,{...result,knowledge_points:[...result.knowledge_points,{code:'1796',name:'AI覆盖',role:'main'}]});await freshWork;
      assert.equal(temporaryClassifyData.chapter,'章');
      assert.equal(temporaryClassifyKnowledgePoints.length,1,'existing labels cannot be overwritten');
      applyClassifyRecommendation();
      const points=KnowledgePointState.snapshot();
      assert.equal(points[0].name,'人工通项');assert.equal(points[0].role,'auxiliary');
      assert.equal(points[0].status,'suggested','legacy status is preserved');
      assert.equal(points[1].status,'confirmed');assert.equal(points[1].evidence,'求数列的和');
      assert.equal(points[1].role,'auxiliary');assert.equal(document.getElementById('editQType').value,'fill_in_blank');
      timers.forEach(fn=>fn());assert.equal(saves.length,1);assert.equal(saves[0].points.length,2);
    """, SETUP + NODES + MODAL)


def test_later_manual_edit_prevents_apply_and_delayed_save_cannot_save_another_question():
    run_js(r"""
      openClassifyModal();let work=runAIClassify();reply(requests.at(-1));await work;
      KnowledgePointState.add({code:'1796',name:'人工选择'});applyClassifyRecommendation();
      assert.equal(KnowledgePointState.snapshot().length,1);assert.equal(timers.length,1);
      openClassifyModal();work=runAIClassify();reply(requests.at(-1));await work;applyClassifyRecommendation();
      EditorState.useQuestion({id:99});timers.forEach(fn=>fn());assert.equal(saves.length,0);
    """, SETUP + NODES + MODAL)
