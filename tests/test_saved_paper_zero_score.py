"""Saved-paper score restoration with synthetic responses and browser storage."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_loading_saved_paper_preserves_zero_scores_through_cart_storage_reload():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the saved-paper frontend regression")

    source = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
    storage_start = source.index("    function loadStateFromStorage()")
    storage_end = source.index("    function getQuestionFigAlign(q)", storage_start)
    load_start = source.index("    window.loadSavedPaper = async function (paperId)")
    load_end = source.index("    window.deleteSavedPaper = async function (paperId)", load_start)
    script = r"""
const assert = require('node:assert/strict');
const STORAGE_KEY_CART = 'mathbank_paper_cart';
const STORAGE_KEY_META = 'mathbank_paper_meta';
const STORAGE_KEY_COLLAPSED = 'mathbank_paper_filter_collapsed';
const drafts = JSON.stringify([{id:'draft-synthetic',content:'未保存的合成题稿'}]);
const stored = new Map([['mathbank_local_drafts', drafts]]);
const localStorage = {
  getItem(key) { return stored.get(key) ?? null; },
  setItem(key, value) { stored.set(key, String(value)); },
  removeItem(key) { stored.delete(key); }
};
const paper = {
  title:'零分题回归示例',subtitle:'保留原分值',paper_type:'quiz',section_order:[],
  questions:[
    {question:{id:4,content:'零分题'},score:0},
    {question:{id:9,content:'常规分值'},score:12},
    {question:{id:16,content:'旧记录缺省分值'}},
    {question:{id:21,content:'旧记录空分值'},score:null}
  ]
};
const expected = [{id:4,score:0},{id:9,score:12},{id:16,score:5},{id:21,score:5}];
const toasts = [];
let closed = 0, rendered = 0;
const window = {
  PaperStore:{cart:[{id:99,score:10}],meta:{},questionsMap:{},
    streamPagination:{selected:{page:3}}},
  showToast(message,type) { toasts.push({message,type}); },
  closeSavedPapersModal() { closed++; },
  async renderPaperWorkspace() {
    rendered++;
    assert.deepEqual(window.PaperStore.cart, expected);
  }
};
let cartQuestionsLoadPromise = null;
function getMissingCartQuestionIds() { return []; }
function updateCartBadges() {}
function normalizeSectionOrder(value) { return value; }
async function fetch(url) {
  assert.equal(url, '/api/papers/7');
  return {json:async()=>({status:'success',data:paper})};
}
""" + source[storage_start:storage_end] + source[load_start:load_end] + r"""
(async()=>{
  await window.loadSavedPaper(7);
  assert.deepEqual(window.PaperStore.cart, expected);
  assert.deepEqual(JSON.parse(stored.get(STORAGE_KEY_CART)), expected);
  assert.equal(JSON.parse(stored.get(STORAGE_KEY_META)).title, paper.title);
  assert.equal(window.PaperStore.streamPagination.selected.page, 1);
  assert.equal(closed, 1);
  assert.equal(rendered, 1);
  assert.equal(toasts.at(-1).type, 'success');
  assert.equal(stored.get('mathbank_local_drafts'), drafts);

  // Execute the real browser-storage restore path as on a later page load.
  window.PaperStore.cart = [];
  window.PaperStore.meta = {};
  loadStateFromStorage();
  assert.deepEqual(window.PaperStore.cart, expected);
  assert.equal(window.PaperStore.meta.title, paper.title);
  assert.equal(stored.get('mathbank_local_drafts'), drafts);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-"], input=script, cwd=ROOT, text=True, encoding="utf-8",
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
