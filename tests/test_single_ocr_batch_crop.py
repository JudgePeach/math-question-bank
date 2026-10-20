"""Batch crop geometry, atomic insertion and stale-result regressions; no API calls."""
from pathlib import Path
import shutil
import subprocess

from test_single_ocr_frontend import _run_js


ROOT = Path(__file__).resolve().parents[1]


def test_all_three_grid_presets_tile_the_selected_region_in_reading_order():
    source = (ROOT / 'static/js/ocr.js').read_text(encoding='utf-8')
    start = source.index('function computeCropGridBoxes(')
    end = source.index('function openCrop(', start)
    script = source[start:end] + r'''
const assert = require('node:assert/strict');
const original = {left:7.5, top:11.25, right:349, bottom:171.75};
for (const [mode, rows, columns] of [['2x2',2,2],['1x4',1,4],['4x1',4,1]]) {
 const boxes = computeCropGridBoxes(original, mode);
 assert.equal(boxes.length, 4);
 assert.deepEqual([boxes[0].left, boxes[0].top], [original.left, original.top]);
 assert.deepEqual([boxes[3].right, boxes[3].bottom], [original.right, original.bottom]);
 assert.equal(boxes.reduce((sum, b) => sum + (b.right-b.left)*(b.bottom-b.top),0),
  (original.right-original.left)*(original.bottom-original.top));
 for(let r=0;r<rows;r++) for(let c=0;c<columns;c++) {
  const box=boxes[r*columns+c];
  if(c) assert.equal(box.left,boxes[r*columns+c-1].right);
  if(r) assert.equal(box.top,boxes[(r-1)*columns+c].bottom);
 }
}
assert.equal(computeCropGridBoxes(null,'2x2').length,0);
assert.equal(computeCropGridBoxes({...original,right:NaN},'2x2').length,0);
assert.equal(computeCropGridBoxes({...original,right:10},'1x4').length,0);
assert.equal(computeCropGridBoxes(original,'unexpected').length,0);
'''
    result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_four_crops_upload_sequentially_and_apply_once_without_rewriting_choices_or_selected_text():
    _run_js(r'''
const original='正文\\begin{choices}\\item A\\item B\\end{choices}';
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:original,image_path:'/static/uploads/original.png'});
await settle();
const textarea=document.getElementById('editContent');
textarea.selectionStart=2;textarea.selectionEnd=7;
let inputs=0;textarea.dispatchEvent=()=>inputs++;
button('contentOcrFigures','从原图追加截图').onclick();
const mode=document.getElementById('singleOcrCropMode');
mode.value='2x2';mode.onchange();
expect(document.getElementById('singleOcrCropSave').disabled,'preset selected a region without user drag');
dragCrop();
document.getElementById('singleOcrCropSave').onclick();
await settle();
for(let i=0;i<4;i++){
 expect(requests.length===i+2,'fragment uploads were not bounded to one at a time');
 expect(textarea.value===original,'batch inserted partial results before all uploads succeeded');
 reply(requests[i+1],{status:'success',file_path:'/static/uploads/batch-'+i+'.png'});
 await settle();
}
expect(inputs===1,'batch was not applied as one editor change');
const expected=['A','B','C','D'].map((label,i)=>`![${label}](/static/uploads/batch-${i}.png)`).join('\n\n');
expect(textarea.value===original.slice(0,2)+'\n\n'+expected+'\n\n'+original.slice(2),'batch rewrote choices or deleted selected source');
expect(Object.keys(FigureLayoutState.imageLayouts).length===4,'batch lost per-image bounded layouts');
''')


def test_partial_upload_failure_keeps_all_editor_text_and_layouts_unchanged():
    _run_js(r'''
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'原题文',image_path:'/static/uploads/original.png'});
await settle();
button('contentOcrFigures','从原图追加截图').onclick();
const mode=document.getElementById('singleOcrCropMode');mode.value='1x4';mode.onchange();
dragCrop();document.getElementById('singleOcrCropSave').onclick();await settle();
reply(requests[1],{status:'success',file_path:'/static/uploads/first.png'});await settle();
requests[2].resolve({ok:false,json:async()=>({status:'error',message:'上传失败'})});await settle();
expect(requests.length===3,'failed batch continued uploading');
expect(document.getElementById('editContent').value==='原题文','failed batch partially inserted images');
expect(Object.keys(FigureLayoutState.imageLayouts).length===0,'failed batch registered partial layouts');
expect(!document.getElementById('singleOcrCropSave').disabled,'failed batch cannot be retried');
expect(!mode.disabled,'failed batch locked its mode');
''')


def test_mid_batch_edits_and_modal_cancellation_stop_remaining_uploads():
    _run_js(r'''
for(const action of ['edit','cancel','session']){
 const start=requests.length;
 runContentOcr({type:'image/png'});
 reply(requests[start],{status:'success',latex:'原题文',image_path:'/static/uploads/original.png'});await settle();
 button('contentOcrFigures','从原图追加截图').onclick();
 const mode=document.getElementById('singleOcrCropMode');mode.value='4x1';mode.onchange();
 dragCrop();document.getElementById('singleOcrCropSave').onclick();await settle();
 if(action==='edit') document.getElementById('editContent').value='后来的编辑';
 if(action==='cancel') document.getElementById('singleOcrCropClose').onclick();
 if(action==='session') {generation++;window.SingleOcrFigures.reset();}
 reply(requests[start+1],{status:'success',file_path:'/static/uploads/stale-'+action+'.png'});await settle();
 expect(requests.length===start+2,'stale batch continued to upload fragments');
 expect(document.getElementById('editContent').value===(action==='edit'?'后来的编辑':'原题文'),'stale batch replaced editor text');
 expect(!Object.keys(FigureLayoutState.imageLayouts).length,'stale batch registered layouts');
}
''')


def test_batch_maps_preview_boundaries_to_original_pixels_without_gaps():
    _run_js(r'''
const PreviousImage=Image;
Image=class extends PreviousImage {naturalWidth=2003;naturalHeight=997};
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'题文',image_path:'/static/uploads/original.png'});await settle();
button('contentOcrFigures','从原图追加截图').onclick();
const mode=document.getElementById('singleOcrCropMode');mode.value='2x2';mode.onchange();
const preview=document.getElementById('singleOcrCropCanvas');
expect(preview.width===1600,'large source preview was not bounded');
const draws=[];canvasContext.drawImage=(...args)=>{if(args.length===9) draws.push(args.slice(1))};
dragCrop();document.getElementById('singleOcrCropSave').onclick();await settle();
for(let i=0;i<4;i++) {reply(requests[i+1],{status:'success',file_path:'/static/uploads/native-'+i+'.png'});await settle();}
expect(draws.length===4,'batch did not create four native source crops');
expect(draws[0][0]+draws[0][2]===draws[1][0],'horizontal native-pixel boundary has a gap/overlap');
expect(draws[0][1]+draws[0][3]===draws[2][1],'vertical native-pixel boundary has a gap/overlap');
expect(draws[0][0]===Math.round(10*2003/preview.width),'source x coordinate used displayed pixels');
expect(draws[0][1]===Math.round(20*997/preview.height),'source y coordinate used displayed pixels');
''')


def test_single_placeholder_repair_cannot_become_a_four_image_replacement():
    _run_js(r'''
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'[插图待补: 图1]',image_path:'/static/uploads/original.png'});await settle();
button('contentOcrFigures','截图补入此处').onclick();
const mode=document.getElementById('singleOcrCropMode');
expect(mode.disabled,'single placeholder unexpectedly permits batch replacement');
mode.value='2x2';mode.onchange();
dragCrop();document.getElementById('singleOcrCropSave').onclick();await settle();
reply(requests[1],{status:'success',file_path:'/static/uploads/single.png'});await settle();
expect(requests.length===2,'single placeholder uploaded multiple fragments');
expect(document.getElementById('editContent').value==='![图1](/static/uploads/single.png)','single crop changed anchor semantics');
''')


def test_oversized_native_crop_and_unloaded_source_never_upload():
    _run_js(r'''
const PreviousImage=Image;
Image=class extends PreviousImage {naturalWidth=100000;naturalHeight=100000};
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'原题文',image_path:'/static/uploads/original.png'});await settle();
button('contentOcrFigures','从原图追加截图').onclick();
const mode=document.getElementById('singleOcrCropMode');mode.value='2x2';mode.onchange();
dragCrop();document.getElementById('singleOcrCropSave').onclick();await settle();
expect(requests.length===1,'native source crop bypassed the total pixel budget');
expect(document.getElementById('editContent').value==='原题文','oversized crop altered text');
document.getElementById('singleOcrCropClose').onclick();
Image=class extends PreviousImage {set src(value){this.source=value}};
button('contentOcrFigures','从原图追加截图').onclick();
canvasContext.drawImage=()=>{throw new Error('drew an unloaded source')};
const pendingMode=document.getElementById('singleOcrCropMode');pendingMode.value='4x1';pendingMode.onchange();
expect(document.getElementById('singleOcrCropSave').disabled,'unloaded source enabled crop submission');
document.getElementById('singleOcrCropClear').onclick();
''')


def test_crop_resize_redraw_is_scoped_and_removed_on_close():
    _run_js(r'''
let listener=null,removed=null;
window.addEventListener=(name,callback)=>{if(name==='resize')listener=callback};
window.removeEventListener=(name,callback)=>{if(name==='resize')removed=callback};
runContentOcr({type:'image/png'});
reply(requests[0],{status:'success',latex:'原题文',image_path:'/static/uploads/original.png'});await settle();
button('contentOcrFigures','从原图追加截图').onclick();
const mode=document.getElementById('singleOcrCropMode');mode.value='2x2';mode.onchange();dragCrop();
let draws=0;canvasContext.drawImage=()=>draws++;
expect(typeof listener==='function','crop did not track viewport resizing');
listener();expect(draws===1,'viewport resize did not redraw readable batch labels');
document.getElementById('singleOcrCropClose').onclick();
expect(removed===listener,'closed crop retained its resize listener');
listener();expect(draws===1,'a closed crop callback drew on a newer modal');
''')
