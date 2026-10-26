"""Metadata-only mistakes preserve proven bodies and do not buy a rewrite."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.import_classification import normalize_import_classifications
from mathbank.source_metadata import (
    SourceMetadataIdentityError, SourceMetadataNoFallbackError,
    apply_source_metadata_response, prepare_word_source_metadata,
)
from mathbank.source_metadata_request import request_word_source_metadata


CURRICULUM = {"必修一": {"函数": ["函数概念"]}}
SOURCE = ("1. 已知函数$f(x)=x^2$，求$f(3)$。\n![](/static/uploads/native.png)\n"
          "【解析】$f(3)=9$。原解不能改写。\n2. 已知集合$A=\\{1,2,3\\}$，求其元素个数。")


def prepared(source=SOURCE):
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"], plan["fallback_reasons"]
    rows = [{"id": q["id"], "question_type": "detailed_answer", "difficulty": "normal",
             "category_compulsory": "必修一", "category_chapter": "函数", "category_knowledge": "函数概念"}
            for q in plan["questions"]]
    return plan, {"items": rows}


def adopt(reply, plan):
    return apply_source_metadata_response(reply, plan, CURRICULUM,
        normalize_fillin=lambda text: text, knowledge_catalog=[])


@pytest.mark.parametrize("field,value", [
    ("question_type", "unknown"), ("question_type", 1), ("question_type", None),
    ("question_type", ["single_choice"]), ("difficulty", "hard"),
    ("difficulty", 4), ("difficulty", None), ("difficulty", {}),
])
def test_only_bad_field_is_pending_and_every_frozen_body_answer_image_survives(field, value):
    plan, reply = prepared()
    before = deepcopy(plan["questions"])
    reply["items"][0][field] = value
    questions = adopt(reply, plan)
    assert len(questions) == 2 and plan["questions"] == before
    assert questions[0][field] == ("" if field == "question_type" else None)
    assert questions[0]["classification_review"]["required"] is True
    assert questions[0]["classification_review"]["original"][field] == value
    assert questions[1]["question_type"] == "detailed_answer" and questions[1]["difficulty"] == "normal"
    assert questions[0]["content"] == before[0]["content"]
    assert questions[0]["answer_markdown"] == "[EXTRACTED_ORIGINAL]" + before[0]["answer_markdown"]
    assert questions[0]["referenced_images"] == ["/static/uploads/native.png"]
    copied = deepcopy(questions)
    normalize_import_classifications(copied, CURRICULUM)
    assert copied[0]["classification_review"] == questions[0]["classification_review"]


@pytest.mark.parametrize("field", ["question_type", "difficulty"])
def test_missing_optional_classification_field_does_not_guess_a_default(field):
    plan, reply = prepared()
    del reply["items"][0][field]
    questions = adopt(reply, plan)
    assert questions[0][field] == ("" if field == "question_type" else None)
    assert questions[0]["classification_review"]["required"] is True


@pytest.mark.parametrize("field", ["notes", "reason", "rationale", "confidence", "warnings", "explanation"])
def test_known_harmless_extras_are_ignored_without_promoting_provenance(field):
    plan, reply = prepared()
    reply["items"][0][field] = {"untrusted": "ignore this diagnostic object"}
    reply["notes"] = "untrusted envelope prose"
    questions = adopt(reply, plan)
    assert questions[0]["content"] == plan["questions"][0]["content"]
    assert field not in questions[0] and questions[0]["classification_review"]["required"] is False
    assert "metadata_diagnostics_ignored" in questions[0]["classification_review"]["reason_codes"]
    assert not questions[0].get("source_review")


@pytest.mark.parametrize("field", ["content", "answer_markdown", "image_paths", "source_review", "source_id", "content_text"])
def test_identity_provenance_and_body_aliases_remain_hard_rejected(field):
    plan, reply = prepared()
    before = deepcopy(plan["questions"])
    reply["items"][0][field] = "cannot overwrite source"
    with pytest.raises(SourceMetadataIdentityError):
        adopt(reply, plan)
    assert plan["questions"] == before


def test_choice_structure_without_explicit_single_multi_type_never_guesses():
    plan, reply = prepared("1. 求$f(2)$，其中$f(x)=x+1$。\nA. $1$ B. $2$ C. $3$ D. $4$")
    assert plan["questions"][0]["has_choices"]
    questions = adopt(reply, plan)
    assert questions[0]["question_type"] == ""
    assert questions[0]["classification_review"]["required"] is True


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "missing", "body", "bad_type", "bad_difficulty", "extra"])
def test_one_metadata_post_never_rewrites_frozen_body_for_local_metadata_errors(monkeypatch, mutation):
    import mathbank.source_metadata_request as module
    plan, reply = prepared()
    if mutation == "unknown": reply["items"][0]["id"] = "unknown"
    elif mutation == "duplicate": reply["items"][1] = deepcopy(reply["items"][0])
    elif mutation == "missing": reply["items"].pop()
    elif mutation == "body": reply["items"][0]["answer_markdown"] = "injected"
    elif mutation == "bad_type": reply["items"][0]["question_type"] = 7
    elif mutation == "bad_difficulty": reply["items"][0]["difficulty"] = "hard"
    else: reply["items"][0]["reason"] = "not trusted"
    monkeypatch.setattr(module, "load_ai_knowledge_catalog", lambda: [])
    monkeypatch.setattr(module, "apply_model_thinking_policy", lambda payload, **_: payload)
    calls, diagnostics = [], {}
    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(reply, ensure_ascii=False)}}]})
    def run():
        return request_word_source_metadata(plan, CURRICULUM,
            provider=SimpleNamespace(api_key="mock", chat_completions_url="https://invalid.local", model_name="mock"),
            post=post, diagnostics=diagnostics, normalize_fillin=lambda text: text)
    if mutation in {"unknown", "duplicate", "missing", "body"}:
        with pytest.raises(SourceMetadataNoFallbackError): run()
        assert diagnostics["word_source_metadata"]["status"] == "metadata_rejected"
        assert diagnostics["word_source_metadata"]["no_fallback"] is True
    else:
        questions = run()
        assert len(questions) == 2 and diagnostics["word_source_metadata"]["status"] == "used"
    assert len(calls) == 1
    assert calls[0]["retry_connection"] is False and calls[0]["allow_redirects"] is False
