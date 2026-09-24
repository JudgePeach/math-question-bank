from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from mathbank import docx_source_verify as verify
from mathbank.ai_providers import resolve_ocr_provider
from mathbank.content_locks import _source_parts
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
    assert run(state)['status'] == 'failed'


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
    assert report['status'] == 'failed' and report['confirmed'] == 0 and len(state.calls) == 1
    assert state.questions[0]['source_review']['required'] is True


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
def test_source_placeholder_cannot_be_hidden_by_output(state, marker):
    state.source = state.source.replace('$y=x^2$', marker)
    stem = _source_parts(state.source, [])[0]
    state.diagnostics['source_matches'][0].update(source_excerpt=stem.text, source_end=stem.source_end)
    assert run(state)['calls'] == 0


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
    assert run(state)['calls'] == 0


def test_candidate_retrieval_never_confirms_by_itself(state):
    state.diagnostics['source_matches'] = []
    candidates, _ = verify._source_candidates(state.questions, state.diagnostics, state.source)
    assert candidates
    assert state.questions[0]['source_review']['required']
