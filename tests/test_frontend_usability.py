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
    helper_end = editor_source.index("function preprocessFormulaForKaTeX(text)", helper_start)
    helper_source = editor_source[helper_start:helper_end]

    assert r"/\\paren\b/g" in helper_source
    assert 'class="exam-zh-paren-preview"' in helper_source
    assert 'aria-label="选择题作答括号"' in helper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r"""
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
        "latexImportModal": "latexImportModalTitle",
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
        "#latexImportModalContent",
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
    assert "sidebar-pagination-controls" in _read(STATIC_JS_DIR / "editor.js")


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
