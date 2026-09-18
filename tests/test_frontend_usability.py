import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
INDEX_PATH = STATIC_DIR / "index.html"
CSS_PATH = STATIC_DIR / "css" / "app.css"
JS_FILES = tuple(
    STATIC_JS_DIR / name
    for name in ("api.js", "editor.js", "ocr.js", "import.js", "paper.js")
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _ElementIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.by_id[element_id] = attributes


def _index_elements() -> dict[str, dict[str, str | None]]:
    parser = _ElementIndex()
    parser.feed(_read(INDEX_PATH))
    return parser.by_id


def test_mobile_viewport_and_five_level_script_order_are_preserved():
    index_source = _read(INDEX_PATH)
    expected_order = (
        "/static/js/api.js",
        "/static/js/editor.js",
        "/static/js/ocr.js",
        "/static/js/import.js",
        "/static/js/paper.js",
    )

    assert 'name="viewport"' in index_source
    assert "width=device-width, initial-scale=1.0" in index_source
    positions = [index_source.index(script_path) for script_path in expected_order]
    assert positions == sorted(positions)


def test_editor_preview_converts_exam_zh_paren_after_protecting_math_blocks():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    css_source = _read(CSS_PATH)

    helper_start = editor_source.index("function transformExamZhParenForPreview(text)")
    helper_end = editor_source.index("function preprocessFormulaForKaTeX(text,", helper_start)
    helper_source = editor_source[helper_start:helper_end]

    assert r"/\\paren\b/g" in helper_source
    assert 'class="exam-zh-paren-preview"' in helper_source
    assert 'aria-label="选择题作答括号"' in helper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = "global.window = {};\n" + helper_source + r"""
const rendered = transformExamZhParenForPreview(String.raw`题干 \paren`);
if (rendered.includes(String.raw`\paren`)) {
  throw new Error(`paren macro leaked into preview: ${rendered}`);
}
if (!rendered.includes('class="exam-zh-paren-preview"') || !rendered.includes('（&nbsp;&nbsp;）')) {
  throw new Error(`paren preview markup missing: ${rendered}`);
}
const similarlyNamed = transformExamZhParenForPreview(String.raw`\parent`);
if (similarlyNamed !== String.raw`\parent`) {
  throw new Error(`similarly named command was changed: ${similarlyNamed}`);
}
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    protect_position = editor_source.index("let tempText = clean;", helper_end)
    strip_position = editor_source.index(
        "tempText = tempText.replace(/<[^>]*>/g, '');", protect_position
    )
    transform_position = editor_source.index(
        "tempText = transformExamZhParenForPreview(tempText);", strip_position
    )
    assert protect_position < strip_position < transform_position

    assert ".exam-zh-paren-preview" in css_source
    assert "float: right;" in css_source
    assert re.search(r"\.choices-grid\s*\{[^}]*clear:\s*both;", css_source, re.DOTALL)


def test_editor_preview_repairs_naked_math_without_touching_existing_blocks():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function normalizeNakedMathForPreview(text)")
    helper_marker = "window.normalizeNakedMathForPreview = normalizeNakedMathForPreview;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    assert "(?<!" not in helper_source
    assert "(?<=" not in helper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = "global.window = {};\n" + helper_source + r'''
const source = String.raw`已知平面向量 \mathbf{a}, \mathbf{b} 不共线，且 2\mathbf{a} + y\mathbf{b} = x\mathbf{a} - 3\mathbf{b}。
当 x \geqslant 0 时，f(x_1) \leqslant f(x_2)，且 y^2 > 0；当 x < 0 时经过点 (4,8)。
已有 $z_1^2$，以及跨行公式 $u^2 +
v^2 = 1$。
\begin{cases} x=1 \\ y=2 \end{cases}
\begin{tabular}{cc} x_1 & y_1 \\ x_2 & y_2 \end{tabular}
题干 \paren
\begin{choices}
\item \frac{1}{2}
\item x = 2
\end{choices}`;
const rendered = normalizeNakedMathForPreview(source);
for (const expected of [
  String.raw`$\mathbf{a}, \mathbf{b}$`,
  String.raw`$2\mathbf{a} + y\mathbf{b} = x\mathbf{a} - 3\mathbf{b}$`,
  String.raw`$x \geqslant 0$`,
  String.raw`$x < 0$`,
  String.raw`$f(x_1) \leqslant f(x_2)$`,
  String.raw`$y^2 > 0$`,
  String.raw`$x < 0$`,
  String.raw`$(4,8)$`,
  String.raw`$z_1^2$`,
  String.raw`$u^2 +
v^2 = 1$`,
  String.raw`$\begin{cases} x=1 \\ y=2 \end{cases}$`,
  String.raw`\begin{tabular}{cc} x_1 & y_1 \\ x_2 & y_2 \end{tabular}`,
  String.raw`题干 \paren`,
  String.raw`\item $\frac{1}{2}$`,
  String.raw`\item $x = 2$`,
]) {
  if (!rendered.includes(expected)) {
    throw new Error(`missing normalized math ${expected}: ${rendered}`);
  }
}
if (rendered.includes(String.raw`$$z_1^2$$`)) {
  throw new Error(`existing math was double wrapped: ${rendered}`);
}
if (rendered.includes(String.raw`$\begin{cases} $`) || rendered.includes(String.raw`$\begin{tabular}`)) {
  throw new Error(`structured environment was split or wrapped incorrectly: ${rendered}`);
}
if (rendered.includes(String.raw`$\paren$`) || rendered.includes(String.raw`\boldsymbol`)) {
  throw new Error(`text macro or explicit vector typography was changed: ${rendered}`);
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_editor_preview_restores_adjacent_math_without_splitting_fillin_lines():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
  },
};
""" + helper_source + r"""
const fillin = String.raw`\fillin`;
const renderedFillin = String.raw`$\underline{\hspace{1.5cm}}$`;
for (let count = 1; count <= 6; count += 1) {
  const source = `11${fillin.repeat(count)}`;
  const rendered = window.preprocessFormulaForKaTeX(source);
  const expected = `11${renderedFillin.repeat(count)}`;
  if (rendered !== expected) {
    throw new Error(`continuous fillin changed at count ${count}: ${rendered}`);
  }
  if (rendered.includes('@@MATH_PLACEHOLDER_')) {
    throw new Error(`math placeholder leaked at count ${count}: ${rendered}`);
  }
}

for (const source of [
  String.raw`$a$$b$$c$`,
  String.raw`$a$$b$$c$$d$$e$`,
  String.raw`prefix$$a+b$$suffix`,
  String.raw`text $a$ and $b$`,
]) {
  const rendered = window.preprocessFormulaForKaTeX(source);
  if (rendered !== source) {
    throw new Error(`adjacent math changed: ${source} -> ${rendered}`);
  }
  if (rendered.includes('@@MATH_PLACEHOLDER_')) {
    throw new Error(`math placeholder leaked: ${rendered}`);
  }
}

const structured = window.preprocessFormulaForKaTeX(String.raw`\begin{tabular}{cc} x_1 & y_1 \\ x_2 & y_2 \end{tabular}`);
if ((structured.match(/<td\b/g) || []).length !== 4) {
  throw new Error(`tabular structure did not render as 2x2 HTML: ${structured}`);
}
for (const cellMath of [String.raw`$x_1$`, String.raw`$y_1$`, String.raw`$x_2$`, String.raw`$y_2$`]) {
  if (!structured.includes(cellMath)) {
    throw new Error(`table cell math was not repaired safely: ${structured}`);
  }
}

const casesSource = String.raw`\begin{cases} x=1 \\ y=2 \end{cases}`;
const renderedCases = window.preprocessFormulaForKaTeX(casesSource);
if (renderedCases !== '$' + casesSource + '$') {
  throw new Error(`cases environment was not wrapped as one formula: ${renderedCases}`);
}

const multilineSource = `$x^2 +\ny^2 = 1$`;
if (window.preprocessFormulaForKaTeX(multilineSource) !== multilineSource) {
  throw new Error('multiline math was changed or double wrapped');
}
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_shared_question_preview_pipeline_renders_fillin_through_local_preprocessor():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.renderQuestionPreviewContent = renderQuestionPreviewContent;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
    sanitizeRichHtml(value) { return value; },
  },
};
let katexCalls = 0;
let choicesCalls = 0;
function renderMathInElement(container, options) {
  katexCalls += 1;
  const serialized = options.delimiters.map(item => `${item.left}:${item.right}:${item.display}`);
  const expected = ['$$:$$:true', '$:$:false', '\\(:\\):false', '\\[:\\]:true'];
  if (JSON.stringify(serialized) !== JSON.stringify(expected)) {
    throw new Error(`unexpected KaTeX delimiters: ${JSON.stringify(serialized)}`);
  }
  if (options.throwOnError !== false) throw new Error('question preview must tolerate KaTeX errors');
}
function adaptChoicesGridLayout(container) {
  choicesCalls += 1;
  if (!container) throw new Error('choices layout received no container');
}
""" + helper_source + r"""
const originalPreprocess = preprocessFormulaForKaTeX;
let preprocessCalls = 0;
preprocessFormulaForKaTeX = function(text) {
  preprocessCalls += 1;
  return originalPreprocess(text);
};
const container = { innerHTML: '' };
const source = String.raw`填空：\fillin ![配图](/static/uploads/a.png) <img src="/static/uploads/b.png">`;
const preparedHtml = window.renderQuestionPreviewContent(
  container,
  source,
  { includeImages: false }
);
if (container.innerHTML.includes(String.raw`\fillin`)) {
  throw new Error(`fillin macro leaked into shared preview: ${container.innerHTML}`);
}
if (!container.innerHTML.includes(String.raw`\underline{\hspace{1.5cm}}`)) {
  throw new Error(`local fillin underline was not generated: ${container.innerHTML}`);
}
if (container.innerHTML.includes('<img') || container.innerHTML.includes('/static/uploads/')) {
  throw new Error(`question images leaked into image-free preview: ${container.innerHTML}`);
}
const reusedContainer = { innerHTML: '' };
const reusedHtml = window.renderQuestionPreviewContent(
  reusedContainer,
  String.raw`这段\fillin不应再预处理`,
  { includeImages: false, preparedHtml: preparedHtml }
);
if (reusedHtml !== preparedHtml || reusedContainer.innerHTML !== preparedHtml) {
  throw new Error('prepared question HTML was not reused exactly');
}
if (preprocessCalls !== 1) {
  throw new Error(`preparedHtml path reran preprocessing: ${preprocessCalls}`);
}
if (katexCalls !== 2 || choicesCalls !== 2) {
  throw new Error(`shared pipeline calls were incomplete: katex=${katexCalls}, choices=${choicesCalls}`);
}
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_preview_separates_choices_before_figure_layout():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    css_source = _read(CSS_PATH)

    helper_start = paper_source.index("function splitChoiceContentForPaperPreview(raw)")
    helper_end = paper_source.index("function formatQuestionContentHtml(", helper_start)
    helper_source = paper_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
const source = String.raw`题干
\begin{choices}
\item A
\item B
\end{choices}

![](/static/uploads/graph.png)`;
const result = splitChoiceContentForPaperPreview(source);
if (result.stemRaw.includes(String.raw`\begin{choices}`)) {
  throw new Error(`choices leaked into stem: ${result.stemRaw}`);
}
if (!result.stemRaw.includes('/static/uploads/graph.png')) {
  throw new Error(`figure was not retained with stem: ${result.stemRaw}`);
}
if (!result.choicesRaw.startsWith(String.raw`\begin{choices}`) || !result.choicesRaw.endsWith(String.raw`\end{choices}`)) {
  throw new Error(`choices block was not preserved: ${result.choicesRaw}`);
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    split_position = paper_source.index(
        "const choiceContentParts = isChoiceQuestion"
    )
    stem_render_position = paper_source.index(
        "formatQuestionContentHtml(choiceContentParts.stemRaw", split_position
    )
    choices_render_position = paper_source.index(
        "formatQuestionContentHtml(choiceContentParts.choicesRaw", stem_render_position
    )
    assert split_position < stem_render_position < choices_render_position
    assert 'class="paper-choice-stem-row' in paper_source
    assert 'class="paper-choice-options-row"' in paper_source
    assert ".paper-choice-options-row" in css_source


def test_a4_preview_paginates_by_measured_height_and_keeps_every_question():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    css_source = _read(CSS_PATH)
    helper_start = paper_source.index("function paginatePaperBlocksByHeight(")
    helper_marker = "window.paginatePaperBlocksByHeight = paginatePaperBlocksByHeight;"
    helper_end = paper_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = paper_source[helper_start:helper_end]

    assert "PAGE_1_MAX" not in paper_source
    assert "PAGE_N_MAX" not in paper_source
    assert "rawContent.length > 200" not in paper_source
    assert "getBoundingClientRect" in paper_source
    assert "paper-page-footer" in paper_source
    assert "new ResizeObserver(repaginateAfterLayoutChange)" in paper_source
    assert "resizeObserver.observe(sheet)" in paper_source
    assert "resizeObserver.observe(block)" in paper_source
    assert "window.scheduleActiveA4Repagination" in paper_source
    assert "sheet.innerHTML = '';" not in paper_source
    assert "a4-paper-sheet--expanded" in paper_source
    assert "预览已自动扩展以完整显示" in paper_source
    assert re.search(
        r"\.a4-paper-sheet\s*,[^{]*\{[^}]*height:\s*1123px;",
        css_source,
        re.DOTALL,
    )
    assert re.search(
        r"\.a4-paper-sheet\.a4-paper-sheet--expanded[^}]*"
        r"height:\s*auto\s*!important;[^}]*"
        r"flex-shrink:\s*0\s*!important;[^}]*"
        r"overflow:\s*visible\s*!important;",
        css_source,
        re.DOTALL,
    )

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {};
""" + helper_source + r"""
const blocks = [
  { id: 'title', type: 'section_title', qType: 'single_choice', height: 40 },
  { id: 'q1', type: 'question', qType: 'single_choice', height: 80 },
  { id: 'q2', type: 'question', qType: 'single_choice', height: 100 },
  { id: 'q3', type: 'question', qType: 'single_choice', height: 100 },
  { id: 'q4', type: 'question', qType: 'single_choice', height: 80 },
  { id: 'q5', type: 'question', qType: 'single_choice', height: 190 },
  { id: 'q6', type: 'question', qType: 'single_choice', height: 120 },
  { id: 'q7', type: 'question', qType: 'single_choice', height: 180 },
];
const pages = window.paginatePaperBlocksByHeight(blocks, 620, 920);
const ids = pages.flat().map(block => block.id);
if (JSON.stringify(ids) !== JSON.stringify(blocks.map(block => block.id))) {
  throw new Error(`pagination lost or reordered blocks: ${JSON.stringify(ids)}`);
}
if (ids.filter(id => id === 'q6').length !== 1) {
  throw new Error(`question 6 count is invalid: ${JSON.stringify(pages)}`);
}
if (pages.length !== 2 || pages[0].some(block => block.id === 'q6') || pages[1][0].id !== 'q6') {
  throw new Error(`measured overflow did not move q6 intact: ${JSON.stringify(pages)}`);
}

const headingBlocks = [
  { id: 'q1', type: 'question', qType: 'single_choice', height: 500 },
  { id: 'title2', type: 'section_title', qType: 'fill_in_blank', height: 40 },
  { id: 'q2', type: 'question', qType: 'fill_in_blank', height: 120 },
];
const headingPages = window.paginatePaperBlocksByHeight(headingBlocks, 620, 920);
if (headingPages[0].some(block => block.id === 'title2') || headingPages[1][0].id !== 'title2') {
  throw new Error(`section heading was orphaned: ${JSON.stringify(headingPages)}`);
}

const oversizeBlocks = [
  { id: 'oversize-title', type: 'section_title', qType: 'detailed_answer', height: 40 },
  { id: 'oversize-question', type: 'question', qType: 'detailed_answer', height: 1251 },
];
const oversizePages = window.paginatePaperBlocksByHeight(oversizeBlocks, 620, 920);
if (oversizePages.length !== 1 || oversizePages[0].map(block => block.id).join(',') !== 'oversize-title,oversize-question') {
  throw new Error(`oversize question lost content or orphaned its heading: ${JSON.stringify(oversizePages)}`);
}

const firstPageHeadingPair = [
  { id: 'later-title', type: 'section_title', qType: 'detailed_answer', height: 40 },
  { id: 'later-question', type: 'question', qType: 'detailed_answer', height: 700 },
];
const firstPageHeadingPages = window.paginatePaperBlocksByHeight(firstPageHeadingPair, 620, 920);
if (firstPageHeadingPages.length !== 2 || firstPageHeadingPages[0].length !== 0 ||
    firstPageHeadingPages[1].map(block => block.id).join(',') !== 'later-title,later-question') {
  throw new Error(`heading pair that fits a later page was split: ${JSON.stringify(firstPageHeadingPages)}`);
}
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_preview_preserves_images_at_authored_complex_content_positions():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    helper_start = paper_source.index(
        "function shouldPreserveInlinePaperImages(raw)"
    )
    helper_end = paper_source.index("// Init on DOMContentLoaded", helper_start)
    helper_source = paper_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
global.window = {
  parseMarkdownWithMath: value => value,
  MathBankSafe: {
    safeImageUrl: value => value,
    escapeAttribute: value => value,
    sanitizeRichHtml: value => value
  }
};

const complex = String.raw`【课本回顾】
![](/static/uploads/fig12.png)

【知识探究】
\begin{tabular}{|c|c|c|}
 & 思路一 & 思路二 \\
\multirow{2}{*}{第一步} & 证明一 & 证明二 \\
 & 推导一 & 推导二 \\
图形表达 & ![](/static/uploads/fig3.png) & ![](/static/uploads/fig4.png) \\
\end{tabular}`;
const rendered = formatQuestionContentHtml(complex, 42, 'right', false, true);
const first = rendered.indexOf('/static/uploads/fig12.png');
const table = rendered.indexOf(String.raw`\begin{tabular}`);
const third = rendered.indexOf('/static/uploads/fig3.png');
const fourth = rendered.indexOf('/static/uploads/fig4.png');
const tableEnd = rendered.indexOf(String.raw`\end{tabular}`);
if (!(first >= 0 && first < table && table < third && third < fourth && fourth < tableEnd)) {
  throw new Error(`complex image anchors changed: ${rendered}`);
}
if (rendered.includes('data-figure-align-qid')) {
  throw new Error(`inline images were converted into a detached figure group: ${rendered}`);
}

const simple = String.raw`普通右图题

![](/static/uploads/graph.png)`;
const simpleRendered = formatQuestionContentHtml(simple, 43, 'right', false, true);
if (!simpleRendered.includes('data-figure-align-qid="43"')) {
  throw new Error(`legacy trailing figure layout was not preserved: ${simpleRendered}`);
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_figure_size_presets_and_auto_wide_resolution_are_executable():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    helper_start = paper_source.index(
        "function shouldPreserveInlinePaperImages(raw)"
    )
    helper_end = paper_source.index("// Init on DOMContentLoaded", helper_start)
    helper_source = paper_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
global.window = {
  parseMarkdownWithMath: value => value,
  MathBankSafe: {
    safeImageUrl: value => value,
    escapeAttribute: value => value,
    sanitizeRichHtml: value => value
  }
};

const single = String.raw`宽幅合成图

![](/static/uploads/wide.png)`;
const autoRendered = formatQuestionContentHtml(single, 51, 'bottom_right', false, true, 'auto');
if (!autoRendered.includes('max-width: min(100%, 200px)') || !autoRendered.includes('max-height: 170px')) {
  throw new Error(`auto did not start from the standard single-image size: ${autoRendered}`);
}
const wideImage = {
  dataset: { figureSize: 'auto', figureAlign: 'bottom_right', figureImageCount: '1' },
  naturalWidth: 2400,
  naturalHeight: 800,
  style: {}
};
applyAutoFigureImageSize(wideImage);
if (wideImage.style.maxWidth !== 'min(100%, 420px)' || wideImage.style.maxHeight !== '300px') {
  throw new Error(`wide auto image did not expand to 420x300: ${JSON.stringify(wideImage.style)}`);
}
const autoMetrics = getDetachedFigureMetrics(single, 'bottom_right', 'auto');
if (autoMetrics.maxWidth !== 420 || autoMetrics.maxHeight !== 300 || autoMetrics.blockHeight < 300) {
  throw new Error(`auto pagination did not reserve the wide ceiling: ${JSON.stringify(autoMetrics)}`);
}

const multiple = String.raw`多图

![](/static/uploads/one.png)

![](/static/uploads/two.png)`;
const multiRendered = formatQuestionContentHtml(multiple, 52, 'center', false, true, 'auto');
const legacyWidths = multiRendered.match(/max-width: min\(100%, 150px\)/g) || [];
const legacyHeights = multiRendered.match(/max-height: 140px/g) || [];
if (legacyWidths.length !== 2 || legacyHeights.length !== 2) {
  throw new Error(`auto multi-image compatibility changed: ${multiRendered}`);
}

const rightLarge = formatQuestionContentHtml(single, 53, 'right', false, true, 'large');
if (!rightLarge.includes('max-width: min(100%, 155px)') ||
    !rightLarge.includes('max-height: 135px') ||
    !rightLarge.includes('width: 160px; max-width: 160px;')) {
  throw new Error(`right layout escaped its 160px safety column: ${rightLarge}`);
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_bottom_left_figure_layout_runs_across_state_and_both_previews():
    api_source = _read(STATIC_JS_DIR / "api.js")
    api_start = api_source.index("const FigureLayoutState = {")
    api_end_marker = "window.FigureLayoutState = FigureLayoutState;"
    api_end = api_source.index(api_end_marker, api_start) + len(api_end_marker)
    layout_state_source = api_source[api_start:api_end]

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    assert "['right', 'bottom_left', 'center', 'bottom_right'].includes(figureAlign)" in editor_source
    assert "['right', 'bottom_left', 'center', 'bottom_right'].includes(value)" in editor_source

    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    editor_preview_start = ocr_source.index("function currentEditorFigureLayout()")
    editor_preview_end_marker = "window.applyEditorFigureLayoutPreview = applyEditorFigureLayoutPreview;"
    editor_preview_end = ocr_source.index(
        editor_preview_end_marker, editor_preview_start
    ) + len(editor_preview_end_marker)
    editor_preview_source = ocr_source[editor_preview_start:editor_preview_end]

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    paper_helper_start = paper_source.index("function shouldPreserveInlinePaperImages(raw)")
    paper_helper_end = paper_source.index("// Init on DOMContentLoaded", paper_helper_start)
    paper_helper_source = paper_source[paper_helper_start:paper_helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
global.window = {};
''' + layout_state_source + r'''
FigureLayoutState.hydrate({ figure_align: 'bottom_left', figure_size: 'large', figure_align_custom: true });
if (FigureLayoutState.align !== 'bottom_left' || !FigureLayoutState.customAlign) {
  throw new Error(`bottom-left layout was rejected during hydration: ${JSON.stringify(FigureLayoutState.snapshot())}`);
}
FigureLayoutState.setAlign('right');
FigureLayoutState.setAlign('bottom_left');
if (FigureLayoutState.align !== 'bottom_left') {
  throw new Error('bottom-left layout was rejected by setAlign');
}

const EDITOR_FIGURE_SIZE_LABELS = { auto: '自动', small: '小', medium: '中', large: '大' };
const EDITOR_FIGURE_ALIGN_LABELS = { right: '题干右侧', bottom_left: '下方居左', center: '下方居中', bottom_right: '下方居右' };
window.FigureLayoutState = FigureLayoutState;
const wrapper = { style: {} };
const editorImage = {
  dataset: {}, style: {}, parentElement: wrapper,
  naturalWidth: 900, naturalHeight: 600, complete: true,
  classList: { add() {}, remove() {} },
  attrs: {},
  setAttribute(name, value) { this.attrs[name] = String(value); },
  addEventListener() {}
};
const editorContainer = {
  style: {}, firstChild: {},
  querySelectorAll() { return [editorImage]; },
  insertBefore() {}, appendChild() {}
};
global.document = { getElementById() { return null; } };
''' + editor_preview_source + r'''
applyEditorFigureLayoutPreview(
  editorContainer,
  '题干\n\n![](/static/uploads/bottom-left.png)'
);
if (wrapper.style.textAlign !== 'left') {
  throw new Error(`editor preview was not left-aligned: ${JSON.stringify(wrapper.style)}`);
}
if (editorImage.attrs['aria-label'] !== '调整插图排版：下方居左，大') {
  throw new Error(`editor accessible label lost bottom-left wording: ${editorImage.attrs['aria-label']}`);
}

window.parseMarkdownWithMath = value => value;
window.MathBankSafe = {
  safeImageUrl: value => value,
  escapeAttribute: value => value,
  sanitizeRichHtml: value => value
};
''' + paper_helper_source + r'''
const source = '题干\n\n![](/static/uploads/bottom-left.png)';
const rendered = formatQuestionContentHtml(source, 61, 'bottom_left', false, true, 'large');
if (!rendered.includes('class="my-2 text-left"') ||
    !rendered.includes('justify-start') ||
    !rendered.includes('data-figure-align="bottom_left"') ||
    !rendered.includes('下方居左')) {
  throw new Error(`paper bottom-left layout was incomplete: ${rendered}`);
}
const embedded = formatQuestionContentHtml(source, 61, 'bottom_left', true, true, 'large');
if (embedded.figAlign !== 'bottom_left' || !embedded.imgHtml.includes('justify-start')) {
  throw new Error(`solution-space bottom-left layout was incomplete: ${JSON.stringify(embedded)}`);
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    assert "['bottom_left', 'center', 'bottom_right'].includes(renderedFigAlign)" in paper_source
    assert "renderedFigAlign === 'bottom_left' ? 'left-3' : 'right-3'" in paper_source


def test_paper_and_editor_figure_layout_controls_keep_their_write_boundaries():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    import_source = _read(STATIC_JS_DIR / "import.js")

    paper_handler_start = paper_source.index("window.setFigureLayout = function")
    paper_handler_end = paper_source.index(
        "window.showFigureAlignPopover = function", paper_handler_start
    )
    paper_handler = paper_source[paper_handler_start:paper_handler_end]
    for marker in (
        "/api/questions/${qid}/figure_layout",
        "formData.append('figure_align', nextAlign)",
        "formData.append('figure_size', nextSize)",
        "window.setFigureSize",
    ):
        assert marker in paper_handler
    assert "const FIGURE_SIZE_VALUES = ['auto', 'small', 'medium', 'large']" in paper_source
    assert "if (q.figure_align_custom && ['right', 'bottom_left', 'center', 'bottom_right'].includes(q.figure_align))" in paper_source
    assert "FIGURE_SIZE_VALUES.map(size =>" in paper_source
    assert "window.setFigureSize(${qid}, '${size}')" in paper_source
    assert "window.waitForFigureLayoutWrite = async function" in paper_source
    assert "window.isQuestionSaveInFlight()" in paper_handler
    assert "await window.waitForFigureLayoutWrite(pendingLayoutQuestionId)" in import_source

    editor_handler_start = ocr_source.index("window.setEditorFigureLayout = function")
    editor_handler_end = ocr_source.index(
        "window.showEditorFigureLayoutPopover = function", editor_handler_start
    )
    editor_handler = ocr_source[editor_handler_start:editor_handler_end]
    assert "FigureLayoutState.setAlign" in editor_handler
    assert "FigureLayoutState.setSize" in editor_handler
    assert "dispatchEvent(new Event('input'))" in editor_handler
    assert "fetch(" not in editor_handler

    assert "let layoutChipRendered = false" in ocr_source
    assert "const showLayoutChip = anchored || (allowLayoutControls && !layoutChipRendered)" in ocr_source
    assert ocr_source.count("showLayoutChip ?") == 1
    assert "window.showEditorFigureLayoutPopover(event)" in ocr_source
    assert "function hasDetachedEditorFigureGroup(sourceText)" in ocr_source
    assert "const allowLayoutControls = hasDetachedEditorFigureGroup" in ocr_source
    assert "调整此图的对齐与尺寸，保持正文位置" in ocr_source
    assert 'data-editor-image-key="${window.MathBankSafe.escapeAttribute(anchored ? imageKey' in ocr_source
    assert "anchoredKeys.has(imageKey)" in ocr_source

    editor_popover_start = ocr_source.index("window.showEditorFigureLayoutPopover")
    editor_popover_end = ocr_source.index(
        "function renderIllustrationBadges()", editor_popover_start
    )
    editor_popover = ocr_source[editor_popover_start:editor_popover_end]
    assert "Object.keys(alignLabels).map(align =>" in editor_popover
    assert 'class="grid grid-cols-2 gap-1"' in editor_popover
    assert "${alignLabels[align]}</button>" in editor_popover
    assert "imageKey ? '保持正文顺序'" in editor_popover
    assert ".replace('题干', '').replace('下方', '')" not in editor_popover
    for label in ('题干右侧', '下方居左', '下方居中', '下方居右'):
        assert label in ocr_source
    assert "['auto', 'small', 'medium', 'large'].map(size =>" in editor_popover
    assert "window.setEditorFigureLayout('size', '${size}')" in editor_popover

    for label in ('题干右侧', '题干下方居左', '题干下方居中', '题干下方居右'):
        assert label in paper_source
    assert "window.setFigureAlign(${qid}, 'bottom_left')" in paper_source
    assert 'aria-label="插图位置：题干下方居左"' in paper_source


def test_editor_detached_preview_click_edits_layout_and_preserves_original_view():
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    helper_start = ocr_source.index("function currentEditorFigureLayout()")
    helper_end_marker = "window.applyEditorFigureLayoutPreview = applyEditorFigureLayoutPreview;"
    helper_end = ocr_source.index(helper_end_marker, helper_start) + len(helper_end_marker)
    helper_source = ocr_source[helper_start:helper_end]
    click_start = ocr_source.index("function handleEditorFigureLayoutPreviewClick(event)")
    click_end = ocr_source.index(
        "function handleEditorFigureLayoutPreviewKeydown(event)", click_start
    )
    click_source = ocr_source[click_start:click_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    api_source = _read(STATIC_JS_DIR / 'api.js')
    shared = api_source[api_source.index('window.ImageLayoutTools = {'):api_source.index('const FigureLayoutState = {')]
    script = r'''
const EDITOR_FIGURE_SIZE_LABELS = { auto: '自动', small: '小', medium: '中', large: '大' };
const EDITOR_FIGURE_ALIGN_LABELS = { right: '题干右侧', bottom_left: '下方居左', center: '下方居中', bottom_right: '下方居右' };
let popoverCount = 0;
let openCount = 0;
global.window = {
  FigureLayoutState: {
    snapshot() { return { figure_align: 'right', figure_size: 'auto' }; }
  },
  showEditorFigureLayoutPopover() { popoverCount += 1; },
  open() { openCount += 1; }
};
global.document = { getElementById() { return null; } };

function makeImage(src) {
  const wrapper = { style: {}, parentElement: null };
  const image = {
    dataset: {}, style: {}, parentElement: wrapper,
    naturalWidth: 600, naturalHeight: 300, complete: true,
    attrs: { src, 'data-safe-image-open': 'true' },
    classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name] || null; },
    addEventListener() {},
    closest(selector) {
      if (selector === 'img[data-editor-figure-layout]' && this.dataset.editorFigureLayout === 'true') return this;
      if (selector === 'img[data-safe-image-open]' && this.attrs['data-safe-image-open']) return this;
      return null;
    }
  };
  return image;
}
function makeContainer(image) {
  return {
    style: {}, firstChild: {},
    querySelectorAll() { return [image]; },
    insertBefore() {}, appendChild() {}
  };
}
function clickEvent(image, modifiers = {}) {
  return {
    target: image,
    metaKey: Boolean(modifiers.metaKey),
    ctrlKey: Boolean(modifiers.ctrlKey),
    prevented: false,
    stopped: false,
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; }
  };
}
function runGenericImageOpener(event) {
  if (event.stopped) return;
  const image = event.target.closest('img[data-safe-image-open]');
  if (image) window.open(image.getAttribute('src'), '_blank');
}
''' + shared + '\n' + helper_source + '\n' + click_source + r'''

const detached = makeImage('/static/uploads/detached.png');
applyEditorFigureLayoutPreview(
  makeContainer(detached),
  '题干\n\n![](/static/uploads/detached.png)'
);
if (detached.dataset.editorFigureLayout !== 'true' || detached.attrs.role !== 'button') {
  throw new Error('detached editor image was not promoted to a layout control');
}
const normalClick = clickEvent(detached);
handleEditorFigureLayoutPreviewClick(normalClick);
runGenericImageOpener(normalClick);
if (popoverCount !== 1 || openCount !== 0 || !normalClick.prevented || !normalClick.stopped) {
  throw new Error(`normal click boundary failed: popover=${popoverCount}, open=${openCount}`);
}

const modifierClick = clickEvent(detached, { metaKey: true });
handleEditorFigureLayoutPreviewClick(modifierClick);
runGenericImageOpener(modifierClick);
if (popoverCount !== 1 || openCount !== 1) {
  throw new Error('Cmd-click did not preserve original-image viewing');
}

const anchored = makeImage('/static/uploads/anchored.png');
applyEditorFigureLayoutPreview(
  makeContainer(anchored),
  '![](/static/uploads/anchored.png)\n\n后续正文'
);
if (anchored.dataset.editorImageKey !== 'anchored.png') {
  throw new Error('anchored image did not receive its own layout control');
}
const anchoredClick = clickEvent(anchored);
handleEditorFigureLayoutPreviewClick(anchoredClick);
runGenericImageOpener(anchoredClick);
if (popoverCount !== 2 || openCount !== 1) {
  throw new Error('anchored image did not open its own menu');
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_right_layout_size_choices_resolve_to_visible_safe_layouts():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    wrapper_start = paper_source.index("window.setFigureAlign = function")
    wrapper_end = paper_source.index(
        "window.showFigureAlignPopover = function", wrapper_start
    )
    wrapper_source = paper_source[wrapper_start:wrapper_end]
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    editor_start = ocr_source.index("window.setEditorFigureLayout = function")
    editor_end = ocr_source.index(
        "window.showEditorFigureLayoutPopover = function", editor_start
    )
    editor_source = ocr_source[editor_start:editor_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
function normalizeFigureSize(value) {
  return ['auto', 'small', 'medium', 'large'].includes(value) ? value : 'auto';
}
function getQuestionFigSize(question) { return normalizeFigureSize(question && question.figure_size); }
function getQuestionFigAlign(question) { return question.figure_align || 'right'; }
const paperCalls = [];
const question = { id: 7, figure_align: 'right', figure_size: 'auto' };
const editorState = { align: 'right', size: 'auto', customAlign: false };
global.window = {
  PaperStore: { questionsMap: { 7: question } },
  setFigureLayout(qid, align, size) {
    paperCalls.push({ qid, align, size });
    question.figure_align = align;
    question.figure_size = size;
  },
  FigureLayoutState: {
    get align() { return editorState.align; },
    get size() { return editorState.size; },
    setAlign(value) { editorState.align = value; },
    setSize(value) { editorState.size = value; },
    setCustomAlign(value) { editorState.customAlign = Boolean(value); }
  },
  renderIllustrationBadges() {}
};
global.document = { getElementById() { return null; } };
global.applyEditorFigureLayoutPreview = function() {};
global.setTimeout = function(callback) { callback(); };
''' + wrapper_source + '\n' + editor_source + r'''

window.setFigureSize(7, 'large');
if (paperCalls[0].align !== 'bottom_right' || paperCalls[0].size !== 'large') {
  throw new Error(`paper large did not move below-right: ${JSON.stringify(paperCalls[0])}`);
}
window.setFigureAlign(7, 'right');
if (paperCalls[1].align !== 'right' || paperCalls[1].size !== 'small') {
  throw new Error(`paper right did not restore compact size: ${JSON.stringify(paperCalls[1])}`);
}

window.setEditorFigureLayout('size', 'medium');
if (editorState.align !== 'bottom_right' || editorState.size !== 'medium' || !editorState.customAlign) {
  throw new Error(`editor medium did not move below-right: ${JSON.stringify(editorState)}`);
}
window.setEditorFigureLayout('align', 'right');
if (editorState.align !== 'right' || editorState.size !== 'small') {
  throw new Error(`editor right did not restore compact size: ${JSON.stringify(editorState)}`);
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_figure_layout_writes_are_serial_and_reconcile_live_objects():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    handler_start = paper_source.index("const figureLayoutWrites = Object.create(null)")
    handler_end = paper_source.index(
        "window.showFigureAlignPopover = function", handler_start
    )
    handler_source = paper_source[handler_start:handler_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
function normalizeFigureSize(value) {
  return ['auto', 'small', 'medium', 'large'].includes(value) ? value : 'auto';
}
function getQuestionFigSize(question) {
  return normalizeFigureSize(question && question.figure_size);
}
function getQuestionFigAlign(question) {
  const value = String(question && question.figure_align || 'right');
  return ['right', 'bottom_left', 'center', 'bottom_right'].includes(value) ? value : 'right';
}
const FIGURE_SIZE_LABELS = { auto: '自动', small: '小', medium: '中', large: '大' };

const editorLayout = {
  figure_align: 'right',
  figure_size: 'auto',
  figure_align_custom: false
};
const baselineCommits = [];
global.window = {
  PaperStore: {
    questionsMap: {
      1: { id: 1, seq_num: 1, figure_align: 'right', custom_figure_align: 'right', figure_size: 'auto' },
      3: { id: 3, seq_num: 3, figure_align: 'right', custom_figure_align: 'right', figure_size: 'auto' },
      4: { id: 4, seq_num: 4, figure_align: 'right', custom_figure_align: 'right', figure_size: 'auto' }
    }
  },
  EditorState: { questionId: 1 },
  FigureLayoutState: {
    setAlign(value) { editorLayout.figure_align = value; },
    setSize(value) { editorLayout.figure_size = value; },
    setCustomAlign(value) { editorLayout.figure_align_custom = Boolean(value); },
    snapshot() { return { ...editorLayout }; }
  },
  commitEditorFigureLayoutBaseline(qid, align, size, customAlign) {
    baselineCommits.push({ qid, align, size, customAlign });
  },
  renderPart3QuestionStream() {},
  renderPaperCanvas() {},
  showToast() {}
};
global.document = { getElementById() { return null; } };
global.FormData = class {
  constructor() { this.entries = []; }
  append(key, value) { this.entries.push([key, value]); }
};
global.console = { ...console, error() {} };

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
function response(ok, body, status) {
  return { ok, status: status || (ok ? 200 : 500), json: async () => body };
}
const fetchCalls = [];
global.fetch = (url, options) => {
  const pending = deferred();
  fetchCalls.push({ url, options, pending });
  return pending.promise;
};
const tick = () => new Promise(resolve => setImmediate(resolve));
''' + handler_source + r'''

(async () => {
  window.setFigureLayout(1, 'center', 'medium');
  window.setFigureLayout(1, 'bottom_right', 'large');
  await tick();
  if (fetchCalls.length !== 1) {
    throw new Error(`second layout write was not queued: ${fetchCalls.length} fetches started`);
  }

  // The user moved to another editor record while the first request was in flight.
  window.EditorState.questionId = 2;
  editorLayout.figure_align = 'right';
  editorLayout.figure_size = 'small';

  fetchCalls[0].pending.resolve(response(true, {
    status: 'success', figure_align: 'center', figure_size: 'medium'
  }));
  await tick();
  await tick();
  if (fetchCalls.length !== 2) {
    throw new Error(`second layout write did not start after first success: ${fetchCalls.length}`);
  }

  fetchCalls[1].pending.resolve(response(false, { detail: 'write failed' }, 500));
  await tick();
  await tick();
  const firstQuestion = window.PaperStore.questionsMap[1];
  if (firstQuestion.figure_align !== 'center' || firstQuestion.figure_size !== 'medium') {
    throw new Error(`second failure did not roll back to first confirmation: ${JSON.stringify(firstQuestion)}`);
  }
  if (editorLayout.figure_align !== 'right' || editorLayout.figure_size !== 'small') {
    throw new Error(`late response changed the newly active editor: ${JSON.stringify(editorLayout)}`);
  }
  if (baselineCommits.length !== 0) {
    throw new Error(`late response committed a baseline for an inactive editor: ${JSON.stringify(baselineCommits)}`);
  }

  // A bank refresh may replace the question object while the request is pending.
  window.setFigureLayout(3, 'center', 'medium');
  await tick();
  if (fetchCalls.length !== 3) {
    throw new Error(`replacement scenario request did not start: ${fetchCalls.length}`);
  }
  const replacement = {
    id: 3, seq_num: 3, figure_align: 'right', custom_figure_align: 'right', figure_size: 'auto'
  };
  window.PaperStore.questionsMap[3] = replacement;
  fetchCalls[2].pending.resolve(response(true, {
    status: 'success', figure_align: 'bottom_right', figure_size: 'large'
  }));
  await tick();
  await tick();
  if (replacement.figure_align !== 'bottom_right' || replacement.figure_size !== 'large') {
    throw new Error(`confirmed layout did not update replacement object: ${JSON.stringify(replacement)}`);
  }

  // A newer unsaved editor-only layout must survive an older paper response,
  // while the server-confirmed layout still advances only the saved baseline.
  window.EditorState.questionId = 4;
  editorLayout.figure_align = 'right';
  editorLayout.figure_size = 'auto';
  window.setFigureLayout(4, 'center', 'medium');
  await tick();
  editorLayout.figure_align = 'bottom_right';
  editorLayout.figure_size = 'large';
  fetchCalls[3].pending.resolve(response(true, {
    status: 'success', figure_align: 'center', figure_size: 'medium'
  }));
  await tick();
  await tick();
  if (editorLayout.figure_align !== 'bottom_right' || editorLayout.figure_size !== 'large') {
    throw new Error(`paper response overwrote newer editor-only layout: ${JSON.stringify(editorLayout)}`);
  }
  const lastBaseline = baselineCommits[baselineCommits.length - 1];
  if (!lastBaseline || lastBaseline.qid !== 4 || lastBaseline.align !== 'center' || lastBaseline.size !== 'medium') {
    throw new Error(`confirmed baseline was not advanced independently: ${JSON.stringify(baselineCommits)}`);
  }
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_solution_space_controls_stay_at_the_resizable_zone_top():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    block_start = paper_source.index("solutionBlankHtml = `")
    block_end = paper_source.index("const itemHtml = `", block_start)
    block_source = paper_source[block_start:block_end]

    zone_position = block_source.index("solution-space-zone")
    controls_position = block_source.index("solution-space-controls", zone_position)
    assert zone_position < controls_position
    assert 'data-solution-space-controls-qid=' in block_source
    assert 'data-solution-space-zone-qid=' in block_source
    assert 'data-solution-space-delta="-1"' in block_source
    assert 'data-solution-space-delta="-0.5"' in block_source
    assert 'data-solution-space-delta="0.5"' in block_source
    assert 'data-solution-space-delta="1"' in block_source
    assert 'inline-flex w-14 justify-center' in block_source

    controls_source = block_source[controls_position:]
    assert "absolute right-2 top-2" in controls_source
    assert "bottom-2" not in controls_source
    assert "min-h-[32px]" not in block_source

    handler_start = paper_source.index(
        "function restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta)"
    )
    handler_end = paper_source.index(
        "window.updateGlobalSolutionSpace", handler_start
    )
    handler_source = paper_source[handler_start:handler_end]
    assert "getBoundingClientRect().top" in handler_source
    assert "scrollParent.scrollTop += shift" in handler_source
    assert "requestAnimationFrame(restoreAnchor)" in handler_source
    assert "nextButton.focus({ preventScroll: true })" in handler_source
    assert handler_source.index("window.renderPaperCanvas()") < handler_source.rindex(
        "restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta)"
    )


def test_static_dialogs_expose_modal_semantics_and_accessible_names():
    elements = _index_elements()
    labelled_dialogs = {
        "aiClassifyModal": "aiClassifyModalTitle",
        "settingsModal": "settingsModalTitle",
        "updateModal": "updateModalTitle",
        "statsModal": "statsModalTitle",
        "parsedDuplicateReviewModal": "parsedDuplicateReviewTitle",
        "pdfCropModal": "pdfCropModalTitle",
        "answerTikzWorkbenchModal": "answerTikzWorkbenchTitle",
    }

    for dialog_id, title_id in labelled_dialogs.items():
        attributes = elements[dialog_id]
        assert attributes["role"] == "dialog"
        assert attributes["aria-modal"] == "true"
        assert attributes["aria-hidden"] == "true"
        assert attributes["aria-labelledby"] == title_id
        assert title_id in elements

    lightbox = elements["imageLightbox"]
    assert lightbox["role"] == "dialog"
    assert lightbox["aria-modal"] == "true"
    assert lightbox["aria-hidden"] == "true"
    assert lightbox["aria-label"]

    workspace_button = elements["workspaceDropdownBtn"]
    assert workspace_button["role"] == "button"
    assert workspace_button["tabindex"] == "0"
    assert workspace_button["aria-haspopup"] == "menu"
    assert workspace_button["aria-expanded"] == "false"
    assert workspace_button["aria-label"]
    for button_id in ("toggleSidebarBtn", "themeDropdownBtn", "darkModeBtn", "statsOpenBtn"):
        assert elements[button_id]["aria-label"]


def test_duplicate_review_has_accessible_decision_controls_and_live_summary():
    elements = _index_elements()
    summary = elements["parsedDuplicateSummary"]
    assert summary["role"] == "status"
    assert summary["aria-live"] == "polite"
    assert summary["aria-atomic"] == "true"

    modal = elements["parsedDuplicateReviewModal"]
    assert modal["aria-describedby"] == "parsedDuplicateReviewDescription"
    for button_id in (
        "duplicateReviewReturnBtn",
        "duplicateReviewSkipBtn",
        "duplicateReviewIndependentBtn",
    ):
        assert button_id in elements

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "window.MathBankModal.open(modal" in import_source
    assert "onEscape: () => closeParsedDuplicateReviewModal('review')" in import_source
    assert "badge.focus({ preventScroll: true })" in import_source


def test_ai_classification_requires_manual_single_or_multi_choice_confirmation():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")
    css_source = _read(CSS_PATH)

    assert "temporaryClassifyData.question_type" not in import_source
    assert "qtypeLabels[data.question_type]" not in import_source
    assert "temporaryClassifyData.question_form === 'choice'" in import_source
    assert "!temporaryClassifyQuestionType" in import_source
    assert "请先确认此题是单选题还是多选题" in import_source
    assert "qtypeSelect.value = temporaryClassifyQuestionType" in import_source
    assert "window.selectClassifiedChoiceType = selectClassifiedChoiceType" in import_source
    assert 'id="recQType"' not in index_source
    assert 'id="choiceTypeConfirm"' in index_source
    assert 'id="classifySingleChoiceBtn"' in index_source
    assert 'id="classifyMultiChoiceBtn"' in index_source
    assert 'role="radiogroup"' in index_source
    assert index_source.count("question-type-choice-check") == 2
    assert "已识别为选择题，请手动确认" in index_source
    assert "确认分类并保存题目" in index_source
    assert '.question-type-choice-button[aria-checked="true"]:hover' in css_source
    assert 'color: #ffffff;' in css_source
    assert '.question-type-choice-button[aria-checked="true"] .question-type-choice-check' in css_source
    assert "button.classList.toggle('bg-brand-50'" not in import_source


def test_modal_manager_traps_focus_handles_escape_and_restores_focus():
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "window.MathBankModal = MathBankModal",
        "const focusableSelector",
        "function resolveInitialFocusTarget(dialog, options = {})",
        "dialog.querySelector('[autofocus]')",
        "const labelledBy = dialog.getAttribute('aria-labelledby')",
        "document.getElementById(labelledBy)",
        "data-modal-initial-focus",
        "focusDialogContext(dialog, options)",
        "previousFocus: document.activeElement",
        "event.key === 'Escape'",
        "event.key !== 'Tab'",
        "if (!entry.dialog.contains(document.activeElement))",
        "!focusable.includes(document.activeElement)",
        "element.inert = isIsolated",
        "restoreTarget.focus({ preventScroll: true })",
    ):
        assert marker in api_source

    assert "dialog.querySelector('[autofocus], [data-modal-close]')" not in api_source
    assert "|| getFocusable(dialog)[0]" not in api_source

    assert api_source.count("window.MathBankModal.open") >= 2
    assert editor_source.count("window.MathBankModal.open") >= 3
    assert import_source.count("window.MathBankModal.open") >= 3
    assert "window.MathBankModal.open(lightbox" in ocr_source
    assert "window.MathBankModal.open(modal" in paper_source


def test_loading_saved_paper_restores_modal_background_interactivity():
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    close_start = paper_source.index("window.closeSavedPapersModal = function ()")
    close_end = paper_source.index("window.openSavedPapersModal = async function ()", close_start)
    close_source = paper_source[close_start:close_end]
    load_start = paper_source.index("window.loadSavedPaper = async function (paperId)")
    load_end = paper_source.index("window.deleteSavedPaper = async function (paperId)", load_start)
    load_source = paper_source[load_start:load_end]

    assert close_source.index("window.MathBankModal.close(modal)") < close_source.index("modal.remove()")
    assert "window.closeSavedPapersModal();" in load_source
    assert "modal.remove()" not in load_source


def test_mobile_layout_touch_targets_and_dialog_panes_have_regression_guards():
    css_source = _read(CSS_PATH)

    for marker in (
        "@media (max-width: 768px)",
        "@media (max-width: 768px), (pointer: coarse)",
        "@media (hover: none), (pointer: coarse)",
        "min-width: 44px",
        "min-height: 44px",
        "height: calc(100dvh - 56px)",
        "#bankWorkspaceSection",
        "#paperWorkspaceSection",
        "#previewSection",
        ".import-workspace-content",
        "#pdfCropModalContent",
        "#pdfPagesThumbnailsContainer",
        '.question-card button[aria-label="删除题目"]',
        "max-width: calc(100vw - 1rem)",
        ".sidebar-pagination-controls",
        "overflow-x: auto",
        "overscroll-behavior-x: contain",
        ".tikz-workbench-modal",
        "#answerTikzWorkbenchModal",
        ".answer-tikz-entry",
    ):
        assert marker in css_source

    assert "html.init-ws-paper #paperWorkspaceSection { display: flex !important; }" in css_source
    assert "#bankWorkspaceSection.bank-browser.hidden" in css_source
    assert "#importWorkspaceSection.hidden" in css_source
    assert "#recordsWorkspaceSection.hidden" in css_source
    assert "sidebar-pagination-controls" in _read(STATIC_JS_DIR / "editor.js")


def test_application_shell_navigation_reuses_peer_workspaces():
    elements = _index_elements()
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    api_source = _read(STATIC_JS_DIR / "api.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    assert elements["appNavigation"]["aria-label"] == "MathBank 主导航"
    assert elements["appNavPrimary"]["aria-label"] == "主要工作区"
    assert elements["appNavDashboard"]["data-app-nav-target"] == "dashboard"
    assert elements["appNavDashboard"]["aria-current"] == "page"
    assert elements["appNavBank"]["data-app-nav-target"] == "bank"
    assert elements["appNavImport"]["data-app-nav-target"] == "import"
    assert elements["appNavPaper"]["data-app-nav-target"] == "paper"
    assert elements["appNavRecords"]["data-app-nav-target"] == "records"

    assert "selectWorkspace('dashboard', '工作台')" in index_source
    assert "selectWorkspace('bank', '题库管理')" in index_source
    assert "selectWorkspace('import', '导入中心')" in index_source
    assert "selectWorkspace('paper', '智能组卷')" in index_source
    assert "openSavedPapersModal()" in index_source
    assert 'id="appContentShell"' in index_source

    assert "window.setAppNavigationActive = function(targetId)" in api_source
    assert "button.setAttribute('aria-current', 'page')" in api_source
    assert "window.setAppNavigationActive(workspaceId)" in api_source
    assert "function openImportModal()" in import_source
    assert "window.selectWorkspace('import', '导入中心')" in import_source
    assert "const importWorkspaceSection = document.getElementById('importWorkspaceSection')" in paper_source
    assert "mainWorkspaceContainer.insertBefore(importWorkspaceSection, paperWorkspaceSection)" in paper_source
    assert "workspaceId === 'import'" in paper_source
    assert "importSec.classList.remove('hidden')" in paper_source
    assert "workspaceId === 'records'" in paper_source
    assert "recordsSec.classList.remove('hidden')" in paper_source
    assert "workspaceId === 'dashboard'" in paper_source
    assert "dashboardSec.classList.remove('hidden')" in paper_source

    for marker in (
        ".app-navigation",
        ".app-content-shell",
        '.app-nav-item[aria-current="page"]',
        "grid-template-columns: repeat(5, minmax(0, 1fr))",
        "padding-bottom: 64px",
    ):
        assert marker in css_source


def test_dashboard_workspace_reuses_read_only_metrics_and_existing_workflows():
    elements = _index_elements()
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    dashboard_source = _read(STATIC_JS_DIR / "dashboard.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for element_id in (
        "dashboardWorkspaceSection",
        "dashboardTitle",
        "dashboardQuestionTotal",
        "dashboardReviewCount",
        "dashboardPaperCount",
        "dashboardMonthAdditions",
        "dashboardTaskList",
        "dashboardActivityList",
    ):
        assert element_id in elements

    for marker in (
        "fetch('/api/stats')",
        "fetch('/api/papers')",
        "window.loadDashboardData = loadDashboardData",
        "selectWorkspace('import', '导入中心')",
        "window.startManualQuestion = startManualQuestion",
        "window.resumeSavedPaper = resumeSavedPaper",
        "window.openNewQuestionEditor",
        "window.loadSavedPaper",
    ):
        assert marker in dashboard_source or marker in index_source

    assert 'id="appNavDashboard"' in index_source
    assert 'onclick="startManualQuestion()"' in index_source
    assert "onclick=\"resumeSavedPaper(" in dashboard_source
    assert "html.init-ws-dashboard #dashboardWorkspaceSection" in css_source
    assert ".dashboard-stat-grid" in css_source
    assert ".dashboard-quick-actions" in css_source
    assert ".dashboard-main-grid" in css_source
    assert "align-items: stretch" in css_source
    assert ".dashboard-task-panel" in css_source
    assert "__preserveNewQuestionEditor" in editor_source
    assert "classList.remove('init-ws-dashboard', 'init-ws-paper')" in paper_source


def test_saved_paper_records_reuses_existing_actions_in_a_dedicated_workspace():
    elements = _index_elements()
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for element_id in (
        "recordsWorkspaceSection",
        "recordsWorkspaceTitle",
        "savedPaperTotalCount",
        "recordsListTitle",
        "savedPapersListContainer",
    ):
        assert element_id in elements

    assert elements["recordsWorkspaceSection"]["aria-labelledby"] == "recordsWorkspaceTitle"
    for marker in (
        "records-workspace-shell",
        "records-workspace-header",
        "records-overview",
        "records-list-panel",
        "records-list-container",
        "records-paper-grid",
        "saved-paper-card",
        "saved-paper-card-actions",
    ):
        assert marker in index_source or marker in paper_source
        assert f".{marker}" in css_source

    for marker in (
        "async function renderSavedPapersWorkspace()",
        "fetch('/api/papers')",
        "loadSavedPaper(${paperId})",
        "quickExportPaperPdf(${paperId})",
        "deleteSavedPaper(${paperId})",
        "window.selectWorkspace('records', '试卷记录')",
        "window.selectWorkspace('paper', '智能组卷')",
    ):
        assert marker in paper_source


def test_import_center_reuses_existing_pipeline_in_a_dedicated_workspace():
    elements = _index_elements()
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")

    for element_id in (
        "importWorkspaceSection",
        "importWorkspaceTitle",
        "importWorkspaceContent",
        "importInputPane",
        "importPaperTitle",
        "texDropzone",
        "texFileInput",
        "importLatexContent",
        "texImagesSection",
        "imagesDropzone",
        "imagesFileInput",
        "importGenerateAnswers",
        "pdfPageRangeContainer",
        "pdfPageRange",
        "runParseBtn",
        "resetAllImportBtn",
        "importReviewPane",
        "importPlaceholder",
        "importLoadingState",
        "importLogsConsole",
        "btnCancelImport",
        "parsedQuestionsWrapper",
        "parsedCardsContainer",
        "saveAllParsedBtn",
    ):
        assert element_id in elements

    for marker in (
        "import-workspace-section",
        "import-workspace-shell",
        "import-workspace-header",
        "import-workspace-heading",
        "import-workspace-steps",
        "import-workspace-content",
        "import-source-pane",
        "import-result-pane",
        "import-config-card",
        "import-primary-actions",
        "import-result-placeholder",
    ):
        assert marker in index_source

    assert "PDF、Word 与 LaTeX 试卷的拆解、审查和批量入库" in index_source
    assert "runAIPaperParse()" in index_source
    assert "confirmClearAllParsed()" in index_source
    assert "saveAllParsedQuestions()" in index_source
    assert "function openImportModal()" in import_source
    assert "function closeImportModal()" in import_source
    assert "Import center workspace" in css_source
    assert ".import-workspace-section" in css_source
    assert "#importInputPane.import-source-pane" in css_source
    assert "#importReviewPane.import-result-pane" in css_source

    workspace = elements["importWorkspaceSection"]
    assert "role" not in workspace
    assert "aria-modal" not in workspace
    assert workspace["aria-labelledby"] == "importWorkspaceTitle"


def test_smart_paper_studio_uses_clear_peer_panels_without_replacing_workflows():
    elements = _index_elements()
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for element_id in (
        "paperWorkspaceSection",
        "paperFilterSection",
        "togglePaperFilterBtn",
        "paperFilterToggleIcon",
        "paperFilterToggleTxt",
        "paperQuestionStream",
        "paperSplitResizer",
        "paperCanvasSection",
    ):
        assert element_id in elements

    for marker in (
        "paper-studio-frame",
        "paper-studio-header",
        "paper-studio-body",
        "paper-library-column",
        "paper-config-panel",
        "paper-question-panel",
        "paper-preview-column",
        "paper-filter-content",
        "paper-panel-heading",
        "paper-live-badge",
        "paper-split-resizer",
        "paper-split-resizer-grip",
    ):
        assert marker in index_source
        assert f".{marker}" in css_source

    assert 'aria-label="组卷配置与题目资源"' in index_source
    assert 'aria-label="试卷预览与导出"' in index_source
    assert 'aria-label="调整题目资源和试卷预览的宽度"' in index_source
    assert 'aria-orientation="vertical"' in index_source
    assert "window.togglePaperFilterBar = function ()" in paper_source
    assert "function initPaperSplitResizer()" in paper_source
    assert "function setPaperSplitRatio(value" in paper_source
    assert "ratioFromPointer(event.clientX)" in paper_source
    assert "window.setPaperSplitRatio = setPaperSplitRatio" in paper_source
    assert "function renderPart2FilterSection()" in paper_source
    assert "function renderPart3QuestionStream()" in paper_source
    assert "window.renderPaperCanvas = function ()" in paper_source
    assert "savePaperToDb()" in paper_source
    assert "exportPaperPdf('paper')" in paper_source
    assert "exportPaperWord()" in paper_source


def test_bank_browser_uses_detail_first_layout_and_card_based_editor_dialog():
    elements = _index_elements()
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")

    for element_id in (
        "bankWorkspaceSection",
        "sidebarSection",
        "sidebarTopPanel",
        "searchInput",
        "filterType",
        "filterDifficulty",
        "filterCompulsory",
        "filterChapter",
        "filterSource",
        "filterSort",
        "questionsList",
        "sidebarPagination",
        "resizer-1",
        "editorSection",
        "saveQuestionBtn",
        "questionContentPanel",
        "answerExplanationPanel",
        "resizer-2",
        "previewSection",
        "editQuestionFromPreviewBtn",
        "questionResultSummary",
    ):
        assert element_id in elements

    for marker in (
        "bank-browser",
        "bank-management-header",
        "bank-management-actions",
        "bank-filter-toolbar",
        "bank-library-panel",
        "bank-library-heading",
        "bank-question-pane",
        "bank-split-resizer",
        "bank-list-toolbar",
        "bank-sort-control",
        "bank-question-list",
        "bank-detail-panel",
        "question-editor-dialog",
        "question-editor-modal-surface",
        "question-editor-steps",
        'data-editor-panel="classification"',
        'data-editor-panel="content"',
        'data-editor-panel="answer"',
    ):
        assert marker in index_source

    assert 'id="filterSource"' in index_source
    assert '<option value="desc" selected>最近更新</option>' in index_source
    assert 'onclick="openNewQuestionEditor()"' in index_source
    assert 'onclick="selectWorkspace(\'import\', \'导入中心\')"' in index_source
    assert 'role="separator"' in index_source
    assert 'aria-orientation="vertical"' in index_source
    assert "openQuestionEditorModal('classification')" in index_source
    assert index_source.index('class="bank-management-header"') < index_source.index('class="bank-filter-toolbar"')
    assert index_source.index('class="bank-filter-toolbar"') < index_source.index('id="sidebarSection"')
    assert "bank-question-card" in editor_source
    assert "bank-question-excerpt" in editor_source
    assert "bank-question-meta" in editor_source
    assert "function switchQuestionEditorPanel(panelId)" in editor_source
    assert "function openQuestionEditorModal(panelId = 'classification')" in editor_source
    assert "function closeQuestionEditorModal()" in editor_source
    assert "function openNewQuestionEditor()" in editor_source
    assert "function setBankSplitRatio(value" in editor_source
    assert "ratioFromPointer(event.clientX)" in editor_source
    assert "window.setBankSplitRatio = setBankSplitRatio" in editor_source
    assert "summary.textContent = `共 ${totalItems} 道题`" in editor_source
    assert "shouldAutoSelectFirstQuestion" in editor_source
    assert "setQuestionDetailEditAvailability(true)" in editor_source
    assert "setQuestionDetailEditAvailability(true)" in import_source
    assert "Bank browser and card-based editor dialog" in css_source
    assert "--bank-list-track" in css_source
    assert "#resizer-1.bank-split-resizer" in css_source
    assert "#previewSection.bank-detail-panel" in css_source
    assert "#editorSection.question-editor-dialog" in css_source


def test_shared_tikz_workbench_is_multimodal_contextual_and_persistent():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")
    api_source = _read(STATIC_JS_DIR / "api.js")

    for marker in (
        'id="openContentTikzWorkbenchBtn"',
        'id="contentTikzAssetsPanel"',
        'id="contentTikzAssetsList"',
        'id="openAnswerTikzWorkbenchBtn"',
        'id="answerTikzInstruction"',
        'id="answerTikzReferenceInput"',
        'id="answerTikzReferenceDropZone"',
        'id="answerTikzUseContext"',
        'id="answerTikzWorkbenchCode"',
        'id="insertAnswerTikzWorkbenchBtn"',
        'id="tikzWorkbenchTargetBadge"',
        'role="button" tabindex="0"',
    ):
        assert marker in index_source

    assert "const TikzState = {" in api_source
    assert "contentAssets: []" in api_source
    assert "answerAssets: []" in api_source
    for marker in (
        "window.openTikzWorkbench",
        "window.openContentTikzWorkbench",
        "window.openAnswerTikzWorkbench",
        "window.renderContentTikzAssets",
        "window.registerAutoContentTikzAsset",
        "window.renderAnswerTikzAssets",
        "window.collectAnswerImagePaths",
        "formData.append('reference_image'",
        "formData.append('reference_image_path'",
        "fetch('/api/ai/draw_tikz'",
        "insertMarkdownAtSavedCursor",
        "answer_tikz_assets",
        "content_tikz_assets",
        "tikz_reference_image_path",
        "window.MathBankModal.open(modal",
        "EditorState.isCurrent(tikzWorkbenchState.editorSession)",
    ):
        assert marker in import_source

    for removed_legacy_marker in (
        'id="contentTikzContainer"',
        'id="answerTikzContainer"',
        'id="editContentTikzCode"',
        'id="editAnswerTikzCode"',
        "window.renderContentTikzToImage",
        "window.renderAnswerTikzToImage",
        "fetch('/api/correct_tikz'",
        "fetch('/api/ai/draw_tikz_from_image'",
    ):
        assert removed_legacy_marker not in index_source
        assert removed_legacy_marker not in import_source


def test_ocr_auto_tikz_keeps_original_question_image_as_edit_reference():
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    import_source = _read(STATIC_JS_DIR / "import.js")

    for marker in (
        "formData.append('skip_tikz'",
        "window.registerAutoContentTikzAsset",
        "referenceImagePath: data.image_path || ''",
        "registerAutoTikzAsset('content', payload)",
        "setTikzReferencePath(",
        "data.reference_image_path",
        "formData.append('reference_image_path'",
    ):
        assert marker in ocr_source or marker in import_source

    assert "uploadedImages.push(safeReferencePath)" not in import_source
    assert "hiddenTikzReferencePaths" in import_source


def test_add_drawing_and_edit_drawing_are_separate_actions():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")

    assert index_source.count("<span>新增绘图</span>") == 2
    assert "<span>插入绘图</span>" not in index_source
    assert "新增一幅题干 TikZ 绘图，不会覆盖已有绘图" in index_source
    assert "新增一幅解答 TikZ 绘图，不会覆盖已有绘图" in index_source
    assert "window.openContentTikzWorkbench = function(assetId = null)" in import_source
    assert "window.openAnswerTikzWorkbench = function(assetId = null)" in import_source
    assert "? `修改${targetLabel} TikZ 绘图`" in import_source
    assert ": `新增${targetLabel} TikZ 绘图`" in import_source
    assert "TikzState.contentAssets.push(nextAsset)" in import_source
    assert "TikzState.contentAssets.splice(editingIndex, 1, nextAsset)" in import_source


def test_paper_question_answers_are_collapsible_and_loaded_on_demand():
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "answerCache: Object.create(null)",
        "expandedAnswerIds: new Set()",
        "window.togglePaperQuestionAnswer",
        "window.collapseAllPaperAnswers",
        "fetch(`/api/questions/${qid}`)",
        "window.parseMarkdownWithMath(answerText)",
        'aria-expanded="${answerExpanded ? \'true\' : \'false\'}"',
        "参考答案与解析",
        "收起全部答案",
    ):
        assert marker in paper_source


def test_final_ui_polish_shares_rhythm_feedback_and_dark_surfaces():
    css_source = _read(CSS_PATH)
    index_source = _read(INDEX_PATH)
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    rendered_sources = "\n".join((index_source, editor_source, paper_source))

    for token in (
        "--workspace-padding",
        "--workspace-gap",
        "--workspace-radius",
        "--workspace-card-radius",
        "--workspace-border",
        "--control-transition",
    ):
        assert token in css_source

    for state_class in ("ui-state-loading", "ui-state-empty", "ui-state-error"):
        assert state_class in rendered_sources

    for state_class in ("ui-state-icon", "ui-state-title", "ui-state-description"):
        assert state_class in rendered_sources
        assert f".{state_class}" in css_source

    assert ".ui-state.hidden" in css_source
    assert ".dark #paperQuestionStream > .space-y-4" in css_source
    assert ".dark .records-primary-action" in css_source
    assert ".dark .saved-paper-pdf-action" in css_source
    assert ".dark .import-workspace-heading h3" in css_source
    assert "min-height: min(520px, calc(100dvh - 180px))" in css_source
    assert 'role="status" aria-live="polite"' in index_source


def test_reduced_motion_dark_contrast_and_busy_feedback_are_explicit():
    css_source = _read(CSS_PATH)
    index_source = _read(INDEX_PATH)
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")

    assert "@media (prefers-reduced-motion: reduce)" in css_source
    assert "animation-duration: 0.01ms !important" in css_source
    assert "transition-duration: 0.01ms !important" in css_source
    assert ".animate-spin" in css_source

    assert ".dark input::placeholder" in css_source
    assert "color: #94A3B8 !important" in css_source
    assert "outline: 2px solid rgb(var(--brand-500-rgb))" in css_source
    assert 'button[aria-busy="true"]' in css_source
    assert 'button[aria-disabled="true"]' in css_source
    assert '#questionsList[aria-busy="true"]' in css_source
    assert '[data-modal-initial-focus="true"]:focus-visible' in css_source

    assert "new MutationObserver" in api_source
    assert "button.setAttribute('aria-busy', 'true')" in api_source
    assert "triggerButton.disabled = true" in editor_source
    assert "triggerButton.setAttribute('aria-busy', 'true')" in editor_source
    assert ".finally(() =>" in editor_source
    assert 'id="toast" role="status" aria-live="polite"' in index_source
    for loading_id in (
        "classifyLoading",
        "importLoadingState",
        "contentOcrLoadingIndicator",
        "ocrLoadingIndicator",
        "aiLoadingIndicator",
    ):
        element = _index_elements()[loading_id]
        assert element["role"] == "status"
        assert element["aria-live"] == "polite"


def test_sidebar_uses_server_pagination_and_latest_request_wins():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    load_start = editor_source.index("function loadQuestions(retryCount = 0)")
    load_end = editor_source.index("//       SIDEBAR PAGINATION SYSTEM HELPERS", load_start)
    load_source = editor_source[load_start:load_end]

    for marker in (
        "new AbortController()",
        "bankQuestionsLoadController.abort()",
        "const loadSequence = ++bankQuestionsLoadSequence",
        "loadSequence !== bankQuestionsLoadSequence",
        "requestController.signal",
        "err.name === 'AbortError'",
        "params.append('page', String(requestedPage))",
        "params.append('page_size', String(PAGE_LIMIT))",
        "params.append('sort', sortOrder === 'asc' ? 'asc' : 'desc')",
        "Array.isArray(payload.items)",
        "const questions = payload.items",
        "Number(payload.total)",
        "Number(payload.total_pages)",
    ):
        assert marker in load_source

    assert "questions.sort(" not in load_source
    assert "questions.slice(" not in load_source
    assert "selectQuestion(questions[0], { silent: true })" in load_source
    assert "function selectQuestion(item, options = {})" in import_source
    assert "if (!silent)" in import_source
    assert "showToast(`题目 #${fullItem.seq_num} 载入成功`)" in import_source


def test_paper_bank_stream_uses_server_pagination_and_latest_request_wins():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    fetch_start = paper_source.index("let bankQuestionsAbortController = null;")
    fetch_end = paper_source.index("// Render Full Paper Workspace", fetch_start)
    fetch_source = paper_source[fetch_start:fetch_end]
    filter_start = paper_source.index("let filterDebounceTimer = null;")
    filter_end = paper_source.index("function syncCanvasHeaderMeta", filter_start)
    filter_source = paper_source[filter_start:filter_end]

    for marker in (
        "const PAPER_STREAM_PAGE_SIZE = 15",
        "params.set('page', String(targetPage))",
        "params.set('page_size', String(PAPER_STREAM_PAGE_SIZE))",
        "params.set('sort', 'desc')",
        "const requestSeq = ++bankQuestionsRequestSeq",
        "bankQuestionsAbortController.abort()",
        "requestSeq !== bankQuestionsRequestSeq",
        "signal: controller.signal",
        "e.name === 'AbortError'",
        "payload && Array.isArray(payload.items)",
        "pagination.total =",
        "pagination.totalPages =",
    ):
        assert marker in paper_source

    assert "const allPagination = window.PaperStore.streamPagination.all" in filter_source
    assert "allPagination.page = 1" in filter_source
    assert "allPagination.total = null" in filter_source
    assert "cancelBankQuestionsFetch()" in filter_source
    assert "fetchBankQuestions(1)" in filter_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
const PAPER_STREAM_PAGE_SIZE = 15;
const controllers = [];
class TestAbortController {
  constructor() {
    this.signal = { aborted: false };
    controllers.push(this);
  }
  abort() { this.signal.aborted = true; }
}
global.AbortController = TestAbortController;
global.window = {
  PaperStore: {
    cart: [],
    filters: {
      compulsory: '', chapter: '', knowledge: '', question_type: '',
      difficulty: '', keyword: '', tab: 'all'
    },
    bankQuestions: [],
    streamPagination: {
      all: { page: 1, total: null, totalPages: 1, loading: false, error: '', retryPage: 1 },
      selected: { page: 1 }
    },
    cartQuestionLoad: { loading: false, error: '', missingIds: [] },
    questionsMap: {},
    answerCache: Object.create(null)
  }
};
function snapshotFigureLayoutsForBankFetch() { return {}; }
function preserveNewerFigureLayout() {}
function seedPaperAnswerCache(question) {
  if (question && typeof question.answer_markdown === 'string') {
    window.PaperStore.answerCache[question.id] = question.answer_markdown;
  }
}
function renderPart3QuestionStream() {}
window.renderPaperCanvas = function() {};
global.console = { ...console, error() {} };

function deferred() {
  let resolve;
  const promise = new Promise(res => { resolve = res; });
  return { promise, resolve };
}
function response(body) {
  return { ok: true, json: async () => body };
}
const fetchCalls = [];
global.fetch = (url, options = {}) => {
  const pending = deferred();
  fetchCalls.push({ url, options, pending });
  return pending.promise;
};
''' + fetch_source + r'''

(async () => {
  const first = fetchBankQuestions(2);
  const second = fetchBankQuestions(3);
  if (fetchCalls.length !== 2) {
    throw new Error(`expected two bank requests, got ${fetchCalls.length}`);
  }
  if (!controllers[0].signal.aborted || controllers[1].signal.aborted) {
    throw new Error('new bank request did not abort only the previous request');
  }
  for (const [index, expectedPage] of [[0, '2'], [1, '3']]) {
    const url = new URL(fetchCalls[index].url, 'http://mathbank.local');
    if (url.searchParams.get('page') !== expectedPage ||
        url.searchParams.get('page_size') !== '15' ||
        url.searchParams.get('sort') !== 'desc') {
      throw new Error(`bad paginated bank URL: ${url}`);
    }
    if (fetchCalls[index].options.signal !== controllers[index].signal) {
      throw new Error(`request ${index} did not receive its abort signal`);
    }
  }

  fetchCalls[1].pending.resolve(response({
    items: [{ id: 30, content: 'new page' }],
    total: 34,
    page: 3,
    page_size: 15,
    total_pages: 3
  }));
  if (await second !== true) throw new Error('latest response was not accepted');
  fetchCalls[0].pending.resolve(response({
    items: [{ id: 20, content: 'stale page' }],
    total: 34,
    page: 2,
    page_size: 15,
    total_pages: 3
  }));
  if (await first !== false) throw new Error('stale response was not rejected');

  const store = window.PaperStore;
  if (store.bankQuestions.length !== 1 || store.bankQuestions[0].id !== 30 ||
      store.streamPagination.all.page !== 3 ||
      store.streamPagination.all.total !== 34 ||
      store.streamPagination.all.totalPages !== 3) {
    throw new Error(`stale response changed current page: ${JSON.stringify(store)}`);
  }

  const detailCalls = [];
  store.cart = [{ id: 30 }, { id: 77 }, { id: 77 }];
  store.streamPagination.selected.page = 9;
  global.fetch = async (url) => {
    detailCalls.push(url);
    return response({
      status: 'success',
      data: [{ id: 77, content: 'restored cart question', answer_markdown: '' }]
    });
  };
  await ensureCartQuestionsLoaded();
  if (detailCalls.length !== 1 || detailCalls[0] !== '/api/paper/questions?ids=77') {
    throw new Error(`missing cart details were not deduplicated: ${JSON.stringify(detailCalls)}`);
  }
  if (!store.questionsMap[77] || store.streamPagination.selected.page !== 1) {
    throw new Error(`cart detail or selected-page clamp failed: ${JSON.stringify(store)}`);
  }
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_cart_full_revalidation_separates_missing_from_transient_failure_and_preserves_layout():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    state_start = paper_source.index("let bankQuestionsAbortController = null;")
    state_end = paper_source.index("// Fetch one server-paginated page", state_start)
    state_source = paper_source[state_start:state_end]
    layout_start = paper_source.index("const figureLayoutWrites = Object.create(null);")
    layout_end = paper_source.index("function getFigureLayoutWriteState", layout_start)
    layout_source = paper_source[layout_start:layout_end]

    for marker in (
        "const idsToLoad = revalidateAll ? cartIds : missingIds",
        "const protectedFigureLayouts = snapshotFigureLayoutsForBankFetch()",
        "preserveNewerFigureLayout(question, protectedFigureLayouts)",
        "confirmedMissingIds.add(qid)",
        "failedIds.add(qid)",
        "delete window.PaperStore.questionsMap[qid]",
        "await ensureCartQuestionsLoaded({ revalidateAll: true })",
    ):
        assert marker in paper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
const PAPER_STREAM_PAGE_SIZE = 15;
const calls = [];
let resolveFirst;
const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
global.window = {
  PaperStore: {
    cart: [{ id: 1, score: 5 }],
    questionsMap: {
      1: {
        id: 1,
        content: 'cached question',
        figure_align: 'right',
        figure_align_custom: true,
        figure_size: 'small'
      }
    },
    streamPagination: {
      all: { page: 1, total: 1, totalPages: 1, loading: false, error: '', retryPage: 1 },
      selected: { page: 1 }
    },
    cartQuestionLoad: {
      loading: false,
      error: '',
      missingIds: [],
      confirmedMissingIds: [],
      failedIds: []
    },
    expandedAnswerIds: new Set([1]),
    answerErrors: { 1: 'old error' },
    answerCache: { 1: 'old answer' }
  }
};
function seedPaperAnswerCache() {}
function renderPart3QuestionStream() {}
window.renderPaperCanvas = function() {};
function getQuestionFigSize(question) {
  return ['auto', 'small', 'medium', 'large'].includes(question && question.figure_size)
    ? question.figure_size
    : 'auto';
}
let fetchMode = 'layout-race';
global.fetch = async (url) => {
  calls.push(String(url));
  if (fetchMode === 'layout-race') return firstResponse;
  if (fetchMode === 'temporary-failure') {
    return { ok: false, status: 503, json: async () => ({}) };
  }
  return {
    ok: true,
    json: async () => ({ status: 'success', data: [] })
  };
};
global.console = { ...console, error() {} };
''' + state_source + '\n' + layout_source + r'''

(async () => {
  const layoutRace = ensureCartQuestionsLoaded({ revalidateAll: true });
  if (calls[0] !== '/api/paper/questions?ids=1') {
    throw new Error(`cached cart ID was not revalidated: ${JSON.stringify(calls)}`);
  }
  window.PaperStore.questionsMap[1].figure_align = 'bottom_right';
  window.PaperStore.questionsMap[1].figure_size = 'large';
  figureLayoutMutationRevision[1] = 1;
  resolveFirst({
    ok: true,
    json: async () => ({
      status: 'success',
      data: [{
        id: 1,
        content: 'server question',
        figure_align: 'right',
        figure_align_custom: true,
        figure_size: 'small'
      }]
    })
  });
  if (await layoutRace !== true ||
      window.PaperStore.questionsMap[1].figure_align !== 'bottom_right' ||
      window.PaperStore.questionsMap[1].figure_size !== 'large') {
    throw new Error('cart hydration overwrote a newer figure layout');
  }

  fetchMode = 'temporary-failure';
  if (await ensureCartQuestionsLoaded({ revalidateAll: true }) !== false) {
    throw new Error('temporary validation failure was reported as complete');
  }
  let state = window.PaperStore.cartQuestionLoad;
  if (!window.PaperStore.questionsMap[1] ||
      state.confirmedMissingIds.length !== 0 ||
      state.failedIds.length !== 1 || state.failedIds[0] !== 1 ||
      !state.error.includes('暂未通过服务端核验')) {
    throw new Error(`temporary failure was misclassified: ${JSON.stringify(state)}`);
  }

  fetchMode = 'confirmed-missing';
  if (await ensureCartQuestionsLoaded({ revalidateAll: true }) !== false) {
    throw new Error('confirmed missing question was reported as complete');
  }
  state = window.PaperStore.cartQuestionLoad;
  if (window.PaperStore.questionsMap[1] ||
      state.confirmedMissingIds.length !== 1 || state.confirmedMissingIds[0] !== 1 ||
      state.failedIds.length !== 0 ||
      !state.error.includes('已删除或不存在')) {
    throw new Error(`confirmed missing question was not isolated: ${JSON.stringify(state)}`);
  }
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_pagination_scrolls_only_the_question_list():
    source = _read(STATIC_JS_DIR / "paper.js")
    start = source.index("    function scrollPaperQuestionStreamToTop()")
    end = source.index("    window.retryPaperBankQuestions", start)
    script = r'''
const assert = require('assert');
const root = { scrollTop: 0 };
const preview = { scrollTop: 100 };
const stream = { scrollTop: 2000 };
const top = { scrollIntoView() { root.scrollTop = 367; } };
let mounted = true;
const document = { getElementById(id) {
  if (id === 'paperQuestionStream') return mounted ? stream : null;
  if (id === 'paperQuestionStreamTop') return top;
  if (id === 'a4PaperPreviewSheet') return preview;
  return null;
}};
''' + source[start:end] + r'''
scrollPaperQuestionStreamToTop();
assert.equal(stream.scrollTop, 0);
assert.equal(root.scrollTop, 0, 'pagination must not shift the whole page');
assert.equal(preview.scrollTop, 100, 'pagination must preserve the paper preview position');
mounted = false;
scrollPaperQuestionStreamToTop();
assert.equal(root.scrollTop, 0);
'''
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_paper_selected_stream_paginates_with_full_cart_indexes_and_total_counts():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    state_start = paper_source.index("function clampPaperStreamPage")
    state_end = paper_source.index("function cancelBankQuestionsFetch", state_start)
    state_source = paper_source[state_start:state_end]
    render_start = paper_source.index("function getPaperStreamPageNumbers")
    render_end = paper_source.index("// Render Part 4", render_start)
    render_source = paper_source[render_start:render_end]

    for marker in (
        ".slice(startIndex, startIndex + PAPER_STREAM_PAGE_SIZE)",
        "cartIndex: startIndex + pageIndex",
        "window.movePaperQuestion(${cartIndex}, 'up')",
        "window.movePaperQuestion(${cartIndex}, 'down')",
        "cartIndex === cart.length - 1",
        "全库试题 (${Number.isInteger(pagination.all.total) ? pagination.all.total : '—'})",
        "renderPaperStreamPagination(currentTab, currentPage, total, totalPages)",
        "共 ${safeTotal} 题 / ${safeTotalPages} 页",
    ):
        assert marker in paper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
const PAPER_STREAM_PAGE_SIZE = 15;
const container = {
  innerHTML: '',
  scrollTop: 41,
  attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; }
};
const cart = Array.from({ length: 23 }, (_, index) => ({ id: index + 1, score: 5 }));
const questionsMap = Object.fromEntries(cart.map(item => [item.id, {
  id: item.id,
  seq_num: item.id,
  content: `question-${item.id}`,
  question_type: 'single_choice',
  difficulty: 'easy',
  usage_count: 0,
  has_answer: false
}]));
global.window = {
  PaperStore: {
    cart,
    bankQuestions: Array.from({ length: 15 }, (_, index) => ({
      id: 100 + index,
      seq_num: 100 + index,
      content: `bank-${index}`,
      question_type: 'single_choice',
      difficulty: 'easy',
      usage_count: 0,
      has_answer: false
    })),
    questionsMap,
    filters: { tab: 'selected' },
    streamPagination: {
      all: { page: 2, total: 47, totalPages: 4, loading: false, error: '', retryPage: 2 },
      selected: { page: 99 }
    },
    cartQuestionLoad: { loading: false, error: '', missingIds: [] },
    expandedAnswerIds: new Set(),
    answerLoadingIds: new Set(),
    answerErrors: Object.create(null),
    answerCache: Object.create(null)
  },
  isInCart(qid) { return this.PaperStore.cart.some(item => item.id === qid); },
  changePaperStreamPage() {},
  togglePaperQuestionAnswer() {},
  collapseAllPaperAnswers() {},
  clearCart() {},
  updatePaperQuestionScore() {},
  movePaperQuestion() {},
  removeFromCart() {},
  addToCart() {}
};
global.document = {
  getElementById(id) { return id === 'paperQuestionStream' ? container : null; }
};
function ensureCartQuestionsLoaded() { return Promise.resolve(); }
function fetchBankQuestions() { return Promise.resolve(true); }
function getQuestionTypeCn() { return '单选题'; }
function getDifficultyBadge() { return ''; }
function seedPaperAnswerCache() {}
function hasCachedPaperAnswer() { return false; }
function escapeHtml(value) { return String(value); }
function formatQuestionContentHtml(content, qid) { return `<span>render-${qid}</span>`; }
function getQuestionFigAlign() { return 'right'; }
function getQuestionFigSize() { return 'auto'; }
function initializeAutoFigureSizing() {}
''' + state_source + '\n' + render_source + r'''

renderPart3QuestionStream();
let html = container.innerHTML;
if (window.PaperStore.streamPagination.selected.page !== 2) {
  throw new Error('selected page was not clamped to the last page');
}
for (const qid of [16, 21, 22, 23]) {
  if (!html.includes(`id="paper-q-render-${qid}"`)) {
    throw new Error(`selected last page omitted question ${qid}`);
  }
}
if (html.includes('id="paper-q-render-15"') ||
    !html.includes('全库试题 (47)') ||
    !html.includes('已选试题 (23)') ||
    !html.includes('共 23 题 / 2 页')) {
  throw new Error('selected page boundaries or total labels are wrong');
}
if (!html.includes("movePaperQuestion(15, 'up')") ||
    !html.includes("movePaperQuestion(22, 'down')") ||
    !/movePaperQuestion\(22, 'down'\)" disabled/.test(html)) {
  throw new Error('selected page did not retain full-cart reorder indexes');
}

window.PaperStore.filters.tab = 'all';
renderPart3QuestionStream();
html = container.innerHTML;
if (!html.includes('全库试题 (47)') || !html.includes('共 47 题 / 4 页') ||
    !html.includes('aria-current="page"')) {
  throw new Error('all-bank tab rendered page length instead of server totals');
}

window.PaperStore.cart = window.PaperStore.cart.slice(0, 5);
window.PaperStore.filters.tab = 'selected';
window.PaperStore.streamPagination.selected.page = 3;
renderPart3QuestionStream();
html = container.innerHTML;
if (window.PaperStore.streamPagination.selected.page !== 1 ||
    !html.includes('id="paper-q-render-1"') ||
    html.includes('id="paper-q-render-6"') ||
    !html.includes('共 5 题 / 1 页')) {
  throw new Error('selected page did not clamp after cart shrink');
}
'''
    result = subprocess.run(
        [node, "-"],
        cwd=PROJECT_ROOT,
        text=True,
        input=script,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_cart_actions_reject_changed_hydration_snapshot_and_are_single_flight():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    state_start = paper_source.index("let bankQuestionsAbortController = null;")
    state_end = paper_source.index("// Fetch one server-paginated page", state_start)
    helper_start = paper_source.index("    function normalizeSectionOrder(")
    helper_end = paper_source.index("    function getPaperTypeOrder(", helper_start)
    state_source = paper_source[helper_start:helper_end] + paper_source[state_start:state_end]
    save_start = paper_source.index("window.savePaperToDb = async function")
    save_end = paper_source.index("// ----------------- Saved Papers", save_start)
    save_source = paper_source[save_start:save_end]

    for marker in (
        "const paperActionInFlight = new Set()",
        "function getPaperCartSignature()",
        "function beginPaperAction(actionKey, actionLabel)",
        "function finishPaperAction(actionKey)",
        "isPaperCartSnapshotCurrent(expectedSignature, actionLabel)",
        "beginPaperAction(actionKey, `导出${targetName} PDF`)",
        "beginPaperAction(actionKey, '导出 Word 试卷')",
        "beginPaperAction(actionKey, '打包导出 LaTeX 资源')",
        "beginPaperAction(actionKey, '保存试卷')",
        "beginPaperAction(actionKey, '导出历史试卷 PDF')",
        "ensurePaperCartReady(`导出${targetName} PDF`, expectedCartSignature)",
        "ensurePaperCartReady('导出 Word 试卷', expectedCartSignature)",
        "ensurePaperCartReady('打包导出 LaTeX 资源', expectedCartSignature)",
        "ensurePaperCartReady('保存试卷', expectedCartSignature)",
        "const complete = await ensureCartQuestionsLoaded({ revalidateAll: true })",
    ):
        assert marker in paper_source
    assert paper_source.count("finishPaperAction(actionKey);") == 5

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
const PAPER_STREAM_PAGE_SIZE = 15;
const toasts = [];
global.window = {
  PaperStore: {
    cart: [{ id: 1, score: 5 }],
    meta: {
      title: 'race test', subtitle: '', paper_type: 'exam',
      show_notice: true, show_secret: true, solution_space_default: '7.0'
    },
    questionsMap: {},
    streamPagination: {
      all: { page: 1, total: 2, totalPages: 1, loading: false, error: '', retryPage: 1 },
      selected: { page: 1 }
    },
    cartQuestionLoad: { loading: false, error: '', missingIds: [] }
  },
  showToast(message, type) { toasts.push({ message, type }); },
  renderPaperCanvas() {}
};
function renderPart3QuestionStream() {}
function seedPaperAnswerCache() {}
function snapshotFigureLayoutsForBankFetch() { return {}; }
function preserveNewerFigureLayout() {}
function deferred() {
  let resolve;
  const promise = new Promise(res => { resolve = res; });
  return { promise, resolve };
}
const hydration = deferred();
let hydrateCalls = 0;
let saveCalls = 0;
global.fetch = (url) => {
  if (String(url).startsWith('/api/paper/questions?')) {
    hydrateCalls += 1;
    if (hydrateCalls === 1) return hydration.promise;
    return Promise.resolve({
      ok: true,
      json: async () => ({ status: 'success', data: [{ id: 2, content: 'current question' }] })
    });
  }
  if (url === '/api/paper/save') {
    saveCalls += 1;
    return Promise.resolve({ json: async () => ({ status: 'success' }) });
  }
  throw new Error(`unexpected fetch: ${url}`);
};
''' + state_source + '\n' + save_source + r'''

(async () => {
  const firstSave = window.savePaperToDb();
  const duplicateSave = window.savePaperToDb();
  if (hydrateCalls !== 1) throw new Error(`duplicate save started ${hydrateCalls} hydrations`);

  window.PaperStore.cart = [{ id: 2, score: 8 }];
  hydration.resolve({
    ok: true,
    json: async () => ({ status: 'success', data: [{ id: 1, content: 'old question' }] })
  });
  await Promise.all([firstSave, duplicateSave]);
  if (saveCalls !== 0) throw new Error('changed cart snapshot was saved after hydration');
  if (!toasts.some(item => item.message.includes('正在进行'))) {
    throw new Error('duplicate save was not reported as single-flight');
  }
  if (!toasts.some(item => item.message.includes('卷面题目已变化'))) {
    throw new Error('changed hydration snapshot was not reported');
  }

  window.PaperStore.questionsMap[2] = { id: 2, content: 'current question' };
  await window.savePaperToDb();
  if (hydrateCalls !== 2 || saveCalls !== 1) {
    throw new Error('action lock was not released or cached cart was not revalidated');
  }
  const originalFetch = global.fetch;
  const orderHydration = deferred();
  global.fetch = url => String(url).startsWith('/api/paper/questions?') ? orderHydration.promise : originalFetch(url);
  const pendingSave = window.savePaperToDb();
  window.PaperStore.meta.section_order = ['calculation', 'single_choice'];
  orderHydration.resolve({ok:true,json:async()=>({status:'success',data:[{id:2,content:'current question'}]})});
  await pendingSave;
  if (saveCalls !== 1) throw new Error('changed section order was saved after hydration');
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_paper_missing_question_recovery_removes_only_confirmed_missing_items():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    storage_start = paper_source.index("function saveCartToStorage()")
    storage_end = paper_source.index("function saveMetaToStorage()", storage_start)
    storage_source = paper_source[storage_start:storage_end]
    state_start = paper_source.index("let bankQuestionsAbortController = null;")
    state_end = paper_source.index("// Fetch one server-paginated page", state_start)
    state_source = paper_source[state_start:state_end]
    removal_start = paper_source.index("window.removeMissingPaperCartQuestions = function")
    removal_end = paper_source.index("window.changePaperStreamPage = async function", removal_start)
    removal_source = paper_source[removal_start:removal_end]

    assert "只移除确认失效题" in paper_source
    assert "onclick=\"window.removeMissingPaperCartQuestions()\"" in paper_source
    for marker in (
        "(loadState.confirmedMissingIds || [])",
        "saveCartToStorage()",
        "clampStoredPaperStreamPages()",
        "window.renderPaperCanvas()",
    ):
        assert marker in removal_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r'''
const PAPER_STREAM_PAGE_SIZE = 15;
const STORAGE_KEY_CART = 'mathbank_paper_cart';
const saved = new Map();
let streamRenders = 0;
let canvasRenders = 0;
const toasts = [];
const cart = Array.from({ length: 13 }, (_, index) => ({ id: index + 1, score: 5 }));
const questionsMap = Object.fromEntries(
  Array.from({ length: 10 }, (_, index) => [index + 1, { id: index + 1 }])
);
global.localStorage = {
  setItem(key, value) { saved.set(key, value); },
  removeItem(key) { saved.delete(key); }
};
global.confirm = () => true;
global.window = {
  PaperStore: {
    cart,
    questionsMap,
    streamPagination: {
      all: { page: 1, total: 13, totalPages: 2, loading: false, error: '', retryPage: 1 },
      selected: { page: 2 }
    },
    cartQuestionLoad: {
      loading: false,
      error: '有 3 道题已确认失效；有 1 道题暂未核验',
      missingIds: [11, 12, 13],
      confirmedMissingIds: [11, 12, 13],
      failedIds: [5]
    },
    expandedAnswerIds: new Set([11]),
    answerErrors: { 11: 'failed' },
    answerCache: { 11: 'stale' }
  },
  renderPaperCanvas() { canvasRenders += 1; },
  showToast(message, type) { toasts.push({ message, type }); }
};
function updateCartBadges() {}
function renderPart3QuestionStream() { streamRenders += 1; }
function seedPaperAnswerCache() {}
''' + storage_source + '\n' + state_source + '\n' + removal_source + r'''

window.removeMissingPaperCartQuestions();
const store = window.PaperStore;
if (store.cart.length !== 10 || !store.cart.some(item => item.id === 5)) {
  throw new Error(`valid cart question was removed: ${JSON.stringify(store.cart)}`);
}
if (store.cart.some(item => [11, 12, 13].includes(item.id))) {
  throw new Error(`unresolved questions remain: ${JSON.stringify(store.cart)}`);
}
if (store.streamPagination.selected.page !== 1 ||
    !store.cartQuestionLoad.error.includes('暂未通过服务端核验') ||
    store.cartQuestionLoad.missingIds.length !== 0 ||
    store.cartQuestionLoad.confirmedMissingIds.length !== 0 ||
    store.cartQuestionLoad.failedIds.length !== 1 ||
    store.cartQuestionLoad.failedIds[0] !== 5) {
  throw new Error(`recovery state was not reset: ${JSON.stringify(store)}`);
}
const persisted = JSON.parse(saved.get('mathbank_paper_cart'));
if (persisted.length !== 10 || persisted.some(item => [11, 12, 13].includes(item.id))) {
  throw new Error(`recovered cart was not persisted: ${JSON.stringify(persisted)}`);
}
if (streamRenders !== 1 || canvasRenders !== 1 ||
    !toasts.some(item => item.message.includes('其他已选题目已保留'))) {
  throw new Error('recovery did not refresh both views or report preserved questions');
}
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_word_export_prepares_pandoc_once_and_can_continue_in_compatibility_mode():
    index_source = _read(INDEX_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        'id="pandocInstallModal"',
        '安装 Word 可编辑公式组件',
        '安装并继续导出',
        '本次兼容导出',
        'id="pandocInstallProgressBar"',
    ):
        assert marker in index_source

    for marker in (
        "fetch('/api/runtime/pandoc/status')",
        "fetch('/api/runtime/pandoc/install', { method: 'POST' })",
        "pollPandocInstall(state.task_id)",
        "await ensurePandocForWordExport()",
        "await generateAndDownloadWord(payload)",
        "decision === 'compatibility'",
    ):
        assert marker in paper_source


def test_rejoined_pandoc_install_failure_allows_retry_compatibility_and_cancel():
    source = _read(STATIC_JS_DIR / "paper.js")
    helpers = source[source.index("    function resetPandocInstallModal()"):
                     source.index("    async function generateAndDownloadWord(payload)")]
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
const assert = require('node:assert/strict');
let elements, visible, posts;
global.window = { showToast() {} };
global.document = { getElementById(id) { return elements.get(id); } };
function setPandocModalVisible(value) { visible = value; }
global.fetch = async function(url, options) {
    if (url === '/api/runtime/pandoc/status') {
        return { ok: true, json: async () => ({pandoc: {status: 'verifying', task_id: 'existing', progress: 96}}) };
    }
    if (url.endsWith('/install/existing')) {
        return { ok: true, json: async () => ({pandoc: {status: 'error', error: 'Word validation failed', progress: 96}}) };
    }
    assert.equal(url, '/api/runtime/pandoc/install');
    assert.equal(options.method, 'POST');
    posts++;
    return { ok: true, json: async () => ({status: 'success', pandoc: {status: 'ready'}}) };
};
""" + helpers + r"""
(async () => {
    for (const [button, expected, expectedPosts] of [
        ['pandocInstallBtn', true, 1],
        ['pandocCompatibilityBtn', true, 0],
        ['pandocCancelBtn', false, 0],
    ]) {
        elements = new Map(); visible = false; posts = 0;
        for (const id of ['pandocInstallProgress', 'pandocInstallError', 'pandocInstallBtn',
            'pandocCompatibilityBtn', 'pandocCancelBtn', 'pandocInstallProgressText',
            'pandocInstallProgressValue', 'pandocInstallProgressBar']) {
            const classes = new Set(['hidden']);
            elements.set(id, {disabled: false, style: {}, classList: {
                add(v) { classes.add(v); }, remove(v) { classes.delete(v); }, contains(v) { return classes.has(v); }
            }});
        }
        const result = ensurePandocForWordExport();
        for (let i = 0; i < 20 && !elements.get(button).onclick; i++) {
            await new Promise(resolve => setImmediate(resolve));
        }
        assert.equal(posts, 0, 'joining must not start another installation');
        assert.equal(visible, true);
        assert.equal(elements.get('pandocInstallError').textContent, 'Word validation failed');
        assert.equal(elements.get('pandocInstallError').classList.contains('hidden'), false);
        assert.equal(elements.get(button).disabled, false);
        assert.equal(typeof elements.get(button).onclick, 'function', 'failed join must restore decisions');
        elements.get(button).onclick();
        assert.equal(await result, expected);
        assert.equal(posts, expectedPosts);
        assert.equal(visible, false);
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", script], cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_generated_tailwind_classes_use_configured_scales():
    combined_source = "\n".join((_read(INDEX_PATH), *(_read(path) for path in JS_FILES)))
    assert "text-2xs" not in combined_source
    for invalid_shadow in ("shadow-xs", "shadow-2xs", "shadow-3xs"):
        assert invalid_shadow not in combined_source

    configured_brand_steps = {50, 100, 200, 500, 600, 700, 900}
    used_brand_steps = {
        int(step)
        for step in re.findall(
            r"(?:text|bg|border|ring)-brand-(\d{2,3})(?=[/\s\"'`])",
            combined_source,
        )
    }
    assert used_brand_steps <= configured_brand_steps

    standard_steps = {50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950}
    used_standard_steps = {
        int(step)
        for step in re.findall(
            r"(?:text|bg|border|ring)-(?:slate|gray|red|rose|orange|amber|yellow|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink)-(\d{2,3})(?=[/\s\"'`])",
            combined_source,
        )
    }
    assert used_standard_steps <= standard_steps
