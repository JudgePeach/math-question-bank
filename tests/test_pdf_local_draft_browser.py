"""Real offline DOM: local bodies remain editable while metadata is optional."""
import json
import os
from pathlib import Path

import pytest

from test_preview_browser import browser  # noqa: F401
from test_document_projects_browser import MOCK, _click

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.system_generated/pdf-local-draft-20261019/browser'
pytestmark = pytest.mark.skipif(os.environ.get('MATHBANK_TEST_BROWSER') != '1', reason='opt-in isolated browser')


@pytest.mark.parametrize('width,height', [(1600, 1100), (375, 812)])
def test_first_ocr_local_cards_edit_metadata_failure_and_safe_metadata_adoption(browser, width, height):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('network', 'route', '**/api/ai/**', '--abort')
    browser.evaluate('window.__localDraftWidth=' + str(width))
    browser.evaluate(r"""(()=>{
      window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      const id='local-draft-browser-'+window.__localDraftWidth,now=Date.now()/1000;
      const project={project_id:id,revision:1,status:'completed',question_count:0,can_resume:false,task_id:null,
       title:'首次识图本地题稿',last_used_at:now,expires_at:now+7*86400,total_bytes:2048,
       draft:{questions:[],baselineQuestions:[],paperTitle:'首次识图本地题稿'},
       source_pages:Array.from({length:9},(_,i)=>({page_number:i+1,markdown:'首次识图第'+(i+1)+'页原文 $x+1$。'}))};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify({[id]:project}));window.__localDraftId=id;
      window.__localDraftReport={pdf_local_draft:{status:'ready',source_basis:'first_pass_transcription',source_preserved:true,
       question_count:19,unresolved_ranges:[],page_issues:[]},pdf_draft_metadata:{status:'preparing',calls:0,new_calls:0}};
      window.__localDraftCards=Array.from({length:19},(_,i)=>({project_card_id:'local-card-'+(i+1),source_number:i+1,
       content:'首次识图第'+(i+1)+'题：已知 $x+1=2$，求 $x$。',answer_markdown:'原版答案 $1$。',
       question_type:'detailed_answer',difficulty:'normal',category_compulsory:'',category_chapter:'',category_knowledge:'',image_paths:[],
       source_review:{required:true,blocking:false,disposition:'advisory',source_basis:'first_pass_transcription',reasons:['首次转录稿可对照原页校对。']},
       classification_review:{required:true,reason_codes:['pdf_local_draft_metadata_unavailable'],
        original:{category_compulsory:'',category_chapter:'',category_knowledge:''}}}));return true;
    })()""")
    browser.evaluate(MOCK)
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(window.__localDraftId)')
    browser.evaluate(r"""(()=>{
      window.__projectBrowserActions=[];
      const session=window.MathBankDocumentImportSession;
      session.update({phase:'local_draft',taskId:'mock-local-draft',projectId:window.__localDraftId,diagnostics:window.__localDraftReport});
      session.previewLocalDraft({status:'local_draft',task_id:'mock-local-draft',project_id:window.__localDraftId,
       diagnostics:window.__localDraftReport,data:structuredClone(window.__localDraftCards)});
      session.update({phase:'ai_metadata',diagnostics:window.__localDraftReport});
      window.__localPreviewStarted=Date.now();return true;
    })()""")
    browser.command('wait', '--fn', 'Date.now()-window.__localPreviewStarted>750')
    assert browser.evaluate("document.querySelectorAll('#parsedCardsContainer > .glass-card').length") == 19
    assert browser.evaluate('getCheckedUnsavedIndices()') == []
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-compulsory').value") == ''
    assert browser.evaluate("document.querySelector('#parsed-card-0 .card-chapter').value") == ''
    assert not browser.evaluate("window.__projectBrowserActions.some(row=>row.method==='PUT')")
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '教师微调后的原题 $x^2+1$。')
    browser.command('fill', '#parsed-card-0 .card-answer-textarea', '教师微调后的原版答案 $3$。')
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__localDraftId].draft.questions[0].answer_markdown==='教师微调后的原版答案 $3$。'")
    browser.evaluate(r"""(()=>{
      const session=window.MathBankDocumentImportSession;
      const report={...window.__localDraftReport,pdf_draft_metadata:{status:'classification_pending',calls:1,new_calls:1,metadata_failed:true}};
      session.update({phase:'completed',diagnostics:report});
      const kept=session.mergeCompletion(structuredClone(window.__localDraftCards));
      replaceParsedQuestions(kept,{saveDraft:false});renderParsedQuestionsList(kept,{diagnostics:report});
      session.completed({status:'completed',task_id:'mock-local-draft',project_id:window.__localDraftId,
        diagnostics:report,data:window.__localDraftCards},kept);return true;
    })()""")
    assert browser.evaluate('parsedQuestionsData.length') == 19
    assert browser.evaluate('getCheckedUnsavedIndices()') == []
    assert browser.evaluate('parsedQuestionsData[0].content') == '教师微调后的原题 $x^2+1$。'
    assert browser.evaluate("document.querySelector('.document-project-parse-summary').textContent.includes('本次新增分类请求 1 次')")
    assert not browser.evaluate("document.querySelector('.document-project-parse-summary').textContent.includes('拆题请求')")
    browser.command('select', '#parsed-card-1 .card-compulsory', '必修二')
    browser.command('select', '#parsed-card-1 .card-chapter', '9. 统计')
    assert browser.evaluate('validateParsedQuestionBeforeImport(1,{notify:false,focus:false})')
    assert browser.evaluate('parsedQuestionsData[1].classification_review.required') is False
    browser.command('fill', '#parsed-card-1 .card-content-textarea', '手选分类的人工正文必须保留。')
    browser.command('fill', '#parsed-card-1 .card-answer-textarea', '手选分类的人工答案必须保留。')
    browser.evaluate(r"""(()=>{
      const session=window.MathBankDocumentImportSession;
      const report={...window.__localDraftReport,pdf_draft_metadata:{status:'completed',calls:1,new_calls:1}};
      session.update({phase:'completed',diagnostics:report});
      const incoming=window.__localDraftCards.map(q=>{const row={...q,category_compulsory:'必修二',category_chapter:'10. 概率'};
       delete row.classification_review;return row;});
      const kept=session.mergeCompletion(incoming);replaceParsedQuestions(kept,{saveDraft:false});
      renderParsedQuestionsList(kept,{diagnostics:report});return true;
    })()""")
    assert browser.evaluate('parsedQuestionsData[0].content') == '教师微调后的原题 $x^2+1$。'
    assert browser.evaluate('parsedQuestionsData[0].answer_markdown') == '教师微调后的原版答案 $3$。'
    assert browser.evaluate('parsedQuestionsData[0].classification_review.required') is False
    assert browser.evaluate('parsedQuestionsData[1].category_chapter') == '9. 统计'
    assert browser.evaluate('parsedQuestionsData[1].project_incoming_candidate.metadata_only') is True
    assert browser.evaluate("document.querySelector('#parsed-card-1 .document-project-candidate button').textContent") == '采用分类建议'
    browser.evaluate("document.querySelector('#parsed-card-1 .document-project-candidate').open=true;true")
    _click(browser, '#parsed-card-1 .document-project-candidate button')
    assert browser.evaluate('parsedQuestionsData[1].content') == '手选分类的人工正文必须保留。'
    assert browser.evaluate('parsedQuestionsData[1].answer_markdown') == '手选分类的人工答案必须保留。'
    assert browser.evaluate('parsedQuestionsData[1].category_chapter') == '10. 概率'
    assert browser.evaluate("document.querySelectorAll('#documentProjectPageEditor textarea').length") == 9
    assert not browser.evaluate("window.__projectBrowserActions.some(row=>/\\/(analyze|resume)$/.test(row.path))")
    browser.command('scrollintoview', '#documentImportRecoveryPanel');browser.settle()
    browser.command('screenshot', str(EVIDENCE / f'local-draft-editable-{width}.png'))
    (EVIDENCE / f'evidence-{width}.json').write_text(json.dumps({'viewport': [width, height], 'cards': 19,
        'preview_progress_puts': 0, 'metadata_failure_cards_retained': True, 'teacher_body_answer_preserved': True,
        'candidate_adopts_only_metadata': True, 'model_calls': 0}, ensure_ascii=False, indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')


@pytest.mark.parametrize('width,height', [(1600, 1100), (375, 812)])
def test_fresh_upload_zero_cards_saved_ocr_reopen_and_page_edit_conflict(browser, width, height):
    """Replay the real fresh-upload/poll path, with no pre-existing card draft."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('network', 'route', '**/api/ai/**', '--abort')
    browser.evaluate('window.__freshZeroWidth=' + str(width))
    browser.evaluate(r"""(()=>{
      window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      const id='fresh-zero-browser-'+window.__freshZeroWidth,now=Date.now()/1000;
      const diagnostics={pdf_project_legacy_parse:{status:'partial',partial:true,new_calls:0},
       pdf_local_draft:{status:'partial',persisted:true,question_count:0,source_preserved:true,
        source_basis:'first_pass_transcription',manual_reasons:['answer_section_ambiguous'],
        unresolved_ranges:[],page_issues:[{page_number:5,reason_codes:['answer_section_ambiguous']},
          {page_number:7,reason_codes:['answer_section_ambiguous']}]}};
      const project={project_id:id,revision:0,status:'completed',partial:true,question_count:0,
       has_draft:false,has_baseline:false,stage_count:14,draft:null,can_resume:false,task_id:'fresh-zero-task',
       title:'新上传首次识图稿',last_used_at:now,expires_at:now+7*86400,total_bytes:2048,diagnostics,
       source_pages:Array.from({length:9},(_,i)=>({page_number:i+1,markdown:'新上传第'+(i+1)+'页已保存原文'}))};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify({[id]:project}));
      window.__freshZeroId=id;window.__freshZeroProject=project;
      window.__freshZeroTask={task_id:'fresh-zero-task',project_id:id,project_revision:0,status:'completed',
       partial:true,document_type:'pdf',source_available:true,can_resume:false,diagnostics,data:[],progress:100};
      return true;
    })()""")
    browser.evaluate(MOCK)
    browser.evaluate(r"""(()=>{
      const projectFetch=window.fetch;
      window.fetch=(input,options={})=>{
        const path=new URL(String(input?.url||input),location.href).pathname;
        if(path==='/api/upload/pdf-task'){
          window.__projectBrowserActions.push({path,method:'POST',fixture:true});
          return Promise.resolve(new Response(JSON.stringify({status:'success',task_id:'fresh-zero-task',
            project_id:window.__freshZeroId,project_revision:0,expires_at:window.__freshZeroProject.expires_at}),
            {status:200,headers:{'Content-Type':'application/json'}}));
        }
        if(path==='/api/tasks/fresh-zero-task/status'){
          window.__projectBrowserActions.push({path,method:'GET',fixture:true});
          return Promise.resolve(new Response(JSON.stringify(window.__freshZeroTask),
            {status:200,headers:{'Content-Type':'application/json'}}));
        }
        if(path==='/api/tasks/fresh-zero-task/retain')return Promise.resolve(new Response('{}',
          {status:200,headers:{'Content-Type':'application/json'}}));
        return projectFetch(input,options);
      };
      window.currentPdfFile=new File(['deterministic local UI fixture'],'first-upload.pdf',{type:'application/pdf'});
      window.currentDocxFile=null;document.getElementById('importPaperTitle').value='首次上传无题卡回归';
      document.getElementById('importLatexContent').value='';runAIPaperParse();return true;
    })()""")
    browser.command('wait', '--fn', "document.getElementById('documentImportRecoveryPanel')?.textContent.includes('原文件和9页识图稿已保存；题卡尚未整理')")
    browser.evaluate('window.__freshZeroCompletedAt=Date.now();true')
    browser.command('wait', '--fn', 'Date.now()-window.__freshZeroCompletedAt>750')
    assert browser.evaluate('parsedQuestionsData.length') == 0
    assert browser.evaluate("document.querySelectorAll('#documentProjectPageEditor textarea').length") == 9
    assert browser.evaluate("document.querySelector('.document-project-save-status').textContent.includes('已保存到本地服务（原文件与逐页识图稿）')")
    assert not browser.evaluate("document.querySelector('.document-project-save-status').textContent.includes('等待保存')")
    assert browser.evaluate("document.querySelector('.document-project-manual-reasons').textContent.includes('第 7 页')")
    assert browser.evaluate("document.querySelector('.document-project-manual-reasons').textContent.includes('原版答案分区不唯一')")
    assert not browser.evaluate("window.__projectBrowserActions.some(row=>row.method==='PUT')")
    assert browser.evaluate("JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__freshZeroId].revision") == 0
    assert browser.evaluate("JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__freshZeroId].draft") is None
    browser.evaluate('window.MathBankDocumentImportSession.refreshProjects()')
    assert browser.evaluate("document.querySelector('#documentProjectsList [data-project-id=\"'+window.__freshZeroId+'\"]').textContent.includes('题卡尚未整理')")
    assert not browser.evaluate("document.querySelector('#documentProjectsList [data-project-id=\"'+window.__freshZeroId+'\"] p:nth-child(2)').textContent.includes('可继续校对')")
    browser.command('scrollintoview', '#documentImportRecoveryPanel');browser.settle()
    browser.command('screenshot', str(EVIDENCE / f'fresh-zero-saved-source-{width}.png'))

    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(window.__freshZeroId)')
    assert browser.evaluate("document.getElementById('documentImportRecoveryPanel').textContent.includes('原文件和9页识图稿已保存；题卡尚未整理')")
    assert not browser.evaluate("window.__projectBrowserActions.some(row=>row.method==='PUT')")
    browser.evaluate("document.getElementById('documentProjectPageEditor').open=true;true")
    browser.command('fill', '#documentProjectPageEditor textarea[data-page-number="7"]', '第7页人工修正，原公式 $x+1$ 保留。')
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__freshZeroId].draft?.page_edits?.[6]?.markdown==='第7页人工修正，原公式 $x+1$ 保留。'")
    assert browser.evaluate("JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__freshZeroId].revision") == 1
    browser.evaluate("(()=>{const store=JSON.parse(localStorage.getItem('mathbank_project_browser_server'));store[window.__freshZeroId].revision++;localStorage.setItem('mathbank_project_browser_server',JSON.stringify(store));return true})()")
    browser.command('fill', '#documentProjectPageEditor textarea[data-page-number="7"]', '冲突时仍保留的本机第7页人工稿。')
    browser.command('wait', '--fn', "document.querySelector('.document-project-save-status')?.textContent.includes('保存冲突')")
    assert not browser.evaluate("document.querySelector('.document-project-save-status').textContent.includes('已保存到本地服务')")
    assert browser.evaluate("document.querySelector('#documentProjectPageEditor textarea[data-page-number=\"7\"]').value") == '冲突时仍保留的本机第7页人工稿。'
    actions = browser.evaluate('window.__projectBrowserActions')
    assert not any('/api/ai/' in row['path'] or row['path'].endswith(('/analyze', '/resume')) for row in actions)
    (EVIDENCE / f'fresh-zero-evidence-{width}.json').write_text(json.dumps({
        'viewport': [width, height], 'fresh_upload_not_existing_draft': True,
        'initial_revision': 0, 'initial_draft': None, 'initial_stages': 14, 'initial_cards': 0,
        'saved_ocr_pages': 9, 'progress_and_reopen_puts': 0, 'manual_page_edit_saved': True,
        'conflicting_edit_preserved': True, 'model_calls': 0, 'actions': actions,
    }, ensure_ascii=False, indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')


@pytest.mark.parametrize('width,height', [(1600, 1100), (375, 812)])
def test_rule_rebuilt_machine_source_replaces_swallowed_card_and_preserves_teacher(browser, width, height):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    browser.command('set', 'viewport', str(width), str(height))
    browser.command('network', 'route', '**/api/ai/**', '--abort')
    browser.evaluate('window.__rebuiltWidth=' + str(width))
    browser.evaluate(r"""(()=>{
      window.MathBankDocumentImportSession.clear();selectWorkspace('import','导入中心');
      const id='rule-rebuilt-browser-'+window.__rebuiltWidth,now=Date.now()/1000;
      const row=n=>({project_card_id:'rebuild-card-'+n,source_number:n,source:'原PDF',
        content:'原版第'+n+'题 $x+1=2$。',answer_markdown:'',question_type:'detailed_answer',difficulty:'normal',
        category_compulsory:'必修二',category_chapter:'9. 统计',category_knowledge:'',
        image_paths:[],referenced_images:[],classification_review:{required:false}});
      const old=Array.from({length:13},(_,i)=>row(i+1));
      old[12].content='旧机器第13题吞入其余题目与整卷答案。'+'吞卷原文。'.repeat(3000);
      const rebuilt=Array.from({length:19},(_,i)=>({...row(i+1),answer_markdown:'原版第'+(i+1)+'题答案 $1$。'}));
      rebuilt[12].source_review={required:true,blocking:false,source_basis:'first_pass_transcription',
        reasons:['source_math_delimiter_requires_review','PDF 第2页：配图位置需要核对','PDF 第6页：图片裁剪范围需要核对']};
      const report=count=>({pdf_local_draft:{status:'ready',persisted:true,question_count:count,source_preserved:true,
        source_basis:'first_pass_transcription',unresolved_ranges:[],
        page_issues:count===19?[{page_number:2,source_number:13,blocked:false,reason_codes:['source_math_delimiter_requires_review']}]:[],
        structure_census:{declared_count:19,final_numbers:Array.from({length:count},(_,i)=>i+1),independent_boundaries_proved:count===19}},
        pdf_draft_metadata:{status:'completed',calls:1,new_calls:1},
        pdf_source_verification:{status:'no_candidates',calls:0,checked:0,pending:1,
          skipped_reasons:count===19?[{question_index:12,reason:'存在配图引用、位置或裁剪风险'}]:[]}});
      const common={revision:0,status:'completed',question_count:13,can_resume:false,task_id:null,
        has_draft:false,has_baseline:true,last_used_at:now,expires_at:now+7*86400,total_bytes:2048,source_pages:[]};
      // The API exposes the immutable machine baseline as its effective draft
      // when no teacher draft exists; revision remains zero.
      const baseline={questions:old,baselineQuestions:old,paperTitle:'结构规则恢复'};
      const store={};
      store[id]={...common,project_id:id,title:'无人工稿的机器基线恢复',draft:structuredClone(baseline),
        diagnostics:report(19),latest_result:rebuilt,latest_result_id:'rebuilt-machine-v3'};
      store[id+'-manual']={...common,project_id:id+'-manual',title:'已有人工修改的机器基线恢复',
        draft:structuredClone(baseline),diagnostics:report(13)};
      localStorage.setItem('mathbank_project_browser_server',JSON.stringify(store));
      window.__rebuiltId=id;window.__rebuiltCards=rebuilt;window.__rebuiltReport=report(19);return true;
    })()""")
    browser.evaluate(MOCK)
    assert browser.evaluate('window.MathBankDocumentImportSession.openProject(window.__rebuiltId)')
    assert browser.evaluate('parsedQuestionsData.length') == 19
    assert browser.evaluate('parsedQuestionsData[12].content') == '原版第13题 $x+1=2$。'
    assert browser.evaluate('parsedQuestionsData[12].answer_markdown') == '原版第13题答案 $1$。'
    assert not browser.evaluate('Boolean(parsedQuestionsData[12].project_incoming_candidate)')
    assert browser.evaluate("document.querySelector('#parsed-card-12 .card-source-review-reasons').textContent.includes('请对照原页检查该题公式')")
    assert browser.evaluate("document.querySelector('#parsed-card-12 .card-source-review-reasons').textContent.includes('存在配图引用、位置或裁剪风险')")
    assert browser.evaluate("document.querySelector('#parsed-card-12 .card-source-review-reasons').textContent.includes('PDF 第2页：配图位置需要核对')")
    assert browser.evaluate("document.querySelector('#parsed-card-12 .card-source-review-reasons').textContent.includes('PDF 第6页：图片裁剪范围需要核对')")
    assert not browser.evaluate("document.getElementById('documentImportRecoveryPanel').textContent.includes('题卡尚未整理')")
    browser.command('wait', '--fn', "JSON.parse(localStorage.getItem('mathbank_project_browser_server'))[window.__rebuiltId].draft.questions.length===19")

    assert browser.evaluate("window.MathBankDocumentImportSession.openProject(window.__rebuiltId+'-manual')")
    assert browser.evaluate('parsedQuestionsData.length') == 13
    browser.command('fill', '#parsed-card-0 .card-content-textarea', '教师手工校对第1题，必须保留。')
    browser.evaluate(r"""(()=>{
      const session=window.MathBankDocumentImportSession;
      session.update({phase:'completed',diagnostics:window.__rebuiltReport});
      const kept=session.mergeCompletion(structuredClone(window.__rebuiltCards));
      replaceParsedQuestions(kept,{saveDraft:false});renderParsedQuestionsList(kept,{diagnostics:window.__rebuiltReport});
      session.completed({status:'completed',project_id:window.__rebuiltId+'-manual',
        diagnostics:window.__rebuiltReport,data:window.__rebuiltCards},kept);return true;
    })()""")
    assert browser.evaluate('parsedQuestionsData.length') == 19
    assert browser.evaluate('parsedQuestionsData[12].content') == '原版第13题 $x+1=2$。'
    assert browser.evaluate('parsedQuestionsData[12].answer_markdown') == '原版第13题答案 $1$。'
    assert browser.evaluate('parsedQuestionsData[0].content') == '教师手工校对第1题，必须保留。'
    assert browser.evaluate('parsedQuestionsData[0].project_incoming_candidate.content') == '原版第1题 $x+1=2$。'
    assert browser.evaluate('parsedQuestionsData[0].project_incoming_candidate.answer_markdown') == '原版第1题答案 $1$。'
    assert browser.evaluate("document.querySelector('#parsed-card-0 .document-project-candidate button').textContent") == '采用本次候选'
    browser.evaluate("document.querySelector('#parsed-card-0 .document-project-candidate').open=true;true")
    assert browser.evaluate("document.querySelector('#parsed-card-0 .document-project-candidate pre').textContent.includes('原版第1题答案')")
    _click(browser, '#parsed-card-0 .document-project-candidate button')
    assert browser.evaluate('parsedQuestionsData[0].content') == '原版第1题 $x+1=2$。'
    assert browser.evaluate('parsedQuestionsData[0].answer_markdown') == '原版第1题答案 $1$。'
    assert not browser.evaluate(r"window.__projectBrowserActions.some(row=>/\/(analyze|resume)$/.test(row.path))")
    (EVIDENCE / f'rule-rebuilt-evidence-{width}.json').write_text(json.dumps({
        'viewport': [width, height], 'previous_machine_cards': 13, 'initial_teacher_draft': False,
        'initial_revision': 0, 'rebuilt_cards': 19, 'unedited_swallowed_body_replaced': True,
        'original_answers_recovered': True, 'teacher_card_preserved_with_full_candidate': True,
        'explicit_full_candidate_adoption': True, 'model_calls': 0,
    }, ensure_ascii=False, indent=2))
    browser.evaluate('window.MathBankDocumentImportSession.clear();true')
