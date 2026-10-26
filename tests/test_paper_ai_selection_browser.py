"""Opt-in AI paper controls in a disposable app, with all model calls stubbed."""
import json
from pathlib import Path

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


@pytest.mark.parametrize("width,height", [(1600, 1100), (375, 812)])
def test_ai_selection_controls_retain_scope_and_stay_outside_exam(browser, width, height):
    browser.command("reload")
    browser.command("set", "viewport", str(width), str(height))
    browser.command("wait", "--fn", "typeof window.renderPaperWorkspace==='function'")
    browser.evaluate(r"""(async()=>{
      const form=new FormData();Object.entries({content:'智能选题浏览器测试：求 $1+2$。',
        question_type:'single_choice',difficulty:'easy',source:'隔离合成题'}).forEach(([k,v])=>form.set(k,v));
      const created=await fetch('/api/questions',{method:'POST',body:form});const data=await created.json();
      if(!created.ok)throw Error(JSON.stringify(data));
      const actualFetch=window.fetch.bind(window);window.__referenceCalls=0;window.__selectionCalls=0;
      window.fetch=async(url,options)=>{
        if(url==='/api/paper/reference-exams'){
          window.__referenceCalls++;
          return new Response(JSON.stringify({status:'success',data:[{key:'mock2025',title:'2025年合成参考卷 <字面文本>',
            year:2025,question_count:19,type_counts:{single_choice:8},completeness_note:'合成测试范围'}]}),{status:200});
        }
        if(url==='/api/paper/ai-select'){
          window.__selectionCalls++;window.__selectionPayload=JSON.parse(options.body);
          return new Response(JSON.stringify({status:'success',data:[data.question],fallback:true,
            ai_analysis:'合成选题依据：参考阶梯；课标参考为合成条目，书内第42页，PDF第45页。此段只在管理区显示。',warnings:['当前范围可用题不足设置题量。'],
            selection_summary:{requested:2,selected:1,shortfall:1},reference_profile:{enabled:true,references:[{title:'2025年合成参考卷'}]}}),{status:200});
        }
        return actualFetch(url,options);
      };
      PaperStore.cart=[];PaperStore.meta.paper_type='exam';PaperStore.aiReferenceLoaded=false;
      PaperStore.aiReferenceExams=[];PaperStore.aiReferenceMode='recent_gaokao';PaperStore.aiReferenceKey='';
      PaperStore.aiSelectionLimit=5;PaperStore.aiSelectionPrompt='';PaperStore.isAiExpanded=false;
      selectWorkspace('paper','智能组卷');await renderPaperWorkspace();setPaperMobilePane('questions');return true;
    })()""")
    browser.command("snapshot", "-i")
    browser.command("click", "#togglePaperAiBtn")
    browser.command("wait", "--fn", "PaperStore.aiReferenceLoaded&&!PaperStore.aiReferenceLoading")
    browser.command("fill", "#paperAiPromptInput", "函数专题，重视基础与迁移")
    browser.command("fill", "#paperAiLimitInput", "2")
    browser.command("select", "#paperAiReferenceKey", "mock2025")
    browser.command("click", "#togglePaperAiBtn")
    browser.command("click", "#togglePaperAiBtn")
    browser.command("wait", "--fn", "!document.getElementById('paperAiDetails').hidden")
    observed = browser.evaluate(r"""(()=>{
      const ids=['paperAiPromptInput','paperAiLimitInput','paperAiReferenceMode','paperAiReferenceKey','paperAiSelectBtn'];
      return {values:ids.slice(0,4).map(id=>document.getElementById(id).value),calls:__referenceCalls,
        help:document.querySelector('#paperAiDetails .paper-ai-help').textContent,
        inputCount:document.querySelectorAll('#paperAiDetails input,#paperAiDetails select').length,
        overflow:document.documentElement.scrollWidth>innerWidth+1,
        controls:ids.map(id=>{const rect=document.getElementById(id).getBoundingClientRect();return {id,left:rect.left,right:rect.right};})};
    })()""")
    assert observed["values"] == ["函数专题，重视基础与迁移", "2", "recent_gaokao", "mock2025"]
    assert observed["calls"] == 1 and not observed["overflow"]
    assert observed["inputCount"] == 4 and "自动参考相关课标" in observed["help"]
    assert all(control["left"] >= -1 and control["right"] <= width + 1 for control in observed["controls"])
    browser.command("scrollintoview", "#paperAiSelectBtn")
    browser.command("click", "#paperAiSelectBtn")
    browser.command("wait", "--fn", "__selectionCalls===1&&!PaperStore.aiSelectionBusy&&PaperStore.cart.length===1")
    selected = browser.evaluate("({payload:__selectionPayload,analysis:PaperStore.meta.ai_analysis,body:[...document.querySelectorAll('#a4PaperPreviewSheet .a4-paper-sheet')].map(node=>node.textContent).join('')})")
    assert selected["payload"]["limit"] == 2 and selected["payload"]["exclude_ids"] == []
    assert selected["payload"]["reference_key"] == "mock2025"
    assert "新增 1 / 2" in selected["analysis"] and "可用题不足" in selected["analysis"]
    assert "书内第42页" in selected["analysis"] and "课标参考" not in selected["body"]
    assert "智能选题浏览器测试" in selected["body"] and "合成选题依据" not in selected["body"]
    evidence = Path(__file__).resolve().parents[1] / ".system_generated" / "paper-ai-browser"
    evidence.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(evidence / f"ai-selection-{width}.png"))
