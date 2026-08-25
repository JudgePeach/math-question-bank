import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from main import (
    LOCAL_TOKEN,
    correct_tikz_endpoint,
    draw_tikz_via_high_model,
    extract_tikz_source,
    ocr_pdf_page_image,
    ocr_via_provider,
)
from mathbank.ai_providers import resolve_ocr_provider


def test_ocr_request_uses_resolved_multimodal_provider(tmp_path):
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_GPT_API_KEY": "ocr-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://vision.example/v1",
            "ZHONGZHAN_GPT_OCR_MODEL": "gpt-5.6-luna:high",
        },
    )
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "识别结果 $x=1$"}}]
    }

    with patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = ocr_via_provider(
            str(image_path), provider, include_illustration_box=True
        )

    assert result == "识别结果 $x=1$"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://vision.example/v1/chat/completions"
    assert kwargs["json"]["model"] == "gpt-5.6-luna"
    assert kwargs["json"]["reasoning_effort"] == "high"
    assert kwargs["json"]["enable_thinking"] is True
    image_item = kwargs["json"]["messages"][0]["content"][1]
    assert image_item["image_url"]["url"].startswith("data:image/png;base64,")


def test_bailian_ocr_explicitly_disables_thinking(tmp_path):
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "bailian",
        {
            "ALI_BAILIAN_API_KEY": "ocr-key",
            "ALI_BAILIAN_OCR_MODEL": "qwen3.7-flash",
        },
    )
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "识别结果 $x=1$"}}]
    }

    with patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = ocr_via_provider(str(image_path), provider)

    assert result == "识别结果 $x=1$"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["enable_thinking"] is False
    assert payload["max_completion_tokens"] == 16384
    assert "thinking_budget" not in payload
    assert "reasoning_effort" not in payload


def test_ocr_read_timeout_is_not_retried(tmp_path):
    image_path = tmp_path / "question.png"
    image_path.write_bytes(b"fake-image-bytes")
    provider = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_GPT_API_KEY": "ocr-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://vision.example/v1",
            "ZHONGZHAN_GPT_OCR_MODEL": "gpt-5.6-luna",
        },
    )

    with patch(
        "mathbank.ai_http.robust_request_post",
        side_effect=requests.exceptions.ReadTimeout("unknown provider state"),
    ) as mock_post:
        with pytest.raises(requests.exceptions.ReadTimeout):
            ocr_via_provider(str(image_path), provider)

    mock_post.assert_called_once()


def test_pdf_ocr_does_not_fallback_after_ambiguous_read_timeout():
    providers = [MagicMock(provider_label="first"), MagicMock(provider_label="second")]

    with patch("main.resolve_ocr_fallbacks", return_value=providers), patch(
        "main.ocr_via_provider",
        side_effect=requests.exceptions.ReadTimeout("unknown provider state"),
    ) as mock_ocr:
        with pytest.raises(RuntimeError, match="避免重复计费"):
            ocr_pdf_page_image("/tmp/page.png")

    mock_ocr.assert_called_once()


def test_draw_request_strips_siliconflow_provider_prefix(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake-diagram-bytes")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        "\\begin{tikzpicture}"
                        "\\draw (0,0)--(1,1);"
                        "\\end{tikzpicture}"
                    )
                }
            }
        ]
    }

    with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sf-key"}):
        with patch(
            "mathbank.ai_http.robust_request_post", return_value=response
        ) as mock_post:
            result = draw_tikz_via_high_model(
                str(image_path),
                "SILICONFLOW/Qwen/Qwen3-VL-32B-Instruct",
                latex_content="三角形 ABC",
            )

    assert result.startswith("\\begin{tikzpicture}")
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.siliconflow.cn/v1/chat/completions"
    assert kwargs["json"]["model"] == "Qwen/Qwen3-VL-32B-Instruct"
    assert isinstance(kwargs["json"]["messages"][0]["content"], list)


def test_draw_request_injects_configured_reasoning_effort(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake-diagram-bytes")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "\\begin{tikzpicture}\\end{tikzpicture}"}}]
    }
    provider_env = {
        "ZHONGZHAN_GPT_API_KEY": "draw-key",
        "ZHONGZHAN_GPT_BASE_URL": "https://draw.example/v1",
    }

    with patch.dict(os.environ, provider_env):
        with patch("mathbank.ai_http.robust_request_post", return_value=response) as mock_post:
            draw_tikz_via_high_model(
                str(image_path),
                "ZHONGZHAN_GPT/gpt-5.6-luna:high",
                latex_content="三角形 ABC",
            )

    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning_effort"] == "high"
    assert payload["enable_thinking"] is True


def test_draw_request_supports_text_only_workbench_input():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": "\\begin{tikzpicture}\\end{tikzpicture}"}}]
    }

    with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sf-key"}):
        with patch(
            "mathbank.ai_http.robust_request_post", return_value=response
        ) as mock_post:
            result = draw_tikz_via_high_model(
                None,
                "SILICONFLOW/Qwen/Qwen3-VL-32B-Instruct",
                latex_content="△ABC 中 AB=AC",
                instruction="标出中线 AD",
            )

    assert result.startswith("\\begin{tikzpicture}")
    payload = mock_post.call_args.kwargs["json"]
    assert isinstance(payload["messages"][0]["content"], str)
    assert "标出中线 AD" in payload["messages"][0]["content"]
    assert "△ABC 中 AB=AC" in payload["messages"][0]["content"]


def test_shared_tikz_parser_wraps_fenced_body_and_rejects_plain_prose():
    source = extract_tikz_source("```latex\n\\draw (0,0)--(1,1);\n```")
    assert source.startswith("\\begin{tikzpicture}")
    assert source.endswith("\\end{tikzpicture}")

    with pytest.raises(RuntimeError, match="完整的 tikzpicture"):
        extract_tikz_source("绘图已经完成，请查收。")


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(output, format="PNG")
    return output.getvalue()


def test_workbench_draw_route_accepts_text_without_reference(client):
    generated = "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}"
    with patch("main.draw_tikz_via_high_model", return_value=generated) as mock_draw:
        response = client.post(
            "/api/ai/draw_tikz",
            data={"instruction": "画一条线段", "context": "题干", "existing_tikz": ""},
            headers={"X-Local-Token": LOCAL_TOKEN},
        )

    assert response.status_code == 200
    assert response.json()["tikz_code"] == generated
    assert response.json()["used_reference_image"] is False
    assert mock_draw.call_args.args[0] is None
    assert mock_draw.call_args.kwargs["instruction"] == "画一条线段"


def test_workbench_draw_route_persists_one_normalized_reference_after_success(client):
    generated = "\\begin{tikzpicture}\\draw (0,0) circle (1);\\end{tikzpicture}"
    observed_path = None

    def draw(reference_path, *_args, **_kwargs):
        nonlocal observed_path
        observed_path = reference_path
        assert os.path.isfile(reference_path)
        assert os.path.basename(reference_path).startswith("tikz_reference_")
        return generated

    persisted_path = None
    try:
        with patch("main.draw_tikz_via_high_model", side_effect=draw):
            response = client.post(
                "/api/ai/draw_tikz",
                data={"instruction": "按原图重绘"},
                files={"reference_image": ("reference.png", _png_bytes(), "image/png")},
                headers={"X-Local-Token": LOCAL_TOKEN},
            )

        assert response.status_code == 200
        assert response.json()["used_reference_image"] is True
        assert observed_path
        assert not os.path.exists(observed_path)
        persisted_url = response.json()["reference_image_path"]
        assert persisted_url.startswith("/static/test_uploads/tikz_reference_")
        persisted_path = Path("main.py").resolve().parent / persisted_url.lstrip("/")
        assert persisted_path.is_file()
    finally:
        if persisted_path is not None:
            persisted_path.unlink(missing_ok=True)


def test_workbench_draw_route_reuses_stored_ocr_reference_image(client):
    generated = "\\begin{tikzpicture}\\draw (0,0) circle (1);\\end{tikzpicture}"
    upload = client.post(
        "/api/upload",
        files={"file": ("ocr-original.png", _png_bytes(), "image/png")},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert upload.status_code == 200
    reference_url = upload.json()["file_path"]
    observed_path = None

    def draw(reference_path, *_args, **_kwargs):
        nonlocal observed_path
        observed_path = Path(reference_path)
        assert observed_path.is_file()
        return generated

    try:
        with patch("main.draw_tikz_via_high_model", side_effect=draw):
            response = client.post(
                "/api/ai/draw_tikz",
                data={
                    "instruction": "根据 OCR 原题图修改",
                    "reference_image_path": reference_url,
                },
                headers={"X-Local-Token": LOCAL_TOKEN},
            )

        assert response.status_code == 200
        assert response.json()["used_reference_image"] is True
        assert response.json()["reference_image_path"] == reference_url
        assert observed_path is not None
        assert observed_path.is_file()
    finally:
        if observed_path is not None:
            observed_path.unlink(missing_ok=True)


def test_workbench_draw_route_rejects_empty_input(client):
    response = client.post(
        "/api/ai/draw_tikz",
        data={"instruction": "", "context": ""},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )

    assert response.status_code == 400
    assert "绘图要求" in response.json()["detail"]


def test_failed_workbench_draw_does_not_leave_reference_file(client):
    upload_root = Path("static/test_uploads").resolve()
    before = set(upload_root.glob("tikz_reference_*"))
    with patch("main.draw_tikz_via_high_model", side_effect=RuntimeError("failed")):
        response = client.post(
            "/api/ai/draw_tikz",
            data={"instruction": "按图重绘"},
            files={"reference_image": ("reference.png", _png_bytes(), "image/png")},
            headers={"X-Local-Token": LOCAL_TOKEN},
        )

    assert response.status_code == 400
    assert set(upload_root.glob("tikz_reference_*")) == before


def test_question_ocr_still_auto_draws_tikz_and_returns_original_reference(client):
    generated = "\\begin{tikzpicture}\\draw (0,0)--(1,0);\\end{tikzpicture}"
    rendered_url = "/static/test_uploads/tikz_auto_test.png"
    observed_original = None
    provider = SimpleNamespace(
        api_key="ocr-key",
        provider_label="Test OCR",
        model_name="test-vision",
        credential_label="TEST_KEY",
    )

    def draw(original_path, *_args, **_kwargs):
        nonlocal observed_original
        observed_original = Path(original_path)
        assert observed_original.is_file()
        return generated

    try:
        with patch("main.resolve_ocr_provider", return_value=provider), patch(
            "main.ocr_via_provider",
            return_value="已知三角形 ABC\n[ILLUSTRATION_BOX: 10, 20, 90, 80]",
        ), patch("main.draw_tikz_via_high_model", side_effect=draw), patch(
            "main.compile_tikz_to_png", return_value=rendered_url
        ):
            response = client.post(
                "/api/ocr",
                data={"engine": "siliconflow", "skip_tikz": "false"},
                files={"file": ("question.png", _png_bytes(), "image/png")},
                headers={"X-Local-Token": LOCAL_TOKEN},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["tikz_code"] == generated
        assert payload["tikz_image_path"] == rendered_url
        assert payload["image_path"].startswith("/static/test_uploads/ocr_original_")
        assert "ILLUSTRATION_BOX" not in payload["latex"]
        assert rendered_url in payload["latex"]
        assert observed_original is not None
    finally:
        if observed_original is not None:
            observed_original.unlink(missing_ok=True)


def test_pdf_ocr_uses_claude_provider_when_selected():
    provider_env = {
        "OCR_PREFER_ENGINE": "zhongzhan_claude",
        "ZHONGZHAN_CLAUDE_API_KEY": "claude-key",
        "ZHONGZHAN_CLAUDE_OCR_MODEL": "claude-vision",
        "SILICONFLOW_API_KEY": "",
        "ALI_BAILIAN_API_KEY": "",
        "ZHONGZHAN_GPT_API_KEY": "",
        "ZHONGZHAN_API_KEY": "",
    }

    with patch.dict(os.environ, provider_env):
        with patch("main.ocr_via_provider", return_value="整页识别结果") as mock_ocr:
            result = ocr_pdf_page_image("/tmp/page.png")

    assert result == "整页识别结果"
    resolved_provider = mock_ocr.call_args.args[1]
    assert resolved_provider.provider_code == "zhongzhan_claude"
    assert resolved_provider.model_name == "claude-vision"


def test_tikz_correction_uses_resolved_bailian_provider(tmp_path):
    upload_dir = tmp_path / "static" / "uploads"
    upload_dir.mkdir(parents=True)
    original_path = upload_dir / "original.png"
    original_path.write_bytes(b"original-image")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "```latex\n\\begin{tikzpicture}\n\\end{tikzpicture}\n```"
                }
            }
        ]
    }
    provider_env = {
        "PREFER_DRAW_MODEL": "BAILIAN/qwen3.7-max:medium",
        "ALI_BAILIAN_API_KEY": "bailian-key",
        "ALI_BAILIAN_API_BASE": "https://draw.example/v1",
    }

    with patch.dict(os.environ, provider_env):
        with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
            "main.UPLOAD_DIR_REL", "static/uploads"
        ):
            with patch(
                "main.compile_tikz_to_png", side_effect=RuntimeError("compile failed")
            ):
                with patch(
                    "mathbank.ai_http.robust_request_post", return_value=response
                ) as mock_post:
                    result = correct_tikz_endpoint(
                        tikz_code="\\begin{tikzpicture}bad\\end{tikzpicture}",
                        original_image_path="/static/uploads/original.png",
                        user_prompt="修正线条",
                    )

    assert result["status"] == "success"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://draw.example/v1/chat/completions"
    assert kwargs["json"]["model"] == "qwen3.7-max"
    assert kwargs["json"]["enable_thinking"] is True
    assert kwargs["json"]["thinking_budget"] == 8192
    assert kwargs["json"]["max_completion_tokens"] == 16384
    assert "reasoning_effort" not in kwargs["json"]
    assert len(kwargs["json"]["messages"][0]["content"]) == 2
