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


def test_static_dialogs_expose_modal_semantics_and_accessible_names():
    elements = _index_elements()
    labelled_dialogs = {
        "aiClassifyModal": "aiClassifyModalTitle",
        "settingsModal": "settingsModalTitle",
        "updateModal": "updateModalTitle",
        "statsModal": "statsModalTitle",
        "latexImportModal": "latexImportModalTitle",
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
