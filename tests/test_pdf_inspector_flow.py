# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from mathbank import pdf_inspector_helper
from mathbank.prompts import build_pdf_parse_system_prompt


def _page(page: int, markdown: str, needs_ocr: bool = False):
    return SimpleNamespace(page=page, markdown=markdown, needs_ocr=needs_ocr)


def test_pdf_inspector_helper_fallback_when_not_installed():
    """未安装探测器且 PyMuPDF 无法可靠读取时，应安全回到视觉 OCR。"""
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", False), \
         patch("mathbank.pdf_inspector_helper._extract_fitz_pages", return_value=[]):
        result = pdf_inspector_helper.inspect_and_extract_pdf(b"%PDF-1.4 mock")
    assert result["available"] is False
    assert result["is_text_based"] is False
    assert result["markdown"] is None


def test_pdf_inspector_extracts_selected_pages_in_requested_order():
    """原生 PDF 只提取用户所选页面，并保留真实页码与顺序。"""
    inspector = SimpleNamespace(extract_pages_markdown=MagicMock(return_value=SimpleNamespace(
        pages=[
            _page(1, "第二页：1. 已知函数 f(x)=x²，求其最小值。"),
            _page(3, "第四页：2. 已知集合 A 与 B，求 A∩B。"),
        ]
    )))
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", inspector):
        result = pdf_inspector_helper.inspect_and_extract_pdf(
            b"%PDF-1.4 mock", page_indices=[1, 3]
        )

    inspector.extract_pages_markdown.assert_called_once_with(ANY, pages=[1, 3])
    assert result["pdf_type"] == "text_based"
    assert [page["page_index"] for page in result["pages"]] == [1, 3]
    assert "MATHBANK_PDF_PAGE:2" in result["markdown"]
    assert "MATHBANK_PDF_PAGE:4" in result["markdown"]


def test_pdf_inspector_routes_only_unreliable_pages_to_ocr():
    """混合 PDF 应保留可靠页文本，只把明确不可靠的页面交给 OCR。"""
    inspector = SimpleNamespace(extract_pages_markdown=MagicMock(return_value=SimpleNamespace(
        pages=[
            _page(0, "第 1 页原生题目文本，包含足够的可复制数学内容。"),
            _page(1, "", needs_ocr=True),
            _page(2, "第 3 页原生选项文本，继续上一页尚未结束的题目。"),
        ]
    )))
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", inspector):
        result = pdf_inspector_helper.inspect_and_extract_pdf(
            b"%PDF-1.4 mixed", page_indices=[0, 1, 2]
        )

    assert result["pdf_type"] == "mixed"
    assert result["is_text_based"] is False
    assert result["pages_needing_ocr"] == [1]
    assert result["pages"][0]["needs_ocr"] is False
    assert result["pages"][2]["needs_ocr"] is False


def test_short_native_title_page_is_not_forced_to_ocr():
    """字符很少但被探测器判定可靠的标题页仍走原生提取。"""
    inspector = SimpleNamespace(extract_pages_markdown=MagicMock(return_value=SimpleNamespace(
        pages=[_page(0, "数学试卷")]
    )))
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", inspector):
        result = pdf_inspector_helper.inspect_and_extract_pdf(b"%PDF-1.4 title")
    assert result["is_text_based"] is True
    assert result["pages_needing_ocr"] == []


def test_empty_per_page_result_keeps_legacy_text_path():
    """逐页 API 无结果时仍可沿用旧版整卷直提，避免升级造成回归。"""
    inspector = SimpleNamespace(
        extract_pages_markdown=MagicMock(return_value=SimpleNamespace(pages=[])),
        process_pdf=MagicMock(return_value=SimpleNamespace(
            pdf_type="TextBased",
            markdown="高中数学试卷\n1. 已知函数 f(x)=x^2，请求出它在给定区间上的最小值。",
            has_encoding_issues=False,
            pages_needing_ocr=[],
            confidence=0.99,
        )),
    )
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", inspector):
        result = pdf_inspector_helper.inspect_and_extract_pdf(
            b"%PDF-1.4 legacy", page_indices=[0]
        )
    assert result["is_text_based"] is True
    assert "已知函数" in result["markdown"]


def test_cross_page_text_is_merged_without_question_terminator():
    """分页文本只加入空行，不加入会截断跨页题的结束符。"""
    merged = pdf_inspector_helper.merge_pdf_page_texts([
        "<!-- MATHBANK_PDF_PAGE:2 -->\n7. 已知函数 f(x)",
        "<!-- MATHBANK_PDF_PAGE:3 -->\n的图像如下，则",
    ])
    assert "f(x)\n\n<!-- MATHBANK_PDF_PAGE:3 -->\n的图像如下" in merged
    assert "7. 已知函数" in merged


def test_pdf_parse_system_prompt_includes_formula_and_cross_page_rules():
    prompt = build_pdf_parse_system_prompt({"必修一": {"集合": []}}, False)
    assert "\\sqrt{...}" in prompt
    assert "\\frac{...}{...}" in prompt
    assert "\\fillin" in prompt
    assert "MATHBANK_PDF_PAGE:N" in prompt
    assert "必须按上下文合并为同一道完整题目" in prompt


def test_pdf_inspector_detects_formula_loss_and_triggers_ocr_fallback():
    """当提取出的文本存在 Word/MathType 公式丢失特征时，应自动触发 OCR 降级。"""
    # 模拟包含空选择题选项 A. B. C. D. 与孤立句式的 Markdown 文本
    loss_markdown = (
        "1. 设 ， 为非零向量，则“存在负数 ，使得 ”是“ ”的()\n"
        "A. 充分而不必要条件 B. 必要而不充分条件 C. 充分必要条件 D. 既不充分也不必要条件\n"
        "2. 设非零向量 ， 满足 ，则()\n"
        "A. B. C. D."
    )
    inspector = SimpleNamespace(extract_pages_markdown=MagicMock(return_value=SimpleNamespace(
        pages=[_page(0, loss_markdown, needs_ocr=False)]
    )))
    with patch("mathbank.pdf_inspector_helper._PDF_INSPECTOR_AVAILABLE", True), \
         patch("mathbank.pdf_inspector_helper.pdf_inspector", inspector):
        result = pdf_inspector_helper.inspect_and_extract_pdf(b"%PDF-1.4 formula_loss")

    assert result["is_text_based"] is False
    assert result["pages_needing_ocr"] == [0]
    assert result["pages"][0]["needs_ocr"] is True


def test_pdf_parsing_force_ocr_strategy():
    """当指定 pdf_strategy="force_ocr" 时，应绕过原生提取并强制发起视觉转译。"""
    from main import run_pdf_parsing_task, PDF_TASKS
    import fitz

    # 创建简易单页 PDF
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    pdf_bytes = doc.tobytes()

    with patch("main.ocr_pdf_page_image", return_value="1. 测验题 1+1=2"), \
         patch("main.parse_paper_text_internal", return_value=[{"content": "1. 测验题 1+1=2", "answer_markdown": ""}]):
        task_id = "test-force-ocr-task"
        run_pdf_parsing_task(
            task_id,
            pdf_bytes,
            "test.pdf",
            generate_answers=False,
            page_range=None,
            pdf_strategy="force_ocr"
        )
        task = PDF_TASKS.get(task_id)
        assert task is not None
        assert task["status"] == "completed"


