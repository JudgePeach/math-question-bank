"""Executable scheduling regressions; real geometry stays in browser tests."""

from pathlib import Path
import shutil
import subprocess

from test_math_preview_regressions import run_preview_script

ROOT = Path(__file__).resolve().parents[1]


def test_choices_auto_width_does_not_use_source_length_as_a_column_limit():
    run_preview_script(r'''
const lengthy = String.raw`\begin{choices}\item $\underbrace{1}_{\text{a very long source annotation}}$\item $2$\item $3$\item $4$\end{choices}`;
const html = preprocessFormulaForKaTeX(lengthy);
assert(html.includes('data-preferred-columns="4"'));
assert.equal((html.match(/class="choices-label/g) || []).length, 4);
const explicit = preprocessFormulaForKaTeX(String.raw`\begin{choices}[columns=2,label-pos=left]\item $1$\item $2$\item $3$\item $4$\end{choices}`);
assert(explicit.includes('data-preferred-columns="2"'));
assert.equal((explicit.match(/class="choices-label/g) || []).length, 4);
assert(!explicit.includes('columns=2,label-pos=left]'));
assert.deepEqual(parsedFormulas(explicit), ['1', '2', '3', '4']);
''')


def test_choices_batch_shared_observer_and_coalesced_image_resize_callbacks():
    source = (ROOT / "static/js/editor.js").read_text(encoding="utf-8")
    start = source.index("        const choicesGridSizes = new WeakMap();")
    marker = "        window.adaptChoicesGridLayout = adaptChoicesGridLayout;"
    end = source.index(marker, start) + len(marker)
    script = r'''
const assert = require('node:assert/strict');
const events = [], frames = [], observers = [];
const window = {requestAnimationFrame(callback) { frames.push(callback); return frames.length; }};
class ResizeObserver {
    constructor(callback) {this.callback=callback; this.targets=new Set(); observers.push(this);}
    observe(target) {this.targets.add(target);}
    unobserve(target) {this.targets.delete(target);}
}
function makeGrid(name, width, natural, preferred=4, imageWidth=0) {
    const classes=new Set();
    const grid={name,width,isConnected:true,dataset:{preferredColumns:String(preferred)},columns:4,children:[],
        style:{setProperty(key,value) {assert.equal(key,'--choices-columns'); events.push(`set:${name}:${value}`);grid.columns=Number(value);}},
        closest() {return null;},
        querySelectorAll(selector) {
            if(selector==='.choices-math-scroll')return classes.has('choices-math-scroll')?[math]:[];
            if(selector==='.choices-content .katex')return [math];
            if(selector==='img')return image?[image]:[];
            throw Error(selector);
        }
    };
    Object.defineProperties(grid,{
        clientWidth:{get(){return grid.width;}},
        offsetWidth:{get(){events.push(`layout:${name}`);return grid.width;}},
        offsetHeight:{get(){return 30*Math.ceil(4/grid.columns);}}
    });
    const image=imageWidth?{complete:false,naturalWidth:imageWidth,listeners:{},addEventListener(event,callback){
        assert(!this.listeners[event]); this.listeners[event]=callback;
    }}:null;
    const content={
        get clientWidth(){return grid.width/grid.columns-20;},
        get scrollWidth(){events.push(`scroll:${name}:${grid.columns}`);return natural;},
        querySelectorAll(selector){assert.equal(selector,'img');return image?[image]:[];}
    };
    const math={offsetWidth:natural,closest(selector){return selector==='.choices-content'?content:null;},
        classList:{add(value){classes.add(value);},remove(value){classes.delete(value);},contains(value){return classes.has(value);}}
    };
    grid.children=Array.from({length:4},()=>({querySelector(){return content;}}));
    grid.math=math;grid.image=image;
    return grid;
}
function host(grids){return {querySelectorAll(){return grids;}};}
function resize(observer,grids) {observer.callback(grids.map(grid=>({target:grid,borderBoxSize:[{inlineSize:grid.offsetWidth,blockSize:grid.offsetHeight}]})));}
''' + source[start:end] + r'''
const short=makeGrid('short',600,60), medium=makeGrid('medium',600,180), wide=makeGrid('wide',200,300);
const image=makeGrid('image',600,50,4,220), explicit=makeGrid('explicit',600,60,2);
const grids=[short,medium,wide,image,explicit];
adaptChoicesGridLayout(host(grids));
assert.equal(observers.length,1);
assert.equal(observers[0].targets.size,5);
assert.deepEqual(grids.map(g=>g.dataset.choiceColumns),['4','2','1','2','2']);
assert(wide.math.classList.contains('choices-math-scroll'));
const firstRead=events.findIndex(e=>e.startsWith('scroll:'));
for(const g of [short,medium,wide,image])assert(events.indexOf(`set:${g.name}:4`)<firstRead,events);
resize(observers[0],grids);
assert.equal(frames.length,0,'our final size must not schedule another pass');
adaptChoicesGridLayout(host(grids));
assert.equal(observers.length,1);
short.width=300;medium.width=300;
resize(observers[0],[short,medium]);resize(observers[0],[short,medium]);
image.image.listeners.load();image.image.listeners.error();
assert.equal(frames.length,1,'multiple resize/image notifications share a frame');
frames.shift()();
assert.equal(frames.length,0);
resize(observers[0],grids);
assert.equal(frames.length,0,'the measurement converges after resize');
wide.isConnected=false;
adaptChoicesGridLayout(host([]));
assert.equal(observers[0].targets.size,4,'detached grids are unobserved even when the replacement has no choices');
assert(!observers[0].targets.has(wide));
short.width=200;resize(observers[0],[short]);short.isConnected=false;
frames.shift()();
assert.equal(observers[0].targets.size,3,'a pending detached target is pruned without laying it out');
'''
    node = shutil.which("node")
    assert node, "Node.js is required for option scheduling regressions"
    result = subprocess.run([node, "-"], input=script, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
