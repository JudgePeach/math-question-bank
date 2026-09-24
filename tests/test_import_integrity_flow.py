"""Word/TeX import boundaries with fake model responses and the isolated test DB."""

import json
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def forbid_live_model_requests(monkeypatch):
    def reject_request(*_args, **_kwargs):
        raise AssertionError("Import integrity tests must not contact a live AI provider.")

    monkeypatch.setattr("requests.sessions.Session.request", reject_request)


def _question(content, answer="", **extra):
    return {
        "content": content,
        "answer_markdown": answer,
        "question_type": "detailed_answer",
        "referenced_images": [],
        **extra,
    }


@pytest.fixture
def import_flow(client, monkeypatch):
    import main

    provider = SimpleNamespace(
        api_key="unused-test-key",
        api_base="https://example.invalid/v1",
        model_name="deepseek-flash",
        provider_label="test provider",
        credential_label="TEST_API_KEY",
        provider_code="deepseek",
        reasoning_effort=None,
    )
    monkeypatch.setattr(main, "resolve_text_provider", lambda _model: provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {})

    def run(channel, source, questions, *, finish_reason="stop", generate_answers=False):
        sent = []
        raw_response = json.dumps({"questions": questions}, ensure_ascii=False)

        def fake_completion(_provider, payload, **_kwargs):
            sent.append(payload)
            choice = {"message": {"content": raw_response}}
            if finish_reason is not None:
                choice["finish_reason"] = finish_reason
            return SimpleNamespace(json=lambda: {
                "choices": [choice],
                "usage": {"prompt_tokens": 300, "completion_tokens": 240, "total_tokens": 540},
            })

        monkeypatch.setattr(main, "post_chat_completion", fake_completion)
        if channel == "word":
            monkeypatch.setattr(main, "extract_docx_markdown", lambda *_args, **_kwargs: {
                "success": True,
                "markdown": source,
                "image_paths": [],
                "image_count": 0,
                "diagnostics": {"omml_converted": 1, "review_required": 0},
            })
            task_id = "integrity-flow-" + uuid.uuid4().hex
            main.DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
            try:
                main.run_docx_parsing_task(task_id, b"mock-docx", "保真核对.docx", generate_answers)
                state = main.DOCUMENT_TASKS.snapshot(task_id)
            finally:
                main.DOCUMENT_TASKS.remove(task_id)
            result = SimpleNamespace(
                success=state["status"] == "completed",
                questions=state.get("data", []),
                diagnostics=state.get("diagnostics", {}),
                error=state.get("error", ""),
                state=state,
            )
        else:
            response = client.post(
                "/api/ai/parse-paper",
                data={
                    "latex_content": source,
                    "paper_title": "保真核对",
                    "image_mapping_json": "{}",
                    "generate_answers": str(generate_answers).lower(),
                },
                headers={"X-Local-Token": main.LOCAL_TOKEN},
            )
            state = response.json()
            result = SimpleNamespace(
                success=response.status_code == 200 and state.get("status") == "success",
                questions=state.get("questions", []),
                diagnostics=state.get("tex_diagnostics", {}),
                error=state.get("message", ""),
                state=state,
            )
        assert len(sent) == 1, "Import must not silently issue another model request."
        result.payload = sent[0]
        result.raw_response = raw_response
        return result

    return run


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_import_accepts_unchanged_formulas_when_model_omits_all_ids(import_flow, channel):
    content = r"已知 $x^2+1$，求最小值。"
    result = import_flow(channel, "1. " + content, [_question(content)])

    assert result.success, result.error
    assert '<mathbank-math id="' in result.payload["messages"][1]["content"]
    assert result.questions[0]["content"] == content
    assert not result.questions[0].get("source_review", {}).get("required")
    assert result.diagnostics["math_locks_by_content"] == 1
    assert result.diagnostics["math_locks_restored"] == 1
    assert result.diagnostics["finish_reason"] == "stop"
    assert result.diagnostics["output_characters"] == len(result.raw_response)
    assert result.diagnostics["usage"]["completion_tokens"] == 240


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_import_keeps_changed_formula_and_source_for_explicit_review(import_flow, channel):
    original = r"已知 $x^2+1$，求最小值。"
    changed = r"已知 $x^2-1$，求最小值。"
    result = import_flow(channel, "1. " + original, [_question(changed)])

    assert result.success, result.error
    question = result.questions[0]
    assert question["content"] == changed
    assert question["source_review"]["required"] is True
    assert question["source_review"]["reasons"]
    assert original in question["source_review"]["source_excerpt"]
    assert result.diagnostics["source_review_count"] >= 1
    assert question["source_review"]["blocking"] is False
    assert question["source_review"]["disposition"] == "advisory"
    assert result.diagnostics["source_review_blocking_count"] == 0


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_import_rejects_length_finish_reason_even_with_complete_json(import_flow, channel):
    content = r"计算 $1+1$。"
    result = import_flow(channel, "1. " + content, [_question(content)], finish_reason="length")

    assert not result.success
    assert "截断" in result.error
    assert not result.questions
    if channel == "word":
        assert result.diagnostics["finish_reason"] == "length"
        assert result.diagnostics["output_characters"] == len(result.raw_response)


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_import_rejects_empty_question_array(import_flow, channel):
    result = import_flow(channel, r"1. 计算 $1+1$。", [])

    assert not result.success
    assert "有效" in result.error
    assert not result.questions


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_original_answer_without_model_marker_survives_as_review_candidate(import_flow, channel):
    source = "1. 计算 $1+1$。\n\n参考答案：\n1. $2$。"
    result = import_flow(channel, source, [_question(r"计算 $1+1$。", r"$2$。")])

    assert result.success, result.error
    question = result.questions[0]
    assert question["answer_markdown"] == r"$2$。"
    assert question["source_review"]["required"] is True
    assert "$2$" in question["source_review"]["source_excerpt"]


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_unsourced_model_answer_is_never_silently_treated_as_original(import_flow, channel):
    result = import_flow(channel, r"1. 计算 $1+1$。", [_question(r"计算 $1+1$。", r"$2$")])

    assert result.success, result.error
    question = result.questions[0]
    assert question["answer_markdown"] == "$2$"
    assert question["source_review"]["required"] is True
    assert question["source_review"]["reasons"]


@pytest.mark.parametrize("channel", ["word", "tex"])
def test_model_cannot_supply_trusted_source_review_metadata(import_flow, channel):
    result = import_flow(
        channel,
        r"1. 已知 $x^2+1$，求最小值。",
        [_question(
            r"已知 $x^2-1$，求最小值。",
            source_review={"required": False, "reasons": ["模型声称已核对"], "source_excerpt": "伪造来源",
                           "verified_by": "vision", "verification": {"decision": "equivalent", "snapshot_hash": "fake"}},
        )],
    )

    assert result.success, result.error
    review = result.questions[0]["source_review"]
    assert review["required"] is True
    assert "模型声称已核对" not in review["reasons"]
    assert "伪造来源" not in review["source_excerpt"]
    assert "$x^2+1$" in review["source_excerpt"]
    assert "verified_by" not in review and "verification" not in review


def test_word_defers_requested_answer_generation_until_after_source_review(import_flow):
    result = import_flow(
        "word",
        r"1. 计算 $1+1$。",
        [_question(r"计算 $1+1$。")],
        generate_answers=True,
    )

    assert result.success, result.error
    assert result.state["generate_answers"] is True
    assert result.questions[0]["answer_markdown"] == ""
    assert "严禁主动生成" in result.payload["messages"][0]["content"]


def test_import_remains_compatible_with_provider_omitting_finish_reason(import_flow):
    result = import_flow("word", r"1. 计算 $1+1$。", [_question(r"计算 $1+1$。")], finish_reason=None)

    assert result.success, result.error
    assert result.questions[0]["content"] == r"计算 $1+1$。"
