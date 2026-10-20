"""Run shipped startup/picker code against bounded synthetic responses only."""

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def _run_node(source: str):
    node = shutil.which("node")
    assert node, "Node.js is required for executable frontend regressions"
    result = subprocess.run([node, "-"], input=source, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_config_readiness_is_lightweight_independent_and_rejects_errors():
    api = (ROOT / "static/js/api.js").read_text()
    source = api[api.index("let systemPreferEngine ="):api.index("// 百炼模型按任务隔离")]
    _run_node(r"""
const assert = require('node:assert/strict');
const indicator = {className: '', innerHTML: ''};
const document = {getElementById: id => id === 'apiStatusIndicator' ? indicator : null};
const updateOcrPlaceholder = () => {};
const parseModelConfig = () => ({});
const renderModelSelector = () => {};
const console = {error() {}};
const timers = new Map(); let timerId = 0;
const setTimeout = (fn, ms) => {timers.set(++timerId, {fn, ms}); return timerId;};
const clearTimeout = id => timers.delete(id);
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (body, ok = true) => ({ok, json: async () => body});
let requests = [], health = () => response({ready: true, status: 'ready'});
let settings = () => response({prefer_engine: 'siliconflow'});
const fetch = (url, options = {}) => {
  requests.push({url, options});
  assert.notEqual(url, '/api/questions');
  return Promise.resolve(url === '/healthz' ? health(options) : settings(options));
};
""" + source + r"""
(async () => {
  let finishSettings;
  settings = () => new Promise(resolve => {finishSettings = resolve;});
  const pending = fetchConfigStatus();
  await flush();
  assert.match(indicator.innerHTML, /题库就绪/, 'health status cannot wait on preference loading');
  assert.deepEqual(requests.map(r => r.url).sort(), ['/api/settings', '/healthz']);
  assert.equal(requests.find(r => r.url === '/healthz').options.cache, 'no-store');
  finishSettings(response({prefer_solve_model: 'my-model'})); await pending;
  assert.equal(systemPreferSolveModel, 'my-model');

  settings = () => response({});
  for (const bad of [
    () => response({ready: true, status: 'ready'}, false),
    () => ({ok: true, json: async () => {throw Error('bad JSON');}}),
    () => response({ready: false, status: 'not_ready'}),
    () => response({ready: true}),
    () => response({detail: 'error'}),
  ]) {
    health = bad; await fetchConfigStatus();
    assert.doesNotMatch(indicator.className, /bg-green/);
    assert.match(indicator.innerHTML, /尚未就绪/);
  }

  // An older, ignored AbortSignal still cannot turn a newer failed check green.
  let finishOld;
  health = () => new Promise(resolve => {finishOld = resolve;});
  const old = fetchConfigStatus(); await flush();
  health = () => response({ready: false}); await fetchConfigStatus();
  finishOld(response({ready: true, status: 'ready'})); await old;
  assert.match(indicator.innerHTML, /尚未就绪/);

  // Abort the real pending health request through its bounded timeout.
  health = ({signal}) => new Promise((resolve, reject) => signal.addEventListener('abort', () => {
    const error = Error('timeout'); error.name = 'AbortError'; reject(error);
  }));
  const timed = fetchConfigStatus(); await flush();
  assert.equal(timers.size, 1);
  const timer = [...timers.values()][0]; assert.equal(timer.ms, 8000); timer.fn();
  await timed; assert.match(indicator.innerHTML, /尚未就绪/);
  assert.equal(timers.size, 0);
})().catch(error => {process.stderr.write(String(error.stack)); process.exitCode = 1;});
""")


PICKER_SETUP = r"""
const assert = require('node:assert/strict');
const window = {MathBankSafe: {escapeText: String}};
const console = {error() {}};
let originalQuestionState = null, uploadedImages = [];
const TikzState = {contentAssets: [], answerAssets: []};
const FigureLayoutState = {align: 'right', size: 'auto', customAlign: false, imageLayouts: {}};
const initial = {
  editContent: 'unchanged', editAnswerMarkdown: '', editReview: '', editQType: 'single_choice',
  editDifficulty: 'medium', editSource: '', editCompulsory: '', editChapter: '', editKnowledge: '',
  editRelatedQuestion: '', editRelatedQuestionNum: '', editRelatedQuestionSearch: '', editTags: '',
};
const makeElement = value => ({value, listeners: {}, options: [], classList: {add() {}, remove() {}},
  addEventListener(name, fn) {this.listeners[name] = fn;},
  setAttribute(k, v) {this[k] = String(v);}, getAttribute(k) {return this[k];}});
const elements = Object.fromEntries(Object.entries(initial).map(([id, value]) => [id, makeElement(value)]));
for (const id of ['relatedQuestionSearchPanel', 'relatedQuestionPageStatus', 'relatedQuestionPrevPage', 'relatedQuestionNextPage']) {
  elements[id] = makeElement('');
}
const dropdown = elements.editRelatedQuestion;
Object.defineProperty(dropdown, 'innerHTML', {set() {this.value = ''; this.options = [];}});
dropdown.appendChild = function(option) {this.options.push(option); if (option.selected) this.value = String(option.value);};
Object.defineProperty(dropdown, 'selectedIndex', {get() {return this.options.findIndex(o => String(o.value) === this.value);}});
const document = {getElementById: id => elements[id] || null, createElement: () => makeElement('')};
const getTypeText = () => '';
const showToast = () => {};
const localStorage = {getItem: () => ''};
let requests = [], hold = null;
const response = body => ({ok: true, json: async () => body});
const question = (id, seq_num = id) => ({id, seq_num, question_type: 'single_choice', content: 'Synthetic question ' + id});
const candidatePage = (items, page = 1, total = 1000) => ({items, page, total, page_size: 20, total_pages: Math.ceil(total / 20)});
const fetch = async (url, options = {}) => {
  requests.push({url, options});
  assert.notEqual(url, '/api/questions', 'picker cannot download the full bank');
  if (hold && (!hold.matches || hold.matches(url, options))) {const captured = hold; hold = null; return new Promise(resolve => {captured.resolve = resolve;});}
  if (options.method === 'DELETE') return response({status: 'success'});
  if (url.endsWith('/associated')) return response([]);
  const params = new URL(url, 'http://localhost').searchParams;
  if (params.has('question_id')) return response(candidatePage([question(Number(params.get('question_id')), 12)]));
  if (params.has('seq_num')) return response(candidatePage([question(90001, Number(params.get('seq_num')))]));
  const page = Number(params.get('page'));
  return response(candidatePage(Array.from({length: 20}, (_, i) => question(100 + page * 20 + i)), page));
};
const flush = () => new Promise(resolve => setImmediate(resolve));
let nextTimer = 0; const timers = new Map();
const setTimeout = (fn, ms) => {timers.set(++nextTimer, {fn, ms}); return nextTimer;};
const clearTimeout = id => timers.delete(id);
const fireDebounce = () => {
  for (const [id, timer] of [...timers]) if (timer.ms === 250) {timers.delete(id); timer.fn();}
};
function clean(id = 17) {
  resetRelatedQuestionPicker();
  if (id) EditorState.useQuestion({id}); else EditorState.useDraft({id: 'legacy-draft'});
  for (const [key, value] of Object.entries(initial)) elements[key].value = value;
  backupEditorState(EditorState.questionId, EditorState.draftId);
}
"""


def _picker_source():
    api = (ROOT / "static/js/api.js").read_text()
    editor = (ROOT / "static/js/editor.js").read_text()
    imports = (ROOT / "static/js/import.js").read_text()
    return "\n".join([
        api[api.index("const EditorState ="):api.index("// Global variables")],
        editor[editor.index("function backupEditorState"):editor.index("// Custom Premium Confirmation Modal")],
        imports[imports.index("let relatedListLoadSequence"):imports.index("// Jump to select another question by ID")],
    ])


def test_association_picker_is_lazy_bounded_and_preserves_user_state():
    imports = (ROOT / 'static/js/import.js').read_text()
    deletion = imports[imports.index('function deleteQuestion(id)'):imports.index('// Toggle Paper Analysis reveal')]
    _run_node(PICKER_SETUP + _picker_source() + r"""
const confirm = () => true;
function blockEditorSessionChangeWhileSaving() {return false;}
function loadQuestions() {}
function loadCategories() {}
function startNewQuestion() {clean(null);}
""" + deletion + r"""
(async () => {
  clean(); setupRelatedQuestionPicker();
  assert.equal(requests.length, 0, 'binding startup events is lazy');
  await loadAssociatedQuestionsInList(null);
  await loadAssociatedQuestionsInList(17);
  assert.equal(requests.length, 1, 'opening a question fetches only its real association list');
  assert.equal(requests[0].url, '/api/questions/17/associated');
  assert.equal(isEditorModified(), false);

  dropdown.listeners.focus(); await flush();
  assert.match(requests.at(-1).url, /association-candidates\?/);
  assert.equal(new URL(requests.at(-1).url, 'http://localhost').searchParams.get('page_size'), '20');
  assert.equal(dropdown.options.length, 20);
  dropdown.value = '120'; dropdown.listeners.change(); backupEditorState(17);
  await activateRelatedQuestionSearch(2);
  assert.equal(dropdown.value, '120');
  assert.equal(elements.editRelatedQuestionNum.value, '120');
  assert.equal(dropdown.options.length, 21, 'one selected target may be pinned outside the page');
  assert.equal(isEditorModified(), false, 'pagination is not an edit');

  // A new unsaved selection wins over a delayed candidate page.
  const delayed = hold = {};
  const page = loadRelatedQuestionCandidates({page: 3}); await flush();
  dropdown.value = '140'; dropdown.listeners.change();
  delayed.resolve(response(candidatePage([question(160)], 3))); await page;
  assert.equal(dropdown.value, '140'); assert.equal(isEditorModified(), true);
  assert.equal(dropdown.options.length, 2);

  // Display sequence lookup reaches a stable ID outside every loaded page.
  elements.editRelatedQuestionNum.value = '17400'; elements.editRelatedQuestionNum.listeners.input();
  fireDebounce(); await flush();
  assert.equal(dropdown.value, '90001');
  assert.equal(elements.editRelatedQuestionNum.value, '17400');
  assert.equal(new URL(requests.at(-1).url, 'http://localhost').searchParams.get('seq_num'), '17400');

  // Clearing while an exact lookup is pending prevents late reselection.
  clean(null);
  const lateLookup = hold = {};
  elements.editRelatedQuestionNum.value = '99'; elements.editRelatedQuestionNum.listeners.input();
  fireDebounce(); await flush();
  const pendingLookup = requests.at(-1);
  clearRelatedQuestion();
  assert.equal(pendingLookup.options.signal.aborted, true);
  lateLookup.resolve(response(candidatePage([question(9900, 99)]))); await flush();
  assert.equal(dropdown.value, '');

  // Changing the editor before the debounce fires sends no old lookup.
  clean(); const before = requests.length;
  elements.editRelatedQuestionNum.value = '17400'; elements.editRelatedQuestionNum.listeners.input();
  clean(23); fireDebounce(); await flush(); assert.equal(requests.length, before);

  // A completed stale response cannot alter the next editor session.
  const late = hold = {};
  const stale = loadRelatedQuestionCandidates({page: 4}); await flush();
  const signal = requests.at(-1).options.signal;
  clean(31); assert.equal(signal.aborted, true);
  late.resolve(response(candidatePage([question(180)], 4))); await stale;
  assert.equal(dropdown.value, ''); assert.equal(isEditorModified(), false);

  // Old drafts can restore their saved single stable target without a bank scan.
  clean(null);
  const draftLookup = hold = {};
  const restored = refreshRelatedDropdown('72000', {expectedValue: ''});
  assert.equal(dropdown.value, '72000', 'restore stable ID synchronously before draft baseline');
  backupEditorState(null, EditorState.draftId);
  assert.equal(isEditorModified(), false);
  assert.equal(new URL(requests.at(-1).url, 'http://localhost').searchParams.get('question_id'), '72000');
  draftLookup.resolve(response(candidatePage([question(72000, 567)]))); await restored;
  assert.equal(dropdown.value, '72000'); assert.equal(isEditorModified(), false);
  assert.equal(elements.editRelatedQuestionNum.value, '567');
  await refreshRelatedDropdown();
  assert.equal(dropdown.value, '72000', 'refresh preserves the already selected draft target');

  // Deletion invalidates the one-page cache and refreshes just the selected ID.
  clean();
  await loadRelatedQuestionCandidates({page: 1});
  dropdown.value = '120'; dropdown.listeners.change(); backupEditorState(17);
  const beforeDelete = requests.length;
  deleteQuestion(50); await flush();
  assert.equal(requests.length - beforeDelete, 2, 'one DELETE plus one bounded exact read');
  assert.equal(new URL(requests.at(-1).url, 'http://localhost').searchParams.get('question_id'), '120');
  assert.equal(dropdown.value, '120');
  assert.equal(elements.editRelatedQuestionNum.value, '12', 'continuous display number refreshes after deletion');
  assert.equal(isEditorModified(), false);
  await activateRelatedQuestionSearch();
  assert.equal(new URL(requests.at(-1).url, 'http://localhost').searchParams.get('q'), '', 'candidate cache was invalidated');

  // Only an empty successful exact response clears a truly deleted target.
  clean(); renderRelatedQuestionOptions([question(23, 21)], '23'); backupEditorState(17);
  elements.editContent.value = 'unsaved stem stays';
  const missing = hold = {matches: url => url.includes('/association-candidates?')};
  deleteQuestion(50); await flush();
  missing.resolve(response(candidatePage([], 1, 0))); await flush();
  assert.equal(dropdown.value, ''); assert.equal(elements.editRelatedQuestionNum.value, '');
  assert.equal(originalQuestionState.related_question_id, '');
  assert.equal(elements.editContent.value, 'unsaved stem stays'); assert.equal(isEditorModified(), true);

  // A failed lookup is no evidence of deletion and cannot clear the target.
  clean(); renderRelatedQuestionOptions([question(23, 21)], '23'); backupEditorState(17);
  const failed = hold = {matches: url => url.includes('/association-candidates?')};
  deleteQuestion(50); await flush(); failed.resolve({ok: false}); await flush();
  assert.equal(dropdown.value, '23'); assert.equal(elements.editRelatedQuestionNum.value, '21');
  assert.equal(originalQuestionState.related_question_id, '23'); assert.equal(isEditorModified(), false);

  // New choices and new editor sessions win over a late deletion refresh.
  for (const switchSession of [false, true]) {
    clean(); renderRelatedQuestionOptions([question(23, 21), question(31, 30)], '23'); backupEditorState(17);
    const lateDelete = hold = {matches: url => url.includes('/association-candidates?')};
    deleteQuestion(50); await flush();
    if (switchSession) clean(31);
    else {dropdown.value = '31'; dropdown.listeners.change();}
    lateDelete.resolve(response(candidatePage([], 1, 0))); await flush();
    assert.equal(dropdown.value, switchSession ? '' : '31');
    assert.equal(isEditorModified(), !switchSession);
  }
  assert.equal(timers.size, 0);
})().catch(error => {process.stderr.write(String(error.stack)); process.exitCode = 1;});
""")


def test_editor_switch_cancels_stale_gets_and_association_jump_fetches_once():
    imports = (ROOT / "static/js/import.js").read_text()
    source = imports[imports.index("// Jump to select another question by ID"):imports.index("window.reloadCurrentQuestionSilently =")]
    # No response resolves: this exercises the actual transition and cancellation
    # paths without requiring a rendered browser or touching any local service.
    _run_node(PICKER_SETUP + _picker_source() + r"""
window.addEventListener = () => {};
window.QuestionReferenceState = {invalidate() {}, reset() {}, hydrate() {}};
function checkAndSwitch(action) {action();}
""" + source + r"""
(async () => {
  clean();
  hold = {}; selectQuestionById(23); await flush();
  assert.equal(requests.length, 1, 'one jump issues one detail GET');
  assert.equal(requests[0].url, '/api/questions/23');
  const firstSignal = requests[0].options.signal;
  hold = {}; selectQuestion({id: 31}); await flush();
  assert.equal(firstSignal.aborted, true);
  assert.equal(requests.length, 2);
  const secondSignal = requests[1].options.signal;
  invalidatePendingQuestionDetailLoad();
  assert.equal(secondSignal.aborted, true, 'new question or draft cancels detail GET');
  assert.equal(questionDetailLoading, false);
})().catch(error => {process.stderr.write(String(error.stack)); process.exitCode = 1;});
""")
