"""Executable single OCR slot, asset and stale-result boundaries without API calls."""
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _run_js(assertions: str):
    node = shutil.which('node')
    assert node, 'Node is required to execute frontend regression checks'
    source = (ROOT / 'static/js/ocr.js').read_text(encoding='utf-8')
    editor_source = (ROOT / 'static/js/editor.js').read_text(encoding='utf-8')
    helper_start = editor_source.index('function previewLiteralPattern()')
    helper_end = editor_source.index('window.normalizeNakedMathForPreview =', helper_start)
    preview_helpers = editor_source[helper_start:helper_end]
    start = source.index('const SingleOcrFigures = (() => {')
    end = source.index('// Switch Question Content workflow tab', start)
    script = r'''
const window = {};
const nodes = new Map();
const strokeRects=[];
const canvasContext = {clearRect(){},drawImage(){},fillRect(){},strokeRect(...args){strokeRects.push(args)}};
function node(id = '') {
    return {
        id, value:'', textContent:'', children:[], disabled:false, checked:true,
        selectionStart:0, selectionEnd:0, width:640, height:480,
        classList:{add(){},remove(){}}, setAttribute(){}, remove(){nodes.delete(this.id)},
        appendChild(value){this.children.push(value); if(value.id)nodes.set(value.id,value)},
        replaceChildren(){this.children=[]}, addEventListener(event, callback){this['on'+event]=callback},
        dispatchEvent(){}, getContext(){return canvasContext}, toBlob(cb){cb({size:100})},
        setPointerCapture(){}, getBoundingClientRect(){return {left:0,top:0,width:this.width,height:this.height}}
    };
}
const document = {
    getElementById(id){if(!nodes.has(id)) nodes.set(id,node(id)); return nodes.get(id)},
    createElement(type){return node()}, body:node('body')
};
const loadedImages=[];
class Image {
    constructor(){loadedImages.push(this)}
    naturalWidth=640; naturalHeight=480; complete=true;
    set src(value){this.source=value; if(this.onload)this.onload()}
}
class FileReader {readAsDataURL(file){this.onload({target:{result:'data:image/png;base64,source'}})}}
class FormData {append(){}}
let generation = 0;
const EditorState = {snapshot:()=>({generation}),isCurrent:s=>s.generation===generation};
const ImageLayoutTools = {normalize:value=>({...value}),key:value=>value};
const FigureLayoutState = {imageLayouts:{}};
window.ImageLayoutTools=ImageLayoutTools;
window.FigureLayoutState=FigureLayoutState;
window.MathBankSafe={safeImageUrl:path=>String(path||'').startsWith('/static/uploads/')?path:''};
window.MathBankImageAssets={collect:text=>[...text.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)].map(match=>match[1])};
window.MathBankModal={open(){},close(){}};
let contentOcrAbortController=null, answerOcrAbortController=null;
const registered=[];
window.registerAutoContentTikzAsset=payload=>registered.push(payload);
window.registerAutoAnswerTikzAsset=payload=>registered.push(payload);
const toasts=[];
function showToast(value){toasts.push(value)}
function cleanMathOcrText(value){return String(value||'')}
function loadToContentEditor(){document.getElementById('editContent').value=document.getElementById('contentOcrResultText').textContent}
function loadToFinalReview(){document.getElementById('editAnswerMarkdown').value=document.getElementById('ocrResultText').textContent}
const requests=[];
function fetch(url, options){return new Promise(resolve=>requests.push({url,options,resolve}))}
const reply=(request,data)=>request.resolve({ok:true,json:async()=>data});
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve))};
const expect=(condition,message)=>{if(!condition)throw new Error(message)};
const dragCrop=()=>{
    const canvas=document.getElementById('singleOcrCropCanvas');
    canvas.onpointerdown({clientX:10,clientY:20,pointerId:1,preventDefault(){}});
    canvas.onpointermove({clientX:180,clientY:170});
    canvas.onpointerup();
};
const button=(id,text)=>{
    const find=root=>root.children.flatMap(child=>[child,...find(child)]);
    return find(document.getElementById(id)).find(child=>child.textContent===text);
};
'''+preview_helpers+source[start:end]+r'''
(async()=>{
'''+assertions+r'''
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run([node, "-"], input=script, encoding="utf-8", cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_batch_tikz_registration_and_inline_layouts_only_after_current_result_is_loaded():
    _run_js(r'''
runContentOcr({type:'image/png'});
document.getElementById('editContent').value='正在编辑的新内容';
const original='/static/uploads/original.png';
const first='/static/uploads/tikz_1.png', second='/static/uploads/tikz_2.png';
reply(requests[0],{status:'success',latex:`选择图形 \\begin{choices}\\item ![图1](${first})\\item ![图2](${second})\\end{choices}`,
 image_path:original,confidence:0.8,tikz_assets:[
  {image_path:first,tikz_code:'source one',reference_image_path:'/static/uploads/ref1.png'},
  {image_path:second,tikz_code:'source two',reference_image_path:'/static/uploads/ref2.png'}],
 image_layouts:{[first]:{align:'left',size:'small'},[second]:{align:'left',size:'small'},'/static/uploads/unreferenced.png':{size:'large'}}});
await settle();
expect(document.getElementById('editContent').value==='正在编辑的新内容','OCR overwrote edits made during request');
expect(registered.length===0,'unapplied result registered TikZ references');
button('contentOcrFigures','载入识别结果').onclick();
expect(registered.length===2,'all inline drawings were not registered');
expect(registered.every(asset=>asset.imagePath!==original),'original screenshot became visible');
expect(registered[0].referenceImagePath==='/static/uploads/ref1.png','per-figure reference was lost');
expect(Object.keys(FigureLayoutState.imageLayouts).length===2,'unreferenced layout was retained');
expect(FigureLayoutState.imageLayouts[first].size==='small','option picture lost compact size');
''')


def test_new_ocr_and_editor_switch_reject_out_of_order_results_and_source_preview():
    _run_js(r'''
runContentOcr({type:'image/png'});
const oldRequest=requests[0];
runContentOcr({type:'image/png'});
reply(requests[1],{status:'success',latex:'新识别',image_path:'/static/uploads/new.png'});
await settle();
reply(oldRequest,{status:'success',latex:'旧识别',image_path:'/static/uploads/old.png'});
await settle();
expect(document.getElementById('editContent').value==='新识别','late OCR replaced newer result');
runOcr({type:'image/png'});
generation+=1;
window.SingleOcrFigures.reset();
reply(requests[2],{status:'success',latex:'其他题的旧解析',image_path:'/static/uploads/answer.png'});
await settle();
expect(document.getElementById('editAnswerMarkdown').value==='','late answer OCR crossed editor session');
''')


def test_manual_crop_replaces_only_unique_original_slot_and_keeps_option_anchor():
    _run_js(r'''
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'\\begin{choices}\\item [插图待补: 图1]\\item [插图待补: 图2]\\end{choices}',
 image_path:'/static/uploads/original.png',illustrations:[{slot:'图1',placeholder:'[插图待补: 图1]',status:'pending'}]});
await settle();
const panel=document.getElementById('contentOcrFigures');
const row=panel.children.find(child=>child.children.some(part=>part.textContent==='图2 · 待截图'));
expect(row,'partial figure metadata hid the second placeholder');
row.children.find(child=>child.textContent==='截图补入此处').onclick();
const canvas=document.getElementById('singleOcrCropCanvas');
canvas.onpointerdown({clientX:10,clientY:20,pointerId:1,preventDefault(){}});
canvas.onpointermove({clientX:180,clientY:170});
canvas.onpointerup();
document.getElementById('singleOcrCropSave').onclick();
await settle();
expect(requests[1].url==='/api/upload','crop did not use the standard safe upload endpoint');
reply(requests[1],{status:'success',file_path:'/static/uploads/cropped.png'});
await settle();
expect(document.getElementById('editContent').value==='\\begin{choices}\\item [插图待补: 图1]\\item ![图2](/static/uploads/cropped.png)\\end{choices}',
 'crop moved to the question tail or replaced the wrong slot');
expect(FigureLayoutState.imageLayouts['/static/uploads/cropped.png'].size==='small','inline crop did not receive bounded size');
''')


def test_crop_upload_completion_preserves_new_edits_and_does_not_register_image():
    _run_js(r'''
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'正文 [插图待补: 图1]',image_path:'/static/uploads/original.png',
 illustrations:[{slot:'图1',placeholder:'[插图待补: 图1]',status:'pending',bbox:{left:0,top:0,right:200,bottom:200}}]});
await settle();
button('contentOcrFigures','截图补入此处').onclick();
dragCrop();
document.getElementById('singleOcrCropSave').onclick();
await settle();
document.getElementById('editContent').value='新编辑 [插图待补: 图1]';
reply(requests[1],{status:'success',file_path:'/static/uploads/stale.png'});
await settle();
expect(document.getElementById('editContent').value==='新编辑 [插图待补: 图1]','late crop replaced new edits');
expect(!FigureLayoutState.imageLayouts['/static/uploads/stale.png'],'stale crop registered layout');
''')


def test_file_preview_finishing_after_fast_ocr_keeps_original_server_crop_source():
    _run_js(r'''
const readers=[];
FileReader=class {readAsDataURL(){readers.push(this)}};
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'[插图待补: 图1]',image_path:'/static/uploads/original.png'});
await settle();
readers[0].onload({target:{result:'data:image/png;base64,localpreview'}});
expect(document.getElementById('contentOcrPreviewImg').src==='data:image/png;base64,localpreview',
 'fast OCR prevented the local screenshot preview');
button('contentOcrFigures','截图补入此处').onclick();
expect(loadedImages[0].source==='/static/uploads/original.png','late reader replaced the normalized crop source');
''')


def test_manual_crop_ignores_literal_and_math_slots_and_replaces_visible_source_offset():
    _run_js(r'''
runContentOcr({type:'image/png'});
const original='`[插图待补: 图1]`示例。$[插图待补: 图1]$正文[插图待补: 图1]';
reply(requests[0],{status:'success',latex:original,image_path:'/static/uploads/original.png',
 illustrations:[{slot:'图1',placeholder:'[插图待补: 图1]',status:'pending',bbox:{left:0,top:0,right:200,bottom:200}}]});
await settle();
const cropButton=button('contentOcrFigures','截图补入此处');
expect(cropButton,'a literal duplicate made the visible figure slot ambiguous');
cropButton.onclick();
dragCrop();
document.getElementById('singleOcrCropSave').onclick();
await settle();
reply(requests[1],{status:'success',file_path:'/static/uploads/visible-crop.png'});
await settle();
expect(document.getElementById('editContent').value==='`[插图待补: 图1]`示例。$[插图待补: 图1]$正文![图1](/static/uploads/visible-crop.png)',
 'manual crop replaced a protected literal or formula');
runContentOcr({type:'image/png'});
reply(requests[2],{status:'success',latex:'`[插图待补: 图2]` $[插图待补: 图3]$ \\begin{equation}[插图待补: 图4]\\end{equation} <!-- [插图待补: 图5] -->',
 image_path:'/static/uploads/original.png'});
await settle();
expect(!button('contentOcrFigures','截图补入此处'),'literal-only OCR gained a crop target');
''')


def test_complete_choices_ocr_keeps_option_picture_paths_byte_for_byte():
    node = shutil.which('node')
    assert node
    ocr = (ROOT / 'static/js/ocr.js').read_text(encoding='utf-8')
    start = ocr.index('function loadToContentEditor(source, isAppend = false)')
    end = ocr.index('// Clear OCR image preview', start)
    editor = (ROOT / 'static/js/editor.js').read_text(encoding='utf-8')
    formatter_start = editor.index('function formatQuestionContent(text)')
    formatter_end = editor.index('// 🟢 智能心跳循环', formatter_start)
    script = r'''
const assert=require('node:assert/strict');
const canonical=String.raw`\begin{choices}
\item ![图1](/static/uploads/ocr-option-A.png)
\item ![图2](/static/uploads/ocr-option-B.png)
\item ![图3](/static/uploads/ocr-option-C.png)
\item ![图4](/static/uploads/ocr-option-D.png)
\end{choices}`;
const nodes={editContent:{value:'',dispatchEvent(){}},contentOcrResultText:{textContent:canonical},editQType:{value:'',dispatchEvent(){}}};
const document={getElementById:id=>nodes[id]};
const window={};
function showToast(){}
'''+editor[formatter_start:formatter_end]+ocr[start:end]+r'''
loadToContentEditor('ocr');
assert.equal(nodes.editContent.value,canonical);
assert.equal(nodes.editQType.value,'single_choice');
nodes.editContent.value='';
nodes.editQType.value='multi_choice';
loadToContentEditor('ocr');
assert.equal(nodes.editQType.value,'multi_choice');
'''
    result = subprocess.run([node, "-"], input=script, encoding="utf-8", cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_comment_math_delimiters_and_markdown_link_labels_do_not_own_visible_slot():
    _run_js(r'''
runContentOcr({type:'image/png'});
const original='<!-- $[插图待补: 图1] --> [插图待补: 图1](https://example.invalid) 正文[插图待补: 图1] $x$';
reply(requests[0],{status:'success',latex:original,image_path:'/static/uploads/original.png',
 illustrations:[{slot:'图1',placeholder:'[插图待补: 图1]',status:'pending',bbox:{left:0,top:0,right:200,bottom:200}}]});
await settle();
const cropButton=button('contentOcrFigures','截图补入此处');
expect(cropButton,'comment delimiter or link label made the visible slot ambiguous');
cropButton.onclick();
dragCrop();
document.getElementById('singleOcrCropSave').onclick();
await settle();
reply(requests[1],{status:'success',file_path:'/static/uploads/visible.png'});
await settle();
expect(document.getElementById('editContent').value==='<!-- $[插图待补: 图1] --> [插图待补: 图1](https://example.invalid) 正文![图1](/static/uploads/visible.png) $x$',
 'crop changed a comment or link label');
''')


def test_visual_review_labels_require_visible_per_figure_proof_for_the_displayed_image():
    _run_js(r'''
const paths=['/static/uploads/verified.png','/static/uploads/corrected.png','/static/uploads/tikz-verified.png','/static/uploads/tikz-unproved.png','/static/uploads/no-proof.png'];
const text=paths.map((path,index)=>`![图${index+1}](${path})`).join(' ')+' 正文[插图待补: 图6]';
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:text,image_path:'/static/uploads/original.png',
 illustration_review:{status:'verified',verified:6},
 illustrations:[
  {slot:'图1',placeholder:'[插图待补: 图1]',status:'cropped',image_path:paths[0],review:{status:'verified',corrected:false}},
  {slot:'图2',placeholder:'[插图待补: 图2]',status:'cropped',image_path:paths[1],review:{status:'verified',corrected:true}},
  {slot:'图3',placeholder:'[插图待补: 图3]',status:'tikz',image_path:paths[2],review:{status:'verified'},preview_review:{status:'verified'}},
  {slot:'图4',placeholder:'[插图待补: 图4]',status:'tikz',image_path:paths[3],review:{status:'verified'},preview_review:{status:'unavailable'}},
  {slot:'图5',placeholder:'[插图待补: 图5]',status:'cropped',image_path:paths[4]},
  {slot:'图6',placeholder:'[插图待补: 图6]',status:'unresolved',review:{status:'unverified'},message:'插图有标注缺失'}
 ]});
await settle();
const labels=document.getElementById('contentOcrFigures').children.flatMap(child=>child.children).map(child=>child.textContent);
expect(labels.includes('图1 · 原图截图 · 原图裁片已视觉核对'),'verified crop has no proof label');
expect(labels.includes('图2 · 原图截图 · 已修正裁图并核对'),'corrected final crop lost the correction proof');
expect(labels.includes('图3 · TikZ 绘图 · 绘图已视觉核对'),'reviewed generated preview has no drawing proof label');
expect(labels.includes('图4 · TikZ 绘图'),'source proof incorrectly claimed the generated drawing was reviewed');
expect(labels.includes('图5 · 原图截图'),'summary proof leaked to an unreviewed figure');
expect(labels.includes('图6 · 待截图 · 视觉核对未通过'),'unverified slot lost its manual crop reason');
expect(button('contentOcrFigures','截图补入此处'),'unverified slot lost its manual repair entry');
''')


def test_manual_repair_does_not_reuse_failed_visual_review_claim():
    _run_js(r'''
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'正文[插图待补: 图1]',image_path:'/static/uploads/original.png',
 illustrations:[{slot:'图1',placeholder:'[插图待补: 图1]',status:'unresolved',review:{status:'unavailable'},
 bbox:{left:0,top:0,right:200,bottom:200}}]});
await settle();
button('contentOcrFigures','截图补入此处').onclick();
dragCrop();
document.getElementById('singleOcrCropSave').onclick();
await settle();
reply(requests[1],{status:'success',file_path:'/static/uploads/manual.png'});
await settle();
const labels=document.getElementById('contentOcrFigures').children.flatMap(child=>child.children).map(child=>child.textContent);
expect(labels.includes('图1 · 手动截图'),'manual crop retained a failed review badge');
expect(!labels.some(label=>label.includes('已视觉核对')),'manual crop claimed an AI vision review it did not receive');
''')


def test_unresolved_manual_crop_never_preselects_failed_or_missing_review_bbox():
    _run_js(r'''
for(const reviewStatus of ['unverified','unavailable','verified']){
 const originalRequests=requests.length;
 runContentOcr({type:'image/png'});
 reply(requests[originalRequests],{status:'success',latex:'正文[插图待补: 图1]',image_path:'/static/uploads/original.png',
   illustrations:[{slot:'图1',placeholder:'[插图待补: 图1]',status:'unresolved',
    bbox:{left:320,top:385,right:490,bottom:775},review:{status:reviewStatus}}]});
 await settle();
 const strokesBefore=strokeRects.length;
 button('contentOcrFigures','截图补入此处').onclick();
 const save=document.getElementById('singleOcrCropSave');
 expect(save.disabled,'an unresolved server bbox became ready to confirm');
 expect(strokeRects.length===strokesBefore,'failed bbox was drawn as a blue selection');
 expect(document.getElementById('singleOcrCropStatus').textContent==='原图框未确认，请手动框选完整插图','manual repair still claimed preset bbox confirmation');
 save.onclick();
 await settle();
 expect(requests.length===originalRequests+1,'blank manual selection uploaded an automatic crop');
 dragCrop();
 expect(!save.disabled,'real user drag did not enable manual crop');
}
''')
