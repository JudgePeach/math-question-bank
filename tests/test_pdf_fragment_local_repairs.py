"""Source-exact v4 local relationship repairs; no model or whole-paper parser."""
from copy import deepcopy

import pytest
import requests

from mathbank.pdf_fragment_draft import RULES_VERSION, prepare_pdf_fragment_draft
from mathbank.pdf_fragment_sources import prepare_pdf_fragment_sources, text_sha
from mathbank.pdf_page_fragments import SCHEMA, validate_pdf_page_fragments


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_k: pytest.fail("No API for local repair"))
    monkeypatch.setattr("mathbank.pdf_local_draft.prepare_pdf_local_draft",
                        lambda *_a, **_k: pytest.fail("No whole-paper fallback"))


def block(role, number, text, edge="none"):
    return {"role": role, "number": number, "text": text, "edge": edge}


def page(number, blocks):
    raw = {"schema": SCHEMA, "blocks": blocks, "figures": [], "ignored_candidates": [],
           "page_complete": True, "warnings": []}
    parsed = validate_pdf_page_fragments(raw, {"page_index": number - 1, "candidates": []})
    return {"page_number": number, "markdown": parsed["markdown"], "fragments": parsed["fragments"]}


def build(pages):
    frozen = deepcopy(pages)
    canonical = prepare_pdf_fragment_sources(pages)
    frozen_canonical = deepcopy(canonical)
    plan = prepare_pdf_fragment_draft(canonical["source"], canonical["source_pages"],
                                     source_document_sha256="d" * 64)
    assert pages == frozen and canonical == frozen_canonical
    assert plan["source"] == canonical["source"]
    assert plan["source_sha256"] == text_sha(canonical["source"])
    for item, question in zip(plan["items"], plan["questions"]):
        for field, key in (("content", "stem_ranges"), ("answer_markdown", "answer_ranges")):
            assert question[field] == "".join(canonical["source"][a:b] for a, b in item[key])
    for receipt in plan["fragment_repairs"]:
        a, b = receipt["parent_range"]
        assert receipt["parent_text_sha256"] == text_sha(canonical["source"][a:b])
        for part in receipt["parts"]:
            a, b = part["range"]
            assert part["text_sha256"] == text_sha(canonical["source"][a:b])
    assert plan["rules_version"] == RULES_VERSION == "pdf-fragment-draft.rules.v4"
    return canonical, plan


SECTION = "一、选择题：本题共 8 小题，每小题 5 分，共 40 分。在每小题给出的四个选项中，只有一项是符合题目要求的。\n"


def test_complete_section_instruction_becomes_context_with_source_receipt():
    raw = [page(1, [block("context", None, "试卷标题\n"), block("stem", None, SECTION),
                    block("stem", 1, "1. 原题 $a_1$ 与 $i$。\n"),
                    block("stem", 2, "2. 后题原文")])]
    _, plan = build(raw)
    assert not plan["partial"] and not plan["unresolved_ranges"]
    assert plan["questions"][0]["content"] == "原题 $a_1$ 与 $i$。\n"
    assert all(question["section_context"] == SECTION for question in plan["questions"])
    assert all(question["question_type"] == "single_choice" for question in plan["questions"])
    assert not any(row["page_number"] == 1 for row in plan["page_issues"])
    assert any(row["text"] == SECTION and row["role"] == "document_context" for row in plan["unassigned"])
    receipt = plan["fragment_repairs"][0]
    assert receipt["original_role"] == "stem" and receipt["derived_role"] == "context"
    assert receipt["requires_review"] is False
    assert receipt["original_pending_reasons"] == ["number_missing"]
    assert receipt["original_block_text_sha256"] == text_sha(SECTION)


@pytest.mark.parametrize("text", [
    "一、选择题\n", SECTION + "1. 另有一道真实题目\n", "原题引用：" + SECTION,
    "```\n" + SECTION + "```", "$" + SECTION + "$", SECTION.replace("本题共 8 小题", "设某题共 8 小题"),
    SECTION.replace("一、选择题", "一、填空题"), SECTION.replace("一、选择题", "一、解答题"),
])
def test_section_recovery_requires_the_entire_conventional_instruction(text):
    _, plan = build([page(1, [block("stem", None, text), block("stem", 1, "1. 独立题")])])
    assert not any(row["reason"] == "fragment_section_context_recovered" for row in plan["fragment_repairs"])
    assert any(row["text"] == text for row in plan["unresolved_ranges"])


@pytest.mark.parametrize("fault", ["other_role", "wrong_edge", "unknown_edge", "barrier", "page_boundary", "no_following"])
def test_section_recovery_requires_an_adjacent_same_page_printed_stem(fault):
    instruction = block("stem", None, SECTION)
    next_block = block("stem", 1, "1. 原题")
    blocks = [instruction, next_block]
    if fault == "other_role": instruction["role"] = "unknown"
    if fault == "wrong_edge": instruction["edge"] = "from_previous"
    if fault == "unknown_edge": instruction["edge"] = "unknown"
    if fault == "barrier": blocks.insert(1, block("context", None, "另一栏目\n"))
    if fault == "no_following": blocks.pop()
    pages = [page(1, blocks)] if fault != "page_boundary" else [page(1, [instruction]), page(2, [next_block])]
    _, plan = build(pages)
    assert not any(row["reason"] == "fragment_section_context_recovered" for row in plan["fragment_repairs"])


TAIL = "由于 $0\\leqslant p\\leqslant 1$，原文 $p<\\dfrac{4}{11}$。 ……………… 15分\n"


def answer_case(edge="none", tail=TAIL):
    return [block("stem", 17, "17. 原题十七\n"), block("stem", 18, "18. 原题十八\n"),
            block("answer", 17, "17. 解：(1) 原答案前段\n"), block("answer", None, tail, edge),
            block("answer", 18, "18. 解：后一题原答案")]


def test_same_page_none_tail_is_attached_between_unique_consecutive_answer_heads():
    canonical, plan = build([page(1, answer_case())])
    assert plan["questions"][0]["answer_markdown"] == "解：(1) 原答案前段\n" + TAIL
    assert plan["questions"][1]["answer_markdown"] == "解：后一题原答案"
    assert not plan["unresolved_ranges"] and plan["partial"]
    assert "fragment_answer_same_page_tail_recovered" in plan["questions"][0]["source_review"]["reasons"]
    assert "number_missing" not in plan["manual_reasons"]
    sources = plan["items"][0]["field_sources"]["answer_markdown"]
    assert [row["edge"] for row in sources] == ["to_next", "from_previous"]
    assert [row["original_edge"] for row in sources] == ["none", "none"]
    receipt = next(row for row in plan["fragment_repairs"] if row.get("source_number") == 17)
    assert receipt["original_block_text_sha256"] == text_sha(TAIL)
    assert receipt["following_head"]["number"] == 18
    assert canonical["source"].count(TAIL) == 1
    assert plan["questions"][0]["source_review"]["original_verified"] is False


@pytest.mark.parametrize("fault", [
    "duplicate_stem", "duplicate_answer", "duplicate_following_stem", "duplicate_following_answer",
    "context", "unknown_role", "furniture", "unknown_edge", "not_consecutive", "hidden_number", "numbered_tail",
])
def test_no_tail_adoption_without_unique_adjacent_same_page_proof(fault):
    blocks = answer_case()
    if fault == "duplicate_stem": blocks.insert(0, block("stem", 17, "17. 另一个题干\n"))
    if fault == "duplicate_answer": blocks.append(block("answer", 17, "\n17. 解：重复原答"))
    if fault == "duplicate_following_stem": blocks.insert(0, block("stem", 18, "18. 重复后题\n"))
    if fault == "duplicate_following_answer": blocks.append(block("answer", 18, "\n18. 解：重复后答"))
    if fault in {"context", "unknown_role", "furniture"}:
        blocks.insert(3, block("unknown" if fault == "unknown_role" else fault, None, "边界资料\n"))
    if fault == "unknown_edge": blocks[3]["edge"] = "unknown"
    if fault == "not_consecutive": blocks[4] = block("answer", 19, "19. 解：另一题原答案")
    if fault == "hidden_number": blocks[3]["text"] = "示例段落\n4. 可能是另一道题\n" + TAIL
    if fault == "numbered_tail": blocks[3] = block("answer", 19, "19. 另一道答案\n" + TAIL)
    _, plan = build([page(1, blocks)])
    assert not any(row["reason"] == "fragment_answer_same_page_tail_recovered" for row in plan["fragment_repairs"])
    seventeens = [row for row in plan["questions"] if row["source_number"] == 17]
    assert all(TAIL not in row["answer_markdown"] for row in seventeens)
    assert any(TAIL in row["text"] for row in plan["unresolved_ranges"])
    if fault in {"unknown_edge", "hidden_number"}:
        review = seventeens[0]["source_review"]
        assert review["required"] and review["unassigned_candidates"][0]["relationship_status"] == "unproved"
        assert review["unassigned_candidates"][0]["possible_source_numbers"] == [17]
        assert "fragment_possible_answer_tail_unassigned" in review["reasons"]
        assert seventeens[0]["answer_markdown"] == "解：(1) 原答案前段\n"


@pytest.mark.parametrize("page_number", [2, 3])
def test_tail_recovery_never_crosses_a_page_boundary_or_missing_page(page_number):
    blocks = answer_case()
    _, plan = build([page(1, blocks[:3]), page(page_number, blocks[3:])])
    assert TAIL not in plan["questions"][0]["answer_markdown"]
    assert any(TAIL in row["text"] for row in plan["unresolved_ranges"])


def test_wrong_outgoing_answer_edge_is_disproved_by_next_independent_same_page_head():
    blocks = answer_case()
    blocks.pop(3)
    blocks[2]["edge"] = "to_next"
    _, plan = build([page(1, blocks)])
    assert plan["questions"][0]["answer_markdown"] == "解：(1) 原答案前段\n"
    assert not plan["unresolved_ranges"]
    assert "fragment_outgoing_not_page_tail" not in plan["manual_reasons"]
    assert "fragment_answer_outgoing_ignored_for_same_page_head" in plan["manual_reasons"]
    receipt = next(row for row in plan["fragment_repairs"] if row["reason"] == "fragment_answer_outgoing_ignored_for_same_page_head")
    assert receipt["original_edge"] == "to_next" and receipt["parts"][0]["edge"] == "none"
    assert receipt["peer"]["number"] == 18


@pytest.mark.parametrize("fault", ["duplicate", "context", "unknown_role", "furniture", "not_consecutive", "cross_page", "missing_page"])
def test_outgoing_edge_is_not_cleared_using_a_nonlocal_or_ambiguous_head(fault):
    blocks = answer_case()
    blocks.pop(3)
    blocks[2]["edge"] = "to_next"
    if fault == "duplicate": blocks.insert(0, block("stem", 18, "18. 另一个后题\n"))
    if fault in {"context", "unknown_role", "furniture"}:
        blocks.insert(3, block("unknown" if fault == "unknown_role" else fault, None, "边界资料\n"))
    if fault == "not_consecutive": blocks[3] = block("answer", 19, "19. 原解")
    if fault in {"cross_page", "missing_page"}:
        pages = [page(1, blocks[:3]), page(2 if fault == "cross_page" else 3, blocks[3:])]
    else: pages = [page(1, blocks)]
    _, plan = build(pages)
    assert not any(row["reason"] == "fragment_answer_outgoing_ignored_for_same_page_head" for row in plan["fragment_repairs"])
    assert plan["items"][0]["field_sources"]["answer_markdown"][0]["edge"] == "to_next"
