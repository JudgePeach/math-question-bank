"""Six source-backed directories and round trips in a disposable real browser."""
import json
from pathlib import Path

from test_preview_browser import browser, pytestmark  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.system_generated/audits/zhixue-multi-curricula-20261017/browser-qa'


def test_six_directories_source_aliases_and_question_round_trip(browser):
    assert browser.root.resolve() != ROOT.resolve()
    assert browser.evaluate('location.port') != '8000'
    original = browser.evaluate(r'''(async()=>{
      const form=new FormData();
      Object.entries({content:'六版隔离回归：判断集合 $A=\\{1,2\\}$。',answer_markdown:'保留原解析',
        tags:'保留原自由标签',question_type:'detailed_answer',difficulty:'normal',
        category_compulsory:'必修一',category_chapter:'1. 集合与常用逻辑用语',category_knowledge:'1.1 集合的概念',
        knowledge_points:JSON.stringify([{code:'1015',role:'main',status:'confirmed',evidence:['原文依据'],reason:'保留理由'}])
      }).forEach(([k,v])=>form.set(k,v));
      const res=await fetch('/api/questions',{method:'POST',body:form}),data=await res.json();
      if(!res.ok)throw Error(JSON.stringify(data));return data.question;
    })()''')
    reports = []
    for version in ('B', 'S', 'N', 'X', 'A'):
        report = browser.evaluate('''(async()=>{
          const version=%s;
          const preset=await fetch('/api/config/curriculum-presets/'+version).then(r=>r.json());
          const res=await fetch('/api/config/metadata',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(preset.metadata)});
          const saved=await res.json();if(!res.ok)throw Error(JSON.stringify(saved));
          await loadCategories();await loadKnowledgePointCatalog(true);
          const question=await fetch('/api/questions/%s').then(r=>r.json());
          const page=await fetch('/api/knowledge-points?version_code='+version+'&limit=1000').then(r=>r.json());
          return {version,active:window.activeCurriculumVersion,question,points:page.total,hierarchy:page.hierarchy?.length||0,switch:saved.classification_switch};
        })()''' % (json.dumps(version), original['id']))
        assert report['active'] == version
        assert report['points'] >= 681
        question = report['question']
        assert question['content'] == original['content']
        assert question['answer_markdown'] == original['answer_markdown']
        assert question['tags'] == original['tags']
        assert question['knowledge_points'] == original['knowledge_points']
        assert question['category_knowledge']
        if version in ('B', 'S', 'N', 'X'):
            assert report['hierarchy'] > 100
        reports.append({k:v for k,v in report.items() if k!='question'})
        if version in ('N', 'X'):
            browser.evaluate("selectWorkspace('bank','题库管理');true")
            if not browser.evaluate("document.getElementById('bankFilterFields').checkVisibility()"):
                browser.command('click', '.bank-filter-toggle')
            browser.command('wait', '--fn', "document.getElementById('bankFilterFields').checkVisibility()")
            browser.command('click', '#bankKnowledgePointPicker .mb-kp-toggle')
            browser.command('wait', '--fn', "document.getElementById('mb-kp-picker-bank').checkVisibility()")
            browser.command('click', '#mb-kp-picker-bank .mb-kp-tools button:nth-child(2)')
            browser.command('wait', '--fn', "document.querySelector('#mb-kp-picker-bank .mb-kp-tree-toggle')?.checkVisibility()")
            tree = browser.evaluate("[...document.querySelectorAll('#mb-kp-picker-bank .mb-kp-tree-label')].map(n=>n.textContent)")
            assert '必修一' in tree
            EVIDENCE.mkdir(parents=True, exist_ok=True)
            browser.command('screenshot', str(EVIDENCE / ('directory-'+version+'.png')))
            browser.command('fill', '#mb-kp-picker-bank input[type=search]', '#943045')
            browser.command('wait', '--fn', "document.querySelectorAll('#mb-kp-picker-bank .mb-kp-option').length===1")
            assert browser.evaluate("document.querySelector('#mb-kp-picker-bank .mb-kp-option').dataset.code") == '1823'
            browser.command('click', '#bankKnowledgePointPicker .mb-kp-toggle')
    restored = browser.evaluate("fetch('/api/questions/%s').then(r=>r.json())" % original['id'])
    assert [restored[k] for k in ('category_compulsory','category_chapter','category_knowledge')] == [original[k] for k in ('category_compulsory','category_chapter','category_knowledge')]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE/'round-trip.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2)+'\n')
