"""Compact filter summaries keep existing filtering and removal behavior."""

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/editor.js"


def run_summary_script(assertions: str) -> None:
    source = EDITOR.read_text(encoding="utf-8")
    start = source.index("        function renderBankFilterConditions(")
    end = source.index("        function clearBankFilters()", start)
    toggle_start = source.index("        function toggleBankFilters(", end)
    toggle_end = source.index("        function showBankDetail()", toggle_start)
    setup = r"""
const assert = require('assert');
class Classes {
  constructor() { this.items = new Set(); }
  contains(name) { return this.items.has(name); }
  add(name) { this.items.add(name); }
  toggle(name, force) {
    const next = force === undefined ? !this.contains(name) : Boolean(force);
    if (next) this.items.add(name); else this.items.delete(name);
    return next;
  }
}
class Element {
  constructor(id = '', tagName = 'DIV', value = '') {
    this.id = id; this.tagName = tagName; this.value = value;
    this.attributes = {}; this.classList = new Classes(); this.childNodes = [];
    this.listeners = {}; this.events = []; this.hidden = false;
  }
  get selectedOptions() { return [{textContent: this.value}]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  append(...nodes) { this.childNodes.push(...nodes); }
  replaceChildren(...nodes) { this.childNodes = [...nodes]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  dispatchEvent(event) { this.events.push(event); this.onchange?.(event); }
}
const ids = ['bankFilterConditions', 'clearBankFiltersBtn', 'bankFilterFields',
  'bankWorkspaceSection', 'clearFilterSourceBtn'];
const nodes = Object.fromEntries(ids.map(id => [id, new Element(id)]));
for (const [id, value] of Object.entries({filterCompulsory:'必修一', filterChapter:'1. 集合',
  filterKnowledge:'1.2 集合间的基本关系', filterType:'单选题', filterDifficulty:'', filterSource:''})) {
  nodes[id] = new Element(id, id === 'filterSource' ? 'INPUT' : 'SELECT', value);
}
const toggle = new Element('toggle', 'BUTTON');
global.document = {getElementById: id => nodes[id] || null,
  querySelector: selector => selector === '.bank-filter-toggle' ? toggle : null,
  createElement: tag => new Element('', tag.toUpperCase())};
global.Event = class { constructor(type, options) { this.type = type; this.bubbles = options.bubbles; } };
let subdivisions = [];
const getFilterSubKnowledge = () => subdivisions;
const subKnowledgePathLabel = path => [path.compulsory, path.chapter, path.knowledge].join(' / ');
const renderSubKnowledgeOptions = (kind, paths) => { subdivisions = paths; };
let points = {knowledge_point_codes: [], knowledge_point_unlabeled: false};
const KnowledgePointState = {filter: () => points};
let pointRenders = 0, reloads = 0;
const renderBankKnowledgePointConditions = () => { pointRenders += 1; };
const reloadBankKnowledgePointFilters = () => { reloads += 1; updateBankFilterSummary(); };
const summary = nodes.bankFilterConditions;
"""
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    result = subprocess.run(
        [node, "-e", setup + source[start:end] + source[toggle_start:toggle_end] + assertions],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_expanded_filters_hide_repeated_summary_and_collapsing_keeps_values():
    run_summary_script(r"""
updateBankFilterSummary();
assert.equal(toggle.textContent, '筛选 (4)');
assert.equal(summary.hidden, false);
assert.equal(summary.childNodes.length, 4);
assert.equal(nodes.clearBankFiltersBtn.hidden, false);
assert(nodes.bankWorkspaceSection.classList.contains('bank-has-filter-summary'));
const values = Object.fromEntries(Object.entries(nodes).map(([id, node]) => [id, node.value]));
toggleBankFilters(toggle);
assert.equal(toggle.attributes['aria-expanded'], 'true');
assert.equal(summary.hidden, true);
assert(!nodes.bankWorkspaceSection.classList.contains('bank-has-filter-summary'));
toggleBankFilters(toggle);
assert.equal(toggle.attributes['aria-expanded'], 'false');
assert.equal(summary.hidden, false);
assert.deepEqual(Object.fromEntries(Object.entries(nodes).map(([id, node]) => [id, node.value])), values);
assert.equal(reloads, 0);
assert.equal(nodes.filterChapter.events.length, 0);
""")


def test_summary_removals_use_existing_change_and_source_reload_paths():
    run_summary_script(r"""
points.knowledge_point_codes = ['1230'];
nodes.filterChapter.onchange = () => {
  nodes.filterKnowledge.value = '';
  updateBankFilterSummary();
};
updateBankFilterSummary();
const chapter = summary.childNodes.find(node => node.attributes['aria-label'] === '移除筛选条件 1. 集合');
assert.equal(chapter.type, 'button');
chapter.listeners.click();
assert.equal(nodes.filterChapter.value, '');
assert.equal(nodes.filterChapter.events[0].type, 'change');
assert.equal(nodes.filterChapter.events[0].bubbles, true);
assert.equal(nodes.filterKnowledge.value, '');
assert.deepEqual(points.knowledge_point_codes, ['1230']);
assert.equal(nodes.filterCompulsory.value, '必修一');
nodes.filterSource.value = '<img src=x onerror=alert(1)>';
updateBankFilterSummary();
const source = summary.childNodes.find(node => node.attributes['aria-label'].includes('<img'));
assert.equal(source.childNodes[0].textContent, nodes.filterSource.value);
assert.equal(source.childNodes[1].attributes['aria-hidden'], 'true');
assert.equal(source.childNodes[1].textContent, '×');
source.listeners.click();
assert.equal(nodes.filterSource.value, '');
assert(nodes.clearFilterSourceBtn.classList.contains('hidden'));
assert.equal(reloads, 1);
assert.deepEqual(points.knowledge_point_codes, ['1230']);
""")


def test_point_only_conditions_keep_collapsed_summary_visible_and_counted():
    run_summary_script(r"""
Object.values(nodes).forEach(node => { node.value = ''; });
points.knowledge_point_codes = ['1230'];
updateBankFilterSummary();
assert.equal(toggle.textContent, '筛选 (1)');
assert.equal(summary.hidden, true);
assert.equal(summary.childNodes.length, 0);
assert(nodes.bankWorkspaceSection.classList.contains('bank-has-filter-summary'));
assert.equal(nodes.clearBankFiltersBtn.hidden, false);
assert.equal(pointRenders, 1);
points.knowledge_point_codes = [];
updateBankFilterSummary();
assert.equal(toggle.textContent, '筛选');
assert.equal(nodes.clearBankFiltersBtn.hidden, true);
assert(!nodes.bankWorkspaceSection.classList.contains('bank-has-filter-summary'));
""")


def test_summary_hosts_follow_fields_inside_filter_toolbar():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    start = html.index('<section class="bank-filter-toolbar"')
    end = html.index('</section>', start)
    toolbar = html[start:end]
    assert toolbar.index('id="bankFilterFields"') < toolbar.index('id="bankFilterConditions"')
    assert toolbar.index('id="bankFilterConditions"') < toolbar.index('id="bankKnowledgePointConditions"')
    assert 'aria-label="已生效的筛选条件"' in toolbar
