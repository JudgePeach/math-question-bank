"""Safe, deterministic preprocessing for single-file TeX exam imports."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


MAX_TEX_BYTES = 5 * 1024 * 1024
_VERBATIM_ENVS = {"verbatim", "Verbatim", "lstlisting", "minted"}
_QUESTION_ENVS = {"problem", "exercise", "question", "example", "ti"}
_TABLE_ENVS = ("tabular", "tabularx", "longtable", "array")
_CORE_COMMANDS = {
    "begin", "end", "documentclass", "usepackage", "title", "author", "date",
    "item", "frac", "dfrac", "tfrac", "sqrt", "left", "right", "text", "textbf",
    "mathrm", "mathbf", "mathbb", "mathcal", "operatorname", "includegraphics",
}


@dataclass(frozen=True)
class MacroDefinition:
    name: str
    arg_count: int
    replacement: str
    original: str


def tex_asset_basename(value: str) -> str:
    """Return a stable basename for TeX, POSIX, or Windows-style asset paths."""
    normalized = str(value or "").strip().replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1]


def tex_asset_references_match(left: str, right: str) -> bool:
    """Match an uploaded asset to a TeX reference without unsafe substring guesses."""
    left_path = str(left or "").strip().replace("\\", "/").lstrip("./").lower()
    right_path = str(right or "").strip().replace("\\", "/").lstrip("./").lower()
    if not left_path or not right_path:
        return False
    if left_path == right_path:
        return True
    left_base = tex_asset_basename(left_path)
    right_base = tex_asset_basename(right_path)
    if left_base == right_base:
        return True
    left_stem = left_base.rsplit(".", 1)[0]
    right_stem = right_base.rsplit(".", 1)[0]
    return bool(left_stem and left_stem == right_stem)


def _is_escaped(value: str, index: int) -> bool:
    slashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        slashes += 1
        cursor -= 1
    return slashes % 2 == 1


def _balanced_group(value: str, start: int) -> Optional[tuple[str, int]]:
    if start >= len(value) or value[start] != "{":
        return None
    depth = 0
    cursor = start
    while cursor < len(value):
        char = value[cursor]
        if char == "{" and not _is_escaped(value, cursor):
            depth += 1
        elif char == "}" and not _is_escaped(value, cursor):
            depth -= 1
            if depth == 0:
                return value[start + 1:cursor], cursor + 1
        cursor += 1
    return None


def decode_tex_bytes(file_bytes: bytes) -> tuple[str, dict]:
    if not file_bytes:
        raise ValueError("TeX 文件为空。")
    if len(file_bytes) > MAX_TEX_BYTES:
        raise ValueError("TeX 文件过大，请上传 5MB 以内的单文件试卷源码。")

    diagnostics = {"encoding": "", "encoding_fallback": False}
    attempts: list[tuple[str, str]] = []
    if file_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        attempts.append(("utf-16", "utf-16"))
    elif len(file_bytes) >= 8:
        even_nuls = file_bytes[0:200:2].count(0)
        odd_nuls = file_bytes[1:200:2].count(0)
        if odd_nuls > max(2, even_nuls * 3):
            attempts.append(("utf-16-le", "utf-16-le"))
        elif even_nuls > max(2, odd_nuls * 3):
            attempts.append(("utf-16-be", "utf-16-be"))
    attempts.extend((("utf-8-sig", "utf-8-sig"), ("gb18030", "gb18030")))

    for label, encoding in attempts:
        try:
            decoded = file_bytes.decode(encoding)
            diagnostics["encoding"] = label
            diagnostics["encoding_fallback"] = label == "gb18030"
            return decoded.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n"), diagnostics
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别 TeX 文件编码；请将文件另存为 UTF-8 或 GB18030 后重试。")


def strip_tex_comments(source: str) -> tuple[str, int]:
    output: list[str] = []
    removed = 0
    active_verbatim: Optional[str] = None
    for line in source.splitlines(keepends=True):
        if active_verbatim:
            output.append(line)
            if re.search(r"\\end\s*\{" + re.escape(active_verbatim) + r"\}", line):
                active_verbatim = None
            continue

        verbatim_match = re.search(r"\\begin\s*\{([^}]+)\}", line)
        if verbatim_match and verbatim_match.group(1) in _VERBATIM_ENVS:
            active_verbatim = verbatim_match.group(1)
            output.append(line)
            continue

        cut = None
        index = 0
        while index < len(line):
            char = line[index]
            if char == "\\" and line.startswith(r"\verb", index) and not _is_escaped(line, index):
                delimiter_index = index + len(r"\verb")
                if delimiter_index < len(line) and line[delimiter_index] == "*":
                    delimiter_index += 1
                if delimiter_index < len(line) and not line[delimiter_index].isspace():
                    delimiter = line[delimiter_index]
                    verb_end = line.find(delimiter, delimiter_index + 1)
                    if verb_end >= 0:
                        index = verb_end + 1
                        continue
            if char == "%" and not _is_escaped(line, index):
                cut = index
                break
            index += 1
        if cut is None:
            output.append(line)
            continue
        removed += 1
        newline = "\n" if line.endswith("\n") else ""
        output.append(line[:cut].rstrip() + newline)
    return "".join(output), removed


def _command_group(source: str, command: str) -> Optional[str]:
    for match in re.finditer(r"\\" + re.escape(command) + r"\b", source):
        cursor = match.end()
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        group = _balanced_group(source, cursor)
        if group:
            return group[0]
    return None


def _clean_title(value: str) -> str:
    text = value
    unwrap_commands = ("textbf", "textit", "textrm", "textsf", "heiti", "kaishu", "songti", "fangsong")
    for _ in range(5):
        changed = False
        for command in unwrap_commands:
            pattern = re.compile(r"\\" + command + r"\s*\{([^{}]*)\}")
            text, count = pattern.subn(r"\1", text)
            changed = changed or bool(count)
        if not changed:
            break
    text = re.sub(r"\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge|bfseries|itshape|centering)\b", "", text)
    text = text.replace("~", " ").replace("\\\\", "\n")
    text = re.sub(r"[{}]", "", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return next((line[:100] for line in lines if line), "")


def extract_tex_title(source: str) -> str:
    for command in ("title", "chead", "lhead", "rhead"):
        value = _command_group(source, command)
        if value:
            cleaned = _clean_title(value)
            if cleaned and "页" not in cleaned and "绝密" not in cleaned:
                return cleaned

    top = source[:4000]
    center = re.search(r"\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}", top)
    if center:
        cleaned = _clean_title(center.group(1))
        if cleaned:
            return cleaned
    return ""


def _parse_macro_definitions(preamble: str) -> tuple[list[MacroDefinition], list[str]]:
    definitions: list[MacroDefinition] = []
    warnings: list[str] = []
    command_pattern = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\*?\s*")
    for match in command_pattern.finditer(preamble):
        cursor = match.end()
        name = ""
        if cursor < len(preamble) and preamble[cursor] == "{":
            name_group = _balanced_group(preamble, cursor)
            if not name_group:
                continue
            name_match = re.fullmatch(r"\s*\\([A-Za-z@]+)\s*", name_group[0])
            if not name_match:
                continue
            name = name_match.group(1)
            cursor = name_group[1]
        else:
            name_match = re.match(r"\\([A-Za-z@]+)", preamble[cursor:])
            if not name_match:
                continue
            name = name_match.group(1)
            cursor += name_match.end()

        while cursor < len(preamble) and preamble[cursor].isspace():
            cursor += 1
        arg_count = 0
        if cursor < len(preamble) and preamble[cursor] == "[":
            end = preamble.find("]", cursor + 1)
            if end < 0:
                continue
            try:
                arg_count = int(preamble[cursor + 1:end].strip())
            except ValueError:
                arg_count = 99
            cursor = end + 1
            while cursor < len(preamble) and preamble[cursor].isspace():
                cursor += 1
            if cursor < len(preamble) and preamble[cursor] == "[":
                warnings.append(f"\\{name} 含可选默认参数，未自动展开")
                continue
        replacement = _balanced_group(preamble, cursor)
        if not replacement:
            continue
        original = preamble[match.start():replacement[1]]
        definitions.append(MacroDefinition(name, arg_count, replacement[0], original))

    def_pattern = re.compile(r"\\def\s*\\([A-Za-z@]+)((?:\s*#\d)*)\s*")
    for match in def_pattern.finditer(preamble):
        cursor = match.end()
        replacement = _balanced_group(preamble, cursor)
        if not replacement:
            continue
        params = [int(value) for value in re.findall(r"#(\d)", match.group(2))]
        arg_count = max(params, default=0)
        original = preamble[match.start():replacement[1]]
        definitions.append(MacroDefinition(match.group(1), arg_count, replacement[0], original))
    return definitions[:100], warnings


def _expand_one_arg_macro(source: str, macro: MacroDefinition) -> tuple[str, int]:
    pattern = re.compile(r"\\" + re.escape(macro.name) + r"(?![A-Za-z@])")
    output: list[str] = []
    cursor = 0
    expanded = 0
    while True:
        match = pattern.search(source, cursor)
        if not match:
            output.append(source[cursor:])
            break
        arg_start = match.end()
        while arg_start < len(source) and source[arg_start].isspace():
            arg_start += 1
        group = _balanced_group(source, arg_start)
        if not group:
            output.append(source[cursor:match.end()])
            cursor = match.end()
            continue
        output.append(source[cursor:match.start()])
        output.append(macro.replacement.replace("#1", group[0]))
        cursor = group[1]
        expanded += 1
    return "".join(output), expanded


def expand_safe_macros(body: str, definitions: list[MacroDefinition]) -> tuple[str, int, list[str]]:
    source = body
    total = 0
    unsupported: list[str] = []
    safe = [macro for macro in definitions if macro.name not in _CORE_COMMANDS and macro.arg_count in (0, 1)]
    unsupported.extend(f"\\{macro.name}" for macro in definitions if macro not in safe)
    for _ in range(4):
        round_count = 0
        for macro in safe:
            if macro.arg_count == 0:
                source, count = re.subn(
                    r"\\" + re.escape(macro.name) + r"(?![A-Za-z@])",
                    lambda _match, replacement=macro.replacement: replacement,
                    source,
                )
            else:
                source, count = _expand_one_arg_macro(source, macro)
            round_count += count
        total += round_count
        if not round_count:
            break
    return source, total, sorted(set(unsupported))


def _normalise_choice_commands(body: str) -> tuple[str, int]:
    converted = 0

    def replace_choices(match: re.Match) -> str:
        nonlocal converted
        inner = match.group(1)
        inner, correct_count = re.subn(r"\\CorrectChoice\b", r"\\item [MATHBANK_ORIGINAL_CORRECT] ", inner)
        inner, choice_count = re.subn(r"\\choice\b", r"\\item ", inner)
        converted += correct_count + choice_count
        return "\\begin{choices}" + inner + "\\end{choices}"

    body = re.sub(r"\\begin\s*\{choices\}([\s\S]*?)\\end\s*\{choices\}", replace_choices, body)
    return body, converted


def _question_count_estimate(body: str) -> int:
    explicit = len(re.findall(r"\\question\b", body))
    environments = sum(
        len(re.findall(r"\\begin\s*\{" + re.escape(env) + r"\}", body, flags=re.IGNORECASE))
        for env in _QUESTION_ENVS
    )
    if explicit or environments:
        return max(explicit, environments)
    numbered = len(re.findall(r"(?m)^\s*(?:\\noindent\s*)?\d{1,3}\s*[\.、．]\s*", body))
    if numbered:
        return numbered

    token_pattern = re.compile(r"\\begin\s*\{([^}]+)\}|\\end\s*\{([^}]+)\}|\\item\b")
    stack: list[tuple[str, int | None, int]] = []
    enumerate_counts: dict[int, tuple[int, int]] = {}
    enumerate_id = 0
    excluded = {"choices", "tasks", "itemize"}
    for token in token_pattern.finditer(body):
        begin_env, end_env = token.group(1), token.group(2)
        if begin_env:
            current_id = None
            if begin_env.lower() == "enumerate":
                enumerate_id += 1
                current_id = enumerate_id
                enumerate_counts[current_id] = (len(stack), 0)
            stack.append((begin_env.lower(), current_id, len(stack)))
        elif end_env:
            target = end_env.lower()
            while stack:
                env, _env_id, _depth = stack.pop()
                if env == target:
                    break
        else:
            if any(env in excluded for env, _env_id, _depth in stack):
                continue
            active = next(((env_id, depth) for env, env_id, depth in reversed(stack) if env == "enumerate"), None)
            if active and active[0] is not None:
                depth, count = enumerate_counts[active[0]]
                enumerate_counts[active[0]] = (depth, count + 1)
    nonempty = [(depth, count) for depth, count in enumerate_counts.values() if count]
    if not nonempty:
        return 0
    shallowest = min(depth for depth, _count in nonempty)
    return sum(count for depth, count in nonempty if depth == shallowest)


def prepare_tex_source(source: str) -> dict:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("TeX 源码为空。")
    if len(source.encode("utf-8", errors="ignore")) > MAX_TEX_BYTES:
        raise ValueError("TeX 源码过大，请控制在 5MB 以内。")

    normalized = source.lstrip("\ufeff").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    title = extract_tex_title(normalized)
    uncommented, comments_removed = strip_tex_comments(normalized)
    begin = re.search(r"\\begin\s*\{document\}", uncommented)
    end_matches = list(re.finditer(r"\\end\s*\{document\}", uncommented))
    document_body_extracted = bool(begin and end_matches and end_matches[-1].start() > begin.end())
    if document_body_extracted:
        preamble = uncommented[:begin.start()]
        body = uncommented[begin.end():end_matches[-1].start()]
    else:
        preamble = ""
        body = uncommented

    definitions, macro_warnings = _parse_macro_definitions(preamble)
    body, macros_expanded, unsupported_macros = expand_safe_macros(body, definitions)
    body, choice_commands_converted = _normalise_choice_commands(body)
    graphics = re.findall(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}", body)
    external_inputs = re.findall(r"\\(?:input|include)\s*\{([^}]+)\}", body)
    tikz_count = len(re.findall(r"\\begin\s*\{tikzpicture\}", body))
    table_count = sum(
        len(re.findall(r"\\begin\s*\{" + re.escape(environment) + r"\}", body))
        for environment in _TABLE_ENVS
    )
    estimate = _question_count_estimate(body)

    warnings = list(macro_warnings)
    if unsupported_macros:
        warnings.append("以下自定义宏未能安全自动展开：" + "、".join(unsupported_macros[:12]))
    if external_inputs:
        warnings.append("单文件模式无法读取外部子文件：" + "、".join(external_inputs[:8]))
    if graphics:
        warnings.append("检测到配图引用，请在配图区上传同名图片：" + "、".join(graphics[:8]))
    if tikz_count:
        warnings.append(
            f"检测到 {tikz_count} 个 TikZ 绘图环境；系统会保留源码，拆分后请核对图形预览。"
        )
    if not document_body_extracted:
        warnings.append("未找到完整的 document 环境，已按正文片段处理")
    if len(body) > 120_000:
        warnings.append("TeX 正文较长，可能接近模型上下文上限，请拆分后重点核对题目数量")

    context_definitions = [macro.original for macro in definitions if f"\\{macro.name}" in unsupported_macros]
    context = ""
    if context_definitions:
        context = (
            "[MATHBANK_TEX_MACRO_CONTEXT_BEGIN]\n"
            + "\n".join(context_definitions[:20])
            + "\n[MATHBANK_TEX_MACRO_CONTEXT_END]\n\n"
        )

    model_source = (context + body.strip()).strip()
    diagnostics = {
        "document_body_extracted": document_body_extracted,
        "comments_removed": comments_removed,
        "macros_found": len(definitions),
        "macros_expanded": macros_expanded,
        "unsupported_macros": unsupported_macros,
        "choice_commands_converted": choice_commands_converted,
        "referenced_graphics": graphics,
        "external_inputs": external_inputs,
        "tikz_environment_count": tikz_count,
        "table_environment_count": table_count,
        "question_count_estimate": estimate,
        "warnings": warnings,
        "source_characters": len(normalized),
        "model_characters": len(model_source),
    }
    return {"source": normalized, "model_source": model_source, "title": title, "diagnostics": diagnostics}


def decode_and_prepare_tex(file_bytes: bytes) -> dict:
    source, encoding_diagnostics = decode_tex_bytes(file_bytes)
    result = prepare_tex_source(source)
    result["diagnostics"].update(encoding_diagnostics)
    return result
