"""Dynamic references in a source-only disposable app and a real browser."""
import json
import os

import pytest

from test_preview_browser import browser


pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1",
    reason="opt-in real question-reference browser regression",
)


def test_references_follow_sequence_changes_and_guard_unsaved_edits(browser, tmp_path):
    fixture = browser.evaluate(r"""(async () => {
        selectWorkspace('bank','题库研讨工作台');
        const values=[
          {content:'引用专项的待删除占位题。'},
          {content:'引用专项目标题：计算 $1+2$。',answer_markdown:'目标答案为 $3$。',source:'引用浏览器测试书 | 例2.5.5'},
          {content:'😀题干参照例2.5.5。',answer_markdown:'😀参照例2.5.5，结论为 $x=1$。\n\n代码 `例2.5.5` 保持原样。',review:'类比例2.5.5。',source:'引用浏览器测试书 | 例2.5.6'}
        ];
        const questions=[];
        for(const value of values) {
          const form=new FormData();for(const [key,text] of Object.entries(value))form.set(key,text);
          form.set('question_type','detailed_answer');form.set('difficulty','medium');
          const response=await fetch('/api/questions',{method:'POST',body:form});const result=await response.json();
          if(!response.ok||!result.question)throw new Error(JSON.stringify(result));questions.push(result.question);
        }
        window.referenceBrowserFixture=questions;
        selectQuestionById(questions[2].id);
        return questions.map(q=>({id:q.id,seq_num:q.seq_num}));
    })()""")
    spare, target, referent = fixture
    browser.command("wait", "--fn", f"EditorState.questionId === {referent['id']} && !questionDetailLoading")
    original = browser.evaluate("document.getElementById('editAnswerMarkdown').value")
    preview_state = browser.evaluate(r"""(() => {
      const ids=['contentPreview','paperContent','answerPreview','paperAnalysisContent','paperReviewContent'];
      return Object.fromEntries(ids.map(id=>[id,{
        buttons:[...document.querySelectorAll('#'+id+' button.mb-question-reference')].map(n=>n.textContent),
        text:document.getElementById(id).textContent,
        errors:document.getElementById(id).querySelectorAll('.katex-error').length
      }]));
    })()""")
    reference_debug = browser.evaluate(r"""(() => {
      const text=document.getElementById('editAnswerMarkdown').value;
      const context=QuestionReferenceState.context('answer_markdown',text);
      return {context,prepared:QuestionReferenceTools.prepare(text,context)};
    })()""")
    for state in preview_state.values():
        assert state["buttons"] == [f"题库 #{target['seq_num']}"], {"preview": preview_state, "debug": reference_debug}
        assert state["errors"] == 0, preview_state
    assert "代码 `例2.5.5` 保持原样" in preview_state["answerPreview"]["text"]

    # Deleting an earlier ID changes the display sequence, not the stable target.
    assert browser.evaluate(f"fetch('/api/questions/{spare['id']}',{{method:'DELETE'}}).then(r=>r.ok)")
    expected_seq = target["seq_num"] - 1
    browser.command("wait", "--fn", f"document.querySelector('#answerPreview button.mb-question-reference')?.textContent === '题库 #{expected_seq}'")
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == original

    # Editing the field disables its old positions while another unchanged field
    # can still navigate through the normal unsaved-edit dialog.
    browser.evaluate(r"""(() => {
      const input=document.getElementById('editAnswerMarkdown');input.value+='新增的未保存内容';updateAnswerPreview();
      return true;
    })()""")
    assert browser.evaluate("document.querySelectorAll('#answerPreview button.mb-question-reference,#paperAnalysisContent button.mb-question-reference').length") == 0
    browser.evaluate("document.querySelector('#paperReviewContent button.mb-question-reference').click(); true")
    browser.command("wait", "--fn", "!!document.getElementById('cancelBtn')")
    browser.command("click", "#cancelBtn")
    assert browser.evaluate("EditorState.questionId") == referent["id"]
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value.endsWith('新增的未保存内容')")
    browser.evaluate("document.querySelector('#paperReviewContent button.mb-question-reference').click(); true")
    browser.command("wait", "--fn", "!!document.getElementById('discardBtn')")
    browser.command("click", "#discardBtn")
    browser.command("wait", "--fn", f"EditorState.questionId === {target['id']} && !questionDetailLoading")
    assert browser.evaluate("document.querySelectorAll('#answerPreview button.mb-question-reference').length") == 0

    # Real details seed the paper stream; references survive its separate answer
    # cache and each expand refreshes their displayed sequence.
    browser.evaluate(f"""(async () => {{
      startNewQuestion();selectWorkspace('paper','组卷排版工作台');
      const q=await fetch('/api/questions/{referent['id']}').then(r=>r.json());
      PaperStore.questionsMap[q.id]=q;PaperStore.bankQuestions=[q];
      PaperStore.cart=[{{id:q.id,score:5,solution_space:0}}];
      Object.assign(PaperStore.streamPagination.all,{{page:1,total:1,totalPages:1,loading:false,error:''}});
      await switchPaperStreamTab('all');renderPaperCanvas();togglePaperQuestionAnswer(q.id);return true;
    }})()""")
    browser.command("wait", "--fn", f"!PaperStore.answerLoadingIds.has({referent['id']}) && !!document.querySelector('#paper-q-answer-content-{referent['id']} button.mb-question-reference')")
    assert browser.evaluate(f"document.querySelector('#paper-q-answer-content-{referent['id']} button.mb-question-reference').textContent") == f"题库 #{expected_seq}"
    assert browser.evaluate(f"document.querySelectorAll('#paper-q-render-{referent['id']} button.mb-question-reference').length") == 1
    assert browser.evaluate(f"document.querySelectorAll('#a4PaperPreviewSheet [data-qid=\"{referent['id']}\"] button.mb-question-reference').length") == 1

    # Deleting the target must never silently link to another reused sequence.
    assert browser.evaluate(f"fetch('/api/questions/{target['id']}',{{method:'DELETE'}}).then(r=>r.ok)")
    browser.command("wait", "--fn", f"!PaperStore.answerLoadingIds.has({referent['id']}) && document.getElementById('paper-q-answer-content-{referent['id']}').textContent.includes('目标已删除')")
    assert browser.evaluate(f"document.querySelectorAll('#paper-q-answer-content-{referent['id']} button.mb-question-reference').length") == 0
    assert browser.evaluate(f"document.getElementById('paper-q-render-{referent['id']}').textContent.includes('目标已删除')")
    assert browser.evaluate(f"document.querySelectorAll('#paper-q-render-{referent['id']} button.mb-question-reference').length") == 0
    browser.command("wait", "--fn", f"document.querySelector('#a4PaperPreviewSheet [data-qid=\"{referent['id']}\"]')?.textContent.includes('目标已删除')")
    assert browser.evaluate(f"document.querySelectorAll('#a4PaperPreviewSheet [data-qid=\"{referent['id']}\"] button.mb-question-reference').length") == 0
    browser.command("screenshot", str(tmp_path / "question-references-paper.png"))
    (tmp_path / "question-references.json").write_text(json.dumps(preview_state, ensure_ascii=False, indent=2))


def test_forged_reference_html_remains_inert(browser):
    result = browser.evaluate(r"""(() => {
      const text='例2.5.5 <button data-question-reference-key="1" onclick="window.__referenceInjection=true">伪按钮</button> $x=1$。';
      const host=document.createElement('div');renderQuestionPreviewContent(host,text);
      return {controls:host.querySelectorAll('button,[onclick],[data-question-reference-key]').length,
        math:host.querySelectorAll('.katex').length,text:host.textContent};
    })()""")
    assert result["controls"] == 0
    assert result["math"] == 1
    assert "伪按钮" in result["text"]


def test_live_reference_controls_survive_detached_preview_cleanup(browser):
    result = browser.evaluate(r"""(() => {
      const source='参照例2.5.5。';
      const reference={field:'answer_markdown',start:2,end:8,text:'例2.5.5',label_key:'例2.5.5',
        status:'resolved',target_question_id:1,seq_num:1};
      let validated=false;
      const context={field:'answer_markdown',source,references:[reference],isCurrent:()=>{validated=true;return false;}};
      const host=document.createElement('div');host.innerHTML=parseMarkdownWithMath(source,{},context);document.body.appendChild(host);
      const button=host.querySelector('button.mb-question-reference');
      for(let index=0;index<4100;index++)parseMarkdownWithMath(source,{},context);
      button.click();host.remove();return validated;
    })()""")
    assert result is True


def test_paper_reference_refresh_retries_after_an_old_inflight_response(browser):
    referent_id = browser.evaluate("referenceBrowserFixture[2].id")
    # A screenshot/focus from the previous test may have started a real refresh.
    # Finish it before installing a controlled pending-response fixture.
    browser.evaluate("PaperStore.expandedAnswerIds.clear(); PaperStore.cart=[]; PaperStore.bankQuestions=[]; true")
    browser.command("wait", "--fn", f"!PaperStore.answerLoadingIds.has({referent_id})")
    browser.evaluate(f"""(async () => {{
      startNewQuestion();selectWorkspace('paper','组卷排版工作台');
      const q=await fetch('/api/questions/{referent_id}').then(r=>r.json());
      q.question_references=q.question_references.map(r=>({{...r,status:'resolved',target_question_id:99999,seq_num:33}}));
      PaperStore.questionsMap[q.id]=q;PaperStore.bankQuestions=[q];
      PaperStore.expandedAnswerIds.clear();PaperStore.answerCache=Object.create(null);
      Object.assign(PaperStore.streamPagination.all,{{page:1,total:1,totalPages:1,loading:false,error:''}});
      await switchPaperStreamTab('all');
      const originalFetch=window.fetch;
      window.referenceRefreshFixture={{originalFetch,q,pending:[],requests:0}};
      window.fetch=function(url,...args) {{
        if(String(url)!=='/api/questions/{referent_id}')return originalFetch.call(this,url,...args);
        referenceRefreshFixture.requests++;
        if(referenceRefreshFixture.requests===1)return new Promise(resolve=>referenceRefreshFixture.pending.push(resolve));
        const fresh={{...q,question_references:q.question_references.map(r=>({{...r,seq_num:44}}))}};
        return Promise.resolve(new Response(JSON.stringify(fresh),{{status:200}}));
      }};
      togglePaperQuestionAnswer(q.id);
      window.dispatchEvent(new Event('mathbank-question-mutated'));
      return true;
    }})()""")
    try:
        browser.evaluate("referenceRefreshFixture.pending.shift()(new Response(JSON.stringify(referenceRefreshFixture.q),{status:200})); true")
        browser.command("wait", "--fn", f"!PaperStore.answerLoadingIds.has({referent_id}) && document.querySelector('#paper-q-answer-content-{referent_id} button.mb-question-reference')?.textContent === '题库 #44'")
        assert browser.evaluate("referenceRefreshFixture.requests") >= 2
    finally:
        browser.evaluate("window.fetch=referenceRefreshFixture.originalFetch; PaperStore.expandedAnswerIds.clear(); true")


def test_range_references_resolve_and_delete_each_member_independently(browser):
    fixture = browser.evaluate(r"""(async () => {
      startNewQuestion();selectWorkspace('bank','题库研讨工作台');
      const create=async value=>{
        const form=new FormData();for(const [key,text] of Object.entries(value))form.set(key,text);
        form.set('question_type','detailed_answer');form.set('difficulty','medium');
        const response=await fetch('/api/questions',{method:'POST',body:form});const data=await response.json();
        if(!response.ok||!data.question)throw new Error(JSON.stringify(data));return data.question;
      };
      const a=await create({content:'范围专项第一道目标。',source:'范围浏览器测试书 | 例1.1.1'});
      const b=await create({content:'范围专项第二道目标。',source:'范围浏览器测试书 | 例1.1.2'});
      const q=await create({content:'范围专项引用题。',source:'范围浏览器测试书 | 例1.1.4',
        answer_markdown:`😀范围说明。

参照例1.1.1～例1.1.3可得结论。`});
      window.rangeReferenceFixture={a,b,q,create};selectQuestionById(q.id);return {a,b,q};
    })()""")
    qid = fixture["q"]["id"]
    browser.command("wait", "--fn", f"EditorState.questionId === {qid} && !questionDetailLoading")
    original = browser.evaluate("document.getElementById('editAnswerMarkdown').value")
    expected = [f"例1.1.1：题库 #{fixture['a']['seq_num']}", f"例1.1.2：题库 #{fixture['b']['seq_num']}"]
    assert browser.evaluate("[...document.querySelectorAll('#answerPreview button.mb-question-reference')].map(n=>n.textContent)") == expected
    assert browser.evaluate("document.getElementById('answerPreview').textContent.includes('例1.1.3：待关联')")
    third = browser.evaluate("rangeReferenceFixture.create({content:'范围专项第三道目标。',source:'范围浏览器测试书 | 例1.1.3'})")
    browser.command("wait", "--fn", "document.querySelectorAll('#answerPreview button.mb-question-reference').length === 3")
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == original
    assert browser.evaluate(f"fetch('/api/questions/{fixture['b']['id']}',{{method:'DELETE'}}).then(r=>r.ok)")
    browser.command("wait", "--fn", "document.getElementById('answerPreview').textContent.includes('例1.1.2：目标已删除')")
    assert browser.evaluate("[...document.querySelectorAll('#answerPreview button.mb-question-reference')].map(n=>n.textContent)") == [expected[0], f"例1.1.3：题库 #{third['seq_num'] - 1}"]
    assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == original
    assert browser.evaluate("document.querySelectorAll('#paperAnalysisContent button.mb-question-reference').length") == 2


def test_old_reference_refresh_cannot_overwrite_a_completed_put(browser):
    fixture = browser.evaluate(r"""(async () => {
      startNewQuestion();selectWorkspace('bank','题库研讨工作台');
      const create=async value=>{
        const form=new FormData();for(const [key,text] of Object.entries(value))form.set(key,text);
        form.set('question_type','detailed_answer');form.set('difficulty','medium');
        const response=await fetch('/api/questions',{method:'POST',body:form});const data=await response.json();
        if(!response.ok||!data.question)throw new Error(JSON.stringify(data));return data.question;
      };
      const a=await create({content:'迟到引用专项第一道目标。',source:'迟到引用浏览器书 | 例3.1.1'});
      const b=await create({content:'迟到引用专项第二道目标。',source:'迟到引用浏览器书 | 例3.1.2'});
      const q=await create({content:'迟到引用专项源题。',source:'迟到引用浏览器书 | 例3.1.3',answer_markdown:'参照例3.1.1可得结论。'});
      window.lateReferenceFixture={a,b,q};selectQuestionById(q.id);return {a,b,q};
    })()""")
    qid = fixture["q"]["id"]
    browser.command("wait", "--fn", f"EditorState.questionId === {qid} && !questionDetailLoading")
    browser.evaluate(f"""(async () => {{
      const old=await fetch('/api/questions/{qid}').then(r=>r.json());
      const originalFetch=window.fetch;Object.assign(lateReferenceFixture,{{old,originalFetch,pending:null,intercepted:false,targetReads:0}});
      window.fetch=function(url,options,...args) {{
        if(String(url)==='/api/questions/{fixture['a']['id']}'&&(!options||!options.method))lateReferenceFixture.targetReads++;
        if(String(url)==='/api/questions/{qid}'&&(!options||!options.method)&&!lateReferenceFixture.intercepted) {{
          lateReferenceFixture.intercepted=true;return new Promise(resolve=>lateReferenceFixture.pending=resolve);
        }}
        return originalFetch.call(this,url,options,...args);
      }};
      lateReferenceFixture.oldButton=document.querySelector('#answerPreview button.mb-question-reference');
      lateReferenceFixture.refresh=refreshCurrentQuestionReferences();
      lateReferenceFixture.oldButton.click();
      document.getElementById('editAnswerMarkdown').value='参照例3.1.2可得结论。';updateAnswerPreview();
      return true;
    }})()""")
    try:
        assert browser.evaluate("lateReferenceFixture.targetReads") == 0
        assert browser.evaluate("saveQuestion(true)") is True
        browser.command("wait", "--fn", f"document.querySelector('#answerPreview button.mb-question-reference')?.textContent === '题库 #{fixture['b']['seq_num']}'")
        stale_result = browser.evaluate("lateReferenceFixture.pending(new Response(JSON.stringify(lateReferenceFixture.old),{status:200})); lateReferenceFixture.refresh")
        assert stale_result is False
        assert browser.evaluate("document.getElementById('editAnswerMarkdown').value") == "参照例3.1.2可得结论。"
        assert browser.evaluate("document.querySelector('#answerPreview button.mb-question-reference').textContent") == f"题库 #{fixture['b']['seq_num']}"
        assert browser.evaluate("EditorState.questionId") == qid
    finally:
        browser.evaluate("window.fetch=lateReferenceFixture.originalFetch; true")


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_reference_canvas_refresh_preserves_focused_title_and_ime(browser, field):
    fixture = browser.evaluate(r"""(async () => {
      startNewQuestion();selectWorkspace('paper','组卷排版工作台');
      const field=""" + json.dumps(field) + r""";
      const book='标题输入浏览器书-'+field;
      const create=async value=>{
        const form=new FormData();for(const [key,text] of Object.entries(value))form.set(key,text);
        form.set('question_type','detailed_answer');form.set('difficulty','medium');
        const response=await fetch('/api/questions',{method:'POST',body:form});const data=await response.json();
        if(!response.ok||!data.question)throw new Error(JSON.stringify(data));return data.question;
      };
      const spare=await create({content:'标题输入占位题-'+field+'。'});
      const target=await create({content:'标题输入引用目标-'+field+'。',source:book+' | 例4.1.1'});
      const q=await create({content:'标题输入源题-'+field+'：参照例4.1.1。',source:book+' | 例4.1.2'});
      PaperStore.cart=[{id:q.id,score:5,solution_space:0}];PaperStore.questionsMap[q.id]=q;
      PaperStore.bankQuestions=[q];PaperStore.expandedAnswerIds.clear();
      Object.assign(PaperStore.streamPagination.all,{page:1,total:1,totalPages:1,loading:false,error:''});
      await switchPaperStreamTab('all');renderPaperCanvas();
      const title=document.querySelector('#a4PaperPreviewSheet .canvas-meta-'+field);
      title.focus();title.textContent='正在编写的数学试卷';title.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
      const selection=window.getSelection(),range=document.createRange();range.setStart(title.firstChild,5);range.collapse(true);
      selection.removeAllRanges();selection.addRange(range);
      window.referenceTitleFixture={field,title,spare,target,q,offset:5,text:title.textContent};
      return {spare,target,q};
    })()""")
    qid = fixture["q"]["id"]
    assert browser.evaluate(f"fetch('/api/questions/{fixture['spare']['id']}',{{method:'DELETE'}}).then(r=>r.ok)")
    browser.command("wait", "--fn", f"!PaperStore.answerLoadingIds.has({qid}) && document.querySelector('#paper-q-render-{qid} button.mb-question-reference')?.textContent === '题库 #{fixture['target']['seq_num'] - 1}'")
    focused = browser.evaluate(r"""(() => {
      const {title,offset,text,field}=referenceTitleFixture;
      return {connected:title.isConnected,same:title===document.querySelector('#a4PaperPreviewSheet .canvas-meta-'+field),
        active:document.activeElement===title,offset:window.getSelection().anchorOffset,
        sameAnchor:window.getSelection().anchorNode===title.firstChild,text:title.textContent,meta:PaperStore.meta[field]};
    })()""")
    assert focused == {"connected": True, "same": True, "active": True, "offset": 5,
                       "sameAnchor": True, "text": "正在编写的数学试卷", "meta": "正在编写的数学试卷"}
    browser.evaluate(r"""(() => {
      const title=referenceTitleFixture.title;
      title.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'中'}));
      title.firstChild.textContent+='中文';
      title.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertCompositionText',data:'中文',isComposing:true}));
      const range=document.createRange();range.setStart(title.firstChild,title.firstChild.textContent.length);range.collapse(true);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      referenceTitleFixture.offset=selection.anchorOffset;return true;
    })()""")
    assert browser.evaluate(f"fetch('/api/questions/{fixture['target']['id']}',{{method:'DELETE'}}).then(r=>r.ok)")
    browser.command("wait", "--fn", f"!PaperStore.answerLoadingIds.has({qid}) && document.getElementById('paper-q-render-{qid}').textContent.includes('目标已删除')")
    assert browser.evaluate("referenceTitleFixture.title.isConnected && document.activeElement===referenceTitleFixture.title && window.getSelection().anchorNode===referenceTitleFixture.title.firstChild && window.getSelection().anchorOffset===referenceTitleFixture.offset")
    assert browser.evaluate(f"PaperStore.meta.{field}") == "正在编写的数学试卷中文"
    # Blur can precede compositionend. The node must survive until both the
    # focused input and its unfinished composition have safely completed.
    browser.evaluate("referenceTitleFixture.title.blur(); new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))")
    assert browser.evaluate("referenceTitleFixture.title.isConnected")
    browser.evaluate("referenceTitleFixture.title.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'中文'})); true")
    browser.command("wait", "--fn", "!referenceTitleFixture.title.isConnected")
    assert browser.evaluate(f"document.querySelector('#a4PaperPreviewSheet .canvas-meta-{field}').textContent") == "正在编写的数学试卷中文"
    assert browser.evaluate(f"document.querySelector('#a4PaperPreviewSheet [data-qid=\"{qid}\"]').textContent.includes('目标已删除')")
