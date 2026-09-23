"""Lossless source-fragment locks for AI-assisted document splitting.

The model sees formulas in their original sentences and is asked for stable
references. ID-less output is checked against local source positions before
restoring exact source bytes; uncertain results remain available for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter, defaultdict
import re
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


def lock_visible_math(value: str, scope: str) -> tuple[str, list[ContentLock]]:
    """Wrap common balanced TeX math forms while keeping formulas visible."""
    source = str(value or "")
    safe_scope = re.sub(r"[^A-Za-z0-9_-]", "", str(scope or "DOCX"))[:48] or "DOCX"
    parts: list[str] = []
    locks: list[ContentLock] = []
    cursor = 0
    formula_index = 0

    while cursor < len(source):
        span = _next_math_span(source, cursor)
        if span is None:
            parts.append(source[cursor:])
            break
        start, end = span
        original = source[start:end]
        if not original.strip():
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


_REFERENCE = re.compile(
    r'<mathbank-math\b[^>]*>.*?</mathbank-math\s*>'
    r'|\[\[\s*MBM_[A-Za-z0-9_-]+\s*\]\]'
    r'|\bMBM_[A-Za-z0-9_-]+\b', re.DOTALL | re.IGNORECASE,
)
_NUMBER = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*)?(?:\\noindent[ \t]*)?'
    r'(?:(\d{1,3})[.．、](?!\d)|第\s*(\d{1,3})\s*题[：:]?)\s*',
    re.MULTILINE,
)
_HEADING = re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]*|\\(?:section|subsection)\*?[ \t]*\{|\\textbf[ \t]*\{)?'
    r'(?:[一二三四五六七八九十]+[、．.]|参考答案|答案与解析|试题解析|参考解析)',
    re.MULTILINE,
)
_ANSWER_START = re.compile(
    r'(?:\n\s*|(?=【))(?:【(?:答案|解析|解答)】|(?:参考答案|答案|解答|解)[：:])'
    r'|\\begin\{(?:solution|answer)\}',
)


def _formulas(value: str, by_id: dict[str, ContentLock] | None = None) -> list[_Formula]:
    """Read model references before math so a tagged formula is one occurrence."""
    references = list(_REFERENCE.finditer(value)) if by_id is not None else []
    by_id = by_id or {}
    result: list[_Formula] = []
    cursor = 0
    for reference in [*references, None]:
        limit = reference.start() if reference else len(value)
        while cursor < limit:
            span = _next_math_span(value[:limit], cursor)
            if span is None:
                break
            start, end = span
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
    value = value.replace('[EXTRACTED_ORIGINAL]', '').strip()
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
    value = re.sub(r'【(?:答案|解析|解答)】|^(?:参考答案|答案|解答|解)[：:]', '', value)
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


def _image_positions(value: str, formulas: list[_Formula] | tuple[_Formula, ...]) -> list[tuple[str, str]]:
    """Compare each image's path and its local prose/formula/option position."""
    image_pattern = re.compile(
        r'!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:[ \t]+[^)]*)?\s*\)'
        r'|\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}',
    )
    return [
        (next(group for group in match.groups() if group is not None),
         _skeleton(value[:match.start()], [f for f in formulas if f.end <= match.start()]))
        for match in image_pattern.finditer(value)
    ]


def _source_parts(source: str, locks: list[ContentLock]) -> list[_SourcePart]:
    source_formulas = _formulas(source)
    # The caller supplies the same source it locked. Do not guess if it differs.
    source_formulas = [
        _Formula(span.start, span.end, span.formula,
                 locks[index].lock_id if index < len(locks) and locks[index].original == span.formula else None)
        for index, span in enumerate(source_formulas)
    ]
    events: list[tuple[int, int | None, str]] = [
        (match.start(), int(match.group(1) or match.group(2)), 'content')
        for match in _NUMBER.finditer(source)
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
        if not any(f.start < event[0] < f.end for f in source_formulas):
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
        parts.append(_SourcePart(source[start:end], number, field, formulas, owner))
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
            answer_start = _ANSWER_START.search(source, start, end)
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
    by_id = {lock.lock_id: lock for lock in locks}
    parts = _source_parts(str(source or ''), locks)
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
    assignments: dict[int, int] = {}
    reasons: dict[int, list[str]] = defaultdict(list)
    source_excerpts: dict[int, str] = {}
    review_parts: dict[int, set[int]] = defaultdict(set)
    matched_parts: set[int] = set()
    verified_ids: set[str] = set()
    by_id_verified: set[str] = set()
    by_content_verified: set[str] = set()

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
            candidates = [index for index in content_parts if skeletons[index] == signature]
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
        formulas = output_formulas[q_index, field]
        label = '题干' if field == 'content' else '原版答案'
        if _image_positions(value, formulas) != _image_positions(part.text, part.formulas):
            review(q_index, f'{label}插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。', part_index)
        if _skeleton(value, formulas) != skeletons[part_index]:
            review(q_index, f'{label}文字或公式位置与原文未能完整对应，请对照原文核对。', part_index)
            return
        for slot, (expected, actual) in enumerate(zip(part.formulas, formulas), 1):
            if not expected.lock_id:
                review(q_index, '原文公式定位信息不完整，请对照原文核对。', part_index)
            elif actual.lock_id:
                if actual.lock_id != expected.lock_id:
                    review(q_index, f'{label}第 {slot} 处公式编号属于其他位置，可能发生串题或调换。', part_index)
                elif id_counts[actual.lock_id] > 1:
                    review(q_index, f'{label}第 {slot} 处公式编号重复出现，请核对是否重复或遗漏内容。', part_index)
                else:
                    verified_ids.add(expected.lock_id)
                    by_id_verified.add(expected.lock_id)
            elif _math_key(actual.formula) == _math_key(expected.formula):
                verified_ids.add(expected.lock_id)
                by_content_verified.add(expected.lock_id)
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
        expected_formulas = parts[part_index].formulas if part_index is not None and _skeleton(value, formulas) == skeletons[part_index] else ()
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
        for start, end, replacement in reversed(replacements):
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
    report = {
        'math_locks_created': len(locks),
        'math_locks_restored': len(verified_ids),
        'math_locks_overwritten': overwritten,
        'math_locks_missing': len(set(by_id) - verified_ids),
        'math_locks_duplicated': sum(count > 1 for lock_id, count in id_counts.items() if lock_id in by_id),
        'math_locks_by_id': len(by_id_verified),
        'math_locks_by_content': len(by_content_verified),
        'source_review_count': sum(bool((q.get('source_review') or {}).get('required')) for q in staged),
        'unmatched_source': unmatched,
        'warnings': warnings,
    }
    for question, restored_question in zip(questions, staged):
        question.update(restored_question)
    return report
