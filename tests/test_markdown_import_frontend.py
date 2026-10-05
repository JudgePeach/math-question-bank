"""Exercise Markdown selection, format routing, and delayed local reads."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_preview_browser import browser


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def markdown_import_script():
    source = (ROOT / "static/js/import.js").read_text(encoding="utf-8")

    def section(start, end):
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    shipped = "\n".join([
        section("window.currentImportSourceFormat = 'tex'", "function openImportModal()"),
        section("function setupImportFileHandlers()", "function demoteCurrentImportLog"),
        section("function runAIPaperParse()", "let documentImportTaskGeneration = 0"),
        section("function clearAllImportInputs()", "function resetImportState("),
        section("function extractTitleFromImportSource(", "function extractTitleFromLatex("),
    ])
    setup = r"""
const assert = require('node:assert/strict');
const elements = new Map();
class Element extends EventTarget {
  constructor() {
    super(); this.value=''; this.disabled=false; this.hidden=false; this.open=false;
    this.textContent=''; this.innerHTML=''; this.checked=false; this.style={}; this.files=[];
    this.classList={add(){},remove(){},toggle(){}};
  }
  click() { this.clicked=true; }
  querySelector() { return null; }
  focus() {}
}
const document={getElementById:id=>elements.get(id)||null};
for (const id of ['texDropzone','texFileInput','texFileName','texFileIcon','importLatexContent',
  'importPaperTitle','importSourceFormat','importSourceLabel','importImagesLabel','importImagesHint',
  'importMarkdownNote','importSourceDetails','importFileSummary','texImagesSection','runParseBtn',
  'pdfPageRangeContainer','pdfPageRange','docxVerificationContainer','importPlaceholder',
  'parsedQuestionsWrapper','importLoadingState','importLogsConsole','importGenerateAnswers',
  'importLoadingText','importProgressBarContainer']) elements.set(id,new Element());
const el=id=>document.getElementById(id);
const window={currentPdfFile:null,currentDocxFile:null,currentTexReadToken:null};
const localStorage={getItem:()=>''};
const toasts=[],logs=[],requests=[];
const showToast=message=>toasts.push(message);
const appendImportLog=message=>logs.push(message);
const setTimeout=()=>1,clearTimeout=()=>{};
const fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));
let batchSelectedImages=[],parsedQuestionsData=[],parsedQuestionsGeneration=0,generation=0;
const systemPreferParseModel='MODEL/model';
const beginDocumentImportTask=()=>++generation;
const isCurrentDocumentImportTask=value=>value===generation;
const replaceParsedQuestions=questions=>{parsedQuestionsData=questions;parsedQuestionsGeneration++;};
const renderParsedQuestionsList=()=>{},renderSourceIntegrityReport=()=>{},appendSourceIntegrityLog=()=>{};
const blockImportResetWhileSaving=()=>false,renderImagesList=()=>{};
const extractTitleFromLatex=source=>(source.match(/\\title\{([^}]+)\}/)||[,''])[1];
const confirm=()=>true;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function file(name,text='题 1. 计算 $x+1$，比例 50%。') {
  const value=new Blob([text]); Object.defineProperty(value,'name',{value:name}); return value;
}
function select(value) { el('texFileInput').files=[value];el('texFileInput').dispatchEvent(new Event('change')); }
function format(value) { el('importSourceFormat').value=value;el('importSourceFormat').dispatchEvent(new Event('change')); }
function success(request,source,title='') {
  request.resolve({ok:true,json:async()=>({status:'success',source,title,diagnostics:{warnings:[]}})});
}
"""
    checks = r"""
setupImportFileHandlers();
const scenarios={
  async markdown_title_fences_use_full_closing_boundary() {
    const tick=String.fromCharCode(96);
    for(const marker of [tick,'~']) {
      const opening=marker.repeat(4),other=(marker===tick?'~':tick).repeat(4);
      const source=[opening+'markdown','# 代码内标题',marker.repeat(3),
        '# 短围栏没有关闭',other,'# 另一字符没有关闭',
        opening+'not-closing','# 带内容的围栏没有关闭',
        '   '+marker.repeat(5)+' \t','# **真正标题**'].join('\r\n');
      assert.equal(extractTitleFromImportSource(source,'markdown'),'真正标题');
      assert.equal(extractTitleFromImportSource(opening+'\n# 代码内标题\n'+marker.repeat(3)+'\n# 仍在代码中','markdown'),'');
      assert.equal(extractTitleFromImportSource(marker.repeat(3)+'md\n# 示例\n'+marker.repeat(4)+'\n# 真标题','markdown'),'真标题');
    }
    const source=tick.repeat(3)+'md\n# 示例\n'+tick.repeat(3)+'info\n# 仍在代码中';
    assert.equal(extractTitleFromImportSource(source,'markdown'),'');
    assert.equal(extractTitleFromImportSource('\\title{旧TeX标题}','tex'),'旧TeX标题');
  },
  async uppercase_selection_and_drop() {
    select(file('paper.MD','# **测试卷**\n题 1. 比例为 50%。'));
    await flush();
    assert.equal(window.currentImportSourceFormat,'markdown');
    assert.equal(el('importSourceFormat').value,'markdown');
    assert.equal(requests[0].url,'/api/upload/markdown-source');
    assert.equal(el('importPaperTitle').value,'测试卷');
    assert.ok(el('importLatexContent').value.includes('50%'));
    assert.equal(el('importSourceDetails').open,true);
    assert.equal(el('texImagesSection').hidden,false);
    assert.equal(el('importMarkdownNote').hidden,false);
    success(requests[0],el('importLatexContent').value);await flush();
    const drop=new Event('drop');drop.dataTransfer={files:[file('dropped.MD','题 2. 比例 75%。')]};
    el('texDropzone').dispatchEvent(drop);await flush();
    assert.equal(requests[1].url,'/api/upload/markdown-source');
    success(requests[1],'题 2. 比例 75%。');await flush();
    assert.equal(el('importLatexContent').value,'题 2. 比例 75%。');
  },
  async newer_read_owns_button_and_source() {
    select(file('old.md','原MD'));await flush();
    select(file('new.tex','新TeX'));await flush();
    assert.equal(el('runParseBtn').disabled,true);
    success(requests[0],'迟到MD');await flush();
    assert.equal(el('runParseBtn').disabled,true,'old finally cannot enable newer read');
    assert.equal(el('importLatexContent').value,'新TeX');
    success(requests[1],'新TeX');await flush();
    assert.equal(el('runParseBtn').disabled,false);
    assert.equal(window.currentImportSourceFormat,'tex');
  },
  async binary_selection_rejects_old_text() {
    for (const suffix of ['pdf','docx']) {
      select(file('old.md','原MD'));await flush();const pending=requests.at(-1);
      select(file('new.'+suffix,'binary'));
      const visible=el('importLatexContent').value;
      success(pending,'迟到MD');await flush();
      assert.equal(el('importLatexContent').value,visible);
      assert.equal(el('importLatexContent').disabled,true);
      assert.equal(el('importSourceDetails').hidden,true);
      assert.equal(el('texImagesSection').hidden,true);
      assert.equal(el('runParseBtn').disabled,false);
    }
  },
  async reset_rejects_pending_local_decode() {
    let resolveBuffer;const old=file('pending.md');
    old.arrayBuffer=()=>new Promise(resolve=>{resolveBuffer=resolve;});
    select(old);clearAllImportInputs();
    resolveBuffer(new TextEncoder().encode('迟到Markdown').buffer);await flush();
    assert.equal(requests.length,0);
    assert.equal(el('importLatexContent').value,'');
    assert.equal(window.currentImportSourceFormat,'tex');
    assert.equal(el('importSourceFormat').value,'tex');
    assert.equal(el('importMarkdownNote').hidden,true);
    assert.equal(el('runParseBtn').disabled,false);
  },
  async format_change_rejects_pending_local_decode() {
    let resolveBuffer;const old=file('pending.tex');
    old.arrayBuffer=()=>new Promise(resolve=>{resolveBuffer=resolve;});
    select(old);format('markdown');
    el('importLatexContent').value='手动Markdown 25%';
    resolveBuffer(new TextEncoder().encode('迟到TeX').buffer);await flush();
    assert.equal(requests.length,0);
    assert.equal(el('importLatexContent').value,'手动Markdown 25%');
    assert.equal(el('importLatexContent').disabled,false);
    assert.equal(window.currentImportSourceFormat,'markdown');
  },
  async manual_edit_rejects_precheck() {
    select(file('paper.md','原文'));await flush();
    el('importLatexContent').value='用户编辑 60%';
    el('importLatexContent').dispatchEvent(new Event('input'));
    success(requests[0],'后端迟到原文','后端标题');await flush();
    assert.equal(el('importLatexContent').value,'用户编辑 60%');
    assert.equal(el('importPaperTitle').value,'paper');
    assert.equal(el('runParseBtn').disabled,false);
  },
  async markdown_fallback_and_parse_format() {
    select(file('paper.md','# 标题\n题 1. 比例 50%。'));await flush();
    requests[0].reject(Error('precheck unavailable'));await flush();
    assert.equal(window.currentTexDiagnostics.local_read_fallback,true);
    assert.ok(window.currentTexDiagnostics.warnings[0].includes('Markdown'));
    runAIPaperParse();await flush();
    const parse=requests.at(-1);assert.equal(parse.url,'/api/ai/parse-paper');
    assert.equal(parse.options.body.get('source_format'),'markdown');
    assert.ok(parse.options.body.get('latex_content').includes('50%'));
    assert.equal(parse.options.body.get('generate_answers'),'false');
    parse.resolve({ok:true,json:async()=>({status:'success',source_format:'markdown',
      questions:[{content:'比例 50%。',answer_markdown:'原答案'}],
      tex_diagnostics:{question_count_estimate:1,warnings:['MD 格式尚不完善，请谨慎使用']}})});
    await flush();
    assert.equal(parsedQuestionsData[0].answer_markdown,'原答案');
    assert.ok(logs.some(message=>message.startsWith('Markdown 题数核对')));
    assert.ok(logs.some(message=>message.startsWith('Markdown 预检')));
    clearAllImportInputs();el('importLatexContent').value='TeX内容';el('importPaperTitle').value='TeX卷';
    runAIPaperParse();await flush();
    assert.equal(requests.at(-1).options.body.get('source_format'),'tex');
    requests.at(-1).resolve({ok:true,json:async()=>({status:'success',questions:[]})});await flush();
  },
};
scenarios[process.argv[1]]().catch(error=>{console.error(error);process.exitCode=1;});
"""
    return setup + shipped + checks


@pytest.mark.parametrize("scenario", [
    "markdown_title_fences_use_full_closing_boundary",
    "uppercase_selection_and_drop", "newer_read_owns_button_and_source",
    "binary_selection_rejects_old_text", "reset_rejects_pending_local_decode",
    "format_change_rejects_pending_local_decode", "manual_edit_rejects_precheck",
    "markdown_fallback_and_parse_format",
])
def test_markdown_import_runtime(markdown_import_script, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the optional JS regression harness")
    result = subprocess.run(
        [node, "-e", markdown_import_script, scenario],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_BROWSER") != "1", reason="opt-in isolated browser validation")
def test_markdown_upload_and_pasted_format_in_browser(browser, tmp_path):
    from PIL import Image
    from mathbank.markdown_helper import prepare_markdown_question_for_storage

    literal_path = "/static/uploads/tmp/literal-fence.png"
    examples = [
        f"    ~~~~\n    ![源码图]({literal_path})\n    $x$",
        f"<pre>~~~~\n![源码图]({literal_path})\n$x$</pre>",
        f"<code>例子 ``` ![源码图]({literal_path}) $x$</code>",
    ]
    stored_examples = []
    for example in examples:
        question = {"content": example + "\n\n求 $y$。\n\n![真图](__MARKDOWN_IMAGE_FIXTURE__)", "answer_markdown": ""}
        prepare_markdown_question_for_storage(question, {})
        stored_examples.append(question["content"])

    paper = tmp_path / "paper.MD"
    original = (
        "# Markdown 测试卷\n\n"
        "题 1. 已知 $x=1$，百分比为 50%，求 $x+1$。\n\n![图示](figure.png)\n\n【答案】$2$。\n\n"
        "题 2. 已知 $a=2$，计算 $a^2$。\n\n【答案】$4$。"
    )
    image = tmp_path / "figure.png"
    Image.new("RGB", (24, 18), "navy").save(image)
    paper.write_text(original, encoding="utf-8")
    browser.evaluate("selectWorkspace('import','导入中心'); clearAllImportInputs(); true")
    browser.command("upload", "#texFileInput", str(paper))
    browser.command("wait", "--fn", "window.currentImportSourceFormat==='markdown' && !window.currentTexReadToken")
    state = browser.evaluate("""({
      source:document.getElementById('importLatexContent').value,
      format:document.getElementById('importSourceFormat').value,
      open:document.getElementById('importSourceDetails').open,
      note:document.getElementById('importMarkdownNote').hidden,
      label:document.getElementById('importImagesLabel').textContent,
      warnings:window.currentTexDiagnostics.warnings
    })""")
    assert state["source"] == original
    assert state["format"] == "markdown" and state["open"] is True
    assert state["note"] is False and state["label"].startswith("Markdown")
    assert any("尚不完善" in warning for warning in state["warnings"])
    browser.command("upload", "#imagesFileInput", str(image))

    browser.evaluate(r"""(()=>{
      window.__markdownOriginalFetch=window.fetch;
      window.fetch=(url,options={})=>{
        if(String(url)==='/api/ai/parse-paper') {
          window.__markdownParse={format:options.body.get('source_format'),source:options.body.get('latex_content')};
          const mapping=JSON.parse(options.body.get('image_mapping_json'));
          const path=mapping['figure.png'];
          if(!path)throw Error('Markdown 配套图片未上传');
          window.__markdownImagePath=path;
          return Promise.resolve(new Response(JSON.stringify({status:'success',source_format:'markdown',
            questions:[
              {content:'已知 $x=1$，百分比为 50%，求 $x+1$。\n\n![图示]('+path+')',answer_markdown:'$2$。',
                question_type:'detailed_answer',difficulty:'normal',image_paths:[path],source:'Markdown 测试卷'},
              {content:'已知 $a=2$，计算 $a^2$。',answer_markdown:'$4$。',
                question_type:'detailed_answer',difficulty:'normal',image_paths:[],source:'Markdown 测试卷'}
            ],tex_diagnostics:{question_count_estimate:2,question_count_actual:2,
              warnings:['MD 格式尚不完善，请谨慎使用']}}),{status:200}));
        }
        return window.__markdownOriginalFetch(url,options);
      };
      document.getElementById('importGenerateAnswers').checked=false;
      runAIPaperParse();return true;
    })()""")
    try:
        browser.command("wait", "--fn", "!!window.__markdownParse && !document.getElementById('runParseBtn').disabled")
        browser.command("wait", "--fn", "!!document.querySelector('#parsed-card-0 .card-content-preview img') && document.querySelector('#parsed-card-0 .card-content-preview img').naturalWidth>0")
        assert browser.evaluate("window.__markdownParse") == {"format": "markdown", "source": original}
        assert browser.evaluate("document.getElementById('importLogsConsole').textContent.includes('Markdown 预检')")
        previews = browser.evaluate(r"""[0,1].map(index=>{
          const card=document.getElementById('parsed-card-'+index);
          return {content:card.querySelector('.card-content-textarea').value,
            answer:card.querySelector('.card-answer-textarea').value,
            contentMath:[...card.querySelectorAll('.card-content-preview annotation')].map(node=>node.textContent),
            answerMath:[...card.querySelectorAll('.card-answer-preview annotation')].map(node=>node.textContent),
            errors:card.querySelectorAll('.katex-error').length,
            images:[...card.querySelectorAll('.card-content-preview img,.card-answer-preview img')].map(image=>({
              src:new URL(image.src).pathname,width:image.naturalWidth,height:image.naturalHeight}))};
        })""")
        mapped_path = browser.evaluate("window.__markdownImagePath")
        assert previews[0]["content"] == "已知 $x=1$，百分比为 50%，求 $x+1$。\n\n![图示](" + mapped_path + ")"
        assert previews[1]["content"] == "已知 $a=2$，计算 $a^2$。"
        assert [item["answer"] for item in previews] == ["$2$。", "$4$。"]
        assert [item["contentMath"] for item in previews] == [["x=1", "x+1"], ["a=2", "a^2"]]
        assert [item["answerMath"] for item in previews] == [["2"], ["4"]]
        assert all(item["errors"] == 0 for item in previews)
        assert previews[0]["images"] == [{"src": mapped_path, "width": 24, "height": 18}]
        assert previews[1]["images"] == []
        for width, height in [(1280, 900), (375, 812)]:
            browser.command("set", "viewport", str(width), str(height))
            literal_previews = browser.evaluate(r"""(() => {
              document.getElementById('markdownLiteralFenceRegression')?.remove();
              const gallery=document.createElement('div');
              gallery.id='markdownLiteralFenceRegression';
              gallery.style.cssText='padding:12px;background:white;color:#334155';
              const sources=%s;
              const results=[];
              for(const stored of sources) {
                const content=stored.split('__MARKDOWN_IMAGE_FIXTURE__').join(%s);
                const host=document.createElement('div');
                host.style.cssText='padding:8px;margin:8px 0;border:1px solid #cbd5e1;overflow-wrap:anywhere';
                gallery.append(host);
                renderQuestionPreviewContent(host,content);
                host.querySelectorAll('img').forEach(image=>image.loading='eager');
                results.push({content,math:[...host.querySelectorAll('annotation')].map(node=>node.textContent),
                  images:[...host.querySelectorAll('img')].map(node=>new URL(node.src).pathname),
                  literal:[...host.querySelectorAll('code.mb-preview-literal')].map(node=>node.textContent),
                  errors:host.querySelectorAll('.katex-error').length});
              }
              document.getElementById('importWorkspaceSection').append(gallery);
              return results;
            })()""" % (json.dumps(stored_examples), json.dumps(mapped_path)))
            for stored, rendered in zip(stored_examples, literal_previews):
                assert rendered["content"] == stored.replace("__MARKDOWN_IMAGE_FIXTURE__", mapped_path)
                assert rendered["math"] == ["y"] and rendered["images"] == [mapped_path]
                assert rendered["errors"] == 0
                assert literal_path in "".join(rendered["literal"])
                assert "$x$" in "".join(rendered["literal"])
            browser.command("wait", "--fn", "[...document.querySelectorAll('#markdownLiteralFenceRegression img')].every(image=>image.naturalWidth===24)")
            browser.command("scrollintoview", "#markdownLiteralFenceRegression")
            browser.command("screenshot", str(tmp_path / f"markdown-literal-fences-{width}.png"))
        browser.evaluate("document.getElementById('markdownLiteralFenceRegression').remove(); true")
        browser.evaluate("clearAllImportInputs(); resetImportState(false); true")
        assert browser.evaluate("window.currentImportSourceFormat") == "tex"
        browser.evaluate("document.getElementById('importSourceDetails').open=true; true")
        browser.command("select", "#importSourceFormat", "markdown")
        assert browser.evaluate("window.currentImportSourceFormat") == "markdown"
        pasted = original.replace("Markdown 测试卷", "Markdown 粘贴测试卷")
        browser.command("fill", "#importLatexContent", pasted)
        browser.command("upload", "#imagesFileInput", str(image))
        browser.evaluate("window.__markdownParse=null; runAIPaperParse(); true")
        browser.command("wait", "--fn", "!!window.__markdownParse && !document.getElementById('runParseBtn').disabled")
        assert browser.evaluate("window.__markdownParse") == {"format": "markdown", "source": pasted}
    finally:
        browser.evaluate("document.getElementById('markdownLiteralFenceRegression')?.remove(); true")
        browser.evaluate("window.fetch=window.__markdownOriginalFetch; clearAllImportInputs(); resetImportState(false); true")
