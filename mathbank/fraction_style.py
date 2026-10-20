"""Conservative, source-preserving fraction typography for question editors."""

from __future__ import annotations

import re

from mathbank.math_markdown import (
    _INNER_MATH_ENVIRONMENTS,
    _STANDALONE_MATH_ENVIRONMENTS,
    _is_escaped,
    _replace_delimited_math,
    _replace_latex_environments,
)
from mathbank.tex_helper import _balanced_group


_COMMAND = re.compile(r"\\(?:[A-Za-z]+|[^\n])")
_BACKTICKS = re.compile(r"`+")
_ENVIRONMENT = re.compile(r"\\(begin|end)\{([^{}\n]+)\}")
_FRACTIONS = {"frac", "dfrac", "tfrac", "cfrac"}
# exam-zh constants are zero-argument atoms, including when an unbraced
# fraction/script argument is immediately followed by another braced argument.
_EXAMZH_CONSTANTS = {"eu", "upe", "iu", "upi"}
_TEXT_COMMANDS = {
    "text", "textrm", "textsf", "texttt", "textnormal", "textbf", "textit",
    "mbox", "hbox", "verb", "operatorname", "label", "tag", "url", "path",
}
# A macro's argument styles cannot be inferred from its name. Keep the whole
# formula when one of these explicitly controls styles or defines other macros.
_UNSUPPORTED_COMMANDS = {
    "displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle",
    "genfrac", "mathchoice", "mathpalette", "substack", "subarray",
    "overset", "underset", "stackrel", "binom", "dbinom", "tbinom",
    "over", "atop", "above", "choose", "overwithdelims", "atopwithdelims",
    "def", "gdef", "edef", "xdef", "let", "newcommand", "renewcommand",
    "providecommand", "DeclareMathOperator", "newenvironment", "renewenvironment",
}
_UNARY_COMMANDS = {
    "sqrt", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal",
    "mathbb", "mathfrak", "boldsymbol", "overline", "underline", "hat",
    "widehat", "bar", "vec", "overrightarrow", "overleftarrow", "tilde",
    "widetilde", "dot", "ddot", "phantom", "vphantom", "hphantom",
    "mathop", "mathord", "mathbin", "mathrel", "mathopen", "mathclose",
    "mathpunct", "mathinner", "boxed", "underbrace", "overbrace",
}
# Unknown/custom macros may put their arguments in a different math style.
# They are deliberately outside this small parser's contract.
_ORDINARY_COMMANDS = set("""
    alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi
    omicron pi rho sigma tau upsilon phi chi psi omega varepsilon vartheta
    varpi varrho varsigma varphi Gamma Delta Theta Lambda Xi Pi Sigma Upsilon
    Phi Psi Omega digamma varkappa
    sin cos tan cot sec csc arcsin arccos arctan arccot sinh cosh tanh coth
    arg deg det dim exp gcd hom inf ker lg lim liminf limsup ln log max min
    Pr sup lcm injlim projlim varinjlim varprojlim
    infty partial nabla ell hbar imath jmath Re Im wp aleph beth emptyset
    varnothing forall exists nexists neg lnot top bot angle measuredangle
    sphericalangle triangle square circle circ prime backprime bullet star
    ast dagger ddagger complement checkmark
    le leq leqslant ge geq geqslant ne neq lt gt approx sim simeq cong equiv
    asymp propto ll gg doteq prec preceq succ succeq subset subseteq subsetneq
    supset supseteq supsetneq in notin ni owns parallel nparallel perp mid nmid
    pm mp times div cdot cdots ldots vdots ddots dots dotso dotsc dotsb dotsm
    dotss cup cap bigcup bigcap setminus smallsetminus uplus sqcup sqcap
    land lor wedge vee opulus oplus ominus otimes oslash odot amalg wr
    sum prod coprod int iint iiint oint bigvee bigwedge bigoplus bigotimes
    to mapsto gets leftarrow rightarrow leftrightarrow Leftarrow Rightarrow
    Leftrightarrow longleftarrow longrightarrow longleftrightarrow
    Longleftarrow Longrightarrow Longleftrightarrow longmapsto hookrightarrow
    hookleftarrow uparrow downarrow updownarrow Uparrow Downarrow Updownarrow
    implies iff colon therefore because not
    left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr
    lbrace rbrace lbrack rbrack langle rangle lceil rceil lfloor rfloor vert Vert
    lvert rvert lVert rVert backslash slash rgroup lgroup arrowvert Arrowvert
    quad qquad enspace thinspace medspace thickspace negthinspace space
    limits nolimits displaylimits hfill hfil vfill relax mathstrut
    begin end cr newline nonumber notag hline hdashline allowbreak
""".split())
_PROTECTED_PATTERN = re.compile(
    r"(?P<fence>(?m:^[ \t]{0,3}(?P<fence_mark>`{3,}|~{3,})[^\n]*\n)"
    r"[\s\S]*?(?:(?m:^[ \t]{0,3}(?P=fence_mark)[ \t]*(?:\n|$))|\Z))"
    r"|<mathbank-math\b[^>]*>[\s\S]*?(?:</mathbank-math\s*>|\Z)"
    r"|\[\[\s*MBM_[A-Za-z0-9_:\-]+\s*\]\]"
    r"|\\begin\{(?P<environment>tikzpicture|pgfpicture|axis|verbatim\*?|Verbatim|lstlisting|minted)\}"
    r"[\s\S]*?(?:\\end\{(?P=environment)\}|\Z)",
    re.IGNORECASE,
)


class _UncertainMath(ValueError):
    """The input needs a full TeX interpreter; leave this math span alone."""


class _MathNormalizer:
    def __init__(self, source: str, comments: set[str]):
        self.source = source
        self.comments = comments
        self.changes: list[tuple[int, int, str]] = []

    def whitespace(self, cursor: int, end: int) -> int:
        while cursor < end and (self.source[cursor].isspace() or self.source[cursor] in self.comments):
            cursor += 1
        return cursor

    def atom(self, cursor: int, end: int, small: bool, depth: int) -> int:
        cursor = self.whitespace(cursor, end)
        if cursor >= end or self.source[cursor] in "}^_$&":
            raise _UncertainMath
        if self.source[cursor] == "{":
            group = _balanced_group(self.source, cursor)
            if group is None or group[1] > end:
                raise _UncertainMath
            self.sequence(cursor + 1, group[1] - 1, small, depth + 1)
            return group[1]
        if self.source[cursor] == "\\":
            return self.command(cursor, end, small, depth + 1, argument=True)
        return cursor + 1

    def command(self, cursor: int, end: int, small: bool, depth: int, *, argument=False) -> int:
        if depth > 100:
            raise _UncertainMath
        match = _COMMAND.match(self.source, cursor)
        if match is None:
            raise _UncertainMath
        name = match.group()[1:]
        cursor = match.end()
        if name in _UNSUPPORTED_COMMANDS:
            raise _UncertainMath
        if name in _FRACTIONS:
            if name in {"frac", "dfrac"}:
                replacement = "\\frac" if small else "\\dfrac"
                if replacement != match.group():
                    self.changes.append((match.start(), cursor, replacement))
            optional = self.whitespace(cursor, end)
            if name == "cfrac" and optional < end and self.source[optional] == "[":
                option_end = self.source.find("]", optional + 1, end)
                if option_end < 0 or self.source[optional + 1:option_end].strip() not in {"l", "r"}:
                    raise _UncertainMath
                cursor = option_end + 1
            cursor = self.atom(cursor, end, True, depth + 1)
            return self.atom(cursor, end, True, depth + 1)
        if name == "sqrt":
            optional = self.whitespace(cursor, end)
            if optional < end and self.source[optional] == "[":
                index_end = optional + 1
                while index_end < end:
                    if self.source[index_end] == "{" and not _is_escaped(self.source, index_end):
                        group = _balanced_group(self.source, index_end)
                        if group is None or group[1] > end:
                            raise _UncertainMath
                        index_end = group[1]
                        continue
                    if self.source[index_end] == "]" and not _is_escaped(self.source, index_end):
                        break
                    index_end += 1
                if index_end >= end:
                    raise _UncertainMath
                self.sequence(optional + 1, index_end, True, depth + 1)
                cursor = index_end + 1
            return self.atom(cursor, end, small, depth + 1)
        if name in _UNARY_COMMANDS:
            return self.atom(cursor, end, small, depth + 1)
        if name in _EXAMZH_CONSTANTS:
            return cursor
        if name.isalpha() and name not in _ORDINARY_COMMANDS:
            raise _UncertainMath
        if argument and self.whitespace(cursor, end) < end:
            # Unknown argument-taking macros could consume the next group. Do
            # not guess where their argument ends inside a fraction or script.
            if self.source[self.whitespace(cursor, end)] == "{":
                raise _UncertainMath
        return cursor

    def sequence(self, cursor: int, end: int, small: bool = False, depth: int = 0) -> None:
        if depth > 100:
            raise _UncertainMath
        while cursor < end:
            char = self.source[cursor]
            if char in "^_":
                cursor = self.atom(cursor + 1, end, True, depth + 1)
            elif char == "{":
                cursor = self.atom(cursor, end, small, depth + 1)
            elif char == "}":
                raise _UncertainMath
            elif char == "\\":
                cursor = self.command(cursor, end, small, depth + 1)
            elif char == "$":
                raise _UncertainMath
            else:
                cursor += 1

    def normalize(self) -> str:
        stack: list[str] = []
        for token in _ENVIRONMENT.finditer(self.source):
            if _is_escaped(self.source, token.start()):
                continue
            action, name = token.groups()
            if name == "smallmatrix":
                return self.source
            if name not in _INNER_MATH_ENVIRONMENTS | _STANDALONE_MATH_ENVIRONMENTS:
                return self.source
            if action == "begin":
                stack.append(name)
            elif not stack or stack.pop() != name:
                return self.source
        if stack:
            return self.source
        left_depth = 0
        for token in _COMMAND.finditer(self.source):
            if token.group() == r"\left":
                left_depth += 1
            elif token.group() == r"\right":
                left_depth -= 1
                if left_depth < 0:
                    return self.source
            elif token.group() == r"\middle" and not left_depth:
                return self.source
        if left_depth:
            return self.source
        try:
            self.sequence(0, len(self.source))
        except (_UncertainMath, RecursionError):
            return self.source
        parts: list[str] = []
        cursor = 0
        for start, end, replacement in self.changes:
            parts.extend((self.source[cursor:start], replacement))
            cursor = end
        parts.append(self.source[cursor:])
        return "".join(parts)


def normalize_fraction_style(value: str) -> str:
    r"""Use ``\dfrac`` for main fractions and ``\frac`` in scripts/fractions.

    Only complete ``$...$``, ``$$...$$``, ``\(...\)``, ``\[...\]`` and
    standalone math environments are visited. Bare formulas are left unchanged.
    Explicit ``\tfrac``/``\cfrac`` commands, code, text macros, TikZ and content
    locks are preserved. Unbalanced math, custom/unknown commands and unsupported
    style-controlling commands leave their entire math span unchanged;
    the function never writes to storage or adds/removes math delimiters.
    """
    if not isinstance(value, str) or not value:
        return value or ""
    protected: dict[str, str] = {}
    comments: set[str] = set()
    next_codepoint = 0xF0000
    original_characters = set(value)

    def save(original: str) -> str:
        nonlocal next_codepoint
        while chr(next_codepoint) in original_characters:
            next_codepoint += 1
        marker = chr(next_codepoint)
        next_codepoint += 1
        protected[marker] = original
        return marker

    source = _PROTECTED_PATTERN.sub(lambda match: save(match.group()), value)
    parts: list[str] = []
    cursor = 0
    copied = 0
    while cursor < len(source):
        end = cursor
        if source[cursor] == "`" and not _is_escaped(source, cursor):
            opening = _BACKTICKS.match(source, cursor).group()
            end = len(source)
            for closing in _BACKTICKS.finditer(source, cursor + len(opening)):
                if closing.group() == opening:
                    end = closing.end()
                    break
        elif source.startswith("![", cursor) and not _is_escaped(source, cursor):
            bracket_depth = 1
            end = cursor + 2
            while end < len(source) and bracket_depth:
                if not _is_escaped(source, end):
                    if source[end] == "[":
                        bracket_depth += 1
                    elif source[end] == "]":
                        bracket_depth -= 1
                end += 1
            if end < len(source) and source[end] == "(":
                paren_depth = 1
                end += 1
                while end < len(source) and paren_depth:
                    if not _is_escaped(source, end):
                        if source[end] == "(":
                            paren_depth += 1
                        elif source[end] == ")":
                            paren_depth -= 1
                    end += 1
        elif source[cursor] == "%" and not _is_escaped(source, cursor):
            end = source.find("\n", cursor)
            end = len(source) if end < 0 else end
        elif source[cursor] == "\\" and not _is_escaped(source, cursor):
            command = _COMMAND.match(source, cursor)
            if command and command.group()[1:] in _TEXT_COMMANDS:
                argument = command.end()
                if argument < len(source) and source[argument] == "*":
                    argument += 1
                while argument < len(source) and source[argument].isspace():
                    argument += 1
                if command.group() == r"\verb" and argument < len(source):
                    close = source.find(source[argument], argument + 1)
                    end = len(source) if close < 0 else close + 1
                elif argument < len(source) and source[argument] == "{":
                    group = _balanced_group(source, argument)
                    end = len(source) if group is None else group[1]
        if end > cursor:
            marker = save(source[cursor:end])
            if source[cursor] == "%":
                comments.add(marker)
            parts.extend((source[copied:cursor], marker))
            copied = cursor = end
        else:
            cursor += 1
    parts.append(source[copied:])
    source = "".join(parts)

    def normalize_environment(environment: str, name: str) -> str:
        if name not in _STANDALONE_MATH_ENVIRONMENTS:
            return environment
        return save(_MathNormalizer(environment, comments).normalize())

    source = _replace_latex_environments(source, normalize_environment)

    def normalize_delimited(span: str) -> str:
        size = 2 if span.startswith(("$$", r"\(", r"\[")) else 1
        return span[:size] + _MathNormalizer(span[size:-size], comments).normalize() + span[-size:]

    result = _replace_delimited_math(source, normalize_delimited)
    if not protected:
        return result
    marker_pattern = re.compile("[" + chr(0xF0000) + "-" + chr(next_codepoint - 1) + "]")

    def restore(match: re.Match[str]) -> str:
        original = protected.get(match.group())
        return match.group() if original is None else marker_pattern.sub(restore, original)

    return marker_pattern.sub(restore, result)
