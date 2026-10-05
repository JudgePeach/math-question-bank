from copy import deepcopy
import json

import pytest

from mathbank import source_review_focus as focus
from mathbank import prompts


def content(source, output):
    return focus.build_review_focus({"content": source}, {"content": output})["fields"]["content"]


def test_focus_is_read_only_serializable_and_not_a_verdict():
    source = {"content": r"已知 $x+1=2$，求 $x$。", "answer_markdown": "$x=1$"}
    output = {"content": r"已知 $x-1=2$，求 $x$。", "answer_markdown": "$x=3$"}
    before = deepcopy((source, output))
    result = focus.build_review_focus(source, output)
    assert (source, output) == before
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    assert result["advisory_only"] is True
    assert set(result) == {"advisory_only", "fields"}
    assert result["fields"]["answer_markdown"]["items"][0]["source"]["formulas"][0]["formula"] == "$x=1$"


@pytest.mark.parametrize('document', ['pdf', 'word'])
def test_visual_prompt_contains_specific_focus_and_complete_unchanged_evidence(document):
    source = '1. 已知 $x+1$，求 $x$。'
    candidate = '1. 已知 $x-1$，求 $x$。'
    if document == 'pdf':
        items = [{'id': 'item_001', 'source_number': 1, 'source_pages': [1],
                  'source_excerpt': source, 'output': candidate}]
        original = deepcopy(items)
        prompt = prompts.build_pdf_source_verification_prompt(items)
        sent = json.loads(prompt[prompt.rfind('{"items": ['):])['items'][0]
        assert sent['source_excerpt'] == source and sent['output'] == candidate
    else:
        items = [{'id': 'word_001', 'source_number': 1, 'source_pages': [1],
                  'original': {'content': source}, 'output': {'content': candidate}}]
        original = deepcopy(items)
        prompt = prompts.build_docx_source_verification_prompt(items)
        sent = json.loads(prompt.rsplit('\n', 1)[1])[0]
        assert sent['original']['content'] == source and sent['output']['content'] == candidate
    assert items == original
    hints = sent['review_focus']
    assert hints['advisory_only'] is True
    change = hints['fields']['content']['items'][0]
    assert change['source']['formulas'][0]['formula'] == '$x+1$'
    assert change['output']['formulas'][0]['formula'] == '$x-1$'
    assert '不能' in prompt and '摘要' in prompt


def test_conservative_existing_presentation_rules_do_not_add_formula_doubts():
    result = content(r"设 $\dfrac{1}{2}$ 为常数。", r"设 \(\frac {1}{2}\) 为常数。")
    assert result["source_formula_count"] == result["output_formula_count"] == 1
    assert result["items"] == []


@pytest.mark.parametrize("source, output", [
    ("$x+1$", "$x-1$"), ("$x_1$", "$x_2$"), ("$x<1$", "$x\\leq 1$"),
    (r"$\sin x$", r"$\sinx$"), (r"$\text{a b}$", r"$\text{ab}$"),
])
def test_math_changes_remain_literal_in_focus(source, output):
    item = content("条件" + source + "成立", "条件" + output + "成立")["items"][0]
    assert item["kind"] == "formula_change"
    assert item["source"]["formulas"][0]["formula"] == source
    assert item["output"]["formulas"][0]["formula"] == output
    assert item["source"]["formulas"][0]["before"] == "条件"


def test_repeated_formula_insertion_is_not_arbitrarily_paired():
    result = content("第一处$x$，第二处$x$，结尾$y$", "新增$x$，第一处$x$，第二处$x$，结尾$y$")
    item = result["items"][0]
    assert result["alignment_ambiguous"] is True
    assert item["kind"] == "formula_group"
    assert [ref["index"] for ref in item["source"]["formulas"]] == [1, 2]
    assert [ref["index"] for ref in item["output"]["formulas"]] == [1, 2, 3]
    assert all(ref["formula"] == "$x$" for ref in item["source"]["formulas"] + item["output"]["formulas"])


def test_unique_formula_deletion_keeps_empty_side_and_later_anchor():
    result = content("先$x$，再$y$，末$z$", "先$x$，末$z$")
    item = result["items"][0]
    assert item["kind"] == "formula_group"
    assert [ref["formula"] for ref in item["source"]["formulas"]] == ["$y$"]
    assert item["output"]["formulas"] == []


def test_formula_reordering_is_ambiguous_instead_of_two_false_replacements():
    result = content("先$x$，后$y$", "先$y$，后$x$")
    assert result["alignment_ambiguous"] is True
    assert all(item["kind"] == "formula_group" for item in result["items"])


@pytest.mark.parametrize("source, output, token", [
    ("对任意$x$都不成立", "对任意$x$都成立", "不"),
    ("长度15米，求$x$", "长度1.5米，求$x$", "."),
    ("$x$至少有两个根", "$x$至多有两个根", "少"),
])
def test_prose_conditions_are_not_normalized_away(source, output, token):
    result = content(source, output)
    assert result["items"]
    assert all(item["kind"] == "text_change" for item in result["items"])
    assert any(token in item["source"]["text"] + item["output"]["text"] for item in result["items"])


def test_identical_repeated_formulas_still_show_changed_surrounding_text():
    result = content("第一处$x$大于第二处$x$", "第二处$x$大于第一处$x$")
    assert result["source_formula_count"] == result["output_formula_count"] == 2
    assert result["items"] and all(item["kind"] == "text_change" for item in result["items"])


def test_long_formula_and_context_are_explicitly_bounded():
    source = "前" * 90 + "$" + "x+" * 200 + "1$" + "后" * 90
    output = source.replace("1$", "2$")
    ref = content(source, output)["items"][0]["source"]["formulas"][0]
    assert len(ref["formula"]) == focus.MAX_FORMULA_CHARS
    assert ref["truncated"] is True and ref["chars"] > len(ref["formula"])
    assert len(ref["before"]) == len(ref["after"]) == focus.MAX_CONTEXT_CHARS
    assert ref['before_truncated'] is True and ref['after_truncated'] is True


def test_large_repeated_group_explicitly_marks_omitted_formulas():
    result = content("，".join(["$x$"] * 20), "，".join(["$x$"] * 21))
    group = result["items"][0]
    assert len(group["source"]["formulas"]) == focus.MAX_ITEMS
    assert group["source"]["omitted"] == 20 - focus.MAX_ITEMS
    assert group["output"]["omitted"] == 21 - focus.MAX_ITEMS


def test_item_limit_reports_omitted_doubts():
    source = "".join(f"$a_{i}$，$b_{i}$；" for i in range(15))
    output = "".join(f"$a_{i}$，$c_{i}$；" for i in range(15))
    result = content(source, output)
    assert len(result["items"]) == focus.MAX_ITEMS
    assert result["omitted"] == 15 - focus.MAX_ITEMS


def test_large_text_comparison_is_coarse_but_never_claims_complete_excerpt():
    result = content("甲" * 1000, "乙" * 1000)
    item = result["items"][0]
    assert item["coarse"] is True
    assert item["source"]["truncated"] is True
    assert item["source"]["chars"] == 1000


def test_over_budget_input_explicitly_has_no_focus():
    result = content("x" * (focus.MAX_FIELD_CHARS + 1), "x")
    assert result["unavailable"] == "field_size_limit"
    result = content("$x$ " * (focus.MAX_FORMULAS + 1), "$x$")
    assert result["unavailable"] == "formula_count_limit"
