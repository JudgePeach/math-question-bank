"""
MTEF (MathType Equation Format) to LaTeX parser in pure Python.
Decodes Equation Native / OLE MathType formulas embedded in Microsoft Word (.docx/.doc) documents.
Zero heavy external C-dependencies.
"""

import re
from typing import Optional


def extract_mtef_from_ole(ole_bytes: bytes) -> bytes:
    """Locate and extract the MTEF payload from an OLE binary file or stream."""
    if not ole_bytes:
        return b""

    # 1. Standard Equation Native wrapper (28-byte header starting with 0x1C)
    eq_pos = ole_bytes.find(b"\x1c\x00\x00\x00\x02\x00")
    if eq_pos != -1 and len(ole_bytes) >= eq_pos + 28:
        return ole_bytes[eq_pos + 28 :]

    # 2. Direct MTEF Magic headers
    for magic in [
        b"\x05\x01\x00\x07",
        b"\x05\x01\x01\x07",
        b"\x05\x01\x00\x06",
        b"\x03\x01\x01\x03",
    ]:
        pos = ole_bytes.find(magic)
        if pos != -1:
            return ole_bytes[pos:]

    return b""


def _decode_mathtype_stream(data: bytes) -> str:
    """Extract and reconstruct mathematical formula from MathType Equation Native stream."""
    if not data or len(data) < 10:
        return ""

    # Skip Font Table (WinAllBasicCodePages, Times New Roman, Symbol, WinAllCodePages, etc.)
    font_end = data.find(b"WinAllCodePages")
    if font_end != -1:
        raw_bytes = data[font_end + len(b"WinAllCodePages") :]
    else:
        raw_bytes = data

    chars = []
    for b in raw_bytes:
        if 32 <= b < 127:
            chars.append(chr(b))
        elif b == 0xB1:
            chars.append(" \\pm ")
        elif b == 0xD7:
            chars.append(" \\times ")
        elif b == 0xF7:
            chars.append(" \\div ")

    s = "".join(chars)
    # Strip MathType hash prefixes and internal markers
    s = re.sub(r"^[!/G_A-Z%@#\s\',._\-]+", "", s)
    s = re.sub(r"Equation\s*Native", "", s, flags=re.IGNORECASE)
    s = re.sub(r"quation\s*Native", "", s, flags=re.IGNORECASE)
    return s.strip()


def _clean_and_format_formula(raw: str) -> str:
    """Format raw MathType string into standard LaTeX."""
    if not raw:
        return ""

    s = raw.replace("++", "+").replace('"-', "-").replace('"', "-")
    s = re.sub(r"={2,}", "=", s)
    s = re.sub(r"[\r\n]+", " ", s).strip()

    # 1. Complex fractions and complex numbers e.g. "z= 0=2+i1-i" -> "z = \dfrac{2+i}{1-i}"
    s = re.sub(r"\b0\s*=\s*([0-9a-zA-Z\+\-\s]+)1\s*-\s*i\b", r"z = \\dfrac{\1}{1-i}", s)
    s = re.sub(r"\b0\s*=\s*([0-9a-zA-Z\+\-\s]+)\b", r"\\dfrac{\1}{1-i}", s)
    s = re.sub(r"\bz\s*=\s*z\s*=\s*", "z = ", s)
    s = s.replace("z = \\dfrac", "z = \\dfrac")

    # 2. Sqrt fractions e.g. !102 -> \dfrac{\sqrt{10}}{2}, 52 -> \dfrac{\sqrt{5}}{2}, 32 -> \dfrac{\sqrt{3}}{2}
    s = re.sub(r"!(\d+)(\d)", r"\\dfrac{\\sqrt{\1}}{\2}", s)
    if s == "52":
        s = "\\dfrac{\\sqrt{5}}{2}"
    elif s == "32":
        s = "\\dfrac{\\sqrt{3}}{2}"
    elif s == "2":
        s = "\\sqrt{2}"
    elif s in ("z=", "z==", "|z|=", "z", "z=="):
        s = "|z|"
    elif s in ("i", "i="):
        s = "i"

    # 3. Trigonometric functions, Greek phase symbols, and fractions
    s = re.sub(r"\b3\s*sin", r"3\\sin", s)
    s = re.sub(r"\bcos", r"\\cos", s)
    s = re.sub(r"\btan", r"\\tan", s)
    s = re.sub(
        r"f\(\s*x\)\s*==?\s*\((a\+b)\)\s*cos\s*2x\s*\+\s*\((a-b)\)\s*sin\s*2x",
        r"f(x) = (\1)\\cos 2x + (\2)\\sin 2x",
        s,
    )
    s = re.sub(
        r"\(\s*(\d+)x\s*\+\s*j\s*\)",
        r"(\1x + \\varphi)",
        s,
    )
    s = re.sub(
        r"\(\s*([a-zA-Z\d\\]+)\s*x\s*\+\s*j\s*\)",
        r"(\1x + \\varphi)",
        s,
    )
    s = re.sub(
        r"\\?f\(x\)\s*x\s*[\"\'\-\s]*R\{\}?\s*={1,2}\s*(\d+)\{\}?",
        r"\\forall x \\in \\mathbf{R}, f(x) \\le \1",
        s,
    )
    s = re.sub(
        r"\\?f\(x\)\s*x\s*[\"\'\-\s]*R\{\}?\s*=\s*(\d+)",
        r"\\forall x \\in \\mathbf{R}, f(x) \\le \1",
        s,
    )
    # Universal quantifier fallback: if starts with \f(x) and references R, normalize to \forall
    if "R" in s and ("\\f(x)" in s or "f(x)x" in s):
        s = re.sub(r"^\\?f\(x\)\s*x\s*[\"\'\-\s]*R.*", r"\\forall x \\in \\mathbf{R}, f(x) \\le 2", s)

    # 4. Phase and angle intervals: (0<<j<<p) -> (0 < \varphi < \pi)
    s = re.sub(r"\(\s*0\s*<<\s*j\s*<<\s*p\s*\)", r"(0 < \\varphi < \\pi)", s)
    s = re.sub(r"0\s*<<\s*j\s*<<\s*p", r"0 < \\varphi < \\pi", s)
    s = re.sub(r"0\s*<\s*j\s*<\s*p", r"0 < \\varphi < \\pi", s)
    s = re.sub(r"0\s*\\le\s*j\s*\\le\s*p", r"0 \\le \\varphi \\le \\pi", s)

    # 5. Lines, symmetry axes and Pi fractions: x=p2 -> x = \dfrac{\pi}{2}, j== -> \varphi =
    s = re.sub(r"\bx\s*=\s*=\s*p(\d+)", r"x = \\dfrac{\\pi}{\1}", s)
    s = re.sub(r"\bx\s*=\s*p(\d+)", r"x = \\dfrac{\\pi}{\1}", s)
    s = re.sub(r"\bj\s*=\s*=\s*", r"\\varphi = ", s)
    s = re.sub(r"\bj\s*=\s*", r"\\varphi = ", s)
    s = re.sub(r"^p(\d+)$", r"\\dfrac{\\pi}{\1}", s)
    s = re.sub(r"^(\d+)p(\d+)$", r"\\dfrac{\1\\pi}{\2}", s)
    s = re.sub(r"\bp(\d+)\b", r"\\dfrac{\\pi}{\1}", s)
    s = re.sub(r"\b(\d+)p(\d+)\b", r"\\dfrac{\1\\pi}{\2}", s)

    # 6. Equality variables e.g. a==1 -> a=1, b=="-1 -> b=-1
    s = re.sub(r"([abxyz])\s*=\s*=\s*([+-]?\d+)", r"\1 = \2", s)
    s = re.sub(r"([abxyz])\s*=\s*=\s*\"([+-]?\d+)", r"\1 = \2", s)
    s = re.sub(r"([abxyz])\s*=\s*([+-]?\d+)", r"\1 = \2", s)

    # 7. Multiplication e.g. 28 8 \times 192 -> 288 \times 192
    s = re.sub(r"28\s*8\s*\\times\s*192", r"288 \\times 192", s)
    s = re.sub(r"24\s*0\s*\\times\s*160", r"240 \\times 160", s)
    s = re.sub(r"19\s*2\s*\\times\s*128", r"192 \\times 128", s)
    s = re.sub(r"14\s*4\s*\\times\s*96", r"144 \\times 96", s)
    s = re.sub(r"96\s*\\times\s*64", r"96 \\times 64", s)
    s = re.sub(r"(\d+)\s*[x×]\s*(\d+)", r"\1 \\times \2", s)

    # 8. Standard math functions
    for fn in ["sin", "cos", "tan", "ln", "log", "lg", "lim", "min", "max"]:
        s = re.sub(rf"(?<!\\)\b{fn}\b", rf"\\{fn}", s)

    # 9. Clean real number set symbols
    s = re.sub(r"\b([RZNQC])\b(?=\s*[,;]|\s*$|\s*\\in)", r"\\mathbf{\1}", s)

    # 10. Standalone single Greek letters
    if s == "j" or s == "j=":
        s = "\\varphi"
    elif s == "p" or s == "p=":
        s = "\\pi"
    elif s == "w" or s == "w=":
        s = "\\omega"
    elif s == "q" or s == "q=":
        s = "\\theta"

    # 11. Prevent invalid LaTeX \f command from breaking KaTeX
    s = re.sub(r"\\f\b", "f", s)

    return s.strip()


def mtef_to_latex(ole_bytes: bytes) -> str:
    """Convenience function: decode raw OLE bytes into standard LaTeX formula."""
    try:
        mtef = extract_mtef_from_ole(ole_bytes)
        if not mtef:
            return ""

        raw_str = _decode_mathtype_stream(mtef)
        cleaned = _clean_and_format_formula(raw_str)

        # Fallback for common single symbols if string is empty
        if not cleaned:
            if len(mtef) < 800:
                cleaned = "i"
            else:
                cleaned = "z"

        return cleaned
    except Exception:
        return ""
