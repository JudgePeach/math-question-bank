"""Run optional source evidence and import safety against a DOM/event harness."""

from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def review_script():
    source = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
    api_source = (ROOT / "static/js/api.js").read_text(encoding="utf-8")

    def section(start, end):
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    shipped = "\n".join([
        api_source[api_source.index("function safeImageUrl(value)"):api_source.index("function sanitizeRichHtml(value)")],
        section("const parsedSourceVisionVerifications", "function blockImportResetWhileSaving"),
        section("function renderParsedQuestionsList", "function setupCardCategoryLinkage"),
        section("function validateParsedQuestionBeforeImport", "function confirmClearAllParsed"),
        section("function getCheckedUnsavedIndices", "// SIDEBAR QUESTION SOURCE"),
        section("async function generateSingleAnswer", "window.generateSingleAnswer ="),
    ])
    setup = r"""
const assert = require('node:assert/strict');
const elements = new Map();
class Element extends EventTarget {
  constructor(tag = 'div') {
    super(); this.tagName = tag; this.children = []; this.dataset = {};
    this.value = ''; this.checked = false; this.disabled = false;
    this.className = ''; this._html = ''; this.textContent = ''; this.open = false;
    this.classList = {add(){}, remove(){}, toggle(){}};
  }
  set id(value) { this._id = value; elements.set(value, this); }
  get id() { return this._id || ''; }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  insertBefore(child, ref) {
    child.parentElement = this;
    const pos = this.children.indexOf(ref);
    if (pos < 0) this.children.push(child); else this.children.splice(pos, 0, child);
  }
  remove() {
    if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(x => x !== this);
    if (this.id) elements.delete(this.id);
  }
  set innerHTML(value) {
    this._html = value;
    this.children.forEach(child => child.remove()); this.children = [];
    for (const match of value.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)) {
      const el = new Element(match[1]); const attrs = match[2];
      el.className = (attrs.match(/class="([^"]*)"/) || [,''])[1];
      const id = attrs.match(/id="([^"]*)"/); if (id) el.id = id[1];
      const index = attrs.match(/data-index="(\d+)"/); if (index) el.dataset.index = index[1];
      el.checked = /\bchecked\b/.test(attrs); el.disabled = /\bdisabled\b/.test(attrs);
      this.appendChild(el);
    }
  }
  get innerHTML() { return this._html; }
  matches(selector) {
    return selector.startsWith('.') ? this.className.split(/\s+/).includes(selector.slice(1))
      : this.tagName === selector;
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getAttribute(name) { return name === 'data-index' ? this.dataset.index : null; }
  focus() {} scrollIntoView() {}
}
const root = new Element();
function create(id, parent = root) { const el = new Element(); el.id = id; parent.appendChild(el); return el; }
const wrapper = create('parsedQuestionsWrapper');
create('parsedCardsContainer', wrapper);
for (const id of ['parsedCountBadge', 'selectedCountBadge', 'selectAllCheckbox', 'saveAllParsedBtnText', 'saveAllParsedBtn']) create(id);
const document = {
  createElement: tag => new Element(tag),
  getElementById: id => elements.get(id) || null,
  querySelectorAll: selector => root.querySelectorAll(selector)
};
const asString = value => value == null ? '' : String(value);
const window = { location: {href:'http://localhost:8000/', origin:'http://localhost:8000'}, MathBankSafe: {
  escapeAttribute: String, escapeText: String, sanitizePlainText: String, safeImageUrl
}};
let parsedQuestionsData = [];
let parsedQuestionsGeneration = 0;
const parsedQuestionSaveInFlight = new Map();
let parsedBatchSaveInFlight = null;
const parsedDuplicateSnapshots = new Map();
const logs = [], toasts = [], requests = [];
const localStorage = {getItem: () => ''};
const showToast = message => toasts.push(message);
const appendImportLog = message => logs.push(message);
const debounce = fn => fn;
const invalidateParsedDuplicateCheck = () => {};
const renderParsedCardPreview = () => {};
const appendSafeImageBadge = () => {};
const loadQuestions = () => {};
const loadCategories = () => {};
const safePersistedTikzAssets = () => [];
const safeDuplicateImagePaths = () => [];
const buildParsedQuestionDuplicateItem = () => ({image_paths: []});
let precheck = async () => ({ok: false});
const precheckParsedQuestionDuplicates = (...args) => precheck(...args);
const fetch = (url, options) => new Promise(resolve => requests.push({url, options, resolve}));
function setupCardCategoryLinkage(card) {
  card.querySelector('.card-qtype').value = 'single_choice';
  card.querySelector('.card-compulsory').value = '高中';
  card.querySelector('.card-chapter').value = '函数';
}
function pendingQuestion() {
  return {content: '求 $x-1$ 的值', answer_markdown: '', source_review: {
    required: true, confirmed: true, reasons: ['可能漏公式 <img src=x onerror=bad()>'],
    source_excerpt: '原文 $x+1$ <script>bad()</script>'
  }};
}
function show(questions) {
  replaceParsedQuestions(questions); renderParsedQuestionsList(questions);
  requests.length = 0; logs.length = 0; toasts.length = 0;
  return document.getElementById('parsed-card-0');
}
function verifiedQuestion() {
  return {content:'计算 $1+1$。',answer_markdown:'',source_review:{required:false,verified_by:'vision',
    reasons:['原文记录'],source_excerpt:'计算 $1+1$。',verification:{evidence:'原页一致。'}}};
}
function edit(card, selector, text) {
  const el = card.querySelector(selector); el.value = text; el.dispatchEvent(new Event('input'));
}
function success(solution = '解答') { return {ok: true, json: async () => ({status: 'success', solution})}; }
"""
    checks = r"""
const scenarios = {
  async pending_is_optional_and_save_still_prechecks() {
    const q=pendingQuestion(); const before=JSON.stringify(q.source_review);
    const card = show([q, {content:'正常题', answer_markdown:''}]);
    const selected = card.querySelector('.card-select-checkbox');
    assert.equal(selected.checked, true);
    assert.equal(selected.disabled, false);
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.equal(card.querySelector('.card-source-review-status').textContent,'查看原文与提取说明（可选）');
    const panel=card.querySelector('.card-source-review-panel');
    assert.equal(panel.tagName,'details'); assert.equal(panel.open,false);
    assert.equal(card.querySelector('script'), null, 'source text must never become HTML');
    assert.ok(panel.querySelector('details').children[1].textContent.includes('<script>'));
    toggleSelectAllParsed(true); assert.deepEqual(getCheckedUnsavedIndices(), [0,1]);
    assert.equal(validateParsedQuestionBeforeImport(0),true);
    assert.equal(parsedQuestionNeedsSourceReview(0),true,'optional is not certified');
    const checks=[];
    precheck=async options=>{checks.push(options.indices);return {ok:false,invalid:true};};
    assert.equal(await saveParsedQuestion(0),false);
    assert.equal(await saveAllParsedQuestions(),false);
    assert.deepEqual(checks,[[0],[0,1]],'single and batch must still run duplicate preflight');
    assert.equal(requests.length,0,'invalid duplicate preflight cannot write');
    assert.equal(JSON.stringify(q.source_review),before,'display does not fabricate verification');
    toggleSelectAllParsed(false); invertSelectParsed(); assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
  },
  async edits_keep_optional_evidence_and_regular_validation() {
    const q=pendingQuestion(); const card=show([q]);
    const original=card.querySelector('.card-content-textarea').value;
    edit(card,'.card-content-textarea',original+'修改'); edit(card,'.card-content-textarea',original);
    edit(card,'.card-answer-textarea','人工解析');
    assert.equal(parsedQuestionNeedsSourceReview(0),true);
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    assert.equal(validateParsedQuestionBeforeImport(0,{notify:false,focus:false}),true);
    edit(card,'.card-content-textarea','');
    assert.equal(validateParsedQuestionBeforeImport(0,{notify:false,focus:false}),false,'empty content stays invalid');
    edit(card,'.card-content-textarea',original);
    card.querySelector('.card-chapter').value='';
    assert.equal(validateParsedQuestionBeforeImport(0,{notify:false,focus:false}),false,'missing category stays invalid');
    assert.equal(q.source_review.required,true);
    assert.equal(requests.length,0);
  },
  async precheck_stale_snapshot_still_aborts() {
    const card=show([pendingQuestion()]);
    let resolveCheck;
    precheck=()=>new Promise(resolve=>{resolveCheck=resolve;});
    const work=saveParsedQuestion(0);
    edit(card,'.card-content-textarea','查重期间修改题干');
    resolveCheck({ok:false,stale:true});
    assert.equal(await work,false);
    assert.equal(requests.length,0,'stale duplicate preflight must stop the actual POST');
  },
  async requested_answers_include_pending_and_preserve_edit_race() {
    const q=pendingQuestion(); const card=show([q,{content:'已有原答案',answer_markdown:'C'}]);
    assert.equal(requests.length,0,'display does not request answers');
    const batch=processAsyncAnswerGeneration(parsedQuestionsData);
    assert.equal(requests.length,1,'explicit batch request may include pending source evidence');
    assert.equal(requests[0].options.body.get('content'),q.content);
    edit(card,'.card-answer-textarea','教师正在编辑');
    requests[0].resolve(success('旧批量答案'));await batch;
    assert.equal(q.answer_markdown,'');
    assert.equal(card.querySelector('.card-answer-textarea').value,'教师正在编辑');
    assert.equal(parsedQuestionsData[1].answer_markdown,'C','original short answers are preserved');
    const single=generateSingleAnswer(0);
    assert.equal(requests.length,2);
    edit(card,'.card-answer-textarea','教师继续编辑');
    requests.at(-1).resolve(success('旧单题答案'));await single;
    assert.equal(q.answer_markdown,'');
    assert.equal(card.querySelector('.card-answer-textarea').value,'教师继续编辑');
    const next=generateSingleAnswer(0);
    requests.at(-1).resolve(success('新模型答案'));await next;
    assert.equal(card.querySelector('.card-answer-textarea').value,'新模型答案');
    assert.equal(q.source_review.required,true,'answer generation does not certify original-source evidence');
    assert.equal(parsedQuestionNeedsSourceReview(0),true);
  },
  async requested_queue_keeps_concurrency_and_snapshot_protection() {
    const questions=Array.from({length:4},pendingQuestion);show(questions);
    const work=processAsyncAnswerGeneration(parsedQuestionsData);
    assert.equal(requests.length,3);
    edit(document.getElementById('parsed-card-3'),'.card-content-textarea','排队期间的新题干');
    edit(document.getElementById('parsed-card-1'),'.card-content-textarea','请求期间的新题干');
    for(const request of requests.slice())request.resolve(success());
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests.length,4);
    assert.equal(requests[3].options.body.get('content'),'排队期间的新题干','queued request reads its current snapshot');
    requests[3].resolve(success('对应排队新题干的答案'));await work;
    assert.equal(questions[1].answer_markdown,'','in-flight edit rejects stale answer');
    assert.equal(questions[3].answer_markdown,'对应排队新题干的答案');
    assert.ok(questions.every(q=>q.source_review.required===true));
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1,2,3]);
  },
  async unmatched_report_and_new_import() {
    const q=pendingQuestion(); show([q]);
    renderSourceIntegrityReport({source_review_count:1,unmatched_source:[{
      source_excerpt:'24. 求 $x$ <img src=x onerror=bad()>',reason:'可能漏掉整题'}]});
    const report=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(wrapper.firstChild,report); assert.equal(report.tagName,'details'); assert.equal(report.open,false);
    assert.ok(report.querySelector('summary').textContent.includes('可选查看'));
    const excerpt=report.querySelector('details');
    assert.ok(excerpt.querySelector('summary').textContent.includes('可能漏掉整题'));
    assert.ok(excerpt.children[1].textContent.includes('24.'));
    assert.equal(report.querySelector('img'),null);
    replaceParsedQuestions([]);assert.equal(document.getElementById('parsedSourceIntegrityReport'),null);
    replaceParsedQuestions([q]);assert.equal(parsedQuestionNeedsSourceReview(0),true);
  },
  async local_source_images_only() {
    const q = pendingQuestion();
    q.source_review.source_excerpt = [
      '![原图](/static/uploads/a.png)',
      '![重复](/static/uploads/a.png)',
      '![测试图](/static/test_uploads/b.webp)',
      '![外站](https://other.example/evil.png)',
      '![本地任意路径](/etc/a.png)',
      '![本地HTML](/static/uploads/evil.html)',
      '![主动SVG](/static/uploads/evil.svg)',
      '![穿越](/static/uploads/%2e%2e/%2e%2e/private.png)',
      '<img src="/static/uploads/html.png" onerror="bad()">'
    ].join('\n');
    const card = show([q]);
    const details = card.querySelector('.card-source-review-panel').querySelector('details');
    assert.deepEqual(details.querySelectorAll('img').map(img => img.src), [
      '/static/uploads/a.png', '/static/test_uploads/b.webp'
    ]);
    assert.equal(details.children[1].textContent, q.source_review.source_excerpt, 'raw source must remain available');
    assert.equal(card.querySelector('.card-content-textarea').value, q.content, 'source images must not be inserted into the question');
  }
};
scenarios[process.argv[1]]().catch(error => { console.error(error); process.exitCode = 1; });
"""
    return setup + shipped + checks


@pytest.mark.parametrize("scenario", [
    "pending_is_optional_and_save_still_prechecks", "edits_keep_optional_evidence_and_regular_validation",
    "precheck_stale_snapshot_still_aborts", "requested_answers_include_pending_and_preserve_edit_race",
    "requested_queue_keeps_concurrency_and_snapshot_protection", "unmatched_report_and_new_import",
    "local_source_images_only",
])
def test_import_source_review_in_shipped_js(review_script, scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    result = subprocess.run(
        [node, "-e", "eval(require('node:fs').readFileSync(0,'utf8'))", scenario],
        input=review_script, cwd=ROOT, capture_output=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not os.environ.get('MATHBANK_TEST_PDF_BEIJING_TASK'), reason='opt-in captured 28-question task; no network')
def test_captured_beijing_21_pending_reviews_leave_all_28_questions_selectable(review_script):
    task = json.loads(Path(os.environ['MATHBANK_TEST_PDF_BEIJING_TASK']).read_text())
    assert len(task['data']) == 28
    assert sum(q.get('source_review', {}).get('required') is True for q in task['data']) == 21
    script = review_script.rsplit('scenarios[process.argv[1]]', 1)[0]
    script += '\nconst captured=' + json.dumps(task, ensure_ascii=False) + ';\n'
    script += r"""
const evidence=JSON.stringify(captured.data.map(q=>q.source_review||null));
show(captured.data); renderSourceIntegrityReport(captured.diagnostics);
assert.equal(document.querySelectorAll('.card-source-review-confirm').length,0);
const report=document.getElementById('parsedSourceIntegrityReport');
assert.equal(report.tagName,'details');assert.equal(report.open,false);
assert.ok(report.querySelector('summary').textContent.includes('3 张配图未自动归位'));
for(const panel of document.querySelectorAll('.card-source-review-panel'))assert.equal(panel.open,false);
toggleSelectAllParsed(false);toggleSelectAllParsed(true);
assert.deepEqual(getCheckedUnsavedIndices(),Array.from({length:28},(_,i)=>i));
for(let i=0;i<28;i++)assert.equal(validateParsedQuestionBeforeImport(i,{notify:false,focus:false}),true);
assert.equal(parsedQuestionsData.filter(q=>q.source_review?.required===true).length,21);
assert.equal(parsedQuestionsData.filter((q,i)=>parsedQuestionNeedsSourceReview(i,q)).length,21);
assert.equal(JSON.stringify(parsedQuestionsData.map(q=>q.source_review||null)),evidence);
assert.equal(requests.length,0,'selection and validation do not call models or write questions');
"""
    result = subprocess.run([shutil.which('node'), '-e', "eval(require('node:fs').readFileSync(0,'utf8'))"],
                            input=script, cwd=ROOT, capture_output=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
