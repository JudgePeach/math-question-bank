# mathbank/omml_helper.py
# -*- coding: utf-8 -*-
"""
OMML (Office Math Markup Language) to LaTeX converter.
Supports fractions, roots, super/subscripts, delimiters, n-ary operators,
matrices, equations, and mathematical accents without any heavy external dependencies.
"""

import xml.etree.ElementTree as ET
from typing import Optional

# OMML XML Namespaces
NS = {
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
}

# Common mathematical symbol translations from OMML text
SYMBOL_MAP = {
    '±': r'\pm ',
    '×': r'\times ',
    '÷': r'\div ',
    '≠': r'\neq ',
    '≤': r'\le ',
    '≥': r'\ge ',
    '≈': r'\approx ',
    '≡': r'\equiv ',
    '∈': r'\in ',
    '∉': r'\notin ',
    '⊂': r'\subset ',
    '⊆': r'\subseteq ',
    '∪': r'\cup ',
    '∩': r'\cap ',
    '∅': r'\varnothing ',
    '∞': r'\infty ',
    '°': r'^\circ ',
    '⊥': r'\perp ',
    '∥': r'\parallel ',
    '∠': r'\angle ',
    '△': r'\triangle ',
    '⊙': r'\odot ',
    'α': r'\alpha ',
    'β': r'\beta ',
    'γ': r'\gamma ',
    'δ': r'\delta ',
    'ε': r'\varepsilon ',
    'θ': r'\theta ',
    'λ': r'\lambda ',
    'μ': r'\mu ',
    'π': r'\pi ',
    'ρ': r'\rho ',
    'σ': r'\sigma ',
    'τ': r'\tau ',
    'φ': r'\varphi ',
    'ω': r'\omega ',
    'Δ': r'\Delta ',
    'Ω': r'\Omega ',
    '∑': r'\sum ',
    '∏': r'\prod ',
    '∫': r'\int ',
    '→': r'\to ',
    '⇒': r'\Rightarrow ',
    '⇔': r'\Leftrightarrow ',
}


def _clean_text(text: str) -> str:
    """Translate math unicode characters to LaTeX equivalents."""
    if not text:
        return ""
    res = []
    for ch in text:
        if ch in SYMBOL_MAP:
            res.append(SYMBOL_MAP[ch])
        else:
            res.append(ch)
    return "".join(res)


def _tag_name(elem) -> str:
    """Extract local tag name without namespace."""
    if elem is None:
        return ""
    tag = elem.tag
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def omml_element_to_latex(elem) -> str:
    """Recursively convert an OMML XML element into a LaTeX string."""
    if elem is None:
        return ""

    tag = _tag_name(elem)

    # 1. Math Root or Container (<m:oMath>, <m:oMathPara>, <m:e>)
    if tag in ('oMath', 'oMathPara', 'e', 'sub', 'sup', 'deg', 'num', 'den', 'fName'):
        parts = []
        for child in elem:
            parts.append(omml_element_to_latex(child))
        return "".join(parts)

    # 2. Text Run (<m:r>)
    if tag == 'r':
        text_parts = []
        for child in elem:
            c_tag = _tag_name(child)
            if c_tag == 't':
                text_parts.append(_clean_text(child.text or ""))
        return "".join(text_parts)

    # 3. Fraction (<m:f>) -> \frac{num}{den} or \dfrac{num}{den}
    if tag == 'f':
        num_elem = elem.find('m:num', NS)
        den_elem = elem.find('m:den', NS)
        num_str = omml_element_to_latex(num_elem).strip() if num_elem is not None else ""
        den_str = omml_element_to_latex(den_elem).strip() if den_elem is not None else ""
        return f"\\dfrac{{{num_str}}}{{{den_str}}}"

    # 4. Radical / Square Root (<m:rad>) -> \sqrt[deg]{base}
    if tag == 'rad':
        deg_elem = elem.find('m:deg', NS)
        base_elem = elem.find('m:e', NS)
        deg_str = omml_element_to_latex(deg_elem).strip() if deg_elem is not None else ""
        base_str = omml_element_to_latex(base_elem).strip() if base_elem is not None else ""
        if deg_str:
            return f"\\sqrt[{deg_str}]{{{base_str}}}"
        return f"\\sqrt{{{base_str}}}"

    # 5. Superscript (<m:sSup>) -> {base}^{sup}
    if tag == 'sSup':
        base_elem = elem.find('m:e', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = omml_element_to_latex(base_elem).strip() if base_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem).strip() if sup_elem is not None else ""
        return f"{{{base_str}}}^{{{sup_str}}}"

    # 6. Subscript (<m:sSub>) -> {base}_{sub}
    if tag == 'sSub':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        base_str = omml_element_to_latex(base_elem).strip() if base_elem is not None else ""
        sub_str = omml_element_to_latex(sub_elem).strip() if sub_elem is not None else ""
        return f"{{{base_str}}}_{{{sub_str}}}"

    # 7. Sub-Superscript (<m:sSubSup>) -> {base}_{sub}^{sup}
    if tag == 'sSubSup':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = omml_element_to_latex(base_elem).strip() if base_elem is not None else ""
        sub_str = omml_element_to_latex(sub_elem).strip() if sub_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem).strip() if sup_elem is not None else ""
        return f"{{{base_str}}}_{{{sub_str}}}^{{{sup_str}}}"

    # 8. Delimiters / Parentheses (<m:d>) -> \left( ... \right)
    if tag == 'd':
        d_pr = elem.find('m:dPr', NS)
        beg_chr = "("
        end_chr = ")"
        if d_pr is not None:
            beg_elem = d_pr.find('m:begChr', NS)
            end_elem = d_pr.find('m:endChr', NS)
            if beg_elem is not None:
                beg_chr = beg_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', beg_elem.attrib.get('val', '('))
            if end_elem is not None:
                end_chr = end_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', end_elem.attrib.get('val', ')'))

        inner_parts = []
        for child in elem:
            if _tag_name(child) == 'e':
                inner_parts.append(omml_element_to_latex(child))
        inner_str = "".join(inner_parts).strip()

        delim_map_l = {"(": r"\left(", "[": r"\left[", "{": r"\left\{", "|": r"\left|"}
        delim_map_r = {")": r"\right)", "]": r"\right]", "}": r"\right\}", "|": r"\right|"}
        left_delim = delim_map_l.get(beg_chr, r"\left.")
        right_delim = delim_map_r.get(end_chr, r"\right.")
        return f"{left_delim}{inner_str}{right_delim}"

    # 9. N-ary Operators (<m:nary>) -> \sum_{sub}^{sup} or \int_{sub}^{sup}
    if tag == 'nary':
        nary_pr = elem.find('m:naryPr', NS)
        chr_val = "∫"
        if nary_pr is not None:
            chr_elem = nary_pr.find('m:chr', NS)
            if chr_elem is not None:
                chr_val = chr_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', chr_elem.attrib.get('val', '∫'))

        sub_elem = elem.find('m:sub', NS)
        sup_elem = elem.find('m:sup', NS)
        base_elem = elem.find('m:e', NS)

        sub_str = omml_element_to_latex(sub_elem).strip() if sub_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem).strip() if sup_elem is not None else ""
        base_str = omml_element_to_latex(base_elem).strip() if base_elem is not None else ""

        op_latex = r"\int" if chr_val in ("∫", "integral") else (r"\sum" if chr_val in ("∑", "sum") else (r"\prod" if chr_val in ("∏", "prod") else chr_val))
        res = op_latex
        if sub_str:
            res += f"_{{{sub_str}}}"
        if sup_str:
            res += f"^{{{sup_str}}}"
        res += f" {base_str}"
        return res

    # 10. Accent / Vector (<m:acc>) -> \vec{base} or \bar{base} or \hat{base}
    if tag == 'acc':
        acc_pr = elem.find('m:accPr', NS)
        chr_val = "→"
        if acc_pr is not None:
            chr_elem = acc_pr.find('m:chr', NS)
            if chr_elem is not None:
                chr_val = chr_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', chr_elem.attrib.get('val', '→'))

        base_elem = elem.find('m:e', NS)
        base_str = omml_element_to_latex(base_elem).strip() if base_elem is not None else ""

        if chr_val in ("→", "⃗"):
            return f"\\vec{{{base_str}}}"
        elif chr_val in ("¯", "-"):
            return f"\\overline{{{base_str}}}"
        elif chr_val in ("^", "̂"):
            return f"\\hat{{{base_str}}}"
        return f"\\dot{{{base_str}}}"

    # 11. Matrix (<m:m>) -> \begin{matrix} ... \end{matrix}
    if tag == 'm':
        rows = []
        for mr in elem.findall('m:mr', NS):
            cols = []
            for e in mr.findall('m:e', NS):
                cols.append(omml_element_to_latex(e).strip())
            rows.append(" & ".join(cols))
        matrix_body = " \\\\ ".join(rows)
        return f"\\begin{{matrix}} {matrix_body} \\end{{matrix}}"

    # 12. Equation Array (<m:eqArr>) -> \begin{cases} ... \end{cases}
    if tag == 'eqArr':
        rows = []
        for e in elem.findall('m:e', NS):
            rows.append(omml_element_to_latex(e).strip())
        cases_body = " \\\\ ".join(rows)
        return f"\\begin{{cases}} {cases_body} \\end{{cases}}"

    # Fallback: traverse all child elements
    parts = []
    for child in elem:
        parts.append(omml_element_to_latex(child))
    return "".join(parts)


def omml_to_latex(omml_xml_or_elem) -> str:
    """Convert an OMML XML string or Element into a clean LaTeX math formula."""
    if omml_xml_or_elem is None:
        return ""

    if isinstance(omml_xml_or_elem, str):
        try:
            elem = ET.fromstring(omml_xml_or_elem)
        except Exception:
            return ""
    else:
        elem = omml_xml_or_elem

    raw_latex = omml_element_to_latex(elem).strip()
    raw_latex = " ".join(raw_latex.split())
    return raw_latex
