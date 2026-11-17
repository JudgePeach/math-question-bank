"""Opt-in AI paper controls in a disposable app, with all model calls stubbed."""
import json
import pytest

from test_sidebar_search_browser import search_browser as browser, pytestmark  # noqa: F401


@pytest.mark.parametrize("width,height", [(1600, 1100), (375, 812)])
def test_ai_selection_controls_retain_scope_and_stay_outside_exam(browser, width, height, tmp_path):
    browser.command("reload")
    browser.command("set", "viewport", str(width), str(height))
    browser.command("wait", "--fn", "typeof window.renderPaperWorkspace==='function'")
    keyword = f"智能选题浏览器测试{width}"
    selected_id = browser.evaluate(r"""(async()=>{
      const prefix=""" + json.dumps(keyword, ensure_ascii=False) + r""";
      const questions=[];
      for(const [source,answer] of [['隔离合成题','换元法'],['旧卷','换元法'],['新卷','直接求值']]){
        const form=new FormData();
        Object.entries({content:prefix+'：求 $1+2$。',question_type:'single_choice',difficulty:'easy',
          source,answer_markdown:answer}).forEach(([k,v])=>form.set(k,v));
        const created=await fetch('/api/questions',{method:'POST',body:form});const data=await created.json();
        if(!created.ok)throw Error(JSON.stringify(data));
        questions.push(data.question);
      }
      const actualFetch=window.fetch.bind(window);window.__referenceCalls=0;window.__selectionCalls=0;
      window.fetch=async(url,options)=>{
        if(url==='/api/paper/reference-exams'){
          window.__referenceCalls++;
          return new Response(JSON.stringify({status:'success',data:[{key:'mock2025',title:'2025年合成参考卷 <字面文本>',
            year:2025,question_count:19,type_counts:{single_choice:8},completeness_note:'合成测试范围'}]}),{status:200});
        }
        if(url==='/api/paper/ai-select'){
          window.__selectionCalls++;window.__selectionPayload=JSON.parse(options.body);
          return new Response(JSON.stringify({status:'success',data:[questions[0]],fallback:true,
            ai_analysis:'合成选题依据：参考阶梯；课标参考为合成条目，书内第42页，PDF第45页。此段只在管理区显示。',warnings:['当前范围可用题不足设置题量。'],
            selection_summary:{requested:2,selected:1,shortfall:1},reference_profile:{enabled:true,references:[{title:'2025年合成参考卷'}]}}),{status:200});
        }
        return actualFetch(url,options);
      };
      PaperStore.cart=[];PaperStore.meta.paper_type='exam';PaperStore.aiReferenceLoaded=false;
      PaperStore.aiReferenceExams=[];PaperStore.aiReferenceMode='recent_gaokao';PaperStore.aiReferenceKey='';
      PaperStore.aiSelectionLimit=5;PaperStore.aiSelectionPrompt='';PaperStore.isAiExpanded=false;
      selectWorkspace('paper','智能组卷');await renderPaperWorkspace();setPaperMobilePane('questions');return questions[0].id;
    })()""")
    browser.command("fill", "#paperFilterKeyword", keyword)
    browser.command("press", "Enter")
    browser.command("wait", "--fn", "PaperStore.streamPagination.all.total===3")
    geometry = r"""[...document.querySelectorAll('.paper-studio-body,#paperQuestionStream,.paper-preview-column')]
        .map(node=>{const {top,width,height}=node.getBoundingClientRect();return {top,width,height};})"""
    assert browser.evaluate("document.getElementById('paperAdvancedFilters').hidden")
    closed = browser.evaluate(geometry)
    browser.command("click", "#togglePaperFilterBtn")
    assert browser.evaluate(geometry) == closed
    assert browser.evaluate(r"""(()=>{
        const panel=document.getElementById('paperAdvancedFilters'), box=panel.getBoundingClientRect();
        return !panel.hidden && box.right<=innerWidth && box.bottom<=innerHeight &&
            panel.contains(document.elementFromPoint(box.left+10,box.top+10));
    })()""")
    if width > 960:
        browser.command("set", "viewport", "1024", "768")
        browser.command("wait", "--fn", r"""
            document.getElementById('paperAdvancedFilters').getBoundingClientRect().right
                <= document.querySelector('.paper-library-column').getBoundingClientRect().right + 1
        """)
        browser.command("set", "viewport", str(width), str(height))
    for index, field, operator, word in [(1, "answer_markdown", "include", "换元"), (2, "source", "exclude", "旧卷")]:
        row = f"#paperSearchRules > :nth-child({index})"
        browser.command("click", "#addPaperSearchRuleBtn")
        browser.command("select", row + " [data-rule-field]", field)
        browser.command("select", row + " [data-rule-operator]", operator)
        browser.command("fill", row + " input", word)
        browser.command("press", "Enter")
        browser.command("wait", "--fn", f"PaperStore.streamPagination.all.total==={3-index}")
    assert browser.evaluate("PaperStore.bankQuestions.map(q=>q.id)") == [selected_id]
    browser.command("screenshot", str(tmp_path / f"paper-filters-{width}.png"))
    with_rules = browser.evaluate(geometry)
    browser.command("press", "Escape")
    assert browser.evaluate("document.getElementById('paperAdvancedFilters').hidden && document.activeElement.id==='togglePaperFilterBtn'")
    assert browser.evaluate(geometry) == with_rules
    browser.command("click", "#togglePaperFilterBtn")
    if width < 960:
        browser.command("click", '[data-paper-pane="preview"]')
        assert browser.evaluate("!document.getElementById('paperFilterKeyword').getClientRects().length")
        browser.command("click", '[data-paper-pane="questions"]')
    else:
        browser.command("click", ".paper-preview-heading")
    assert browser.evaluate("document.getElementById('paperAdvancedFilters').hidden && document.querySelector('#paperSearchRules > :last-child input').value==='旧卷'")
    browser.command("snapshot", "-i")
    browser.command("click", "#togglePaperAiBtn")
    assert browser.evaluate("document.getElementById('paperAdvancedFilters').hidden && document.getElementById('paperFilterConditions').children.length===2")
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
    assert selected["payload"]["search_rules"] == [
        {"field": "answer_markdown", "operator": "include", "keyword": "换元"},
        {"field": "source", "operator": "exclude", "keyword": "旧卷"},
    ]
    assert "新增 1 / 2" in selected["analysis"] and "可用题不足" in selected["analysis"]
    assert "书内第42页" in selected["analysis"] and "课标参考" not in selected["body"]
    assert "智能选题浏览器测试" in selected["body"] and "合成选题依据" not in selected["body"]
    browser.command("click", "#paperFilterConditions .is-exclusion")
    browser.command("wait", "--fn", "PaperStore.streamPagination.all.total===2")
    assert browser.evaluate("PaperStore.cart.map(q=>q.id)") == [selected_id]
