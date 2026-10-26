"""Source metadata quotes follow proven local formatting and frozen catalogues."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import (
    _map_normalized_source_evidence, apply_source_metadata_response,
    build_source_metadata_messages, prepare_word_source_metadata,
)
from mathbank.source_metadata_request import request_word_source_metadata
from test_word_hybrid_safety import (
    CURRICULUM, FIELDS, PROVIDER, Response, call, native_plan, reply_for,
)


CATALOG = [{"code": "local:source-function", "name": "函数求值",
            "original_name": "函数求值", "aliases": ["求函数值"]}]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *_a, **_k: pytest.fail("No live model requests"))
    for module in ("source_metadata_request", "source_hybrid_request", "ai_knowledge_points"):
        monkeypatch.setattr(f"mathbank.{module}.load_ai_knowledge_catalog", lambda: deepcopy(CATALOG))


@pytest.fixture
def normalizer():
    # Exercise the real formatter without importing a service or live database.
    tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "normalize_fillin_macro")
    namespace = {"re": re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace["normalize_fillin_macro"]


def point(evidence, source="content", **changes):
    return {"code": CATALOG[0]["code"], "role": "main", "evidence_source": source,
            "evidence": evidence, "reason": "题目直接要求求值", **changes}


def prepared(source="1. 已知函数$f(x)=x^2$，则$f(2)$的值为____。"):
    plan = prepare_word_source_metadata(source, _new_diagnostics())
    assert plan["eligible"], plan["fallback_reasons"]
    return plan


def packet(plan, points):
    return {"items": [{"id": plan["questions"][0]["id"], **FIELDS,
                       "category_knowledge": "", "knowledge_points": points}]}


def test_literal_input_quote_tracks_actual_blank_normalization(normalizer):
    plan = prepared()
    quote = plan["questions"][0]["content"]
    response = packet(plan, [point(quote)])
    before = deepcopy((plan, response))
    sent = json.loads(build_source_metadata_messages(plan, CURRICULUM, CATALOG)[1]["content"])
    assert sent["items"][0]["content"] == quote
    result = apply_source_metadata_response(response, plan, CURRICULUM,
        normalize_fillin=normalizer, knowledge_catalog=CATALOG)[0]
    assert result["content"] == quote.replace("____", r"\fillin")
    assert result["knowledge_points"][0]["evidence"] == result["content"]
    assert result["knowledge_point_warnings"] == []
    assert (plan, response) == before


def test_target_quotes_track_only_the_same_proven_blank_normalization(normalizer):
    plan = prepared()
    quote = plan["questions"][0]["content"]
    response = packet(plan, [point(quote, target_evidence=quote)])
    original = deepcopy(response)
    result = apply_source_metadata_response(response, plan, CURRICULUM,
        normalize_fillin=normalizer, knowledge_catalog=CATALOG)[0]
    assert result["knowledge_points"][0]["target_evidence"] == result["content"]
    assert response == original and not result["knowledge_point_warnings"]


@pytest.mark.parametrize("quote", [r"值为\fillin。", "___", "原文不存在的函数"])
def test_normalization_cannot_license_invented_or_partial_source_quote(normalizer, quote):
    plan = prepared()
    result = apply_source_metadata_response(packet(plan, [point(quote)]), plan, CURRICULUM,
        normalize_fillin=normalizer, knowledge_catalog=CATALOG)[0]
    assert result["knowledge_points"] == []
    assert result["knowledge_point_warnings"]
    assert r"值为\fillin。" in result["content"]


def test_source_field_and_program_added_answer_marker_are_not_model_evidence(normalizer):
    plan = prepared("1. 求函数$f(x)=x^2$在$x=2$时的值。\n【答案】结果为$4$。")
    for suggestion in (point("[EXTRACTED_ORIGINAL]", "answer_markdown"),
                       point("结果为$4$。", "content")):
        result = apply_source_metadata_response(packet(plan, [suggestion]), plan, CURRICULUM,
            normalize_fillin=normalizer, knowledge_catalog=CATALOG)[0]
        assert result["knowledge_points"] == []
        assert result["knowledge_point_warnings"]
        assert result["answer_markdown"].startswith("[EXTRACTED_ORIGINAL]")


def test_literal_code_quote_and_original_answer_stay_exact(normalizer):
    code = chr(96) + "x____" + chr(96)
    plan = prepared("1. 阅读代码" + code + "，求函数$f(x)=x^2$在$x=2$时的值____。\n【答案】结果为$4$。")
    response = packet(plan, [point(code)])
    result = apply_source_metadata_response(response, plan, CURRICULUM,
        normalize_fillin=normalizer, knowledge_catalog=CATALOG)[0]
    assert result["knowledge_points"][0]["evidence"] == code
    assert result["answer_markdown"] == "[EXTRACTED_ORIGINAL]结果为$4$。"
    assert result["knowledge_point_warnings"] == []


def test_repeated_quotes_and_replay_with_changed_normalization_abstain():
    source = "甲____，乙____。"
    assert _map_normalized_source_evidence("____", source, "甲空，乙空。",
        lambda value: value.replace("____", "空")) == "____"
    assert _map_normalized_source_evidence("甲____", "甲____。", "甲空。",
        lambda _value: "甲空。") == "甲____"
    assert _map_normalized_source_evidence("___", "甲____。", "甲空。",
        lambda _value: pytest.fail("Overlapping occurrences must not be replayed")) == "___"


@pytest.mark.parametrize("missing", [False, True])
def test_empty_and_missing_point_field_keep_distinct_diagnostics(missing):
    plan = prepared()
    response = packet(plan, [])
    if missing:
        response["items"][0].pop("knowledge_points")
    result = apply_source_metadata_response(response, plan, CURRICULUM, knowledge_catalog=CATALOG)[0]
    assert result["knowledge_points"] == []
    assert result["knowledge_point_diagnostics"]["outcome"] == ("missing_field" if missing else "abstained")


def test_word_request_copies_catalogue_before_transport_mutates_it(monkeypatch, normalizer):
    plan, catalogue, loads, posts = prepared(), deepcopy(CATALOG), [], []
    def load():
        loads.append(True)
        return catalogue
    monkeypatch.setattr("mathbank.source_metadata_request.load_ai_knowledge_catalog", load)
    monkeypatch.setattr("mathbank.ai_knowledge_points.load_ai_knowledge_catalog",
                        lambda: pytest.fail("Response cannot reread catalogue"))
    def post(_provider, payload, **_options):
        posts.append(payload)
        assert CATALOG[0]["name"] in payload["messages"][0]["content"]
        catalogue.clear()
        return Response(packet(plan, [point(plan["questions"][0]["content"])]))
    result = request_word_source_metadata(plan, CURRICULUM, provider=PROVIDER, post=post,
        diagnostics={}, normalize_fillin=normalizer)
    assert loads == [True] and len(posts) == 1
    assert result[0]["knowledge_points"][0]["name"] == CATALOG[0]["name"]


@pytest.mark.parametrize("scoped_retry", [False, True])
def test_hybrid_routes_and_scoped_retry_keep_one_catalogue(monkeypatch, tmp_path, scoped_retry):
    plan, catalogue, loads, posts = native_plan(tmp_path), deepcopy(CATALOG), [], []
    def load():
        loads.append(True)
        return catalogue
    monkeypatch.setattr("mathbank.source_hybrid_request.load_ai_knowledge_catalog", load)
    monkeypatch.setattr("mathbank.ai_knowledge_points.load_ai_knowledge_catalog",
                        lambda: pytest.fail("Response cannot reread catalogue"))
    def post(_provider, payload, **_options):
        posts.append(payload)
        assert CATALOG[0]["name"] in payload["messages"][0]["content"]
        response = reply_for(plan, payload)
        envelope = json.loads(payload["messages"][1]["content"])
        metadata = {item["id"]: item for item in envelope["metadata_items"]}
        for item in response["metadata"]["items"]:
            item["knowledge_points"] = [point(metadata[item["id"]]["content"])]
        for group in response["splits"]:
            for question in group["questions"]:
                question["knowledge_points"] = [point("求函数值")]
        catalogue.clear()
        if scoped_retry and len(posts) == 1:
            response["splits"] = []
        return Response(response)
    result, report = call(plan, post)
    assert loads == [True] and len(posts) == (2 if scoped_retry else 1)
    assert not result.partial
    assert all(question["knowledge_points"][0]["name"] == CATALOG[0]["name"]
               for question in result.questions)
    assert report["word_hybrid"]["status"] == "used"
