# -*- coding: utf-8 -*-
"""Safe, diagnostic Word (.docx) exam extraction.

The extractor keeps source fidelity ahead of apparent automation: native OMML
is converted structurally, while an unsupported MathType object is preserved as
its Word preview image and marked for review instead of being guessed.
"""

from __future__ import annotations

import io
import hashlib
import os
import posixpath
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path
from typing import Any, Dict, MutableMapping, Optional

from defusedxml import ElementTree as SafeET
from PIL import Image, UnidentifiedImageError

from mathbank.mtef_helper import decode_mtef_formula
from mathbank.mathml_helper import MATHML_NS, MathMLUnsupported, mathml_element_to_latex
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
_COMPATIBILITY_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

# w:sym is a font glyph reference. A low byte is not a character identity in
# an arbitrary font: e.g. Arial/Wingdings F061 must never become Symbol alpha.
_WORD_UNICODE_SYMBOL_FONTS = {"arial", "times new roman", "cambria", "cambria math",
                              "calibri", "courier new", "times", "helvetica"}

# Adobe Symbol mapping data notice retained from the Unicode vendor table:
# Copyright (c) 1991-2011 Unicode, Inc. All Rights reserved.
#
# This file is provided as-is by Unicode, Inc. (The Unicode Consortium). No
# claims are made as to fitness for any particular purpose. No warranties of
# any kind are expressed or implied. The recipient agrees to determine
# applicability of information provided. If this file has been provided on
# magnetic media by Unicode, Inc., the sole remedy for any claim will be
# exchange of defective media within 90 days of receipt.
#
# Unicode, Inc. hereby grants the right to freely use the information
# supplied in this file in the creation of products supporting the
# Unicode Standard, and to make copies of this file in any form for
# internal or external distribution as long as this notice remains
# attached.
_SYMBOL_FONT_MAP = {
    # Adobe Symbol slots, checked against Unicode's official vendor mapping:
    # https://www.unicode.org/Public/MAPPINGS/VENDORS/ADOBE/symbol.txt
    # Extenders and composite bracket/integral pieces intentionally have no map.
    0x22: r"\forall ", 0x24: r"\exists ", 0x27: r"\ni ",
    0x2A: r"\ast ", 0x2B: "+", 0x2C: ",", 0x2D: "-", 0x2E: ".", 0x2F: "/",
    0x3A: ":", 0x3B: ";", 0x3C: "<", 0x3D: "=", 0x3E: ">",
    0x40: r"\cong ",
    0x41: r"\mathrm{A}", 0x42: r"\mathrm{B}", 0x43: r"\mathrm{X}",
    0x44: r"\Delta ", 0x45: r"\mathrm{E}", 0x46: r"\Phi ",
    0x47: r"\Gamma ", 0x48: r"\mathrm{H}", 0x49: r"\mathrm{I}",
    0x4A: r"\vartheta ", 0x4B: r"\mathrm{K}", 0x4C: r"\Lambda ",
    0x4D: r"\mathrm{M}", 0x4E: r"\mathrm{N}", 0x4F: r"\mathrm{O}",
    0x50: r"\Pi ", 0x51: r"\Theta ", 0x52: r"\mathrm{P}",
    0x53: r"\Sigma ", 0x54: r"\mathrm{T}", 0x55: r"\Upsilon ",
    0x56: r"\varsigma ", 0x57: r"\Omega ", 0x58: r"\Xi ",
    0x59: r"\Psi ", 0x5A: r"\mathrm{Z}", 0x5C: r"\therefore ", 0x5E: r"\perp ",
    0x61: r"\alpha ", 0x62: r"\beta ", 0x63: r"\chi ",
    0x64: r"\delta ", 0x65: r"\varepsilon ", 0x66: r"\varphi ",
    0x67: r"\gamma ", 0x68: r"\eta ", 0x69: r"\iota ",
    0x6A: r"\phi ", 0x6B: r"\kappa ", 0x6C: r"\lambda ",
    0x6D: r"\mu ", 0x6E: r"\nu ", 0x6F: r"o",
    0x70: r"\pi ", 0x71: r"\theta ", 0x72: r"\rho ",
    0x73: r"\sigma ", 0x74: r"\tau ", 0x75: r"\upsilon ",
    0x76: r"\varpi ", 0x77: r"\omega ", 0x78: r"\xi ", 0x79: r"\psi ",
    0x7A: r"\zeta ", 0xB1: r"\pm ", 0xB4: r"\times ",
    0xB9: r"\neq ", 0xA3: r"\le ", 0xB3: r"\ge ",
    0xA5: r"\infty ", 0xCE: r"\in ", 0xCF: r"\notin ",
    0x7E: r"\sim ", 0xA2: r"\prime ",
    0xAB: r"\leftrightarrow ", 0xAC: r"\leftarrow ", 0xAD: r"\uparrow ",
    0xAE: r"\rightarrow ", 0xAF: r"\downarrow ",
    0xB0: r"{}^\circ ", 0xB2: r"\prime\prime ", 0xB5: r"\propto ",
    0xB6: r"\partial ", 0xB7: r"\bullet ", 0xB8: r"\div ",
    0xBA: r"\equiv ", 0xBB: r"\approx ", 0xBC: r"\ldots ",
    0xC0: r"\aleph ", 0xC1: r"\Im ",
    0xC2: r"\Re ", 0xC3: r"\wp ", 0xC4: r"\otimes ", 0xC5: r"\oplus ",
    0xC6: r"\varnothing ", 0xC7: r"\cap ", 0xC8: r"\cup ",
    0xC9: r"\supset ", 0xCA: r"\supseteq ", 0xCB: r"\not\subset ",
    0xCC: r"\subset ", 0xCD: r"\subseteq ", 0xD0: r"\angle ",
    0xD1: r"\nabla ", 0xD5: r"\prod ", 0xD6: r"\surd ", 0xD7: r"\cdot ",
    0xD8: r"\neg ", 0xD9: r"\land ", 0xDA: r"\lor ",
    0xDB: r"\Leftrightarrow ", 0xDC: r"\Leftarrow ", 0xDD: r"\Uparrow ",
    0xDE: r"\Rightarrow ", 0xDF: r"\Downarrow ", 0xE0: r"\lozenge ",
    0xE1: r"\langle ", 0xE5: r"\sum ", 0xF1: r"\rangle ", 0xF2: r"\int ",
}

_SYMBOL_FONT_MAP.update({code: chr(code) for code in range(0x30, 0x3A)})


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
        "mtef_ignored_spacing_codes": [],
        "native_missing_glyphs": 0,
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


def _numbering_level_definition(level, ilvl: int) -> Dict[str, Any]:
    try:
        start = int(_word_attr(level.find("w:start", W_NS), "val", "1"))
    except ValueError:
        start = 1
    try:
        # OOXML uses a one-based restart level. Omitted/invalid values mean
        # the previous level, while zero explicitly means never restart.
        restart = int(_word_attr(level.find("w:lvlRestart", W_NS), "val", str(ilvl)))
    except ValueError:
        restart = ilvl
    if restart < 0 or restart > ilvl:
        restart = ilvl
    return {
        "start": start,
        "format": _word_attr(level.find("w:numFmt", W_NS), "val", "decimal"),
        "text": _word_attr(level.find("w:lvlText", W_NS), "val", f"%{ilvl + 1}."),
        "restart": restart,
    }


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
                levels[ilvl] = _numbering_level_definition(level, ilvl)
            if abstract_id:
                abstracts[abstract_id] = levels

        nums: Dict[str, Dict[str, Any]] = {}
        for num in root.findall("w:num", W_NS):
            num_id = _word_attr(num, "numId")
            abstract_id = _word_attr(num.find("w:abstractNumId", W_NS), "val")
            overrides: Dict[int, int] = {}
            level_overrides: Dict[int, Dict[str, Any]] = {}
            for override in num.findall("w:lvlOverride", W_NS):
                try:
                    ilvl = int(_word_attr(override, "ilvl", "0"))
                    override_level = override.find("w:lvl", W_NS)
                    if override_level is not None:
                        level_overrides[ilvl] = _numbering_level_definition(override_level, ilvl)
                    start_override = override.find("w:startOverride", W_NS)
                    if start_override is not None:
                        overrides[ilvl] = int(_word_attr(start_override, "val", "1"))
                except ValueError:
                    continue
            if num_id:
                nums[num_id] = {
                    "abstract_id": abstract_id,
                    "overrides": overrides,
                    "levels": level_overrides,
                }
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
    abstract_levels = dict((numbering.get("abstracts") or {}).get(
        (num_definition or {}).get("abstract_id", ""), {}
    ))
    # The num instance's complete lvl replaces its abstract level definition;
    # startOverride, if present, then takes precedence over that level's start.
    abstract_levels.update((num_definition or {}).get("levels") or {})
    level = abstract_levels.get(ilvl)
    if not num_definition or not level:
        diagnostics["numbering_unavailable"] += 1
        diagnostics["review_required"] += 1
        _warn(diagnostics, f"无法还原 Word 自动编号：numId={num_id}, level={ilvl}")
        return "[自动编号待核对] "

    counters = numbering.setdefault("counters", {}).setdefault(num_id, {})
    for deeper_level in [key for key in counters if key > ilvl]:
        child_definition = abstract_levels.get(deeper_level, {})
        if child_definition.get("restart", deeper_level) == ilvl + 1:
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


def _select_compatible_alternatives(root, z: zipfile.ZipFile, rels: Dict[str, str]) -> None:
    """Read one representation of Word's Choice/Fallback pairs, never both.

    A fallback belongs to the same visible object as its choice. In particular,
    reading a legacy OLE fallback after native OMML creates a spurious formula
    warning. Prefer a choice only when its drawing resources can be read by our
    extractor; an unsupported vector drawing may have a usable raster fallback.
    This only prunes the parsed XML copy, without changing the source package.
    """
    image_support: Dict[str, bool] = {}

    def has_image(elem) -> bool:
        references = [blip.attrib.get(f"{{{_REL_NS}}}embed")
                      for blip in elem.iter(f"{{{_DRAWING_NS}}}blip")]
        references.extend(image.attrib.get(f"{{{_REL_NS}}}id")
                          for image in elem.iter(f"{{{_VML_NS}}}imagedata"))
        for rel_id in references:
            target = rels.get(rel_id or "", "")
            if target not in image_support:
                archive_path = _resolve_archive_target(z, target)
                try:
                    image_support[target] = bool(
                        archive_path and _normalise_image(
                            z.read(archive_path), os.path.splitext(archive_path)[1].lower()
                        )
                    )
                except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                    image_support[target] = False
            if image_support[target]:
                return True
        return False

    def payload_support(elem) -> tuple[bool, bool]:
        # Native formulas keep their usual structural diagnostics; an unknown
        # formula must not silently turn into an unmarked fallback picture.
        if elem.tag in {f"{{{_MATH_NS}}}oMath", f"{{{_MATH_NS}}}oMathPara", f"{{{MATHML_NS}}}math"}:
            return True, True
        if elem.tag in {f"{{{_DRAWING_NS}}}blip", f"{{{_VML_NS}}}imagedata"}:
            return True, has_image(elem)
        if elem.tag in {f"{{{_WORD_NS}}}drawing", f"{{{_WORD_NS}}}pict"}:
            return True, has_image(elem) or any(
                payload_support(text_box) == (True, True)
                for text_box in elem.iter(f"{{{_WORD_NS}}}txbxContent")
            )
        if elem.tag == f"{{{_WORD_NS}}}object":
            return True, True
        if elem.tag in {f"{{{_WORD_NS}}}t", f"{{{_WORD_NS}}}sym"}:
            return True, True
        visible_children = [supported for found, supported in
                            (payload_support(child) for child in elem) if found]
        return bool(visible_children), all(visible_children)

    def visit(elem) -> None:
        for child in list(elem):
            visit(child)
        if elem.tag != f"{{{_COMPATIBILITY_NS}}}AlternateContent":
            return
        choices = [child for child in elem
                   if child.tag == f"{{{_COMPATIBILITY_NS}}}Choice"]
        fallback = next((child for child in elem
                         if child.tag == f"{{{_COMPATIBILITY_NS}}}Fallback"), None)
        selected = next((choice for choice in choices
                         if payload_support(choice) == (True, True)), None)
        if selected is None:
            # If all representations are unavailable, retain one so the normal
            # extraction path still reports its genuine resource/formula error.
            selected = fallback if fallback is not None else (choices[0] if choices else None)
        elem[:] = [selected] if selected is not None else []

    visit(root)


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
    if "_source_review_assets" in diagnostics:
        import hashlib
        diagnostics["_source_review_assets"].append({"url": url,
            "absolute_path": str(destination.absolute()),
            "normalized_sha256": hashlib.sha256(safe_bytes).hexdigest()})
    from .docx_source_assets import record_empty_raster
    record_empty_raster(diagnostics, url, destination, image_bytes, safe_bytes)
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
                from .docx_source_assets import note_image_context
                note_image_context(diagnostics, url, elem)
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
    if len(ole_objects) > 1:
        # AlternateContent was already reduced to one representation. Multiple
        # remaining OLE records belong to an ambiguous visible carrier: a safe
        # first formula must not hide the later original records or previews.
        _warn(diagnostics, "同一 Word 对象含多个嵌入记录，已保留原预览供核对")
        review_before_preview = diagnostics["review_required"]
        preview_urls, saved_references = [], {}
        for image in elem.iter():
            if image.tag == f"{{{_DRAWING_NS}}}blip":
                reference = image.attrib.get(f"{{{_REL_NS}}}embed")
            elif image.tag == f"{{{_VML_NS}}}imagedata":
                reference = image.attrib.get(f"{{{_REL_NS}}}id")
            else:
                continue
            if reference and reference in rels:
                if reference not in saved_references:
                    saved_references[reference] = _save_image(
                        z, rels[reference], output_dir, url_prefix,
                        asset_prefix, diagnostics, asset_cache,
                    )
                url = saved_references[reference]
                if url:
                    from .docx_source_assets import note_image_context
                    note_image_context(diagnostics, url, elem)
                    preview_urls.append(url)
        if diagnostics["review_required"] == review_before_preview:
            diagnostics["review_required"] += 1
        if preview_urls:
            diagnostics["mtef_fallback_images"] += 1
            return "\n" + "\n".join(
                f"![]({url})" for url in preview_urls
            ) + "\n"
        diagnostics["mtef_unavailable"] += 1
        diagnostics["native_missing_glyphs"] += 1
        _warn(diagnostics, "多个嵌入记录的 Word 对象没有可用原预览图")
        return ""
    for ole_obj in ole_objects:
        partial_latex = ""
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
                    unsupported_tokens = formula_diagnostics.get(
                        "unsupported_math_tokens", []
                    )
                    if unsupported_tokens:
                        partial_latex = latex
                        for token in unsupported_tokens:
                            _warn(diagnostics, f"MathType 公式包含无法识别的字符：{token}")
                        # A parsed record with unknown glyphs is not a converted
                        # formula. Keep its original preview through the same
                        # bounded fallback as a failed structural decode.
                    else:
                        for code in result.ignored_spacing_codes:
                            if code not in diagnostics["mtef_ignored_spacing_codes"]:
                                diagnostics["mtef_ignored_spacing_codes"].append(code)
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
                        return f" ${latex}$ "
                if result and result.warning:
                    _warn(diagnostics, result.warning)

        review_before_preview = diagnostics["review_required"]
        preview_url = _find_and_extract_image(
            elem, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        )
        if preview_url:
            if is_formula:
                diagnostics["mtef_fallback_images"] += 1
                if diagnostics["review_required"] == review_before_preview:
                    diagnostics["review_required"] += 1
                return f"\n![]({preview_url})\n"
            return f"\n![]({preview_url})\n"

        if is_formula:
            diagnostics["mtef_unavailable"] += 1
            diagnostics["native_missing_glyphs"] += 1
            # An unavailable preview has already recorded this object's review;
            # do not count the same failed fallback twice.
            if diagnostics["review_required"] == review_before_preview:
                diagnostics["review_required"] += 1
            _warn(diagnostics, "有一个 MathType 公式既无法可靠转换，也没有可用预览图")
            return f" ${partial_latex}$ " if partial_latex else ""
    return None


def _parse_word_symbol(elem, diagnostics: MutableMapping) -> str:
    raw_code = elem.attrib.get(f"{{{_WORD_NS}}}char", "")
    font = elem.attrib.get(f"{{{_WORD_NS}}}font", "")
    try:
        code = int(raw_code, 16)
        font_key = font.strip().casefold()
        low_code = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
        if font_key == "symbol":
            mapped = _SYMBOL_FONT_MAP.get(low_code)
            if mapped:
                diagnostics["symbols_converted"] += 1
                return f"${mapped.strip()}$"
        if (font_key in _WORD_UNICODE_SYMBOL_FONTS and 0x20 <= code <= 0x10FFFF
                and not 0xD800 <= code <= 0xDFFF
                and not (0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0xFFFFD
                         or 0x100000 <= code <= 0x10FFFD)):
            diagnostics["symbols_converted"] += 1
            return chr(code)
    except (TypeError, ValueError):
        pass
    diagnostics["symbols_unavailable"] += 1
    diagnostics["native_missing_glyphs"] += 1
    diagnostics["review_required"] += 1
    reason = f"无法转换 Word 特殊字符：{font or '未知字体'} {raw_code or '未知编码'}"
    _retain_formula_source(diagnostics, "Word font symbol", ET.tostring(elem, encoding="unicode"), reason)
    _warn(diagnostics, reason)
    return ""


def _retain_formula_source(diagnostics: MutableMapping, source_type: str, source: str,
                           reason: str) -> None:
    """Keep complete bounded source evidence; never a truncated pseudo-formula."""
    evidence = diagnostics.setdefault("unsupported_formula_sources", [])
    row = {"type": source_type, "sha256": hashlib.sha256(source.encode()).hexdigest(),
           "characters": len(source), "reason": reason}
    if len(source) <= 32768 and len(evidence) < 32:
        row["source"] = source
    else:
        row["source_retained"] = False
    if len(evidence) < 128:
        evidence.append(row)


def _parse_embedded_mathml(elem, diagnostics: MutableMapping) -> str:
    try:
        value = mathml_element_to_latex(elem)
    except MathMLUnsupported as exc:
        reason = str(exc)
        diagnostics["review_required"] += 1
        diagnostics["mathml_unsupported"] = diagnostics.get("mathml_unsupported", 0) + 1
        diagnostics["native_missing_glyphs"] += 1
        _retain_formula_source(diagnostics, "MathML", ET.tostring(elem, encoding="unicode"), reason)
        _warn(diagnostics, "MathML 原式无法可靠转换：" + reason)
        return ""
    diagnostics["mathml_converted"] = diagnostics.get("mathml_converted", 0) + 1
    return f" ${value}$ "


def _record_equation_fields(paragraph, diagnostics: MutableMapping) -> int:
    """Find legacy EQ fields without executing instructions or losing caches.

    Complex field instructions may span runs. Only code before a field's
    separator is assembled; nested text boxes and deleted revisions have their
    own visibility/paragraph handling and must not be counted twice here.
    """
    instructions = []
    stack = []
    orphan_chunks = []

    pending = [paragraph]
    while pending:
        node = pending.pop()
        tag = _local_tag(node)
        if node is not paragraph and tag in {"p", "txbxContent", "del", "moveFrom"}:
            continue
        if node.tag == f"{{{_WORD_NS}}}fldSimple":
            instructions.append(node.attrib.get(f"{{{_WORD_NS}}}instr", ""))
        elif node.tag == f"{{{_WORD_NS}}}fldChar":
            state = node.attrib.get(f"{{{_WORD_NS}}}fldCharType", "")
            if state == "begin":
                stack.append({"chunks": [], "code": True})
            elif state == "separate" and stack:
                stack[-1]["code"] = False
            elif state == "end" and stack:
                instructions.append("".join(stack.pop()["chunks"]))
        elif node.tag == f"{{{_WORD_NS}}}instrText":
            text = node.text or ""
            if stack:
                if stack[-1]["code"]:
                    stack[-1]["chunks"].append(text)
            else:
                orphan_chunks.append(text)
        pending.extend(reversed(list(node)))
    instructions.extend("".join(field["chunks"]) for field in stack)
    if orphan_chunks:
        instructions.append("".join(orphan_chunks))
    found = 0
    for instruction in instructions:
        if not re.match(r"^\s*EQ(?=\s|\\|$)", instruction, re.I):
            continue
        found += 1
        diagnostics["review_required"] += 1
        diagnostics["equation_fields_unsupported"] = diagnostics.get("equation_fields_unsupported", 0) + 1
        _retain_formula_source(diagnostics, "Word EQ field", instruction, "旧 EQ 公式域尚未支持结构转换")
        _warn(diagnostics, "Word 含旧 EQ 公式域：保留可见缓存结果，原公式结构须核对")
    return found


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
    if isinstance(elem.tag, str) and elem.tag.startswith(f"{{{MATHML_NS}}}"):
        return _parse_embedded_mathml(elem, diagnostics)
    if tag in {"del", "delText", "moveFrom", "instrText", "fldChar", "rPr", "pPr", "sdtPr", "sdtEndPr", "customXmlPr", "proofErr", "bookmarkStart", "bookmarkEnd"}:
        return ""
    if elem.tag == f"{{{_WORD_NS}}}sdt":
        content = elem.find("w:sdtContent", W_NS)
        return _parse_inline(
            content, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache, escape_plain_text,
        ) if content is not None else ""
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
        unsupported_tags = list(dict.fromkeys(
            formula_diagnostics.get("unsupported_omml_tags", [])
            + formula_diagnostics.get("unsupported_math_tokens", [])
        ))
        for unsupported_tag in unsupported_tags:
            if unsupported_tag not in diagnostics["unsupported_omml_tags"]:
                diagnostics["unsupported_omml_tags"].append(unsupported_tag)
        if unsupported_tags:
            diagnostics["omml_unsupported"] += 1
            diagnostics["native_missing_glyphs"] += 1
            diagnostics["review_required"] += 1
            _retain_formula_source(diagnostics, "OMML", ET.tostring(elem, encoding="unicode"),
                                   ", ".join(unsupported_tags))
            return f" ${latex}$ " if latex else ""
        return f" ${latex}$ " if latex else ""
    if tag == "txbxContent":
        # A text box is a block container, not a run. Joining its paragraphs
        # without boundaries can merge question numbers or numeric conditions.
        blocks = []
        for child in _iter_word_container_children(elem, {
            f"{{{_WORD_NS}}}p", f"{{{_WORD_NS}}}tbl",
            f"{{{_MATH_NS}}}oMath", f"{{{_MATH_NS}}}oMathPara",
        }):
            if _local_tag(child) == "p":
                value = _parse_paragraph(
                    child, z, rels, output_dir, url_prefix, asset_prefix,
                    diagnostics, asset_cache, escape_plain_text,
                )
            elif _local_tag(child) == "tbl":
                value = _parse_table(
                    child, z, rels, output_dir, url_prefix, asset_prefix,
                    diagnostics, asset_cache,
                )
            else:
                value = _parse_inline(
                    child, z, rels, output_dir, url_prefix, asset_prefix,
                    diagnostics, asset_cache, escape_plain_text,
                )
            if value:
                blocks.append(value)
        return "\n\n".join(blocks)
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
    equation_fields = _record_equation_fields(paragraph, diagnostics)
    value = _parse_inline(
        paragraph, z, rels, output_dir, url_prefix, asset_prefix,
        diagnostics, asset_cache, escape_plain_text,
    ).strip()
    if equation_fields:
        diagnostics["native_missing_glyphs"] += equation_fields
    if not escape_plain_text:
        candidate = _question_heading_image_scope(paragraph)
        image = re.search(r"(!\[\]\(([^\n)]+)\))[ \t\r\n]*", value)
        if candidate is not None and image:
            number, field, token, safe, reason = candidate
            if safe and len(re.findall(r"!\[\]\([^\n)]+\)", value)) != 1:
                # Include visible VML pictures inside structured wrappers and
                # formula previews, which need not be additional w:drawing.
                safe, reason = False, "multiple_visible_image_outputs"
            adjacent = diagnostics.get("_paragraph_image_ambiguities", {}).get(id(paragraph))
            if adjacent is not None and safe:
                safe, reason = False, "following_question_refers_unplaced_figure"
            remaining = value[image.end():]
            heading = re.match(r"([1-9]\d{0,2}[.．、])(?!\d)" if field == "content" else
                r"(?:\\(?:textbf|textit|textrm)\{【(?:答案|解析|详解)】\}|【(?:答案|解析|详解)】)", remaining)
            same = (heading and (int(re.match(r"\d+", heading.group()).group()) == number
                    if field == "content" else token in heading.group()))
            if safe and image.start() == 0 and same:
                # Move only the ordinal before the image. Its relationship to
                # every stem/formula/caption stays in the original sequence;
                # no image is moved across paragraph boundaries or reshaped.
                value = heading.group() + "\n\n" + image.group(1) + "\n\n" + remaining[heading.end():]
                status = ("same_paragraph_heading_order_corrected" if field == "content"
                          else "same_paragraph_answer_label_order_corrected")
            else:
                diagnostics["review_required"] += 1
                status = "ownership_uncertain"
                reason = reason if not safe else "rendered_heading_not_safely_separable"
                label = f"第{number}题题首" if number is not None else "答案标签首段"
                _warn(diagnostics, f"{label}浮图归属需核对：{reason}；{image.group(2)}")
            diagnostics.setdefault("heading_image_ownership", []).append({
                "source_number": number, "image_path": image.group(2),
                "field": field, "status": status, "reason": reason,
                **({"possible_adjacent_source_number": adjacent} if adjacent is not None else {}),
            })
    prefix = _paragraph_number_prefix(paragraph, diagnostics)
    return prefix + value if value else ""


def _question_heading_image_scope(paragraph):
    """Prove one leading picture belongs to this explicit question paragraph.

    Word serializes floating pictures before the paragraph's text. Without the
    ordinal first, a source splitter can attach it to the previous answer. Only
    a complete, nonnegative paragraph-relative anchor supplies this evidence.
    """
    metadata = {"pPr", "proofErr", "bookmarkStart", "bookmarkEnd", "permStart", "permEnd"}
    children = [child for child in paragraph if _local_tag(child) not in metadata]
    if not children or children[0].tag != f"{{{_WORD_NS}}}r":
        return None
    payload = [child for child in children[0] if _local_tag(child) != "rPr"]
    if len(payload) != 1 or payload[0].tag != f"{{{_WORD_NS}}}drawing":
        return None
    drawing = payload[0]

    def words(node):
        if _local_tag(node) in {"del", "moveFrom", "sdtPr", "sdtEndPr", "rPr", "pPr", "drawing", "object", "oMath", "oMathPara"}:
            return ""
        if node.tag == f"{{{_WORD_NS}}}t":
            return node.text or ""
        return "".join(words(child) for child in node)

    raw = "".join(words(child) for child in children[1:]).lstrip()
    heading = re.match(r"([1-9]\d{0,2})[.．、](?!\d)", raw)
    answer = re.match(r"【(?:答案|解析|详解)】", raw)
    if heading:
        stem = raw[heading.end():].lstrip()
        stem = re.sub(r"^[（(](?:(?:本(?:小)?题)?满分)?\d+(?:\.\d+)?分[）)][ \t]*", "", stem)
        if not re.match(r"如图|已知|设|在|若|函数|某", stem):
            return None
        number, field, token = int(heading.group(1)), "content", heading.group()
    elif answer:
        number, field, token = None, "answer_markdown", answer.group()
        heading = answer
    else:
        return None
    def uncertain(reason):return number, field, token, False, reason
    if paragraph.find("w:pPr/w:numPr", W_NS) is not None:
        return uncertain("automatic_numbering_also_present")
    if re.search(r"(?<!\d)[1-9]\d{0,2}[.．、](?!\d)", raw[heading.end():]):
        return uncertain("another_visible_numbered_boundary_in_paragraph")
    if answer and re.search(r"【(?:答案|解析|详解)】", raw[answer.end():]):
        return uncertain("multiple_answer_labels_in_paragraph")
    drawings = list(paragraph.iter(f"{{{_WORD_NS}}}drawing"))
    if len(drawings) != 1 or any(_local_tag(node) == "pict" for child in children[1:]
                                for node in child if _local_tag(child) != "object"):
        return uncertain("multiple_or_unknown_picture_carriers")
    forbidden = {"object", "OLEObject", "oMath", "oMathPara", "math", "txbxContent", "textbox", "txBody", "chart", "svgBlip", "sp", "cxnSp", "grpSp", "wsp", "wgp"}
    if any(_local_tag(node) in forbidden or _local_tag(node) == "t" and (node.text or "").strip()
           for node in drawing.iter()):
        return uncertain("picture_contains_nonraster_content")
    for node in drawing.iter():
        for key, value in node.attrib.items():
            if key.rsplit("}", 1)[-1] in {"descr", "description", "alt", "title"}:
                if re.search(r"上(?:一)?题|前(?:一)?题", value):
                    return uncertain("explicit_previous_question_description")
                if any(number is None or int(m.group(1)) != number for m in re.finditer(r"第\s*(\d{1,3})\s*题", value)):
                    return uncertain("conflicting_question_description")
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    anchors = [node for node in drawing if node.tag == f"{{{wp}}}anchor"]
    if len(anchors) != 1 or len(list(drawing.iter(f"{{{_DRAWING_NS}}}blip"))) != 1:
        return uncertain("not_one_complete_raster_anchor")
    anchor = anchors[0]
    if anchor.attrib.get("simplePos") != "0" or anchor.attrib.get("behindDoc") != "0":
        return uncertain("background_or_simple_position_anchor")
    horizontal = anchor.find(f"{{{wp}}}positionH")
    vertical = anchor.find(f"{{{wp}}}positionV")
    extent = anchor.find(f"{{{wp}}}extent")
    try:
        if horizontal is None or horizontal.attrib.get("relativeFrom") not in {"column", "margin"}:
            return uncertain("horizontal_reference_not_local_column_or_margin")
        if vertical is None or vertical.attrib.get("relativeFrom") != "paragraph":
            return uncertain("vertical_reference_not_paragraph")
        h_items, v_items = list(horizontal), list(vertical)
        if (len(h_items) != 1 or len(v_items) != 1
                or _local_tag(v_items[0]) != "posOffset" or extent is None):
            return uncertain("anchor_position_or_extent_incomplete")
        if _local_tag(h_items[0]) == "posOffset":
            x = int(h_items[0].text)
        elif (field == "answer_markdown" and _local_tag(h_items[0]) == "align"
                and h_items[0].text == "right"):
            x = 0
        else:
            return uncertain("anchor_position_or_extent_incomplete")
        y = int(v_items[0].text)
        width, height = int(extent.attrib["cx"]), int(extent.attrib["cy"])
        if min(x, y) < 0 or min(width, height) <= 0 or max(x, y, width, height) > 2_147_483_647 or y > height:
            return uncertain("negative_or_distant_paragraph_anchor")
    except (KeyError, TypeError, ValueError):
        return uncertain("anchor_position_or_extent_invalid")
    return number, field, token, True, "one_plain_picture_nonnegative_paragraph_anchor"


def _adjacent_question_image_ambiguities(root) -> dict:
    """Veto a possible cross-paragraph figure; never infer a replacement owner."""
    result = {}
    for container in root.iter():
        children = list(container)
        for index, current in enumerate(children[:-1]):
            following = children[index + 1]
            if current.tag != f"{{{_WORD_NS}}}p" or following.tag != f"{{{_WORD_NS}}}p":
                continue
            candidate = _question_heading_image_scope(current)
            if candidate is None or candidate[1] != "content":
                continue
            current_text = "".join(t.text or "" for t in current.iter(f"{{{_WORD_NS}}}t"))
            next_text = "".join(t.text or "" for t in following.iter(f"{{{_WORD_NS}}}t"))
            head = re.match(r"[ \t]*([1-9]\d{0,2})[.．、](?!\d)", next_text)
            if not head or int(head.group(1)) == candidate[0] or "图" in current_text:
                continue
            if not re.search(r"如(?:[上下左右])?图|(?:根据|参照|结合)图|图[^\n]{0,5}所示", next_text):
                continue
            has_picture = False
            for later in children[index + 1:]:
                if later.tag != f"{{{_WORD_NS}}}p":
                    break
                text = "".join(t.text or "" for t in later.iter(f"{{{_WORD_NS}}}t"))
                if later is not following and re.match(r"[ \t]*[1-9]\d{0,2}[.．、](?!\d)", text):
                    break
                if any(_local_tag(node) in {"drawing", "pict"} for node in later.iter()):
                    has_picture = True
                    break
            if not has_picture:
                result[id(current)] = int(head.group(1))
    return result


def _iter_word_container_children(container, child_tags):
    """Unwrap visible structured content at one logical Word nesting level.

    Descending only through content wrappers avoids treating nested tables as
    outer rows/cells, or reading SDT metadata/deleted revisions as visible text.
    AlternateContent has already selected one representation before parsing.
    """
    for child in container:
        if child.tag in child_tags:
            yield child
        elif child.tag == f"{{{_WORD_NS}}}sdt":
            content = child.find("w:sdtContent", W_NS)
            if content is not None:
                yield from _iter_word_container_children(content, child_tags)
        elif child.tag in {
            f"{{{_WORD_NS}}}customXml", f"{{{_WORD_NS}}}sdtContent",
            f"{{{_WORD_NS}}}ins", f"{{{_WORD_NS}}}moveTo",
        }:
            yield from _iter_word_container_children(child, child_tags)


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
    for row in _iter_word_container_children(table, {f"{{{_WORD_NS}}}tr"}):
        cells = []
        column_count = 0
        for cell in _iter_word_container_children(row, {f"{{{_WORD_NS}}}tc"}):
            cell_parts = []
            for child in _iter_word_container_children(cell, {
                f"{{{_WORD_NS}}}p", f"{{{_WORD_NS}}}tbl",
                f"{{{_MATH_NS}}}oMath", f"{{{_MATH_NS}}}oMathPara",
            }):
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
                else:
                    text = _parse_inline(
                        child, z, rels, output_dir, url_prefix, asset_prefix,
                        diagnostics, asset_cache, True,
                    )
                    if text:
                        cell_parts.append(text)

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
    if isinstance(elem.tag, str) and elem.tag.startswith(f"{{{MATHML_NS}}}"):
        return [_parse_embedded_mathml(elem, diagnostics).strip()]
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
    if tag in {"sectPr", "bookmarkStart", "bookmarkEnd", "del", "moveFrom", "sdtPr", "sdtEndPr", "customXmlPr"}:
        return []
    if elem.tag == f"{{{_WORD_NS}}}sdt":
        content = elem.find("w:sdtContent", W_NS)
        return _parse_block(
            content, z, rels, output_dir, url_prefix, asset_prefix,
            diagnostics, asset_cache,
        ) if content is not None else []
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
    include_source_asset_evidence: bool = False,
    include_source_review_evidence: bool = False,
) -> Dict[str, Any]:
    """Extract DOCX content and return Markdown plus a fidelity report."""
    diagnostics = _new_diagnostics()
    if include_source_review_evidence:
        diagnostics["_source_review_assets"] = []
    destination = Path(output_dir or UPLOADS_DIR)
    document_sha256 = None
    if include_source_review_evidence:
        import hashlib
        if isinstance(file_bytes_or_path, (str, os.PathLike)):
            source_path = Path(file_bytes_or_path)
            try:
                if source_path.stat().st_size <= MAX_ARCHIVE_UNCOMPRESSED:
                    file_bytes_or_path = source_path.read_bytes()
            except OSError:
                pass  # The normal extractor keeps its existing error response.
        if isinstance(file_bytes_or_path, (bytes, bytearray)):
            file_bytes_or_path = bytes(file_bytes_or_path)
            document_sha256 = hashlib.sha256(file_bytes_or_path).hexdigest()
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
            _select_compatible_alternatives(root, z, rels)
            diagnostics["_paragraph_image_ambiguities"] = _adjacent_question_image_ambiguities(root)
            body = root.find("w:body", W_NS)
            if body is None:
                raise ValueError("Word 文档结构无效：缺少 w:body")

            blocks = []
            review_journal = []
            review_capture_valid = False
            if include_source_review_evidence:
                from .docx_source_scopes import _review_snapshot
                try:
                    review_prelude = _review_snapshot(diagnostics)
                    review_capture_valid = True
                except Exception:
                    pass
            asset_cache: Dict[str, str] = {}
            for child_index, child in enumerate(body):
                if review_capture_valid:
                    try:
                        before_review = _review_snapshot(diagnostics)
                    except Exception:
                        review_capture_valid = False
                child_blocks = _parse_block(
                    child, z, rels, destination, url_prefix, asset_prefix,
                    diagnostics, asset_cache,
                )
                if review_capture_valid:
                    try:
                        review_journal.append({"body_child_index": child_index,
                            "output_block_indices": [len(blocks), len(blocks) + len(child_blocks)],
                            "blocks": child_blocks, "before": before_review,
                            "after": _review_snapshot(diagnostics),
                            "structural_risks": (["floating_table_anchor_ownership"] if any(
                                table.find("w:tblPr/w:tblpPr", W_NS) is not None
                                for table in child.iter(f"{{{_WORD_NS}}}tbl")) else [])})
                    except Exception:
                        review_capture_valid = False
                blocks.extend(child_blocks)

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
            diagnostics.pop("_paragraph_image_ambiguities", None)
            from .docx_source_assets import finalize_word_asset_evidence
            asset_evidence = finalize_word_asset_evidence(markdown, diagnostics)
            review_evidence = None
            review_assets = diagnostics.pop("_source_review_assets", [])
            if include_source_review_evidence and review_capture_valid:
                from .docx_source_scopes import _finalize_source_review_evidence
                def normalize_review_piece(value):
                    return normalize_subquestions_double_newlines(re.sub(r"\n{3,}", "\n\n", value))
                try:
                    review_evidence = _finalize_source_review_evidence(markdown, diagnostics,
                        document_sha256, review_prelude, review_journal, normalize_review_piece, review_assets)
                except Exception:
                    pass  # Missing proof falls back without changing source/diagnostics.
            return {
                "success": True,
                "markdown": markdown,
                "error": None,
                "image_count": diagnostics["images_extracted"],
                "image_paths": list(diagnostics.get("asset_paths", [])),
                "diagnostics": diagnostics,
                **({"_source_asset_evidence": asset_evidence} if include_source_asset_evidence else {}),
                **({"_source_review_evidence": review_evidence} if include_source_review_evidence else {}),
            }
    except Exception as exc:
        diagnostics.pop("_numbering", None)
        diagnostics.pop("_paragraph_image_ambiguities", None)
        diagnostics.pop("_empty_rasters", None)
        diagnostics.pop("_nonempty_image_contexts", None)
        diagnostics.pop("_source_review_assets", None)
        return {
            "success": False,
            "markdown": "",
            "error": f"Word 文档解析失败：{exc}",
            "image_count": 0,
            "image_paths": list(diagnostics.get("asset_paths", [])),
            "diagnostics": diagnostics,
        }
