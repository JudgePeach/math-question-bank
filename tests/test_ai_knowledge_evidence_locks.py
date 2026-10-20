"""Knowledge quotes follow trusted formula locks without changing source text."""
from copy import deepcopy

import pytest

from mathbank.ai_knowledge_points import annotate_ai_knowledge_points
from mathbank.content_locks import (
    ContentLockIntegrityError, lock_visible_math, reconcile_visible_math, restore_visible_math,
)


CATALOG = [{"code": "local:derivative", "name": "导数"}]


def point(evidence, source="content"):
    return {"code": "local:derivative", "role": "main", "evidence_source": source,
            "evidence": evidence, "reason": "直接考查导数"}


def restore(mode, questions, locks, source, **kwargs):
    if mode == "strict":
        return restore_visible_math(questions, locks)
    return reconcile_visible_math(questions, locks, source, **kwargs)


@pytest.mark.parametrize("mode", ["strict", "reconcile"])
@pytest.mark.parametrize("kind", ["token", "tag", "modified_tag", "math_shell"])
def test_formula_evidence_uses_the_same_trusted_bytes_as_its_question(mode, kind):
    source = r"1. 已知 $f(x)=x^2$，求导数。"
    _, locks = lock_visible_math(source, "point-evidence")
    frozen_locks = deepcopy(locks)
    lock = locks[0]
    token = f"[[{lock.lock_id}]]"
    reference = {
        "token": token,
        "tag": f'<mathbank-math id="{lock.lock_id}">{lock.original}</mathbank-math>',
        "modified_tag": f'<mathbank-math id="{lock.lock_id}">$伪造公式$</mathbank-math>',
        "math_shell": "$" + token + "$",
    }[kind]
    raw_points = [point("已知 " + reference)]
    frozen_points = deepcopy(raw_points)
    questions = [{"content": f"已知 {token}，求导数。", "answer_markdown": "",
                  "knowledge_points": raw_points}]
    report = restore(mode, questions, locks, source)
    assert report["math_locks_restored"] == 1
    assert report["math_locks_overwritten"] == 0  # Point tags never affect source counters.
    assert questions[0]["content"] == "已知 $f(x)=x^2$，求导数。"
    assert questions[0]["knowledge_points"][0]["evidence"] == "已知 $f(x)=x^2$"
    assert raw_points == frozen_points and locks == frozen_locks
    if mode == "reconcile":
        once = deepcopy(questions)
        restore(mode, questions, locks, source)
        assert questions == once
    annotate_ai_knowledge_points(questions[0], CATALOG)
    assert questions[0]["knowledge_points"][0]["status"] == "confirmed"
    assert questions[0]["knowledge_point_warnings"] == []


@pytest.mark.parametrize("mode", ["strict", "reconcile"])
def test_same_formula_in_another_question_does_not_license_its_id_as_evidence(mode):
    source = "1. 关于甲，已知 $x^2$，求值。\n\n2. 关于乙，已知 $x^2$，求值。"
    _, locks = lock_visible_math(source, "same-formula")
    questions = [
        {"content": f"关于甲，已知 [[{locks[0].lock_id}]]，求值。", "answer_markdown": "",
         "knowledge_points": [point(f"[[{locks[1].lock_id}]]")]},
        {"content": f"关于乙，已知 [[{locks[1].lock_id}]]，求值。", "answer_markdown": "",
         "knowledge_points": [point(f"[[{locks[1].lock_id}]]")]},
    ]
    restore(mode, questions, locks, source)
    assert questions[0]["knowledge_points"][0]["evidence"] == f"[[{locks[1].lock_id}]]"
    assert questions[1]["knowledge_points"][0]["evidence"] == "$x^2$"
    for question in questions:
        annotate_ai_knowledge_points(question, CATALOG)
    assert questions[0]["knowledge_points"] == [] and questions[0]["knowledge_point_warnings"]
    assert questions[1]["knowledge_points"][0]["evidence"] == "$x^2$"


@pytest.mark.parametrize("mode", ["strict", "reconcile"])
def test_same_formula_in_wrong_source_field_is_rejected(mode):
    source = "1. 已知 $x^2$，求值。\n【答案】$x^2$。"
    _, locks = lock_visible_math(source, "wrong-field-point")
    q = {"content": f"已知 [[{locks[0].lock_id}]]，求值。",
         "answer_markdown": f"[EXTRACTED_ORIGINAL][[{locks[1].lock_id}]]。",
         "knowledge_points": [point(f"[[{locks[0].lock_id}]]", "answer_markdown")]}
    restore(mode, [q], locks, source)
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"] == [] and q["knowledge_point_warnings"]


@pytest.mark.parametrize("mode", ["strict", "reconcile"])
@pytest.mark.parametrize("template", [
    "[[MBM_unknown_0001]]",
    '<mathbank-math id="MBM_unknown_0001">{id}</mathbank-math>',
    '<mathbank-math data-id="{id}">$x^2$</mathbank-math>',
    '<mathbank-math id="{id}" id="MBM_other_0001">$x^2$</mathbank-math>',
])
def test_unknown_and_forged_tag_id_attributes_cannot_supply_point_evidence(mode, template):
    source = "1. 已知 $x^2$，求值。"
    _, locks = lock_visible_math(source, "unknown-point")
    evidence = template.replace("{id}", locks[0].lock_id)
    q = {"content": f"已知 [[{locks[0].lock_id}]]，求值。", "answer_markdown": "",
         "knowledge_points": [point(evidence)]}
    restore(mode, [q], locks, source)
    assert q["knowledge_points"][0]["evidence"] == evidence
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"] == [] and q["knowledge_point_warnings"]


@pytest.mark.parametrize("mode", ["strict", "reconcile"])
@pytest.mark.parametrize("literal", [
    "`{ref}`", "```text\n{ref}\n```", r"\verb|{ref}|",
    r"\detokenize{{ref}}", "<code>{ref}</code>", "https://example.invalid/{ref}",
])
def test_evidence_code_url_and_tex_literals_are_not_protocol_references(mode, literal):
    source = "1. 已知 $x^2$，求值。"
    _, locks = lock_visible_math(source, "literal-point")
    evidence = literal.replace("{ref}", f"[[{locks[0].lock_id}]]")
    q = {"content": f"已知 [[{locks[0].lock_id}]]，求值。", "answer_markdown": "",
         "knowledge_points": [point(evidence)]}
    restore(mode, [q], locks, source)
    assert q["knowledge_points"][0]["evidence"] == evidence


@pytest.mark.parametrize("mode", ["strict", "reconcile"])
def test_unknown_id_quoted_from_actual_literal_source_is_preserved(mode):
    literal = "`[[MBM_literal_example]]`"
    source = "1. 阅读示例" + literal + "，已知 $x^2$，求值。"
    _, locks = lock_visible_math(source, "literal-source-point")
    q = {"content": "阅读示例" + literal + f"，已知 [[{locks[0].lock_id}]]，求值。",
         "answer_markdown": "", "knowledge_points": [point(literal)]}
    restore(mode, [q], locks, source, tex_comments=False)
    assert q["knowledge_points"][0]["evidence"] == literal
    annotate_ai_knowledge_points(q, CATALOG)
    assert q["knowledge_points"][0]["evidence"] == literal


def test_strict_failure_is_atomic_for_evidence_as_well_as_body():
    _, locks = lock_visible_math("1. 已知 $x^2$ 和 $y^2$，求值。", "atomic-point")
    q = {"content": f"已知 [[{locks[0].lock_id}]]，求值。",
         "knowledge_points": [point(f"[[{locks[0].lock_id}]]")]}
    before = deepcopy(q)
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math([q], locks)
    assert q == before
