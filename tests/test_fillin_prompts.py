"""Fill-in formatting belongs to generation, never read-only adjudication."""

import pytest

from mathbank.prompts import (
    COMMON_OCR_PROMPT,
    FILLIN_STYLE_RULE,
    build_ai_solve_prompts,
    build_classification_system_prompt,
    build_import_parse_system_prompt,
    build_pdf_layout_prompt,
    build_pdf_page_vision_prompt,
    build_pdf_parse_system_prompt,
    build_pdf_region_vision_prompt,
    build_pdf_source_verification_prompt,
    build_single_ocr_figure_review_prompt,
    build_single_ocr_vision_prompt,
)


CURRICULUM = {"必修一": {"1. 集合": ["1.1 集合的概念"]}}


@pytest.mark.parametrize("prompt", [
    COMMON_OCR_PROMPT,
    build_single_ocr_vision_prompt(),
    build_import_parse_system_prompt(CURRICULUM, "tex"),
    build_import_parse_system_prompt(CURRICULUM, "markdown"),
])
def test_transcription_prompts_require_text_mode_fillin(prompt):
    assert prompt.count(FILLIN_STYLE_RULE) == 1
    assert r"正文态 `\fillin`" in prompt
    assert r"`$...$`、`$$...$$`、`\(...\)` 或 `\[...\]`" in prompt
    assert r"正确示例：`$x=$\fillin`" in prompt
    assert r"错误示例：`$\fillin$`、`$x=\fillin$`" in prompt
    assert "相邻的普通公式仍须有各自完整的数学定界符" in prompt
    assert r"已有 `\fillin` 的参数及闭合结构完整保留" in prompt
    assert "锁定公式及其 ID 优先原样保留，不受本规则改写" in prompt


@pytest.mark.parametrize("prompt", [
    build_pdf_page_vision_prompt({"page_index": 0}),
    build_pdf_region_vision_prompt([]),
    build_pdf_region_vision_prompt([], include_figures=False),
    build_pdf_parse_system_prompt(CURRICULUM, False),
    build_pdf_parse_system_prompt(CURRICULUM, True),
])
def test_pdf_prompts_keep_original_fillin_contract(prompt):
    # The restored upstream PDF prompt is intentionally frozen separately from
    # single-image OCR and Word/TeX/Markdown's newer text-mode fill-in rule.
    assert FILLIN_STYLE_RULE not in prompt
    assert r"\fillin" in prompt
    assert "锁定" in prompt


@pytest.mark.parametrize("question_type", [
    "single_choice", "multi_choice", "fill_in_blank", "detailed_answer", "unknown",
])
def test_solve_prompts_share_fillin_rule_without_rewriting_input(question_type):
    content = r"已知 $x=\fillin$，求 $x^2$。"
    system_prompt, user_prompt = build_ai_solve_prompts(question_type, content)
    assert system_prompt.count(FILLIN_STYLE_RULE) == 1
    assert user_prompt.endswith(content)


@pytest.mark.parametrize("prompt", [
    build_pdf_layout_prompt("原文", {"page_index": 0}),
    build_pdf_source_verification_prompt([]),
    build_single_ocr_figure_review_prompt({}),
    build_single_ocr_figure_review_prompt({}, preview=True),
    build_classification_system_prompt(CURRICULUM),
])
def test_read_only_prompts_do_not_request_fillin_reformatting(prompt):
    assert FILLIN_STYLE_RULE not in prompt
