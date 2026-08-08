"""
MTEF (MathType Equation Format) to LaTeX parser in pure Python.
Decodes Equation Native / OLE MathType formulas embedded in Microsoft Word (.docx/.doc) documents.
Zero heavy external C-dependencies.
"""

import struct
import re
from typing import Optional, List, Tuple


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


class MTEFDecoder:
    """Decodes MTEF v3, v4, and v5 record streams into standard LaTeX math syntax."""

    # MathType Symbol / Character Code to LaTeX Map
    SYMBOL_MAP = {
        0x2B: "+",
        0x2D: "-",
        0x3D: "=",
        0x3C: "<",
        0x3E: ">",
        0x28: "(",
        0x29: ")",
        0x5B: "[",
        0x5D: "]",
        0x7B: "\\{",
        0x7D: "\\}",
        0x2C: ", ",
        0x2E: ".",
        0x2F: "/",
        0x3A: ":",
        0x3B: ";",
        0xB1: "\\pm ",
        0xD7: "\\times ",
        0xF7: "\\div ",
        0x2208: "\\in ",
        0x2209: "\\notin ",
        0x2282: "\\subset ",
        0x2286: "\\subseteq ",
        0x2229: "\\cap ",
        0x222A: "\\cup ",
        0x2264: "\\le ",
        0x2265: "\\ge ",
        0x2260: "\\ne ",
        0x2248: "\\approx ",
        0x221E: "\\infty ",
        0x22A5: "\\perp ",
        0x2225: "\\parallel ",
        0x2235: "\\because ",
        0x2234: "\\therefore ",
        0x2200: "\\forall ",
        0x2203: "\\exists ",
        0x03B1: "\\alpha ",
        0x03B2: "\\beta ",
        0x03B3: "\\gamma ",
        0x03B4: "\\delta ",
        0x03B5: "\\varepsilon ",
        0x03B8: "\\theta ",
        0x03BB: "\\lambda ",
        0x03BC: "\\mu ",
        0x03C0: "\\pi ",
        0x03C1: "\\rho ",
        0x03C3: "\\sigma ",
        0x03C4: "\\tau ",
        0x03C6: "\\varphi ",
        0x03C9: "\\omega ",
        0x0394: "\\Delta ",
        0x03A9: "\\Omega ",
        0x221A: "\\sqrt{}",
        0x2211: "\\sum ",
        0x220F: "\\prod ",
        0x222B: "\\int ",
        0x2192: "\\rightarrow ",
        0x21D2: "\\Rightarrow ",
        0x2194: "\\leftrightarrow ",
        0x21D4: "\\Leftrightarrow ",
        # Standard MathType Extended Symbol codes
        0x00E0: "\\le ",
        0x00E3: "\\le ",
        0x00F0: "\\ge ",
        0x00F3: "\\ge ",
        0x00B9: "\\ne ",
        0x00A5: "\\infty ",
        0x00CE: "\\in ",
        0x00CF: "\\notin ",
        0x00C7: "\\cap ",
        0x00C8: "\\cup ",
        0x00CC: "\\subset ",
        0x00CD: "\\subseteq ",
        0x00D6: "\\times ",
        0x00B8: "\\div ",
        0x00B1: "\\pm ",
        0x005B: "[",
        0x005D: "]",
        0x007B: "\\{",
        0x007D: "\\}",
        0x0028: "(",
        0x0029: ")",
        0x003D: "=",
        0x003C: "<",
        0x003E: ">",
        0x002B: "+",
        0x002D: "-",
    }

    EMBELL_MAP = {
        1: "'",
        2: "''",
        3: "'''",
        4: "\\dot",
        5: "\\ddot",
        6: "\\dddot",
        7: "\\bar",
        8: "\\vec",
        9: "\\hat",
        10: "\\tilde",
    }

    def __init__(self, mtef_bytes: bytes):
        self.data = mtef_bytes
        self.pos = 0
        self.fonts: List[Tuple[str, int]] = []

    def to_latex(self) -> str:
        """Decode MTEF stream into standard LaTeX math expression."""
        if not self.data or len(self.data) < 5:
            return ""

        ver = self.data[0]
        self.pos = 4

        # Read null-terminated application key (e.g. DSMT7\0 or MathType\0)
        key_end = self.data.find(b"\x00", self.pos)
        if key_end != -1:
            self.pos = key_end + 1

        if self.pos < len(self.data):
            # Skip equation options
            self.pos += 1

        raw_latex = self._parse_container()
        cleaned = self._clean_latex(raw_latex)
        return cleaned

    def _parse_container(self) -> str:
        """Recursively parse container (LINE, PILE, or TEMPLATE)."""
        chunks = []
        while self.pos < len(self.data):
            raw_tag = self.data[self.pos]
            self.pos += 1

            # Mask out MTEF5 high nibbles (e.g. 0xF2 -> 2)
            tag = raw_tag & 0x0F if raw_tag >= 0x80 else raw_tag

            if tag == 0:  # END
                break
            elif tag == 1:  # LINE
                if self.pos < len(self.data):
                    self.pos += 1  # line_options
                line_content = self._parse_container()
                chunks.append(line_content)
            elif tag == 2:  # CHAR
                ch = self._parse_char()
                if ch:
                    chunks.append(ch)
            elif tag == 3:  # TMPL
                tmpl_str = self._parse_template()
                if tmpl_str:
                    chunks.append(tmpl_str)
            elif tag in (4, 5):  # PILE, MATRIX
                if self.pos < len(self.data):
                    self.pos += 1  # options
                pile_content = self._parse_container()
                chunks.append(pile_content)
            elif tag == 6:  # EMBELL
                if self.pos < len(self.data):
                    emb_val = self.data[self.pos]
                    self.pos += 1
                    emb_tex = self.EMBELL_MAP.get(emb_val, "")
                    if emb_tex:
                        if emb_tex in ("'", "''", "'''"):
                            chunks.append(emb_tex)
                        else:
                            # Apply to previous token if available
                            if chunks:
                                prev = chunks.pop()
                                chunks.append(f"{emb_tex}{{{prev}}}")
                            else:
                                chunks.append(emb_tex)
            elif tag == 8:  # FONT_STYLE_DEF
                self.pos += 2
            elif tag == 17:  # FONT_DEF
                if self.pos < len(self.data):
                    enc_idx = self.data[self.pos]
                    self.pos += 1
                    n_end = self.data.find(b"\x00", self.pos)
                    if n_end != -1:
                        fname = self.data[self.pos : n_end].decode(
                            "latin1", errors="ignore"
                        )
                        self.pos = n_end + 1
                        self.fonts.append((fname, enc_idx))
                    else:
                        self.pos = len(self.data)
            elif tag == 18:  # EQN_PREFS
                self.pos += 1
            elif tag == 19:  # ENCODING_DEF
                n_end = self.data.find(b"\x00", self.pos)
                if n_end != -1:
                    self.pos = n_end + 1
                else:
                    self.pos = len(self.data)
            else:
                # Fallback: scan forward for next valid tag or boundary
                pass

        return "".join(chunks)

    def _parse_char(self) -> str:
        """Parse CHAR record."""
        if self.pos >= len(self.data):
            return ""
        # char_options
        self.pos += 1

        typeface = 0
        if self.pos + 2 <= len(self.data):
            typeface = struct.unpack("<h", self.data[self.pos : self.pos + 2])[0]
            self.pos += 2

        char_code = 0
        if self.pos + 2 <= len(self.data):
            char_code = struct.unpack("<H", self.data[self.pos : self.pos + 2])[0]
            self.pos += 2

        # Check symbol map
        if char_code in self.SYMBOL_MAP:
            return self.SYMBOL_MAP[char_code]

        # Standard ASCII characters
        if 32 <= char_code < 127:
            return chr(char_code)

        # Chinese / Unicode characters in MathType
        if char_code >= 0x4E00:
            try:
                return chr(char_code)
            except Exception:
                return ""

        return chr(char_code) if 0 < char_code < 256 and chr(char_code).isprintable() else ""

    def _parse_template(self) -> str:
        """Parse TMPL (Template) record."""
        if self.pos >= len(self.data):
            return ""
        # tmpl options
        self.pos += 1

        selector = 0
        if self.pos < len(self.data):
            selector = self.data[self.pos]
            self.pos += 1

        variation = 0
        if self.pos + 2 <= len(self.data):
            variation = struct.unpack("<H", self.data[self.pos : self.pos + 2])[0]
            self.pos += 2

        # Sub-container contents
        body = self._parse_container()

        # Selector mapping
        # 0: Parentheses ( ... )
        if selector == 0:
            return f"\\left({body}\\right)"
        # 1: Absolute value | ... |
        elif selector == 1:
            return f"\\left|{body}\\right|"
        # 2: Brackets [ ... ]
        elif selector == 2:
            return f"\\left[{body}\\right]"
        # 3: Braces { ... }
        elif selector == 3:
            return f"\\left\\{{{body}\\right\\}}"
        # 4: Sqrt root
        elif selector == 4:
            return f"\\sqrt{{{body}}}"
        # 5: Fraction \dfrac{num}{den}
        elif selector == 5:
            # Handle numerator and denominator lines if separated
            return f"\\dfrac{{{body}}}"
        # 6: Superscript / Subscript
        elif selector == 6:
            # variation 0: sub, 1: sup, 2: sub+sup
            if variation == 1:
                return f"^{{{body}}}"
            elif variation == 0:
                return f"_{{{body}}}"
            return f"^{{{body}}}"
        # 7: Integrals
        elif selector == 7:
            return f"\\int {body}"
        # 8: Sums / Products
        elif selector == 8:
            return f"\\sum {body}"
        # 10: Vectors / Arrows
        elif selector == 10:
            return f"\\vec{{{body}}}"
        # 12: Cases / System of equations
        elif selector == 12:
            return f"\\begin{{cases}} {body} \\end{{cases}}"

        return body

    def _clean_latex(self, s: str) -> str:
        """Post-clean and polish extracted LaTeX math string."""
        if not s:
            return ""

        # Normalize spaces and common artifacts
        s = s.strip()
        # Clean double braces and redundant brackets
        s = re.sub(r"\s+", " ", s)
        # Clean empty braces
        s = s.replace("{}", "")

        # Format function names as standard LaTeX functions
        for fn in [
            "sin",
            "cos",
            "tan",
            "ln",
            "log",
            "lg",
            "min",
            "max",
            "lim",
            "exp",
        ]:
            s = re.sub(rf"(?<!\\)\b{fn}\b", rf"\\{fn}", s)

        # Fix \mathbf{R} for real numbers
        s = re.sub(r"\b([RZNQC])\b(?=\s*[,;]|\s*$|\s*\\in)", r"\\mathbf{\1}", s)

        return s.strip()


def mtef_to_latex(ole_bytes: bytes) -> str:
    """Convenience function: decode raw OLE bytes into LaTeX formula."""
    try:
        mtef = extract_mtef_from_ole(ole_bytes)
        if not mtef:
            return ""
        decoder = MTEFDecoder(mtef)
        return decoder.to_latex()
    except Exception:
        return ""
