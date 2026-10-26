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

_FILE_ERROR = re.compile(
    r"(?m)^(?P<file>(?:[A-Za-z]:)?[^:\r\n]+\.(?:tex|sty|cls)):(?P<line>\d+):[ \t]*(?P<error>[^\r\n]+)"
)
_MISSING_GLYPH = re.compile(
    r"Missing character:\s*There is no\s+(?P<character>.*?)\s*"
    r"(?:\((?P<codepoint>U\+[0-9A-Fa-f]{4,8})\)\s*)?in font\s+",
    re.DOTALL,
)
_FIELD_LABELS = {"content": "题干", "answer_markdown": "参考答案与解析",
                 "figure": "插图", "solution_space": "作答留白"}


def extract_missing_glyphs(log_text: str) -> list[dict[str, Any]]:
    """Read actual XeTeX glyph-loss messages, without guessing from Unicode.

    XeTeX wraps font specifications at the log line width. A normal warning
    has no source location; only a file-line error or its own strict-error
    ``l.N`` context can supply one. Duplicated stdout/log evidence is deduped.
    """
    log = str(log_text or "")
    matches = list(_MISSING_GLYPH.finditer(log))
    results: list[dict[str, Any]] = []
    seen = set()
    for index, match in enumerate(matches):
        limit = min(len(log), match.end() + 1200)
        if index + 1 < len(matches):
            limit = min(limit, matches[index + 1].start())
        after = log[match.end():limit]
        terminal = re.search(r"[!.][ \t]*(?=\r?$)", after, re.MULTILINE)
        terminator = terminal.start() if terminal else -1
        # Normal warnings end in '!', strict errors end in '.'. Both may hard
        # wrap in the middle of the font name. Never consume TeX's next context.
        font = after[:terminator] if terminator >= 0 else after.splitlines()[0] if after else ""
        font = "".join(font.splitlines()).strip()[:500]
        line_start = log.rfind("\n", 0, match.start()) + 1
        prefix = log[line_start:match.start()]
        file_location = _FILE_ERROR.match(log, line_start)
        line_number = None
        if file_location and file_location.start() <= match.start() < file_location.end():
            filename = file_location.group("file").replace("\\", "/").rsplit("/", 1)[-1]
            if filename == "paper.tex":
                line_number = int(file_location.group("line"))
        elif prefix.strip() == "!":
            # A warning followed later by an unrelated error's l.N must not
            # inherit that location. Only strict Missing-character errors do.
            strict_context = after[terminal.end():] if terminal else after
            strict_context = re.split(r"(?m)^! |^.*\.(?:tex|sty|cls):\d+:", strict_context, maxsplit=1)[0]
            source_line = re.search(r"(?m)^l\.(\d+)(?:\s|$)", strict_context)
            if source_line:
                line_number = int(source_line.group(1))
        codepoint = (match.group("codepoint") or "").upper()
        character = match.group("character").strip()
        if not codepoint and len(character) == 1:
            codepoint = f"U+{ord(character):04X}"
        if len(character) > 8:
            try:
                character = chr(int(codepoint[2:], 16))
            except (ValueError, OverflowError):
                character = character[:8]
        key = (character, codepoint, font, line_number)
        if key in seen:
            continue
        seen.add(key)
        results.append({"character": character, "codepoint": codepoint,
                        "font": font, "line_number": line_number})
    return results


def _first_error_block(log_text: str) -> tuple[str, int | None, str]:
    log = str(log_text or "")

    # With ``-file-line-error`` XeLaTeX reports the actionable error as
    # ``./paper.tex:42: Undefined control sequence.`` instead of beginning
    # the line with ``!``. Prefer that format so nearby class/package messages
    # cannot steal the diagnosis.
    file_error = _FILE_ERROR.search(log)
    if file_error:
        filename = file_error.group("file").replace("\\", "/").rsplit("/", 1)[-1]
        line_number = int(file_error.group("line")) if filename == "paper.tex" else None
        nearby = log[file_error.end():file_error.end() + 2000]
        source_line = re.search(rf"(?m)^l\.{line_number}\s*(.*)$", nearby) if line_number else None
        line_text = source_line.group(1).strip() if source_line else ""
        return file_error.group("error").strip(), line_number, line_text

    error_match = re.search(r"(?m)^!\s*(.+)$", log)
    error = error_match.group(1).strip() if error_match else "LaTeX 编译器返回了错误"

    search_from = error_match.end() if error_match else 0
    nearby = log[search_from:search_from + 3000]
    line_match = re.search(r"(?m)^l\.(\d+)\s*(.*)$", nearby)
    if not line_match:
        return error, None, ""
    return error, int(line_match.group(1)), line_match.group(2).strip()


def _source_details(tex_content: str, line_number: int | None, radius: int = 3) -> dict[str, str]:
    empty = {"source_context": "", "question_id": "", "question_number": "", "field": "", "error_excerpt": ""}
    if not line_number:
        return empty
    lines = str(tex_content or "").splitlines()
    if not lines or not 1 <= line_number <= len(lines):
        return empty
    line_index = line_number - 1
    start = max(0, line_index - radius)
    end = min(len(lines), line_index + radius + 1)
    details = dict(empty)
    explicit_ends = any(re.fullmatch(r"%\s*MathBank-Question-End\s*", line.strip()) for line in lines)
    for index, line in enumerate(lines[:line_index + 1]):
        stripped = line.strip()
        marker = re.fullmatch(r"%\s*MathBank-Question-ID:\s*(\d+)\s*", stripped)
        if marker:
            details.update(question_id=marker.group(1), question_number="", field="content")
            start = max(start, index)
        elif re.fullmatch(r"%\s*MathBank-Question-End\s*", stripped) or stripped == r"\end{document}":
            details.update(question_id="", question_number="", field="")
        elif details["question_id"]:
            number = re.fullmatch(r"%\s*MathBank-Question-Number:\s*(\d+)\s*", stripped)
            field = re.fullmatch(r"%\s*MathBank-Field:\s*([a-z_]+)\s*", stripped)
            if number:
                details["question_number"] = number.group(1)
            elif field and field.group(1) in _FIELD_LABELS:
                details["field"] = field.group(1)
            elif not explicit_ends and re.fullmatch(r"\\end\{(?:question|problem|solution)\}", stripped):
                # Legacy documents have no explicit scopes. An error after a
                # closed question cannot safely inherit that question's ID.
                details.update(question_id="", question_number="", field="")
    for index in range(line_index + 1, end):
        if re.match(r"%\s*MathBank-Question-(?:ID:|End)", lines[index].strip()):
            end = index
            break
    details["source_context"] = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))[:3000]
    details["error_excerpt"] = lines[line_index].strip()[:600]
    return details


def _source_context(tex_content: str, line_number: int | None, radius: int = 3) -> tuple[str, str]:
    details = _source_details(tex_content, line_number, radius)
    return details["source_context"], details["question_id"]


def _runaway_question_scope(log_text: str, tex_content: str, error: str) -> dict[str, Any] | None:
    """Locate only an unfinished captured exam environment, never a loose log marker.

    A missing closing group can make exam-zh consume the rest of the input
    without a source line. The last runtime scope is useful only for this exact
    scanning failure and only when its ID, number, field and environment agree
    with a complete generated scope in the source. It proves a question range,
    not a position inside the question or the meaning of a malformed formula.
    """
    environment_error = re.fullmatch(
        r"File ended while scanning use of\s+\\environment\s+(question|problem|solution)\s*\.?",
        error.strip(), re.IGNORECASE,
    )
    if not environment_error:
        return None
    log = str(log_text or "")
    runaway = re.search(r"(?m)^Runaway argument\?\s*\n(?P<context>[\s\S]*?)(?=^!|\Z)", log)
    fatal = re.search(r"(?m)^!\s*" + re.escape(error.strip()) + r"\s*$", log)
    if not runaway or not fatal or runaway.start() > fatal.start():
        return None
    active = None
    event_pattern = re.compile(
        r"(?m)^MathBank-Question-(?:Start: (?P<id>\d+) (?P<number>\d+) "
        r"(?P<field>content|answer_markdown)|Finish: (?P<finish>\d+))\s*$"
    )
    preserved_events = log.rsplit("\nMathBank compilation scope events:\n", 1)
    event_log = preserved_events[1] if len(preserved_events) == 2 else log[:fatal.start()]
    for event in event_pattern.finditer(event_log):
        if event.group("id"):
            active = (event.group("id"), event.group("number"), event.group("field"))
        elif active and event.group("finish") == active[0]:
            active = None
        else:
            # A mismatched finish is not evidence for any earlier question.
            active = None
    if not active:
        return None
    question_id, question_number, field = active
    environment = environment_error.group(1).lower()
    if (field == "answer_markdown") != (environment == "solution"):
        return None
    lines = str(tex_content or "").splitlines()
    id_line = f"% MathBank-Question-ID: {question_id}"
    starts = [index for index, line in enumerate(lines) if line.strip() == id_line]
    verified = []
    start_command = rf"\typeout{{MathBank-Question-Start: {question_id} {question_number} {field}}}"
    finish_command = rf"\typeout{{MathBank-Question-Finish: {question_id}}}"
    for start in starts:
        end = next((index for index in range(start + 1, len(lines))
                    if re.match(r"%\s*MathBank-Question-(?:ID:|End)", lines[index].strip())), len(lines))
        scope = [line.strip() for line in lines[start:end]]
        if (end == len(lines) or lines[end].strip() != "% MathBank-Question-End"
                or scope.count(f"% MathBank-Question-Number: {question_number}") != 1
                or scope.count(start_command) != 1 or scope.count(finish_command) != 1):
            continue
        runtime_start = start + scope.index(start_command)
        runtime_finish = start + scope.index(finish_command)
        if runtime_finish <= runtime_start:
            continue
        previous_field = next((line for line in reversed(scope[:scope.index(start_command)])
                               if line.startswith("% MathBank-Field:")), "")
        if previous_field != f"% MathBank-Field: {field}":
            continue
        following = next((line.strip() for line in lines[runtime_start + 1:runtime_finish]
                          if line.strip() and not line.strip().startswith("%")
                          and line.strip() != r"\mathbankbeginquestionbody"), "")
        if not re.match(r"\\begin\{" + environment + r"\}(?:\[|$)", following):
            continue
        field_end = next((index for index in range(runtime_start + 1, runtime_finish)
                          if re.fullmatch(r"\\typeout\{MathBank-Question-Start: \d+ \d+ (?:content|answer_markdown)\}",
                                          lines[index].strip())), runtime_finish)
        if rf"\end{{{environment}}}" not in [line.strip() for line in lines[runtime_start + 1:field_end]]:
            continue
        verified.append({
            "question_id": question_id, "question_number": question_number, "field": field,
            "source_context": "\n".join(f"{index + 1}: {lines[index]}"
                                          for index in range(runtime_start + 1, field_end))[:3000],
            "error_excerpt": ("Runaway argument?\n" + runaway.group("context").strip())[:600],
        })
    return verified[0] if len(verified) == 1 else None


def _find_command(error: str, line_text: str, context: str, line_number: int | None, log_text: str = "") -> str:
    if "undefined control sequence" in error.lower():
        error_start = log_text.lower().find("undefined control sequence")
        error_context = log_text[max(0, error_start):max(0, error_start) + 2400]
        error_context = re.split(r"(?m)^! |^.*\.(?:tex|sty|cls):\d+:", error_context, maxsplit=1)[0]
        recent = re.search(r"<recently read>\s*\\([A-Za-z@]+)", error_context)
        if recent:
            return recent.group(1)
        # TeX's l.N diagnostic stops immediately after the offending token.
        stopped = re.search(r"\\([A-Za-z@]+)\s*$", line_text)
        if stopped:
            return stopped.group(1)
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


def _question_token_candidates(tex_content: str, line_number: int | None,
                               question_id: str, token: str) -> tuple[list[dict[str, Any]], int, set[str], int, int]:
    """Find a log-confirmed token only inside its reliably located question.

    exam-zh captures a question body and can report all its errors at the end
    environment. Matching inside that proven scope supplies candidates, not a
    replacement compiler location. No token can establish a question's ID.
    """
    if not question_id or not line_number or not token:
        return [], 0, set(), 0, 0
    lines = str(tex_content or "").splitlines()
    if not 1 <= line_number <= len(lines):
        return [], 0, set(), 0, 0
    reported = line_number - 1
    start = -1
    for index in range(reported, -1, -1):
        if re.fullmatch(r"%\s*MathBank-Question-ID:\s*" + re.escape(question_id), lines[index].strip()):
            start = index
            break
        if re.fullmatch(r"%\s*MathBank-Question-End\s*", lines[index].strip()):
            return [], 0, set(), 0, 0
    if start < 0:
        return [], 0, set(), 0, 0
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"%\s*MathBank-Question-(?:ID:|End)", lines[index].strip()) or lines[index].strip() == r"\end{document}":
            end = index
            break
    # Material after the compiler's reported input position has not yet been
    # read. A later answer containing the same token is not evidence for an
    # error reported while finishing the question body.
    end = min(end, reported + 1)
    field = "content"
    candidates = []
    count = 0
    fields = set()
    pattern = re.compile(re.escape(token) + (r"(?![A-Za-z@])" if token.startswith("\\") else ""))
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        field_marker = re.fullmatch(r"%\s*MathBank-Field:\s*([a-z_]+)\s*", stripped)
        if field_marker and field_marker.group(1) in _FIELD_LABELS:
            field = field_marker.group(1)
        elif stripped == r"\begin{solution}":
            field = "answer_markdown"
        elif not stripped.startswith("%"):
            for match in pattern.finditer(lines[index]):
                count += 1
                fields.add(field)
                if len(candidates) >= 3:
                    continue
                excerpt_start = max(0, match.start() - 160)
                excerpt_end = min(len(lines[index]), match.end() + 240)
                candidates.append({"source_line": index + 1, "field": field,
                                   "source_column": match.start() + 1,
                                   "excerpt": lines[index][excerpt_start:excerpt_end].strip(), "token": token})
    return candidates, count, fields, start, end


def build_local_latex_diagnostic(log_text: str, tex_content: str) -> dict[str, Any]:
    """Return an immediately useful diagnosis without requiring an AI service."""
    error, line_number, line_text = _first_error_block(log_text)
    glyphs = extract_missing_glyphs(log_text)
    missing_glyph = bool(glyphs and ("missing character" in error.lower() or error == "LaTeX 编译器返回了错误"))
    if missing_glyph:
        line_number = glyphs[0]["line_number"]
    details = _source_details(tex_content, line_number)
    runtime_scope = _runaway_question_scope(str(log_text or ""), tex_content, error) if line_number is None and not glyphs else None
    if runtime_scope:
        details.update(runtime_scope)
    context, question_id = details["source_context"], details["question_id"]
    command = _find_command(error, line_text, context, line_number, str(log_text or ""))
    package = _COMMAND_PACKAGES.get(command, "")

    lower_log = str(log_text or "").lower()
    kind = "latex_error"
    if missing_glyph:
        kind = "missing_glyph"
        glyph = glyphs[0]
        shown_codepoint = f"（{glyph['codepoint']}）" if glyph["codepoint"] else ""
        summary = f"PDF 导出缺字：字符“{glyph['character']}”{shown_codepoint}无法显示"
        display_font = (glyph["font"] or "未知字体").split("/OT:", 1)[0]
        cause = f"编译器确认当前字体“{display_font}”没有这个字形；生成的 PDF 会丢失该字符。"
        fixes = [
            r"回到对应题目编辑数学片段：三角形使用 $\triangle ABC$，角使用 $\angle ACB$；数学公式须保留完整的 $...$ 定界符。",
            "其他缺失字符请先对照原题确认含义，再使用系统支持的等价数学写法；不要直接删除或猜换成相似符号。",
        ]
        command = ""
        package = ""
    elif "fontspec error" in lower_log and "cannot be found" in lower_log:
        kind = "missing_font"
        font_match = re.search(r'The font "([^"]+)" cannot be found', str(log_text), re.IGNORECASE)
        font_name = font_match.group(1) if font_match else "指定字体"
        summary = f"PDF 编译器找不到字体“{font_name}”"
        cause = "当前电脑没有安装模板指定的字体，或 XeLaTeX 无法识别该字体名称。"
        fixes = [
            "改用项目内置的 Fandol 中文字体配置后重新导出。",
            "如果必须使用该字体，请确认字体已安装，并刷新 TeX 字体缓存。",
        ]
    elif "undefined control sequence" in lower_log:
        kind = "undefined_command"
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
        kind = "missing_resource"
        summary = "PDF 编译时缺少所需的文件或宏包"
        cause = "公式、图片或模板引用了当前编译环境中不存在的资源。"
        fixes = ["检查报错行引用的文件名。", "确认所需宏包或图片已经随项目离线内置。"]
    elif "missing $ inserted" in lower_log or "extra }, or forgotten $" in lower_log or "bad math environment delimiter" in lower_log:
        kind = "math_delimiter"
        summary = "数学公式的定界符或花括号不配对"
        cause = "编译器在此处无法确定正文与数学公式的边界；真正缺失的定界符也可能位于前文。"
        fixes = [
            r"检查标出的片段及其前文，行内公式使用完整的 $...$，例如 $\triangle ABC$、$\angle ACB$。",
            r"检查 $、\(...\)、\[...\] 与花括号是否成对，保留原有数学含义后重新导出。",
        ]
    elif "missing {" in lower_log or "missing }" in lower_log or "runaway argument" in lower_log or "file ended while scanning" in lower_log:
        kind = "unbalanced_group"
        summary = "公式中的花括号或命令参数不完整"
        cause = "某个 LaTeX 命令缺少必需的 `{...}` 参数，编译器无法确定公式边界。"
        fixes = ["检查报错行附近的 `{` 与 `}` 是否成对。", "重点检查分式、根式、上下标和大型运算符。"]
        if runtime_scope:
            # Runtime boundaries prove only the unfinished question range.
            # Internal capture/typeout commands are not offending math tokens.
            command = ""
            package = ""
            cause = "编译器读取这道题的环境时遇到未闭合参数，已确定题目范围，但没有提供准确出错行。"
            fixes = ["检查下方题目范围及编译器读到的片段，核对 `{` 与 `}` 是否成对。",
                     "重点检查分式、根式、上下标和大型运算符；对照原题确认后再修改。"]
    else:
        summary = "PDF 编译器发现了 LaTeX 排版错误"
        cause = error.rstrip(".") + "。"
        fixes = ["检查下方标出的源码位置。", "若是导入公式，请回到对应题目核对公式结构。"]

    compiler_field = details["field"]
    token = glyphs[0]["character"] if missing_glyph else f"\\{command}" if kind == "undefined_command" and command else ""
    candidates, candidate_count, candidate_fields, scope_start, scope_end = _question_token_candidates(
        tex_content, line_number, question_id, token)
    source_match_line = None
    excerpt_confidence = "source_line" if line_number and details["error_excerpt"] else "unlocated"
    if runtime_scope:
        excerpt_confidence = "compiler_context"
    candidate_note = ""
    if candidates:
        source_match_line = candidates[0]["source_line"] if candidate_count == 1 else None
        details["error_excerpt"] = candidates[0]["excerpt"]
        if candidate_count == 1:
            details["field"] = candidates[0]["field"]
            if source_match_line == line_number:
                excerpt_confidence = "source_line"
            else:
                excerpt_confidence = "question_candidate"
                candidate_note = "（题内符号/命令候选；编译器在其他行报告）"
        else:
            details["field"] = next(iter(candidate_fields)) if len(candidate_fields) == 1 else ""
            excerpt_confidence = "multiple_candidates"
            candidate_note = f"（题内有 {candidate_count} 处候选，具体出错处待核对）"
        # Show the candidate's bounded original neighborhood, rather than an
        # unhelpful end-environment line. This remains within the proven scope.
        lines = str(tex_content or "").splitlines()
        center = candidates[0]["source_line"] - 1
        details["source_context"] = "\n".join(
            f"{index + 1}: {lines[index]}" for index in range(max(scope_start, center - 2), min(scope_end, center + 3))
        )[:3000]
        context = details["source_context"]
    elif question_id and re.fullmatch(r"\\end\{(?:question|problem|solution|minipage)\}", details["error_excerpt"]):
        details["field"] = ""
        details["error_excerpt"] = ""
        excerpt_confidence = "question_context"
        candidate_note = "（编译器在题末或排版块结束时报告，请检查前面的公式）"

    location = "试卷模板或公式源码"
    if question_id:
        number = f"卷面第 {details['question_number']} 题，" if details["question_number"] else ""
        field_label = _FIELD_LABELS.get(details["field"], "题文")
        location = f"{number}题库题目 ID {question_id} 的{field_label}附近{candidate_note}"
        if runtime_scope:
            location = f"{number}题库题目 ID {question_id} 的{field_label}范围（题范围定位，编译器未提供准确行号）"
    elif line_number:
        location = f"生成的 LaTeX 第 {line_number} 行附近（题目归属待核对）"
    elif glyphs:
        location = "缺字位置待核对（编译日志未提供可靠的题目行号）"

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
        "kind": kind,
        "question_number": details["question_number"],
        "field": details["field"],
        "error_excerpt": details["error_excerpt"],
        "compiler_field": compiler_field,
        "source_match_line": source_match_line,
        "source_candidates": candidates[:3],
        "source_candidate_count": candidate_count,
        "excerpt_location_confidence": excerpt_confidence,
        "location_confidence": "runtime_scope" if runtime_scope else "source_line" if question_id else "line_only" if line_number else "unlocated",
        "missing_glyphs": glyphs[:20],
        "character": glyphs[0]["character"] if missing_glyph else "",
        "codepoint": glyphs[0]["codepoint"] if missing_glyph else "",
        "font": glyphs[0]["font"] if missing_glyph else "",
        "ai_used": False,
    }


def merge_ai_latex_diagnostic(local: dict[str, Any], ai_value: Any) -> dict[str, Any]:
    """Merge only bounded, user-facing fields from a model response."""
    if not isinstance(ai_value, dict):
        return local
    merged = dict(local)
    accepted = False
    for key in ("summary", "cause"):
        value = ai_value.get(key)
        if isinstance(value, str) and value.strip():
            merged[f"local_{key}"] = local.get(key, "")
            merged[key] = value.strip()[:1000]
            accepted = True
    fixes = ai_value.get("fixes")
    if isinstance(fixes, list):
        safe_fixes = [item.strip()[:1000] for item in fixes if isinstance(item, str) and item.strip()]
        if safe_fixes:
            merged["local_fixes"] = local.get("fixes", [])
            merged["fixes"] = safe_fixes[:5]
            accepted = True
    if accepted:
        merged["ai_used"] = True
        merged["ai_note"] = "AI 补充解释；题号、字段及错误位置以本地编译证据为准，修改前请对照原题核实。"
    return merged
