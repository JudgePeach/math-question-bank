from .image_layout import split_image_anchors, image_key, normalize_image_layouts, SIZE_CM, DETACHED_FIGURE_SIZE_CM, TABLE_IMAGE_MAX_HEIGHT_CM, COMPACT_CHOICE_IMAGE_SIZE_CM
from .geometry_symbols import normalize_school_math_symbols
from .markdown_tables import find_markdown_tables, replace_markdown_tables_for_latex
from .source_comments import strip_source_attribution_comment
from .content_sources import (
    CONTENT_SOURCES_JSON_NAME,
    CONTENT_SOURCES_NOTICE_NAME,
    content_source_export_files,
    content_sources_notice,
)
from .export_preflight import assert_tex_image_files
from .fillin import normalize_fillin_math_wrappers
from .paper_layout import item_solution_space_cm
from .content_locks import _reference_literal_ranges
from .question_assets import markdown_literal_ranges
from .math_markdown import _replace_delimited_math, _replace_latex_environments

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict
from io import BytesIO
from sqlalchemy.orm import Session
from mathbank.database import Question, Paper, PaperQuestion, QuestionCurriculum
from mathbank.paths import TEMPLATES_DIR
from mathbank.question_types import PAPER_TYPE_ORDER, custom_type_labels, is_written_question_type, paper_type_order
from mathbank.latex_diagnostics import build_local_latex_diagnostic
from mathbank.asset_security import AssetSecurityError, resolve_upload_asset

# In-memory LRU cache for compiled PDF bytes
_PDF_CACHE_LOCK = threading.Lock()
_PDF_CACHE = OrderedDict()  # key -> (pdf_bytes, log_or_err)
_MAX_PDF_CACHE_SIZE = 20

_CONTENT_SOURCE_TEX_BEGIN = f"% MathBank content sources: {CONTENT_SOURCES_JSON_NAME}\n"
_CONTENT_SOURCE_TEX_END = "% End MathBank content sources\n\n"


def _with_content_source_tex_notice(source: str, notice: str | None = None) -> str:
    """Keep one artifact-level source notice in comments, outside the page body."""
    if source.startswith(_CONTENT_SOURCE_TEX_BEGIN):
        end = source.find(_CONTENT_SOURCE_TEX_END, len(_CONTENT_SOURCE_TEX_BEGIN))
        if end >= 0:
            source = source[end + len(_CONTENT_SOURCE_TEX_END):]
    notice = content_sources_notice() if notice is None else notice
    comments = "".join("% " + line + "\n" for line in notice.splitlines())
    return _CONTENT_SOURCE_TEX_BEGIN + comments + _CONTENT_SOURCE_TEX_END + source


def _with_content_source_pdf_metadata(pdf_bytes: bytes, notice: str) -> bytes:
    """Attach scoped credits to document metadata without touching page content."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        keys = {"title", "author", "subject", "keywords", "creator", "producer",
                "creationDate", "modDate", "trapped"}
        metadata = {key: value for key, value in (document.metadata or {}).items() if key in keys}
        subject = metadata.get("subject", "") or ""
        notice = notice.rstrip()
        if notice in subject:
            return pdf_bytes
        metadata["subject"] = subject.rstrip() + ("\n\n" if subject else "") + notice
        document.set_metadata(metadata)
        return document.tobytes()

# Only these known, offline TeX Live packages may be inserted automatically.
# A model response can never add a package to this allowlist at runtime.
_AUTO_LATEX_PACKAGES = frozenset({
    "amsmath",
    "cancel",
    "extarrows",
    "graphicx",
    "mathtools",
    "mhchem",
    "siunitx",
    "yhmath",
})
_MAX_AUTO_PACKAGE_REPAIRS = 3

_FIGURE_SIZE_LIMITS = {
    size: tuple(f"{value:.1f}cm" for value in dimensions)
    for size, dimensions in DETACHED_FIGURE_SIZE_CM.items()
}
_DEFAULT_FIGURE_MAX_HEIGHT = "6.0cm"
_TABLE_IMAGE_MAX_HEIGHT = f"{TABLE_IMAGE_MAX_HEIGHT_CM:.1f}cm"
_COMPACT_CHOICE_IMAGE_LIMITS = tuple(f"{value:.1f}cm" for value in COMPACT_CHOICE_IMAGE_SIZE_CM)


def _normalize_figure_size(value: object) -> str:
    figure_size = str(value or "auto").strip().lower()
    return figure_size if figure_size in {"auto", *_FIGURE_SIZE_LIMITS} else "auto"


def _figure_height_limit_cm(figure_size: str) -> float:
    height = (
        _DEFAULT_FIGURE_MAX_HEIGHT
        if figure_size == "auto"
        else _FIGURE_SIZE_LIMITS[figure_size][1]
    )
    return float(height.removesuffix("cm"))


def _bounded_includegraphics(filename: str, width: str, height: str) -> str:
    """Render an image within both its requested cap and the current line box."""

    image = (
        rf"\includegraphics[max width={width},max height={height},keepaspectratio]"
        rf"{{{filename}}}"
    )
    return rf"\adjustbox{{max width=\linewidth}}{{{image}}}"


def _bounded_tikz(
    tikz_code: str,
    *,
    figure_size: str,
    figure_align: str,
) -> str:
    """Keep right-side TikZ safe while allowing explicit larger lower figures."""

    if figure_size == "auto":
        # Preserve the historical automatic TikZ width while still respecting
        # the active line/minipage box and a finite vertical budget.
        resized = rf"\resizebox{{4.5cm}}{{!}}{{{tikz_code}}}"
        return (
            rf"\adjustbox{{max width=\linewidth,max height={_DEFAULT_FIGURE_MAX_HEIGHT},"
            rf"keepaspectratio}}{{{resized}}}"
        )

    width, height = _FIGURE_SIZE_LIMITS[figure_size]
    if figure_align == "right":
        width = "5.0cm"
    bounded = (
        rf"\adjustbox{{max width={width},max height={height},keepaspectratio}}"
        rf"{{{tikz_code}}}"
    )
    return rf"\adjustbox{{max width=\linewidth}}{{{bounded}}}"


def build_restricted_tex_environment(output_dir: str) -> dict[str, str]:
    """Build a minimal kpathsea policy for compiling untrusted question TeX."""

    env = os.environ.copy()
    # ``p`` forbids absolute and parent-directory input/output while keeping
    # normal TeX tree package lookup available.  TEXMFOUTPUT pins generated
    # artifacts to the per-request temporary directory.
    env["openin_any"] = "p"
    env["openout_any"] = "p"
    env["TEXMFOUTPUT"] = os.path.abspath(output_dir)
    return env

# Constants for question type labels
TYPE_LABELS = {
    "single_choice": "单项选择题",
    "multi_choice": "多项选择题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题"
}

TYPE_ORDER = PAPER_TYPE_ORDER

def clean_choice_stem_parentheses(text: str) -> str:
    """清洗选择题末尾的空括号，并保持数学定界符平衡。

    OCR 可能会把空括号识别为 ``$(\\quad)$`` 或 ``（$\\quad$）``。
    这些定界符必须和括号一起移除，否则会遗留孤立的 ``$$``
    并让 XeLaTeX 后续一直处于数学模式。
    """
    if not text:
        return ""
    text = text.strip()

    blank = r'(?:\\quad|\\qquad|\\hspace\{[^{}]*\}|[\s\xa0\u3000_])*'
    plain_parens = rf'[\(（]\s*{blank}\s*[\)）]'
    math_wrapped_parens = rf'\$\s*{plain_parens}\s*\$'
    math_inside_parens = rf'[\(（]\s*\$\s*{blank}\s*\$\s*[\)）]'
    pattern = (
        rf'(?:[\s\xa0\u3000]*(?:{math_wrapped_parens}|'
        rf'{math_inside_parens}|{plain_parens}))+[\s\xa0\u3000]*$'
    )
    cleaned = re.sub(pattern, '', text).strip()
    cleaned = re.sub(r'\\paren\b', '', cleaned).strip()

    # 仅在未闭合的数学内容后补 "$"。若末尾本身就是孤立 "$"，
    # 直接移除；追加另一个 "$" 会把它变成未闭合的行间数学定界符 "$$"。
    dollars = list(re.finditer(r'(?<!\\)\$', cleaned))
    if len(dollars) % 2 != 0:
        last_dollar = dollars[-1]
        if cleaned[last_dollar.end():].strip():
            cleaned += "$"
        else:
            cleaned = cleaned[:last_dollar.start()].rstrip()

    return cleaned


def _export_literal_ranges(text: str, *, include_table_cells: bool = False) -> list[tuple[int, int]]:
    ranges = markdown_literal_ranges(text, include_links=False)
    images = [match.span() for match in _MARKDOWN_IMAGE_RE.finditer(text)]
    tables = [] if include_table_cells else [(table.start, table.end) for table in find_markdown_tables(text)]
    ranges = [(start, end) for start, end in ranges
              if not any(left <= start and end <= right for left, right in tables)]
    ranges.extend(
        (start, end) for start, end in _reference_literal_ranges(text, tex_comments=False)
        if not text[start:end].startswith("![")
        and not any(left <= start and end <= right for left, right in images)
        and not any(left <= start and end <= right for left, right in tables)
    )
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _protect_export_literals(text: str, token_prefix: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Keep source examples intact; actual standalone TikZ remains a visual slot."""
    output, literals, visuals = [], {}, {}
    cursor = 0
    for start, end in _export_literal_ranges(text):
        original = text[start:end]
        is_drawing = original.startswith(r"\begin{tikzpicture}") and original.endswith(r"\end{tikzpicture}")
        blocks = visuals if is_drawing else literals
        token = f"{token_prefix}{'N' if is_drawing else 'L'}{len(blocks)}END"
        blocks[token] = original
        output.extend((text[cursor:start], token))
        cursor = end
    output.append(text[cursor:])
    return "".join(output), literals, visuals


def _split_export_image_anchors(text: str) -> tuple[str, str]:
    """Use normal anchor rules, without reading environments inside source examples."""
    text = strip_source_attribution_comment(text)
    chars = list(text)
    for start, end in _export_literal_ranges(text, include_table_cells=True):
        for index in range(start, end):
            if not chars[index].isspace():
                chars[index] = "X"
    scan = "".join(chars)
    _, tail = split_image_anchors(scan)
    if not tail:
        return text, ""
    start = len(scan.rstrip()) - len(tail)
    return text[:start].rstrip(), text[start:].strip()


def _place_export_choice_paren(text: str, visual_tokens: set[str], *, append: bool) -> str:
    """Put one answer blank at the prose tail, before trailing standalone figures.

    Images have protected slots at this stage. Tables, complete math and
    source examples cannot provide an option boundary or an answer blank.
    Only an empty terminal placeholder is removed; meaningful parentheses
    and placeholders in earlier prose/structures are retained.
    """
    def mask(value: str) -> str:
        return "".join(char if char in "\r\n" else " " for char in value)

    scan = _replace_delimited_math(text, mask)
    scan = _replace_latex_environments(scan, lambda value, name: mask(value))
    choices = re.search(r"\\begin\{choices\}|^[ \t]*[-*]?[ \t]*[A-D][.、][ \t]+", scan, re.MULTILINE)
    boundary = choices.start() if choices else len(text)
    stem, rest = text[:boundary].rstrip(), text[boundary:]
    visual_start = len(stem)
    # Strip no source bytes from the visual tail; just locate its first slot.
    while True:
        before = stem[:visual_start].rstrip()
        token = next((item for item in visual_tokens if before.endswith(item)), None)
        if token is None:
            break
        visual_start = len(before) - len(token)
    prose_end = len(stem[:visual_start].rstrip())
    prose, figures = stem[:prose_end], stem[prose_end:]
    blank = r"(?:\\quad|\\qquad|\\hspace\{[^{}]*\}|[\s\xa0\u3000_])*"
    plain = rf"[(（]{blank}[)）]"
    dollar_blank = rf"\${blank}\$"
    tex_blank = rf"\\\({blank}\\\)"
    empty = (
        rf"(?:\${plain}\$|\\\({plain}\\\)|"
        rf"[(（](?:{dollar_blank}|{tex_blank})[)）]|{tex_blank}|{plain}|\\paren\b)\s*$"
    )
    while match := re.search(empty, prose):
        # An escaped delimiter belongs to the source, not to the blank syntax.
        if (len(prose[:match.start()]) - len(prose[:match.start()].rstrip("\\"))) % 2:
            break
        prose = prose[:match.start()].rstrip()
    # Preserve the legacy lone OCR dollar repair, without changing paired math.
    if prose.endswith("$") and len(re.findall(r"(?<!\\)\$", prose)) % 2:
        prose = prose[:-1].rstrip()
    if append:
        prose += r" \paren"
    return prose + figures + ("\n" if rest else "") + rest


def _export_choices_start(text: str) -> int | None:
    """Find the real choices boundary after protected source has been restored."""
    chars = list(text)
    for start, end in [*markdown_literal_ranges(text, include_links=False),
                       *_reference_literal_ranges(text, tex_comments=False)]:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    def mask(value: str) -> str:
        return "".join(char if char in "\r\n" else " " for char in value)
    scan = _replace_delimited_math("".join(chars), mask)
    scan = _replace_latex_environments(scan, lambda value, name: mask(value))
    match = re.search(r"\\begin\{choices\}", scan)
    return match.start() if match else None

def format_stem_paragraphs(stem_text: str) -> str:
    r"""
    Safely assemble question stem lines into coherent LaTeX paragraphs.
    1. Preserves explicit double line breaks (empty lines) intended by the user.
    2. Identifies sub-question & note starts ONLY at line start (e.g. (1), (2), (a), (b), ①, ②, 注：, 提示：),
       placing each into a separate LaTeX paragraph with standard 2em indentation.
    3. Keeps block LaTeX environments (e.g. \begin{...}, \end{...}, $$) intact without inline space merging.
    4. Recombines single line breaks inside the same paragraph into a space for continuous TeX wrapping.
    """
    if not stem_text:
        return ""
        
    lines_list = stem_text.split('\n')
    paragraphs = []
    current_para = []

    for line in lines_list:
        line_str = line.strip()
        
        # 遇到显式空行，代表原作者故意留下的独立段落，直接收尾当前段落
        if not line_str:
            if current_para:
                paragraphs.append(" ".join(current_para))
                current_para = []
            continue
            
        # 判定是否为小问或独立提示/注记开头
        is_sub_start = bool(re.match(r'^([（(]?[0-9a-zA-Z一二三四五六七八九十]+[）\.\)]|①|②|③|④|⑤|【|注[:：]|提示[:：])', line_str))
        # 判定是否为独立块级 TeX 环境
        is_block_env = bool(re.match(r'^(\$\||\\begin\{|\\end\{)', line_str))
        
        if (is_sub_start or is_block_env) and current_para:
            paragraphs.append(" ".join(current_para))
            current_para = [line_str]
        else:
            current_para.append(line_str)

    if current_para:
        paragraphs.append(" ".join(current_para))

    return "\n\n".join(paragraphs)

_MARKDOWN_IMAGE_RE = re.compile(
    r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)'
)


def _should_preserve_inline_image_positions(content: str) -> bool:
    """Keep images anchored when authored content continues after the first one.

    A trailing image or trailing cluster remains eligible for the historical
    ``figure_align`` layout.  Images inside tables, between sub-questions, or
    before later explanatory text must stay where the author inserted them.
    """

    text = str(content or "")
    matches = list(_MARKDOWN_IMAGE_RE.finditer(text))
    if not matches:
        return False
    trailing = _MARKDOWN_IMAGE_RE.sub("", text[matches[0].start():]).strip()
    return bool(trailing)


def _tikz_image_key(path: str) -> str:
    """Match upload URL aliases without conflating files in different folders."""
    return re.sub(r"^/?(?:static/)?uploads/", "", str(path or "").strip())


def _inline_tikz_sources(content: str, assets: list, legacy_code: str) -> dict[str, str]:
    sources = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        key = _tikz_image_key(asset.get("image_path"))
        code = str(asset.get("tikz_code") or "").strip()
        if key and code:
            sources[key] = code

    # Old records have one source but no explicit image/source association.
    # Only infer it when there is exactly one distinct rendered TikZ image.
    if not assets and legacy_code:
        candidates = {
            _tikz_image_key(path)
            for path in _MARKDOWN_IMAGE_RE.findall(content)
            if os.path.basename(path).startswith("tikz_")
        }
        if len(candidates) == 1:
            sources[candidates.pop()] = legacy_code
    return sources


def _image_is_inside_table_environment(content: str, position: int) -> bool:
    prefix = content[:position]
    table_environments = (
        "tabular",
        "tabular*",
        "tabularx",
        "longtable",
        "tblr",
        "longtblr",
        "talltblr",
    )
    return any(
        prefix.rfind(rf"\begin{{{environment}}}")
        > prefix.rfind(rf"\end{{{environment}}}")
        for environment in table_environments
    )


def _image_is_inside_choices_environment(content: str, position: int) -> bool:
    prefix = content[:position]
    return prefix.rfind(r"\begin{choices}") > prefix.rfind(r"\end{choices}")


def _read_balanced_latex_group(source: str, start: int) -> tuple[str, int] | None:
    if start >= len(source) or source[start] != "{":
        return None
    depth = 0
    index = start
    while index < len(source):
        if source[index] == "\\" and index + 1 < len(source):
            index += 2
            continue
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index], index + 1
        index += 1
    return None


def _bound_full_width_multicolumns(body: str) -> str:
    source = str(body or "")
    command = r"\multicolumn"
    output: list[str] = []
    cursor = 0
    while True:
        start = source.find(command, cursor)
        if start < 0:
            output.append(source[cursor:])
            break
        output.append(source[cursor:start])
        group_cursor = start + len(command)
        groups: list[tuple[str, int]] = []
        valid = True
        for _ in range(3):
            while group_cursor < len(source) and source[group_cursor].isspace():
                group_cursor += 1
            group = _read_balanced_latex_group(source, group_cursor)
            if group is None:
                valid = False
                break
            groups.append(group)
            group_cursor = group[1]
        if not valid or groups[0][0].strip() != "3":
            output.append(command)
            cursor = start + len(command)
            continue

        original_spec = groups[1][0].strip()
        alignment = (
            r"\raggedright\arraybackslash"
            if "l" in original_spec.lower()
            else r"\raggedleft\arraybackslash"
            if "r" in original_spec.lower()
            else r"\centering\arraybackslash"
        )
        left_rule = "|" if original_spec.startswith("|") else ""
        right_rule = "|" if original_spec.endswith("|") else ""
        bounded_spec = (
            left_rule
            + rf">{{{alignment}}}p{{\dimexpr\linewidth-2\tabcolsep-2\arrayrulewidth\relax}}"
            + right_rule
        )
        output.append(
            rf"\multicolumn{{3}}{{{bounded_spec}}}{{{groups[2][0]}}}"
        )
        cursor = groups[2][1]
    return "".join(output)


def _normalize_simple_three_column_tables(content: str) -> str:
    """Give OCR-style three-column comparison tables bounded wrapping columns."""

    pattern = re.compile(
        r"\\begin\{tabular\}\s*\{(?P<spec>[^{}]*)\}"
        r"(?P<body>[\s\S]*?)\\end\{tabular\}",
        re.IGNORECASE,
    )

    def replace(match: re.Match) -> str:
        normalized_spec = re.sub(r"\s+", "", match.group("spec"))
        if normalized_spec != "|c|c|c|":
            return match.group(0)
        column_spec = (
            r"|>{\centering\arraybackslash}m{4em}"
            r"|>{\centering\arraybackslash}X"
            r"|>{\centering\arraybackslash}X|"
        )
        body = _bound_full_width_multicolumns(match.group("body"))
        return (
            rf"\noindent\begin{{tabularx}}{{\linewidth}}{{{column_spec}}}"
            f"{body}"
            r"\end{tabularx}"
        )

    return pattern.sub(replace, content)


def _normalize_display_array_tags(content: str) -> str:
    """Adapt a sole display tag to amsmath's exportable display context.

    amsmath forbids tags inside array cells and raw $$ displays. A complete,
    sole simple array may move its tag out; tagged $$ uses the equivalent
    amsmath-aware \\[ shell. Rows and tag text stay unchanged.
    Ambiguous structures and literal content are left exactly as authored.
    """
    source = str(content or "")
    ranges: list[tuple[int, int]] = []
    literal = re.compile(
        r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`]*`|"
        r"\\begin\{(?P<env>tikzpicture|verbatim\*?|Verbatim\*?|lstlisting|minted|comment)\}"
        r"[\s\S]*?(?:\\end\{(?P=env)\}|$)|"
        r"(?i:<(?:pre|code)\b[^>]*>[\s\S]*?(?:</(?:pre|code)\s*>|$))|"
        r"\\(?:verb|Verb|lstinline)\*?(?:\[[^]\n]*\])?(?P<sep>[^\w\s{])"
        r"[^\n]*?(?P=sep)"
    )
    ranges.extend((match.start(), match.end()) for match in literal.finditer(source))
    for match in re.finditer(r"\\(?:detokenize|url|path|texttt)\s*\{", source):
        group = _read_balanced_latex_group(source, match.end() - 1)
        ranges.append((match.start(), group[1] if group else len(source)))
    display = re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]")

    def replace(match: re.Match) -> str:
        start, end = match.span()
        if any(first < end and last > start for first, last in ranges):
            return match[0]
        if ((start > 0 and source[start - 1] in "\\$")
                or (end < len(source) and source[end] == "$")):
            return match[0]
        original = match[0]
        inner = original[2:-2]
        array = re.fullmatch(
            r"(?P<before>\s*\\begin\{array\}\s*\{[lcr|\s]+\})"
            r"(?P<body>[\s\S]*?)(?P<after>\\end\{array\}\s*)", inner)
        if array is None and original[:2] != "$$":
            return original
        body = array["body"] if array is not None else inner
        if re.search(r"\\(?:begin|end)\{|(?<!\\)%", body):
            return original
        tags = list(re.finditer(r"(?<!\\)\\tag\*?\s*\{", body))
        if len(tags) != 1:
            return original
        tag = tags[0]
        depth = 0
        index = 0
        while index < tag.start():
            if body[index] == "\\":
                index += 2
                continue
            depth += (body[index] == "{") - (body[index] == "}")
            if depth < 0:
                return original
            index += 1
        if depth:
            return original
        group = _read_balanced_latex_group(body, tag.end() - 1)
        if group is None:
            return original
        depth = index = 0
        while index < len(body):
            if body[index] == "\\":
                index += 2
                continue
            depth += (body[index] == "{") - (body[index] == "}")
            if depth < 0:
                return original
            index += 1
        if depth:
            return original
        tag_source = body[tag.start():group[1]]
        new_body = body[:tag.start()] + body[group[1]:]
        rendered = (array["before"] + new_body + array["after"] + tag_source
                    if array is not None else inner)
        opening, closing = (r"\[", r"\]") if original[:2] == "$$" else (original[:2], original[-2:])
        return opening + rendered + closing

    return display.sub(replace, source)


def clean_content_for_latex(
    content: str,
    q_type: str = "",
    is_answer: bool = False,
    preserve_image_positions: bool = False,
    tikz_sources: dict[str, str] | None = None,
    image_layouts: dict | None = None,
) -> str:
    r"""
    Clean markdown/LaTeX question content for exam-zh LaTeX document export based on 试卷类模板.tex.
    Preserves math delimiters $...$ and $$...$$, tikz code, and handles choices & \paren environments.
    """
    if not content:
        return r"\paren" if q_type in {"single_choice", "multi_choice"} else ""
    
    text = strip_source_attribution_comment(content)
    token_prefix = "MATHBANKTIKZ" + hashlib.sha256(text.encode()).hexdigest()
    while token_prefix in text or any(token_prefix in code for code in (tikz_sources or {}).values()):
        token_prefix += "X"
    text, literal_blocks, tikz_blocks = _protect_export_literals(text, token_prefix)
    text = text.strip()
    # Repair legacy/model-only math wrappers in the export copy. Do not run
    # general underscore normalization over historical question source here.
    from .fillin_placeholders import normalize_fillin_placeholder_clusters
    text = normalize_fillin_placeholder_clusters(normalize_fillin_math_wrappers(text))
    visual_tokens = set(tikz_blocks)
    text = normalize_school_math_symbols(text, target="latex")
    text = _normalize_display_array_tags(text)
    # Interpret only complete Markdown table blocks before image conversion:
    # their images must be sized against a cell, rather than the whole page.
    text = replace_markdown_tables_for_latex(text)
    # Restore source/TikZ only after prose/table/choice transformations. A '%'
    # inside a drawing or literal example must never swallow following code.
    
    # If TikZ code is present in text, remove ANY markdown image tags ![](...) to avoid duplicate image rendering
    if visual_tokens and not preserve_image_positions:
        text = re.sub(r'!\[.*?\]\([^)]+\)', '', text)

    # Four image-only options form one compact row. Mark only complete groups;
    # prose, multi-image options and authored choices/image settings keep their layout.
    compact_choice_ranges = []
    choice_offset = 0

    def compact_image_choices(match):
        nonlocal choice_offset
        parts = re.split(r"\\item\b", match.group(1))
        image_matches = [_MARKDOWN_IMAGE_RE.fullmatch(part.strip()) for part in parts[1:]]
        if len(parts) == 5 and not parts[0].strip() and all(image_matches) and not any(
            (image_layouts or {}).get(image_key(image[1])) for image in image_matches
        ):
            replacement = r"\begin{choices}[columns=4,label-pos=left]" + match.group(1) + r"\end{choices}"
            start = match.start() + choice_offset
            compact_choice_ranges.append((start, start + len(replacement)))
            choice_offset += len(replacement) - len(match.group(0))
            return replacement
        return match.group(0)

    text = re.sub(r"\\begin\{choices\}([\s\S]*?)\\end\{choices\}", compact_image_choices, text)

    # Convert Markdown images ![](/static/uploads/xxx.png) or ![](uploads/xxx.png) to \includegraphics{...}
    def replace_img(match):
        img_path = match.group(1)
        base_name = os.path.basename(img_path)
        inside_table = _image_is_inside_table_environment(text, match.start())
        inside_choices = _image_is_inside_choices_environment(text, match.start())
        code = (tikz_sources or {}).get(_tikz_image_key(img_path))
        layout = (image_layouts or {}).get(image_key(img_path))
        if any(start <= match.start() < end for start, end in compact_choice_ranges):
            if code:
                if r"\begin{tikzpicture}" not in code:
                    code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"
                rendered = (r"\adjustbox{max width=\linewidth}{"
                            rf"\adjustbox{{max width={_COMPACT_CHOICE_IMAGE_LIMITS[0]},max height={_COMPACT_CHOICE_IMAGE_LIMITS[1]},keepaspectratio}}{{" + code + "\n}}")
            else:
                rendered = _bounded_includegraphics(base_name, *_COMPACT_CHOICE_IMAGE_LIMITS)
            token = f"{token_prefix}N{len(tikz_blocks)}END"
            tikz_blocks[token] = rendered
            return token
        if layout:
            # All values are normalized enums. Wrap the result in a protected
            # token so subsequent prose formatting cannot alter generated TeX.
            width, height = SIZE_CM.get(layout['size'], (9.0, 6.0))
            if inside_table:
                height = min(height, TABLE_IMAGE_MAX_HEIGHT_CM)
            if code:
                if r"\begin{tikzpicture}" not in code:
                    code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"
                visual = rf"\adjustbox{{max width={width}cm,max height={height}cm,keepaspectratio}}{{" + code + "\n}"
            else:
                visual = rf"\includegraphics[max width={width}cm,max height={height}cm,keepaspectratio]{{{base_name}}}"
            visual = r"\adjustbox{max width=\linewidth}{" + visual + "}"
            if inside_table:
                alignment = {'left': 'l', 'center': 'c', 'right': 'r'}[layout['align']]
                rendered = r"\makebox[\linewidth][" + alignment + "]{" + r"\adjustbox{valign=m,margin=0pt 3pt}{" + visual + "}}"
            elif inside_choices:
                # exam-zh measures choices in a horizontal box before choosing
                # columns. Keep alignment in that box, not a paragraph environment.
                alignment = {'left': 'l', 'center': 'c', 'right': 'r'}[layout['align']]
                rendered = r"\makebox[\linewidth][" + alignment + "]{" + visual + "}"
            else:
                environment = {'left': 'flushleft', 'center': 'center', 'right': 'flushright'}[layout['align']]
                rendered = "\\begin{" + environment + "}\n" + visual + "\n\\end{" + environment + "}"
            token = f"{token_prefix}N{len(tikz_blocks)}END"
            tikz_blocks[token] = rendered
            return token if inside_table or inside_choices else "\n" + token + "\n"
        if code:
            if r"\begin{tikzpicture}" not in code:
                code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"
            if inside_table:
                rendered = (
                    r"\adjustbox{valign=m,margin=0pt 3pt}{"
                    rf"\adjustbox{{max width=\linewidth,max height={_TABLE_IMAGE_MAX_HEIGHT},keepaspectratio}}{{"
                    + code + "\n}}"
                )
            elif inside_choices:
                rendered = (
                    r"\adjustbox{max width=\linewidth}{"
                    r"\adjustbox{max width=9.0cm,max height=6.0cm,keepaspectratio}{"
                    + code + "\n}}"
                )
            else:
                rendered = (
                    "\\begin{center}\n"
                    r"\adjustbox{max width=\linewidth}{"
                    r"\adjustbox{max width=9.0cm,max height=6.0cm,keepaspectratio}{"
                    + code + "\n}}\n\\end{center}"
                )
            token = f"{token_prefix}N{len(tikz_blocks)}END"
            tikz_blocks[token] = rendered
            return token if inside_table or inside_choices else "\n" + token + "\n"
        if inside_table:
            return (
                r"\adjustbox{valign=m,margin=0pt 3pt}{"
                rf"\includegraphics[max width=\linewidth,max height={_TABLE_IMAGE_MAX_HEIGHT},keepaspectratio]{{{base_name}}}"
                r"}"
            )
        if inside_choices:
            return _bounded_includegraphics(base_name, "9.0cm", "6.0cm")
        if preserve_image_positions:
            return (
                "\n\\begin{center}\n"
                rf"\includegraphics[max width=9.0cm,max height=6.0cm,keepaspectratio]{{{base_name}}}"
                "\n\\end{center}\n"
            )
        return f"\n\\begin{{center}}\n\\includegraphics[max width=0.85\\linewidth]{{{base_name}}}\n\\end{{center}}\n"
    
    def protected_image(match):
        rendered = replace_img(match)
        if rendered.strip() in tikz_blocks:
            token = rendered.strip()
        else:
            token = f"{token_prefix}N{len(tikz_blocks)}END"
            tikz_blocks[token] = rendered
        if not _image_is_inside_table_environment(text, match.start()) and not _image_is_inside_choices_environment(text, match.start()):
            visual_tokens.add(token)
        return rendered if rendered.strip() == token else token

    text = _MARKDOWN_IMAGE_RE.sub(protected_image, text)
    text = _normalize_simple_three_column_tables(text)

    # A pure authored figure wrapper is one visual slot too. Keep the wrapper
    # and its exact drawing/image body, rather than putting a blank inside it.
    def protected_visual_wrapper(match):
        body = match.group(2)
        used = [token for token in visual_tokens if token in body]
        for token in used:
            body = body.replace(token, "")
        body = re.sub(r"\\centering\b", "", body)
        if not used or body.strip():
            return match.group(0)
        rendered = match.group(0)
        for token in used:
            rendered = rendered.replace(token, tikz_blocks[token])
        token = f"{token_prefix}N{len(tikz_blocks)}END"
        tikz_blocks[token] = rendered
        visual_tokens.add(token)
        return token

    text = re.sub(r"\\begin\{(center|figure|flushleft|flushright)\}([\s\S]*?)\\end\{\1\}", protected_visual_wrapper, text)
    if q_type in {"single_choice", "multi_choice"} or r"\begin{choices}" in text:
        text = _place_export_choice_paren(text, visual_tokens, append=q_type in {"single_choice", "multi_choice"})
    
    # Choice inference and paragraph assembly must never reinterpret complete
    # math shells or the wrapping tables generated above. Restore these before
    # rendered image/TikZ tokens, because a protected table can contain them.
    format_blocks: dict[str, str] = {}

    def save_format_block(value: str) -> str:
        token = f"{token_prefix}F{len(format_blocks)}END"
        format_blocks[token] = value
        return token

    text = re.sub(
        r"\\begingroup\s*\\setlength\{\\tabcolsep\}\{(?:2|4)pt\}"
        r"\s*\\renewcommand\{\\arraystretch\}\{1\.2\}"
        r"\s*\\noindent\\begin\{tabularx\}[\s\S]*?\\end\{tabularx\}\s*\\endgroup",
        lambda match: save_format_block(match[0]), text)
    math_output: list[str] = []
    cursor = copied = 0

    def is_escaped(position: int) -> bool:
        preceding = position - 1
        while preceding >= 0 and text[preceding] == "\\":
            preceding -= 1
        return (position - preceding - 1) % 2 == 1

    while cursor < len(text):
        opening = next((token for token in ("$$", "$", r"\(", r"\[")
                        if text.startswith(token, cursor)), None)
        if opening is None or is_escaped(cursor):
            cursor += 1
            continue
        closing = {r"\(": r"\)", r"\[": r"\]"}.get(opening, opening)
        end = text.find(closing, cursor + len(opening))
        while end >= 0 and is_escaped(end):
            end = text.find(closing, end + len(closing))
        if end < 0:
            cursor += len(opening)
            continue
        end += len(closing)
        math_output.extend((text[copied:cursor], save_format_block(text[cursor:end])))
        cursor = copied = end
    math_output.append(text[copied:])
    text = "".join(math_output)


    # Check choices formatting: if markdown list like - A. xx - B. xx, convert to \begin{choices}
    if r"\begin{choices}" not in text and re.search(r'^\s*[-*]?\s*[A-D][\.、\s]', text, re.MULTILINE):
        lines = text.split('\n')
        stem_lines = []
        choices_items = []
        for line in lines:
            m = re.match(r'^\s*[-*]?\s*([A-D])[\.、\s]+(.*)', line)
            if m:
                choices_items.append(f"    \\item {m.group(2).strip()}")
            else:
                if not choices_items:
                    stem_lines.append(line)
                else:
                    stem_lines.append(line)
        if choices_items:
            choices_block = "  \\begin{choices}\n" + "\n".join(choices_items) + "\n  \\end{choices}"
            text = "\n".join(stem_lines).strip() + "\n" + choices_block

    if is_answer:
        # 解答/解析步骤（is_answer=True）：在带小问/解答步骤前自动补全空行
        text = re.sub(r'([^\n])\n(?=[（(]?[0-9一二三四五六七八九十]+[）\.\)]|解[:：]|证明[:：]|因为|所以|故|又因为|由|综上|因此|得|【)', r'\1\n\n', text)
    else:
        # 题干文本处理：安全地按行首拆分与段落重组
        text = format_stem_paragraphs(text)

    for token, original in reversed(list(format_blocks.items())):
        text = text.replace(token, original)
    for token, rendered in tikz_blocks.items():
        text = text.replace(token, rendered)
    for token, original in literal_blocks.items():
        text = text.replace(token, original)
    return text

def _escape_paper_type_label(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
        "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_",
        "$": r"\textdollar{}", "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def build_latex_document(
    title: str,
    subtitle: str,
    paper_type: str,
    questions_data: list,
    include_answers: bool = False,
    show_secret: bool = True,
    show_notice: bool = True,
    question_types: list | None = None,
    section_order: list | None = None,
) -> str:
    """
    Generate LaTeX source code based on exam-zh document class matching 试卷类模板.tex.
    questions_data: list of dicts with keys 'question' (Question dict) and 'score' (int).
    """
    paper_title = title.strip() or "2025 年普通高等学校招生全国统一考试(模拟卷)"
    sub_title = subtitle.strip()
    
    total_q_count = len(questions_data)
    total_score_sum = sum(q.get("score", 5) for q in questions_data)
    
    lines = []
    lines.append(r"\let\stop\empty")
    # Fandol ships with TeX Live and avoids platform-specific ctex font names
    # such as STHeiti, which XeLaTeX may fail to resolve on macOS.
    lines.append(r"\documentclass[fontset=fandol]{exam-zh}")
    # Stable high-school mathematics baseline. Keep packages that alter core
    # math semantics (for example physics/unicode-math) out of this default.
    lines.append(r"\usepackage{amsmath,mathtools,cancel,cases,mhchem,siunitx,extarrows}")
    fraction_sources = []
    for item in questions_data:
        question = item.get("question", {})
        fraction_sources.extend(str(question.get(key) or "") for key in ("content", "answer_markdown", "tikz_code"))
        for key in ("content_tikz_assets", "answer_tikz_assets"):
            fraction_sources.extend(str(asset.get("tikz_code") or "") for asset in (question.get(key) or []) if isinstance(asset, dict))
    if any(re.search(r"\\sfrac(?![A-Za-z])", source) for source in fraction_sources):
        lines.append(r"\usepackage{xfrac}")
    # exam-zh uses unicode-math, which rejects the legacy bm package. Preserve
    # imported \bm{...} formulas through the native unicode-math equivalent.
    lines.append(r"\providecommand{\bm}[1]{\symbf{#1}}")
    # unicode-math already supplies a real, stretchable top-parenthesis accent.
    # Avoid loading yhmath, which also replaces unrelated hats and tildes.
    for arc_command in ("wideparen", "widearc"):
        lines.append(rf"\providecommand{{\{arc_command}}}[1]{{\overparen{{#1}}}}")
    lines.append(
        r"\ProvideDocumentCommand{\overarc}{O{1}m}{"
        r"\ifdim #1pt=1pt\overparen{#2}\else"
        r"\PackageError{mathbank}{Unsupported optional overarc width}"
        r"{Use overarc without an option or with [1].}\fi}"
    )
    lines.append(r"\providecommand{\degree}{\ensuremath{{}^\circ}}")
    lines.append(r"\providecommand{\celsius}{\ensuremath{{}^\circ\mathrm{C}}}")
    lines.append(r"\providecommand{\perthousand}{\ensuremath{\mathord{\text{\textperthousand}}}}")
    lines.append(r"\providecommand{\permil}{\perthousand}")
    # Tables: standard/aligned columns, three-line tables, adaptive width,
    # long tables, merged cells, diagonal headers, colored cells and tblr.
    lines.append(r"\usepackage{array,booktabs,tabularx,longtable,multirow,makecell,diagbox,colortbl,tabularray,threeparttable}")
    lines.append(r"\UseTblrLibrary{booktabs}")
    lines.append(r"\usepackage{tkz-euclide}")
    # Editable drawings were compiled in the workbench with these libraries.
    # Keep them available when exporting their source instead of the preview PNG.
    if any(
        q.get("content_tikz_assets") or q.get("tikz_code")
        or r"\begin{tikzpicture}" in str(q.get("content") or "")
        for q in (item.get("question", {}) for item in questions_data)
    ):
        lines.append(r"\usepackage{pgfplots}")
        lines.append(r"\pgfplotsset{compat=1.16}")
        lines.append(
            r"\usetikzlibrary{patterns,calc,positioning,intersections,arrows,"
            r"shapes.geometric,through,decorations.pathmorphing,arrows.meta,quotes,"
            r"mindmap,shapes.symbols,shapes.arrows,automata,angles,3d,trees,shadows,"
            r"shapes.callouts,decorations.pathreplacing,decorations.markings}"
        )
    lines.append(r"\usepackage{lastpage}")
    # Keep the first large detached figure away from the physical page edge
    # without adding another TeX package to the portable runtime contract.
    lines.append(
        r"\newcommand{\mathbankneedspace}[1]{\par\begingroup\dimen0=#1\relax"
        r"\ifdim\pagegoal<\maxdimen\ifdim\dimexpr\pagegoal-\pagetotal\relax<\dimen0"
        r"\newpage\fi\fi\endgroup}"
    )
    # Small, non-discardable empty boxes preserve the requested physical space
    # across page breaks. A single vspace or tall minipage cannot do that.
    lines.append(
        r"\newcommand{\mathbanksolutionspace}[1]{\par\begingroup\dimen0=#1\relax"
        r"\loop\ifdim\dimen0>0pt\dimen2=.25cm\relax"
        r"\ifdim\dimen0<\dimen2\dimen2=\dimen0\fi"
        r"\nointerlineskip\vbox to\dimen2{\vfil}\penalty0"
        r"\advance\dimen0 by-\dimen2\repeat\endgroup}"
    )
    lines.append(r"\usepackage{caption}")
    lines.append(r"\usepackage{wrapfig}")
    lines.append(r"\usepackage{graphicx}")
    # Exported source packages keep figures in images/.  The ./ fallback keeps
    # the same source compatible with the in-app compiler's temporary layout.
    lines.append(r"\graphicspath{{images/}{./}}")
    # clean_content_for_latex emits the adjustbox-provided `max width` key for
    # Markdown images.  `export` makes that key available to \includegraphics.
    lines.append(r"\usepackage[export]{adjustbox}")
    lines.append(r"\examsetup{")
    lines.append(r"  page/size=a4paper,")
    lines.append(r"  paren/show-paren=true,")
    if include_answers:
        lines.append(r"  paren/show-answer=true,")
        lines.append(r"  fillin/show-answer=true,")
        lines.append(r"  solution/show-solution=show-stay,")
    else:
        lines.append(r"  paren/show-answer=false,")
        lines.append(r"  fillin/show-answer=false,")
    lines.append(r"  font      = times,")
    lines.append(r"  math-font = xits,")
    lines.append(r"  fillin/no-answer-type=none")
    lines.append(r"}")
    # Keep the native Unicode math accent and also allow the editor's bare
    # shortcut (or an accent inside \text) to enter math mode safely.
    lines.append(r"\let\mathbankoverparen\overparen")
    lines.append(r"\RenewDocumentCommand{\overparen}{m}{\ensuremath{\mathbankoverparen{#1}}}")
    lines.append(r"\newcommand{\customcurve}[1][1]{%")
    lines.append(r"  \tikz[baseline,yshift=3,scale=0.05,thick,line width=0.8pt,")
    lines.append(r"        line cap=round, domain=-1:2.832, samples=300]{")
    lines.append(r"    \draw plot (\x,{sqrt(16/(\x+2)^2 - (\x-2)^2)});")
    lines.append(r"    \draw plot (\x,{-sqrt(16/(\x+2)^2 - (\x-2)^2)});")
    lines.append(r"    }")
    lines.append(r"}")
    lines.append(r"\everymath{\displaystyle}")
    lines.append("")
    lines.append(f"\\title{{{paper_title}}}")
    lines.append(r"\subject{数学}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\raggedbottom")
    lines.append("")
    
    is_exam_style = (paper_type == "exam" or paper_type == "exam_19")
    if is_exam_style:
        if show_secret:
            lines.append(r"\secret")
        else:
            lines.append(r"% \secret")
        lines.append("")
    
    lines.append(r"\maketitle")
    
    if sub_title:
        tex_sub_title = re.sub(r' {2,}', lambda m: r'\ ' * len(m.group(0)), sub_title)
        lines.append(rf"\begin{{center}}\large\bfseries {tex_sub_title}\end{{center}}")
        
    if is_exam_style:
        lines.append(r"\begin{center}")
        lines.append(f"    本试卷共 \\pageref{{LastPage}} 页，{total_q_count} 题。全卷满分 {total_score_sum} 分。考试用时 120 分钟。")
        lines.append(r"\end{center}")
        lines.append("")
        if show_notice:
            lines.append(r"\begin{notice}")
            lines.append(r"  \item 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。")
            lines.append(r"  \item 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。")
            lines.append(r"  \item 考试结束后，将本试卷和答题卡一并交回。")
            lines.append(r"\end{notice}")
        else:
            lines.append(r"% \begin{notice}")
            lines.append(r"%   \item 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。")
            lines.append(r"%   \item 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。")
            lines.append(r"%   \item 考试结束后，将本试卷和答题卡一并交回。")
            lines.append(r"% \end{notice}")
        lines.append("")

    # Group questions by question_type
    grouped = {}
    for item in questions_data:
        q = item.get("question", {})
        q_type = q.get("question_type", "single_choice")
        if q_type not in grouped:
            grouped[q_type] = []
        grouped[q_type].append(item)
        
    labels = custom_type_labels(question_types)
    for q_type in paper_type_order(grouped, paper_type, question_types, section_order):
        if q_type not in grouped or not grouped[q_type]:
            continue
        items = grouped[q_type]
        written_type = is_written_question_type(q_type)
        type_label = _escape_paper_type_label(labels.get(q_type, q_type))
        count = len(items)
        sec_score = sum(it.get("score", 5) for it in items)
        unit_score = items[0].get("score", 5) if count > 0 else 5
        
        # For exam_19, set fixed starting question number according to Gaokao layout:
        # 单选: 1
        # 多选: 9
        # 填空: 12
        # 解答: 15
        if paper_type == "exam_19":
            if q_type == "single_choice":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {1} \ExplSyntaxOff")
            elif q_type == "multi_choice":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {9} \ExplSyntaxOff")
            elif q_type == "fill_in_blank":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {12} \ExplSyntaxOff")
            elif q_type == "detailed_answer":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {15} \ExplSyntaxOff")

        if paper_type == "quiz":
            if q_type == "single_choice":
                section_header = "单选题"
            elif q_type == "multi_choice":
                section_header = "多选题"
            elif q_type == "fill_in_blank":
                section_header = "填空题"
            else:
                section_header = "解答题" if q_type == "detailed_answer" else type_label
        else:
            if q_type == "single_choice":
                section_header = f"选择题：本题共 {count} 小题，每小题 {unit_score} 分，共 {sec_score} 分。\n  在每小题给出的四个选项中，只有一项是符合题目要求的。"
            elif q_type == "multi_choice":
                section_header = f"多选题：本题共 {count} 小题，每小题 {unit_score} 分，共 {sec_score} 分。\n  在每小题给出的四个选项中，有多项符合题目要求。\n  全部选对的得 {unit_score} 分，部分选对的得部分分，有选错的得 0 分。"
            elif q_type == "fill_in_blank":
                section_header = f"填空题：本题共 {count} 小题，每小题 {unit_score} 分，共 {sec_score} 分。"
            else:
                section_header = f"{'解答题' if q_type == 'detailed_answer' else type_label}：本题共 {count} 小题，共 {sec_score} 分。解答应写出文字说明、证明过程或演算步骤。"
            
        lines.append(f"\\section{{\n  {section_header}\n}}")
        lines.append("")
        
        for item in items:
            q = item.get("question", {})
            raw_content = q.get("content", "")
            q_score = item.get("score", 5)
            content_tikz_assets = q.get("content_tikz_assets", [])
            if not isinstance(content_tikz_assets, list):
                content_tikz_assets = []
            tikz_codes = [
                str(asset.get("tikz_code") or "").strip()
                for asset in content_tikz_assets
                if isinstance(asset, dict) and str(asset.get("tikz_code") or "").strip()
            ]
            if not tikz_codes:
                legacy_tikz = q.get("tikz_code", "").strip()
                if legacy_tikz:
                    tikz_codes = [legacy_tikz]


            fig_body = ""
            anchored_raw, detached_raw = _split_export_image_anchors(raw_content)
            cleaned_raw = anchored_raw
            figure_specs = []
            img_matches = _MARKDOWN_IMAGE_RE.findall(detached_raw)
            sources = _inline_tikz_sources(raw_content, content_tikz_assets, str(q.get("tikz_code") or "").strip())
            seen_figures = set()
            for img_path in img_matches:
                key = _tikz_image_key(img_path)
                if key in seen_figures:
                    continue
                seen_figures.add(key)
                code = sources.get(key)
                if code:
                    if r"\begin{tikzpicture}" not in code:
                        code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"
                    figure_specs.append(("tikz", code))
                else:
                    figure_specs.append(("image", os.path.basename(img_path)))
            # Legacy source-only drawings have no Markdown anchor.
            if not _MARKDOWN_IMAGE_RE.search(raw_content):
                if not tikz_codes and r"\begin{tikzpicture}" in raw_content:
                    match = re.search(r'(\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\})', raw_content)
                    if match:
                        tikz_codes = [match.group(1)]
                        cleaned_raw = cleaned_raw.replace(match.group(1), '').strip()
                for code in tikz_codes:
                    if r"\begin{tikzpicture}" not in code:
                        code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"
                    figure_specs.append(("tikz", code))
            preserve_inline_images = bool(_MARKDOWN_IMAGE_RE.search(cleaned_raw))
            cleaned_content = clean_content_for_latex(
                cleaned_raw,
                q_type=q_type,
                preserve_image_positions=preserve_inline_images,
                image_layouts=normalize_image_layouts(q.get("image_layouts", {}), cleaned_raw),
                tikz_sources=(
                    _inline_tikz_sources(
                        raw_content, content_tikz_assets, str(q.get("tikz_code") or "").strip()
                    ) if preserve_inline_images else None
                ),
            )
            env_name = "problem" if written_type else "question"
            points_arg = f"[points = {q_score}]" if written_type else ""

            default_fig_align = "bottom_right" if paper_type == "quiz" else "right"
            fig_align = q.get("figure_align")
            if not fig_align or (
                paper_type == "quiz"
                and fig_align == "right"
                and not q.get("figure_align_custom")
                and not q.get("custom_figure_align")
            ):
                fig_align = default_fig_align
            # 多张插图且原设定为右侧时，默认自动优化为下方居中 (center)
            if len(figure_specs) > 1 and fig_align == "right":
                fig_align = "center"

            figure_size = _normalize_figure_size(q.get("figure_size"))
            figure_height_reserve_cm = _figure_height_limit_cm(figure_size)
            fig_elements = []
            compact_auto_images = len(img_matches) > 1 or bool(tikz_codes)
            for figure_kind, figure_value in figure_specs:
                if figure_kind == "tikz":
                    fig_elements.append(
                        _bounded_tikz(
                            figure_value,
                            figure_size=figure_size,
                            figure_align=fig_align,
                        )
                    )
                    continue

                if figure_size == "auto":
                    image_width = "3.8cm" if compact_auto_images else "5.0cm"
                    image_height = _DEFAULT_FIGURE_MAX_HEIGHT
                else:
                    image_width, image_height = _FIGURE_SIZE_LIMITS[figure_size]
                    if fig_align == "right":
                        image_width = "5.0cm"
                fig_elements.append(
                    _bounded_includegraphics(
                        figure_value,
                        image_width,
                        image_height,
                    )
                )

            if fig_elements:
                figure_separator = (
                    "\n\\par\\vspace{2pt}\n"
                    if len(fig_elements) > 1 and figure_size in {"medium", "large"}
                    else "\n\\vspace{2pt}\n"
                )
                fig_body = figure_separator.join(fig_elements)

            question_id = q.get("id")
            if question_id is not None:
                lines.append(f"% MathBank-Question-ID: {question_id}")
            lines.append(f"\\begin{{{env_name}}}{points_arg}")
            if fig_body:
                choices_part = ""
                choices_start = _export_choices_start(cleaned_content)
                if choices_start is not None:
                    stem_text = cleaned_content[:choices_start].strip()
                    choices_part = cleaned_content[choices_start:]
                else:
                    stem_text = cleaned_content

                space_val = item_solution_space_cm(item, q)
                is_sol_spaced = (written_type and not include_answers and space_val > 0)
                figure_solution_box_height = figure_height_reserve_cm
                figure_in_solution_box = bool(
                    is_sol_spaced
                    and fig_align in {"bottom_left", "center", "bottom_right"}
                    and len(fig_elements) == 1
                )

                if fig_align in {"bottom_left", "center", "bottom_right"}:
                    lines.append(stem_text)
                    if figure_in_solution_box:
                        lines.append(
                            rf"\par\noindent\begin{{minipage}}[t][{figure_solution_box_height:.1f}cm][t]{{\linewidth}}"
                        )
                    lower_environment = {
                        "bottom_left": "flushleft",
                        "center": "center",
                        "bottom_right": "flushright",
                    }[fig_align]
                    split_large_figures = (
                        len(fig_elements) > 1 and figure_size in {"medium", "large"}
                    )
                    if split_large_figures:
                        lines.append(
                            rf"\mathbankneedspace{{{figure_height_reserve_cm + 0.5:.1f}cm}}"
                        )
                        for figure_element in fig_elements:
                            lines.append(rf"\begin{{{lower_environment}}}")
                            lines.append(f"  {figure_element}")
                            lines.append(rf"\end{{{lower_environment}}}")
                    else:
                        lines.append(rf"\begin{{{lower_environment}}}")
                        lines.append(r"  \vspace*{-0.4em}")
                        lines.append(f"  {fig_body}")
                        lines.append(rf"\end{{{lower_environment}}}")
                    if figure_in_solution_box:
                        lines.append(r"\end{minipage}")
                else:  # default "right"
                    figure_in_solution_box = False
                    lines.append(r"\noindent\begin{minipage}[t]{\dimexpr\linewidth-5.8cm\relax}")
                    lines.append(r"  \setlength{\parindent}{2em}")
                    lines.append(r"  \hangindent=0pt")
                    lines.append(r"  \hangafter=0")
                    # The question environment already owns the item label.  A minipage
                    # starts a fresh paragraph, so suppress its first-line indent while
                    # preserving the configured indent for any later paragraphs.
                    lines.append(f"  \\noindent {stem_text}")
                    lines.append(r"\end{minipage}%")
                    lines.append(r"\hfill")
                    lines.append(r"\begin{minipage}[t]{5.2cm}")
                    lines.append(r"  \vspace{-2.0em}")
                    lines.append(r"  \raggedleft")
                    lines.append(f"  \\adjustbox{{valign=t}}{{{fig_body}}}")
                    lines.append(r"\end{minipage}")

                if choices_part:
                    lines.append(choices_part)
            else:
                lines.append(cleaned_content)
            
            # Inject solution space for detailed_answer questions on papers
            if written_type and not include_answers:
                space_val = item_solution_space_cm(item, q)
                if space_val > 0:
                    remaining_space = max(0.0, space_val - figure_solution_box_height) if (
                        fig_body and figure_in_solution_box) else space_val
                    if remaining_space > 0:
                        lines.append(f"\\mathbanksolutionspace{{{remaining_space:.1f}cm}}")

            lines.append(f"\\end{{{env_name}}}")

            if include_answers:
                ans_text = q.get("answer_markdown", "").strip()
                if ans_text:
                    lines.append(r"\begin{solution}")
                    lines.append(clean_content_for_latex(ans_text))
                    lines.append(r"\end{solution}")
            lines.append("")

    lines.append(r"\end{document}")
    return _with_content_source_tex_notice("\n".join(lines))

def collect_referenced_images(
    questions_data: list,
    uploads_dir: str,
    upload_url_prefix: str = "static/uploads",
) -> list:
    """
    Find all referenced image file paths in static/uploads directory.
    Returns list of absolute image file paths.
    """
    image_paths = []

    def add_reference(reference: str) -> None:
        try:
            path = resolve_upload_asset(
                reference,
                uploads_dir=uploads_dir,
                url_prefix=upload_url_prefix,
            )
        except (AssetSecurityError, TypeError):
            return
        absolute = str(path)
        if absolute not in image_paths:
            image_paths.append(absolute)

    for item in questions_data:
        q = item.get("question", {})
        imgs = q.get("image_paths", [])
        if isinstance(imgs, list):
            for img_rel in imgs:
                add_reference(img_rel)
        
        # Also parse content for Markdown image paths
        content = q.get("content", "") + " " + q.get("answer_markdown", "")
        found = re.findall(r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)', content)
        for fname in found:
            add_reference(fname)

    return image_paths


def _latex_failure_message(processes: list, temp_dir: str) -> str:
    """Collect the first actionable TeX error plus the final log summary."""
    log_parts = []
    for proc in processes:
        if proc is None:
            continue
        log_parts.extend((getattr(proc, "stdout", "") or "", getattr(proc, "stderr", "") or ""))
    log_path = os.path.join(temp_dir, "paper.log")
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="ignore") as log_file:
            log_parts.append(log_file.read())
    log_txt = "\n".join(part for part in log_parts if part)
    first_error = log_txt.find("\n!")
    if len(log_txt) <= 8000:
        diagnostic = log_txt
    elif first_error >= 0:
        diagnostic = f"{log_txt[max(0, first_error - 1000):first_error + 3000]}\n...\n{log_txt[-4000:]}"
    else:
        diagnostic = f"{log_txt[:2000]}\n...\n{log_txt[-6000:]}"
    return f"编译失败，无法生成完整 PDF:\n{diagnostic}"


def _has_latex_package(tex_content: str, package: str) -> bool:
    for match in re.finditer(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{([^}]*)\}", tex_content):
        packages = {item.strip() for item in match.group(1).split(",")}
        if package in packages:
            return True
    return False


def _inject_latex_package(tex_content: str, package: str) -> str | None:
    """Insert an allowlisted package without changing generated source line numbers."""
    if package not in _AUTO_LATEX_PACKAGES or _has_latex_package(tex_content, package):
        return None
    document_class = re.search(r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}", tex_content)
    if not document_class:
        return None
    insertion = document_class.group(0) + rf"\usepackage{{{package}}}"
    return tex_content[:document_class.start()] + insertion + tex_content[document_class.end():]


def _clear_latex_intermediates(temp_dir: str) -> None:
    for suffix in ("aux", "log", "out", "toc", "xdv", "pdf"):
        path = os.path.join(temp_dir, f"paper.{suffix}")
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def compile_tex_to_pdf(tex_content: str, image_paths: list = None) -> tuple:
    """
    Compiles LaTeX string into PDF using system xelatex.
    Features an in-memory LRU cache based on TeX content and image mtime hashes.
    Returns (pdf_bytes, log_output_or_error).
    """
    if image_paths is None:
        image_paths = []

    try:
        image_paths = assert_tex_image_files([tex_content], image_paths)
    except (OSError, ValueError) as exc:
        return None, str(exc)

    # 1. Compute MD5 Cache Key from TeX content & image modification times
    img_signatures = []
    for img_p in image_paths:
        if os.path.exists(img_p):
            img_signatures.append(f"{img_p}:{os.path.getmtime(img_p)}")
    
    source_notice = content_sources_notice()
    cache_raw_str = f"{tex_content}||{'|'.join(img_signatures)}||{source_notice}"
    cache_key = hashlib.md5(cache_raw_str.encode("utf-8")).hexdigest()

    # 2. Check LRU Cache
    with _PDF_CACHE_LOCK:
        if cache_key in _PDF_CACHE:
            result = _PDF_CACHE.pop(cache_key)
            _PDF_CACHE[cache_key] = result
            if result[0] is not None:
                print(f"[PDF_CACHE_HIT] Reusing cached PDF. Key: {cache_key[:10]}... (Total Cached: {len(_PDF_CACHE)})", flush=True)
                return result

    # 3. Cache Miss: Perform xelatex compilation
    print(f"[PDF_CACHE_MISS] Compiling TeX via xelatex. Key: {cache_key[:10]}...", flush=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, "paper.tex")
        # Copy images into temp_dir
        for img_p in image_paths:
            try:
                shutil.copy(img_p, temp_dir)
            except OSError:
                return None, "导出图片复制失败：" + os.path.basename(img_p)

        # Try compiling with xelatex (requires 2 passes to resolve \pageref{LastPage} and .aux references)
        try:
            cmd = [
                "xelatex",
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "paper.tex",
            ]
            current_tex = tex_content
            auto_loaded_packages = []
            failure_message = ""

            for repair_round in range(_MAX_AUTO_PACKAGE_REPAIRS + 1):
                _clear_latex_intermediates(temp_dir)
                with open(tex_path, "w", encoding="utf-8") as tex_file:
                    tex_file.write(current_tex)

                # Pass 1: Generate .aux file and initial layout.
                first_proc = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=build_restricted_tex_environment(temp_dir),
                )
                if first_proc.returncode != 0:
                    failure_message = _latex_failure_message([first_proc], temp_dir)
                    local = build_local_latex_diagnostic(failure_message, current_tex)
                    package = str(local.get("package") or "")
                    repaired_tex = _inject_latex_package(current_tex, package)
                    if repair_round < _MAX_AUTO_PACKAGE_REPAIRS and repaired_tex is not None:
                        current_tex = repaired_tex
                        auto_loaded_packages.append(package)
                        continue
                    if auto_loaded_packages:
                        failure_message = (
                            f"[MathBank 自动修复] 已尝试加载宏包：{', '.join(auto_loaded_packages)}。\n"
                            + failure_message
                        )
                    return (None, failure_message)

                # Pass 2: Resolve \pageref{LastPage} and cross references from .aux.
                proc = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=build_restricted_tex_environment(temp_dir),
                )
                pdf_path = os.path.join(temp_dir, "paper.pdf")
                if proc.returncode == 0 and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                    with open(pdf_path, "rb") as pf:
                        pdf_bytes = pf.read()
                    pdf_bytes = _with_content_source_pdf_metadata(pdf_bytes, source_notice)

                    # Save to LRU Cache on success, using the user's original source key.
                    with _PDF_CACHE_LOCK:
                        if cache_key in _PDF_CACHE:
                            _PDF_CACHE.pop(cache_key)
                        _PDF_CACHE[cache_key] = (pdf_bytes, proc.stdout)
                        while len(_PDF_CACHE) > _MAX_PDF_CACHE_SIZE:
                            _PDF_CACHE.popitem(last=False)

                    return (pdf_bytes, proc.stdout)

                failure_message = _latex_failure_message([first_proc, proc], temp_dir)
                local = build_local_latex_diagnostic(failure_message, current_tex)
                package = str(local.get("package") or "")
                repaired_tex = _inject_latex_package(current_tex, package)
                if repair_round < _MAX_AUTO_PACKAGE_REPAIRS and repaired_tex is not None:
                    current_tex = repaired_tex
                    auto_loaded_packages.append(package)
                    continue
                if auto_loaded_packages:
                    failure_message = (
                        f"[MathBank 自动修复] 已尝试加载宏包：{', '.join(auto_loaded_packages)}。\n"
                        + failure_message
                    )
                return (None, failure_message)

            return (None, failure_message or "LaTeX 自动修复后仍未能生成 PDF。")
        except FileNotFoundError:
            return (None, "系统未检测到 xelatex 编译器，请确保已安装 TeX Live / MiKTeX / MacTeX 并加入 PATH。")
        except subprocess.TimeoutExpired:
            return (None, "LaTeX 编译超时（超过 30 秒）。")
        except Exception as e:
            return (None, f"编译过程异常: {str(e)}")

def extract_figures_for_answer_sheet(content: str) -> str:
    """Extract TikZ code or images from question content for answer sheet embedding."""
    if not content:
        return ""
    figs = []
    # 1. TikZ blocks
    tikz_blocks = re.findall(r'(\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\})', content)
    for t in tikz_blocks:
        figs.append(t)
    # 2. Markdown image ![...](path)
    img_matches = re.findall(r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)', content)
    for img_name in img_matches:
        base_n = os.path.basename(img_name)
        if tikz_blocks and base_n.startswith("tikz_"):
            continue
        img_tag = f"\\includegraphics[max width=3.4cm]{{{base_n}}}"
        if img_tag not in figs:
            figs.append(img_tag)
    # 3. Direct \includegraphics
    direct_imgs = re.findall(r'(\\includegraphics(?:\[.*?\])?\{.*?\})', content)
    for d in direct_imgs:
        if d not in figs:
            figs.append(d)
    return "\n".join(figs)

def clear_pdf_cache():
    """Clear all entries in PDF compilation LRU cache."""
    with _PDF_CACHE_LOCK:
        _PDF_CACHE.clear()
        print("[PDF_CACHE] PDF LRU memory cache cleared.", flush=True)

def build_answer_sheet_latex(title: str, subtitle: str, questions_data: list) -> str:
    """
    Generate Answer Sheet (答题卡.tex) LaTeX code based on templates/答题卡.tex or built-in default.
    Dynamic updates: title, question scores, and embedded TikZ/image nodes for Q15-Q19.
    """
    template_path = str(TEMPLATES_DIR / "答题卡.tex")
    content = ""
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            pass

    if not content:
        content = r"""
\documentclass[UTF8, fontset=fandol, 11pt, oneside]{ctexart}
\usepackage{amsmath, amsthm, amssymb, graphicx}
\usepackage[export]{adjustbox}
\usepackage[bookmarks=true, colorlinks, citecolor=blue, linkcolor=black]{hyperref}
\usepackage[a3paper, landscape,left=0.7cm, right=0.7cm, top=0.6cm, bottom=0.6cm]{geometry}
\usepackage{diagbox, tikz, fancyhdr, makecell, caption, float, eso-pic}
\linespread{1.625}
\setcounter{page}{1}
\pagestyle{fancy}
\fancyhf{}
\definecolor{mycolor}{RGB}{255,50,165}
\AddToShipoutPictureFG{%
    \begin{tikzpicture}[remember picture, overlay]
        \fill[black] ([xshift=0.7cm, yshift=0.6cm] current page.south west) rectangle ++(10mm, 6mm);
        \fill[black] ([xshift=13.9cm, yshift=0.6cm] current page.south west) rectangle ++(10mm, 6mm);
        \fill[black] ([xshift=27.1cm, yshift=0.6cm] current page.south west) rectangle ++(10mm, 6mm);
        \fill[black] ([xshift=-0.7cm, yshift=0.6cm] current page.south east) rectangle ++(-10mm, 6mm);
        \fill[black] ([xshift=-0.7cm, yshift=-0.6cm] current page.north east) rectangle ++(-10mm, -6mm);
        \fill[black] ([xshift=0.7cm, yshift=-0.6cm] current page.north west) rectangle ++(10mm, -6mm);
        \fill[black] ([xshift=13.9cm, yshift=-0.6cm] current page.north west) rectangle ++(10mm, -6mm);
        \fill[black] ([xshift=27.1cm, yshift=-0.6cm] current page.north west) rectangle ++(10mm, -6mm);
        \node at (current page.south) [anchor=south, yshift=0.6cm] {\color{mycolor}\textbf{数学答题卡第\thepage 面（共2面）}};
    \end{tikzpicture}%
}
\begin{document}
\centering
\begin{tikzpicture}
    \useasboundingbox (0,0) rectangle (39.2,28.25);
    \node[font=\fontsize{16pt}{16pt}\selectfont] at (6.45,27.3) {\color{mycolor}\textbf{2026年普通高等学校招生全国统一考试}};
    \node[font=\fontsize{22pt}{22pt}\selectfont] at (6.45,26.3){\textbf{数学答题卡}};
    \node at (6.45,25) {考场号：\textcolor{mycolor}{\underline{\hspace{1.3cm}}} \hspace{0.6em}座位号：\textcolor{mycolor}{\underline{\hspace{1.3cm}}}\hspace{0.6em}姓名：\textcolor{mycolor}{\underline{\hspace{2.2cm}}} \hspace{0.6em}班级：\textcolor{mycolor}{\underline{\hspace{1.8cm}}}};
    
    \def\rows{10}\def\cols{10}\def\cellw{0.8}\def\cellh{0.5}\def\beginx{4.9}\def\beginy0{24.2}\def\fontHeight{0.6}\def\smallHeight{0.2}\def\selectw{0.5}\def\selecth{0.25}
    \foreach \col [evaluate=\col as \x using \beginx+\col*\cellw] in {0,...,\cols} { \draw[mycolor] (\x, \beginy0-\fontHeight) -- (\x,\beginy0-\fontHeight-\cellw-\cellh*\rows-2*\smallHeight); }
    \draw[mycolor] (\beginx,\beginy0-\fontHeight) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight);
    \draw[mycolor] (\beginx,\beginy0) -- (\beginx+\cellw*\cols,\beginy0);
    \draw[mycolor] (\beginx,\beginy0) -- (\beginx,\beginy0-\fontHeight);
    \draw[mycolor] (\beginx+\cellw*\cols,\beginy0) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight);
    \draw[mycolor] (\beginx,\beginy0-\fontHeight-\cellw) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight-\cellw);
    \draw[mycolor] (\beginx,\beginy0-\fontHeight-\cellw-\smallHeight*2-\cellh*\rows) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight-\cellw-\smallHeight*2-\cellh*\rows);
    \node at (\beginx+0.5*\cols*\cellw,\beginy0-0.5*\fontHeight) {\small\textbf{考\hspace{1em}生\hspace{1em}号}};
    \foreach \row in {1,...,\rows} {
        \foreach \col in {1,...,\cols} {
            \node at (\beginx+\col*\cellw-0.5*\cellw,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh) {\footnotesize\textcolor{mycolor}{\the\numexpr \row - 1 \relax}};
            \draw[mycolor] (\beginx+\col*\cellw-0.5*\cellw-0.5*\selectw+0.25*\selectw,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh+0.5*\selecth) -- ++(-0.25*\selectw,0) -- ++(0,-\selecth) -- ++(0.25*\selectw,0);
            \draw[mycolor] (\beginx+\col*\cellw-0.5*\cellw-0.5*\selectw+0.75*\selectw,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh+0.5*\selecth) -- ++(0.25*\selectw,0) -- ++(0,-\selecth) -- ++(-0.25*\selectw,0);
        }
    }
    \foreach \row in {1,...,\rows} { \fill[black] (-0.7,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh+0.5*\selecth) rectangle ++(\selectw,-\selecth); }
    \foreach \col in{1,2,...,15} { \fill[black] (\beginx+\col*\cellw-0.5*\cellw-0.5*\selectw-4.5,28.1) rectangle ++(\selectw,-\selecth); }

    \def\startx{0}\def\starty{24.2}\def\squarew{4.7}\def\squareh{6.8}\def\fontmarginx{0.1}\def\fontmarginy{0.1}
    \draw[mycolor] (\startx,\starty) rectangle ++(\squarew,-\squareh);
    \node[text width=4.3cm,align=left,anchor=north west] at (\startx+\fontmarginx,\starty-\fontmarginy)
        {\fontsize{8pt}{8pt}\selectfont\textbf{注意事项}：\\1．答题前，考生务必用黑色字迹的钢笔或签字笔将考场号、座位号、姓名和考生号填写在答题卡上，并用2B铅笔将考生号对应的数字涂黑。\\2．选择题的选出每小题答案后，用2B铅笔把答题卡上对应题目的答案标号涂黑。如需改动，用橡皮擦擦干净后，再选其它答案标号涂黑。非选择题的答案不能超出指定答题区域。\\3．答题卡保持卡面整洁，不要折叠和弄破。\\};

    \def\bigx{0}\def\bigy{16.9}\def\bigw{12.9}\def\bigh{16.3}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\chosex{0.2}\def\chosey{16.2}\def\chosew{12.5}\def\choseh{2.7}
    \draw[black] (\chosex,\chosey) rectangle ++(\chosew,-\choseh);
    \node[anchor=north west, align=left, font=\fontsize{10pt}{9pt}\selectfont] at (\chosex+0.1, \chosey+0.6) {\textbf{一、选择题}\\};
    \def\chosenumx{0.7}\def\chosenumy{15.67}\def\chosedistancey{0.55}\def\numenglishd{0.7}\def\chosedistancex{0.75}\def\distance{1.1}\def\quantity{11}
    \pgfmathsetmacro{\d}{\numenglishd+3*\chosedistancex+\distance}
    \foreach \row in {0,...,\numexpr\quantity-1\relax} {
        \pgfmathtruncatemacro{\nx}{floor(\row/4)}
        \node at (\chosenumx+\nx*\d,\chosenumy-\row*\chosedistancey+\nx*4*\chosedistancey) {\the\numexpr \row + 1 \relax};
        \foreach \col in {0,1,2,3} {
            \pgfmathsetmacro{\x}{{"A","B","C","D"}[\col]}
            \node at (\chosenumx+\numenglishd+\chosedistancex*\col+\nx*\d,\chosenumy-\row*\chosedistancey+\nx*4*\chosedistancey) {\footnotesize\textcolor{mycolor}{\x}};
            \draw[mycolor] (\chosenumx+\numenglishd-0.25*\selectw+\chosedistancex*\col+\nx*\d,\chosenumy-\row*\chosedistancey+0.5*\selecth+\nx*4*\chosedistancey) -- ++(-0.25*\selectw,0) -- ++(0,-\selecth) -- ++(0.25*\selectw,0);
            \draw[mycolor] (\chosenumx+\numenglishd+0.25*\selectw+\chosedistancex*\col+\nx*\d,\chosenumy-\row*\chosedistancey+0.5*\selecth+\nx*4*\chosedistancey) -- ++(0.25*\selectw,0) -- ++(0,-\selecth) -- ++(-0.25*\selectw,0);
        }
    }
    \foreach \row in{0,1,2,3} { \fill[black] (-0.7,\chosenumy-\row*\chosedistancey+0.5*\selecth) rectangle ++(\selectw,-\selecth); }

    \def\blankx{0.2}\def\blanky{12.8}\def\blankw{12.5}\def\blankh{1.4}
    \def\blanknumx{0.7}\def\blanknumy{12}
    \pgfmathsetmacro{\distancew}{\numenglishd + 3*\chosedistancex + \distance}
    \draw[black] (\blankx,\blanky) rectangle ++(\blankw,-\blankh);
    \node[anchor=north west, align=left, font=\fontsize{10pt}{9pt}\selectfont] at (\blankx+0.1, \blanky+0.6) {\textbf{二、填空题}\\};
    \foreach \row in {0,1,2} {
        \node at (\blanknumx+\row*\distancew+0.1,\blanknumy) {\the\numexpr \row + 12 \relax.};
        \draw[mycolor] (\blanknumx+\row*\distancew+0.4,\blanknumy-0.2) -- ++(\distancew-1,0);
    }
    
    \def\ebx{0.2}\def\eby{10.7}\def\ebw{12.5}\def\ebh{9.5}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    \node[anchor=north west, align=left, font=\fontsize{10pt}{9pt}\selectfont] at (\ebx+0.1, \eby+0.6) {\textbf{三、解答题}\\};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{1.5}\def\exy{10.2}
    \node at (\exx,\exy) {15.（13分）};

    \def\bigx{13.2}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{13.4}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    \draw (\ebx,\eby-0.25*\ebh) -- (\ebx+\ebw,\eby-0.25*\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{14.7}\def\exy{26.3}
    \node at (\exx,\exy-0.25*\ebh) {16.（15分）};
    \node at (\exx,\exy) {\textcolor{mycolor}{\textbf{（续15题）}}};

    \def\bigx{26.4}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{26.6}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{27.9}\def\exy{26.3}
    \node at (\exx,\exy) {17.（15分）};
\end{tikzpicture}
\newpage
\begin{tikzpicture}
    \useasboundingbox (0,0) rectangle (39.2,28.25);
    \def\bigx{0}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{0.2}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};

    \def\exx{1.5}\def\exy{26.3}
    \node at (\exx,\exy) {18.（17分）};

    \def\bigx{13.2}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{13.4}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    \draw (\ebx,\eby-0.5*\ebh) -- (\ebx+\ebw,\eby-0.5*\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{14.7}\def\exy{26.3}
    \node at (\exx,\exy-0.5*\ebh) {19.（17分）};
    \node at (\exx,\exy) {\textcolor{mycolor}{\textbf{（续18题）}}};

    \def\bigx{26.4}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{26.6}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{27.9}\def\exy{26.3}
    \node at (\exx,\exy) {\textcolor{mycolor}{\textbf{（续19题）}}};
\end{tikzpicture}
\end{document}
"""

    # Answer-sheet figures use the same ZIP layout as the main paper.  Inject
    # the search path for both the built-in fallback and any future file-backed
    # answer-sheet template without rewriting every \includegraphics command.
    if r"\graphicspath" not in content:
        content = content.replace(
            r"\begin{document}",
            "\\graphicspath{{images/}{./}}\n\\begin{document}",
            1,
        )

    # `max width` is an adjustbox key, not a native graphicx key.  Normalize a
    # file-backed template that loads adjustbox without options, or inject the
    # correct package declaration when the template does not load it at all.
    adjustbox_package = re.compile(
        r"\\usepackage(?:\[([^\]]*)\])?\{adjustbox\}"
    )
    adjustbox_match = adjustbox_package.search(content)
    if adjustbox_match:
        package_options = [
            option.strip()
            for option in (adjustbox_match.group(1) or "").split(",")
            if option.strip()
        ]
        if "export" not in package_options:
            package_options.append("export")
            content = adjustbox_package.sub(
                lambda _match: (
                    f"\\usepackage[{','.join(package_options)}]{{adjustbox}}"
                ),
                content,
                count=1,
            )
    else:
        content = content.replace(
            r"\begin{document}",
            "\\usepackage[export]{adjustbox}\n\\begin{document}",
            1,
        )

    lines = content.splitlines()
    cleaned_lines = []
    for line in lines:
        sline = line.strip()
        if sline.startswith("作者：") or sline.startswith("链接：") or sline.startswith("来源：") or sline.startswith("著作权归作者所有"):
            cleaned_lines.append("% " + line)
        else:
            cleaned_lines.append(line)
    tex_str = "\n".join(cleaned_lines)

    paper_title = title.strip() or "2026年普通高等学校招生全国统一考试"
    tex_str = re.sub(r'202\d年普通高等学校招生全国统一考试', paper_title, tex_str)

    detailed_questions = []
    for item in questions_data:
        q = item.get("question", {})
        if q.get("question_type") == "detailed_answer":
            detailed_questions.append(item)

    # Map for Q15..Q19 (full node line, template line, figure top-right anchor coordinates locked 0.7cm inside right border)
    coords_map = [
        ("15", r"\node at (\exx,\exy) {15.（13分）};", r"\node at (\exx,\exy) {{15.（{score}分）}};", "12.0, 10.2"),
        ("16", r"\node at (\exx,\exy-0.25*\ebh) {16.（15分）};", r"\node at (\exx,\exy-0.25*\ebh) {{16.（{score}分）}};", "25.2, 20.0"),
        ("17", r"\node at (\exx,\exy) {17.（15分）};", r"\node at (\exx,\exy) {{17.（{score}分）}};", "38.4, 26.3"),
        ("18", r"\node at (\exx,\exy) {18.（17分）};", r"\node at (\exx,\exy) {{18.（{score}分）}};", "12.0, 26.3"),
        ("19", r"\node at (\exx,\exy-0.5*\ebh) {19.（17分）};", r"\node at (\exx,\exy-0.5*\ebh) {{19.（{score}分）}};", "25.2, 13.5"),
    ]

    for idx in range(min(5, len(detailed_questions))):
        item = detailed_questions[idx]
        q = item.get("question", {})
        score = item.get("score", 15)
        q_num, default_line, new_line_tmpl, fig_coords = coords_map[idx]

        new_line = new_line_tmpl.format(score=score)

        # Check for figures
        q_content = q.get("content", "")
        content_tikz_assets = q.get("content_tikz_assets", [])
        tikz_codes = [
            str(asset.get("tikz_code") or "").strip()
            for asset in content_tikz_assets
            if isinstance(asset, dict) and str(asset.get("tikz_code") or "").strip()
        ] if isinstance(content_tikz_assets, list) else []
        if not tikz_codes:
            legacy_tikz = q.get("tikz_code", "").strip()
            if legacy_tikz:
                tikz_codes = [legacy_tikz]
        full_content = q_content + ("\n" + "\n".join(tikz_codes) if tikz_codes else "")
        fig_code = extract_figures_for_answer_sheet(full_content)
        if fig_code:
            fig_node = f"\\node[anchor=north east, inner sep=0pt, outer sep=0pt] at ({fig_coords}) {{\\resizebox{{4.2cm}}{{!}}{{{fig_code}}}}};"
            replacement = f"{new_line}\n    {fig_node}"
        else:
            replacement = new_line

        tex_str = tex_str.replace(default_line, replacement)

    return tex_str

def create_tex_zip_package(title: str, tex_content: str, ans_tex_content: str, image_paths: list, answer_sheet_tex: str = None, preflight_report: dict = None) -> bytes:
    """
    Creates an in-memory ZIP package containing paper.tex, answer.tex, answer_sheet.tex (if present), and referenced images.
    """
    image_paths = assert_tex_image_files([tex_content, ans_tex_content, answer_sheet_tex], image_paths)
    source_files = content_source_export_files()
    notice = source_files[CONTENT_SOURCES_NOTICE_NAME].decode("utf-8")
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("试卷正文.tex", _with_content_source_tex_notice(tex_content, notice).encode("utf-8"))
        if ans_tex_content:
            zf.writestr("参考答案与解析.tex", _with_content_source_tex_notice(ans_tex_content, notice).encode("utf-8"))
        if answer_sheet_tex:
            zf.writestr("答题卡.tex", _with_content_source_tex_notice(answer_sheet_tex, notice).encode("utf-8"))
        for filename, data in source_files.items():
            zf.writestr(filename, data)
            
        if preflight_report is not None:
            import json
            zf.writestr("export-preflight.json", json.dumps(preflight_report, ensure_ascii=False, indent=2))
        for img_p in image_paths:
            fname = os.path.basename(img_p)
            zf.write(img_p, arcname=f"images/{fname}")

    return zip_buffer.getvalue()

def create_full_bundle_zip_package(
    title: str,
    tex_content: str,
    ans_tex_content: str,
    image_paths: list,
    answer_sheet_tex: str = None,
    main_pdf_bytes: bytes = None,
    ans_pdf_bytes: bytes = None,
    answer_sheet_pdf_bytes: bytes = None,
    preflight_report: dict = None,
) -> bytes:
    """
    Creates an in-memory ZIP package containing LaTeX TeX files, compiled PDF files, and referenced images.
    """
    zip_buffer = BytesIO(create_tex_zip_package(title, tex_content, ans_tex_content, image_paths, answer_sheet_tex, preflight_report))
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zf:
        # Reuse the source package so a combined archive has one catalog/notice.
        if main_pdf_bytes:
            zf.writestr("试卷正文.pdf", main_pdf_bytes)
        if ans_pdf_bytes:
            zf.writestr("参考答案与解析.pdf", ans_pdf_bytes)
        if answer_sheet_pdf_bytes:
            zf.writestr("答题卡.pdf", answer_sheet_pdf_bytes)
            
    return zip_buffer.getvalue()
