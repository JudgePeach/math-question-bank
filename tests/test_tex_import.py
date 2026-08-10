# -*- coding: utf-8 -*-

import json
import io
import os
import re
from unittest.mock import MagicMock, patch

from PIL import Image

from main import LOCAL_TOKEN
from mathbank.content_locks import lock_visible_math, restore_visible_math
from mathbank.tex_helper import (
    decode_tex_bytes,
    prepare_tex_source,
    tex_asset_basename,
    tex_asset_references_match,
)


def test_tex_decoder_supports_utf8_bom_and_gb18030():
    utf8_text, utf8_diag = decode_tex_bytes(b"\xef\xbb\xbf\\title{UTF-8\xe8\xaf\x95\xe5\x8d\xb7}")
    assert utf8_text == r"\title{UTF-8试卷}"
    assert utf8_diag["encoding"] == "utf-8-sig"

    source = r"\title{旧版中文试卷}"
    gb_text, gb_diag = decode_tex_bytes(source.encode("gb18030"))
    assert gb_text == source
    assert gb_diag["encoding"] == "gb18030"
    assert gb_diag["encoding_fallback"] is True


def test_tex_preprocessor_extracts_body_title_macros_choices_and_diagnostics():
    source = r"""
\documentclass{exam}
% ordinary comment
\newcommand{\R}{\mathbb{R}}
\newcommand{\abs}[1]{\left|#1\right|}
\newcommand{\pair}[2]{(#1,#2)}
\title{\textbf{高一期中试卷}}
\begin{document}
\question 已知 $x\in\R$，求 $\abs{x}$。
\begin{choices}\choice 1 \CorrectChoice 2\end{choices}
\question 如图，计算 $x+1$。\includegraphics[width=3cm]{figures/curve.png}
\input{answers}
\end{document}
"""
    result = prepare_tex_source(source)
    model_source = result["model_source"]
    diagnostics = result["diagnostics"]

    assert result["title"] == "高一期中试卷"
    assert r"\documentclass" not in model_source
    assert "ordinary comment" not in model_source
    assert r"$x\in\mathbb{R}$" in model_source
    assert r"$\left|x\right|$" in model_source
    assert r"\item [MATHBANK_ORIGINAL_CORRECT]" in model_source
    assert diagnostics["document_body_extracted"] is True
    assert diagnostics["comments_removed"] == 1
    assert diagnostics["macros_found"] == 3
    assert diagnostics["macros_expanded"] == 2
    assert diagnostics["unsupported_macros"] == [r"\pair"]
    assert diagnostics["question_count_estimate"] == 2
    assert diagnostics["referenced_graphics"] == ["figures/curve.png"]
    assert diagnostics["external_inputs"] == ["answers"]


def test_content_locks_cover_all_common_tex_math_delimiters():
    source = (
        r"行内 \(x+1\)，展示 \[x^2\]，"
        r"以及 \begin{align}a&=b\\c&=d\end{align}。"
    )
    locked, locks = lock_visible_math(source, "tex-case")
    assert len(locks) == 3
    assert all(f'<mathbank-math id="{lock.lock_id}">' in locked for lock in locks)

    questions = [{
        "content": "、".join(f"[[{lock.lock_id}]]" for lock in locks),
        "answer_markdown": "",
    }]
    report = restore_visible_math(questions, locks)
    assert report["math_locks_restored"] == 3
    assert r"\(x+1\)" in questions[0]["content"]
    assert r"\[x^2\]" in questions[0]["content"]
    assert r"\begin{align}" in questions[0]["content"]


def test_tex_asset_matching_handles_cross_platform_paths_without_substring_guesses():
    assert tex_asset_basename(r"figures\curve.png") == "curve.png"
    assert tex_asset_references_match(r"figures\curve.png", "curve.PNG")
    assert tex_asset_references_match("figures/curve", "curve.webp")
    assert not tex_asset_references_match("a.png", "data.png")


def test_content_locks_cover_bare_matrix_and_cases_environments():
    source = r"\begin{cases}x,&x>0\\0,&x\le 0\end{cases} \begin{pmatrix}1&0\\0&1\end{pmatrix}"
    locked, locks = lock_visible_math(source, "bare-envs")
    assert len(locks) == 2
    assert all(f"[[{lock.lock_id}]]" not in locked for lock in locks)


def test_batch_image_upload_validates_content_and_duplicate_names(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    with patch("main.UPLOAD_DIR", str(tmp_path)), patch("main.UPLOAD_DIR_REL", "static/uploads"):
        good = client.post(
            "/api/upload/batch",
            files=[("files", (r"figures\curve.png", png_bytes, "image/png"))],
            headers=headers,
        )
        invalid = client.post(
            "/api/upload/batch",
            files=[("files", ("fake.png", b"not-an-image", "image/png"))],
            headers=headers,
        )
        duplicate = client.post(
            "/api/upload/batch",
            files=[
                ("files", ("curve.png", png_bytes, "image/png")),
                ("files", ("CURVE.PNG", png_bytes, "image/png")),
            ],
            headers=headers,
        )

    assert good.status_code == 200
    assert set(good.json()["mapping"]) == {"curve.png"}
    assert invalid.status_code == 400
    assert duplicate.status_code == 400


def test_tex_upload_endpoint_decodes_source_and_nested_title(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    source = r"\title{\textbf{高二月考试卷}}\begin{document}题目\end{document}"
    response = client.post(
        "/api/upload/tex-source",
        files={"file": ("paper.tex", source.encode("gb18030"), "application/x-tex")},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["title"] == "高二月考试卷"
    assert data["diagnostics"]["encoding"] == "gb18030"


def test_frontend_tex_selection_keeps_local_read_fallback():
    source = (os.path.dirname(os.path.dirname(__file__)) + "/static/js/import.js")
    with open(source, encoding="utf-8") as handle:
        import_source = handle.read()

    assert "decodeTexFileLocally(file)" in import_source
    assert "local_read_fallback: true" in import_source
    assert "TeX 文件已读取；后端预检暂不可用" in import_source


def test_tex_parse_route_preprocesses_and_restores_original_formulas(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    source = r"""
\documentclass{exam}
\newcommand{\R}{\mathbb{R}}
\title{公式保真测试卷}
\begin{document}
\question 已知 $x\in\R$，求集合。
\question 计算 \(x+1\) 的值。
\end{document}
"""

    def fake_post(_url, **kwargs):
        sent_source = kwargs["json"]["messages"][1]["content"]
        assert r"\documentclass" not in sent_source
        assert r"$x\in\mathbb{R}$" in sent_source
        lock_ids = re.findall(r'id="(MBM_[^"]+)"', sent_source)
        assert len(lock_ids) == 2
        payload = {
            "questions": [
                {
                    "content": f"已知 [[{lock_ids[0]}]]，求集合。",
                    "answer_markdown": "",
                    "question_type": "detailed_answer",
                    "category_compulsory": "必修一",
                    "category_chapter": "1. 集合与常用逻辑用语",
                    "difficulty": "easy_error",
                    "source": None,
                    "referenced_images": [],
                },
                {
                    "content": f"计算 [[{lock_ids[1]}]] 的值。",
                    "answer_markdown": "",
                    "question_type": "detailed_answer",
                    "category_compulsory": "必修一",
                    "category_chapter": "1. 集合与常用逻辑用语",
                    "difficulty": "easy_error",
                    "source": None,
                    "referenced_images": [],
                },
            ]
        }
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
        return response

    provider_env = {
        "PREFER_PARSE_MODEL": "BAILIAN/qwen3.7-max",
        "ALI_BAILIAN_API_KEY": "fake-bailian-key",
        "ALI_BAILIAN_API_BASE": "https://bailian.example/v1/",
    }
    with patch.dict(os.environ, provider_env):
        with patch("mathbank.ai_http.robust_request_post", side_effect=fake_post):
            response = client.post(
                "/api/ai/parse-paper",
                data={
                    "latex_content": source,
                    "paper_title": "",
                    "image_mapping_json": "{}",
                    "generate_answers": "false",
                },
                headers=headers,
            )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["questions"][0]["content"] == r"已知 $x\in\mathbb{R}$，求集合。"
    assert data["questions"][1]["content"] == r"计算 \(x+1\) 的值。"
    assert data["questions"][0]["source"] == "公式保真测试卷"
    assert data["tex_diagnostics"]["math_locks_restored"] == 2
    assert data["tex_diagnostics"]["question_count_estimate"] == 2
    assert data["tex_diagnostics"]["question_count_actual"] == 2


def test_tex_parse_route_maps_includegraphics_by_stable_basename(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    source = r"""
\begin{document}
\question 如图，求曲线交点。\includegraphics[width=4cm]{figures/curve.png}
\end{document}
"""

    payload = {
        "questions": [{
            "content": r"如图，求曲线交点。\includegraphics[width=4cm]{figures/curve.png}",
            "answer_markdown": "",
            "question_type": "detailed_answer",
            "category_compulsory": "必修一",
            "category_chapter": "1. 集合与常用逻辑用语",
            "difficulty": "easy_error",
            "source": None,
            "referenced_images": ["curve.png"],
        }]
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
    provider_env = {
        "PREFER_PARSE_MODEL": "BAILIAN/qwen3.7-max",
        "ALI_BAILIAN_API_KEY": "fake-bailian-key",
        "ALI_BAILIAN_API_BASE": "https://bailian.example/v1/",
    }
    with patch.dict(os.environ, provider_env):
        with patch("mathbank.ai_http.robust_request_post", return_value=mock_response):
            response = client.post(
                "/api/ai/parse-paper",
                data={
                    "latex_content": source,
                    "paper_title": "配图试卷",
                    "image_mapping_json": json.dumps({"curve.PNG": "/static/uploads/curve-local.png"}),
                    "generate_answers": "false",
                },
                headers=headers,
            )

    assert response.status_code == 200
    data = response.json()
    question = data["questions"][0]
    assert r"\includegraphics" not in question["content"]
    assert "![插图](/static/uploads/curve-local.png)" in question["content"]
    assert question["image_paths"] == ["/static/uploads/curve-local.png"]
    assert data["tex_diagnostics"]["unmapped_images"] == []
    assert data["tex_diagnostics"]["unassigned_source_images"] == []
