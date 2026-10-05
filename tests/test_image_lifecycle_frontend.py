"""Run shipped image ownership, promotion, and task leases in isolated Node DOMs."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / 'static/js/api.js').read_text()
EDITOR = (ROOT / 'static/js/editor.js').read_text()
IMPORT = (ROOT / 'static/js/import.js').read_text()


def section(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


COMMON = r"""
const assert = require('node:assert/strict');
const window = {location: {href:'http://localhost:8000/', origin:'http://localhost:8000'}};
const asString = value => value == null ? '' : String(value);
const toasts=[];
function showToast(message) { toasts.push(message); }
function renderIllustrationBadges() {}
function renderQuestionPreviewContent() { return ''; }
function loadQuestions() {}
function loadCategories() {}
function refreshRelatedDropdown() {}
function updateQuestionSaveButtonState() {}
function clearContentOcrPreview() {}
function clearOcrPreview() {}
function renderParsedCardPreview() {}
function appendSafeImageBadge() {}
const elements = {};
function element(value='') { return { value, innerHTML:'', dataset:{},
  selectionStart:0, selectionEnd:0, scrollTop:17, scrollLeft:3,
  setSelectionRange(a,b) { this.selectionStart=a;this.selectionEnd=b; },
  classList:{contains:()=>false,add(){},remove(){}},
  dispatchEvent(){}, focus(){}, querySelector(){return null;} }; }
for (const id of ['editContent','editAnswerMarkdown','editReview','editQType','editDifficulty',
  'editSource','editCompulsory','editChapter','editKnowledge','editRelatedQuestion','editTags',
  'editorSection','editorTitle','contentPreview','paperContent','draftCount']) elements[id]=element();
elements.editQType.value='single_choice'; elements.editDifficulty.value='medium';
elements.editCompulsory.value='高中';elements.editChapter.value='函数';
const document={getElementById:id=>elements[id]||null};
const draftStorage={};
const localStorage={getItem:key=>draftStorage[key]||null,setItem:(key,value)=>{draftStorage[key]=value;}};
const requests=[];
let fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
let questionDetailLoading=false,saveQuestionInFlight=null,activeSidebarTab='bank';
const old='/static/uploads/tmp/figure.png',final='/static/uploads/figure.png';
const reference='/static/uploads/tmp/reference.png',finalReference='/static/uploads/reference.png';
const mark=path=>`![插图](${path})`;
"""
SOURCES = (
    section(API, 'function safeImageUrl(value)', 'function sanitizeRichHtml')
    + '\nwindow.MathBankSafe={safeImageUrl};\n'
    + section(API, 'const EditorState =', '// Global variables')
    + section(API, 'let uploadedImages =', 'let contentOcrAbortController')
    + section(IMPORT, '// ocr.js is loaded', 'function findContentTikzImagePath')
    + section(EDITOR, 'function backupEditorState', '// Custom Premium Confirmation Modal')
    + section(EDITOR, 'function getLocalStorageDrafts', 'function selectDraft')
    + 'const editContent=elements.editContent;\n'
    + section(EDITOR, 'const updateContentPreview = () => {', 'const updateAnswerPreview = () => {')
    + section(IMPORT, 'function saveQuestion(skipCheck = false)', '// AI classification modal handlers')
)


def run_js(checks, sources=SOURCES):
    node = shutil.which('node')
    assert node, 'Node is required for isolated frontend regression checks'
    result = subprocess.run([node, '-e', COMMON + sources + '\n(async()=>{\n' + checks
                             + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize('source', [
    '![图]({old})', '![图](<{old}> "图注")',
    r'\includegraphics[width=2cm]{{{old}}}', '<img alt="图" src="{old}">',
    "<img src='{old}'>", '<img src={old}>', r'\% 已知 ![图]({old})',
    r'\includegraphics{{  {old}  }}',
    '![完成50%]({old} "50%")', '![完成50%]({old}) ![第二幅]({old})',
    '![注释`50%]({old}) ![第二幅]({old})',
])
def test_collect_and_migrate_supported_destinations(source):
    source = source.format(old='/static/uploads/tmp/figure.png')
    run_js('const source=' + json.dumps(source) + r""";
      assert.deepEqual(window.MathBankImageAssets.collect(source),[old]);
      assert.equal(window.MathBankImageAssets.mapText(source,{[old]:final}),source.split(old).join(final));
    """)


@pytest.mark.parametrize('wrapper', [
    '`%s`', '```latex\n%s\n```', '~~~\n%s\n~~~', '````latex\n%s\n`````', '<!-- %s -->',
    r'\begin{verbatim}%s\end{verbatim}', r'\begin{tikzpicture}%s\end{tikzpicture}',
    r'\%s', '%% %s', r'\verb|%s|', r'\Verb+%s+', r'\lstinline[language=TeX]|%s|',
    r'\lstinline{%s}', r'\detokenize{%s}', r'\mintinline{latex}{%s}', r'\path{%s}', r'\url{%s}',
])
def test_literal_images_are_neither_owned_nor_rewritten(wrapper):
    literal = wrapper % '![示例](/static/uploads/tmp/figure.png)' if wrapper != '%% %s' else '% ![示例](/static/uploads/tmp/figure.png)'
    run_js('const literal=' + json.dumps(literal) + r""";
      const content=literal+'\n'+mark(old);
      assert.deepEqual(window.MathBankImageAssets.collect(literal),[]);
      assert.deepEqual(window.MathBankImageAssets.collect(content),[old]);
      assert.equal(window.MathBankImageAssets.mapText(content,{[old]:final}),literal+'\n'+mark(final));
    """)


@pytest.mark.parametrize('prefix', ['```latex\n', '~~~\n', '<!-- ', r'\begin{tikzpicture}'])
def test_unclosed_literal_protects_remaining_text(prefix):
    run_js('const content=' + json.dumps(prefix) + r"""+mark(old);
      assert.deepEqual(window.MathBankImageAssets.collect(content),[]);
      assert.equal(window.MathBankImageAssets.mapText(content,{[old]:final}),content);
    """)


def test_cut_undo_paste_shared_answer_and_private_tikz_draft_assets():
    run_js(r"""
      const content='题干 '+mark(final);
      elements.editContent.value=content; updateContentPreview();
      assert.deepEqual(uploadedImages,[final]);
      elements.editContent.value='题干';updateContentPreview();assert.deepEqual(uploadedImages,[]);
      elements.editContent.value=content;updateContentPreview();assert.deepEqual(uploadedImages,[final]);
      elements.editAnswerMarkdown.value=mark(final);window.syncAnswerImagesFromMarkdown();
      TikzState.contentAssets=[{id:'drawing',image_path:old,tikz_code:'literal '+old,reference_image_path:reference}];
      elements.editContent.value+=' '+mark(reference);updateContentPreview();
      assert.deepEqual(uploadedImages,[final]);
      assert.deepEqual(uploadedAnswerImages,[final]);
      saveCurrentToDrafts();
      const draft=JSON.parse(draftStorage.mathbank_local_drafts)[0];
      assert.deepEqual(new Set(draft.image_paths),new Set([final,old,reference]));
      assert.equal(draft.content_tikz_assets[0].reference_image_path,reference);
      assert.equal(isEditorModified(),false);
    """)


@pytest.mark.parametrize('edit_during_save', [False, True])
def test_save_promotes_current_urls_and_saved_baseline_without_losing_later_edits(edit_during_save):
    run_js('const editDuringSave=' + json.dumps(edit_during_save) + r""";
      EditorState.useQuestion({id:7});
      elements.editContent.value='题干 '+mark(old);
      elements.editAnswerMarkdown.value='解析 '+mark(old);
      TikzState.contentAssets=[{id:'drawing',image_path:old,tikz_code:'literal '+old,
        instruction:old,reference_image_path:reference}];
      FigureLayoutState.imageLayouts={'tmp/figure.png':{align:'left',size:'small'}};
      syncEditorImageReferences();backupEditorState(7);
      const work=saveQuestion(true);
      const request=requests.at(-1);assert.equal(request.url,'/api/questions/7');
      assert.deepEqual(new Set(JSON.parse(request.options.body.get('image_paths'))),new Set([old,reference]));
      if(editDuringSave) elements.editContent.value+=' 保存期间新输入';
      const cursor=elements.editContent.value.length;
      elements.editContent.selectionStart=cursor;elements.editContent.selectionEnd=cursor;
      request.resolve({ok:true,status:200,json:async()=>({status:'success',question:{id:7},
        asset_path_map:{[old]:final,[reference]:finalReference}})});
      assert.equal(await work,true,JSON.stringify(toasts));
      assert.equal(elements.editContent.value,'题干 '+mark(final)+(editDuringSave?' 保存期间新输入':''));
      assert.equal(elements.editContent.selectionStart,elements.editContent.value.length);
      assert.equal(elements.editContent.scrollTop,17);
      assert.equal(elements.editAnswerMarkdown.value,'解析 '+mark(final));
      assert.deepEqual(uploadedImages,[final]);
      assert.equal(TikzState.contentAssets[0].image_path,final);
      assert.equal(TikzState.contentAssets[0].reference_image_path,finalReference);
      assert.equal(TikzState.contentAssets[0].tikz_code,'literal '+old);
      assert.equal(TikzState.contentAssets[0].instruction,old);
      assert.deepEqual(FigureLayoutState.imageLayouts,{'figure.png':{align:'left',size:'small'}});
      assert.equal(originalQuestionState.content,'题干 '+mark(final));
      assert.equal(isEditorModified(),editDuringSave);
    """)


def test_stale_editor_response_cannot_migrate_new_question():
    run_js(r"""
      EditorState.useQuestion({id:7});const session=EditorState.snapshot();
      EditorState.useQuestion({id:8});elements.editContent.value=mark(old);
      applyEditorAssetPathMap({[old]:final},session);
      assert.equal(elements.editContent.value,mark(old));
      assert.deepEqual(window.MathBankImageAssets.collect('![x](https://other.invalid/a.png)'),[]);
      assert.deepEqual(Object.keys(window.MathBankImageAssets.normalizeMap({[old]:'https://other.invalid/a.png'})),[]);
    """)


def test_migration_changes_only_exact_destination_and_keeps_alt_filename_prefix():
    run_js(r"""
      const prefix='/static/uploads/tmp/figure.png.copy.png';
      const content=`![原名 ${old}](${old} "${old}") `+mark(prefix);
      assert.deepEqual(window.MathBankImageAssets.collect(content),[old,prefix]);
      assert.equal(window.MathBankImageAssets.mapText(content,{[old]:final}),
        `![原名 ${old}](${final} "${old}") `+mark(prefix));
    """)


def test_import_mapping_keeps_other_cards_and_current_edits_and_rejects_stale_generation():
    source = SOURCES + section(IMPORT, 'function isParsedQuestionSaveContextCurrent', 'function blockImportResetWhileSaving')
    source += section(IMPORT, 'function applyParsedAssetPathMap', 'function saveParsedQuestion(index)')
    run_js(r"""
      const q={content:mark(old),answer_markdown:mark(old),image_paths:[old]};
      const sibling={content:mark(old),image_paths:[old]};
      parsedQuestionsData=[q,sibling];parsedQuestionsGeneration=3;
      const content=element(mark(old)+' 新修改'),answer=element(mark(old));
      elements['parsed-card-0']={querySelector:selector=>selector==='.card-content-textarea'?content:answer};
      applyParsedAssetPathMap({[old]:final},3,0,q);
      assert.equal(content.value,mark(final)+' 新修改');assert.equal(q.content,content.value);
      assert.deepEqual(q.image_paths,[final]);assert.equal(sibling.content,mark(old));
      applyParsedAssetPathMap({[final]:old},2,0,q);assert.equal(q.content,mark(final)+' 新修改');
      applyParsedAssetPathMap({[old]:final},3,0,q);assert.equal(q.content,mark(final)+' 新修改');
    """, source + '\nlet parsedQuestionsData=[],parsedQuestionsGeneration=0;\n')


def test_result_lease_tracks_only_current_unsaved_visible_import():
    source = SOURCES + section(IMPORT, 'let parsedQuestionsData =', 'const parsedQuestionSaveInFlight =')
    run_js(r"""
      const intervals=new Map();let counter=0;
      global.setInterval=(fn,delay)=>{assert.equal(delay,60000);intervals.set(++counter,fn);return counter;};
      global.clearInterval=id=>intervals.delete(id);
      let hidden=false;
      elements.importWorkspaceSection={classList:{contains:()=>hidden}};
      fetch=async(url,options)=>{requests.push({url,options});return{status:200};};
      const tick=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
      window.currentPdfTaskId='task-1';parsedQuestionsGeneration=1;parsedQuestionsData=[{saved:false}];
      startDocumentResultRetention('task-1');await tick();
      assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/tasks/task-1/retain');
      assert.equal(intervals.size,1);
      [...intervals.values()][0]();await tick();assert.equal(requests.length,2);
      hidden=true;refreshDocumentResultRetention();assert.equal(intervals.size,0);
      hidden=false;refreshDocumentResultRetention();await tick();assert.equal(intervals.size,1);
      parsedQuestionsData[0].saved=true;refreshDocumentResultRetention();assert.equal(intervals.size,0);
      const count=requests.length;refreshDocumentResultRetention();await tick();assert.equal(requests.length,count);
      stopDocumentResultRetention();parsedQuestionsData=[{saved:false}];window.currentPdfTaskId='task-2';
      refreshDocumentResultRetention();assert.equal(requests.length,count);
      startDocumentResultRetention('task-2');await tick();assert.equal(intervals.size,1);
      parsedQuestionsGeneration++;refreshDocumentResultRetention();assert.equal(intervals.size,0);
    """, source)


@pytest.mark.parametrize('edit_during_save', ['content', 'answer', 'category', 'asset', 'none'])
def test_import_save_response_rewrites_retry_payload_without_touching_shared_sibling(edit_during_save):
    source = SOURCES + section(IMPORT, 'function isParsedQuestionSaveContextCurrent', 'function blockImportResetWhileSaving')
    source += section(IMPORT, 'function safeDuplicateImagePaths', 'async function requestQuestionDuplicateCheck')
    source += section(IMPORT, 'function applyParsedAssetPathMap', 'function confirmClearAllParsed')
    source += section(IMPORT, 'let parsedQuestionsData =', 'const parsedQuestionSaveInFlight =')
    source += r"""
let parsedBatchSaveInFlight=null;
const parsedQuestionSaveInFlight=new Map();
function validateParsedQuestionBeforeImport(){return true;}
function updateSelectedCount(){}
"""
    run_js('const modification=' + json.dumps(edit_during_save) + r""";
      const question={content:mark(old),answer_markdown:'',image_paths:[old],
        content_tikz_assets:[{id:'figure',image_path:old,tikz_code:'draw',instruction:'',reference_image_path:reference}]};
      const sibling={content:mark(old),image_paths:[old],saved:true};parsedQuestionsData=[question,sibling];
      const intervals=new Map();let counter=0;
      global.setInterval=(fn,delay)=>{intervals.set(++counter,fn);return counter;};
      global.clearInterval=id=>intervals.delete(id);
      const realFetch=fetch;
      fetch=(url,options)=>url.endsWith('/retain')?Promise.resolve({status:200}):realFetch(url,options);
      window.currentPdfTaskId='live-result';startDocumentResultRetention('live-result');
      const fields={};
      for(const name of ['content-textarea','answer-textarea','qtype','difficulty','source',
        'compulsory','chapter','knowledge','save-btn','status-badge','select-checkbox']){
        fields['.card-'+name]=element('');
      }
      fields['.card-content-textarea'].value=mark(old);
      fields['.card-qtype'].value='single_choice';fields['.card-difficulty'].value='medium';
      fields['.card-compulsory'].value='高中';fields['.card-chapter'].value='函数';
      elements['parsed-card-0']={querySelector:selector=>fields[selector]||null};
      const first=saveParsedQuestion(0);
      const firstRequest=requests.at(-1);
      assert.deepEqual(JSON.parse(firstRequest.options.body.get('image_paths')),[old,reference]);
      if(modification==='content') fields['.card-content-textarea'].value+=' 保存期间补充';
      if(modification==='answer') fields['.card-answer-textarea'].value='新增解析';
      if(modification==='category') fields['.card-chapter'].value='几何';
      if(modification==='asset') question.content_tikz_assets[0].instruction='新绘图要求';
      firstRequest.resolve({ok:true,status:200,json:async()=>({status:'success',question:{id:7},
        asset_path_map:{[old]:final,[reference]:finalReference}})});
      assert.equal(await first,true,JSON.stringify(toasts));
      const dirty=modification!=='none';
      const expectedContent=mark(final)+(modification==='content'?' 保存期间补充':'');
      assert.equal(question.content,expectedContent);assert.equal(sibling.content,mark(old));
      assert.deepEqual(question.image_paths,[final]);
      assert.equal(question.content_tikz_assets[0].reference_image_path,finalReference);
      assert.equal(question.saved,!dirty);assert.equal(question.modifiedAfterSave,dirty);
      assert.equal(fields['.card-status-badge'].textContent,dirty?'仍有修改待导入':'已导入');
      assert.equal(fields['.card-select-checkbox'].disabled,!dirty);
      assert.equal(fields['.card-select-checkbox'].checked,dirty);assert.equal(intervals.size,dirty?1:0);
      const second=saveParsedQuestion(0);
      const nextRequest=requests.at(-1);
      assert.equal(nextRequest.options.body.get('content'),expectedContent);
      if(modification==='answer') assert.equal(nextRequest.options.body.get('answer_markdown'),'新增解析');
      if(modification==='category') assert.equal(nextRequest.options.body.get('category_chapter'),'几何');
      if(modification==='asset') assert.equal(JSON.parse(nextRequest.options.body.get('content_tikz_assets'))[0].instruction,'新绘图要求');
      assert.deepEqual(JSON.parse(nextRequest.options.body.get('image_paths')),[final,finalReference]);
      nextRequest.resolve({ok:true,status:200,json:async()=>({status:'success',question:{id:8},asset_path_map:{}})});
      assert.equal(await second,true);
      assert.equal(question.saved,true);assert.equal(question.modifiedAfterSave,false);
      assert.equal(fields['.card-status-badge'].textContent,'已导入');
      assert.equal(fields['.card-select-checkbox'].disabled,true);
      assert.equal(fields['.card-select-checkbox'].checked,false);assert.equal(intervals.size,0);
    """,source)
