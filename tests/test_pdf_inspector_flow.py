# tests/test_pdf_inspector_flow.py
# -*- coding: utf-8 -*-

from unittest.mock import patch, MagicMock
from mathbank import pdf_inspector_helper
from mathbank.prompts import build_pdf_parse_system_prompt


def test_pdf_inspector_helper_fallback_when_not_installed():
    """测试在未安装 pdf_inspector 时的安全优雅降级"""
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", False):
        res = pdf_inspector_helper.inspect_and_extract_pdf(b"%PDF-1.4 mock")
        assert res["available"] is False
        assert res["is_text_based"] is False
        assert res["markdown"] is None


def test_pdf_inspector_extracts_text_based_pdf():
    """测试在检测到 TextBased 原生 PDF 时能成功输出 Markdown"""
    mock_inspector = MagicMock()
    mock_result = MagicMock()
    mock_result.pdf_type = "TextBased"
    mock_result.markdown = "# 高中数学期末试卷\n\n1. 已知函数 $f(x) = x^2 - 1$，求 $f(2)$ 的值。"
    mock_inspector.process_pdf.return_value = mock_result

    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", mock_inspector):
        res = pdf_inspector_helper.inspect_and_extract_pdf(b"%PDF-1.4 mock")
        assert res["available"] is True
        assert res["is_text_based"] is True
        assert "已知函数" in res["markdown"]


def test_pdf_inspector_flags_scanned_pdf_for_vlm_fallback():
    """测试在检测到 Scanned 扫描件 PDF 时触发 VLM Fallback"""
    mock_inspector = MagicMock()
    mock_result = MagicMock()
    mock_result.pdf_type = "Scanned"
    mock_result.markdown = None
    mock_inspector.process_pdf.return_value = mock_result

    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", mock_inspector):
        res = pdf_inspector_helper.inspect_and_extract_pdf(b"%PDF-1.4 scanned")
        assert res["available"] is True
        assert res["is_text_based"] is False
        assert res["markdown"] is None


def test_pdf_parse_system_prompt_includes_formula_normalization():
    """测试 PDF 拆卷 Prompt 包含 Unicode 公式向 LaTeX 自愈规范"""
    prompt = build_pdf_parse_system_prompt({"必修一": {"集合": []}}, False)
    assert "\\sqrt{...}" in prompt
    assert "\\frac{...}{...}" in prompt
    assert "\\fillin" in prompt
