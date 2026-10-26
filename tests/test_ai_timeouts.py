"""Timeout policy is bounded, independent and safe to diagnose without secrets."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys
import warnings

import pytest

from mathbank.ai_timeouts import (
    AI_REQUEST_TIMEOUT_ENV, DOCUMENT_AI_TIMEOUT_ENV,
    DEFAULT_AI_REQUEST_TIMEOUT_SECONDS, DEFAULT_DOCUMENT_AI_TIMEOUT_SECONDS,
    resolve_ai_timeouts,
)


def test_default_policy_extends_both_request_classes_without_shared_override():
    with warnings.catch_warnings(record=True) as captured:
        policy = resolve_ai_timeouts({})
    assert (policy.request_seconds, policy.document_seconds) == (900, 1800)
    assert not captured
    with pytest.raises(FrozenInstanceError):
        policy.request_seconds = 60


@pytest.mark.parametrize("name,default", [
    (AI_REQUEST_TIMEOUT_ENV, DEFAULT_AI_REQUEST_TIMEOUT_SECONDS),
    (DOCUMENT_AI_TIMEOUT_ENV, DEFAULT_DOCUMENT_AI_TIMEOUT_SECONDS),
])
@pytest.mark.parametrize("value", ["", "59", "7201", "-1", "0", "NaN", "Infinity", "900.5",
                                  "1e3", "9" * 1000, None, True, 900])
def test_invalid_overrides_fall_back_and_never_echo_raw_value(name, default, value):
    environment = {name: value}
    if value is None:
        with warnings.catch_warnings(record=True) as captured:
            policy = resolve_ai_timeouts(environment)
        assert not captured
    else:
        with pytest.warns(RuntimeWarning) as captured:
            policy = resolve_ai_timeouts(environment)
        assert len(captured) == 1
        message = str(captured[0].message)
        assert name in message and "60 to 7200" in message
        assert f"default {default}" in message
        if isinstance(value, str) and len(value) > 4:
            assert value not in message
    assert (policy.request_seconds if name == AI_REQUEST_TIMEOUT_ENV else policy.document_seconds) == default
    assert environment == {name: value}


@pytest.mark.parametrize("value,expected", [("60", 60), ("7200", 7200), (" 1200 ", 1200)])
def test_valid_integer_boundaries_and_whitespace(value, expected):
    policy = resolve_ai_timeouts({AI_REQUEST_TIMEOUT_ENV: value, DOCUMENT_AI_TIMEOUT_ENV: value})
    assert policy.request_seconds == policy.document_seconds == expected


def test_request_and_document_overrides_are_independent():
    request = resolve_ai_timeouts({AI_REQUEST_TIMEOUT_ENV: "1200"})
    document = resolve_ai_timeouts({DOCUMENT_AI_TIMEOUT_ENV: "2400"})
    assert (request.request_seconds, request.document_seconds) == (1200, 1800)
    assert (document.request_seconds, document.document_seconds) == (900, 2400)


def test_runtime_aliases_and_default_deadlines_use_one_frozen_policy():
    script = """
import inspect, json, os
from mathbank.ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS, DOCUMENT_AI_TIMEOUT_SECONDS
from mathbank.ai_classification import CLASSIFICATION_TIMEOUT_SECONDS
from mathbank.ocr_figure_review import REVIEW_TIMEOUT_SECONDS
from mathbank.document_requests import DOCUMENT_AI_TIMEOUT_SECONDS as document
from mathbank.pdf_vision_request import PDF_VISION_TIMEOUT_SECONDS, request_pdf_vision
from mathbank.paper_parse import PAPER_SPLIT_TIMEOUT_SECONDS
from mathbank.source_review_repair import repair_verified_differences
os.environ["MATHBANK_AI_REQUEST_TIMEOUT_SECONDS"] = "60"
print(json.dumps([AI_REQUEST_TIMEOUT_SECONDS, CLASSIFICATION_TIMEOUT_SECONDS,
    REVIEW_TIMEOUT_SECONDS, DOCUMENT_AI_TIMEOUT_SECONDS, document, PDF_VISION_TIMEOUT_SECONDS,
    PAPER_SPLIT_TIMEOUT_SECONDS, inspect.signature(request_pdf_vision).parameters["timeout_seconds"].default,
    inspect.signature(repair_verified_differences).parameters["timeout_seconds"].default]))
"""
    # No live credentials or project .env are inherited by this import-only child.
    environment = {"PYTHONDONTWRITEBYTECODE": "1", AI_REQUEST_TIMEOUT_ENV: "1200",
                   DOCUMENT_AI_TIMEOUT_ENV: "2400"}
    result = subprocess.run([sys.executable, "-B", "-c", script],
        cwd=Path(__file__).parents[1], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [1200, 1200, 1200, 2400, 2400, 2400, 2400, 2400, 2400]


def test_startup_warning_redacts_an_accidentally_sensitive_override():
    sensitive = "private-token-or-path-must-not-be-logged"
    environment = {"PYTHONDONTWRITEBYTECODE": "1", AI_REQUEST_TIMEOUT_ENV: sensitive}
    result = subprocess.run([sys.executable, "-B", "-c",
        "from mathbank.ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS; print(AI_REQUEST_TIMEOUT_SECONDS)"],
        cwd=Path(__file__).parents[1], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == "900"
    assert AI_REQUEST_TIMEOUT_ENV in result.stderr and "default 900" in result.stderr
    assert sensitive not in result.stdout + result.stderr
