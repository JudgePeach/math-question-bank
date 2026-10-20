"""Paper source labels in the real, disposable browser workspace."""
import json
from pathlib import Path

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


@pytest.mark.parametrize("width,height", [(1600, 1100), (375, 812)])
def test_paper_sources_in_candidates_and_selection_stay_outside_exam(browser, width, height):
    source = ('来源核对：2026模拟卷 <em>原文</em> "甲&乙" ' + "高考数学参考资料 / " * 18).strip()
    browser.command("reload")
    browser.command("set", "viewport", str(width), str(height))
    browser.command("wait", "--fn", "typeof window.renderPaperWorkspace==='function'")
    questions = browser.evaluate(r"""(async()=>{
      const questions=[];
      for(const [index,source] of %s.entries()){
        const form=new FormData();Object.entries({content:'来源浏览器回归 '+innerWidth+' 题'+index+'：求 $1+2$。',
          source,question_type:'detailed_answer',difficulty:'normal'}).forEach(([k,v])=>form.set(k,v));
        const response=await fetch('/api/questions',{method:'POST',body:form});const data=await response.json();
        if(!response.ok)throw Error(JSON.stringify(data));questions.push(data.question);
      }
      PaperStore.cart=[{id:questions[0].id,score:12,solution_space:0},{id:questions[1].id,score:12,solution_space:0}];
      PaperStore.questionsMap=Object.fromEntries(questions.map(q=>[q.id,q]));
      PaperStore.filters.tab='all';PaperStore.filters.keyword='来源浏览器回归 '+innerWidth;
      PaperStore.streamPagination.all.page=1;PaperStore.streamPagination.selected.page=1;
      selectWorkspace('paper','组卷排版');await renderPaperWorkspace();setPaperMobilePane('questions');
      return questions;
    })()""" % json.dumps([source, "", "另一份试卷"], ensure_ascii=False))
    browser.command("wait", "--fn", "!PaperStore.cartQuestionLoad.loading&&!PaperStore.streamPagination.all.loading&&document.querySelectorAll('#paperQuestionStream .paper-resource-card').length===3")
    selector = f'[data-paper-question-id="{questions[0]["id"]}"] .paper-resource-source'
    browser.command("scrollintoview", selector)
    browser.settle()
    observed = browser.evaluate("""(()=>{
      const label=document.querySelector(%s),rect=label.getBoundingClientRect();
      return {text:label.textContent,tooltip:label.getAttribute('data-tooltip')||label.getAttribute('title'),
        markup:label.querySelector('em')!==null,ellipsis:getComputedStyle(label).textOverflow,
        right:rect.right,left:rect.left,overflow:document.documentElement.scrollWidth>innerWidth+1};
    })()""" % json.dumps(selector))
    assert source in observed["text"] and observed["tooltip"] == source
    assert not observed["markup"] and not observed["overflow"]
    assert observed["ellipsis"] == "ellipsis" and observed["right"] <= width + 1 and observed["left"] >= -1
    assert browser.evaluate(f"document.querySelector('[data-paper-question-id=\"{questions[1]['id']}\"] .paper-resource-source')===null")
    assert browser.evaluate("document.querySelectorAll('#paperQuestionStream .paper-resource-source').length") == 2
    evidence = Path(__file__).resolve().parents[1] / ".system_generated" / "paper-source-browser"
    evidence.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(evidence / f"sources-{width}.png"))
    selected_tab = "#paperQuestionStream button[onclick=\"switchPaperStreamTab('selected')\"]"
    browser.command("scrollintoview", selected_tab)
    browser.settle()
    browser.command("click", selected_tab)
    browser.command("wait", "--fn", "PaperStore.filters.tab==='selected'&&!PaperStore.cartQuestionLoad.loading")
    assert browser.evaluate("document.querySelectorAll('#paperQuestionStream .paper-resource-source').length") == 1
    assert source not in browser.evaluate("document.getElementById('a4PaperPreviewSheet').textContent")
