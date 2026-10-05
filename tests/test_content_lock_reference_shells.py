"""A sole lock reference may have a redundant model-added math shell."""

from copy import deepcopy

import pytest

from mathbank.content_locks import (
    ContentLockIntegrityError, lock_visible_math, reconcile_visible_math, restore_visible_math,
)


SHELLS = [('$', '$'), ('$$', '$$'), (r'\(', r'\)'), (r'\[', r'\]')]
FRAGMENTS = [
    '$AB$', '$$x^2+1$$', r'\(x+1\)', r'\[x^2\]',
    r'\begin{align}x&=1\\y&=2\end{align}',
    r'$\begin{tabular}{cc}甲&乙\\丙&丁\end{tabular}$',
]


@pytest.mark.parametrize('fragment', FRAGMENTS)
@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('kind', ['token', 'xml', 'bare'])
def test_sole_known_reference_shell_restores_the_exact_fragment(fragment, shell, kind):
    source = '1. 已知 ' + fragment + '，求值。'
    _, locks = lock_visible_math(source, 'shell-source')
    before_locks = deepcopy(locks)
    assert len(locks) == 1
    lock = locks[0]
    reference = (f'[[{lock.lock_id}]]' if kind == 'token' else lock.lock_id if kind == 'bare'
                 else f'<mathbank-math id="{lock.lock_id}">{lock.original}</mathbank-math>')
    model = '已知 ' + shell[0] + ' \t' + reference + '\r\n ' + shell[1] + '，求值。'
    questions = [{'content': model, 'answer_markdown': ''}]
    if kind != 'bare':  # The strict API historically accepts tokens/XML only.
        strict = deepcopy(questions)
        report = restore_visible_math(strict, locks)
        assert strict[0]['content'] == '已知 ' + fragment + '，求值。'
        assert report['math_locks_restored'] == 1 and report['math_locks_overwritten'] == 0
    report = reconcile_visible_math(questions, locks, source)
    assert questions[0]['content'] == '已知 ' + fragment + '，求值。'
    assert report['math_locks_restored'] == report['math_locks_by_id'] == 1
    assert report['math_locks_missing'] == report['source_review_count'] == 0
    assert not report['unmatched_source']
    for match in report['source_matches']:
        assert source[match['source_start']:match['source_end']] == match['source_excerpt']
    assert locks == before_locks


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('body', ['$x+999$', '% modified body', '`unfinished example', 'https://model.invalid/$99'])
def test_wrapped_modified_xml_keeps_the_existing_overwrite_rule(shell, body):
    source = '1. 已知 $x+1$，求值。'
    _, locks = lock_visible_math(source, 'xml-source')
    xml = f'<mathbank-math id="{locks[0].lock_id}">{body}</mathbank-math>'
    questions = [{'content': '已知 ' + shell[0] + xml + shell[1] + '，求值。', 'answer_markdown': ''}]
    strict = deepcopy(questions)
    assert restore_visible_math(strict, locks)['math_locks_overwritten'] == 1
    assert strict[0]['content'] == '已知 $x+1$，求值。'
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_overwritten'] == report['math_locks_restored'] == 1
    assert report['source_review_count'] == 0
    assert questions[0]['content'] == strict[0]['content']


@pytest.mark.parametrize('template', [
    '${ref}+x$', '$x+{ref}$', '$x+\\({ref}\\)$', '$$$' + '{ref}' + '$$$',
    r'\begin{equation}\({ref}\)\end{equation}',
])
def test_compound_nested_and_ambiguous_shells_do_not_gain_a_new_normalization(template):
    source = '1. 已知 $AB$，求值。'
    _, locks = lock_visible_math(source, 'compound-source')
    reference = f'[[{locks[0].lock_id}]]'
    # A simple replacement preserves the pre-existing behavior outside a sole shell.
    wrapped = template.replace('{ref}', reference)
    before = '已知 ' + wrapped + '，求值。'
    expected = before.replace(reference, locks[0].original)
    strict = [{'content': before, 'answer_markdown': ''}]
    restore_visible_math(strict, locks)
    assert strict[0]['content'] == expected
    questions = [{'content': before, 'answer_markdown': ''}]
    report = reconcile_visible_math(questions, locks, source)
    assert questions[0]['content'] == expected
    # The old prose comparer discards plain dollars; this fix does not change
    # that decision for an ambiguous triple-dollar model output.
    expected_pending = 0 if template == '$$${ref}$$$' else 1
    assert report['math_locks_missing'] == report['source_review_count'] == expected_pending


def test_multiple_references_in_one_shell_are_not_collapsed():
    source = '1. 已知 $AB$ 与 $CD$，求值。'
    _, locks = lock_visible_math(source, 'multiple-source')
    references = [f'[[{lock.lock_id}]]' for lock in locks]
    model = '已知 $' + references[0] + '+' + references[1] + '$，求值。'
    questions = [{'content': model, 'answer_markdown': ''}]
    report = reconcile_visible_math(questions, locks, source)
    assert questions[0]['content'] == '已知 $$AB$+$CD$$，求值。'
    assert report['math_locks_missing'] == 2 and report['source_review_count'] == 1


@pytest.mark.parametrize('literal', [
    '`{shell}`', '```tex\n{shell}\n```', '~~~tex\n{shell}\n~~~',
    r'\begin{tikzpicture}{shell}\end{tikzpicture}',
    r'\begin{verbatim*}{shell}\end{verbatim*}', r'\verb|{shell}|',
    r'\detokenize{{shell}}', '<code>{shell}</code>', '% {shell}',
    'https://model.invalid/{shell}',
])
def test_literal_examples_keep_the_old_reference_span(literal):
    from mathbank.content_locks import _formulas
    _, locks = lock_visible_math('$AB$', 'literal-source')
    lock = locks[0]
    token = f'[[{lock.lock_id}]]'
    value = literal.replace('{shell}', '$' + token + '$')
    formulas = _formulas(value, {lock.lock_id: lock}, shell_ids={lock.lock_id})
    reference = next(formula for formula in formulas if formula.lock_id == lock.lock_id)
    assert value[reference.start:reference.end] == token
    questions = [{'content': value, 'answer_markdown': ''}]
    restore_visible_math(questions, locks)
    assert questions[0]['content'] == value.replace(token, lock.original)


@pytest.mark.parametrize('location', ['same_field', 'other_field', 'other_question'])
def test_duplicate_shell_ids_do_not_weaken_exactly_once_and_strict_restore_is_atomic(location):
    source = '1. 已知 $AB$，求值。'
    _, locks = lock_visible_math(source, 'duplicate-source')
    wrapped = f'$[[{locks[0].lock_id}]]$'
    questions = [{'content': '已知 ' + wrapped + '，求值。', 'answer_markdown': ''}]
    if location == 'same_field': questions[0]['content'] += wrapped
    elif location == 'other_field': questions[0]['answer_markdown'] = wrapped
    else: questions.append({'content': wrapped, 'answer_markdown': ''})
    before = deepcopy(questions)
    with pytest.raises(ContentLockIntegrityError): restore_visible_math(questions, locks)
    assert questions == before
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_duplicated'] == report['math_locks_missing'] == 1
    assert report['source_review_count'] >= 1
    assert '$$AB$$' in repr(questions)  # Duplicate wrappers were not consumed.


def test_later_missing_id_does_not_commit_an_earlier_shell_replacement():
    _, locks = lock_visible_math('1. 已知 $AB$ 与 $CD$，求值。', 'atomic-shell')
    questions = [{'content': f'已知 $[[{locks[0].lock_id}]]$，求值。', 'answer_markdown': ''}]
    before = deepcopy(questions)
    with pytest.raises(ContentLockIntegrityError): restore_visible_math(questions, locks)
    assert questions == before


def test_unknown_shell_id_stays_unverified_and_strict_missing_is_atomic():
    source = '1. 已知 $AB$，求值。'
    _, locks = lock_visible_math(source, 'unknown-shell')
    questions = [{'content': '已知 $[[MBM_foreign_0001]]$，求值。', 'answer_markdown': ''}]
    before = deepcopy(questions)
    with pytest.raises(ContentLockIntegrityError): restore_visible_math(questions, locks)
    assert questions == before
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_restored'] == 0
    assert report['math_locks_missing'] == report['source_review_count'] == 1


def test_wrong_source_field_or_slot_still_requires_review():
    source = '1. 已知 $AB$，求值。\n【答案】$CD$。'
    _, locks = lock_visible_math(source, 'wrong-field')
    questions = [{'content': f'已知 $[[{locks[1].lock_id}]]$，求值。',
                  'answer_markdown': f'[EXTRACTED_ORIGINAL]$[[{locks[0].lock_id}]]$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_restored'] == 0 and report['math_locks_missing'] == 2
    assert report['source_review_count'] == 1
    assert any('其他位置' in reason for reason in questions[0]['source_review']['reasons'])


def test_swapping_wrapped_ids_across_questions_does_not_certify_them():
    source = '1. 已知 $AB$，求值。\n\n2. 已知 $CD$，求值。'
    _, locks = lock_visible_math(source, 'wrong-slot')
    questions = [{'content': f'1. 已知 $[[{locks[1].lock_id}]]$，求值。'},
                 {'content': f'2. 已知 $[[{locks[0].lock_id}]]$，求值。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_restored'] == 0 and report['math_locks_missing'] == 2
    assert report['source_review_count'] == 2


@pytest.mark.parametrize('first_kind', ['reference', 'copied_math'])
def test_math_containing_url_cannot_hide_the_following_reference_shell(first_kind):
    fragment = r'$\text{https://example.test/x}$'
    source = '1. 已知 ' + fragment + ' 与 $AB$，求值。'
    _, locks = lock_visible_math(source, 'url-in-math')
    first = f'$[[{locks[0].lock_id}]]$' if first_kind == 'reference' else fragment
    model = '已知 ' + first + f' 与 $[[{locks[1].lock_id}]]$，求值。'
    questions = [{'content': model, 'answer_markdown': ''}]
    if first_kind == 'reference':
        strict = deepcopy(questions)
        restore_visible_math(strict, locks)
        assert strict[0]['content'] == source[3:]
    report = reconcile_visible_math(questions, locks, source)
    assert questions[0]['content'] == source[3:]
    assert report['math_locks_restored'] == 2 and report['source_review_count'] == 0
