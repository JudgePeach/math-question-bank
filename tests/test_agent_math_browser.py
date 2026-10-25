"""Assistant question previews and streamed TeX against a source-only app."""
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


def test_assistant_choices_in_proposals_and_streamed_markdown(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser validation")
    module = os.getenv("MATHBANK_PLAYWRIGHT_MODULE", "playwright")
    probe = subprocess.run([node, "-e", "require(process.argv[1])", module], capture_output=True)
    if probe.returncode:
        pytest.skip("Set MATHBANK_PLAYWRIGHT_MODULE to an existing Playwright package")
    root = tmp_path / "app"
    root.mkdir()
    shutil.copy2(ROOT / "main.py", root / "main.py")
    for folder in ("mathbank", "static", "templates"):
        shutil.copytree(ROOT / folder, root / folder,
                        ignore=shutil.ignore_patterns("uploads", "test_uploads", "__pycache__", "*.pyc"))
    question = r"""$(1-3\iu)^2=$（ ）
\begin{choices}
\item $-8+6\iu$
\item $-8-6\iu$
\item $8+6\iu$
\item $8-6\iu$
\end{choices}"""
    wide = r"""\begin{choices}
\item $\frac{abcdefghijklmnopqrstuvwxyz+abcdefghijklmnopqrstuvwxyz}{\sqrt{1+x^2}}$
\item 这是用于验证窄屏自动换行的较长文字选项，必须完整显示每一段说明。
\item $\wideparen{AB}=\ang{30}$
\item $\eu^{\iu x}=1$
\end{choices}"""
    reply = "**实时题目**\n\n" + question + "\n\n**继续核对**\n\n" + wide + r"""

\begin{tabular}{cc}
项目 & 数学 \\
甲 & $\frac{1}{2}$ \\
\end{tabular}

代码保持原样：
```latex
\begin{choices}
\item $x_1$
\end{choices}
```

`\item $y_2$` 与 $a<b>c$。

<img src=x onerror="window.agentInjected=true">
"""
    (root / "fixtures.json").write_text(json.dumps({"question": question, "wide": wide, "reply": reply}), encoding="utf-8")
    (root / "agent_math_test_app.py").write_text('''
import json
import time
from pathlib import Path
import main
from mathbank.database import Question, SessionLocal

fixtures = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))
with SessionLocal() as db:
    db.add(Question(id=1, content=fixtures["question"], question_type="single_choice",
                    source="合成复数题", answer_markdown=fixtures["question"], difficulty="normal"))
    for qid in range(2, 20):
        kind = 'single_choice' if qid <= 8 else 'multi_choice' if qid <= 11 else 'fill_in_blank' if qid <= 14 else 'detailed_answer'
        db.add(Question(id=qid, content='合成高考题 $x^2$ ' + str(qid), question_type=kind))
    db.commit()

def stream_completion(messages):
    results = [m for m in messages if m["role"] == "user" and m["content"].startswith("工具结果（资料")]
    original = next(m["content"] for m in messages if m["role"] == "user" and not m["content"].startswith("工具结果（资料"))
    if "19" in original and not results:
        encoded = ('{}</｜｜DSML｜｜ parameter>'
                   '<｜｜DSML｜｜ parameter name="tool" string="true">statistics</｜｜DSML｜｜ parameter>'
                   '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>')
        for start in range(0, len(encoded), 3):
            yield encoded[start:start+3]
            time.sleep(.01)
        return
    if "DSML" in original and not results:
        encoded = '<｜｜DSML｜｜ calls>'
        for theme in ('函数', '数列', '立体几何', '概率', '圆锥曲线'):
            encoded += '<｜｜DSML｜｜ invoke name="tool"><｜｜DSML｜｜ parameter name="arguments" string="false">'
            encoded += json.dumps({'q':theme,'offset':0}, ensure_ascii=False)
            encoded += '</｜｜DSML｜｜ parameter><｜｜DSML｜｜ parameter name="tool" string="true">search_questions</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke>'
        encoded += '</｜｜DSML｜｜ calls>'
        for start in range(0, len(encoded), 23):
            yield encoded[start:start+23]
            time.sleep(.01)
        return
    if "19" in original:
        if len(results) == 1:
            action = {"tool": "get_questions", "arguments": {"ids": list(range(1, 11))}}
        elif len(results) == 2:
            action = {"tool": "get_questions", "arguments": {"ids": list(range(11, 20))}}
        else:
            scores = [5]*8 + [6]*3 + [5]*3 + [13,15,15,17,17]
            action = {"tool": "propose_paper", "arguments": {"title": "合成高考卷19", "paper_type": "exam_19",
                "questions": [{"id": qid, "score": scores[qid-1]} for qid in range(1, 20)]}}
    elif "DSML" in original:
        action = {"reply": "五个主题检索已完成"}
    elif "实时" in original:
        action = {"reply": fixtures["reply"]}
    elif not results:
        action = {"tool": "get_question", "arguments": {"id": 1}}
    elif "删除" in original:
        action = {"tool": "propose_delete", "questions": [{"id": 1, "reason": "仅验证预览，不执行删除"}]}
    elif "组卷" in original:
        action = {"tool": "propose_paper", "arguments": {"title": "合成卷", "questions": [{"id": 1}]}}
    else:
        action = {"tool": "propose_answer", "arguments": {"changes": [{"id": 1,
            "patch": {"answer_markdown": fixtures["wide"]}, "reason": "验证解析前后预览"}]}}
    encoded = json.dumps(action, ensure_ascii=False)
    for start in range(0, len(encoded), 23):
        yield encoded[start:start+23]
        time.sleep(.025)

main.LIBRARY_AGENT.stream_completion = stream_completion
app = main.app
''', encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {key: value for key, value in os.environ.items() if not any(
        term in key.upper() for term in ("API_KEY", "PREFER_", "PYTHONPATH", "TOKEN", "SECRET", "PASSWORD"))}
    evidence = ROOT / ".system_generated" / "agent-math-validation"
    evidence.mkdir(parents=True, exist_ok=True)
    script = r'''
const assert = require('node:assert/strict');
const {chromium} = require(process.env.MATHBANK_PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.MATHBANK_CHROMIUM_EXECUTABLE?{executablePath:process.env.MATHBANK_CHROMIUM_EXECUTABLE}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(process.env.AGENT_TEST_URL);
    await page.waitForFunction(()=>typeof window.loadAgentConfig==='function'&&typeof window.renderPaperCanvas==='function');
    await page.click('#appNavAgent');
    await page.waitForFunction(()=>document.getElementById('agentModel').textContent.includes('工具调用不限次数'));
    async function send(message) {
      await page.fill('#agentInput',message);await page.click('#agentSend');
      await page.waitForFunction(()=>document.getElementById('agentForm').getAttribute('aria-busy')==='false');
    }
    async function assertChoices(preview,count) {
      assert.equal(await preview.locator('.choices-item').count(),count);
      assert.equal(await preview.locator('.katex-error').count(),0);
      assert.equal(await preview.locator('.choices-label').allTextContents().then(items=>items.join(',')),Array.from({length:count},(_,i)=>'ABCD'[i%4]+'.').join(','));
      assert.equal(await preview.textContent().then(text=>/\\(?:(?:begin|end)\{choices\}|item\b)/.test(text)),false);
      assert.equal(await preview.textContent().then(text=>/[\uE000-\uF8FF]/.test(text)),false);
      const bounds=await preview.locator('.choices-content').evaluateAll(items=>items.map(el=>({width:el.clientWidth,scroll:el.scrollWidth})));
      assert.ok(bounds.every(box=>box.width>0),'choices were measured while detached');
    }
    await send('DSML五主题检索');
    assert.equal(await page.locator('.agent-trace summary').textContent(),'已执行 5 次题库查询 / 校验');
    assert.equal(await page.locator('.agent-message-assistant').last().locator('.agent-message-text').textContent().then(text=>text.trim()),'五个主题检索已完成');
    assert.doesNotMatch(await page.locator('#agentStatus').textContent(),/未完成/);
    await page.click('#agentNewChat');
    await send('组一套含19道题的卷子，符合高考题格式');
    const exam=page.locator('[data-agent-kind="paper"]');
    assert.equal(await exam.locator('h3').textContent(),'待确认：组卷 19 题');
    assert.equal(await exam.locator(':scope > details').count(),19);
    assert.match(await exam.textContent(),/150 分/);
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/papers')).json()).data.length),0);
    assert.doesNotMatch(await page.locator('#agentStatus').textContent(),/未完成/);
    await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/dsml-continuation-exam19.png',fullPage:true});
    await page.click('#agentNewChat');
    await send('生成删除方案');
    const deletion=page.locator('[data-agent-kind="delete"] .agent-field-preview');
    await assertChoices(deletion,4);
    assert.equal(await deletion.locator('.choices-content .katex').count(),4);
    assert.equal(await deletion.locator('annotation').allTextContents().then(items=>items.includes('-8-6\\iu')),true);
    assert.equal(await page.evaluate(async()=> (await fetch('/api/questions/1')).status),200);
    for(const [width,dark,name] of [[1440,false,'delete-desktop'],[375,true,'delete-mobile-dark']]){
      await page.setViewportSize({width,height:width===375?812:1000});
      await page.evaluate(dark=>document.documentElement.classList.toggle('dark',dark),dark);
      await deletion.scrollIntoViewIfNeeded();
      await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/'+name+'.png',fullPage:true});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await assertChoices(deletion,4);
    }
    await page.click('#agentNewChat');
    await send('生成组卷方案');
    await assertChoices(page.locator('[data-agent-kind="paper"] .agent-field-preview'),4);
    await page.click('#agentNewChat');
    await send('修改解析');
    const previews=page.locator('[data-agent-kind="update"] .agent-field-preview');
    assert.equal(await previews.count(),2);
    await assertChoices(previews.nth(0),4);await assertChoices(previews.nth(1),4);
    // Exercise resizing after the panel has mounted and the observer registered.
    await page.setViewportSize({width:1440,height:1000});
    await previews.nth(1).scrollIntoViewIfNeeded();
    await page.waitForTimeout(100);
    await page.setViewportSize({width:375,height:812});
    await page.waitForFunction(()=>document.querySelectorAll('[data-agent-kind="update"] .choices-grid')[1]?.dataset.choiceColumns==='1');
    const mathScroll=previews.nth(1).locator('.choices-math-scroll');
    assert.equal(await mathScroll.count(),1);
    assert.ok(await mathScroll.evaluate(el=>{el.scrollLeft=el.scrollWidth;return el.scrollLeft>0;}),'wide formula cannot scroll to its end');
    await page.click('#agentNewChat');
    await page.fill('#agentInput','实时输出公式');await page.click('#agentSend');
    const reply=page.locator('.agent-message-assistant').last().locator('.agent-message-text');
    await reply.locator('.choices-grid').first().waitFor();
    assert.equal(await page.getAttribute('#agentForm','aria-busy'),'true');
    assert.equal(await reply.locator('.choices-grid').first().locator('.choices-item').count(),4);
    await page.waitForFunction(()=>document.getElementById('agentForm').getAttribute('aria-busy')==='false');
    assert.equal(await reply.locator('.choices-grid').count(),2);
    assert.equal(await reply.locator('.choices-item').count(),8);
    assert.equal(await reply.locator('.katex-error').count(),0);
    assert.equal(await reply.locator('table td .katex').count(),1);
    assert.equal(await reply.locator('pre .katex,code .katex').count(),0);
    assert.match(await reply.locator('pre code').textContent(),/\\begin\{choices\}[\s\S]*\$x_1\$/);
    assert.equal(await reply.locator('img[onerror],script').count(),0);
    assert.equal(await page.evaluate(()=>Boolean(window.agentInjected)),false);
    for(const [width,dark,name] of [[1440,false,'stream-desktop'],[375,true,'stream-mobile-dark']]){
      await page.setViewportSize({width,height:width===375?812:1000});
      await page.evaluate(dark=>document.documentElement.classList.toggle('dark',dark),dark);
      await reply.scrollIntoViewIfNeeded();
      await page.screenshot({path:process.env.AGENT_TEST_EVIDENCE+'/'+name+'.png',fullPage:true});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,choices:['delete','paper','answer-before','answer-after','stream'],viewports:[1440,375],errors}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
'''
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with (root / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, "-B", "-u", "-m", "uvicorn", "agent_math_test_app:app",
                                   "--host", "127.0.0.1", "--port", str(port)], cwd=root, env=env,
                                  stdout=log, stderr=subprocess.STDOUT,
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
                pytest.fail("Isolated app did not become ready")
            result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True,
                                    timeout=90, env=dict(env, AGENT_TEST_URL=url, AGENT_TEST_EVIDENCE=str(evidence)))
            assert result.returncode == 0, result.stdout + result.stderr
            (evidence / "result.json").write_text(result.stdout, encoding="utf-8")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
