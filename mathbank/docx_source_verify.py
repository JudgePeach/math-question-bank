"""Bounded review of Word split results against the original rendered document.

Retrieval only proposes a source; it never certifies content. Only a complete
visual verdict tied to the source bytes, pages and unchanged output may release
the corresponding question. Source-backed correction drafts require a fresh
visual verdict before adoption. Failed transport requests are never retried.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import json
import os
import re

from mathbank import prompts
from mathbank.ai_http import post_chat_completion
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.asset_security import resolve_upload_asset
from mathbank.content_locks import _source_parts, _plain_key, _IMAGE_REFERENCE, _REFERENCE, _NUMBER, _bare_math_spelling, _formulas
from mathbank.paths import UPLOADS_DIR, TEST_UPLOADS_DIR
from mathbank.source_review_images import prepare_candidate_images, _visible_text, _references
from mathbank.source_review_results import parse_review_response
from mathbank.source_review_repair import MAX_EXTRA_CALLS, RepairStopped, repair_verified_differences
from mathbank.task_manager import TaskCancelled

MAX_CALLS = 8
MAX_ITEMS = 6
MAX_PAGES = 4
MAX_CHARS = 24000
MAX_OUTPUT_TOKENS = 4096
_CHECKS = {'same_question', 'complete_content', 'math_and_conditions',
           'options_and_subquestions', 'figures', 'answer'}
_REVIEWABLE = {
    '无法唯一确定这道题在原文中的位置，请核对是否漏题或串题。',
    '题干文字或公式位置与原文未能完整对应，请对照原文核对。',
    '原版答案文字或公式位置与原文未能完整对应，请对照原文核对。',
    '题干插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。',
    '原版答案插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。',
    'Word 原生公式提取存在疑点，请对照原页核对。',
}
_FORMULA_DIFFERENCE = re.compile(r'(?:题干|原版答案)第 [1-9][0-9]* 处公式与原文不同，请核对符号、数值和次序。')
_STRUCTURE_NOTE = '[公式结构待核对]'
_MATHTYPE_PREVIEW_NOTE = re.compile(r'\[公式待核对\]\n!\[MathType 公式待核对\]\(([^\s)]+)\)')
_INCOMPLETE_OUTPUT = re.compile(
    r'\[(?:公式[^\]\n]{0,80}待核对|特殊字符待核对|插图待补|[^\]\n]{0,20}不支持[^\]\n]{0,20})'
    r'|\[MathType 公式待核对\]'
    r'|无法识别的公式|公式无法安全提取'
)


def _readable_formula(value):
    # The native extractor uses these explicit placeholders for lost glyphs.
    # A model saying "equivalent" cannot turn one into a complete formula.
    return (not re.search(r'\\text\s*\{\s*\?\s*\}|\ufffd', value)
            and not any(0xE000 <= ord(char) <= 0xF8FF or 0xF0000 <= ord(char) <= 0xFFFFD
                        or 0x100000 <= ord(char) <= 0x10FFFD for char in value))


def _without_structure_notes(value):
    """Propose removal of diagnostic labels, preserving every formula byte.

    This is a comparison copy only. It is committed only after the original
    rendered page and all six checks explicitly confirm it. Missing formulas
    and previews still marked as unparsed are never treated as complete.
    """
    formulas = _formulas(value)
    removals = []
    for match in re.finditer(re.escape(_STRUCTURE_NOTE), value):
        previous = next((formula for formula in reversed(formulas) if formula.end <= match.start()), None)
        if (previous is not None and not value[previous.end:match.start()].strip()
                and _readable_formula(previous.formula)):
            removals.append((match.start(), match.end()))
    for start, end in reversed(removals):
        value = value[:start] + value[end:]
    return value, len(removals)


def _without_mathtype_preview_notes(value, image_evidence, field):
    """Remove only a rendered formula preview's fixed diagnostic wrapper.

    The exact image remains in the comparison copy. No missing formula,
    unrelated placeholder, or literal code example can use this exception.
    """
    if not image_evidence['complete']:
        return value, 0
    paths = {image['path'] for image in image_evidence['images']
             if not image.get('is_uniform', True)
             and any(binding['field'] == field for binding in image['bindings'])}
    matches = [match for match in _MATHTYPE_PREVIEW_NOTE.finditer(_visible_text(value))
               if match.group(1) in paths]
    for match in reversed(matches):
        value = value[:match.start()] + f'![MathType 公式]({match.group(1)})' + value[match.end():]
    return value, len(matches)


class _VerificationError(ValueError):
    """Fixed local validation messages safe for a task report."""


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _retrieval_key(value):
    # Used solely for candidate retrieval, never for automatic acceptance.
    value = _IMAGE_REFERENCE.sub('', value)
    value = _bare_math_spelling(value)
    value = re.sub(r'\\(?:begin|end)\{[^{}]+\}|\\[A-Za-z]+', '', value)
    return re.sub(r'OPTION[A-H]', '', _plain_key(value))


def _images(value):
    # The count must describe the same visible references as the actual-pixel
    # evidence. Literal/escaped Markdown is not an image; unknown syntax is
    # retained as an empty path so callers can refuse to certify it.
    return [reference['path'] for reference in _references(value, '', '')]


def _source_candidates(questions, diagnostics, source):
    parts = _source_parts(source, [])
    stems = [(index, part) for index, part in enumerate(parts)
             if part.field == 'content' and type(part.number) is int and part.number > 0]
    candidates, skipped = [], []
    for index, question in enumerate(questions):
        review = question.get('source_review') or {}
        if not review.get('required'):
            continue
        reason = None
        if not review.get('reasons') or any(not isinstance(item, str) or (
                item not in _REVIEWABLE and not _FORMULA_DIFFERENCE.fullmatch(item)) for item in review['reasons']):
            reason = '存在公式编号、来源冲突或其他需单独核对的问题。'
        matches = [item for item in diagnostics.get('source_matches', [])
                   if item.get('question_index') == index and item.get('field') == 'content']
        selected = []
        if len(matches) == 1:
            match = matches[0]
            selected = [(part_index, part) for part_index, part in stems
                        if part.source_start == match.get('source_start')
                        and part.source_end == match.get('source_end')
                        and part.text == match.get('source_excerpt')]
        elif not matches:
            key = _retrieval_key(question.get('content', ''))
            ranked = sorted(((SequenceMatcher(None, key, _retrieval_key(part.text), autojunk=False).ratio(),
                              part_index, part) for part_index, part in stems), key=lambda item: item[0], reverse=True)
            if ranked and ranked[0][0] >= .72 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= .15):
                selected = [(ranked[0][1], ranked[0][2])]
        if len(selected) != 1:
            reason = reason or '无法唯一找到可供核验的原 Word 题段。'
        if reason:
            skipped.append({'question_index': index, 'reason': reason})
            continue
        part_index, stem = selected[0]
        if sum(part.number == stem.number for _, part in stems) != 1:
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '原文中存在多个同号题段，无法唯一核验来源。'})
            continue
        answers = [part for part in parts if part.field == 'answer_markdown'
                   and (part.owner == part_index or part.number == stem.number)]
        if len(answers) > 1 or (question.get('answer_markdown', '').strip() and not answers):
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '原版答案来源不唯一，不能由一次视觉核验解除。'})
            continue
        answer = answers[0] if answers else None
        original = {'content': stem.text, 'answer_markdown': answer.text if answer else ''}
        output_before = {field: question.get(field, '') for field in original}
        output, removed_notes = {}, 0
        for field, value in output_before.items():
            output[field], count = _without_structure_notes(value)
            removed_notes += count
        blocking_checks = {}
        uncertain_text = '\n'.join([*original.values(), *output.values()])
        if _REFERENCE.search(uncertain_text) or '无法识别的公式来源' in uncertain_text:
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '仍有无法识别的公式来源编号，保留人工核对。'})
            continue
        if any(_INCOMPLETE_OUTPUT.search(value) or not _readable_formula(value) for value in output.values()):
            blocking_checks['complete_content'] = '待核验结果仍有未解析的公式、缺失字符或图片占位，不能以占位与原文相似解除核对。'
        if sum(map(len, [*original.values(), *output.values()])) > MAX_CHARS:
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '单题及原文超出本次核验文字额度。'})
            continue
        ranges = [{'question_index': index, 'field': part.field, 'source_number': stem.number,
                   'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
                  for part in (stem, answer) if part is not None]
        candidates.append({'id': f'word_{index + 1:03d}', 'question_index': index,
                           'source_number': stem.number, 'original': original, 'output': output,
                           'source_matches': ranges, 'inline_answer': answer is None or answer.owner == part_index,
                           'blocking_reasons': list(blocking_checks.values()),
                           'diagnostic_labels_removed': removed_notes,
                           '_source_range_exact': len(matches) == 1,
                           '_blocking_checks': blocking_checks, '_output_before_cleanup': output_before})
    # Two outputs must not be confirmed from the same original question.
    duplicates = {item['source_number'] for item in candidates
                  if sum(other['source_number'] == item['source_number'] for other in candidates) > 1
                  or any(match.get('field') == 'content' and match.get('source_number') == item['source_number']
                         and match.get('question_index') != item['question_index']
                         for match in diagnostics.get('source_matches', []))}
    for item in candidates:
        if item['source_number'] in duplicates:
            skipped.append({'question_index': item['question_index'], 'source_number': item['source_number'],
                            'reason': '多张题卡对应同一原题，保留人工核对。'})
    return [item for item in candidates if item['source_number'] not in duplicates], skipped


_LOCATOR_ANSWER_SECTION = re.compile(
    r'^[ \t]*(?:参考答案(?:与解析)?|答案与解析|试题解析|参考解析)[ \t]*[:：]?[ \t]*$', re.MULTILINE)


def _locator_text_anchors(value):
    """Exact prose anchors locate a field; they never certify its formulas.

    Native-rendered text does not use source LaTeX spelling. Keep only literal
    Chinese runs outside math, images, diagnostic labels and explicit code.
    Whitespace may change during native pagination; no punctuation, symbols,
    characters or formula values are substituted to create a match.
    """
    if not isinstance(value, str) or len(value) > 50000:
        return []
    visible = _visible_text(value)
    chars = list(visible)
    ranges = [(formula.start, formula.end) for formula in _formulas(value)]
    ranges.extend((match.start(), match.end()) for match in _IMAGE_REFERENCE.finditer(value))
    ranges.extend((match.start(), match.end()) for match in _INCOMPLETE_OUTPUT.finditer(value))
    for start, end in ranges:
        chars[start:end] = '\x00' * (end - start)
    text = re.sub(r'\s+', '', ''.join(chars))
    anchors = re.findall(r'[\u3400-\u9fff]{4,}', text)
    anchors = [anchor for anchor in anchors if anchor not in {
        '参考答案', '答案与解析', '参考答案与解析', '试题解析', '参考解析',
    }]
    return anchors if sum(map(len, anchors)) >= 8 else []


def _locator_document(pages):
    """Build an ordered text index only for complete, consecutive page IDs."""
    if (not pages or any(not isinstance(page, dict) or type(page.get('page_number')) is not int
                         or not isinstance(page.get('text'), str) for page in pages)
            or [page['page_number'] for page in pages] != list(range(1, len(pages) + 1))):
        return None
    pieces, offsets, cursor = [], [], 0
    for page in pages:
        offsets.append(cursor)
        pieces.append(page['text'])
        cursor += len(page['text']) + 1
    return '\n'.join(pieces), offsets


def _locator_field_range(value, number, document, headings):
    anchors = _locator_text_anchors(value)
    if not anchors:
        return None
    candidates = []
    for index, heading in enumerate(headings):
        if heading[0] != number:
            continue
        end = len(document)
        if index + 1 < len(headings):
            following = headings[index + 1]
            if following[0] == number + 1:
                end = following[1]
            else:
                # A reset numbering sequence is a boundary only when the
                # original page text explicitly labels the answer section.
                section = list(_LOCATOR_ANSWER_SECTION.finditer(document, heading[2], following[1]))
                if len(section) != 1:
                    continue
                end = section[0].start()
        excerpt = re.sub(r'\s+', '', document[heading[2]:end])
        cursor = 0
        for anchor in anchors:
            found = excerpt.find(anchor, cursor)
            if found < 0:
                break
            cursor = found + len(anchor)
        else:
            candidates.append((heading[1], end))
    return candidates[0] if len(candidates) == 1 else None


def _locator_range_pages(span, value, pages, offsets):
    start, end = span
    first = max(index for index, offset in enumerate(offsets) if offset <= start)
    last = max(index for index, offset in enumerate(offsets) if offset <= end)
    # Text-only fields ending at a heading on the next page do not need that
    # unrelated page. For image-bearing fields retain it: floating pictures
    # before the next heading need not appear in the auxiliary page text.
    if (last > first and not pages[last]['text'][:end - offsets[last]].strip()
            and not _images(value)):
        last -= 1
    return [page['page_number'] for page in pages[first:last + 1]]


def _locate_separate_answer_pages(candidate, pages):
    original = candidate.get('original')
    indexed = _locator_document(pages)
    if (not isinstance(original, dict) or not indexed or type(candidate.get('source_number')) is not int
            or not original.get('content') or not original.get('answer_markdown')):
        return []
    document, offsets = indexed
    headings = [(int(match.group(1) or match.group(2)), match.start(), match.end())
                for match in _NUMBER.finditer(document)]
    stem = _locator_field_range(original['content'], candidate['source_number'], document, headings)
    answer = _locator_field_range(original['answer_markdown'], candidate['source_number'], document, headings)
    # Separate answers cannot be inferred from an overlapping/same question
    # segment. Both complete numbered ranges must be independently identified.
    if stem is None or answer is None or stem[1] > answer[0]:
        return []
    numbers = sorted(set(_locator_range_pages(stem, original['content'], pages, offsets))
                     | set(_locator_range_pages(answer, original['answer_markdown'], pages, offsets)))
    return numbers if len(numbers) <= MAX_PAGES else []


def _locate_pages(candidate, pages):
    # The original DOCX renderer's page text is an independent index. Include
    # all pages from this numbered heading through the next numbered heading;
    # continuation pages are never silently cut off.
    if not candidate.get('inline_answer', False):
        if len(pages) <= MAX_PAGES:
            return [page['page_number'] for page in pages]
        return _locate_separate_answer_pages(candidate, pages)
    headings = []
    for page in pages:
        for match in _NUMBER.finditer(page.get('text', '')):
            headings.append((int(match.group(1) or match.group(2)), page['page_number'], match.start()))
    starts = [item for item in headings if item[0] == candidate['source_number']]
    if len(starts) == 1:
        start = starts[0]
        following = [item for item in headings if item[0] == start[0] + 1
                     and (item[1], item[2]) > (start[1], start[2])]
        if len(following) <= 1:
            end = following[0][1] if following else pages[-1]['page_number']
            if following and end > start[1] and candidate.get('original'):
                next_page = next(page for page in pages if page['page_number'] == end)
                # A following heading at the very start of the next page is
                # outside this text-only question. Keep it for image-bearing
                # questions, whose floating illustrations may precede a heading.
                if (not next_page['text'][:following[0][2]].strip()
                        and not any(_images(value) for value in candidate['original'].values())):
                    end -= 1
            return [page['page_number'] for page in pages if start[1] <= page['page_number'] <= end]
    # Small documents can be shown in full, so no guessed page label is needed.
    return [page['page_number'] for page in pages] if len(pages) <= MAX_PAGES else []


def _page_content(pages, numbers):
    messages = []
    for page in pages:
        if page['page_number'] not in numbers:
            continue
        url = page['image_path']
        if url.startswith('/static/uploads/tmp/'):
            root, prefix = UPLOADS_DIR, '/static/uploads'
        elif url.startswith('/static/test_uploads/tmp/'):
            root, prefix = TEST_UPLOADS_DIR, '/static/test_uploads'
        else:
            raise ValueError('原 Word 页面不是本地任务资产')
        path = resolve_upload_asset(url, uploads_dir=root, url_prefix=prefix, allowed_extensions={'.png'})
        if not path.name.startswith('docx_page_') or path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('原 Word 页面格式或大小不符合核验范围')
        data = path.read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(data).hexdigest() != page.get('image_sha256'):
            raise ValueError('原 Word 页面已变化')
        messages.extend([{'type': 'text', 'text': f'原始 Word 直接渲染第 {page["page_number"]} 页'},
                         {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(data).decode()}}])
    return messages


def _decisions(raw, batch):
    try:
        parsed = parse_review_response(raw)
    except Exception as exc:
        raise _VerificationError('核验结果不是有效JSON') from exc
    # Some compatible vision endpoints return the requested items as a root
    # array even in JSON-object mode. Only unwrap this structural variation;
    # IDs, source numbers and every accepted item's checks remain mandatory.
    if isinstance(parsed, list):
        parsed = {'items': parsed}
    expected = {item['id']: item for item in batch}
    if not isinstance(parsed, dict) or set(parsed) != {'items'} or not isinstance(parsed['items'], list):
        raise _VerificationError('核验结果不完整')
    grouped = {identifier: [] for identifier in expected}
    for item in parsed['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or item['id'] not in expected):
            raise _VerificationError('核验结果含未知或无法归属的题目ID')
        grouped[item['id']].append(item)
    decisions, invalid = {}, []
    for identifier, entries in grouped.items():
        candidate = expected[identifier]
        def reject(code, reason):
            invalid.append({'id': identifier, 'question_index': candidate['question_index'],
                            'source_number': candidate['source_number'], 'source_pages': candidate['source_pages'],
                            'code': code, 'reason': reason})
        if not entries:
            reject('missing_result', '视觉模型未返回本题核验结论，已保留原提示。')
            continue
        if len(entries) != 1:
            reject('duplicate_id', '视觉模型重复返回本题ID，无法唯一采纳，已保留原提示。')
            continue
        item = entries[0]
        if not isinstance(item, dict) or set(item) != {'id', 'source_number', 'decision', 'evidence', 'checks'}:
            reject('invalid_fields', '本题核验结果字段不完整或无效，已保留原提示。')
            continue
        if (type(item['source_number']) is not int or item['source_number'] != candidate['source_number']
                or not isinstance(item['decision'], str) or item['decision'] not in {'equivalent', 'different', 'uncertain'}
                or not isinstance(item['evidence'], str) or not 6 <= len(item['evidence'].strip()) <= 600
                or not isinstance(item['checks'], dict) or set(item['checks']) != _CHECKS
                or any(type(value) is not bool for value in item['checks'].values())
                or item['decision'] == 'equivalent' and not all(item['checks'].values())):
            reject('invalid_verdict', '本题核验题号、依据或六项判断不完整，已保留原提示。')
            continue
        decisions[identifier] = item
    return decisions, invalid


def _batch_image_evidence(batch, candidate_image_paths):
    # Keep the exact pre-cleanup comparison values for a second asset read.
    image_items = [{'id': item['id'], 'output': deepcopy(item['output'])} for item in batch]
    images = prepare_candidate_images(image_items, allowed_paths=candidate_image_paths,
                                      uploads_dir=UPLOADS_DIR, test_uploads_dir=TEST_UPLOADS_DIR)
    for item in batch:
        image_evidence = images['per_item'][item['id']]
        item['candidate_image_evidence'] = image_evidence
        checks = item['_blocking_checks']
        original_images = {field: _images(value) for field, value in item['original'].items()}
        output_images = {field: _images(value) for field, value in item['output'].items()}
        if not image_evidence['complete']:
            checks['figures'] = '；'.join(image_evidence['reasons'])
        elif any(not path for paths in [*original_images.values(), *output_images.values()] for path in paths):
            checks['figures'] = '原文或候选图片引用格式无法完整识别，不能证明图像内容完整，已保留核对。'
        elif any(len(original_images[field]) != len(output_images[field]) for field in item['original']):
            checks['figures'] = '题干或答案的图片存在新增、缺失或重复，候选图片无法证明内容完整，已保留核对。'
        elif image_evidence['images'] and not item['_source_range_exact']:
            checks['figures'] = '候选图片尚无唯一精确原文来源，不能仅凭图片路径或相似题文解除核对。'
        for field, value in item['output'].items():
            cleaned, count = _without_mathtype_preview_notes(value, image_evidence, field)
            item['output'][field] = cleaned
            item['diagnostic_labels_removed'] += count
        if all(not _INCOMPLETE_OUTPUT.search(value) and _readable_formula(value)
               for value in item['output'].values()):
            checks.pop('complete_content', None)
        item['blocking_reasons'] = list(checks.values())
    return image_items, images


def _planned_batch_evidence(batch, candidate_image_paths):
    # Planning never changes candidates. Re-read task assets for the actual
    # request, then again before accepting any verdict.
    planned = deepcopy(batch)
    _, images = _batch_image_evidence(planned, candidate_image_paths)
    prompt = prompts.build_docx_source_verification_prompt(planned)
    pages = sorted(set().union(*(set(item['source_pages']) for item in planned)))
    chars = (len(prompt) + sum(len(part.get('text', '')) for part in images['messages'])
             + sum(len(f'原始 Word 直接渲染第 {number} 页') for number in pages))
    return images, chars


def verify_docx_source_suspicions(questions, diagnostics, source, evidence, *, candidate_image_paths=(),
                                 check_cancelled=lambda: None, progress=lambda message: None):
    required = sum(bool((q.get('source_review') or {}).get('required')) for q in questions)
    report = {'status': 'no_candidates', 'calls': 0, 'checked': 0, 'confirmed': 0,
              'pending': required, 'skipped': 0, 'skipped_reasons': [], 'items': [], 'invalid_items': [], 'usage': {}}
    candidates, report['skipped_reasons'] = _source_candidates(questions, diagnostics, source)
    if not candidates:
        report['skipped'] = required
        return report
    if (evidence.get('status') != 'ready' or evidence.get('evidence_kind') != 'original_docx_render'
            or evidence.get('evidence_version') != 2):
        report.update(status='unavailable', skipped=required,
                      notes=evidence.get('notes') or ['本机暂不可直接渲染原Word，未调用视觉核验。'])
        return report
    pages = evidence.get('pages', [])
    if (not pages or any(type(page.get('page_number')) is not int for page in pages)
            or len({page['page_number'] for page in pages}) != len(pages)
            or not re.fullmatch('[a-f0-9]{64}', evidence.get('source_sha256', ''))):
        report.update(status='failed', notes=['原 Word 页面证据不完整，未调用核验。'])
        return report
    batches = []
    planned_evidence_hash = _fingerprint(evidence)
    for item in candidates:
        item['source_pages'] = _locate_pages(item, pages)
        if not item['source_pages'] or len(item['source_pages']) > MAX_PAGES:
            report['skipped_reasons'].append({'question_index': item['question_index'], 'source_number': item['source_number'],
                                             'reason': '无法在本次页面额度内完整展示该原题及答案。'})
            continue
        check_cancelled()
        single_images, single_chars = _planned_batch_evidence([item], candidate_image_paths)
        item_images = single_images['per_item'][item['id']]
        if not item_images['complete']:
            report['skipped_reasons'].append({'question_index': item['question_index'], 'source_number': item['source_number'],
                                             'source_pages': item['source_pages'], 'code': 'candidate_images_unavailable',
                                             'reason': '候选图片证据不完整，本题未请求视觉模型，已保留原提示。',
                                             'details': item_images['reasons']})
            continue
        if single_chars > MAX_CHARS:
            report['skipped_reasons'].append({'question_index': item['question_index'], 'source_number': item['source_number'],
                                             'reason': '单题核验请求超出本次文字额度。'})
            continue
        trial = [*(batches[-1] if batches else []), item]
        trial_images, trial_chars = _planned_batch_evidence(trial, candidate_image_paths) if batches else (single_images, single_chars)
        if (not batches or len(batches[-1]) >= MAX_ITEMS
                or len(set().union(*(set(candidate['source_pages']) for candidate in trial))) > MAX_PAGES
                or trial_chars > MAX_CHARS
                or not all(candidate['complete'] for candidate in trial_images['per_item'].values())):
            if len(batches) >= MAX_CALLS:
                report['skipped_reasons'].append({'question_index': item['question_index'], 'source_number': item['source_number'],
                                                 'reason': '超过本次自动核验的调用或文字额度。'})
                continue
            batches.append([])
        batches[-1].append(item)
    report['skipped'] = required - sum(len(batch) for batch in batches)
    if not batches:
        return report
    provider = resolve_ocr_provider(os.getenv('OCR_PREFER_ENGINE', 'siliconflow'))
    if not provider.api_key or not provider.chat_completions_url or not provider.supports_image_input:
        report.update(status='unavailable', notes=['识图模型未配置或不支持图片输入，未调用核验。'])
        return report
    repair_budget = {'remaining': MAX_EXTRA_CALLS}
    for batch_index, batch in enumerate(batches):
        calls_before_batch = report['calls']
        try:
            fresh_candidates = {item['question_index']: item for item in _source_candidates(questions, diagnostics, source)[0]}
            comparison_keys = ('original', 'output', '_output_before_cleanup', 'source_matches',
                               'source_number', '_source_range_exact', 'diagnostic_labels_removed', '_blocking_checks')
            if (planned_evidence_hash != _fingerprint(evidence)
                    or any(item['question_index'] not in fresh_candidates
                           or any(fresh_candidates[item['question_index']][key] != item[key] for key in comparison_keys)
                           for item in batch)):
                raise _VerificationError('核验准备期间原文、页面或候选题文发生变化')
            snapshot = _fingerprint({'questions': questions, 'source': source,
                                     'matches': diagnostics.get('source_matches'), 'evidence': evidence,
                                     'candidate_image_paths': sorted(candidate_image_paths)})
            page_numbers = sorted(set().union(*(set(item['source_pages']) for item in batch)))
            image_items, image_evidence = _batch_image_evidence(batch, candidate_image_paths)
            prompt = prompts.build_docx_source_verification_prompt(batch)
            content = [{'type': 'text', 'text': prompt}, *_page_content(pages, page_numbers), *image_evidence['messages']]
            if sum(len(part.get('text', '')) for part in content) > MAX_CHARS:
                raise _VerificationError('包含候选图片绑定的核验请求超出本批文字额度')
            payload = apply_model_thinking_policy({'model': provider.model_name, 'messages': [{'role': 'user', 'content': content}],
                                                   'max_tokens': MAX_OUTPUT_TOKENS, 'stream': False,
                                                   'response_format': {'type': 'json_object'}}, provider=provider, task='ocr')
            cap_key = 'max_completion_tokens' if 'max_completion_tokens' in payload else 'max_tokens'
            payload[cap_key] = MAX_OUTPUT_TOKENS
            payload.pop('max_tokens' if cap_key == 'max_completion_tokens' else 'max_completion_tokens', None)
            check_cancelled()
            report['calls'] += 1
            response = post_chat_completion(provider, payload, timeout=120, check_status=False, retry_connection=False)
            check_cancelled()
            if response.status_code != 200:
                raise ValueError('核验请求未成功')
            body = response.json()
            usage = body.get('usage') or {}
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                if type(usage.get(key)) is int and usage[key] >= 0:
                    report['usage'][key] = report['usage'].get(key, 0) + usage[key]
            choice = body['choices'][0]
            raw = choice.get('message', {}).get('content')
            if choice.get('finish_reason') != 'stop' or not isinstance(raw, str) or not raw.strip() or len(raw) > 24000:
                raise ValueError('核验结果截断或为空')
            decisions, invalid_items = _decisions(raw, batch)
            if snapshot != _fingerprint({'questions': questions, 'source': source,
                                         'matches': diagnostics.get('source_matches'), 'evidence': evidence,
                                         'candidate_image_paths': sorted(candidate_image_paths)}):
                raise ValueError('核验期间原文或结果发生变化')
            check_cancelled()
            # Check asset hashes again before accepting a model verdict.
            _page_content(pages, page_numbers)
            current_images = prepare_candidate_images(image_items, allowed_paths=candidate_image_paths,
                                                       uploads_dir=UPLOADS_DIR, test_uploads_dir=TEST_UPLOADS_DIR)
            if current_images['fingerprint'] != image_evidence['fingerprint']:
                raise _VerificationError('核验期间候选图片证据发生变化')
            # A repair is a new draft, followed by a separate referee call.
            # Both use the same untouched original pages and task snapshot.
            def check_repair_evidence():
                check_cancelled()
                if snapshot != _fingerprint({'questions': questions, 'source': source,
                        'matches': diagnostics.get('source_matches'), 'evidence': evidence,
                        'candidate_image_paths': sorted(candidate_image_paths)}):
                    raise _VerificationError('修正期间原文或题目发生变化')
                _page_content(pages, page_numbers)
                fresh = prepare_candidate_images(image_items, allowed_paths=candidate_image_paths,
                    uploads_dir=UPLOADS_DIR, test_uploads_dir=TEST_UPLOADS_DIR)
                if fresh['fingerprint'] != image_evidence['fingerprint']:
                    raise _VerificationError('修正期间候选图片发生变化')

            originals = {item['id']: item for item in batch}

            def revised_candidates(items):
                changed = []
                for item in items:
                    candidate = deepcopy(originals[item['id']])
                    candidate.update(output=deepcopy(item['output']), _output_before_cleanup=deepcopy(item['output']),
                                     _blocking_checks={}, blocking_reasons=[], diagnostic_labels_removed=0)
                    if any(_INCOMPLETE_OUTPUT.search(value) or not _readable_formula(value)
                           for value in item['output'].values()):
                        candidate['_blocking_checks']['complete_content'] = '修正后仍有未解析公式或缺失字符。'
                    changed.append(candidate)
                _batch_image_evidence(changed, candidate_image_paths)
                return changed

            def repair_attachments(items):
                changed = revised_candidates(items)
                _, images = _batch_image_evidence(changed, candidate_image_paths)
                if not all(part['complete'] for part in images['per_item'].values()):
                    raise RepairStopped('修正候选图片证据不完整，未继续请求。')
                wanted = sorted(set().union(*(set(item['source_pages']) for item in changed)))
                return [*_page_content(pages, wanted), *images['messages']]

            def recheck_content(items):
                return [{'type': 'text', 'text': prompts.build_docx_source_verification_prompt(revised_candidates(items))},
                        *repair_attachments(items)]

            def validate_repair(item, output):
                candidate = revised_candidates([{**item, 'output': output}])[0]
                if candidate['_blocking_checks']:
                    raise RepairStopped('；'.join(candidate['blocking_reasons']))
                if sum(len(value) for value in [*item['original'].values(), *output.values()]) > MAX_CHARS:
                    raise RepairStopped('修正后的完整题文超出单题核验额度。')

            def repair_verdicts(raw, items):
                changed = revised_candidates(items)
                verdicts, _ = _decisions(raw, changed)
                return {item['id']: verdicts[item['id']] for item in changed
                        if item['id'] in verdicts and not item['_blocking_checks']}

            repair = repair_verified_differences([
                {key: deepcopy(item[key]) for key in ('id', 'source_number', 'source_pages', 'original', 'output')}
                | {'repair_block_reason': item['_blocking_checks'].get('figures', '')} for item in batch], decisions,
                provider=provider, request=post_chat_completion, attachments=repair_attachments,
                verification_content=recheck_content, parse_verdicts=repair_verdicts, validate_output=validate_repair,
                check_evidence=check_repair_evidence, check_cancelled=check_cancelled,
                budget=repair_budget, max_chars=MAX_CHARS, max_output_tokens=MAX_OUTPUT_TOKENS, progress=progress)
            report['calls'] += repair['calls']
            if repair['repairs']:
                for key in ('repair_calls', 'recheck_calls'):
                    report[key] = report.get(key, 0) + repair[key]
                report.setdefault('repaired', 0)
            for key, value in repair['usage'].items():
                report['usage'][key] = report['usage'].get(key, 0) + value
            check_repair_evidence()
            staged_questions, staged_diagnostics, staged_report = deepcopy(questions), deepcopy(diagnostics), deepcopy(report)
            if repair['repairs']:
                staged_report['repaired'] += len(repair['outputs'])
            staged_report['invalid_items'].extend(invalid_items)
            for item in batch:
                if item['id'] not in decisions:
                    continue
                repaired_output = repair['outputs'].get(item['id'])
                decision = repair['decisions'].get(item['id'], decisions[item['id']])
                original_output = deepcopy(item['_output_before_cleanup'])
                if repaired_output is not None:
                    revised = revised_candidates([{'id': item['id'], 'output': repaired_output}])[0]
                    item.update(revised)
                    _, corrected_images = _batch_image_evidence([deepcopy(item)], candidate_image_paths)
                model_decision = decision['decision']
                if model_decision == 'equivalent' and item['_blocking_checks']:
                    decision = {**decision, 'decision': 'uncertain',
                                'checks': {**decision['checks'], **{key: False for key in item['_blocking_checks']}},
                                'evidence': '；'.join(item['blocking_reasons'])}
                index = item['question_index']
                staged_report['items'].append({'question_index': index, 'source_pages': item['source_pages'], **decision})
                # Keep the original warning as audit evidence. A concrete
                # explanation tells the user what remains wrong or unreadable.
                review = deepcopy(staged_questions[index]['source_review'])
                if item['id'] in repair['repairs']:
                    review['repair'] = repair['repairs'][item['id']]
                verification = {**decision, 'document_type': 'docx',
                    'model': provider.model_name, 'source_pages': item['source_pages'], 'snapshot_hash': snapshot,
                    'source_sha256': evidence['source_sha256'], 'evidence_reused': True,
                    'candidate_image_evidence': deepcopy(item['candidate_image_evidence']),
                    'candidate_images_sha256': corrected_images['fingerprint'] if repaired_output is not None else image_evidence['fingerprint'],
                    'model_decision': model_decision,
                    'output_before_cleanup_sha256': _fingerprint(original_output),
                    'verified_output_sha256': _fingerprint(item['output']),
                    'diagnostic_labels_removed': item['diagnostic_labels_removed'] if decision['decision'] == 'equivalent' else 0}
                if decision['decision'] == 'equivalent':
                    for field, value in item['output'].items():
                        if value != original_output[field]:
                            staged_questions[index][field] = value
                    review.update(required=False, verified_by='vision', verification=verification)
                    staged_report['confirmed'] += 1
                else:
                    review['verification_attempt'] = verification
                    review['reasons'] = [*review.get('reasons', []), '原文自动核验：' + decision['evidence']]
                staged_questions[index]['source_review'] = review
                if decision['checks']['same_question']:
                    review['source_number'] = item['source_number']
                    if not review.get('source_excerpt'):
                        review['source_excerpt'] = '\n\n'.join(match['source_excerpt'] for match in item['source_matches'])
                    # Same-question evidence resolves retrieval even when a
                    # real content difference still requires review. It does
                    # not hide missing source fragments elsewhere in the file.
                    for match in item['source_matches']:
                        if not any(other.get('question_index') == index and other.get('field') == match['field']
                                   for other in staged_diagnostics.get('source_matches', [])):
                            staged_diagnostics.setdefault('source_matches', []).append({**match, 'match_method': 'visual'})
                    remaining = list(staged_diagnostics.get('unmatched_source', []))
                    for match in item['source_matches']:
                        identical = [entry for entry in remaining if entry.get('source_excerpt') == match['source_excerpt']]
                        if len(identical) == 1:
                            remaining.remove(identical[0])
                    staged_diagnostics['unmatched_source'] = remaining
            staged_report['checked'] += len(decisions)
            staged_report['status'] = ('partial' if staged_report['checked'] else 'failed') if staged_report['invalid_items'] else 'completed'
            # Every final candidate (including corrected image bindings) is
            # staged before the last evidence check. No asset reads or model
            # validation may occur inside the following commit block.
            check_repair_evidence()
            for item in batch:
                index = item['question_index']
                questions[index].update(staged_questions[index])
            for key in ('source_matches', 'unmatched_source'):
                if key in staged_diagnostics:
                    diagnostics[key] = staged_diagnostics[key]
            report.clear()
            report.update(staged_report)
        except TaskCancelled:
            raise
        except Exception as exc:
            report['status'] = 'partial' if report['checked'] else 'failed'
            detail = str(exc) if isinstance(exc, _VerificationError) else type(exc).__name__
            report['notes'] = [f'Word原文自动核验未完成（{detail}）；已保留未确认题目，未自动重试。']
            # Stopping after a failure avoids extra requests, but unrequested
            # questions must not be presented as if the model reviewed them.
            first_unrequested = batch_index if report['calls'] == calls_before_batch else batch_index + 1
            for pending_batch in batches[first_unrequested:]:
                for item in pending_batch:
                    report['skipped'] += 1
                    report['skipped_reasons'].append({
                        'question_index': item['question_index'], 'source_number': item['source_number'],
                        'source_pages': item['source_pages'], 'code': 'verification_stopped',
                        'reason': '本次自动核验准备或前批请求未完成，本题尚未请求视觉模型，已保留原提示。',
                    })
            break
    report['pending'] = sum(bool((q.get('source_review') or {}).get('required')) for q in questions)
    diagnostics['source_review_count'] = report['pending']
    return report
