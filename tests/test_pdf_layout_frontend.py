"""Execute PDF import requests, polling, source review, and crop page mapping."""

from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_import_source_review import review_script  # reuse the existing DOM/event harness
from test_preview_browser import browser, complete_import_fixture_categories  # isolated app/session


ROOT = Path(__file__).resolve().parents[1]


def _open_source_report(browser):
    if browser.evaluate("!!document.getElementById('parsedSourceIntegrityReport') && !document.getElementById('parsedSourceIntegrityReport').open"):
        browser.command('click', '#parsedSourceIntegrityReport > summary')


@pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1' or not os.environ.get('MATHBANK_TEST_PDF_BEIJING_TASK'),
                    reason='opt-in isolated browser replay of the Beijing task')
def test_captured_beijing_optional_review_ui_selects_28_without_approval_or_network(browser, tmp_path):
    task_path = Path(os.environ['MATHBANK_TEST_PDF_BEIJING_TASK'])
    task = json.loads(task_path.read_text())
    assert len(task['data']) == 28
    assert sum(q.get('source_review', {}).get('required') is True for q in task['data']) == 21
    backups = {}
    backup_index = task_path.parent / 'figure-assets.json'
    if backup_index.exists():
        for item in json.loads(backup_index.read_text()):
            candidate = Path(item['local']).resolve()
            if candidate.is_relative_to(task_path.parent.resolve()):
                backups[item['path']] = candidate
    for asset in task.get('temp_assets', []):
        if not isinstance(asset, str) or not re.fullmatch(r'/static/uploads/tmp/[A-Za-z0-9_-]+\.png', asset):
            continue
        source = ROOT / asset.lstrip('/')
        if not source.exists():
            source = backups.get(asset)
        if source is not None and source.exists():
            destination = browser.root / asset.lstrip('/')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    browser.evaluate('window.__beijingTask=' + json.dumps(task, ensure_ascii=False) + '; true')
    browser.evaluate(r"""
    (()=>{
        selectWorkspace('import','导入中心');
        stopCurrentDocumentPoll();
        window.__beijingOriginalFetch=window.fetch;window.__beijingBlocked=[];
        window.fetch=(input,options={})=>{
            const url=String(input?.url||input),method=String(options.method||'GET').toUpperCase();
            if(url.includes('/api/ai/')||url.includes('/api/upload/')||
               (method!=='GET'&&/\/api\/questions(?:\/|$)/.test(url))){
                window.__beijingBlocked.push(url);return Promise.reject(Error('No models or writes in cached UI replay'));
            }
            return window.__beijingOriginalFetch(input,options);
        };
        replaceParsedQuestions(structuredClone(window.__beijingTask.data));
        renderParsedQuestionsList(parsedQuestionsData);
        renderSourceIntegrityReport(window.__beijingTask.diagnostics);
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        return true;
    })()
    """)
    try:
        browser.settle()
        assert browser.evaluate('parsedQuestionsData.filter(q=>q.source_review?.required===true).length') == 21
        assert browser.evaluate("document.querySelectorAll('.card-source-review-confirm').length") == 0
        assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').open")
        assert browser.evaluate("document.querySelector('#parsedSourceIntegrityReport > summary').textContent.includes('3 张配图未自动归位')")
        assert browser.evaluate("[...document.querySelectorAll('.card-source-review-panel')].every(panel=>!panel.open)")
        assert browser.evaluate('toggleSelectAllParsed(false);toggleSelectAllParsed(true);getCheckedUnsavedIndices()') == list(range(28))
        assert browser.evaluate('parsedQuestionsData.every((q,i)=>validateParsedQuestionBeforeImport(i,{notify:false,focus:false}))')
        assert browser.evaluate('parsedQuestionsData.filter((q,i)=>parsedQuestionNeedsSourceReview(i,q)).length') == 21
        assert browser.evaluate('JSON.stringify(parsedQuestionsData.map(q=>q.source_review||null))===JSON.stringify(window.__beijingTask.data.map(q=>q.source_review||null))')
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.command('scrollintoview', '#parsedSourceIntegrityReport')
            browser.settle()
            assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').open")
            browser.command('screenshot', str(tmp_path / f'beijing-28-optional-{width}.png'))
        _open_source_report(browser)
        assert browser.evaluate("document.getElementById('parsedSourceIntegrityReport').textContent.includes('无法唯一确定')")
        assert browser.evaluate('window.__beijingBlocked') == []
    finally:
        browser.evaluate('window.fetch=window.__beijingOriginalFetch; true')
        browser.command('set', 'viewport', '1600', '1100')
    print(f'Beijing 28-question optional review screenshots: {tmp_path}')


def test_pdf_strategy_markup_defaults_to_layout_aware():
    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.radios = []
            self.verification = None
            self.docx_verification = None
            self.docx_container = None
            self.answer_generation = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "input" and attrs.get("name") == "pdfStrategy":
                self.radios.append(attrs)
            if tag == "input" and attrs.get("id") == "pdfVerifySuspicions":
                self.verification = attrs
            if tag == "input" and attrs.get("id") == "docxVerifySuspicions":
                self.docx_verification = attrs
            if tag == "input" and attrs.get("id") == "importGenerateAnswers":
                self.answer_generation = attrs
            if attrs.get("id") == "docxVerificationContainer":
                self.docx_container = attrs

    parser = Inputs()
    parser.feed((ROOT / "static/index.html").read_text(encoding="utf-8"))
    assert [r["value"] for r in parser.radios] == ["layout_aware", "native_preferred", "force_ocr"]
    assert [r["value"] for r in parser.radios if "checked" in r] == ["layout_aware"]
    assert parser.verification is not None and "checked" not in parser.verification
    assert parser.docx_verification is not None and "checked" not in parser.docx_verification
    assert parser.answer_generation is not None and "checked" not in parser.answer_generation
    assert parser.docx_container is not None and "hidden" in parser.docx_container
    assert all(r.get("onchange") == "updatePdfVerificationOption()" for r in parser.radios)


@pytest.fixture(scope="module")
def pdf_script(review_script):
    source = (ROOT / "static/js/import.js").read_text(encoding="utf-8")

    def section(start, end):
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    # Keep the established review harness and shipped review functions without
    # dispatching its scenarios. Add the actual request/poll/crop functions.
    base = review_script.rsplit("scenarios[process.argv[1]]", 1)[0]
    base = base.replace(
        "const appendImportLog = message => logs.push(message);",
        "const logTypes = []; const appendImportLog = (message, type) => { logs.push(message); logTypes.push(type); };",
    )
    setup = r"""
window.addEventListener = () => {};
let checkedStrategy = null;
document.querySelector = selector => selector === 'input[name="pdfStrategy"]:checked' ? checkedStrategy : null;
const intervals = new Map();
let nextInterval = 0;
const setInterval = fn => { intervals.set(++nextInterval, fn); return nextInterval; };
const clearInterval = id => intervals.delete(id);
const flush = () => new Promise(resolve => setImmediate(resolve));
for (const id of ['importPaperTitle', 'importLatexContent', 'importPlaceholder', 'importLoadingState',
  'importLogsConsole', 'runParseBtn', 'importLoadingText', 'importSubLoadingText', 'importProgressBarContainer',
  'importProgressBar', 'importGenerateAnswers', 'pdfPageRange', 'pdfCropActiveImage', 'pdfCropImageContainer',
  'pdfCropConfirmBtn', 'pdfCropPageIndicator', 'pdfPagesThumbnailsContainer', 'pdfVerifySuspicions', 'pdfVerifySuspicionsNote',
  'docxVerifySuspicions', 'docxVerificationContainer', 'importSourceDetails', 'importFileSummary', 'texImagesSection']) {
  const el = create(id); el.style = {};
}
document.getElementById('importPaperTitle').value = '测试卷';
document.getElementById('pdfCropImageContainer').getBoundingClientRect = () => ({width:100, height:100});
let activePageIndex = 0, rectLeft = 10, rectTop = 20, rectWidth = 30, rectHeight = 40;
const clearPdfCropSelection = () => {};
"""
    shipped = "\n".join([
        section("function updateImportSourceView(kind)", "function openImportModal()"),
        section("function selectedPdfStrategy()", "window.zoomPdfCropIn ="),
        section("function loadPdfCropPage(", "function setupPdfCropDrawListeners"),
        section("function submitPdfCropCoordinates()", "function performOrphanedTempCropsCleanup"),
        section("function runAIPaperParse()", "function renderImagesList()"),
    ])
    checks = r"""
const pdfScenarios = {
  async completion_respects_explicit_answer_generation_choice() {
    for (const requested of [false,true]) {
      show([]);
      const generation=beginDocumentImportTask();
      pollPdfTaskStatus('answers-'+requested,generation);
      [...intervals.values()][0]();
      requests[0].resolve({ok:true,json:async()=>({status:'completed',document_type:'pdf',data:[
        pendingQuestion(),{content:'有原答案的题',answer_markdown:'C'}],generate_answers:requested,
        diagnostics:{source_review_count:1,unmatched_source:[]}})});
      await flush();
      assert.equal(intervals.size,0);
      if (!requested) {
        assert.equal(requests.length,1,'generate_answers=false sends no solve request for empty pending answers');
        assert.equal(parsedQuestionsData[0].answer_markdown,'');
      } else {
        assert.equal(requests.length,2,'explicit answer request includes pending source evidence');
        assert.equal(requests[1].url,'/api/ai/solve');
        requests[1].resolve(success('用户请求的解答'));await flush();
        assert.equal(parsedQuestionsData[0].answer_markdown,'用户请求的解答');
      }
      assert.equal(parsedQuestionsData[1].answer_markdown,'C','the original answer is never overwritten');
      assert.equal(parsedQuestionsData[0].source_review.required,true,'generation is not a source-verification verdict');
      assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    }
  },
  async docx_verification_controls_and_request() {
    const option=document.getElementById('docxVerificationContainer');
    for (const kind of ['empty','pdf','tex','docx']) {
      updateImportSourceView(kind);
      assert.equal(option.hidden,kind!=='docx','Word verification is shown only for Word');
    }
    window.currentDocxFile=new Blob(['docx']);
    for (const enabled of [true,false]) {
      document.getElementById('docxVerifySuspicions').checked=enabled;
      runAIPaperParse();
      const request=requests.at(-1);
      assert.equal(request.url,'/api/upload/docx-task');
      assert.equal(request.options.body.get('docx_verify_suspicions'),String(enabled));
      assert.equal(request.options.body.get('pdf_verify_suspicions'),null);
      assert.equal(request.options.body.get('generate_answers'),'false');
    }
    document.getElementById('docxVerifySuspicions').remove();
    runAIPaperParse();
    assert.equal(requests.at(-1).options.body.get('docx_verify_suspicions'),'false','old markup adds no hidden verification call');
    window.currentDocxFile=null;
    window.currentPdfFile=new Blob(['pdf']);
    runAIPaperParse();
    assert.equal(requests.at(-1).options.body.get('docx_verify_suspicions'),null,'Word option does not alter PDF requests');
  },
  async docx_verification_confirmation_report_and_revocation() {
    const q={content:'已知 $x+1=2$。',answer_markdown:'$x=1$',source_review:{required:false,verified_by:'vision',
      source_number:14,source_excerpt:'已知 $x+1=2$。',verification:{document_type:'docx',evidence:'已核对原文。 <img src=x onerror=bad()>'}}};
    const card=show([q,pendingQuestion()]);
    assert.equal(card.querySelector('.card-source-review-status').textContent,'查看原文自动核验记录');
    const panel=card.querySelector('.card-source-review-panel');
    assert.equal(panel.open,false);assert.equal(panel.querySelector('img'),null,'evidence is plain text');
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    assert.equal(validateParsedQuestionBeforeImport(0),true);
    const diagnostics={source_review_count:1,docx_source_verification:{status:'completed',calls:1,checked:2,
      confirmed:1,pending:1,skipped:1,skipped_reasons:[{question_index:1,source_number:null,source_pages:[],
        code:'source_evidence_missing',reason:'无法唯一对应本题的原文。 <script>bad()</script>'}]}};
    renderSourceIntegrityReport(diagnostics);appendSourceIntegrityLog(diagnostics);
    let report=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(report.open,false);
    assert.ok(report.querySelector('.pdf-source-verification-summary').textContent.includes('AI 核验 2 题，已确认 1 题，保留提取说明 1 题'));
    assert.deepEqual(report.querySelector('.pdf-review-question-list').querySelectorAll('button').map(b=>b.textContent),['第2张题卡']);
    assert.ok(report.querySelector('.pdf-layout-notes').querySelector('p').textContent.includes('第2张题卡：无法唯一对应'));
    assert.equal(report.querySelector('.pdf-layout-notes').querySelector('p').textContent.includes('原卷第'),false);
    assert.equal(report.querySelector('script'),null);assert.ok(logs.some(line=>line.includes('AI 核验 2 题')));
    const original=card.querySelector('.card-content-textarea').value;
    edit(card,'.card-content-textarea',original+'修改');edit(card,'.card-content-textarea',original);
    assert.equal(parsedQuestionNeedsSourceReview(0),true,'Word edits and undo revoke vision approval');
    assert.ok(card.querySelector('.card-source-review-reasons').textContent.includes('先前的自动核验仅适用于旧内容'));
    assert.equal(validateParsedQuestionBeforeImport(0),true,'expired evidence does not gate import');
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    replaceParsedQuestions([q]);renderParsedQuestionsList(parsedQuestionsData);
    assert.equal(parsedQuestionNeedsSourceReview(0),true,'re-render must not revive Word approval');
    renderSourceIntegrityReport({source_review_count:1,docx_source_verification:{status:'no_candidates',calls:0,pending:1,
      notes:['未找到 Word 页面渲染组件，已保留人工核对提示。']}});
    report=document.getElementById('parsedSourceIntegrityReport');
    assert.ok(report.querySelector('.pdf-source-verification-summary').textContent.includes('未找到 Word 页面渲染组件'));
    assert.ok(report.querySelector('.pdf-source-verification-summary').textContent.includes('有 1 题保留提取说明'));
    assert.equal(requests.length,0);
  },
  async docx_verification_polling_and_completion() {
    const generation=beginDocumentImportTask();
    pollPdfTaskStatus('docx-1',generation);
    const tick=[...intervals.values()][0];
    tick();
    requests.at(-1).resolve({ok:true,json:async()=>({status:'source_verification',document_type:'docx',
      log:'正在核验 Word 疑点',progress:90})});
    await flush();
    assert.equal(document.getElementById('importSubLoadingText').textContent,
      '正在按所选设置核验 Word 原文；结果保留在可选提取说明中。');
    tick();
    const q={content:'Word 已确认题',answer_markdown:'C',source_review:{required:false,verified_by:'vision',
      verification:{document_type:'docx',evidence:'与原文一致。'}}};
    requests.at(-1).resolve({ok:true,json:async()=>({status:'completed',document_type:'docx',log:'完成',data:[q],
      generate_answers:false,diagnostics:{source_review_count:0,review_required:2,docx_source_verification:{status:'completed',calls:1,
        checked:1,confirmed:1,pending:0,skipped:0,notes:[]}}})});
    await flush();
    assert.equal(intervals.size,0);
    assert.equal(parsedQuestionNeedsSourceReview(0),false);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    assert.ok(document.getElementById('parsedSourceIntegrityReport').querySelector('.pdf-source-verification-summary').textContent.includes('已确认 1 题，保留提取说明 0 题'));
    assert.ok(logs.some(line=>line.includes('提取阶段标记 2 处疑点')));
    assert.ok(toasts.some(line=>line.includes('不影响导入')));
    assert.equal(requests.length,2,'only mocked task polling, no AI or save requests');
  },
  async strategy_request() {
    window.currentPdfFile = new Blob(['pdf']);
    document.getElementById('pdfPageRange').value = '2, 5';
    for (const strategy of [null, 'layout_aware', 'native_preferred', 'force_ocr']) {
      checkedStrategy = strategy ? {value:strategy} : null;
      document.getElementById('pdfVerifySuspicions').checked=true;
      updatePdfVerificationOption();
      runAIPaperParse();
      const request = requests.at(-1);
      assert.equal(request.url, '/api/upload/pdf-task');
      assert.equal(request.options.body.get('pdf_strategy'), strategy || 'layout_aware');
      assert.equal(request.options.body.get('page_range'), '2, 5');
      assert.equal(request.options.body.get('generate_answers'), 'false');
      assert.equal(request.options.body.get('pdf_verify_suspicions'), !strategy || strategy==='layout_aware' ? 'true' : 'false');
      assert.equal(document.getElementById('pdfVerifySuspicions').disabled, !!strategy && strategy!=='layout_aware');
    }
    checkedStrategy={value:'layout_aware'};
    document.getElementById('pdfVerifySuspicions').checked=false;
    runAIPaperParse();
    assert.equal(requests.at(-1).options.body.get('pdf_verify_suspicions'),'false');
    document.getElementById('pdfVerifySuspicions').remove();
    runAIPaperParse();
    assert.equal(requests.at(-1).options.body.get('pdf_verify_suspicions'),'false','old markup never silently adds paid verification');
  },
  async vision_confirmation_and_edit_invalidation() {
    const q={content:'计算 $1+1$。',answer_markdown:'',source_review:{required:false,verified_by:'vision',
      reasons:['原文字体需要核对。'],source_excerpt:'计算 $1+1$。'}};
    const card=show([q,pendingQuestion()]);
    assert.equal(parsedQuestionNeedsSourceReview(0),false);
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    assert.equal(card.querySelector('.card-source-review-status').textContent,'查看原页自动核验记录');
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.equal(card.querySelector('.card-source-review-panel').open,false);
    const original=card.querySelector('.card-content-textarea').value;
    edit(card,'.card-content-textarea',original+' 已编辑');edit(card,'.card-content-textarea',original);
    assert.equal(parsedQuestionNeedsSourceReview(0),true,'undo does not revive a revoked AI verification');
    assert.equal(card.querySelector('.card-select-checkbox').disabled,false);
    assert.ok(card.querySelector('.card-source-review-reasons').textContent.includes('先前的自动核验仅适用于旧内容'));
    assert.equal(validateParsedQuestionBeforeImport(0),true);
    assert.equal(card.querySelector('.card-source-review-status').textContent,'查看原文与提取说明（可选）');
    edit(card,'.card-answer-textarea','教师补充解析');
    replaceParsedQuestions([q]);renderParsedQuestionsList(parsedQuestionsData);
    assert.equal(parsedQuestionNeedsSourceReview(0),true,'reusing the object is not a new model verification');
    assert.equal(requests.length,0);
  },
  async vision_snapshot_revocation_does_not_gate_import() {
    const q={content:'计算 $2+2$。',answer_markdown:'',source_review:{required:false,verified_by:'vision',source_excerpt:'计算 $2+2$。'}};
    const card=show([q]);assert.equal(validateParsedQuestionBeforeImport(0),true);
    card.querySelector('.card-content-textarea').value='计算 $2-2$。';
    assert.equal(parsedQuestionNeedsSourceReview(0),true);
    assert.equal(validateParsedQuestionBeforeImport(0),true,'silent change invalidates proof, not import permission');
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    card.querySelector('.card-content-textarea').value=q.content;
    assert.equal(parsedQuestionNeedsSourceReview(0),true,'detected silent changes permanently revoke the old result');
    const next={content:'新核验题',answer_markdown:'',source_review:{required:false,verified_by:'vision'}};
    replaceParsedQuestions([next]);
    assert.equal(parsedQuestionNeedsSourceReview(0),false,'stale old DOM cannot revoke a new question object');
    renderParsedQuestionsList(parsedQuestionsData);assert.equal(parsedQuestionNeedsSourceReview(0),false);
    assert.equal(requests.length,0);
  },
  async source_verification_report_success_pending_and_failure() {
    show([{content:'已核验题',answer_markdown:'',source_review:{required:false,verified_by:'vision'}},pendingQuestion()]);
    renderSourceIntegrityReport({source_review_count:1,pdf_source_verification:{status:'completed',calls:1,
      checked:2,confirmed:1,pending:1,skipped:0,notes:[]}});
    let report=document.getElementById('parsedSourceIntegrityReport');
    assert.ok(report.querySelector('.pdf-source-verification-summary').textContent.includes('AI 核验 2 题，已确认 1 题，保留提取说明 1 题'));
    assert.deepEqual(report.querySelector('.pdf-review-question-list').querySelectorAll('button').map(b=>b.textContent),['第2张题卡']);
    const failure={source_review_count:1,pdf_source_verification:{status:'failed',calls:1,checked:0,confirmed:0,pending:1,
      notes:['自动核验未完成：请求失败 <img src=x onerror=bad()>；已保留人工核对提示。']}};
    renderSourceIntegrityReport(failure);
    report=document.getElementById('parsedSourceIntegrityReport');
    assert.ok(report.querySelector('.pdf-source-verification-summary').textContent.includes('请求失败 <img'));
    assert.equal(report.querySelector('img'),null);
    assert.equal(parsedQuestionNeedsSourceReview(1),true);
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    assert.equal(pdfSourceVerificationReportSummary({status:'disabled'}),'');
    assert.ok(pdfSourceVerificationReportSummary({status:'no_candidates',calls:0,pending:0}).includes('无需额外模型核验'));
    assert.ok(pdfSourceVerificationReportSummary({status:'no_candidates',calls:0,pending:2,skipped:2}).includes('有 2 题保留提取说明'));
    const withoutEvidence=pdfSourceVerificationReportSummary({status:'no_candidates',calls:0,pending:2,skipped:2});
    assert.ok(withoutEvidence.includes('现有依据不足以自动确认'));
    assert.equal(withoutEvidence.includes('本次未额外调用'),false);
    assert.ok(pdfSourceVerificationReportSummary({status:'no_candidates',calls:0,pending:1,
      notes:['无法唯一对应本题的原页范围']}).includes('无法唯一对应本题的原页范围'));
    const skipped={status:'no_candidates',calls:0,pending:1,skipped:1,skipped_reasons:[
      {question_index:1,source_number:18,source_pages:[4],code:'source_evidence_missing',reason:'缺少可唯一对应的原页依据。'}]};
    assert.ok(pdfSourceVerificationReportSummary(skipped).includes('缺少可唯一对应的原页依据'));
    renderSourceIntegrityReport({source_review_count:1,pdf_source_verification:skipped});
    report=document.getElementById('parsedSourceIntegrityReport');
    const skippedNotes=report.querySelector('.pdf-layout-notes');
    assert.ok(skippedNotes.querySelector('p').textContent.includes('第18题（原卷第4页）'));
    assert.equal(skippedNotes.querySelector('p').textContent.includes('source_evidence_missing'),false);
    assert.equal(!!skippedNotes.open,false);
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1],'explanation text must not create more pending cards');
  },
  async warnings_and_unmatched_images() {
    const warning = '原图归属不明确 <img src=x onerror=bad()>';
    renderSourceIntegrityReport({pdf_layout:{pages_checked:2, figures_extracted:3,
      figures_attached:2, unmatched_figures:1, visual_calls:2, review_pages:1, warnings:[warning]},
      unmatched_source:[{reason:'图 3 未归位', source_excerpt:[
        '![图3](/static/uploads/pdf_figure_3.png)',
        '![恶意外链](https://evil.example/image.png)',
        '<script>bad()</script>'
      ].join('\n')}]});
    const report = document.getElementById('parsedSourceIntegrityReport');
    assert.ok(report);
    assert.ok(report.querySelector('p').textContent.includes('已归位 2 张，未归位 1 张'));
    assert.ok(report.querySelectorAll('p').some(p => p.textContent === warning));
    assert.equal(report.querySelector('script'), null);
    assert.deepEqual(report.querySelectorAll('img').map(img => img.src), ['/static/uploads/pdf_figure_3.png']);
    assert.equal(report.open,false);
    assert.ok(report.querySelector('details').querySelector('summary').textContent.includes('图 3 未归位'));
    renderSourceIntegrityReport({pdf_layout:{warnings:[warning]}});
    const warningsOnly = document.getElementById('parsedSourceIntegrityReport');
    assert.ok(warningsOnly, 'warnings remain visible even without question review flags');
    assert.equal(warningsOnly.querySelector('strong'), null, 'do not invent pending questions');
    assert.equal(warningsOnly.querySelector('img'), null);
    renderSourceIntegrityReport(null);
    assert.equal(document.getElementById('parsedSourceIntegrityReport'), null);
  },
  async notes_are_collapsed_nonblocking_information() {
    const card = show([{content:'正常题', answer_markdown:''}]);
    const notes = ['第 3 页：已自动补齐图形边缘。', '背景图已忽略 <img src=x onerror=bad()>'];
    const diagnostics = {source_review_count:0,math_locks_created:3,math_locks_restored_by_id:3,pdf_layout:{pages_checked:2,figures_extracted:3,
      figures_attached:3,unmatched_figures:0,visual_calls:2,review_pages:0,warnings:[],notes}};
    renderSourceIntegrityReport(diagnostics);
    const report = document.getElementById('parsedSourceIntegrityReport');
    assert.ok(report.className.includes('text-slate-600'));
    assert.equal(report.className.includes('amber'),false,'ordinary notes are not warning-colored');
    assert.equal(report.querySelector('strong'),null);
    assert.equal(report.querySelector('p').textContent.includes('需人工核对'),false);
    const details=report.querySelector('.pdf-layout-notes');
    assert.ok(details);
    assert.equal(!!details.open,false);
    assert.equal(details.querySelector('summary').textContent,'处理说明（2 项）');
    assert.deepEqual(details.querySelectorAll('p').map(p=>p.textContent),notes);
    assert.equal(details.querySelector('img'),null);
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.equal(card.querySelector('.card-select-checkbox').disabled,false);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    appendSourceIntegrityLog(diagnostics);
    assert.ok(logTypes.every(type=>type !== 'warning'));
    assert.ok(logs.every(line=>!line.includes('需人工核对')));
  },
  async native_quality_reasons_are_filtered_nonblocking_notes() {
    const card=show([{content:'已完成识别的正常题',answer_markdown:''}]);
    const reason='原生文字含缺字或未解码的私用字体符号。';
    const unsafe='需识别原页 <img src=x onerror=bad()>';
    const diagnostics={source_review_count:0,pdf_review_items:[],unmatched_source:[],warnings:[],
      pdf_extraction:{native_pages:0,regional_pages:0,full_vision_pages:1,native_characters_reused:0},
      pdf_native_quality:[{page_number:1,reasons:[reason,' '+reason+' ',null,{},42,'']},
        {page_number:3,reasons:[' 需识别复杂公式。 ',unsafe]},
        {page_number:0,reasons:[reason]},{page_number:-1,reasons:[reason]},
        {page_number:1.5,reasons:[reason]},{page_number:'2',reasons:[reason]},
        {page_number:true,reasons:[reason]},null,'unexpected',
        {page_number:4,reasons:reason},{page_number:5,reasons:[null,{},42,'  ']}]};
    const before=JSON.stringify(diagnostics);
    renderSourceIntegrityReport(diagnostics);
    const report=document.getElementById('parsedSourceIntegrityReport');
    const details=report.querySelector('.pdf-layout-notes');
    assert.equal(!!details.open,false);
    assert.deepEqual(details.querySelectorAll('p').map(p=>p.textContent),[
      '第1页转入视觉识别：'+reason,'第3页转入视觉识别：需识别复杂公式。；'+unsafe]);
    assert.equal(details.querySelector('summary').textContent,'处理说明（2 项）');
    assert.equal(report.querySelector('img'),null,'diagnostic reasons are plain text');
    assert.equal(report.className.includes('amber'),false,'quality fallback is not an unresolved review warning');
    assert.equal(report.querySelector('strong'),null);
    assert.equal(report.querySelector('.pdf-review-question-list'),null);
    assert.equal(report.querySelector('.pdf-extraction-summary').textContent.includes('扫描'),false);
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.equal(card.querySelector('.card-select-checkbox').disabled,false);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    appendSourceIntegrityLog(diagnostics);
    assert.ok(logTypes.every(type=>type!=='warning'));
    assert.equal(JSON.stringify(diagnostics),before,'display must not mutate warning or review counts');
    for (const invalid of [null,{},'not an array',[null,{page_number:2,reasons:[]}],
      [{page_number:'1',reasons:[reason]}]]) {
      assert.deepEqual(pdfLayoutNotes({pdf_native_quality:invalid}),[]);
      renderSourceIntegrityReport({pdf_native_quality:invalid});
      assert.equal(document.getElementById('parsedSourceIntegrityReport'),null);
    }
  },
  async ignored_source_and_inapplicable_guard_are_nonblocking_notes() {
    const card=show([{content:'正常题',answer_markdown:''}]);
    const ignored=['已忽略卷首 3 条考试说明。','已按页脚格式排除 4 处页面标记。'];
    const diagnostics={source_review_count:0,pdf_review_items:[],unmatched_source:[],ignored_source_notes:ignored};
    renderSourceIntegrityReport(diagnostics);
    let report=document.getElementById('parsedSourceIntegrityReport');
    assert.ok(report,'local-only processing notes should remain inspectable');
    assert.equal(report.className.includes('amber'),false);
    assert.equal(report.querySelector('strong'),null);
    assert.deepEqual(report.querySelector('.pdf-layout-notes').querySelectorAll('p').map(p=>p.textContent),ignored);
    assert.equal(!!report.querySelector('.pdf-layout-notes').open,false);
    assert.deepEqual(diagnostics.unmatched_source,[]);
    assert.equal(card.querySelector('.card-source-review-confirm'),null);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    renderSourceIntegrityReport({...diagnostics,pdf_layout:{review_pages:0,unmatched_figures:0,
      warnings:[],notes:['本地图形规则不适用于这类插图，保留已有图框。']},
      pdf_source_verification:{status:'no_candidates',calls:0,pending:0,skipped_reasons:[]}});
    report=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(report.className.includes('amber'),false);
    assert.equal(report.querySelector('.pdf-review-question-list'),null);
    assert.equal(parsedQuestionNeedsSourceReview(0),false);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
  },
  async global_page_warning_does_not_require_all_questions() {
    show([{content:'正常题',answer_markdown:''}, pendingQuestion()]);
    renderSourceIntegrityReport({source_review_count:1,pdf_layout:{review_pages:1,notes:['边缘已补齐'],
      warnings:['第 7 页有无法定位的候选区域，请核对原页。']},unmatched_source:[{
        reason:'第 7 页配图检测覆盖不完整', source_excerpt:'![原页](/static/uploads/page7.png)'}]});
    const report=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(document.getElementById('parsed-card-0').querySelector('.card-source-review-confirm'),null);
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    assert.equal(report.querySelector('strong').textContent,'原文对照记录（1 题）');
    assert.equal(report.querySelector('.pdf-review-question-link').textContent,'第2张题卡');
    assert.ok(report.querySelector('summary').textContent.includes('可选查看'));
    assert.deepEqual(report.querySelectorAll('img').map(img=>img.src),['/static/uploads/page7.png']);
    show([{content:'正常题',answer_markdown:''}]);
    renderSourceIntegrityReport({source_review_count:0,pdf_layout:{review_pages:1,warnings:['请核对原页']},
      unmatched_source:[{reason:'第 7 页配图检测覆盖不完整',source_excerpt:'![原页](/static/uploads/page7.png)'}]});
    assert.equal(document.getElementById('parsedSourceIntegrityReport').querySelector('strong').textContent.includes('逐题确认'),false);
  },
  async structured_review_numbers_scopes_and_jump_links() {
    show([pendingQuestion(),pendingQuestion()]);
    const diagnostics={source_review_count:2,pdf_layout:{pages_checked:2,review_pages:2,
      warnings:['p6_raster_001 coverage 75%']},pdf_review_items:[
        {question_index:0,source_number:21,source_pages:[6],reasons:['请检查两幅图是否完整。']},
        {question_index:1,source_number:null,source_pages:[6,7],reasons:['本题公式与原文不一致。<script>bad()</script>']}
      ],unmatched_source:[
        {scope:'question',page_index:5,affected_questions:[{question_index:0,source_number:21}],
          reason:'配图边缘需要核对。',source_excerpt:'![配图](/static/uploads/figure21.png)'},
        {scope:'possible_questions',source_pages:[6],affected_questions:[{question_index:0,source_number:21},{question_index:1,source_number:null}],
          reason:'该图的归属尚不明确。',source_excerpt:'![原页](/static/uploads/page6.png)'},
        {scope:'page_unassigned',page_index:7,affected_questions:[],reason:'本页存在无法归属的插图。',
          source_excerpt:'![原页](/static/uploads/page8.png)'}
      ]};
    renderSourceIntegrityReport(diagnostics);
    const report=document.getElementById('parsedSourceIntegrityReport');
    const links=report.querySelector('.pdf-review-question-list').querySelectorAll('button');
    assert.deepEqual(links.map(link=>link.textContent),['第21题（原卷第6页）','第2张题卡（原卷第6、7页）']);
    assert.equal(report.querySelector('strong').textContent,'原文对照记录（2 题）');
    const titles=report.querySelectorAll('details').map(detail=>detail.querySelector('summary').textContent);
    assert.equal(titles[0],'第21题（原卷第6页）：配图边缘需要核对。');
    assert.equal(titles[1],'可能涉及第21题、第2张题卡（原卷第6页）：该图的归属尚不明确。');
    assert.equal(titles[2],'原卷第8页，尚无法确定题号：本页存在无法归属的插图。');
    const allText=node=>node.textContent+' '+node.children.map(allText).join(' ');
    assert.equal(allText(report).includes('p6_raster_001'),false);
    assert.equal(allText(report).includes('75%'),false);
    assert.equal(report.querySelector('script'),null);
    const target=document.getElementById('parsed-card-1'),events=[];
    target.scrollIntoView=options=>events.push(['scroll',options.block]);
    target.focus=options=>events.push(['focus',options.preventScroll]);
    links[1].dispatchEvent(new Event('click'));
    assert.deepEqual(events,[['scroll','start'],['focus',true]]);
    assert.equal(parsedQuestionNeedsSourceReview(1),true,'jumping does not confirm the source review');
    appendSourceIntegrityLog(diagnostics);
    assert.equal(logs.some(line=>line.includes('p6_raster_001')||line.includes('75%')),false);
  },
  async joint_and_separate_visual_call_counts() {
    const joint=pdfLayoutReportSummary({pages_checked:2,joint_visual_calls:2,visual_calls:0});
    assert.ok(joint.includes('联合识图 2 次，单独配图核对 0 次'));
    const mixed=pdfLayoutReportSummary({joint_visual_calls:1,visual_calls:3});
    assert.ok(mixed.includes('联合识图 1 次，单独配图核对 3 次'));
    const legacy=pdfLayoutReportSummary({visual_calls:2});
    assert.ok(legacy.includes('配图核对调用 2 次'));
    assert.equal(legacy.includes('联合识图'),false);
  },
  async native_structure_repair_summary_and_notes_are_local_only() {
    const card=show([{content:'原生修复后提取的题目',answer_markdown:''}]);
    const extraction={native_pages:2,repaired_pages:1,regional_pages:0,full_vision_pages:1};
    const diagnostics={source_review_count:0,warnings:[],unmatched_source:[],pdf_extraction:extraction,
      pdf_native_repair:[
        {page_number:1,status:'repaired',notes:[' 已按嵌入字体恢复分段公式。 ',null,{},'已按嵌入字体恢复分段公式。']},
        {page_number:3,status:'fallback',reason:'未能确认完整公式结构。',notes:['保留视觉识别。 <img src=x onerror=bad()>']},
        {page_number:'4',status:'repaired',notes:['invalid']},
        {page_number:0,status:'fallback',reason:'invalid'},
        {page_number:2,status:'unknown',notes:['invalid']},null,
        {page_number:2,status:'repaired',notes:[{},null,42,'  '],reason:'not a successful repair note'}]};
    const before=JSON.stringify(diagnostics);
    renderSourceIntegrityReport(diagnostics);
    const panel=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(panel.querySelector('.pdf-extraction-summary').textContent,
      'PDF 提取方式：原生直提 2 页（其中本地公式结构修复 1 页），局部识别 0 页，整页识别 1 页。');
    const details=panel.querySelector('.pdf-layout-notes');
    assert.equal(!!details.open,false);
    assert.deepEqual(details.querySelectorAll('p').map(p=>p.textContent),[
      '第1页本地公式结构修复：已按嵌入字体恢复分段公式。',
      '第3页未采用本地公式结构修复：未能确认完整公式结构。；保留视觉识别。 <img src=x onerror=bad()>']);
    assert.equal(panel.querySelector('img'),null);
    assert.equal(panel.className.includes('amber'),false);
    assert.equal(panel.querySelector('strong'),null);
    assert.equal(panel.querySelector('.pdf-source-verification-summary'),null,'local repair never claims model verification');
    assert.equal(card.querySelector('.card-source-review-status'),null);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    appendSourceIntegrityLog(diagnostics);
    assert.ok(logs.some(line=>line.includes('其中本地公式结构修复 1 页')));
    assert.ok(logTypes.every(type=>type!=='warning'));
    assert.equal(JSON.stringify(diagnostics),before);
    const legacy=pdfExtractionReportSummary({...extraction,repaired_pages:undefined});
    for (const value of [undefined,null,0,-1,0.5,'1',true,NaN,Infinity,3,Number.MAX_SAFE_INTEGER+1]) {
      assert.equal(pdfExtractionReportSummary({...extraction,repaired_pages:value}),legacy,'invalid repair counts cannot imply success');
    }
    assert.equal(pdfExtractionReportSummary({native_pages:0,repaired_pages:1}).includes('本地公式结构修复'),false);
    for (const value of [null,{},'invalid',[{page_number:1,status:'fallback',notes:{},reason:null}]]) {
      assert.deepEqual(pdfLayoutNotes({pdf_native_repair:value}),[]);
    }
    show([{content:'正常题',answer_markdown:''},pendingQuestion()]);
    renderSourceIntegrityReport({...diagnostics,source_review_count:1});
    assert.equal(parsedQuestionNeedsSourceReview(1),true,'local repair does not clear existing source review');
    assert.deepEqual(getCheckedUnsavedIndices(),[0,1]);
    assert.equal(document.getElementById('parsedSourceIntegrityReport').querySelector('.pdf-review-question-list').querySelectorAll('button').length,1);
  },
  async regional_extraction_summary_is_visible_without_review_warning() {
    show([{content:'正常题目',answer_markdown:''}]);
    const diagnostics={pdf_extraction:{native_pages:2,regional_pages:3,full_vision_pages:1,native_characters_reused:120}};
    renderSourceIntegrityReport(diagnostics);
    const panel=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(panel.querySelector('.pdf-extraction-summary').textContent,
      'PDF 提取方式：原生直提 2 页，局部识别 3 页，整页识别 1 页。局部页保留原生文字 120 字符。');
    assert.equal(panel.className.includes('amber'),false);
    assert.equal(panel.querySelector('.pdf-review-question-link'),null);
    assert.deepEqual(getCheckedUnsavedIndices(),[0]);
    appendSourceIntegrityLog(diagnostics);
    assert.ok(logs.some(line=>line.includes('局部识别 3 页')));
    const generation=beginDocumentImportTask();
    pollPdfTaskStatus('regional-1',generation);
    [...intervals.values()][0]();
    requests.at(-1).resolve({ok:true,json:async()=>({status:'ocr_extraction',log:'局部识别进行中',diagnostics})});
    await flush();
    assert.ok(document.getElementById('importSubLoadingText').textContent.includes('保留可靠原生文字'));
    stopCurrentDocumentPoll();
  },
  async missing_review_metadata_uses_current_cards_and_provenance() {
    const questions=[pendingQuestion(),pendingQuestion(),pendingQuestion(),{content:'正常题',answer_markdown:''}];
    questions[1].source_review.source_number=21;
    questions[1].source_review.source_pages=[6];
    questions[2].content='99. 模型写出的数字不能当作原卷题号。';
    questions[2].pdf_source_figures=[{page_index:7}];
    show(questions);
    const report={source_review_count:3,source_matches:[
      {question_index:0,source_number:14},
      {question_index:2,source_number:4},{question_index:2,source_number:5}
    ]};
    for (const supplied of [undefined,[],[{question_index:0,source_number:14,source_pages:[3],reasons:['原文公式需核对']} ]]) {
      const diagnostics={...report};
      if (supplied !== undefined) diagnostics.pdf_review_items=supplied;
      renderSourceIntegrityReport(diagnostics);
      const panel=document.getElementById('parsedSourceIntegrityReport');
      const links=panel.querySelector('.pdf-review-question-list').querySelectorAll('button');
      assert.deepEqual(links.map(link=>link.textContent),[
        supplied?.length ? '第14题（原卷第3页）' : '第14题', '第21题（原卷第6页）', '第3张题卡（原卷第8页）'
      ]);
      assert.equal(panel.querySelector('strong').textContent,'原文对照记录（3 题）');
      const events=[], target=document.getElementById('parsed-card-2');
      target.scrollIntoView=()=>events.push('scroll'); target.focus=()=>events.push('focus');
      links[2].dispatchEvent(new Event('click'));
      assert.deepEqual(events,['scroll','focus']);
      assert.equal(parsedQuestionNeedsSourceReview(2),true);
    }
    renderSourceIntegrityReport(null);
    assert.equal(document.getElementById('parsedSourceIntegrityReport'),null,'clear must not recreate a fallback report');
    replaceParsedQuestions([pendingQuestion()]);
    assert.equal(document.getElementById('parsedSourceIntegrityReport'),null,'new cards need their own diagnostics before a report appears');
    renderSourceIntegrityReport({source_review_count:1});
    assert.equal(document.querySelectorAll('.pdf-review-question-link')[0].textContent,'第1张题卡');
  },
  async captured_question_14_report_and_missing_metadata() {
    const task=JSON.parse(require('node:fs').readFileSync(process.env.MATHBANK_TEST_PDF_REPORT_TASK,'utf8'));
    show(task.data);
    assert.equal(task.data.length,24);
    assert.equal(task.data[13].source_review.required,true);
    renderSourceIntegrityReport(task.diagnostics);
    let panel=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(panel.querySelector('.pdf-review-question-link').textContent,'第14题（原卷第3页）');
    const fallback={...task.diagnostics};
    delete fallback.pdf_review_items;
    renderSourceIntegrityReport(fallback);
    panel=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(panel.querySelector('.pdf-review-question-link').textContent,'第14题');
    delete fallback.source_matches;
    renderSourceIntegrityReport(fallback);
    panel=document.getElementById('parsedSourceIntegrityReport');
    assert.equal(panel.querySelector('.pdf-review-question-link').textContent,'第14张题卡');
  },
  async polling_progress_and_completion() {
    const generation = beginDocumentImportTask();
    pollPdfTaskStatus('pdf-1', generation);
    const tick = [...intervals.values()][0];
    tick();
    requests.at(-1).resolve({ok:true,json:async()=>({status:'layout_analysis',log:'多模态图文核对',
      page_images:['/static/uploads/tmp/page2.png','/static/uploads/tmp/page5.png'],page_numbers:[2,5],progress:50})});
    await flush();
    assert.ok(document.getElementById('importSubLoadingText').textContent.includes('核对归属'));
    assert.deepEqual(window.pdfPageNumbers,[2,5]);
    tick();
    requests.at(-1).resolve({ok:true,json:async()=>({status:'source_verification',log:'正在对照原页核验...',progress:90})});
    await flush();
    assert.ok(document.getElementById('importSubLoadingText').textContent.includes('按所选设置核验'));
    tick();
    requests.at(-1).resolve({ok:true,json:async()=>({status:'completed',log:'完成',document_type:'pdf',data:[],
      diagnostics:{pdf_layout:{pages_checked:2,figures_extracted:1,figures_attached:1,visual_calls:2,warnings:[]},
        math_locks_created:3,math_locks_restored_by_id:3},generate_answers:false})});
    await flush();
    assert.equal(intervals.size,0);
    assert.ok(logs.some(line=>line.includes('配图核对调用 2 次')));
    assert.ok(logs.some(line=>line.includes('公式核对：3 处')),'PDF formula report also appears');
    assert.ok(document.getElementById('parsedSourceIntegrityReport'));
  },
  async stale_poll_does_not_replace_pages_or_report() {
    const generation = beginDocumentImportTask();
    pollPdfTaskStatus('old', generation);
    [...intervals.values()][0]();
    const oldRequest = requests.at(-1);
    beginDocumentImportTask();
    window.pdfPageNumbers=[9]; window.pdfPageImages=['current'];
    oldRequest.resolve({ok:true,json:async()=>({status:'completed',document_type:'pdf',data:[],
      page_images:['stale'],page_numbers:[1],diagnostics:{pdf_layout:{warnings:['stale']}}})});
    await flush();
    assert.deepEqual(window.pdfPageNumbers,[9]);
    assert.deepEqual(window.pdfPageImages,['current']);
    assert.equal(document.getElementById('parsedSourceIntegrityReport'),null);
  },
  async crop_uses_original_pdf_page() {
    window.pdfPageNumbers=[2,5];
    window.pdfPageImages=['/static/uploads/tmp/page2.png','/static/uploads/tmp/page5.png'];
    loadPdfCropPage(1);
    assert.ok(document.getElementById('pdfCropPageIndicator').textContent.includes('原卷第 5 页'));
    submitPdfCropCoordinates();
    let payload=JSON.parse(requests.at(-1).options.body);
    assert.equal(payload.page_index,4,'crop filename uses zero-based original page');
    assert.equal(payload.xmin,10); assert.equal(payload.ymax,60);
    window.pdfPageNumbers=[];
    submitPdfCropCoordinates();
    payload=JSON.parse(requests.at(-1).options.body);
    assert.equal(payload.page_index,1,'older servers remain compatible');
  }
};
pdfScenarios[process.argv[1]]().catch(error=>{console.error(error);process.exitCode=1;});
"""
    return base + setup + shipped + checks


@pytest.mark.parametrize("scenario", [
    "completion_respects_explicit_answer_generation_choice",
    "docx_verification_controls_and_request", "docx_verification_confirmation_report_and_revocation",
    "docx_verification_polling_and_completion",
    "strategy_request", "warnings_and_unmatched_images", "polling_progress_and_completion",
    "vision_confirmation_and_edit_invalidation", "vision_snapshot_revocation_does_not_gate_import",
    "source_verification_report_success_pending_and_failure",
    "stale_poll_does_not_replace_pages_or_report", "crop_uses_original_pdf_page",
    "notes_are_collapsed_nonblocking_information", "global_page_warning_does_not_require_all_questions",
    "native_quality_reasons_are_filtered_nonblocking_notes",
    "ignored_source_and_inapplicable_guard_are_nonblocking_notes",
    "structured_review_numbers_scopes_and_jump_links",
    "joint_and_separate_visual_call_counts",
    "regional_extraction_summary_is_visible_without_review_warning",
    "native_structure_repair_summary_and_notes_are_local_only",
    "missing_review_metadata_uses_current_cards_and_provenance",
    pytest.param("captured_question_14_report_and_missing_metadata", marks=pytest.mark.skipif(
        not os.environ.get("MATHBANK_TEST_PDF_REPORT_TASK"), reason="opt-in captured Q14 report; no network")),
])
def test_pdf_layout_ui_behavior(pdf_script, scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for executable frontend regression checks"
    # Linux limits each argv entry to roughly 128 KiB. Feed the complete
    # shipped-code harness through UTF-8 stdin, keeping scenario argv stable.
    result = subprocess.run([node, "-e", "eval(require('node:fs').readFileSync(0,'utf8'))", scenario],
                            input=pdf_script, cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr


def _prepare_browser_pdf_review(browser):
    return browser.evaluate(r"""
    (async () => {
        const canvas = document.createElement('canvas');
        canvas.width = 400; canvas.height = 480;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 400, 480);
        ctx.strokeStyle = '#234'; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(70, 350); ctx.lineTo(320, 350); ctx.lineTo(180, 90); ctx.closePath(); ctx.stroke();
        ctx.fillStyle = '#234'; ctx.font = '32px serif';
        ctx.fillText('A', 170, 75); ctx.fillText('B', 40, 380); ctx.fillText('C', 325, 380);
        const form = new FormData();
        form.set('file', await new Promise(resolve => canvas.toBlob(resolve)), 'pdf-review.png');
        const response = await fetch('/api/upload', {method:'POST', body:form});
        const data = await response.json();
        if (!response.ok || !data.file_path) throw Error(JSON.stringify(data));
        window.__pdfReviewImage = data.file_path;
        selectWorkspace('import', '导入中心');
        replaceParsedQuestions([{content:'在三角形 $ABC$ 中，求边长。', question_type:'detailed_answer',
            answer_markdown:'', source_review:{required:true, reasons:['请核对原卷第 3 页图形。'],
                source_excerpt:'原卷第 3 页\n![原页](' + data.file_path + ')'}}]);
        renderParsedQuestionsList(parsedQuestionsData);
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        document.getElementById('pdfPageRangeContainer').classList.remove('hidden');
        return data.file_path;
    })()
    """)


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_ignored_exam_metadata_and_inapplicable_graphic_do_not_warn(browser, tmp_path):
    _prepare_browser_pdf_review(browser)
    browser.evaluate(r"""
    (() => {
        replaceParsedQuestions([{content:'一个正方形被分割成四个矩形，求染色方法数。',answer_markdown:'',question_type:'fill_in_blank'}]);
        renderParsedQuestionsList(parsedQuestionsData);
        renderSourceIntegrityReport({source_review_count:0,unmatched_source:[],
            ignored_source_notes:['已忽略卷首 3 条考试说明。','已按页脚格式排除 4 处页面标记。'],
            pdf_source_verification:{status:'no_candidates',calls:0,checked:0,confirmed:0,pending:0,skipped:0,notes:[],skipped_reasons:[]},
            pdf_layout:{pages_checked:4,figures_extracted:2,figures_attached:2,unmatched_figures:0,joint_visual_calls:4,
                visual_calls:0,review_pages:0,warnings:[],notes:['本地线图检查不适用于该配图；保留首次识图结果。']}});
        return true;
    })()
    """)
    browser.command('scrollintoview', '#parsedSourceIntegrityReport')
    assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').className.includes('amber')")
    assert browser.evaluate("document.getElementById('parsedSourceIntegrityReport').textContent.includes('无需额外模型核验')")
    assert browser.evaluate("document.querySelectorAll('.card-source-review-confirm').length") == 0
    assert browser.evaluate('getCheckedUnsavedIndices()') == [0]
    assert browser.evaluate("!document.querySelector('#parsedSourceIntegrityReport details').open")
    _open_source_report(browser)
    browser.command('click', '#parsedSourceIntegrityReport details summary')
    assert browser.evaluate("document.querySelector('#parsedSourceIntegrityReport details').textContent.includes('已忽略卷首 3 条考试说明')")
    assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').textContent.includes('可能整题遗漏')")
    browser.command('screenshot', str(tmp_path / 'ignored-metadata-nonblocking.png'))


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_pdf_modes_and_review_report_in_real_browser(browser, tmp_path):
    _prepare_browser_pdf_review(browser)
    try:
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            if width == 375:
                assert browser.evaluate("document.getElementById('importInputPane').getBoundingClientRect().bottom <= document.getElementById('importReviewPane').getBoundingClientRect().top"), 'mobile configuration and results must not overlap'
            for strategy in ['native_preferred', 'force_ocr', 'layout_aware']:
                selector = f'input[name="pdfStrategy"][value="{strategy}"]'
                browser.command('scrollintoview', selector)
                browser.command('check', selector)
                assert browser.evaluate('selectedPdfStrategy()') == strategy
                assert browser.evaluate(r"""
                (() => {
                    const input = document.querySelector('input[name="pdfStrategy"]:checked');
                    const bounds = input.getBoundingClientRect();
                    return input.closest('label').contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
                })()
                """), 'selected option must not be covered by the results pane or actions'
            metrics = browser.evaluate(r"""
            [...document.querySelectorAll('input[name="pdfStrategy"]')].map(input => {
                const bounds = input.closest('label').getBoundingClientRect();
                return {width:bounds.width, left:bounds.left, right:bounds.right};
            })
            """)
            assert all(m['width'] > 70 and m['left'] >= 0 and m['right'] <= width + 1 for m in metrics), metrics
            browser.command('screenshot', str(tmp_path / f'pdf-modes-{width}.png'))

            browser.evaluate(r"""
            renderSourceIntegrityReport({pdf_layout:{pages_checked:1,figures_extracted:0,figures_attached:0,
                visual_calls:1,review_pages:1,warnings:['第 3 页未能确定配图范围，请核对原页。<img src=x onerror=window.pdfWarnInjected=true>']}});
            true
            """)
            browser.command('scrollintoview', '#parsedSourceIntegrityReport')
            assert browser.evaluate("document.getElementById('parsedSourceIntegrityReport').textContent.includes('第 3 页未能确定')")
            assert browser.evaluate("document.querySelectorAll('#parsedSourceIntegrityReport img').length") == 0
            assert browser.evaluate("typeof window.pdfWarnInjected === 'undefined'")
            browser.command('screenshot', str(tmp_path / f'pdf-warning-only-{width}.png'))

            browser.evaluate(r"""
            renderSourceIntegrityReport({pdf_layout:{pages_checked:1,figures_extracted:1,figures_attached:0,
                unmatched_figures:1,visual_calls:1,review_pages:1,warnings:['配图尚未归位，请展开核对。']},
                source_review_count:1,unmatched_source:[{reason:'原卷第 3 页图 1 未归位',
                source_excerpt:'![未归位图](' + window.__pdfReviewImage + ')'}]}); true
            """)
            _open_source_report(browser)
            browser.command('click', '#parsedSourceIntegrityReport details summary')
            browser.command('wait', '--fn', "document.querySelector('#parsedSourceIntegrityReport img').naturalWidth === 400")
            _open_source_report(browser)
            browser.command('scrollintoview', '#parsedSourceIntegrityReport img')
            browser.command('screenshot', str(tmp_path / f'pdf-unmatched-{width}.png'))
            browser.command('scrollintoview', '#parsed-card-0 details summary')
            if not browser.evaluate("document.querySelector('#parsed-card-0 details').open"):
                browser.command('click', '#parsed-card-0 details summary')
            browser.command('wait', '--fn', "document.querySelector('#parsed-card-0 details img').naturalWidth === 400")
            assert not browser.evaluate("document.querySelector('#parsed-card-0 .card-select-checkbox').disabled")
            browser.command('scrollintoview', '#parsed-card-0 details img')
            browser.command('screenshot', str(tmp_path / f'pdf-source-page-{width}.png'))
    finally:
        browser.command('set', 'viewport', '1600', '1100')
    print(f'PDF import visual QA: {tmp_path}')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_pdf_manual_crop_nonconsecutive_pages_in_real_browser(browser, tmp_path):
    _prepare_browser_pdf_review(browser)
    browser.evaluate(r"""
    (() => {
        window.pdfPageImages = [window.__pdfReviewImage, window.__pdfReviewImage];
        window.pdfPageNumbers = [3,7];
        window.currentPdfTaskId = 'browser-fixture';
        window.__pdfCropRequests = [];
        window.__originalPdfFetch = window.fetch;
        window.fetch = (input, options) => {
            if (input === '/api/ai/manual-crop-pdf') {
                window.__pdfCropRequests.push(JSON.parse(options.body));
                return Promise.resolve(new Response(JSON.stringify({status:'success', image_path:window.__pdfReviewImage}),
                    {status:200,headers:{'Content-Type':'application/json'}}));
            }
            return window.__originalPdfFetch(input, options);
        };
        return true;
    })()
    """)
    try:
        for local_index, original_index in [(0, 2), (1, 6)]:
            browser.evaluate('openPdfCropModalForQuestion(0); true')
            browser.command('wait', '--fn', "document.getElementById('pdfCropActiveImage').naturalWidth === 400 && !document.getElementById('pdfCropModal').classList.contains('opacity-0')")
            browser.command('click', f'#pdfPagesThumbnailsContainer > div:nth-child({local_index + 1})')
            assert f'原卷第 {original_index + 1} 页' in browser.evaluate("document.getElementById('pdfCropPageIndicator').textContent")
            assert browser.evaluate("[...document.querySelectorAll('#pdfPagesThumbnailsContainer > div > div')].map(n=>n.textContent)") == ['P3', 'P7']
            browser.settle()
            bounds = browser.evaluate("(() => {const r=document.getElementById('pdfCropImageContainer').getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
            browser.command('mouse', 'move', str(round(bounds['x'] + bounds['w'] * .15)), str(round(bounds['y'] + bounds['h'] * .15)))
            browser.command('mouse', 'down', 'left')
            browser.command('mouse', 'move', str(round(bounds['x'] + bounds['w'] * .85)), str(round(bounds['y'] + bounds['h'] * .85)))
            browser.command('mouse', 'up', 'left')
            browser.command('wait', '--fn', '!document.getElementById("pdfCropConfirmBtn").disabled')
            browser.command('screenshot', str(tmp_path / f'pdf-crop-page-{original_index + 1}.png'))
            browser.command('click', '#pdfCropConfirmBtn')
            browser.command('wait', '--fn', 'document.getElementById("pdfCropModal").classList.contains("hidden")')
            payload = browser.evaluate('window.__pdfCropRequests.at(-1)')
            assert payload['page_index'] == original_index, payload
            assert 0 <= payload['xmin'] < payload['xmax'] <= 100
            assert 0 <= payload['ymin'] < payload['ymax'] <= 100
    finally:
        browser.evaluate('window.fetch = window.__originalPdfFetch; closePdfCropModal(); true')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_pdf_notes_and_global_page_warning_in_real_browser(browser, tmp_path):
    _prepare_browser_pdf_review(browser)
    browser.evaluate(r"""
    (() => {
        replaceParsedQuestions([{content:'计算 $1+1$。', answer_markdown:'', question_type:'detailed_answer'}]);
        renderParsedQuestionsList(parsedQuestionsData);
        renderSourceIntegrityReport({source_review_count:0,pdf_layout:{pages_checked:1,figures_extracted:1,
            figures_attached:1,unmatched_figures:0,visual_calls:1,review_pages:0,warnings:[],notes:[
                '第 3 页：已自动补齐图形边缘。',
                '已忽略整页背景图。<img src=x onerror=window.pdfNoteInjected=true>'
            ]}});
        return true;
    })()
    """)
    try:
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            _open_source_report(browser)
            browser.command('scrollintoview', '#parsedSourceIntegrityReport .pdf-layout-notes summary')
            if browser.evaluate("document.querySelector('#parsedSourceIntegrityReport .pdf-layout-notes').open"):
                browser.command('click', '#parsedSourceIntegrityReport .pdf-layout-notes summary')
            assert browser.evaluate("!document.querySelector('#parsedSourceIntegrityReport .pdf-layout-notes').open && !document.querySelector('#parsedSourceIntegrityReport .pdf-layout-notes p').checkVisibility()")
            assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').className.includes('amber')")
            assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').textContent.includes('需人工核对')")
            assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').textContent.includes('逐题确认')")
            assert browser.evaluate('getCheckedUnsavedIndices()') == [0]
            assert browser.evaluate("document.querySelectorAll('#parsed-card-0 .card-source-review-confirm').length") == 0
            browser.command('screenshot', str(tmp_path / f'pdf-notes-collapsed-{width}.png'))
            browser.command('click', '#parsedSourceIntegrityReport .pdf-layout-notes summary')
            assert browser.evaluate("document.querySelector('#parsedSourceIntegrityReport .pdf-layout-notes').open && document.querySelector('#parsedSourceIntegrityReport .pdf-layout-notes p').checkVisibility()")
            assert browser.evaluate("document.querySelectorAll('#parsedSourceIntegrityReport .pdf-layout-notes img').length") == 0
            assert browser.evaluate("typeof window.pdfNoteInjected === 'undefined'")
            browser.command('screenshot', str(tmp_path / f'pdf-notes-expanded-{width}.png'))
        browser.evaluate(r"""
        renderSourceIntegrityReport({source_review_count:0,pdf_layout:{pages_checked:1,review_pages:1,
            warnings:['第 7 页有无法定位的候选区域，请核对原页。'],notes:['已自动补齐其他图形边缘。']},
            unmatched_source:[{reason:'第 7 页配图检测覆盖不完整',source_excerpt:'![原页](' + window.__pdfReviewImage + ')'}]}); true
        """)
        _open_source_report(browser)
        browser.command('click', '#parsedSourceIntegrityReport > details:not(.pdf-layout-notes) > summary')
        browser.command('wait', '--fn', "document.querySelector('#parsedSourceIntegrityReport img').naturalWidth === 400")
        assert browser.evaluate("!document.getElementById('parsedSourceIntegrityReport').textContent.includes('逐题确认')")
        assert browser.evaluate('getCheckedUnsavedIndices()') == [0]
        _open_source_report(browser)
        browser.command('scrollintoview', '#parsedSourceIntegrityReport img')
        browser.command('screenshot', str(tmp_path / 'pdf-global-page-warning.png'))
    finally:
        browser.command('set', 'viewport', '1600', '1100')
    print(f'PDF notes visual QA: {tmp_path}')


@pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1" or not os.environ.get("MATHBANK_TEST_PDF_REAL_RESULT"),
    reason="opt-in saved real PDF result; requires adjacent assets/ PNG copies",
)
def test_saved_real_pdf_option_order_and_badminton_position(browser, tmp_path):
    """Render a saved Wuhan result without contacting a model or saving questions."""
    task_path = Path(os.environ["MATHBANK_TEST_PDF_REAL_RESULT"]).expanduser().resolve()
    task_bytes = task_path.read_bytes()
    task = json.loads(task_bytes)
    questions = task.get("data", [])
    assert task.get("status") == "completed" and len(questions) >= 22
    choice_question, badminton_question = questions[2], questions[21]
    assert "奥运会" in choice_question.get("content", "")
    assert "羽毛球" in badminton_question.get("content", "")
    choices = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", choice_question["content"])
    badminton_images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", badminton_question["content"])
    assert len(choices) == 4 and len(set(choices)) == 4
    assert len(badminton_images) == 1
    first_part = re.search(r"[（(]\s*1\s*[）)]", badminton_question["content"])
    assert first_part and badminton_question["content"].index(badminton_images[0]) < first_part.start()
    for asset_url in set(re.findall(r"/static/(?:uploads|test_uploads)/(?:tmp/)?[A-Za-z0-9_.-]+\.png", task_bytes.decode())):
        source = task_path.parent / "assets" / Path(asset_url).name
        assert source.is_file(), f"Missing saved PDF fixture asset: {source}"
        target = browser.root / asset_url.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    before = browser.evaluate("fetch('/api/questions').then(r=>r.json()).then(rows=>rows.length)")
    browser.command('set', 'viewport', '1600', '1100')
    browser.evaluate("""
    (() => {
        selectWorkspace('import', '导入中心');
        window.pdfPageImages = %s;
        window.pdfPageNumbers = %s;
        replaceParsedQuestions(%s);
        renderParsedQuestionsList(parsedQuestionsData);
        document.getElementById('importPlaceholder').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
        return true;
    })()
    """ % (json.dumps(task.get("page_images", [])), json.dumps(task.get("page_numbers", [])), json.dumps(questions)))
    browser.command('scrollintoview', '#parsed-card-2 .card-content-preview')
    browser.command('wait', '--fn', "document.querySelectorAll('#parsed-card-2 .card-content-preview img').length === 4 && [...document.querySelectorAll('#parsed-card-2 .card-content-preview img')].every(img=>img.complete && img.naturalWidth>0)")
    browser.settle()
    choices_rendered = browser.evaluate(r"""
    [...document.querySelectorAll('#parsed-card-2 .card-content-preview .choices-item')].map(item=>({
        label:item.querySelector('.choices-label').textContent,
        images:[...item.querySelectorAll('img')].map(img=>img.getAttribute('src'))
    }))
    """)
    assert [item['label'] for item in choices_rendered] == ['A.', 'B.', 'C.', 'D.']
    assert [item['images'] for item in choices_rendered] == [[path] for path in choices]
    browser.command('scrollintoview', '#parsed-card-2')
    browser.evaluate("document.querySelector('#parsed-card-2 .card-content-preview').parentElement.scrollTop = 0; true")
    browser.command('screenshot', str(tmp_path / 'real-pdf-question-3-options.png'))
    browser.evaluate("(() => {const host=document.querySelector('#parsed-card-2 .card-content-preview').parentElement;host.scrollTop=host.scrollHeight;return true;})()")
    browser.command('screenshot', str(tmp_path / 'real-pdf-question-3-options-cd.png'))

    # Preview images load lazily; bring the distant card and its image into view
    # before waiting for pixel dimensions, as a user would by scrolling.
    browser.command('scrollintoview', '#parsed-card-21 .card-content-preview img')
    browser.command('wait', '--fn', "document.querySelectorAll('#parsed-card-21 .card-content-preview img').length === 1 && document.querySelector('#parsed-card-21 .card-content-preview img').naturalWidth > 0")
    browser.settle()
    position = browser.evaluate(r"""
    (() => {
        const preview = document.querySelector('#parsed-card-21 .card-content-preview');
        const image = preview.querySelector('img');
        const walker = document.createTreeWalker(preview, NodeFilter.SHOW_TEXT);
        let firstQuestion = null, node;
        while ((node=walker.nextNode())) {
            if (/[（(]\s*1\s*[）)]/.test(node.textContent)) { firstQuestion=node; break; }
        }
        if (!firstQuestion) throw Error('The first sub-question marker is absent from the rendered preview');
        const range = document.createRange();
        range.selectNodeContents(firstQuestion);
        const textBounds = range.getBoundingClientRect(), imageBounds = image.getBoundingClientRect();
        return {src:image.getAttribute('src'), width:image.naturalWidth, height:image.naturalHeight,
            imageBeforeFirstQuestion:!!(image.compareDocumentPosition(firstQuestion) & Node.DOCUMENT_POSITION_FOLLOWING),
            imageBottom:imageBounds.bottom, firstQuestionTop:textBounds.top};
    })()
    """)
    assert position['src'] == badminton_images[0]
    assert position['imageBeforeFirstQuestion'] and position['imageBottom'] <= position['firstQuestionTop'] + 1, position
    browser.command('scrollintoview', '#parsed-card-21')
    browser.evaluate(r"""
    (() => {
        const preview=document.querySelector('#parsed-card-21 .card-content-preview');
        const host=preview.parentElement, image=preview.querySelector('img');
        host.scrollTop += image.getBoundingClientRect().top - host.getBoundingClientRect().top - 12;
        return true;
    })()
    """)
    browser.command('screenshot', str(tmp_path / 'real-pdf-question-22-badminton.png'))
    after = browser.evaluate("fetch('/api/questions').then(r=>r.json()).then(rows=>rows.length)")
    assert before == after, "Rendering the saved result must not import questions"
    evidence = {"task": str(task_path), "task_sha256": hashlib.sha256(task_bytes).hexdigest(),
                "task_updated_at": task.get("updated_at"), "choice_order": choices_rendered,
                "badminton_position": position, "screenshots": str(tmp_path)}
    (tmp_path / "real-pdf-browser-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print('Real PDF browser evidence: ' + json.dumps(evidence, ensure_ascii=False))


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_pdf_card_action_labels_do_not_wrap_with_image_badges(browser, tmp_path):
    image_url = _prepare_browser_pdf_review(browser)
    paths = ['/static/uploads/figure_' + 'long_filename_' * 10 + str(index) + '.png' for index in range(8)]
    for path in paths:
        target = browser.root / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(browser.root / image_url.lstrip('/'), target)
    browser.evaluate(r"""
    (() => {
        window.pdfPageImages=[window.__pdfReviewImage];
        const paths=%s;
        replaceParsedQuestions([
            {content:'无配图徽标的题目。',answer_markdown:'',question_type:'detailed_answer',image_paths:[]},
            {content:'一张长文件名配图。',answer_markdown:'已有解析',question_type:'detailed_answer',image_paths:paths.slice(0,1)},
            {content:'八张长文件名配图。',answer_markdown:'',question_type:'detailed_answer',image_paths:paths,saved:true},
            {content:'手动截图后追加四张配图。',answer_markdown:'',question_type:'detailed_answer',image_paths:[]}
        ]);
        renderParsedQuestionsList(parsedQuestionsData);
        // The crop response appends badges without rebuilding the footer.
        const dynamicBadges=document.getElementById('card-images-badges-3');
        paths.slice(0,4).forEach(path=>appendSafeImageBadge(dynamicBadges,path));
        return true;
    })()
    """ % json.dumps(paths))
    results = []
    try:
        for width, height in [(1600, 1100), (1280, 1000), (375, 812), (320, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            for index in range(4):
                browser.command('scrollintoview', f'#parsed-card-{index} > div:last-child')
                metrics = browser.evaluate(r"""
                (() => {
                    const card=document.getElementById('parsed-card-%s');
                    const footer=card.lastElementChild, cardBounds=card.getBoundingClientRect();
                    const badges=card.querySelector('[id^="card-images-badges-"]');
                    const badgeBounds=badges.getBoundingClientRect();
                    const buttons=[...footer.querySelectorAll('button')].map(button=>{
                        const label=button.querySelector('span'), range=document.createRange();
                        range.selectNodeContents(label);
                        const box=button.getBoundingClientRect();
                        return {text:label.textContent, lines:range.getClientRects().length,
                            x:box.x,y:box.y,right:box.right,height:box.height};
                    });
                    return {width:innerWidth,cardWidth:cardBounds.width,left:cardBounds.left,right:cardBounds.right,
                        badgeCount:badges.children.length,badgeHeight:badgeBounds.height,badgeBottom:badgeBounds.bottom,
                        badgeOverflow:badges.scrollWidth>badges.clientWidth+1,
                        names:[...badges.querySelectorAll('span')].map(label=>({
                            width:label.getBoundingClientRect().width,fullName:label.title===label.textContent,
                            clipped:label.scrollWidth>label.clientWidth,
                            ellipsis:getComputedStyle(label).textOverflow==='ellipsis'})),buttons};
                })()
                """ % index)
                results.append(metrics)
                browser.command('screenshot', str(tmp_path / f'pdf-card-actions-{width}-{index}.png'))
                assert metrics['badgeCount'] == [0,1,8,4][index]
                assert metrics['badgeHeight'] <= 65 and not metrics['badgeOverflow'], metrics
                if metrics['badgeCount']:
                    assert metrics['badgeBottom'] < min(button['y'] for button in metrics['buttons']), metrics
                    assert all(name['width'] <= 100 and name['fullName'] and name['clipped'] and name['ellipsis'] for name in metrics['names']), metrics
                assert len(metrics['buttons']) == 3
                assert all(button['lines'] == 1 for button in metrics['buttons']), metrics
                heights = [button['height'] for button in metrics['buttons']]
                assert max(heights) - min(heights) <= 1, metrics
                assert all(button['x'] >= metrics['left'] - 1 and button['right'] <= metrics['right'] + 1 for button in metrics['buttons']), metrics
                if width >= 375:
                    assert max(button['y'] for button in metrics['buttons']) - min(button['y'] for button in metrics['buttons']) <= 1, metrics
    finally:
        browser.command('set', 'viewport', '1600', '1100')
    (tmp_path / 'pdf-card-actions-metrics.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f'PDF card actions visual QA: {tmp_path}')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_pdf_review_report_locates_questions_and_unassigned_page(browser, tmp_path):
    _prepare_browser_pdf_review(browser)
    browser.evaluate(r"""
    (() => {
        replaceParsedQuestions([
            {content:'原卷第21题：请根据图形判断结论。',answer_markdown:'',question_type:'detailed_answer',
                source_review:{required:true,reasons:['请核对两幅配图是否完整。'],source_excerpt:'![配图]('+window.__pdfReviewImage+')'}},
            {content:'请解方程 $x^2-1=0$。',answer_markdown:'',question_type:'detailed_answer',
                source_review:{required:true,reasons:['本题公式需要核对。'],source_excerpt:'原式 $x^2+1=0$'}}
        ]);
        renderParsedQuestionsList(parsedQuestionsData);
        renderSourceIntegrityReport({source_review_count:2,pdf_layout:{pages_checked:2,figures_extracted:1,
            figures_attached:1,unmatched_figures:0,joint_visual_calls:2,visual_calls:0,review_pages:1,
            warnings:['p6_raster_001 only 75% covered']},pdf_review_items:[
                {question_index:0,source_number:21,source_pages:[6],reasons:['请核对两幅配图是否完整。']},
                {question_index:1,source_number:null,source_pages:[2],reasons:['本题公式与原文不一致，请检查正负号。']}
            ],unmatched_source:[
                {scope:'question',page_index:5,affected_questions:[{question_index:0,source_number:21}],
                    reason:'配图边缘需要核对。',source_excerpt:'![配图]('+window.__pdfReviewImage+')'},
                {scope:'page_unassigned',page_index:5,affected_questions:[],
                    reason:'另有一处插图尚未找到对应题目。',source_excerpt:'![原页]('+window.__pdfReviewImage+')'}
            ]});
        return true;
    })()
    """)
    try:
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            _open_source_report(browser)
            browser.command('scrollintoview', '#parsedSourceIntegrityReport .pdf-review-question-list')
            texts = browser.evaluate("[...document.querySelectorAll('#parsedSourceIntegrityReport .pdf-review-question-list button')].map(button=>button.textContent)")
            assert texts == ['第21题（原卷第6页）', '第2张题卡（原卷第2页）']
            assert browser.evaluate("!/[a-z]6_raster_001|75%/.test(document.getElementById('parsedSourceIntegrityReport').textContent)")
            assert browser.evaluate("[...document.querySelectorAll('#parsedSourceIntegrityReport details > summary')].map(summary=>summary.textContent)") == [
                '第21题（原卷第6页）：配图边缘需要核对。',
                '原卷第6页，尚无法确定题号：另有一处插图尚未找到对应题目。',
            ]
            browser.command('screenshot', str(tmp_path / f'pdf-review-question-list-{width}.png'))
            for index in (0, 1):
                link = f'#parsedSourceIntegrityReport .pdf-review-question-list button[data-question-index="{index}"]'
                browser.command('scrollintoview', link)
                browser.command('click', link)
                browser.command('wait', '--fn', f'document.activeElement.id === "parsed-card-{index}"')
                browser.settle()
                assert browser.evaluate(f'parsedQuestionNeedsSourceReview({index})'), 'jump links must not confirm a question'
                assert browser.evaluate(f'(() => {{const rect=document.getElementById("parsed-card-{index}").getBoundingClientRect();return rect.bottom>120 && rect.top<innerHeight-60;}})()')
            browser.command('screenshot', str(tmp_path / f'pdf-review-question-jump-{width}.png'))
        _open_source_report(browser)
        browser.command('scrollintoview', '#parsedSourceIntegrityReport details:last-child > summary')
        _open_source_report(browser)
        browser.command('click', '#parsedSourceIntegrityReport details:last-child > summary')
        browser.command('wait', '--fn', "document.querySelector('#parsedSourceIntegrityReport details:last-child img').naturalWidth === 400")
        _open_source_report(browser)
        browser.command('scrollintoview', '#parsedSourceIntegrityReport details:last-child img')
        browser.command('screenshot', str(tmp_path / 'pdf-review-unassigned-page.png'))
    finally:
        browser.command('set', 'viewport', '1600', '1100')
    print(f'PDF question review visual QA: {tmp_path}')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_pdf_source_verification_control_and_revocable_result(browser, tmp_path):
    _prepare_browser_pdf_review(browser)
    browser.evaluate(r"""
    (() => {
        window.__pdfVerifySideEffects=[];
        window.__pdfVerifyOriginalFetch=window.fetch;
        window.fetch=(input, options={})=>{
            const url=typeof input==='string' ? input : input.url;
            if (url.includes('/api/ai/') || url.includes('/api/upload/pdf-task') ||
                (String(options.method || 'GET').toUpperCase() !== 'GET' && /\/api\/questions(?:\/|$)/.test(url))) {
                window.__pdfVerifySideEffects.push(url);
                return Promise.reject(Error('Verification UI tests must not call models or save questions'));
            }
            return window.__pdfVerifyOriginalFetch(input,options);
        };
        return true;
    })()
    """)
    browser.command('check', 'input[name="pdfStrategy"][value="layout_aware"]')
    browser.command('check', '#pdfVerifySuspicions')
    assert browser.evaluate('selectedPdfSourceVerification()')
    for strategy in ('native_preferred', 'force_ocr'):
        browser.command('check', f'input[name="pdfStrategy"][value="{strategy}"]')
        assert browser.evaluate("document.getElementById('pdfVerifySuspicions').disabled")
        assert browser.evaluate('!selectedPdfSourceVerification()')
        assert browser.evaluate("document.getElementById('pdfVerifySuspicionsNote').textContent.includes('仅智能图文提取模式可用')")
    browser.command('check', 'input[name="pdfStrategy"][value="layout_aware"]')
    assert browser.evaluate("!document.getElementById('pdfVerifySuspicions').disabled && selectedPdfSourceVerification()")
    browser.command('scrollintoview', '#pdfVerifySuspicions')
    browser.command('screenshot', str(tmp_path / 'pdf-auto-verification-option.png'))
    browser.evaluate(r"""
    (() => {
        replaceParsedQuestions([
            {content:'计算 $1+1$。',answer_markdown:'',question_type:'detailed_answer',source_review:{required:false,
                verified_by:'vision',reasons:['原文公式位置待核对。'],source_excerpt:'计算 $1+1$。',
                verification:{evidence:'原页与题卡的公式和条件一致。<img src=x onerror=window.verifyInjected=true>'}}},
            {content:'求 $x$。',answer_markdown:'',question_type:'detailed_answer',source_review:{required:true,
                reasons:['仍无法确定原文条件。'],source_excerpt:'求 $x+1$。'}}
        ]);
        renderParsedQuestionsList(parsedQuestionsData);
        renderSourceIntegrityReport({source_review_count:1,pdf_source_verification:{status:'completed',calls:1,
            checked:2,confirmed:1,pending:1,skipped:0,notes:[]}});
        return true;
    })()
    """)
    try:
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.settle()
            browser.command('scrollintoview', '#parsed-card-0 .card-source-review-status')
            assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-status').textContent") == '查看原页自动核验记录'
            assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-confirm') === null")
            assert browser.evaluate('getCheckedUnsavedIndices()') == [0, 1]
            browser.command('screenshot', str(tmp_path / f'pdf-auto-verified-{width}.png'))
        browser.command('click', '#parsed-card-0 details summary')
        assert browser.evaluate("document.querySelector('#parsed-card-0 details').textContent.includes('模型核验记录')")
        assert browser.evaluate("typeof window.verifyInjected === 'undefined'")
        browser.command('fill', '#parsed-card-0 .card-content-textarea', '计算 $1-1$。')
        browser.command('fill', '#parsed-card-0 .card-content-textarea', '计算 $1+1$。')
        assert browser.evaluate('parsedQuestionNeedsSourceReview(0)')
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-confirm') === null")
        assert not browser.evaluate("document.querySelector('#parsed-card-0 .card-select-checkbox').disabled")
        browser.command('scrollintoview', '#parsed-card-0 .card-source-review-status')
        browser.command('screenshot', str(tmp_path / 'pdf-auto-verification-revoked.png'))
        assert browser.evaluate('parsedQuestionNeedsSourceReview(0)')
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-status').textContent") == '查看原文与提取说明（可选）'
        assert browser.evaluate("!document.querySelector('#parsed-card-0 .card-select-checkbox').disabled")
        browser.command('check', '#parsed-card-0 .card-select-checkbox')
        assert browser.evaluate('getCheckedUnsavedIndices()') == [0, 1]
        assert browser.evaluate('window.__pdfVerifySideEffects') == []
    finally:
        browser.evaluate('window.fetch=window.__pdfVerifyOriginalFetch; true')
        browser.command('set', 'viewport', '1600', '1100')
    print(f'PDF automatic source verification visual QA: {tmp_path}')


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in actual browser regression")
def test_docx_source_verification_upload_poll_and_revocable_result(browser, tmp_path):
    """Exercise real file selection and the shipped poller with local fake replies."""
    word_file = tmp_path / '原文核验界面测试.docx'
    pdf_file = tmp_path / '另一份试卷.pdf'
    word_file.write_bytes(b'UI-only DOCX fixture, never sent to a parser')
    pdf_file.write_bytes(b'UI-only PDF fixture, never sent to a parser')
    browser.evaluate(r"""
    (() => {
        selectWorkspace('import', '导入中心');
        replaceParsedQuestions([]);
        updateImportSourceView('empty');
        window.currentDocxFile=null;
        window.currentPdfFile=null;
        window.__docxUiNetwork={uploads:[],polls:0,blocked:[]};
        window.__docxUiOriginalFetch=window.fetch;
        const confirmed={content:'计算 $1+1$。',answer_markdown:'2',question_type:'detailed_answer',
            source_review:{required:false,verified_by:'vision',source_number:1,
                reasons:['题干文字或公式位置与原文未能完整对应，请对照原文核对。'],source_excerpt:'1. 计算 $1+1$。',
                verification:{document_type:'docx',evidence:'原文与题卡的公式及条件一致。'}}};
        const pending={content:'求函数值。[公式结构待核对]',answer_markdown:'',question_type:'detailed_answer',
            source_review:{required:true,source_number:2,source_excerpt:'2. 求函数值。[公式结构待核对]',
                reasons:['原 Word 中有未能可靠提取的公式结构，请对照原文件核对。']}};
        window.__docxUiCompleted={status:'completed',document_type:'docx',log:'拆分完成，请核对原文提示。',progress:100,
            data:[confirmed,pending],generate_answers:false,diagnostics:{omml_converted:4,images_extracted:0,
                review_required:1,source_review_count:1,docx_source_verification:{status:'completed',calls:1,
                    checked:1,confirmed:1,pending:1,skipped:1,notes:[],skipped_reasons:[{question_index:1,
                        source_number:2,source_pages:[],code:'other_review_reason',reason:'仍有未解析的公式结构，保留人工核对。'}]}}};
        const reply=value=>Promise.resolve(new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}}));
        window.fetch=(input,options={})=>{
            const url=typeof input==='string' ? input : input.url;
            if (url.endsWith('/api/upload/docx-task')) {
                window.__docxUiNetwork.uploads.push({verify:options.body.get('docx_verify_suspicions'),
                    answers:options.body.get('generate_answers'),file:options.body.get('file').name});
                return reply({status:'success',task_id:'docx-ui-mock'});
            }
            if (url.endsWith('/api/tasks/docx-ui-mock/status')) {
                window.__docxUiNetwork.polls++;
                return reply(window.__docxUiNetwork.polls===1
                    ? {status:'source_verification',document_type:'docx',log:'正在核验 Word 原文疑点...',progress:90}
                    : structuredClone(window.__docxUiCompleted));
            }
            if (url.includes('/api/ai/') || url.includes('/api/upload/') ||
                (String(options.method || 'GET').toUpperCase()!=='GET' && /\/api\/questions(?:\/|$)/.test(url))) {
                window.__docxUiNetwork.blocked.push(url);
                return Promise.reject(Error('No model calls or question writes in Word UI regression'));
            }
            return window.__docxUiOriginalFetch(input,options);
        };
        return true;
    })()
    """)
    try:
        assert not browser.evaluate("document.getElementById('docxVerificationContainer').checkVisibility()")
        browser.command('upload', '#texFileInput', str(pdf_file))
        assert not browser.evaluate("document.getElementById('docxVerificationContainer').checkVisibility()")
        browser.command('upload', '#texFileInput', str(word_file))
        browser.command('wait', '--fn', "document.getElementById('docxVerificationContainer').checkVisibility()")
        browser.command('fill', '#importPaperTitle', 'Word 原文核验示例')
        assert browser.evaluate("!document.getElementById('docxVerifySuspicions').checked && !selectedDocxSourceVerification()")
        browser.command('check', '#docxVerifySuspicions')
        assert browser.evaluate('window.__docxUiNetwork.uploads') == [], 'selecting a file starts no task'
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.command('scrollintoview', '#docxVerifySuspicions')
            browser.settle()
            assert browser.evaluate("document.getElementById('docxVerifySuspicions').checkVisibility()")
            browser.command('screenshot', str(tmp_path / f'docx-verification-option-{width}.png'))
        browser.command('set', 'viewport', '1600', '1100')
        browser.command('uncheck', '#importGenerateAnswers')
        browser.command('scrollintoview', '#runParseBtn')
        browser.command('click', '#runParseBtn')
        browser.command('wait', '--fn', "document.getElementById('importSubLoadingText').textContent.includes('核验 Word 原文')")
        browser.command('wait', '--fn', "window.__docxUiNetwork.polls===2 && parsedQuestionsData.length===2 && parsedQuestionsData[0].answer_markdown==='2' && !!document.querySelector('#parsed-card-1 .card-source-review-status')")
        browser.settle()
        assert browser.evaluate('window.__docxUiNetwork.uploads') == [
            {'verify': 'true', 'answers': 'false', 'file': word_file.name}]
        assert browser.evaluate('window.__docxUiNetwork.polls') == 2
        summary = browser.evaluate("document.querySelector('#parsedSourceIntegrityReport .pdf-source-verification-summary').textContent")
        assert 'AI 核验 1 题，已确认 1 题，保留提取说明 1 题' in summary
        assert browser.evaluate("document.querySelector('#parsedSourceIntegrityReport .pdf-review-question-link').textContent") == '第2题'
        assert browser.evaluate("document.getElementById('importLogsConsole').textContent.includes('提取阶段标记 1 处疑点')")
        assert not browser.evaluate("document.querySelector('#parsed-card-1 .card-source-review-panel').className.includes('amber')")
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.command('scrollintoview', '#parsedSourceIntegrityReport')
            browser.settle()
            assert browser.evaluate('getCheckedUnsavedIndices()') == [0, 1]
            assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-status').textContent") == '查看原文自动核验记录'
            assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-confirm') === null")
            assert not browser.evaluate("document.querySelector('#parsed-card-1 .card-select-checkbox').disabled")
            browser.command('screenshot', str(tmp_path / f'docx-verification-result-{width}.png'))
            browser.command('scrollintoview', '#parsed-card-0 .card-source-review-status')
            browser.settle()
            assert browser.evaluate("(() => {const r=document.querySelector('#parsed-card-0 .card-source-review-status').getBoundingClientRect();return r.bottom>120 && r.top<innerHeight-80 && r.left>=0 && r.right<=innerWidth;})()")
            browser.command('screenshot', str(tmp_path / f'docx-verification-confirmed-card-{width}.png'))
        browser.command('fill', '#parsed-card-0 .card-content-textarea', '计算 $1-1$。')
        browser.command('fill', '#parsed-card-0 .card-content-textarea', '计算 $1+1$。')
        assert browser.evaluate('parsedQuestionNeedsSourceReview(0)'), 'restoring text does not restore AI approval'
        assert browser.evaluate("document.querySelector('#parsed-card-0 .card-source-review-confirm') === null")
        assert not browser.evaluate("document.querySelector('#parsed-card-0 .card-select-checkbox').disabled")
        complete_import_fixture_categories(browser, 0)
        assert browser.evaluate('validateParsedQuestionBeforeImport(0,{notify:false,focus:false})') is True
        assert browser.evaluate('window.__docxUiNetwork.blocked') == []
        for width, height in [(1600, 1100), (375, 812)]:
            browser.command('set', 'viewport', str(width), str(height))
            browser.command('scrollintoview', '#parsed-card-0 .card-source-review-panel')
            browser.settle()
            browser.command('screenshot', str(tmp_path / f'docx-verification-revoked-{width}.png'))
        assert browser.evaluate('parsedQuestionNeedsSourceReview(0)')
        browser.command('check', '#parsed-card-0 .card-select-checkbox')
        assert browser.evaluate('getCheckedUnsavedIndices()') == [0, 1]
        assert browser.evaluate('parsedQuestionNeedsSourceReview(1)'), 'original extraction problem remains pending'
        assert browser.evaluate('window.__docxUiNetwork.blocked') == []
        (tmp_path / 'docx-ui-network.json').write_text(json.dumps(browser.evaluate('window.__docxUiNetwork'), ensure_ascii=False, indent=2))
    finally:
        browser.evaluate('stopCurrentDocumentPoll(); window.fetch=window.__docxUiOriginalFetch; true')
        browser.command('set', 'viewport', '1600', '1100')
    print(f'Word automatic source verification visual QA: {tmp_path}')
