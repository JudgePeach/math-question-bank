"""Execute the shipped diagnostic renderers with hostile text and real escaping."""

from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PAPER = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
API = (ROOT / "static/js/api.js").read_text(encoding="utf-8")


def _section(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


def _node(script):
    node = shutil.which("node")
    assert node, "Node.js is required to execute the shipped diagnostic renderers"
    safe = _section(API, "const MathBankSafe = (() => {", "// Safe image previews")
    renderers = _section(PAPER, "function paperDiagnosticEvidenceHtml(", "function setPandocModalVisible(")
    escape = _section(PAPER, "function escapeHtml(", "function isWrittenQuestionType(")
    result = subprocess.run([node, "-"],
        input="global.window = {};\n" + safe + renderers + escape + script,
        text=True, capture_output=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_pdf_error_page_shows_source_math_examples_and_escaped_ai_and_local_evidence():
    _node(r'''
const assert = require('node:assert/strict');
const hostile = '</pre><img src=x onerror="alert(1)"><script>alert(2)</script>';
const report = {
  summary: 'AI建议 ' + hostile, cause: 'AI原因 ' + hostile,
  location: '卷面第2题，题库题目ID83的题干附近 ' + hostile,
  fixes: [String.raw`使用 $\triangle ABC$ 和 $\angle ACB$。`, hostile],
  error_excerpt: String.raw`求 $\badformula{x}$，x<y。` + hostile,
  source_context: '42: 局部源码 ' + hostile, technical_error: '! compiler ' + hostile,
  ai_used: true, local_summary: 'LOCALSUMMARY ' + hostile,
  local_cause: 'LOCALCAUSE ' + hostile, local_fixes: ['LOCALFIX ' + hostile],
  ai_note: 'AI 补充解释；定位以本地编译证据为准。 ' + hostile
};
let html = '', opened = 0, closed = 0;
const tab = {document: {open() {opened++;}, write(value) {html += value;}, close() {closed++;}}};
setPdfTabErrorState(tab, 'PDF 编译错误 ' + hostile, report, 'fallback');
assert.equal(opened, 1); assert.equal(closed, 1);
for (const text of ['AI 补充建议，请核对', '请核对这一处', '编译日志确认的信息',
  'LOCALSUMMARY', 'LOCALCAUSE', 'LOCALFIX', '局部源码', 'compiler',
  String.raw`$\triangle ABC$`, String.raw`$\angle ACB$`, String.raw`$\badformula{x}$`]) {
  assert.ok(html.includes(text), text);
}
assert.ok(html.includes('x&lt;y。'));
assert.ok(html.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
assert.ok(!html.includes('<img ')); assert.ok(!html.includes('<script>'));
assert.ok(!html.includes(hostile));
''')


def test_local_only_error_page_remains_actionable_when_ai_is_unconfigured():
    _node(r'''
const assert = require('node:assert/strict');
let html = '';
const tab = {document: {open() {}, write(value) {html = value;}, close() {}}};
setPdfTabErrorState(tab, 'PDF错误', {
  summary: '数学公式边界不配对', cause: '缺少完整公式定界符。',
  location: '卷面第1题，题库题目ID41的题干附近', ai_used: false,
  error_excerpt: String.raw`如图，\angle ACB=90°。`,
  fixes: [String.raw`请使用完整 $\triangle ABC$、$\angle ACB$。`],
  ai_note: '试卷拆解模型未配置，当前显示本地诊断结果。'
}, 'fallback');
for (const text of ['本地诊断结果', '未配置', 'ID41', '请核对这一处',
  String.raw`$\triangle ABC$`, String.raw`$\angle ACB$`]) assert.ok(html.includes(text), text);
assert.ok(!html.includes('AI 补充建议，请核对'));
''')


def test_bundle_error_panel_has_an_existing_host_and_escapes_all_evidence():
    renderer = _section(PAPER, "function showPaperExportDiagnostic(", "function setPdfTabErrorState(")
    destination = re.search(r"document\.querySelector\(['\"]([^'\"]+)['\"]\)", renderer).group(1)
    for element_id in re.findall(r"#([A-Za-z][\w-]*)", destination):
        assert f'id="{element_id}"' in PAPER, "The diagnostic host ID must exist in the rendered workspace"
    for css_class in re.findall(r"\.([A-Za-z][\w-]*)", destination):
        assert re.search(r'class="[^"]*\b' + re.escape(css_class) + r'\b', PAPER), "The diagnostic host class must exist"
    _node(r'''
const assert = require('node:assert/strict');
const nodes = new Map(), host = {children: [], appendChild(child) {
  this.children.push(child); nodes.set(child.id, child);
}};
global.document = {
  querySelector(selector) {assert.equal(selector, DESTINATION); return host;},
  getElementById(id) {return nodes.get(id) || null;},
  createElement(tag) {return {tag, style: {}, attributes: {}, setAttribute(key, value) {this.attributes[key] = value;}};}
};
const report = {
  summary: '数学公式错误', location: '卷面第2题',
  fixes: [String.raw`使用完整 $\angle ACB$。`],
  error_excerpt: '</pre><img src=x onerror=alert(1)>', source_context: 'x<y',
  technical_error: '<script>alert(1)</script>', ai_note: '当前显示本地诊断。'
};
showPaperExportDiagnostic(report, '参考答案与解析：编译失败');
assert.equal(host.children.length, 1);
const panel = host.children[0];
assert.equal(panel.attributes.role, 'alert');
for (const text of ['参考答案与解析', '卷面第2题', '请核对这一处',
  String.raw`$\angle ACB$`, '本地诊断', 'x&lt;y']) assert.ok(panel.innerHTML.includes(text), text);
assert.ok(panel.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert.ok(!panel.innerHTML.includes('<img ')); assert.ok(!panel.innerHTML.includes('<script>'));
showPaperExportDiagnostic({...report, error_excerpt: 'SECONDREPORT'}, '第二次错误');
assert.equal(host.children.length, 1, 'a retry must update the existing panel');
assert.ok(panel.innerHTML.includes('SECONDREPORT'));
'''.replace("DESTINATION", repr(destination)))
