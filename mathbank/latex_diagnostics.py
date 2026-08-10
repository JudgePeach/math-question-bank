"""Safe extraction and local explanation of XeLaTeX compile errors."""

from __future__ import annotations

import re
from typing import Any


_COMMAND_PACKAGES = {
    "cancel": "cancel",
    "bcancel": "cancel",
    "xcancel": "cancel",
    "cancelto": "cancel",
    "ce": "mhchem",
    "includegraphics": "graphicx",
    "qty": "siunitx",
    "SI": "siunitx",
    "si": "siunitx",
    "wideparen": "yhmath",
    "coloneqq": "mathtools",
    "xrightarrow": "amsmath",
    "xleftarrow": "amsmath",
    "xlongequal": "extarrows",
}


def _first_error_block(log_text: str) -> tuple[str, int | None, str]:
    log = str(log_text or "")

    # With ``-file-line-error`` XeLaTeX reports the actionable error as
    # ``./paper.tex:42: Undefined control sequence.`` instead of beginning
    # the line with ``!``. Prefer that format so nearby class/package messages
    # cannot steal the diagnosis.
    file_error = re.search(r"(?m)^(?:\./)?paper\.tex:(\d+):\s*(.+)$", log)
    if file_error:
        line_number = int(file_error.group(1))
        nearby = log[file_error.end():file_error.end() + 2000]
        source_line = re.search(rf"(?m)^l\.{line_number}\s*(.*)$", nearby)
        line_text = source_line.group(1).strip() if source_line else ""
        return file_error.group(2).strip(), line_number, line_text

    error_match = re.search(r"(?m)^!\s*(.+)$", log)
    error = error_match.group(1).strip() if error_match else "LaTeX 编译器返回了错误"

    search_from = error_match.end() if error_match else 0
    nearby = log[search_from:search_from + 3000]
    line_match = re.search(r"(?m)^l\.(\d+)\s*(.*)$", nearby)
    if not line_match:
        line_match = re.search(r"(?m)^.*?\.tex:(\d+):\s*(.*)$", log)
    if not line_match:
        return error, None, ""
    return error, int(line_match.group(1)), line_match.group(2).strip()


def _source_context(tex_content: str, line_number: int | None, radius: int = 3) -> tuple[str, str]:
    if not line_number:
        return "", ""
    lines = str(tex_content or "").splitlines()
    if not lines:
        return "", ""
    line_index = min(max(line_number - 1, 0), len(lines) - 1)
    start = max(0, line_index - radius)
    end = min(len(lines), line_index + radius + 1)
    context = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))

    question_id = ""
    marker = re.compile(r"^%\s*MathBank-Question-ID:\s*(\d+)\s*$")
    for index in range(line_index, -1, -1):
        match = marker.match(lines[index].strip())
        if match:
            question_id = match.group(1)
            break
    return context[:3000], question_id


def _find_command(error: str, line_text: str, context: str, line_number: int | None) -> str:
    candidates = [line_text]
    if line_number:
        exact_line = re.search(rf"(?m)^{line_number}:\s*(.*)$", context)
        if exact_line:
            candidates.append(exact_line.group(1))
    candidates.extend((error, context))
    for candidate in candidates:
        commands = re.findall(r"\\([A-Za-z@]+)", candidate or "")
        for command in commands:
            if command not in {"begin", "end", "documentclass", "usepackage"}:
                return command
    return ""


def build_local_latex_diagnostic(log_text: str, tex_content: str) -> dict[str, Any]:
    """Return an immediately useful diagnosis without requiring an AI service."""
    error, line_number, line_text = _first_error_block(log_text)
    context, question_id = _source_context(tex_content, line_number)
    command = _find_command(error, line_text, context, line_number)
    package = _COMMAND_PACKAGES.get(command, "")

    location = "试卷模板或公式源码"
    if question_id:
        location = f"题库题目 ID {question_id} 附近"
    elif line_number:
        location = f"生成的 LaTeX 第 {line_number} 行附近"

    lower_log = str(log_text or "").lower()
    if "fontspec error" in lower_log and "cannot be found" in lower_log:
        font_match = re.search(r'The font "([^"]+)" cannot be found', str(log_text), re.IGNORECASE)
        font_name = font_match.group(1) if font_match else "指定字体"
        summary = f"PDF 编译器找不到字体“{font_name}”"
        cause = "当前电脑没有安装模板指定的字体，或 XeLaTeX 无法识别该字体名称。"
        fixes = [
            "改用项目内置的 Fandol 中文字体配置后重新导出。",
            "如果必须使用该字体，请确认字体已安装，并刷新 TeX 字体缓存。",
        ]
    elif "undefined control sequence" in lower_log:
        shown_command = f"\\{command}" if command else "某个 LaTeX 命令"
        summary = f"公式中存在当前模板不认识的命令 {shown_command}"
        if package:
            cause = f"{shown_command} 通常由 {package} 宏包提供，但当前试卷模板没有加载它。"
            fixes = [
                f"在确认环境已内置该宏包后加载 \\usepackage{{{package}}}。",
                "也可以把该命令替换为当前 KaTeX 与 XeLaTeX 都支持的等价写法。",
            ]
        else:
            cause = "该命令可能拼写错误，也可能来自当前模板尚未加载的宏包。"
            fixes = [
                "检查命令拼写及左右花括号是否完整。",
                "确认提供该命令的宏包已经随项目离线内置并在模板中加载。",
            ]
    elif "file" in lower_log and "not found" in lower_log:
        summary = "PDF 编译时缺少所需的文件或宏包"
        cause = "公式、图片或模板引用了当前编译环境中不存在的资源。"
        fixes = ["检查报错行引用的文件名。", "确认所需宏包或图片已经随项目离线内置。"]
    elif "missing {" in lower_log or "missing }" in lower_log:
        summary = "公式中的花括号或命令参数不完整"
        cause = "某个 LaTeX 命令缺少必需的 `{...}` 参数，编译器无法确定公式边界。"
        fixes = ["检查报错行附近的 `{` 与 `}` 是否成对。", "重点检查分式、根式、上下标和大型运算符。"]
    else:
        summary = "PDF 编译器发现了 LaTeX 排版错误"
        cause = error.rstrip(".") + "。"
        fixes = ["检查下方标出的源码位置。", "若是导入公式，请回到对应题目核对公式结构。"]

    technical = "\n".join(part for part in (f"! {error}", f"l.{line_number} {line_text}" if line_number else "") if part)
    return {
        "summary": summary,
        "cause": cause,
        "location": location,
        "fixes": fixes,
        "package": package,
        "command": f"\\{command}" if command else "",
        "line_number": line_number,
        "question_id": question_id,
        "source_context": context,
        "technical_error": technical[:1600],
        "ai_used": False,
    }


def merge_ai_latex_diagnostic(local: dict[str, Any], ai_value: Any) -> dict[str, Any]:
    """Merge only bounded, user-facing fields from a model response."""
    if not isinstance(ai_value, dict):
        return local
    merged = dict(local)
    for key in ("summary", "cause", "location", "package", "command"):
        value = ai_value.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()[:1000]
    fixes = ai_value.get("fixes")
    if isinstance(fixes, list):
        safe_fixes = [str(item).strip()[:1000] for item in fixes if str(item).strip()]
        if safe_fixes:
            merged["fixes"] = safe_fixes[:5]
    merged["ai_used"] = True
    return merged
