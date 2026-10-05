"""Default distribution and machine-local QA opt-out, without a real database."""

import ast
import json
import os
from pathlib import Path

import pytest
from starlette.responses import HTMLResponse, JSONResponse

from mathbank.ui_settings import community_qa_enabled, render_qa_visibility


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [None, "1", "true", "yes", "on"])
def test_qa_ships_enabled_by_default(monkeypatch, value):
    monkeypatch.delenv("MATHBANK_QA_ENABLED", raising=False)
    if value is not None:
        monkeypatch.setenv("MATHBANK_QA_ENABLED", value)
    assert community_qa_enabled()


@pytest.mark.parametrize("value", ["0", "FALSE", " no ", "off"])
def test_qa_local_opt_out(monkeypatch, value):
    monkeypatch.setenv("MATHBANK_QA_ENABLED", value)
    assert not community_qa_enabled()


@pytest.mark.parametrize("enabled", [True, False])
def test_index_route_serves_visibility_and_keeps_other_workspaces(monkeypatch, enabled):
    # Execute the actual route alone so importing the app cannot touch user data.
    source = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    route = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "read_index")
    route.decorator_list = []
    namespace = {
        "os": os, "json": json, "HTMLResponse": HTMLResponse, "JSONResponse": JSONResponse,
        "STATIC_DIR": ROOT / "static", "STATIC_JS_DIR": ROOT / "static/js",
        "STATIC_CSS_DIR": ROOT / "static/css", "LOCAL_TOKEN": "test-token",
        "SERVER_INSTANCE_ID": "test-instance",
    }
    exec(compile(ast.Module(body=[route], type_ignores=[]), "index-route", "exec"), namespace)
    monkeypatch.setenv("MATHBANK_QA_ENABLED", "1" if enabled else "0")
    response = namespace["read_index"]()
    html = response.body.decode()
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert f"window.__qaEnabled = {json.dumps(enabled)}" in html
    for marker in ('id="appNavQa"', 'id="ws-btn-qa"', 'id="qaWorkspaceSection"', '/static/js/qa-data.js', '/static/js/qa.js'):
        assert (marker in html) is enabled
    for workspace in ("dashboard", "bank", "import", "paper", "records"):
        assert f'id="{workspace}WorkspaceSection"' in html
    assert ("qa-disabled" in html) is not enabled
    assert '/static/js/api.js?v=' in html


def test_visibility_does_not_modify_bundled_source():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert render_qa_visibility(html, enabled=True) == html
    assert html.count("<!-- QA_START -->") == html.count("<!-- QA_END -->") == 4
