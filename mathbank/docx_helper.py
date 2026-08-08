# mathbank/docx_helper.py
# -*- coding: utf-8 -*-
"""
High-fidelity Word (.docx) exam parser and extractor.
Directly parses OpenXML zip package to extract text, tables, OMML formulas (to LaTeX),
and media illustrations with zero heavy C-extension dependencies.
"""

import os
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional
from mathbank.paths import UPLOADS_DIR
from mathbank.omml_helper import omml_element_to_latex, NS as OMML_NS

from mathbank.mtef_helper import mtef_to_latex

# OpenXML Namespaces for WordprocessingML, DrawingML, VML and OLE
W_NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
    'v': 'urn:schemas-microsoft-com:vml',
    'o': 'urn:schemas-microsoft-com:office:office',
}


def _extract_rels(z: zipfile.ZipFile) -> Dict[str, str]:
    """Extract document relationship mappings (e.g. rId5 -> media/image1.png)."""
    rels = {}
    rel_path = 'word/_rels/document.xml.rels'
    if rel_path in z.namelist():
        try:
            rel_xml = z.read(rel_path)
            root = ET.fromstring(rel_xml)
            for rel in root:
                r_id = rel.attrib.get('Id')
                target = rel.attrib.get('Target')
                if r_id and target:
                    rels[r_id] = target
        except Exception:
            pass
    return rels


def _save_image(z: zipfile.ZipFile, target_rel_path: str) -> Optional[str]:
    """Extract an embedded image from zip and save to static/uploads/."""
    clean_target = target_rel_path.lstrip('/')
    if not clean_target.startswith('word/'):
        clean_target = 'word/' + clean_target.lstrip('./')

    if clean_target not in z.namelist():
        base_name = os.path.basename(target_rel_path)
        for name in z.namelist():
            if name.endswith('/' + base_name) or name == base_name:
                clean_target = name
                break

    if clean_target in z.namelist():
        try:
            img_bytes = z.read(clean_target)
            ext = os.path.splitext(clean_target)[1].lower()
            if not ext:
                ext = '.png'
            filename = f"word_img_{uuid.uuid4().hex[:12]}{ext}"
            out_path = os.path.join(UPLOADS_DIR, filename)
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            return f"/static/uploads/{filename}"
        except Exception:
            pass
    return None


def _extract_ole_formula_or_image(elem, z: zipfile.ZipFile, rels: Dict[str, str]) -> Optional[str]:
    """Extract MathType formula from OLEObject via MTEF or fallback to embedded shape image."""
    # 1. Search for <o:OLEObject r:id="rId..."/>
    for ole_obj in elem.iter('{urn:schemas-microsoft-com:office:office}OLEObject'):
        r_id = ole_obj.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        if r_id and r_id in rels:
            target = rels[r_id].lstrip('/')
            if not target.startswith('word/'):
                target = 'word/' + target.lstrip('./')
            if target in z.namelist():
                try:
                    ole_bytes = z.read(target)
                    latex = mtef_to_latex(ole_bytes).strip()
                    if latex:
                        return f" ${latex}$ "
                except Exception:
                    pass

    # 2. Fallback to embedded preview image if MTEF not decoded
    img_url = _find_and_extract_image(elem, z, rels)
    if img_url:
        return f"\n![]({img_url})\n"

    return None


def _parse_paragraph(p_elem, z: zipfile.ZipFile, rels: Dict[str, str]) -> str:
    """Parse a paragraph element <w:p> into mixed text, LaTeX formulas and images."""
    parts = []
    
    for child in p_elem:
        tag = child.tag
        local_tag = tag.split('}', 1)[1] if '}' in tag else tag

        # 1. Text Run (<w:r>)
        if local_tag == 'r':
            for r_child in child:
                rc_tag = r_child.tag.split('}', 1)[1] if '}' in r_child.tag else r_child.tag
                if rc_tag == 't':
                    parts.append(r_child.text or "")
                elif rc_tag == 'tab':
                    parts.append("    ")
                elif rc_tag == 'br':
                    parts.append("\n")
                elif rc_tag in ('drawing', 'pict'):
                    img_url = _find_and_extract_image(r_child, z, rels)
                    if img_url:
                        parts.append(f"\n![]({img_url})\n")
                elif rc_tag == 'object':
                    formula_or_img = _extract_ole_formula_or_image(r_child, z, rels)
                    if formula_or_img:
                        parts.append(formula_or_img)

        # 2. Native Math Formula (<m:oMath> or <m:oMathPara>)
        elif local_tag in ('oMath', 'oMathPara'):
            latex_formula = omml_element_to_latex(child).strip()
            if latex_formula:
                parts.append(f" ${latex_formula}$ ")

        # 3. Direct Drawing or OLE Object in Paragraph
        elif local_tag in ('drawing', 'pict'):
            img_url = _find_and_extract_image(child, z, rels)
            if img_url:
                parts.append(f"\n![]({img_url})\n")
        elif local_tag == 'object':
            formula_or_img = _extract_ole_formula_or_image(child, z, rels)
            if formula_or_img:
                parts.append(formula_or_img)

    return "".join(parts).strip()


def _find_and_extract_image(elem, z: zipfile.ZipFile, rels: Dict[str, str]) -> Optional[str]:
    """Find blip or imagedata references in a drawing element and save it."""
    # Check DrawingML <a:blip r:embed="rId5"/>
    for blip in elem.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}blip'):
        embed_id = blip.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
        if embed_id and embed_id in rels:
            return _save_image(z, rels[embed_id])

    # Check VML <v:imagedata r:id="rId5"/>
    for v_img in elem.iter('{urn:schemas-microsoft-com:vml}imagedata'):
        embed_id = v_img.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        if embed_id and embed_id in rels:
            return _save_image(z, rels[embed_id])

    return None


def _parse_table(tbl_elem, z: zipfile.ZipFile, rels: Dict[str, str]) -> str:
    """Convert <w:tbl> to a clean LaTeX tabular environment."""
    rows = []
    max_cols = 1
    
    for tr in tbl_elem.findall('w:tr', W_NS):
        row_cells = []
        for tc in tr.findall('w:tc', W_NS):
            cell_text_parts = []
            for p in tc.findall('w:p', W_NS):
                p_text = _parse_paragraph(p, z, rels)
                if p_text:
                    cell_text_parts.append(p_text)
            cell_str = " ".join(cell_text_parts).strip()
            row_cells.append(cell_str or " ")
        if row_cells:
            max_cols = max(max_cols, len(row_cells))
            rows.append(row_cells)

    if not rows:
        return ""

    col_spec = "|" + "c|" * max_cols
    lines = [f"\\begin{{tabular}}{{{col_spec}}}", "\\hline"]
    for row in rows:
        # Pad row to max_cols if needed
        while len(row) < max_cols:
            row.append(" ")
        lines.append(" & ".join(row) + " \\\\")
        lines.append("\\hline")
    lines.append("\\end{tabular}")
    return "\n" + "\n".join(lines) + "\n"


def extract_docx_markdown(file_bytes_or_path) -> Dict[str, Any]:
    """
    Extract a .docx Word exam file into Markdown with LaTeX formulas, tables, and images.
    Returns:
    {
        "success": bool,
        "markdown": str,
        "error": Optional[str],
        "image_count": int
    }
    """
    import io
    
    if isinstance(file_bytes_or_path, str):
        file_obj = file_bytes_or_path
    else:
        file_obj = io.BytesIO(file_bytes_or_path)

    try:
        with zipfile.ZipFile(file_obj, 'r') as z:
            if 'word/document.xml' not in z.namelist():
                return {
                    "success": False,
                    "markdown": "",
                    "error": "Not a valid .docx file: word/document.xml missing",
                    "image_count": 0
                }

            rels = _extract_rels(z)
            doc_xml = z.read('word/document.xml')
            root = ET.fromstring(doc_xml)
            
            body = root.find('w:body', W_NS)
            if body is None:
                return {
                    "success": False,
                    "markdown": "",
                    "error": "Invalid document structure: w:body not found",
                    "image_count": 0
                }

            markdown_blocks = []
            img_count = 0

            for child in body:
                tag = child.tag.split('}', 1)[1] if '}' in child.tag else child.tag
                if tag == 'p':
                    p_str = _parse_paragraph(child, z, rels)
                    if p_str:
                        markdown_blocks.append(p_str)
                        img_count += len(re.findall(r'!\[.*?\]\(.*?\)', p_str))
                elif tag == 'tbl':
                    tbl_str = _parse_table(child, z, rels)
                    if tbl_str:
                        markdown_blocks.append(tbl_str)

            final_markdown = "\n\n".join(markdown_blocks).strip()
            
            # Post-cleanup: normalize multiple newlines and clean redundant spaces
            final_markdown = re.sub(r'\n{3,}', '\n\n', final_markdown)
            
            return {
                "success": True,
                "markdown": final_markdown,
                "error": None,
                "image_count": img_count
            }
    except Exception as ex:
        return {
            "success": False,
            "markdown": "",
            "error": f"Failed to parse Word document: {str(ex)}",
            "image_count": 0
        }
