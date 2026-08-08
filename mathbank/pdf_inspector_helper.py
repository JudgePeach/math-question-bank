# mathbank/pdf_inspector_helper.py
# -*- coding: utf-8 -*-
"""
pdf-inspector 高性能 PDF 检查与文本提取适配器模块。
负责检测 PDF 是否为原生文本（TextBased）或扫描件（Scanned），并在原生 PDF 场景下
提供毫秒级的结构化 Markdown/排版提取，减少不必要的多模态视觉 OCR 成本与延迟。
"""

import os
import tempfile
from typing import Dict, Any, Optional

try:
    import pdf_inspector
    _PDF_INSPECTOR_AVAILABLE = True
except ImportError:
    pdf_inspector = None
    _PDF_INSPECTOR_AVAILABLE = False


try:
    import fitz
    _FITZ_AVAILABLE = True
except ImportError:
    fitz = None
    _FITZ_AVAILABLE = False


def is_pdf_inspector_available() -> bool:
    """检查当前 Python 环境是否安装了原生矢量直提引擎 (pdf-inspector)"""
    return _PDF_INSPECTOR_AVAILABLE


def _extract_fitz_text(file_bytes: bytes) -> Optional[str]:
    """使用 PyMuPDF 高精度提取含有 CID/中文字体映射的原生试卷文本"""
    if not _FITZ_AVAILABLE:
        return None
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages_text = []
        for idx, page in enumerate(doc):
            t = page.get_text("text")
            if t and t.strip():
                pages_text.append(f"<!-- Page {idx + 1} -->\n" + t.strip())
        if pages_text:
            return "\n\n".join(pages_text)
    except Exception:
        pass
    return None


def inspect_and_extract_pdf(
    file_bytes: bytes,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    使用 pdf-inspector 与 PyMuPDF 双核适配器快速探测 PDF 类型并在原生试卷场景下提取精准文本。
    
    返回字典结构:
    {
        "available": bool,        # 原生直提引擎是否可用
        "pdf_type": str,          # "text_based", "scanned", "image_based", "mixed", "unknown"
        "is_text_based": bool,    # 是否可走极速原生文本直提通道
        "markdown": Optional[str], # 提取出的排版与文本内容（若是原生 PDF）
        "error": Optional[str]    # 错误或异常信息（若有）
    }
    """
    if not _PDF_INSPECTOR_AVAILABLE:
        # 当 pdf_inspector 未安装时，若 fitz 可提取丰富文本也支持直提
        fitz_text = _extract_fitz_text(file_bytes)
        if fitz_text and len(fitz_text.strip()) >= 50:
            return {
                "available": False,
                "pdf_type": "text_based",
                "is_text_based": True,
                "markdown": fitz_text,
                "error": None
            }
        return {
            "available": False,
            "pdf_type": "unknown",
            "is_text_based": False,
            "markdown": None,
            "error": "pdf-inspector is not installed"
        }

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(file_bytes)
            tmp_path = tf.name

        result = pdf_inspector.process_pdf(tmp_path)
        raw_type = str(getattr(result, "pdf_type", "")).lower().replace("-", "_")
        markdown_text = getattr(result, "markdown", None)
        has_encoding_issues = getattr(result, "has_encoding_issues", False)

        # 检查 pdf_type 是否属于 TextBased
        is_type_text = "text_based" in raw_type or "textbased" in raw_type
        
        # 决策文本提取来源:
        # 1. 若 pdf_inspector 的 markdown 良好且无编码缺陷，优先采用
        if is_type_text and markdown_text and len(markdown_text.strip()) >= 30 and not has_encoding_issues:
            return {
                "available": True,
                "pdf_type": "text_based",
                "is_text_based": True,
                "markdown": markdown_text,
                "error": None
            }
            
        # 2. 若存在中文字体编码问题或排版特殊，但通过 fitz 能直接提取出真实文本
        fitz_text = _extract_fitz_text(file_bytes)
        if (is_type_text or (fitz_text and len(fitz_text.strip()) >= 50)) and fitz_text and len(fitz_text.strip()) >= 50:
            return {
                "available": True,
                "pdf_type": "text_based",
                "is_text_based": True,
                "markdown": fitz_text,
                "error": None
            }

        is_text = is_type_text and bool(markdown_text and len(markdown_text.strip()) >= 30)
        return {
            "available": True,
            "pdf_type": "text_based" if is_text else (raw_type or "scanned"),
            "is_text_based": is_text,
            "markdown": markdown_text if is_text else None,
            "error": None
        }
    except Exception as ex:
        fitz_text = _extract_fitz_text(file_bytes)
        if fitz_text and len(fitz_text.strip()) >= 50:
            return {
                "available": True,
                "pdf_type": "text_based",
                "is_text_based": True,
                "markdown": fitz_text,
                "error": None
            }
        return {
            "available": True,
            "pdf_type": "error",
            "is_text_based": False,
            "markdown": None,
            "error": str(ex)
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
