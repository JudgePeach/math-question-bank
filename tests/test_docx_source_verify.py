from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from mathbank import docx_source_verify as verify
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.content_locks import _source_parts, lock_visible_math, reconcile_visible_math
from mathbank.task_manager import TaskCancelled


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('No live AI'))
    provider = resolve_ocr_provider('siliconflow', {'SILICONFLOW_API_KEY': 'test-only'})
    monkeypatch.setattr(verify, 'resolve_ocr_provider', lambda _: provider)
    root = tmp_path / 'uploads'
    (root / 'tmp').mkdir(parents=True)
    image = root / 'tmp/docx_page_test_1.png'
    Image.new('RGB', (60, 60), 'white').save(image)
    monkeypatch.setattr(verify, 'UPLOADS_DIR', root)
    source = '题1. 已知函数 $y=x^2$，求顶点。\n【解析】顶点为原点。\n题2. 求函数零点。'
    stem = _source_parts(source, [])[0]
    questions = [{'content': '给出函数 $y=x^2$，求顶点。', 'answer_markdown': '顶点为原点。',
                  'source_review': {'required': True, 'reasons': ['题干文字或公式位置与原文未能完整对应，请对照原文核对。'],
                                    'source_excerpt': stem.text}}]
    diagnostics = {'source_review_count': 1, 'source_matches': [{'question_index': 0, 'field': 'content',
                   'source_number': 1, 'source_start': stem.source_start, 'source_end': stem.source_end,
                   'source_excerpt': stem.text}], 'unmatched_source': [{'source_excerpt': 'another missing question'}]}
    evidence = {'status': 'ready', 'evidence_kind': 'original_docx_render', 'evidence_version': 2, 'source_sha256': 'a' * 64,
                'pages': [{'page_number': 1, 'image_path': '/static/uploads/tmp/' + image.name,
                           'text': source, 'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}]}
    decision = {'id': 'word_001', 'source_number': 1, 'decision': 'equivalent',
                'evidence': '原文函数及所求相同，仅已知和给出的措辞变化。', 'checks': {key: True for key in verify._CHECKS}}
    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': [decision]})}}],
                'usage': {'prompt_tokens': 123, 'completion_tokens': 54, 'total_tokens': 177}}
    data = SimpleNamespace(source=source, questions=questions, diagnostics=diagnostics, evidence=evidence,
                           response=response, decision=decision, calls=[], image=image)
    def post(provider, payload, **kwargs):
        data.calls.append((payload, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: data.response)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    return data


def run(state, **kwargs):
    return verify.verify_docx_source_suspicions(state.questions, state.diagnostics, state.source, state.evidence, **kwargs)


def test_visual_confirmation_preserves_content_evidence_and_unrelated_unmatched(state):
    original = deepcopy(state.questions[0])
    report = run(state)
    assert report['confirmed'] == 1 and report['pending'] == 0
    assert report['usage'] == state.response['usage'] and report['calls'] == 1
    question = state.questions[0]
    assert question['content'] == original['content'] and question['answer_markdown'] == original['answer_markdown']
    assert question['source_review']['reasons'] == original['source_review']['reasons']
    assert question['source_review']['verified_by'] == 'vision'
    assert question['source_review']['verification']['document_type'] == 'docx'
    assert state.diagnostics['unmatched_source'] == [{'source_excerpt': 'another missing question'}]
    assert state.calls[0][1]['retry_connection'] is False
    assert state.calls[0][0]['max_tokens'] == 4096


def _signed_footer_evidence(state, tmp_path, monkeypatch):
    from mathbank import docx_source_evidence as renderer
    from test_docx_footer_cache_render_evidence import package
    import pymupdf as fitz
    monkeypatch.setattr(renderer, 'SYSTEM_GENERATED_DIR', tmp_path / 'isolated-system')
    monkeypatch.setattr(renderer, 'find_docx_renderer', lambda explicit=None: '/test/native')
    def native(_renderer, _source, work, _cancel):
        path = work / 'original.pdf'
        with fitz.open() as doc:
            page = doc.new_page(width=200, height=300)
            page.insert_text((10, 20), state.source)
            doc.save(path)
        return path
    monkeypatch.setattr(renderer, '_native_pdf', native)
    state.evidence = renderer.prepare_docx_source_evidence(package(), output_dir=verify.UPLOADS_DIR / 'tmp',
        url_prefix='/static/uploads/tmp', task_id='footer_check', register_asset=lambda _: None)
    assert renderer.validate_docx_body_render_evidence(state.evidence)
    return renderer


def test_signed_footer_cache_runs_body_verification_with_explicit_scope(state, tmp_path, monkeypatch):
    _signed_footer_evidence(state, tmp_path, monkeypatch)
    report = run(state)
    assert report['calls'] == 1 and report['confirmed'] == 1 and report['evidence_scope'] == 'body_only'
    prompt = state.calls[0][0]['messages'][0]['content'][0]['text']
    assert '仅微小页脚图的外链被隔离' in prompt and '不能确认页脚原内容' in prompt


def test_modified_footer_receipt_is_not_model_input(state, tmp_path, monkeypatch):
    _signed_footer_evidence(state, tmp_path, monkeypatch)
    state.evidence['footer_cache_receipt']['body_parts_unchanged'] = False
    assert run(state)['status'] == 'unavailable'
    assert state.calls == [] and state.questions[0]['source_review']['required']


def test_footer_evidence_changed_during_model_call_cannot_confirm(state, tmp_path, monkeypatch):
    _signed_footer_evidence(state, tmp_path, monkeypatch)
    normal_post = verify.post_chat_completion
    def changed(*args, **kwargs):
        response = normal_post(*args, **kwargs)
        state.evidence['derivative_sha256'] = '0' * 64
        return response
    monkeypatch.setattr(verify, 'post_chat_completion', changed)
    report = run(state)
    assert report['calls'] == 1 and report['confirmed'] == 0 and report['status'] == 'failed'
    assert state.questions[0]['source_review']['required']


@pytest.mark.parametrize('decision', ['different', 'uncertain'])
def test_not_equivalent_stays_review(state, decision):
    state.decision['decision'] = decision
    state.response['choices'][0]['message']['content'] = json.dumps({'items': [state.decision]})
    assert run(state)['confirmed'] == 0
    assert state.questions[0]['source_review']['required'] is True


def test_compatible_vision_root_array_keeps_full_item_validation(state):
    state.response['choices'][0]['message']['content'] = '```json\n' + json.dumps([state.decision]) + '\n```'
    assert run(state)['confirmed'] == 1


def test_root_array_still_rejects_missing_boolean_checks(state):
    state.decision['checks'].pop('answer')
    state.response['choices'][0]['message']['content'] = json.dumps([state.decision])
    report = run(state)
    assert report['status'] == 'failed' and report['checked'] == 0
    assert report['invalid_items'][0]['code'] == 'invalid_verdict'


@pytest.mark.parametrize('change', ['id', 'number', 'check', 'evidence', 'extra', 'duplicate', 'missing', 'truncated'])
def test_invalid_response_is_not_retried_or_accepted(state, change):
    item = state.decision
    items = [item]
    if change == 'id': item['id'] = 'wrong'
    if change == 'number': item['source_number'] = True
    if change == 'check': item['checks']['answer'] = False
    if change == 'evidence': item['evidence'] = ''
    if change == 'extra': item['extra'] = True
    if change == 'duplicate': items.append(item)
    if change == 'missing': items = []
    if change == 'truncated': state.response['choices'][0]['finish_reason'] = 'length'
    state.response['choices'][0]['message']['content'] = json.dumps({'items': items})
    report = run(state)
    assert report['status'] == 'failed'
    assert report['confirmed'] == report['checked'] == 0 and len(state.calls) == 1
    assert state.questions[0]['source_review']['required'] is True


def _two_question_batch(state):
    state.source = '1. 已知 $x=1$，求结果。\n2. 已知 $y=2$，求结果。'
    state.questions = [
        {'content': f'{number}. 已知 ${symbol}={number}$ [公式结构待核对]，求结果。', 'answer_markdown': '',
         'source_review': {'required': True, 'reasons': ['Word 原生公式提取存在疑点，请对照原页核对。']}}
        for number, symbol in [(1, 'x'), (2, 'y')]
    ]
    state.diagnostics = {'source_matches': [
        {'question_index': index, 'field': 'content', 'source_number': part.number,
         'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
        for index, part in enumerate(_source_parts(state.source, []))]}
    state.evidence['pages'][0]['text'] = state.source
    return [deepcopy(state.decision), {**deepcopy(state.decision), 'id': 'word_002', 'source_number': 2}]


@pytest.mark.parametrize('failure,code', [('missing', 'missing_result'), ('duplicate', 'duplicate_id'),
                                        ('field', 'invalid_fields'), ('verdict', 'invalid_verdict')])
def test_one_invalid_result_preserves_other_complete_word_verdict(state, failure, code):
    items = _two_question_batch(state)
    if failure == 'missing':
        items.pop()
    elif failure == 'duplicate':
        items.append(deepcopy(items[1]))
    elif failure == 'field':
        items[1].pop('evidence')
    else:
        items[1]['checks']['answer'] = False
    original = deepcopy(state.questions[1])
    state.response['choices'][0]['message']['content'] = json.dumps({'items': items})
    report = run(state)
    assert report['status'] == 'partial'
    assert report['checked'] == report['confirmed'] == report['pending'] == report['calls'] == 1
    assert not state.questions[0]['source_review']['required']
    assert state.questions[1] == original
    assert report['invalid_items'] == [{'id': 'word_002', 'question_index': 1, 'source_number': 2,
                                        'source_pages': [1], 'code': code,
                                        'reason': report['invalid_items'][0]['reason']}]
    assert report['invalid_items'][0]['reason']


@pytest.mark.parametrize('failure', ['unknown_id', 'unattributed', 'broken_json', 'truncated', 'duplicate_key'])
def test_complete_word_item_cannot_escape_invalid_global_response(state, failure):
    items = _two_question_batch(state)
    if failure == 'unknown_id':
        items[1]['id'] = 'word_999'
    elif failure == 'unattributed':
        items[1].pop('id')
    raw = json.dumps({'items': items})
    if failure == 'broken_json':
        raw = raw[:-1]
    elif failure == 'truncated':
        state.response['choices'][0]['finish_reason'] = 'length'
    elif failure == 'duplicate_key':
        raw = raw.replace('"id": "word_002"', '"id": "word_002", "id": "word_002"')
    state.response['choices'][0]['message']['content'] = raw
    before = deepcopy(state.questions)
    report = run(state)
    assert report['status'] == 'failed'
    assert report['checked'] == report['confirmed'] == 0 and report['calls'] == 1
    assert state.questions == before


def test_partial_batch_continues_to_later_batch_without_retry(state, monkeypatch):
    items = _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(provider, payload, **kwargs):
        state.calls.append((payload, kwargs))
        result = [] if len(state.calls) == 1 else [items[1]]
        body = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': result})}}]}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['status'] == 'partial' and report['calls'] == 2
    assert report['checked'] == report['confirmed'] == report['pending'] == 1
    assert state.questions[0]['source_review']['required']
    assert not state.questions[1]['source_review']['required']


@pytest.mark.parametrize('target', ['content', 'answer', 'review', 'source_match', 'page_text', 'page_bytes', 'sha'])
def test_async_changes_reject_stale_verdict(state, monkeypatch, target):
    def post(*a, **k):
        if target == 'content': state.questions[0]['content'] += 'changed'
        if target == 'answer': state.questions[0]['answer_markdown'] += 'changed'
        if target == 'review': state.questions[0]['source_review']['reasons'].append('changed')
        if target == 'source_match': state.diagnostics['source_matches'][0]['source_end'] += 1
        if target == 'page_text': state.evidence['pages'][0]['text'] += 'changed'
        if target == 'page_bytes': state.image.write_bytes(b'changed')
        if target == 'sha': state.evidence['source_sha256'] = 'b' * 64
        return SimpleNamespace(status_code=200, json=lambda: state.response)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    assert run(state)['confirmed'] == 0
    assert state.questions[0]['source_review']['required']


@pytest.mark.parametrize('status', ['unavailable', 'failed'])
def test_unavailable_render_keeps_review_without_call(state, status):
    state.evidence['status'] = status
    report = run(state)
    assert report['calls'] == 0 and report['pending'] == 1 and not state.calls


@pytest.mark.parametrize('marker', ['[公式待核对]', '[公式结构待核对]', '[插图待补:图1]'])
def test_source_extraction_placeholder_uses_actual_pages_to_check_complete_output(state, marker):
    state.source = state.source.replace('$y=x^2$', marker)
    stem = _source_parts(state.source, [])[0]
    state.diagnostics['source_matches'][0].update(source_excerpt=stem.text, source_end=stem.source_end)
    # The rendered original remains the actual evidence. Its auxiliary local
    # extraction may be incomplete without making a complete output unreviewable.
    assert run(state)['confirmed'] == 1
    assert len(state.calls) == 1


@pytest.mark.parametrize('marker', ['[公式待核对]', '[公式待核对：公式无法安全提取]', '[插图待补:图1]', '[特殊字符待核对]'])
def test_output_placeholder_gets_one_visual_check_but_cannot_be_confirmed(state, marker):
    state.questions[0]['content'] = state.questions[0]['content'].replace('$y=x^2$', marker)
    report = run(state)
    assert report['calls'] == 1 and report['confirmed'] == 0 and report['pending'] == 1
    assert report['items'][0]['decision'] == 'uncertain'
    assert report['items'][0]['checks']['complete_content'] is False
    assert state.questions[0]['source_review']['verification_attempt']['model_decision'] == 'equivalent'
    assert marker in state.questions[0]['content']


@pytest.mark.parametrize('field', ['content', 'answer_markdown'])
def test_real_formula_difference_is_visually_checked_and_preserved(state, field):
    state.source = '1. 已知函数 $y=x^2$，求顶点。\n【解析】顶点为 $(0,0)$。\n2. 求零点。'
    state.evidence['pages'][0]['text'] = state.source
    state.questions = [{'content': '1. 已知函数 $y=x^2$，求顶点。', 'answer_markdown': '顶点为 $(0,0)$。'}]
    state.questions[0][field] = state.questions[0][field].replace('x^2', 'x^3').replace('(0,0)', '(0,1)')
    _, locks = lock_visible_math(state.source, 'word')
    state.diagnostics = reconcile_visible_math(state.questions, locks, state.source)
    assert any('处公式与原文不同' in reason for reason in state.questions[0]['source_review']['reasons'])
    original = deepcopy(state.questions[0])
    state.decision.update(decision='different', evidence='原图公式与拆题结果的指数或坐标值不同，必须按原页人工核对。')
    state.decision['checks']['math_and_conditions'] = False
    state.response['choices'][0]['message']['content'] = json.dumps({'items': [state.decision]})
    report = run(state)
    assert report['calls'] == 2 and report['confirmed'] == 0 and report['pending'] == 1
    assert state.questions[0]['content'] == original['content']
    assert state.questions[0]['answer_markdown'] == original['answer_markdown']
    assert state.questions[0]['source_review']['verification_attempt']['decision'] == 'different'
    assert state.questions[0]['source_review']['repair']['status'] == 'stopped'
    assert state.questions[0]['source_review']['repair']['attempts'] == 1
    assert report['repair_calls'] == 1 and report['recheck_calls'] == 0


def test_formula_not_equal_by_local_spelling_can_be_confirmed_from_original_page(state):
    state.source = '1. 已知函数 $y=\\frac{x}{2}$，求顶点。'
    state.questions = [{'content': '1. 已知函数 $y={x\\over 2}$，求顶点。', 'answer_markdown': ''}]
    _, locks = lock_visible_math(state.source, 'word')
    state.diagnostics = reconcile_visible_math(state.questions, locks, state.source)
    assert state.questions[0]['source_review']['required']
    assert any('处公式与原文不同' in reason for reason in state.questions[0]['source_review']['reasons'])
    original = state.questions[0]['content']
    state.decision['evidence'] = '原页分式的分子为x分母为2，两种LaTeX命令表达同一完整公式。'
    state.response['choices'][0]['message']['content'] = json.dumps({'items': [state.decision]})
    assert run(state)['confirmed'] == 1
    assert state.questions[0]['content'] == original


@pytest.mark.parametrize('field', ['content', 'answer_markdown'])
def test_equivalent_formula_removes_only_native_structure_diagnostic_label(state, field):
    state.questions[0]['source_review']['reasons'] = ['Word 原生公式提取存在疑点，请对照原页核对。']
    state.questions[0][field] += ' $\\frac{x+1}{2}$ [公式结构待核对]'
    original = deepcopy(state.questions[0])
    report = run(state)
    assert report['confirmed'] == 1 and report['pending'] == 0
    question = state.questions[0]
    assert question[field] == original[field].replace('[公式结构待核对]', '')
    other = 'answer_markdown' if field == 'content' else 'content'
    assert question[other] == original[other]
    assert question['source_review']['verification']['diagnostic_labels_removed'] == 1
    assert question['source_review']['verification']['output_before_cleanup_sha256'] != question['source_review']['verification']['verified_output_sha256']
    prompt = state.calls[0][0]['messages'][0]['content'][0]['text']
    assert '_output_before_cleanup' not in prompt
    assert 'diagnostic_labels_removed' in prompt


@pytest.mark.parametrize('decision', ['different', 'uncertain'])
def test_unconfirmed_structure_label_is_not_removed(state, decision):
    state.questions[0]['content'] += ' $x$ [公式结构待核对]'
    original = state.questions[0]['content']
    state.decision['decision'] = decision
    state.response['choices'][0]['message']['content'] = json.dumps({'items': [state.decision]})
    assert run(state)['confirmed'] == 0
    assert state.questions[0]['content'] == original


def test_structure_cleanup_cannot_overwrite_changes_during_visual_request(state, monkeypatch):
    state.questions[0]['content'] += ' $x$ [公式结构待核对]'
    def post(*args, **kwargs):
        state.questions[0]['content'] += '新编辑'
        return SimpleNamespace(status_code=200, json=lambda: state.response)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    assert run(state)['confirmed'] == 0
    assert state.questions[0]['content'].endswith('[公式结构待核对]新编辑')
    assert state.questions[0]['source_review']['required']


def test_visual_formula_suspicions_remain_within_shared_call_budget(state, monkeypatch):
    state.source = '1. 已知 $x$，求结果。\n2. 已知 $y$，求结果。'
    state.questions = [
        {'content': '1. 已知 $x$ [公式结构待核对]，求结果。', 'answer_markdown': ''},
        {'content': '2. 已知 $y$ [公式结构待核对]，求结果。', 'answer_markdown': ''},
    ]
    for question in state.questions:
        question['source_review'] = {'required': True, 'reasons': ['Word 原生公式提取存在疑点，请对照原页核对。']}
    state.diagnostics = {'source_matches': [
        {'question_index': index, 'field': 'content', 'source_number': part.number,
         'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
        for index, part in enumerate(_source_parts(state.source, []))
    ]}
    state.evidence['pages'][0]['text'] = state.source
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    monkeypatch.setattr(verify, 'MAX_CALLS', 1)
    report = run(state)
    assert report['calls'] == 1 and report['confirmed'] == 1 and report['pending'] == 1
    assert report['skipped'] == 1
    assert not state.questions[0]['source_review']['required']
    assert state.questions[1]['source_review']['required']
    assert '[公式结构待核对]' in state.questions[1]['content']


def test_structure_cleanup_in_one_batch_keeps_later_batch_snapshots_valid(state, monkeypatch):
    state.source = '\n'.join(f'{index}. 已知 $x={index}$，求结果。' for index in range(1, 4))
    state.questions = [
        {'content': f'{index}. 已知 $x={index}$ [公式结构待核对]，求结果。', 'answer_markdown': '',
         'source_review': {'required': True, 'reasons': ['Word 原生公式提取存在疑点，请对照原页核对。']}}
        for index in range(1, 4)
    ]
    state.diagnostics = {'source_matches': [
        {'question_index': index, 'field': 'content', 'source_number': part.number,
         'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
        for index, part in enumerate(_source_parts(state.source, []))
    ]}
    state.evidence['pages'][0]['text'] = state.source
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(provider, payload, **kwargs):
        state.calls.append((payload, kwargs))
        index = len(state.calls)
        decision = {**state.decision, 'id': f'word_{index:03d}', 'source_number': index}
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': [decision]})}}]}
        return SimpleNamespace(status_code=200, json=lambda: response)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == 3 and report['confirmed'] == 3 and report['pending'] == 0
    assert all(not question['source_review']['required'] for question in state.questions)
    assert all('[公式结构待核对]' not in question['content'] for question in state.questions)
    assert len({question['source_review']['verification']['snapshot_hash'] for question in state.questions}) == 3


@pytest.mark.parametrize('failure', ['request', 'preparation'])
def test_failed_word_batch_reports_unrequested_questions_without_retry(state, monkeypatch, failure):
    state.source = '\n'.join(f'{index}. 已知 $x={index}$，求结果。' for index in range(1, 3))
    state.questions = [
        {'content': f'{index}. 已知 $x={index}$ [公式结构待核对]，求结果。', 'answer_markdown': '',
         'source_review': {'required': True, 'reasons': ['Word 原生公式提取存在疑点，请对照原页核对。']}}
        for index in range(1, 3)
    ]
    state.diagnostics = {'source_matches': [
        {'question_index': index, 'field': 'content', 'source_number': part.number,
         'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
        for index, part in enumerate(_source_parts(state.source, []))]}
    state.evidence['pages'][0]['text'] = state.source
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    if failure == 'preparation':
        monkeypatch.setattr(verify, '_page_content', lambda *a, **k: (_ for _ in ()).throw(ValueError('missing page')))
    else:
        def post(*a, **k):
            state.calls.append(True)
            return SimpleNamespace(status_code=503)
        monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['calls'] == (1 if failure == 'request' else 0)
    assert report['pending'] == 2 and report['confirmed'] == report['checked'] == 0
    assert report['skipped'] == (1 if failure == 'request' else 2)
    assert [item['question_index'] for item in report['skipped_reasons']] == ([1] if failure == 'request' else [0, 1])
    assert all(item['code'] == 'verification_stopped' for item in report['skipped_reasons'])
    assert all(question['source_review']['required'] for question in state.questions)


@pytest.mark.parametrize('formula', [r'$x\text{?}y$', '$x\ufffdy$', '$x\ue123y$', '', '文字'])
def test_unknown_or_missing_formula_glyphs_cannot_clear_structure_label(state, formula):
    state.questions[0]['content'] += ' ' + formula + '[公式结构待核对]'
    original = state.questions[0]['content']
    report = run(state)
    assert report['calls'] == 1 and report['confirmed'] == 0
    assert state.questions[0]['content'] == original


@pytest.mark.parametrize('field', ['content', 'answer_markdown'])
@pytest.mark.parametrize('formula', [r'$x\text{[字符待核对]}y$',
                                   '$x\\text \t{ \n[ 字符待核对 ] \t}y$'])
def test_explicit_unknown_character_marker_cannot_clear_structure_or_accept_equivalent(state, field, formula):
    state.questions[0][field] += ' ' + formula + '[公式结构待核对]'
    original = deepcopy(state.questions[0])
    report = run(state)
    assert report['calls'] == 1 and report['confirmed'] == 0 and report['pending'] == 1
    assert report['items'][0]['decision'] == 'uncertain'
    assert report['items'][0]['checks']['complete_content'] is False
    assert state.questions[0][field] == original[field]
    review = state.questions[0]['source_review']
    assert review['required'] is True and 'verification' not in review
    assert review['verification_attempt']['model_decision'] == 'equivalent'
    assert review['verification_attempt']['diagnostic_labels_removed'] == 0


@pytest.mark.parametrize('marker', [r'$x\text{[字符待核对]}y$', '[字符待核对]'])
def test_unknown_character_marker_is_pending_even_without_structure_note(state, marker):
    state.questions[0]['content'] += ' ' + marker
    original = state.questions[0]['content']
    report = run(state)
    assert report['confirmed'] == 0 and report['pending'] == 1
    assert state.questions[0]['content'] == original
    assert state.questions[0]['source_review']['required']


def test_literal_original_question_mark_is_not_treated_as_an_extractor_placeholder(state):
    state.questions[0]['content'] += ' $x?y$ [公式结构待核对]'
    report = run(state)
    assert report['confirmed'] == 1 and report['pending'] == 0
    assert '$x?y$' in state.questions[0]['content']
    assert '[公式结构待核对]' not in state.questions[0]['content']


@pytest.mark.parametrize('reason', [
    '模型返回了无法识别的公式编号，请对照原文补全公式。',
    '公式编号重复出现，请核对公式是否放错位置。',
    '题干第 1 处公式编号属于其他位置，可能发生串题或调换。',
    '多道拆分结果对应同一段原文，请核对重复题与遗漏题。',
])
def test_visual_formula_support_does_not_release_identity_conflicts(state, reason):
    state.questions[0]['source_review']['reasons'].append(reason)
    assert run(state)['calls'] == 0
    assert state.questions[0]['source_review']['required']


def test_repeated_source_number_cannot_clear_multiple_missing_fragments(state):
    part = '1. 求 $1+1$。\n\n'
    state.source = part * 2
    state.questions[0].update(content='求 $1+1$。', answer_markdown='')
    state.diagnostics['source_matches'] = [{'question_index': 0, 'field': 'content', 'source_number': 1,
                                          'source_start': 0, 'source_end': len(part), 'source_excerpt': part}]
    state.diagnostics['unmatched_source'] = [{'source_excerpt': part}, {'source_excerpt': part}]
    assert run(state)['calls'] == 0
    assert len(state.diagnostics['unmatched_source']) == 2


def test_independent_answer_pages_cannot_be_omitted():
    candidate = {'source_number': 1, 'inline_answer': False}
    pages = [{'page_number': number, 'text': '1.题干\n2.下一题' if number == 1 else '1\n.答案'} for number in range(1, 6)]
    assert verify._locate_pages(candidate, pages) == []


def test_cost_cap_uses_actual_prompt_size(state, monkeypatch):
    monkeypatch.setattr(verify, 'MAX_CHARS', 100)
    assert run(state)['calls'] == 0


def test_cancellation_is_propagated_without_call(state):
    def cancel(): raise TaskCancelled()
    with pytest.raises(TaskCancelled): run(state, check_cancelled=cancel)
    assert not state.calls


def test_missing_image_reference_is_not_a_visual_false_alarm(state):
    state.questions[0]['content'] += '\n![额外图](/static/uploads/a.png)'
    report = run(state)
    assert report['calls'] == report['confirmed'] == 0
    assert report['skipped_reasons'][0]['code'] == 'candidate_images_unavailable'
    assert state.questions[0]['source_review']['required']


def test_image_anchor_without_actual_candidate_pixels_cannot_be_confirmed(state):
    image = '![图](/static/uploads/a.png)'
    state.source = state.source.replace('求顶点。', image + ' 求顶点。')
    state.questions[0]['content'] += ' ' + image
    stem = _source_parts(state.source, [])[0]
    state.diagnostics['source_matches'][0].update(source_excerpt=stem.text, source_end=stem.source_end)
    state.questions[0]['source_review']['reasons'] = ['题干插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。']
    original = state.questions[0]['content']
    assert run(state)['confirmed'] == 0
    assert state.questions[0]['content'] == original


def _formula_preview(state, field='content'):
    path = state.image.parent / 'mathtype_candidate.png'
    picture = Image.new('RGB', (100, 30), 'white')
    ImageDraw.Draw(picture).text((8, 8), 'y=x^2', fill='black')
    picture.save(path)
    url = '/static/uploads/tmp/' + path.name
    markup = f'[公式待核对]\n![MathType 公式待核对]({url})'
    state.source = state.source.replace('$y=x^2$', markup) if field == 'content' else state.source.replace('顶点为原点。', markup)
    state.questions[0][field] = markup
    stem = _source_parts(state.source, [])[0]
    state.diagnostics['source_matches'][0].update(source_excerpt=stem.text, source_end=stem.source_end)
    state.questions[0]['source_review']['reasons'] = ['Word 原生公式提取存在疑点，请对照原页核对。']
    return path, url, markup


@pytest.mark.parametrize('field', ['content', 'answer_markdown'])
def test_complete_mathtype_preview_uses_actual_pixels_and_only_removes_diagnostic(state, field):
    path, url, markup = _formula_preview(state, field)
    original_bytes = path.read_bytes()
    report = run(state, candidate_image_paths=[url])
    assert report['confirmed'] == report['checked'] == 1 and report['pending'] == 0
    assert state.questions[0][field] == f'![MathType 公式]({url})'
    assert path.read_bytes() == original_bytes
    verification = state.questions[0]['source_review']['verification']
    assert verification['diagnostic_labels_removed'] == 1
    image = verification['candidate_image_evidence']['images'][0]
    assert image['sha256'] == hashlib.sha256(original_bytes).hexdigest()
    assert image['bindings'] == [{'id': 'word_001', 'field': field, 'occurrence': 1}]
    messages = state.calls[0][0]['messages'][0]['content']
    assert sum(item['type'] == 'image_url' for item in messages) == 2
    assert any('候选题文实际图片' in item.get('text', '') for item in messages)


@pytest.mark.parametrize('failure', ['allowlist', 'missing', 'corrupt', 'marker_only', 'wrong_alt', 'bad_glyph', 'alt_only'])
def test_incomplete_or_unowned_mathtype_preview_keeps_original_warning(state, failure):
    path, url, markup = _formula_preview(state)
    allowed = [url]
    if failure == 'allowlist':
        allowed = []
    elif failure == 'missing':
        path.unlink()
    elif failure == 'corrupt':
        path.write_bytes(b'not an image')
    elif failure == 'marker_only':
        state.questions[0]['content'] = '[公式待核对]'
    elif failure == 'wrong_alt':
        state.questions[0]['content'] = markup.replace('MathType 公式待核对', '普通配图')
    elif failure == 'alt_only':
        state.questions[0]['content'] = markup.replace('[公式待核对]\n', '')
    else:
        state.questions[0]['content'] += '\ufffd'
    original = state.questions[0]['content']
    report = run(state, candidate_image_paths=allowed)
    assert report['confirmed'] == 0 and report['pending'] == 1
    assert state.questions[0]['content'] == original


@pytest.mark.parametrize('change', ['pixels', 'allowlist', 'question'])
def test_mathtype_cleanup_cannot_accept_changed_image_or_binding(state, monkeypatch, change):
    path, url, markup = _formula_preview(state)
    allowed = [url]
    def post(*args, **kwargs):
        if change == 'pixels':
            Image.new('RGB', (100, 30), 'white').save(path)
        elif change == 'allowlist':
            allowed.clear()
        else:
            state.questions[0]['content'] += '新编辑'
        return SimpleNamespace(status_code=200, json=lambda: state.response)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state, candidate_image_paths=allowed)
    assert report['status'] == 'failed' and report['confirmed'] == report['checked'] == 0
    assert '[公式待核对]' in state.questions[0]['content']


@pytest.mark.parametrize('decision', ['different', 'uncertain'])
def test_unconfirmed_mathtype_pixels_do_not_change_candidate_text(state, decision):
    path, url, markup = _formula_preview(state)
    state.decision['decision'] = decision
    state.response['choices'][0]['message']['content'] = json.dumps({'items': [state.decision]})
    report = run(state, candidate_image_paths=[url])
    assert report['confirmed'] == 0
    assert state.questions[0]['content'] == markup


def test_mathtype_cleanup_does_not_remove_code_example_using_same_image_path(state):
    path, url, markup = _formula_preview(state)
    state.questions[0]['content'] += '\n```text\n' + markup + '\n```'
    before = state.questions[0]['content']
    report = run(state, candidate_image_paths=[url])
    # The remaining literal diagnostic is conservative and cannot be removed
    # merely because a real visible formula uses the same path elsewhere.
    assert report['confirmed'] == 0
    assert state.questions[0]['content'] == before


def test_image_anchor_with_candidate_pixels_can_be_visually_confirmed(state):
    path, url, markup = _formula_preview(state)
    state.questions[0]['content'] = f'求顶点。 ![图]({url})'
    state.questions[0]['source_review']['reasons'] = ['题干插图的引用或所在位置与原文不同，请核对缺图、错图及选项位置。']
    original = state.questions[0]['content']
    report = run(state, candidate_image_paths=[url])
    assert report['confirmed'] == 1
    assert state.questions[0]['content'] == original
    assert sum(part['type'] == 'image_url' for part in state.calls[0][0]['messages'][0]['content']) == 2


def test_candidate_image_moved_from_stem_to_answer_cannot_pass_image_count_check(state):
    path, url, markup = _formula_preview(state)
    state.questions[0]['content'] = '求顶点。'
    state.questions[0]['answer_markdown'] += f' ![图]({url})'
    report = run(state, candidate_image_paths=[url])
    assert report['confirmed'] == 0
    assert report['items'][0]['checks']['figures'] is False


def test_different_candidate_image_reference_can_be_checked_from_pixels_without_rewriting(state):
    path, url, markup = _formula_preview(state)
    replacement = state.image.parent / 'replacement.png'
    Image.new('RGB', (100, 30), 'gray').save(replacement)
    new_url = '/static/uploads/tmp/' + replacement.name
    state.questions[0]['content'] = f'![候选公式]({new_url})'
    original = state.questions[0]['content']
    report = run(state, candidate_image_paths=[new_url])
    assert report['confirmed'] == 1
    assert state.questions[0]['content'] == original
    image = report['items'][0]
    assert image['checks']['figures'] is True
    evidence = state.questions[0]['source_review']['verification']['candidate_image_evidence']
    assert [entry['path'] for entry in evidence['images']] == [new_url]


def test_broken_candidate_picture_cannot_discard_an_unrelated_valid_word_result(state):
    items = _two_question_batch(state)
    url = '/static/uploads/tmp/broken_candidate.png'
    state.source = state.source.replace('2. 已知 $y=2$', f'2. ![图]({url}) 已知 $y=2$')
    state.questions[1]['content'] += f' ![图]({url})'
    parts = _source_parts(state.source, [])
    state.diagnostics['source_matches'][1].update(source_excerpt=parts[1].text,
                                                source_start=parts[1].source_start, source_end=parts[1].source_end)
    state.response['choices'][0]['message']['content'] = json.dumps({'items': [items[0]]})
    report = run(state, candidate_image_paths=[url])
    assert report['confirmed'] == report['checked'] == report['pending'] == report['skipped'] == 1
    assert not state.questions[0]['source_review']['required']
    assert state.questions[1]['source_review']['required']
    assert report['skipped_reasons'][0]['question_index'] == 1
    assert report['skipped_reasons'][0]['code'] == 'candidate_images_unavailable'


def test_candidate_image_labels_are_included_in_request_text_cap(state, monkeypatch):
    path, url, markup = _formula_preview(state)
    original_prepare = verify.prepare_candidate_images
    def oversized_labels(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        result['messages'].append({'type': 'text', 'text': 'x' * verify.MAX_CHARS})
        return result
    monkeypatch.setattr(verify, 'prepare_candidate_images', oversized_labels)
    report = run(state, candidate_image_paths=[url])
    assert report['calls'] == 0 and report['confirmed'] == 0
    assert state.questions[0]['source_review']['required']


@pytest.mark.parametrize('mode,color', [('RGB', 'white'), ('RGB', 'black'), ('RGBA', (0, 0, 0, 0))])
def test_uniform_mathtype_preview_cannot_clear_formula_diagnostic(state, mode, color):
    path, url, markup = _formula_preview(state)
    Image.new(mode, (100, 30), color).save(path)
    report = run(state, candidate_image_paths=[url])
    assert report['confirmed'] == 0 and report['pending'] == 1
    assert state.questions[0]['content'] == markup
    assert report['items'][0]['checks']['complete_content'] is False


def _image_question_batch(state, monkeypatch, count=6, pictures_per_item=4):
    source_parts, state.questions, allowed = [], [], []
    for number in range(1, count + 1):
        markup = []
        for image_number in range(pictures_per_item):
            path = state.image.parent / f'question_{number}_image_{image_number}.png'
            Image.new('RGB', (20, 20), 'white').save(path)
            url = '/static/uploads/tmp/' + path.name
            allowed.append(url)
            markup.append(f'![图{image_number}]({url})')
        content = f'{number}. 已知 $x={number}$，求结果。' + ' '.join(markup)
        source_parts.append(content)
        state.questions.append({'content': content, 'answer_markdown': '',
                                'source_review': {'required': True, 'reasons': [
                                    '题干文字或公式位置与原文未能完整对应，请对照原文核对。']}})
    state.source = '\n'.join(source_parts)
    state.diagnostics = {'source_matches': [
        {'question_index': index, 'field': 'content', 'source_number': part.number,
         'source_start': part.source_start, 'source_end': part.source_end, 'source_excerpt': part.text}
        for index, part in enumerate(_source_parts(state.source, []))]}
    state.evidence['pages'][0]['text'] = state.source
    def post(provider, payload, **kwargs):
        state.calls.append((payload, kwargs))
        visible = json.loads(payload['messages'][0]['content'][0]['text'].split('\n')[-1])
        decisions = [{**deepcopy(state.decision), 'id': item['id'], 'source_number': item['source_number']}
                     for item in visible]
        body = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': decisions})}}]}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    return allowed


def test_candidate_image_batch_budget_splits_before_any_paid_call(state, monkeypatch):
    from mathbank import source_review_images
    allowed = _image_question_batch(state, monkeypatch)
    monkeypatch.setattr(source_review_images, 'MAX_BATCH_PIXELS', 16 * 20 * 20)
    report = run(state, candidate_image_paths=allowed)
    assert report['calls'] == 2 and report['confirmed'] == report['checked'] == 6
    assert report['pending'] == report['skipped'] == 0
    # Six questions with four 20x20 option pictures each become 4+2 questions
    # under this pixel budget; the hard image-count cap is not the limiter.
    assert [sum(part['type'] == 'image_url' for part in payload['messages'][0]['content'])
            for payload, _ in state.calls] == [17, 9]
    assert all(sum(len(part.get('text', '')) for part in payload['messages'][0]['content']) <= verify.MAX_CHARS
               for payload, _ in state.calls)


def test_image_based_batch_split_still_honors_total_call_cap(state, monkeypatch):
    from mathbank import source_review_images
    allowed = _image_question_batch(state, monkeypatch)
    monkeypatch.setattr(source_review_images, 'MAX_BATCH_PIXELS', 16 * 20 * 20)
    monkeypatch.setattr(verify, 'MAX_CALLS', 1)
    report = run(state, candidate_image_paths=allowed)
    assert report['calls'] == 1 and report['confirmed'] == report['checked'] == 4
    assert report['pending'] == report['skipped'] == 2


def test_full_image_metadata_and_binding_text_drives_word_batch_planning(state, monkeypatch):
    allowed = _image_question_batch(state, monkeypatch, count=2, pictures_per_item=1)
    candidates, _ = verify._source_candidates(state.questions, state.diagnostics, state.source)
    for candidate in candidates:
        candidate['source_pages'] = [1]
    _, combined_chars = verify._planned_batch_evidence(candidates, allowed)
    assert len(verify.prompts.build_docx_source_verification_prompt(candidates)) < combined_chars - 1
    monkeypatch.setattr(verify, 'MAX_CHARS', combined_chars - 1)
    report = run(state, candidate_image_paths=allowed)
    assert report['calls'] == 2 and report['confirmed'] == report['checked'] == 2
    assert report['pending'] == report['skipped'] == 0


def test_single_question_with_seven_small_images_uses_available_resource_budget(state, monkeypatch):
    allowed = _image_question_batch(state, monkeypatch, count=1, pictures_per_item=7)
    report = run(state, candidate_image_paths=allowed)
    assert report['calls'] == report['confirmed'] == report['checked'] == 1
    assert report['skipped'] == report['pending'] == 0


def test_single_question_candidate_pixel_limit_skips_only_affected_question(state, monkeypatch):
    from mathbank import source_review_images
    allowed = _image_question_batch(state, monkeypatch, count=1, pictures_per_item=7)
    monkeypatch.setattr(source_review_images, 'MAX_ITEM_PIXELS', 6 * 20 * 20)
    report = run(state, candidate_image_paths=allowed)
    assert report['calls'] == 0 and report['skipped'] == report['pending'] == 1
    assert report['skipped_reasons'][0]['code'] == 'candidate_images_unavailable'
    assert any('上限' in reason for reason in report['skipped_reasons'][0]['details'])


def test_word_candidate_changes_during_image_planning_cannot_be_confirmed(state, monkeypatch):
    path, url, markup = _formula_preview(state)
    original_prepare = verify.prepare_candidate_images
    def edit_during_preparation(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        state.questions[0]['content'] += '新编辑'
        return result
    monkeypatch.setattr(verify, 'prepare_candidate_images', edit_during_preparation)
    report = run(state, candidate_image_paths=[url])
    assert report['status'] == 'failed' and report['calls'] == report['checked'] == report['confirmed'] == 0
    assert state.questions[0]['content'].endswith('新编辑')
    assert state.questions[0]['source_review']['required']


@pytest.mark.parametrize('wrapper', ['`BODY`', '```text\nBODY\n```', r'\BODY',
                                    r'\detokenize{BODY}', r'\begin{verbatim}' + '\nBODY\n' + r'\end{verbatim}'])
def test_candidate_image_changed_to_literal_or_escape_is_missing_visible_evidence(state, monkeypatch, wrapper):
    allowed = _image_question_batch(state, monkeypatch, count=1, pictures_per_item=1)
    markup = f'![图0]({allowed[0]})'
    state.questions[0]['content'] = state.questions[0]['content'].replace(markup, wrapper.replace('BODY', markup))
    original = state.questions[0]['content']
    report = run(state, candidate_image_paths=allowed)
    assert report['confirmed'] == 0 and report['pending'] == 1
    assert state.questions[0]['content'] == original
    assert report['items'][0]['checks']['figures'] is False
    assert sum(part['type'] == 'image_url' for part in state.calls[0][0]['messages'][0]['content']) == 1


@pytest.mark.parametrize('original_image', ['![图0][missing_ref]', r'\includegraphics{', '<img src="unknown.png">'])
def test_unknown_original_image_reference_cannot_be_cleared_by_complete_candidate_image(state, monkeypatch, original_image):
    allowed = _image_question_batch(state, monkeypatch, count=1, pictures_per_item=1)
    state.source = state.source.replace(f'![图0]({allowed[0]})', original_image)
    stem = _source_parts(state.source, [])[0]
    state.diagnostics['source_matches'][0].update(source_excerpt=stem.text, source_end=stem.source_end)
    report = run(state, candidate_image_paths=allowed)
    assert report['confirmed'] == 0 and report['pending'] == 1
    assert report['items'][0]['checks']['figures'] is False


@pytest.mark.parametrize('failure', ['http', 'truncated', 'unknown_id', 'page_changed'])
def test_later_failed_batch_keeps_previous_confirmation_and_reports_partial(state, monkeypatch, failure):
    items = _two_question_batch(state)
    monkeypatch.setattr(verify, 'MAX_ITEMS', 1)
    def post(provider, payload, **kwargs):
        state.calls.append((payload, kwargs))
        number = len(state.calls)
        if number == 2 and failure == 'http':
            return SimpleNamespace(status_code=503)
        decision = deepcopy(items[number - 1])
        finish_reason = 'stop'
        if number == 2:
            if failure == 'truncated':
                finish_reason = 'length'
            elif failure == 'unknown_id':
                decision['id'] = 'unknown'
            elif failure == 'page_changed':
                state.image.write_bytes(b'changed')
        body = {'choices': [{'finish_reason': finish_reason, 'message': {'content': json.dumps({'items': [decision]})}}]}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(verify, 'post_chat_completion', post)
    report = run(state)
    assert report['status'] == 'partial' and report['calls'] == 2
    assert report['confirmed'] == report['checked'] == report['pending'] == 1
    assert not state.questions[0]['source_review']['required']
    assert state.questions[1]['source_review']['required']


def test_candidate_retrieval_never_confirms_by_itself(state):
    state.diagnostics['source_matches'] = []
    candidates, _ = verify._source_candidates(state.questions, state.diagnostics, state.source)
    assert candidates
    assert state.questions[0]['source_review']['required']
