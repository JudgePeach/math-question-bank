from pathlib import Path
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cascade_loads_shared_safety_boundary_before_feature_modules():
    index_source = _read(STATIC_DIR / "index.html")
    expected_order = (
        "/static/js/api.js",
        "/static/js/editor.js",
        "/static/js/ocr.js",
        "/static/js/import.js",
        "/static/js/paper.js",
    )

    positions = [index_source.index(script_path) for script_path in expected_order]
    assert positions == sorted(positions)
    assert index_source.index("/static/lib/dompurify/purify.min.js") < positions[0]


def test_untrusted_html_uses_dompurify_and_local_image_allowlist():
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    assert "window.MathBankSafe = MathBankSafe" in api_source
    assert "window.DOMPurify.sanitize" in api_source
    assert "ALLOWED_TAGS" in api_source
    assert "ALLOWED_ATTR" in api_source
    assert "decodedPath.startsWith('/static/uploads/')" in api_source
    assert "url.origin !== window.location.origin" in api_source

    assert "sanitizeRichHtml(preprocessFormulaForKaTeX(text))" in editor_source
    assert "MathBankSafe.safeImageUrl(src)" in editor_source
    assert "MathBankSafe.safeImageUrl(m[1])" in paper_source
    assert "window.parseMarkdownWithMath(html)" in paper_source
    assert "MathBankSafe.sanitizeRichHtml(html)" in import_source
    assert "MathBankSafe.escapeAttribute(rawModel)" in api_source
    assert r"\.(?:png|jpe?g|gif|webp)$" in api_source
    assert "return url.pathname;" in api_source
    assert "return url.pathname + url.search;" not in api_source


def test_plain_text_helpers_preserve_math_comparison_signs_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    helper_start = api_source.index("const MathBankSafe = (() =>")
    helper_end = api_source.index("window.MathBankSafe = MathBankSafe;", helper_start)
    helper_source = api_source[helper_start:helper_end]
    script = f"""
const window = {{}};
{helper_source}
const raw = '$x<y$ and $y>z$ <not-a-tag>';
if (MathBankSafe.sanitizePlainText(raw) !== raw) {{
  throw new Error('plain-text sink changed mathematical comparison signs');
}}
const escaped = MathBankSafe.escapeText(raw);
if (escaped !== '$x&lt;y$ and $y&gt;z$ &lt;not-a-tag&gt;') {{
  throw new Error(`unexpected escaped text: ${{escaped}}`);
}}
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_category_refresh_only_reloads_editor_when_explicitly_requested():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    helper_start = api_source.index("function loadCategories(options = {})")
    helper_end = api_source.index("// Populate Metadata Select Option Lists", helper_start)
    helper_source = api_source[helper_start:helper_end]
    assert "backupEditorState" not in helper_source
    script = f"""
const window = {{}};
let systemMetadata = {{}};
let categoryTree = {{}};
let reloads = 0;
window.reloadCurrentQuestionSilently = () => {{ reloads += 1; }};
const EditorState = {{ questionId: 7 }};
function populateMetadataDropdowns() {{}}
function populateCategoryDropdowns() {{}}
function populateFilterDropdowns() {{}}
function showToast() {{}}
function fetch(url) {{
  const data = url.endsWith('/metadata')
    ? {{ question_types: [], difficulties: [], curriculum: {{}} }}
    : {{ high_school: {{}} }};
  return Promise.resolve({{ ok: true, json: () => Promise.resolve(data) }});
}}
{helper_source}
(async () => {{
  await loadCategories();
  if (reloads !== 0) throw new Error('ordinary metadata refresh reloaded the editor');
  await loadCategories({{ reloadCurrentQuestion: true }});
  if (reloads !== 1) throw new Error('explicit curriculum refresh did not reload the editor');
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_explicit_settings_refresh_preserves_dirty_editor_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    import_source = _read(STATIC_JS_DIR / "import.js")
    helper_start = import_source.index("window.reloadCurrentQuestionSilently = function()")
    helper_end = import_source.index("// Save/Update Question", helper_start)
    helper_source = import_source[helper_start:helper_end]
    api_source = _read(STATIC_JS_DIR / "api.js")
    assert "loadCategories({ reloadCurrentQuestion: true })" in api_source

    script = f"""
const window = {{}};
const EditorState = {{ questionId: 17, seqNum: 4, createdAt: '2026-01-01' }};
let dirty = true;
let selections = 0;
const toasts = [];
window.isEditorModified = () => dirty;
function selectQuestion(question) {{
  if (question.id !== 17) throw new Error('wrong question identity');
  selections += 1;
}}
function showToast(message, type) {{ toasts.push([message, type]); }}
{helper_source}

if (window.reloadCurrentQuestionSilently() !== false) {{
  throw new Error('dirty editor claimed to reload');
}}
if (selections !== 0) throw new Error('settings refresh overwrote dirty editor');
if (toasts.length !== 1 || toasts[0][1] !== 'info') {{
  throw new Error('dirty refresh did not explain that content was preserved');
}}

dirty = false;
if (window.reloadCurrentQuestionSilently() !== true || selections !== 1) {{
  throw new Error('clean editor no longer receives explicit curriculum refresh');
}}
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ai_solve_is_bound_to_editor_generation_and_controller_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_start = api_source.index("const EditorState = (() =>")
    editor_end = api_source.index("window.EditorState = EditorState;", editor_start)
    editor_source = api_source[editor_start:editor_end]

    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    helper_start = ocr_source.index("let aiSolveRequestSequence = 0")
    helper_end = ocr_source.index("// Global Esc key listener", helper_start)
    helper_source = ocr_source[helper_start:helper_end]
    solve_start = ocr_source.index("function triggerAISolve()")
    solve_end = ocr_source.index("// Import Tab results", solve_start)
    solve_source = ocr_source[solve_start:solve_end]

    assert "const editorSnapshot = EditorState.snapshot()" in solve_source
    assert "const requestSequence = ++aiSolveRequestSequence" in solve_source
    assert "controller === aiSolveAbortController" in helper_source
    assert "aiSolveCompletionTimer = setTimeout" in solve_source
    completion_start = solve_source.index("aiSolveCompletionTimer = setTimeout")
    assert "if (!requestIsCurrent()) return;" in solve_source[completion_start:]
    catch_start = solve_source.index(".catch(err =>")
    assert "if (!requestIsCurrent()) return;" in solve_source[catch_start:]

    script = f"""
const window = {{}};
const document = {{ getElementById() {{ return null; }} }};
let contentOcrAbortController = null;
let answerOcrAbortController = null;
let aiSolveAbortController = null;
function clearContentOcrPreview() {{}}
function clearOcrPreview() {{}}
function showToast() {{}}
{editor_source}
{helper_source}

EditorState.reset();
const oldSnapshot = EditorState.snapshot();
const oldController = new AbortController();
const oldSequence = ++aiSolveRequestSequence;
aiSolveAbortController = oldController;
if (!isAiSolveRequestCurrent(oldSequence, oldController, oldSnapshot)) {{
  throw new Error('new AI request was not current');
}}

EditorState.useQuestion({{ id: 22, seq_num: 3 }});
if (!oldController.signal.aborted) throw new Error('question switch did not abort AI request');
if (isAiSolveRequestCurrent(oldSequence, oldController, oldSnapshot)) {{
  throw new Error('old AI request survived editor generation change');
}}

const newSnapshot = EditorState.snapshot();
const newController = new AbortController();
const newSequence = ++aiSolveRequestSequence;
aiSolveAbortController = newController;
if (!isAiSolveRequestCurrent(newSequence, newController, newSnapshot)) {{
  throw new Error('replacement AI request was not current');
}}
if (isAiSolveRequestCurrent(oldSequence, oldController, oldSnapshot)) {{
  throw new Error('old callback could take ownership from replacement request');
}}
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_editor_transition_aborts_content_and_answer_ocr_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    state_start = api_source.index("const EditorState = (() => {")
    state_end = api_source.index("window.EditorState = EditorState;", state_start)
    state_source = api_source[state_start:state_end]

    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    cancel_start = ocr_source.index("let aiSolveRequestSequence = 0;")
    cancel_end = ocr_source.index("// Global Esc key listener", cancel_start)
    cancel_source = ocr_source[cancel_start:cancel_end]

    script = f"""
const window = {{}};
const document = {{ getElementById: () => null }};
let contentAborts = 0;
let answerAborts = 0;
let idleClears = 0;
let toasts = 0;
let contentOcrAbortController = {{ abort: () => {{ contentAborts += 1; }} }};
let answerOcrAbortController = {{ abort: () => {{ answerAborts += 1; }} }};
let aiSolveAbortController = null;
function clearContentOcrPreview() {{ idleClears += 1; }}
function clearOcrPreview() {{ idleClears += 1; }}
function showToast() {{ toasts += 1; }}
{state_source}
{cancel_source}

EditorState.beginTransition();
if (contentAborts !== 1 || answerAborts !== 1) {{
  throw new Error('editor transition left an OCR request attached to the old question');
}}
if (contentOcrAbortController !== null || answerOcrAbortController !== null) {{
  throw new Error('aborted OCR controller was not released');
}}
const feedbackAfterAbort = toasts;
EditorState.beginTransition();
if (idleClears !== 0 || toasts !== feedbackAfterAbort) {{
  throw new Error('an idle editor transition cleared OCR UI or emitted a false toast');
}}
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_editor_bound_uploads_and_tikz_requests_reject_old_sessions_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    content_upload_start = ocr_source.index("function uploadIllustration(file)")
    content_upload_end = ocr_source.index("function insertImageTag", content_upload_start)
    answer_upload_start = ocr_source.index("function uploadAnswerImage(file)")
    answer_upload_end = ocr_source.index("function insertAnswerImageTag", answer_upload_start)
    upload_source = (
        ocr_source[content_upload_start:content_upload_end]
        + ocr_source[answer_upload_start:answer_upload_end]
    )

    import_source = _read(STATIC_JS_DIR / "import.js")
    request_helper_start = import_source.index("function beginEditorBoundRequest(")
    request_helper_end = import_source.index(
        "window.extractTikzCodeFromTextarea", request_helper_start
    )
    request_helper_source = import_source[request_helper_start:request_helper_end]
    assert import_source.count("const finishRequest = beginEditorBoundRequest(") == 6
    assert import_source.count("if (!finishRequest()) return;") == 12

    script = f"""
let generation = 1;
const EditorState = {{
  snapshot: () => ({{ generation }}),
  isCurrent: session => session.generation === generation
}};
class FormData {{ append() {{}} }}
let fetchResolvers = [];
function fetch() {{
  return new Promise(resolve => {{ fetchResolvers.push(resolve); }});
}}
let insertedContent = 0;
let insertedAnswer = 0;
let uploadedImages = [];
function insertImageTag() {{ insertedContent += 1; }}
function insertAnswerImageTag() {{ insertedAnswer += 1; }}
function renderIllustrationBadges() {{}}
function showToast() {{}}
{upload_source}
{request_helper_source}

(async () => {{
  uploadIllustration({{ type: 'image/png' }});
  uploadAnswerImage({{ type: 'image/png' }});
  generation = 2;
  for (const resolve of fetchResolvers) {{
    resolve({{ json: () => Promise.resolve({{ status: 'success', file_path: '/static/uploads/old.png' }}) }});
  }}
  await new Promise(resolve => setTimeout(resolve, 0));
  if (insertedContent || insertedAnswer || uploadedImages.length) {{
    throw new Error('old upload response wrote into the replacement editor session');
  }}

  const button = {{ disabled: true, innerHTML: 'busy' }};
  generation = 3;
  const finishOldSession = beginEditorBoundRequest(button, 'idle');
  generation = 4;
  if (finishOldSession() !== false || button.disabled !== false) {{
    throw new Error('stale TikZ request did not release its own button safely');
  }}

  button.disabled = true;
  const finishSuperseded = beginEditorBoundRequest(button, 'first');
  const finishNewest = beginEditorBoundRequest(button, 'second');
  if (finishSuperseded() !== false || button.disabled !== true) {{
    throw new Error('old TikZ callback modified controls owned by a newer request');
  }}
  if (finishNewest() !== true || button.disabled !== false || button.innerHTML !== 'second') {{
    throw new Error('current TikZ callback could not finish its request');
  }}
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_delayed_tikz_auto_compile_rejects_old_editor_session_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    import_source = _read(STATIC_JS_DIR / "import.js")
    helper_start = import_source.index("window.extractTikzCodeFromTextarea = function(")
    helper_end = import_source.index("// 题干清理与编译", helper_start)
    helper_source = import_source[helper_start:helper_end]

    script = f"""
let generation = 1;
const EditorState = {{
  snapshot: () => ({{ generation }}),
  isCurrent: session => session.generation === generation
}};
let delayedCallback = null;
function setTimeout(callback) {{ delayedCallback = callback; }}
function showToast() {{}}
class Event {{}}
let compileCalls = 0;
let focusCalls = 0;
const textarea = {{
  value: '\\\\begin{{tikzpicture}}\\\\draw (0,0)--(1,1);\\\\end{{tikzpicture}}',
  dispatchEvent() {{}}
}};
const tikzInput = {{
  value: '',
  dispatchEvent() {{}},
  scrollIntoView() {{}},
  focus() {{ focusCalls += 1; }}
}};
const container = {{ classList: {{ remove() {{}} }} }};
const document = {{
  getElementById(id) {{
    if (id === 'editContent') return textarea;
    if (id === 'editContentTikzCode') return tikzInput;
    if (id === 'contentTikzContainer') return container;
    return null;
  }}
}};
const window = {{
  renderContentTikzToImage() {{ compileCalls += 1; }},
  renderAnswerTikzToImage() {{ compileCalls += 1; }}
}};
{helper_source}

window.extractTikzCodeFromTextarea('editContent');
if (!delayedCallback) throw new Error('TikZ auto compile was not scheduled');
generation = 2;
delayedCallback();
if (compileCalls !== 0 || focusCalls !== 0) {{
  throw new Error('old TikZ timer acted on the replacement editor session');
}}

textarea.value = '\\\\begin{{tikzpicture}}\\\\draw (0,0)--(2,2);\\\\end{{tikzpicture}}';
window.extractTikzCodeFromTextarea('editContent');
delayedCallback();
if (compileCalls !== 1 || focusCalls !== 1) {{
  throw new Error('current TikZ timer no longer compiles and focuses');
}}
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_import_answer_generation_rejects_replaced_question_set_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    import_source = _read(STATIC_JS_DIR / "import.js")
    helpers_start = import_source.index("function replaceParsedQuestions(nextQuestions)")
    helpers_end = import_source.index("function blockImportResetWhileSaving()", helpers_start)
    helpers_source = import_source[helpers_start:helpers_end]
    answer_start = import_source.index("async function generateSingleAnswer(index)")
    answer_end = import_source.index("window.generateSingleAnswer = generateSingleAnswer", answer_start)
    answer_source = import_source[answer_start:answer_end]

    script = f"""
const window = {{}};
let parsedQuestionsData = [];
let parsedQuestionsGeneration = 0;
const pendingFetches = [];
function fetch() {{
  return new Promise(resolve => pendingFetches.push(resolve));
}}
class FormData {{ append() {{}} }}
const localStorage = {{ getItem() {{ return ''; }} }};
const logs = [];
const toasts = [];
function appendImportLog(message) {{ logs.push(message); }}
function showToast(message) {{ toasts.push(message); }}
function renderParsedCardPreview() {{}}
function makeCard() {{
  const button = {{ disabled: false, innerHTML: '' }};
  const answer = {{ value: '' }};
  const preview = {{ innerHTML: '' }};
  return {{
    button,
    answer,
    querySelector(selector) {{
      if (selector === '.card-solve-btn') return button;
      if (selector === '.card-answer-textarea') return answer;
      if (selector === '.card-answer-preview') return preview;
      return null;
    }}
  }};
}}
let activeCard = null;
const document = {{ getElementById() {{ return activeCard; }} }};
{helpers_source}
{answer_source}

(async () => {{
  const oldQuestion = {{ content: 'old', answer_markdown: '' }};
  const newQuestion = {{ content: 'new', answer_markdown: '' }};
  const oldCard = makeCard();
  const newCard = makeCard();

  replaceParsedQuestions([oldQuestion]);
  activeCard = oldCard;
  const staleSingle = generateSingleAnswer(0);
  replaceParsedQuestions([newQuestion]);
  activeCard = newCard;
  pendingFetches.shift()({{
    ok: true,
    json: () => Promise.resolve({{ status: 'success', solution: 'stale answer' }})
  }});
  await staleSingle;
  if (oldQuestion.answer_markdown || newQuestion.answer_markdown || toasts.length) {{
    throw new Error('stale single-answer callback changed a replaced import session');
  }}

  const currentSingle = generateSingleAnswer(0);
  pendingFetches.shift()({{
    ok: true,
    json: () => Promise.resolve({{ status: 'success', solution: 'current answer' }})
  }});
  await currentSingle;
  if (newQuestion.answer_markdown !== 'current answer' || newCard.button.disabled) {{
    throw new Error('current single-answer request did not finish normally');
  }}

  const oldBatchQuestion = {{ content: 'old batch', answer_markdown: '' }};
  const replacement = {{ content: 'replacement', answer_markdown: '' }};
  const batchGeneration = replaceParsedQuestions([oldBatchQuestion]);
  activeCard = null;
  const staleBatch = processAsyncAnswerGeneration(parsedQuestionsData, batchGeneration);
  replaceParsedQuestions([replacement]);
  pendingFetches.shift()({{
    ok: true,
    json: () => Promise.resolve({{ status: 'success', solution: 'stale batch answer' }})
  }});
  await staleBatch;
  if (oldBatchQuestion.answer_markdown || replacement.answer_markdown) {{
    throw new Error('stale answer queue changed a replaced import session');
  }}
  if (logs.some(message => message.includes('全部完成'))) {{
    throw new Error('stale answer queue announced completion in the new session');
  }}
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_document_poll_generation_rejects_old_terminal_callbacks_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    import_source = _read(STATIC_JS_DIR / "import.js")
    helper_start = import_source.index("let documentImportTaskGeneration = 0")
    helper_end = import_source.index("function cancelCurrentImportTask()", helper_start)
    helper_source = import_source[helper_start:helper_end]
    poll_start = import_source.index("function pollPdfTaskStatus(")
    poll_end = import_source.index("function renderImagesList()", poll_start)
    poll_source = import_source[poll_start:poll_end]
    latex_start = import_source.index("// Normal LaTeX branch")
    latex_end = import_source.index("let documentImportTaskGeneration = 0", latex_start)
    latex_source = import_source[latex_start:latex_end]

    assert "if (!isCurrentDocumentPoll(identity)) return;" in poll_source
    assert "if (!finishDocumentPoll(identity)) return;" in poll_source
    assert "pollPdfTaskStatus(taskId, importTaskGeneration)" in import_source
    assert import_source.count("if (!isCurrentDocumentImportTask(importTaskGeneration)) return;") >= 4
    assert latex_source.count(
        "if (!isCurrentDocumentImportTask(importTaskGeneration)) return null;"
    ) == 2
    assert "if (!isCurrentDocumentImportTask(importTaskGeneration) || !r) return null;" in latex_source
    assert "if (!isCurrentDocumentImportTask(importTaskGeneration) || !data) return;" in latex_source
    assert "processAsyncAnswerGeneration(parsedQuestionsData, parsedQuestionsGeneration)" in latex_source

    script = f"""
const window = {{ currentPdfTaskId: null }};
{helper_source}

const oldGeneration = beginDocumentImportTask();
const oldIdentity = {{ generation: oldGeneration, taskId: 'old', intervalId: 1 }};
activeDocumentPoll = oldIdentity;
window.currentPdfTaskId = oldIdentity.taskId;
if (!isCurrentDocumentPoll(oldIdentity)) throw new Error('old poll never became current');

const newGeneration = beginDocumentImportTask();
const newIdentity = {{ generation: newGeneration, taskId: 'new', intervalId: 2 }};
activeDocumentPoll = newIdentity;
window.currentPdfTaskId = newIdentity.taskId;
if (finishDocumentPoll(oldIdentity)) throw new Error('old terminal callback finished the new poll');
if (activeDocumentPoll !== newIdentity) {{
  throw new Error('old callback cleared replacement poll state');
}}
if (!finishDocumentPoll(newIdentity)) throw new Error('current terminal callback was rejected');
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ai_import_values_cannot_break_out_of_attributes_or_textareas():
    import_source = _read(STATIC_JS_DIR / "import.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")

    assert 'value="${q.source || \'\'}"' not in import_source
    assert ">${q.content || ''}</textarea>" not in import_source
    assert ">${q.answer_markdown || ''}</textarea>" not in import_source
    assert "card.querySelector('.card-source').value" in import_source
    assert "card.querySelector('.card-content-textarea').value" in import_source
    assert "card.querySelector('.card-answer-textarea').value" in import_source
    assert "window.syncAnswerImagesFromMarkdown = function()" in import_source
    assert "window.renderAnswerImageBadges = function()" in import_source

    assert "onclick=\"window.open('${src}'" not in editor_source
    assert "onclick=\"window.open('${p}'" not in import_source
    assert 'data-safe-image-open="true"' in editor_source
    assert 'data-safe-image-open="true"' in import_source


def test_fetch_token_is_limited_to_same_origin_api_writes_and_supports_request():
    api_source = _read(STATIC_JS_DIR / "api.js")

    assert "input instanceof Request" in api_source
    assert "input.method" in api_source
    assert "input.headers" in api_source
    assert "requestUrl.origin === window.location.origin" in api_source
    assert "requestUrl.pathname.startsWith('/api/')" in api_source
    assert "isSameOriginApi && isWriteRequest" in api_source
    assert "new Headers(requestInit.headers || undefined)" in api_source


def test_question_selection_and_save_are_transactional():
    import_source = _read(STATIC_JS_DIR / "import.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    select_start = import_source.index("function selectQuestion(item)")
    select_end = import_source.index("window.reloadCurrentQuestionSilently", select_start)
    select_source = import_source[select_start:select_end]
    save_start = import_source.index("function saveQuestion(skipCheck = false)")
    save_end = import_source.index("// AI classification modal handlers", save_start)
    save_source = import_source[save_start:save_end]

    assert "EditorState.useQuestion(item)" not in select_source
    assert select_source.index(".then(fullItem =>") < select_source.index("EditorState.useQuestion(fullItem)")
    assert "questionDetailLoading = true" in select_source
    assert "questionDetailLoading = false" in select_source
    assert "loadSequence !== questionDetailLoadSequence" in select_source
    assert "Number(fullItem.id) !== requestedQuestionId" in select_source
    assert "invalidatePendingQuestionDetailLoad" in editor_source

    assert "let saveQuestionInFlight = null" in import_source
    assert "if (saveQuestionInFlight)" in import_source
    assert "return saveQuestionInFlight" in import_source
    assert "saveQuestionInFlight = saveOperation.finally" in import_source
    assert "button.disabled = isBusy" in import_source
    assert "const requestBackupSnapshot = Object.freeze" in save_source
    assert "EditorState.isCurrent(editorSession)" in save_source
    assert "window.editorMatchesBackupSnapshot(requestBackupSnapshot)" in save_source
    assert "backupEditorState(data.question.id, null, requestBackupSnapshot)" in save_source
    assert "EditorState.useQuestion(data.question)" in save_source
    assert "selectQuestion(data.question" not in save_source
    assert "window.isQuestionSaveInFlight" in import_source
    assert "window.isQuestionSaveInFlight && window.isQuestionSaveInFlight()" in editor_source

    for name in ("api.js", "editor.js", "ocr.js", "import.js", "paper.js"):
        assert "new Promise(async" not in _read(STATIC_JS_DIR / name)


def test_request_snapshot_keeps_post_submit_edits_dirty_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function backupEditorState")
    helper_end = editor_source.index("// Custom Premium Confirmation Modal", helper_start)
    helper_source = editor_source[helper_start:helper_end]
    script = f"""
const window = {{}};
let originalQuestionState = null;
let uploadedImages = ['/static/uploads/saved.png'];
const values = {{
  editContent: 'request payload',
  editAnswerMarkdown: 'saved answer',
  editReview: 'saved review',
  editQType: 'single_choice',
  editDifficulty: 'easy_error',
  editSource: 'saved source',
  editCompulsory: 'high_school',
  editChapter: 'chapter 1',
  editKnowledge: 'section 1',
  editTags: 'saved tag'
}};
const elements = Object.fromEntries(
  Object.entries(values).map(([id, value]) => [id, {{ value }}])
);
const document = {{
  getElementById(id) {{ return elements[id] || null; }}
}};
{helper_source}
const requestSnapshot = Object.freeze({{
  content: 'request payload',
  answer_markdown: 'saved answer',
  review: 'saved review',
  question_type: 'single_choice',
  difficulty: 'easy_error',
  source: 'saved source',
  category_compulsory: 'high_school',
  category_chapter: 'chapter 1',
  category_knowledge: 'section 1',
  image_paths: JSON.stringify(['/static/uploads/saved.png']),
  tags: 'saved tag'
}});

backupEditorState(17, null, requestSnapshot);
if (isEditorModified()) throw new Error('saved request snapshot should be clean');
elements.editContent.value = 'request payload plus later input';
if (!isEditorModified()) throw new Error('post-submit input was incorrectly marked saved');
elements.editContent.value = 'request payload';
if (isEditorModified()) throw new Error('restoring request snapshot should clear dirty state');
"""

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_clear_and_session_reset_entries_block_while_save_is_in_flight():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    import_source = _read(STATIC_JS_DIR / "import.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    guard_start = import_source.index("function blockEditorSessionChangeWhileSaving()")
    guard_end = import_source.index("window.isQuestionSaveInFlight", guard_start)
    guard_source = import_source[guard_start:guard_end]
    clear_start = import_source.index("function clearEditor()")
    clear_end = import_source.index("function startNewQuestion()", clear_start)
    clear_source = import_source[clear_start:clear_end]

    guard_call = (
        "window.blockEditorSessionChangeWhileSaving && "
        "window.blockEditorSessionChangeWhileSaving()"
    )
    assert clear_source.index(guard_call) < clear_source.index("confirm(")
    assert clear_source.index(guard_call) < clear_source.index("EditorState.reset()")

    guarded_import_functions = (
        ("function startNewQuestionWithoutPrompt()", "function switchSidebarTab", "EditorState.reset()"),
        ("function startNewQuestion()", "function refreshRelatedDropdown", "EditorState.reset()"),
    )
    for start_marker, end_marker, mutation_marker in guarded_import_functions:
        start = import_source.index(start_marker)
        end = import_source.index(end_marker, start)
        function_source = import_source[start:end]
        assert guard_call in function_source
        assert function_source.index(guard_call) < function_source.index(mutation_marker)

    guarded_editor_functions = (
        ("function saveCurrentToDrafts()", "function selectDraft", "EditorState.setDraftId"),
        ("function selectDraft(draft)", "function deleteDraft", "EditorState.useDraft"),
        ("function deleteDraft(id)", "function openStatsModal", "EditorState.clearDraft"),
    )
    for start_marker, end_marker, mutation_marker in guarded_editor_functions:
        start = editor_source.index(start_marker)
        end = editor_source.index(end_marker, start)
        function_source = editor_source[start:end]
        assert guard_call in function_source
        assert function_source.index(guard_call) < function_source.index(mutation_marker)

    script = f"""
let saveQuestionInFlight = Promise.resolve(true);
const window = {{}};
const messages = [];
let confirmCalls = 0;
function showToast(message, type) {{ messages.push([message, type]); }}
function confirm() {{ confirmCalls += 1; return false; }}
{guard_source}
{clear_source}

clearEditor();
if (confirmCalls !== 0) throw new Error('clear confirmation ran during an active save');
if (messages.length !== 1 || messages[0][1] !== 'info') {{
  throw new Error('blocked clear did not provide visible feedback');
}}

saveQuestionInFlight = null;
clearEditor();
if (confirmCalls !== 1) throw new Error('clear stayed blocked after save settled');
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_parsed_question_imports_use_question_identity_and_generation():
    import_source = _read(STATIC_JS_DIR / "import.js")
    save_start = import_source.index("function saveParsedQuestion(index)")
    save_end = import_source.index("function confirmClearAllParsed()", save_start)
    save_source = import_source[save_start:save_end]

    assert "const parsedQuestionSaveInFlight = new Map()" in import_source
    assert "let parsedQuestionsGeneration = 0" in import_source
    assert "parsedQuestionSaveInFlight.get(q)" in save_source
    assert "if (existingSave) return existingSave" in save_source
    assert "const saveGeneration = parsedQuestionsGeneration" in save_source
    assert "isParsedQuestionSaveContextCurrent(saveGeneration, index, q)" in save_source
    assert "parsedQuestionSaveInFlight.set(q, trackedSave)" in save_source
    assert "parsedQuestionSaveInFlight.delete(q)" in save_source


def test_parsed_save_generation_prevents_index_reuse_and_stale_callback_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    import_source = _read(STATIC_JS_DIR / "import.js")
    helpers_start = import_source.index("function replaceParsedQuestions(nextQuestions)")
    helpers_end = import_source.index("function openImportModal()", helpers_start)
    helpers_source = import_source[helpers_start:helpers_end]
    save_start = import_source.index("function saveParsedQuestion(index)")
    save_end = import_source.index("function confirmClearAllParsed()", save_start)
    save_source = import_source[save_start:save_end]

    for start_marker, end_marker, mutation_marker in (
        ("function closeImportModal()", "// PDF & Crop Global States", "performOrphanedTempCropsCleanup"),
        ("function clearAllImportInputs()", "function resetImportState", "batchSelectedImages = []"),
        ("function resetImportState", "function appendSafeImageBadge", "replaceParsedQuestions([])"),
        ("function confirmClearAllParsed()", "function clearAllParsedSources", "confirm("),
    ):
        start = import_source.index(start_marker)
        end = import_source.index(end_marker, start)
        function_source = import_source[start:end]
        assert "blockImportResetWhileSaving()" in function_source
        assert function_source.index("blockImportResetWhileSaving()") < function_source.index(mutation_marker)

    script = f"""
const window = {{ MathBankSafe: {{ safeImageUrl(value) {{ return value; }} }} }};
let parsedQuestionsData = [];
let parsedQuestionsGeneration = 0;
const parsedQuestionSaveInFlight = new Map();
const toasts = [];
function showToast(message, type) {{ toasts.push([message, type]); }}
function loadCategories() {{}}
function loadQuestions() {{}}
function updateSelectedCount() {{}}
class FormData {{ append() {{}} }}

function createCard(content) {{
  const fields = {{
    '.card-content-textarea': {{ value: content }},
    '.card-answer-textarea': {{ value: '' }},
    '.card-qtype': {{ value: 'single_choice' }},
    '.card-difficulty': {{ value: 'easy_error' }},
    '.card-source': {{ value: '' }},
    '.card-compulsory': {{ value: 'high_school' }},
    '.card-chapter': {{ value: 'chapter_1' }},
    '.card-knowledge': {{ value: 'section_1' }},
    '.card-save-btn': {{ disabled: false, innerHTML: '', className: '' }},
    '.card-status-badge': {{ textContent: '', className: '' }},
    '.card-select-checkbox': {{
      disabled: false,
      checked: true,
      classList: {{ add() {{}} }}
    }}
  }};
  return {{ fields, querySelector(selector) {{ return fields[selector] || null; }} }};
}}

const pendingFetches = [];
function fetch() {{
  return new Promise(resolve => pendingFetches.push(resolve));
}}
let activeCard = null;
const document = {{ getElementById() {{ return activeCard; }} }};
{helpers_source}
{save_source}

(async () => {{
  const oldQuestion = {{ content: 'old paper question', image_paths: [] }};
  const newQuestion = {{ content: 'new paper question', image_paths: [] }};
  const oldCard = createCard(oldQuestion.content);
  const newCard = createCard(newQuestion.content);

  replaceParsedQuestions([oldQuestion]);
  activeCard = oldCard;
  const oldSave = saveParsedQuestion(0);
  const duplicateOldSave = saveParsedQuestion(0);
  if (oldSave !== duplicateOldSave) throw new Error('same question did not reuse its in-flight promise');

  replaceParsedQuestions([newQuestion]);
  activeCard = newCard;
  const newSave = saveParsedQuestion(0);
  if (newSave === oldSave) throw new Error('new paper index 0 reused the old paper promise');

  pendingFetches[0]({{ json() {{ return Promise.resolve({{ status: 'success' }}); }} }});
  await oldSave;
  if (oldQuestion.saved === true) throw new Error('stale callback mutated the old import session');
  if (oldCard.fields['.card-status-badge'].textContent) {{
    throw new Error('stale callback updated a detached card');
  }}

  pendingFetches[1]({{ json() {{ return Promise.resolve({{ status: 'success' }}); }} }});
  await newSave;
  if (newQuestion.saved !== true) throw new Error('current generation did not receive save success');
  if (newCard.fields['.card-status-badge'].textContent !== '已导入') {{
    throw new Error('current card did not receive save feedback');
  }}
  if (parsedQuestionSaveInFlight.size !== 0) throw new Error('in-flight identities leaked');
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
