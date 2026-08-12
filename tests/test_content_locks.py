import re
from unittest.mock import patch

import pytest

from mathbank.content_locks import (
    ContentLockIntegrityError,
    lock_visible_math,
    restore_visible_math,
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

    def fake_parse(locked_text, _generate_answers):
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
