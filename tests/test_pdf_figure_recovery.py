"""Historical figure inputs stay local, exact and separate from review receipts."""
from copy import deepcopy

import pytest

from mathbank.pdf_figure_recovery import PdfFigureRecoveryError, prepare_recovered_figure_inputs
from mathbank.pdf_figures import describe_figure_slots


OLD = '/static/uploads/tmp/historical.png'
OTHER = '/static/uploads/tmp/pending.png'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *_a, **_kw: pytest.fail('No model call in pure recovery preparation'))


def figure(identifier='p7-f1', slot='p7-s1', path=OLD, **extra):
    return {'id': identifier, 'slot_id': slot, 'image_path': path,
            'bbox': [100, 200, 300, 400], 'attached': True,
            'crop_review': {'verified': True, 'status': 'accepted'}, **extra}


def run(source, figures, *, pending=(), first='首次机器稿保持不变。', **options):
    candidate = {'schema': 'layout-test', 'pages': [
        {'page_index': 2, 'figures': [], 'marker': 'current-unrecovered'},
        {'page_index': 6, 'figures': [], 'marker': 'old-current-page'}],
        'page_texts': ['原布局第3页不改。', '原布局第7页。'],
        'issues': [{'page_index': 2, 'reason': 'keep'}, {'page_index': 6, 'reason': 'replace'}]}
    working = {'pages': [
        {'page_index': 2, 'figures': [], 'marker': 'ignored-working-page'},
        {'page_index': 6, 'figures': figures, 'pending_figures': list(pending), 'marker': 'recovered'}],
        'issues': [{'page_index': 6, 'reason': 'historical'}]}
    inputs = (candidate, ['3. 当前好页 $x+1$。', first], working,
              ['3. 历史不应覆盖当前页。', source], [2, 6], {7})
    before = deepcopy(inputs)
    output = prepare_recovered_figure_inputs(*inputs, **options)
    assert inputs == before, 'raw/current/recovery inputs are immutable'
    return output, before


def test_only_recovered_page_owned_markdown_becomes_slot_with_exact_anchor():
    source = '17. 恢复正文  $x^2+1$，原答案保留。\n![原配图](' + OLD + ')\n下一行。'
    (layout, texts), before = run(source, [figure()])
    expected = source.replace('![原配图](' + OLD + ')', '[插图待补: 图1]')
    assert texts == [before[1][0], expected]
    assert layout['pages'][0] == before[0]['pages'][0]
    assert layout['page_texts'][0] == before[0]['page_texts'][0]
    assert layout['page_texts'][1] == expected
    row = layout['pages'][1]['figures'][0]
    slot = describe_figure_slots(expected, 6)[0]
    assert row['slot_id'] == 'p7-s1' and row['anchor_before'] == expected[:slot['end']]
    assert row['anchor_after'] == '' and row['image_path'] == OLD
    assert row['attached'] is False and row['review_required'] is True
    assert 'crop_review' not in row, 'old acceptance is not a new review receipt'
    assert layout['issues'] == [before[0]['issues'][0], {'page_index': 6, 'reason': 'historical'}]


def test_mixed_attached_and_pending_figures_keep_original_slot_text_and_identity():
    source = '16. 原题\n![图1](' + OLD + ')\n正文\n[插图待补: 图2]\n尾句。'
    success = figure()
    failed = figure('p7-f2', 'p7-s2', None, candidate_image_path=OTHER)
    (layout, texts), _ = run(source, [success], pending=[failed])
    assert texts[1] == source.replace('![图1](' + OLD + ')', '[插图待补: 图1]')
    rows = layout['pages'][1]['figures']
    assert [(row['id'], row['slot_id'], row['image_path']) for row in rows] == [
        ('p7-f1', 'p7-s1', OLD), ('p7-f2', 'p7-s2', OTHER)]
    assert layout['pages'][1]['pending_figures'] == []
    assert all(row['anchor_before'].endswith('[插图待补: 图' + str(index) + ']')
               for index, row in enumerate(rows, 1))


def test_duplicate_metadata_prefers_actual_candidate_asset_in_pending_list():
    source = '17. 原题。\n![图](' + OTHER + ')'
    empty = figure(path=None, bbox=None)
    actual = figure(path=None, candidate_image_path=OTHER)
    (layout, texts), _ = run(source, [empty], pending=[actual])
    assert len(layout['pages'][1]['figures']) == 1
    assert layout['pages'][1]['figures'][0]['image_path'] == OTHER
    assert texts[1].endswith('[插图待补: 图1]')


def test_existing_slot_without_crop_stays_for_later_localization():
    source = '17. 原文。[插图待补: 图1]  原答案 $2$。'
    (layout, texts), _ = run(source, [], pending=[figure(path=None, bbox=None)])
    assert texts[1] == source
    row = layout['pages'][1]['figures'][0]
    assert row['bbox'] is None and row['slot_id'] == 'p7-s1'


def test_code_math_comments_and_literal_image_examples_are_not_rewritten():
    examples = '\n'.join([
        '`![例](' + OLD + ')`',
        '```text\n![例](' + OLD + ')\n```',
        '<!-- ![例](' + OLD + ') -->',
        '$x+\\text{![例](' + OLD + ')}$',
        '\\[x+\\text{![例](' + OLD + ')}\\]',
        '\\begin{equation}x+\\text{![例](' + OLD + ')}\\end{equation}',
        '\\begin{tikzpicture}![例](' + OLD + ')\\end{tikzpicture}',
        '\\verb|![例](' + OLD + ')|',
        '\n    ![缩进代码](' + OLD + ')\n',
    ])
    visible = '正文 ![真实配图](' + OLD + ') 尾句 $y^2$。'
    source = examples + '\n\n' + visible
    (layout, texts), _ = run(source, [figure()])
    assert texts[1] == examples + '\n\n' + visible.replace('![真实配图](' + OLD + ')', '[插图待补: 图1]')


@pytest.mark.parametrize('image', [
    '![图](<' + OLD + '>)',
    '![图](' + OLD + ' "原标签")',
    '![图](' + OLD + " '原标签')",
])
def test_complete_markdown_angle_destination_and_title_are_owned_atomic_units(image):
    (layout, texts), _ = run('正文 ' + image + ' 尾句。', [figure()])
    assert texts[1] == '正文 [插图待补: 图1] 尾句。'


@pytest.mark.parametrize('source,rows,code', [
    ('正文 ![未知](/static/uploads/tmp/unknown.png)', [figure()], 'unknown_or_shared_image'),
    ('正文 ![外图](https://example.test/foreign.png)', [figure()], 'unknown_or_shared_image'),
    ('![一](' + OLD + ')\n![二](' + OLD + ')', [figure()], 'repeated_owned_image'),
    ('![一](' + OLD + ')', [figure(), figure('p7-f2', 'p7-s2')], 'unknown_or_shared_image'),
    ('![一](' + OLD + ')', [figure(slot='p7-s2')], 'conflicting_service_slot'),
    ('正文 \\includegraphics{' + OLD + '}', [figure()], 'unsupported_image_reference'),
    ('正文 <img src="' + OLD + '">', [figure()], 'unsupported_image_reference'),
    ('正文 ![坏图](' + OLD, [figure()], 'unsupported_image_reference'),
    ('正文只有文字。', [figure()], 'figure_slot_not_unique'),
])
def test_ambiguous_recovered_page_is_rejected_without_touching_source_or_other_pages(source, rows, code):
    with pytest.raises(PdfFigureRecoveryError) as caught:
        run(source, rows)
    assert caught.value.page_number == 7 and caught.value.code == code
    assert isinstance(caught.value, ValueError)


def test_all_inputs_remain_unchanged_even_when_later_page_rejected():
    candidate = {'pages': [{'page_index': 2}, {'page_index': 6}]}
    working = {'pages': [{'page_index': 2, 'figures': [figure('p3-f1', 'p3-s1')]},
                          {'page_index': 6, 'figures': [figure()]}]}
    inputs = (candidate, ['first3', 'first7'], working,
              ['![图](' + OLD + ')', '![未知](/static/uploads/tmp/missing.png)'], [2, 6], {3, 7})
    before = deepcopy(inputs)
    with pytest.raises(PdfFigureRecoveryError) as caught:
        prepare_recovered_figure_inputs(*inputs)
    assert caught.value.page_number == 7 and inputs == before


def test_without_recovered_pages_working_text_and_layout_are_ignored():
    candidate = {'pages': [{'page_index': 6, 'figures': [figure()]}]}
    original = deepcopy(candidate)
    layout, texts = prepare_recovered_figure_inputs(candidate, ['first $x$'], None, ['old unsafe'], [6], set())
    assert candidate == original and layout == original and layout is not candidate
    assert texts == ['first $x$']


def test_recovered_page_without_figures_keeps_all_text_bytes():
    source = '17. 历史好题 $f(x)=x^2$。\n\n原版答案  $1$。'
    (layout, texts), _ = run(source, [])
    assert texts[1] == source and layout['pages'][1]['figures'] == []


@pytest.mark.parametrize('first,working', [(None, ['old']), (['first'], None)])
def test_invalid_text_vectors_fail_as_value_error(first, working):
    with pytest.raises(ValueError):
        prepare_recovered_figure_inputs({'pages': []}, first, {'pages': []}, working, [6], {7})


def test_malformed_recovered_layout_has_page_scoped_value_error():
    with pytest.raises(PdfFigureRecoveryError) as caught:
        prepare_recovered_figure_inputs({'pages': [None]}, ['first'], {'pages': []}, ['old'], [6], {7})
    assert caught.value.page_number == 7 and caught.value.code == 'invalid_recovered_page'
