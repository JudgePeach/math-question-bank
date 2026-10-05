"""Exercise the actual Word/PDF adapters, with no network or database writes."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_docx_source_verify import state as word_state
from test_pdf_source_verify import setup as pdf_state
from mathbank import docx_source_verify as word, pdf_source_verify as pdf
from mathbank.task_manager import TaskCancelled


@pytest.fixture(params=['word', 'pdf'])
def flow(request, monkeypatch):
    kind = request.param
    state = request.getfixturevalue(kind + '_state')
    module = word if kind == 'word' else pdf
    if kind == 'pdf':
        state.questions[:] = state.questions[:1]
        state.diagnostics['source_matches'][:] = state.diagnostics['source_matches'][:1]
        state.diagnostics['pdf_review_items'][:] = state.diagnostics['pdf_review_items'][:1]
        state.diagnostics['source_review_count'] = 1
    state.questions[0]['content'] = state.questions[0]['content'].replace('x^2', 'x^3')
    identifier, number = ('word_001', 1) if kind == 'word' else ('item_001', 14)
    calls, responses = [], []
    def verdict(decision='equivalent', evidence='原页函数指数是二，候选题干和答案与原页完整一致。'):
        item = {'id': identifier, 'source_number': number, 'decision': decision, 'evidence': evidence,
                'checks': {key: True for key in module._CHECKS}}
        if decision != 'equivalent': item['checks']['math_and_conditions'] = False
        if kind == 'pdf': item['source_pages'] = [1]
        return {'items': [item]}
    def proposal(before='x^3', after='x^2'):
        return {'items': [{'id': identifier, 'source_number': number, 'source_pages': [1],
            'patches': [{'field': 'content', 'before': before, 'after': after}],
            'evidence': '原页题干中的函数指数明确为二，修正误识别的三。'}]}
    def post(provider, payload, **kwargs):
        calls.append(deepcopy(payload))
        assert kwargs['timeout'] == (600 if kind == 'pdf' else 120)
        assert kwargs['retry_connection'] is False
        assert len(calls) <= 7
        value = responses.pop(0)
        if callable(value): value = value()
        if isinstance(value, BaseException): raise value
        return SimpleNamespace(status_code=200, json=lambda: {
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    monkeypatch.setattr(module, 'post_chat_completion', post)
    def run(**kwargs):
        if kind == 'word':
            return word.verify_docx_source_suspicions(state.questions, state.diagnostics, state.source, state.evidence, **kwargs)
        return pdf.verify_pdf_source_suspicions(state.questions, state.diagnostics, state.urls, state.numbers, **kwargs)
    return SimpleNamespace(kind=kind, state=state, calls=calls, responses=responses, run=run,
                           verdict=verdict, proposal=proposal, module=module)


def test_confirmed_repair_is_committed_only_after_independent_full_review(flow):
    original = deepcopy(flow.state.questions[0])
    def recheck():
        assert flow.state.questions[0] == original
        prompt = flow.calls[-1]['messages'][0]['content'][0]['text']
        assert 'x^2' in prompt and '"patches"' not in prompt
        return flow.verdict()
    flow.responses[:] = [flow.verdict('different'), flow.proposal(), recheck]
    report = flow.run()
    result = flow.state.questions[0]
    assert report['calls'] == 3 and report['repaired'] == report['confirmed'] == 1
    assert report['usage'] == {'prompt_tokens': 30, 'completion_tokens': 15, 'total_tokens': 45}
    assert result['content'] == original['content'].replace('x^3', 'x^2')
    assert result['answer_markdown'] == original['answer_markdown']
    assert result['source_review']['required'] is False
    assert result['source_review']['repair']['attempts'] == 1
    assert result['source_review']['repair']['status'] == 'confirmed'
    assert result['source_review']['verification']['decision'] == 'equivalent'
    assert result['source_review']['source_excerpt'] == original['source_review']['source_excerpt']
    assert len(result['source_review']['verification']['verified_output_sha256']) == 64


def test_three_failed_rounds_keep_original_and_specific_difference(flow):
    original = flow.state.questions[0]['content']
    flow.responses[:] = [flow.verdict('different')]
    for before, after in [('x^3', 'x^4'), ('x^4', 'x^5'), ('x^5', 'x^6')]:
        flow.responses += [flow.proposal(before, after), flow.verdict('different')]
    report = flow.run()
    review = flow.state.questions[0]['source_review']
    assert report['calls'] == 7 and report['confirmed'] == 0
    assert flow.state.questions[0]['content'] == original
    assert review['required'] is True and review['verification_attempt']['decision'] == 'different'
    assert review['repair']['attempts'] == 3 and review['repair']['status'] == 'exhausted'
    assert len(review['repair']['history']) == 3


@pytest.mark.parametrize('failure', ['uncertain', 'malformed', 'connection', 'cycle'])
def test_failed_draft_never_overwrites_original(flow, failure):
    original = flow.state.questions[0]['content']
    flow.responses[:] = [flow.verdict('different'), flow.proposal()]
    if failure == 'uncertain': flow.responses.append(flow.verdict('uncertain'))
    elif failure == 'malformed': flow.responses.append({'items': []})
    elif failure == 'connection': flow.responses.append(ConnectionError('private credential must not leak'))
    else: flow.responses += [flow.verdict('different'), flow.proposal('x^2', 'x^3')]
    report = flow.run()
    assert report['confirmed'] == 0 and flow.state.questions[0]['content'] == original
    assert flow.state.questions[0]['source_review']['repair']['status'] in {'failed', 'stopped'}
    assert 'private credential' not in json.dumps(report) + json.dumps(flow.state.questions)


def test_uncertain_does_not_guess_or_request_repair(flow):
    flow.responses[:] = [flow.verdict('uncertain')]
    report = flow.run()
    assert report['calls'] == 1 and len(flow.calls) == 1
    assert 'repair' not in flow.state.questions[0]['source_review']


def test_mutation_during_repair_invalidates_entire_batch(flow):
    def mutate():
        flow.state.questions[0]['content'] = '用户的新题目'
        return flow.proposal()
    flow.responses[:] = [flow.verdict('different'), mutate]
    report = flow.run()
    assert report['confirmed'] == 0 and flow.state.questions[0]['content'] == '用户的新题目'
    assert flow.state.questions[0]['source_review']['required'] is True


def test_cancellation_during_repair_is_not_a_failed_verdict(flow):
    flow.responses[:] = [flow.verdict('different'), TaskCancelled('cancelled')]
    original = deepcopy(flow.state.questions)
    with pytest.raises(TaskCancelled): flow.run()
    assert flow.state.questions == original


def test_source_answer_is_repaired_only_when_original_answer_exists(flow):
    if flow.kind == 'pdf':
        # PDF fixture has no original-answer source; a generated answer is blocked.
        proposal = flow.proposal()
        proposal['items'][0]['patches'] = [{'field': 'answer_markdown', 'before': 'none', 'after': '2'}]
        flow.responses[:] = [flow.verdict('different'), proposal]
        report = flow.run()
        assert report['confirmed'] == 0 and flow.state.questions[0]['answer_markdown'] == ''
    else:
        flow.state.questions[0]['answer_markdown'] = '顶点为其他点。'
        proposal = flow.proposal()
        proposal['items'][0]['patches'].append({'field': 'answer_markdown', 'before': '其他点', 'after': '原点'})
        flow.responses[:] = [flow.verdict('different'), proposal, flow.verdict()]
        assert flow.run()['confirmed'] == 1
        assert flow.state.questions[0]['answer_markdown'] == '顶点为原点。'


def test_missing_original_answer_can_be_restored_then_rechecked(flow):
    flow.state.questions[0]['answer_markdown'] = ''
    if flow.kind == 'pdf':
        excerpt = '答案：顶点为原点。'
        start = flow.state.diagnostics['source_matches'][0]['source_end'] + 1
        flow.state.diagnostics['source_matches'].append({'question_index': 0, 'field': 'answer_markdown',
            'source_number': 14, 'source_start': start, 'source_end': start + len(excerpt), 'source_excerpt': excerpt})
    proposal = flow.proposal()
    proposal['items'][0]['patches'].append({'field': 'answer_markdown', 'before': '', 'after': '顶点为原点。'})
    flow.responses[:] = [flow.verdict('different'), proposal, flow.verdict()]
    report = flow.run()
    assert report['calls'] == 3 and report['confirmed'] == 1
    assert flow.state.questions[0]['answer_markdown'] == '顶点为原点。'


def test_repair_cannot_introduce_original_answer_protocol_marker(flow):
    proposal = flow.proposal(after='[EXTRACTED_ORIGINAL]x^2')
    flow.responses[:] = [flow.verdict('different'), proposal]
    report = flow.run()
    assert report['confirmed'] == 0 and report['calls'] == 2
    assert '[EXTRACTED_ORIGINAL]' not in flow.state.questions[0]['content']


def test_repaired_image_order_records_final_candidate_evidence_fingerprint(flow):
    from PIL import Image, ImageDraw
    from mathbank.content_locks import _source_parts
    from mathbank.source_review_images import prepare_candidate_images

    state = flow.state
    root = state.image.parent.parent if flow.kind == 'word' else state.root
    paths = []
    for number in (1, 2):
        path = root / 'tmp' / f'repair_formula_{number}.png'
        image = Image.new('RGB', (40, 40), 'white')
        ImageDraw.Draw(image).line((0, number * 10, 39, number * 10), fill='black', width=2)
        image.save(path)
        paths.append('/static/uploads/tmp/' + path.name)
    number = 1 if flow.kind == 'word' else 14
    identifier = 'word_001' if flow.kind == 'word' else 'item_001'
    first, second = paths
    original = f'{number}. 第一图 ![甲]({first})，第二图 ![乙]({second})，求结果。'
    incorrect = f'{number}. 第一图 ![甲]({second})，第二图 ![乙]({first})，求结果。'
    question = state.questions[0]
    question['content'] = incorrect
    if flow.kind == 'word':
        state.source = original + '\n【解析】顶点为原点。\n2. 求函数零点。'
        stem = _source_parts(state.source, [])[0]
        state.diagnostics['source_matches'][0].update(
            source_start=stem.source_start, source_end=stem.source_end, source_excerpt=stem.text)
        question['source_review']['source_excerpt'] = stem.text
        state.evidence['pages'][0]['text'] = state.source
    else:
        state.diagnostics['source_matches'][0].update(
            source_start=0, source_end=len(original), source_excerpt=original)
        question['source_review']['source_excerpt'] = original

    def current_images():
        return prepare_candidate_images([{'id': identifier, 'output': {
            field: question[field] for field in ('content', 'answer_markdown')}}],
            allowed_paths=paths, uploads_dir=root)

    before_fingerprint = current_images()['fingerprint']
    difference = flow.verdict('different', '两张候选图片顺序与原页相反，需要交换回原文顺序。')
    difference['items'][0]['checks'].update(math_and_conditions=True, figures=False)
    flow.responses[:] = [difference, flow.proposal(before=incorrect, after=original), flow.verdict()]
    report = flow.run(candidate_image_paths=paths)
    assert report['calls'] == 3 and report['confirmed'] == report['repaired'] == 1
    assert question['content'] == original
    final_images = current_images()
    assert final_images['fingerprint'] != before_fingerprint
    fingerprint_key = 'candidate_images_sha256' if flow.kind == 'word' else 'candidate_image_evidence_hash'
    assert question['source_review']['verification'][fingerprint_key] == final_images['fingerprint']
    assert [image['path'] for image in final_images['per_item'][identifier]['images']] == paths


def test_original_page_mutation_during_final_staging_discards_whole_batch(flow, monkeypatch):
    original_questions = deepcopy(flow.state.questions)
    original_repair = flow.module.repair_verified_differences
    phase = {'repaired': False, 'mutated': False}

    def mark_repair_returned(*args, **kwargs):
        result = original_repair(*args, **kwargs)
        phase['repaired'] = True
        return result

    monkeypatch.setattr(flow.module, 'repair_verified_differences', mark_repair_returned)
    image_function = '_batch_image_evidence' if flow.kind == 'word' else '_candidate_images'
    original_images = getattr(flow.module, image_function)

    def mutate_on_staged_image_read(candidates, *args, **kwargs):
        corrected = any('x^2' in (item['output']['content'] if isinstance(item['output'], dict)
                                 else item['output']) for item in candidates)
        if phase['repaired'] and corrected and not phase['mutated']:
            page = flow.state.image if flow.kind == 'word' else flow.state.root / 'tmp/pdf_page_test_1.png'
            page.write_bytes(b'Original page replaced while preparing final corrected image evidence')
            phase['mutated'] = True
        return original_images(candidates, *args, **kwargs)

    monkeypatch.setattr(flow.module, image_function, mutate_on_staged_image_read)
    flow.responses[:] = [flow.verdict('different'), flow.proposal(), flow.verdict()]
    report = flow.run()
    assert phase['mutated'], 'the mutation must occur after the fresh referee, while staging corrected output'
    assert report['calls'] == 3 and report['confirmed'] == report.get('repaired', 0) == 0
    assert flow.state.questions == original_questions
    assert report['usage']['total_tokens'] == 45, 'discarded content still consumed completed requests'


def test_received_repair_usage_is_retained_when_question_snapshot_changed(flow):
    def mutate_after_repair_response():
        flow.state.questions[0]['content'] = '用户的新题目'
        return flow.proposal()

    flow.responses[:] = [flow.verdict('different'), mutate_after_repair_response]
    report = flow.run()
    assert report['calls'] == 2 and report['confirmed'] == 0
    assert report['usage'] == {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}
    assert flow.state.questions[0]['content'] == '用户的新题目'
    assert flow.state.questions[0]['source_review']['required'] is True
