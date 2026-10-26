"""Actual XeLaTeX failures remain inspectable in the isolated browser UI."""

import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from test_preview_browser import browser as source_browser
from mathbank.paths import SYSTEM_GENERATED_DIR


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path(os.environ.get("MATHBANK_PDF_EXPORT_EVIDENCE_DIR", "")) if os.environ.get(
    "MATHBANK_PDF_EXPORT_EVIDENCE_DIR") else SYSTEM_GENERATED_DIR / "test_artifacts" / "pdf-export-browser"
pytestmark = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_BROWSER") != "1" or not shutil.which("xelatex"),
    reason="opt-in source-only browser and native XeLaTeX regression",
)


@pytest.fixture(scope="module")
def pdf_browser(tmp_path_factory):
    # The shared fixture copies only source/templates/static and creates its
    # own DB. Explicitly blank inherited provider keys as well as omitting .env.
    blank_keys = {key: "" for key in os.environ if "API_KEY" in key}
    blank_keys.update({key: "" for key in (
        "DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY", "ALI_BAILIAN_API_KEY",
        "ZHONGZHAN_API_KEY", "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    )})
    blank_keys["PREFER_PARSE_MODEL"] = "deepseek-flash"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, blank_keys, clear=False):
        yield from source_browser.__wrapped__(tmp_path_factory)


def _put_question_on_paper(browser, content):
    question = browser.evaluate(r"""(async()=>{
      const form=new FormData();
      Object.entries({content:CONTENT,question_type:'fill_in_blank',difficulty:'normal'})
        .forEach(([key,value])=>form.set(key,value));
      const response=await fetch('/api/questions',{method:'POST',body:form});
      const data=await response.json();if(!response.ok)throw Error(JSON.stringify(data));
      const question=data.question;
      PaperStore.meta={...PaperStore.meta,title:'PDF导出错误浏览器验收',subtitle:'',paper_type:'quiz',
        show_notice:false,show_secret:false};
      PaperStore.cart=[{id:question.id,score:5,solution_space:0}];
      PaperStore.questionsMap={[question.id]:question};
      selectWorkspace('paper','组卷排版');await renderPaperWorkspace();
      if(typeof setPaperMobilePane==='function')setPaperMobilePane('preview');
      return question;
    })()""".replace("CONTENT", json.dumps(content, ensure_ascii=False)))
    browser.command("wait", "--fn", "!PaperStore.cartQuestionLoad.loading && !!document.querySelector(\"button[onclick=\\\"exportPaperPdf('paper')\\\"]\")")
    browser.evaluate(r"""(()=>{
      if(!window.__exportEvidenceFetch){
        window.__exportEvidenceFetch=window.fetch;
        window.fetch=async(...args)=>{
          const response=await window.__exportEvidenceFetch(...args);
          if(String(args[0]).startsWith('/api/paper/export/')){
            window.__lastExportEvidence={url:String(args[0]),status:response.status,
              data:await response.clone().json().catch(()=>null)};
          }
          return response;
        };
      }
      window.__lastExportEvidence=null;
      if(!window.__nativePdfOpen){
        window.__nativePdfOpen=window.open.bind(window);
        window.open=(...args)=>{const popup=window.__nativePdfOpen(...args);window.__pdfErrorPopup=popup;return popup;};
      }
      window.__pdfErrorPopup=null;
      return true;
    })()""")
    return question


def _assert_core_error(text, question_id, bad_fragment):
    assert "卷面第 1 题" in text
    assert f"题库题目 ID {question_id}" in text
    assert bad_fragment in text
    assert "题内候选片段" in text


def _screenshot_real_popup(browser, root_tab, title, filename):
    tabs = browser.command("tab")["tabs"]
    popup_tab = next((tab["tabId"] for tab in tabs
                      if tab["tabId"] != root_tab and tab["title"] == title), None)
    if popup_tab:
        browser.command("tab", popup_tab)
        browser.command("snapshot", "-i")
        browser.command("screenshot", str(EVIDENCE / filename))
        browser.command("tab", root_tab)
        browser.command("tab", "close", popup_tab)
        return
    # Some agent-browser versions do not enumerate the application's native
    # window.open popup. Its real same-origin DOM was asserted above; render
    # that exact generated document in a CLI-managed evidence tab for pixels.
    popup_html = browser.evaluate("window.__pdfErrorPopup.document.documentElement.outerHTML")
    browser.command("tab", "new", "about:blank")
    evidence_tab = next(tab["tabId"] for tab in browser.command("tab")["tabs"] if tab["active"])
    browser.evaluate("document.open();document.write(" + json.dumps(popup_html) + ");document.close();true")
    browser.command("snapshot", "-i")
    browser.command("screenshot", str(EVIDENCE / filename))
    browser.command("tab", root_tab)
    browser.command("tab", "close", evidence_tab)
    browser.evaluate("if(window.__pdfErrorPopup&&!window.__pdfErrorPopup.closed)window.__pdfErrorPopup.close();true")


def test_real_pdf_missing_glyph_opens_detailed_error_page_and_preserves_source(pdf_browser):
    browser = pdf_browser
    content = "浏览器缺字回归：在△ABC中，∠ACB=90°，特殊字符🦄需要核对。"
    question = _put_question_on_paper(browser, content)
    root_tab = next(tab["tabId"] for tab in browser.command("tab")["tabs"] if tab["active"])
    browser.command("snapshot", "-i")
    selector = 'button[onclick="exportPaperPdf(\'paper\')"]'
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)
    browser.command("tab", root_tab)
    browser.command("wait", "--fn", "!!window.__lastExportEvidence && !!window.__pdfErrorPopup && window.__pdfErrorPopup.document.body.innerText.includes('PDF 导出缺字')")
    observed = browser.evaluate("({body:__pdfErrorPopup.document.body.innerText,evidence:__lastExportEvidence,title:__pdfErrorPopup.document.title})")
    _assert_core_error(observed["body"], question["id"], "特殊字符🦄需要核对")
    assert r"$\triangle ABC$" in observed["body"]
    assert r"$\angle ACB$" in observed["body"]
    assert "本地诊断结果" in observed["body"]
    diagnostic = observed["evidence"]["data"]["diagnostic"]
    assert observed["evidence"]["status"] == 400
    assert diagnostic["ai_used"] is False
    assert diagnostic["kind"] == "missing_glyph"
    assert diagnostic["character"] == "🦄"
    _screenshot_real_popup(browser, root_tab, observed["title"], "missing-glyph-popup.png")
    stored = browser.evaluate(f"fetch('/api/questions/{question['id']}').then(r=>r.json())")
    assert stored["content"] == content
    assert not (browser.root / ".env").exists()


def test_real_bundle_missing_glyph_keeps_an_inspectable_error_panel(pdf_browser):
    browser = pdf_browser
    content = "浏览器归档缺字回归：在△ABC中，∠ACB=90°，归档缺字🦄请核对。"
    question = _put_question_on_paper(browser, content)
    browser.command("snapshot", "-i")
    selector = 'button[onclick="exportPaperBundle()"]'
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)
    browser.command("wait", "--fn", "!!document.querySelector('#paperCompileErrorPanel[role=alert]') && !!window.__lastExportEvidence")
    observed = browser.evaluate("({text:document.getElementById('paperCompileErrorPanel').innerText,evidence:__lastExportEvidence})")
    _assert_core_error(observed["text"], question["id"], "归档缺字🦄请核对")
    assert r"$\triangle ABC$" in observed["text"]
    assert r"$\angle ACB$" in observed["text"]
    assert observed["evidence"]["status"] == 400
    assert observed["evidence"]["data"]["diagnostic"]["ai_used"] is False
    assert "试卷正文" in observed["evidence"]["data"]["message"]
    browser.command("scrollintoview", "#paperCompileErrorPanel")
    browser.settle()
    browser.command("screenshot", str(EVIDENCE / "bundle-missing-glyph-panel.png"))
    assert browser.evaluate("(()=>{const r=document.getElementById('paperCompileErrorPanel').getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(document.getElementById('paperCompileErrorPanel')).display!=='none';})()")
    assert browser.evaluate(f"fetch('/api/questions/{question['id']}').then(r=>r.json()).then(q=>q.content)") == content


def test_real_undefined_command_without_api_still_displays_source_evidence(pdf_browser):
    browser = pdf_browser
    content = r"浏览器无API回归：求值 $\doesnotexist{x}$。"
    question = _put_question_on_paper(browser, content)
    root_tab = next(tab["tabId"] for tab in browser.command("tab")["tabs"] if tab["active"])
    browser.command("snapshot", "-i")
    selector = 'button[onclick="exportPaperPdf(\'paper\')"]'
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)
    browser.command("tab", root_tab)
    browser.command("wait", "--fn", "!!window.__lastExportEvidence && !!window.__pdfErrorPopup && window.__pdfErrorPopup.document.body.innerText.includes('doesnotexist') && window.__pdfErrorPopup.document.body.innerText.includes('未配置')")
    observed = browser.evaluate("({text:__pdfErrorPopup.document.body.innerText,evidence:__lastExportEvidence,title:__pdfErrorPopup.document.title})")
    _assert_core_error(observed["text"], question["id"], r"\doesnotexist{x}")
    assert "本地诊断结果" in observed["text"]
    assert "试卷拆解模型未配置" in observed["text"]
    assert observed["evidence"]["data"]["diagnostic"]["ai_used"] is False
    assert observed["evidence"]["data"]["diagnostic"]["command"] == r"\doesnotexist"
    _screenshot_real_popup(browser, root_tab, observed["title"], "no-api-undefined-command-popup.png")
    assert browser.evaluate(f"fetch('/api/questions/{question['id']}').then(r=>r.json()).then(q=>q.content)") == content
