"""Show reading provenance in the real app; model-facing API traffic is stubbed."""
import json

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401


@pytest.mark.parametrize('width', [1600, 375])
@pytest.mark.parametrize('fallback', [False, True])
def test_reading_stage_and_local_limits_are_visible_outside_exam(browser, tmp_path, width, fallback):
    browser.command('reload')
    browser.command('set', 'viewport', str(width), '1000')
    browser.command('wait', '--fn', "typeof window.renderPaperWorkspace==='function'")
    analysis = ('实际模型请求2次：初筛完成；整题复读失败（未自动重发）。本地未复核补选1道。\n'
                '题库ID 测试：本地规则，未采信AI完整阅读结论；题图未提供或未获完整复读结论。'
                '未完整复读原因：当前型号未确认识图。' if fallback else
                '实际模型请求2次：初筛完成；整题复读完成。本地未复核补选0道。\n'
                '题库ID 测试：AI整题复读后选定；必要题图像素已完整提供。')
    browser.evaluate(r"""(async()=>{
      const form=new FormData();Object.entries({content:'整题阅读界面夹具：已知 $f(x)=x^2$。\n\n(1) 求导函数。\n\n(2) 求单调区间。',question_type:'detailed_answer',difficulty:'normal'}).forEach(([k,v])=>form.set(k,v));
      const created=await fetch('/api/questions',{method:'POST',body:form});const q=(await created.json()).question;
      const actualFetch=window.fetch.bind(window);window.__readingApiCalls=0;
      window.fetch=(url,options)=>{
        if(url==='/api/paper/ai-select'){
          window.__readingApiCalls++;
          return Promise.resolve(new Response(JSON.stringify({status:'success',data:[q],fallback:FALLBACK,
            selection_engine:FALLBACK?'local':'ai',model_used:'模拟同一型号',ai_analysis:ANALYSIS,
            warnings:FALLBACK?['整题复读失败；未自动重发，现为本地规则结果。']:[],
            selection_summary:{requested:1,selected:1,shortfall:0},
            selection_reading:{model_calls:2,review_required:true,selected:[{id:q.id,basis:FALLBACK?'local_unreviewed':'ai_full_review'}]}}),{status:200}));
        }return actualFetch(url,options);
      };
      PaperStore.cart=[];PaperStore.meta.paper_type='exam';PaperStore.aiReferenceMode='none';
      PaperStore.aiReferenceKey='';PaperStore.aiSelectionLimit=1;PaperStore.aiSelectionPrompt='';PaperStore.isAiExpanded=false;
      selectWorkspace('paper','智能组卷');await renderPaperWorkspace();setPaperMobilePane('questions');return true;
    })()""".replace('FALLBACK', json.dumps(fallback)).replace('ANALYSIS', json.dumps(analysis, ensure_ascii=False)))
    browser.command('snapshot', '-i')
    browser.command('click', '#togglePaperAiBtn')
    browser.command('fill', '#paperAiPromptInput', '选1道函数题，完整查看两个小问')
    browser.command('click', '#paperAiSelectBtn')
    browser.command('wait', '--fn', 'window.__readingApiCalls===1&&!PaperStore.aiSelectionBusy&&PaperStore.cart.length===1')
    browser.evaluate("setPaperMobilePane('preview');true")
    observed = browser.evaluate("({analysis:PaperStore.meta.ai_analysis,body:[...document.querySelectorAll('#a4PaperPreviewSheet .a4-paper-sheet')].map(node=>node.textContent).join(''),screen:document.getElementById('a4PaperPreviewSheet').textContent})")
    assert analysis in observed['analysis']
    assert '实际模型请求2次' in observed['screen'] and '整题复读' in observed['screen']
    assert '实际模型请求' not in observed['body'] and '未完整复读原因' not in observed['body']
    assert '求单调区间' in observed['body']
    if fallback:
        assert '未采信AI完整阅读结论' in observed['analysis'] and '当前型号未确认识图' in observed['screen']
    browser.command('screenshot', str(tmp_path / f'reading-{width}-fallback-{fallback}.png'))


def test_late_two_stage_response_cannot_join_a_changed_selection(browser):
    browser.command('reload')
    browser.command('set', 'viewport', '1600', '1000')
    browser.command('wait', '--fn', "typeof window.renderPaperWorkspace==='function'")
    browser.evaluate(r"""(async()=>{
      const actualFetch=window.fetch.bind(window);window.__lateReadingRequest=null;
      window.fetch=(url,options)=>url==='/api/paper/ai-select'
        ? new Promise(resolve=>window.__lateReadingRequest=resolve) : actualFetch(url,options);
      PaperStore.cart=[];PaperStore.meta={paper_type:'exam'};PaperStore.aiReferenceMode='none';
      PaperStore.aiSelectionPrompt='原始要求';PaperStore.aiSelectionLimit=1;PaperStore.isAiExpanded=false;
      selectWorkspace('paper','智能组卷');await renderPaperWorkspace();setPaperMobilePane('questions');return true;
    })()""")
    browser.command('click', '#togglePaperAiBtn')
    browser.command('click', '#paperAiSelectBtn')
    browser.command('wait', '--fn', "typeof window.__lateReadingRequest==='function'")
    browser.command('fill', '#paperAiPromptInput', '已经改变的要求')
    browser.evaluate("window.__lateReadingRequest(new Response(JSON.stringify({status:'success',data:[{id:99999,content:'过期完整复读结果',question_type:'single_choice'}],ai_analysis:'实际模型请求2次：整题复读完成',selection_summary:{requested:1,selected:1},selection_reading:{model_calls:2}}),{status:200}));true")
    browser.command('wait', '--fn', '!PaperStore.aiSelectionBusy')
    assert browser.evaluate('PaperStore.cart.length') == 0
    assert not browser.evaluate("String(PaperStore.meta.ai_analysis||'').includes('整题复读完成')")
