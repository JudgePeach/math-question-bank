"""Editable Word exam export with native OMML equations.

The application owns the document structure and layout. Pandoc is used only as
an offline LaTeX-math-to-OMML adapter; it never receives the complete exam
document. Formulas that Pandoc cannot translate are rendered as visible PNG
fallbacks instead of being silently dropped.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Iterable
from lxml import etree

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Inches, Pt, RGBColor

from mathbank.runtime_components import find_usable_pandoc
from mathbank.question_types import PAPER_TYPE_ORDER, custom_type_labels, is_written_question_type, paper_type_order
from mathbank.geometry_symbols import normalize_school_math_symbols
from mathbank.markdown_tables import find_markdown_tables, normalize_table_cell_math
from mathbank.source_comments import strip_source_attribution_comment
from mathbank.content_sources import content_source_export_files, content_sources_notice
from mathbank.word_lists import LatexList, find_latex_lists
from mathbank.paper_layout import item_solution_space_cm


MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TYPE_LABELS = {
    "single_choice": "选择题",
    "multi_choice": "多选题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题",
}
TYPE_ORDER = PAPER_TYPE_ORDER
EXAM_19_STARTS = {
    "single_choice": 1,
    "multi_choice": 9,
    "fill_in_blank": 12,
    "detailed_answer": 15,
}
from .image_layout import (
    split_image_anchors, image_key, normalize_image_layouts, SIZE_CM,
    DETACHED_FIGURE_SIZE_CM, TABLE_IMAGE_MAX_HEIGHT_CM, COMPACT_CHOICE_IMAGE_SIZE_CM,
)

IMAGE_PATTERN = re.compile(r"!\[.*?\]\(([^)]+)\)")
CHOICES_PATTERN = re.compile(
    r"\\begin\{choices\}([\s\S]*?)\\end\{choices\}", re.IGNORECASE
)
TABULAR_PATTERN = re.compile(
    r"\\begin\{tabular\}\s*\{([^}]*)\}([\s\S]*?)\\end\{tabular\}",
    re.IGNORECASE,
)
BARE_MATH_ENVS = ("cases", "aligned", "alignedat", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix")
CJK_BODY_FONT = "SimSun"
CJK_HEADING_FONT = "SimHei"
CJK_TITLE_FONT = "STZhongsong"
LATIN_FONT = "Times New Roman"
WORD_FILLIN_BLANK_SPACES = 18
WORD_RIGHT_FIGURE_MAX_WIDTH_INCHES = 1.55
WORD_AUTO_SINGLE_FIGURE_WIDTH_INCHES = 2.35
WORD_AUTO_MULTI_FIGURE_WIDTH_INCHES = 1.85
WORD_FIGURE_SIZE_WIDTHS_INCHES = {
    size: dimensions[0] / 2.54 for size, dimensions in DETACHED_FIGURE_SIZE_CM.items()
}
WORD_FIGURE_SIZE_HEIGHTS_INCHES = {
    size: dimensions[1] / 2.54 for size, dimensions in DETACHED_FIGURE_SIZE_CM.items()
}
WORD_AUTO_FIGURE_MAX_HEIGHT_INCHES = 2.36
STIX_TWO_MATH_PATHS = (
    Path("/System/Library/Fonts/Supplemental/STIXTwoMath.otf"),
    Path("/Library/Fonts/STIXTwoMath.otf"),
    Path.home() / "Library/Fonts/STIXTwoMath.otf",
)


def _resolve_word_math_font() -> str:
    """Choose a complete OpenType math font that exists on this platform."""
    if any(path.is_file() for path in STIX_TWO_MATH_PATHS):
        return "STIX Two Math"
    return "Cambria Math"


MATH_FONT = _resolve_word_math_font()
BODY_FONT_SIZE = 10.5
SMALL_FONT_SIZE = 10.5
SECTION_FONT_SIZE = 11.0


class WordExportError(RuntimeError):
    """Raised when a DOCX cannot be generated safely."""


@dataclass
class WordExportDiagnostics:
    native_formulas: int = 0
    fallback_formulas: int = 0
    failed_formulas: int = 0
    missing_images: int = 0
    answer_card_omitted: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "native_formulas": self.native_formulas,
            "fallback_formulas": self.fallback_formulas,
            "failed_formulas": self.failed_formulas,
            "missing_images": self.missing_images,
            "answer_card_omitted": self.answer_card_omitted,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MathToken:
    latex: str
    display: bool = False


@dataclass(frozen=True)
class BlankToken:
    """A Word-native underlined answer blank, kept outside OMML."""


@dataclass(frozen=True)
class TableCellSpec:
    text: str
    colspan: int = 1
    rowspan: int = 1
    alignment: str | None = None


@dataclass(frozen=True)
class TableCellPlacement:
    cell: TableCellSpec
    start_col: int


@dataclass
class PreparedQuestion:
    question: dict
    score: int
    stem: str
    choices: list[str]
    answer: str
    images: list[Path]
    solution_space: float


def _should_preserve_inline_image_positions(content: str) -> bool:
    text = str(content or "")
    matches = list(IMAGE_PATTERN.finditer(text))
    if not matches:
        return False
    trailing = IMAGE_PATTERN.sub("", text[matches[0].start():]).strip()
    return bool(trailing)


def _split_content_segments(content: str) -> list[tuple[str, str | LatexList, str]]:
    """Locate whole lists/tables before splitting their surrounding prose."""

    source = str(content or "")
    segments: list[tuple[str, str | LatexList, str]] = []

    def add_text(value: str) -> None:
        for paragraph in re.split(r"\n\s*\n", value):
            if paragraph.strip():
                segments.append(("text", paragraph.strip(), ""))

    lists = find_latex_lists(source)
    blocks = [(item.start, item.end, "list", item, "")
              for item in lists]
    tables = [(match.start(), match.end(), "table", match.group(1), match.group(2))
              for match in TABULAR_PATTERN.finditer(source)]
    tables.extend((table.start, table.end, "markdown_table",
                   source[table.start:table.end], "")
                  for table in find_markdown_tables(source))
    # Tables inside an item belong to that item, not the surrounding question.
    blocks.extend(block for block in tables if not any(
        item.start < block[1] and block[0] < item.end for item in lists
    ))
    cursor = 0
    for start, end, kind, first, second in sorted(blocks):
        add_text(source[cursor:start])
        segments.append((kind, first, second))
        cursor = end
    add_text(source[cursor:])
    return segments


def _read_latex_group(source: str, start: int) -> tuple[str, int] | None:
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


def _parse_leading_table_command(
    source: str,
    command: str,
    group_count: int,
) -> tuple[list[str], str] | None:
    value = source.strip()
    prefix = "\\" + command
    if not value.startswith(prefix):
        return None
    cursor = len(prefix)
    groups: list[str] = []
    for _ in range(group_count):
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        group = _read_latex_group(value, cursor)
        if group is None:
            return None
        groups.append(group[0])
        cursor = group[1]
    return groups, value[cursor:].strip()


def _parse_table_cell(value: str) -> TableCellSpec:
    text = value.strip()
    colspan = 1
    rowspan = 1
    alignment = None
    for _ in range(2):
        multicolumn = _parse_leading_table_command(text, "multicolumn", 3)
        if multicolumn is not None:
            groups, remainder = multicolumn
            try:
                colspan = max(1, int(groups[0]))
            except ValueError:
                colspan = 1
            spec = groups[1].lower()
            alignment = "l" if "l" in spec else "r" if "r" in spec else "c"
            text = (groups[2] + remainder).strip()
            continue
        multirow = _parse_leading_table_command(text, "multirow", 3)
        if multirow is not None:
            groups, remainder = multirow
            try:
                parsed_rowspan = int(groups[0])
                rowspan = parsed_rowspan if parsed_rowspan > 0 else 1
            except ValueError:
                rowspan = 1
            text = (groups[2] + remainder).strip()
            continue
        break
    return TableCellSpec(
        text=text,
        colspan=colspan,
        rowspan=rowspan,
        alignment=alignment,
    )


def _find_pandoc() -> str | None:
    resolved = find_usable_pandoc()
    return os.fspath(resolved[0]) if resolved else None


def _normalize_formula(formula: str) -> str:
    value = (formula or "").strip()
    value = re.sub(r"^\$\$?|\$\$?$", "", value).strip()
    value = re.sub(r"\\lt\s*", "<", value)
    value = re.sub(r"\\gt\s*", ">", value)
    value = re.sub(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b", "", value)
    return value.strip()


_WORD_EXAMZH_CONSTANTS = {"iu": "i", "upi": "i", "eu": "e", "upe": "e"}
_WORD_FORMULA_COMMAND = re.compile(r"\\(?:[A-Za-z@]+|[^\n])|%")
_WORD_FORMULA_LITERAL_COMMANDS = {
    "string", "detokenize", "unexpanded", "noexpand", "meaning", "verb", "Verb", "lstinline", "texttt",
    "url", "path", "href", "includegraphics", "label", "tikz",
    "def", "gdef", "edef", "xdef", "let", "futurelet", "newcommand",
    "renewcommand", "providecommand", "DeclareRobustCommand", "DeclareMathOperator",
    "NewDocumentCommand", "RenewDocumentCommand", "ProvideDocumentCommand", "DeclareDocumentCommand",
    "newenvironment", "renewenvironment", "catcode", "makeatletter", "makeatother",
    "csname", "endcsname",
}


def _prepare_word_formula(formula: str) -> str:
    r"""Adapt four known zero-argument atoms in a conversion copy only.

    Keep the authored formula as the OMML/fallback cache key and diagnostic.
    Full control tokens consume escaped backslash pairs before aliases; braces
    keep each expansion one atom even in ``\frac\iu\eu`` or ``x^\iu``.
    Literal/definition-bearing input stays intact rather than guessing TeX.
    """
    if not formula or not any("\\" + name in formula for name in _WORD_EXAMZH_CONSTANTS):
        return formula
    if ("`" in formula or re.search(r"https?://|/static/|<\s*(?:code|pre|mathbank-math)\b|\[\[MBM_", formula)
            or re.search(r"\\begin\s*\{(?:verbatim\*?|Verbatim|lstlisting|minted|tikzpicture|pgfpicture|axis)\}", formula)):
        return formula
    commands = list(_WORD_FORMULA_COMMAND.finditer(formula))
    if any(match.group() == "%" or match.group()[1:] in _WORD_FORMULA_LITERAL_COMMANDS
           for match in commands):
        return formula
    return _WORD_FORMULA_COMMAND.sub(
        lambda match: "{\\mathrm{" + _WORD_EXAMZH_CONSTANTS[match.group()[1:]] + "}}"
        if match.group()[1:] in _WORD_EXAMZH_CONSTANTS else match.group(), formula,
    )


def _consume_math(text: str, start: int) -> tuple[MathToken | None, int]:
    if text.startswith("$$", start):
        end = text.find("$$", start + 2)
        if end >= 0:
            return MathToken(_normalize_formula(text[start + 2 : end]), True), end + 2
    if text.startswith(r"\[", start):
        end = text.find(r"\]", start + 2)
        if end >= 0:
            return MathToken(_normalize_formula(text[start + 2 : end]), True), end + 2
    if text.startswith(r"\(", start):
        end = text.find(r"\)", start + 2)
        if end >= 0:
            return MathToken(_normalize_formula(text[start + 2 : end]), False), end + 2
    if text[start : start + 1] == "$" and (start == 0 or text[start - 1] != "\\"):
        cursor = start + 1
        while cursor < len(text):
            if text[cursor] == "$" and text[cursor - 1] != "\\":
                return MathToken(_normalize_formula(text[start + 1 : cursor]), False), cursor + 1
            cursor += 1
    for env_name in BARE_MATH_ENVS:
        opener = rf"\begin{{{env_name}}}"
        if text.startswith(opener, start):
            closer = rf"\end{{{env_name}}}"
            end = text.find(closer, start + len(opener))
            if end >= 0:
                finish = end + len(closer)
                return MathToken(_normalize_formula(text[start:finish]), True), finish
    return None, start


def tokenize_mixed_content(text: str) -> list[str | MathToken | BlankToken]:
    """Split mixed Chinese/LaTeX text without relying on regex lookbehind."""
    from mathbank.fillin_placeholders import normalize_fillin_placeholder_clusters
    source = normalize_school_math_symbols(normalize_fillin_placeholder_clusters(text or ""), target="word")
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[str | MathToken | BlankToken] = []
    plain: list[str] = []
    index = 0
    while index < len(source):
        token, next_index = _consume_math(source, index)
        if token is not None:
            if plain:
                tokens.append("".join(plain))
                plain = []
            if token.latex:
                formula_parts = token.latex.split(r"\fillin")
                for part_index, formula_part in enumerate(formula_parts):
                    if formula_part.strip():
                        tokens.append(MathToken(formula_part.strip(), token.display))
                    if part_index < len(formula_parts) - 1:
                        tokens.append(BlankToken())
            index = next_index
            continue
        if source.startswith(r"\fillin", index):
            if plain:
                tokens.append("".join(plain))
                plain = []
            tokens.append(BlankToken())
            index += len(r"\fillin")
            continue
        plain.append(source[index])
        index += 1
    if plain:
        tokens.append("".join(plain))
    return tokens


def _extract_formulas(texts: Iterable[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for token in tokenize_mixed_content(text):
            if isinstance(token, MathToken) and token.latex and token.latex not in seen:
                values.append(token.latex)
                seen.add(token.latex)
    return values


def _word_formula_sources(source: str) -> list[str]:
    """Collect the same nested item/cell math that the document will render."""
    values: list[str] = []
    for kind, first, second in _split_content_segments(source):
        if kind == "list":
            for item in first.items:
                if item.label is not None:
                    values.append(item.label)
                values.extend(_word_formula_sources(item.content))
        elif kind == "markdown_table":
            for table in find_markdown_tables(first):
                values.extend(
                    part for row in (table.header, *table.rows) for cell in row
                    for literal, part in _markdown_cell_parts(normalize_table_cell_math(cell))
                    if not literal
                )
        else:
            values.extend((first, second))
    return values


def _markdown_cell_parts(value: str) -> list[tuple[bool, str]]:
    """Separate literal code from math-bearing text in a Markdown cell."""
    parts: list[tuple[bool, str]] = []
    cursor = copied = 0
    while cursor < len(value):
        token, next_index = _consume_math(value, cursor)
        if token is not None:
            cursor = next_index
            continue
        code = re.match(r"(`+)(.*?)\1", value[cursor:])
        verb = re.match(
            r"\\(?:verb|Verb|lstinline)\*?(?:\[[^]\n]*\])?"
            r"(?P<sep>[^\w\s{])(?P<body>.*?)\1", value[cursor:])
        match = code or verb
        if match:
            if copied < cursor:
                parts.append((False, value[copied:cursor]))
            parts.append((True, code[2] if code else verb["body"]))
            cursor = copied = cursor + match.end()
        else:
            cursor += 1
    if copied < len(value):
        parts.append((False, value[copied:]))
    return parts


def _decode_markdown_cell_pipes(value: str) -> str:
    parts: list[str] = []
    cursor = copied = 0
    while cursor < len(value):
        token, next_index = _consume_math(value, cursor)
        if token is not None:
            parts.extend((value[copied:cursor].replace(r"\|", "|"), value[cursor:next_index]))
            cursor = copied = next_index
        else:
            cursor += 1
    parts.append(value[copied:].replace(r"\|", "|"))
    return "".join(parts)


class PandocOmmlConverter:
    """Batch-convert unique LaTeX formulas and return serialized m:oMath nodes."""

    def __init__(self, pandoc_path: str | None = None):
        self.pandoc_path = pandoc_path or _find_pandoc()

    def convert_many(self, formulas: Iterable[str]) -> dict[str, bytes]:
        unique = list(dict.fromkeys(item for item in formulas if item))
        if not unique or not self.pandoc_path:
            return {}
        converted: dict[str, bytes] = {}
        self._convert_chunk(unique, converted)
        return converted

    def _convert_chunk(self, formulas: list[str], output: dict[str, bytes]) -> None:
        if not formulas:
            return
        result = self._run_pandoc(formulas)
        output.update(result)
        missing = [item for item in formulas if item not in result]
        if not missing or len(formulas) == 1:
            return
        midpoint = max(1, len(missing) // 2)
        self._convert_chunk(missing[:midpoint], output)
        self._convert_chunk(missing[midpoint:], output)

    def _run_pandoc(self, formulas: list[str]) -> dict[str, bytes]:
        markdown_parts: list[str] = []
        markers: dict[str, str] = {}
        for index, formula in enumerate(formulas):
            marker = f"MBFORMULA{index:06d}"
            markers[marker] = formula
            markdown_parts.extend((marker, "", f"$${_prepare_word_formula(formula)}$$", ""))
        with tempfile.TemporaryDirectory(prefix="mathbank_word_math_") as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "formulas.md"
            docx_path = temp_path / "formulas.docx"
            source_path.write_text("\n".join(markdown_parts), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        str(self.pandoc_path),
                        "-f",
                        "markdown+tex_math_dollars",
                        "-t",
                        "docx",
                        str(source_path),
                        "-o",
                        str(docx_path),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=45,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return {}
            if completed.returncode != 0 or not docx_path.exists():
                return {}
            with zipfile.ZipFile(docx_path, "r") as archive:
                xml_data = archive.read("word/document.xml")

        root = parse_xml(xml_data)
        paragraphs = root.xpath(".//w:body/w:p")
        result: dict[str, bytes] = {}
        pending_formula: str | None = None
        for paragraph in paragraphs:
            plain_text = "".join(paragraph.xpath(".//w:t/text()"))
            if plain_text in markers:
                pending_formula = markers[plain_text]
                continue
            if pending_formula:
                math_nodes = paragraph.xpath(".//m:oMath")
                if math_nodes:
                    from lxml import etree

                    result[pending_formula] = etree.tostring(math_nodes[0])
                    pending_formula = None
                elif plain_text.strip():
                    pending_formula = None
        return result


def _set_cell_margins(cell, top=70, start=70, bottom=70, end=70) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for name, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{name}"))
        if node is None:
            node = OxmlElement(f"w:{name}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _set_table_borders(table, color: str | None = None, size: int = 4) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), "nil" if color is None else "single")
        if color:
            tag.set(qn("w:color"), color)
            tag.set(qn("w:sz"), str(size))


def _set_cell_width(cell, width_twips: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.first_child_found_in("w:tcW")
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(width_twips))
    tc_w.set(qn("w:type"), "dxa")


def _set_table_geometry(table, column_widths: list[int]) -> None:
    """Apply matching fixed DXA geometry to the table, grid, and cells."""

    total_width = sum(column_widths)
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.first_child_found_in("w:tblW")
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(total_width))
    tbl_w.set(qn("w:type"), "dxa")

    layout = tbl_pr.first_child_found_in("w:tblLayout")
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")

    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in column_widths:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(width))
        grid.append(grid_col)

    for row in table.rows:
        for index, cell in enumerate(row.cells):
            _set_cell_width(cell, column_widths[min(index, len(column_widths) - 1)])


def _prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = tr_pr.find(qn("w:tblHeader"))
    if header is None:
        header = OxmlElement("w:tblHeader")
        tr_pr.append(header)
    header.set(qn("w:val"), "true")


def _set_rfonts(r_pr, cjk_font: str, latin_font: str) -> None:
    r_fonts = r_pr.rFonts
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    r_fonts.set(qn("w:ascii"), latin_font)
    r_fonts.set(qn("w:hAnsi"), latin_font)
    r_fonts.set(qn("w:cs"), latin_font)
    r_fonts.set(qn("w:eastAsia"), cjk_font)


def _set_run_font(
    run,
    size: float = BODY_FONT_SIZE,
    bold: bool = False,
    color: RGBColor | None = None,
    cjk_font: str = CJK_BODY_FONT,
    latin_font: str = LATIN_FONT,
) -> None:
    run.font.name = latin_font
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    r_pr = run._element.get_or_add_rPr()
    _set_rfonts(r_pr, cjk_font, latin_font)


def _clean_plain_text(text: str) -> str:
    value = text or ""
    value = IMAGE_PATTERN.sub("", value)
    value = re.sub(r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}", "", value)
    value = re.sub(r"\\begin\{(?:center|table|figure)\}", "", value)
    value = re.sub(r"\\end\{(?:center|table|figure)\}", "", value)
    value = re.sub(r"\\centering\b", "", value)
    value = re.sub(r"\\(?:textbf|mathbf|mathrm|operatorname)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\(?:quad|qquad)\b|\\[,;!]", " ", value)
    value = value.replace(r"\%", "%").replace(r"\_", "_")
    value = value.replace("\\\\", "\n")
    return value


def _omml_character_style(char: str, previous: str = "p") -> str:
    """Return Word's semantic math style for one visible character."""
    category = unicodedata.category(char)
    if category.startswith("M"):
        return previous
    if category.startswith("L"):
        name = unicodedata.name(char, "")
        if "LATIN" in name or "GREEK" in name or "MATHEMATICAL" in name:
            return "i"
    return "p"


def _set_omml_variable_font(math_run, style: str) -> None:
    """Apply the exam's Times New Roman variable face to one OMML run."""
    if style not in {"i", "bi"}:
        return
    math_r_pr = math_run.find(qn("m:rPr"))
    if math_r_pr is not None and math_r_pr.find(qn("m:scr")) is not None:
        return
    if math_r_pr is None:
        math_r_pr = OxmlElement("m:rPr")
        math_run.insert(0, math_r_pr)
    if math_r_pr.find(qn("m:nor")) is None:
        normal_text = OxmlElement("m:nor")
        style_node = math_r_pr.find(qn("m:sty"))
        if style_node is None:
            math_r_pr.append(normal_text)
        else:
            math_r_pr.insert(math_r_pr.index(style_node), normal_text)
    word_r_pr = math_run.find(qn("w:rPr"))
    if word_r_pr is None:
        word_r_pr = OxmlElement("w:rPr")
        insertion_index = math_run.index(math_r_pr) + 1
        math_run.insert(insertion_index, word_r_pr)
    run_fonts = word_r_pr.find(qn("w:rFonts"))
    if run_fonts is None:
        run_fonts = OxmlElement("w:rFonts")
        word_r_pr.insert(0, run_fonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs"):
        run_fonts.set(qn(attribute), LATIN_FONT)
    if word_r_pr.find(qn("w:i")) is None:
        word_r_pr.append(OxmlElement("w:i"))
    if word_r_pr.find(qn("w:iCs")) is None:
        word_r_pr.append(OxmlElement("w:iCs"))
    if style == "bi" and word_r_pr.find(qn("w:b")) is None:
        word_r_pr.append(OxmlElement("w:b"))


def _set_native_omml_run_styles(omml) -> None:
    """Make Word's plain/italic math semantics explicit without setting fonts.

    Pandoc leaves letters and digits without ``m:sty`` because Microsoft Word
    infers their usual math style.  WPS for Mac instead treats every unmarked
    run as italic, so even numerals appear slanted.  Split mixed runs when
    necessary and mark Latin/Greek variables italic while keeping numerals,
    punctuation, operators and CJK text plain.  Existing Pandoc styles (for
    operators, ``\\mathrm``, functions and special alphabets) are preserved.
    """
    for math_run in list(omml.iter(qn("m:r"))):
        math_r_pr = math_run.find(qn("m:rPr"))
        if math_r_pr is not None and len(math_r_pr):
            style_node = math_r_pr.find(qn("m:sty"))
            if style_node is not None:
                _set_omml_variable_font(math_run, style_node.get(qn("m:val"), ""))
            continue
        text_node = math_run.find(qn("m:t"))
        if text_node is None or not text_node.text:
            continue

        groups: list[tuple[str, str]] = []
        previous_style = "p"
        for char in text_node.text:
            style = _omml_character_style(char, previous_style)
            previous_style = style
            if groups and groups[-1][0] == style:
                groups[-1] = (style, groups[-1][1] + char)
            else:
                groups.append((style, char))

        parent = math_run.getparent()
        if parent is None:
            continue
        insertion_index = parent.index(math_run)
        for offset, (style, value) in enumerate(groups):
            styled_run = copy.deepcopy(math_run)
            styled_r_pr = styled_run.find(qn("m:rPr"))
            if styled_r_pr is None:
                styled_r_pr = OxmlElement("m:rPr")
                styled_run.insert(0, styled_r_pr)
            style_node = OxmlElement("m:sty")
            style_node.set(qn("m:val"), style)
            styled_r_pr.append(style_node)
            if style == "i":
                # Traditional Chinese exam papers conventionally use Times
                # New Roman Italic for Latin/Greek variables.  Restrict the
                # direct font to variable runs only: applying it to numerals
                # caused WPS for Mac to hide choice contents until equations
                # were manually rebuilt as "Professional" format.
                _set_omml_variable_font(styled_run, style)
            styled_run.find(qn("m:t")).text = value
            parent.insert(insertion_index + offset, styled_run)
        parent.remove(math_run)


def _append_omml(paragraph, xml_bytes: bytes) -> None:
    """Append Pandoc's native OMML without rewriting individual math runs.

    Word for Mac can hide otherwise valid digits and symbols when a converter
    adds direct ``w:rPr`` formatting to every ``m:r``.  Keeping Pandoc's
    minimal OMML lets Word apply the document math font and paragraph size in
    the same way as an equation created through Word's own equation editor.
    """
    omml = copy.deepcopy(parse_xml(xml_bytes))
    _set_native_omml_run_styles(omml)
    paragraph._p.append(omml)


def _formula_fallback_png(formula: str) -> bytes | None:
    xelatex = shutil.which("xelatex")
    if not xelatex:
        return None
    prepared_formula = _prepare_word_formula(formula)
    tex = rf"""\documentclass[preview,border=2pt]{{standalone}}
\usepackage{{amsmath,amssymb,mathtools,cancel,extarrows,yhmath}}
\begin{{document}}
$\displaystyle {prepared_formula}$
\end{{document}}
"""
    with tempfile.TemporaryDirectory(prefix="mathbank_word_fallback_") as temp_dir:
        temp_path = Path(temp_dir)
        tex_path = temp_path / "formula.tex"
        tex_path.write_text(tex, encoding="utf-8")
        try:
            completed = subprocess.run(
                [xelatex, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
                cwd=temp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
            pdf_path = temp_path / "formula.pdf"
            if completed.returncode != 0 or not pdf_path.exists():
                return None
            import pymupdf as fitz
            from PIL import Image, ImageChops

            with fitz.open(pdf_path) as pdf:
                pixmap = pdf[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")
            background = Image.new("RGB", image.size, "white")
            bbox = ImageChops.difference(image, background).getbbox()
            if bbox:
                left, top, right, bottom = bbox
                image = image.crop((max(0, left - 8), max(0, top - 6), min(image.width, right + 8), min(image.height, bottom + 6)))
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
        except Exception:
            return None


class WordExamBuilder:
    def __init__(self, omml_map: dict[str, bytes], diagnostics: WordExportDiagnostics):
        self.omml_map = omml_map
        self.diagnostics = diagnostics
        self.math_font = MATH_FONT
        self.fallback_cache: dict[str, bytes | None] = {}
        self.doc = Document()
        self._list_abstract_ids: dict[tuple[str, int], int] = {}
        self._configure_document()

    def _configure_document(self) -> None:
        section = self.doc.sections[0]
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        # Match the 1-inch geometry used by the LaTeX exam template.
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(2.54)
        section.right_margin = Cm(2.54)
        section.header_distance = Cm(1.0)
        section.footer_distance = Cm(1.0)

        normal = self.doc.styles["Normal"]
        normal.font.name = LATIN_FONT
        normal.font.size = Pt(BODY_FONT_SIZE)
        _set_rfonts(normal._element.get_or_add_rPr(), CJK_BODY_FONT, LATIN_FONT)
        # Fractions and radicals may exceed a fixed line box. "At least" keeps
        # the normal rhythm while allowing Word to expand formula-heavy lines.
        normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.AT_LEAST
        normal.paragraph_format.line_spacing = Pt(18)
        normal.paragraph_format.space_after = Pt(0)

        for style_name in ("Title", "Subtitle", "Heading 1", "Heading 2"):
            style = self.doc.styles[style_name]
            style.font.name = LATIN_FONT
            _set_rfonts(style._element.get_or_add_rPr(), CJK_HEADING_FONT, LATIN_FONT)

        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run("第 ")
        _set_run_font(run, 9)
        self._append_field(footer, "PAGE")
        run = footer.add_run(" 页")
        _set_run_font(run, 9)

        settings = self.doc.settings.element
        theme_font_lang = settings.find(qn("w:themeFontLang"))
        if theme_font_lang is None:
            theme_font_lang = OxmlElement("w:themeFontLang")
            settings.append(theme_font_lang)
        theme_font_lang.set(qn("w:val"), "zh-CN")
        theme_font_lang.set(qn("w:eastAsia"), "zh-CN")

        math_pr = settings.find(qn("m:mathPr"))
        if math_pr is None:
            math_pr = OxmlElement("m:mathPr")
            settings.insert(0, math_pr)
        math_font = math_pr.find(qn("m:mathFont"))
        if math_font is None:
            math_font = OxmlElement("m:mathFont")
            math_pr.insert(0, math_font)
        math_font.set(qn("m:val"), self.math_font)

        update_fields = OxmlElement("w:updateFields")
        update_fields.set(qn("w:val"), "true")
        settings.append(update_fields)

    @staticmethod
    def _append_field(paragraph, field_name: str) -> None:
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = f" {field_name} "
        separate = OxmlElement("w:fldChar")
        separate.set(qn("w:fldCharType"), "separate")
        text_node = OxmlElement("w:t")
        text_node.text = "1"
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        for node in (begin, instruction, separate, text_node, end):
            run = OxmlElement("w:r")
            run.append(node)
            paragraph._p.append(run)

    def add_mixed(
        self,
        paragraph,
        text: str,
        size: float = BODY_FONT_SIZE,
        *,
        images: list[Path] | None = None,
        max_image_width: float = 4.8,
        max_image_height: float | None = None,
        image_layouts: dict | None = None,
    ) -> None:
        image_by_name = {path.name: path for path in (images or []) if path.exists()}

        def add_text(value: str) -> None:
            for token in tokenize_mixed_content(value):
                if isinstance(token, BlankToken):
                    run = paragraph.add_run("\u00a0" * WORD_FILLIN_BLANK_SPACES)
                    _set_run_font(run, size)
                    run.underline = True
                    continue
                if isinstance(token, str):
                    plain = _clean_plain_text(token)
                    # 只有当包含小问换行时才保留 \n 软换行，普通单 \n 替换为空格，保持文本紧贴插图流动 (禁用后行断言)
                    plain_clean = re.sub(r"\n(?!\s*(?:[\(\（][0-9a-zA-Z一二三四五六七八九十]+\s*[\)\）]|[①-⑩]))", " ", plain)
                    chunks = plain_clean.split("\n")
                    for index, chunk in enumerate(chunks):
                        if chunk:
                            run = paragraph.add_run(chunk)
                            _set_run_font(run, size)
                        if index < len(chunks) - 1:
                            paragraph.add_run().add_break()
                    continue
                formula = token.latex
                xml_bytes = self.omml_map.get(formula)
                if xml_bytes:
                    _append_omml(paragraph, xml_bytes)
                    self.diagnostics.native_formulas += 1
                    continue
                if formula not in self.fallback_cache:
                    self.fallback_cache[formula] = _formula_fallback_png(formula)
                image_bytes = self.fallback_cache[formula]
                if image_bytes:
                    run = paragraph.add_run()
                    try:
                        from PIL import Image

                        image = Image.open(BytesIO(image_bytes))
                        ratio = image.width / max(image.height, 1)
                        height = 0.24 if not token.display else 0.34
                        width = min(6.1, max(0.3, ratio * height))
                        run.add_picture(BytesIO(image_bytes), width=Inches(width), height=Inches(height))
                        self.diagnostics.fallback_formulas += 1
                        continue
                    except Exception:
                        pass
                run = paragraph.add_run(f"[公式待核对：{formula}]")
                _set_run_font(run, size, color=RGBColor(192, 0, 0))
                self.diagnostics.failed_formulas += 1

        cursor = 0
        original_alignment = paragraph.alignment
        for match in IMAGE_PATTERN.finditer(text or ""):
            add_text((text or "")[cursor:match.start()])
            image_path = image_by_name.get(Path(match.group(1)).name)
            layout = (image_layouts or {}).get(image_key(match.group(1)))
            width, height = max_image_width, 2.8 if max_image_height is None else max_image_height
            if layout:
                if paragraph.runs:
                    next_paragraph = paragraph._parent.add_paragraph()
                    paragraph._p.addnext(next_paragraph._p)
                    paragraph = next_paragraph
                paragraph.alignment = {'left': WD_ALIGN_PARAGRAPH.LEFT, 'center': WD_ALIGN_PARAGRAPH.CENTER, 'right': WD_ALIGN_PARAGRAPH.RIGHT}[layout['align']]
                if layout['size'] in SIZE_CM:
                    cm_width, cm_height = SIZE_CM[layout['size']]
                    width = min(width, cm_width / 2.54)
                    # The compatibility auto height must not truncate an authored
                    # large body image. Explicit cell limits remain authoritative.
                    height = cm_height / 2.54 if max_image_height is None else min(height, cm_height / 2.54)
            if image_path is not None:
                self._add_image(
                    paragraph,
                    image_path,
                    max_width=width,
                    max_height=height,
                )
            else:
                self.diagnostics.missing_images += 1
                self.diagnostics.warnings.append(
                    f"插图无法写入：{Path(match.group(1)).name}"
                )
            cursor = match.end()
            if layout and (text or "")[cursor:].strip():
                next_paragraph = paragraph._parent.add_paragraph()
                paragraph._p.addnext(next_paragraph._p)
                paragraph = next_paragraph
                paragraph.alignment = original_alignment
        add_text((text or "")[cursor:])

    def add_title_block(
        self,
        title: str,
        subtitle: str,
        total_count: int,
        total_score: int,
        paper_type: str,
        show_secret: bool,
        show_notice: bool,
    ) -> None:
        is_exam_style = paper_type in {"exam", "exam_19"}
        if show_secret and is_exam_style:
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(4)
            run = p.add_run("绝密★启用前")
            _set_run_font(run, 10.5, bold=True, cjk_font=CJK_HEADING_FONT)

        title_p = self.doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_p.paragraph_format.space_before = Pt(2)
        title_p.paragraph_format.space_after = Pt(3)
        run = title_p.add_run(title or "未命名试卷")
        _set_run_font(run, 18, bold=True, cjk_font=CJK_TITLE_FONT)

        if is_exam_style:
            subject_p = self.doc.add_paragraph()
            subject_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            subject_p.paragraph_format.space_before = Pt(2)
            subject_p.paragraph_format.space_after = Pt(4)
            run = subject_p.add_run("数  学")
            _set_run_font(run, 15, bold=True, cjk_font=CJK_TITLE_FONT)

        if subtitle:
            subtitle_p = self.doc.add_paragraph()
            subtitle_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            subtitle_p.paragraph_format.space_before = Pt(2)
            subtitle_p.paragraph_format.space_after = Pt(4)
            run = subtitle_p.add_run(subtitle)
            _set_run_font(run, 12, bold=True, cjk_font=CJK_TITLE_FONT)

        if is_exam_style:
            meta = self.doc.add_paragraph()
            meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
            meta.paragraph_format.space_before = Pt(4)
            meta.paragraph_format.space_after = Pt(6)
            run = meta.add_run(f"本试卷共 {total_count} 题。全卷满分 {total_score} 分。考试用时 120 分钟。")
            _set_run_font(run, 10.5, cjk_font=CJK_BODY_FONT)

            if show_notice:
                notice_head = self.doc.add_paragraph()
                notice_head.alignment = WD_ALIGN_PARAGRAPH.LEFT
                notice_head.paragraph_format.space_before = Pt(6)
                notice_head.paragraph_format.space_after = Pt(2)
                notice_head.paragraph_format.keep_with_next = True
                run = notice_head.add_run("注意事项：")
                _set_run_font(run, 10.5, bold=True, cjk_font=CJK_HEADING_FONT)

                notices = [
                    "1. 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。",
                    "2. 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。",
                    "3. 考试结束后，将本试卷和答题卡一并交回。",
                ]
                for idx, line in enumerate(notices):
                    p = self.doc.add_paragraph()
                    p.paragraph_format.left_indent = Pt(18)
                    p.paragraph_format.first_line_indent = Pt(-18)
                    p.paragraph_format.space_before = Pt(0)
                    p.paragraph_format.space_after = Pt(2) if idx < len(notices) - 1 else Pt(6)
                    self.add_mixed(p, line, 10.0)

    def add_section_heading(self, q_type: str, items: list[PreparedQuestion], paper_type: str, chinese_index: int, custom_labels: dict[str, str] | None = None) -> None:
        count = len(items)
        section_score = sum(item.score for item in items)
        scores = {item.score for item in items}
        unit_text = f"每小题 {next(iter(scores))} 分，" if len(scores) == 1 else ""
        label = TYPE_LABELS.get(q_type, (custom_labels or {}).get(q_type, q_type))
        if paper_type == "quiz":
            heading = label
        else:
            numerals = "一二三四五六七八九十"
            prefix = numerals[chinese_index - 1] if 0 < chinese_index <= len(numerals) else str(chinese_index)
            heading = f"{prefix}、{label}：本题共 {count} 小题，{unit_text}共 {section_score} 分。"
        p = self.doc.add_paragraph()
        p.paragraph_format.keep_with_next = True
        details = {
            "single_choice": "在每小题给出的四个选项中，只有一项符合题目要求。",
            "multi_choice": "在每小题给出的四个选项中，有多项符合题目要求；有选错项不得分。",
            "detailed_answer": "解答应写出文字说明、证明过程或演算步骤。",
        }
        if paper_type != "quiz" and q_type in details:
            heading = f"{heading} {details[q_type]}"

        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(4)
        run = p.add_run(heading)
        _set_run_font(run, SECTION_FONT_SIZE, bold=True, cjk_font=CJK_BODY_FONT)

    def _usable_page_width_inches(self) -> float:
        section = self.doc.sections[0]
        usable_width = (
            section.page_width - section.left_margin - section.right_margin
        )
        return max(0.1, float(usable_width) / float(Inches(1)))

    def _usable_page_height_inches(self) -> float:
        section = self.doc.sections[0]
        usable_height = (
            section.page_height - section.top_margin - section.bottom_margin
        )
        return max(0.1, float(usable_height) / float(Inches(1)))

    def _detached_figure_width(
        self,
        question: dict,
        *,
        align: str,
        image_count: int,
    ) -> float:
        available_width = self._usable_page_width_inches()
        if align == "right":
            return min(WORD_RIGHT_FIGURE_MAX_WIDTH_INCHES, available_width)

        figure_size = str(question.get("figure_size") or "auto").strip()
        if figure_size == "auto":
            requested_width = (
                WORD_AUTO_SINGLE_FIGURE_WIDTH_INCHES
                if image_count == 1
                else WORD_AUTO_MULTI_FIGURE_WIDTH_INCHES
            )
        else:
            requested_width = WORD_FIGURE_SIZE_WIDTHS_INCHES.get(
                figure_size,
                WORD_AUTO_SINGLE_FIGURE_WIDTH_INCHES
                if image_count == 1
                else WORD_AUTO_MULTI_FIGURE_WIDTH_INCHES,
            )
        return min(requested_width, available_width)

    def _detached_figure_height(self, question: dict) -> float:
        figure_size = str(question.get("figure_size") or "auto").strip()
        requested_height = WORD_FIGURE_SIZE_HEIGHTS_INCHES.get(
            figure_size,
            WORD_AUTO_FIGURE_MAX_HEIGHT_INCHES,
        )
        return min(requested_height, self._usable_page_height_inches())

    @staticmethod
    def _fit_image_dimensions(
        path: Path,
        *,
        max_width: float,
        max_height: float,
    ) -> tuple[float, float]:
        from PIL import Image

        with Image.open(path) as image:
            ratio = image.width / max(image.height, 1)
        width = min(max_width, max_height * ratio)
        return width, width / max(ratio, 0.01)

    def _detached_figure_block_height(
        self,
        paths: list[Path],
        *,
        max_width: float,
        max_height: float,
    ) -> float:
        """Estimate the inline image rows added below a question, in inches."""

        available_width = self._usable_page_width_inches()
        total_height = 0.0
        row_width = 0.0
        row_height = 0.0
        for path in paths:
            width, height = self._fit_image_dimensions(
                path,
                max_width=max_width,
                max_height=max_height,
            )
            if row_width > 0 and row_width + width > available_width:
                total_height += row_height
                row_width = 0.0
                row_height = 0.0
            row_width += width
            row_height = max(row_height, height)
        return total_height + row_height

    def _add_floating_right_image(
        self,
        paragraph,
        path: Path,
        max_width: float = WORD_RIGHT_FIGURE_MAX_WIDTH_INCHES,
        max_height: float = WORD_AUTO_FIGURE_MAX_HEIGHT_INCHES,
    ) -> bool:
        """
        在 Word 题干段落首行插入原生【四周型右侧文字环绕】插图 (Square Text Wrap)。
        文字可享受 75%+ 的完整宽度空间并于插图左侧自然环绕下流，完全避免无边框表格容器截断。
        """
        try:
            width, height = self._fit_image_dimensions(
                path,
                max_width=max_width,
                max_height=max_height,
            )
            run = paragraph.add_run()
            shape = run.add_picture(
                str(path),
                width=Inches(width),
                height=Inches(height),
            )
            inline = shape._inline
            cx = inline.extent.cx
            cy = inline.extent.cy
            doc_pr = inline.docPr
            doc_id = doc_pr.id
            name = doc_pr.name
            graphic_xml = inline.graphic.xml

            anchor_xml = f'''<wp:anchor {nsdecls('wp', 'r', 'a', 'pic', 'c')}
                distT="36000" distB="36000" distL="144000" distR="36000" simplePos="0" relativeHeight="251658240"
                behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">
                <wp:simplePos x="0" y="0"/>
                <wp:positionH relativeFrom="margin">
                    <wp:align>right</wp:align>
                </wp:positionH>
                <wp:positionV relativeFrom="paragraph">
                    <wp:align>top</wp:align>
                </wp:positionV>
                <wp:extent cx="{cx}" cy="{cy}"/>
                <wp:effectExtent l="0" t="0" r="0" b="0"/>
                <wp:wrapSquare wrapText="both"/>
                <wp:docPr id="{doc_id}" name="{name}"/>
                <wp:cNvGraphicFramePr>
                    <a:graphicFrameLocks {nsdecls('a')} noChangeAspect="1"/>
                </wp:cNvGraphicFramePr>
                {graphic_xml}
            </wp:anchor>'''

            anchor_node = parse_xml(anchor_xml)
            inline.getparent().replace(inline, anchor_node)
            return True
        except Exception:
            return False

    def add_question(self, item: PreparedQuestion, number: int) -> None:
        align = item.question.get("figure_align") or "right"
        usable_images = [path for path in item.images if path.exists()]
        image_layouts = normalize_image_layouts(item.question.get("image_layouts", {}), item.question.get("content", ""))
        preserve_inline_images = bool(IMAGE_PATTERN.search(item.stem))
        stem_image_names = {
            Path(match.group(1)).name
            for match in IMAGE_PATTERN.finditer(item.stem)
        }
        choice_image_names = {
            Path(match.group(1)).name
            for choice in item.choices for match in IMAGE_PATTERN.finditer(choice)
        }
        anchored_names = stem_image_names | choice_image_names
        inline_images = [path for path in usable_images if path.name in stem_image_names]
        _, tail = split_image_anchors(item.question.get('content', ''))
        tail_names = {Path(m[1]).name for m in IMAGE_PATTERN.finditer(tail)}
        detached_images = [path for path in usable_images if path.name not in anchored_names or path.name in tail_names]
        if len(detached_images) > 1 and align == "right":
            align = "center"
        detached_width = self._detached_figure_width(
            item.question,
            align=align,
            image_count=len(detached_images),
        )
        detached_height = self._detached_figure_height(item.question)
        detached_block_height = 0.0
        if detached_images and align in {"bottom_left", "center", "bottom_right"}:
            try:
                detached_block_height = self._detached_figure_block_height(
                    detached_images,
                    max_width=detached_width,
                    max_height=detached_height,
                )
            except Exception:
                detached_block_height = detached_height
        if detached_images and align == "right" and len(detached_images) == 1:
            p = self.doc.add_paragraph()
            self._format_question_paragraph(p, number)
            # 在题干段落首行嵌入四周型右侧文字环绕插图，彻底取消表格容器
            self._add_floating_right_image(
                p,
                detached_images[0],
                max_width=detached_width,
                max_height=detached_height,
            )
            # single_paragraph=True 确保全文在同一个 Word 段落中，使文字平滑紧贴图片四周型环绕！
            self._add_content_blocks(p, item.stem, single_paragraph=not preserve_inline_images, images=inline_images, image_layouts=image_layouts)
        else:
            p = self.doc.add_paragraph()
            self._format_question_paragraph(p, number)
            self._add_content_blocks(p, item.stem, images=inline_images, image_layouts=image_layouts)
            if detached_images:
                fig_p = self.doc.add_paragraph()
                fig_p.alignment = {
                    "bottom_left": WD_ALIGN_PARAGRAPH.LEFT,
                    "bottom_right": WD_ALIGN_PARAGRAPH.RIGHT,
                }.get(align, WD_ALIGN_PARAGRAPH.CENTER)
                for path in detached_images:
                    self._add_image(
                        fig_p,
                        path,
                        max_width=detached_width,
                        max_height=detached_height,
                    )

        if item.choices:
            self.add_choices(item.choices, images=usable_images, image_layouts=image_layouts)

        if is_written_question_type(item.question.get("question_type", "single_choice")) and item.solution_space > 0:
            figure_height_cm = detached_block_height * 2.54
            remaining = max(0.0, item.solution_space - figure_height_cm)
            # Exact-height blank paragraphs can flow across pages. Paragraph
            # spacing after one empty paragraph may be discarded or overflow.
            while remaining > 0.001:
                chunk = min(2.0, remaining)
                spacer = self.doc.add_paragraph()
                spacer.paragraph_format.space_before = Pt(0)
                spacer.paragraph_format.space_after = Pt(0)
                spacer.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
                spacer.paragraph_format.line_spacing = Cm(chunk)
                spacer.paragraph_format.keep_together = False
                spacer.paragraph_format.keep_with_next = False
                spacer.paragraph_format.widow_control = False
                remaining -= chunk

    def _format_question_paragraph(self, paragraph, number: int) -> None:
        paragraph.paragraph_format.left_indent = Cm(0)
        paragraph.paragraph_format.first_line_indent = Cm(0)
        paragraph.paragraph_format.space_before = Pt(3)
        paragraph.paragraph_format.space_after = Pt(4)
        paragraph.paragraph_format.keep_together = True
        run = paragraph.add_run(f"{number}. ")
        _set_run_font(run, BODY_FONT_SIZE, bold=True)

    def _add_content_blocks(
        self,
        first_paragraph,
        content: str,
        parent=None,
        single_paragraph: bool = False,
        images: list[Path] | None = None,
        image_layouts: dict | None = None,
        list_depth: int = 0,
        list_kind_depths: tuple[int, int] = (0, 0),
    ) -> None:
        segments = _split_content_segments(content)
        paragraph = first_paragraph
        has_output = False
        for kind, first_value, second_value in segments:
            if kind == "list":
                self._add_latex_list(
                    first_value, parent=parent, depth=list_depth,
                    images=images, image_layouts=image_layouts,
                    kind_depths=list_kind_depths,
                )
                # A block has introduced sibling paragraphs. Subsequent prose
                # must follow them, including with a floating question image.
                single_paragraph = False
                has_output = True
                continue
            if kind == "markdown_table":
                self.add_markdown_table(
                    first_value, parent=parent, images=images,
                    image_layouts=image_layouts,
                )
                single_paragraph = False
                has_output = True
                continue
            if kind == "table":
                self.add_tabular(
                    first_value,
                    second_value,
                    parent=parent,
                    images=images,
                    image_layouts=image_layouts,
                )
                single_paragraph = False
                has_output = True
                continue

            part = first_value
            if has_output:
                if single_paragraph:
                    paragraph.add_run().add_break()
                else:
                    paragraph = parent.add_paragraph() if parent is not None else self.doc.add_paragraph()
                    if list_depth:
                        paragraph.paragraph_format.left_indent = Cm(0.635 * list_depth)
                        paragraph.paragraph_format.first_line_indent = Cm(0)
                    else:
                        paragraph.paragraph_format.first_line_indent = Cm(0.74)
                    paragraph.paragraph_format.space_after = Pt(2)
            if IMAGE_PATTERN.fullmatch(part.strip()):
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self.add_mixed(paragraph, part, images=images, image_layouts=image_layouts)
            has_output = True

    def _new_list_numbering(self, environment: str, depth: int, kind_depth: int) -> int:
        """Create a fresh native sequence; Word templates otherwise continue it."""
        numbering = self.doc.part.numbering_part.element
        # LaTeX's enumerate and itemize counters nest independently. An
        # enumerate inside itemize still starts with decimal markers.
        shift = (kind_depth - depth) % 4
        key = (environment, shift)
        abstract_id = self._list_abstract_ids.get(key)
        if abstract_id is None:
            abstract_id = max((int(node.get(qn("w:abstractNumId")))
                               for node in numbering.findall(qn("w:abstractNum"))), default=-1) + 1
            abstract = OxmlElement("w:abstractNum")
            abstract.set(qn("w:abstractNumId"), str(abstract_id))
            multilevel = OxmlElement("w:multiLevelType")
            multilevel.set(qn("w:val"), "multilevel")
            abstract.append(multilevel)
            formats = ("decimal", "lowerLetter", "lowerRoman", "upperLetter")
            bullets = ("•", "–", "∗", "·")
            for level in range(9):
                node = OxmlElement("w:lvl")
                node.set(qn("w:ilvl"), str(level))
                placeholder = f"%{level + 1}"
                format_index = (level + shift) % 4
                label = f"({placeholder})" if format_index == 1 else placeholder + "."
                for tag, value in (
                    ("start", "1"),
                    ("numFmt", "bullet" if environment == "itemize" else formats[format_index]),
                    ("lvlText", bullets[format_index] if environment == "itemize" else label),
                    ("suff", "tab"), ("lvlJc", "left"),
                ):
                    child = OxmlElement("w:" + tag)
                    child.set(qn("w:val"), value)
                    node.append(child)
                p_pr = OxmlElement("w:pPr")
                tabs = OxmlElement("w:tabs")
                tab = OxmlElement("w:tab")
                tab.set(qn("w:val"), "num")
                tab.set(qn("w:pos"), str((level + 1) * 360))
                tabs.append(tab)
                p_pr.append(tabs)
                indent = OxmlElement("w:ind")
                indent.set(qn("w:left"), str((level + 1) * 360))
                indent.set(qn("w:hanging"), "360")
                p_pr.append(indent)
                node.append(p_pr)
                abstract.append(node)
            first_num = numbering.find(qn("w:num"))
            if first_num is None:
                numbering.append(abstract)
            else:
                first_num.addprevious(abstract)
            self._list_abstract_ids[key] = abstract_id
        num = numbering.add_num(abstract_id)
        num.add_lvlOverride(ilvl=depth).add_startOverride(1)
        return num.numId

    def _add_latex_list(
        self, block: LatexList, *, parent=None, depth: int = 0,
        images: list[Path] | None = None, image_layouts: dict | None = None,
        kind_depths: tuple[int, int] = (0, 0),
    ) -> None:
        if depth > 8:
            raise WordExportError("Word 列表嵌套超过 9 层，无法保留原生编号。")
        kind_index = 0 if block.environment == "enumerate" else 1
        num_id = self._new_list_numbering(block.environment, depth, kind_depths[kind_index])
        nested_depths = tuple(value + (index == kind_index)
                              for index, value in enumerate(kind_depths))
        target = parent if parent is not None else self.doc
        for item in block.items:
            paragraph = target.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(2)
            if item.label is None:
                num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
                num_pr.get_or_add_ilvl().val = depth
                num_pr.get_or_add_numId().val = num_id
            else:
                # Explicit TeX labels replace the marker without advancing
                # enumerate's counter, so these items have no automatic numPr.
                paragraph.paragraph_format.left_indent = Cm(0.635 * (depth + 1))
                paragraph.paragraph_format.first_line_indent = Cm(-0.635)
                self.add_mixed(paragraph, item.label)
                paragraph.add_run("\t")
            self._add_content_blocks(
                paragraph, item.content, parent=parent, images=images,
                image_layouts=image_layouts, list_depth=depth + 1,
                list_kind_depths=nested_depths,
            )

    def _add_image(
        self,
        paragraph,
        path: Path,
        max_width: float,
        max_height: float | None = None,
    ) -> None:
        try:
            run = paragraph.add_run()
            width = max_width
            height = None
            if max_height is not None:
                width, height = self._fit_image_dimensions(
                    path,
                    max_width=max_width,
                    max_height=max_height,
                )
            if height is None:
                run.add_picture(str(path), width=Inches(width))
            else:
                run.add_picture(str(path), width=Inches(width), height=Inches(height))
        except Exception:
            self.diagnostics.missing_images += 1
            self.diagnostics.warnings.append(f"插图无法写入：{path.name}")

    def add_choices(self, choices: list[str], *, images: list[Path] | None = None,
                    image_layouts: dict | None = None) -> None:
        plain_lengths = [len(re.sub(r"\\[A-Za-z]+|[{}$\\]", "", IMAGE_PATTERN.sub("", value))) for value in choices]
        max_length = max(plain_lengths, default=0)
        columns = 4 if len(choices) <= 4 and max_length <= 10 else 2 if max_length <= 24 else 1
        compact_images = len(choices) == 4 and all(
            IMAGE_PATTERN.fullmatch(value.strip()) for value in choices
        ) and not any(
            (image_layouts or {}).get(image_key(match.group(1)))
            for value in choices for match in IMAGE_PATTERN.finditer(value)
        )
        rows = (len(choices) + columns - 1) // columns
        table = self.doc.add_table(rows=rows, cols=columns)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _set_table_borders(table)
        total_twips = 9000
        width = total_twips // columns
        _set_table_geometry(table, [width] * columns)
        for row_index, row in enumerate(table.rows):
            _prevent_row_split(row)
            for col_index, cell in enumerate(row.cells):
                _set_cell_width(cell, width)
                _set_cell_margins(cell, 80, 70, 80, 70)
                choice_index = row_index * columns + col_index
                p = cell.paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                if choice_index >= len(choices):
                    continue
                label = chr(ord("A") + choice_index)
                label_paragraph = p
                if IMAGE_PATTERN.fullmatch(choices[choice_index].strip()):
                    # Inline pictures sit on the text baseline, leaving A/B/C/D
                    # at their bottom. Separate cells give Word/WPS real vertical
                    # centering without a font- or image-height-dependent offset.
                    pair = cell.add_table(rows=1, cols=2)
                    pair.autofit = False
                    _set_table_borders(pair)
                    _set_table_geometry(pair, [300, max(1, width - 440)])
                    _prevent_row_split(pair.rows[0])
                    for part in pair.rows[0].cells:
                        _set_cell_margins(part, 0, 0, 0, 0)
                        part.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                        part.paragraphs[0].paragraph_format.space_before = Pt(0)
                        part.paragraphs[0].paragraph_format.space_after = Pt(0)
                    cell._tc.remove(p._p)
                    # Word requires a paragraph after a nested table; keep that
                    # structural paragraph from adding a blank line below images.
                    tail = cell.paragraphs[-1]
                    tail.paragraph_format.space_before = Pt(0)
                    tail.paragraph_format.space_after = Pt(0)
                    tail.paragraph_format.line_spacing = Pt(1)
                    label_paragraph = pair.cell(0, 0).paragraphs[0]
                    p = pair.cell(0, 1).paragraphs[0]
                run = label_paragraph.add_run(f"{label}. ")
                _set_run_font(run, BODY_FONT_SIZE, bold=True)
                self.add_mixed(
                    p, choices[choice_index], BODY_FONT_SIZE,
                    images=images, image_layouts=image_layouts,
                    max_image_width=min(max(0.4, (width - 440) / 1440), COMPACT_CHOICE_IMAGE_SIZE_CM[0] / 2.54) if compact_images else max(0.4, (width - 440) / 1440),
                    max_image_height=COMPACT_CHOICE_IMAGE_SIZE_CM[1] / 2.54 if compact_images else WORD_AUTO_FIGURE_MAX_HEIGHT_INCHES,
                )
        after = self.doc.add_paragraph()
        after.paragraph_format.space_after = Pt(1)

    def add_markdown_table(
        self,
        source: str,
        parent=None,
        images: list[Path] | None = None,
        image_layouts: dict | None = None,
    ) -> None:
        """Write explicit cells directly, preserving editable formulas and images."""
        parsed = find_markdown_tables(source)
        if len(parsed) != 1:
            return
        spec = parsed[0]
        rows = (spec.header, *spec.rows)
        count = len(spec.header)
        total_width = (max(1, int(parent.width) // 635 - 120)
                       if parent is not None and parent.width is not None else 9000)
        widths = [total_width // count] * count
        widths[-1] += total_width - sum(widths)
        table = (parent.add_table(rows=len(rows), cols=count, width=Inches(total_width / 1440))
                 if parent is not None else self.doc.add_table(rows=len(rows), cols=count))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _set_table_borders(table, "7F7F7F", 5)
        _set_table_geometry(table, widths)
        _repeat_table_header(table.rows[0])
        for row_index, values in enumerate(rows):
            row = table.rows[row_index]
            _prevent_row_split(row)
            for column_index, value in enumerate(values):
                cell = row.cells[column_index]
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                _set_cell_margins(cell, 65, 40, 65, 40)
                paragraph = cell.paragraphs[0]
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.alignment = {"l": WD_ALIGN_PARAGRAPH.LEFT,
                                       "c": WD_ALIGN_PARAGRAPH.CENTER,
                                       "r": WD_ALIGN_PARAGRAPH.RIGHT}[spec.alignments[column_index]]
                for literal, part in _markdown_cell_parts(normalize_table_cell_math(value)):
                    if literal:
                        _set_run_font(paragraph.add_run(part), SMALL_FONT_SIZE)
                    else:
                        self.add_mixed(
                            paragraph, _decode_markdown_cell_pipes(part), SMALL_FONT_SIZE,
                            images=images, image_layouts=image_layouts,
                            max_image_width=max(0.05, (widths[column_index] - 80) / 1440),
                            max_image_height=TABLE_IMAGE_MAX_HEIGHT_CM / 2.54,
                        )
        spacer = parent.add_paragraph() if parent is not None else self.doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(1)

    def add_tabular(
        self,
        column_spec: str,
        body: str,
        parent=None,
        images: list[Path] | None = None,
        image_layouts: dict | None = None,
    ) -> None:
        alignments = [char for char in column_spec if char in "lcr"] or ["c"]
        cleaned = re.sub(r"\\(?:toprule|midrule|bottomrule|hline)\b", "", body)
        cleaned = re.sub(
            r"\\(?:cline|cmidrule)(?:\([^)]*\))?\s*\{[^{}]*\}",
            "",
            cleaned,
        )
        raw_rows = [row.strip() for row in re.split(r"\\\\(?:\[[^]]*\])?", cleaned) if row.strip()]
        source_rows = [
            [_parse_table_cell(value) for value in _split_table_cells(row)]
            for row in raw_rows
        ]
        if not source_rows:
            return
        column_count = min(max(1, len(alignments)), 32)
        parsed_rows: list[list[TableCellPlacement]] = []
        for row_index, source_row in enumerate(source_rows):
            placements: list[TableCellPlacement] = []
            start_col = 0
            for source_cell in source_row:
                if start_col >= column_count:
                    if source_cell.text:
                        self.diagnostics.warnings.append(
                            f"表格第 {row_index + 1} 行超出声明列数，末尾内容未写入。"
                        )
                    continue
                colspan = min(source_cell.colspan, column_count - start_col)
                rowspan = min(source_cell.rowspan, len(source_rows) - row_index)
                if colspan != source_cell.colspan or rowspan != source_cell.rowspan:
                    self.diagnostics.warnings.append(
                        f"表格第 {row_index + 1} 行合并范围超界，已限制在表格内。"
                    )
                safe_cell = TableCellSpec(
                    text=source_cell.text,
                    colspan=max(1, colspan),
                    rowspan=max(1, rowspan),
                    alignment=source_cell.alignment,
                )
                placements.append(TableCellPlacement(safe_cell, start_col))
                start_col += safe_cell.colspan
            parsed_rows.append(placements)
        table = (
            parent.add_table(rows=len(parsed_rows), cols=column_count, width=Inches(4.5))
            if parent is not None
            else self.doc.add_table(rows=len(parsed_rows), cols=column_count)
        )
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        _set_table_borders(table, "7F7F7F", 5)
        if column_count == 3:
            first_column_lengths = [
                len(_clean_plain_text(placement.cell.text))
                for row in parsed_rows
                for placement in row
                if placement.start_col == 0 and placement.cell.colspan == 1
            ]
            later_column_lengths = [
                len(_clean_plain_text(placement.cell.text))
                for row in parsed_rows
                for placement in row
                if placement.start_col > 0
            ]
            use_narrow_first = (
                max(first_column_lengths, default=0) <= 8
                and max(later_column_lengths, default=0) > 8
            )
        else:
            use_narrow_first = False
        column_widths = (
            [1080, 3960, 3960]
            if use_narrow_first
            else [9000 // column_count] * column_count
        )
        _set_table_geometry(table, column_widths)
        if all(placement.cell.rowspan == 1 for placement in parsed_rows[0]):
            _repeat_table_header(table.rows[0])
        covered: dict[tuple[int, int], tuple[int, int]] = {}
        for row_index, values in enumerate(parsed_rows):
            row = table.rows[row_index]
            _prevent_row_split(row)
            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                _set_cell_margins(cell, 65, 85, 65, 85)

            for placement in values:
                value = placement.cell
                start_col = placement.start_col
                end_col = start_col + value.colspan - 1
                end_row = row_index + value.rowspan - 1
                rectangle = {
                    (target_row, target_col)
                    for target_row in range(row_index, end_row + 1)
                    for target_col in range(start_col, end_col + 1)
                }
                conflicts = rectangle.intersection(covered)
                if conflicts:
                    if not value.text and rectangle.issubset(covered):
                        continue
                    anchor = covered[next(iter(conflicts))]
                    conflict_cell = table.cell(*anchor)
                    conflict_paragraph = conflict_cell.paragraphs[0]
                    if value.text:
                        conflict_paragraph.add_run().add_break()
                        self.add_mixed(
                            conflict_paragraph,
                            value.text,
                            SMALL_FONT_SIZE,
                            images=images,
                            image_layouts=image_layouts,
                            max_image_width=max(
                                0.5,
                                sum(column_widths[start_col:end_col + 1]) / 1440 - 0.2,
                            ),
                            max_image_height=TABLE_IMAGE_MAX_HEIGHT_CM / 2.54,
                        )
                        self.diagnostics.warnings.append(
                            f"表格第 {row_index + 1} 行合并区域冲突，内容已保留在相邻合并格。"
                        )
                    continue
                cell = table.cell(row_index, start_col)
                if end_col > start_col or end_row > row_index:
                    cell = cell.merge(table.cell(end_row, end_col))
                    for coordinate in rectangle:
                        if coordinate != (row_index, start_col):
                            covered[coordinate] = (row_index, start_col)

                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                _set_cell_margins(cell, 65, 85, 65, 85)
                p = cell.paragraphs[0]
                alignment = value.alignment or alignments[min(start_col, len(alignments) - 1)]
                p.alignment = {"l": WD_ALIGN_PARAGRAPH.LEFT, "c": WD_ALIGN_PARAGRAPH.CENTER, "r": WD_ALIGN_PARAGRAPH.RIGHT}[alignment]
                self.add_mixed(
                    p,
                    value.text,
                    SMALL_FONT_SIZE,
                    images=images,
                    image_layouts=image_layouts,
                    max_image_width=max(
                        0.5,
                        sum(column_widths[start_col:end_col + 1]) / 1440 - 0.2,
                    ),
                    max_image_height=TABLE_IMAGE_MAX_HEIGHT_CM / 2.54,
                )
        spacer = parent.add_paragraph() if parent is not None else self.doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(1)

    def add_answers(self, questions: list[PreparedQuestion], numbers: list[int], title: str) -> None:
        p = self.doc.add_paragraph()
        p.paragraph_format.page_break_before = True
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(14)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(f"{title} 参考答案与解析")
        _set_run_font(run, 16, bold=True, cjk_font=CJK_HEADING_FONT)
        for item, number in zip(questions, numbers):
            p = self.doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.keep_with_next = True
            run = p.add_run(f"{number}. ")
            _set_run_font(run, BODY_FONT_SIZE, bold=True)
            if item.answer.strip():
                self._add_content_blocks(
                    p, item.answer, images=item.images,
                    image_layouts=item.question.get("image_layouts"),
                )
            else:
                run = p.add_run("暂无答案与解析。")
                _set_run_font(run, SMALL_FONT_SIZE, color=RGBColor(127, 127, 127))

    def save_bytes(self) -> bytes:
        properties = self.doc.core_properties
        properties.title = "MathBank 可编辑 Word 试卷"
        properties.subject = "高中数学试卷"
        properties.author = "MathBank"
        properties.keywords = "数学, 试卷, OMML, 可编辑公式"
        output = BytesIO()
        self.doc.save(output)
        return output.getvalue()


def create_word_bundle_zip(title: str, main_docx_bytes: bytes, ans_docx_bytes: bytes) -> bytes:
    """Create an in-memory ZIP package containing the clean exam DOCX and the answers DOCX."""
    safe_title = re.sub(r'[/\\?%*:|"<>]', "_", title.strip()) or "试卷"
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_title}.docx", main_docx_bytes)
        zf.writestr(f"{safe_title}_含答案与解析.docx", ans_docx_bytes)
        for filename, data in content_source_export_files().items():
            zf.writestr(filename, data)
    return zip_buffer.getvalue()


def _with_content_sources_docx_property(data: bytes, notice: str) -> bytes:
    """Set one scoped custom property without resaving Word content or styles."""
    custom_ns = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
    values_ns = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
    rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    relationship_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
    content_type = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
    fmtid = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
    property_name = "MathBankContentSources"
    with zipfile.ZipFile(BytesIO(data)) as source:
        relationships = etree.fromstring(source.read("_rels/.rels"))
        candidates = [node for node in relationships if node.get("Type") == relationship_type]
        if len(candidates) > 1:
            raise WordExportError("Word 自定义属性关系不唯一，不能安全附加来源说明。")
        updates = {}
        if candidates:
            relation = candidates[0]
            target = relation.get("Target", "")
            part = posixpath.normpath(target.lstrip("/"))
            if relation.get("TargetMode") == "External" or "\\" in target or ":" in target or part in {".", ".."} or part.startswith("../"):
                raise WordExportError("Word 自定义属性路径无效。")
        else:
            part = "docProps/custom.xml"
            ids = {node.get("Id") for node in relationships}
            number = 1
            while f"rId{number}" in ids:
                number += 1
            etree.SubElement(relationships, f"{{{rels_ns}}}Relationship", Id=f"rId{number}", Type=relationship_type, Target=part)
            updates["_rels/.rels"] = etree.tostring(relationships, encoding="UTF-8", xml_declaration=True, standalone=True)

        if part in source.namelist():
            properties = etree.fromstring(source.read(part))
            if properties.tag != f"{{{custom_ns}}}Properties":
                raise WordExportError("Word 自定义属性结构无效。")
        else:
            properties = etree.Element(f"{{{custom_ns}}}Properties", nsmap={None: custom_ns, "vt": values_ns})
        owned = [node for node in properties if node.get("name") == property_name]
        current = owned[0] if owned else None
        exact = (len(owned) == 1 and len(current) == 1 and current[0].tag == f"{{{values_ns}}}lpwstr"
                 and current[0].text == notice and current.get("fmtid") == fmtid)
        if not exact:
            ids = {node.get("pid") for node in properties if node not in owned}
            pid = current.get("pid") if current is not None else None
            if not pid or not pid.isdigit() or int(pid) < 2 or pid in ids:
                pid = str(max([1, *(int(value) for value in ids if value and value.isdigit())]) + 1)
            if current is None:
                current = etree.SubElement(properties, f"{{{custom_ns}}}property")
            for duplicate in owned[1:]:
                properties.remove(duplicate)
            for child in list(current):
                current.remove(child)
            current.set("fmtid", fmtid)
            current.set("pid", pid)
            current.set("name", property_name)
            etree.SubElement(current, f"{{{values_ns}}}lpwstr").text = notice
            updates[part] = etree.tostring(properties, encoding="UTF-8", xml_declaration=True, standalone=True)

        types = etree.fromstring(source.read("[Content_Types].xml"))
        overrides = [node for node in types if node.get("PartName") == "/" + part]
        if not overrides:
            etree.SubElement(types, f"{{{types_ns}}}Override", PartName="/" + part, ContentType=content_type)
            updates["[Content_Types].xml"] = etree.tostring(types, encoding="UTF-8", xml_declaration=True, standalone=True)
        elif overrides[0].get("ContentType") != content_type:
            overrides[0].set("ContentType", content_type)
            updates["[Content_Types].xml"] = etree.tostring(types, encoding="UTF-8", xml_declaration=True, standalone=True)
        if not updates:
            return data
        result = BytesIO()
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output:
            output.comment = source.comment
            for info in source.infolist():
                output.writestr(info, updates.pop(info.filename, source.read(info.filename)))
            for name, payload in updates.items():
                output.writestr(name, payload)
        return result.getvalue()


def _split_table_cells(row: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(row):
        char = row[index]
        if char == "{" and (index == 0 or row[index - 1] != "\\"):
            depth += 1
        elif char == "}" and (index == 0 or row[index - 1] != "\\"):
            depth = max(0, depth - 1)
        if char == "&" and depth == 0 and (index == 0 or row[index - 1] != "\\"):
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _resolve_image_paths(question: dict, uploads_dir: str | Path | None) -> list[Path]:
    root = Path(uploads_dir) if uploads_dir else None
    values: list[str] = []
    stored = question.get("image_paths", [])
    if isinstance(stored, list):
        values.extend(str(item) for item in stored)
    values.extend(IMAGE_PATTERN.findall(question.get("content", "") or ""))
    values.extend(IMAGE_PATTERN.findall(question.get("answer_markdown", "") or ""))
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        basename = Path(value).name
        if not basename or basename in seen:
            continue
        seen.add(basename)
        candidate = root / basename if root else Path(value)
        if candidate.exists():
            paths.append(candidate)
    return paths


def _prepare_question(item: dict, uploads_dir: str | Path | None) -> PreparedQuestion:
    question = dict(item.get("question", {}) or {})
    question["content"] = strip_source_attribution_comment(question.get("content", "") or "")
    content, _ = split_image_anchors(question.get("content", "") or "")
    choices: list[str] = []
    match = CHOICES_PATTERN.search(content)
    if match:
        stem = (content[: match.start()] + content[match.end() :]).strip()
        choices = [part.strip() for part in re.split(r"\\item\b", match.group(1)) if part.strip()]
    else:
        stem_lines: list[str] = []
        for line in content.splitlines():
            option = re.match(r"^\s*[-*]?\s*[A-D][\.、]\s*(.*)$", line)
            if option:
                choices.append(option.group(1).strip())
            else:
                stem_lines.append(line)
        stem = "\n".join(stem_lines).strip()
    stem = re.sub(r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}", "", stem).strip()
    answer = question.get("answer_markdown", "") or ""
    answer_tables = find_markdown_tables(answer)
    answer_clean = IMAGE_PATTERN.sub(
        lambda image: image.group(0) if any(table.start <= image.start() < table.end
                                          for table in answer_tables) else "", answer)
    answer_clean = re.sub(r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}", "", answer_clean).strip()
    solution_space = item_solution_space_cm(item, question)
    return PreparedQuestion(
        question=question,
        score=int(item.get("score", 5)),
        stem=stem,
        choices=choices,
        answer=answer_clean,
        images=_resolve_image_paths(question, uploads_dir),
        solution_space=solution_space,
    )


def build_word_document(
    title: str,
    subtitle: str,
    paper_type: str,
    questions_data: list,
    *,
    include_answers: bool = False,
    show_secret: bool = True,
    show_notice: bool = True,
    uploads_dir: str | Path | None = None,
    question_types: list | None = None,
    section_order: list | None = None,
) -> tuple[bytes, dict]:
    """Build an A4 editable DOCX and return bytes plus conversion diagnostics."""
    prepared = [_prepare_question(item, uploads_dir) for item in questions_data]
    diagnostics = WordExportDiagnostics(answer_card_omitted=(paper_type == "exam_19"))
    if diagnostics.answer_card_omitted:
        diagnostics.warnings.append("Word 版本仅导出试卷正文，不包含答题卡。")

    formula_sources: list[str] = []
    for item in prepared:
        formula_sources.extend((item.stem, item.answer, *item.choices))
    table_formula_sources: list[str] = []
    for source in formula_sources:
        table_formula_sources.extend(_word_formula_sources(source))
    formulas = _extract_formulas(table_formula_sources)
    converter = PandocOmmlConverter()
    omml_map = converter.convert_many(formulas)
    if formulas and not converter.pandoc_path:
        diagnostics.warnings.append("未检测到 Pandoc，公式将尝试转为清晰图片。")
    elif len(omml_map) < len(formulas):
        diagnostics.warnings.append("少数公式无法转为 Word 原生公式，已尝试清晰图片兜底。")

    builder = WordExamBuilder(omml_map, diagnostics)
    total_score = sum(item.score for item in prepared)
    builder.add_title_block(
        title,
        subtitle,
        len(prepared),
        total_score,
        paper_type,
        show_secret,
        show_notice,
    )

    grouped: dict[str, list[PreparedQuestion]] = {}
    for item in prepared:
        q_type = item.question.get("question_type", "single_choice")
        grouped.setdefault(q_type, []).append(item)

    number_by_object: dict[int, int] = {}
    ordered_items: list[PreparedQuestion] = []
    next_number = 1
    section_index = 0
    labels = custom_type_labels(question_types)
    for q_type in paper_type_order(grouped, paper_type, question_types, section_order):
        items = grouped.get(q_type, [])
        if not items:
            continue
        ordered_items.extend(items)
        section_index += 1
        builder.add_section_heading(q_type, items, paper_type, section_index, labels)
        current = EXAM_19_STARTS[q_type] if paper_type == "exam_19" else next_number
        for item in items:
            number_by_object[id(item)] = current
            builder.add_question(item, current)
            current += 1
        if paper_type != "exam_19":
            next_number = current

    if include_answers:
        answer_items = prepared if paper_type == "exam_19" else ordered_items
        ordered_numbers = [number_by_object.get(id(item), index + 1) for index, item in enumerate(answer_items)]
        builder.add_answers(answer_items, ordered_numbers, title)

    output = _with_content_sources_docx_property(builder.save_bytes(), content_sources_notice())
    if diagnostics.fallback_formulas:
        diagnostics.warnings.append(f"{diagnostics.fallback_formulas} 处公式以图片保真，不能在 Word 中直接编辑。")
    if diagnostics.failed_formulas:
        diagnostics.warnings.append(f"{diagnostics.failed_formulas} 处公式未能转换，文档中已用红字标出。")
    return output, diagnostics.to_dict()
