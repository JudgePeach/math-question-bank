"""The opt-in MinerU precision API used for PDF figure proposals."""

from contextlib import closing
import json
import os
import re
import time
from urllib.parse import urlsplit
import uuid

import requests

from mathbank.ai_timeouts import DOCUMENT_AI_TIMEOUT_SECONDS
from mathbank.pdf_mineru_source import (
    MAX_MINERU_BUNDLE_BYTES, MAX_MINERU_DOCUMENT_BYTES, MAX_MINERU_DOCUMENT_PAGES, load_mineru_pdf_source,
)


MINERU_HOMEPAGE = "https://mineru.net/"
MINERU_API_DOCS = "https://mineru.net/apiManage/docs"
_API = MINERU_HOMEPAGE + "api/v4"
_RESPONSE_LIMIT = 64 * 1024
_POLL_SECONDS = 3
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")


class MineruAPIError(ValueError):
    """A bounded, user-facing failure without service URLs or credentials."""


def mineru_api_token() -> str:
    token = os.getenv("MINERU_API_TOKEN", "").strip()
    return token if _TOKEN_PATTERN.fullmatch(token) else ""


def mineru_pdf_figures_enabled() -> bool:
    return os.getenv("MINERU_PDF_FIGURES", "").strip().lower() in {"1", "true"}


def validate_mineru_api_token(token: str) -> str:
    token = token.strip()
    if token and not _TOKEN_PATTERN.fullmatch(token):
        raise MineruAPIError("MinerU API Token 的格式无效，请填写 API 管理页面生成的 Token。")
    return token


def _signed_url(value, *, upload):
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        host = (parts.hostname or "") if parts else ""
        allowed = host.endswith(".aliyuncs.com")
        if not upload:
            allowed = allowed or host.endswith((".openxlab.org.cn", ".mineru.net"))
        if (parts and len(value) <= 16384 and parts.scheme == "https" and allowed
                and parts.username is None and parts.password is None and parts.port is None
                and not parts.fragment):
            return value
    except ValueError:
        pass
    raise MineruAPIError("MinerU 返回的传输地址无效。")


def _read_response(response, limit, check_wait):
    parts = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        check_wait()
        if len(parts) + len(chunk) > limit:
            raise MineruAPIError("MinerU 的响应超过本机处理上限。")
        parts.extend(chunk)
    return bytes(parts)


def request_mineru_pdf_figures(original: bytes, token: str, *, check_cancelled):
    """Submit the original PDF once, poll its batch and bind the returned layout."""
    if not 0 < len(original) <= MAX_MINERU_DOCUMENT_BYTES:
        raise MineruAPIError("PDF 超出本机 MinerU 配图处理的大小上限。")
    token = validate_mineru_api_token(token)
    if not token:
        raise MineruAPIError("请先配置 MinerU API Token。")
    check_cancelled()
    import pymupdf as fitz

    try:
        with fitz.open(stream=original, filetype="pdf") as pdf:
            count = len(pdf)
    except (ValueError, RuntimeError):
        raise MineruAPIError("无法读取要提交给 MinerU 的 PDF。") from None
    if not 0 < count <= MAX_MINERU_DOCUMENT_PAGES:
        raise MineruAPIError("PDF 超出本机 MinerU 配图处理的页数上限。")
    deadline = time.monotonic() + DOCUMENT_AI_TIMEOUT_SECONDS
    authorization = {"Authorization": f"Bearer {token}"}
    data_id = uuid.uuid4().hex

    def check_wait():
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MineruAPIError("等待 MinerU 配图结果超时。")
        return remaining

    def request(session, method, url, *, limit, headers=None, **kwargs):
        remaining = check_wait()
        with session.request(method, url, headers=headers or {}, stream=True, allow_redirects=False,
                             timeout=(min(12, remaining), remaining), **kwargs) as response:
            if not 200 <= response.status_code < 300:
                raise MineruAPIError(f"MinerU 请求失败（HTTP {response.status_code}）。")
            return _read_response(response, limit, check_wait) if limit else b""

    def api(session, method, path, **kwargs):
        try:
            payload = json.loads(request(session, method, _API + path, limit=_RESPONSE_LIMIT,
                                         headers=authorization, **kwargs))
        except (UnicodeError, json.JSONDecodeError):
            raise MineruAPIError("MinerU 返回了无效的 API 响应。") from None
        if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise MineruAPIError("MinerU 拒绝了请求，请核对 Token 和服务额度。")
        return payload["data"]

    try:
        with closing(requests.Session()) as session:
            # Do not inherit netrc Basic Auth on a signed asset URL.
            session.auth = lambda prepared: prepared
            created = api(session, "POST", "/file-urls/batch", json={
                "files": [{"name": "mathbank.pdf", "data_id": data_id, "is_ocr": True}],
                "model_version": "vlm", "language": "ch", "enable_formula": True, "enable_table": True,
            })
            batch = created.get("batch_id")
            urls = created.get("file_urls")
            if not (isinstance(batch, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch)
                    and isinstance(urls, list) and len(urls) == 1):
                raise MineruAPIError("MinerU 上传回执缺少当前文档。")
            request(session, "PUT", _signed_url(urls[0], upload=True), limit=0, data=original)

            while True:
                payload = api(session, "GET", f"/extract-results/batch/{batch}")
                rows = payload.get("extract_result")
                if not isinstance(rows, list):
                    raise MineruAPIError("MinerU 结果列表无效。")
                matched = [row for row in rows if isinstance(row, dict) and row.get("data_id") == data_id]
                if matched:
                    if len(matched) != 1:
                        raise MineruAPIError("MinerU 返回了重复的当前文档结果。")
                    state = matched[0].get("state")
                    if state == "failed":
                        raise MineruAPIError("MinerU 未能解析本次 PDF。")
                    if state == "done":
                        bundle = request(session, "GET", _signed_url(matched[0].get("full_zip_url"), upload=False),
                                         limit=MAX_MINERU_BUNDLE_BYTES)
                        return load_mineru_pdf_source(original, bundle, check_cancelled=check_wait)
                    if state not in {"waiting-file", "pending", "running", "converting"}:
                        raise MineruAPIError("MinerU 返回了未知的文档状态。")
                time.sleep(min(_POLL_SECONDS, check_wait()))
    except requests.RequestException:
        raise MineruAPIError("连接 MinerU 失败，请检查网络后再试。") from None
