"""Independent late-boundary checks; every transport is a local fixture."""

import pytest

from mathbank.source_metadata import SourceMetadataContractError
from mathbank.word_hybrid_plan import WordHybridPlanError
from mathbank.word_hybrid_request import request_word_hybrid
from mathbank.task_manager import TaskCancelled
from test_word_hybrid_safety import native_plan, reply_for, Response, CURRICULUM, PROVIDER, FIELDS


def test_source_change_during_final_local_adoption_is_a_hard_stop(tmp_path):
    plan = native_plan(tmp_path)
    posts = []
    normalized = False

    def post(_provider, payload, **_options):
        posts.append(True)
        return Response(reply_for(plan, payload))

    def normalize(value):
        nonlocal normalized
        if not normalized:
            normalized = True
            plan['source'] += '\nOriginal source changed after HTTP completion.'
        return value

    with pytest.raises((WordHybridPlanError, SourceMetadataContractError)):
        request_word_hybrid(plan, CURRICULUM, provider=PROVIDER, post=post, diagnostics={},
            normalize_fillin=normalize, full_source_fallback=lambda: pytest.fail('Source change paid fallback'),
            task_id='independent-hybrid', generation=3)
    assert len(posts) == 1


def test_source_change_during_original_full_fallback_cannot_return_stale_result(tmp_path):
    plan = native_plan(tmp_path)
    posts = []; fallbacks = []

    def post(_provider, payload, **_options):
        posts.append(True)
        parsed = reply_for(plan, payload)
        parsed['metadata']['items'][0]['id'] = 'unknown-id-requires-full-fallback'
        return Response(parsed)

    def fallback():
        fallbacks.append(True)
        plan['source'] += '\nSource changed while full fallback was in flight.'
        return [{'content':'Stale result', 'answer_markdown':'', **FIELDS}]

    with pytest.raises((WordHybridPlanError, SourceMetadataContractError)):
        request_word_hybrid(plan, CURRICULUM, provider=PROVIDER, post=post, diagnostics={},
            normalize_fillin=lambda value:value, full_source_fallback=fallback,
            task_id='independent-hybrid', generation=3)
    assert len(posts) == 1 and len(fallbacks) == 1


def test_original_asset_change_inside_local_adoption_revokes_result_even_with_valid_http(tmp_path):
    plan = native_plan(tmp_path, with_image=True)
    asset = next(tmp_path.glob('*.png'))
    posts = []; normalized = 0
    target = sum(len(group['question_ids']) for group in plan['groups'] if group['route'] == 'metadata')

    def post(_provider, payload, **_options):
        posts.append(True)
        return Response(reply_for(plan, payload))

    def normalize(value):
        nonlocal normalized
        normalized += 1
        if normalized == target:
            asset.write_bytes(b'Changed after final HTTP proof check')
        return value

    with pytest.raises((WordHybridPlanError, SourceMetadataContractError)):
        request_word_hybrid(plan, CURRICULUM, provider=PROVIDER, post=post, diagnostics={},
            normalize_fillin=normalize, full_source_fallback=lambda: pytest.fail('Asset change paid fallback'),
            task_id='independent-hybrid', generation=3)
    assert len(posts) == 1


def test_cancellation_inside_final_local_adoption_never_returns_a_used_result(tmp_path):
    plan = native_plan(tmp_path)
    cancelled = False; posts = []
    def check():
        if cancelled: raise TaskCancelled('Cancelled during local source reconstruction')
    def post(_provider, payload, **_options):
        posts.append(True); return Response(reply_for(plan, payload))
    def normalize(value):
        nonlocal cancelled
        cancelled = True
        return value
    with pytest.raises(TaskCancelled):
        request_word_hybrid(plan, CURRICULUM, provider=PROVIDER, post=post, diagnostics={},
            normalize_fillin=normalize, full_source_fallback=lambda: pytest.fail('Cancelled local adoption paid fallback'),
            task_id='independent-hybrid', generation=3, check_cancelled=check)
    assert len(posts) == 1


def test_partial_missing_plain_original_answer_is_present_in_ui_unmatched_excerpts(tmp_path, monkeypatch):
    import json
    import uuid
    import main
    from mathbank.source_metadata import prepare_word_source_metadata
    from test_word_hybrid_flow import blob

    monkeypatch.setenv('PREFER_PARSE_MODEL', 'DEEPSEEK/deepseek-flash')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'isolated-no-network')
    monkeypatch.setattr(main, 'TMP_UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(main, 'get_current_curriculum', lambda: CURRICULUM)
    monkeypatch.setattr('requests.sessions.Session.request',
        lambda *_args, **_kwargs: pytest.fail('Network forbidden'))
    extract_original = main.extract_docx_markdown
    captured = {}; posts = []

    def extract(*args, **kwargs):
        native = extract_original(*args, **kwargs)
        captured.update(native)
        return native

    def post(_provider, payload, **_options):
        posts.append(payload)
        envelope = json.loads(payload['messages'][1]['content'])
        return Response({'metadata': {'items': [
            {'id': row['id'], **FIELDS} for row in envelope['metadata_items']
        ]}, 'splits': []})

    monkeypatch.setattr(main, 'extract_docx_markdown', extract)
    monkeypatch.setattr(main, 'post_chat_completion', post)
    task_id = 'independent-partial-ui-' + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type='docx', temp_assets=[])
    try:
        main.run_docx_parsing_task(task_id, blob(), 'isolated-ui.docx', docx_verify_suspicions=False)
        result = main.DOCUMENT_TASKS.snapshot(task_id)
        assert result['status'] == 'completed', result.get('error')
        assert result['diagnostics']['word_hybrid']['partial'] is True
        assert len(posts) == 2 and len(result['data']) == 2
        inspection = prepare_word_source_metadata(captured['markdown'], captured['diagnostics'],
            inspect_ineligible=True)
        original = next(q for q in inspection['questions'] if q['source_number'] == 2)
        # import.js displays these fields; a complete cache alone cannot satisfy this check.
        display_source = '\n\n'.join(row['source_excerpt']
            for row in result['diagnostics']['unmatched_source'])
        assert original['raw_content'].strip() in display_source
        assert original['raw_answer'].strip() in display_source
        assert original['raw_answer'].strip() in result['docx_source_cache']['source_markdown']
    finally:
        main.DOCUMENT_TASKS.remove(task_id)
