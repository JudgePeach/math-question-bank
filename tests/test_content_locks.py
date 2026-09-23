import re
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mathbank.content_locks import (
    ContentLockIntegrityError,
    lock_visible_math,
    restore_visible_math,
    reconcile_visible_math,
)


def test_visible_math_lock_keeps_formula_inline_and_restores_exact_source():
    source = r"已知 $f(x)=\dfrac{x+1}{x-1}$，价格为 \$5，且 $$A=\begin{matrix}1&2\\3&4\end{matrix}$$。"
    locked, locks = lock_visible_math(source, "task-1")
    assert len(locks) == 2
    assert r"$f(x)=\dfrac{x+1}{x-1}$" in locked
    assert r"$$A=\begin{matrix}1&2\\3&4\end{matrix}$$" in locked
    assert r"价格为 \$5" in locked

    questions = [{
        "content": f"已知 [[{locks[0].lock_id}]]，且 [[{locks[1].lock_id}]]。",
        "answer_markdown": "",
    }]
    report = restore_visible_math(questions, locks)
    assert report["math_locks_restored"] == 2
    assert questions[0]["content"] == (
        r"已知 $f(x)=\dfrac{x+1}{x-1}$，且 $$A=\begin{matrix}1&2\\3&4\end{matrix}$$。"
    )


def test_visible_math_lock_overwrites_model_modified_formula_inside_tag():
    locked, locks = lock_visible_math(r"求 $x^2+1$ 的最小值。", "task2")
    modified = locked.replace("x^2+1", "x^2-1")
    questions = [{"content": modified, "answer_markdown": ""}]
    report = restore_visible_math(questions, locks)
    assert questions[0]["content"] == r"求 $x^2+1$ 的最小值。"
    assert report["math_locks_overwritten"] == 1


@pytest.mark.parametrize("content", ["公式被删除", "[[{id}]] 和 [[{id}]]"])
def test_visible_math_lock_rejects_missing_or_duplicate_ids(content):
    _locked, locks = lock_visible_math(r"计算 $1+1$。", "task3")
    questions = [{"content": content.format(id=locks[0].lock_id), "answer_markdown": ""}]
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math(questions, locks)


def test_docx_task_restores_formula_before_returning_questions():
    from main import DOCUMENT_TASKS, run_docx_parsing_task

    task_id = "word-lock-integration"
    extracted = {
        "success": True,
        "markdown": r"1. 已知 $f(x)=x^2+1$，求最小值。",
        "image_count": 0,
        "image_paths": [],
        "diagnostics": {
            "omml_converted": 1,
            "mtef_converted": 0,
            "review_required": 0,
        },
    }

    def fake_parse(locked_text, _generate_answers, **_kwargs):
        assert '<mathbank-math id="' in locked_text
        assert r"$f(x)=x^2+1$" in locked_text
        lock_id = re.search(r'id="(MBM_[^"]+)"', locked_text).group(1)
        return [{
            "content": f"1. 已知 [[{lock_id}]]，求最小值。",
            "answer_markdown": "",
            "question_type": "detailed_answer",
            "referenced_images": [],
        }]

    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    with patch("main.extract_docx_markdown", return_value=extracted):
        with patch("main.parse_paper_text_internal", side_effect=fake_parse):
            run_docx_parsing_task(task_id, b"fake-docx", "公式锁定测试.docx")

    task = DOCUMENT_TASKS.snapshot(task_id)
    DOCUMENT_TASKS.remove(task_id)
    assert task["status"] == "completed"
    assert task["data"][0]["content"] == r"1. 已知 $f(x)=x^2+1$，求最小值。"
    assert task["diagnostics"]["math_locks_restored"] == 1


def test_strict_restore_does_not_partially_mutate_on_integrity_error():
    _, locks = lock_visible_math('1. 计算 $x$ 与 $y$。', 'atomic')
    content = f'计算 [[{locks[0].lock_id}]]。'
    questions = [{'content': content, 'answer_markdown': ''}]
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math(questions, locks)
    assert questions[0]['content'] == content


@pytest.mark.parametrize('reference_mode', ['all', 'none', 'mixed'])
def test_reconcile_accepts_ids_or_matching_local_formula_slots(reference_mode):
    source = '1. 计算 $x+1$ 与 $y-2$ 的和。\n\n2. 求函数 $f(x)=x^2$ 的最小值。'
    _, locks = lock_visible_math(source, 'compatible')
    values = [f'[[{lock.lock_id}]]' if reference_mode == 'all' or reference_mode == 'mixed' and index % 2 == 0
              else lock.original for index, lock in enumerate(locks)]
    questions = [
        {'content': f'计算 {values[0]} 与 {values[1]} 的和。', 'answer_markdown': ''},
        {'content': f'求函数 {values[2]} 的最小值。', 'answer_markdown': ''},
    ]
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_restored'] == 3
    assert report['math_locks_missing'] == report['source_review_count'] == 0
    assert not report['unmatched_source']
    assert questions[0]['content'] == '计算 $x+1$ 与 $y-2$ 的和。'
    assert report['math_locks_by_id'] == {'all': 3, 'none': 0, 'mixed': 2}[reference_mode]


def test_reconcile_repeated_formulas_use_their_local_slots_and_choices():
    source = '1．已知 $BC=2$，求 $BC$ 的值。\nA．$BC$ B．$AB$ C．$BC$ D．$AC$\n\n2．比较 $BC$ 与 $AC$。'
    _, locks = lock_visible_math(source, 'repeated')
    questions = [
        {'content': r'已知 $BC=2$，求 $BC$ 的值。\begin{choices}\item $BC$\item $AB$\item $BC$\item $AC$\end{choices}'},
        {'content': '比较 $BC$ 与 $AC$。'},
    ]
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_by_content'] == 8
    assert report['source_review_count'] == 0
    # Same count, different option location: this must not pass a multiset check.
    questions[0]['content'] = questions[0]['content'].replace(r'\item $AB$\item $BC$', r'\item $BC$\item $AB$')
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert report['math_locks_missing'] == 2


def test_reconcile_same_prose_in_different_questions_is_not_global_formula_matching():
    source = '1. 计算 $x+1$。\n\n2. 计算 $x+2$。'
    _, locks = lock_visible_math(source, 'ambiguous')
    questions = [{'content': '计算 $x+1$。'}, {'content': '计算 $x+2$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 2
    assert report['math_locks_by_content'] == 0
    assert len(report['unmatched_source']) == 2


def test_reconcile_swapped_ids_on_identical_stems_still_requires_order_review():
    source = '1. 计算 $x+1$。\n\n2. 计算 $x+2$。'
    _, locks = lock_visible_math(source, 'identical-stems')
    questions = [{'content': f'计算 [[{locks[1].lock_id}]]。'},
                 {'content': f'计算 [[{locks[0].lock_id}]]。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 2
    assert any('顺序' in reason for reason in questions[0]['source_review']['reasons'])


def test_reconcile_changed_sign_is_preserved_for_review_and_does_not_discard_other_questions():
    source = '1. 已知 $x^2-3=5x$，求根。\n\n2. 求 $a+b$ 的值。'
    _, locks = lock_visible_math(source, 'sign')
    questions = [{'content': '已知 $x^2+3=5x$，求根。'}, {'content': '求 $a+b$ 的值。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert report['math_locks_missing'] == 1
    assert '$x^2+3=5x$' in questions[0]['content']
    assert '$x^2-3=5x$' in questions[0]['source_review']['source_excerpt']
    assert 'source_review' not in questions[1]


@pytest.mark.parametrize('use_ids', [False, True])
def test_reconcile_cross_question_formula_swap_is_flagged(use_ids):
    source = '1. 已知方程 $x^2-1=0$，求根。\n\n2. 已知函数 $y=x^2+1$，求最小值。'
    _, locks = lock_visible_math(source, 'swap')
    values = [f'[[{lock.lock_id}]]' if use_ids else lock.original for lock in locks]
    questions = [{'content': f'已知方程 {values[1]}，求根。'}, {'content': f'已知函数 {values[0]}，求最小值。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 2
    assert report['math_locks_missing'] == 2
    assert all('MBM_' not in question['content'] for question in questions)


def test_reconcile_missing_whole_question_and_non_question_formulas_remain_visible():
    source = '考试须知：取 $\\pi=3.14$。\n\n1. 计算 $1+1$。\n\n2. 说明单循环比赛的含义。\n\n3. 求 $x^2$ 的最小值。'
    _, locks = lock_visible_math(source, 'dropped')
    questions = [{'content': '计算 $1+1$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert len(report['unmatched_source']) == 3
    assert any('单循环' in item['source_excerpt'] for item in report['unmatched_source'])
    assert any(r'$\pi=3.14$' in item['source_excerpt'] for item in report['unmatched_source'])


def test_reconcile_missing_formula_is_not_compensated_by_answer():
    source = '1. 已知方程 $x^2-1=0$，求根。'
    _, locks = lock_visible_math(source, 'answer-mask')
    questions = [{'content': '已知方程，求根。', 'answer_markdown': '$x^2-1=0$'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert report['math_locks_restored'] == 0


def test_reconcile_original_answer_separate_section_has_its_own_slots():
    source = '1. 计算 $1+1$。\n\n参考答案：\n\n1. $2$。'
    _, locks = lock_visible_math(source, 'answers')
    questions = [{'content': '计算 $1+1$。', 'answer_markdown': '[EXTRACTED_ORIGINAL] $2$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_by_content'] == 2
    assert report['source_review_count'] == 0
    assert not report['unmatched_source']


def test_reconcile_missing_original_answer_reports_its_source():
    source = '1. 计算 $1+1$。\n【答案】$2$。'
    _, locks = lock_visible_math(source, 'inline-answer')
    questions = [{'content': '计算 $1+1$。', 'answer_markdown': ''}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert '$2$' in questions[0]['source_review']['source_excerpt']
    assert report['math_locks_missing'] == 1


def test_reconcile_content_and_answer_differences_keep_both_source_fragments():
    source = '1. 已知 $x=1$，求值。\n【答案】$2$。'
    _, locks = lock_visible_math(source, 'both-fields')
    questions = [{'content': '已知 $x=3$，求值。', 'answer_markdown': '[EXTRACTED_ORIGINAL]$4$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert '$x=1$' in questions[0]['source_review']['source_excerpt']
    assert '$2$' in questions[0]['source_review']['source_excerpt']


def test_reconcile_duplicate_and_unknown_ids_never_leave_raw_protocol_markers():
    source = '1. 已知 $x$ 与 $y$，求和。'
    _, locks = lock_visible_math(source, 'bad-ids')
    questions = [{'content': f'已知 [[{locks[0].lock_id}]] 与 [[{locks[0].lock_id}]]，求和。',
                  'answer_markdown': '[[MBM_unknown_0001]]'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == report['math_locks_duplicated'] == 1
    assert 'MBM_' not in repr(questions)
    assert '无法识别' in questions[0]['answer_markdown']


def test_reconcile_no_locks_still_reports_lost_numbered_questions_and_unknown_ids():
    source = '1. 说明平行线的定义。\n\n2. 说明三角形的定义。'
    questions = [{'content': '说明平行线的定义。', 'answer_markdown': '[[MBM_unknown_0001]]'}]
    report = reconcile_visible_math(questions, [], source)
    assert len(report['unmatched_source']) == 1
    assert '三角形' in report['unmatched_source'][0]['source_excerpt']
    assert 'MBM_' not in questions[0]['answer_markdown']


@pytest.mark.parametrize('content', [
    '如图，已知 $x=1$，求值。',
    '如图，已知 $x=1$，求值。\n![](/static/uploads/wrong.png)',
    '如图，![](/static/uploads/a.png)已知 $x=1$，求值。',
])
def test_reconcile_missing_changed_or_relocated_image_requires_review(content):
    source = '1. 如图，已知 $x=1$，求值。\n![](/static/uploads/a.png)'
    _, locks = lock_visible_math(source, 'images')
    questions = [{'content': content, 'referenced_images': ['/static/uploads/a.png']}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1
    assert any('插图' in reason for reason in questions[0]['source_review']['reasons'])


def test_reconcile_image_only_question_is_not_silently_omitted():
    source = '1. ![](/static/uploads/full-question.png)\n2. 计算 $1+1$。'
    _, locks = lock_visible_math(source, 'image-question')
    questions = [{'content': '2. 计算 $1+1$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert len(report['unmatched_source']) == 1
    assert 'full-question.png' in report['unmatched_source'][0]['source_excerpt']


def test_reconcile_original_answer_marker_does_not_certify_a_hallucinated_plain_answer():
    source = '1. 说明平行线的定义。'
    questions = [{'content': '说明平行线的定义。', 'answer_markdown': '[EXTRACTED_ORIGINAL]A'}]
    report = reconcile_visible_math(questions, [], source)
    assert report['source_review_count'] == 1
    assert '原卷' in questions[0]['source_review']['reasons'][0]


@pytest.mark.parametrize(('original', 'changed'), [('20%', '20'), ('1.5', '15'), ('2÷3', '23'), ('-3', '3'), ('2/3', '23')])
def test_reconcile_bare_mathematical_symbols_are_not_discarded(original, changed):
    source = f'1. 参加活动人数为全班的{original}，求参加活动的人数。'
    questions = [{'content': f'参加活动人数为全班的{changed}，求参加活动的人数。'}]
    report = reconcile_visible_math(questions, [], source)
    assert report['source_review_count'] == 1


@pytest.mark.parametrize('changed', [r'$\sinx$', r'$\text{ab}$', '$x^{2}+1$'])
def test_reconcile_does_not_equate_control_words_visible_text_or_algebra(changed):
    original = r'$\sin x$' if 'sin' in changed else r'$\text{a b}$' if 'text' in changed else '$1+x^{2}$'
    source = f'1. 已知表达式 {original}，求值。'
    _, locks = lock_visible_math(source, 'conservative')
    questions = [{'content': f'已知表达式 {changed}，求值。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1


def test_reconcile_presentation_spacing_delimiters_and_fraction_style_restore_original():
    source = r'1. 求 $\dfrac{x+1}{x-1}$ 的值。'
    _, locks = lock_visible_math(source, 'style')
    questions = [{'content': r'求 \(\frac{ x + 1 }{ x - 1 }\) 的值。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 0
    assert questions[0]['content'] == r'求 $\dfrac{x+1}{x-1}$ 的值。'


def test_reconcile_tex_top_level_items_do_not_split_choice_items_or_subquestions():
    source = r'''\begin{enumerate}
\item 计算 $x+1$ 的值。
\begin{choices}\item $1$\item $2$\end{choices}
\item 已知 $y=2x$，回答问题。
\begin{enumerate}\item 求 $x$。\item 求 $y$。\end{enumerate}
\end{enumerate}'''
    _, locks = lock_visible_math(source, 'tex')
    questions = [
        {'content': r'计算 $x+1$ 的值。\begin{choices}\item $1$\item $2$\end{choices}'},
        {'content': r'已知 $y=2x$，回答问题。\begin{enumerate}\item 求 $x$。\item 求 $y$。\end{enumerate}'},
    ]
    report = reconcile_visible_math(questions, locks, source)
    assert report['math_locks_restored'] == 6
    assert report['source_review_count'] == 0
    assert not report['unmatched_source']


def test_reconcile_unsegmented_source_is_conservative_if_multiple_questions_cannot_be_located():
    source = '计算 $x$。\n\n计算 $y$。'
    _, locks = lock_visible_math(source, 'unsegmented')
    questions = [{'content': '计算 $x$。'}, {'content': '计算 $y$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 2
    assert report['math_locks_restored'] == 0
    assert report['unmatched_source']


@pytest.mark.skipif(not os.getenv('MATHBANK_LOCK_REAL_DOCX'), reason='optional local user-provided Word fixture')
def test_reconcile_real_docx_without_ids_with_model_choices_and_no_question_numbers(tmp_path):
    from mathbank.docx_helper import extract_docx_markdown
    from mathbank.content_locks import _source_parts

    extracted = extract_docx_markdown(Path(os.environ['MATHBANK_LOCK_REAL_DOCX']), output_dir=tmp_path)
    assert extracted['success']
    source = extracted['markdown']
    _, locks = lock_visible_math(source, 'real-word')
    questions = []
    for part in _source_parts(source, locks):
        if part.field != 'content':
            continue
        content = re.sub(r'^\d+[．.、]', '', part.text.strip())
        choice_start = re.search(r'(?<![A-Za-z])A．', content)
        if choice_start:
            stem, choices = content[:choice_start.start()], content[choice_start.start():]
            choices = re.sub(r'(?<![A-Za-z])[A-D]．', lambda _m: r'\item ', choices)
            content = stem + r'\begin{choices}' + choices + r'\end{choices}'
        questions.append({'content': content, 'answer_markdown': ''})
    report = reconcile_visible_math(questions, locks, source)
    assert len(questions) == 24
    assert len(locks) == report['math_locks_by_content'] == 158
    assert report['source_review_count'] == report['math_locks_missing'] == 0
    assert not report['unmatched_source']
