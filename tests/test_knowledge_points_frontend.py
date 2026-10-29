"""Shipped point state, stale replies, and filter contracts in isolated Node contexts."""
from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/editor.js").read_text(encoding="utf-8")


def section(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


STATE = section(API, "const EditorState =", "// Global variables")


def run_js(checks, extra=""):
    node = shutil.which("node")
    assert node, "Node is required for isolated state checks"
    result = subprocess.run(
        [node, "-"], input=("const assert=require('node:assert/strict');const window={};\n"
         + STATE + "\n" + extra + "\n(async()=>{\n" + checks
         + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"), encoding="utf-8",
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_suggestions_require_acceptance_and_research_origin_survives_role_edits():
    run_js(r"""
      EditorState.useQuestion({id:7});
      KnowledgePointState.hydrate([{code:'1796',name:'通项',role:'main',status:'suggested',
        origin:'research',scope:['(1)'],evidence:'原文',research:{batch:'frozen'}},
        {code:'1803',name:'求和',role:'auxiliary',status:'suggested',origin:'research'}]);
      const captured=KnowledgePointState.capture();
      assert.equal(captured.labels.every(p=>p.status==='suggested'),true);
      KnowledgePointState.accept('1796');
      assert.deepEqual(KnowledgePointState.snapshot().map(p=>p.status),['confirmed','suggested']);
      KnowledgePointState.setRole('1796','auxiliary');
      const first=KnowledgePointState.snapshot()[0];
      assert.equal(first.origin,'research');assert.deepEqual(first.scope,['(1)']);
      assert.deepEqual(first.research,{batch:'frozen'});assert.equal(first.evidence,'原文');
      first.research.batch='mutated';assert.equal(KnowledgePointState.snapshot()[0].research.batch,'frozen');
      KnowledgePointState.add({code:'1859',name:'零点'});
      assert.equal(KnowledgePointState.snapshot().at(-1).status,'confirmed');
      assert.equal(KnowledgePointState.snapshot().at(-1).role,'main');
    """)


def test_save_ack_never_overwrites_later_points_or_another_editor_session():
    run_js(r"""
      EditorState.useQuestion({id:7});KnowledgePointState.add({code:'1796',name:'通项'});
      const before=KnowledgePointState.capture();
      KnowledgePointState.setRole('1796','auxiliary');
      assert.equal(KnowledgePointState.acknowledge(before,before.labels),false);
      assert.equal(KnowledgePointState.snapshot()[0].role,'auxiliary');
      const current=KnowledgePointState.capture();EditorState.useQuestion({id:8});
      KnowledgePointState.hydrate([{code:'1088',name:'最值',role:'main',status:'confirmed'}]);
      assert.equal(KnowledgePointState.acknowledge(current,current.labels),false);
      assert.equal(KnowledgePointState.snapshot()[0].code,'1088');
      const saved=KnowledgePointState.capture();assert.equal(KnowledgePointState.acknowledge(saved,saved.labels),true);
      EditorState.reset();assert.deepEqual(KnowledgePointState.snapshot(),[]);
    """)


def test_any_all_role_and_unlabeled_include_legacy_labels_without_confirmation():
    run_js(r"""
      const labels=[{code:'1796',role:'main',status:'confirmed'},
        {code:'1803',role:'auxiliary',status:'confirmed'},
        {code:'1859',role:'main',status:'suggested'}];
      let f=KnowledgePointState.setFilter('bank',{knowledge_point_codes:['1796','1803']});
      assert.equal(KnowledgePointState.matches(labels,f),true);
      f=KnowledgePointState.setFilter('bank',{knowledge_point_mode:'all'});
      assert.equal(KnowledgePointState.matches(labels,f),false);
      f=KnowledgePointState.setFilter('bank',{knowledge_point_role:'all'});
      assert.equal(KnowledgePointState.matches(labels,f),true);
      f=KnowledgePointState.setFilter('bank',{knowledge_point_codes:['1859']});
      assert.equal(KnowledgePointState.matches(labels,f),true);
      assert.equal(f.knowledge_point_status,'all');
      f=KnowledgePointState.setFilter('bank',{knowledge_point_status:'confirmed'});
      assert.equal(f.knowledge_point_status,'all','old saved UI state cannot hide legacy labels');
      f=KnowledgePointState.setFilter('bank',{knowledge_point_status:'all'});
      assert.equal(KnowledgePointState.matches(labels,f),true);
      const params=new URLSearchParams({chapter:'4. 数列',q:'题干文字'});
      KnowledgePointState.appendParams(params,KnowledgePointState.setFilter('bank',{knowledge_point_codes:['1796','1803']}));
      assert.deepEqual(params.getAll('knowledge_point_codes'),['1796','1803']);
      assert.equal(params.get('chapter'),'4. 数列');assert.equal(params.get('q'),'题干文字');
      assert.equal(params.get('knowledge_point_mode'),'all');
      assert.equal(params.get('knowledge_point_status'),'all');
      f=KnowledgePointState.setFilter('bank',{knowledge_point_codes:[],knowledge_point_unlabeled:true});
      assert.equal(KnowledgePointState.matches([],f),true);
      assert.equal(KnowledgePointState.matches(labels,f),false);
      assert.equal(KnowledgePointState.matches(labels,{...f,knowledge_point_unlabeled:false}),true);
    """)


def test_ai_merge_is_immediately_usable_preserves_existing_and_rejects_late_edits():
    run_js(r"""
      EditorState.useQuestion({id:7});
      KnowledgePointState.hydrate([{code:'1796',name:'已有点',role:'auxiliary',status:'suggested',origin:'manual'}]);
      const capture=KnowledgePointState.capture();
      assert.equal(KnowledgePointState.mergeSuggestions(capture,[
        {code:'1796',name:'AI覆盖',role:'main',status:'confirmed'},
        {code:'1803',name:'新点',role:'auxiliary',status:'suggested',origin:'ai_classification',evidence:'求和',reason:'直接考查'}]),true);
      const points=KnowledgePointState.snapshot();
      assert.equal(points[0].name,'已有点');assert.equal(points[0].role,'auxiliary');
      assert.equal(points[0].status,'suggested','existing legacy status is not rewritten');
      assert.equal(points[1].status,'confirmed');assert.equal(points[1].evidence,'求和');
      assert.equal(KnowledgePointState.matches(points,KnowledgePointState.setFilter('bank',{knowledge_point_codes:['1796'],knowledge_point_role:'all'})),true);
      assert.equal(KnowledgePointState.mergeSuggestions(capture,[{code:'1859'}]),false);
      const fresh=KnowledgePointState.capture();EditorState.useQuestion({id:8});
      assert.equal(KnowledgePointState.mergeSuggestions(fresh,[{code:'1859'}]),false);
    """)


def test_paper_filters_share_semantics_without_touching_the_cart():
    run_js(r"""
      window.PaperStore={filters:{},cart:[{id:11,score:5}]};
      const f=KnowledgePointState.setFilter('paper',{knowledge_point_codes:['1796'],knowledge_point_mode:'all'});
      assert.deepEqual(window.PaperStore.cart,[{id:11,score:5}]);
      assert.equal(KnowledgePointState.matches([{code:'1796',role:'main',status:'confirmed'}],f),true);
      assert.deepEqual(KnowledgePointState.filter('bank').knowledge_point_codes,[]);
    """)


def test_directory_caching_uses_the_current_curriculum_and_never_hydrates_labels():
    loader = section(EDITOR, "async function loadKnowledgePointCatalog", "function reloadBankKnowledgePointFilters")
    extra = r"""
      const knowledgePointCatalogCache=new Map(),knowledgePointNames=new Map();
      let renders=0;const requests=[];
      function knowledgePointVersion(){return window.activeCurriculumVersion;}
      function renderKnowledgePointViews(){renders++;}
      function fetch(url){return new Promise(resolve=>requests.push({url,resolve}));}
    """ + loader
    run_js(r"""
      window.activeCurriculumVersion='A';KnowledgePointState.add({code:'1796',name:'用户选择'});
      const a=loadKnowledgePointCatalog();const aAgain=loadKnowledgePointCatalog();
      assert.equal(requests.length,1);assert.ok(requests[0].url.includes('version_code=A'));
      window.activeCurriculumVersion='B';const b=loadKnowledgePointCatalog();assert.equal(requests.length,2);
      requests[1].resolve({ok:true,json:async()=>({items:[{code:'B1',name:'B目录'}],total:1})});await b;
      const rendered=renders;
      requests[0].resolve({ok:true,json:async()=>({items:[{code:'A1',name:'A目录'}],total:1})});await a;await aAgain;
      assert.equal(renders,rendered);assert.equal(knowledgePointCatalogCache.get('B').items[0].code,'B1');
      assert.equal(KnowledgePointState.snapshot()[0].code,'1796');
    """, extra)


def test_directory_fetches_every_page_before_publishing_or_marking_ready():
    loader = section(EDITOR, "async function loadKnowledgePointCatalog", "function reloadBankKnowledgePointFilters")
    extra = r"""
      const knowledgePointCatalogCache=new Map(),knowledgePointNames=new Map();
      const requests=[];let renders=0;
      function knowledgePointVersion(){return window.activeCurriculumVersion;}
      function renderKnowledgePointViews(){renders++;}
      function fetch(url){return new Promise(resolve=>requests.push({url,resolve}));}
      const tick=()=>new Promise(setImmediate);
    """ + loader
    run_js(r"""
      window.activeCurriculumVersion='A';
      const pending=loadKnowledgePointCatalog();
      const first=Array.from({length:1000},(_,i)=>({code:String(i),name:'知识点'+i}));
      const hierarchy=[{compulsory:'册',chapter:'章',knowledge:'1 集合',ancestors:[]},
        {compulsory:'册',chapter:'章',knowledge:'1.1 概念',ancestors:['1 集合']}];
      requests[0].resolve({ok:true,json:async()=>({items:first,total:1002,offset:0,version_code:'A',hierarchy})});
      await tick();
      assert.equal(requests.length,2);
      assert.equal(new URL(requests[1].url,'http://localhost').searchParams.get('offset'),'1000');
      assert.equal(knowledgePointCatalogCache.get('A').ready,false);
      assert.equal(knowledgePointCatalogCache.get('A').items.length,0);
      assert.equal(knowledgePointNames.size,0);
      requests[1].resolve({ok:true,json:async()=>({items:[{code:'1000',name:'末页甲'},{code:'1001',name:'末页乙'}],total:1002,offset:1000,version_code:'A'})});
      const items=await pending;
      assert.equal(items.length,1002);assert.equal(new Set(items.map(p=>p.code)).size,1002);
      assert.equal(knowledgePointCatalogCache.get('A').ready,true);
      assert.deepEqual(knowledgePointCatalogCache.get('A').hierarchy,hierarchy);
      assert.equal(knowledgePointNames.get('1001'),'末页乙');
      await loadKnowledgePointCatalog();assert.equal(requests.length,2);
    """, extra)


def test_directory_failure_keeps_the_last_complete_cache_and_never_accepts_partial_pages():
    loader = section(EDITOR, "async function loadKnowledgePointCatalog", "function reloadBankKnowledgePointFilters")
    extra = r"""
      const knowledgePointCatalogCache=new Map(),knowledgePointNames=new Map();
      function knowledgePointVersion(){return window.activeCurriculumVersion;}
      function renderKnowledgePointViews(){}
      let responses=[];
      async function fetch(){const value=responses.shift();if(!value)throw Error('unexpected fetch');return {ok:true,json:async()=>value};}
    """ + loader
    run_js(r"""
      window.activeCurriculumVersion='A';
      const prior=[{code:'prior',name:'上次完整目录'}];
      const cases=[
        [{items:[{code:'one',name:'甲'}],total:2},{items:[],total:2}],
        [{items:[{code:'one',name:'甲'}],total:2},{items:[{code:'one',name:'重复'}],total:2}],
        [{items:[{code:'one',name:'甲'}],total:2},{items:[{code:'two',name:'乙'}],total:3}],
        [{items:[{code:'one',name:'甲'}],total:2},{items:[{code:'two'}],total:2}],
        [{items:[{code:'one',name:'甲'}],total:0}],
        [{items:[],total:'0'}],
        [{items:[],total:0,version_code:'B'}],
        [{items:[],total:0,offset:10}],
        [{items:[],total:0,hierarchy:{}}],
        [{items:[],total:0,hierarchy:[{compulsory:'册',chapter:'章',knowledge:'节',ancestors:['节']}]}]
      ];
      for (const pages of cases) {
        knowledgePointCatalogCache.set('A',{ready:true,items:prior,promise:null});
        responses=pages;
        await assert.rejects(loadKnowledgePointCatalog(true));
        const cached=knowledgePointCatalogCache.get('A');
        assert.equal(cached.ready,false);assert.deepEqual(cached.items,prior);assert.ok(cached.error);
        assert.equal(knowledgePointNames.has('one'),false);
      }
      responses=[{items:[],total:0,offset:0,version_code:'A'}];
      assert.deepEqual(await loadKnowledgePointCatalog(true),[]);
      assert.equal(knowledgePointCatalogCache.get('A').ready,true);
    """, extra)


def test_forced_directory_reload_cannot_be_overwritten_by_a_late_same_version_request():
    loader = section(EDITOR, "async function loadKnowledgePointCatalog", "function reloadBankKnowledgePointFilters")
    extra = r"""
      const knowledgePointCatalogCache=new Map(),knowledgePointNames=new Map(),requests=[];
      let renders=0;
      function knowledgePointVersion(){return 'A';}
      function renderKnowledgePointViews(){renders++;}
      function fetch(url){return new Promise(resolve=>requests.push({url,resolve}));}
    """ + loader
    run_js(r"""
      const old=loadKnowledgePointCatalog(),fresh=loadKnowledgePointCatalog(true);
      requests[1].resolve({ok:true,json:async()=>({items:[{code:'shared',name:'新目录'}],total:1})});
      await fresh;const rendered=renders;
      requests[0].resolve({ok:true,json:async()=>({items:[{code:'shared',name:'迟到旧目录'}],total:1})});
      await old;
      assert.equal(knowledgePointCatalogCache.get('A').items[0].name,'新目录');
      assert.equal(knowledgePointNames.get('shared'),'新目录');assert.equal(renders,rendered);
    """, extra)


def test_untrusted_names_are_written_as_dom_text():
    node_factory = section(EDITOR, "function knowledgePointNode", "function knowledgePointButton")
    extra = "const document={createElement:tag=>({tag,textContent:'',className:''})};\n" + node_factory
    run_js("const name=" + json.dumps('<img src=x onerror="bad()">', ensure_ascii=False)
           + ";const node=knowledgePointNode('span',name);assert.equal(node.textContent,name);"
             "assert.equal(Object.hasOwn(node,'innerHTML'),false);", extra)


def test_save_and_draft_integration_keep_the_old_curriculum_and_tags_separate():
    importer = (ROOT / "static/js/import.js").read_text(encoding="utf-8")
    paper = (ROOT / "static/js/paper.js").read_text(encoding="utf-8")
    assert "formData.append('knowledge_points', JSON.stringify(knowledgePoints))" in importer
    assert "KnowledgePointState.acknowledge(knowledgePointCapture, savedKnowledgePoints)" in importer
    assert "KnowledgePointState.signature(KnowledgePointState.snapshot())" in EDITOR
    assert "KnowledgePointState.hydrate(draft.knowledge_points || [])" in EDITOR
    assert "...KnowledgePointState.filter('paper')" in paper
    assert "KnowledgePointState.appendParams(params, KnowledgePointState.filter('paper'))" in paper
    assert "formData.append('sub_knowledge'" not in importer
