"""Lossless source-fragment locks for AI-assisted document splitting.

The model sees formulas in their original sentences and is asked for stable
references. ID-less output is checked against local source positions before
restoring exact source bytes; uncertain results remain available for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter, defaultdict
import re
import unicodedata
from typing import Any


_LOCK_TAG = "mathbank-math"


class ContentLockIntegrityError(ValueError):
    """Raised when a splitting response loses or duplicates protected content."""


@dataclass(frozen=True)
class ContentLock:
    lock_id: str
    original: str


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _find_math_end(value: str, start: int, delimiter: str) -> int:
    cursor = start + len(delimiter)
    while cursor < len(value):
        end = value.find(delimiter, cursor)
        if end < 0:
            return -1
        if not _is_escaped(value, end):
            return end + len(delimiter)
        cursor = end + len(delimiter)
    return -1


_MATH_ENVIRONMENTS = (
    "equation", "equation*", "align", "align*", "gather", "gather*",
    "aligned", "alignedat", "gathered", "split", "multline", "multline*",
    "eqnarray", "eqnarray*", "displaymath", "math", "array", "cases",
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix",
    "smallmatrix",
)


def _next_math_span(source: str, cursor: int) -> tuple[int, int] | None:
    candidates: list[tuple[int, str, str]] = []
    for opening, closing in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        start = source.find(opening, cursor)
        while start >= 0 and _is_escaped(source, start):
            start = source.find(opening, start + len(opening))
        if start >= 0:
            candidates.append((start, opening, closing))
    for environment in _MATH_ENVIRONMENTS:
        opening = rf"\begin{{{environment}}}"
        start = source.find(opening, cursor)
        while start >= 0 and _is_escaped(source, start):
            start = source.find(opening, start + len(opening))
        if start >= 0:
            candidates.append((start, opening, rf"\end{{{environment}}}"))
    if not candidates:
        return None
    start, opening, closing = min(candidates, key=lambda item: (item[0], -len(item[1])))
    end = _find_math_end(source, start, closing)
    if end < 0:
        return None
    return start, end


def _is_choice_answer_blank(source: str, start: int, end: int) -> bool:
    """The empty answer bracket immediately before choices is layout, not math."""
    value = source[start:end].strip()
    for opening, closing in (('$$', '$$'), ('$', '$'), (r'\(', r'\)'), (r'\[', r'\]')):
        if value.startswith(opening) and value.endswith(closing):
            value = value[len(opening):-len(closing)]
            break
    value = re.sub(r'\\(?:left|right)\b', '', value)
    spacing = r'(?:(?:\\(?:quad|qquad|enspace|thinspace|space)\b|\\[,;:! ])\s*)+'
    if re.fullmatch(r'\s*' + spacing, value):
        return bool(re.search(r'[（(][ \t]*$', source[:start]) and re.match(
            r'\s*[）)][ \t]*[。．.]?\s*(?:\\begin\{choices\}|(?:\*\*)?A[.．、])', source[end:]
        ))
    if not re.fullmatch(r'\(\s*(?:(?:\\(?:quad|qquad|enspace|thinspace|space)\b|\\[,;:! ])\s*)*\)', value):
        return False
    return bool(re.match(r'\s*[。．.]?\s*(?:\\begin\{choices\}|(?:\*\*)?A[.．、])', source[end:]))


def _strip_choice_answer_blanks(value: str) -> str:
    spans = []
    cursor = 0
    while cursor < len(value):
        span = _next_math_span(value, cursor)
        if span is None:
            break
        if _is_choice_answer_blank(value, *span):
            spans.append(span)
        cursor = span[1]
    for start, end in reversed(spans):
        value = value[:start] + value[end:]
    return value


def lock_visible_math(value: str, scope: str) -> tuple[str, list[ContentLock]]:
    """Wrap common balanced TeX math forms while keeping formulas visible."""
    source = str(value or "")
    safe_scope = re.sub(r"[^A-Za-z0-9_-]", "", str(scope or "DOCX"))[:48] or "DOCX"
    parts: list[str] = []
    locks: list[ContentLock] = []
    cursor = 0
    formula_index = 0
    metadata_ranges = _page_footer_ranges(source) + _preamble_instruction_ranges(source)

    while cursor < len(source):
        span = _next_math_span(source, cursor)
        if span is None:
            parts.append(source[cursor:])
            break
        start, end = span
        original = source[start:end]
        if (not original.strip() or _is_choice_answer_blank(source, start, end)
                or any(left <= start and end <= right for left, right in metadata_ranges)):
            parts.append(source[cursor:end])
            cursor = end
            continue

        formula_index += 1
        lock_id = f"MBM_{safe_scope}_{formula_index:04d}"
        parts.append(source[cursor:start])
        parts.append(f'<{_LOCK_TAG} id="{lock_id}">{original}</{_LOCK_TAG}>')
        locks.append(ContentLock(lock_id=lock_id, original=original))
        cursor = end

    return "".join(parts), locks


def _restore_lock_in_text(value: str, lock: ContentLock) -> tuple[str, int, bool]:
    text = str(value or "")
    token_pattern = re.compile(r"\[\[\s*" + re.escape(lock.lock_id) + r"\s*\]\]")
    tag_pattern = re.compile(
        rf"<{_LOCK_TAG}\b[^>]*\bid\s*=\s*(['\"])"
        + re.escape(lock.lock_id)
        + rf"\1[^>]*>(.*?)</{_LOCK_TAG}\s*>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    token_matches = list(token_pattern.finditer(text))
    tag_matches = list(tag_pattern.finditer(text))
    count = len(token_matches) + len(tag_matches)
    modified = any(match.group(2) != lock.original for match in tag_matches)
    if count == 1:
        if token_matches:
            text = token_pattern.sub(lambda _match: lock.original, text, count=1)
        else:
            text = tag_pattern.sub(lambda _match: lock.original, text, count=1)
    return text, count, modified


def restore_visible_math(
    questions: list[dict[str, Any]],
    locks: list[ContentLock],
) -> dict[str, int]:
    """Restore every lock exactly once across question content and original answers."""
    report = {
        "math_locks_created": len(locks),
        "math_locks_restored": 0,
        "math_locks_overwritten": 0,
        "math_locks_missing": 0,
        "math_locks_duplicated": 0,
    }
    missing: list[str] = []
    duplicated: list[str] = []

    # Work on copies: a later missing ID must not leave earlier formulas restored.
    staged = [dict(question) for question in questions]
    for lock in locks:
        occurrences: list[tuple[dict[str, Any], str, str, int, bool]] = []
        total = 0
        for question in staged:
            for field in ("content", "answer_markdown"):
                field_value = question.get(field, "")
                if not isinstance(field_value, str):
                    continue
                restored, count, modified = _restore_lock_in_text(field_value, lock)
                if count:
                    occurrences.append((question, field, restored, count, modified))
                    total += count
        if total == 0:
            missing.append(lock.lock_id)
            continue
        if total != 1:
            duplicated.append(lock.lock_id)
            continue
        question, field, restored, _count, modified = occurrences[0]
        question[field] = restored
        report["math_locks_restored"] += 1
        if modified:
            report["math_locks_overwritten"] += 1

    report["math_locks_missing"] = len(missing)
    report["math_locks_duplicated"] = len(duplicated)
    if missing or duplicated:
        details = []
        if missing:
            details.append("丢失 " + ", ".join(missing[:6]))
        if duplicated:
            details.append("重复 " + ", ".join(duplicated[:6]))
        raise ContentLockIntegrityError(
            "AI 拆题时未完整保留公式定位标记（" + "；".join(details) + "），"
            "系统已停止本次结果，避免公式静默丢失或串题。"
        )
    for question, restored_question in zip(questions, staged):
        question.update(restored_question)
    return report


@dataclass(frozen=True)
class _Formula:
    start: int
    end: int
    formula: str
    lock_id: str | None = None
    overwritten: bool = False


@dataclass(frozen=True)
class _SourcePart:
    text: str
    number: int | None
    field: str
    formulas: tuple[_Formula, ...]
    owner: int | None = None
    source_start: int = 0
    source_end: int = 0


@dataclass(frozen=True)
class _NumericAtom:
    start: int
    end: int
    literal: str
    formula: _Formula | None = None


@dataclass(frozen=True)
class _NumericComparison:
    signature: str
    numbers: tuple[_NumericAtom, ...]
    formulas: tuple[_Formula, ...]


_REFERENCE = re.compile(
    r'<mathbank-math\b[^>]*>.*?</mathbank-math\s*>'
    r'|\[\[\s*MBM_[A-Za-z0-9_-]+\s*\]\]'
    r'|\bMBM_[A-Za-z0-9_-]+\b', re.DOTALL | re.IGNORECASE,
)
_NUMBER = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*)?(?:\\noindent[ \t]*)?'
    r'(?:(?:\\(?:textbf|textit|textrm)[ \t]*\{)|\*\*|__)?[ \t]*(?:【[ \t]*)?'
    r'(?:(?!(?:(?:\\(?:textbf|textit|textrm)[ \t]*\{)|\*\*|__)?[ \t]*\d{1,3}[ \t]*(?:\}|\*\*|__)?[ \t]*[：:])'
    r'(?:题(?:目)?[ \t]*)?(?:(?:\\(?:textbf|textit|textrm)[ \t]*\{)|\*\*|__)?[ \t]*(\d{1,3})'
    r'[ \t]*(?:(?:\}|\*\*|__)[ \t]*)?(?:[.．、](?!\d)|[：:])'
    r'|第[ \t]*(\d{1,3})[ \t]*题[：:]?)'
    r'[ \t]*(?:】[ \t]*)?(?:\}|\*\*|__)?[ \t]*',
    re.MULTILINE,
)
_HEADING = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*)?'
    r'(?:\\(?:section|subsection)\*?[ \t]*\{|\\textbf[ \t]*\{|\*\*|__)?'
    r'(?:[一二三四五六七八九十]+(?:\*\*|__)?[、．.]|参考答案|答案与解析|试题解析|参考解析)',
    re.MULTILINE,
)
_ANSWER_LABEL = r'【(?:[^【】\n]{0,12})?(?:答案|解析|解答|详解|分析)】'
_ANSWER_START = re.compile(
    r'(?:\n\s*|(?=【))(?:' + _ANSWER_LABEL + r'|(?:参考答案|答案|解答|解)[：:])'
    r'|\\begin\{(?:solution|answer)\}',
)
_FOOTER_DOT_CONTENT = r'(?:\\(?:cdot|bullet)(?![A-Za-z])|[·•⋅∙])'
_FOOTER_DOT = (
    r'(?:' + _FOOTER_DOT_CONTENT + r'|\$\$[ \t]*' + _FOOTER_DOT_CONTENT + r'[ \t]*\$\$'
    r'|\$[ \t]*' + _FOOTER_DOT_CONTENT + r'[ \t]*\$'
    r'|\\\([ \t]*' + _FOOTER_DOT_CONTENT + r'[ \t]*\\\)'
    r'|\\\[[ \t]*' + _FOOTER_DOT_CONTENT + r'[ \t]*\\\])'
)
_FOOTER_SUBJECT = r'数[ \t]*(?:[学學](?:[ \t]*[试試][ \t]*[题題卷])?|试)'
_PAGE_FOOTER = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__|\\textbf[ \t]*\{)?'
    r'(?:(?:(?:高|初)[ \t]*[一二三][ \t]*)?' + _FOOTER_SUBJECT
    + r'[ \t]*(?:' + _FOOTER_DOT + r'[ \t]*)?)?'
    r'第[ \t]*\d+[ \t]*页[ \t]*(?:[/／][ \t]*共[ \t]*\d+[ \t]*页'
    r'|[（(][ \t]*共[ \t]*\d+[ \t]*页[ \t]*[）)])'
    r'(?:\*\*|__|\})?[ \t]*$', re.MULTILINE,
)
_LEADING_SCORE = re.compile(r'[ \t\r\n]*[（(][ \t]*\d{1,3}[ \t]*分[ \t]*[）)](?=[ \t\r\n]|$)')
_NOTICE_HEADING = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__|\\textbf[ \t]*\{)?'
    r'(?:注意事项|考生须知|答题须知)[ \t]*[:：]?(?:\*\*|__|\})?[ \t]*$', re.MULTILINE,
)
_QUESTION_SECTION = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__|\\textbf[ \t]*\{)?'
    r'[一二三四五六七八九十]+[、．.][ \t]*'
    r'(?:单项选择题|多项选择题|单选题|多选题|选择题|填空题|解答题)', re.MULTILINE,
)


def _page_footer_ranges(value: str) -> list[tuple[int, int]]:
    """Recognize whole footer lines, never an outer mathematical expression.

    A subject label, an optional *single separator*, and standard page counts
    form the contract. Only that separator may be in math delimiters. A real
    multiplication, an unknown subject or surrounding prose cannot match.
    """
    matches = list(_PAGE_FOOTER.finditer(value))
    if not matches:
        return []
    spans, cursor = [], 0
    while cursor < len(value):
        span = _next_math_span(value, cursor)
        if span is None:
            break
        spans.append(span)
        cursor = span[1]
    # A quoted code/example block can legitimately contain a footer-looking
    # line as its subject matter. Its line is not physical page metadata.
    literal_ranges = []
    opened = None
    for token in re.finditer(r'^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$', value, re.MULTILINE):
        fence = token.group(1)
        if opened is None:
            opened = (token.start(), fence[0], len(fence))
        elif fence[0] == opened[1] and len(fence) >= opened[2]:
            literal_ranges.append((opened[0], token.end()))
            opened = None
    if opened:
        literal_ranges.append((opened[0], len(value)))
    for token in re.finditer(r'\\begin\{(verbatim\*?|Verbatim|lstlisting|minted)\}', value):
        closing = re.search(r'\\end\{' + re.escape(token.group(1)) + r'\}', value[token.end():])
        literal_ranges.append((token.start(), token.end() + closing.end() if closing else len(value)))
    return [(match.start(), match.end()) for match in matches
            if not any(start < match.start() < end or start < match.end() < end for start, end in spans)
            and not any(start <= match.start() and match.end() <= end for start, end in literal_ranges)]


def _comparison_layout(value: str) -> str:
    """Mask proven paper metadata in a same-length comparison copy only."""
    for start, end in reversed(_page_footer_ranges(value)):
        value = value[:start] + ' ' * (end - start) + value[end:]
    stripped = value.lstrip()
    number = _NUMBER.match(stripped)
    if number:
        score = _LEADING_SCORE.match(value, len(value) - len(stripped) + number.end())
        if score:
            value = value[:score.start()] + ' ' * len(score.group()) + value[score.end():]
    return value


def _question_metadata_layout(value: str, question: dict[str, Any]) -> str:
    """Mask verified metadata only when it was actually moved out of the stem."""
    number = _NUMBER.match(value.lstrip())
    if number is None:
        return value
    cursor = len(value) - len(value.lstrip()) + number.end()
    output = str(question.get('content') or '').lstrip()
    output_number = _NUMBER.match(output)
    output_cursor = output_number.end() if output_number else 0
    type_labels = {'单选题': 'single_choice', '单项选择题': 'single_choice',
                   '多选题': 'multi_choice', '多项选择题': 'multi_choice',
                   '填空题': 'fill_in_blank', '解答题': 'detailed_answer'}
    for _ in range(2):
        label = re.match(r'[ \t]*[（(]([^\n（）()]{1,100})[）)][ \t]*', value[cursor:])
        if label is None:
            break
        title = label.group(1).strip()
        expected_type = type_labels.get(title)
        type_matches = expected_type is not None and question.get('question_type') == expected_type
        source_matches = (
            re.fullmatch(r'(?:19|20)\d{2}[ \t]*[·•・.．—\-][ \t]*[\u3400-\u9fffA-Za-z0-9 ·•・.．—\-]{2,90}', title)
            and re.search(r'(?:月考|期中|期末|联考|高考|中考|竞赛|模拟|测试)(?:数学)?(?:试题|试卷|卷)?$', title)
            and re.search(r'中学|附中|附校|[一二三四五六七八九十百\d]+中|高中|初中|小学|学校|高[一二三]|初[一二三]|高考|中考|全国(?:卷|联考)', title)
            and not re.search(r'已知|满足|至少|至多|共有|元素|人数|每[人班组]|求|若|则|且', title)
            and not re.search(r'[零一二三四五六七八九十百千万\d]+[ \t]*(?:维|元|次|个|人|分)', title)
            and re.sub(r'\s+', '', str(question.get('source') or '')) == re.sub(r'\s+', '', title)
        )
        if not type_matches and not source_matches:
            break
        end = cursor + label.end()
        retained = re.match(r'[ \t]*[（(]([^\n（）()]{1,100})[）)][ \t]*', output[output_cursor:])
        if retained is not None and re.sub(r'\s+', '', retained.group(1)) == re.sub(r'\s+', '', title):
            # Keep identical visible prefixes on both sides of strict text,
            # formula and image-position comparison. The metadata field being
            # populated does not imply that the model removed the prefix.
            cursor = end
            output_cursor += retained.end()
            continue
        value = value[:cursor] + ' ' * (end - cursor) + value[end:]
        cursor = end
    return value


def _administrative_numeric_view(body: str) -> str | None:
    """Project only delimited positive integer atoms for template validation.

    This primitive deliberately does not call _formulas or preamble helpers:
    those helpers use the resulting administrative ranges when reading locks.
    Expressions, variables, signs and partial math stay ordinary source data.
    """
    spans, cursor = [], 0
    while cursor < len(body):
        span = _next_math_span(body, cursor)
        if span is None:
            break
        start, end = span
        raw, number = body[start:end], None
        for opening, closing in (('$$', '$$'), ('$', '$'), (r'\(', r'\)'), (r'\[', r'\]')):
            if raw.startswith(opening) and raw.endswith(closing):
                inner = raw[len(opening):-len(closing)].strip()
                if re.fullmatch(r'[1-9][0-9]*', inner):
                    number = inner
                break
        if number is None:
            return None
        spans.append((start, end, number))
        cursor = end
    for start, end, number in reversed(spans):
        body = body[:start] + number + body[end:]
    return body


def _is_exam_administration(body: str) -> bool:
    """Match complete administrative statements, not arbitrary preamble prose."""
    comparison = _administrative_numeric_view(body)
    if comparison is None:
        return False
    plain = re.sub(r'\s+', '', comparison)
    patterns = (
        # Paper composition, full score and time limit. Extra questions after
        # these clauses do not match the complete statement.
        r'(?:本试卷(?:(?:分试题卷(?:和|与)答题卷两部分|共\d+页)[。.,，]?)?)?'
        r'(?:满分|总分)\d+分[，,。.]?(?:考试时间|考试用时|用时)\d+分钟[。.!！]?',
        r'(?:请)?(?:用|使用)黑色(?:字迹的)?(?:钢笔(?:或签字笔)?|签字笔)'
        r'在(?:答题卡|答题纸)(?:上)?(?:指定的?|相应的?)(?:答题)?区域'
        r'(?:[（(]黑色边框[）)])?内(?:作答|答题)'
        r'(?:[，,]超出(?:答题)?区域的?(?:作答|答案)无效)?[。.!！]?',
        r'考试结束(?:后)?[，,]?(?:只需|只须|只|请|须|必须)?(?:上交|交回|交)答题卡[。.!！]?',
    )
    return any(re.fullmatch(pattern, plain) for pattern in patterns)


def _preamble_instruction_ranges(source: str) -> list[tuple[int, int]]:
    """Ignore explicit response instructions inside a bounded exam preamble.

    Both the standalone notice heading and the first real question-section
    heading are required. Strict templates may contain delimited positive
    integer metadata. All other formulas and unknown paragraphs remain source
    evidence. Returned coordinates always refer to the untouched source.
    """
    section = _QUESTION_SECTION.search(source)
    if section is None:
        return []
    notice = next((match for match in _NOTICE_HEADING.finditer(source, 0, section.start())), None)
    if notice is None or _NUMBER.search(source, 0, notice.start()):
        return []
    numbers = list(_NUMBER.finditer(source, notice.end(), section.start()))
    ignored = []
    for index, number in enumerate(numbers):
        end = numbers[index + 1].start() if index + 1 < len(numbers) else section.start()
        body = source[number.end():end].strip()
        if ((re.match(r'(?:非)?选择题的作答[ \t]*[:：]', body)
                and '答题卡' in body and _next_math_span(body, 0) is None) or _is_exam_administration(body)):
            ignored.append((number.start(), end))
    return ignored


def _preamble_instruction_starts(source: str) -> set[int]:
    return {start for start, _ in _preamble_instruction_ranges(source)}


def _formulas(value: str, by_id: dict[str, ContentLock] | None = None) -> list[_Formula]:
    """Read model references before math so a tagged formula is one occurrence."""
    references = list(_REFERENCE.finditer(value)) if by_id is not None else []
    by_id = by_id or {}
    result: list[_Formula] = []
    cursor = 0
    metadata_ranges = _page_footer_ranges(value) + _preamble_instruction_ranges(value)
    for reference in [*references, None]:
        limit = reference.start() if reference else len(value)
        while cursor < limit:
            span = _next_math_span(value[:limit], cursor)
            if span is None:
                break
            start, end = span
            if (not _is_choice_answer_blank(value, start, end)
                    and not any(left <= start and end <= right for left, right in metadata_ranges)):
                result.append(_Formula(start, end, value[start:end]))
            cursor = end
        if reference is None:
            break
        raw = reference.group()
        id_match = re.search(r'MBM_[A-Za-z0-9_-]+', raw)
        lock_id = id_match.group() if id_match else '__unknown_reference__'
        lock = by_id.get(lock_id)
        inner = re.search(r'>(.*?)</', raw, re.DOTALL)
        result.append(_Formula(
            reference.start(), reference.end(),
            lock.original if lock else '〔无法识别的公式来源，请对照原文〕',
            lock_id, bool(lock and inner and inner.group(1) != lock.original),
        ))
        cursor = reference.end()
    return result


def _math_key(value: str) -> tuple[str, ...]:
    """Only presentation changes; never simplify signs, braces or expressions."""
    value = value.strip()
    for opening, closing in (('$$', '$$'), ('$', '$'), (r'\(', r'\)'), (r'\[', r'\]')):
        if value.startswith(opening) and value.endswith(closing):
            value = value[len(opening):-len(closing)]
            break
    # Tokenize control words before removing spaces: ``\sin x`` must never
    # compare equal to the different command ``\sinx``. Text macro arguments
    # retain their spaces and nested braces verbatim.
    tokens: list[str] = []
    cursor = 0
    token_pattern = re.compile(r'\\[A-Za-z]+|\\.|[^\s]', re.DOTALL)
    while cursor < len(value):
        match = token_pattern.search(value, cursor)
        if not match:
            break
        token = match.group()
        cursor = match.end()
        if re.fullmatch(r'\\(?:text[A-Za-z]*|mbox|hbox|operatorname)', token):
            opening = cursor
            while opening < len(value) and value[opening].isspace():
                opening += 1
            if opening < len(value) and value[opening] == '{':
                end, depth = opening + 1, 1
                while end < len(value) and depth:
                    if not _is_escaped(value, end):
                        depth += (value[end] == '{') - (value[end] == '}')
                    end += 1
                if depth == 0:
                    token += value[opening:end]
                    cursor = end
        tokens.append(r'\frac' if token == r'\dfrac' else token)
    return tuple(tokens)


def _plain_key(value: str) -> str:
    """Discard layout while retaining words, numbers, and option positions."""
    # Older valid JSON responses sometimes encoded the layout macro \fillin
    # with a single backslash, decoding \f into U+000C. Compare that exact
    # broken blank marker as the same macro; never alter the source evidence
    # or repair unrelated controls, words, or mathematical commands here.
    value = re.sub(r'\x0cillin(?![A-Za-z])', lambda _: r'\fillin', value)
    value = _comparison_layout(value)
    value = _strip_choice_answer_blanks(value.replace('[EXTRACTED_ORIGINAL]', '')).strip()
    number_match = _NUMBER.match(value)
    if number_match:
        value = value[number_match.end():]
    value = re.sub(r'^\s*\\(?:item|question|qitem)\b(?:\[[^\]]*\])?', '', value)
    value = re.sub(r'!\[[^\]]*\]\([^\n)]*\)', '', value)
    value = re.sub(r'\\includegraphics(?:\[[^\]]*\])?\{[^{}]*\}', '', value)
    value = re.sub(r'\\(?:begin|end)\{(?:choices|enumerate|questions|question|solution|answer)\}(?:\[[^\]]*\])?', '', value)
    value = re.sub(r'(?<![A-Za-z])([A-H])[.．、:：]', lambda m: 'OPTION' + m.group(1), value)
    item_index = 0
    def option(_match: re.Match[str]) -> str:
        nonlocal item_index
        item_index += 1
        return 'OPTION' + chr(64 + item_index)
    value = re.sub(r'\\item(?:\[[^\]]*\])?', option, value)
    value = re.sub(r'\\(?:textbf|textit|textrm|emph|noindent|question|qitem|paren|fillin)\b', '', value)
    value = re.sub(_ANSWER_LABEL + r'|^(?:参考答案|答案|解答|解)[：:]', '', value)
    # Bare numeric data is common in Word paragraphs, not just in math spans.
    # Preserve decimal points and visible operators; changing 20% to 20, or
    # 1.5 to 15, must never be certified as a harmless layout change.
    value = value.translate(str.maketrans({'（': '(', '）': ')', '％': '%', '＋': '+', '－': '-', '＝': '='}))
    value = re.sub(r'\(\s*\)', '', value)
    value = re.sub(r'(?<=\d)[.．](?=\d)', 'MATHBANKDECIMALPOINT', value)
    return re.sub(r'[^\w+\-=<>%‰‱°×÷±∓√∞∈∉∪∩⊂⊆⊃⊇≤≥≠≈∥⊥∠△^/():\[\]]+|_', '', value, flags=re.UNICODE)


def _skeleton(value: str, formulas: list[_Formula] | tuple[_Formula, ...]) -> str:
    parts: list[str] = []
    cursor = 0
    for formula in formulas:
        parts.append(value[cursor:formula.start])
        # A word placeholder survives normalization and preserves each slot.
        parts.append('MATHBANKFORMULASLOT')
        cursor = formula.end
    parts.append(value[cursor:])
    return _plain_key(''.join(parts)).replace('MATHBANKFORMULASLOT', '⟦FORMULA⟧')


_NUMERIC_LITERAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:\\%|%)?')


def _numeric_literal(formula: str) -> str | None:
    value = formula.strip()
    for opening, closing in (('$$', '$$'), ('$', '$'), (r'\(', r'\)'), (r'\[', r'\]')):
        if value.startswith(opening) and value.endswith(closing):
            value = value[len(opening):-len(closing)].strip()
            return value.replace(r'\%', '%') if _NUMERIC_LITERAL.fullmatch(value) else None
    return None


def _numeric_comparison(value: str, formulas: list[_Formula] | tuple[_Formula, ...]) -> _NumericComparison | None:
    """Compare number atoms in prose/math without discarding locks or positions.

    Decimal points, signs, percentages, numeric boundaries and prose positions
    survive. Non-numeric expressions remain ordinary protected formula slots.
    This comparison copy is never returned as user content.
    """
    value = _comparison_layout(value)
    if 'mathbanknumericstart' in value:
        return None
    prefix = _NUMBER.match(value.lstrip())
    if prefix and not value.lstrip()[prefix.end():].strip():
        prefix = None  # A standalone answer "28." is not a question heading.
    cursor = len(value) - len(value.lstrip()) + prefix.end() if prefix else 0
    # This fallback only changes numeric delimiters. The ordinary prose key
    # intentionally discards presentation punctuation; it must not discard an
    # unparsed operator, combining mark or subscript next to a numeric atom.
    # Keep such expressions on the strict comparison path instead.
    prose = []
    prose_cursor = cursor
    for formula in formulas:
        if formula.start < cursor:
            return None
        prose.append(value[prose_cursor:formula.start])
        prose_cursor = formula.end
    prose.append(value[prose_cursor:])
    prose_text = re.sub(
        r'!\[[^\]]*\]\([^\n)]*\)|\\includegraphics(?:\[[^\]]*\])?\{[^{}]*\}',
        '', ''.join(prose),
    )
    if any(char in "_$^*'`·•′″‴⁗" or (ord(char) > 127 and (unicodedata.category(char)[0] in 'SM'
                                                        or unicodedata.category(char) == 'Pd'))
           for char in prose_text):
        return None
    chunks: list[str] = []
    segments: list[tuple[int, int, int, int, _Formula | None]] = []
    other_formulas: list[_Formula] = []
    length = 0

    def append(text: str, start: int, end: int, formula: _Formula | None = None) -> None:
        nonlocal length
        chunks.append(text)
        segments.append((length, length + len(text), start, end, formula))
        length += len(text)

    for formula in formulas:
        if formula.start < cursor:
            return None
        append(value[cursor:formula.start], cursor, formula.start)
        number = _numeric_literal(formula.formula)
        if number is None:
            append('MATHBANKFORMULASLOT', formula.start, formula.end, formula)
            other_formulas.append(formula)
        else:
            append(number, formula.start, formula.end, formula)
        cursor = formula.end
    append(value[cursor:], cursor, len(value))
    flat = ''.join(chunks)
    replacements = []
    atoms: dict[str, _NumericAtom] = {}
    for index, match in enumerate(_NUMERIC_LITERAL.finditer(flat)):
        intersecting = [segment for segment in segments
                        if segment[4] is not None and segment[0] < match.end() and match.start() < segment[1]]
        if len(intersecting) > 1:
            return None  # $2$$8$ must not certify the single original atom 28.
        formula = None
        if intersecting:
            left, right, original_start, original_end, formula = intersecting[0]
            if not (match.start() <= left <= right <= match.end()):
                return None
            if flat[match.start():left] not in ('', '+', '-') or flat[right:match.end()] not in ('', '%', r'\%'):
                return None  # Do not certify partial wrapping such as $2$8.
            start = original_start - (left - match.start())
            end = original_end + (match.end() - right)
        else:
            segment = next((segment for segment in segments if segment[4] is None
                            and segment[0] <= match.start() and match.end() <= segment[1]), None)
            if segment is None:
                return None
            start = segment[2] + match.start() - segment[0]
            end = start + len(match.group())
        # Letter-only IDs keep the number tokenizer from reading its own tokens.
        suffix, ordinal = '', index
        while True:
            suffix = chr(65 + ordinal % 26) + suffix
            ordinal = ordinal // 26 - 1
            if ordinal < 0:
                break
        marker = 'mathbanknumericstart' + suffix + 'mathbanknumericend'
        atoms[marker] = _NumericAtom(start, end, match.group().replace(r'\%', '%'), formula)
        replacements.append((match.start(), match.end(), marker))
    for start, end, marker in reversed(replacements):
        flat = flat[:start] + marker + flat[end:]
    signature = _plain_key(flat).replace('MATHBANKFORMULASLOT', '⟦FORMULA⟧')
    visible_atoms = []

    def restore_atom(match):
        atom = atoms[match.group()]
        visible_atoms.append(atom)
        return '⟦NUMBER:' + atom.literal + '⟧'

    signature = re.sub(r'mathbanknumericstart[A-Z]+mathbanknumericend', restore_atom, signature)
    if sum(atom.formula is not None for atom in visible_atoms) != len(formulas) - len(other_formulas):
        return None  # A layout-removal rule must not hide a numeric source lock.
    return _NumericComparison(signature, tuple(visible_atoms), tuple(other_formulas))


def _image_positions(value: str, formulas: list[_Formula] | tuple[_Formula, ...], *, numeric: bool = False) -> list[tuple[str, str]]:
    """Compare each image's path and its local prose/formula/option position."""
    image_pattern = re.compile(
        r'!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:[ \t]+[^)]*)?\s*\)'
        r'|\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}',
    )
    result = []
    for match in image_pattern.finditer(value):
        prefix = value[:match.start()]
        prefix_formulas = [formula for formula in formulas if formula.end <= match.start()]
        comparison = _numeric_comparison(prefix, prefix_formulas) if numeric else None
        position = comparison.signature if comparison else _skeleton(prefix, prefix_formulas)
        result.append((next(group for group in match.groups() if group is not None), position))
    return result


_BARE_MATH_ALIASES = {
    'le': '≤', 'leq': '≤', 'leqslant': '≤', 'ge': '≥', 'geq': '≥', 'geqslant': '≥', 'lt': '<', 'gt': '>',
    'ne': '≠', 'neq': '≠', 'in': '∈', 'notin': '∉', 'cap': '∩', 'cup': '∪',
    'subseteq': '⊆', 'supseteq': '⊇', 'subset': '⊂', 'supset': '⊃',
    'forall': '∀', 'exists': '∃', 'infty': '∞', 'emptyset': '∅', 'varnothing': '∅',
    'times': '×', 'div': '÷', 'pm': '±', 'mp': '∓', 'mid': '|',
}
_BARE_SCRIPTS = dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ᵢ',
                        ['^' + str(n) for n in range(10)] + ['_' + str(n) for n in range(10)] + ['_i']))
_IMAGE_REFERENCE = re.compile(
    r'!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:[ \t]+[^)]*)?\s*\)'
    r'|\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}',
)


def _bare_math_spelling(value: str) -> str:
    """Only literal Unicode/TeX spellings, never algebra or inferred typography."""
    value = value.translate(str.maketrans({'，': ',', '；': ';', '：': ':', '．': '.',
                                         '（': '(', '）': ')', '∣': '|',
                                         '＋': '+', '－': '-', '＝': '=', '％': '%'}))
    value = ''.join(_BARE_SCRIPTS.get(char, char) for char in value)
    value = re.sub(r'\\([A-Za-z]+)', lambda m: _BARE_MATH_ALIASES.get(m.group(1), m.group()), value)
    value = value.replace(r'\{', '{').replace(r'\}', '}').replace(r'\%', '%')
    # Braces around one script atom do not change that script's extent.
    return re.sub(r'([_^])\{([A-Za-z0-9])\}', r'\1\2', value)


def _real_domain_context(value: str) -> bool:
    """Every R in this question must already denote a written number domain."""
    if re.search(r'向量|矩阵', value):
        return False
    occurrences = list(re.finditer(r'(?<![A-Za-z])R(?![A-Za-z])', value))
    def has_suffix(end: int) -> bool:
        following = value[end:].lstrip()[:1]
        return bool(following) and (
            following in '_^0123456789([（{\'′″‴⁗'
            or unicodedata.category(following).startswith('M')
            or unicodedata.decomposition(following).startswith(('<sub>', '<super>'))
        )
    return bool(occurrences) and all(
        re.search(r'(?:[∈∉]|\\(?:in|notin)\b|(?:定义域|值域)(?:为|是))\s*$', value[:match.start()])
        and not has_suffix(match.end())
        for match in occurrences
    )


def _unwrapped_simple_math(value: str, *, real_domain: bool = False, prefix: str = '') -> str | None:
    for opening, closing in (('$$', '$$'), ('$', '$'), (r'\(', r'\)'), (r'\[', r'\]')):
        if value.startswith(opening) and value.endswith(closing):
            inner = value[len(opening):-len(closing)]
            if real_domain:
                inner = re.sub(r'(\\(?:in|notin)\b\s*|[∈∉]\s*)\\mathbf\s*\{R\}', r'\1 R', inner)
                if re.fullmatch(r'\s*\\mathbf\s*\{R\}\s*', inner) and re.search(r'(?:定义域|值域)(?:为|是)\s*$', prefix):
                    inner = 'R'
            inner = re.sub(r'([_^])\{([A-Za-z0-9])\}', r'\1\2', inner)
            # Raw TeX groups are invisible. Only escaped braces can prove the
            # visible set braces of Word prose (apart from one-atom scripts).
            if re.search(r'(?<!\\)[{}]', inner):
                return None
            inner = _bare_math_spelling(inner)
            inner = re.sub(r'\\(?:fillin|paren)\b', '', inner)
            if re.search(r'[_^](?![A-Za-z0-9])', inner):
                return None
            # Unknown macros, text, font changes, and environments need review.
            if inner.strip() and re.fullmatch(r'[A-Za-z0-9\s,.;:+\-*/=<>!|{}()\[\]_^%≤≥≠∈∉∩∪⊆⊇⊂⊃∀∃∞∅×÷±∓°]+', inner):
                return inner
            return None
    return None


def _delimited_plain_key(value: str) -> str | None:
    """Protect numeric boundaries and every extra math symbol from prose cleanup."""
    value = _strip_choice_answer_blanks(value)
    if 'mathbankdelimited' in value or '$' in value:
        return None
    value = _comparison_layout(value)
    prefix = _NUMBER.match(value)
    if prefix:
        value = value[prefix.end():]
    value = _IMAGE_REFERENCE.sub('', value)
    value = _bare_math_spelling(value)
    value = re.sub(r'(?<![A-Za-z])([A-H])[.．、:：]', lambda m: 'OPTION' + m.group(1), value)
    # Leave known document layout to the established prose normalizer. Unknown
    # naked commands must not turn into ordinary words after slash removal.
    commands = re.findall(r'\\([A-Za-z]+)', value)
    if any(command not in {'begin', 'end', 'item', 'fillin', 'paren', 'noindent'} for command in commands):
        return None
    atoms: dict[str, str] = {}
    def protect(match: re.Match[str]) -> str:
        # Alphabetic IDs cannot be confused with the document's number atoms.
        index, suffix = len(atoms), ''
        while True:
            suffix = chr(65 + index % 26) + suffix
            index = index // 26 - 1
            if index < 0:
                break
        marker = 'mathbankdelimited' + suffix + 'end'
        atoms[marker] = match.group().strip()
        return marker
    def protect_math_atom(match: re.Match[str]) -> str:
        token = match.group().strip()
        # Unicode spaces are matched by the non-ASCII branch. Keep them as
        # separators; indexing an empty stripped token used to abort the task.
        if not token:
            return match.group()
        char = token[0]
        if (char.isdigit() or char in '{},;!、_|^*\'"`.．·•′″‴⁗'
                or unicodedata.category(char)[0] in 'SMN'
                or unicodedata.category(char) == 'Pd'):
            return protect(match)
        return match.group()
    # Structural environment braces are layout; mathematical braces are not.
    value = re.sub(r'\\(?:begin|end)\{(?:choices|enumerate|questions|question|solution|answer)\}', '', value)
    value = re.sub(r'[0-9]+(?:[.．][0-9]+)?|[.．][0-9]+|(?<=[A-Za-z0-9])[ \t]*[.．][ \t]*(?=[A-Za-z0-9])|[{},;!、_|^*\'"`·•′″‴⁗]|[^\x00-\x7f\u3400-\u9fff]',
                   protect_math_atom, value)
    key = _plain_key(value)
    markers = re.compile(r'mathbankdelimited[A-Z]+end')
    return markers.sub(lambda m: '⟦' + atoms[m.group()] + '⟧', key)


def _delimited_prose_layout(value: str, formulas, chosen: dict[int, int]) -> str:
    """Mask only prose commas at two explicit paragraph/formula boundaries."""
    def outside_groups(position: int) -> bool:
        depth = 0
        cursor = 0
        for formula in [*formulas, None]:
            limit = min(position, formula.start) if formula is not None else position
            for char in value[cursor:limit]:
                if char in '([{（［':
                    depth += 1
                elif char in ')]}）］':
                    depth -= 1
                    if depth < 0:
                        return False
            if formula is None or formula.end >= position:
                break
            cursor = formula.end
        return depth == 0
    masks = []
    start = 0
    previous = None
    for formula_index, following in enumerate([*formulas, None]):
        end = following.start if following is not None else len(value)
        prose = value[start:end]
        if following is not None and formula_index in chosen:
            # “满足，$...$” and “满足 $...$”; not a,b, a Chinese math
            # argument, or a separator inside any existing/new formula.
            comma = re.search(r'满足[ \t]*([,，])[ \t]*$', prose)
            if comma and outside_groups(start + comma.start(1)):
                masks.append(start + comma.start(1))
        for comma in re.finditer(r'[,，](?=[ \t]*\n[ \t]*\n\s*(?:则|所以|因此|当|若|由|故|综上))', prose):
            prefix = value[:start + comma.start()].rstrip()
            closing = prefix.endswith((')', ']', '）', '］'))
            if previous is not None and len(prefix) == previous.end:
                inner = _unwrapped_simple_math(previous.formula)
                closing = inner is not None and inner.rstrip().endswith((')', ']'))
            if closing and outside_groups(start + comma.start()):
                masks.append(start + comma.start())
        if following is not None:
            start = following.end
            previous = following
    for index in reversed(masks):
        value = value[:index] + ' ' + value[index + 1:]
    return value


def _delimited_equivalence(source: str, expected: tuple[_Formula, ...], value: str,
                           actual: list[_Formula], *, locate_only: bool = False,
                           context: str | None = None, allow_layout_only: bool = False) -> list[tuple[_Formula, _Formula]] | None:
    """Allow extra delimiters around bare math only with a unique local alignment.

    Existing source formulas stay atomic and keep strict math keys/IDs. The
    additional model formulas may only re-encode the exact visible bare text.
    This comparison never edits source text, source offsets, or extra formulas.
    """
    if len(source) > 50000 or len(value) > 50000 or len(actual) > 500 or len(expected) > 500:
        return None
    # This path only addresses additional delimiters. It must not use formula
    # values to disambiguate existing identical-prose source questions.
    if (len(actual) < len(expected) or len(actual) == len(expected) and not allow_layout_only
            or 'MATHBANKLOCKSLOT' in source or 'MATHBANKLOCKSLOT' in value):
        return None
    keys = [_math_key(formula.formula) for formula in actual]
    real_domain = _real_domain_context(source if context is None else context)
    paths: list[list[int]] = [[]]
    for formula in expected:
        next_paths = []
        key = _math_key(formula.formula)
        for path in paths:
            next_paths.extend([*path, index] for index in range(path[-1] + 1 if path else 0, len(actual))
                              if locate_only or keys[index] == key)
            if len(next_paths) > 32:
                return None
        paths = next_paths
        if not paths:
            return None
    def projection(text: str, formulas, chosen: dict[int, int]) -> str | None:
        text = _delimited_prose_layout(text, formulas, chosen)
        chunks, cursor = [], 0
        for index, formula in enumerate(formulas):
            chunks.append(text[cursor:formula.start])
            if index in chosen:
                chunks.append('MATHBANKLOCKSLOT' + chr(65 + chosen[index] // 26) + chr(65 + chosen[index] % 26))
            else:
                plain = _unwrapped_simple_math(formula.formula, real_domain=real_domain,
                                               prefix=text[:formula.start])
                if plain is None or formula.lock_id:
                    return None
                if text[:formula.start].rstrip().endswith(('^', '_')) or text[formula.end:].lstrip().startswith(('^', '_')):
                    return None
                # Keep number atoms on either side of a delimiter separate:
                # neither $2$8 nor $2$$8$ proves the original literal 28.
                chunks.append(' ' + plain + ' ')
            cursor = formula.end
        chunks.append(text[cursor:])
        return _delimited_plain_key(''.join(chunks))
    expected_key = projection(source, expected, {index: index for index in range(len(expected))})
    if expected_key is None:
        return None
    matches = []
    for path in paths:
        chosen = {index: slot for slot, index in enumerate(path)}
        if projection(value, actual, chosen) != expected_key:
            continue
        source_images, actual_images = [], []
        for text, formulas, selection, target in ((source, expected, {i: i for i in range(len(expected))}, source_images),
                                                 (value, actual, chosen, actual_images)):
            for image in _IMAGE_REFERENCE.finditer(text):
                before = [f for f in formulas if f.end <= image.start()]
                target.append((next(group for group in image.groups() if group is not None),
                               projection(text[:image.start()], before, {i: slot for i, slot in selection.items() if i < len(before)})))
        if source_images == actual_images:
            matches.append([(formula, actual[index]) for formula, index in zip(expected, path)])
        if len(matches) > 1:
            return None
    return matches[0] if len(matches) == 1 else None


def _answer_table_comparison_layout(source: str) -> str:
    """Mask only a complete one-cell table whose sole payload starts an answer.

    Some Word exports put just the explanation in a one-cell border. Keep all
    payload bytes/offsets; multiple columns/rows, merged or nested tables and
    tables containing a stem never qualify for this comparison-only mask.
    """
    table = re.compile(r'\\begin\{tabular\}\{\|?[clr]\|?\}([\s\S]*?)\\end\{tabular\}')
    protected = [(formula.start, formula.end) for formula in _formulas(source)]
    protected.extend((match.start(), match.end()) for match in re.finditer(r'```[\s\S]*?```', source))
    table_events = list(re.finditer(r'\\(begin|end)\{(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)\}', source))
    masks = []
    for match in table.finditer(source):
        if any(left <= match.start() < right for left, right in protected):
            continue
        ancestors = []
        malformed = False
        for event in table_events:
            if event.start() >= match.start():
                break
            action, name = event.groups()
            if action == 'begin':
                ancestors.append(name)
            elif not ancestors or ancestors.pop() != name:
                malformed = True
        if ancestors or malformed:
            continue
        start, end = match.start(1), match.end(1)
        while start < end and source[start].isspace():
            start += 1
        if source.startswith(r'\hline', start):
            start += len(r'\hline')
        while start < end and source[start].isspace():
            start += 1
        while end > start and source[end - 1].isspace():
            end -= 1
        if source[:end].endswith(r'\hline'):
            end -= len(r'\hline')
        while end > start and source[end - 1].isspace():
            end -= 1
        if source[:end].endswith('\\\\'):
            end -= 2
        while end > start and source[end - 1].isspace():
            end -= 1
        body = source[start:end]
        if not re.match(r'(?:' + _ANSWER_LABEL + r'|(?:参考答案|答案|解答|解)[：:])', body):
            continue
        if re.search(r'&|\\\\|\\(?:hline|multicolumn|multirow|begin|end)\b', body):
            continue
        if _NUMBER.search(body):
            continue
        masks.extend(((match.start(), start), (end, match.end())))
    for start, end in reversed(sorted(masks)):
        source = source[:start] + ' ' * (end - start) + source[end:]
    return source


def _comparable_answer(value: str, source: str) -> str:
    """Ignore a redundant plain summary only when the source explicitly says it.

    PDF splitters commonly prepend ``C`` or ``19`` to otherwise verbatim source
    explanations. This is not a license to derive an answer, ignore a conflicting
    result, or drop the explanation. Spaces preserve every formula/image offset.
    """
    token = r'(?:[A-H]{1,8}|[+-]?\d+(?:\.\d+)?)'
    header = re.match(r'^\s*(?:\[EXTRACTED_ORIGINAL\]\s*)?(' + token + r')[ \t]*\n(?:[ \t]*\n)?', value)
    if not header or not value[header.end():].strip():
        return value
    explicit = re.compile(
        r'(?:故[ \t]*选|(?:故[ \t]*)?答案[ \t]*(?:为|是))[ \t]*[:：]?[ \t]*'
        r'(?:\$[ \t]*(' + token + r')[ \t]*\$|(' + token + r')(?=[ \t]*[。．.,，\n]|[ \t]*$))'
    )
    results = {match.group(1) or match.group(2) for match in explicit.finditer(_comparison_layout(source))}
    if results == {header.group(1)}:
        return ' ' * header.end() + value[header.end():]
    return value


def _source_parts(source: str, locks: list[ContentLock]) -> list[_SourcePart]:
    source_formulas = _formulas(source)
    # The caller supplies the same source it locked. Do not guess if it differs.
    source_formulas = [
        _Formula(span.start, span.end, span.formula,
                 locks[index].lock_id if index < len(locks) and locks[index].original == span.formula else None)
        for index, span in enumerate(source_formulas)
    ]
    ignored_instructions = _preamble_instruction_starts(source)
    literal_ranges = [(match.start(), match.end()) for match in re.finditer(
        r'\\begin\{(?:verbatim\*?|Verbatim|lstlisting|minted)\}[\s\S]*?\\end\{(?:verbatim\*?|Verbatim|lstlisting|minted)\}'
        r'|<!--[\s\S]*?-->', source)]
    fence_start = None
    for fence in re.finditer(r'^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$', source, re.MULTILINE):
        marker = fence.group(1)
        if fence_start is None:
            fence_start = fence.start(), marker[0], len(marker)
        elif marker[0] == fence_start[1] and len(marker) >= fence_start[2]:
            literal_ranges.append((fence_start[0], fence.end()))
            fence_start = None
    if fence_start is not None:
        literal_ranges.append((fence_start[0], len(source)))
    events: list[tuple[int, int | None, str]] = [
        (match.start(), int(match.group(1) or match.group(2)), 'content')
        for match in _NUMBER.finditer(source) if match.start() not in ignored_instructions
    ]
    # TeX questions are only top-level items; choices and nested lists are not questions.
    environment_stack: list[str] = []
    list_depth = 0
    for match in re.finditer(r'\\(begin|end)\{([^{}]+)\}|\\(question|qitem|item)\b', source):
        action, environment, command = match.groups()
        if action == 'begin':
            if environment in ('question', 'problem'):
                events.append((match.start(), None, 'content'))
            environment_stack.append(environment)
        elif action == 'end':
            if environment_stack and environment_stack[-1] == environment:
                environment_stack.pop()
        elif command in ('question', 'qitem') and not any(env in ('choices', 'solution', 'answer') for env in environment_stack):
            events.append((match.start(), None, 'content'))
        elif command == 'item':
            list_depth = sum(env in ('enumerate', 'questions') for env in environment_stack)
            if list_depth == 1 and environment_stack[-1:] in (['enumerate'], ['questions']):
                events.append((match.start(), None, 'content'))
    for heading in _HEADING.finditer(source):
        heading_end = source.find('\n', heading.start())
        heading_end = len(source) if heading_end < 0 else heading_end
        heading_line = source[heading.start():heading_end]
        events.append((heading.start(), None, 'answers' if re.search('参考答案|答案与解析|试题解析|参考解析', heading_line) else 'extra'))
    events_by_start = {}
    for event in events:
        if (not any(f.start < event[0] < f.end for f in source_formulas)
                and not any(start <= event[0] < end for start, end in literal_ranges)):
            # Explicit numbered/heading events take precedence over generic TeX.
            previous = events_by_start.get(event[0])
            if previous is None or event[1] is not None or event[2] != 'content':
                events_by_start[event[0]] = event
    events = sorted(events_by_start.values(), key=lambda event: event[0])
    if not events:
        events = [(0, None, 'content')]
    elif events[0][0] != 0:
        events.insert(0, (0, None, 'extra'))
    parts: list[_SourcePart] = []
    answers_section = False

    def append(start: int, end: int, number: int | None, field: str, owner: int | None = None) -> int:
        formulas = tuple(_Formula(f.start - start, f.end - start, f.formula, f.lock_id)
                         for f in source_formulas if start <= f.start < end)
        parts.append(_SourcePart(source[start:end], number, field, formulas, owner, start, end))
        return len(parts) - 1

    for index, (start, number, kind) in enumerate(events):
        end = events[index + 1][0] if index + 1 < len(events) else len(source)
        if kind == 'answers':
            answers_section = True
            append(start, end, None, 'extra')
        elif kind == 'extra':
            append(start, end, None, 'extra')
        elif answers_section:
            append(start, end, number, 'answer_markdown')
        else:
            heading = _NUMBER.match(source[start:end])
            if heading and re.fullmatch(r'[ \t\r\n]*(?:待补充|[（(【]待补充[）)】])[。.]?[ \t\r\n]*',
                                       source[start + heading.end():end]):
                append(start, end, number, 'placeholder')
                continue
            answer_start = next((
                match for match in _ANSWER_START.finditer(source, start, end)
                if not any(formula.start <= match.start() < formula.end for formula in source_formulas)
            ), None)
            if answer_start:
                owner = append(start, answer_start.start(), number, 'content')
                append(answer_start.start(), end, number, 'answer_markdown', owner)
            else:
                append(start, end, number, 'content')
    return parts


def reconcile_visible_math(
    questions: list[dict[str, Any]],
    locks: list[ContentLock],
    source: str,
) -> dict[str, Any]:
    """Reconcile formulas locally and return reviewable, never silently lost results.

    IDs remain useful evidence, but ID-less output is accepted only at matching
    formula slots inside a uniquely identified source question. Uncertain source
    fragments are returned separately so dropping a whole question stays visible.
    The source is the exact original text passed to ``lock_visible_math``.
    """
    source = str(source or '')
    by_id = {lock.lock_id: lock for lock in locks}
    parts = _source_parts(source, locks)
    table_layout = _answer_table_comparison_layout(source)
    format_sources = {index: table_layout[part.source_start:part.source_end]
                      for index, part in enumerate(parts)}
    format_contexts = {
        index: '\n'.join(other.text for other_index, other in enumerate(parts)
                          if other_index == index or other.owner == index or part.owner == other_index
                          or part.number is not None and other.number == part.number)
        for index, part in enumerate(parts)
    }
    ignored_source_notes = []
    instruction_count = len(_preamble_instruction_starts(source))
    footer_count = len(_page_footer_ranges(source))
    if instruction_count:
        ignored_source_notes.append(f'已忽略卷首 {instruction_count} 条考试说明。')
    if footer_count:
        ignored_source_notes.append(f'已按页脚格式排除 {footer_count} 处页面标记。')
    placeholders = [str(part.number) for part in parts if part.field == 'placeholder' and part.number is not None]
    if placeholders:
        ignored_source_notes.append('原题 ' + '、'.join(placeholders) + ' 仅为“待补充”占位，已单独标注，未作为遗漏题。')
    content_parts = [index for index, part in enumerate(parts) if part.field == 'content']
    staged = [dict(question) for question in questions]
    output_formulas = {
        (index, field): _formulas(str(question.get(field) or ''), by_id)
        for index, question in enumerate(staged) for field in ('content', 'answer_markdown')
    }
    id_counts = Counter(formula.lock_id for formulas in output_formulas.values()
                        for formula in formulas if formula.lock_id)
    source_id_part = {formula.lock_id: index for index, part in enumerate(parts)
                      for formula in part.formulas if formula.lock_id}
    skeletons = {index: _skeleton(part.text, part.formulas) for index, part in enumerate(parts)}
    numeric_sources = {index: _numeric_comparison(part.text, part.formulas) for index, part in enumerate(parts)}
    assignments: dict[int, int] = {}
    answer_assignments: dict[int, int] = {}
    reasons: dict[int, list[str]] = defaultdict(list)
    source_excerpts: dict[int, str] = {}
    review_parts: dict[int, set[int]] = defaultdict(set)
    matched_parts: set[int] = set()
    verified_ids: set[str] = set()
    by_id_verified: set[str] = set()
    by_content_verified: set[str] = set()
    numeric_replacements: dict[tuple[int, str], list[tuple[int, int, str]]] = defaultdict(list)
    comparison_sources: dict[tuple[int, int], str] = {}

    def source_for_question(q_index: int, part_index: int) -> str:
        key = q_index, part_index
        if key not in comparison_sources:
            part = parts[part_index]
            comparison_sources[key] = (_question_metadata_layout(part.text, staged[q_index])
                                       if part.field == 'content' else part.text)
        return comparison_sources[key]

    def review(index: int, reason: str, part_index: int | None = None) -> None:
        if reason not in reasons[index]:
            reasons[index].append(reason)
        if part_index is not None:
            review_parts[index].add(part_index)
            source_excerpts[index] = '\n\n'.join(parts[part].text for part in sorted(review_parts[index]))

    for q_index, question in enumerate(staged):
        value = str(question.get('content') or '')
        formulas = output_formulas[q_index, 'content']
        signature = _skeleton(value, formulas)
        number_match = _NUMBER.match(value)
        number = int(number_match.group(1) or number_match.group(2)) if number_match else None
        candidates = [index for index in content_parts if number is not None and parts[index].number == number]
        if len(candidates) != 1:
            candidates = [index for index in content_parts
                          if _skeleton(source_for_question(q_index, index), parts[index].formulas) == signature]
        if len(candidates) != 1:
            numeric_output = _numeric_comparison(value, formulas)
            if numeric_output is not None:
                candidates = [index for index in content_parts
                              if (candidate := _numeric_comparison(source_for_question(q_index, index), parts[index].formulas)) is not None
                              and candidate.signature == numeric_output.signature]
        if len(candidates) != 1:
            candidates = [index for index in content_parts
                          if _delimited_equivalence(_question_metadata_layout(format_sources[index], question), parts[index].formulas, value, formulas,
                                                    locate_only=True, context=format_contexts[index],
                                                    allow_layout_only=format_sources[index] != parts[index].text) is not None]
        if len(candidates) != 1:
            # Exact, unique prose anchors identify the fragment for human review;
            # unlike fuzzy similarity they cannot certify changed formulas.
            anchored: set[int] = set()
            for index in content_parts:
                source_segments = skeletons[index].split('⟦FORMULA⟧')
                for segment in source_segments:
                    for offset in range(max(0, len(segment) - 11)):
                        anchor = segment[offset:offset + 12]
                        if anchor in signature and sum(anchor in skeletons[other] for other in content_parts) == 1:
                            anchored.add(index)
                            break
            candidates = list(anchored)
        if len(candidates) != 1:
            referenced_parts = {source_id_part.get(f.lock_id) for f in formulas if f.lock_id}
            if len(referenced_parts) == 1 and next(iter(referenced_parts)) in content_parts:
                candidates = list(referenced_parts)
        if len(candidates) == 1:
            assignments[q_index] = candidates[0]
            matched_parts.add(candidates[0])
        else:
            review(q_index, '无法唯一确定这道题在原文中的位置，请核对是否漏题或串题。')

    duplicate_parts = {part for part, count in Counter(assignments.values()).items() if count > 1}
    # An ID alone cannot distinguish swapping two formulas from swapping two
    # otherwise identical question stems. Preserve source ordering as evidence.
    latest: tuple[int, int] | None = None
    for q_index, part_index in assignments.items():
        if latest is not None and part_index < latest[1]:
            review(q_index, '拆分题目顺序与原文不一致，请核对是否串题或调换。', part_index)
            review(latest[0], '拆分题目顺序与原文不一致，请核对是否串题或调换。', latest[1])
        if latest is None or part_index > latest[1]:
            latest = q_index, part_index

    def compare(q_index: int, field: str, part_index: int) -> None:
        part = parts[part_index]
        value = str(staged[q_index].get(field) or '')
        if field == 'answer_markdown':
            value = _comparable_answer(value, part.text)
        formulas = output_formulas[q_index, field]
        label = '题干' if field == 'content' else '原版答案'
        expected_text = source_for_question(q_index, part_index)
        exact_skeleton = _skeleton(value, formulas) == _skeleton(expected_text, part.formulas)
        numeric_source = (_numeric_comparison(expected_text, part.formulas)
                          if expected_text != part.text else numeric_sources[part_index])
        numeric_output = _numeric_comparison(value, formulas) if not exact_skeleton else None
        numeric_match = bool(numeric_source is not None and numeric_output is not None
                             and numeric_source.signature == numeric_output.signature
                             and len(numeric_source.formulas) == len(numeric_output.formulas)
                             and len(numeric_source.numbers) == len(numeric_output.numbers))
        delimited_source = (_question_metadata_layout(format_sources[part_index], staged[q_index])
                            if field == 'content' else format_sources[part_index])
        delimited_pairs = (_delimited_equivalence(delimited_source, part.formulas, value, formulas,
                                                 context=format_contexts[part_index],
                                                 allow_layout_only=format_sources[part_index] != part.text)
                           if not exact_skeleton and not numeric_match else None)
        if delimited_pairs is None and _image_positions(value, formulas, numeric=numeric_match) != _image_positions(expected_text, part.formulas, numeric=numeric_match):
            review(q_index, f'{label}插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。', part_index)
        if not exact_skeleton and not numeric_match and delimited_pairs is None:
            review(q_index, f'{label}文字或公式位置与原文未能完整对应，请对照原文核对。', part_index)
            return
        pairs = [(expected, actual, actual.start, actual.end, expected.formula, False)
                 for expected, actual in zip(part.formulas, formulas)]
        if delimited_pairs is not None:
            pairs = [(expected, actual, actual.start, actual.end, expected.formula, False)
                     for expected, actual in delimited_pairs]
        if numeric_match:
            pairs = [(expected, actual, actual.start, actual.end, expected.formula, False)
                     for expected, actual in zip(numeric_source.formulas, numeric_output.formulas)]
            for expected_number, actual_number in zip(numeric_source.numbers, numeric_output.numbers):
                if expected_number.formula is None:
                    if actual_number.formula is not None and actual_number.formula.lock_id:
                        review(q_index, f'{label}数字位置引用了其他来源的公式编号，请核对是否串题。', part_index)
                    continue
                pairs.append((expected_number.formula, actual_number.formula, actual_number.start, actual_number.end,
                              part.text[expected_number.start:expected_number.end], True))
            pairs.sort(key=lambda pair: pair[0].start)
        slots = {formula.start: index for index, formula in enumerate(part.formulas, 1)}
        for expected, actual, start, end, original, number_equivalent in pairs:
            slot = slots[expected.start]
            if not expected.lock_id:
                review(q_index, '原文公式定位信息不完整，请对照原文核对。', part_index)
            elif actual is not None and actual.lock_id:
                if actual.lock_id != expected.lock_id:
                    review(q_index, f'{label}第 {slot} 处公式编号属于其他位置，可能发生串题或调换。', part_index)
                elif id_counts[actual.lock_id] > 1:
                    review(q_index, f'{label}第 {slot} 处公式编号重复出现，请核对是否重复或遗漏内容。', part_index)
                else:
                    verified_ids.add(expected.lock_id)
                    by_id_verified.add(expected.lock_id)
            elif number_equivalent or actual is not None and _math_key(actual.formula) == _math_key(expected.formula):
                verified_ids.add(expected.lock_id)
                by_content_verified.add(expected.lock_id)
                if numeric_match or delimited_pairs is not None:
                    numeric_replacements[q_index, field].append((start, end, original))
            else:
                review(q_index, f'{label}第 {slot} 处公式与原文不同，请核对符号、数值和次序。', part_index)

    for q_index, part_index in assignments.items():
        if part_index in duplicate_parts:
            review(q_index, '多道拆分结果对应同一段原文，请核对重复题与遗漏题。', part_index)
        compare(q_index, 'content', part_index)
        source_part = parts[part_index]
        answer_parts = [index for index, part in enumerate(parts) if part.field == 'answer_markdown'
                        and (part.owner == part_index or source_part.number is not None and part.number == source_part.number)]
        if len(answer_parts) == 1:
            answer_assignments[q_index] = answer_parts[0]
            matched_parts.add(answer_parts[0])
            compare(q_index, 'answer_markdown', answer_parts[0])
        elif str(staged[q_index].get('answer_markdown') or '').replace('[EXTRACTED_ORIGINAL]', '').strip():
            review(q_index, '无法在原文中唯一定位对应答案，需核对答案是否来自原卷。', part_index)

    # Apply replacements only after every match has been analyzed. Known IDs can
    # still be displayed as formulas on review cards; they never certify a slot
    # belonging to another source position. Unknown IDs are readable review text.
    overwritten = 0
    for (q_index, field), formulas in output_formulas.items():
        value = str(staged[q_index].get(field) or '')
        replacements: list[tuple[int, int, str]] = []
        part_index = assignments.get(q_index)
        if field == 'answer_markdown' and part_index is not None:
            matches = [index for index, part in enumerate(parts) if part.field == field and
                       (part.owner == part_index or parts[part_index].number is not None and part.number == parts[part_index].number)]
            part_index = matches[0] if len(matches) == 1 else None
        comparison_value = _comparable_answer(value, parts[part_index].text) if field == 'answer_markdown' and part_index is not None else value
        expected_formulas = (parts[part_index].formulas if part_index is not None
                             and _skeleton(comparison_value, formulas) == _skeleton(source_for_question(q_index, part_index), parts[part_index].formulas) else ())
        for slot, actual in enumerate(formulas):
            if actual.lock_id:
                replacements.append((actual.start, actual.end, actual.formula))
                overwritten += int(actual.overwritten)
                if actual.lock_id not in by_id:
                    review(q_index, '模型返回了无法识别的公式编号，请对照原文补全公式。', part_index)
                elif id_counts[actual.lock_id] > 1:
                    review(q_index, '公式编号重复出现，请核对公式是否放错位置。', part_index)
            elif slot < len(expected_formulas):
                expected = expected_formulas[slot]
                if expected.lock_id in by_content_verified and _math_key(actual.formula) == _math_key(expected.formula):
                    replacements.append((actual.start, actual.end, expected.formula))
        replacements.extend(numeric_replacements.get((q_index, field), []))
        for start, end, replacement in sorted(replacements, reverse=True):
            value = value[:start] + replacement + value[end:]
        if field in staged[q_index] or value:
            staged[q_index][field] = value

    unmatched = [
        {'source_excerpt': part.text, 'reason': ('这段原文未匹配到拆分题目，可能整题遗漏。'
                                               if part.field == 'content' else '这段含公式的原文未匹配到题目或原版答案，请人工核对。')}
        for index, part in enumerate(parts) if index not in matched_parts
        and (part.field == 'content' and (_plain_key(part.text) or _image_positions(part.text, part.formulas)) or part.formulas)
    ]
    for q_index, messages in reasons.items():
        existing = staged[q_index].get('source_review') or {}
        staged[q_index]['source_review'] = {
            'required': True,
            'reasons': list(dict.fromkeys([*existing.get('reasons', []), *messages])),
            'source_excerpt': source_excerpts.get(q_index, existing.get('source_excerpt', '')),
        }
    warnings = []
    if by_content_verified:
        warnings.append(f'{len(by_content_verified)} 处公式未使用编号，已按原文题目及所在位置核对并恢复。')
    if reasons:
        warnings.append(f'{len(reasons)} 道题需要对照原文核对，已保留拆分结果。')
    if unmatched:
        warnings.append(f'{len(unmatched)} 段原文未匹配到拆分结果，请核对是否遗漏。')
    duplicate_answers = {part for part, count in Counter(answer_assignments.values()).items() if count > 1}
    report = {
        'math_locks_created': len(locks),
        'math_locks_restored': len(verified_ids),
        'math_locks_overwritten': overwritten,
        'math_locks_missing': len(set(by_id) - verified_ids),
        'math_locks_duplicated': sum(count > 1 for lock_id, count in id_counts.items() if lock_id in by_id),
        'math_locks_by_id': len(by_id_verified),
        'math_locks_by_content': len(by_content_verified),
        'source_review_count': sum(bool((q.get('source_review') or {}).get('required')) for q in staged),
        'source_matches': [
            {
                'question_index': q_index,
                'field': parts[part_index].field,
                'source_number': parts[part_index].number,
                'source_excerpt': parts[part_index].text,
                'source_start': parts[part_index].source_start,
                'source_end': parts[part_index].source_end,
            }
            for mapping, duplicates in ((assignments, duplicate_parts), (answer_assignments, duplicate_answers))
            for q_index, part_index in mapping.items() if part_index not in duplicates
        ],
        'unmatched_source': unmatched,
        'warnings': warnings,
        'ignored_source_notes': ignored_source_notes,
    }
    for question, restored_question in zip(questions, staged):
        question.update(restored_question)
    return report
