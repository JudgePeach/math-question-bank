"""Visible source dependencies must not certify separated cards as independent."""

from copy import deepcopy

import pytest

from mathbank.word_source_dependencies import (
    MAX_IMAGE_MARKERS, MAX_LITERAL_MARKERS, MAX_QUESTIONS, MAX_REFERENCES, MAX_SOURCE_CHARACTERS,
    analyze_word_source_dependencies,
)


def paper(*bodies, prefix="", numbers=None):
    source = prefix
    questions = []
    for index, body in enumerate(bodies):
        number = numbers[index] if numbers is not None else index + 1
        start = len(source)
        source += f"{number}. {body}\n"
        questions.append({"id": f"q{index + 1}", "source_number": number,
                          "source_range": [start, len(source)], "raw_content": source[start:]})
    return source, questions


def analyze(*bodies, **kwargs):
    source, questions = paper(*bodies, **kwargs)
    return analyze_word_source_dependencies(source, questions)


def test_independent_questions_and_titles_are_not_dependencies():
    report = analyze("已知$x=2$，求$x+1$。", "求$y=3$。", prefix="2026年第1题杯数学试卷\n一、选择题\n")
    assert report["status"] == "complete"
    assert not report["has_dependencies"] and not report["has_unresolved"]
    assert report["references"] == []


@pytest.mark.parametrize("second", [
    "根据第1题中的函数，求$f(3)$。",
    "利用1题求出的结果继续计算。",
    "第1题所给的函数满足什么条件？",
    "参照第1题计算。",
    "根据第1题的图甲计算长度。",
])
def test_explicit_cross_question_references_group_the_exact_target(second):
    report = analyze("设$f(x)=x^2-1$，求$f(2)$。![](/static/uploads/first.png)", second, "独立第三题。")
    assert report["has_dependencies"] and not report["has_unresolved"]
    assert report["groups"][0]["member_ids"] == ["q1", "q2"]
    assert report["groups"][0]["scope_exhaustive"] is True
    assert not any(item["id"] == "q3" for item in report["question_risks"])


@pytest.mark.parametrize("declaration", [
    "以下第1、2题共用条件：设$f(x)=x^2-1$。求$f(2)$。",
    "第1题与第2题共用函数$f(x)$。",
    "1题和2题共享同一幅图甲。",
    "第1至2题共用材料，计算结果。",
    "本题与下一题共用条件，求$x$。",
    "与第2题共用条件，求$x$。",
    "和下一题共用条件，求$x$。",
    "以下两题共用条件：设$x=3$。",
])
def test_embedded_shared_declarations_are_not_independent(declaration):
    report = analyze(declaration, "求$f(3)$。", "独立第三题。")
    assert report["has_dependencies"]
    assert report["groups"][0]["member_ids"] == ["q1", "q2"]


def test_preamble_shared_declaration_targets_following_questions():
    report = analyze("求$f(2)$。", "求$f(3)$。", "第三题。", prefix="以下两题共用条件：设$f(x)=x^2$。\n")
    assert report["has_dependencies"]
    assert report["groups"][0]["member_ids"] == ["q1", "q2"]


def test_numbered_shared_declaration_outside_question_is_retained():
    report = analyze("第一题。", "第二题。", prefix="第1题与第2题共用同一图形。\n")
    assert report["has_dependencies"]
    assert report["references"][0]["owner_id"] is None


def test_unscoped_shared_declaration_does_not_invent_adjacent_target():
    report = analyze("相邻两题共用条件，求$x$。", "第二题。", "第三题。")
    assert report["has_unresolved"]
    assert report["references"][0]["kind"] == "shared_scope_unresolved"
    assert report["references"][0]["member_ids"] == ["q1"]


def test_dependency_number_is_identity_not_physical_ascending_order():
    report = analyze("独立第三号题。", "根据第3题的结果计算。", numbers=[3, 1])
    assert report["references"][0]["target_numbers"] == [3]
    assert report["groups"][0]["member_ids"] == ["q1", "q2"]


def test_self_reference_is_not_cross_question():
    report = analyze("第1题中的函数已知为$f(x)=x^2$，求值。")
    assert report["references"] == []


@pytest.mark.parametrize("body", [
    "根据第99题的图计算。", "第99题中的函数是多少？",
])
def test_unknown_number_preserves_uncertainty_without_inventing_target(body):
    report = analyze("第一题。", body)
    assert not report["has_dependencies"] and report["has_unresolved"]
    assert report["references"][0]["target_numbers"] == [99]
    assert report["references"][0]["member_ids"] == ["q2"]
    assert report["groups"][0]["scope_exhaustive"] is False


def test_duplicate_number_does_not_guess_the_nearest_target():
    report = analyze("第一题。", "第二题。", "根据第1题的函数求值。", numbers=[1, 1, 2])
    assert report["has_unresolved"]
    assert report["references"][0]["member_ids"] == ["q3"]
    assert not report["has_dependencies"]


@pytest.mark.parametrize("reference", ["同上。", "根据上述函数求值。", "根据前面的图求值。", "根据上图计算。"])
def test_implicit_context_stays_uncertain_and_conservatively_grouped(reference):
    report = analyze("已有第一题条件。", reference, "独立第三题。")
    assert report["has_unresolved"]
    assert report["groups"][0]["member_ids"] == ["q1", "q2"]
    assert report["groups"][0]["scope_exhaustive"] is False
    assert report["partial_scope_certified"] is False


@pytest.mark.parametrize("reference", ["根据上述图形求值。", "根据上方的图求值。", "根据前面的图求值。", "根据上图求值。"])
def test_reference_to_preamble_image_is_not_dismissed_as_metadata(reference):
    report = analyze(reference, "独立下一题。", prefix="![](/static/uploads/even-if-uniform-white.png)\n")
    assert report["has_unresolved"]
    assert report["references"][0]["kind"] == "prior_external_visual_reference"
    assert report["references"][0]["owner_id"] == "q1"


def test_unique_prior_image_inside_same_question_is_local():
    report = analyze("![](/static/uploads/local.png)\n根据上述图形求角度。", "下一独立题。")
    assert report["references"] == []


def test_multiple_local_images_do_not_prove_which_image_is_referenced():
    report = analyze("![](/static/uploads/a.png)![](/static/uploads/b.png)\n根据上述图形求值。")
    assert report["has_unresolved"]


def test_complete_local_condition_can_supply_above_conditions():
    report = analyze("已知$f(x)=x^2$。根据上述条件求$f(3)$。", "另一独立题。")
    assert report["references"] == []


def test_local_condition_does_not_disambiguate_reference_when_other_source_precedes_it():
    report = analyze("已知$f(x)=x^2$。", "设$x>0$。根据上述条件求$f(x)$。")
    assert report["has_unresolved"]


def test_local_image_does_not_disambiguate_reference_when_external_image_precedes_it():
    report = analyze("![](/static/uploads/prior.png) 第一题。", "![](/static/uploads/local.png) 根据上图求角度。")
    assert report["has_unresolved"]


@pytest.mark.parametrize("literal", [
    r"$\text{根据第1题的函数及上述图形}$",
    r"\(\text{以下第1、2题共用条件}\)",
    r"\[\text{根据第1题的函数}\]",
    r"\begin{equation}\text{根据第1题的函数}\end{equation}",
    "`根据第1题的函数`",
    "```text\n根据第1题的函数\n```",
    r"\verb|根据第1题的函数|",
    r"\detokenize{根据第1题的函数}",
    r"\url{https://example.test/根据第1题的函数}",
    "https://example.test/根据第1题的函数",
    "[根据第1题的函数](https://example.test/above)",
    "<!--根据第1题的函数-->",
    "<code>根据第1题的函数</code>",
    "![根据第1题的函数](/static/uploads/above.png)",
])
def test_math_code_urls_link_titles_and_image_syntax_do_not_advertise_dependencies(literal):
    report = analyze("第一题。", "阅读字面材料：" + literal + "。")
    assert report["status"] == "complete", report
    assert report["references"] == []


def test_literal_fake_preamble_image_cannot_establish_image_evidence():
    report = analyze("根据上方的图求值。", prefix="`![](/static/uploads/fake.png)`\n")
    assert report["has_unresolved"]
    assert report["references"][0]["kind"] == "implicit_source_reference"


def test_source_and_questions_are_immutable_and_report_is_deterministic():
    source, questions = paper("条件。", "根据第1题的函数求值。")
    before = deepcopy(questions)
    first = analyze_word_source_dependencies(source, questions)
    assert questions == before
    assert first == analyze_word_source_dependencies(source, questions)
    ref = first["references"][0]
    assert source[slice(*ref["source_range"])] == ref["evidence"]


def test_original_answer_dependencies_keep_their_question_owner():
    source, questions = paper("第一题。", "第二题。")
    source += "参考答案：\n"
    start = len(source)
    source += "2. 根据第1题的结果求值。\n"
    questions[1]["answer_source_range"] = [start, len(source)]
    questions[1]["raw_answer"] = source[start:]
    report = analyze_word_source_dependencies(source, questions)
    assert report["has_dependencies"]
    assert report["references"][0]["owner_id"] == "q2"
    assert report["groups"][0]["member_ids"] == ["q1", "q2"]


def test_reverse_original_answers_do_not_create_false_dependencies():
    source, questions = paper("第一题。", "第二题。")
    source += "参考答案：\n"
    for index, text in ((1, "2. $4$。\n"), (0, "1. $3$。\n")):
        start = len(source)
        source += text
        questions[index]["answer_source_range"] = [start, len(source)]
        questions[index]["raw_answer"] = text
    report = analyze_word_source_dependencies(source, questions)
    assert report["status"] == "complete" and report["references"] == []


def test_stale_original_answer_range_cannot_report_clean():
    source, questions = paper("第一题。")
    questions[0]["answer_source_range"] = [0, 4]
    questions[0]["raw_answer"] = "stale"
    report = analyze_word_source_dependencies(source, questions)
    assert report["status"] == "unavailable"
    assert report["limits"] == ["answer_source_changed"]


@pytest.mark.parametrize("mutation", ["overlap", "stale", "bool", "duplicate_id"])
def test_invalid_or_changed_ranges_cannot_report_clean(mutation):
    source, questions = paper("第一题。", "第二题。")
    if mutation == "overlap":
        questions[1]["source_range"][0] = 0
    elif mutation == "stale":
        questions[0]["raw_content"] = "changed"
    elif mutation == "bool":
        questions[0]["source_range"][0] = False
    else:
        questions[1]["id"] = questions[0]["id"]
    report = analyze_word_source_dependencies(source, questions)
    assert report["status"] == "unavailable" and report["has_unresolved"]


@pytest.mark.parametrize("limit", ["source", "questions", "references", "literals", "images", "math"])
def test_scan_limits_keep_uncertainty_instead_of_silently_truncating(limit):
    if limit == "source":
        source, questions = paper("x" * MAX_SOURCE_CHARACTERS)
    elif limit == "questions":
        source, questions = paper(*(["独立题。"] * (MAX_QUESTIONS + 1)))
    elif limit == "references":
        source, questions = paper("第一题。", "根据第1题的函数。" * (MAX_REFERENCES + 1))
    elif limit == "literals":
        source, questions = paper("`字面`" * (MAX_LITERAL_MARKERS + 1))
    elif limit == "images":
        source, questions = paper("![](/static/uploads/a.png)" * (MAX_IMAGE_MARKERS + 1))
    else:
        source, questions = paper("未闭合的$x根据第1题的函数。")
    report = analyze_word_source_dependencies(source, questions)
    assert report["status"] == "unavailable" and report["has_unresolved"]
    assert report["limits"]
