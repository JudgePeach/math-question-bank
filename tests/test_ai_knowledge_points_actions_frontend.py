"""Exercise classification actions and save hand-off with the shipped JS only."""
from pathlib import Path
import shutil
import subprocess

import pytest

from test_ai_knowledge_points_frontend import MODAL, NODES, SETUP
from test_knowledge_points_frontend import STATE, section


ROOT = Path(__file__).resolve().parents[1]
IMPORT = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
SAVE = section(IMPORT, "function saveQuestion(skipCheck = false)", "// AI classification modal handlers")


def run(checks, extra=""):
    # Stdin keeps the behavioral tests portable across Linux/macOS argument limits.
    source = "const assert=require('node:assert/strict');const window={};\n" + STATE
    source += "\n" + SETUP + NODES + MODAL + extra
    source += "\n(async()=>{\n" + checks + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    result = subprocess.run([shutil.which("node") or "node", "-"], input=source,
                            text=True, capture_output=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr + result.stdout


def test_partial_placement_allows_points_only_without_type_change_or_save():
    run(r"""
      document.getElementById('editCompulsory').value='原册';
      document.getElementById('editChapter').value='原章';
      document.getElementById('editKnowledge').value='原节';
      document.getElementById('editQType').value='single_choice';
      openClassifyModal();const work=runAIClassify();
      reply(requests.at(-1),{...result,placement_valid:false,compulsory:'',chapter:'',knowledge:'',
        question_form:'choice',classification_review:{required:true}});await work;
      assert.equal(document.getElementById('classifyApplyButton').disabled,true);
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,false);
      assert.ok(document.getElementById('classifyPlacementNotice').textContent.includes('只应用'));
      assert.equal(document.getElementById('recKnowledge').textContent,'未可靠定位');
      selectClassifiedChoiceType('multi_choice');
      assert.equal(document.getElementById('classifyApplyButton').disabled,true,'type confirmation cannot enable invalid placement');
      applyClassifyRecommendation();assert.equal(KnowledgePointState.snapshot().length,0);
      applyClassifyKnowledgePointsOnly();
      assert.equal(KnowledgePointState.snapshot()[0].code,'1803');
      assert.deepEqual(['editCompulsory','editChapter','editKnowledge','editQType'].map(id=>document.getElementById(id).value),
        ['原册','原章','原节','single_choice']);
      timers.forEach(fn=>fn());assert.equal(saves.length,0);
      assert.ok(toasts.at(-1).includes('仍待保存'));
    """)


def test_points_only_preserves_every_existing_field_and_shows_differences():
    run(r"""
      const original={code:'1796',name:'人工通项',role:'auxiliary',status:'suggested',origin:{kind:'manual'},
        evidence:'已有原文',evidence_source:'content',reason:'已有理由',scope:['(1)'],custom:{keep:['all']}};
      KnowledgePointState.hydrate([original,{code:'1859',name:'未被AI选中的旧点',role:'main',status:'confirmed'}]);
      const before=KnowledgePointState.snapshot();
      openClassifyModal();const work=runAIClassify();
      reply(requests.at(-1),{...result,question_form:'choice',knowledge_points:[...result.knowledge_points,
        {code:'1796',name:'AI改名',role:'main',status:'confirmed',evidence:'不同原文',reason:'不同依据'}]});await work;
      assert.equal(temporaryClassifyQuestionType,null);
      assert.ok(document.getElementById('classifyExistingPointDifferences').textContent.includes('人工通项'));
      assert.equal(temporaryClassifyKnowledgePoints.length,1);
      applyClassifyKnowledgePointsOnly();
      assert.deepEqual(KnowledgePointState.snapshot().slice(0,2),before);
      assert.equal(KnowledgePointState.snapshot()[2].code,'1803');
      timers.forEach(fn=>fn());assert.equal(saves.length,0);
    """)


@pytest.mark.parametrize("mutation", [
    "document.getElementById('editContent').value+='改'",
    "document.getElementById('editAnswerMarkdown').value+='改'",
    "document.getElementById('editReview').value+='改'",
    "document.getElementById('editQType').value='multi_choice'",
    "document.getElementById('editKnowledge').value='人工节'",
    "KnowledgePointState.add({code:'1796',name:'人工点'})",
    "window.activeCurriculumVersion='B'",
    "EditorState.useQuestion({id:9})",
    "closeClassifyModal()",
])
def test_points_only_rejects_stale_or_cancelled_results(mutation):
    run(r"""
      openClassifyModal();const work=runAIClassify();reply(requests.at(-1));await work;
    """ + mutation + r""";
      const before=KnowledgePointState.snapshot();applyClassifyKnowledgePointsOnly();
      assert.deepEqual(KnowledgePointState.snapshot(),before);
      assert.equal(saves.length,0);
    """)


def test_points_only_uses_selected_new_points_and_disables_empty_action():
    run(r"""
      openClassifyModal();const work=runAIClassify();reply(requests.at(-1));await work;
      const input=document.getElementById('classifyKnowledgePoints').children[0].children[0].children[0];
      input.checked=false;input.events.change();
      assert.equal(document.getElementById('classifyPointsApplyButton').disabled,true);
      applyClassifyKnowledgePointsOnly();assert.equal(KnowledgePointState.snapshot().length,0);
      input.checked=true;input.events.change();applyClassifyKnowledgePointsOnly();
      assert.equal(KnowledgePointState.snapshot().length,1);
      assert.equal('classification_selected' in KnowledgePointState.snapshot()[0],false);
    """)


def test_all_apply_does_not_autosave_later_typing_or_reopened_classification():
    for mutation in ("document.getElementById('editContent').value+='新输入'", "openClassifyModal()"):
        run(r"""
          openClassifyModal();const work=runAIClassify();reply(requests.at(-1));await work;
          applyClassifyRecommendation();
        """ + mutation + r""";
          timers.forEach(fn=>fn());assert.equal(saves.length,0);
          assert.equal(KnowledgePointState.snapshot()[0].code,'1803');
        """)


SAVE_SETUP = r"""
let referenceRefreshSequence=0;
window.QuestionReferenceState={invalidate(){},reset(){},hydrate(){}};
window.refreshCurrentQuestionReferences=()=>{};
const FigureLayoutState={snapshot:()=>({})};
const TikzState={contentAssets:[]};
function updateQuestionSaveButtonState(){}
let resolveMissing;
function showMissingCompulsoryModal(){return new Promise(resolve=>resolveMissing=resolve);}
""" + SAVE


def test_missing_classification_hands_off_after_save_lock_released():
    run(r"""
      const work=saveQuestion();assert.ok(saveQuestionInFlight);
      assert.equal(requests.length,0);resolveMissing('ai');assert.equal(await work,false);
      assert.equal(saveQuestionInFlight,null);
      assert.equal(document.getElementById('aiClassifyModal').dataset.classifyOpen,'true');
      assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/ai/classify');
      assert.equal(requests[0].options.body.get('content'),'求数列的和');
      closeClassifyModal();reply(requests[0]);
    """, SAVE_SETUP)


@pytest.mark.parametrize("mutation", [
    "document.getElementById('editContent').value+='改'",
    "KnowledgePointState.add({code:'1796'})",
    "window.activeCurriculumVersion='B'",
    "EditorState.useQuestion({id:9})",
    "closeClassifyModal()",
])
def test_missing_classification_does_not_launch_for_changed_context(mutation):
    run(r"""
      const work=saveQuestion();assert.ok(saveQuestionInFlight);
    """ + mutation + r""";
      resolveMissing('ai');assert.equal(await work,false);
      assert.equal(saveQuestionInFlight,null);assert.equal(requests.length,0);
    """, SAVE_SETUP)


def test_cancel_missing_classification_does_not_launch_ai():
    run(r"""
      const work=saveQuestion();resolveMissing('cancel');assert.equal(await work,false);
      assert.equal(saveQuestionInFlight,null);assert.equal(requests.length,0);
    """, SAVE_SETUP)
