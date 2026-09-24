"""Literal Word prose/math formatting may change; mathematical evidence may not."""

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os

import pytest

from mathbank.content_locks import lock_visible_math, reconcile_visible_math


def reconcile(source, content, answer=''):
    _, locks = lock_visible_math(source, 'word-format')
    questions = [{'content': content, 'answer_markdown': answer}]
    report = reconcile_visible_math(questions, locks, source)
    return questions, report


def test_word_bare_variables_and_conditions_accept_added_math_delimiters():
    source = '题2.设a, b是实数,则“a+b>0”是“ab>0”的\nA.充分条件 B.必要条件'
    content = r'设 $a, b$ 是实数,则“$a+b>0$”是“$ab>0$”的 ()\begin{choices}\item 充分条件\item 必要条件\end{choices}'
    questions, report = reconcile(source, content)
    assert report['source_review_count'] == 0
    assert report['unmatched_source'] == []
    assert questions[0]['content'] == content
    assert report['source_matches'][0]['source_excerpt'] == source
    assert report['source_matches'][0]['source_start'] == 0
    assert report['source_matches'][0]['source_end'] == len(source)


@pytest.mark.parametrize('space', ['\u00a0', '\u1680', '\u2000', '\u2002', '\u2003', '\u2007', '\u2009', '\u2028', '\u2029', '\u202f', '\u205f', '\u3000'])
def test_word_unicode_whitespace_does_not_abort_format_comparison(space):
    from mathbank.content_locks import _delimited_plain_key
    assert _delimited_plain_key('已知a' + space + '+b>0') == _delimited_plain_key('已知a +b>0')
    _, report = reconcile('1.设a' + space + '+b>0，求值。', '设 $a+b>0$，求值。')
    assert report['source_review_count'] == 0
    _, report = reconcile('1.设a=2' + space + '8，求值。', '设 $a=28$，求值。')
    assert report['source_review_count'] == 1


def test_word_full_width_prose_punctuation_is_literal_formatting_only():
    source = '1.设a,b为实数，则a+b>0。\n【解析】方法:当a=3,b=-1时,a+b>0;当a=-3,b=-1时,a+b<0.故选D.'
    content = '设 $a$，$b$ 为实数，则 $a+b>0$。'
    answer = 'D\n\n方法：当 $a=3,b=-1$ 时，$a+b>0$；当 $a=-3,b=-1$ 时，$a+b<0$．故选D．'
    _, report = reconcile(source, content, answer)
    assert report['source_review_count'] == 0


@pytest.mark.parametrize(('source', 'returned'), [
    ('a,b', 'a；b'), ('(1,2)', '(1；2)'), ('a;b', 'a,b'),
    ('a.b', 'ab'), ('1.5', '15'), ('1,23', '12,3'),
])
def test_word_punctuation_width_compatibility_does_not_remove_math_separators(source, returned):
    _, report = reconcile('1.已知' + source + '，求值。', '已知 $' + returned + '$，求值。')
    assert report['source_review_count'] == 1


@pytest.mark.parametrize(('bare', 'latex'), [('x≤2', r'x\leqslant 2'), ('x≥2', r'x\geqslant 2')])
def test_word_new_bare_math_accepts_slanted_relation_spellings(bare, latex):
    _, report = reconcile('1.已知' + bare + '，求值。', '已知 $' + latex + '$，求值。')
    assert report['source_review_count'] == 0
    _, report = reconcile('1.已知 $' + bare + '$，求值。', '已知 $' + latex + '$，求值。')
    assert report['source_review_count'] == 1  # Existing OMML locks remain strict.


def test_word_extra_chinese_word_is_not_a_delimiter_format_change():
    _, report = reconcile('1.设集合A={1,2},B={1}，求值。', r'设集合 $A=\{1,2\}$，集合 $B=\{1\}$，求值。')
    assert report['source_review_count'] == 1


def test_word_redundant_chinese_comma_before_existing_formula_is_layout():
    source = r'1.已知实数a,b满足，$a^2+b^2=1$，求a。'
    _, report = reconcile(source, r'已知实数 $a,b$ 满足 $a^2+b^2=1$，求 $a$。')
    assert report['source_review_count'] == 0
    assert report['math_locks_restored'] == 1


def test_word_closed_interval_sentence_end_can_use_comma_before_new_paragraph():
    source = '1.已知函数值域包含[0,+∞).\n\n则x>0成立。'
    _, report = reconcile(source, '已知函数值域包含 $[0,+\\infty)$，\n\n则 $x>0$ 成立。')
    assert report['source_review_count'] == 0


@pytest.mark.parametrize(('source', 'returned'), [
    (r'1.已知a,b，$x$，求y。', r'已知 $a,b$ $x$，求 $y$。'),
    (r'1.已知1,2，$x$，求y。', r'已知 $1,2$ $x$，求 $y$。'),
    (r'1.已知f(甲,$x$)，求y。', r'已知f(甲$x$)，求 $y$。'),
    (r'1.已知甲,$x$，求y。', r'已知甲$x$，求 $y$。'),
    (r'1.已知 $f(甲,乙)$，求y。', r'已知 $f(甲乙)$，求 $y$。'),
    ('1.已知[1,2]，\n\n则x>0。', '已知 $[12]$．\n\n则 $x>0$。'),
    ('1.已知 $[1,2],$\n\n则x>0。', '已知 $[1,2]$，\n\n则 $x>0$。'),
])
def test_word_prose_comma_compatibility_never_removes_math_separators(source, returned):
    _, report = reconcile(source, returned)
    assert report['source_review_count'] == 1


@pytest.mark.parametrize('blank', [r'（ $\quad$ ）', r'( $\qquad$ )', r'（ $\;\quad$ ）'])
def test_word_empty_choice_bracket_may_wrap_only_its_spacing_in_math(blank):
    source = '1.设a+b>0，则正确的是()\nA.a>0 B.b>0'
    returned = r'设 $a+b>0$，则正确的是' + blank + r'\begin{choices}\item $a>0$\item $b>0$\end{choices}'
    _, report = reconcile(source, returned)
    assert report['source_review_count'] == 0


@pytest.mark.parametrize('blank', [r'（ $1$ ）', r'（ $x$ ）', r'（ $\quad x$ ）', r'（ $\quad$ ]'])
def test_word_nonempty_or_unpaired_choice_brackets_are_not_layout(blank):
    source = '1.设a+b>0，则正确的是()\nA.a>0 B.b>0'
    returned = r'设 $a+b>0$，则正确的是' + blank + r'\begin{choices}\item $a>0$\item $b>0$\end{choices}'
    _, report = reconcile(source, returned)
    assert report['source_review_count'] == 1


def test_word_spacing_formula_inside_content_or_option_is_still_locked():
    _, locks = lock_visible_math(r'1.内容为（$\quad$），继续讨论。\begin{choices}\item ($\quad$)\end{choices}', 'spacing')
    assert len(locks) == 2


def test_word_existing_locks_remain_atomic_next_to_new_bare_math_wrapping():
    source = r'1.设正数x,y满足x+y=3，计算 $\dfrac{x}{y}$。' + '\n【解析】当x=1时，' + r'$\dfrac{x}{y}=\dfrac12$。故答案为：2。'
    content = r'设正数 $x,y$ 满足 $x+y=3$，计算 $\frac{x}{y}$。'
    answer = '2\n\n当 $x=1$ 时，' + r'$\frac{x}{y}=\frac12$。故答案为：2。'
    questions, report = reconcile(source, content, answer)
    assert report['source_review_count'] == 0
    assert report['math_locks_restored'] == 2
    assert report['math_locks_missing'] == 0
    assert r'$\dfrac{x}{y}$' in questions[0]['content']
    assert '$x,y$' in questions[0]['content']


@pytest.mark.parametrize(('bare', 'wrapped'), [
    ('A={1,2,4}', r'A=\{1,2,4\}'),
    ('B={x|x²-4x+m=0}', r'B=\{x\mid x^2-4x+m=0\}'),
    ('A∩B=∅', r'A\cap B=\varnothing'),
    ('[0,+∞)', r'[0,+\infty)'),
    ('∀x₀∈R,x₀≥0', r'\forall x_0\in R,x_0\geq 0'),
    ('xᵢ≤2', r'x_{i}\le 2'),
])
def test_word_literal_symbol_encodings_are_equivalent(bare, wrapped):
    _, report = reconcile('1.已知' + bare + '，求解。', '已知 $' + wrapped + '$，求解。')
    assert report['source_review_count'] == 0


@pytest.mark.parametrize(('bare', 'wrapped'), [
    ('a+b>0', 'a+b<0'), ('a+b>0', 'a+b>=0'), ('a+b>0', 'a-b>0'),
    ('a+b>0', 'a+b>1'), ('ab>0', 'ac>0'), ('x^2', 'x^3'),
    ('x_1', 'x_2'), ('x_{a+b}', 'x_a+b'), ('{1,2}', '12'),
    ('(1,2]', '[1,2]'), ('A∩B', r'A\cup B'),
    ('∀x∈R', r'\exists x\in R'), ('a,b', 'ab'), ('a、b', 'ab'),
    ('x!', 'x'), ('x|y', 'xy'), ('1,23', '12,3'),
    ('.5', '5'), ('1.50', '1.5'), ('2 8', '28'),
    ('3·5', '3 5'), ('3′5', '3 5'), ('3*5', '3 5'),
    ('a∥b', 'ab'), ('a⊥b', 'ab'), ('α', 'a'), ('α', 'β'),
    ('R', r'\mathbf{R}'), ('v', r'\vec{v}'), ('CnP', r'\complement_R P'),
    ('x', r'\unknown{x}'), ('x', r'\text{x}'),
    ('{1,2}', '{1,2}'), ('a.b=1', 'ab=1'), ('a . b=1', 'ab=1'),
])
def test_word_bare_wrapping_cannot_hide_math_changes(bare, wrapped):
    _, report = reconcile('1.已知' + bare + '，求结果。', '已知 $' + wrapped + '$，求结果。')
    assert report['source_review_count'] == 1


@pytest.mark.parametrize('returned', ['$2$8', '2$8$', '$2$$8$', '$2$ $8$'])
def test_word_partial_numeric_wrapping_stays_invalid_with_extra_variables(returned):
    _, report = reconcile('1.设x=28，求x。', '设 $x$=' + returned + '，求 $x$。')
    assert report['source_review_count'] == 1


@pytest.mark.parametrize('returned', ['$x^$ $2$', '$x$^$2$', '$x$_$2$'])
def test_word_script_may_not_cross_model_math_delimiters(returned):
    _, report = reconcile('1.已知x²，求值。', '已知' + returned + '，求值。')
    assert report['source_review_count'] == 1


@pytest.mark.parametrize('returned', [
    r'设 $x=1$，有 $x^2-1$，求值。',
    r'设 $x=1$，有 $x^2+1$，求值。',
])
def test_word_added_delimiters_do_not_remove_or_change_source_formula_locks(returned):
    # The second case is unchanged as visible text, but deletes a separately
    # protected formula boundary by merging it into the new model formula.
    source = r'1.设x=1，有 $x^2$+1，求值。'
    _, report = reconcile(source, returned)
    assert report['source_review_count'] == 1
    assert report['math_locks_missing'] == 1


@pytest.mark.parametrize('changed', ['去掉不', '改量词', '交换选项'])
def test_word_added_delimiters_preserve_conditions_and_options(changed):
    source = '1.并非所有x都满足x>0。\nA.x>0 B.x<0'
    content = r'并非所有 $x$ 都满足 $x>0$。\begin{choices}\item $x>0$\item $x<0$\end{choices}'
    if changed == '去掉不':
        content = content.replace('并非', '')
    elif changed == '改量词':
        content = content.replace('所有', '存在')
    else:
        content = content.replace(r'\item $x>0$\item $x<0$', r'\item $x<0$\item $x>0$')
    _, report = reconcile(source, content)
    assert report['source_review_count'] == 1


def test_word_added_math_delimiters_preserve_image_prefix_and_path():
    source = '1.设a,b满足a+b>0。\n![](/static/uploads/a.png)\n求ab的值。'
    correct = '设 $a,b$ 满足 $a+b>0$。\n![](/static/uploads/a.png)\n求 $ab$ 的值。'
    _, report = reconcile(source, correct)
    assert report['source_review_count'] == 0
    for content in (correct.replace('a.png', 'b.png'), correct.replace('![](/static/uploads/a.png)\n', '') + '\n![](/static/uploads/a.png)'):
        _, report = reconcile(source, content)
        assert report['source_review_count'] == 1


def test_word_repeated_source_with_added_delimiters_is_not_unique():
    source = '1.设a+b>0，求值。\n\n2.设a+b>0，求值。'
    _, report = reconcile(source, '设 $a+b>0$，求值。')
    assert report['source_review_count'] == 1


def test_word_extra_delimiters_do_not_disambiguate_source_by_formula_value():
    source = '1.设x为实数，计算 $x+1$。\n\n2.设x为实数，计算 $x+2$。'
    _, locks = lock_visible_math(source, 'ambiguous-word')
    questions = [{'content': '设 $x$ 为实数，计算 $x+1$。'},
                 {'content': '设 $x$ 为实数，计算 $x+2$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 2
    assert len(report['unmatched_source']) == 2


@pytest.mark.parametrize(('bare', 'wrapped'), [
    ('x∈R', r'x\in\mathbf{R}'), ('x∉R', r'x\notin\mathbf{R}'),
    ('x₀∈R', r'x_0\in\mathbf{R}'),
])
def test_word_real_domain_font_only_in_explicit_membership_context(bare, wrapped):
    _, report = reconcile('1.已知' + bare + '，求值。', '已知 $' + wrapped + '$，求值。')
    assert report['source_review_count'] == 0


@pytest.mark.parametrize('name', ['定义域', '值域'])
def test_word_real_domain_font_only_after_explicit_domain_name(name):
    _, report = reconcile('1.函数f(x)的' + name + '为R。', '函数 $f(x)$ 的' + name + r'为 $\mathbf{R}$。')
    assert report['source_review_count'] == 0


@pytest.mark.parametrize('prefix', ['设R={1,2}，', 'R表示集合，', '向量条件下，', '矩阵条件下，', '已知R，', '半径为R，'])
def test_word_real_domain_font_is_not_inferred_for_a_locally_defined_R(prefix):
    _, report = reconcile('1.' + prefix + '已知x∈R，求值。', prefix + r'已知 $x\in\mathbf{R}$，求值。')
    assert report['source_review_count'] == 1


def test_word_R_definition_in_answer_prevents_stem_font_inference():
    _, report = reconcile('1.已知x∈R，求值。\n【解析】这里R={1,2}。', r'已知 $x\in\mathbf{R}$，求值。', '这里R={1,2}。')
    assert report['source_review_count'] == 1


def test_word_real_domain_font_does_not_rewrite_existing_omml_lock():
    _, report = reconcile(r'1.已知 $x\in R$，求x。', r'已知 $x\in\mathbf{R}$，求 $x$。')
    assert report['source_review_count'] == 1
    assert report['math_locks_missing'] == 1


@pytest.mark.parametrize('suffix', ['1', '_1', '^2', '₁', '²', 'ᵢ', '(x)', '（x）', "'", '\u0302'])
def test_word_domain_R_with_an_attached_index_is_not_bare_real_domain(suffix):
    source = '1.已知x∈R' + suffix + '，函数定义域为R。'
    content = r'已知 $x\in\mathbf{R}' + suffix + r'$，函数定义域为 $\mathbf{R}$。'
    _, report = reconcile(source, content)
    assert report['source_review_count'] == 1


@pytest.mark.parametrize('suffix', ['′', '₁', '(x)'])
def test_word_R_modifier_outside_new_math_delimiters_still_disqualifies_font_change(suffix):
    _, report = reconcile('1.已知x∈R' + suffix + '，求值。', r'已知 $x\in\mathbf{R}$' + suffix + '，求值。')
    assert report['source_review_count'] == 1


def test_word_one_cell_answer_table_is_comparison_only_with_original_offsets():
    source = '1.求x的值。\n' + r'\begin{tabular}{|c|}' + '\n' + r'\hline' + '\n【解析】由x=1得到结果。故答案为：1。 ' + '\\\\\n' + r'\hline' + '\n' + r'\end{tabular}'
    content = '求 $x$ 的值。'
    answer = '1\n\n由 $x=1$ 得到结果。故答案为：1。'
    questions, report = reconcile(source, content, answer)
    assert report['source_review_count'] == 0
    assert questions[0]['content'] == content
    for match in report['source_matches']:
        assert source[match['source_start']:match['source_end']] == match['source_excerpt']
    assert r'\begin{tabular}' in report['source_matches'][0]['source_excerpt']
    assert r'\end{tabular}' in report['source_matches'][1]['source_excerpt']


@pytest.mark.parametrize(('columns', 'body'), [
    ('cc', '【解析】x=1 & y=2'),
    ('c', r'【解析】x=1 \\ y=2'),
    ('c', r'【解析】\multicolumn{1}{c}{x=1}'),
    ('c', r'【解析】\multirow{1}{*}{x=1}'),
    ('c', r'【解析】\begin{tabular}{c}x=1\end{tabular}'),
    ('c', '已知x=1，求值。'),
])
def test_word_complex_or_stem_tables_are_not_silently_flattened(columns, body):
    source = '1.求x的值。\n' + '\\begin{tabular}{' + columns + '}\n' + body + '\n\\end{tabular}'
    _, report = reconcile(source, '求 $x$ 的值。', '由 $x=1$ 得到结果。')
    assert report['source_review_count'] == 1


def test_word_single_cell_answer_table_does_not_hide_changed_formula():
    source = r'1.求x。\begin{tabular}{c}' + '\n【解析】' + r'$x=1$' + '\n' + r'\end{tabular}'
    _, report = reconcile(source, '求 $x$。', '$x=2$')
    assert report['source_review_count'] == 1
    assert report['math_locks_missing'] == 1


@pytest.mark.parametrize('columns', ['cc', 'c'])
def test_word_nested_one_cell_answer_table_keeps_all_wrappers(columns):
    from mathbank.content_locks import _answer_table_comparison_layout
    source = '1.求x的值。\n\\begin{tabular}{' + columns + '}\n说明 & ' + r'\begin{tabular}{c}' + '\n【解析】由x=1得到结果。\n' + r'\end{tabular}\end{tabular}'
    assert _answer_table_comparison_layout(source) == source
    _, report = reconcile(source, '求 $x$ 的值。\n\\begin{tabular}{' + columns + '}说明 &', r'由 $x=1$ 得到结果。\end{tabular}')
    assert report['source_review_count'] == 1


def test_word_one_cell_answer_table_containing_another_stem_keeps_its_wrapper():
    from mathbank.content_locks import _answer_table_comparison_layout
    source = '1.求x的值。\n' + r'\begin{tabular}{c}' + '\n【解析】由x=1得到结果。\n2.已知y=2，求y。\n' + r'\end{tabular}'
    assert _answer_table_comparison_layout(source) == source


@pytest.mark.skipif(not os.environ.get('MATHBANK_WORD_SOURCE_AUDIT'), reason='local Word audit fixture is opt-in')
def test_word_xuejun_full_cached_result_replay_keeps_unproven_changes():
    base = Path(os.environ['MATHBANK_WORD_SOURCE_AUDIT'])
    extracted = json.loads((base / 'native-extract.json').read_text())
    task = json.loads((base / 'task.json').read_text())
    source = extracted['markdown']
    for new, old in zip(extracted['image_paths'], task['diagnostics']['asset_paths']):
        assert hashlib.sha256((base / 'native-assets' / Path(new).name).read_bytes()).digest() == hashlib.sha256((base / 'assets' / Path(old).name).read_bytes()).digest()
        source = source.replace(new, old)
    _, locks = lock_visible_math(source, 'word-audit')
    questions = deepcopy(task['data'])
    for question in questions:
        question.pop('source_review', None)
    report = reconcile_visible_math(questions, locks, source)
    assert len(questions) == 18
    cleared = [index + 1 for index, question in enumerate(questions) if not question.get('source_review', {}).get('required')]
    assert cleared == [1, 2, 4, 6, 7, 9, 11, 12, 13]
    assert report['source_review_count'] == 9
    assert report['math_locks_created'] == 142
    for match in report['source_matches']:
        assert source[match['source_start']:match['source_end']] == match['source_excerpt']


@pytest.mark.parametrize(('task_name', 'remaining'), [
    ('task.json', [1, 3, 5, 8, 10, 14, 15, 17, 18]),
    ('6fc9bbd2-ac99-4267-b588-be1f6c95cfe5.json', [3, 5, 8, 10, 14, 15, 17, 18]),
])
@pytest.mark.skipif(not (os.environ.get('MATHBANK_WORD_SOURCE_AUDIT') and os.environ.get('MATHBANK_WORD_RECHECK_AUDIT')),
                    reason='both local Word audit fixtures are opt-in')
def test_word_new_cached_result_uses_hash_bound_assets_and_retains_actual_edits(task_name, remaining):
    native = Path(os.environ['MATHBANK_WORD_SOURCE_AUDIT'])
    latest = Path(os.environ['MATHBANK_WORD_RECHECK_AUDIT'])
    extracted = json.loads((native / 'native-extract.json').read_text())
    task = json.loads((latest / task_name).read_text())
    identities = {}
    for path in extracted['image_paths']:
        digest = hashlib.sha256((native / 'native-assets' / Path(path).name).read_bytes()).hexdigest()
        identities.setdefault(digest, []).append(path)
    source = extracted['markdown']
    mapped = set()
    # Deliberately reverse order: association must use bytes, never item order.
    for path in reversed(task['diagnostics']['asset_paths']):
        digest = hashlib.sha256((latest / 'assets' / Path(path).name).read_bytes()).hexdigest()
        assert len(identities[digest]) == 1
        original = identities[digest][0]
        assert original not in mapped
        source = source.replace(original, path)
        mapped.add(original)
    assert len(mapped) == 7
    _, locks = lock_visible_math(source, 'latest-word-audit')
    questions = deepcopy(task['data'])
    for question in questions:
        question.pop('source_review', None)
    report = reconcile_visible_math(questions, locks, source)
    assert len(questions) == 18
    assert [index + 1 for index, question in enumerate(questions)
            if question.get('source_review', {}).get('required')] == remaining
    assert report['source_review_count'] == len(remaining)
    if task_name == 'task.json':
        assert questions[0]['source_review']['required']  # Added Chinese word remains visible.
    for match in report['source_matches']:
        assert source[match['source_start']:match['source_end']] == match['source_excerpt']
