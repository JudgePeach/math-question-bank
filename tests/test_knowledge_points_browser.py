"""Focused live Chromium checks against a source-only, disposable application."""
import json
from pathlib import Path

import pytest

from test_preview_browser import browser, pytestmark  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/compact-knowledge-20261012-203001/browser-qa"
LAYOUT_EVIDENCE = []
TOGGLE_EVIDENCE = []
SEQUENCE = {"book": "选修二", "chapter": "4. 数列", "section": "4.3 等比数列"}
DERIVATIVE = "5. 一元函数的导数及其应用"


def reset_bank(browser, width=1600):
    assert browser.root.resolve() != ROOT.resolve()
    browser.command("reload")
    browser.command("set", "viewport", str(width), "1000" if width > 600 else "812")
    browser.command("wait", "--fn", "window.activeCurriculumVersion==='A' && typeof window.KnowledgePointState==='object' && document.getElementById('filterCompulsory').options.length>1")
    assert browser.evaluate("location.port") != "8000"
    browser.evaluate("selectWorkspace('bank','题库管理');clearBankFilters();true")
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy')")
    assert browser.evaluate("loadKnowledgePointCatalog().then(points=>points.length)") >= 632


def capture(browser, name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command("screenshot", str(EVIDENCE / (name + ".png")))
    LAYOUT_EVIDENCE.append({"capture": name, "fields": browser.evaluate("""(()=>{
      return ['bank','edit','paper'].map(kind=>{const host=document.getElementById(kind+'KnowledgePointPicker');
        if(!host||!host.checkVisibility())return null;const picker=document.getElementById(host.querySelector('.mb-kp-toggle').getAttribute('aria-controls'));
        const panel=picker.querySelector('.mb-kp-panel'),rect=host.getBoundingClientRect(),panelRect=panel?.getBoundingClientRect();
        return {kind,width:innerWidth,height:rect.height,open:picker.open,
          panelHeight:panel?.checkVisibility()?panelRect.height:0,panelScrollTop:panel?.scrollTop,
          candidateInPanel:[...picker.querySelectorAll('.mb-kp-option')].filter(n=>{const r=n.getBoundingClientRect();
            return n.checkVisibility()&&r.bottom>panelRect.top&&r.top<panelRect.bottom;}).length,
          overflow:document.documentElement.scrollWidth>innerWidth+1};
      }).filter(Boolean);})()""")})
    (EVIDENCE / "layout-metrics.json").write_text(json.dumps(LAYOUT_EVIDENCE,ensure_ascii=False,indent=2))


def seed(browser, marker, labels, chapter="4. 数列", section="4.3 等比数列"):
    payload = {"content": marker, "question_type": "detailed_answer", "difficulty": "easy_error",
               "category_compulsory": "选修二", "category_chapter": chapter,
               "category_knowledge": section, "tags": "隔离回归标签",
               "knowledge_points": json.dumps(labels, ensure_ascii=False)}
    return browser.evaluate("""(async()=>{
      const form=new FormData();Object.entries(%s).forEach(([k,v])=>form.set(k,v));
      const response=await fetch('/api/questions',{method:'POST',body:form});const result=await response.json();
      if(!response.ok||!result.question)throw Error(JSON.stringify(result));loadQuestions();return result.question;
    })()""" % json.dumps(payload, ensure_ascii=False))


def question(browser, qid):
    return browser.evaluate(f"fetch('/api/questions/{qid}').then(r=>r.json())")


def open_editor(browser, qid):
    if browser.evaluate("innerWidth<=960&&document.getElementById('bankWorkspaceSection').classList.contains('bank-detail-open')"):
        browser.command("click", ".bank-detail-back")
    browser.command("wait", f'#questionsList [data-id="{qid}"]')
    browser.command("click", f'#questionsList [data-id="{qid}"]')
    browser.command("wait", "--fn", f"EditorState.questionId==={qid}&&!questionDetailLoading&&!isEditorModified()")
    browser.command("click", "#editQuestionFromPreviewBtn")
    browser.command("click", '[data-editor-panel-target="classification"]')


def save_editor(browser, clean=True):
    browser.command("click", "#saveQuestionBtn")
    review = "!!document.getElementById('parsedDuplicateReviewModal')&&!document.getElementById('parsedDuplicateReviewModal').classList.contains('hidden')"
    browser.command("wait", "--fn", "!isQuestionSaveInFlight()||(" + review + ")")
    if browser.evaluate(review):
        browser.command("click", "#duplicateReviewIndependentBtn")
    browser.command("wait", "--fn", "!isQuestionSaveInFlight()" + ("&&!isEditorModified()" if clean else ""))


def select_location(browser, chapter="4. 数列", section="4.3 等比数列"):
    browser.command("select", "#editCompulsory", "选修二")
    browser.command("select", "#editChapter", chapter)
    if section:
        browser.command("select", "#editKnowledge", section)


def picker_selector(browser, host):
    popup_id = browser.evaluate("document.querySelector(%s).getAttribute('aria-controls')" % json.dumps(host+" .mb-kp-toggle"))
    assert popup_id
    return "#"+popup_id


def toggle_picker(browser, host, opened):
    selector = host + " .mb-kp-toggle"
    popup = picker_selector(browser, host)
    browser.command("scrollintoview", selector)
    browser.settle()
    position = """(()=>{const button=document.querySelector(%s),r=button.getBoundingClientRect(),ancestors=[];
      for(let node=button.parentElement;node;node=node.parentElement){const box=node.getBoundingClientRect();
        if(node.scrollHeight>node.clientHeight+1||node.classList.contains('question-editor-modal-surface'))
          ancestors.push({id:node.id,class:node.className,top:box.top,left:box.left,scroll:node.scrollTop,
            clientHeight:node.clientHeight,scrollHeight:node.scrollHeight,clientWidth:node.clientWidth});}
      return {left:r.left+scrollX,top:r.top+scrollY,width:r.width,height:r.height,ancestors};})()""" % json.dumps(selector)
    before = browser.evaluate(position)
    browser.command("click", selector)
    browser.command("wait", "--fn", "document.querySelector(%s).open===%s" % (json.dumps(popup),str(opened).lower()))
    browser.settle()
    after = browser.evaluate(position)
    stable = all(abs(before[key]-after[key])<=1 for key in ["left","top","width","height"])
    TOGGLE_EVIDENCE.append({"host":host,"width":browser.evaluate("innerWidth"),"opened":opened,"before":before,"after":after})
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE / "toggle-layout.json").write_text(json.dumps(TOGGLE_EVIDENCE,ensure_ascii=False,indent=2))
    if not stable:
        capture(browser, "toggle-position-failure-"+host.strip("#")+"-"+str(browser.evaluate("innerWidth")))
    assert stable, {"before":before,"after":after,"opened":opened}
    assert browser.evaluate("document.querySelector(%s).getAttribute('aria-expanded')" % json.dumps(selector)) == str(opened).lower()
    assert browser.evaluate("document.querySelector(%s).hidden" % json.dumps(popup)) is not opened


def open_picker(browser, host):
    if not browser.evaluate("document.querySelector(%s).open" % json.dumps(picker_selector(browser,host))):
        toggle_picker(browser, host, True)


def assert_compact_closed(browser, host):
    observed = browser.evaluate("""(()=>{const host=document.querySelector(%s),picker=document.getElementById(host.querySelector('.mb-kp-toggle').getAttribute('aria-controls'));
      return {open:picker.open,hidden:picker.hidden,height:host.getBoundingClientRect().height,
        visibleOptions:[...picker.querySelectorAll('.mb-kp-option')].filter(n=>n.checkVisibility()).length,
        visibleControls:[...picker.querySelectorAll('.mb-kp-filter-controls select')].filter(n=>n.checkVisibility()).length,
        menuVisible:host.querySelector('.mb-kp-menu').checkVisibility()};})()""" % json.dumps(host))
    assert not observed["open"] and observed["hidden"] and not observed["menuVisible"], observed
    assert observed["visibleOptions"] == observed["visibleControls"] == 0, observed
    assert observed["height"] <= 120, observed


def assert_candidate_visible_without_panel_scroll(browser, host):
    observed = browser.evaluate("""(()=>{const host=document.querySelector(%s),picker=document.getElementById(host.querySelector('.mb-kp-toggle').getAttribute('aria-controls')),panel=picker.querySelector('.mb-kp-panel');
      const tree=picker.querySelector('.mb-kp-options').classList.contains('mb-kp-tree');
      const candidates=[...picker.querySelectorAll(tree?'.mb-kp-tree-toggle,.mb-kp-option':'.mb-kp-option')].filter(n=>{const r=n.getBoundingClientRect();
        const clip={left:0,top:0,right:innerWidth,bottom:innerHeight};
        for(let node=n.parentElement;node;node=node.parentElement){const style=getComputedStyle(node),p=node.getBoundingClientRect();
          if(/auto|scroll|hidden|clip/.test(style.overflowY)){clip.top=Math.max(clip.top,p.top+node.clientTop);clip.bottom=Math.min(clip.bottom,p.top+node.clientTop+node.clientHeight);}
          if(/auto|scroll|hidden|clip/.test(style.overflowX)){clip.left=Math.max(clip.left,p.left+node.clientLeft);clip.right=Math.min(clip.right,p.left+node.clientLeft+node.clientWidth);}}
        return n.checkVisibility()&&r.top>=clip.top&&r.bottom<=clip.bottom&&r.left>=clip.left&&r.right<=clip.right;});
      return {scroll:panel.scrollTop,candidates:candidates.length,tree};})()""" % json.dumps(host))
    assert observed["scroll"] == 0 and observed["candidates"] >= 1, observed


def assert_no_horizontal_overflow(browser, host):
    observed = browser.evaluate("""(()=>{const host=document.querySelector(%s),picker=document.getElementById(host.querySelector('.mb-kp-toggle').getAttribute('aria-controls'));
      return {overflow:document.documentElement.scrollWidth>innerWidth+1,
        outside:[...host.querySelectorAll('button,input,select'),...picker.querySelectorAll('button,input,select')].filter(n=>n.checkVisibility())
          .some(n=>n.getBoundingClientRect().right>innerWidth+1||n.getBoundingClientRect().left<0)};})()""" % json.dumps(host))
    assert not observed["overflow"] and not observed["outside"], observed


def assert_checkbox_hit_area(browser, popup):
    observed = browser.evaluate("""(()=>{const input=document.querySelector(%s),r=input.getBoundingClientRect();
      return {width:r.width,height:r.height,labelHeight:input.closest('label').getBoundingClientRect().height,viewport:innerWidth,
        compact:matchMedia('(min-width: 641px) and (pointer: fine)').matches};})()""" % json.dumps(popup+" .mb-kp-check input"))
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE / "checkbox-metrics.json").write_text(json.dumps({"popup":popup,**observed},ensure_ascii=False,indent=2))
    assert observed["width"] == observed["height"] == 17, observed
    assert observed["labelHeight"] >= (28 if observed["compact"] else 44), observed


def pick(browser, host, code):
    open_picker(browser, host)
    popup = picker_selector(browser,host)
    browser.command("scrollintoview", popup + " .mb-kp-tools input")
    browser.command("fill", popup + " .mb-kp-tools input", "#"+code)
    browser.settle()
    assert_no_horizontal_overflow(browser,host)
    observed = browser.evaluate("""(()=>{const host=document.querySelector(%s);const kind=%s;
      const view=knowledgePointViews.get(kind);return {query:host.querySelector('.mb-kp-tools input').value,
        remembered:view?.query,scope:view?.scope,same:view?.search===host.querySelector('.mb-kp-tools input'),
        count:host.querySelectorAll('.mb-kp-options .mb-kp-option').length,text:host.querySelector('.mb-kp-options').textContent,
        catalog:knowledgePointCatalogCache.get(knowledgePointVersion())?.items.length,
        open:view?.picker.open,hidden:document.getElementById('paperAdvancedFilters')?.hidden,
        active:document.activeElement?.outerHTML?.slice(0,500),attrs:view?.search.outerHTML};})()"""
        % (json.dumps(popup), json.dumps("paper" if "paper" in host else "edit" if "edit" in host else "bank")))
    assert observed["count"] == 1, json.dumps(observed,ensure_ascii=False)
    assert browser.evaluate("""(()=>{const host=document.querySelector(%s),option=host.querySelector('.mb-kp-option');
      const point=knowledgePointCatalogCache.get(knowledgePointVersion()).items.find(p=>p.code===%s);
      return !option.querySelector('small')&&option.textContent===point.name;})()""" % (json.dumps(popup),json.dumps(code)))
    browser.command("wait", "--fn", f"document.querySelectorAll('{popup} .mb-kp-options .mb-kp-option').length===1")
    browser.command("scrollintoview", popup + " .mb-kp-option")
    browser.settle()
    browser.command("click", popup + " .mb-kp-option")


def edit_role(browser, code, role):
    index = browser.evaluate("""(()=>{
      const labels=KnowledgePointState.snapshot().filter(p=>p.status==='confirmed');
      return labels.findIndex(p=>p.code===%s)+1;
    })()""" % json.dumps(code))
    assert index > 0
    target = f"#editKnowledgePointPicker .mb-kp-selected button:nth-child({index})"
    browser.command("scrollintoview", target)
    browser.settle()
    hit = browser.evaluate("""(()=>{const b=document.querySelector(%s),r=b.getBoundingClientRect();
      const h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      const ancestors=[];for(let node=b.parentElement;node;node=node.parentElement){const style=getComputedStyle(node);if(/auto|hidden|clip|scroll/.test(style.overflowY))ancestors.push({id:node.id,classes:node.className,rect:node.getBoundingClientRect().toJSON(),scroll:node.scrollTop,client:node.clientHeight,scrollHeight:node.scrollHeight});}
      return {same:h===b||b.contains(h),rect:r.toJSON(),hit:h?.closest('button')?.textContent,node:h?.outerHTML.slice(0,600),ancestors};})()""" % json.dumps(target))
    if not hit["same"]:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        browser.command("screenshot",str(EVIDENCE / "editor-point-hit-failure.png"))
        (EVIDENCE / "editor-point-hit-failure.json").write_text(json.dumps(hit,ensure_ascii=False,indent=2))
    assert hit["same"], hit
    if not browser.evaluate("document.querySelector(%s).getAttribute('aria-expanded')==='true'" % json.dumps(target)):
        browser.command("click", target)
    browser.command("select", "#editKnowledgePointPicker .mb-kp-menu select", role)
    browser.command("wait", "--fn", "KnowledgePointState.snapshot().find(p=>p.code==="+json.dumps(code)+").role==="+json.dumps(role))


@pytest.mark.parametrize("width", [1600, 375])
def test_real_editor_cross_chapter_roles_save_reopen_and_draft_refresh(browser, width):
    reset_bank(browser, width)
    browser.command("click", ".bank-create-btn")
    browser.command("wait", "--fn", "!document.getElementById('editorSection').classList.contains('hidden')")
    browser.command("click", "#contentTabBtn-manual")
    content = f"KP-editor-{width}：给定等比数列，已知首项为{width + 9}，求通项。"
    browser.command("fill", "#editContent", content)
    browser.command("click", '[data-editor-panel-target="classification"]')
    select_location(browser)
    assert_compact_closed(browser, "#editKnowledgePointPicker")
    capture(browser, f"editor-points-collapsed-{width}")
    open_picker(browser,"#editKnowledgePointPicker")
    popup = picker_selector(browser,"#editKnowledgePointPicker")
    if width == 1600:
        browser.command("fill",popup+" .mb-kp-tools input","23")
        browser.command("wait","--fn",f"document.querySelectorAll('{popup} .mb-kp-option').length===0")
        assert "未找到匹配的知识点" in browser.evaluate("document.querySelector(%s).textContent" % json.dumps(popup+" .mb-kp-options"))
        browser.command("fill",popup+" .mb-kp-tools input","#1230")
        browser.command("wait","--fn",f"document.querySelectorAll('{popup} .mb-kp-option').length===1")
        assert browser.evaluate("document.querySelector(%s).textContent" % json.dumps(popup+" .mb-kp-option")) == "二分法"
        capture(browser,"editor-explicit-code-search-1600")
    browser.command("click",popup+" .mb-kp-tools input")
    browser.command("press","Escape")
    browser.command("wait","--fn","document.querySelector(%s).hidden" % json.dumps(popup))
    assert browser.evaluate("!document.getElementById('editorSection').classList.contains('hidden')")
    assert browser.evaluate("document.activeElement===document.querySelector('#editKnowledgePointPicker .mb-kp-toggle')")
    pick(browser, "#editKnowledgePointPicker", "1796")
    pick(browser, "#editKnowledgePointPicker", "1859")
    # The CLI select command does not send the outside pointer event that a
    # person opening the chapter dropdown would use to dismiss the popup.
    toggle_picker(browser,"#editKnowledgePointPicker",False)
    browser.command("select", "#editChapter", DERIVATIVE)
    assert browser.evaluate("KnowledgePointState.snapshot().map(p=>p.code).sort()") == ["1796", "1859"]
    browser.command("select", "#editChapter", "4. 数列")
    browser.command("select", "#editKnowledge", "4.3 等比数列")
    edit_role(browser, "1859", "auxiliary")
    assert browser.evaluate("""(()=>{const buttons=[...document.querySelectorAll('#editKnowledgePointPicker .mb-kp-selected button')];
      const labels=KnowledgePointState.snapshot().filter(p=>p.status==='confirmed');
      return buttons.every((button,index)=>button.textContent===labels[index].name);})()""")
    browser.command("fill", "#editTags", "原标签,待讲")
    assert browser.evaluate("isEditorModified()")
    browser.command("scrollintoview", "#editKnowledgePointPicker")
    layout = browser.evaluate("""(()=>({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth+1,
      outside:[...document.querySelectorAll('#editKnowledgePointPicker button')].filter(b=>b.offsetParent).some(b=>b.getBoundingClientRect().right>innerWidth+1)}))()""")
    assert not layout["overflow"] and not layout["outside"], layout
    capture(browser, f"editor-points-{width}")
    save_editor(browser)
    qid = browser.evaluate("EditorState.questionId")
    stored = question(browser, qid)
    assert {(p["code"], p["role"], p["status"]) for p in stored["knowledge_points"]} == {
        ("1796", "main", "confirmed"), ("1859", "auxiliary", "confirmed")}
    assert stored["category_chapter"] == "4. 数列" and stored["category_knowledge"] == "4.3 等比数列"
    assert stored["tags"] == "原标签,待讲"
    browser.command("click", "#editorSection .question-editor-close")
    open_editor(browser, qid)
    assert not browser.evaluate("document.querySelector(%s).open" % json.dumps(picker_selector(browser,"#editKnowledgePointPicker")))
    assert browser.evaluate("KnowledgePointState.snapshot().find(p=>p.code==='1859').role") == "auxiliary"
    edit_role(browser, "1796", "auxiliary")
    draft_id = browser.evaluate("saveCurrentToDrafts();EditorState.draftId")
    assert draft_id
    draft_state = browser.evaluate("getLocalStorageDrafts().find(d=>d.id==="+json.dumps(draft_id)+")")
    assert all(p["role"] == "auxiliary" for p in draft_state["knowledge_points"]), draft_state
    reset_bank(browser, width)
    browser.evaluate("""selectDraft(getLocalStorageDrafts().find(d=>d.id===%s));openQuestionEditorModal('classification');true""" % json.dumps(draft_id))
    restored = browser.evaluate("({points:KnowledgePointState.snapshot(),draft:EditorState.draftId,question:EditorState.questionId,loading:questionDetailLoading})")
    assert all(p["role"] == "auxiliary" for p in restored["points"]), {"before":draft_state,"after":restored}
    assert browser.evaluate("document.getElementById('editTags').value") == "原标签,待讲"
    assert not browser.evaluate("isEditorModified()")
    assert question(browser, qid)["knowledge_points"][0]["role"] == "main"
    capture(browser, f"draft-points-restored-{width}")


def test_legacy_labels_are_usable_without_promotion_and_late_save_keeps_point_edits(browser):
    reset_bank(browser)
    origin = {"source": "ai_research_candidates", "kind": "reviewed_research_candidate", "fixture": "isolated"}
    initial = seed(browser, "KP-ack：已知等比数列首项37，公比2，求第5项。", [
        {"code": "1796", "role": "main", "status": "confirmed", "origin": origin,
         "scope":["第(1)问"],"evidence_source":"content","evidence":"已知等比数列首项37","reason":"通项公式用于求第5项"},
        {"code": "1803", "role": "main", "status": "suggested", "origin": origin}])
    open_editor(browser, initial["id"])
    assert browser.evaluate("document.querySelector('#editKnowledgePointPicker .mb-kp-suggestions')===null")
    assert browser.evaluate("document.querySelectorAll('#editKnowledgePointPicker .mb-kp-selected button').length") == 2
    browser.command("click", "#editKnowledgePointPicker .mb-kp-selected button:first-child")
    browser.command("click", "#editKnowledgePointPicker .mb-kp-label-details summary")
    provenance = browser.evaluate("document.querySelector('#editKnowledgePointPicker .mb-kp-label-details').textContent")
    assert "AI研究标注（已接入，可修改）" in provenance and "第(1)问" in provenance and "首项37" in provenance
    assert "[object Object]" not in provenance and "fixture" not in provenance
    browser.command("fill", "#editSource", "只改来源的普通保存")
    save_editor(browser)
    assert {p["code"]: p["status"] for p in question(browser, initial["id"])["knowledge_points"]} == {"1796": "confirmed", "1803": "suggested"}
    browser.evaluate("""(()=>{
      window.__kpAckFetch=window.fetch;window.__kpAckReady=false;
      window.fetch=async(input,options)=>{const response=await __kpAckFetch(input,options);
        if(String(input)==='/api/questions/'+EditorState.questionId&&options?.method==='PUT'){
          window.__kpAckReady=true;await new Promise(resolve=>window.__kpAckRelease=resolve);
        }return response;};window.__kpAckSave=saveQuestion(true);return true;
    })()""")
    try:
        browser.command("wait", "--fn", "window.__kpAckReady===true")
        edit_role(browser, "1796", "auxiliary")
        assert browser.evaluate("__kpAckRelease();__kpAckSave") is True
    finally:
        browser.evaluate("if(window.__kpAckRelease)__kpAckRelease();window.fetch=window.__kpAckFetch;true")
    assert browser.evaluate("KnowledgePointState.snapshot().find(p=>p.code==='1796').role") == "auxiliary"
    assert browser.evaluate("isEditorModified()")
    stored = question(browser, initial["id"])
    assert next(p for p in stored["knowledge_points"] if p["code"] == "1796")["role"] == "main"
    assert all(p["origin"] == origin for p in stored["knowledge_points"])
    save_editor(browser)
    assert next(p for p in question(browser, initial["id"])["knowledge_points"] if p["code"] == "1796")["role"] == "auxiliary"


@pytest.fixture(scope="module")
def filter_examples(browser):
    reset_bank(browser)
    rows = [
        ("KP-filter-甲：求首项11公比2的数列前5项和。", "4. 数列", "4.3 等比数列", [{"code":"1796","role":"main","status":"confirmed"},{"code":"1803","role":"main","status":"confirmed"}]),
        ("KP-filter-乙：求首项13公比3的数列前6项和。", "4. 数列", "4.3 等比数列", [{"code":"1796","role":"main","status":"confirmed"},{"code":"1803","role":"auxiliary","status":"confirmed"}]),
        ("KP-filter-丙：求函数在17处的导数。", DERIVATIVE, "5.3 导数在研究函数中的应用", [{"code":"1796","role":"main","status":"suggested"}]),
        ("KP-filter-丁：求函数在19处的导数。", DERIVATIVE, "5.3 导数在研究函数中的应用", [{"code":"1796","role":"main","status":"confirmed"}]),
        ("KP-filter-戊：求数列第23项。", "4. 数列", "4.3 等比数列", []),
    ]
    return [seed(browser, content, labels, chapter, section) for content, chapter, section, labels in rows]


def expected_query(browser, codes, mode="any", role="main", status="all", chapter=""):
    params = {"q":"KP-filter-", "page":"1", "page_size":"100", "knowledge_point_mode":mode,
              "knowledge_point_role":role, "knowledge_point_status":status}
    if chapter:
        params["chapter"] = chapter
    pairs = list(params.items()) + [("knowledge_point_codes", code) for code in codes]
    return browser.evaluate("fetch('/api/questions?'+new URLSearchParams(%s)).then(r=>r.json()).then(p=>p.items.map(q=>q.id).sort((a,b)=>a-b))" % json.dumps(pairs, ensure_ascii=False))


def wait_ids(browser, expected):
    expected = sorted(expected)
    browser.command("wait", "--fn", "!document.getElementById('questionsList').hasAttribute('aria-busy')&&JSON.stringify([...document.querySelectorAll('#questionsList .question-card')].map(n=>Number(n.dataset.id)).sort((a,b)=>a-b))===" + json.dumps(json.dumps(expected, separators=(",", ":"))))


def test_bank_filters_include_legacy_points_and_combine_with_chapter(browser, filter_examples):
    reset_bank(browser)
    browser.command("fill", "#searchInput", "KP-filter-")
    if browser.evaluate("document.querySelector('.bank-filter-toggle').getAttribute('aria-expanded')!=='true'"):
        browser.command("click", ".bank-filter-toggle")
    for width in [1600,375]:
        browser.command("set", "viewport", str(width), "1000" if width > 600 else "812")
        browser.command("scrollintoview", "#bankKnowledgePointPicker")
        browser.settle()
        assert_compact_closed(browser, "#bankKnowledgePointPicker")
        assert_no_horizontal_overflow(browser, "#bankKnowledgePointPicker")
        capture(browser,f"bank-points-collapsed-{width}")
    open_picker(browser, "#bankKnowledgePointPicker")
    capture(browser,"bank-points-initial-expanded-375")
    assert_candidate_visible_without_panel_scroll(browser, "#bankKnowledgePointPicker")
    toggle_picker(browser, "#bankKnowledgePointPicker", False)
    browser.command("set", "viewport", "1600", "1000")
    pick(browser, "#bankKnowledgePointPicker", "1796")
    pick(browser, "#bankKnowledgePointPicker", "1803")
    popup = picker_selector(browser,"#bankKnowledgePointPicker")
    ids = [q["id"] for q in filter_examples]
    expected = expected_query(browser, ["1796","1803"])
    assert expected == sorted(ids[:4])
    wait_ids(browser, expected)
    browser.command("select", popup+' select[aria-label="匹配方式"]', "all")
    wait_ids(browser, expected_query(browser,["1796","1803"],mode="all"))
    assert expected_query(browser,["1796","1803"],mode="all") == [ids[0]]
    browser.command("select", popup+' select[aria-label="考查作用"]', "all")
    wait_ids(browser, expected_query(browser,["1796","1803"],mode="all",role="all"))
    assert expected_query(browser,["1796","1803"],mode="all",role="all") == sorted(ids[:2])
    browser.command("select", popup+' select[aria-label="匹配方式"]', "any")
    assert browser.evaluate("document.querySelector(" + json.dumps(popup+' select[aria-label="确认状态"]') + ")===null")
    wait_ids(browser, expected_query(browser,["1796","1803"],role="all",status="all"))
    assert expected_query(browser,["1796","1803"],role="all",status="all") == sorted(ids[:4])
    browser.command("select", "#filterCompulsory", "选修二")
    browser.command("select", "#filterChapter", DERIVATIVE)
    wait_ids(browser, expected_query(browser,["1796","1803"],role="all",status="all",chapter=DERIVATIVE))
    assert browser.evaluate("KnowledgePointState.filter('bank').knowledge_point_codes") == ["1796","1803"]
    assert expected_query(browser,["1796","1803"],role="all",status="all",chapter=DERIVATIVE) == sorted(ids[2:4])
    browser.command("click", ".bank-filter-toggle")
    browser.command("click", '#bankFilterConditions button[aria-label="移除筛选条件 '+DERIVATIVE+'"]')
    wait_ids(browser, expected_query(browser,["1796","1803"],role="all",status="all"))
    browser.command("click", ".bank-filter-toggle")
    open_picker(browser,"#bankKnowledgePointPicker")
    capture(browser,"bank-point-combined-filters")
    assert_checkbox_hit_area(browser,popup)
    browser.command("check", popup+" .mb-kp-check input")
    assert browser.evaluate("KnowledgePointState.filter('bank').knowledge_point_codes") == []
    wait_ids(browser,[ids[4]])
    browser.command("set", "viewport", "375", "812")
    assert_no_horizontal_overflow(browser, "#bankKnowledgePointPicker")
    capture(browser,"bank-points-expanded-375")
    browser.command("set", "viewport", "1600", "1000")


def test_paper_point_get_and_ai_payload_match_and_keep_cart(browser, filter_examples):
    reset_bank(browser)
    browser.evaluate("""(()=>{
      selectWorkspace('paper','组卷排版工作台');window.__kpPaperFetch=window.fetch;window.__kpPaperRequests=[];
      window.fetch=async(input,options)=>{
        if(String(input).startsWith('/api/questions?'))__kpPaperRequests.push(String(input));
        if(String(input)==='/api/paper/ai-select'){window.__kpAiPayload=JSON.parse(options.body);
          return new Response(JSON.stringify({status:'success',data:[]}),{status:200,headers:{'Content-Type':'application/json'}});}
        return __kpPaperFetch(input,options);};return true;
    })()""")
    try:
        browser.command("wait", "#paperFilterKeyword")
        browser.command("fill", "#paperFilterKeyword", "KP-filter-")
        if browser.evaluate("document.getElementById('paperAdvancedFilters').hidden"):
            browser.command("click", "#togglePaperFilterBtn")
        for width in [1600,375]:
            browser.command("set", "viewport", str(width), "1000" if width > 600 else "812")
            browser.command("scrollintoview", "#paperKnowledgePointPicker")
            browser.settle()
            assert_compact_closed(browser, "#paperKnowledgePointPicker")
            assert_no_horizontal_overflow(browser, "#paperKnowledgePointPicker")
            capture(browser,f"paper-points-collapsed-{width}")
        open_picker(browser, "#paperKnowledgePointPicker")
        capture(browser,"paper-points-initial-expanded-375")
        assert_candidate_visible_without_panel_scroll(browser, "#paperKnowledgePointPicker")
        popup = picker_selector(browser,"#paperKnowledgePointPicker")
        browser.command("scrollintoview",popup+" .mb-kp-check input")
        browser.settle()
        assert_checkbox_hit_area(browser,popup)
        capture(browser,"paper-checkbox-375")
        toggle_picker(browser, "#paperKnowledgePointPicker", False)
        browser.command("set", "viewport", "1600", "1000")
        browser.evaluate(f"addToCart({filter_examples[0]['id']});true")
        cart = browser.evaluate("PaperStore.cart.map(item=>item.id)")
        pick(browser,"#paperKnowledgePointPicker","1796")
        pick(browser,"#paperKnowledgePointPicker","1803")
        popup = picker_selector(browser,"#paperKnowledgePointPicker")
        browser.command("select", popup+' select[aria-label="匹配方式"]', "all")
        browser.command("select", popup+' select[aria-label="考查作用"]', "all")
        browser.command("wait", "--fn", "!PaperStore.streamPagination.all.loading&&PaperStore.bankQuestions.length===2")
        assert sorted(browser.evaluate("PaperStore.bankQuestions.map(q=>q.id)")) == expected_query(browser,["1796","1803"],mode="all",role="all")
        assert browser.evaluate("PaperStore.cart.map(item=>item.id)") == cart
        last_query = browser.evaluate("__kpPaperRequests.at(-1)")
        for token in ["knowledge_point_codes=1796", "knowledge_point_codes=1803", "knowledge_point_mode=all", "knowledge_point_role=all", "knowledge_point_status=all"]:
            assert token in last_query
        browser.command("scrollintoview", "#togglePaperAiBtn")
        browser.settle()
        browser.command("click", "#togglePaperAiBtn")
        browser.command("wait", "--fn", "!document.getElementById('paperAiDetails').hidden")
        browser.command("scrollintoview", "#paperAiPromptInput")
        browser.command("fill", "#paperAiPromptInput", "按当前知识点条件选题")
        browser.command("scrollintoview", 'button[onclick="triggerAiPaperSelect()"]')
        browser.settle()
        browser.command("click", 'button[onclick="triggerAiPaperSelect()"]')
        browser.command("wait", "--fn", "!!window.__kpAiPayload")
        payload = browser.evaluate("__kpAiPayload")
        assert payload["knowledge_point_codes"] == ["1796","1803"]
        assert payload["knowledge_point_mode"] == "all" and payload["knowledge_point_role"] == "all"
        assert payload["knowledge_point_status"] == "all" and payload["knowledge_point_unlabeled"] is False
        assert browser.evaluate("PaperStore.cart.map(item=>item.id)") == cart
        capture(browser,"paper-point-filters-and-cart")
        browser.command("set", "viewport", "375", "812")
        browser.command("scrollintoview", "#paperKnowledgePointPicker")
        browser.settle()
        open_picker(browser,"#paperKnowledgePointPicker")
        assert_no_horizontal_overflow(browser, "#paperKnowledgePointPicker")
        capture(browser,"paper-points-expanded-375")
        browser.command("set", "viewport", "1600", "1000")
    finally:
        browser.evaluate("window.fetch=window.__kpPaperFetch;true")


def test_new_local_point_is_explicit_and_duplicate_is_rejected(browser):
    reset_bank(browser)
    browser.command("click", ".bank-create-btn")
    browser.command("click", '[data-editor-panel-target="classification"]')
    select_location(browser)
    browser.command("click", "#editKnowledgePointPicker .mb-kp-create > summary")
    name = "QA专项：奇偶分组求和的辅助步骤"
    browser.command("fill", "#editKnowledgePointPicker .mb-kp-create input:not([type])", name)
    browser.command("fill", "#editKnowledgePointPicker .mb-kp-create textarea", "QA奇偶求和")
    browser.command("check", "#editKnowledgePointPicker .mb-kp-create input[type=checkbox]")
    browser.command("click", "#editKnowledgePointPicker .mb-kp-create > button")
    browser.command("wait", "--fn", "KnowledgePointState.snapshot().some(p=>p.name==="+json.dumps(name,ensure_ascii=False)+")")
    rows = browser.evaluate("fetch('/api/knowledge-points?q='+encodeURIComponent(%s)).then(r=>r.json()).then(p=>p.items)" % json.dumps(name,ensure_ascii=False))
    assert len(rows) == 1 and rows[0]["source"] == "local" and rows[0]["code"].startswith("local:")
    assert rows[0]["aliases"] == ["QA奇偶求和"]
    assert rows[0]["curriculum_paths"][0]["knowledge"] == "4.3 等比数列"
    browser.command("fill", "#editKnowledgePointPicker .mb-kp-create input:not([type])", name)
    browser.command("click", "#editKnowledgePointPicker .mb-kp-create > button")
    browser.command("wait", "--fn", "document.querySelector('#editKnowledgePointPicker .mb-kp-create [role=alert]').textContent.includes('已存在')")
    rejected = browser.evaluate("""fetch('/api/knowledge-points',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:%s})}).then(async r=>({status:r.status,body:await r.json()}))""" % json.dumps(name,ensure_ascii=False))
    assert rejected["status"] == 400
    assert len(browser.evaluate("KnowledgePointState.snapshot()")) == 1
    capture(browser,"local-point-explicit-and-duplicate")
