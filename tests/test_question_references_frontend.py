"""Reference positions and editor baselines in isolated JavaScript contexts."""
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


STATE = section(API, "const EditorState =", "// Fine-grained points")
PREPARE = section(EDITOR, "function prepare(text, context)", "function appendControls(html, entries)")


def run_js(checks):
    node = shutil.which("node")
    assert node, "Node is required for the isolated reference tests"
    result = subprocess.run(
        [node, "-e", "const assert=require('node:assert/strict');const window={};\n"
         "const inputs={};const document={getElementById:id=>inputs[id]};\n" + STATE
         + "\nlet counter=0;\n"
         "const replacePreviewLiteralSpans=(text,fn)=>text.replace(/`[^`]*`|https?:[^ ]+/g,fn);\n"
         "const replaceDelimitedMathForPreview=(text,fn)=>text.replace(/\\$[^$]*\\$/g,fn);\n"
         "const replaceLatexEnvironmentsForPreview=(text,fn)=>text;\n" + PREPARE
         + "\n" + checks],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_code_point_offsets_require_exact_phrase_and_context():
    text = "😀参照例2.5.5，可知结论。"
    item = {"field": "answer_markdown", "start": 3, "end": 9, "text": "例2.5.5",
            "prefix": "😀参照", "suffix": "，可知", "status": "resolved", "target_question_id": 17, "seq_num": 9}
    run_js("const text=" + json.dumps(text, ensure_ascii=False) + ";const item=" + json.dumps(item, ensure_ascii=False) + r""";
      const context={field:'answer_markdown',source:text,references:[item]};
      const good=prepare(text,context);
      assert.equal(good.entries.length,1);
      assert.equal(good.source.replace(good.entries[0].marker,''),text);
      assert.equal(good.source.indexOf(good.entries[0].marker),text.indexOf('，'));
      for(const patch of [{start:4,end:10},{prefix:'错误'},{suffix:'错误'},{text:'例2.5.6'},
                         {target_question_id:-1},{seq_num:null},{status:'stale'}]) {
        assert.equal(prepare(text,{...context,references:[{...item,...patch}]}).entries.length,0);
      }
      assert.equal(prepare(text+'新增',{...context}).entries.length,0);
      const deleted=prepare(text,{...context,references:[{...item,status:'target_deleted',target_question_id:null,seq_num:null}]});
      assert.equal(deleted.entries.length,1);assert.equal(deleted.entries[0].resolved,false);
    """)


def test_reference_controls_expire_after_edit_switch_draft_and_refresh():
    run_js(r"""
      const q={id:7,content:'题干',answer_markdown:'参照例2.5.5。',review:'评析',question_references:[{field:'answer_markdown'}]};
      inputs.editAnswerMarkdown={value:q.answer_markdown};
      EditorState.useQuestion(q);
      const old=QuestionReferenceState.context('answer_markdown',q.answer_markdown);
      assert.equal(old.isCurrent(),true);
      inputs.editAnswerMarkdown.value+='后续编辑';
      assert.equal(old.isCurrent(),false);
      assert.equal(QuestionReferenceState.context('answer_markdown',inputs.editAnswerMarkdown.value),null);
      inputs.editAnswerMarkdown.value=q.answer_markdown;
      QuestionReferenceState.hydrate({...q,question_references:[]});
      assert.equal(old.isCurrent(),false);
      assert.equal(QuestionReferenceState.context('answer_markdown',q.answer_markdown).references.length,0);
      EditorState.useQuestion({id:8,answer_markdown:q.answer_markdown});
      assert.equal(old.isCurrent(),false);
      EditorState.useDraft({id:'draft-1',answer_markdown:q.answer_markdown});
      assert.equal(QuestionReferenceState.context('answer_markdown',q.answer_markdown),null);
      EditorState.useQuestion(q);EditorState.beginTransition();
      assert.equal(QuestionReferenceState.context('answer_markdown',q.answer_markdown),null);
      EditorState.reset();assert.equal(QuestionReferenceState.context('answer_markdown',q.answer_markdown),null);
    """)


def test_protected_literals_and_math_never_get_reference_controls():
    run_js(r"""
      for(const text of ['`例2.5.5`','$例2.5.5$','https://example.test/例2.5.5']) {
        const start=Array.from(text.slice(0,text.indexOf('例'))).length;
        const ref={field:'answer_markdown',start,end:start+6,text:'例2.5.5',status:'resolved',target_question_id:2,seq_num:2};
        assert.equal(prepare(text,{field:'answer_markdown',source:text,references:[ref]}).entries.length,0);
      }
    """)


def test_textarea_line_normalization_translates_saved_offsets_without_changing_source():
    run_js(r"""
      const raw='😀说明。\r\n参照例2.5.5。\r\n后文';
      const chars=Array.from(raw),start=chars.indexOf('例'),end=start+6;
      const ref={field:'answer_markdown',start,end,text:'例2.5.5',prefix:chars.slice(0,start).join(''),
        suffix:chars.slice(end).join(''),status:'resolved',target_question_id:2,seq_num:2};
      const normalized=raw.replace(/\r\n?/g,'\n');inputs.editAnswerMarkdown={value:normalized};
      EditorState.useQuestion({id:7,answer_markdown:raw,question_references:[ref]});
      const context=QuestionReferenceState.context('answer_markdown',normalized);
      assert.equal(context.isCurrent(),true);assert.equal(context.references[0].start,start-1);
      assert.equal(context.references[0].suffix,'。\n后文');
      const prepared=prepare(normalized,context);assert.equal(prepared.entries.length,1);
      assert.equal(prepared.source.replace(prepared.entries[0].marker,''),normalized);
      assert.equal(ref.start,start);assert.equal(ref.suffix,'。\r\n后文');
      assert.equal(raw,'😀说明。\r\n参照例2.5.5。\r\n后文');
    """)


def test_range_members_share_one_position_marker_and_keep_independent_statuses():
    run_js(r"""
      const source='😀参照例2.5.5～例2.5.7可得结论。',text='例2.5.5～例2.5.7';
      const start=3,end=start+Array.from(text).length;
      const base={field:'answer_markdown',start,end,text,prefix:'😀参照',suffix:'可得结论。',is_range:true};
      const refs=[
        {...base,label_key:'例2.5.5',status:'resolved',target_question_id:12,seq_num:8},
        {...base,label_key:'例2.5.6',status:'target_deleted',target_question_id:null,seq_num:null},
        {...base,label_key:'例2.5.7',status:'unresolved',target_question_id:null,seq_num:null}
      ];
      const prepared=prepare(source,{field:'answer_markdown',source,references:refs});
      assert.equal(prepared.entries.length,1);assert.equal(prepared.entries[0].members.length,3);
      assert.equal(prepared.entries[0].isRange,true);
      assert.deepEqual(prepared.entries[0].members.map(m=>m.reference.status),['resolved','target_deleted','unresolved']);
      assert.equal(prepared.source.replace(prepared.entries[0].marker,''),source);
      assert.equal(prepare(source,{field:'answer_markdown',source,references:[refs[2]]}).entries[0].isRange,true);
    """)


def test_reference_controls_keep_rich_html_allowlist_closed():
    boundary = section(API, "const RICH_HTML_CONFIG", "function asString")
    assert "'button'" not in boundary
    assert "data-question-reference-key" not in boundary
    assert "'a'" not in boundary
    renderer = section(EDITOR, "function parseMarkdownWithMath(text,", "window.parseMarkdownWithMath =")
    assert renderer.index("sanitizeRichHtml") < renderer.index("appendControls")
    assert "control.textContent" in EDITOR
    assert "selectQuestionById(entry.target)" in EDITOR
