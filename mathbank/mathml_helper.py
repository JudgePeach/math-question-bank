"""Bounded, conservative Presentation MathML extraction for embedded Word XML.

This is deliberately a small structural reader, not a general MathML renderer.
An unsupported construct rejects the whole formula so its original XML can be
retained for review by the Word extractor, instead of flattening its operands.
"""

from __future__ import annotations

from mathbank.omml_helper import SYMBOL_MAP, normalize_word_formula_latex

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
MAX_MATHML_NODES = 4096
MAX_MATHML_DEPTH = 64
MAX_MATHML_CHARACTERS = 32768


class MathMLUnsupported(ValueError):
    """The complete MathML formula cannot be converted without guessing."""


def mathml_element_to_latex(element) -> str:
    """Convert a complete, explicitly supported Presentation MathML tree."""
    count = characters = 0

    def visit(node, depth=0):
        nonlocal count, characters
        count += 1
        characters += len(node.text or "") + len(node.tail or "")
        if count > MAX_MATHML_NODES or depth > MAX_MATHML_DEPTH or characters > MAX_MATHML_CHARACTERS:
            raise MathMLUnsupported("MathML 结构超过安全读取限额")
        if not isinstance(node.tag, str) or not node.tag.startswith("{" + MATHML_NS + "}"):
            raise MathMLUnsupported("MathML 含未知命名空间")
        tag = node.tag.rsplit("}", 1)[-1]
        allowed = {"math": {"display"}, "mi": {"mathvariant"}, "mn": set(),
                   "mo": set(), "mtext": set(), "mfrac": {"linethickness", "bevelled"}}
        if set(node.attrib) - allowed.get(tag, set()):
            raise MathMLUnsupported(f"MathML {tag} 含未支持的排版属性")
        children = list(node)
        if tag in {"mi", "mn", "mo", "mtext"}:
            if children:
                raise MathMLUnsupported(f"MathML {tag} 含嵌套内容")
            raw = node.text or ""
            if not raw.strip():
                raise MathMLUnsupported(f"MathML {tag} 缺少内容")
            if any(ord(ch) < 32 or ord(ch) in range(0x2061, 0x2065) for ch in raw):
                raise MathMLUnsupported("MathML 含尚未支持的控制或不可见运算符")
            if "\u22f0" in raw:
                raise MathMLUnsupported("MathML 含尚未支持统一渲染的符号 U+22F0")
            diag = {}
            normalized = normalize_word_formula_latex(raw, diag)
            if diag.get("unsupported_math_tokens"):
                raise MathMLUnsupported("MathML 含未知私用字符")
            if any(ch in normalized for ch in "\\^~"):
                # MathML token text is literal; a backslash is not a TeX command.
                raise MathMLUnsupported("MathML token 含字面反斜杠")
            escaped = "".join({"{": r"\{", "}": r"\}", "%": r"\%",
                "#": r"\#", "&": r"\&", "_": r"\_", "$": r"\$"}.get(ch, ch)
                for ch in normalized)
            if tag == "mtext":
                return r"\text{" + escaped + "}"
            value = "".join(SYMBOL_MAP.get(ch, ch) if ch not in "{}%#&_$" else
                            {"{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#",
                             "&": r"\&", "_": r"\_", "$": r"\$"}[ch]
                            for ch in normalized)
            if tag == "mi":
                variant = node.attrib.get("mathvariant", "italic" if len(raw) == 1 else "normal")
                command = {"normal": "mathrm", "italic": "mathit", "bold": "mathbf",
                    "double-struck": "mathbb", "script": "mathcal", "fraktur": "mathfrak",
                    "monospace": "mathtt", "sans-serif": "mathsf"}.get(variant)
                if command is None:
                    raise MathMLUnsupported("MathML 标识符字体样式尚未支持")
                if variant not in {"normal", "italic"} and not all(
                    "A" <= char <= "Z" or "a" <= char <= "z" or "0" <= char <= "9"
                    for char in normalized
                ):
                    raise MathMLUnsupported("MathML 非拉丁标识符的特殊字体样式尚未支持")
                return "\\" + command + "{" + value + "}"
            return value
        if (node.text or "").strip() or any((child.tail or "").strip() for child in children):
            raise MathMLUnsupported(f"MathML {tag} 含不在token中的文字")
        if tag in {"math", "mrow"}:
            if tag == "math" and node.attrib.get("display", "inline") not in {"inline", "block"}:
                raise MathMLUnsupported("MathML display 值无效")
            if not children:
                raise MathMLUnsupported("MathML 公式为空")
            return " ".join(visit(child, depth + 1) for child in children)
        arity = {"mfrac": 2, "msqrt": None, "mroot": 2, "msub": 2,
                 "msup": 2, "msubsup": 3}.get(tag, -1)
        if arity != -1:
            if not children or arity is not None and len(children) != arity:
                raise MathMLUnsupported(f"MathML {tag} 的操作数数量无效")
            values = [visit(child, depth + 1) for child in children]
            if tag == "mfrac":
                thickness = node.attrib.get("linethickness", "medium")
                bevelled = node.attrib.get("bevelled", "false")
                if thickness not in {"medium", "0", "0px", "0pt"} or bevelled not in {"true", "false"}:
                    raise MathMLUnsupported("MathML 分式排版尚未支持")
                if thickness != "medium" and bevelled == "true":
                    raise MathMLUnsupported("MathML 无横线斜分式尚未支持")
                if thickness != "medium":
                    # A barless MathML fraction has no implied parenthesis.
                    # \binom would introduce glyphs absent from the source.
                    return r"\genfrac{}{}{0pt}{}{ " + values[0] + " }{ " + values[1] + " }"
                command = "sfrac" if bevelled == "true" else "dfrac"
                return "\\" + command + "{" + values[0] + "}{" + values[1] + "}"
            if tag == "msqrt":
                return r"\sqrt{" + " ".join(values) + "}"
            if tag == "mroot":
                return r"\sqrt[" + values[1] + "]{" + values[0] + "}"
            base = "{" + values[0] + "}"
            return base + ("_{" + values[1] + "}" if tag != "msup" else "") + (
                "^{" + values[-1] + "}" if tag != "msub" else "")
        if tag == "mtable":
            if not children or any(child.tag != "{" + MATHML_NS + "}mtr" for child in children):
                raise MathMLUnsupported("MathML 矩阵行无效")
            widths = {len(row) for row in children}
            if len(widths) != 1 or 0 in widths:
                raise MathMLUnsupported("MathML 矩阵不是完整矩形")
            return r"\begin{matrix}" + r" \\ ".join(visit(row, depth + 1) for row in children) + r"\end{matrix}"
        if tag == "mtr":
            if not children or any(child.tag != "{" + MATHML_NS + "}mtd" for child in children):
                raise MathMLUnsupported("MathML 矩阵单元格无效")
            return " & ".join(visit(child, depth + 1) for child in children)
        if tag == "mtd":
            if not children:
                raise MathMLUnsupported("MathML 矩阵单元格为空")
            return " ".join(visit(child, depth + 1) for child in children)
        raise MathMLUnsupported(f"尚未支持的 MathML 结构：{tag}")

    if element.tag != "{" + MATHML_NS + "}math":
        raise MathMLUnsupported("MathML 缺少完整math根节点")
    return visit(element).strip()
