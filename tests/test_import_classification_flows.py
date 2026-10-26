"""Classification repair must preserve source data without another paid call."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest


TREE = {"必修二": {"9. 统计": ["9.2 用样本估计总体"]}}


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    def reject(*_args, **_kwargs):
        raise AssertionError("Classification regression tests must never contact a model.")
    monkeypatch.setattr("requests.sessions.Session.request", reject)


def provider():
    return SimpleNamespace(api_key="unused", api_base="https://example.invalid/v1",
        chat_completions_url="https://example.invalid/v1/chat/completions",
        model_name="test-model", provider_code="test", provider_label="test",
        credential_label="TEST_KEY", reasoning_effort=None)


def completion(questions):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"questions": questions}, ensure_ascii=False)}}]}


def test_split_uses_one_curriculum_snapshot_and_keeps_actual_chapter_title(monkeypatch):
    import main
    reads = []; calls = []
    current = deepcopy(TREE)
    def catalogue():
        reads.append(True)
        return current
    monkeypatch.setattr(main, "get_current_curriculum", catalogue)
    monkeypatch.setattr(main, "resolve_text_provider", lambda _model: provider())
    question = {"content": "这组数据的极差为多少？", "answer_markdown": "D",
        "category_compulsory": "必修二", "category_chapter": "统计",
        "category_knowledge": "9.2 用样本估计总体", "referenced_images": [],
        "classification_review": {"required": False, "original": {"spoof": True}}}
    def post(_provider, payload, **_kwargs):
        calls.append(payload)
        current.clear()
        current["另一册"] = {"另一章": []}
        return SimpleNamespace(json=lambda: completion([question]))
    monkeypatch.setattr(main, "post_chat_completion", post)
    diagnostics = {}
    result = main.parse_paper_text_internal("1. 这组数据的极差为多少？", False,
        preserve_source_answers=True, diagnostics=diagnostics)
    assert len(calls) == len(reads) == 1
    assert "9. 统计" in calls[0]["messages"][0]["content"]
    assert result[0]["category_chapter"] == "9. 统计"
    assert result[0]["answer_markdown"] == "D"
    assert result[0]["classification_review"]["original"]["category_chapter"] == "统计"
    assert diagnostics["import_classification"]["normalized"] == 1


@pytest.mark.parametrize("chapter", ["统计", "不存在的章节", None, ["统计"]])
def test_source_metadata_keeps_original_body_with_one_request(monkeypatch, chapter):
    import mathbank.source_metadata_request as request_module
    from mathbank.docx_helper import _new_diagnostics
    from mathbank.source_metadata import prepare_word_source_metadata
    source = "1. 已知样本数据为$1,2,3$，求极差。"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"]
    row = {"id": plan["questions"][0]["id"], "question_type": "detailed_answer",
        "category_compulsory": "必修二", "category_chapter": chapter,
        "category_knowledge": "9.2 用样本估计总体", "difficulty": "normal"}
    calls = []
    def post(_provider, _payload, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"items": [row]}, ensure_ascii=False)}}]})
    monkeypatch.setattr(request_module, "apply_model_thinking_policy", lambda payload, **_: payload)
    diagnostics = {}
    questions = request_module.request_word_source_metadata(plan, TREE, provider=provider(),
        post=post, diagnostics=diagnostics, normalize_fillin=lambda value: value)
    assert len(calls) == 1
    assert diagnostics["word_source_metadata"]["status"] == "used"
    assert questions[0]["content"] == plan["questions"][0]["content"]
    assert questions[0]["classification_review"]["original"]["category_chapter"] == chapter
    if chapter == "统计":
        assert questions[0]["category_chapter"] == "9. 统计"
        assert questions[0]["classification_review"]["required"] is False
    else:
        assert questions[0]["category_compulsory"] == "必修二"
        assert questions[0]["category_chapter"] == questions[0]["category_knowledge"] == ""
        assert questions[0]["classification_review"]["required"] is True


@pytest.mark.parametrize("knowledge", [None, [], {}, 0, False])
def test_certified_outside_grade_invalid_optional_refinement_retains_body_without_guessing(knowledge):
    from mathbank.docx_helper import _new_diagnostics
    from mathbank.source_metadata import (
        apply_source_metadata_response, prepare_word_source_metadata,
    )
    source = "九年级数学试卷\n1. 已知一次函数$y=2x+1$，求$x=3$时的函数值。"
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"], plan["fallback_reasons"]
    row = {"id": plan["questions"][0]["id"], "question_type": "detailed_answer",
        "category_compulsory": "九年级", "category_chapter": "一次函数",
        "category_knowledge": "", "difficulty": "normal"}
    valid = apply_source_metadata_response({"items": [row]}, plan, TREE)
    assert valid[0]["category_compulsory"] == "九年级"
    assert valid[0]["content"] == plan["questions"][0]["content"]
    result = apply_source_metadata_response({"items": [{**row, "category_knowledge": knowledge}]}, plan, TREE)
    assert result[0]["content"] == plan["questions"][0]["content"]
    assert result[0]["category_compulsory"] == result[0]["category_chapter"] == result[0]["category_knowledge"] == ""
    review = result[0]["classification_review"]
    assert review["required"] is True
    assert review["original"]["category_compulsory"] == "九年级"
    assert review["original"]["category_knowledge"] == knowledge
