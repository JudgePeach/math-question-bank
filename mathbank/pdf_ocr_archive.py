"""Private first-pass OCR packets, including rejected and truncated responses.

These are audit records, never accepted source or transcription witnesses.
Only safe request metadata and a digest of the full request are retained;
image inputs and request credentials are not duplicated in the archive.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = "mathbank.pdf-ocr-response.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CONTROLS = {"provider_code", "model", "reasoning_effort", "response_format_type",
             "enable_thinking", "stream", "thinking_budget", "max_tokens", "max_completion_tokens",
             "max_attempts", "timeout_seconds", "page_index", "attempt", "stage", "http_status"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8", errors="surrogatepass")).hexdigest()


def _image_sha(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("PDF 原页图不能是符号链接。")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 8 <= info.st_size <= 24 * 1024 * 1024:
            raise ValueError("PDF 原页图不是有效的普通文件。")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(24 * 1024 * 1024 + 1)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) != info.st_size:
            raise ValueError("PDF 原页图内容无效或读取期间发生变化。")
        return hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)


def retain_pdf_ocr_response(store, project_id, document_sha256, body, metadata, *,
                            page_number, image_path, run=0):
    """Save one immutable private stage and return its non-content receipt.

    The PDF is read from the server-owned store, and the navigation PNG is
    hashed from disk. Invalid bindings fail locally; the request callback
    boundary records that failure without scheduling another model call.
    """
    if (not isinstance(document_sha256, str) or not _SHA.fullmatch(document_sha256)
            or type(page_number) is not int or page_number < 1
            or type(run) is not int or run < 0 or not isinstance(metadata, dict)):
        raise ValueError("PDF 识图响应存档绑定无效。")
    request_sha = metadata.get("request_payload_sha256")
    attempt = metadata.get("attempt")
    if (not isinstance(request_sha, str) or not _SHA.fullmatch(request_sha)
            or type(attempt) is not int or attempt < 1
            or metadata.get("http_status") != 200
            or metadata.get("page_index") != page_number - 1):
        raise ValueError("PDF 识图响应缺少实际请求指纹或页码。")
    safe = {}
    for key in _CONTROLS:
        value = metadata.get(key)
        if isinstance(value, (bool, int, float)) and not isinstance(value, complex):
            safe[key] = value
        elif isinstance(value, str) and len(value) <= 256:
            safe[key] = value
    with store.operation(project_id):
        actual_document_sha = hashlib.sha256(store.load_source(project_id)).hexdigest()
        if actual_document_sha != document_sha256:
            raise ValueError("PDF 识图响应与当前原文件不一致。")
        image_sha = _image_sha(image_path)
        expected_image = metadata.get("navigation_image_sha256")
        if expected_image is not None and expected_image != image_sha:
            raise ValueError("PDF 原页图与实际请求输入不一致。")
        binding = {"schema": SCHEMA, "document_sha256": document_sha256,
            "image_sha256": image_sha, "page_number": page_number,
            "request_payload_sha256": request_sha, "run": run, "attempt": attempt,
            "stage": safe.get("stage", "")}
        fingerprint = _digest(binding)
        response_sha = _digest(body)
        stage = "ocr-response-" + _digest({"binding": binding, "response_sha256": response_sha})
        record = {**binding, "metadata": safe, "response_sha256": response_sha,
                  "body": deepcopy(body), "accepted_source": False,
                  "original_verified": False}
        # Content-addressed names retain differing responses for the same
        # request/attempt, and an identical callback is an idempotent read.
        existing = store.load_stage(project_id, stage, fingerprint)
        if existing is None:
            store.save_stage(project_id, stage, record, fingerprint, complete=True)
        elif existing != record:
            raise ValueError("PDF 识图响应存档发生冲突。")
    return {**binding, "stage": stage, "input_fingerprint": fingerprint,
            "response_sha256": response_sha, "retained": True, "accepted_source": False}
