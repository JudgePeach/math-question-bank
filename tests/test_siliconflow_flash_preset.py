"""Model-preset changes must preserve saved Qwen and unrelated gateway IDs."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_current_siliconflow_flash_preset_preserves_saved_and_transit_models():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for isolated model-selector execution")
    source = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
    start = source.index("        const BAILIAN_MODEL_PRESETS_BY_TASK")
    end = source.index("        // 挂载辅助方法到全局", start)
    script = r"""
const assert = require('node:assert/strict');
const storage = new Map([
  ['custom_models_siliconflow', JSON.stringify(['deepseek-ai/DeepSeek-V4-Flash'])],
  ['custom_models_zhongzhan_gpt', JSON.stringify(['deepseek-ai/DeepSeek-V4-Flash'])],
]);
const beforeStorage = Array.from(storage.entries());
globalThis.localStorage={getItem:key=>storage.get(key)||null,setItem:()=>{throw Error('unexpected settings write')}};
globalThis.window={};
const elements=new Map();
globalThis.document={getElementById:id=>elements.get(id)||null};
globalThis.fetch=()=>{throw Error('unexpected network')};
globalThis.MathBankSafe={escapeAttribute:value=>String(value),escapeText:value=>String(value)};
""" + source[start:end] + r"""
const current='deepseek-ai/DeepSeek-V4.1-Flash';
const old='deepseek-ai/DeepSeek-V4-Flash';
for(const task of ['solve','parse','classify','draw']) {
  assert.ok(getPresetModels('siliconflow',task).includes(current));
  assert.ok(!getPresetModels('siliconflow',task).includes(old));
  const container={innerHTML:''};elements.set(task+'ModelValueContainer',container);
  renderModelSelector(task,'siliconflow','Qwen/Qwen3.8-27B');
  assert.ok(container.innerHTML.includes('value="Qwen/Qwen3.8-27B" selected'));
  renderModelSelector(task,'siliconflow',old);
  assert.ok(container.innerHTML.includes('value="'+old+'" selected'),'saved legacy choices stay visible');
  elements.set(task+'ModelProvider',{value:'siliconflow'});
  window.onModelProviderChange(task);
  assert.ok(container.innerHTML.includes('value="'+current+'" selected'));
}
const ocr=getModelListForProvider('siliconflow','ocr');
assert.ok(ocr.includes('Qwen/Qwen3.8-27B'));
assert.ok(ocr.includes(current),'verified V4.1 Flash vision preset is available');
assert.deepEqual(ocr.filter(name=>name.toLowerCase().includes('deepseek')),[current]);
elements.set('ocrModelProvider',{value:'siliconflow'});
elements.set('ocrModelValueContainer',{innerHTML:''});
window.onModelProviderChange('ocr');
assert.ok(elements.get('ocrModelValueContainer').innerHTML.includes('value="Qwen/Qwen3-VL-8B-Instruct" selected'));
for(const gateway of ['zhongzhan_gpt','zhongzhan_claude']) {
  const parsed=parseModelConfig(gateway.toUpperCase()+'/'+old,gateway,'');
  assert.equal(parsed.model,old);
  renderModelSelector('parse',gateway,old);
  assert.ok(elements.get('parseModelValueContainer').innerHTML.includes('value="'+old+'"'));
}
assert.deepEqual(Array.from(storage.entries()),beforeStorage);
"""
    result = subprocess.run([node, "-e", "eval(require('node:fs').readFileSync(0,'utf8'))"],
        input=script, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
