"""The actual Word task keeps local fields while consuming one mixed response."""
from io import BytesIO
import json
import uuid
import zipfile
import pytest
import main
from mathbank.source_metadata import prepare_word_source_metadata
from mathbank.question_assets import embedded_question_assets
from test_word_hybrid_safety import paragraph, bad_question, W, M, FIELDS, CURRICULUM, Response


def blob():
    body = paragraph("一、解答题") + paragraph(r"1. 已知$x=1$，阅读代码`x___`与$\underline{AB}$，填 ____。")
    body += paragraph("这是本题需要完整保留的文字说明。" * 20)
    body += paragraph(r"【解析】原代码`[EXTRACTED_ORIGINAL]`与$x+1=2$。")
    body += bad_question(2) + paragraph("【解析】保留第二题完整原答案，不能编造。")
    body += paragraph("3. 已知$x=3$，求$x+x$。") + paragraph("【解析】$6$。")
    data=BytesIO()
    with zipfile.ZipFile(data,"w") as archive:
        archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr("word/document.xml",f'<w:document xmlns:w="{W}" xmlns:m="{M}"><w:body>{body}</w:body></w:document>')
    return data.getvalue()


@pytest.mark.parametrize("partial", [False,True])
def test_actual_word_task_preserves_safe_fields_and_exposes_failed_scopes(monkeypatch,tmp_path,partial):
    monkeypatch.setenv("PREFER_PARSE_MODEL","DEEPSEEK/deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY","isolated-no-network")
    monkeypatch.setattr(main,"TMP_UPLOAD_DIR",tmp_path)
    monkeypatch.setattr(main,"get_current_curriculum",lambda:CURRICULUM)
    monkeypatch.setattr("requests.sessions.Session.request",lambda *_a,**_k:pytest.fail("Network forbidden"))
    original=main.extract_docx_markdown
    captured={};calls=[]
    def extract(*args,**kwargs):
        result=original(*args,**kwargs);captured.update(result);return result
    monkeypatch.setattr(main,"extract_docx_markdown",extract)
    def post(_provider,payload,**kwargs):
        calls.append(payload)
        envelope=json.loads(payload["messages"][1]["content"])
        inspection=prepare_word_source_metadata(captured["markdown"],captured["diagnostics"],inspect_ineligible=True)
        by_id={q["id"]:q for q in inspection["questions"]}
        meta=[{"id":row["id"],**FIELDS} for row in envelope["metadata_items"]]
        splits=[]
        if not partial:
            for group in envelope["split_groups"]:
                questions=[]
                for qid in group["source_ids"]:
                    q=by_id[qid]
                    questions.append({"source_id":qid,**FIELDS,"content":q["content"],
                        "answer_markdown":"[EXTRACTED_ORIGINAL]"+q["answer_markdown"],
                        "referenced_images":embedded_question_assets(q["content"],q["answer_markdown"])})
                splits.append({"group_id":group["group_id"],"questions":questions})
        return Response({"metadata":{"items":meta},"splits":splits})
    monkeypatch.setattr(main,"post_chat_completion",post)
    task="hybrid-flow-"+uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task,document_type="docx",temp_assets=[])
    try:
        main.run_docx_parsing_task(task,blob(),"isolated.docx",docx_verify_suspicions=False)
        result=main.DOCUMENT_TASKS.snapshot(task)
        assert result["status"]=="completed",result.get("error")
        assert result["diagnostics"]["word_hybrid"]["partial"] is partial
        assert len(calls)==(2 if partial else 1)
        assert len(result["data"])==(2 if partial else 3)
        first=result["data"][0]
        assert "`x___`" in first["content"] and r"$\underline{AB}$" in first["content"]
        assert "`[EXTRACTED_ORIGINAL]`" in first["answer_markdown"]
        if partial:
            assert result["diagnostics"]["unmatched_source"]
            assert "第二题完整原答案" in result["docx_source_cache"]["source_markdown"]
    finally:
        main.DOCUMENT_TASKS.remove(task)


def test_per_question_postprocessing_cannot_turn_a_model_flag_into_source_preservation(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"TMP_UPLOAD_DIR",tmp_path)
    values=[{"content":r"保留 `x___` 和 $\underline{AB}$。","answer_markdown":"","referenced_images":[]},
            {"content":r"旧路输入\n续行 ____。","answer_markdown":"","source_body_preserved":True,"referenced_images":[]}]
    result=main.post_process_pdf_parsed_questions(values,"isolated",preserved_indices={0})
    assert result[0]["content"]==r"保留 `x___` 和 $\underline{AB}$。"
    assert "\n续行" in result[1]["content"] and r"\fillin" in result[1]["content"]
