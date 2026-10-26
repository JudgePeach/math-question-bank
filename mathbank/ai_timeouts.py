"""Central, bounded AI waiting policy, fixed when the process imports it.

Load the project's environment before importing AI request modules. These
limits extend existing transport/deadline budgets; they do not create retries,
change cancellation, or turn a requests read timeout into a total-task limit.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
import warnings


DEFAULT_AI_REQUEST_TIMEOUT_SECONDS = 900
DEFAULT_DOCUMENT_AI_TIMEOUT_SECONDS = 1800
MIN_AI_TIMEOUT_SECONDS = 60
MAX_AI_TIMEOUT_SECONDS = 7200
AI_REQUEST_TIMEOUT_ENV = "MATHBANK_AI_REQUEST_TIMEOUT_SECONDS"
DOCUMENT_AI_TIMEOUT_ENV = "MATHBANK_DOCUMENT_AI_TIMEOUT_SECONDS"


@dataclass(frozen=True)
class AITimeoutPolicy:
    request_seconds: int
    document_seconds: int


def _configured_seconds(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    text = raw.strip() if isinstance(raw, str) else ""
    # Reject floats, nonfinite values and huge integers without ever echoing a
    # raw setting, which may accidentally contain a credential or local path.
    if 1 <= len(text) <= 4 and text.isascii() and text.isdecimal():
        seconds = int(text)
        if MIN_AI_TIMEOUT_SECONDS <= seconds <= MAX_AI_TIMEOUT_SECONDS:
            return seconds
    warnings.warn(
        f"{name} must be an integer from {MIN_AI_TIMEOUT_SECONDS} to "
        f"{MAX_AI_TIMEOUT_SECONDS} seconds; using default {default} seconds.",
        RuntimeWarning, stacklevel=3,
    )
    return default


def resolve_ai_timeouts(environ: Mapping[str, str] | None = None) -> AITimeoutPolicy:
    """Resolve separate request/document budgets; invalid overrides fall back."""
    environment = os.environ if environ is None else environ
    return AITimeoutPolicy(
        request_seconds=_configured_seconds(
            environment, AI_REQUEST_TIMEOUT_ENV, DEFAULT_AI_REQUEST_TIMEOUT_SECONDS),
        document_seconds=_configured_seconds(
            environment, DOCUMENT_AI_TIMEOUT_ENV, DEFAULT_DOCUMENT_AI_TIMEOUT_SECONDS),
    )


_POLICY = resolve_ai_timeouts()
AI_REQUEST_TIMEOUT_SECONDS = _POLICY.request_seconds
DOCUMENT_AI_TIMEOUT_SECONDS = _POLICY.document_seconds
