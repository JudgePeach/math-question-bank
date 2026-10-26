"""AI classifications use the installed catalogue and literal local evidence."""
from copy import deepcopy
import json
import re
from types import SimpleNamespace

import pytest

from mathbank.ai_knowledge_points import (
    annotate_ai_knowledge_points, build_ai_knowledge_prompt, load_ai_knowledge_catalog,
)
from mathbank.database import KnowledgePoint


CATALOG = [{"code": "local:teacher-function", "name": "函数概念", "original_name": "函数的概念",
            "aliases": ["函数定义"]},
           {"code": "1001", "name": "导数", "original_name": "导数", "aliases": []}]


def point(**overrides):
    return {"code": "local:teacher-function", "role": "main", "evidence_source": "content",
            "evidence": "函数$f(x)$", "reason": "直接考查定义", **overrides}


def test_installed_catalog_includes_custom_identity_and_updated_name(db_session):
    row = KnowledgePoint(code="local:teacher-function", name="老师修改后的名称",
                         original_name="来源原名", source="local")
    row.aliases = ["旧名称"]
    db_session.add(row)
    db_session.flush()
    catalog = load_ai_knowledge_catalog(db_session)
    assert {"code": row.code, "name": row.name, "original_name": "来源原名", "aliases": ["旧名称"]} in catalog
    prompt = build_ai_knowledge_prompt(catalog, profile="target_first")
    assert row.code in prompt and row.name in prompt
    # Search aliases remain in the catalogue but cannot broaden the AI task name.
    assert "来源原名" not in prompt and "旧名称" not in prompt
    assert "不能因解析偶然用导数" in prompt


def test_accepted_points_keep_literal_source_and_program_owned_state():
    question = {"content": "研究函数$f(x)$。", "answer_markdown": "原解使用导数。", "review_markdown": "注意定义域。",
                "knowledge_points": [point(name="模型伪造名称", status="suggested", origin="teacher"),
                    point(code="1001", role="auxiliary", evidence_source="answer_markdown", evidence="使用导数")]}
    before = deepcopy(question)
    annotate_ai_knowledge_points(question, CATALOG)
    assert question["content"] == before["content"]
    assert question["answer_markdown"] == before["answer_markdown"]
    main, auxiliary = question["knowledge_points"]
    assert main["name"] == "函数概念" and main["status"] == "confirmed"
    assert main["origin"] == "ai_classification"
    assert auxiliary["role"] == "auxiliary" and auxiliary["evidence"] == "使用导数"
    assert question["knowledge_point_warnings"] == []


@pytest.mark.parametrize("bad", [
    point(code="made-up"), point(code=1001), point(role="primary"),
    point(evidence="模型自行推导的内容"), point(evidence_source="chapter"),
    point(evidence_source="review_markdown", evidence="函数$f(x)$"),
    point(evidence={"quote": "函数$f(x)$"}), point(reason={"scope": "invalid"}),
    "not an object",
])
def test_bad_optional_points_do_not_destroy_text_or_inherit_model_confirmation(bad):
    question = {"content": "研究函数$f(x)$。", "answer_markdown": "原解",
                "knowledge_points": [bad], "knowledge_point_warnings": ["模型伪造的核验成功"]}
    annotate_ai_knowledge_points(question, CATALOG)
    assert question["content"] == "研究函数$f(x)$。"
    assert question["answer_markdown"] == "原解"
    assert question["knowledge_points"] == []
    assert question["knowledge_point_warnings"] and "核验成功" not in str(question["knowledge_point_warnings"])


def test_duplicate_points_and_non_list_are_dropped_with_bounded_local_warnings():
    question = {"content": "函数$f(x)$", "knowledge_points": [point(), point()]}
    annotate_ai_knowledge_points(question, CATALOG)
    assert len(question["knowledge_points"]) == 1
    warnings = list(question["knowledge_point_warnings"])
    annotate_ai_knowledge_points(question, CATALOG)
    assert question["knowledge_point_warnings"] == warnings
    annotate_ai_knowledge_points(question, CATALOG, raw={"points": []})
    assert question["knowledge_points"] == [] and question["knowledge_point_warnings"]


def test_same_single_question_post_returns_points_and_coarse_form(monkeypatch, db_session):
    import main
    provider = SimpleNamespace(api_key="isolated", api_base="https://example.invalid/v1", model_name="isolated",
                               credential_label="isolated", reasoning_effort=None, provider_code="test")
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"必修一": {"函数": ["概念"]}})
    monkeypatch.setattr(main, "load_ai_knowledge_catalog", lambda *_: deepcopy(CATALOG))
    calls = []
    def post(_provider, payload, **_kwargs):
        calls.append(payload)
        inputs = json.loads(payload["messages"][1]["content"])
        assert inputs["answer_markdown"] == "原解使用导数。"
        return SimpleNamespace(json=lambda: {"choices": [{"message": {"content": json.dumps({
            "compulsory": "必修一", "chapter": "函数", "knowledge": "概念", "question_form": "choice",
            "knowledge_points": [point(), point(code="unknown")]
        }, ensure_ascii=False)}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    result = main.ai_classify("研究函数$f(x)$。", "原解使用导数。", "", db_session)
    assert result["status"] == "success" and result["question_form"] == "choice"
    assert len(calls) == 1 and "local:teacher-function" in calls[0]["messages"][0]["content"]
    assert result["knowledge_points"][0]["status"] == "confirmed"
    assert len(result["knowledge_point_warnings"]) == 1


def test_word_metadata_keeps_source_and_bad_points_without_full_source_fallback(monkeypatch):
    from mathbank.docx_helper import _new_diagnostics
    from mathbank.source_metadata import prepare_word_source_metadata, apply_source_metadata_response, build_source_metadata_messages
    plan = prepare_word_source_metadata("1. 研究函数$f(x)$的概念。", _new_diagnostics())
    assert plan["eligible"]
    messages = build_source_metadata_messages(plan, {"必修一": {"函数": []}}, CATALOG)
    assert "local:teacher-function" in messages[0]["content"]
    response = {"items": [{"id": plan["questions"][0]["id"], "question_type": "detailed_answer", "difficulty": "challenge",
                           "category_compulsory": "必修一", "category_chapter": "函数", "category_knowledge": "",
                           "knowledge_points": [point(), point(code="unknown")]}]}
    questions = apply_source_metadata_response(response, plan, {"必修一": {"函数": []}}, knowledge_catalog=CATALOG)
    assert questions[0]["content"] == plan["questions"][0]["content"]
    assert len(questions[0]["knowledge_points"]) == 1
    assert questions[0]["knowledge_point_warnings"]


def test_single_question_rejects_stale_curriculum_before_request(monkeypatch, db_session):
    import main
    provider = SimpleNamespace(api_key="isolated", api_base="https://example.invalid/v1", model_name="isolated",
                               credential_label="isolated", reasoning_effort=None, provider_code="test")
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: provider)
    monkeypatch.setattr(main, "get_active_version_code", lambda: "A")
    monkeypatch.setattr(main, "post_chat_completion", lambda *_a, **_k: pytest.fail("Stale version must not spend an API request"))
    result = main.ai_classify("函数$f(x)$", "", "", db_session, "B")
    assert result.status_code == 409


@pytest.mark.parametrize("knowledge", ["概念", "模型合并的小节", None])
def test_single_request_normalizes_unique_placement_and_keeps_usable_points(monkeypatch, db_session, knowledge):
    import main
    provider = SimpleNamespace(api_key="isolated", api_base="https://example.invalid/v1", model_name="isolated",
                               credential_label="isolated", reasoning_effort=None, provider_code="test")
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"选修二": {"4. 函数": ["概念"]}})
    monkeypatch.setattr(main, "load_ai_knowledge_catalog", lambda *_: deepcopy(CATALOG))
    calls = []
    def post(*_args, **_kwargs):
        calls.append(True)
        return SimpleNamespace(json=lambda: {"choices": [{"message": {"content": json.dumps({
            "compulsory": "选择性必修第二册", "chapter": "函数", "knowledge": knowledge,
            "question_form": "detailed_answer", "knowledge_points": [point()]
        }, ensure_ascii=False)}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    result = main.ai_classify("研究函数$f(x)$。", "", "", db_session)
    assert result["status"] == "success" and calls == [True]
    assert (result["compulsory"], result["chapter"]) == ("选修二", "4. 函数")
    assert result["knowledge"] == ("概念" if knowledge == "概念" else "")
    assert result["knowledge_points"][0]["code"] == "local:teacher-function"


@pytest.mark.parametrize("source_format", ["tex", "markdown"])
def test_source_import_returns_valid_points_from_its_existing_single_post(monkeypatch, source_format):
    import main
    provider = SimpleNamespace(api_key="isolated", api_base="https://example.invalid/v1", model_name="isolated",
                               credential_label="isolated", provider_label="isolated",
                               reasoning_effort=None, provider_code="test")
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"必修一": {"函数": []}})
    monkeypatch.setattr(main, "load_ai_knowledge_catalog", lambda *_: deepcopy(CATALOG))
    calls = []
    def post(_provider, payload, **_kwargs):
        calls.append(payload)
        return SimpleNamespace(json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "questions": [{"content": "研究函数的定义。", "answer_markdown": "", "question_type": "detailed_answer",
                "category_compulsory": "必修一", "category_chapter": "函数", "category_knowledge": "",
                "difficulty": "challenge", "referenced_images": [],
                "knowledge_points": [point(evidence="函数"), point(code="invented", evidence="函数")]}]
        }, ensure_ascii=False)}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    result = main.ai_parse_paper("1. 研究函数的定义。", "孤立试卷", "{}", "false", source_format)
    assert isinstance(result, dict) and result["status"] == "success"
    assert len(calls) == 1 and "local:teacher-function" in calls[0]["messages"][0]["content"]
    question = result["questions"][0]
    assert question["content"] == "研究函数的定义。" and question["answer_markdown"] == ""
    assert question["knowledge_points"][0]["status"] == "confirmed"
    assert len(question["knowledge_point_warnings"]) == 1


@pytest.mark.parametrize("source_format", ["tex", "markdown"])
@pytest.mark.parametrize("evidence_kind", ["token", "tag"])
def test_source_import_restores_formula_lock_evidence_before_point_validation(monkeypatch, source_format, evidence_kind):
    import main
    provider = SimpleNamespace(api_key="isolated", api_base="https://example.invalid/v1", model_name="isolated",
                               credential_label="isolated", provider_label="isolated",
                               reasoning_effort=None, provider_code="test")
    monkeypatch.setattr(main, "resolve_text_provider", lambda *_: provider)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: {"必修一": {"函数": []}})
    monkeypatch.setattr(main, "load_ai_knowledge_catalog", lambda *_: deepcopy(CATALOG))
    calls = []
    def post(_provider, payload, **_kwargs):
        calls.append(payload)
        model_source = payload["messages"][1]["content"]
        tag = re.search(r'<mathbank-math id="(MBM_[^"]+)">(.*?)</mathbank-math>', model_source, re.DOTALL)
        assert tag
        token = "[[" + tag[1] + "]]"
        evidence = token if evidence_kind == "token" else tag[0]
        return SimpleNamespace(json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "questions": [{"content": "已知" + token + "，研究其定义。", "answer_markdown": "", "question_type": "detailed_answer",
                "category_compulsory": "必修一", "category_chapter": "函数", "category_knowledge": "",
                "difficulty": "challenge", "referenced_images": [], "knowledge_points": [point(evidence=evidence)]}]
        }, ensure_ascii=False)}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    result = main.ai_parse_paper("1. 已知$f(x)=x^2$，研究其定义。", "孤立试卷", "{}", "false", source_format)
    assert isinstance(result, dict) and result["status"] == "success"
    assert len(calls) == 1
    question = result["questions"][0]
    assert question["content"] == "已知$f(x)=x^2$，研究其定义。"
    assert question["knowledge_points"][0]["evidence"] == "$f(x)=x^2$"
    assert question["knowledge_point_warnings"] == []
