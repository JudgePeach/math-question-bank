# mathbank/omml_helper.py
# -*- coding: utf-8 -*-
"""
OMML (Office Math Markup Language) to LaTeX converter.
Supports fractions, roots, super/subscripts, delimiters, n-ary operators,
matrices, equations, and mathematical accents without any heavy external dependencies.
"""

import re
import xml.etree.ElementTree as ET
from typing import Optional, MutableMapping

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
    '−': '-',
    '⋅': r'\cdot ',
    '·': r'\cdot ',
    '∝': r'\propto ',
    '∀': r'\forall ',
    '∃': r'\exists ',
    '∧': r'\land ',
    '∨': r'\lor ',
    '∂': r'\partial ',
    '∇': r'\nabla ',
    '<': r'\lt ',
    '>': r'\gt ',
}

# Some Word/Office equation producers serialize a visible Cambria Math glyph
# as a Private Use Area code point instead of its Unicode semantic character.
# Keep this map evidence-based: every entry must come from a real DOCX sample
# whose visual source unambiguously identifies the glyph.
WORD_MATH_PRIVATE_USE_MAP = {
    '\uec07': '|',
    '\uec08': '|',
}

# Word linear-equation text occasionally omits the delimiter after a LaTeX
# control word (for example \capB instead of \cap B). Split only verified
# zero-argument math commands and only when the suffix looks like a variable,
# so ordinary commands such as \caption and \infty remain untouched.
WORD_LINEAR_FUNCTION_COMMANDS = {
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
    "log", "ln", "lg", "lim", "max", "min", "sup", "inf",
    "exp", "det", "gcd",
}
WORD_LINEAR_BOUNDARY_COMMANDS = {
    "cap", "cup", "in", "notin", "subset", "subseteq", "supset", "supseteq",
    "setminus", "varnothing", "le", "ge", "lt", "gt", "neq", "approx", "equiv",
    "parallel", "perp", "land", "lor", "to", "Rightarrow", "Leftrightarrow",
    "times", "cdot", "pm", "div", "sum", "prod", "int", "bigcup", "bigcap",
    "forall", "exists", "partial", "nabla", "propto", "angle", "triangle", "odot",
    "alpha", "beta", "gamma", "delta", "varepsilon", "varphi", "phi", "theta",
    "lambda", "mu", "pi", "rho", "sigma", "tau", "omega", "Delta", "Omega",
} | WORD_LINEAR_FUNCTION_COMMANDS
_WORD_LINEAR_BOUNDARY_PREFIXES = tuple(sorted(
    WORD_LINEAR_BOUNDARY_COMMANDS, key=len, reverse=True
))


def _is_private_use_character(ch: str) -> bool:
    code = ord(ch)
    return (
        0xE000 <= code <= 0xF8FF
        or 0xF0000 <= code <= 0xFFFFD
        or 0x100000 <= code <= 0x10FFFD
    )


def normalize_known_word_math_private_characters(text: str) -> str:
    """Replace only verified Word math PUA glyphs; never guess unknown glyphs."""
    return "".join(WORD_MATH_PRIVATE_USE_MAP.get(ch, ch) for ch in (text or ""))


def find_unknown_word_math_private_characters(text: str) -> list[str]:
    """Return stable U+XXXX labels for PUA glyphs that still lack a safe map."""
    return sorted({
        f"U+{ord(ch):04X}"
        for ch in (text or "")
        if _is_private_use_character(ch) and ch not in WORD_MATH_PRIVATE_USE_MAP
    })


def normalize_word_linear_latex_boundaries(
    text: str,
    diagnostics: Optional[MutableMapping] = None,
) -> str:
    r"""Repair verified greedy control words such as \capB safely."""
    repair_count = 0

    def replace_control_word(match):
        nonlocal repair_count
        word = match.group(1)
        if word in WORD_LINEAR_BOUNDARY_COMMANDS:
            return match.group(0)
        for command in _WORD_LINEAR_BOUNDARY_PREFIXES:
            if not word.startswith(command):
                continue
            suffix = word[len(command):]
            suffix_is_variable = bool(suffix) and (
                suffix[0].isupper()
                or (len(suffix) == 1 and suffix.isalpha())
            )
            if suffix_is_variable:
                repair_count += 1
                return f"\\{command} {suffix}"
        return match.group(0)

    normalized = re.sub(r"\\([A-Za-z]+)", replace_control_word, text or "")
    if diagnostics is not None and repair_count:
        diagnostics["control_word_boundaries_repaired"] = (
            diagnostics.get("control_word_boundaries_repaired", 0) + repair_count
        )
    return normalized


def normalize_word_formula_latex(
    text: str,
    diagnostics: Optional[MutableMapping] = None,
) -> str:
    """Normalize LaTeX emitted by any Word formula backend.

    OMML and MathType/MTEF are separate storage formats, but both may expose
    the same Office private glyphs or omit a control-word boundary. Keep the
    repair in one shared finalizer so a rule cannot affect only one path.
    """
    normalized_chars = []
    for char in text or "":
        if char in WORD_MATH_PRIVATE_USE_MAP:
            normalized_chars.append(WORD_MATH_PRIVATE_USE_MAP[char])
            if diagnostics is not None:
                diagnostics["private_use_symbols_converted"] = (
                    diagnostics.get("private_use_symbols_converted", 0) + 1
                )
        elif _is_private_use_character(char):
            normalized_chars.append(r"\text{?}")
            if diagnostics is not None:
                token = f"privateUse:U+{ord(char):04X}"
                unsupported = diagnostics.setdefault("unsupported_math_tokens", [])
                if token not in unsupported:
                    unsupported.append(token)
        else:
            normalized_chars.append(char)
    return normalize_word_linear_latex_boundaries(
        "".join(normalized_chars), diagnostics
    )


def _clean_text(text: str, diagnostics: Optional[MutableMapping] = None) -> str:
    """Translate math unicode characters to LaTeX equivalents."""
    if not text:
        return ""
    res = []
    for ch in text:
        if ch in WORD_MATH_PRIVATE_USE_MAP:
            res.append(WORD_MATH_PRIVATE_USE_MAP[ch])
            if diagnostics is not None:
                diagnostics["private_use_symbols_converted"] = (
                    diagnostics.get("private_use_symbols_converted", 0) + 1
                )
        elif _is_private_use_character(ch):
            _record_unsupported(diagnostics, f"privateUse:U+{ord(ch):04X}")
            # Never pass an unknown PUA glyph through to KaTeX/XeLaTeX.
            res.append(r"\text{?}")
        elif ch in SYMBOL_MAP:
            res.append(SYMBOL_MAP[ch])
        else:
            res.append(ch)
    return "".join(res)


def _clean_control_text(text: str, diagnostics: Optional[MutableMapping] = None) -> str:
    """Normalize delimiter/operator properties, dropping unknown PUA safely."""
    parts = []
    for ch in text or "":
        if ch in WORD_MATH_PRIVATE_USE_MAP:
            parts.append(WORD_MATH_PRIVATE_USE_MAP[ch])
            if diagnostics is not None:
                diagnostics["private_use_symbols_converted"] = (
                    diagnostics.get("private_use_symbols_converted", 0) + 1
                )
        elif _is_private_use_character(ch):
            _record_unsupported(diagnostics, f"privateUse:U+{ord(ch):04X}")
        else:
            parts.append(ch)
    return "".join(parts)


def _tag_name(elem) -> str:
    """Extract local tag name without namespace."""
    if elem is None:
        return ""
    tag = elem.tag
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _record_unsupported(diagnostics: Optional[MutableMapping], tag: str) -> None:
    if diagnostics is None or not tag or tag.endswith('Pr'):
        return
    unsupported = diagnostics.setdefault("unsupported_omml_tags", [])
    if tag not in unsupported:
        unsupported.append(tag)


def _function_latex(value: str) -> str:
    cleaned = value.strip()
    known = {"sin", "cos", "tan", "cot", "log", "ln", "lim", "max", "min", "exp"}
    if cleaned in known:
        return f"\\{cleaned}"
    return cleaned


def omml_element_to_latex(elem, diagnostics: Optional[MutableMapping] = None) -> str:
    """Recursively convert an OMML XML element into a LaTeX string."""
    if elem is None:
        return ""

    tag = _tag_name(elem)

    # 1. Math Root or Container (<m:oMath>, <m:oMathPara>, <m:e>)
    if tag in ('oMath', 'oMathPara', 'e', 'sub', 'sup', 'deg', 'num', 'den', 'fName', 'lim'):
        parts = []
        for child in elem:
            parts.append(omml_element_to_latex(child, diagnostics))
        return "".join(parts)

    # 2. Text Run (<m:r>)
    if tag == 'r':
        text_parts = []
        for child in elem:
            c_tag = _tag_name(child)
            if c_tag == 't':
                text_parts.append(_clean_text(child.text or "", diagnostics))
        return "".join(text_parts)

    # 3. Fraction (<m:f>) -> \frac{num}{den} or \dfrac{num}{den}
    if tag == 'f':
        num_elem = elem.find('m:num', NS)
        den_elem = elem.find('m:den', NS)
        num_str = omml_element_to_latex(num_elem, diagnostics).strip() if num_elem is not None else ""
        den_str = omml_element_to_latex(den_elem, diagnostics).strip() if den_elem is not None else ""
        return f"\\dfrac{{{num_str}}}{{{den_str}}}"

    # 4. Radical / Square Root (<m:rad>) -> \sqrt[deg]{base}
    if tag == 'rad':
        deg_elem = elem.find('m:deg', NS)
        base_elem = elem.find('m:e', NS)
        deg_str = omml_element_to_latex(deg_elem, diagnostics).strip() if deg_elem is not None else ""
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        if deg_str:
            return f"\\sqrt[{deg_str}]{{{base_str}}}"
        return f"\\sqrt{{{base_str}}}"

    # 5. Superscript (<m:sSup>) -> {base}^{sup}
    if tag == 'sSup':
        base_elem = elem.find('m:e', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        return f"{{{base_str}}}^{{{sup_str}}}"

    # 6. Subscript (<m:sSub>) -> {base}_{sub}
    if tag == 'sSub':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sub_str = omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        return f"{{{base_str}}}_{{{sub_str}}}"

    # 7. Sub-Superscript (<m:sSubSup>) -> {base}_{sub}^{sup}
    if tag == 'sSubSup':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sub_str = omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
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
        beg_chr = _clean_control_text(beg_chr, diagnostics)
        end_chr = _clean_control_text(end_chr, diagnostics)

        inner_parts = []
        for child in elem:
            if _tag_name(child) == 'e':
                inner_parts.append(omml_element_to_latex(child, diagnostics))
        sep_chr = "|"
        if d_pr is not None:
            sep_elem = d_pr.find('m:sepChr', NS)
            if sep_elem is not None:
                sep_chr = sep_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', sep_chr)
        sep_chr = _clean_control_text(sep_chr, diagnostics)
        separator = {"|": r"\middle|", ",": ",", ";": ";"}.get(
            sep_chr, _clean_text(sep_chr, diagnostics)
        )
        inner_str = separator.join(inner_parts).strip()

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

        sub_str = omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""

        op_latex = {
            "∫": r"\int", "integral": r"\int", "∑": r"\sum", "sum": r"\sum",
            "∏": r"\prod", "prod": r"\prod", "⋃": r"\bigcup", "⋂": r"\bigcap",
        }.get(chr_val, _clean_text(chr_val, diagnostics))
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
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""

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
                cols.append(omml_element_to_latex(e, diagnostics).strip())
            rows.append(" & ".join(cols))
        matrix_body = " \\\\ ".join(rows)
        return f"\\begin{{matrix}} {matrix_body} \\end{{matrix}}"

    # 12. Equation Array (<m:eqArr>) -> \begin{cases} ... \end{cases}
    if tag == 'eqArr':
        rows = []
        for e in elem.findall('m:e', NS):
            rows.append(omml_element_to_latex(e, diagnostics).strip())
        cases_body = " \\\\ ".join(rows)
        return f"\\begin{{cases}} {cases_body} \\end{{cases}}"

    # 13. Limit structures (<m:limLow>/<m:limUpp>)
    if tag in ('limLow', 'limUpp'):
        base_elem = elem.find('m:e', NS)
        lim_elem = elem.find('m:lim', NS)
        base_str = _function_latex(omml_element_to_latex(base_elem, diagnostics).strip()) if base_elem is not None else ""
        lim_str = omml_element_to_latex(lim_elem, diagnostics).strip() if lim_elem is not None else ""
        operator = "_" if tag == 'limLow' else "^"
        return f"{base_str}{operator}{{{lim_str}}}"

    # 14. Overline/underline (<m:bar>)
    if tag == 'bar':
        base_elem = elem.find('m:e', NS)
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        bar_pr = elem.find('m:barPr', NS)
        position = "top"
        if bar_pr is not None:
            pos_elem = bar_pr.find('m:pos', NS)
            if pos_elem is not None:
                position = pos_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', "top")
        command = r"\underline" if position == "bot" else r"\overline"
        return f"{command}{{{base_str}}}"

    # 15. Function application (<m:func>)
    if tag == 'func':
        fn_elem = elem.find('m:fName', NS)
        arg_elem = elem.find('m:e', NS)
        fn_str = _function_latex(omml_element_to_latex(fn_elem, diagnostics).strip()) if fn_elem is not None else ""
        arg_str = omml_element_to_latex(arg_elem, diagnostics).strip() if arg_elem is not None else ""
        return f"{fn_str} {arg_str}".strip()

    # 16. Prescripts, used for tensors and isotopes (<m:sPre>)
    if tag == 'sPre':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sub_str = omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        sup_str = omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        return f"{{}}_{{{sub_str}}}^{{{sup_str}}}{{{base_str}}}"

    # 17. Group characters such as over/under braces (<m:groupChr>)
    if tag == 'groupChr':
        base_elem = elem.find('m:e', NS)
        base_str = omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        group_pr = elem.find('m:groupChrPr', NS)
        char_value = "⏞"
        position = "top"
        if group_pr is not None:
            chr_elem = group_pr.find('m:chr', NS)
            pos_elem = group_pr.find('m:pos', NS)
            if chr_elem is not None:
                char_value = chr_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', char_value)
            if pos_elem is not None:
                position = pos_elem.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/math}val', position)
        if char_value in ('⏟', '︸') or position == 'bot':
            return f"\\underbrace{{{base_str}}}"
        if char_value in ('⏞', '︷'):
            return f"\\overbrace{{{base_str}}}"
        _record_unsupported(diagnostics, f"groupChr:{char_value}")
        return base_str

    # Style-only wrappers retain their mathematical content.
    if tag in ('box', 'borderBox', 'phant'):
        base_elem = elem.find('m:e', NS)
        return omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""

    # Fallback: traverse all child elements
    _record_unsupported(diagnostics, tag)
    parts = []
    for child in elem:
        parts.append(omml_element_to_latex(child, diagnostics))
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

    raw_latex = normalize_word_formula_latex(
        omml_element_to_latex(elem).strip()
    )
    raw_latex = " ".join(raw_latex.split())
    return raw_latex
