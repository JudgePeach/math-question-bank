"""Opt-in real Chromium against a source-only app with deterministic model replies.

Set MATHBANK_TEST_BROWSER=1. Optional MATHBANK_PLAYWRIGHT_MODULE and
MATHBANK_CHROMIUM_EXECUTABLE select existing test tools, never app dependencies.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv("MATHBANK_TEST_BROWSER") != "1", reason="opt-in browser validation")


def test_chat_review_write_and_responsive_navigation(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the opt-in browser validation")
    module_path = os.getenv("MATHBANK_PLAYWRIGHT_MODULE", "")
    if not module_path:
        probe = subprocess.run([node, "-e", "require.resolve('playwright')"], capture_output=True, text=True)
        if probe.returncode != 0:
            pytest.skip("Set MATHBANK_PLAYWRIGHT_MODULE to an installed Playwright package")
    root = tmp_path / "app"
    root.mkdir()
    shutil.copy2(ROOT / "main.py", root / "main.py")
    for folder in ("mathbank", "static", "templates"):
        shutil.copytree(ROOT / folder, root / folder,
                        ignore=shutil.ignore_patterns("uploads", "test_uploads", "__pycache__", "*.pyc"))
    (root / "agent_test_app.py").write_text('''
import json
import time
import main

def stream_completion(messages):
    results = [m for m in messages if m['role'] == 'user' and m['content'].startswith('工具结果（资料')]
    original = next(m['content'] for m in reversed(messages) if m['role'] == 'user' and not m['content'].startswith('工具结果（资料'))
    if '添加标签' in original:
        if not results:
            time.sleep(0.5)
            action = {'tool':'get_question','arguments':{'id':1}}
        else:
            action = {'tool':'propose_changes','arguments':{'changes':[{'id':1,'patch':{'tags':'原有标签,合成新增','answer_markdown':'**合成解析**：$1+1=2$'},'reason':'**合成依据** <img src=x onerror=alert(1)>'}]}}
    elif '整理刚才' in original:
        if not results:
            assert any(m['content'].startswith('历史工具结果') and '"id": 2' in m['content'] for m in messages)
            action = {'tool':'get_questions','arguments':{'ids':[1,2]}}
        else:
            action = {'tool':'propose_changes','arguments':{'changes':[
                {'id':qid,'patch':{'difficulty':'challenge'},'reason':'合成批量测试'} for qid in [1,2]]}}
    elif '生成一次解析修改' in original:
        action = {'tool':'get_question','arguments':{'id':1}} if not results else {'tool':'propose_answer','arguments':{
            'changes':[{'id':1,'patch':{'answer_markdown':'**推导补充**：$x^2=1$'},'reason':'测试解析修改'}]}}
    elif '先组卷' in original:
        action = {'tool':'get_questions','arguments':{'ids':[1,2]}} if not results else {'tool':'propose_paper','arguments':{
            'title':'Agent合成小练','paper_type':'quiz','questions':[{'id':2,'score':12,'reason':'第二题'}]}}
    elif '先生成删除方案' in original:
        action = {'tool':'get_question','arguments':{'id':2}} if not results else {'tool':'propose_delete','arguments':{
            'questions':[{'id':2,'reason':'浏览器确认流程测试'}]}}
    elif '查找合成' in original:
        action = {'tool':'search_questions','arguments':{'q':'合成'}} if not results else {'reply':'找到合成题，可以继续整理。'}
    elif 'Markdown' in original:
        action = {'reply': json.loads((__import__('pathlib').Path(__file__).with_name('markdown_reply.json')).read_text(encoding='utf-8'))}
    elif not results:
        action = {'tool':'statistics','arguments':{}}
    else:
        action = {'reply':'统计完成。<script>window.agentInjected=true</script>'}
    encoded = json.dumps(action, ensure_ascii='Markdown' in original)
    for index in range(0, len(encoded), 7):
        yield encoded[index:index + 7]
        time.sleep(0.025)

main.LIBRARY_AGENT.stream_completion = stream_completion
app = main.app
''', encoding="utf-8")
    markdown_reply = r'''## 实时 Markdown

**正在生成**：$x_1<\frac{1}{2}$，$\iu^2=-1$。

比较式 $a<b>c$；$$\frac{a_1}{b_2}$$

\begin{align}a_1&=1\\b_2&=2\end{align}

- 第一项
- 第二项 *强调*

> 保留数学公式与 Markdown 排版。

| 表达式 | 结果 |
| --- | --- |
| $\frac{1}{2}$ | 0.5 |

```python
print("$x_1$ <script>alert(1)</script>")
```

[安全链接](https://example.invalid/math) [危险链接](javascript:alert(1))
![远程图片](https://example.invalid/secret.png)
<img src=x onerror="window.agentInjected=true">
<svg onload="window.agentInjected=true"></svg>

转义字符：引号 "、反斜杠 `\` 与 emoji 😀。公式不进入代码 `$x_1$`。
'''
    (root / 'markdown_reply.json').write_text(json.dumps(markdown_reply), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {key: value for key, value in os.environ.items() if not any(token in key.upper() for token in ("API_KEY", "PREFER_", "PYTHONPATH"))}
    env["MATHBANK_QA_ENABLED"] = "1"
    python = os.getenv("MATHBANK_TEST_PYTHON", sys.executable)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    evidence = ROOT / ".system_generated" / "agent-validation"
    evidence.mkdir(parents=True, exist_ok=True)
    script = r'''
const assert = require('node:assert/strict');
const {chromium} = require(process.env.MATHBANK_PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
  const browser = await chromium.launch({headless:true, ...(process.env.MATHBANK_CHROMIUM_EXECUTABLE ? {executablePath:process.env.MATHBANK_CHROMIUM_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    await page.goto(process.env.AGENT_TEST_URL);
    await page.waitForFunction(()=>typeof window.loadAgentConfig==='function'&&typeof window.renderPaperCanvas==='function');
    await page.click('#appNavAgent');
    await page.waitForSelector('#agentWorkspaceSection:not(.hidden)');
    const messages=page.locator('#agentMessages');
    const follow=page.locator('#agentFollowOutput');
    await follow.click();
    assert.equal(await follow.getAttribute('aria-pressed'),'false');
    assert.equal(await page.locator('#agentFollowLabel').textContent(),'跟随输出：暂停');
    await page.evaluate(()=>{
      const container=document.getElementById('agentMessages');
      for(let index=0;index<12;index++){
        const row=document.createElement('article');row.className='agent-message';row.style.minHeight='48px';row.textContent='滚动行为测试 '+index;container.append(row);
      }
    });
    await page.waitForFunction(()=>document.getElementById('agentMessages').scrollHeight>document.getElementById('agentMessages').clientHeight);
    const beforeManualOutput=await messages.evaluate(el=>el.scrollTop);
    await page.evaluate(()=>{
      const row=document.createElement('article');row.className='agent-message';row.style.minHeight='48px';row.textContent='新增内容保持阅读位置';
      document.getElementById('agentMessages').append(row);
    });
    await page.waitForTimeout(120);
    assert.ok(Math.abs(await messages.evaluate(el=>el.scrollTop)-beforeManualOutput)<3,'paused follow mode unexpectedly scrolled');
    await page.locator('#agentScrollBottom').click();
    await page.waitForFunction(()=>{
      const el=document.getElementById('agentMessages');
      return document.getElementById('agentFollowOutput').getAttribute('aria-pressed')==='true'
        && Math.abs(el.scrollHeight-el.clientHeight-el.scrollTop)<3;
    });
    await page.evaluate(()=>{
      const container=document.getElementById('agentMessages');
      container.scrollTop=0;container.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,bubbles:true}));
    });
    assert.equal(await follow.getAttribute('aria-pressed'),'false');
    await page.locator('#agentScrollBottom').click();
    await page.waitForFunction(()=>Math.abs(document.getElementById('agentMessages').scrollHeight-document.getElementById('agentMessages').clientHeight-document.getElementById('agentMessages').scrollTop)<3);
    await page.evaluate(()=>document.getElementById('agentMessages').replaceChildren());
    // Exercise the actual application's local-token middleware.
    assert.equal(await page.evaluate(async()=> (await fetch('/api/agent/chat',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':'invalid'},body:JSON.stringify({message:'x'})})).status),403);
    assert.equal(await page.evaluate(async()=> (await fetch('/api/agent/chat/stream',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':'invalid'},body:JSON.stringify({message:'x'})})).status),403);
    await page.evaluate(async()=>{
      for (const values of [
        {content:'合成测试题：求 $1+1$。',question_type:'detailed_answer',difficulty:'normal',tags:'原有标签',answer_markdown:'旧解析'},
        {content:'待删除的合成题',question_type:'detailed_answer',difficulty:'normal'}
      ]) {
        const form=new FormData(); Object.entries(values).forEach(([key,value])=>form.set(key,value));
        const response=await fetch('/api/questions',{method:'POST',headers:{'X-Local-Token':window.__localToken},body:form}); if(!response.ok)throw Error(await response.text());
      }
    });
    await page.evaluate(()=>{
      window.agentMutationEvents=0;
      window.addEventListener('mathbank-question-mutated',()=>window.agentMutationEvents++);
    });
    await page.click('#appNavAgent');
    await page.waitForSelector('#agentWorkspaceSection:not(.hidden)');
    await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/desktop.png',fullPage:true});
    await page.fill('#agentInput','请为题目添加标签');
    await page.click('#agentSend');
    await page.waitForFunction(()=>document.getElementById('agentForm').getAttribute('aria-busy')==='true');
    await page.fill('#agentInput','这是处理期间继续输入的内容');
    await page.waitForFunction(()=>document.querySelector('.agent-live-model')?.textContent.length>0);
    assert.equal(await page.locator('[data-agent-apply]').count(),0);
    assert.equal(await page.getAttribute('#agentForm','aria-busy'),'true');
    await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/stream-desktop.png',fullPage:true});
    await page.waitForSelector('[data-agent-apply]');
    assert.match(await page.locator('.agent-live-log').textContent(),/读取题目详情完成/);
    await page.waitForFunction(()=>{
      const el=document.getElementById('agentMessages');
      return Math.abs(el.scrollHeight-el.clientHeight-el.scrollTop)<3;
    });
    assert.equal(await page.inputValue('#agentInput'),'这是处理期间继续输入的内容');
    assert.equal(await page.locator('.agent-reason img').count(),0);
    assert.equal(await page.locator('.agent-reason strong').textContent(),'合成依据');
    assert.equal(await page.locator('.agent-field-preview .katex').count(),1);
    assert.match(await page.locator('.agent-source pre').last().textContent(),/\*\*合成解析\*\*/);
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/1')).json()).tags),'原有标签');
    await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/review-desktop.png',fullPage:true});
    for(const [width,dark,name] of [[375,false,'mobile'],[375,true,'mobile-dark']]){
      await page.setViewportSize({width,height:812});
      await page.evaluate(value=>{document.documentElement.classList.toggle('dark',value);document.body.classList.toggle('dark',value)},dark);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.equal(await page.locator('#appNavAgent').isVisible(),true);
      assert.equal(await page.evaluate(()=>document.getElementById('agentSend').getBoundingClientRect().width>=44),true);
      await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/'+name+'.png',fullPage:true});
    }
    await page.click('[data-agent-apply]');
    await page.waitForFunction(()=>document.querySelector('[data-agent-apply]').textContent==='已写入');
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/1')).json()).tags),'原有标签,合成新增');
    assert.equal(await page.evaluate(()=>window.agentMutationEvents>0),true);
    assert.equal(await page.inputValue('#agentInput'),'这是处理期间继续输入的内容');
    await page.click('#appNavImport');
    await page.click('#appNavAgent');
    assert.equal(await page.inputValue('#agentInput'),'这是处理期间继续输入的内容');
    await page.click('#agentNewChat');
    await page.fill('#agentInput','生成一次解析修改');
    await page.click('#agentSend');
    await page.waitForSelector('[data-agent-kind="update"]');
    assert.equal(await page.locator('.agent-field h4').last().textContent(),'解析');
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/1')).json()).answer_markdown),'**合成解析**：$1+1=2$');
    await page.click('[data-agent-apply]');
    await page.waitForFunction(()=>document.querySelector('[data-agent-apply]').textContent==='已写入');
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/1')).json()).answer_markdown),'**推导补充**：$x^2=1$');
    await page.click('#agentNewChat');
    await page.fill('#agentInput','先组卷，按模型给出的题序和分值生成一份小练');
    await page.click('#agentSend');
    await page.waitForSelector('[data-agent-kind="paper"]');
    assert.equal(await page.locator('[data-agent-kind="paper"] .agent-change').count(),1);
    assert.match(await page.locator('[data-agent-kind="paper"]').textContent(),/Agent合成小练.*12 分/s);
    await page.click('[data-agent-apply]');
    await page.waitForFunction(()=>document.querySelector('[data-agent-apply]').textContent==='已保存试卷');
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/papers/1')).json()).data.questions.map(item=>item.score).join(',')),'12');
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/2')).json()).usage_count),1);
    await page.click('#agentNewChat');
    await page.fill('#agentInput','先生成删除方案');
    await page.click('#agentSend');
    await page.waitForSelector('[data-agent-kind="delete"]');
    assert.match(await page.locator('.agent-delete-impact').first().textContent(),/已保存试卷.*删除/s);
    assert.equal(await page.evaluate(async()=> (await fetch('/api/questions/2')).ok),true);
    await page.click('[data-agent-apply]');
    await page.waitForFunction(()=>document.querySelector('[data-agent-apply]').textContent==='已删除');
    assert.equal(await page.evaluate(async()=> (await fetch('/api/questions/2')).status),404);
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/papers/1')).json()).data.total_score),0);
    assert.equal(await page.evaluate(()=>Boolean(window.EditorState.questionId)),false);
    await page.click('#agentNewChat');
    await page.fill('#agentInput','统计题量');
    await page.click('#agentSend');
    await page.waitForFunction(()=>document.getElementById('agentForm').getAttribute('aria-busy')==='false');
    assert.equal(await page.evaluate(()=>Boolean(window.agentInjected)),false);
    assert.equal(await page.locator('#agentMessages script').count(),0);
    assert.match(await page.locator('.agent-live-log').last().textContent(),/统计题库完成.*1 题/);
    await page.evaluate(async()=>{
      const form=new FormData(); Object.entries({content:'合成测试第二题',question_type:'detailed_answer',difficulty:'normal'}).forEach(([key,value])=>form.set(key,value));
      const response=await fetch('/api/questions',{method:'POST',body:form}); if(!response.ok)throw Error(await response.text());
    });
    await page.fill('#agentInput','查找合成题');
    await page.click('#agentSend');
    await page.waitForSelector('.agent-open-question',{state:'attached'});
    await page.locator('.agent-trace').last().locator('summary').click();
    await page.waitForSelector('.agent-open-question');
    await page.fill('#agentInput','整理刚才的题目难度');
    await page.click('#agentSend');
    await page.waitForSelector('[data-agent-apply]');
    assert.equal(await page.locator('.agent-change').count(),2);
    assert.match(await page.locator('.agent-live-log').last().textContent(),/批量读取题目详情完成/);
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/2')).json()).difficulty),'normal');
    await page.click('[data-agent-apply]');
    await page.waitForFunction(()=>document.querySelector('[data-agent-apply]').textContent==='已写入');
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/questions/2')).json()).difficulty),'challenge');
    // Opening a result uses its stable ID even when another record was deleted.
    await page.evaluate(async()=>{const response=await fetch('/api/questions/1',{method:'DELETE'});if(!response.ok)throw Error(await response.text())});
    await page.locator('.agent-open-question').first().click();
    await page.waitForFunction(()=>window.EditorState.questionId===2);
    assert.equal(await page.inputValue('#editContent'),'合成测试第二题');
    await page.click('#appNavAgent');
    // Repeated new conversations release server-side capacity immediately.
    for(let index=0;index<18;index++){
      await page.click('#agentNewChat');
      await page.fill('#agentInput','统计题量');
      await page.click('#agentSend');
      await page.waitForFunction(()=>document.getElementById('agentForm').getAttribute('aria-busy')==='false');
      assert.match(await page.locator('.agent-live-log').last().textContent(),/统计题库完成/);
    }
    await page.click('#agentNewChat');
    await page.fill('#agentInput','显示 Markdown 实时渲染');
    await page.click('#agentSend');
    const reply=page.locator('.agent-message-assistant').last().locator('.agent-message-text');
    await reply.locator('.katex').first().waitFor();
    assert.equal(await page.getAttribute('#agentForm','aria-busy'),'true');
    assert.equal(await reply.locator('strong').textContent(),'正在生成');
    assert.equal(await follow.getAttribute('aria-pressed'),'true');
    await page.evaluate(()=>document.getElementById('agentMessages').dispatchEvent(new WheelEvent('wheel',{deltaY:-120,bubbles:true})));
    assert.equal(await follow.getAttribute('aria-pressed'),'false');
    const pausedTop=await messages.evaluate(el=>el.scrollTop);
    await page.waitForFunction(()=>document.getElementById('agentForm').getAttribute('aria-busy')==='false');
    assert.ok(Math.abs(await messages.evaluate(el=>el.scrollTop)-pausedTop)<3,'streaming ignored the paused reading position');
    await page.locator('#agentScrollBottom').click();
    await page.waitForFunction(()=>Math.abs(document.getElementById('agentMessages').scrollHeight-document.getElementById('agentMessages').clientHeight-document.getElementById('agentMessages').scrollTop)<3);
    assert.equal(await reply.locator('h2').textContent(),'实时 Markdown');
    assert.equal(await reply.locator('li').count(),2);
    assert.equal(await reply.locator('table td .katex').count(),1);
    assert.equal(await reply.locator('.katex-error').count(),0);
    assert.equal(await reply.locator('.katex-display').count(),2);
    assert.equal(await reply.textContent().then(text=>/[\uE000-\uF8FF]/.test(text)),false);
    assert.equal(await reply.locator('annotation').allTextContents().then(items=>items.includes('a\\lt b\\gt c')),true);
    assert.equal(await reply.locator('pre .katex, code .katex, script, svg[onload], img').count(),0);
    assert.match(await reply.locator('pre code').textContent(),/\$x_1\$ <script>/);
    assert.equal(await reply.locator('a').count(),1);
    assert.equal(await reply.locator('a').getAttribute('rel'),'noopener noreferrer');
    assert.match(await reply.textContent(),/emoji 😀/);
    assert.equal(await page.evaluate(()=>Boolean(window.agentInjected)),false);
    for(const [width,dark,name] of [[1440,false,'markdown-desktop'],[375,true,'markdown-mobile-dark']]){
      await page.setViewportSize({width,height:width===375?812:1000});
      await page.evaluate(value=>{document.documentElement.classList.toggle('dark',value);document.body.classList.toggle('dark',value);document.getElementById('agentMessages').scrollTop=0},dark);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/'+name+'.png',fullPage:true});
    }
    await page.click('#appNavPaper');
    assert.equal(await page.locator('#agentWorkspaceSection').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,viewports:[1440,375],themes:['light','dark'],model:'deterministic fake',errors}));
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
'''
    with (root / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([python, "-u", "-m", "uvicorn", "agent_test_app:app", "--host", "127.0.0.1", "--port", str(port)],
                                  cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    with opener.open(url + "/healthz", timeout=1) as response:
                        if json.load(response).get("ready"):
                            break
                except (OSError, ValueError):
                    pass
                if server.poll() is not None:
                    pytest.fail((root / "server.log").read_text(encoding="utf-8"))
                time.sleep(.1)
            else:
                pytest.fail((root / "server.log").read_text(encoding="utf-8"))
            result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8",
                                    capture_output=True, timeout=110, env=dict(os.environ,
                                    AGENT_TEST_URL=url, AGENT_TEST_EVIDENCE=str(evidence)))
            assert result.returncode == 0, result.stdout + result.stderr + (root / "server.log").read_text(encoding="utf-8")[-4000:]
            (evidence / "result.json").write_text(result.stdout, encoding="utf-8")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
