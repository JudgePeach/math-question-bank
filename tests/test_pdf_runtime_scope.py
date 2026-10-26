"""A narrowly verified runtime scope can locate a captured environment at EOF."""

import json
import os
import shutil

import pytest

from mathbank.latex_diagnostics import build_local_latex_diagnostic
from mathbank.paper_helper import build_latex_document, compile_tex_to_pdf


def _source(*, answer=False, long=False):
    items = [
        {"question": {"id": 82, "question_type": "fill_in_blank", "content": "PREVIOUSQUESTION"}},
        {"question": {"id": 83, "question_type": "fill_in_blank",
                      "content": ("BOUNDEDCONTEXT " * 1000 if long else "") + r"求值 $\frac{1}{2$。",
                      "answer_markdown": r"解析 $\frac{1}{2$。"}},
        {"question": {"id": 84, "question_type": "fill_in_blank", "content": "UNREADNEXTQUESTION"}},
    ]
    return build_latex_document("未闭合参数验证", "", "quiz", items,
                                include_answers=answer, show_secret=False, show_notice=False)


def _log(*, field="content", environment="question", close=False):
    events = ("MathBank-Question-Start: 82 1 content\nMathBank-Question-Finish: 82\n"
              "MathBank-Question-Start: 83 2 content\n")
    if field == "answer_markdown":
        events += "MathBank-Question-Start: 83 2 answer_markdown\n"
    if close:
        events += "MathBank-Question-Finish: 83\n"
    return events + (
        "Runaway argument?\n"
        r"\begin 求值 $\frac {1}{2$。 \end {question}\ETC." + "\n"
        f"! File ended while scanning use of \\environment {environment} .\n"
        "<inserted text> \\par\n<*> paper.tex\n"
    )


def test_unclosed_question_uses_verified_runtime_range_without_inventing_a_line():
    result = build_local_latex_diagnostic(_log(), _source())
    assert result["kind"] == "unbalanced_group"
    assert result["question_id"] == "83" and result["question_number"] == "2"
    assert result["field"] == "content"
    assert result["location_confidence"] == "runtime_scope"
    assert result["line_number"] is None and result["source_match_line"] is None
    assert result["excerpt_location_confidence"] == "compiler_context"
    assert result["command"] == result["package"] == ""
    assert result["error_excerpt"].startswith("Runaway argument?")
    assert "未提供准确行号" in result["location"] and "范围" in result["location"]
    assert r"\frac{1}{2$" in result["source_context"]
    assert "PREVIOUSQUESTION" not in result["source_context"]
    assert "UNREADNEXTQUESTION" not in result["source_context"]


def test_unclosed_solution_can_use_the_answer_runtime_scope():
    result = build_local_latex_diagnostic(_log(field="answer_markdown", environment="solution"),
                                          _source(answer=True))
    assert result["field"] == "answer_markdown"
    assert result["question_id"] == "83" and result["line_number"] is None
    assert "参考答案与解析" in result["location"]
    assert "解析" in result["source_context"]
    assert "求值" not in result["source_context"]


@pytest.mark.parametrize("change", [
    lambda source: source.replace("% MathBank-Question-Number: 2", "% MathBank-Question-Number: 99"),
    lambda source: source.replace(r"\typeout{MathBank-Question-Start: 83 2 content}", ""),
    lambda source: source.replace(r"\typeout{MathBank-Question-Finish: 83}", ""),
    lambda source: source.replace("% MathBank-Field: content\n\\typeout{MathBank-Question-Start: 83", "% MathBank-Field: figure\n\\typeout{MathBank-Question-Start: 83"),
])
def test_runtime_events_cannot_locate_a_question_without_matching_source_scopes(change):
    result = build_local_latex_diagnostic(_log(), change(_source()))
    assert result["question_id"] == ""
    assert result["location_confidence"] == "unlocated"


@pytest.mark.parametrize("log", [
    _log(close=True),
    _log(environment="solution"),
    _log().replace("MathBank-Question-Start: 83 2 content", "MathBank-Question-Start: 999 2 content"),
    "MathBank-Question-Start: 83 2 content\n! Undefined control sequence.\n",
    "MathBank-Question-Start: 83 2 content\nRunaway argument?\n! File ended while scanning use of \\foo.\n",
    "MathBank-Question-Start: 83 2 content\nMissing character: There is no 🦄 in font nullfont!\n",
    "MathBank-Question-Start: 83 2 content\n! Template error.\n",
    "MathBank-Question-Start: 83 2 content\nRunaway argument?\n! File ended while scanning use of \\environment question .\nMissing character: There is no 🦄 in font nullfont!\n",
])
def test_runtime_fallback_is_not_used_for_closed_mismatched_glyph_or_template_errors(log):
    result = build_local_latex_diagnostic(log, _source())
    assert result["question_id"] == ""
    assert result["location_confidence"] != "runtime_scope"


def test_preserved_runtime_events_survive_truncation_and_context_is_bounded():
    log = "MathBank-Question-Start: 999 9 content\n" + _log().split("Runaway argument?", 1)[1]
    log = "Runaway argument?\n" + log
    log += ("\nMathBank compilation scope events:\n"
            "MathBank-Question-Start: 82 1 content\nMathBank-Question-Finish: 82\n"
            "MathBank-Question-Start: 83 2 content\n")
    result = build_local_latex_diagnostic(log, _source(long=True))
    assert result["question_id"] == "83"
    assert len(result["source_context"]) <= 3000
    assert len(result["error_excerpt"]) <= 600
    assert result["line_number"] is None


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_LAYOUT_NATIVE") != "1" or not shutil.which("xelatex"),
                    reason="Opt-in native XeLaTeX unclosed-environment diagnostic validation")
def test_native_missing_fraction_brace_identifies_question_range_at_eof(tmp_path):
    source = _source()
    (tmp_path / "brace.tex").write_text(source, encoding="utf-8")
    pdf, log = compile_tex_to_pdf(source)
    (tmp_path / "brace.log").write_text(log, encoding="utf-8")
    assert pdf is None
    assert "File ended while scanning use of \\environment question" in log
    assert "MathBank-Question-Start: 83 2 content" in log
    result = build_local_latex_diagnostic(log, source)
    (tmp_path / "brace-diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["question_id"] == "83" and result["question_number"] == "2"
    assert result["field"] == "content" and result["kind"] == "unbalanced_group"
    assert result["location_confidence"] == "runtime_scope"
    assert result["line_number"] is None and result["source_match_line"] is None
    assert result["excerpt_location_confidence"] == "compiler_context"
    assert r"\frac" in result["error_excerpt"]
