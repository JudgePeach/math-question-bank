"""Word imports keep source bodies local and bound paid fallback attempts."""
from copy import deepcopy
import json
import uuid

import pytest

import main
from mathbank import docx_helper
from mathbank.task_manager import TaskCancelled


SOURCE = "1. 已知 $x=1$，求 $x+1$ 的值。"


class Response:
    status_code = 200

    def __init__(self, parsed):
        self.parsed = parsed

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.parsed, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def setup_word(monkeypatch, tmp_path):
    native = docx_helper._new_diagnostics()
    monkeypatch.setattr(main, "extract_docx_markdown", lambda *_args, **_kwargs: {
        "success": True, "markdown": SOURCE, "image_paths": [], "image_count": 0,
        "diagnostics": deepcopy(native),
    })
    monkeypatch.setattr(main, "TMP_UPLOAD_DIR", tmp_path)
    monkeypatch.setenv("PREFER_PARSE_MODEL", "DEEPSEEK/deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-no-real-network")
    monkeypatch.setattr("requests.sessions.Session.request", lambda *_a, **_k: pytest.fail("Network forbidden"))
    curriculum = main.get_current_curriculum()
    category, chapters = next(iter(curriculum.items()))
    chapter = next(iter(chapters))
    fields = {"question_type": "detailed_answer", "category_compulsory": category,
              "category_chapter": chapter, "difficulty": "challenge"}
    return fields


def run_task(on_created=lambda _task_id: None):
    task_id = "metadata-flow-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    on_created(task_id)
    try:
        main.run_docx_parsing_task(task_id, b"isolated-test", "试题.docx",
                                   docx_verify_suspicions=False)
        return main.DOCUMENT_TASKS.snapshot(task_id)
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_verified_source_has_one_metadata_post_and_no_source_echo(monkeypatch, tmp_path):
    fields = setup_word(monkeypatch, tmp_path)
    calls = []

    def post(_provider, payload, **kwargs):
        calls.append((deepcopy(payload), kwargs))
        assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
        items = json.loads(payload["messages"][1]["content"])["items"]
        return Response({"items": [{"id": item["id"], **fields} for item in items]})

    monkeypatch.setattr(main, "post_chat_completion", post)
    task = run_task()
    assert task["status"] == "completed"
    assert len(calls) == 1
    assert task["diagnostics"]["word_source_metadata"]["status"] == "used"
    assert len(task["data"]) == 1
    assert "$x=1$" in task["data"][0]["content"]
    assert "$x+1$" in task["data"][0]["content"]
    assert task["data"][0]["answer_markdown"] == ""


def test_unknown_metadata_id_falls_back_once_without_transport_retries(monkeypatch, tmp_path):
    fields = setup_word(monkeypatch, tmp_path)
    calls = []

    def post(_provider, payload, **kwargs):
        calls.append((deepcopy(payload), kwargs))
        assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
        if len(calls) == 1:
            return Response({"items": [{"id": "not-a-source-id", **fields}]})
        return Response({"questions": [{"content": "已知 $x=1$，求 $x+1$ 的值。",
                                        "answer_markdown": "", "referenced_images": [], "source": "", **fields}]})

    monkeypatch.setattr(main, "post_chat_completion", post)
    task = run_task()
    assert task["status"] == "completed"
    assert len(calls) == 2
    assert task["diagnostics"]["word_source_metadata"]["status"] == "fallback"
    assert "$x+1$" in task["data"][0]["content"]


def test_plan_preparation_failure_preserves_the_original_split_path(monkeypatch, tmp_path):
    fields = setup_word(monkeypatch, tmp_path)

    def broken_plan(*_args, **_kwargs):
        raise ValueError("local preparation failure")

    monkeypatch.setattr(main, "prepare_word_source_metadata", broken_plan)
    captured = []
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: captured.append(True) or [
        {"content": "已知 $x=1$，求 $x+1$ 的值。", "answer_markdown": "",
         "referenced_images": [], "source": "", **fields}])
    task = run_task()
    assert task["status"] == "completed" and len(captured) == 1
    assert task["diagnostics"]["word_source_metadata"]["calls"] == 0


def test_cancelled_metadata_request_never_starts_full_source_fallback(monkeypatch, tmp_path):
    setup_word(monkeypatch, tmp_path)
    active = []

    def cancel(*_args, **_kwargs):
        main.DOCUMENT_TASKS.cancel(active[0])
        raise TaskCancelled("isolated cancellation")

    monkeypatch.setattr(main, "request_word_source_metadata", cancel)
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: pytest.fail("Cancelled task retried"))
    task = run_task(active.append)
    assert task["status"] == "cancelled"


def test_network_failure_after_cancellation_does_not_send_another_post(monkeypatch, tmp_path):
    import requests
    setup_word(monkeypatch, tmp_path)
    active, calls = [], []

    def failed_post(*_args, **_kwargs):
        calls.append(True)
        main.DOCUMENT_TASKS.cancel(active[0])
        raise requests.ConnectionError("request failed after the cancellation")

    monkeypatch.setattr(main, "post_chat_completion", failed_post)
    task = run_task(active.append)
    assert task["status"] == "cancelled"
    assert len(calls) == 1
    assert task["diagnostics"]["word_source_metadata"]["status"] == "cancelled"


def test_source_body_postprocessing_keeps_literal_escape_bytes(monkeypatch, tmp_path):
    setup_word(monkeypatch, tmp_path)
    content = r"阅读代码 `print(\n)`，比较 $\underline{AB}$。"
    answer = r"保留原代码 `value=\n`。"
    result = main.post_process_pdf_parsed_questions([
        {"content": content, "answer_markdown": answer, "referenced_images": []}],
        "源件", source_body_preserved=True)
    assert result[0]["content"] == content
    assert result[0]["answer_markdown"] == answer


@pytest.mark.parametrize("literal", ["`[EXTRACTED_ORIGINAL]`", r"\texttt{[EXTRACTED_ORIGINAL]}"])
def test_final_answer_prefix_does_not_erase_literal_original_markers(literal):
    from mathbank.paper_parse import finalize_source_answers
    original = "原始代码 " + literal + "。"
    questions = [{"content": "解释代码。", "answer_markdown": "[EXTRACTED_ORIGINAL]" + original}]
    finalize_source_answers(questions, original)
    assert questions[0]["answer_markdown"] == original
    assert not questions[0].get("source_review", {}).get("required")


def test_literal_origin_marker_does_not_certify_an_unmarked_model_answer():
    from mathbank.paper_parse import finalize_source_answers
    original = "阅读 `[EXTRACTED_ORIGINAL]` 标记。"
    questions = [{"content": "解释标记。", "answer_markdown": original}]
    finalize_source_answers(questions, original)
    assert questions[0]["answer_markdown"] == original
    assert questions[0]["source_review"]["required"] is True


def test_literal_source_image_path_is_not_promoted_to_a_question_asset(monkeypatch, tmp_path):
    setup_word(monkeypatch, tmp_path)
    literal = "示例 `![](/static/uploads/tmp/ghost.png)`。"
    actual = "![](/static/uploads/tmp/real.png)"
    questions = [{"content": literal + "\n\n" + actual, "answer_markdown": "",
                  "referenced_images": ["/static/uploads/tmp/real.png"]}]
    result = main.post_process_pdf_parsed_questions(questions, "源件", source_body_preserved=True)
    assert result[0]["content"] == literal + "\n\n" + actual
    assert result[0]["image_paths"] == ["/static/uploads/tmp/real.png"]
