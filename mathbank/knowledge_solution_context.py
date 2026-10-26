"""Conservative, local evidence of a supplied solution step.

These lexical checks distinguish explicit result-only material from worked
steps. They do not certify the mathematics or infer an omitted solution.
"""
from __future__ import annotations

import re

from .question_assets import _mask_literals, markdown_literal_ranges


_FIELDS = ("answer_markdown", "review_markdown")
_ORIGINAL_MARKER = "[EXTRACTED_ORIGINAL]"
_ANSWER_HEAD = re.compile(r"^\s*(?:【\s*(?:参考)?答案\s*】|\[\s*(?:参考)?答案\s*\]|(?:参考)?答案\s*[:：])\s*")
_SOLUTION_HEAD = re.compile(r"(?:【\s*(?:解析|证明|解答|分析|详解)\s*】|(?:解析|证明|解答)\s*[:：])")
_IMAGE = re.compile(
    r"!\[(?:\\.|[^\]\\])*\]\([^\n]*?\)"
    r"|<img\b[^>]*>"
    r"|\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{[^{}]*\}", re.IGNORECASE)
_HYPOTHETICAL = re.compile(
    r"(?:若|如果|假设|倘若)\s*(?:改问|改为|改成|换成|把(?:本)?题改|将(?:本)?题改)"
    r"|(?:若|如果|假设|倘若)\s*(?:采用|使用|利用|改用|换用|尝试|考虑用)"
    r"|(?:若|如果|假设|倘若)\s*(?:本题|此题|这道题|题目|第[^。！？\n]{0,15}问)"
    r"[^。！？\n]{0,100}(?:不是|而是|改问|改为|改成|换成|直接让)"
    r"|(?:未采用|仅供参考|尚未使用|可替代)的?(?:另一种)?(?:解法|方法)"
)
_RESUME_ACTUAL = re.compile(r"^\s*(?:【\s*(?:原解|解析|解答|证明)\s*】|回到(?:原题|本题)|原解如下)")
_NEGATED_EXECUTION = re.compile(
    r"(?:并没有|并未|尚未|未曾|没有|无需|无须|不必|不需要|不需|不用|不能|不是|并非|未|不)"
    r"\s*(?:(?:直接|实际|真正|必须|再|去|曾经)\s*)?"
    r"(?:使用|采用|利用|运用|建立|构造|通过)"
)
_CLAUSE_END = re.compile(r"[，；;。！？\n]|而是|而用|但是|不过")
_EXECUTION = re.compile(
    r"(?:使用|采用|利用|根据|代入|配方|化简|整理|相减|相加|相乘|相除|联立|建立|构造)"
    r"[^。！？\n]{1,100}"
    r"|(?:原解|解析|解答|证明)(?:中)?\s*(?:用|运用)[^。！？\n]{1,80}"
    r"|(?:解得|可得|求得|得到|得出|计算得)[^。！？\n]{1,100}"
    r"|(?:因为|由于|由)[^。！？\n]{2,100}(?:所以|因此|故|得|可知|可得|即得)"
    r"|(?:导数|法向量|公差|抽样比|通项公式)(?:为|是|满足)[^。！？\n]{1,100}"
    r"|设[^。！？\n]{0,80}(?:法向量|公差|坐标系)[^。！？\n]{0,80}"
    r"|(?:两边|两式)[^。！？\n]{0,60}(?:乘|除|加|减)"
)
_RESULT_TEXT = re.compile(
    r"^(?:(?:[A-DＡ-Ｄ]+(?:[、,， /]+[A-DＡ-Ｄ]+)*)|"
    r"(?:正确|错误|对|错|是|否|不存在|无解|空集))$", re.IGNORECASE)
_MATH_COMMAND = re.compile(r"\\[A-Za-z]+\*?")
_MATH_FRAGMENT = re.compile(
    r"\$\$([^$]{1,500})\$\$|(?<![\\$])\$(?!\$)([^$\n]{1,500})\$(?!\$)"
    r"|\\\(([\s\S]{1,500}?)\\\)|\\\[([\s\S]{1,500}?)\\\]")
_ALGEBRA_OPERATION = re.compile(r"[+*/^−-]|\\(?:d?frac|tfrac|sqrt|times|cdot|div)\b")
_FOLLOWING_CONCLUSION = re.compile(
    r"\s*[,，;；。]?\s*(?:故|因此|所以|于是|从而|进而)([^。！？\n]{1,160})")


def _mask_material(source: str) -> str:
    """Keep source offsets while excluding literals, images and hypotheticals."""
    chars = list(_mask_literals(source, tex_comments=False))
    for start, end in markdown_literal_ranges(source):
        chars[start:end] = ["\n" if char == "\n" else " " for char in source[start:end]]
    value = "".join(chars)
    for match in _IMAGE.finditer(value):
        chars[match.start():match.end()] = [" " for _ in match[0]]
    value = "".join(chars)
    hypothetical = False
    # A note about a changed question can continue over several paragraphs.
    # Only an explicit return to an actual solution ends that discourse scope.
    for match in re.finditer(r"[^\n]*(?:\n|$)", value):
        line = match[0]
        if _RESUME_ACTUAL.search(line):
            hypothetical = False
        marker = _HYPOTHETICAL.search(line)
        if marker:
            hypothetical = True
            start = match.start() + marker.start()
        else:
            start = match.start()
        if hypothetical:
            chars[start:match.end()] = ["\n" if char == "\n" else " "
                                       for char in source[start:match.end()]]
    # A quote cannot remove a negation and reverse an explicit statement that
    # a method was not used. End at a clause/contrast so the positive half of
    # “没有采用导数，而是利用函数单调性” remains inspectable. ASCII commas are
    # not boundaries here: they often belong to mathematical coordinates.
    value = "".join(chars)
    for match in _NEGATED_EXECUTION.finditer(value):
        boundary = _CLAUSE_END.search(value, match.end())
        end = boundary.start() if boundary else len(value)
        chars[match.start():end] = [" " for _ in source[match.start():end]]
    return "".join(chars)


def _without_protocol(value: str) -> str:
    stripped = value.lstrip()
    if stripped.startswith(_ORIGINAL_MARKER):
        stripped = stripped[len(_ORIGINAL_MARKER):]
    return stripped.strip()


def _result_only(value: str) -> bool:
    """Recognize result syntax, not a guessed short-answer length threshold."""
    text = value.strip().strip("。．.，,;； \t\r\n")
    if not text:
        return True
    if _RESULT_TEXT.fullmatch(text):
        return True
    # A result may be one number, a set, an interval, a vector or several
    # variable assignments. Chinese prose must not disappear with math macros.
    if re.search(r"[\u3400-\u9fff]", text):
        return False
    if (not re.search(r"[0-9$\\{}\[\]()+*/=^_<>≤≥≠∞∈−+-]", text)
            and not re.fullmatch(r"[A-Za-z]", text)):
        return False
    clean = _MATH_COMMAND.sub("", text)
    clean = re.sub(r"(?:\$|[{}\[\](),，;；.:：|<>+*/=^_!&%\\\s\-−±≤≥≠∞∈∪∩])", "", clean)
    return bool(re.fullmatch(r"[A-Za-z0-9]*", clean))


def _formula_progression(value: str) -> bool:
    # Require linked equalities/implications, not two independent final values.
    compact = value.replace("&", "")
    return bool(re.search(r"=[^=,，;；\n]{1,160}=[^=\n]{1,160}", compact)
                or re.search(r"(?:\\(?:Rightarrow|implies|Longrightarrow)|⇒|∴)[^。\n]{2,160}", compact))


def _result_statement(value: str) -> bool:
    """Result-introducing words alone do not demonstrate a method or step."""
    text = _without_protocol(value)
    head = _ANSWER_HEAD.match(text) or _SOLUTION_HEAD.match(text)
    if head:
        text = text[head.end():]
    intro = re.match(r"^\s*(?:(?:故|因此|所以|于是|从而)\s*(?:解得|可得|求得|得到|得出|计算得)?"
                     r"|(?:解得|可得|求得|得到|得出|计算得))\s*[:：]?\s*", text)
    if intro is None:
        return False
    remainder = text[intro.end():]
    remainder = re.sub(r"^(?:参数(?:值)?|所求(?:的)?(?:值|结果)|结果|答案)\s*(?:为|是|等于|[:：])\s*", "", remainder)
    calculation = re.search(r"=[^。\n]{0,160}(?:\\(?:times|cdot)|[+*/])[^。\n]{1,160}", remainder)
    return _result_only(remainder) and not _formula_progression(remainder) and not calculation


def _short_formula_step_ranges(value: str) -> list[tuple[int, int]]:
    """Find a structural rewrite followed by an actual visible conclusion.

    A solution heading or a final factored expression alone is insufficient.
    Both sides must contain operations and share a symbol; assignments,
    independent result lists and equations ending in zero do not qualify.
    Callers mask hypothetical/literal material before asking for these ranges.
    """
    ranges = []
    for match in _MATH_FRAGMENT.finditer(value):
        group = next(index for index in range(1, 5) if match.group(index) is not None)
        formula = match.group(group).replace("&", "").strip()
        if formula.count("=") != 1:
            continue
        left, right = (part.strip() for part in formula.split("="))
        if (not left or not right or left[-1] in "<>!" or left == right
                or not _ALGEBRA_OPERATION.search(left) or not _ALGEBRA_OPERATION.search(right)):
            continue
        left_symbols = set(re.findall(r"[A-Za-z]", _MATH_COMMAND.sub("", left)))
        right_symbols = set(re.findall(r"[A-Za-z]", _MATH_COMMAND.sub("", right)))
        if not left_symbols.intersection(right_symbols):
            continue
        conclusion = _FOLLOWING_CONCLUSION.match(value, match.end())
        if conclusion and conclusion.group(1).strip():
            ranges.append(match.span(group))
    return ranges


def _has_step(value: str) -> bool:
    if _result_statement(value):
        return False
    return bool(_EXECUTION.search(value) or _formula_progression(value)
                or _short_formula_step_ranges(value))


def _quoted_math_step(value: str) -> bool:
    # Inside an already evidenced solution, an evaluated derivative or an
    # arithmetic substitution can itself be a step. A bare result ``x=5``
    # still does not explain which auxiliary method was used.
    return bool("=" in value and (
        re.search(r"(?:'|\\prime)[^=\n]{0,60}=", value)
        or re.search(r"^\s*(?:故|因此|所以|于是|从而|可知)[^。\n]{2,160}=", value)
        or re.search(r"=[^。\n]{0,160}(?:\\(?:times|cdot)|[+*/])[^。\n]{1,160}", value)))


def _field_state(source) -> str:
    if not isinstance(source, str) or not _without_protocol(source):
        return "absent"
    visible = _without_protocol(_mask_material(source))
    if not visible:
        # There is supplied material, but no inspectable actual derivation.
        return "answer_only" if _IMAGE.search(source) else "uncertain"
    if _result_statement(visible):
        return "answer_only"
    answer_head = _ANSWER_HEAD.match(visible)
    if answer_head:
        remainder = visible[answer_head.end():]
        if not _SOLUTION_HEAD.search(remainder) and _result_only(remainder):
            return "answer_only"
    if _has_step(visible):
        return "reasoning"
    if _result_only(visible):
        return "answer_only"
    return "uncertain"


def solution_context(question: dict) -> dict:
    """Report source states; ``reasoning`` means observable steps, not truth."""
    states = {field: _field_state(question.get(field)) for field in _FIELDS}
    return {**states, "has_reasoning": "reasoning" in states.values()}


def image_reference_is_actual(source: str, reference: str) -> bool:
    """A bound image cannot escape a hypothetical/negated source frame.

    This only checks where the image was supplied. Its OCR still needs a
    separately inspectable actual step and never becomes verified pixel text.
    """
    if not isinstance(source, str) or not isinstance(reference, str) or not reference:
        return False
    chars, image_starts = list(source), set()
    for match in _IMAGE.finditer(source):
        image_starts.add(match.start())
        chars[match.start():match.end()] = ["\n" if char == "\n" else " " for char in match[0]]
        # Hide alt/title words before interpreting the source frame; a fixed
        # same-offset sentinel allows the normal literal/discourse mask to
        # decide whether the image itself was in an actual solution region.
        chars[match.start()] = "图"
    visible = _mask_material("".join(chars))
    start = source.find(reference)
    while start >= 0:
        if start in image_starts and visible[start] == "图":
            return True
        start = source.find(reference, start + 1)
    return False


def evidence_is_reasoning(source: str, evidence: str) -> bool:
    """Accept only an exact quote containing an inspectable actual step.

    Generic quotations from a result, a literal example, or a hypothetical
    alternative cannot acquire step status from other text in the same field.
    """
    if (not isinstance(source, str) or not isinstance(evidence, str)
            or not evidence.strip() or _result_statement(evidence) or _field_state(source) != "reasoning"):
        return False
    visible = _mask_material(source)
    formula_steps = _short_formula_step_ranges(visible)
    start = source.find(evidence)
    while start >= 0:
        if (visible[start:start + len(evidence)] == evidence
                and (_has_step(evidence) or _quoted_math_step(evidence)
                     or any(start <= left and right <= start + len(evidence)
                            for left, right in formula_steps))):
            return True
        start = source.find(evidence, start + 1)
    return False
