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
    '∁': r'\complement ', '∋': r'\ni ', '∓': r'\mp ',
    '∖': r'\setminus ', '∗': r'\ast ', '∘': r'\circ ',
    '∣': r'\mid ', '∤': r'\nmid ', '∦': r'\nparallel ',
    '∬': r'\iint ', '∭': r'\iiint ', '∮': r'\oint ',
    '⊄': r'\not\subset ', '⊅': r'\not\supset ',
    '⊈': r'\nsubseteq ', '⊉': r'\nsupseteq ',
    '⊕': r'\oplus ', '⊖': r'\ominus ', '⊗': r'\otimes ',
    '⊘': r'\oslash ', '⋮': r'\vdots ', '⋯': r'\cdots ', '⋱': r'\ddots ',
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
# An already complete standard command must win before prefix repair. For
# example, splitting \cdots into \cdot s changes the mathematical content.
# Include every Unicode command produced above plus structural/style macros
# emitted by both Word backends; this is separate from the narrow repair set.
_WORD_LINEAR_COMPLETE_COMMANDS = WORD_LINEAR_BOUNDARY_COMMANDS | {
    command for value in SYMBOL_MAP.values()
    for command in re.findall(r'\\([A-Za-z]+)', value)
} | {
    'left', 'right', 'middle', 'frac', 'dfrac', 'tfrac', 'cfrac', 'genfrac', 'sfrac',
    'sqrt', 'vec', 'overrightarrow', 'overleftarrow', 'overleftrightarrow',
    'hat', 'widehat', 'tilde', 'widetilde', 'overline', 'underline', 'dot', 'ddot',
    'acute', 'grave', 'breve', 'check', 'mathring', 'wideparen',
    'mathrm', 'mathit', 'mathbf', 'boldsymbol', 'mathbb', 'mathcal', 'mathfrak',
    'mathsf', 'mathtt', 'text', 'operatorname', 'phantom', 'hphantom', 'vphantom',
    'langle', 'rangle', 'Vert', 'lfloor', 'rfloor', 'lceil', 'rceil', 'backslash',
    'begin', 'end', 'overbrace', 'underbrace', 'overset', 'underset', 'centerdot',
    'Gamma', 'Theta', 'Xi', 'Pi', 'Sigma', 'Phi', 'Psi', 'epsilon', 'varpi',
}
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
        if word in _WORD_LINEAR_COMPLETE_COMMANDS:
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
            # Keep uncertainty in extraction diagnostics, not in the formula.
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
        elif ch == '\u22f0':
            # Ascending diagonal dots are not descending \ddots. The current
            # renderer has no proven compatible glyph, so retain review.
            _record_unsupported(diagnostics, 'symbol:U+22F0')
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
    upright = re.fullmatch(r'\\mathrm\{([A-Za-z]+)\}', cleaned)
    if upright and upright.group(1) in known:
        cleaned = upright.group(1)
    if cleaned in known:
        return f"\\{cleaned}"
    return cleaned


def _math_property(props, name: str, default: str) -> str:
    element = props.find(f'm:{name}', NS) if props is not None else None
    if element is None:
        return default
    return element.attrib.get(f"{{{NS['m']}}}val", element.attrib.get('val', default))


def _check_math_properties(props, known: set[str], diagnostics, context: str) -> None:
    if props is not None:
        for child in props:
            tag = _tag_name(child)
            if tag not in known | {'ctrlPr'}:
                _record_unsupported(diagnostics, f'{context}Property:{tag}:unsupported')


def _styled_math_run(elem, text: str, diagnostics) -> str:
    """Respect explicit OMML alphabet/style identities without remapping PUA."""
    props = elem.find('m:rPr', NS)
    if props is None or not text:
        return text
    _check_math_properties(props, {'scr', 'sty', 'nor', 'lit', 'aln', 'brk'}, diagnostics, 'run')
    script = _math_property(props, 'scr', 'roman')
    style = _math_property(props, 'sty', '')
    scripts = {'roman': '', 'double-struck': 'mathbb', 'fraktur': 'mathfrak',
               'script': 'mathcal', 'sans-serif': 'mathsf', 'monospace': 'mathtt'}
    styles = {'': '', 'i': 'mathit', 'p': 'mathrm', 'b': 'mathbf', 'bi': 'boldsymbol'}
    if script not in scripts:
        _record_unsupported(diagnostics, f'runScript:{script}')
        return text
    if style not in styles:
        _record_unsupported(diagnostics, f'runStyle:{style}')
        return text
    raw_text = ''.join(child.text or '' for child in elem if _tag_name(child) == 't')
    word_props = elem.find('w:rPr', NS)
    fonts = word_props.find('w:rFonts', NS) if word_props is not None else None
    font_values = ({key.rsplit('}', 1)[-1]: value for key, value in fonts.attrib.items()}
                   if fonts is not None else {})
    known_plain_fonts = {'Times New Roman', 'Cambria Math', 'STIX Two Math', 'Arial',
                         'Calibri', 'SimSun', 'SimHei', '宋体', '黑体'}
    plain_font = not font_values or all(value in known_plain_fonts for value in font_values.values())
    # Numerals and explicit Unicode operators are already upright in math.
    # Wrapping each digit in \mathrm changes round-trip source needlessly.
    operator_chars = {char for char in SYMBOL_MAP if not char.isalpha()} | set('+-=<>.,:;()[]|!')
    ordinary_upright = (script == 'roman' and style in {'', 'p'} and bool(raw_text)
                        and all(char.isdecimal() or char in operator_chars for char in raw_text)
                        and plain_font)

    def word_italic_enabled(name):
        node = word_props.find(f'w:{name}', NS) if word_props is not None else None
        return node is not None and node.attrib.get(f"{{{NS['w']}}}val", '1') in {'1', 'true', 'on'}

    # Existing native export uses nor to retain Times New Roman while the
    # explicit OMML and Word styles both prove a single italic variable.
    proved_italic_variable = (script == 'roman' and style == 'i' and len(raw_text) == 1
        and raw_text.isascii() and raw_text.isalpha()
        and {'ascii', 'hAnsi', 'cs'} <= set(font_values)
        and all(key in {'ascii', 'hAnsi', 'cs', 'eastAsia'} and value == 'Times New Roman'
                for key, value in font_values.items())
        and word_italic_enabled('i') and word_italic_enabled('iCs')
        and all(_tag_name(child) in {'rFonts', 'i', 'iCs'} for child in word_props))
    for name in ('nor', 'lit'):
        present = props.find(f'm:{name}', NS) is not None
        value = _math_property(props, name, '1') if present else '0'
        if value not in {'0', 'false', '1', 'true'}:
            _record_unsupported(diagnostics, f'runProperty:{name}={value}')
        elif value in {'1', 'true'} and not (name == 'nor' and (ordinary_upright or proved_italic_variable)):
            # A normal/literal text run may depend on its Word font and text
            # escaping. Do not certify it from mathematical style alone.
            _record_unsupported(diagnostics, f'runProperty:{name}')
    if ordinary_upright:
        return text
    if script != 'roman':
        if style not in {'', 'p'}:
            _record_unsupported(diagnostics, f'runScriptStyle:{script}/{style}')
        command = scripts[script]
    else:
        command = styles[style]
    return f'\\{command}{{{text}}}' if command else text


_OMML_ACCENTS = {
    '→': 'vec', '⃗': 'vec', '←': 'overleftarrow', '⃖': 'overleftarrow',
    '↔': 'overleftrightarrow', '⃡': 'overleftrightarrow',
    '^': 'hat', '̂': 'hat', 'ˆ': 'hat', '~': 'widetilde', '̃': 'widetilde', '˜': 'widetilde',
    '¯': 'overline', '-': 'overline', '̄': 'overline', '̅': 'overline',
    '.': 'dot', '˙': 'dot', '̇': 'dot', '¨': 'ddot', '̈': 'ddot',
    '´': 'acute', '́': 'acute', '`': 'grave', '̀': 'grave',
    'ˇ': 'check', '̌': 'check', '˘': 'breve', '̆': 'breve',
    '˚': 'mathring', '̊': 'mathring', '⏜': 'wideparen', '̑': 'wideparen',
}
_OMML_DELIMITERS = {
    '': '.', '(': '(', ')': ')', '[': '[', ']': ']', '{': r'\{', '}': r'\}',
    '|': '|', '‖': r'\Vert', '∥': r'\Vert',
    '⟨': r'\langle', '⟩': r'\rangle', '〈': r'\langle', '〉': r'\rangle',
    '⌊': r'\lfloor', '⌋': r'\rfloor', '⌈': r'\lceil', '⌉': r'\rceil',
    '/': '/', '\\': r'\backslash',
}


_LINEAR_TEX_BRACE_ARITY = {
    "frac": 2, "dfrac": 2, "tfrac": 2, "cfrac": 2, "binom": 2, "dbinom": 2,
    "sqrt": 1, "text": 1, "textbf": 1, "textit": 1, "textrm": 1, "texttt": 1,
    "mathrm": 1, "mathbf": 1, "mathit": 1, "mathsf": 1, "mathtt": 1,
    "mathcal": 1, "mathbb": 1, "boldsymbol": 1, "operatorname": 1,
    "vec": 1, "overrightarrow": 1, "overline": 1, "underline": 1, "widehat": 1,
    "hat": 1, "bar": 1, "tilde": 1, "widetilde": 1, "overset": 2, "underset": 2,
    "underbrace": 1, "overbrace": 1, "textcolor": 2, "begin": 1, "end": 1,
    "detokenize": 1, "url": 1, "path": 1,
}
_LINEAR_BRACE_MAX_CHARACTERS = 8192
_LINEAR_BRACE_MAX_TOKENS = 256


def _escaped_at(value: str, position: int) -> bool:
    count, cursor = 0, position - 1
    while cursor >= 0 and value[cursor] == "\\":
        count += 1
        cursor -= 1
    return bool(count % 2)


def _linear_group_end(value: str, start: int) -> int | None:
    if start >= len(value) or value[start] != "{":
        return None
    depth = 1
    for position in range(start + 1, len(value)):
        if _escaped_at(value, position):
            continue
        depth += (value[position] == "{") - (value[position] == "}")
        if depth == 0:
            return position + 1
    return None


def _unproven_linear_braces(value: str) -> bool:
    """Qualify explicit TeX arguments/scripts only; never infer set notation."""
    if "{" not in value and "}" not in value:
        return False
    if len(value) > _LINEAR_BRACE_MAX_CHARACTERS or value.count("{") + value.count("}") > _LINEAR_BRACE_MAX_TOKENS:
        # Linear-time escape scan only. Oversized unescaped groups have not
        # been proven safe, even when they resemble a known TeX command.
        slashes = 0
        for char in value:
            if char == "\\":
                slashes += 1
                continue
            if char in "{}" and not slashes % 2:
                return True
            slashes = 0
        return False
    protected = []
    for match in re.finditer(r"\\(?:verb|Verb|lstinline)\*?([^\w\s{])[^\n]*?\1", value):
        if not _escaped_at(value, match.start()):
            protected.append(match.span())
    for match in re.finditer(r"\\([A-Za-z]+)\*?|[_^]", value):
        if _escaped_at(value, match.start()):
            continue
        command = match.group(1)
        arity = _LINEAR_TEX_BRACE_ARITY.get(command, 0) if command else 1
        cursor = match.end()
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if command == "sqrt" and cursor < len(value) and value[cursor] == "[":
            end = value.find("]", cursor + 1)
            if end < 0:
                continue
            cursor = end + 1
        groups = []
        for _ in range(arity):
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
            end = _linear_group_end(value, cursor)
            if end is None:
                break
            groups.append((cursor, end))
            cursor = end
        if len(groups) == arity and arity:
            protected.extend(groups)
    return any(char in "{}" and not _escaped_at(value, position)
               and not any(start <= position < end for start, end in protected)
               for position, char in enumerate(value))


def _record_literal_brace_evidence(elem, diagnostics: Optional[MutableMapping]) -> None:
    if diagnostics is None or elem is None:
        return
    streams = []

    def walk(node):
        if _tag_name(node) == "r":
            streams.append("".join(child.text or "" for child in node if _tag_name(child) == "t"))
            return
        group = []
        for child in node:
            if _tag_name(child) == "r":
                group.extend(part.text or "" for part in child if _tag_name(part) == "t")
            else:
                if group:
                    streams.append("".join(group))
                    group = []
                walk(child)
        if group:
            streams.append("".join(group))

    walk(elem)
    count = sum(_unproven_linear_braces(text) for text in streams)
    if count:
        _record_unsupported(diagnostics, "literalVisibleBraces")
        diagnostics["literal_brace_review_count"] = diagnostics.get("literal_brace_review_count", 0) + count


def omml_element_to_latex(elem, diagnostics: Optional[MutableMapping] = None) -> str:
    """Keep output unchanged and diagnose ambiguous visible braces in text.

    Structured m:d delimiters and explicit linear TeX controls retain their
    prior behavior. A naked text brace cannot certify its visible semantics as
    a TeX grouping brace, so native import must preserve a review boundary.
    """
    _record_literal_brace_evidence(elem, diagnostics)
    return _convert_omml_element_to_latex(elem, diagnostics)


def _convert_omml_element_to_latex(elem, diagnostics: Optional[MutableMapping] = None) -> str:
    """Recursively convert an OMML XML element into a LaTeX string."""
    if elem is None:
        return ""

    tag = _tag_name(elem)

    # 1. Math Root or Container (<m:oMath>, <m:oMathPara>, <m:e>)
    if tag in ('oMath', 'oMathPara', 'e', 'sub', 'sup', 'deg', 'num', 'den', 'fName', 'lim'):
        parts = []
        for child in elem:
            parts.append(_convert_omml_element_to_latex(child, diagnostics))
        return "".join(parts)

    # 2. Text Run (<m:r>)
    if tag == 'r':
        text_parts = []
        for child in elem:
            c_tag = _tag_name(child)
            if c_tag == 't':
                text_parts.append(_clean_text(child.text or "", diagnostics))
        return _styled_math_run(elem, "".join(text_parts), diagnostics)

    # 3. Fraction (<m:f>) -> \frac{num}{den} or \dfrac{num}{den}
    if tag == 'f':
        num_elem = elem.find('m:num', NS)
        den_elem = elem.find('m:den', NS)
        num_str = _convert_omml_element_to_latex(num_elem, diagnostics).strip() if num_elem is not None else ""
        den_str = _convert_omml_element_to_latex(den_elem, diagnostics).strip() if den_elem is not None else ""
        props = elem.find('m:fPr', NS)
        _check_math_properties(props, {'type'}, diagnostics, 'fraction')
        fraction_type = _math_property(props, 'type', 'bar')
        if fraction_type == 'bar':
            return f"\\dfrac{{{num_str}}}{{{den_str}}}"
        if fraction_type == 'noBar':
            return f"\\genfrac{{}}{{}}{{0pt}}{{}}{{{num_str}}}{{{den_str}}}"
        if fraction_type == 'skw':
            return f"\\sfrac{{{num_str}}}{{{den_str}}}"
        if fraction_type == 'lin':
            # Group each operand: slash precedence must not turn a+b/c+d
            # into a different expression than the original fraction.
            return f"{{{num_str}}}/{{{den_str}}}"
        _record_unsupported(diagnostics, f'fractionType:{fraction_type}')
        return ''

    # 4. Radical / Square Root (<m:rad>) -> \sqrt[deg]{base}
    if tag == 'rad':
        deg_elem = elem.find('m:deg', NS)
        base_elem = elem.find('m:e', NS)
        deg_str = _convert_omml_element_to_latex(deg_elem, diagnostics).strip() if deg_elem is not None else ""
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        if deg_str:
            return f"\\sqrt[{deg_str}]{{{base_str}}}"
        return f"\\sqrt{{{base_str}}}"

    # 5. Superscript (<m:sSup>) -> {base}^{sup}
    if tag == 'sSup':
        base_elem = elem.find('m:e', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sup_str = _convert_omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        return f"{{{base_str}}}^{{{sup_str}}}"

    # 6. Subscript (<m:sSub>) -> {base}_{sub}
    if tag == 'sSub':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sub_str = _convert_omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        return f"{{{base_str}}}_{{{sub_str}}}"

    # 7. Sub-Superscript (<m:sSubSup>) -> {base}_{sub}^{sup}
    if tag == 'sSubSup':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sub_str = _convert_omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        sup_str = _convert_omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        return f"{{{base_str}}}_{{{sub_str}}}^{{{sup_str}}}"

    # 8. Delimiters / Parentheses (<m:d>) -> \left( ... \right)
    if tag == 'd':
        d_pr = elem.find('m:dPr', NS)
        _check_math_properties(d_pr, {'begChr', 'endChr', 'sepChr', 'grow', 'shp'}, diagnostics, 'delimiter')
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
                inner_parts.append(_convert_omml_element_to_latex(child, diagnostics))
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

        for side, char in (('begin', beg_chr), ('end', end_chr)):
            if char not in _OMML_DELIMITERS:
                _record_unsupported(diagnostics, f'delimiter:{side}={char}')
                return '{' + inner_str + '}'
        left_delim = r'\left' + _OMML_DELIMITERS[beg_chr]
        right_delim = r'\right' + _OMML_DELIMITERS[end_chr]
        if left_delim[-1:].isalpha():
            left_delim += ' '
        if right_delim[-1:].isalpha():
            right_delim += ' '
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

        sub_str = _convert_omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        sup_str = _convert_omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""

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
        _check_math_properties(acc_pr, {'chr'}, diagnostics, 'accent')
        # ISO/IEC 29500: omitted accPr/chr defaults to U+0302, not an arrow.
        chr_val = _math_property(acc_pr, 'chr', '\u0302')

        base_elem = elem.find('m:e', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""

        if chr_val in _OMML_ACCENTS:
            return f"\\{_OMML_ACCENTS[chr_val]}{{{base_str}}}"
        _record_unsupported(diagnostics, f'accent:{chr_val or "empty"}')
        return '{' + base_str + '}'

    # 11. Matrix (<m:m>) -> \begin{matrix} ... \end{matrix}
    if tag == 'm':
        rows = []
        for mr in elem.findall('m:mr', NS):
            cols = []
            for e in mr.findall('m:e', NS):
                cols.append(_convert_omml_element_to_latex(e, diagnostics).strip())
            rows.append(" & ".join(cols))
        matrix_body = " \\\\ ".join(rows)
        return f"\\begin{{matrix}} {matrix_body} \\end{{matrix}}"

    # 12. Equation Array (<m:eqArr>): only an outer m:d can add braces.
    if tag == 'eqArr':
        rows = []
        for e in elem.findall('m:e', NS):
            rows.append(_convert_omml_element_to_latex(e, diagnostics).strip())
        array_body = " \\\\ ".join(rows)
        # OMML alternates align/spacer ampersands. A multi-point array needs
        # a richer layout conversion than a simple aligned environment.
        if any(row.count('&') > 1 for row in rows):
            _record_unsupported(diagnostics, 'equationArray:multipleAlignmentPoints')
        _check_math_properties(elem.find('m:eqArrPr', NS),
                               {'baseJc', 'maxDist', 'objDist', 'rSp', 'rSpRule'},
                               diagnostics, 'equationArray')
        return f"\\begin{{aligned}} {array_body} \\end{{aligned}}"

    # 13. Limit structures (<m:limLow>/<m:limUpp>)
    if tag in ('limLow', 'limUpp'):
        base_elem = elem.find('m:e', NS)
        lim_elem = elem.find('m:lim', NS)
        base_str = _function_latex(_convert_omml_element_to_latex(base_elem, diagnostics).strip()) if base_elem is not None else ""
        lim_str = _convert_omml_element_to_latex(lim_elem, diagnostics).strip() if lim_elem is not None else ""
        operator = "_" if tag == 'limLow' else "^"
        return f"{base_str}{operator}{{{lim_str}}}"

    # 14. Overline/underline (<m:bar>)
    if tag == 'bar':
        base_elem = elem.find('m:e', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
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
        fn_str = _function_latex(_convert_omml_element_to_latex(fn_elem, diagnostics).strip()) if fn_elem is not None else ""
        arg_str = _convert_omml_element_to_latex(arg_elem, diagnostics).strip() if arg_elem is not None else ""
        return f"{fn_str} {arg_str}".strip()

    # 16. Prescripts, used for tensors and isotopes (<m:sPre>)
    if tag == 'sPre':
        base_elem = elem.find('m:e', NS)
        sub_elem = elem.find('m:sub', NS)
        sup_elem = elem.find('m:sup', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
        sub_str = _convert_omml_element_to_latex(sub_elem, diagnostics).strip() if sub_elem is not None else ""
        sup_str = _convert_omml_element_to_latex(sup_elem, diagnostics).strip() if sup_elem is not None else ""
        return f"{{}}_{{{sub_str}}}^{{{sup_str}}}{{{base_str}}}"

    # 17. Group characters such as over/under braces (<m:groupChr>)
    if tag == 'groupChr':
        base_elem = elem.find('m:e', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""
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

    if tag == 'phant':
        base_elem = elem.find('m:e', NS)
        base_str = _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ''
        props = elem.find('m:phantPr', NS)
        _check_math_properties(props, {'show', 'transp', 'zeroWid', 'zeroAsc', 'zeroDesc'}, diagnostics, 'phantom')
        flags = {}
        for name in ('show', 'transp', 'zeroWid', 'zeroAsc', 'zeroDesc'):
            node = props.find(f'm:{name}', NS) if props is not None else None
            default = '1' if name in {'show', 'transp'} else '0'
            value = _math_property(props, name, '1') if node is not None else default
            if value not in {'0', 'false', '1', 'true'}:
                _record_unsupported(diagnostics, f'phantomProperty:{name}={value}')
                return ''
            flags[name] = value in {'1', 'true'}
        zeros = (flags['zeroWid'], flags['zeroAsc'], flags['zeroDesc'])
        if flags['show']:
            if any(zeros):
                _record_unsupported(diagnostics, 'phantom:visibleZeroDimensions')
            return base_str
        if zeros == (True, True, True):
            # Keep a valid zero-size mathematical object. An empty return
            # would be mistaken for an undecodable equation by DOCX callers.
            return r'\phantom{}'
        command = {(False, False, False): 'phantom',
                   (True, False, False): 'vphantom',
                   (False, True, True): 'hphantom'}.get(zeros)
        if command is None:
            _record_unsupported(diagnostics, 'phantom:partialZeroDimensions')
            command = 'phantom'
        return f'\\{command}{{{base_str}}}'

    # Style-only wrappers retain their mathematical content.
    if tag in ('box', 'borderBox'):
        base_elem = elem.find('m:e', NS)
        return _convert_omml_element_to_latex(base_elem, diagnostics).strip() if base_elem is not None else ""

    # Fallback: traverse all child elements
    _record_unsupported(diagnostics, tag)
    parts = []
    for child in elem:
        parts.append(_convert_omml_element_to_latex(child, diagnostics))
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
