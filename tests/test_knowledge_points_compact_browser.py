"""Compact filter and knowledge tree UX in a disposable, source-only app.

Opt in with MATHBANK_TEST_BROWSER=1. The shared browser fixture copies source
assets only, creates an empty SQLite database, and never contacts a real model.
"""
import json
from pathlib import Path

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".system_generated/audits/knowledge-picker-20261017"
BOOK = "必修一"
CHAPTER = "1. 集合与常用逻辑用语"
SECTION = "1.2 集合间的基本关系"
HOST = "#bankKnowledgePointPicker"
POPUP = "#mb-kp-picker-bank"
METRICS = []


def reset_bank(browser, width=1600, dark=False):
    assert browser.root.resolve() != ROOT.resolve()
    assert not (browser.root / ".env").exists()
    browser.command("reload")
    browser.command("set", "viewport", str(width), "1100" if width > 600 else "812")
    browser.command("wait", "--fn", "window.activeCurriculumVersion==='A' && typeof window.KnowledgePointState==='object' && document.getElementById('filterCompulsory').options.length>1")
    browser.evaluate("selectWorkspace('bank','题库管理');clearBankFilters();document.documentElement.classList.toggle('dark',%s);true" % str(dark).lower())
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy')")
    assert browser.evaluate("loadKnowledgePointCatalog().then(items=>items.length)") >= 632
    if not browser.evaluate("document.querySelector('.bank-filter-toggle').getAttribute('aria-expanded')==='true'"):
        browser.command("click", ".bank-filter-toggle")
    browser.command("select", "#filterCompulsory", BOOK)
    browser.command("select", "#filterChapter", CHAPTER)
    browser.command("select", "#filterKnowledge", SECTION)
    browser.command("select", "#filterType", "single_choice")
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy')")
    browser.command("scrollintoview", HOST)
    browser.settle()


def open_picker(browser):
    if not browser.evaluate("document.querySelector(%s).open" % json.dumps(POPUP)):
        browser.command("click", HOST + " .mb-kp-toggle")
    browser.command("wait", "--fn", "document.querySelector(%s).open&&!document.querySelector(%s).hidden" % (json.dumps(POPUP), json.dumps(POPUP)))
    browser.settle()


def click_scope(browser, name):
    selector = POPUP + " .mb-kp-tools button:nth-child(" + ("2" if name == "全库目录" else "1") + ")"
    assert browser.evaluate("document.querySelector(%s).textContent" % json.dumps(selector)) == name
    browser.command("click", selector)
    browser.settle()


def location(browser):
    return browser.evaluate("['filterCompulsory','filterChapter','filterKnowledge'].map(id=>document.getElementById(id).value)")


def selected(browser):
    return browser.evaluate("KnowledgePointState.filter('bank').knowledge_point_codes")


def expand_path(browser, *path):
    for index in range(1, len(path) + 1):
        key = json.dumps(["A", *path[:index]], ensure_ascii=False, separators=(",", ":"))
        selector = POPUP + " .mb-kp-tree-toggle[data-tree-key=" + json.dumps(key, ensure_ascii=False) + "]"
        assert browser.evaluate("!!document.querySelector(%s)" % json.dumps(selector)), {"missing": selector}
        if browser.evaluate("document.querySelector(%s).getAttribute('aria-expanded')!=='true'" % json.dumps(selector)):
            browser.command("scrollintoview", selector)
            browser.command("click", selector)
            browser.settle()


def point_selector(code):
    return POPUP + ' .mb-kp-option[data-code=' + json.dumps(code) + ']'


def click_point(browser, code, index=0):
    selector = point_selector(code)
    path = browser.evaluate("document.querySelectorAll(%s)[%d]?.dataset.treePath||''" % (json.dumps(selector), index))
    if path:
        selector += '[data-tree-path=' + json.dumps(path, ensure_ascii=False) + ']'
    browser.command("scrollintoview", selector)
    browser.settle()
    browser.command("click", selector)
    browser.settle()


def capture(browser, name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if "tree" in name:
        browser.evaluate("document.querySelector(%s).scrollTop=0;true" % json.dumps(POPUP + " .mb-kp-options"))
        browser.settle()
    browser.command("screenshot", str(EVIDENCE / (name + ".png")))
    metrics = browser.evaluate("""(()=>{const host=document.querySelector(%s),picker=document.querySelector(%s),toggle=host.querySelector('.mb-kp-toggle'),
      header=host.querySelector('.mb-kp-header'),panel=picker.querySelector('.mb-kp-panel');
      const rect=node=>node?.getBoundingClientRect().toJSON();
      return {viewport:innerWidth,dark:document.documentElement.classList.contains('dark'),host:rect(host),header:rect(header),
        toggle:rect(toggle),picker:rect(picker),panel:rect(panel),open:picker.open,
        summary:[...document.querySelectorAll('#bankFilterConditions,#bankKnowledgePointConditions')].map(n=>({id:n.id,hidden:n.hidden,visible:n.checkVisibility(),rect:rect(n)})),
        tree:[...picker.querySelectorAll('.mb-kp-tree-toggle')].filter(n=>n.checkVisibility()).length,
        horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1};})()""" % (json.dumps(HOST), json.dumps(POPUP)))
    METRICS.append({"capture": name, **metrics})
    (EVIDENCE / "layout-metrics.json").write_text(json.dumps(METRICS, ensure_ascii=False, indent=2))
    assert not metrics["horizontalOverflow"], metrics
    return metrics


@pytest.fixture(scope="module")
def catalog_examples(browser):
    reset_bank(browser)
    points = browser.evaluate("""(async()=>{const result=[];
      for(const payload of [
        {name:'隔离验收：跨节集合关系',curriculum_paths:[
          {version_code:'A',compulsory:%s,chapter:%s,knowledge:'1.1 集合的概念'},
          {version_code:'A',compulsory:%s,chapter:%s,knowledge:%s}]},
        {name:'隔离验收：未关联教材的知识点',curriculum_paths:[]}]){
        const response=await fetch('/api/knowledge-points',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const body=await response.json();if(!response.ok)throw Error(JSON.stringify(body));result.push(body);}
      await loadKnowledgePointCatalog(true);return result;})()""" % tuple(json.dumps(x, ensure_ascii=False) for x in [BOOK, CHAPTER, BOOK, CHAPTER, SECTION]))
    assert len(points) == 2 and all(point["code"].startswith("local:") for point in points)
    browser.evaluate(r"""(async()=>{for(const [i,content] of [
      '设集合 $A=\\{0,-a\\}$，$B=\\{1,a-2,2a-2\\}$，若 $A\\subseteq B$，则 $a=$\\begin{choices}\\item $2$\\item $1$\\item $\\frac23$\\item $-1$\\end{choices}',
      '已知集合 $M=\\{1,2,3\\}$，$N=\\{2,3,4\\}$，则 $M\\cap N=$\\begin{choices}\\item $\\{2,3\\}$\\item $\\{1,4\\}$\\item $\\{1,2,3,4\\}$\\item $\\varnothing$\\end{choices}'
      ].entries()){
        const form=new FormData();Object.entries({content,question_type:'single_choice',difficulty:'easy_error',category_compulsory:'必修一',category_chapter:'1. 集合与常用逻辑用语',category_knowledge:'1.2 集合间的基本关系',source:'隔离浏览器验收 '+i,
          knowledge_points:JSON.stringify([{code:'1028',role:'main',status:'confirmed'}])}).forEach(([k,v])=>form.set(k,v));
        const response=await fetch('/api/questions',{method:'POST',body:form});if(!response.ok)throw Error(await response.text());}
      await loadQuestions();return true;})()""")
    return {"cross": points[0], "unmapped": points[1]}


def test_expanded_filters_omit_duplicate_chips_and_collapsed_summary_remains_removable(browser, catalog_examples):
    reset_bank(browser)
    expanded = browser.evaluate("""['bankFilterConditions','bankKnowledgePointConditions'].map(id=>{const n=document.getElementById(id);return {id,visible:n.checkVisibility(),height:n.getBoundingClientRect().height};})""")
    assert all(not row["visible"] and row["height"] == 0 for row in expanded), expanded
    before = location(browser)
    capture(browser, "desktop-expanded-compact-light")
    browser.command("click", ".bank-filter-toggle")
    browser.command("wait", "#bankFilterConditions button")
    metrics = capture(browser, "desktop-collapsed-summary-light")
    assert location(browser) == before
    assert browser.evaluate("document.querySelectorAll('#bankFilterConditions button').length") == 4
    assert browser.evaluate("document.getElementById('bankFilterConditions').getBoundingClientRect().height") <= 40
    assert not browser.evaluate("document.querySelector('#bankKnowledgePointConditions').checkVisibility()")
    assert all(row["rect"]["height"] <= 40 for row in metrics["summary"])
    browser.command("click", '#bankFilterConditions button[aria-label="移除筛选条件 ' + SECTION + '"]')
    browser.command("wait", "--fn", "document.getElementById('filterKnowledge').value===''")
    assert browser.evaluate("document.querySelectorAll('#bankFilterConditions button').length") == 3
    browser.command("click", ".bank-filter-toggle")
    assert not browser.evaluate("document.getElementById('bankFilterConditions').checkVisibility()")
    assert browser.evaluate("document.getElementById('filterChapter').value") == CHAPTER


def test_recommendations_are_compact_left_anchored_and_tree_only_in_catalog(browser, catalog_examples):
    reset_bank(browser)
    open_picker(browser)
    assert not browser.evaluate("!![...document.querySelectorAll(%s)].find(n=>n.checkVisibility())" % json.dumps(POPUP + " .mb-kp-tree-toggle"))
    metrics = capture(browser, "desktop-recommendations-light")
    assert metrics["picker"]["width"] <= 560
    assert abs(metrics["picker"]["left"] - metrics["toggle"]["left"]) <= 2
    assert metrics["toggle"]["left"] <= metrics["header"]["left"] + 100
    button = browser.evaluate("""(()=>{const buttons=[...document.querySelectorAll(%s)].filter(n=>n.checkVisibility());return {count:buttons.length,max:Math.max(...buttons.map(n=>n.getBoundingClientRect().height))};})()""" % json.dumps(POPUP + " .mb-kp-option"))
    assert button["count"] >= 5 and button["max"] <= 40, button
    assert metrics["picker"]["height"] < 430, metrics
    click_scope(browser, "全库目录")
    directory = browser.evaluate("""(()=>{const p=document.querySelector(%s),r=p.getBoundingClientRect(),list=p.querySelector('.mb-kp-options');
      return {height:r.height,listHeight:list.clientHeight,top:r.top,bottom:r.bottom,viewport:innerHeight};})()""" % json.dumps(POPUP))
    assert 620 <= directory["height"] <= 660, directory
    assert directory["listHeight"] >= 440, directory
    assert 12 <= directory["top"] < directory["bottom"] <= directory["viewport"] - 12, directory
    tree_state = browser.evaluate("""(()=>{const view=knowledgePointViews.get('bank');return {count:view.options.querySelectorAll('.mb-kp-tree-toggle').length,scope:view.scope,query:view.query,info:view.info.textContent,html:view.options.innerHTML.slice(0,1500),pressed:view.global.getAttribute('aria-pressed')};})()""")
    assert tree_state["count"] >= 3, tree_state
    initial = browser.evaluate("""[...document.querySelectorAll(%s)].filter(n=>n.dataset.treeKey.includes('1.1 集合的概念')||n.dataset.treeKey.includes('1.2 集合间的基本关系')).map(n=>({label:n.querySelector('.mb-kp-tree-label').textContent,expanded:n.getAttribute('aria-expanded')}))""" % json.dumps(POPUP + " .mb-kp-tree-toggle"))
    assert {row["label"]: row["expanded"] for row in initial} == {"1.1 集合的概念": "false", SECTION: "true"}, initial
    before = location(browser)
    expand_path(browser, BOOK, CHAPTER, SECTION)
    section_key = json.dumps(["A", BOOK, CHAPTER, SECTION], ensure_ascii=False, separators=(",", ":"))
    section_toggle = POPUP + " .mb-kp-tree-toggle[data-tree-key=" + json.dumps(section_key, ensure_ascii=False) + "]"
    browser.command("click", section_toggle)
    assert browser.evaluate("document.querySelector(%s).getAttribute('aria-expanded')" % json.dumps(section_toggle)) == "false"
    browser.command("click", section_toggle)
    assert browser.evaluate("document.querySelector(%s).getAttribute('aria-expanded')" % json.dumps(section_toggle)) == "true"
    assert location(browser) == before
    assert selected(browser) == []
    capture(browser, "desktop-tree-light")
    click_point(browser, "1028")
    browser.evaluate("changeTheme('ocean',false);true")
    capture(browser, "desktop-tree-ocean-selected")
    click_point(browser, "1028")
    browser.evaluate("changeTheme('violet',false);true")
    click_scope(browser, "本节推荐")
    assert not browser.evaluate("!![...document.querySelectorAll(%s)].find(n=>n.checkVisibility())" % json.dumps(POPUP + " .mb-kp-tree-toggle"))
    assert location(browser) == before


def test_tree_leaf_multiselect_syncs_across_paths_and_keeps_curriculum_selection(browser, catalog_examples):
    reset_bank(browser)
    open_picker(browser)
    click_scope(browser, "全库目录")
    before = location(browser)
    expand_path(browser, BOOK, CHAPTER, "1.1 集合的概念")
    expand_path(browser, BOOK, CHAPTER, SECTION)
    selector = point_selector(catalog_examples["cross"]["code"])
    assert browser.evaluate("document.querySelectorAll(%s).length" % json.dumps(selector)) == 2
    click_point(browser, catalog_examples["cross"]["code"])
    browser.command("wait", "--fn", "KnowledgePointState.filter('bank').knowledge_point_codes.length===1")
    states = browser.evaluate("[...document.querySelectorAll(%s)].map(n=>({pressed:n.getAttribute('aria-pressed'),checked:n.querySelector('input').checked}))" % json.dumps(selector))
    assert all(row == {"pressed": "true", "checked": True} for row in states), states
    assert selected(browser) == [catalog_examples["cross"]["code"]]
    assert location(browser) == before
    another = browser.evaluate("""[...document.querySelectorAll(%s)].find(n=>n.checkVisibility()&&n.dataset.code!==%s)?.dataset.code""" % (json.dumps(POPUP + " .mb-kp-option[data-code]"), json.dumps(catalog_examples["cross"]["code"])))
    assert another
    click_point(browser, another)
    assert set(selected(browser)) == {catalog_examples["cross"]["code"], another}
    click_point(browser, catalog_examples["cross"]["code"], 1)
    assert selected(browser) == [another]
    assert location(browser) == before
    browser.command("press", "Escape")
    assert browser.evaluate("document.querySelector(%s).hidden" % json.dumps(POPUP))
    browser.command("click", ".bank-filter-toggle")
    browser.command("wait", "#bankKnowledgePointConditions button")
    assert browser.evaluate("document.querySelectorAll('#bankKnowledgePointConditions button').length") == 1
    browser.command("click", "#bankKnowledgePointConditions button")
    assert selected(browser) == []
    assert location(browser) == before


def test_unmapped_tree_points_and_exact_code_search_are_available(browser, catalog_examples):
    reset_bank(browser)
    open_picker(browser)
    click_scope(browser, "全库目录")
    expand_path(browser, "未关联当前教材")
    selector = point_selector(catalog_examples["unmapped"]["code"])
    assert browser.evaluate("document.querySelector(%s)?.checkVisibility()" % json.dumps(selector))
    click_point(browser, catalog_examples["unmapped"]["code"])
    assert selected(browser) == [catalog_examples["unmapped"]["code"]]
    browser.command("fill", POPUP + " .mb-kp-tools input", "#" + catalog_examples["cross"]["code"])
    browser.settle()
    assert browser.evaluate("document.querySelectorAll(%s).length" % json.dumps(POPUP + " .mb-kp-option")) == 1
    assert browser.evaluate("document.querySelector(%s).dataset.code" % json.dumps(POPUP + " .mb-kp-option")) == catalog_examples["cross"]["code"]
    assert not browser.evaluate("!![...document.querySelectorAll(%s)].find(n=>n.checkVisibility())" % json.dumps(POPUP + " .mb-kp-tree-toggle"))
    click_point(browser, catalog_examples["cross"]["code"])
    assert set(selected(browser)) == {catalog_examples["cross"]["code"], catalog_examples["unmapped"]["code"]}
    browser.command("fill", POPUP + " .mb-kp-tools input", "#not-a-valid-code")
    browser.settle()
    assert browser.evaluate("document.querySelectorAll(%s).length" % json.dumps(POPUP + " .mb-kp-option")) == 0
    assert browser.evaluate("document.querySelector(%s).textContent.includes('未找到')" % json.dumps(POPUP))


def test_mobile_second_selected_label_keeps_long_name_and_click_target(browser, catalog_examples):
    reset_bank(browser, 375)
    browser.command("click", ".bank-create-btn")
    browser.command("click", '[data-editor-panel-target="classification"]')
    browser.command("select", "#editCompulsory", "选修二")
    browser.command("select", "#editChapter", "4. 数列")
    browser.command("select", "#editKnowledge", "4.3 等比数列")
    browser.command("scrollintoview", "#editKnowledgePointPicker .mb-kp-toggle")
    browser.command("click", "#editKnowledgePointPicker .mb-kp-toggle")
    for code in ("1796", "1859"):
        browser.command("fill", "#mb-kp-picker-edit .mb-kp-tools input", "#" + code)
        browser.settle()
        browser.command("click", "#mb-kp-picker-edit .mb-kp-option")
        browser.settle()
    browser.command("press", "Escape")
    target = "#editKnowledgePointPicker .mb-kp-selected button:nth-child(2)"
    browser.command("scrollintoview", target)
    browser.settle()
    observed = browser.evaluate("""(()=>{const b=document.querySelector(%s),r=b.getBoundingClientRect(),p=b.parentElement.getBoundingClientRect(),style=getComputedStyle(b),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {text:b.textContent,tooltip:b.dataset.tooltip,aria:b.getAttribute('aria-label'),height:r.height,top:r.top,bottom:r.bottom,parentTop:p.top,parentBottom:p.bottom,whiteSpace:style.whiteSpace,ellipsis:style.textOverflow,clickable:hit===b||b.contains(hit)};})()""" % json.dumps(target))
    assert len(observed["text"]) > 12 and observed["tooltip"] == observed["text"], observed
    assert observed["text"] in observed["aria"]
    assert observed["whiteSpace"] == "nowrap" and observed["ellipsis"] == "ellipsis", observed
    assert 43 <= observed["height"] <= 45 and observed["clickable"], observed
    assert observed["parentTop"] <= observed["top"] < observed["bottom"] <= observed["parentBottom"], observed
    browser.command("click", target)
    browser.command("select", "#editKnowledgePointPicker .mb-kp-menu select", "auxiliary")
    assert browser.evaluate("KnowledgePointState.snapshot().find(p=>p.code==='1859').role") == "auxiliary"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(EVIDENCE / "editor-second-long-label-375.png"))


def test_short_mobile_directory_keeps_rows_and_footer_reachable(browser, catalog_examples):
    reset_bank(browser, 375)
    browser.command("set", "viewport", "375", "450")
    browser.command("scrollintoview", HOST + " .mb-kp-toggle")
    browser.settle()
    open_picker(browser)
    click_scope(browser, "全库目录")
    observed = browser.evaluate("""(()=>{const p=document.querySelector(%s),r=p.getBoundingClientRect(),list=p.querySelector('.mb-kp-options');
      return {top:r.top,bottom:r.bottom,listHeight:list.clientHeight,rowHeight:p.querySelector('.mb-kp-tree-toggle').getBoundingClientRect().height,
        height:innerHeight,panelOverflow:getComputedStyle(p.querySelector('.mb-kp-panel')).overflowY};})()""" % json.dumps(POPUP))
    assert 12 <= observed["top"] < observed["bottom"] <= observed["height"] - 12, observed
    assert observed["listHeight"] >= 120 and observed["rowHeight"] >= 43, observed
    assert observed["panelOverflow"] in ("auto", "scroll"), observed
    browser.command("scrollintoview", POPUP + " .mb-kp-check")
    browser.settle()
    footer = browser.evaluate("""(()=>{const p=document.querySelector(%s).getBoundingClientRect(),label=document.querySelector(%s).getBoundingClientRect();
      return {top:label.top,bottom:label.bottom,panelTop:p.top,panelBottom:p.bottom};})()""" % (json.dumps(POPUP), json.dumps(POPUP + " .mb-kp-check")))
    assert footer["panelTop"] <= footer["top"] < footer["bottom"] <= footer["panelBottom"], footer
    browser.command("check", POPUP + " .mb-kp-check input")
    browser.settle()
    assert browser.evaluate("KnowledgePointState.filter('bank').knowledge_point_unlabeled")
    capture(browser, "tree-short-375-footer")


@pytest.mark.parametrize("width,dark", [(1600, True), (375, False), (375, True)])
def test_tree_and_summary_fit_narrow_viewports_and_both_themes(browser, catalog_examples, width, dark):
    reset_bank(browser, width, dark)
    open_picker(browser)
    click_scope(browser, "全库目录")
    expand_path(browser, BOOK, CHAPTER, SECTION)
    capture(browser, f"tree-{width}-{'dark' if dark else 'light'}")
    observed = browser.evaluate("""(()=>{const p=document.querySelector(%s),panel=p.querySelector('.mb-kp-panel'),list=p.querySelector('.mb-kp-options'),r=p.getBoundingClientRect();
      return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight,
        listOverflow:getComputedStyle(list).overflowY,listHeight:list.clientHeight,listScrollHeight:list.scrollHeight,
        targets:[...p.querySelectorAll('button,input,select')].filter(n=>n.checkVisibility()).map(n=>({width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height})),
        background:getComputedStyle(panel).backgroundColor};})()""" % json.dumps(POPUP))
    assert 0 <= observed["left"] < observed["right"] <= width
    assert 0 <= observed["top"] < observed["bottom"] <= observed["height"]
    assert observed["listOverflow"] in ("auto", "scroll")
    assert observed["listScrollHeight"] > observed["listHeight"] >= 44, observed
    assert observed["background"] not in ("transparent", "rgba(0, 0, 0, 0)")
    if width == 375:
        assert all(row["height"] >= 43 for row in observed["targets"] if row["width"] > 20), observed
        click_point(browser, "1028")
        assert selected(browser) == ["1028"]
        browser.command("scrollintoview", POPUP + " .mb-kp-check")
        browser.settle()
        unlabeled = browser.evaluate("""(()=>{const p=document.querySelector(%s),panel=p.querySelector('.mb-kp-panel').getBoundingClientRect(),label=p.querySelector('.mb-kp-check').getBoundingClientRect();return {top:label.top,bottom:label.bottom,panelTop:panel.top,panelBottom:panel.bottom};})()""" % json.dumps(POPUP))
        assert unlabeled["panelTop"] <= unlabeled["top"] < unlabeled["bottom"] <= unlabeled["panelBottom"], unlabeled
        browser.command("check", POPUP + " .mb-kp-check input")
        browser.settle()
        assert browser.evaluate("KnowledgePointState.filter('bank').knowledge_point_unlabeled")
        assert selected(browser) == []
        capture(browser, f"controls-{width}-{'dark' if dark else 'light'}")
    browser.command("press", "Escape")
    browser.command("click", ".bank-filter-toggle")
    capture(browser, f"summary-{width}-{'dark' if dark else 'light'}")
    summary = browser.evaluate("""(()=>{const n=document.getElementById('bankFilterConditions'),toolbar=n.parentElement;return {height:n.getBoundingClientRect().height,overflow:getComputedStyle(toolbar).overflowX,scroll:toolbar.scrollWidth,client:toolbar.clientWidth,wrap:getComputedStyle(toolbar).flexWrap};})()""")
    assert summary["height"] <= 48, summary
    assert summary["overflow"] in ("auto", "scroll"), summary
    assert summary["wrap"] == "nowrap", summary
    if width == 375:
        browser.command("scrollintoview", "#bankFilterConditions button:last-child")
        browser.settle()
        browser.command("click", "#bankFilterConditions button:last-child")
        assert browser.evaluate("document.getElementById('filterType').value") == ""
