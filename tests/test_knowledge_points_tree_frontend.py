"""Point directory navigation checks without an application or database import."""
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/editor.js").read_text(encoding="utf-8")


def section(start, end):
    offset = EDITOR.index(start)
    return EDITOR[offset:EDITOR.index(end, offset)]


def run_js(checks, functions):
    node = shutil.which("node")
    assert node, "Node is required for isolated directory checks"
    result = subprocess.run(
        [node], input="const assert=require('node:assert/strict');\n" + functions + "\n" + checks,
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr + result.stdout


TREE = section("function buildKnowledgePointTree", "function toggleKnowledgePointSelection")
MATCH = section("function knowledgePointMatchesQuery", "// Curriculum paths are recommendations")


def test_real_resource_all_points_remain_reachable_in_curriculum_order():
    points = json.loads((ROOT / "mathbank/resources/knowledge_points.json").read_text(encoding="utf-8"))["points"]
    curriculum = json.loads((ROOT / "mathbank/resources/curriculums/A.json").read_text(encoding="utf-8"))
    run_js("const points=" + json.dumps(points, ensure_ascii=False) + ";\nconst curriculum="
           + json.dumps(curriculum, ensure_ascii=False) + r""";
      const tree=buildKnowledgePointTree(points,'A',curriculum),found=[];
      const visit=nodes=>nodes.forEach(node=>{found.push(...node.points.map(p=>p.code));visit(node.children);});
      visit(tree);
      assert.deepEqual([...new Set(found)].sort(),points.map(p=>p.code).sort());
      const expectedBooks=Object.keys(curriculum);
      if(points.some(point=>!point.curriculum_paths.some(path=>path.version_code==='A')))expectedBooks.push('未关联当前教材');
      assert.deepEqual(tree.map(n=>n.label),expectedBooks);
      // Chapter-level mappings are redundant navigation evidence, whereas
      // genuine mappings to two leaf sections must remain discoverable twice.
      assert.equal(found.filter(code=>code==='1015').length,1);
      assert.equal(found.filter(code=>code==='1177').length,2);
      const withoutH=points.map(point=>({...point,curriculum_paths:point.curriculum_paths.filter(path=>path.version_code!=='H')}));
      const allUnmapped=buildKnowledgePointTree(withoutH,'H',{});
      assert.equal(allUnmapped.length,1);assert.equal(allUnmapped[0].label,'未关联当前教材');
      assert.equal(allUnmapped[0].count,points.length);
    """, TREE)


def test_real_multitextbook_hierarchies_render_only_declared_source_paths():
    resource = json.loads((ROOT / "mathbank/resources/knowledge_points.json").read_text(encoding="utf-8"))
    hierarchies = resource.get("curriculum_hierarchies", {})
    curriculums = {version: json.loads((ROOT / f"mathbank/resources/curriculums/{version}.json").read_text(encoding="utf-8"))
                   for version in hierarchies}
    run_js("const points=" + json.dumps(resource["points"], ensure_ascii=False) + ";\nconst hierarchies="
           + json.dumps(hierarchies, ensure_ascii=False) + ";\nconst curriculums="
           + json.dumps(curriculums, ensure_ascii=False) + r""";
      for (const [version,hierarchy] of Object.entries(hierarchies)) {
        const index=new Map(hierarchy.map(row=>[JSON.stringify([row.compulsory,row.chapter,row.knowledge]),row]));
        const tree=buildKnowledgePointTree(points,version,curriculums[version],hierarchy),found=new Set();
        const visit=branches=>branches.forEach(branch=>{
          branch.points.forEach(point=>{
            found.add(point.code);
            const paths=point.curriculum_paths.filter(path=>path.version_code===version);
            if(branch.path[0]==='未关联当前教材')assert.equal(paths.length,0);
            else assert.ok(paths.some(path=>{
              const row=index.get(JSON.stringify([path.compulsory,path.chapter,path.knowledge]));
              const expected=[path.compulsory,path.chapter,...(row?.ancestors||[]),...(path.knowledge?[path.knowledge]:[])];
              return JSON.stringify(branch.path)===JSON.stringify(expected);
            }),JSON.stringify({version,code:point.code,path:branch.path}));
          });
          visit(branch.children);
        });
        visit(tree);
        assert.deepEqual([...found].sort(),points.map(point=>point.code).sort());
      }
    """, TREE)


def test_only_current_version_paths_are_used_and_custom_points_remain_available():
    run_js(r"""
      const mapped={code:'one',name:'共有考点',curriculum_paths:[
        {version_code:'A',compulsory:'册',chapter:'章',knowledge:'',level:'chapter'},
        {version_code:'A',compulsory:'册',chapter:'章',knowledge:'1.1 节',level:'parent'},
        {version_code:'A',compulsory:'册',chapter:'章',knowledge:'1.1 节',reference_section:'1.1.2 细节',level:'leaf'},
        {version_code:'A',compulsory:'册',chapter:'章',knowledge:'1.2 节',level:'leaf'},
        {version_code:'B',compulsory:'其他教材',chapter:'另章',knowledge:'另节',level:'leaf'}]};
      const custom={code:'local:one',name:'<自定义名称>',curriculum_paths:[]};
      const noVersion={code:'legacy',name:'未证明所属版本',curriculum_paths:[{compulsory:'册',chapter:'章',knowledge:'1.1 节'}]};
      const input=[mapped,custom,noVersion],before=JSON.stringify(input);
      const tree=buildKnowledgePointTree(input,'A',{'册':{'章':['1.2 节','1.1 节']}});
      assert.equal(tree[0].label,'册');assert.equal(tree[0].count,1);
      const chapters=tree[0].children;
      assert.equal(chapters.length,1);
      assert.deepEqual(chapters[0].children.map(n=>n.label),['1.2 节','1.1 节']);
      assert.equal(chapters[0].points.length,0);
      assert.equal(chapters[0].children[1].children[0].label,'1.1.2 细节');
      assert.equal(tree[1].label,'未关联当前教材');
      assert.deepEqual(tree[1].points.map(p=>p.code),['local:one','legacy']);
      assert.equal(JSON.stringify(input),before);
      const b=buildKnowledgePointTree(input,'B',{});
      assert.equal(b[0].label,'其他教材');
      assert.equal(b[0].children[0].label,'另章');
    """, TREE)


def test_explicit_hierarchy_keeps_integer_parents_deep_sections_and_descendant_recommendations():
    run_js(r"""
      const curriculum={'册':{'章':['1 集合','1.1 概念','1.1.1 元素','1.2 关系','2 函数']}};
      const hierarchy=[{compulsory:'册',chapter:'章',knowledge:'1 集合',ancestors:[]},
        {compulsory:'册',chapter:'章',knowledge:'1.1 概念',ancestors:['1 集合']},
        {compulsory:'册',chapter:'章',knowledge:'1.1.1 元素',ancestors:['1 集合','1.1 概念']},
        {compulsory:'册',chapter:'章',knowledge:'1.2 关系',ancestors:['1 集合']},
        {compulsory:'册',chapter:'章',knowledge:'2 函数',ancestors:[]}];
      const path=(knowledge,level='leaf')=>({version_code:'N',compulsory:'册',chapter:'章',knowledge,reference_section:knowledge,level});
      const points=[{code:'parent',name:'仅父节来源',curriculum_paths:[path('1 集合','parent')]},
        {code:'shared',name:'父子共同来源',curriculum_paths:[path('1 集合','parent'),path('1.1.1 元素')]},
        {code:'relation',name:'关系点',curriculum_paths:[path('1.2 关系')]},
        {code:'function',name:'函数点',curriculum_paths:[path('2 函数')]}];
      const tree=buildKnowledgePointTree(points,'N',curriculum,hierarchy),branches=tree[0].children[0].children;
      assert.deepEqual(branches.map(node=>node.label),['1 集合','2 函数']);
      assert.deepEqual(branches[0].points.map(point=>point.code),['parent']);
      assert.deepEqual(branches[0].children.map(node=>node.label),['1.1 概念','1.2 关系']);
      assert.equal(branches[0].children[0].children[0].label,'1.1.1 元素');
      assert.deepEqual(branches[0].children[0].children[0].points.map(point=>point.code),['shared']);
      assert.deepEqual(branches[0].children[1].points.map(point=>point.code),['relation']);
      const parentLocation={compulsory:'册',chapter:'章',knowledge:'1 集合'};
      assert.equal(knowledgePointPathMatchesLocation(path('1.1.1 元素'),'N',parentLocation,hierarchy),true);
      assert.equal(knowledgePointPathMatchesLocation(path('1 集合','parent'),'N',parentLocation,hierarchy),true);
      assert.equal(knowledgePointPathMatchesLocation(path('2 函数'),'N',parentLocation,hierarchy),false);
      assert.equal(knowledgePointPathMatchesLocation(path('1.1.1 元素'),'A',parentLocation,hierarchy),false);
      assert.equal(knowledgePointPathMatchesLocation(path('1.1.1 元素'),'N',parentLocation,[]),false);
      assert.equal(knowledgePointPathMatchesLocation(path('1 集合','parent'),'N',{...parentLocation,knowledge:'1.1 概念'},hierarchy),false);
    """, TREE)


def test_search_keeps_name_original_alias_and_exact_full_code_contract():
    run_js(r"""
      const point={code:'1230',name:'二分法',original_name:'方程求根',aliases:['折半法'],source_codes:['1230','999001']};
      assert.equal(knowledgePointMatchesQuery(point,'二分'),true);
      assert.equal(knowledgePointMatchesQuery(point,'求根'),true);
      assert.equal(knowledgePointMatchesQuery(point,'折半'),true);
      assert.equal(knowledgePointMatchesQuery(point,'123'),false);
      assert.equal(knowledgePointMatchesQuery(point,'#123'),false);
      assert.equal(knowledgePointMatchesQuery(point,'#1230'),true);
      assert.equal(knowledgePointMatchesQuery(point,'#999001'),true);
      assert.equal(knowledgePointMatchesQuery(point,'#999'),false);
      assert.equal(knowledgePointMatchesQuery(point,'999001'),false);
      const catalogPoint={code:'zxxk:catalog:210505',name:'集合目录',source:'zxxk',source_codes:['zxxk:catalog:210505']};
      assert.equal(knowledgePointMatchesQuery(catalogPoint,'#zxxk:catalog:210505'),true);
      assert.equal(knowledgePointMatchesQuery(catalogPoint,'#210505'),false);
      assert.equal(knowledgePointMatchesQuery(catalogPoint,'#zxxk:catalog:2105'),false);
      assert.equal(knowledgePointMatchesQuery(catalogPoint,'210505'),false);
    """, MATCH)


def test_leaf_selection_reads_current_state_instead_of_a_stale_tree_snapshot():
    toggle = section("function toggleKnowledgePointSelection", "function knowledgePointOption")
    run_js(r"""
      const view={kind:'bank'},point={code:'shared',name:'多处共用'};
      toggleKnowledgePointSelection(view,point);
      assert.deepEqual(state.knowledge_point_codes,['shared']);assert.equal(state.knowledge_point_unlabeled,false);
      toggleKnowledgePointSelection(view,point);
      assert.deepEqual(state.knowledge_point_codes,[]);
      KnowledgePointState.busy=true;
      toggleKnowledgePointSelection({kind:'edit'},point);assert.deepEqual(labels,[]);
      KnowledgePointState.busy=false;
      toggleKnowledgePointSelection({kind:'edit'},point);assert.equal(labels[0].code,'shared');
      toggleKnowledgePointSelection({kind:'edit'},point);assert.deepEqual(labels,[]);
    """, r"""
      let state={knowledge_point_codes:[],knowledge_point_unlabeled:true},labels=[];
      const KnowledgePointState={busy:false,filter:()=>state,snapshot:()=>labels,
        add:point=>labels.push({...point,status:'confirmed'}),remove:code=>{labels=labels.filter(p=>p.code!==code);}};
      function updateKnowledgePointFilter(kind,patch){state={...state,...patch};}
    """ + toggle)


def test_taller_directory_uses_available_visual_viewport_and_recommendations_stay_compact():
    position = section("function positionKnowledgePointPicker", "function setKnowledgePointPickerOpen")
    run_js(r"""
      let innerWidth=1600,innerHeight=1100;
      const window={};
      const view={picker:{hidden:false,style:{setProperty(key,value){this[key]=value;}},
        getBoundingClientRect(){return {height:Math.min(this.contentHeight,parseFloat(this.style['--mb-kp-panel-height']))};},contentHeight:900},
        toggle:{getBoundingClientRect(){return {left:80,top:220,bottom:252,width:90,height:32};}},
        options:{classList:{contains:()=>true}}};
      positionKnowledgePointPicker(view);
      assert.equal(view.picker.style.width,'520px');
      assert.equal(view.picker.style['--mb-kp-panel-height'],'660px');
      assert.equal(view.picker.style.top,'258px');
      view.options.classList.contains=()=>false;view.picker.contentHeight=230;
      positionKnowledgePointPicker(view);
      assert.equal(view.picker.style['--mb-kp-panel-height'],'430px');
      assert.equal(view.picker.style.top,'258px');
      view.options.classList.contains=()=>true;view.picker.contentHeight=900;
      view.toggle.getBoundingClientRect=()=>({left:1400,top:780,bottom:812,width:90,height:32});
      positionKnowledgePointPicker(view);
      assert.equal(view.picker.style.left,'1068px');
      assert.equal(view.picker.style.top,'114px');
      // A keyboard/pinch viewport has its own edges rather than the layout viewport's.
      window.visualViewport={offsetLeft:30,offsetTop:120,width:375,height:450};
      view.toggle.getBoundingClientRect=()=>({left:48,top:430,bottom:474,width:100,height:44});
      positionKnowledgePointPicker(view);
      assert.equal(view.picker.style.width,'351px');
      assert.equal(view.picker.style.left,'42px');
      assert.equal(view.picker.style['--mb-kp-panel-height'],'292px');
      assert.equal(view.picker.style.top,'132px');
      view.toggle.getBoundingClientRect=()=>({left:48,top:170,bottom:214,width:100,height:44});
      positionKnowledgePointPicker(view);
      assert.equal(view.picker.style['--mb-kp-panel-height'],'338px');
      assert.equal(view.picker.style.top,'220px');
    """, position)
