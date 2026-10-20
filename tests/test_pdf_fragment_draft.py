"""Exact first-pass fragment assembly and canonical image sources, offline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

from mathbank.pdf_page_fragments import SCHEMA, validate_pdf_page_fragments
from mathbank.pdf_fragment_sources import (prepare_pdf_fragment_sources, checked_fragment_page,
                                          invalidate_pdf_fragment_derivation)
from mathbank.pdf_fragment_draft import RULES_VERSION, prepare_pdf_fragment_draft


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_k: pytest.fail("Local fragments only"))
    monkeypatch.setattr("mathbank.pdf_local_draft.prepare_pdf_local_draft",
                        lambda *_a, **_k: pytest.fail("No whole-paper regex fallback"))


def block(role, number, text, edge="none"):
    return {"role": role, "number": number, "text": text, "edge": edge}


def page(number, blocks, **extra):
    value = {"schema": SCHEMA, "blocks": blocks, "figures": [], "ignored_candidates": [],
             "page_complete": True, "warnings": [], **extra}
    parsed = validate_pdf_page_fragments(value, {"page_index": number - 1, "candidates": []})
    return {"page_number": number, "markdown": parsed["markdown"], "fragments": parsed["fragments"]}


def build(pages, **kwargs):
    canonical = prepare_pdf_fragment_sources(pages, **kwargs)
    plan = prepare_pdf_fragment_draft(canonical["source"], canonical["source_pages"],
                                     source_document_sha256="a" * 64)
    for item, question in zip(plan["items"], plan["questions"]):
        for field, key in (("content", "stem_ranges"), ("answer_markdown", "answer_ranges")):
            assert "".join(canonical["source"][a:b] for a, b in item[key]) == question[field]
        for receipt in item["removals"]:
            assert receipt["text"] == canonical["source"][receipt["start"]:receipt["end"]]
    for match in plan["source_matches"]:
        assert match["source_excerpt"] == "".join(canonical["source"][a:b] for a, b in match["source_ranges"])
        assert match["source_excerpt"] == plan["questions"][match["question_index"]][match["field"]]
    return canonical, plan


def test_explicit_fragments_preserve_scores_formula_bytes_and_detailed_answer_priority():
    raw = [page(1, [block("context", None, "一、单项选择题：只有一项。\n"),
        block("stem", 1, "**1.** （本题满分5分）原题 $x^2$。\n"),
        block("answer_key", 1, "1. A\n"), block("answer", 1, "1. 【答案】A\n原解析$1+1=2$。……5分\n"),
        block("furniture", None, "数学 第1页 共1页")])]
    frozen = deepcopy(raw)
    canonical, plan = build(raw)
    assert plan["workflow"] == "page-fragments.v2" and not plan["partial"]
    assert plan["questions"][0]["content"] == "（本题满分5分）原题 $x^2$。\n"
    assert plan["questions"][0]["answer_markdown"] == "【答案】A\n原解析$1+1=2$。……5分\n"
    assert plan["questions"][0]["question_type"] == "single_choice"
    assert any(row["reason"] == "detailed_answer_preferred" for row in plan["unassigned"])
    assert plan["page_furniture"] and raw == frozen
    assert plan["fragment_summary"]["answers"] == 1


def test_cross_page_answer_chains_skip_only_declared_furniture_and_context():
    _, plan = build([page(1, [block("stem", 10, "10. 题文十\n"), block("stem", 15, "15. 题文十五\n"),
        block("answer", 10, "10. 原解前半\n", "to_next"), block("furniture", None, "第1页")]),
        page(2, [block("furniture", None, "参考答案第2页\n"), block("answer", None, "原解后半\n", "from_previous"),
            block("answer", 15, "15. 第十五题原解前\n", "to_next")]),
        page(3, [block("answer", None, "第十五题原解后", "from_previous")])])
    assert len(plan["questions"]) == 2
    assert plan["questions"][0]["answer_markdown"] == "原解前半\n\n\n原解后半\n"
    assert "第1页" not in plan["questions"][0]["answer_markdown"]
    assert plan["questions"][1]["answer_markdown"] == "第十五题原解前\n\n第十五题原解后"
    # The intentionally selected 10/15 identities remain, with a conservative number-gap notice.
    assert "fragment_question_number_gap" in plan["manual_reasons"]


@pytest.mark.parametrize("fault", ["missing_page", "closed_previous", "wrong_number", "not_page_tail"])
def test_unproved_continuation_keeps_unassigned_without_consuming_next_card(fault):
    first = [block("stem", 1, "1. 待续原文\n", "to_next")]
    last_page = 3 if fault == "missing_page" else 2
    if fault == "closed_previous":
        first[0]["edge"] = "none"
    if fault == "not_page_tail":
        first.append(block("stem", 2, "2. 本页独立题\n"))
    continuation = block("stem", None, "原续文\n", "from_previous")
    if fault == "wrong_number":
        continuation["number"] = 9
    _, plan = build([page(1, first), page(last_page, [continuation, block("stem", 3, "3. 后续完整题")])])
    third = next(row for row in plan["questions"] if row["source_number"] == 3)
    assert third["content"] == "后续完整题" and "原续文" not in third["content"]
    assert plan["partial"] and any("原续文" in row["text"] for row in plan["unresolved_ranges"])


def test_duplicate_stems_and_answers_never_choose_an_arbitrary_copy():
    _, plan = build([page(1, [block("stem", 1, "1. 第一份\n"), block("stem", 1, "1. 冲突第二份\n"),
        block("stem", 2, "2. 完整第二题\n"), block("answer", 2, "2. 原解甲\n"), block("answer", 2, "2. 原解乙")])])
    assert [row["source_number"] for row in plan["questions"]] == [2]
    assert plan["questions"][0]["answer_markdown"] == ""
    assert {"duplicate_stem_number", "original_answer_conflict"} <= set(plan["manual_reasons"])


def test_unknown_role_and_relation_mismatch_are_local_not_global_math_masks():
    _, plan = build([page(1, [block("stem", 1, "1. 未闭$x\n\\begin{cases}\na=b\n"),
        block("unknown", None, "不能归属的正文\n"), block("stem", 2, "2. 后题完整\n"),
        block("stem", 9, "3. 题号元数据冲突")])])
    assert [row["source_number"] for row in plan["questions"]] == [1, 2]
    assert "\\begin{cases}" in plan["questions"][0]["content"]
    assert "fragment_math_review" in plan["questions"][0]["source_review"]["reasons"]
    assert not plan["questions"][1]["source_review"]["required"]
    assert plan["partial"] and any("题号元数据冲突" in row["text"] for row in plan["unresolved_ranges"])


def test_broken_choices_is_only_fragment_advisory_without_swallowing_following_question():
    _, plan = build([page(1, [block("stem", 1, "1. 选择题\n\\begin{choices}\\item 甲\n"),
                            block("stem", 2, "2. 另一道题")])])
    assert [row["source_number"] for row in plan["questions"]] == [1, 2]
    assert plan["questions"][1]["content"] == "另一道题"
    assert not plan["partial"] and plan["questions"][0]["source_review"]["required"]


def test_table_answer_key_fallback_uses_printed_header_cells_not_array_position():
    table = "\\begin{tabular}{|c|c|c|}\n\\hline\n题号 & 2 & 1 \\\\\n答案 & B & A \\\\\n\\hline\n\\end{tabular}"
    _, plan = build([page(1, [block("stem", 1, "1. 甲\n"), block("stem", 2, "2. 乙\n"),
                            block("answer_key", None, table)])])
    assert [row["answer_markdown"] for row in plan["questions"]] == ["A", "B"]
    assert not plan["partial"]


def test_trusted_blank_page_has_zero_source_and_can_bridge_a_proved_chain():
    empty = {"page_number": 2, "markdown": "", "fragments": {"schema": SCHEMA, "page_index": 1,
        "source_sha256": hashlib.sha256(b"").hexdigest(), "blocks": [], "slot_blocks": {}}}
    canonical, plan = build([page(1, [block("stem", 1, "1. 首段", "to_next")]), empty,
                            page(3, [block("stem", None, "后段", "from_previous")])])
    assert canonical["page_texts"][1] == "" and not plan["partial"]
    assert plan["questions"][0]["content"] == "首段\n\n后段"


def test_manual_page_edit_without_sidecar_stays_unassigned_no_regex_or_ocr():
    first = page(1, [block("stem", 1, "1. 原题")])
    first.update(markdown="1. 人工逐页更改题文", fragments=None)
    _, plan = build([first, page(2, [block("stem", 2, "2. 独立完整题")])])
    assert [row["source_number"] for row in plan["questions"]] == [2]
    assert any(row["reason"] == "fragment_metadata_missing" for row in plan["unresolved_ranges"])


def test_wrong_incoming_edge_with_unique_printed_head_does_not_join_previous_card():
    _, plan = build([page(1, [block("stem", 6, "6. 第六题\n")]),
                    page(2, [block("stem", 7, "7. 第七题\n", "from_previous"), block("stem", 8, "8. 第八题")])])
    assert [row["source_number"] for row in plan["questions"]] == [6, 7, 8]
    assert plan["questions"][1]["content"] == "第七题\n" and not plan["partial"]
    assert "fragment_incoming_edge_ignored_for_visible_head" in plan["questions"][1]["source_review"]["reasons"]


def test_printed_same_number_on_open_continuation_is_conflict_not_forced_join():
    _, plan = build([page(1, [block("stem", 7, "7. 原待续\n", "to_next")]),
                    page(2, [block("stem", 7, "7. 又一个完整编号\n", "from_previous"), block("stem", 8, "8. 完整后题")])])
    assert [row["source_number"] for row in plan["questions"]] == [8]
    assert plan["partial"] and len(plan["unresolved_ranges"]) == 2


@pytest.mark.parametrize("barrier", ["context", "answer", "unknown"])
@pytest.mark.parametrize("where", ["outgoing", "incoming"])
def test_only_furniture_may_be_ignored_at_real_cross_page_content_edges(barrier, where):
    extra = block(barrier, 9 if barrier == "answer" else None,
                  "9. 另一题原答\n" if barrier == "answer" else "另一试卷或未知正文\n")
    left = [block("stem", 1, "1. 原题前半\n", "to_next")]
    right = [block("stem", None, "可疑续文\n", "from_previous"), block("stem", 2, "2. 后题完整")]
    (left if where == "outgoing" else right).insert(len(left) if where == "outgoing" else 0, extra)
    _, plan = build([page(1, left), page(2, right)])
    first = next(row for row in plan["questions"] if row["source_number"] == 1)
    assert "可疑续文" not in first["content"] and plan["partial"]
    assert any("可疑续文" in row["text"] for row in plan["unresolved_ranges"])
    assert next(row for row in plan["questions"] if row["source_number"] == 2)["content"] == "后题完整"


def test_wrong_outgoing_hints_retain_legal_head_and_answer_as_partial_cards():
    _, plan = build([page(1, [block("stem", 1, "1. 本页完整第一题\n", "to_next"),
        block("stem", 2, "2. 完整第二题\n"), block("answer", 1, "1. 完整第一题原答\n", "to_next"),
        block("answer", 2, "2. 完整第二题原答")])])
    assert [row["source_number"] for row in plan["questions"]] == [1, 2]
    assert plan["questions"][0]["content"] == "本页完整第一题\n"
    assert plan["questions"][0]["answer_markdown"] == "完整第一题原答\n"
    assert plan["partial"] and plan["questions"][0]["source_review"]["required"]
    assert any(row.get("retained_in_partial_card") for row in plan["unresolved_ranges"])


def test_same_page_adjacent_answer_handshake_joins_tail_without_consuming_next_head():
    raw = [page(1, [block("stem", 13, "13. 前题\n"), block("stem", 14, "14. 后题\n"),
        block("answer", 13, "13. 原解析，经检验\n", "to_next"),
        block("answer", None, "符合题意，故$a=1$。\n", "from_previous"),
        block("answer", 14, "14. 后题原答")])]
    _, plan = build(raw)
    assert not plan["partial"]
    assert plan["questions"][0]["answer_markdown"] == "原解析，经检验\n符合题意，故$a=1$。\n"
    assert "后题原答" not in plan["questions"][0]["answer_markdown"]
    assert plan["questions"][1]["answer_markdown"] == "后题原答"
    assert "fragment_same_page_continuation" in plan["questions"][0]["source_review"]["reasons"]


@pytest.mark.parametrize("barrier", ["context", "furniture", "unknown"])
def test_same_page_handshake_cannot_jump_any_other_block(barrier):
    _, plan = build([page(1, [block("stem", 1, "1. 题\n"),
        block("answer", 1, "1. 原解前\n", "to_next"), block(barrier, None, "插入内容\n"),
        block("answer", None, "不明续解", "from_previous")])])
    assert plan["partial"]
    assert plan["questions"][0]["answer_markdown"] == "原解前\n"
    assert any("不明续解" in row["text"] for row in plan["unresolved_ranges"])


def test_page_incomplete_cannot_become_complete_draft_even_when_heads_are_usable():
    _, plan = build([page(1, [block("stem", 1, "1. 已保留原文")], page_complete=False)])
    assert len(plan["questions"]) == 1 and plan["partial"]
    assert "fragment_page_incomplete" in plan["manual_reasons"]


def test_proved_answer_continuation_may_split_one_explicit_next_head_with_sha_receipts():
    raw = [page(1, [block("stem", 18, "18. 前题\n"), block("stem", 19, "19. 后题\n"),
        block("answer", 18, "18. 原解前段\n", "to_next")]), page(2, [block("answer", None,
        "② 原解尾段。\n19. 解：(1) 后题原答 $a=1$。", "both"), block("furniture", None, "第4页")])]
    frozen = deepcopy(raw)
    canonical, plan = build(raw)
    assert len(plan["questions"]) == 2 and plan["partial"]
    assert "原解尾段" in plan["questions"][0]["answer_markdown"]
    assert "后题原答" not in plan["questions"][0]["answer_markdown"]
    assert plan["questions"][1]["answer_markdown"] == "解：(1) 后题原答 $a=1$。"
    assert len(plan["fragment_repairs"]) == 1
    repair = plan["fragment_repairs"][0]
    assert repair["parent_text_sha256"] == hashlib.sha256(
        canonical["source"][slice(*repair["parent_range"])].encode()).hexdigest()
    for part in repair["parts"]:
        assert part["text_sha256"] == hashlib.sha256(canonical["source"][slice(*part["range"])].encode()).hexdigest()
    assert raw == frozen


@pytest.mark.parametrize("fault", ["missing_stem", "existing_answer", "wrong_next_number", "open_math", "code_example"])
def test_embedded_answer_split_never_guesses_unproved_identity_or_protected_text(fault):
    heads = [block("stem", 18, "18. 前题\n")]
    if fault != "missing_stem": heads.append(block("stem", 19, "19. 后题\n"))
    heads.append(block("answer", 18, "18. 原解前段\n", "to_next"))
    text = "原解尾段。\n19. 解：潜在后题原答。"
    if fault == "wrong_next_number": text = text.replace("19.", "20.")
    if fault == "open_math": text = "$未闭数学\n" + text
    if fault == "code_example": text = "原解尾段。\n```text\n19. 解：示例文字\n```"
    tail = [block("answer", None, text, "both")]
    if fault == "existing_answer": tail.append(block("answer", 19, "19. 另一份原答"))
    _, plan = build([page(1, heads), page(2, tail)])
    assert not plan["fragment_repairs"] and plan["partial"]
    if fault != "code_example":
        first = next(row for row in plan["questions"] if row["source_number"] == 18)
        assert "潜在后题原答" not in first["answer_markdown"]


def noisy_answer_pages():
    return [page(1, [block("stem", n, f"{n}. 原题{n}\n") for n in [10, 11, 15, 16, 17, 18, 19]]),
        page(2, [block("answer", 10, "10. 【答案】原答十前。\n"), block("furniture", None, "页脚\n")]),
        page(3, [block("answer", None, "原答十尾。\n", "from_previous"),
            block("answer", 11, "11. 【答案】原答十一。\n"),
            block("answer", 15, "15. 解：原答十五前$S_3=$\n", "to_next")]),
        page(4, [block("answer", None, "原答十五尾$7$。……13分\n16. (1)证明：原答十六。……15分\n"
            "17. 解：原答十七。……15分\n18. 解：原答十八前。……10分\n", "from_previous")]),
        page(5, [block("answer", None, "②原答十八尾。……17分\n19. 解：(1)原答十九前。\n", "from_previous"),
            block("answer", None, "(2)原答十九尾$S\\cdot ON=2$。……17分\n", "to_next"),
            block("furniture", None, "页脚")])]


def test_multiple_known_answer_heads_and_single_sided_edges_preserve_all_original_ranges():
    raw = noisy_answer_pages()
    frozen = deepcopy(raw)
    canonical, plan = build(raw)
    answers = {q["source_number"]: q["answer_markdown"] for q in plan["questions"]}
    assert set(answers) == {10, 11, 15, 16, 17, 18, 19} and all(answers.values())
    assert "原答十前" in answers[10] and "原答十尾" in answers[10] and "原答十一" not in answers[10]
    assert "原答十五前$S_3=$" in answers[15] and "原答十五尾$7$。……13分" in answers[15]
    for number, label, next_label in [(15, "十五", "十六"), (16, "十六", "十七"), (17, "十七", "十八"), (18, "十八", "十九")]:
        assert f"原答{label}" in answers[number] and f"原答{next_label}" not in answers[number]
    assert "原答十八前" in answers[18] and "原答十八尾" in answers[18]
    assert "原答十九前" in answers[19] and "原答十九尾$S\\cdot ON=2$。……17分" in answers[19]
    assert plan["partial"] and plan["rules_version"] == RULES_VERSION
    assert {"fragment_answer_boundary_recovered", "fragment_answer_outgoing_recovered",
            "fragment_answer_same_page_edge_recovered"} <= set(plan["manual_reasons"])
    assert any(q["source_number"] == 19 and "fragment_continuation_unclosed" in q["source_review"]["reasons"]
               for q in plan["questions"])
    for receipt in plan["fragment_repairs"]:
        assert receipt["parent_text_sha256"] == hashlib.sha256(
            canonical["source"][slice(*receipt["parent_range"])].encode()).hexdigest()
        for part in receipt["parts"]:
            assert part["text_sha256"] == hashlib.sha256(canonical["source"][slice(*part["range"])].encode()).hexdigest()
        assert receipt["original_verified"] is False and "original_edge" in receipt
    assert raw == frozen


@pytest.mark.parametrize("fault", ["context", "missing_page", "duplicate_stem", "duplicate_answer", "number_gap",
                                  "unclosed_math", "formula_head", "code_head"])
def test_multiple_answer_heads_require_unique_printed_consecutive_unprotected_identity(fault):
    raw = noisy_answer_pages()
    if fault == "context":
        # Only furniture can be ignored between actual physical content edges.
        raw[2] = page(3, [block("answer", 15, "15. 原答十五前\n", "to_next"),
                         block("context", None, "另一套参考答案\n")])
    elif fault == "missing_page":
        raw = [p for p in raw if p["page_number"] != 3]
    elif fault == "duplicate_stem":
        raw[0] = page(1, [block("stem", n, f"{n}. 原题{n}\n") for n in [10, 11, 15, 16, 16, 17, 18, 19]])
    elif fault == "duplicate_answer":
        raw[1] = page(2, [block("answer", 16, "16. 解：另一份原答\n"), block("answer", 10, "10. 原答十前\n")])
    else:
        text = raw[3]["markdown"]
        if fault == "number_gap": text = text.replace("17. 解：", "20. 解：")
        elif fault == "unclosed_math": text = "$未闭公式\n" + text
        elif fault == "formula_head": text = text.replace("16. (1)证明：", "$$\n16. (1)证明：").replace("17. 解：", "$$\n17. 解：")
        else: text = text.replace("16. (1)证明：", "```text\n16. (1)证明：").replace("17. 解：", "```\n17. 解：")
        raw[3] = page(4, [block("answer", None, text, "from_previous")])
    _, plan = build(raw)
    assert plan["partial"]
    assert not any(r["reason"] == "fragment_answer_boundary_recovered" and r["page_number"] == 4
                   for r in plan["fragment_repairs"])
    q15 = next(q for q in plan["questions"] if q["source_number"] == 15)
    assert "原答十六" not in q15["answer_markdown"]


@pytest.mark.parametrize("fault", ["previous_context", "incoming_context", "missing_page", "duplicate_head", "next_not_consecutive"])
def test_single_incoming_edge_cannot_bridge_ambiguous_answer_origins(fault):
    first = [block("stem", 10, "10. 原题\n"), block("stem", 11, "11. 后题\n"),
             block("answer", 10, "10. 原答十前\n")]
    second = [block("answer", None, "原答十尾\n", "from_previous"), block("answer", 11, "11. 解：原答十一")]
    if fault == "previous_context": first.append(block("context", None, "新题组\n"))
    elif fault == "incoming_context": second.insert(0, block("context", None, "新题组\n"))
    elif fault == "duplicate_head": first.insert(2, block("answer", 10, "10. 另一原答\n"))
    elif fault == "next_not_consecutive": second[1] = block("answer", 12, "12. 解：原答十二")
    _, plan = build([page(1, first), page(3 if fault == "missing_page" else 2, second)])
    assert plan["partial"]
    assert not any(r["reason"] == "fragment_answer_outgoing_recovered" for r in plan["fragment_repairs"])
    q10 = next(q for q in plan["questions"] if q["source_number"] == 10)
    assert "原答十尾" not in q10["answer_markdown"]


@pytest.mark.parametrize("barrier", ["context", "furniture", "unknown", "new_head", "code_head", "math_head"])
def test_inferred_same_page_tail_cannot_cross_a_block_or_absorb_an_independent_head(barrier):
    body = "原答十九尾\n"
    if barrier == "new_head": body = "20. 另一题原解\n"
    elif barrier == "code_head": body = "```text\n20. 解：代码示例\n```"
    elif barrier == "math_head": body = "$$\n20. 解：数学内字面\n$$"
    blocks = [block("stem", 19, "19. 原题\n"), block("answer", 19, "19. 解：原答十九前\n")]
    if barrier in {"context", "furniture", "unknown"}: blocks.append(block(barrier, None, "插入正文\n"))
    blocks.append(block("answer", None, body, "to_next"))
    _, plan = build([page(1, blocks)])
    assert plan["partial"] and plan["questions"][0]["answer_markdown"] == "解：原答十九前\n"
    assert not any(r["reason"] == "fragment_answer_same_page_edge_recovered" for r in plan["fragment_repairs"])


@pytest.mark.parametrize("defect", ["source_hash", "block_hash", "overlap", "page_identity"])
def test_changed_or_unbound_fragment_sources_fail_locally(defect):
    raw = page(1, [block("stem", 1, "1. 原题")])
    if defect == "source_hash": raw["markdown"] += "变更"
    elif defect == "block_hash": raw["fragments"]["blocks"][0]["text_sha256"] = "0" * 64
    elif defect == "overlap": raw["fragments"]["blocks"][0]["range"][0] = 1
    else: raw["fragments"]["page_index"] = 1
    with pytest.raises(ValueError): prepare_pdf_fragment_sources([raw])


def figure_fixture(tmp_path, slot="p1-s1"):
    path = tmp_path / "crop.png"
    Image.new("RGB", (12, 10), "white").save(path)
    url = "/static/uploads/tmp/fragment-crop.png"
    receipt = {"status": "accepted", "image_path": url, "crop_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    figure = {"id": "p1-f1", "page_index": 0, "slot_id": slot, "attached": True,
              "review_required": False, "image_path": url, "crop_review": receipt}
    return path, figure


def test_only_verified_crop_replaces_unique_owning_block_and_pending_slot_identity_stays_stable(tmp_path):
    path, figure = figure_fixture(tmp_path)
    raw = [page(1, [block("stem", 1, "1. 题$未闭\n[插图待补: 图1]\n"),
                    block("stem", 2, "2. 后题\n[插图待补: 图2]")])]
    frozen = deepcopy(raw)
    canonical, plan = build(raw, layout_result={"pages": [{"page_index": 0, "figures": [figure]}]},
                            resolve_asset=lambda _url: path)
    assert "![插图](/static/uploads/tmp/fragment-crop.png)" in plan["questions"][0]["content"]
    assert "[插图待补: 图2]" in plan["questions"][1]["content"]
    assert canonical["source_pages"][0]["fragments"]["slot_blocks"] == raw[0]["fragments"]["slot_blocks"]
    checked_fragment_page(canonical["source_pages"][0])
    assert raw == frozen
    warm = prepare_pdf_fragment_sources(raw, {"pages": [{"page_index": 0, "figures": [figure]}]},
                                        resolve_asset=lambda _url: path)
    assert json.dumps(warm, sort_keys=True) == json.dumps(canonical, sort_keys=True)


@pytest.mark.parametrize("defect", ["pending", "not_attached", "bytes_changed", "duplicate", "url", "native_slot"])
def test_unverified_or_ambiguous_crop_never_attaches(defect, tmp_path):
    path, figure = figure_fixture(tmp_path)
    raw = [page(1, [block("stem", 1, "1. 原题\n[插图待补: 图1]")])]
    figures = [figure]
    if defect == "pending": figure["crop_review"]["status"] = "pending"
    elif defect == "not_attached": figure["attached"] = False
    elif defect == "bytes_changed": path.write_bytes(b"changed PNG bytes")
    elif defect == "duplicate": figures.append(deepcopy(figure))
    elif defect == "url": figure["image_path"] = figure["crop_review"]["image_path"] = "https://evil.invalid/x.png"
    else: figure["slot_id"] = "native:p1-f1"
    canonical, plan = build(raw, layout_result={"pages": [{"page_index": 0, "figures": figures}]},
                            resolve_asset=lambda _url: path)
    assert "![插图]" not in canonical["source"]
    assert "[插图待补: 图1]" in plan["questions"][0]["content"]


def test_same_source_text_with_corrected_role_changes_structural_cache_input():
    first = page(1, [block("unknown", None, "1. 这是一道题")])
    second = page(1, [block("stem", 1, "1. 这是一道题")])
    a, before = build([first]); b, after = build([second])
    assert a["source"] == b["source"] and before["source_sha256"] == after["source_sha256"]
    assert a["source_pages"] != b["source_pages"]
    assert not before["questions"] and len(after["questions"]) == 1


def derivation_fixture(tmp_path):
    path, figure = figure_fixture(tmp_path)
    # The unmatched dollar pairs into another block in the old whole-page
    # scanner. The fragment ownership and PNG receipt remain unambiguous.
    raw = [page(1, [block("stem", 1, "1. 原题$未闭\n[插图待补: 图1]\n"),
                    block("stem", 2, "2. 后题$x$。")])]
    previous = {"pages": [{"page_index": 0, "figures": []}]}
    current = {"pages": [{"page_index": 0, "figures": [figure]}]}
    before = prepare_pdf_fragment_sources(raw, previous, resolve_asset=lambda _url: path)
    checkpoint = {"split_input": {"source_markdown": before["source"], "model_source": before["source"],
        "model_source_sha256": hashlib.sha256(before["source"].encode()).hexdigest()},
        "accepted_split": {"protocol": "page-fragments.v2", "questions": [{"content": "旧候选"}]},
        "pages": {"0": {"original": "untouched"}}, "prepared": {"page_texts": ["unchanged evidence"]}}
    return raw, previous, current, checkpoint, path


def test_fragment_image_change_invalidates_only_derived_input_even_across_bad_dollars(tmp_path):
    raw, before, after, checkpoint, path = derivation_fixture(tmp_path)
    frozen = deepcopy((raw, before, after, checkpoint))
    archived = []
    result = invalidate_pdf_fragment_derivation(checkpoint, raw, before, after,
        resolve_asset=lambda _url: path, archive=lambda packet, sha: archived.append((packet, sha)))
    assert result["changed"] and len(archived) == 1
    assert "split_input" not in checkpoint and "accepted_split" not in checkpoint
    assert checkpoint["pages"] == frozen[3]["pages"] and checkpoint["prepared"] == frozen[3]["prepared"]
    assert (raw, before, after) == frozen[:3]
    packet, sha = archived[0]
    assert packet["split_input"] == frozen[3]["split_input"]
    assert packet["raw_pages"][0]["source_sha256"] == raw[0]["fragments"]["source_sha256"]
    assert packet["raw_pages"][0]["blocks"][0]["text_sha256"] == raw[0]["fragments"]["blocks"][0]["text_sha256"]
    assert result["archive_sha256"] == sha


@pytest.mark.parametrize("defect", ["raw_changed", "model_hash", "crop_hash", "archive_failure", "archive_mutation"])
def test_fragment_derivation_failure_never_discards_old_checkpoint(defect, tmp_path):
    raw, before, after, checkpoint, path = derivation_fixture(tmp_path)
    if defect == "raw_changed":
        raw[0]["markdown"] += "改正文"
    elif defect == "model_hash":
        checkpoint["split_input"]["model_source_sha256"] = "0" * 64
    elif defect == "crop_hash":
        after["pages"][0]["figures"][0]["crop_review"]["crop_sha256"] = "0" * 64
    frozen = deepcopy(checkpoint)
    def archive(_packet, _sha):
        if defect == "archive_failure": raise OSError("disk full")
        if defect == "archive_mutation": checkpoint["accepted_split"]["questions"].append({"content": "new"})
    with pytest.raises((ValueError, OSError)):
        invalidate_pdf_fragment_derivation(checkpoint, raw, before, after,
                                          resolve_asset=lambda _url: path, archive=archive)
    assert "split_input" in checkpoint and "accepted_split" in checkpoint
    if defect != "archive_mutation": assert checkpoint == frozen


def test_same_warm_fragment_source_has_no_archive_or_checkpoint_mutation(tmp_path):
    raw, before, after, checkpoint, path = derivation_fixture(tmp_path)
    frozen = deepcopy(checkpoint)
    result = invalidate_pdf_fragment_derivation(checkpoint, raw, before, before,
        resolve_asset=lambda _url: path, archive=lambda *_args: pytest.fail("Unchanged source is not archived"))
    assert not result["changed"] and checkpoint == frozen
