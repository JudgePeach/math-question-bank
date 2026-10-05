"""Local source reconciliation accepts spelling, never inferred mathematics."""

import pytest

from mathbank.content_locks import lock_visible_math, reconcile_visible_math


def _reconcile_formula(original, returned, *, bare_prefix=False):
    # This shared post-splitting path receives native PDF and Word source alike.
    prefix = '设a为实数，' if bare_prefix else ''
    source = '1.' + prefix + '已知 $' + original + '$，求值。'
    _, locks = lock_visible_math(source, 'native-spelling')
    content = ('设 $a$ 为实数，' if bare_prefix else '') + '已知 $' + returned + '$，求值。'
    questions = [{'content': content, 'answer_markdown': ''}]
    report = reconcile_visible_math(questions, locks, source)
    return source, questions, report


@pytest.mark.parametrize(('original', 'returned'), [
    (r'x\le 2', r'x\leq 2'), (r'x\ge 2', r'x\geq 2'),
    (r'x\ne 2', r'x\neq 2'),
    ('x≤2', r'x\leq 2'), ('x≥2', r'x\geq 2'), ('x≠2', r'x\neq 2'),
    ('x<2', r'x\lt 2'), ('x>2', r'x\gt 2'),
    ('x^{2}+a_{i}', 'x^2+a_i'), ('x_{1}^{n}', 'x_1^n'),
    (r'\dfrac{x^{2}}{a_{1}}\le 2', r'\frac{x^2}{a_1}\leq 2'),
])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('bare_prefix', [False, True])
def test_native_formula_literal_spellings_restore_original_without_review(original, returned, reverse, bare_prefix):
    if reverse:
        original, returned = returned, original
    source, questions, report = _reconcile_formula(original, returned, bare_prefix=bare_prefix)
    assert report['source_review_count'] == 0
    assert report['math_locks_restored'] == 1
    assert report['math_locks_missing'] == 0
    assert report['unmatched_source'] == []
    assert '$' + original + '$' in questions[0]['content']
    assert report['source_matches'][0]['source_excerpt'] == source


@pytest.mark.parametrize(('original', 'returned'), [
    (r'x\le 2', r'x\geq 2'), (r'x\le 2', 'x<2'),
    (r'x\ne 2', 'x=2'), ('x≤2', 'x≤3'), ('x≤2', 'y≤2'),
    (r'x\leqslant 2', r'x\leq 2'), (r'x\geqslant 2', r'x\geq 2'),
    ('x^{12}', 'x^12'), ('x^{n+1}', 'x^n+1'), ('x_{ij}', 'x_ij'),
    ('x^{2}', 'x_2'), ('x_{1}', 'x_2'), ('{x+y}^2', 'x+y^2'),
    (r'A=\{1,2\}', 'A={1,2}'), ('[1,2)', '[1,2]'),
    (r'\vec{a}', r'\mathbf{a}'), (r'\overrightarrow{AB}', 'AB'),
    (r'\mathbb{R}', r'\mathbf{R}'), (r'\mathrm{x}', 'x'),
    (r'\text{x^{2}}', r'\text{x^2}'), (r'\text{a b}', r'\text{ab}'),
    (r'\text{x≤2}', r'\text{x\leq 2}'),
    (r'\operatorname{f_{i}}', r'\operatorname{f_i}'),
    (r'\sin x', r'\sinx'), (r'\unknown{x}', 'x'),
    (r'2\quad8', '28'), ('x^{2}+1', '1+x^2'),
    (r'\frac{1}{2}', '0.5'),
])
def test_native_formula_spelling_comparison_preserves_real_differences(original, returned):
    _, questions, report = _reconcile_formula(original, returned)
    assert report['source_review_count'] == 1
    assert report['math_locks_missing'] == 1
    assert questions[0]['source_review']['required'] is True
    assert '$' + returned + '$' in questions[0]['content']


@pytest.mark.parametrize('wrapper', [
    r'\verb|%s|', r'\verb*|%s|', r'\Verb|%s|', r'\lstinline|%s|',
    r'\mintinline{tex}|%s|', r'\detokenize{%s}', r'\url{%s}',
    r'\nolinkurl{%s}', r'\path|%s|', r'\href{%s}{link}',
])
@pytest.mark.parametrize(('before', 'after'), [
    ('x^{2}', 'x^2'), (r'x\le 2', r'x\leq 2'),
    ('a b', 'ab'), (r'\dfrac{1}{2}', r'\frac{1}{2}'),
])
@pytest.mark.parametrize('bare_prefix', [False, True])
def test_literal_macro_contents_never_use_math_spelling_equivalence(wrapper, before, after, bare_prefix):
    original, returned = wrapper % before, wrapper % after
    _, questions, report = _reconcile_formula(original, returned, bare_prefix=bare_prefix)
    assert report['source_review_count'] == 1
    assert report['math_locks_restored'] == 0
    assert report['math_locks_missing'] == 1
    assert questions[0]['source_review']['required'] is True
    assert '$' + returned + '$' in questions[0]['content']


@pytest.mark.parametrize('formula', [r'\verb|x^{2}|', r'\detokenize{x\le 2}', r'\url{a_b}'])
def test_identical_literal_macro_formula_still_restores_source(formula):
    _, _, report = _reconcile_formula(formula, formula)
    assert report['source_review_count'] == 0
    assert report['math_locks_restored'] == 1


def test_literal_spelling_does_not_disambiguate_identical_source_stems():
    source = r'1.已知 $x\le 2$，求值。' + '\n\n' + r'2.已知 $x\ge 2$，求值。'
    _, locks = lock_visible_math(source, 'ambiguous')
    questions = [{'content': r'已知 $x\leq 2$，求值。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert questions[0]['source_review']['required'] is True
    assert report['math_locks_restored'] == 0


@pytest.mark.parametrize(('original', 'returned', 'locally_equivalent'), [
    ('x≤2', r'x\leq 2', True), (r'x\ge 2', r'x\geq 2', True),
    (r'x\ne 2', r'x\neq 2', True), ('x^{2}+a_{1}', 'x^2+a_1', True),
    (r'\verb|x^{2}|', r'\verb|x^2|', False),
    (r'\detokenize{x^{2}}', r'\detokenize{x^2}', False),
    (r'\verb|x\le 2|', r'\verb|x\leq 2|', False),
])
def test_native_pdf_import_visually_checks_only_real_formula_spelling_differences(monkeypatch, original, returned, locally_equivalent):
    """Exercise real adapter/task/reconciliation; stub only external outputs."""
    from types import SimpleNamespace
    import uuid

    import pymupdf as fitz
    import main
    from mathbank import pdf_inspector_helper, pdf_page_vision, pdf_region_vision, pdf_source_verify

    def forbidden(*args, **kwargs):
        pytest.fail('Literal formula spelling must not trigger a visual request')

    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setattr(pdf_page_vision, 'request_pdf_page', forbidden)
    monkeypatch.setattr(pdf_region_vision, 'request_pdf_regions', forbidden)
    verification_calls = []

    def verify(questions, diagnostics, *args, **kwargs):
        assert not locally_equivalent
        assert questions[0]['source_review']['required'] is True
        verification_calls.append(True)
        return {'status': 'completed', 'calls': 1, 'checked': 1, 'confirmed': 0, 'pending': 1}

    monkeypatch.setattr(pdf_source_verify, 'verify_pdf_source_suspicions', verify)
    monkeypatch.setattr(main, 'ocr_pdf_page_image', forbidden)
    source = '1.已知 $' + original + '$，求值。'
    monkeypatch.setattr(pdf_inspector_helper, '_PDF_INSPECTOR_AVAILABLE', True)
    monkeypatch.setattr(pdf_inspector_helper, 'pdf_inspector', SimpleNamespace(
        extract_pages_markdown=lambda *a, **k: SimpleNamespace(pages=[SimpleNamespace(
            page=0, markdown=source, needs_ocr=False)])))
    monkeypatch.setattr(main, 'inspect_and_extract_pdf', pdf_inspector_helper.inspect_and_extract_pdf)
    split_inputs = []

    def split(text, *args, **kwargs):
        split_inputs.append(text)
        return [{'content': '已知 $' + returned + '$，求值。', 'answer_markdown': ''}]

    monkeypatch.setattr(main, 'parse_paper_text_internal', split)
    with fitz.open() as document:
        document.new_page()
        file_bytes = document.tobytes()
    task_id = 'formula-spelling-' + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type='pdf', temp_assets=[])
    try:
        main.run_pdf_parsing_task(task_id, file_bytes, 'formula-spelling.pdf', pdf_strategy='layout_aware')
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task['status'] == 'completed', task.get('error')
        assert len(split_inputs) == 1 and '<mathbank-math ' in split_inputs[0]
        assert task['pdf_source_pages'][0]['origin'] == 'native'
        preserved_formula = original if locally_equivalent else returned
        assert task['data'][0]['content'] == '已知 $' + preserved_formula + '$，求值。'
        assert task['diagnostics']['math_locks_restored'] == int(locally_equivalent)
        assert task['diagnostics']['source_review_count'] == int(not locally_equivalent)
        assert task['diagnostics']['pdf_source_verification']['calls'] == int(not locally_equivalent)
        assert verification_calls == ([] if locally_equivalent else [True])
        if locally_equivalent:
            assert task['diagnostics']['pdf_source_verification']['status'] == 'no_candidates'
        else:
            assert task['data'][0]['source_review']['required'] is True
        extraction = task['diagnostics']['pdf_extraction']
        assert extraction['native_pages'] == 1
        assert extraction['regional_pages'] == extraction['full_vision_pages'] == 0
    finally:
        removed = main.DOCUMENT_TASKS.remove(task_id)
        if removed:
            main._delete_task_temp_assets(removed.get('temp_assets', []))
