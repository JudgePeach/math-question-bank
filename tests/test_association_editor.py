"""Execute the shipped editor functions with controllable response ordering."""

from pathlib import Path
import shutil
import subprocess

from frontend_timeout_helpers import FRONTEND_TIMEOUTS


ROOT = Path(__file__).resolve().parents[1]


def test_association_baseline_and_stale_responses():
    node = shutil.which("node")
    assert node, "Node.js is required for executable frontend regression"
    api = (ROOT / "static/js/api.js").read_text()
    editor = (ROOT / "static/js/editor.js").read_text()
    imports = (ROOT / "static/js/import.js").read_text()
    sources = [
        FRONTEND_TIMEOUTS,
        api[api.index("const EditorState ="):api.index("// Global variables")],
        editor[editor.index("function backupEditorState"):editor.index("// Custom Premium Confirmation Modal")],
        imports[imports.index("let relatedListLoadSequence"):imports.index("// Jump to select another question by ID")],
    ]
    setup = r"""
const assert = require('node:assert/strict');
const window = {MathBankSafe: {escapeText: String}};
let originalQuestionState = null, uploadedImages = [];
const TikzState = {contentAssets: [], answerAssets: []};
const FigureLayoutState = {align: 'right', size: 'auto', customAlign: false, imageLayouts: {}};
const values = {
  editContent: 'unchanged content', editAnswerMarkdown: '', editReview: '',
  editQType: 'single_choice', editDifficulty: 'medium', editSource: '',
  editCompulsory: '', editChapter: '', editKnowledge: '',
  editRelatedQuestion: '', editRelatedQuestionNum: '', editTags: ''
};
const elements = Object.fromEntries(Object.entries(values).map(([id, value]) => [id, {value}]));
const dropdown = elements.editRelatedQuestion;
Object.defineProperty(dropdown, 'innerHTML', {set() { this.value = ''; this.options = []; }});
dropdown.options = [];
dropdown.appendChild = function(option) {
  this.options.push(option);
  if (option.selected) this.value = String(option.value);
};
Object.defineProperty(dropdown, 'selectedIndex', {
  get() { return this.options.findIndex(o => String(o.value) === this.value); }
});
const document = {
  getElementById(id) { return elements[id] || null; },
  createElement() { return {setAttribute(k, v) {this[k] = v;}, getAttribute(k) {return this[k];}}; }
};
const getTypeText = () => '';
const showToast = () => {};
const localStorage = {getItem: () => ''};
const all = [17, 23, 31].map((id, i) => ({id, seq_num: i + 1, content: 'Question ' + id}));
let linked = false, hold = null;
const response = (body, ok = true) => ({ok, json: async () => body});
const fetch = async (url, options = {}) => {
  if (hold && hold.matches(url, options)) {
    const captured = hold;
    hold = null;
    return new Promise(resolve => { captured.resolve = resolve; });
  }
  if (options.method === 'POST') { linked = true; return response({status: 'success'}); }
  if (options.method === 'DELETE') { linked = false; return response({status: 'success'}); }
  assert.notEqual(url, '/api/questions', 'association UI cannot download the full library');
  return response(url.startsWith('/api/questions/association-candidates?')
    ? candidates(all) : linked ? [all.find(q => q.id !== EditorState.questionId)] : []);
};
const candidates = items => ({items, total: items.length, page: 1, page_size: 20, total_pages: 1});
const flush = () => new Promise(resolve => setImmediate(resolve));
function pause(matches) { hold = {matches}; return hold; }
function clean(id = 17) {
  EditorState.useQuestion({id});
  for (const [key, value] of Object.entries(values)) elements[key].value = value;
  backupEditorState(id);
}
"""
    checks = r"""
(async () => {
  clean();
  await loadAssociatedQuestionsInList(17);
  assert.equal(isEditorModified(), false, 'unassociated question stays clean');
  dropdown.value = '23';
  assert.equal(isEditorModified(), true, 'unconfirmed selection stays dirty');
  await associateRelatedQuestion();
  assert.equal(isEditorModified(), false, 'successful association clears only association dirtiness');

  // Match selectQuestion: start asynchronous hydration, then capture the DOM.
  for (const id of [23, 17, 23, 17]) {
    clean(id);
    const pending = loadAssociatedQuestionsInList(id);
    backupEditorState(id);
    await pending;
    assert.equal(isEditorModified(), false, 'switching linked questions should not prompt');
  }
  await clearRelatedQuestion();
  assert.equal(dropdown.value, '');
  assert.equal(isEditorModified(), false, 'successful unlink stays clean');

  clean();
  elements.editContent.value = 'unsaved stem';
  elements.editAnswerMarkdown.value = 'unsaved answer';
  dropdown.value = '23';
  await associateRelatedQuestion();
  assert.equal(isEditorModified(), true);
  await clearRelatedQuestion();
  assert.equal(isEditorModified(), true, 'unlink must preserve real edits');
  elements.editContent.value = values.editContent;
  elements.editAnswerMarkdown.value = '';
  assert.equal(isEditorModified(), false, 'only actual text edits remained dirty');

  // Ordinary save refreshes options after committing the question identity.
  clean(); dropdown.value = '23'; backupEditorState(17);
  dropdown.value = '31';
  await refreshRelatedDropdown('23', {expectedValue: '23'});
  assert.equal(dropdown.value, '31');
  assert.equal(isEditorModified(), true, 'save refresh must preserve later selection');

  // Preserve newer selections during hydration and user-triggered candidate loading.
  for (const stage of ['associated', 'options']) {
    clean(); linked = true;
    if (stage === 'options') await loadAssociatedQuestionsInList(17);
    const delayed = pause(url => stage === 'associated' ? url.endsWith('/associated') : url.startsWith('/api/questions/association-candidates?'));
    const pending = stage === 'associated' ? loadAssociatedQuestionsInList(17) : loadRelatedQuestionCandidates();
    await flush();
    dropdown.value = '31';
    delayed.resolve(response(stage === 'associated' ? [all[1]] : candidates(all)));
    await pending;
    assert.equal(dropdown.value, '31', stage + ' must preserve later selection');
    assert.equal(isEditorModified(), true);
    dropdown.value = '23';
    assert.equal(isEditorModified(), false);
  }

  // Late writes must also preserve a new unsaved association selection.
  for (const method of ['POST', 'DELETE']) {
    clean(); dropdown.value = '23';
    if (method === 'DELETE') backupEditorState(17);
    const delayed = pause((url, options) => options.method === method);
    const pending = method === 'POST' ? associateRelatedQuestion() : clearRelatedQuestion();
    dropdown.value = '31';
    linked = method === 'POST';
    delayed.resolve(response({status: 'success'}));
    await pending;
    assert.equal(dropdown.value, '31');
    assert.equal(isEditorModified(), true);
    dropdown.value = method === 'POST' ? '23' : '';
    assert.equal(isEditorModified(), false);
  }

  // A -> B -> A must reject old responses even though the question ID matches.
  for (const stage of ['associated', 'options', 'POST', 'DELETE']) {
    clean(); linked = true;
    if (stage === 'options') await loadAssociatedQuestionsInList(17);
    const delayed = pause((url, options) => stage === 'associated' ? url.endsWith('/associated')
      : stage === 'options' ? url.startsWith('/api/questions/association-candidates?') : options.method === stage);
    if (stage === 'POST') dropdown.value = '23';
    const pending = stage === 'POST' ? associateRelatedQuestion()
      : stage === 'DELETE' ? clearRelatedQuestion() : stage === 'options' ? loadRelatedQuestionCandidates() : loadAssociatedQuestionsInList(17);
    await flush();
    clean(23); clean(17);
    dropdown.value = '31'; backupEditorState(17);
    delayed.resolve(response(stage === 'associated' ? [all[1]]
      : stage === 'options' ? candidates(all) : {status: 'success'}));
    await pending;
    assert.equal(dropdown.value, '31', stage + ' response must not touch new session');
    assert.equal(isEditorModified(), false);
  }

  // Latest load wins within one session, too.
  clean(); linked = true;
  const delayed = pause(url => url.endsWith('/associated'));
  const old = loadAssociatedQuestionsInList(17);
  linked = false;
  await loadAssociatedQuestionsInList(17);
  delayed.resolve(response([all[1]]));
  await old;
  assert.equal(dropdown.value, '');
  assert.equal(isEditorModified(), false);

  // Failed writes/reads cannot claim that pending edits were saved.
  for (const method of ['POST', 'DELETE']) {
    clean(); dropdown.value = '23';
    const delayed = pause((url, options) => options.method === method);
    const pending = method === 'POST' ? associateRelatedQuestion() : clearRelatedQuestion();
    delayed.resolve(response({status: 'error'}, false));
    await pending;
    assert.equal(dropdown.value, '23');
    assert.equal(isEditorModified(), true);
  }
  clean(); dropdown.value = '23'; backupEditorState(17);
  const failed = pause(url => url.endsWith('/associated'));
  const pending = loadAssociatedQuestionsInList(17);
  failed.resolve(response({detail: 'unavailable'}, false));
  await pending;
  assert.equal(dropdown.value, '23');
  assert.equal(isEditorModified(), false);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(
        [node, "-"], input=setup + "\n".join(sources) + checks,
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
