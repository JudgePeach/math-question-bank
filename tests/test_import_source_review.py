"""Run the shipped import review gates against a small DOM/event harness."""

from pathlib import Path
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
        section("let parsedSourceReviewConfirmations", "function blockImportResetWhileSaving"),
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
    this.className = ''; this._html = ''; this.textContent = '';
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
function approve(card, checked = true) {
  const cb = card.querySelector('.card-source-review-confirm'); cb.checked = checked;
  cb.dispatchEvent(new Event('change'));
}
function edit(card, selector, text) {
  const el = card.querySelector(selector); el.value = text; el.dispatchEvent(new Event('input'));
}
function success(solution = '解答') { return {ok: true, json: async () => ({status: 'success', solution})}; }
"""
    checks = r"""
const scenarios = {
  async default_and_save_gates() {
    const card = show([pendingQuestion(), {content:'正常题', answer_markdown:''}]);
    const blocked = card.querySelector('.card-select-checkbox');
    assert.equal(blocked.checked, false);
    assert.equal(blocked.disabled, true);
    assert.equal(card.querySelector('.card-source-review-status').textContent, '待核对原文');
    assert.equal(card.querySelector('script'), null, 'source text must never become HTML');
    assert.ok(card.querySelector('details').children[1].textContent.includes('<script>'));
    toggleSelectAllParsed(true);
    assert.deepEqual(getCheckedUnsavedIndices(), [1]);
    blocked.disabled = false; blocked.checked = true;
    assert.deepEqual(getCheckedUnsavedIndices(), [1], 'batch collection must recheck pending review');
    assert.equal(await saveParsedQuestion(0), false);
    toggleSelectAllParsed(false); blocked.checked = true;
    assert.equal(await saveAllParsedQuestions(), false);
    assert.equal(requests.length, 0, 'neither save path may write a pending question');
    invertSelectParsed();
    assert.deepEqual(getCheckedUnsavedIndices(), [1]);
  },
  async approval_and_edit_invalidation() {
    const q = pendingQuestion(); const card = show([q]);
    approve(card);
    assert.equal(parsedQuestionNeedsSourceReview(0), false);
    toggleSelectAllParsed(true);
    assert.deepEqual(getCheckedUnsavedIndices(), [0]);
    assert.equal(validateParsedQuestionBeforeImport(0), true);
    const original = card.querySelector('.card-content-textarea').value;
    edit(card, '.card-content-textarea', original + '改');
    edit(card, '.card-content-textarea', original);
    assert.equal(parsedQuestionNeedsSourceReview(0), true, 'undo must not silently restore teacher approval');
    assert.equal(card.querySelector('.card-source-review-confirm').checked, false);
    assert.equal(card.querySelector('.card-select-checkbox').checked, false);
    approve(card); edit(card, '.card-answer-textarea', '人工解析');
    assert.equal(validateParsedQuestionBeforeImport(0, {notify:false, focus:false}), false);
    approve(card);
    // A programmatic edit with no event must still fail the save-time snapshot.
    card.querySelector('.card-content-textarea').value += '静默变化';
    assert.equal(await saveParsedQuestion(0), false);
    assert.equal(requests.length, 0);
  },
  async approval_cannot_change_during_precheck() {
    const card = show([pendingQuestion()]); approve(card);
    let resolveCheck;
    precheck = () => new Promise(resolve => { resolveCheck = resolve; });
    const save = saveParsedQuestion(0);
    approve(card, false);
    resolveCheck({ok:false});
    assert.equal(await save, false);
    assert.equal(requests.length, 0, 'revoke while waiting must stop the actual POST');
  },
  async automatic_answers_skip_and_invalidate() {
    const q = pendingQuestion();
    const card = show([q, {content:'正常题', answer_markdown:''}]);
    await generateSingleAnswer(0);
    assert.equal(requests.length, 0);
    const work = processAsyncAnswerGeneration(parsedQuestionsData);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].options.body.get('content'), '正常题');
    requests[0].resolve(success()); await work;
    assert.equal(q.answer_markdown, '');
    assert.ok(logs.some(line => line.includes('暂缓 1 道待核对题')));
    approve(card);
    const single = generateSingleAnswer(0);
    edit(card, '.card-answer-textarea', '教师正在编辑');
    requests.at(-1).resolve(success('旧模型答案')); await single;
    assert.equal(card.querySelector('.card-answer-textarea').value, '教师正在编辑');
    assert.equal(q.answer_markdown, '');
    assert.equal(parsedQuestionNeedsSourceReview(0), true);
    approve(card);
    const next = generateSingleAnswer(0);
    requests.at(-1).resolve(success('新模型答案')); await next;
    assert.equal(card.querySelector('.card-answer-textarea').value, '新模型答案');
    assert.equal(parsedQuestionNeedsSourceReview(0), true, 'new AI answer is not teacher approval');
  },
  async queue_rechecks_review_after_waiting() {
    const questions = Array.from({length:4}, pendingQuestion);
    show(questions);
    questions.forEach((_, i) => approve(document.getElementById(`parsed-card-${i}`)));
    const work = processAsyncAnswerGeneration(parsedQuestionsData);
    assert.equal(requests.length, 3);
    approve(document.getElementById('parsed-card-3'), false);
    approve(document.getElementById('parsed-card-1'), false);
    for (const request of requests) request.resolve(success());
    await work;
    assert.equal(requests.length, 3, 'queued question revoked before its turn must not be sent');
    assert.equal(questions[1].answer_markdown, '', 'revoked in-flight question must reject output');
    assert.equal(questions[3].answer_markdown, '');
  },
  async unmatched_report_and_new_import() {
    const q = pendingQuestion(); const card = show([q]); approve(card);
    renderSourceIntegrityReport({source_review_count:1, unmatched_source:[{
      source_excerpt:'24. 求 $x$ <img src=x onerror=bad()>', reason:'可能漏掉整题'
    }]});
    const report = document.getElementById('parsedSourceIntegrityReport');
    assert.equal(wrapper.firstChild, report);
    assert.ok(report.querySelector('summary').textContent.includes('可能漏掉整题'));
    assert.ok(report.querySelector('details').children[1].textContent.includes('24.'));
    assert.equal(report.querySelector('img'), null);
    replaceParsedQuestions([]);
    assert.equal(document.getElementById('parsedSourceIntegrityReport'), null);
    replaceParsedQuestions([q]);
    assert.equal(parsedQuestionNeedsSourceReview(0), true, 'even reused objects need a fresh session approval');
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
    const details = card.querySelector('details');
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
    "default_and_save_gates", "approval_and_edit_invalidation",
    "approval_cannot_change_during_precheck", "automatic_answers_skip_and_invalidate",
    "queue_rechecks_review_after_waiting", "unmatched_report_and_new_import",
    "local_source_images_only",
])
def test_import_source_review_in_shipped_js(review_script, scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    result = subprocess.run(
        [node, "-e", review_script, scenario], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
