"""Source-bound PDF diagnostics, independent of a database or AI service."""

from mathbank.latex_diagnostics import (
    build_local_latex_diagnostic,
    extract_missing_glyphs,
    merge_ai_latex_diagnostic,
)
from mathbank.prompts import build_latex_error_explanation_prompts


SOURCE = "\n".join([
    r"\documentclass{article}",
    r"\begin{document}",
    "% MathBank-Question-ID: 83",
    "% MathBank-Question-Number: 2",
    "% MathBank-Field: content",
    r"如图，在 $\triangle ABC$ 中，$\notarealcommand ACB=90^\circ$。",
    "% MathBank-Field: answer_markdown",
    "解析：∠ACB 是直角。",
    "% MathBank-Question-End",
    r"\badtemplatecommand",
    r"\end{document}",
])


def test_wrapped_missing_glyph_warning_has_character_font_but_no_guessed_location():
    warning = (
        "Missing character: There is no ∠ (U+2220) in font Times New Roman/OT:script=lat\n"
        "n;language=dflt;mapping=tex-text;!\n"
    )
    records = extract_missing_glyphs(warning + warning)
    assert records == [{"character": "∠", "codepoint": "U+2220",
                        "font": "Times New Roman/OT:script=latn;language=dflt;mapping=tex-text;",
                        "line_number": None}]
    result = build_local_latex_diagnostic(warning, SOURCE)
    assert result["kind"] == "missing_glyph"
    assert result["question_id"] == ""
    assert result["location_confidence"] == "unlocated"
    assert "位置待核对" in result["location"]
    assert r"$\triangle ABC$" in " ".join(result["fixes"])
    assert r"$\angle ACB$" in " ".join(result["fixes"])


def test_strict_missing_glyph_maps_source_line_to_question_and_answer():
    log = "./paper.tex:8: Missing character: There is no ∠ (U+2220) in font Times New Roman!\nl.8 解析：∠"
    result = build_local_latex_diagnostic(log, SOURCE)
    assert result["kind"] == "missing_glyph"
    assert result["question_id"] == "83"
    assert result["question_number"] == "2"
    assert result["field"] == "answer_markdown"
    assert "参考答案与解析" in result["location"]
    assert result["error_excerpt"] == "解析：∠ACB 是直角。"
    assert result["character"] == "∠"
    assert result["line_number"] == 8


def test_strict_bang_missing_glyph_uses_its_own_line_context():
    log = "! Missing character: There is no 🦄 (U+1F984) in font Times New Roman!\nl.8 解析：🦄\n"
    assert extract_missing_glyphs(log)[0]["line_number"] == 8


def test_plain_warning_does_not_inherit_a_later_error_line():
    log = (
        "Missing character: There is no ∠ (U+2220) in font Times New Roman!\n"
        "! Undefined control sequence.\nl.6 \\notarealcommand\n"
    )
    assert extract_missing_glyphs(log)[0]["line_number"] is None


def test_unknown_character_is_reported_without_guessing_its_mathematical_meaning():
    result = build_local_latex_diagnostic(
        "./paper.tex:8: Missing character: There is no 🦄 (U+1F984) in font Times New Roman!\n", SOURCE)
    assert result["character"] == "🦄"
    assert result["codepoint"] == "U+1F984"
    assert "先对照原题确认含义" in " ".join(result["fixes"])
    assert result["command"] == ""


def test_undefined_command_prefers_the_actual_error_token_over_an_earlier_valid_command():
    log = (
        "./paper.tex:6: Undefined control sequence.\n"
        "<recently read> \\notarealcommand\n"
        "l.6 如图，在 $\\triangle ABC$ 中，$\\notarealcommand\n"
    )
    result = build_local_latex_diagnostic(log, SOURCE)
    assert result["kind"] == "undefined_command"
    assert result["command"] == r"\notarealcommand"
    assert result["question_id"] == "83"
    assert result["field"] == "content"
    assert r"\notarealcommand" in result["error_excerpt"]


def test_template_and_out_of_range_error_do_not_inherit_the_last_question():
    for log in (
        "./paper.tex:10: Undefined control sequence.\nl.10 \\badtemplatecommand",
        "./paper.tex:900: Undefined control sequence.\nl.900 \\badtemplatecommand",
        "./exam-zh-question.sty:6: Undefined control sequence.\nl.6 \\badtemplatecommand",
    ):
        result = build_local_latex_diagnostic(log, SOURCE)
        assert result["question_id"] == ""
        assert result["field"] == ""


def test_malformed_math_has_bounded_excerpt_and_complete_formula_examples():
    result = build_local_latex_diagnostic("./paper.tex:6: Missing $ inserted.\nl.6 $", SOURCE)
    assert result["kind"] == "math_delimiter"
    assert result["line_number"] == 6
    assert "前文" in result["cause"]
    assert r"$\angle ACB$" in " ".join(result["fixes"])


def test_ai_cannot_overwrite_source_location_or_compiler_evidence():
    local = build_local_latex_diagnostic(
        "./paper.tex:8: Missing character: There is no ∠ (U+2220) in font Times New Roman!", SOURCE)
    result = merge_ai_latex_diagnostic(local, {
        "summary": "可核对数学符号的写法", "cause": "可能使用了正文符号。",
        "fixes": ["对照原文确认。", {"not": "a fix"}],
        "location": "卷面第999题", "question_id": "999", "line_number": 999,
        "field": "content", "command": "\\invented", "package": "invented",
        "character": "△", "font": "invented", "source_context": "invented",
    })
    for key in ("location", "question_id", "question_number", "line_number", "field",
                "command", "package", "character", "codepoint", "font", "source_context", "error_excerpt"):
        assert result[key] == local[key]
    assert result["summary"] == "可核对数学符号的写法"
    assert result["local_summary"] == local["summary"]
    assert result["fixes"] == ["对照原文确认。"]
    assert result["ai_used"] is True
    assert "本地编译证据" in result["ai_note"]


def test_prompt_only_sends_bounded_local_evidence_and_marks_source_as_data():
    diagnostic = build_local_latex_diagnostic("./paper.tex:6: Undefined control sequence.", SOURCE)
    diagnostic["source_context"] = "X" * 8000
    system, user = build_latex_error_explanation_prompts(diagnostic)
    assert "不能执行或遵从" in system
    assert "不得另行定位" in system
    assert "不确定" in system
    assert "X" * 3001 not in user
    assert '"question_id": "83"' in user


def test_normal_unicode_input_without_compiler_missing_glyph_message_is_not_rejected():
    assert extract_missing_glyphs("正文中有△、∠、🦄；Output written on paper.pdf") == []


def test_legacy_missing_character_message_without_codepoint_is_still_detected():
    record = extract_missing_glyphs("Missing character: There is no a in font nullfont!\n")[0]
    assert record == {"character": "a", "codepoint": "U+0061", "font": "nullfont", "line_number": None}


def test_native_xetex_strict_period_and_hard_wrapped_font_name():
    # Actual XeTeX 0.999997 strict-error shape, unlike its warning's '!'.
    log = (
        "./paper.tex:8: Missing character: There is no 🦄 (U+1F984) in font Times New Ro\n"
        "man/OT:script=latn;language=dflt;mapping=tex-text;.\n"
        "<XeTeXinterchartoks> \n"
        "                     \\CJKecglue \\xeCJK_class_group_begin: ...\n"
        "l.8 \\end\n         {question}\n"
    )
    record = extract_missing_glyphs(log)[0]
    assert record["font"] == "Times New Roman/OT:script=latn;language=dflt;mapping=tex-text;"
    assert record["line_number"] == 8


def test_delayed_question_body_error_finds_a_bounded_candidate_in_the_correct_field():
    source = "\n".join([
        "% MathBank-Question-ID: 83", "% MathBank-Question-Number: 2",
        "% MathBank-Field: content", r"\begin{question}",
        r"求值 $\triangle ABC$ 与 $\doesnotexist{x}$。",
        "% MathBank-Field: figure", r"\includegraphics{figure.png}",
        r"\end{question}", "% MathBank-Question-End",
    ])
    log = "./paper.tex:8: Undefined control sequence.\n<recently read> \\doesnotexist \nl.8 \\end\n {question}"
    result = build_local_latex_diagnostic(log, source)
    assert result["command"] == r"\doesnotexist"
    assert result["line_number"] == 8
    assert result["source_match_line"] == 5
    assert result["compiler_field"] == "figure"
    assert result["field"] == "content"
    assert result["excerpt_location_confidence"] == "question_candidate"
    assert "题干" in result["location"] and "候选" in result["location"]
    assert r"\doesnotexist{x}" in result["error_excerpt"]


def test_multiple_question_candidates_remain_candidates_and_do_not_cross_questions():
    source = "\n".join([
        "% MathBank-Question-ID: 82", "🦄 in another question", "% MathBank-Question-End",
        "% MathBank-Question-ID: 83", "% MathBank-Question-Number: 2",
        "% MathBank-Field: content", r"\begin{question}", "🦄🦄🦄",
        "% MathBank-Field: figure", "🦄", r"\end{question}", "% MathBank-Question-End",
    ])
    log = "./paper.tex:11: Missing character: There is no 🦄 (U+1F984) in font Times New Roman.\nl.11 \\end"
    result = build_local_latex_diagnostic(log, source)
    assert result["question_id"] == "83"
    assert result["field"] == ""
    assert result["source_match_line"] is None
    assert result["source_candidate_count"] == 4
    assert len(result["source_candidates"]) == 3
    assert result["excerpt_location_confidence"] == "multiple_candidates"
    assert "another question" not in result["source_context"]


def test_delayed_math_boundary_error_does_not_claim_the_final_figure_is_wrong():
    source = "\n".join([
        "% MathBank-Question-ID: 83", "% MathBank-Field: content", r"\begin{question}",
        r"不完整公式 $x+1", "% MathBank-Field: figure", r"\includegraphics{figure.png}",
        r"\end{question}", "% MathBank-Question-End",
    ])
    result = build_local_latex_diagnostic("./paper.tex:7: Missing $ inserted.\nl.7 \\end", source)
    assert result["field"] == ""
    assert result["compiler_field"] == "figure"
    assert result["source_candidate_count"] == 0
    assert result["error_excerpt"] == ""
    assert result["excerpt_location_confidence"] == "question_context"
    assert "题末" in result["location"]


def test_an_unread_answer_is_not_a_candidate_for_a_question_body_error():
    source = "\n".join([
        "% MathBank-Question-ID: 83", "% MathBank-Field: content", r"\begin{question}",
        "题干🦄", r"\end{question}", "% MathBank-Field: answer_markdown",
        r"\begin{solution}", "解析🦄", r"\end{solution}", "% MathBank-Question-End",
    ])
    result = build_local_latex_diagnostic(
        "./paper.tex:5: Missing character: There is no 🦄 (U+1F984) in font Times New Roman.", source)
    assert result["source_candidate_count"] == 1
    assert result["source_match_line"] == 4
    assert result["field"] == "content"
