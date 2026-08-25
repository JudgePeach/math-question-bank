# mathbank/pdf_inspector_helper.py
# -*- coding: utf-8 -*-
"""pdf-inspector 的逐页 PDF 检查与文本提取适配器。"""

import os
import tempfile
from typing import Any, Dict, List, Optional

try:
    import pdf_inspector
    _PDF_INSPECTOR_AVAILABLE = True
except ImportError:
    pdf_inspector = None
    _PDF_INSPECTOR_AVAILABLE = False

try:
    import pymupdf as fitz
    _FITZ_AVAILABLE = True
except ImportError:
    fitz = None
    _FITZ_AVAILABLE = False


import re


def is_pdf_inspector_available() -> bool:
    """检查当前 Python 环境是否安装了 pdf-inspector。"""
    return _PDF_INSPECTOR_AVAILABLE


def _has_math_formula_loss(
    markdown: str,
    page_index: int = 0,
    pdf_path: Optional[str] = None,
) -> bool:
    """检测提取出的 Markdown 是否存在 Word/MathType 公式转换流失的特征。"""
    text = markdown or ""
    if not text.strip():
        return True

    # 1. 特征 A: 连续连写的空选择题选项 (如 A. B. C. D. 之间无实质表达式)
    if re.search(r"\bA\.\s*B\.\s*C\.\s*D\.", text):
        return True
    if re.search(r"\bA\.\s*[\n\r]+\s*B\.\s*[\n\r]+\s*C\.\s*[\n\r]+\s*D\.", text):
        return True

    # 特征 B: 丢失变量只留分隔逗号的句式 (如 "设 ， 为", "已知 ， ，", "对边分别为 ， ，")
    if re.search(r"(?:设|已知|若|满足)\s*[，,]\s*(?:[，,]\s*)*(?:为|满足|则|已知)", text):
        return True
    if re.search(r"对边分别为\s*[，,]\s*[，,]", text):
        return True
    if re.search(r"使得\s*”\s*是\s*“\s*”\s*的", text):
        return True

    # 2. 特征 C: 如果有 pdf_path 且能读取位置分布，检查是否存在大量的内联公式图片而 Markdown 无公式
    if pdf_path and _PDF_INSPECTOR_AVAILABLE:
        try:
            extract_positions = getattr(pdf_inspector, "extract_text_with_positions", None)
            if callable(extract_positions):
                items = extract_positions(pdf_path, pages=[page_index])
                image_items_count = sum(
                    1 for item in items if getattr(item, "item_type", "") == "image"
                )
                if image_items_count >= 3:
                    dollar_count = text.count("$")
                    if dollar_count < 4:
                        return True
        except Exception:
            pass

    return False



def _page_marker(page_index: int) -> str:
    """生成不会强制中断跨页题目的来源页标记。"""
    return f"<!-- MATHBANK_PDF_PAGE:{page_index + 1} -->"


def _extract_fitz_pages(
    file_bytes: bytes,
    page_indices: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """在 pdf-inspector 不可用时，按用户所选页码保序提取 PyMuPDF 文本。"""
    if not _FITZ_AVAILABLE:
        return []
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            selected = page_indices if page_indices is not None else list(range(len(doc)))
            pages = []
            for page_index in selected:
                if page_index < 0 or page_index >= len(doc):
                    continue
                text = doc.load_page(page_index).get_text("text").strip()
                pages.append({
                    "page_index": page_index,
                    "markdown": text,
                    "needs_ocr": len(text) < 30,
                    "source": "pymupdf",
                })
        return pages
    except Exception:
        return []


def _join_native_pages(pages: List[Dict[str, Any]]) -> str:
    """按原页序合并可靠文本；页标记仅追踪来源，不表示题目边界。"""
    chunks = []
    for page in pages:
        if page.get("needs_ocr"):
            continue
        text = str(page.get("markdown") or "").strip()
        if text:
            chunks.append(f"{_page_marker(int(page['page_index']))}\n{text}")
    return "\n\n".join(chunks)


def merge_pdf_page_texts(page_texts: List[Optional[str]]) -> str:
    """按页序连续合并文本，不把换页误当成一道题的结束。"""
    return "\n\n".join(str(text).strip() for text in page_texts if text and str(text).strip())


def _build_result(
    *,
    available: bool,
    pages: List[Dict[str, Any]],
    pdf_type: str,
    error: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    reliable_pages = [page for page in pages if not page.get("needs_ocr")]
    pages_needing_ocr = [
        int(page["page_index"])
        for page in pages
        if page.get("needs_ocr")
    ]
    return {
        "available": available,
        "pdf_type": pdf_type,
        "is_text_based": bool(pages) and len(reliable_pages) == len(pages),
        "markdown": _join_native_pages(pages) if reliable_pages else None,
        "pages": pages,
        "pages_needing_ocr": pages_needing_ocr,
        "confidence": confidence,
        "error": error,
    }


def inspect_and_extract_pdf(
    file_bytes: bytes,
    task_id: Optional[str] = None,
    page_indices: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """逐页提取可靠原生文本，并明确列出需要视觉 OCR 的页面。

    ``page_indices`` 使用 0 起始页码。返回的 ``pages`` 与调用者给出的页序一致；
    原生页和 OCR 页可在上层逐页混合，再整卷一次性拆题，从而保留跨页题。
    ``task_id`` 保留用于兼容既有调用。
    """
    del task_id
    if not _PDF_INSPECTOR_AVAILABLE:
        fitz_pages = _extract_fitz_pages(file_bytes, page_indices)
        if fitz_pages and all(not page["needs_ocr"] for page in fitz_pages):
            return _build_result(
                available=False,
                pages=fitz_pages,
                pdf_type="text_based",
            )
        return _build_result(
            available=False,
            pages=fitz_pages,
            pdf_type="unknown",
            error="pdf-inspector is not installed",
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_file.write(file_bytes)
            tmp_path = tmp_file.name

        extract_pages = getattr(pdf_inspector, "extract_pages_markdown", None)
        if callable(extract_pages):
            extracted = extract_pages(tmp_path, pages=page_indices)
            raw_pages = list(getattr(extracted, "pages", []) or [])
            pages = []
            for raw_page in raw_pages:
                page_index = int(getattr(raw_page, "page"))
                markdown = str(getattr(raw_page, "markdown", "") or "").strip()
                # pdf-inspector 的逐页结论是首要依据；短标题页不能仅因字符少就被误送 OCR。
                needs_ocr = bool(getattr(raw_page, "needs_ocr", False)) or not markdown
                if not needs_ocr and markdown:
                    if _has_math_formula_loss(markdown, page_index, tmp_path):
                        needs_ocr = True
                pages.append({
                    "page_index": page_index,
                    "markdown": markdown,
                    "needs_ocr": needs_ocr,
                    "source": "pdf-inspector",
                })

            if pages:
                ocr_count = sum(1 for page in pages if page["needs_ocr"])
                if ocr_count == 0:
                    pdf_type = "text_based"
                elif ocr_count == len(pages):
                    pdf_type = "scanned"
                else:
                    pdf_type = "mixed"
                return _build_result(
                    available=True,
                    pages=pages,
                    pdf_type=pdf_type,
                )
            # 极旧或异常版本若未返回逐页结果，继续走原有整卷 API，避免功能退化。

        # 兼容旧版 pdf-inspector：仍尊重用户页码范围，但只能整段返回。
        try:
            result = pdf_inspector.process_pdf(tmp_path, pages=page_indices)
        except TypeError:
            # 更早版本尚无 pages 参数，保留其原有整卷直提能力。
            result = pdf_inspector.process_pdf(tmp_path)
        raw_type = str(getattr(result, "pdf_type", "")).lower().replace("-", "_")
        markdown = str(getattr(result, "markdown", "") or "").strip()
        has_encoding_issues = bool(getattr(result, "has_encoding_issues", False))
        pages_needing_ocr = set(getattr(result, "pages_needing_ocr", []) or [])
        is_text_type = "text_based" in raw_type or "textbased" in raw_type
        if is_text_type and markdown and len(markdown) >= 30 and not has_encoding_issues and not pages_needing_ocr:
            legacy_page = page_indices[0] if page_indices else 0
            has_loss = _has_math_formula_loss(markdown, legacy_page, tmp_path)
            pages = [{
                "page_index": legacy_page,
                "markdown": markdown,
                "needs_ocr": has_loss,
                "source": "pdf-inspector-legacy",
            }]
            return _build_result(
                available=True,
                pages=pages,
                pdf_type="scanned" if has_loss else "text_based",
                confidence=getattr(result, "confidence", None),
            )
        return _build_result(
            available=True,
            pages=[],
            pdf_type=raw_type or "scanned",
        )
    except Exception as ex:
        # 探测器异常时保留旧有 PyMuPDF 兜底；但不覆盖探测器明确给出的编码异常。
        fitz_pages = _extract_fitz_pages(file_bytes, page_indices)
        if fitz_pages and all(not page["needs_ocr"] for page in fitz_pages):
            return _build_result(
                available=True,
                pages=fitz_pages,
                pdf_type="text_based",
                error=f"pdf-inspector failed, used PyMuPDF: {ex}",
            )
        return _build_result(
            available=True,
            pages=fitz_pages,
            pdf_type="error",
            error=str(ex),
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
