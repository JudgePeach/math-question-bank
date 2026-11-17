import json

import pytest
from fastapi.testclient import TestClient

from mathbank import mineru_api
from mathbank.task_manager import TaskCancelled
from test_pdf_mineru_source import make_bundle, pdf


class Response:
    status_code = 200

    def __init__(self, value):
        self.content = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_content(self, *, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


def test_mineru_api_uses_one_bound_result_and_keeps_token_off_asset_hosts(monkeypatch):
    original = pdf("Source provenance")
    bundle = make_bundle([{"page_idx": 0, "type": "image", "bbox": [200, 300, 500, 600]}], original)
    upload = "https://bucket.oss-cn-shanghai.aliyuncs.com/upload?signature=value"
    download = "https://cdn-mineru.openxlab.org.cn/pdf/batch.zip"
    calls = []

    class Session:
        def request(self, method, url, *, headers, stream, allow_redirects, timeout, **kwargs):
            calls.append((method, url))
            assert stream and not allow_redirects and all(value > 0 for value in timeout)
            if url.startswith("https://mineru.net/"):
                assert headers == {"Authorization": "Bearer test-token"}
            else:
                assert not headers
            if method == "POST":
                assert url == "https://mineru.net/api/v4/file-urls/batch"
                self.data_id = kwargs["json"]["files"][0]["data_id"]
                assert kwargs["json"]["model_version"] == "vlm"
                return Response({"code": 0, "data": {"batch_id": "batch", "file_urls": [upload]}})
            if url == upload:
                assert method == "PUT" and kwargs["data"] == original
                return Response(b"")
            if url.endswith("/extract-results/batch/batch"):
                state = "pending" if len(calls) == 3 else "done"
                return Response({"code": 0, "data": {"extract_result": [
                    {"data_id": self.data_id, "state": state, "full_zip_url": download},
                ]}})
            assert url == download
            return Response(bundle)

        def close(self):
            pass

    monkeypatch.setattr(mineru_api.requests, "Session", Session)
    monkeypatch.setattr(mineru_api.time, "sleep", lambda _: None)
    result = mineru_api.request_mineru_pdf_figures(original, "test-token", check_cancelled=lambda: None)
    assert result.single_image_box(0) == (200, 300, 500, 600)
    assert [method for method, _ in calls] == ["POST", "PUT", "GET", "GET", "GET"]


@pytest.mark.parametrize("value", ["http://cdn-mineru.openxlab.org.cn/a.zip", "https://example.org/file.zip"])
def test_mineru_asset_link_stays_with_https_service_hosts(value):
    with pytest.raises(mineru_api.MineruAPIError, match="地址无效"):
        mineru_api._signed_url(value, upload=False)


def test_mineru_honors_cancel_before_creating_a_batch():
    def cancel():
        raise TaskCancelled("cancelled")

    with pytest.raises(TaskCancelled):
        mineru_api.request_mineru_pdf_figures(pdf("Source"), "test-token", check_cancelled=cancel)


def test_settings_can_configure_mineru_without_exposing_the_saved_token(monkeypatch, tmp_path):
    import main

    env = dict(main.os.environ)
    env.pop("MINERU_API_TOKEN", None)
    env.pop("MINERU_PDF_FIGURES", None)
    monkeypatch.setattr(main.os, "environ", env)
    monkeypatch.setattr(main, "ENV_FILE", tmp_path / ".env")

    with TestClient(main.app) as client:
        client.headers.update({"X-Local-Token": main.LOCAL_TOKEN})
        initial = client.get("/api/settings").json()
        assert initial["mineru_homepage"] == "https://mineru.net/"
        assert initial["mineru_pdf_figures"] is False
        assert client.post("/api/settings/save", data={"mineru_pdf_figures": "true"}).status_code == 400
        configured = client.post("/api/settings/save", data={
            "mineru_api_token": "example-test-token", "mineru_pdf_figures": "true",
        })
        assert configured.status_code == 200
        settings = client.get("/api/settings")
        assert settings.json()["mineru_pdf_figures"] is True
        assert settings.json()["mineru_api_token"] == "••••••••"
        assert "example-test-token" not in settings.text
        assert client.post("/api/settings/save", data={"mineru_api_token": "••••••••",
                           "mineru_pdf_figures": "true"}).status_code == 200
        assert env["MINERU_API_TOKEN"] == "example-test-token"
        assert client.post("/api/settings/save", data={"mineru_api_token": " ",
                           "mineru_pdf_figures": "false"}).status_code == 200
        assert env["MINERU_API_TOKEN"] == ""
        assert client.get("/api/settings").json()["mineru_pdf_figures"] is False


def test_upload_seals_per_file_mineru_and_split_options(monkeypatch):
    import main

    calls = []
    monkeypatch.setenv("MINERU_API_TOKEN", "test-mineru")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-siliconflow")
    monkeypatch.setattr(main, "_queue_document_upload",
                        lambda *args: calls.append(args) or {"status": "success", "task_id": "test"})
    original = pdf("Real document")
    with TestClient(main.app) as client:
        client.headers.update({"X-Local-Token": main.LOCAL_TOKEN})
        response = client.post("/api/upload/pdf-task", files={"file": ("paper.pdf", original, "application/pdf")},
                               data={
                                   "pdf_strategy": "layout_aware", "pdf_mineru_figures": "mineru",
                                   "pdf_ocr_engine": "mineru", "whole_page_ocr_dpi": "300",
                                   "pdf_parse_model": "SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash",
                               })
        assert response.status_code == 200, response.text
    [(raw, filename, kind, req, options, func, args)] = calls
    chosen = {key: options[key] for key in main.PDF_OPTIONAL_SETTINGS}
    assert raw == original and kind == "pdf"
    assert chosen == {
        "pdf_mineru_figures": "mineru", "pdf_ocr_engine": "mineru", "whole_page_ocr_dpi": 300,
        "pdf_parse_model": "SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash",
    }
    assert func.func is main.run_pdf_parsing_task and func.keywords == chosen


def test_mineru_ocr_upload_sends_bound_transcript_to_selected_api(monkeypatch):
    from time import monotonic, sleep

    import main
    from mathbank import pdf_page_vision

    original = pdf("Source page with material")
    transcript = "一、解答题\n1. 计算 $1+1$。\n\n【答案】$2$。"
    bundle = make_bundle([{"page_idx": 0, "type": "text", "text": transcript}], original)
    bound = mineru_api.load_mineru_pdf_source(original, bundle)
    calls = []
    monkeypatch.setenv("MINERU_API_TOKEN", "test-mineru")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-siliconflow")
    monkeypatch.setenv("PREFER_PARSE_MODEL", "deepseek-flash")
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *a, **k: {
        "pdf_type": "scanned", "pages": [{"page_index": 0, "needs_ocr": True, "markdown": ""}]})
    monkeypatch.setattr(main, "schedule_database_export", lambda *a, **k: None)
    monkeypatch.setattr(pdf_page_vision, "request_pdf_page", lambda *a, **k: pytest.fail("Unexpected fallback OCR"))

    def obtain(content, token, *, check_cancelled):
        check_cancelled()
        assert content == original and token == "test-mineru"
        calls.append("mineru")
        return bound

    class Completion:
        status_code = 200
        headers = {}

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"questions": [{
                "source_number": 1, "content": "计算 $1+1$。", "question_type": "detailed_answer",
                "answer_markdown": "[EXTRACTED_ORIGINAL] $2$。", "difficulty": "normal",
            }]}, ensure_ascii=False)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    def split(provider, payload, **kwargs):
        assert provider.provider_code == "siliconflow"
        assert payload["model"] == "deepseek-ai/DeepSeek-V4.1-Flash"
        assert "1+1" in payload["messages"][1]["content"]
        calls.append("split")
        return Completion()

    monkeypatch.setattr(mineru_api, "request_mineru_pdf_figures", obtain)
    monkeypatch.setattr(main, "post_chat_completion", split)
    with TestClient(main.app) as client:
        client.headers.update({"X-Local-Token": main.LOCAL_TOKEN})
        sent = client.post("/api/upload/pdf-task", files={"file": ("paper.pdf", original, "application/pdf")},
                           data={"pdf_strategy": "layout_aware", "pdf_ocr_engine": "mineru",
                                 "pdf_verify_suspicions": "false",
                                 "pdf_parse_model": "SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash"})
        assert sent.status_code == 200, sent.text
        task_id = sent.json()["task_id"]
        deadline = monotonic() + 10
        while monotonic() < deadline:
            item = client.get(f"/api/tasks/{task_id}/status").json()
            if item.get("status") in {"completed", "error", "cancelled"}:
                break
            sleep(0.02)
        assert item.get("status") == "completed", item
        assert item["diagnostics"]["pdf_mineru_trial"]["used_pages"] == [1]
        assert "$2$" in item["data"][0]["answer_markdown"]
        assert calls == ["mineru", "split"]
