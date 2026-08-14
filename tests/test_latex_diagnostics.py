from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from io import BytesIO
import os
import zipfile

from mathbank.latex_diagnostics import build_local_latex_diagnostic
from mathbank.paper_helper import (
    build_answer_sheet_latex,
    build_latex_document,
    clean_content_for_latex,
    compile_tex_to_pdf,
    create_tex_zip_package,
)


def test_local_diagnostic_maps_cancel_command_to_package():
    tex = "\n".join([
        r"\documentclass{article}",
        r"\begin{document}",
        "% MathBank-Question-ID: 42",
        r"\begin{problem}",
        r"已知 $\cancel{x}=1$。",
        r"\end{problem}",
        r"\end{document}",
    ])
    log = "! Undefined control sequence.\nl.5 已知 $\\cancel{x}=1$。"
    result = build_local_latex_diagnostic(log, tex)
    assert result["package"] == "cancel"
    assert result["command"] == r"\cancel"
    assert result["question_id"] == "42"
    assert "题库题目 ID 42" in result["location"]
    assert "cancel 宏包" in result["cause"]


def test_local_diagnostic_supports_file_line_error_format():
    tex = "\n".join([
        r"\documentclass{article}",
        r"\begin{document}",
        "% MathBank-Question-ID: 51",
        r"公式 $\cancel{x}$。",
        r"\end{document}",
    ])
    log = "./paper.tex:4: Undefined control sequence.\nl.4 公式 $\\cancel{x}$。"
    result = build_local_latex_diagnostic(log, tex)
    assert result["command"] == r"\cancel"
    assert result["package"] == "cancel"
    assert result["question_id"] == "51"


def test_paper_tex_uses_portable_fontset_and_question_marker():
    tex = build_latex_document(
        "测试试卷",
        "",
        "exam",
        [{
            "question": {
                "id": 73,
                "question_type": "detailed_answer",
                "content": "求函数的最值。",
            },
            "score": 10,
        }],
    )
    assert r"\documentclass[fontset=fandol]{exam-zh}" in tex
    assert "% MathBank-Question-ID: 73" in tex


def test_paper_tex_preloads_common_math_and_table_packages():
    tex = build_latex_document("宏包测试", "", "exam", [])
    for package in (
        "amsmath", "mathtools", "cancel", "cases",
        "mhchem", "siunitx", "extarrows", "array",
        "booktabs", "tabularx", "longtable", "multirow", "makecell",
        "diagbox", "colortbl", "tabularray", "threeparttable",
    ):
        assert package in tex
    assert r"\UseTblrLibrary{booktabs}" in tex
    assert r"\providecommand{\bm}[1]{\symbf{#1}}" in tex
    assert "amssymb" not in tex
    assert "mathrsfs" not in tex


def test_answer_images_export_adjustbox_max_width_key():
    questions = [{
        "question": {
            "id": 82,
            "question_type": "detailed_answer",
            "content": "带图题。![](/static/uploads/question.png)",
            "answer_markdown": "解析图。![](/static/uploads/answer.png)",
        },
        "score": 10,
    }]

    answer_tex = build_latex_document(
        "答案图片测试", "", "exam", questions, include_answers=True
    )
    answer_sheet_tex = build_answer_sheet_latex("答题卡图片测试", "", questions)

    assert r"\usepackage[export]{adjustbox}" in answer_tex
    assert r"\includegraphics[max width=0.85\linewidth]{answer.png}" in answer_tex
    assert r"\usepackage[export]{adjustbox}" in answer_sheet_tex
    assert r"\includegraphics[max width=3.4cm]{question.png}" in answer_sheet_tex


def test_choice_empty_parentheses_do_not_leave_orphan_display_math():
    for content in (
        r"题干 $(\quad)$",
        r"题干 $（\quad）$",
        r"题干 $(\qquad)$",
        r"题干 $(\hspace{2em})$",
        r"题干 $（____）$",
        r"题干（$\quad$）",
        "题干 $",
    ):
        cleaned = clean_content_for_latex(content, q_type="single_choice")
        assert cleaned == r"题干 \paren"
        assert "$$" not in cleaned

    # A real, paired display formula must not be mistaken for an empty answer
    # parenthesis and removed.
    assert clean_content_for_latex(
        "$$x=1$$", q_type="single_choice"
    ) == r"$$x=1$$ \paren"


def test_tex_zip_image_directory_matches_generated_graphics_path(tmp_path):
    image_path = tmp_path / "curve.png"
    image_path.write_bytes(b"test image payload")
    questions = [{
        "question": {
            "id": 81,
            "question_type": "detailed_answer",
            "content": "带图题。![](/static/uploads/curve.png)",
        },
        "score": 10,
    }]
    paper_tex = build_latex_document("图片路径测试", "", "exam", questions)
    answer_sheet_tex = build_answer_sheet_latex("图片路径测试", "", questions)
    archive_bytes = create_tex_zip_package(
        "图片路径测试",
        paper_tex,
        "",
        [str(image_path)],
        answer_sheet_tex=answer_sheet_tex,
    )

    assert r"\graphicspath{{images/}{./}}" in paper_tex
    assert r"\graphicspath{{images/}{./}}" in answer_sheet_tex
    assert r"\includegraphics[width=5.0cm]{curve.png}" in paper_tex
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        assert "images/curve.png" in archive.namelist()
        assert r"\graphicspath{{images/}{./}}" in archive.read(
            "试卷正文.tex"
        ).decode("utf-8")
        assert r"\graphicspath{{images/}{./}}" in archive.read(
            "答题卡.tex"
        ).decode("utf-8")


def test_compile_rejects_error_even_if_xelatex_leaves_a_pdf():
    calls = []

    def fake_run(_cmd, *, cwd, **_kwargs):
        calls.append(cwd)
        Path(cwd, "paper.pdf").write_bytes(b"partial pdf")
        Path(cwd, "paper.log").write_text(
            "! Undefined control sequence.\nl.4 \\cancel{x}", encoding="utf-8"
        )
        return SimpleNamespace(
            returncode=1,
            stdout="! Undefined control sequence.\nl.4 \\cancel{x}",
            stderr="",
        )

    with patch("mathbank.paper_helper.subprocess.run", side_effect=fake_run):
        pdf_bytes, error = compile_tex_to_pdf("unique invalid tex for return-code test")

    assert pdf_bytes is None
    assert "Undefined control sequence" in error
    # This deliberately malformed source has no document preamble, so the
    # compiler must reject it without attempting to inject a package.
    assert len(calls) == 1


def test_compile_auto_loads_known_missing_package():
    calls = []

    def fake_run(_cmd, *, cwd, **_kwargs):
        calls.append(Path(cwd, "paper.tex").read_text(encoding="utf-8"))
        if r"\usepackage{cancel}" not in calls[-1]:
            Path(cwd, "paper.log").write_text(
                "./paper.tex:3: Undefined control sequence.\nl.3 $\\cancel{x}$",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1, stdout="Undefined control sequence", stderr="")
        Path(cwd, "paper.pdf").write_bytes(b"complete pdf")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    tex = "\n".join([r"\documentclass{article}", r"\begin{document}", r"$\cancel{x}$", r"\end{document}"])
    with patch("mathbank.paper_helper.subprocess.run", side_effect=fake_run):
        pdf_bytes, output = compile_tex_to_pdf(tex)

    assert pdf_bytes == b"complete pdf"
    assert output == "ok"
    assert len(calls) == 3  # one failed pass, then two successful passes
    assert r"\documentclass{article}\usepackage{cancel}" in calls[1]


def test_ai_explanation_uses_parse_model_and_minimal_context():
    from main import explain_latex_compile_error

    lines = ["% 不应发送的整份试卷机密"] + [f"普通行 {index}" for index in range(2, 15)]
    lines.extend([
        "% MathBank-Question-ID: 88",
        r"题目公式 $\cancel{x}$。",
        r"\end{problem}",
    ])
    tex = "\n".join(lines)
    log = "! Undefined control sequence.\nl.16 题目公式 $\\cancel{x}$。"
    response = MagicMock()
    response.json.return_value = {
        "choices": [{
            "message": {
                "content": (
                    '{"summary":"划线命令缺少支持",'
                    '"cause":"模板没有加载 cancel 宏包",'
                    '"location":"题目 88",'
                    '"fixes":["加载 cancel 宏包","或改用兼容写法"],'
                    '"package":"cancel","command":"\\\\cancel"}'
                )
            }
        }]
    }
    environment = {
        "PREFER_PARSE_MODEL": "BAILIAN/qwen3.7-max",
        "ALI_BAILIAN_API_KEY": "fake-key",
        "ALI_BAILIAN_API_BASE": "https://bailian.example/v1",
    }
    with patch.dict(os.environ, environment, clear=False):
        with patch("main.post_chat_completion", return_value=response) as request_mock:
            result = explain_latex_compile_error(log, tex)

    assert result["ai_used"] is True
    assert result["summary"] == "划线命令缺少支持"
    sent_payload = request_mock.call_args.args[1]
    assert sent_payload["model"] == "qwen3.7-max"
    assert sent_payload["enable_thinking"] is False
    assert "reasoning_effort" not in sent_payload
    assert "thinking_budget" not in sent_payload
    assert "max_tokens" not in sent_payload
    sent_context = sent_payload["messages"][1]["content"]
    assert "题目公式" in sent_context
    assert "不应发送的整份试卷机密" not in sent_context


def test_pdf_export_failure_returns_structured_diagnostic(client):
    from main import LOCAL_TOKEN

    diagnostic = {
        "summary": "公式命令缺少支持",
        "cause": "模板没有加载相应宏包。",
        "location": "题目 1",
        "fixes": ["加载宏包。"],
        "ai_used": True,
    }
    with patch(
        "main.compile_tex_to_pdf",
        return_value=(None, "! Undefined control sequence.\nl.1 \\cancel{x}"),
    ):
        with patch("main.explain_latex_compile_error", return_value=diagnostic):
            response = client.post(
                "/api/paper/export/pdf",
                json={"title": "错误诊断测试", "paper_type": "exam", "questions": []},
                headers={"X-Local-Token": LOCAL_TOKEN},
            )

    assert response.status_code == 400
    data = response.json()
    assert data["message"] == "公式命令缺少支持"
    assert data["diagnostic"]["fixes"] == ["加载宏包。"]
