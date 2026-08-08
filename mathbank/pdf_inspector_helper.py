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


def is_pdf_inspector_available() -> bool:
    """检查当前 Python 环境是否安装了 pdf-inspector 库"""
    return _PDF_INSPECTOR_AVAILABLE


def inspect_and_extract_pdf(
    file_bytes: bytes,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    使用 pdf-inspector 快速探测 PDF 类型并在原生试卷场景下提取结构化文本。
    
    返回字典结构:
    {
        "available": bool,        # pdf-inspector 是否可用
        "pdf_type": str,          # "text_based", "scanned", "image_based", "mixed", "unknown"
        "is_text_based": bool,    # 是否可走极速原生文本直提通道
        "markdown": Optional[str], # 提取出的排版与文本内容（若是原生 PDF）
        "error": Optional[str]    # 错误或异常信息（若有）
    }
    """
    if not _PDF_INSPECTOR_AVAILABLE:
        return {
            "available": False,
            "pdf_type": "unknown",
            "is_text_based": False,
            "markdown": None,
            "error": "pdf-inspector is not installed"
        }

    tmp_path = None
    try:
        # pdf-inspector 接收文件路径进行高性能处理
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(file_bytes)
            tmp_path = tf.name

        # 调用 pdf-inspector 底层 Rust 极速解析器
        result = pdf_inspector.process_pdf(tmp_path)
        
        raw_type = str(getattr(result, "pdf_type", "")).lower().replace("-", "_")
        markdown_text = getattr(result, "markdown", None)
        
        # 判断是否可安全判定为 TextBased 原生文本 PDF
        is_text = ("text_based" in raw_type or "textbased" in raw_type) and bool(markdown_text and len(markdown_text.strip()) > 30)

        return {
            "available": True,
            "pdf_type": raw_type or "unknown",
            "is_text_based": is_text,
            "markdown": markdown_text if is_text else None,
            "error": None
        }
    except Exception as ex:
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
