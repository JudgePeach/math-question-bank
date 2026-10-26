"""The retained legacy parser honors explicit frozen providers and cache keys.

The default metadata project path is covered by test_pdf_local_draft_http.
"""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf_feature_contract import experimental_pdf_only
from mathbank.ai_knowledge_points import bind_ai_knowledge_catalog
from mathbank.ai_providers import TextProviderConfig, apply_model_thinking_policy
from mathbank.document_project_parse import run_project_legacy_parse
from mathbank.document_projects import DocumentProjectStore
from mathbank.paper_parse import PAPER_SPLIT_TIMEOUT_SECONDS, parse_paper_completion
from mathbank.pdf_legacy_parse import completion_request_binding
from mathbank.task_manager import TaskCancelled


@pytest.fixture
def isolated_parser():
    # Do not import main, initialize its DB, read settings, or start its server.
    tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text())
    parser = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "parse_paper_text_internal")
    code = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                           parser], type_ignores=[])
    providers = {name: TextProviderConfig("deepseek", "fake", "FAKE_KEY", "fake-not-a-secret",
        f"https://{name.lower()}.invalid/v1", name, None, name) for name in ("A", "B")}
    current, resolved, posts = ["B"], [], []

    def resolve(name):
        resolved.append(name)
        return providers[name]

    def post(provider, payload, **kwargs):
        posts.append({"provider": provider.model_name, "model": payload["model"],
                      "endpoint": provider.chat_completions_url})
        packet = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"questions": [{"content": "fake result from " + provider.model_name}]})}}]}
        return SimpleNamespace(json=lambda: deepcopy(packet))

    namespace = {"copy": SimpleNamespace(deepcopy=deepcopy),
        "os": SimpleNamespace(getenv=lambda key, default=None: current[0] if key == "PREFER_PARSE_MODEL" else default),
        "resolve_text_provider": resolve, "get_current_curriculum": lambda: {},
        "build_pdf_parse_system_prompt": lambda *_: "isolated original prompt",
        "load_ai_knowledge_catalog": lambda: [],
        "bind_ai_knowledge_catalog": bind_ai_knowledge_catalog,
        "apply_model_thinking_policy": apply_model_thinking_policy,
        "parse_paper_completion": parse_paper_completion, "post_chat_completion": post,
        "PAPER_SPLIT_TIMEOUT_SECONDS": PAPER_SPLIT_TIMEOUT_SECONDS, "TaskCancelled": TaskCancelled,
        "validate_import_knowledge": lambda *_args, **_kwargs: None,
        "normalize_question_math_markdown": lambda value: value,
        "diagnostics": {}, "layout_result": None, "curriculum": {},
        "check_pdf_cancelled": lambda: None, "report_pdf_split_attempt": lambda _: None}
    exec(compile(ast.fix_missing_locations(code), "isolated-main-provider-snapshot", "exec"), namespace)
    return SimpleNamespace(namespace=namespace, providers=providers, current=current, resolved=resolved, posts=posts)


@pytest.mark.parametrize("captured,changed", [("A", "B"), ("B", "A")])
@experimental_pdf_only
def test_legacy_cache_profile_and_actual_parser_http_share_explicit_provider_snapshot(
        isolated_parser, tmp_path, captured, changed):
    fixture = isolated_parser
    provider = fixture.providers[captured]
    profile = completion_request_binding(provider, apply_model_thinking_policy(
        {"model": provider.model_name, "reasoning_effort": provider.reasoning_effort}, provider=provider, task="parse"))
    prompt_version = "legacy-source-identities.v1:" + hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    fixture.namespace["provider"] = provider
    # This is the precise race: settings change after the project captured its
    # cache profile but before the retained parser prepares HTTP.
    fixture.current[0] = changed
    store = DocumentProjectStore(tmp_path / "projects")
    raw = b"%PDF-isolated-provider-snapshot"
    project_id = store.create(raw, "fake.pdf", {}, {})["project_id"]
    executions = []

    def executor(cache):
        executions.append(1)
        fixture.namespace["cache"] = cache
        rows = fixture.namespace["parse_paper_text_internal"]("1. fake",False,
            request_post=fixture.namespace["post_chat_completion"],provider_snapshot=provider,
            curriculum_snapshot={},pdf_retry=False,connection_retry=False)
        return {"questions": rows, "partial": False, "preserved_indices": []}

    arguments = {"source_document_sha256": hashlib.sha256(raw).hexdigest(), "prompt_version": prompt_version}
    original = run_project_legacy_parse(store, project_id, "1. fake", [], {}, executor=executor, **arguments)
    assert original["questions"][0]["content"] == "fake result from " + captured
    assert fixture.posts == [{"provider": captured, "model": captured, "endpoint": provider.chat_completions_url}]
    assert fixture.resolved == [], "an explicit parser snapshot must not resolve live settings again"
    fixture.current[0] = captured
    diagnostics = {}
    restored = run_project_legacy_parse(store, project_id, "1. fake", [], {}, executor=executor,
                                       diagnostics=diagnostics, **arguments)
    assert restored == original and len(executions) == len(fixture.posts) == 1
    assert diagnostics["pdf_project_legacy_parse"]["new_calls"] == 0
    assert diagnostics["pdf_project_legacy_parse"]["executor_calls"] == 0


def test_non_project_parser_without_snapshot_keeps_current_settings_behavior(isolated_parser):
    fixture = isolated_parser
    result = fixture.namespace["parse_paper_text_internal"]("1. fake", False,
        connection_retry=False)
    assert result[0]["content"] == "fake result from B"
    assert fixture.resolved == ["B"] and fixture.posts[0]["provider"] == "B"
