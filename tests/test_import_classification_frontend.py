"""Exercise document classification recovery without AI, services, or database writes."""

from pathlib import Path
import json
import shutil
import subprocess

import pytest

from test_document_import_resilience_frontend import session_harness
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def linkage_script():
    source = (ROOT / 'static/js/import.js').read_text()
    start = source.index('function setupCardCategoryLinkage(')
    return source[start:source.index('function renderParsedCardPreview(', start)]


def run_node(script):
    node = shutil.which('node')
    assert node, 'Node is required for the executable classification regression'
    result = subprocess.run([node, '-e', "eval(require('node:fs').readFileSync(0,'utf8'))"],
                            input=script, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope='module')
def classification_script(review_script):
    base = review_script.rsplit('scenarios[process.argv[1]]', 1)[0]
    return base + linkage_script() + r"""
let categoryTree={'必修一':{'1. 集合':['1.1 集合的概念'],'2. 函数':['2.1 函数']},
                  '必修二':{'9. 统计':['9.2 用样本估计总体']}};
window.categoryTree=categoryTree;window.activeCurriculumVersion='A';
function classified(overrides={}) {
 return {content:'原题 $x+1=2$。',answer_markdown:'原版答案',question_type:'single_choice',
   category_compulsory:'必修一',category_chapter:'1. 集合',category_knowledge:'',...overrides};
}
function review(required=true,overrides={}) {
 return {required,message:'章节或小节无法唯一匹配，请确认分类。',reason_codes:['knowledge_not_found'],
   original:{category_compulsory:'必修一',category_chapter:'集合',category_knowledge:'未知小节'},...overrides};
}
function cards(questions) {
 show(questions);
 questions.forEach((q,i)=>document.getElementById(`parsed-card-${i}`).querySelector('.card-qtype').value='single_choice');
 return questions.map((q,i)=>document.getElementById(`parsed-card-${i}`));
}
function change(card,selector,value) {
 const input=card.querySelector(selector);input.value=value;input.dispatchEvent(new Event('change',{bubbles:true}));
}
const classificationScenarios={
 async pending_cards_do_not_stop_default_batch_and_manual_selection_is_validated() {
  const ordinary=classified(),normalized=classified({classification_review:review(false)});
  const pending=classified({category_compulsory:'',category_chapter:'',classification_review:review()});
  const [normalCard,normalizedCard,pendingCard]=cards([ordinary,normalized,pending]);
  assert.equal(normalCard.querySelector('.card-select-checkbox').checked,true);
  assert.equal(normalizedCard.querySelector('.card-select-checkbox').checked,true);
  assert.equal(pendingCard.querySelector('.card-select-checkbox').checked,false);
  assert.equal(pendingCard.querySelector('.card-compulsory').value,'','no implicit first book');
  assert.equal(pendingCard.querySelector('.card-chapter').value,'','no implicit first chapter');
  toggleSelectAllParsed(true);assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
  pendingCard.querySelector('.card-select-checkbox').checked=true;
  const checked=[];precheck=async options=>{checked.push(options.indices);return {ok:false};};
  const work=saveAllParsedQuestions();await new Promise(setImmediate);
  assert.deepEqual(checked,[[0,1]],'only qualified cards are duplicate checked');
  assert.equal(requests.length,2,'a pending card does not stop qualified cards');
  assert.ok(requests.every(request=>request.url==='/api/questions'));
  requests.forEach(request=>request.resolve(success()));
  assert.equal(await work,true);
  assert.deepEqual(parsedQuestionsData.map(q=>!!q.saved),[true,true,false]);
  assert.equal(pending.import_outcome.status,'skipped');
  assert.ok(pending.import_outcome.reason.includes('当前教材中的学段'));
  assert.ok(document.getElementById('parsedBatchImportReport').children[0].textContent.includes('成功 2，失败 0，跳过 1'));
  assert.equal(parsedQuestionsData.length,3,'all cards remain in the session');
  assert.equal(normalized.classification_review.required,false);
 },
 async chapter_confirmation_is_explicit_and_does_not_certify_source_content() {
  const q=classified({classification_review:review(),source_review:{required:true,reasons:['原文待比对']}});
  const original=JSON.stringify(q.classification_review.original),source=JSON.stringify(q.source_review);
  const [card]=cards([q]);
  assert.equal(validateParsedQuestionBeforeImport(0),false,'cleared unknown subsection needs a teacher decision');
  const confirm=card.querySelector('.card-classification-confirm');
  assert.equal(confirm.hidden,false);assert.equal(confirm.textContent,'按整章保留');
  confirm.dispatchEvent(new Event('click'));
  assert.equal(q.classification_review.required,false);
  assert.equal(q.classification_review.confirmed_by,'user');
  assert.equal(JSON.stringify(q.classification_review.original),original);
  assert.equal(JSON.stringify(q.source_review),source);
  assert.equal(validateParsedQuestionBeforeImport(0),true);
  assert.equal(card.querySelector('.card-select-checkbox').checked,true);
  assert.equal(parsedQuestionNeedsSourceReview(0),true,'classification approval does not approve the transcription');
 },
 async parent_changes_clear_children_and_real_selection_confirms() {
  const q=classified({category_knowledge:'1.1 集合的概念',classification_review:review(false)});
  const [card]=cards([q]);
  change(card,'.card-compulsory','必修二');
  assert.equal(card.querySelector('.card-chapter').value,'');
  assert.equal(card.querySelector('.card-knowledge').value,'');
  assert.equal(q.classification_review.required,true);
  change(card,'.card-chapter','9. 统计');
  assert.equal(q.category_compulsory,'必修二');assert.equal(q.category_chapter,'9. 统计');
  assert.equal(q.category_knowledge,'');assert.equal(q.classification_review.required,false);
  change(card,'.card-knowledge','9.2 用样本估计总体');
  assert.equal(q.classification_review.confirmed_classification.category_knowledge,'9.2 用样本估计总体');
  assert.equal(validateParsedQuestionBeforeImport(0),true);
 },
 async stale_paths_and_catalog_changes_require_confirmation() {
  const q=classified({classification_review:review()});const [card]=cards([q]);
  assert.equal(confirmParsedClassificationReview(0),true);
  card.querySelector('.card-knowledge').value='不存在的小节';
  assert.equal(validateParsedQuestionBeforeImport(0),false);
  assert.ok(toasts.at(-1).includes('小节'));
  assert.equal(q.classification_review.required,true);
  const wholeChapter=card.querySelector('.card-classification-confirm');
  assert.equal(wholeChapter.hidden,false);assert.equal(wholeChapter.textContent,'按整章保留');
  wholeChapter.dispatchEvent(new Event('click'));
  assert.equal(card.querySelector('.card-knowledge').value,'');
  assert.equal(q.classification_review.required,false);
  change(card,'.card-knowledge','1.1 集合的概念');
  assert.equal(q.classification_review.required,false);
  categoryTree={...categoryTree,'自定义册':{'自定义章':[]}};window.categoryTree=categoryTree;
  refreshParsedClassificationReviews();
  assert.equal(q.classification_review.required,true,'even same-version catalog edits revoke the old catalog decision');
  assert.equal(card.querySelector('.card-select-checkbox').checked,false);
  const confirm=card.querySelector('.card-classification-confirm');
  assert.equal(confirm.hidden,false);assert.equal(confirm.textContent,'确认当前分类');
  assert.equal(confirmParsedClassificationReview(0),true);
  window.activeCurriculumVersion='B';
  assert.equal(validateParsedQuestionBeforeImport(0),false,'curriculum switching also invalidates classification approval');
  assert.equal(q.classification_review.original.category_chapter,'集合');
 },
 async fresh_cards_refresh_new_paths_without_losing_old_values_or_rebinding() {
  const q=classified({category_knowledge:'1.1 集合的概念',classification_review:review(false)});
  const ordinary=classified();const [card,ordinaryCard]=cards([q,ordinary]);
  const original=JSON.stringify(q.classification_review.original);
  categoryTree={'必修一':{'2. 函数':['2.1 函数']},'必修三':{'1. 统计研究':['1.1 数据分析']}};
  window.categoryTree=categoryTree;refreshParsedClassificationReviews();refreshParsedClassificationReviews();
  assert.ok(card.querySelector('.card-compulsory').children.some(option=>option.value==='必修三'));
  assert.equal(ordinaryCard.querySelector('.card-compulsory').children.some(option=>option.value==='必修三'),false,
    'ordinary historical cards keep their existing behavior');
  assert.equal(card.querySelector('.card-chapter').value,'1. 集合');
  assert.equal(card.querySelector('.card-knowledge').value,'1.1 集合的概念');
  assert.ok(card.querySelector('.card-chapter').children.find(option=>option.value==='1. 集合').textContent.includes('不在当前目录'));
  assert.equal(q.classification_review.required,true);
  let confirmations=0;const originalConfirm=confirmParsedClassificationReview;
  confirmParsedClassificationReview=(...args)=>{confirmations++;return originalConfirm(...args);};
  change(card,'.card-compulsory','必修三');
  assert.equal(card.querySelector('.card-chapter').value,'');
  change(card,'.card-chapter','1. 统计研究');
  change(card,'.card-knowledge','1.1 数据分析');
  assert.equal(confirmations,3,'option refresh must not bind duplicate change handlers');
  assert.equal(q.classification_review.required,false);
  assert.equal(q.category_compulsory,'必修三');assert.equal(q.category_chapter,'1. 统计研究');
  assert.equal(JSON.stringify(q.classification_review.original),original);
  assert.equal(validateParsedQuestionBeforeImport(0),true);
 },
 async original_suggestions_are_plain_text_and_saved_cards_remain_saved() {
  const original={category_compulsory:'<script>bad()</script>',category_chapter:'<img src=x onerror=bad()>',category_knowledge:'未知'};
  const q=classified({classification_review:review(false,{original,candidates:['<img src=x>']}),saved:true});
  const [card]=cards([q]);
  const text=card.querySelector('.card-classification-original');
  assert.ok(text.textContent.includes('<script>bad()</script>'));assert.ok(text.textContent.includes('<img src=x>'));
  assert.equal(card.querySelector('script'),null);
  assert.equal(q.saved,true);assert.equal(card.querySelector('.card-select-checkbox').disabled,true);
  toggleSelectAllParsed(true);assert.equal(card.querySelector('.card-select-checkbox').checked,false);
  assert.deepEqual(q.classification_review.original,original);
 }
};
"""


@pytest.mark.parametrize('scenario', [
    'pending_cards_do_not_stop_default_batch_and_manual_selection_is_validated',
    'chapter_confirmation_is_explicit_and_does_not_certify_source_content',
    'parent_changes_clear_children_and_real_selection_confirms',
    'stale_paths_and_catalog_changes_require_confirmation',
    'original_suggestions_are_plain_text_and_saved_cards_remain_saved',
    'fresh_cards_refresh_new_paths_without_losing_old_values_or_rebinding',
])
def test_document_classification_controls(classification_script, scenario):
    output = run_node(classification_script + '\nclassificationScenarios[' + json.dumps(scenario) + r""" ]()
.then(()=>console.log('completed'))
.catch(error=>{console.error(error);process.exitCode=1});
""")
    assert 'completed' in output, 'scenario must finish rather than exit with an unresolved Promise'


def test_batch_partial_failure_keeps_all_cards_and_plain_text_reasons(classification_script):
    output = run_node(classification_script + r"""
(async()=>{
 const valid=classified(),failed=classified(),pending=classified({category_compulsory:'',category_chapter:'',classification_review:review()});
 const list=cards([valid,failed,pending]);list[2].querySelector('.card-select-checkbox').checked=true;
 precheck=async options=>{assert.deepEqual(options.indices,[0,1]);return {ok:false};};
 const work=saveAllParsedQuestions();await new Promise(setImmediate);
 assert.equal(requests.length,2);
 requests[0].resolve(success());
 requests[1].resolve({ok:false,status:503,json:async()=>({detail:'模拟服务错误 <img src=x onerror=bad()>'})});
 assert.equal(await work,true);
 assert.deepEqual(parsedQuestionsData.map(q=>q.import_outcome.status),['success','failed','skipped']);
 assert.deepEqual(parsedQuestionsData.map(q=>!!q.saved),[true,false,false]);
 const report=document.getElementById('parsedBatchImportReport');
 assert.ok(report.children[0].textContent.includes('成功 1，失败 1，跳过 1'));
 assert.ok(report.children[2].textContent.includes('<img src=x onerror=bad()>'));
 assert.equal(report.querySelector('img'),null,'error messages must stay plain text');
 assert.equal(parsedQuestionsData.length,3);assert.equal(parsedQuestionSaveInFlight.size,0);
 assert.ok(toasts.at(-1).includes('成功 1，失败 1，跳过 1'));
 console.log('completed');
})().catch(error=>{console.error(error);process.exitCode=1});
""")
    assert 'completed' in output


@pytest.mark.parametrize('outcome', [{'ok': False, 'stale': True}, {'ok': False, 'invalid': True}])
def test_batch_precheck_exit_keeps_per_card_skip_reasons(classification_script, outcome):
    output = run_node(classification_script + '\nconst fixtureOutcome=' + json.dumps(outcome) + r""";
(async()=>{
 cards([classified(),classified()]);precheck=async()=>fixtureOutcome;
 assert.equal(await saveAllParsedQuestions(),false);
 assert.equal(requests.length,0);assert.equal(parsedQuestionsData.length,2);
 assert.ok(parsedQuestionsData.every(q=>q.import_outcome.status==='skipped'&&q.import_outcome.reason));
 assert.ok(document.getElementById('parsedBatchImportReport').children[0].textContent.includes('跳过 2'));
 console.log('completed');
})().catch(error=>{console.error(error);process.exitCode=1});
""")
    assert 'completed' in output


def test_session_preserves_pending_and_confirmed_classification_without_paid_requests(pdf_script):
    question = {
        'content': '原题', 'answer_markdown': '原版答案', 'question_type': 'single_choice',
        'category_compulsory': '必修一', 'category_chapter': '1. 集合', 'category_knowledge': '',
        'classification_review': {'required': True, 'reason_codes': ['knowledge_not_found'],
                                  'original': {'category_compulsory': '必修一', 'category_chapter': '集合',
                                               'category_knowledge': '未知小节'}},
    }
    saved = {'version': 1, 'phase': 'completed', 'questions': [question], 'baselineQuestions': [question]}
    setup = "let categoryTree={'必修一':{'1. 集合':['1.1 集合的概念']}};window.categoryTree=categoryTree;window.activeCurriculumVersion='A';\n"
    first = session_harness(pdf_script, saved) + setup + linkage_script() + r"""
listeners.DOMContentLoaded();
const q=parsedQuestionsData[0];
assert.equal(q.classification_review.required,true);
assert.equal(validateParsedQuestionBeforeImport(0),false);
assert.equal(confirmParsedClassificationReview(0),true);
window.MathBankDocumentImportSession.flush();
assert.equal(requests.length,0,'session recovery and classification decisions cannot request models or writes');
console.log(storage.get('mathbank_document_import_session_v1'));
"""
    confirmed = json.loads(run_node(first).splitlines()[-1])
    assert confirmed['questions'][0]['classification_review']['required'] is False
    assert confirmed['questions'][0]['classification_review']['confirmed_by'] == 'user'
    assert confirmed['questions'][0]['classification_review']['original'] == question['classification_review']['original']
    second = session_harness(pdf_script, confirmed) + setup + linkage_script() + r"""
listeners.DOMContentLoaded();
assert.equal(parsedQuestionsData[0].classification_review.required,false,'refresh preserves the real teacher decision');
assert.equal(validateParsedQuestionBeforeImport(0),true);
const readyCatalog=categoryTree;categoryTree={};window.categoryTree=categoryTree;
assert.equal(validateParsedQuestionBeforeImport(0),false,'saved labels cannot bypass a directory that is still loading');
assert.equal(parsedQuestionsData[0].classification_review.required,false,'loading alone does not erase a saved teacher decision');
categoryTree=readyCatalog;window.categoryTree=categoryTree;
assert.equal(validateParsedQuestionBeforeImport(0),true);
categoryTree={'必修一':{'2. 函数':[]}};window.categoryTree=categoryTree;
refreshParsedClassificationReviews();
assert.equal(parsedQuestionsData[0].classification_review.required,true,'a restored option is not proof that the current directory contains it');
assert.equal(validateParsedQuestionBeforeImport(0),false);
assert.equal(requests.length,0);
"""
    run_node(second)


def test_session_restores_teacher_confirmation_before_catalogs_finish_loading(pdf_script):
    values = {'category_compulsory': '必修一', 'category_chapter': '1. 集合', 'category_knowledge': ''}
    question = {'content': '已人工确认分类的恢复题', 'answer_markdown': '', 'question_type': 'single_choice',
                **values, 'classification_review': {'required': False, 'confirmed_by': 'user',
                    'confirmed_classification': values, 'original': {'category_chapter': '集合'}}}
    saved = {'version': 1, 'phase': 'completed', 'questions': [question], 'baselineQuestions': [question]}
    script = session_harness(pdf_script, saved) + linkage_script() + r"""
let categoryTree={};window.categoryTree=categoryTree;window.activeCurriculumVersion='A';
listeners.DOMContentLoaded();
assert.equal(parsedQuestionsData[0].classification_review.required,false,
    'temporary empty controls before catalog loading must not revoke a restored teacher confirmation');
assert.equal(validateParsedQuestionBeforeImport(0),false,'still wait for a real catalog before saving');
categoryTree={'必修一':{'1. 集合':['1.1 集合的概念']}};window.categoryTree=categoryTree;
window.MathBankDocumentImportSession.refreshCatalogs();refreshParsedClassificationReviews();
assert.equal(parsedQuestionsData[0].classification_review.required,false);
assert.equal(validateParsedQuestionBeforeImport(0),true);
assert.equal(requests.length,0);
"""
    run_node(script)
