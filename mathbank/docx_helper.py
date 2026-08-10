# -*- coding: utf-8 -*-
"""Safe, diagnostic Word (.docx) exam extraction.

The extractor keeps source fidelity ahead of apparent automation: native OMML
is converted structurally, while an unsupported MathType object is preserved as
its Word preview image and marked for review instead of being guessed.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, MutableMapping, Optional

from defusedxml import ElementTree as SafeET
from PIL import Image, UnidentifiedImageError

from mathbank.mtef_helper import decode_mtef_formula
from mathbank.omml_helper import (
    normalize_word_formula_latex,
    omml_element_to_latex,
)
from mathbank.paths import UPLOADS_DIR


W_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
}

MAX_ARCHIVE_FILES = 5_000
MAX_ARCHIVE_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ARCHIVE_MEMBER = 50 * 1024 * 1024
MAX_DOCUMENT_XML = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000

_MATH_NS = W_NS["m"]
_WORD_NS = W_NS["w"]
_REL_NS = W_NS["r"]
_DRAWING_NS = W_NS["a"]
_VML_NS = W_NS["v"]
_OFFICE_NS = W_NS["o"]

_SYMBOL_FONT_MAP = {
    0x61: r"\alpha ", 0x62: r"\beta ", 0x63: r"\chi ",
    0x64: r"\delta ", 0x65: r"\varepsilon ", 0x66: r"\varphi ",
    0x67: r"\gamma ", 0x68: r"\eta ", 0x69: r"\iota ",
    0x6A: r"\phi ", 0x6B: r"\kappa ", 0x6C: r"\lambda ",
    0x6D: r"\mu ", 0x6E: r"\nu ", 0x6F: r"o",
    0x70: r"\pi ", 0x71: r"\theta ", 0x72: r"\rho ",
    0x73: r"\sigma ", 0x74: r"\tau ", 0x75: r"\upsilon ",
    0x77: r"\omega ", 0x78: r"\xi ", 0x79: r"\psi ",
    0x7A: r"\zeta ", 0xB1: r"\pm ", 0xB4: r"\times ",
    0xB9: r"\neq ", 0xA3: r"\le ", 0xB3: r"\ge ",
    0xA5: r"\infty ", 0xCE: r"\in ", 0xCF: r"\notin ",
    0xC7: r"\cap ", 0xC8: r"\cup ", 0xD8: r"\varnothing ",
}


def _new_diagnostics() -> Dict[str, Any]:
    return {
        "omml_converted": 0,
        "omml_unsupported": 0,
        "omml_private_chars_converted": 0,
        "omml_control_words_repaired": 0,
        "mtef_converted": 0,
        "mtef_annotation_converted": 0,
        "mtef_structural_converted": 0,
        "mtef_compatibility_converted": 0,
        "mtef_private_chars_converted": 0,
        "mtef_control_words_repaired": 0,
        "mtef_fallback_images": 0,
        "mtef_unavailable": 0,
        "images_extracted": 0,
        "images_unavailable": 0,
        "symbols_converted": 0,
        "symbols_unavailable": 0,
        "numbering_converted": 0,
        "numbering_unavailable": 0,
        "superscripts_converted": 0,
        "subscripts_converted": 0,
        "underlines_converted": 0,
        "text_styles_converted": 0,
        "tables_review_required": 0,
        "unsupported_omml_tags": [],
        "warnings": [],
        "review_required": 0,
    }


def _warn(diagnostics: MutableMapping, message: str) -> None:
    warnings = diagnostics.setdefault("warnings", [])
    if message not in warnings:
        warnings.append(message)


def _local_tag(elem) -> str:
    tag = getattr(elem, "tag", "")
    return tag.split("}", 1)[1] if "}" in tag else tag


def _validate_archive(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise ValueError(f"Word 压缩包文件数量异常（超过 {MAX_ARCHIVE_FILES} 个）")
    total_size = 0
    for info in infos:
        if info.file_size > MAX_ARCHIVE_MEMBER:
            raise ValueError(f"Word 内部文件过大：{info.filename}")
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_UNCOMPRESSED:
            raise ValueError("Word 解压后内容超过 200MB 安全上限")
        if info.compress_size and info.file_size > 10 * 1024 * 1024:
            if info.file_size / info.compress_size > 250:
                raise ValueError(f"Word 内部文件压缩比异常：{info.filename}")


def _extract_rels(z: zipfile.ZipFile, diagnostics: MutableMapping) -> Dict[str, str]:
    rels: Dict[str, str] = {}
    rel_path = "word/_rels/document.xml.rels"
    if rel_path not in z.namelist():
        return rels
    try:
        root = SafeET.fromstring(z.read(rel_path))
        for rel in root:
            if rel.attrib.get("TargetMode", "").lower() == "external":
                continue
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                rels[rel_id] = target
    except Exception as exc:
        _warn(diagnostics, f"Word 关系文件读取失败：{exc}")
    return rels


def _word_attr(elem, name: str, default: str = "") -> str:
    if elem is None:
        return default
    return elem.attrib.get(f"{{{_WORD_NS}}}{name}", elem.attrib.get(name, default))


def _extract_numbering(z: zipfile.ZipFile, diagnostics: MutableMapping) -> Dict[str, Any]:
    """Read Word list definitions so automatic question/choice numbers survive."""
    numbering_path = "word/numbering.xml"
    if numbering_path not in z.namelist():
        return {"abstracts": {}, "nums": {}, "counters": {}}
    try:
        root = SafeET.fromstring(z.read(numbering_path))
        abstracts: Dict[str, Dict[int, Dict[str, Any]]] = {}
        for abstract in root.findall("w:abstractNum", W_NS):
            abstract_id = _word_attr(abstract, "abstractNumId")
            levels: Dict[int, Dict[str, Any]] = {}
            for level in abstract.findall("w:lvl", W_NS):
                try:
                    ilvl = int(_word_attr(level, "ilvl", "0"))
                except ValueError:
                    ilvl = 0
                try:
                    start = int(_word_attr(level.find("w:start", W_NS), "val", "1"))
                except ValueError:
                    start = 1
                levels[ilvl] = {
                    "start": start,
                    "format": _word_attr(level.find("w:numFmt", W_NS), "val", "decimal"),
                    "text": _word_attr(level.find("w:lvlText", W_NS), "val", f"%{ilvl + 1}."),
                }
            if abstract_id:
                abstracts[abstract_id] = levels

        nums: Dict[str, Dict[str, Any]] = {}
        for num in root.findall("w:num", W_NS):
            num_id = _word_attr(num, "numId")
            abstract_id = _word_attr(num.find("w:abstractNumId", W_NS), "val")
            overrides: Dict[int, int] = {}
            for override in num.findall("w:lvlOverride", W_NS):
                try:
                    ilvl = int(_word_attr(override, "ilvl", "0"))
                    start_override = override.find("w:startOverride", W_NS)
                    if start_override is not None:
                        overrides[ilvl] = int(_word_attr(start_override, "val", "1"))
                except ValueError:
                    continue
            if num_id:
                nums[num_id] = {"abstract_id": abstract_id, "overrides": overrides}
        return {"abstracts": abstracts, "nums": nums, "counters": {}}
    except Exception as exc:
        _warn(diagnostics, f"Word 自动编号定义读取失败：{exc}")
        return {"abstracts": {}, "nums": {}, "counters": {}}


def _roman_number(value: int) -> str:
    if value <= 0:
        return str(value)
    pairs = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    parts = []
    remaining = value
    for number, symbol in pairs:
        while remaining >= number:
            parts.append(symbol)
            remaining -= number
    return "".join(parts)


def _chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if 0 <= value < 10:
        return digits[value]
    if 10 <= value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    if 20 <= value < 100:
        return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")
    return str(value)


def _format_list_number(value: int, number_format: str) -> str:
    if number_format == "upperLetter":
        return chr(ord("A") + (value - 1) % 26)
    if number_format == "lowerLetter":
        return chr(ord("a") + (value - 1) % 26)
    if number_format == "upperRoman":
        return _roman_number(value)
    if number_format == "lowerRoman":
        return _roman_number(value).lower()
    if number_format in {"chineseCounting", "chineseLegalSimplified", "ideographTraditional"}:
        return _chinese_number(value)
    if number_format == "decimalEnclosedCircle" and 1 <= value <= 20:
        return chr(0x2460 + value - 1)
    return str(value)


def _paragraph_number_prefix(paragraph, diagnostics: MutableMapping) -> str:
    p_props = paragraph.find("w:pPr", W_NS)
    num_props = p_props.find("w:numPr", W_NS) if p_props is not None else None
    if num_props is None:
        return ""
    num_id = _word_attr(num_props.find("w:numId", W_NS), "val")
    if not num_id or num_id == "0":
        return ""
    try:
        ilvl = int(_word_attr(num_props.find("w:ilvl", W_NS), "val", "0"))
    except ValueError:
        ilvl = 0

    numbering = diagnostics.get("_numbering") or {}
    num_definition = (numbering.get("nums") or {}).get(num_id)
    abstract_levels = (numbering.get("abstracts") or {}).get(
        (num_definition or {}).get("abstract_id", ""), {}
    )
    level = abstract_levels.get(ilvl)
    if not num_definition or not level:
        diagnostics["numbering_unavailable"] += 1
        diagnostics["review_required"] += 1
        _warn(diagnostics, f"无法还原 Word 自动编号：numId={num_id}, level={ilvl}")
        return "[自动编号待核对] "

    counters = numbering.setdefault("counters", {}).setdefault(num_id, {})
    for deeper_level in [key for key in counters if key > ilvl]:
        counters.pop(deeper_level, None)
    if ilvl not in counters:
        counters[ilvl] = (num_definition.get("overrides") or {}).get(ilvl, level.get("start", 1))
    else:
        counters[ilvl] += 1

    number_format = level.get("format", "decimal")
    level_text = level.get("text") or f"%{ilvl + 1}."
    if number_format == "none":
        return ""
    if number_format == "bullet":
        prefix = "-"
    else:
        prefix = level_text
        for list_level in range(9):
            placeholder = f"%{list_level + 1}"
            if placeholder not in prefix:
                continue
            child_level = abstract_levels.get(list_level, level)
            child_value = counters.get(list_level, child_level.get("start", 1))
            prefix = prefix.replace(
                placeholder,
                _format_list_number(child_value, child_level.get("format", "decimal")),
            )
    diagnostics["numbering_converted"] += 1
    return prefix.rstrip() + " "


def _resolve_archive_target(z: zipfile.ZipFile, target: str) -> Optional[str]:
    if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None
    if target.startswith("/"):
        candidate = posixpath.normpath(target.lstrip("/"))
    else:
        candidate = posixpath.normpath(posixpath.join("word", target))
    if candidate.startswith("../") or candidate not in z.namelist():
        return None
    return candidate


def _normalise_image(image_bytes: bytes, source_ext: str) -> Optional[tuple[bytes, str]]:
    """Validate image contents and convert unsupported raster/vector previews to PNG."""
    if not image_bytes or len(image_bytes) > MAX_ARCHIVE_MEMBER:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                return None
            image_format = (image.format or "").upper()
            if image_format in {"PNG", "JPEG", "GIF", "WEBP"}:
                ext = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}[image_format]
                image.verify()
                return image_bytes, ext

        # Pillow may decode BMP/TIFF and, on some platforms, WMF/EMF. Convert
        # those to a browser-safe PNG rather than serving active/unknown media.
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return None
            converted = io.BytesIO()
            image.convert("RGBA").save(converted, format="PNG")
            return converted.getvalue(), ".png"
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def _save_image(
    z: zipfile.ZipFile,
    target_rel_path: str,
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
) -> Optional[str]:
    archive_path = _resolve_archive_target(z, target_rel_path)
    if archive_path is None:
        diagnostics["images_unavailable"] += 1
        diagnostics["review_required"] += 1
        _warn(diagnostics, f"无法定位 Word 图片资源：{target_rel_path}")
        return None
    if archive_path in asset_cache:
        return asset_cache[archive_path]

    try:
        image_bytes = z.read(archive_path)
    except Exception as exc:
        diagnostics["images_unavailable"] += 1
        diagnostics["review_required"] += 1
        _warn(diagnostics, f"Word 图片读取失败：{exc}")
        return None

    normalised = _normalise_image(image_bytes, os.path.splitext(archive_path)[1].lower())
    if normalised is None:
        diagnostics["images_unavailable"] += 1
        diagnostics["review_required"] += 1
        _warn(diagnostics, f"图片格式无法安全转换，已跳过：{os.path.basename(archive_path)}")
        return None

    safe_bytes, ext = normalised
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{asset_prefix}_{uuid.uuid4().hex[:12]}{ext}"
    destination = output_dir / filename
    try:
        destination.write_bytes(safe_bytes)
    except Exception as exc:
        diagnostics["images_unavailable"] += 1
        diagnostics["review_required"] += 1
        _warn(diagnostics, f"Word 图片保存失败：{exc}")
        return None

    url = f"{url_prefix.rstrip('/')}/{filename}"
    asset_cache[archive_path] = url
    diagnostics["images_extracted"] = diagnostics.get("images_extracted", 0) + 1
    diagnostics.setdefault("asset_paths", []).append(url)
    return url


def _find_and_extract_image(
    elem,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
) -> Optional[str]:
    references = []
    for blip in elem.iter(f"{{{_DRAWING_NS}}}blip"):
        references.append(blip.attrib.get(f"{{{_REL_NS}}}embed"))
    for image_data in elem.iter(f"{{{_VML_NS}}}imagedata"):
        references.append(image_data.attrib.get(f"{{{_REL_NS}}}id"))
    for rel_id in references:
        if rel_id and rel_id in rels:
            url = _save_image(
                z, rels[rel_id], output_dir, url_prefix, asset_prefix,
                diagnostics, asset_cache,
            )
            if url:
                return url
    return None


def _extract_ole_formula_or_image(
    elem,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
) -> Optional[str]:
    ole_objects = list(elem.iter(f"{{{_OFFICE_NS}}}OLEObject"))
    for ole_obj in ole_objects:
        rel_id = ole_obj.attrib.get(f"{{{_REL_NS}}}id")
        prog_id = (ole_obj.attrib.get("ProgID") or "").lower()
        is_formula = "equation" in prog_id or "mathtype" in prog_id or not prog_id
        if rel_id and rel_id in rels:
            archive_path = _resolve_archive_target(z, rels[rel_id])
            if archive_path:
                try:
                    result = decode_mtef_formula(z.read(archive_path))
                except Exception:
                    result = None
                if result and result.success:
                    formula_diagnostics: Dict[str, Any] = {}
                    latex = normalize_word_formula_latex(
                        result.latex, formula_diagnostics
                    )
                    diagnostics["mtef_converted"] += 1
                    diagnostics["mtef_private_chars_converted"] += (
                        formula_diagnostics.get("private_use_symbols_converted", 0)
                    )
                    diagnostics["mtef_control_words_repaired"] += (
                        formula_diagnostics.get("control_word_boundaries_repaired", 0)
                    )
                    confidence_key = {
                        "high": "mtef_annotation_converted",
                        "structural": "mtef_structural_converted",
                        "compatibility": "mtef_compatibility_converted",
                    }.get(result.confidence)
                    if confidence_key:
                        diagnostics[confidence_key] += 1
                    unsupported_tokens = formula_diagnostics.get(
                        "unsupported_math_tokens", []
                    )
                    if unsupported_tokens:
                        diagnostics["review_required"] += 1
                        for token in unsupported_tokens:
                            _warn(diagnostics, f"MathType 公式包含无法识别的字符：{token}")
                        return f" ${latex}$ [公式结构待核对]"
                    return f" ${latex}$ "
                if result and result.warning:
                    _warn(diagnostics, result.warning)

        preview_url = _find_and_extract_image(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        )
        if preview_url:
            if is_formula:
                diagnostics["mtef_fallback_images"] += 1
                diagnostics["review_required"] += 1
                return f"\n[公式待核对]\n![MathType 公式待核对]({preview_url})\n"
            return f"\n![]({preview_url})\n"

        if is_formula:
            diagnostics["mtef_unavailable"] += 1
            diagnostics["review_required"] += 1
            _warn(diagnostics, "有一个 MathType 公式既无法可靠转换，也没有可用预览图")
            return " [公式待核对：Word 中的 MathType 公式无法安全提取] "
    return None


def _parse_word_symbol(elem, diagnostics: MutableMapping) -> str:
    raw_code = elem.attrib.get(f"{{{_WORD_NS}}}char", "")
    font = elem.attrib.get(f"{{{_WORD_NS}}}font", "")
    try:
        code = int(raw_code, 16)
        low_code = code & 0xFF if code >= 0xF000 else code
        if "symbol" in font.lower() or low_code in _SYMBOL_FONT_MAP:
            mapped = _SYMBOL_FONT_MAP.get(low_code)
            if mapped:
                diagnostics["symbols_converted"] += 1
                return f"${mapped.strip()}$"
        if 0 <= code <= 0x10FFFF and not 0xE000 <= code <= 0xF8FF:
            diagnostics["symbols_converted"] += 1
            return chr(code)
    except (TypeError, ValueError):
        pass
    diagnostics["symbols_unavailable"] += 1
    diagnostics["review_required"] += 1
    _warn(diagnostics, f"无法转换 Word 特殊字符：{font or '未知字体'} {raw_code or '未知编码'}")
    return "[特殊字符待核对]"


def _word_property_enabled(props, tag: str) -> bool:
    if props is None:
        return False
    prop = props.find(f"w:{tag}", W_NS)
    if prop is None:
        return False
    return _word_attr(prop, "val", "true").lower() not in {"0", "false", "none", "off"}


def _script_latex(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("$") and stripped.endswith("$") and stripped.count("$") == 2:
        return stripped[1:-1].strip()
    if re.fullmatch(r"[A-Za-z0-9+\-=.,()]+", stripped):
        return stripped
    safe_text = stripped.replace("\\", r"\textbackslash ").replace("{", r"\{").replace("}", r"\}")
    return rf"\text{{{safe_text}}}"


def _escape_latex_plain_text(value: str) -> str:
    """Escape literal Word text without touching generated formulas or markup."""
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "$": r"\textdollar{}",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _parse_run(
    elem,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
    escape_plain_text: bool = False,
) -> str:
    props = elem.find("w:rPr", W_NS)
    if _word_property_enabled(props, "vanish") or _word_property_enabled(props, "webHidden"):
        return ""
    value = "".join(
        _parse_inline(
            child, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache, escape_plain_text,
        )
        for child in elem
        if _local_tag(child) != "rPr"
    )
    if not value or props is None:
        return value

    vert_align = _word_attr(props.find("w:vertAlign", W_NS), "val").lower()
    if vert_align == "superscript":
        diagnostics["superscripts_converted"] += 1
        return f"$^{{{_script_latex(value)}}}$"
    if vert_align == "subscript":
        diagnostics["subscripts_converted"] += 1
        return f"$_{{{_script_latex(value)}}}$"

    underline = props.find("w:u", W_NS)
    underline_enabled = underline is not None and _word_attr(underline, "val", "single").lower() not in {
        "0", "false", "none", "off",
    }
    if underline_enabled:
        diagnostics["underlines_converted"] += 1
        if not value.replace("\u00a0", " ").replace("_", "").strip():
            return r"\fillin"
        value = rf"\underline{{{value}}}"

    # Preserve teacher-authored emphasis, but do not wrap image/formula fallback
    # blocks because those contain their own Markdown/LaTeX boundaries.
    can_wrap_text = "\n![" not in value and "[公式待核对" not in value
    if can_wrap_text and _word_property_enabled(props, "i"):
        value = rf"\textit{{{value}}}"
        diagnostics["text_styles_converted"] += 1
    if can_wrap_text and _word_property_enabled(props, "b"):
        value = rf"\textbf{{{value}}}"
        diagnostics["text_styles_converted"] += 1
    return value


def _parse_inline(
    elem,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
    escape_plain_text: bool = False,
) -> str:
    tag = _local_tag(elem)
    if tag in {"del", "delText", "instrText", "fldChar", "rPr", "pPr", "proofErr", "bookmarkStart", "bookmarkEnd"}:
        return ""
    if tag == "t":
        value = elem.text or ""
        return _escape_latex_plain_text(value) if escape_plain_text else value
    if tag == "r":
        return _parse_run(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache, escape_plain_text,
        )
    if tag == "tab":
        return "    "
    if tag in {"br", "cr"}:
        return "\n"
    if tag == "noBreakHyphen":
        return "‑"
    if tag == "softHyphen":
        return "\u00ad"
    if tag == "sym":
        return _parse_word_symbol(elem, diagnostics)
    if tag in {"oMath", "oMathPara"}:
        formula_diagnostics: Dict[str, Any] = {}
        latex = normalize_word_formula_latex(
            omml_element_to_latex(elem, formula_diagnostics).strip(),
            formula_diagnostics,
        )
        diagnostics["omml_converted"] += 1
        diagnostics["omml_private_chars_converted"] += formula_diagnostics.get(
            "private_use_symbols_converted", 0
        )
        diagnostics["omml_control_words_repaired"] += formula_diagnostics.get(
            "control_word_boundaries_repaired", 0
        )
        unsupported_tags = formula_diagnostics.get("unsupported_omml_tags", [])
        for unsupported_tag in unsupported_tags:
            if unsupported_tag not in diagnostics["unsupported_omml_tags"]:
                diagnostics["unsupported_omml_tags"].append(unsupported_tag)
        if unsupported_tags:
            diagnostics["omml_unsupported"] += 1
            diagnostics["review_required"] += 1
            return f" ${latex}$ [公式结构待核对]" if latex else "[公式待核对]"
        return f" ${latex}$ " if latex else "[公式待核对]"
    if tag in {"drawing", "pict"}:
        url = _find_and_extract_image(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        )
        embedded_text = []
        for text_box in elem.iter(f"{{{_WORD_NS}}}txbxContent"):
            embedded_text.append(_parse_inline(
                text_box, z, rels, output_dir, url_prefix, asset_prefix,
                diagnostics, asset_cache, escape_plain_text,
            ))
        parts = [text for text in embedded_text if text]
        if url:
            parts.append(f"\n![]({url})\n")
        return "\n".join(parts)
    if tag == "object":
        value = _extract_ole_formula_or_image(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        )
        return value or ""

    parts = []
    for child in elem:
        parts.append(_parse_inline(
            child, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache, escape_plain_text,
        ))
    return "".join(parts)


def _parse_paragraph(
    paragraph,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
    escape_plain_text: bool = False,
) -> str:
    value = _parse_inline(
        paragraph, z, rels, output_dir, url_prefix, asset_prefix,
        diagnostics, asset_cache, escape_plain_text,
    ).strip()
    prefix = _paragraph_number_prefix(paragraph, diagnostics)
    return prefix + value if value else ""


def _parse_table(
    table,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
) -> str:
    rows: list[list[Dict[str, Any]]] = []
    max_cols = 1
    for row in table.findall("w:tr", W_NS):
        cells = []
        column_count = 0
        for cell in row.findall("w:tc", W_NS):
            cell_parts = []
            for child in cell:
                child_tag = _local_tag(child)
                if child_tag == "p":
                    text = _parse_paragraph(
                        child, z, rels, output_dir, url_prefix, asset_prefix,
                        diagnostics, asset_cache, True,
                    )
                    if text:
                        cell_parts.append(text)
                elif child_tag == "tbl":
                    diagnostics["tables_review_required"] += 1
                    diagnostics["review_required"] += 1
                    _warn(diagnostics, "检测到嵌套 Word 表格，已保留其文本但排版需人工核对")
                    nested_text = " ".join(t.text or "" for t in child.iter(f"{{{_WORD_NS}}}t"))
                    if nested_text:
                        cell_parts.append(_escape_latex_plain_text(nested_text))

            cell_text = " ".join(cell_parts).strip() or " "
            span = 1
            vertical_merge = None
            cell_props = cell.find("w:tcPr", W_NS)
            if cell_props is not None:
                grid_span = cell_props.find("w:gridSpan", W_NS)
                if grid_span is not None:
                    try:
                        span = max(1, int(grid_span.attrib.get(f"{{{_WORD_NS}}}val", "1")))
                    except ValueError:
                        span = 1
                vertical_merge_elem = cell_props.find("w:vMerge", W_NS)
                if vertical_merge_elem is not None:
                    merge_value = _word_attr(vertical_merge_elem, "val").lower()
                    vertical_merge = "restart" if merge_value == "restart" else "continue"
            cells.append({
                "text": cell_text,
                "span": span,
                "start_col": column_count,
                "vmerge": vertical_merge,
                "rowspan": 1,
                "continuation": False,
            })
            column_count += span
        if cells:
            max_cols = max(max_cols, column_count)
            rows.append(cells)

    if not rows:
        return ""

    # Word represents vertical merges as a restart cell followed by one or
    # more continuation cells. Resolve the complete chain before rendering.
    for row_index, cells in enumerate(rows):
        for cell in cells:
            if cell["vmerge"] != "restart":
                continue
            continuation_cells = []
            for later_row in rows[row_index + 1:]:
                continuation = next((
                    candidate for candidate in later_row
                    if candidate["start_col"] == cell["start_col"]
                    and candidate["span"] == cell["span"]
                    and candidate["vmerge"] == "continue"
                ), None)
                if continuation is None:
                    break
                continuation_cells.append(continuation)
            if continuation_cells:
                cell["rowspan"] = len(continuation_cells) + 1
                for continuation in continuation_cells:
                    continuation["continuation"] = True

    for cells in rows:
        for cell in cells:
            if cell["vmerge"] == "continue" and not cell["continuation"]:
                diagnostics["tables_review_required"] += 1
                diagnostics["review_required"] += 1
                _warn(diagnostics, "检测到不完整的纵向合并单元格，已保留其文字并建议核对")

    lines = [f"\\begin{{tabular}}{{|{'c|' * max_cols}}}", "\\hline"]
    for row_index, cells in enumerate(rows):
        used = sum(cell["span"] for cell in cells)
        rendered = []
        for cell in cells:
            text = "" if cell["continuation"] else cell["text"]
            if cell["rowspan"] > 1:
                text = f"\\multirow{{{cell['rowspan']}}}{{*}}{{{text}}}"
            if cell["span"] > 1:
                text = f"\\multicolumn{{{cell['span']}}}{{|c|}}{{{text}}}"
            rendered.append(text)
        rendered.extend(" " for _ in range(max_cols - used))
        lines.append(" & ".join(rendered) + " \\\\")
        blocked_columns = set()
        for start_row, start_cells in enumerate(rows[:row_index + 1]):
            for cell in start_cells:
                if cell["rowspan"] > 1 and start_row <= row_index < start_row + cell["rowspan"] - 1:
                    blocked_columns.update(range(
                        cell["start_col"] + 1,
                        cell["start_col"] + cell["span"] + 1,
                    ))
        if not blocked_columns:
            lines.append("\\hline")
        else:
            interval_start = None
            for column in range(1, max_cols + 2):
                is_available = column <= max_cols and column not in blocked_columns
                if is_available and interval_start is None:
                    interval_start = column
                elif not is_available and interval_start is not None:
                    lines.append(f"\\cline{{{interval_start}-{column - 1}}}")
                    interval_start = None
    lines.append("\\end{tabular}")
    return "\n" + "\n".join(lines) + "\n"


def _parse_block(
    elem,
    z: zipfile.ZipFile,
    rels: Dict[str, str],
    output_dir: Path,
    url_prefix: str,
    asset_prefix: str,
    diagnostics: MutableMapping,
    asset_cache: MutableMapping[str, str],
) -> list[str]:
    """Recursively unwrap body-level content controls/custom XML containers."""
    tag = _local_tag(elem)
    if tag == "p":
        value = _parse_paragraph(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        )
        return [value] if value else []
    if tag == "tbl":
        value = _parse_table(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        )
        return [value] if value else []
    if tag in {"sectPr", "bookmarkStart", "bookmarkEnd"}:
        return []
    blocks: list[str] = []
    for child in elem:
        blocks.extend(_parse_block(
            child, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        ))
    return blocks


def extract_docx_markdown(
    file_bytes_or_path,
    *,
    output_dir: Optional[Path] = None,
    url_prefix: str = "/static/uploads",
    asset_prefix: str = "word_img",
) -> Dict[str, Any]:
    """Extract DOCX content and return Markdown plus a fidelity report."""
    diagnostics = _new_diagnostics()
    destination = Path(output_dir or UPLOADS_DIR)
    file_obj = file_bytes_or_path if isinstance(file_bytes_or_path, (str, os.PathLike)) else io.BytesIO(file_bytes_or_path)
    try:
        with zipfile.ZipFile(file_obj, "r") as z:
            _validate_archive(z)
            if "word/document.xml" not in z.namelist():
                raise ValueError("不是有效的 .docx 文件：缺少 word/document.xml")
            doc_info = z.getinfo("word/document.xml")
            if doc_info.file_size > MAX_DOCUMENT_XML:
                raise ValueError("Word 主文档 XML 超过 25MB 安全上限")

            rels = _extract_rels(z, diagnostics)
            diagnostics["_numbering"] = _extract_numbering(z, diagnostics)
            root = SafeET.fromstring(z.read("word/document.xml"))
            body = root.find("w:body", W_NS)
            if body is None:
                raise ValueError("Word 文档结构无效：缺少 w:body")

            blocks = []
            asset_cache: Dict[str, str] = {}
            for child in body:
                blocks.extend(_parse_block(
                    child, z, rels, destination, url_prefix, asset_prefix,
                    diagnostics, asset_cache,
                ))

            markdown = re.sub(r"\n{3,}", "\n\n", "\n\n".join(blocks).strip())
            from mathbank.ai_json import normalize_subquestions_double_newlines
            markdown = normalize_subquestions_double_newlines(markdown)
            diagnostics["unsupported_omml_tags"].sort()
            if diagnostics["unsupported_omml_tags"]:
                _warn(
                    diagnostics,
                    "部分 Office 公式含暂未完整支持的结构："
                    + ", ".join(diagnostics["unsupported_omml_tags"]),
                )
            diagnostics.pop("_numbering", None)
            return {
                "success": True,
                "markdown": markdown,
                "error": None,
                "image_count": diagnostics["images_extracted"],
                "image_paths": list(diagnostics.get("asset_paths", [])),
                "diagnostics": diagnostics,
            }
    except Exception as exc:
        diagnostics.pop("_numbering", None)
        return {
            "success": False,
            "markdown": "",
            "error": f"Word 文档解析失败：{exc}",
            "image_count": 0,
            "image_paths": list(diagnostics.get("asset_paths", [])),
            "diagnostics": diagnostics,
        }
