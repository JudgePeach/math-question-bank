"""Real route/helper seams use extended waits and startup honors local overrides."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mathbank.ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS, DOCUMENT_AI_TIMEOUT_SECONDS
from mathbank.processing_timeouts import TIKZ_COMPILE_TIMEOUT_SECONDS


def test_settings_exposes_only_resolved_request_waits():
    import main
    assert main.get_settings()["request_timeouts"] == {
        "ai_seconds": AI_REQUEST_TIMEOUT_SECONDS, "document_seconds": DOCUMENT_AI_TIMEOUT_SECONDS}


def test_draw_request_default_and_explicit_override_keep_one_post(monkeypatch):
    import main
    calls = []
    def post(_provider, payload, **options):
        calls.append(options["timeout"])
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {
            "content": r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"}}]})
    monkeypatch.setattr(main, "post_chat_completion", post)
    provider = SimpleNamespace(model_name="fixture", provider_code="fixture", reasoning_effort=None,
                               api_base="https://example.invalid")
    assert main.request_tikz_completion(provider, "synthetic input").startswith(r"\begin{tikzpicture}")
    main.request_tikz_completion(provider, "synthetic input", timeout=123)
    assert calls == [AI_REQUEST_TIMEOUT_SECONDS, 123]


def test_local_tikz_compile_has_a_longer_wait_and_matching_message(tmp_path, monkeypatch):
    import main
    calls = []
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(main.shutil, "which", lambda _name: "/fixture/xelatex")
    def run(command, **options):
        calls.append(options["timeout"])
        raise subprocess.TimeoutExpired(command, options["timeout"])
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match=f"超过 {TIKZ_COMPILE_TIMEOUT_SECONDS} 秒"):
        main.compile_tikz_to_png(r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}")
    assert calls == [TIKZ_COMPILE_TIMEOUT_SECONDS]


def test_saved_env_is_loaded_before_business_timeout_aliases(tmp_path):
    root = Path(__file__).parents[1]
    app = tmp_path / "source-only"
    app.mkdir()
    shutil.copy2(root / "main.py", app / "main.py")
    for name in ("mathbank", "static", "templates", "scripts"):
        shutil.copytree(root / name, app / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "uploads", "test_uploads", ".env"))
    (app / ".env").write_text("MATHBANK_AI_REQUEST_TIMEOUT_SECONDS=1200\nMATHBANK_DOCUMENT_AI_TIMEOUT_SECONDS=2400\n")
    script = """
import pytest, json
import main
from mathbank.ocr_figure_review import REVIEW_TIMEOUT_SECONDS
print('TIMEOUT_POLICY=' + json.dumps([main.get_settings()['request_timeouts'],
    main.CLASSIFICATION_TIMEOUT_SECONDS, REVIEW_TIMEOUT_SECONDS,
    main.request_tikz_completion.__kwdefaults__['timeout']]))
"""
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=app,
        env={"PYTHONDONTWRITEBYTECODE": "1"}, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = next(line.removeprefix("TIMEOUT_POLICY=") for line in result.stdout.splitlines()
                  if line.startswith("TIMEOUT_POLICY="))
    assert json.loads(output) == [{"ai_seconds": 1200, "document_seconds": 2400}, 1200, 1200, 1200]
