"""Structural MathType/MTEF extraction and conservative legacy fallback.

MathType stores equations as MTEF records inside an OLE ``Equation Native``
stream.  Template records, rather than flattened printable bytes, decide
whether content is a fraction, radical, script, fence, matrix, and so on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import math
import re
import struct
from typing import Any

import olefile
from olefile.olefile import OleFileError


MAX_MTEF_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_DEPTH = 128


@dataclass(frozen=True)
class MtefDecodeResult:
    latex: str = ""
    success: bool = False
    confidence: str = "none"
    warning: str = ""


@dataclass
class MtefNode:
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["MtefNode"] = field(default_factory=list)


class MtefParseError(ValueError):
    """Raised when an MTEF byte stream is truncated or structurally invalid."""


def _looks_like_mtef(data: bytes) -> bool:
    if len(data) < 8 or data[0] != 5:
        return False
    if data[1] not in (0, 1) or data[2] not in (0, 1):
        return False
    if data[3] < 4 or data[3] > 20:
        return False
    return b"\x00" in data[5:256]


def _unwrap_equation_native(stream: bytes) -> bytes:
    """Remove the 28-byte EQNOLEFILEHDR when present."""
    if not stream or len(stream) > MAX_MTEF_BYTES + 28:
        return b""
    if _looks_like_mtef(stream):
        return stream
    if len(stream) < 28:
        return b""
    try:
        cb_hdr, _version, _clipboard_format, cb_object, *_reserved = struct.unpack_from(
            "<HIHI4I", stream, 0
        )
    except struct.error:
        return b""
    if cb_hdr < 28 or cb_hdr > len(stream):
        return b""
    available = len(stream) - cb_hdr
    if cb_object <= 0 or cb_object > available or cb_object > MAX_MTEF_BYTES:
        return b""
    payload = stream[cb_hdr : cb_hdr + cb_object]
    return payload if _looks_like_mtef(payload) else b""


def extract_mtef_from_ole(ole_bytes: bytes) -> bytes:
    """Extract MTEF from a real OLE ``Equation Native`` stream.

    Direct MTEF and direct Equation-Native stream bytes are accepted as well;
    this keeps the helper testable and supports DOCX producers that omit the
    compound-file wrapper.
    """
    if not ole_bytes or len(ole_bytes) > MAX_MTEF_BYTES * 2:
        return b""

    direct = _unwrap_equation_native(ole_bytes)
    if direct:
        return direct

    try:
        with olefile.OleFileIO(io.BytesIO(ole_bytes)) as ole:
            for stream_path in ole.listdir(streams=True, storages=False):
                leaf = stream_path[-1].lstrip("\x00\x01\x02\x03\x04\x05").strip().lower()
                if leaf != "equation native":
                    continue
                native = ole.openstream(stream_path).read(MAX_MTEF_BYTES + 29)
                return _unwrap_equation_native(native)
    except (OSError, IOError, ValueError, TypeError, OleFileError):
        pass

    # A few malformed legacy documents expose the stream contiguously but have
    # a damaged compound-file directory. Keep this bounded locator as the last
    # extraction fallback; the payload still has to pass structural validation.
    search_start = max(0, len(ole_bytes) - MAX_MTEF_BYTES)
    pos = ole_bytes.find(b"\x05", search_start)
    while pos != -1 and pos < len(ole_bytes) - 7:
        candidate = ole_bytes[pos:]
        if _looks_like_mtef(candidate):
            return candidate
        pos = ole_bytes.find(b"\x05", pos + 1)
    return b""


class _MtefParser:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.record_count = 0

    def _need(self, size: int) -> None:
        if size < 0 or self.pos + size > len(self.data):
            raise MtefParseError("MTEF 数据不完整")

    def u8(self) -> int:
        self._need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        self._need(2)
        value = int.from_bytes(self.data[self.pos:self.pos + 2], "little")
        self.pos += 2
        return value

    def unsigned(self) -> int:
        value = self.u8()
        return self.u16() if value == 255 else value

    def signed(self) -> int:
        value = self.u8()
        return self.u16() - 32768 if value == 255 else value - 128

    def c_string(self, limit: int = 4096) -> str:
        end_limit = min(len(self.data), self.pos + limit)
        end = self.data.find(b"\x00", self.pos, end_limit)
        if end == -1:
            raise MtefParseError("MTEF 字符串未终止")
        raw = self.data[self.pos:end]
        self.pos = end + 1
        return raw.decode("latin-1", errors="replace")

    def nudge(self) -> tuple[int, int]:
        dx = self.u8()
        dy = self.u8()
        if dx == 128 and dy == 128:
            raw_dx = self.u16()
            raw_dy = self.u16()
            dx = raw_dx - 65536 if raw_dx >= 32768 else raw_dx
            dy = raw_dy - 65536 if raw_dy >= 32768 else raw_dy
            return dx, dy
        return dx - 128, dy - 128

    def variation(self) -> int:
        first = self.u8()
        if first & 0x80:
            return (first & 0x7F) | (self.u8() << 8)
        return first

    def header(self) -> dict[str, Any]:
        if self.u8() != 5:
            raise MtefParseError("仅支持 MathType MTEF v5")
        platform = self.u8()
        product = self.u8()
        product_version = self.u8()
        product_subversion = self.u8()
        if platform not in (0, 1) or product not in (0, 1) or product_version < 4:
            raise MtefParseError("MTEF 头部无效")
        application_key = self.c_string(256)
        equation_options = self.u8()
        return {
            "platform": platform,
            "product": product,
            "product_version": product_version,
            "product_subversion": product_subversion,
            "application_key": application_key,
            "equation_options": equation_options,
        }

    def object_list(self, depth: int, *, null_line: bool = False) -> list[MtefNode]:
        if null_line:
            return []
        if depth > MAX_DEPTH:
            raise MtefParseError("MTEF 嵌套层级过深")
        children: list[MtefNode] = []
        while True:
            self._need(1)
            if self.data[self.pos] == 0:
                self.pos += 1
                return children
            children.append(self.record(depth + 1))

    def _dimension_array(self) -> None:
        count = self.u8()
        completed = 0
        while completed < count:
            packed = self.u8()
            for nibble in (packed >> 4, packed & 0x0F):
                if nibble == 0x0F:
                    completed += 1
                    if completed == count:
                        break

    def record(self, depth: int = 0) -> MtefNode:
        self.record_count += 1
        if self.record_count > MAX_RECORDS:
            raise MtefParseError("MTEF 记录数量异常")
        record_type = self.u8()
        if record_type == 0:
            raise MtefParseError("END 记录位置错误")

        if record_type == 1:  # LINE
            options = self.u8()
            if options & 0x08:
                self.nudge()
            if options & 0x04:
                self.u16()
            if options & 0x02:
                ruler = self.record(depth + 1)
                if ruler.kind != "ruler":
                    raise MtefParseError("LINE 的 RULER 记录无效")
            null_line = bool(options & 0x01)
            return MtefNode("line", {"null": null_line}, self.object_list(depth, null_line=null_line))

        if record_type == 2:  # CHAR
            options = self.u8()
            if options & 0x08:
                self.nudge()
            typeface = self.signed()
            mtcode = None if options & 0x20 else self.u16()
            char8 = self.u8() if options & 0x04 else None
            char16 = self.u16() if options & 0x10 else None
            embellishments: list[MtefNode] = []
            if options & 0x01:
                embellishments = self.object_list(depth)
                if any(node.kind != "embell" for node in embellishments):
                    raise MtefParseError("CHAR 修饰记录无效")
            return MtefNode("char", {
                "typeface": typeface,
                "mtcode": mtcode,
                "char8": char8,
                "char16": char16,
                "function_start": bool(options & 0x02),
                "embellishments": [node.attrs["type"] for node in embellishments],
            })

        if record_type == 3:  # TMPL
            options = self.u8()
            if options & 0x08:
                self.nudge()
            selector = self.u8()
            variation = self.variation()
            template_options = self.u8()
            return MtefNode("template", {
                "selector": selector,
                "variation": variation,
                "template_options": template_options,
            }, self.object_list(depth))

        if record_type == 4:  # PILE
            options = self.u8()
            if options & 0x08:
                self.nudge()
            halign = self.u8()
            valign = self.u8()
            if options & 0x02:
                ruler = self.record(depth + 1)
                if ruler.kind != "ruler":
                    raise MtefParseError("PILE 的 RULER 记录无效")
            return MtefNode("pile", {"halign": halign, "valign": valign}, self.object_list(depth))

        if record_type == 5:  # MATRIX
            options = self.u8()
            if options & 0x08:
                self.nudge()
            valign, h_just, v_just = self.u8(), self.u8(), self.u8()
            rows, cols = self.u8(), self.u8()
            if rows == 0 or cols == 0 or rows * cols > 10_000:
                raise MtefParseError("MTEF 矩阵尺寸无效")
            self._need(math.ceil((rows + 1) / 4) + math.ceil((cols + 1) / 4))
            self.pos += math.ceil((rows + 1) / 4) + math.ceil((cols + 1) / 4)
            return MtefNode("matrix", {
                "rows": rows, "cols": cols, "valign": valign,
                "h_just": h_just, "v_just": v_just,
            }, self.object_list(depth))

        if record_type == 6:  # EMBELL
            options = self.u8()
            if options & 0x08:
                self.nudge()
            return MtefNode("embell", {"type": self.u8()})

        if record_type == 7:  # RULER (no options byte)
            stops = self.u8()
            self._need(stops * 3)
            self.pos += stops * 3
            return MtefNode("ruler")

        if record_type == 8:  # FONT_STYLE_DEF
            return MtefNode("definition", {"type": record_type, "font": self.unsigned(), "style": self.u8()})

        if record_type == 9:  # SIZE
            first = self.u8()
            if first == 101:
                self.u16()
            elif first == 100:
                self.u8()
                self.u16()
            else:
                self.u8()
            return MtefNode("style")

        if 10 <= record_type <= 14:
            return MtefNode("style")

        if record_type == 15:  # COLOR
            self.unsigned()
            return MtefNode("style")

        if record_type == 16:  # COLOR_DEF
            options = self.u8()
            for _ in range(4 if options & 0x01 else 3):
                self.u16()
            if options & 0x04:
                self.c_string()
            return MtefNode("definition", {"type": record_type})

        if record_type == 17:  # FONT_DEF
            encoding = self.unsigned()
            name = self.c_string()
            return MtefNode("definition", {"type": record_type, "encoding": encoding, "name": name})

        if record_type == 18:  # EQN_PREFS
            self.u8()  # options
            self._dimension_array()
            self._dimension_array()
            style_count = self.u8()
            for _ in range(style_count):
                definition_index = self.unsigned()
                if definition_index:
                    self.u8()
            return MtefNode("definition", {"type": record_type})

        if record_type == 19:  # ENCODING_DEF
            return MtefNode("definition", {"type": record_type, "name": self.c_string()})

        if record_type >= 100:
            length = self.unsigned()
            self._need(length)
            self.pos += length
            return MtefNode("future", {"type": record_type})

        raise MtefParseError(f"不支持的 MTEF 记录类型：{record_type}")

    def parse(self) -> list[MtefNode]:
        self.header()
        nodes = self.object_list(0)
        if any(byte != 0 for byte in self.data[self.pos:]):
            raise MtefParseError("MTEF 尾部存在无法解释的数据")
        return nodes


_UNICODE_LATEX = {
    0x00B0: r"^\circ", 0x00B1: r"\pm", 0x00B7: r"\cdot", 0x00D7: r"\times",
    0x00F7: r"\div", 0x019B: r"\lambda", 0x0391: r"A", 0x0392: r"B",
    0x0393: r"\Gamma", 0x0394: r"\Delta", 0x0398: r"\Theta", 0x039B: r"\Lambda",
    0x039E: r"\Xi", 0x03A0: r"\Pi", 0x03A3: r"\Sigma", 0x03A6: r"\Phi",
    0x03A8: r"\Psi", 0x03A9: r"\Omega", 0x03B1: r"\alpha", 0x03B2: r"\beta",
    0x03B3: r"\gamma", 0x03B4: r"\delta", 0x03B5: r"\varepsilon", 0x03B6: r"\zeta",
    0x03B7: r"\eta", 0x03B8: r"\theta", 0x03B9: r"\iota", 0x03BA: r"\kappa",
    0x03BB: r"\lambda", 0x03BC: r"\mu", 0x03BD: r"\nu", 0x03BE: r"\xi",
    0x03C0: r"\pi", 0x03C1: r"\rho", 0x03C2: r"\varsigma", 0x03C3: r"\sigma",
    0x03C4: r"\tau", 0x03C5: r"\upsilon", 0x03C6: r"\varphi", 0x03C7: r"\chi",
    0x03C8: r"\psi", 0x03C9: r"\omega", 0x03D1: r"\vartheta", 0x03D5: r"\phi",
    0x03F5: r"\epsilon", 0x2016: r"\Vert", 0x2026: r"\ldots", 0x2190: r"\leftarrow",
    0x2192: r"\to", 0x2194: r"\leftrightarrow", 0x21D0: r"\Leftarrow",
    0x21D2: r"\Rightarrow", 0x21D4: r"\Leftrightarrow", 0x2200: r"\forall",
    0x2202: r"\partial", 0x2203: r"\exists", 0x2205: r"\varnothing", 0x2207: r"\nabla",
    0x2208: r"\in", 0x2209: r"\notin", 0x220F: r"\prod", 0x2211: r"\sum",
    0x2212: "-", 0x221A: r"\sqrt{}", 0x221D: r"\propto", 0x221E: r"\infty",
    0x2220: r"\angle", 0x2225: r"\parallel", 0x2227: r"\wedge", 0x2228: r"\vee",
    0x2229: r"\cap", 0x222A: r"\cup", 0x222B: r"\int", 0x2234: r"\therefore",
    0x2235: r"\because", 0x223C: r"\sim", 0x2248: r"\approx", 0x2260: r"\ne",
    0x2261: r"\equiv", 0x2264: r"\le", 0x2265: r"\ge", 0x2282: r"\subset",
    0x2283: r"\supset", 0x2286: r"\subseteq", 0x2287: r"\supseteq", 0x22A5: r"\perp",
}

_SYMBOL_FONT = {
    **{ord(k): v for k, v in {
        "a": r"\alpha", "b": r"\beta", "c": r"\chi", "d": r"\delta",
        "e": r"\varepsilon", "f": r"\varphi", "g": r"\gamma", "h": r"\eta",
        "i": r"\iota", "j": r"\phi", "k": r"\kappa", "l": r"\lambda",
        "m": r"\mu", "n": r"\nu", "p": r"\pi", "q": r"\theta",
        "r": r"\rho", "s": r"\sigma", "t": r"\tau", "u": r"\upsilon",
        "w": r"\omega", "x": r"\xi", "y": r"\psi", "z": r"\zeta",
    }.items()},
    0xB1: r"\pm", 0xB4: r"\times", 0xB9: r"\ne", 0xA3: r"\le",
    0xB3: r"\ge", 0xA5: r"\infty", 0xCE: r"\in", 0xCF: r"\notin",
}

_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "ln", "log", "lg", "exp", "lim", "min", "max",
    "sup", "inf", "det", "gcd",
}


def _escape_ascii_character(char: str) -> str:
    return {"{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "&": r"\&"}.get(char, char)


def _character_latex(node: MtefNode) -> str:
    attrs = node.attrs
    mtcode = attrs.get("mtcode")
    encoded = attrs.get("char16") if attrs.get("char16") is not None else attrs.get("char8")
    typeface = attrs.get("typeface")
    value = ""
    if mtcode in _UNICODE_LATEX:
        value = _UNICODE_LATEX[mtcode]
    elif mtcode is not None and 0x20 <= mtcode <= 0x10FFFF and not 0xD800 <= mtcode <= 0xDFFF:
        value = _escape_ascii_character(chr(mtcode))
    elif encoded is not None and typeface in (4, 5, 6):
        value = _SYMBOL_FONT.get(encoded, "")
    elif encoded is not None and 0x20 <= encoded < 0x7F:
        value = _escape_ascii_character(chr(encoded))
    if not value:
        raise MtefParseError(f"无法转换 MathType 字符编码：{mtcode!r}/{encoded!r}")

    for embellishment in attrs.get("embellishments", []):
        if embellishment == 2:
            value = rf"\dot{{{value}}}"
        elif embellishment == 3:
            value = rf"\ddot{{{value}}}"
        elif embellishment == 4:
            value = rf"\overset{{\dots}}{{{value}}}"
        elif embellishment == 5:
            value += "'"
        elif embellishment == 6:
            value += "''"
        elif embellishment == 8:
            value = rf"\widetilde{{{value}}}"
        elif embellishment == 9:
            value = rf"\widehat{{{value}}}"
        elif embellishment == 10:
            value = rf"\not{{{value}}}"
        elif embellishment == 11:
            value = rf"\overrightarrow{{{value}}}"
        elif embellishment == 12:
            value = rf"\overleftarrow{{{value}}}"
        elif embellishment == 13:
            value = rf"\overleftrightarrow{{{value}}}"
        elif embellishment == 17:
            value = rf"\overline{{{value}}}"
        elif embellishment == 18:
            value += "'''"
        elif embellishment == 29:
            value = rf"\underline{{{value}}}"
        else:
            raise MtefParseError(f"暂不支持 MathType 字符修饰类型：{embellishment}")
    return value


def _visible_children(node: MtefNode) -> list[MtefNode]:
    return [child for child in node.children if child.kind not in {"definition", "style", "future", "ruler"}]


def _render_sequence(nodes: list[MtefNode]) -> str:
    parts: list[str] = []

    def append_part(value: str) -> None:
        """Keep TeX control words separate from following letter operands.

        MTEF stores an operator/symbol and the following variable as distinct
        nodes. Blind concatenation turns ``\forall`` + ``z`` into the invalid
        control word ``\forallz``. Detect the node boundary generically instead
        of maintaining a command-by-command repair list.
        """
        if not value:
            return
        if (
            parts
            and re.search(r"\\[A-Za-z]+$", parts[-1])
            and re.match(r"[A-Za-z]", value)
        ):
            parts.append(" ")
        parts.append(value)

    index = 0
    while index < len(nodes):
        node = nodes[index]
        if node.kind == "char" and (node.attrs.get("function_start") or node.attrs.get("typeface") == 2):
            function_chars: list[str] = []
            cursor = index
            while cursor < len(nodes) and nodes[cursor].kind == "char":
                candidate = _character_latex(nodes[cursor])
                if len(candidate) != 1 or not candidate.isalpha():
                    break
                if cursor > index and nodes[cursor].attrs.get("typeface") not in (2, 3):
                    break
                function_chars.append(candidate)
                cursor += 1
            function_name = "".join(function_chars)
            if function_name in _FUNCTIONS:
                append_part("\\" + function_name)
                index = cursor
                continue
        append_part(_render_node(node))
        index += 1
    return "".join(parts)


def _slot(children: list[MtefNode], index: int) -> str:
    if index >= len(children):
        return ""
    return _render_node(children[index])


def _render_template(node: MtefNode) -> str:
    selector = node.attrs["selector"]
    variation = node.attrs["variation"]
    children = _visible_children(node)

    if 0 <= selector <= 8:
        content = _slot(children, 0)
        defaults = {
            0: (r"\langle", r"\rangle"), 1: ("(", ")"),
            2: (r"\{", r"\}"), 3: ("[", "]"), 4: ("|", "|"),
            5: (r"\Vert", r"\Vert"), 6: (r"\lfloor", r"\rfloor"),
            7: (r"\lceil", r"\rceil"), 8: (r"\llbracket", r"\rrbracket"),
        }
        left, right = defaults[selector]
        if len(children) > 1:
            left = _slot(children, 1) or left
        if len(children) > 2:
            right = _slot(children, 2) or right
        if not (variation & 0x01):
            left = "."
        if not (variation & 0x02):
            right = "."
        return rf"\left{left}{content}\right{right}"

    if selector == 9:
        content = _slot(children, 0)
        left = ("(", ")", "[", "]")[variation & 0x03]
        right = ("(", ")", "[", "]")[(variation >> 4) & 0x03]
        return rf"\left{left}{content}\right{right}"

    if selector == 10:
        radicand = _slot(children, 0)
        root_index = _slot(children, 1)
        if not radicand:
            raise MtefParseError("根式缺少被开方数")
        return rf"\sqrt[{root_index}]{{{radicand}}}" if variation == 1 and root_index else rf"\sqrt{{{radicand}}}"

    if selector == 11:
        numerator, denominator = _slot(children, 0), _slot(children, 1)
        if not numerator or not denominator:
            raise MtefParseError("分式缺少分子或分母")
        if variation & 0x02:
            return rf"{{{numerator}}}/{{{denominator}}}"
        return rf"\dfrac{{{numerator}}}{{{denominator}}}"

    if selector in (12, 13):
        command = "underline" if selector == 12 else "overline"
        value = rf"\{command}{{{_slot(children, 0)}}}"
        return rf"\{command}{{{value}}}" if variation & 0x01 else value

    if selector == 14:
        main = _slot(children, 0)
        arrow = _slot(children, 1)
        return arrow or rf"\xrightarrow{{{main}}}"

    if 15 <= selector <= 22:
        main, upper, lower = _slot(children, 0), _slot(children, 1), _slot(children, 2)
        operator_child = _slot(children, 3)
        defaults = {
            15: r"\int", 16: r"\sum", 17: r"\prod", 18: r"\coprod",
            19: r"\bigcup", 20: r"\bigcap", 21: r"\mathop", 22: r"\mathop",
        }
        operator = operator_child or defaults[selector]
        limits = (rf"_{{{lower}}}" if lower else "") + (rf"^{{{upper}}}" if upper else "")
        return operator + limits + (rf"{{{main}}}" if main else "")

    if selector == 23:
        main, lower, upper = _slot(children, 0), _slot(children, 1), _slot(children, 2)
        return main + (rf"_{{{lower}}}" if lower else "") + (rf"^{{{upper}}}" if upper else "")

    if selector in (24, 25):
        main, annotation = _slot(children, 0), _slot(children, 1)
        if selector == 24:
            command = "overbrace" if variation & 0x01 else "underbrace"
        else:
            command = "overline" if variation & 0x01 else "underline"
        rendered = rf"\{command}{{{main}}}"
        if annotation:
            rendered += rf"^{{{annotation}}}" if variation & 0x01 else rf"_{{{annotation}}}"
        return rendered

    if selector == 26:
        dividend, quotient = _slot(children, 0), _slot(children, 1)
        return rf"\overline{{{quotient}\smash{{\big)}}{dividend}}}"

    if selector in (27, 28, 29):
        subscript, superscript = _slot(children, 0), _slot(children, 1)
        script = ""
        if selector in (27, 29) and subscript:
            script += rf"_{{{subscript}}}"
        if selector in (28, 29) and superscript:
            script += rf"^{{{superscript}}}"
        return "{}" + script if variation & 0x01 else script

    if selector == 30:
        left, right = _slot(children, 0), _slot(children, 1)
        return rf"\left\langle {left}\middle|{right}\right\rangle"

    if selector == 31:
        main = _slot(children, 0)
        if variation & 0x04:
            return rf"\underrightarrow{{{main}}}"
        if variation & 0x01:
            return rf"\overleftarrow{{{main}}}"
        return rf"\vec{{{main}}}"

    if selector in (32, 33, 34, 35):
        command = {32: "widetilde", 33: "widehat", 34: "wideparen", 35: "overline"}[selector]
        return rf"\{command}{{{_slot(children, 0)}}}"

    if selector == 36:
        main = _slot(children, 0)
        if variation & 0x01:
            return rf"\cancelto{{}}{{{main}}}"
        return rf"\cancel{{{main}}}"

    if selector == 37:
        return rf"\boxed{{{_slot(children, 0)}}}"

    raise MtefParseError(f"暂不支持 MathType 模板类型：{selector}")


def _render_node(node: MtefNode) -> str:
    if node.kind == "char":
        return _character_latex(node)
    if node.kind == "line":
        return "" if node.attrs.get("null") else _render_sequence(_visible_children(node))
    if node.kind == "template":
        return _render_template(node)
    if node.kind == "pile":
        rows = [_render_node(child) for child in _visible_children(node)]
        return r"\begin{gathered}" + r" \\ ".join(rows) + r"\end{gathered}"
    if node.kind == "matrix":
        rows, cols = node.attrs["rows"], node.attrs["cols"]
        cells = [_render_node(child) for child in _visible_children(node)]
        if len(cells) < rows * cols:
            raise MtefParseError("MTEF 矩阵单元格数量不足")
        lines = [" & ".join(cells[row * cols:(row + 1) * cols]) for row in range(rows)]
        return r"\begin{matrix}" + r" \\ ".join(lines) + r"\end{matrix}"
    if node.kind in {"definition", "style", "future", "ruler"}:
        return ""
    raise MtefParseError(f"无法渲染 MTEF 节点：{node.kind}")


def _balanced_latex(value: str) -> bool:
    if not value or len(value) > 20_000 or "\x00" in value:
        return False
    depth = 0
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _decode_compatibility_stream(data: bytes) -> str:
    """Read only general textual hints from malformed legacy MathType data."""
    if not data or len(data) < 10:
        return ""
    font_end = data.find(b"WinAllCodePages")
    raw_bytes = data[font_end + len(b"WinAllCodePages"):] if font_end != -1 else data
    symbols = {
        0xB1: r" \pm ", 0xD7: r" \times ", 0xF7: r" \div ", 0xB7: r" \cdot ",
        0xB0: r"^\circ ", 0xA5: r" \infty ", 0xA3: r" \le ", 0xB3: r" \ge ",
        0xA2: r" \ne ", 0xCE: r" \in ", 0xCF: r" \notin ",
    }
    chars = [chr(byte) if 32 <= byte < 127 else symbols.get(byte, "") for byte in raw_bytes]
    value = "".join(chars)
    value = re.sub(r"(?:E|e)?quation\s*Native", "", value, flags=re.IGNORECASE)
    return re.sub(r"[\r\n]+", " ", value).strip()


def _apply_compatibility_rules(raw: str) -> str:
    """Normalize unambiguous text without guessing symbols or templates.

    Deliberately absent are historical rules such as ``32 -> sqrt(3)/2`` and
    ``p -> pi``. Those meanings are now emitted only from MTEF structure or
    character encoding.
    """
    value = re.sub(r"\s+", " ", raw).strip()
    for name in sorted(_FUNCTIONS, key=len, reverse=True):
        value = re.sub(rf"(?<!\\)\b{name}\b", rf"\\{name}", value)
    function_pattern = "|".join(sorted(_FUNCTIONS, key=len, reverse=True))
    value = re.sub(rf"([A-Za-z0-9])\s+(\\(?:{function_pattern})\b)", r"\1\2", value)
    value = re.sub(r"(\d+)\s*[x×]\s*(\d+)", r"\1 \\times \2", value)
    return value


def _plausible_compatibility_formula(value: str) -> bool:
    if not value or len(value) > 4_000 or not _balanced_latex(value):
        return False
    if any(marker.lower() in value.lower() for marker in (
        "WinAllCodePages", "Times New Roman", "Equation Native", "MathType",
    )):
        return False
    if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        return False
    return "\\" in value or any(char in value for char in "=<>+-*/^")


def decode_mtef_formula(ole_bytes: bytes) -> MtefDecodeResult:
    """Decode a MathType formula using annotation, structure, then safe text."""
    payload = extract_mtef_from_ole(ole_bytes)
    if not payload:
        return MtefDecodeResult(warning="未找到 MathType Equation Native 数据流")

    text = payload.decode("utf-8", errors="ignore")
    match = re.search(
        r"(?:MathType\s*)?(?:LaTeX|TeX)\s*(?::|=)\s*([^\x00\r\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        latex = match.group(1).strip().strip("$")
        if _balanced_latex(latex):
            return MtefDecodeResult(latex=latex, success=True, confidence="high")

    structural_error = ""
    try:
        nodes = _MtefParser(payload).parse()
        latex = _render_sequence(_visible_children(MtefNode("root", children=nodes))).strip()
        if _balanced_latex(latex):
            return MtefDecodeResult(latex=latex, success=True, confidence="structural")
        structural_error = "结构解析结果为空或括号不完整"
    except MtefParseError as exc:
        structural_error = str(exc)

    compatibility = _apply_compatibility_rules(_decode_compatibility_stream(payload))
    if _plausible_compatibility_formula(compatibility):
        return MtefDecodeResult(
            latex=compatibility,
            success=True,
            confidence="compatibility",
            warning=f"MathType 结构解析失败（{structural_error}），已使用有限文本兼容模式",
        )

    return MtefDecodeResult(
        warning=f"MathType 结构无法可靠转换（{structural_error or '未知格式'}），已保留公式预览图"
    )


def mtef_to_latex(ole_bytes: bytes) -> str:
    """Backward-compatible convenience API."""
    return decode_mtef_formula(ole_bytes).latex
