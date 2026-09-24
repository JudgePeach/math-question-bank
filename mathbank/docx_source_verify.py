"""Bounded review of Word split results against the original rendered document.

Retrieval only proposes a source; it never certifies content. Only a complete
visual verdict tied to the source bytes, pages and unchanged output may release
the corresponding question. No content is rewritten and no request is retried.
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
from mathbank.ai_json import parse_ai_json
from mathbank.ai_providers import apply_model_thinking_policy, resolve_ocr_provider
from mathbank.asset_security import resolve_upload_asset
from mathbank.content_locks import _source_parts, _plain_key, _IMAGE_REFERENCE, _REFERENCE, _NUMBER, _bare_math_spelling
from mathbank.paths import UPLOADS_DIR, TEST_UPLOADS_DIR
from mathbank.task_manager import TaskCancelled

MAX_CALLS = 3
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
}


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
    return [next(group for group in match.groups() if group is not None)
            for match in _IMAGE_REFERENCE.finditer(value)]


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
        if not review.get('reasons') or any(item not in _REVIEWABLE for item in review['reasons']):
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
        output = {field: question.get(field, '') for field in original}
        if any(_images(original[field]) != _images(output[field]) for field in original):
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '图片引用缺失、重复或次序不同，保留人工核对。'})
            continue
        uncertain_text = '\n'.join([*original.values(), *output.values()])
        if (_REFERENCE.search(uncertain_text)
                or re.search(r'\[(?:公式[^\]\n]{0,20}待核对|插图待补|[^\]\n]{0,20}不支持[^\]\n]{0,20})|无法识别的公式', uncertain_text)):
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '仍有未解析的公式或图片占位，保留人工核对。'})
            continue
        if sum(map(len, [*original.values(), *output.values()])) > MAX_CHARS:
            skipped.append({'question_index': index, 'source_number': stem.number,
                            'reason': '单题及原文超出本次核验文字额度。'})
            continue
        ranges = [{'question_index': index, 'field': part.field, 'source_number': stem.number,
                   'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
                  for part in (stem, answer) if part is not None]
        candidates.append({'id': f'word_{index + 1:03d}', 'question_index': index,
                           'source_number': stem.number, 'original': original, 'output': output,
                           'source_matches': ranges, 'inline_answer': answer is None or answer.owner == part_index})
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


def _locate_pages(candidate, pages):
    # The original DOCX renderer's page text is an independent index. Include
    # all pages from this numbered heading through the next numbered heading;
    # continuation pages are never silently cut off.
    if not candidate.get('inline_answer', False):
        return [page['page_number'] for page in pages] if len(pages) <= MAX_PAGES else []
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
        parsed = parse_ai_json(raw)
    except Exception as exc:
        raise _VerificationError('核验结果不是有效JSON') from exc
    # Some compatible vision endpoints return the requested items as a root
    # array even in JSON-object mode. Only unwrap this structural variation;
    # item count, IDs, source numbers and every check remain mandatory.
    if isinstance(parsed, list):
        parsed = {'items': parsed}
    expected = {item['id']: item['source_number'] for item in batch}
    if not isinstance(parsed, dict) or set(parsed) != {'items'} or not isinstance(parsed['items'], list) or len(parsed['items']) != len(batch):
        raise _VerificationError('核验结果不完整')
    decisions = {}
    for item in parsed['items']:
        if not isinstance(item, dict) or set(item) != {'id', 'source_number', 'decision', 'evidence', 'checks'}:
            raise _VerificationError('核验结果字段无效')
        identifier = item['id']
        if (not isinstance(identifier, str) or identifier not in expected or identifier in decisions
                or type(item['source_number']) is not int or item['source_number'] != expected[identifier]
                or item['decision'] not in {'equivalent', 'different', 'uncertain'}
                or not isinstance(item['evidence'], str) or not 6 <= len(item['evidence'].strip()) <= 600
                or not isinstance(item['checks'], dict) or set(item['checks']) != _CHECKS
                or any(type(value) is not bool for value in item['checks'].values())
                or item['decision'] == 'equivalent' and not all(item['checks'].values())):
            raise _VerificationError('核验依据或逐项判断不完整')
        decisions[identifier] = item
    return decisions


def verify_docx_source_suspicions(questions, diagnostics, source, evidence, *, check_cancelled=lambda: None):
    required = sum(bool((q.get('source_review') or {}).get('required')) for q in questions)
    report = {'status': 'no_candidates', 'calls': 0, 'checked': 0, 'confirmed': 0,
              'pending': required, 'skipped': 0, 'skipped_reasons': [], 'items': [], 'usage': {}}
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
    for item in candidates:
        item['source_pages'] = _locate_pages(item, pages)
        if not item['source_pages'] or len(item['source_pages']) > MAX_PAGES:
            report['skipped_reasons'].append({'question_index': item['question_index'], 'source_number': item['source_number'],
                                             'reason': '无法在本次页面额度内完整展示该原题及答案。'})
            continue
        size = len(prompts.build_docx_source_verification_prompt([item]))
        if size > MAX_CHARS:
            report['skipped_reasons'].append({'question_index': item['question_index'], 'source_number': item['source_number'],
                                             'reason': '单题核验请求超出本次文字额度。'})
            continue
        if not batches or len(batches[-1]) >= MAX_ITEMS or len(set(item['source_pages']).union(*(set(x['source_pages']) for x in batches[-1]))) > MAX_PAGES or len(prompts.build_docx_source_verification_prompt([*batches[-1], item])) > MAX_CHARS:
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
    for batch in batches:
        try:
            snapshot = _fingerprint({'questions': questions, 'source': source,
                                     'matches': diagnostics.get('source_matches'), 'evidence': evidence})
            page_numbers = sorted(set().union(*(set(item['source_pages']) for item in batch)))
            content = [{'type': 'text', 'text': prompts.build_docx_source_verification_prompt(batch)}, *_page_content(pages, page_numbers)]
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
            decisions = _decisions(raw, batch)
            if snapshot != _fingerprint({'questions': questions, 'source': source,
                                         'matches': diagnostics.get('source_matches'), 'evidence': evidence}):
                raise ValueError('核验期间原文或结果发生变化')
            check_cancelled()
            # Check asset hashes again before accepting a model verdict.
            _page_content(pages, page_numbers)
            for item in batch:
                decision = decisions[item['id']]
                index = item['question_index']
                report['items'].append({'question_index': index, 'source_pages': item['source_pages'], **decision})
                # A concrete visual explanation replaces neither the original
                # warning nor the output. It tells the user what remains wrong
                # or unreadable instead of repeating a generic position error.
                review = deepcopy(questions[index]['source_review'])
                verification = {**decision, 'document_type': 'docx',
                    'model': provider.model_name, 'source_pages': item['source_pages'], 'snapshot_hash': snapshot,
                    'source_sha256': evidence['source_sha256'], 'evidence_reused': True}
                if decision['decision'] == 'equivalent':
                    review.update(required=False, verified_by='vision', verification=verification)
                    report['confirmed'] += 1
                else:
                    review['verification_attempt'] = verification
                    review['reasons'] = [*review.get('reasons', []), '原文自动核验：' + decision['evidence']]
                questions[index]['source_review'] = review
                if decision['checks']['same_question']:
                    review['source_number'] = item['source_number']
                    if not review.get('source_excerpt'):
                        review['source_excerpt'] = '\n\n'.join(match['source_excerpt'] for match in item['source_matches'])
                    # Same-question evidence resolves retrieval even when a
                    # real content difference still requires review. It does
                    # not hide missing source fragments elsewhere in the file.
                    for match in item['source_matches']:
                        if not any(other.get('question_index') == index and other.get('field') == match['field']
                                   for other in diagnostics.get('source_matches', [])):
                            diagnostics.setdefault('source_matches', []).append({**match, 'match_method': 'visual'})
                    remaining = list(diagnostics.get('unmatched_source', []))
                    for match in item['source_matches']:
                        identical = [entry for entry in remaining if entry.get('source_excerpt') == match['source_excerpt']]
                        if len(identical) == 1:
                            remaining.remove(identical[0])
                    diagnostics['unmatched_source'] = remaining
            report['checked'] += len(batch)
            report['status'] = 'completed'
        except TaskCancelled:
            raise
        except Exception as exc:
            report['status'] = 'failed'
            detail = str(exc) if isinstance(exc, _VerificationError) else type(exc).__name__
            report['notes'] = [f'Word原文自动核验未完成（{detail}）；已保留未确认题目，未自动重试。']
            break
    report['pending'] = sum(bool((q.get('source_review') or {}).get('required')) for q in questions)
    diagnostics['source_review_count'] = report['pending']
    return report
