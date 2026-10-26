"""Compiler warnings must not pass the PDF success/cache gate."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from mathbank import paper_helper as paper
from mathbank.latex_diagnostics import build_local_latex_diagnostic


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    from collections import OrderedDict
    monkeypatch.setattr(paper, "_PDF_CACHE", OrderedDict())
    monkeypatch.setattr(paper, "_with_content_source_pdf_metadata", lambda data, notice: data)


@pytest.mark.parametrize("pass_number", [1, 2])
def test_missing_glyph_in_full_log_blocks_even_success_and_never_caches(monkeypatch, pass_number):
    calls = []
    def run(_cmd, *, cwd, **kwargs):
        calls.append(1)
        Path(cwd, "paper.pdf").write_bytes(b"incomplete pdf")
        warning = "Missing character: There is no 🦄 (U+1F984) in font Times New Roman!\nl.5 已知🦄。"
        Path(cwd, "paper.log").write_text("noise\n" * 10000 + (warning if len(calls) == pass_number else "ok"))
        return SimpleNamespace(returncode=0, stdout="output truncated", stderr="")
    monkeypatch.setattr(paper.subprocess, "run", run)
    tex = "\n".join([r"\documentclass{article}", r"\begin{document}",
                        "% MathBank-Question-ID: 11", "% MathBank-Field: content", "已知🦄。", r"\end{document}"])
    for _ in range(2):
        before = len(calls)
        # Each request must recompile. Never retain a failed PDF in cache.
        def request_run(cmd, **kwargs):
            # Reset request pass accounting without losing total calls.
            index = len(calls) - before + 1
            result = run(cmd, **kwargs)
            if index == pass_number:
                Path(kwargs['cwd'], 'paper.log').write_text(
                    "noise\n" * 10000 + "Missing character: There is no 🦄 (U+1F984) in font Times New Roman!\nl.5 已知🦄。")
            else:
                Path(kwargs['cwd'], 'paper.log').write_text('ok')
            return result
        monkeypatch.setattr(paper.subprocess, "run", request_run)
        pdf, log = paper.compile_tex_to_pdf(tex)
        assert pdf is None
        assert "Missing character" in log
        assert "🦄" in log
        assert len(calls) - before == pass_number
        assert not paper._PDF_CACHE


def test_success_is_cached_only_after_both_passes(monkeypatch):
    calls = []
    def run(cmd, *, cwd, **kwargs):
        calls.append(1)
        source = Path(cwd, "paper.tex").read_text()
        assert source.startswith(r"\tracinglostchars=3\relax ")
        assert source.count("\n") == 3
        Path(cwd, "paper.log").write_text("ordinary warnings only")
        Path(cwd, "paper.pdf").write_bytes(b"validated pdf")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(paper.subprocess, "run", run)
    tex = "\n".join([r"\documentclass{article}", r"\begin{document}", "x", r"\end{document}"])
    assert paper.compile_tex_to_pdf(tex)[0] == b"validated pdf"
    assert paper.compile_tex_to_pdf(tex)[0] == b"validated pdf"
    assert len(calls) == 2


def test_answer_sheet_has_bounded_question_figure_location():
    tex = paper.build_answer_sheet_latex("答题卡", "", [{"question": {
        "id": 81, "question_type": "detailed_answer", "content": "已知。",
        "tikz_code": r"\begin{tikzpicture}\node {🦄};\end{tikzpicture}",
    }, "score": 13}])
    row = next(i for i, text in enumerate(tex.splitlines(), 1) if r"\node {🦄}" in text)
    diagnostic = build_local_latex_diagnostic(
        f"./paper.tex:{row}: Missing character: There is no 🦄 (U+1F984) in font Times New Roman!", tex)
    assert diagnostic["question_id"] == "81"
    assert str(diagnostic["question_number"]) == "15"
    assert diagnostic["field"] == "figure"
