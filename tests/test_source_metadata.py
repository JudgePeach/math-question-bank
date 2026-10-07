"""Source-only reconstruction must preserve data or abstain before cloud use."""

from copy import deepcopy
import json
import re

import pytest

from mathbank.content_locks import _formulas, lock_visible_math, reconcile_visible_math
from mathbank.docx_helper import _new_diagnostics
from mathbank.source_metadata import (
    SourceMetadataContractError, apply_source_metadata_response,
    build_source_metadata_messages, prepare_word_source_metadata,
    source_metadata_diagnostics, normalize_source_fillin,
)


CURRICULUM = {"必修一": {"1. 集合与常用逻辑用语": [], "3. 函数的概念与性质": []}}


def prepare(source):
    return prepare_word_source_metadata(source, _new_diagnostics())


def metadata(plan, *, question_type="detailed_answer"):
    return {"items": [{"id": q["id"], "question_type": "single_choice" if q["has_choices"] else question_type,
        "category_compulsory": "必修一", "category_chapter": "3. 函数的概念与性质", "difficulty": "normal"}
        for q in plan["questions"]]}


def test_complete_mixed_paper_retains_options_images_subquestions_scores_and_reverse_answers():
    source = r'''2026北京东城高三一模数学试卷
一、单项选择题
1. （本小题5分）已知 $f(x)=x^2-2x$，则 $f(3)=$（   ）
A. $-3$  B. $0$  C. $3$  D. $6$

二、填空题
2. （本小题5分）设 $a=2$，则 $a+a=$ ____。

三、解答题
3. 已知 $f(x)=x^2-2x$。
（1）求 $f(3)$；
（2）求 $f(0)$。
![](/static/uploads/q3.png)

参考答案：
3. （1）$f(3)=3$。……3分
（2）$f(0)=0$。……6分
1. C。
2. $4$。'''
    plan = prepare(source)
    assert plan["eligible"], plan["fallback_reasons"]
    assert len(plan["questions"]) == 3
    assert plan["source_ranges"][0]["start"] == 0
    assert plan["source_ranges"][-1]["end"] == len(source)
    assert all(a["end"] == b["start"] for a, b in zip(plan["source_ranges"], plan["source_ranges"][1:]))
    response = metadata(plan)
    response["items"][1]["question_type"] = "fill_in_blank"
    questions = apply_source_metadata_response(response, plan, CURRICULUM,
        normalize_fillin=lambda value: value.replace("____", r"\fillin"))
    assert questions[0]["content"].count(r"\item") == 4
    assert "本小题5分" in questions[0]["content"] and "本小题5分" in questions[1]["content"]
    assert r"\fillin" in questions[1]["content"]
    assert "（1）" in questions[2]["content"] and "（2）" in questions[2]["content"]
    assert questions[2]["referenced_images"] == ["/static/uploads/q3.png"]
    assert questions[0]["answer_markdown"] == "[EXTRACTED_ORIGINAL]C。"
    assert "……3分" in questions[2]["answer_markdown"] and "……6分" in questions[2]["answer_markdown"]
    _, locks = lock_visible_math(source, "metadata-test")
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0
    assert report["unmatched_source"] == []
    assert len({lock.lock_id for lock in locks}) == len(_formulas(source))


def test_inline_answer_on_a_new_line_is_retained_as_source_answer():
    plan = prepare("1. 已知变量$x=3$，求其值。\n【答案】$3$。\n2. 给定参数$y=4$，写出结果。\n答案：$4$。")
    assert plan["eligible"], plan["fallback_reasons"]
    questions = apply_source_metadata_response(metadata(plan), plan, CURRICULUM)
    assert [q["answer_markdown"] for q in questions] == ["[EXTRACTED_ORIGINAL]$3$。", "[EXTRACTED_ORIGINAL]$4$。"]


def test_current_curriculum_full_body_and_literal_section_context_sent_without_lock_shells():
    body = "已知$f(x)=x^2$。" + "条件中间不能省略。" * 80 + "求$f(3)$。"
    plan = prepare("一、解答题\n1. " + body)
    assert plan["eligible"]
    messages = build_source_metadata_messages(plan, CURRICULUM)
    items = json.loads(messages[1]["content"])["items"]
    assert body in items[0]["content"]
    assert items[0]["section_context"] == "一、解答题"
    assert "mathbank-math" not in messages[1]["content"]
    assert "MBM_" not in messages[1]["content"]
    assert "1. 集合与常用逻辑用语" in messages[0]["content"]


@pytest.mark.parametrize("field", ["review_required", "omml_unsupported", "mtef_fallback_images", "mtef_unavailable",
    "images_unavailable", "symbols_unavailable", "numbering_unavailable", "tables_review_required"])
def test_existing_word_extraction_review_blocks_the_optimization(field):
    diagnostics = _new_diagnostics()
    diagnostics[field] = 1
    plan = prepare_word_source_metadata("1. 求$x$。", diagnostics)
    assert not plan["eligible"]
    assert "word_extraction_requires_review" in plan["fallback_reasons"]


@pytest.mark.parametrize("diagnostics", [None, {}, {"page_complete": True}, True,
    {**_new_diagnostics(), "review_required": False}, {**_new_diagnostics(), "warnings": ["需要核对"]},
    {**_new_diagnostics(), "unsupported_omml_tags": ["unknown"]}])
def test_missing_boolean_or_model_self_report_is_not_a_word_quality_certificate(diagnostics):
    assert not prepare_word_source_metadata("1. 求$x$。", diagnostics)["eligible"]


@pytest.mark.parametrize("source", [
    "15\n（1）计算$x$。\n16. 求$y$。",
    "阅读材料：设$f(x)=x^2$。\n1. 根据上述材料求$f(3)$。",
    "一、选择题（已知a=2）\n1. 求$a$。",
    "1. 求$x$。\n1. 求$y$。",
    "1. 求$x$。【答案】$3$。",
    "1. 求$x$（C）。",
    "1. 求$x$。\n参考答案：\n2. $3$。",
    "1. 求$x$。\n参考答案：\n1. $3$。\n1. $4$。",
    "1. 求$x$。\n2. 求$y$。\n参考答案：\n1. C；2. D。",
    r"1. 比较\begin{tabular}{c}\begin{tabular}{c}$1$\end{tabular}\end{tabular}",
    "1. 比较\\begin{tabular}{cc}\n2. 数据 & $3$\\\\\n\\end{tabular}",
    "1. 求$x$。\nA. $1$\nB. $2$\nC. $3$",
    "1. 求$x$。\nA. $1$\nB. $2$\nB. $3$\nD. $4$",
    r"1. 求\begin{choices}\item $1$\end{choices}",
    "1. 求[公式待核对]。",
    "1. 求 $x。",
    r"1. 求\begin{choices}\item $1$",
    "![](/static/uploads/unknown.png)\n数学试卷\n1. 求$x$。",
    "第1题与第2题共用下表。\n1. 求$x$。\n2. 求$y$。",
    "1. 已知$x=2$。 2. 已知$y=3$。",
    "1. 比较函数。\n第二题：给定$y=4$，求值。",
    "1. 2. 已知$x=2$，求值。",
    "1. 正常第一题。\n2. 文本框第二题文字3. 文本框第三题文字。",
])
def test_ambiguous_or_incomplete_sources_preserve_every_byte_and_use_whole_source_fallback(source):
    plan = prepare(source)
    assert not plan["eligible"], source
    assert plan["source"] == source
    assert plan["fallback_reasons"]


def test_complete_body_table_remains_one_question_with_cell_images():
    source = r"1. 根据下表求值。\begin{tabular}{cc}甲 & 乙\\$x^2$ & ![](/static/uploads/cell.png)\\\end{tabular}"
    plan = prepare(source)
    assert plan["eligible"], plan["fallback_reasons"]
    questions = apply_source_metadata_response(metadata(plan), plan, CURRICULUM)
    assert r"\begin{tabular}{cc}" in questions[0]["content"]
    assert "$x^2$" in questions[0]["content"]
    assert questions[0]["referenced_images"] == ["/static/uploads/cell.png"]


def test_protected_math_code_and_image_paths_do_not_reach_fillin_normalizer():
    source = r"1. 比较 $\underline{AB}$，阅读 `x___ ![](/static/uploads/code___x.png)`，填入 ____。![](/static/uploads/figure___x.png)"
    plan = prepare(source)
    assert plan["eligible"], plan["fallback_reasons"]
    observed = []
    def normalize(value):
        observed.append(value)
        return value.replace("____", r"\fillin").replace("___", "BAD")
    questions = apply_source_metadata_response(metadata(plan), plan, CURRICULUM, normalize_fillin=normalize)
    assert all(r"\underline{AB}" not in value and "code___x.png" not in value and "figure___x.png" not in value for value in observed)
    assert r"$\underline{AB}$" in questions[0]["content"]
    assert "`x___ ![](/static/uploads/code___x.png)`" in questions[0]["content"]
    assert "/static/uploads/figure___x.png" in questions[0]["content"]
    assert "BAD" not in questions[0]["content"]
    again = normalize_source_fillin(questions[0]["content"], normalize)
    assert again == questions[0]["content"]
    assert r"$\underline{AB}$" in again and "`x___ ![](/static/uploads/code___x.png)`" in again


@pytest.mark.parametrize("change", ["missing", "unknown", "duplicate", "content", "bad_category", "cross_category", "bad_type", "bad_difficulty", "outer_extra"])
def test_model_contract_failure_cannot_edit_or_drop_the_local_source(change):
    plan = prepare("1. 求$x$。\n2. 求$y$。")
    original = deepcopy(plan["questions"])
    parsed = metadata(plan)
    if change == "missing":
        parsed["items"].pop()
    elif change == "unknown":
        parsed["items"][0]["id"] = "FAKE"
    elif change == "duplicate":
        parsed["items"][1] = deepcopy(parsed["items"][0])
    elif change == "content":
        parsed["items"][0]["content"] = "changed"
    elif change == "bad_category":
        parsed["items"][0]["category_chapter"] = "未知章节"
    elif change == "cross_category":
        parsed["items"][0]["category_compulsory"] = "必修二"
    elif change == "bad_type":
        parsed["items"][0]["question_type"] = "unknown"
    elif change == "bad_difficulty":
        parsed["items"][0]["difficulty"] = "unknown"
    else:
        parsed["page_complete"] = True
    with pytest.raises(SourceMetadataContractError):
        apply_source_metadata_response(parsed, plan, CURRICULUM)
    assert plan["questions"] == original


def test_response_order_does_not_reorder_original_questions_or_pair_answers_by_index():
    plan = prepare("1. 已知变量$x=3$，求其值。\n2. 给定参数$y=4$，写出结果。\n参考答案：\n2. $4$。\n1. $3$。")
    assert plan["eligible"], plan["fallback_reasons"]
    parsed = metadata(plan)
    parsed["items"].reverse()
    questions = apply_source_metadata_response(parsed, plan, CURRICULUM)
    assert [q["content"] for q in questions] == ["已知变量$x=3$，求其值。", "给定参数$y=4$，写出结果。"]
    assert [q["answer_markdown"] for q in questions] == ["[EXTRACTED_ORIGINAL]$3$。", "[EXTRACTED_ORIGINAL]$4$。"]


def test_indistinguishable_short_prose_does_not_bypass_existing_source_uniqueness_guard():
    plan = prepare("1. 求$x$。\n2. 求$y$。\n参考答案：\n2. $4$。\n1. $3$。")
    assert not plan["eligible"]
    assert "source_reconciliation_requires_review" in plan["fallback_reasons"]


def test_proven_preamble_and_exam_titles_are_metadata_without_losing_original_snapshot():
    source = ("\\textbf{2026北京东城高三一模数学试卷}\n注意事项：\n"
              "1. 本试卷满分150分，考试时间120分钟。\n"
              "一、解答题\n1. 已知变量$x=2$，求其值。")
    plan = prepare(source)
    assert plan["eligible"], plan["fallback_reasons"]
    assert plan["source"] == source
    assert len(plan["questions"]) == 1
    assert plan["document_metadata"]


def test_percent_escaped_dollar_url_and_code_question_numbers_are_not_new_questions():
    source = (r"1. 已知$x=2$，价格为\$5，折扣20\%，阅读 https://example.com/2.abc 。"
              "\n```text\n2. 不是真题 $3$\n第十题：也只是代码。\n```\n继续求$x$。")
    plan = prepare(source)
    assert plan["eligible"], plan["fallback_reasons"]
    assert len(plan["questions"]) == 1
    questions = apply_source_metadata_response(metadata(plan), plan, CURRICULUM)
    assert "20\\%" in questions[0]["content"] and "\\$5" in questions[0]["content"]
    assert "https://example.com/2.abc" in questions[0]["content"]
    assert "第十题：也只是代码。" in questions[0]["content"]


def test_question_number_adjacent_to_small_question_remains_one_complete_body():
    plan = prepare("15.（1）计算$x=2$；\n（2）证明结论。\n16.已知函数$f(x)=x^2$，求$f(3)$。")
    assert plan["eligible"], plan["fallback_reasons"]
    assert [q["source_number"] for q in plan["questions"]] == [15, 16]
    assert "（1）" in plan["questions"][0]["content"] and "（2）" in plan["questions"][0]["content"]


@pytest.mark.parametrize("heading", [
    "一、选择题（本题共8小题，每小题5分，共40分，在每小题给出的四个选项中，只有一项是符合题目要求的）",
    "二、填空题（本题共3小题，每小题5分，共15分）",
    "三、解答题（本题共6小题，共70分，解答应写出文字说明、证明过程或演算步骤）",
])
def test_only_whitelisted_section_counts_scores_and_response_format_are_metadata(heading):
    plan = prepare(heading + "\n1. 已知变量$x=2$，求其值。")
    assert plan["eligible"], plan["fallback_reasons"]
    assert plan["document_metadata"][0]["text"].strip() == heading
    assert plan["source"] == heading + "\n1. 已知变量$x=2$，求其值。"


@pytest.mark.parametrize("heading", [
    "一、填空题（定义域均为实数）",
    "一、解答题（所有参数取正实数）",
    "一、选择题（题中各字母均代表正数）",
    "一、填空题（本题共3小题，未知量皆为非负实数）",
    "一、解答题（每小题5分，各变量都不等于零）",
    "一、选择题（任意出现的量都属于整数）",
    "一、填空题（各函数共同采用全体实数作输入）",
    "一、解答题（所得结果保留两位小数）",
    r"一、解答题（$a>0$）",
    r"一、填空题（$x\in\mathbb{R}$）",
    r"一、选择题（共8小题，$n\ge1$）",
    "\\textbf{一、填空题（定义域}\\textbf{均为实数）}",
])
def test_shared_mathematical_assumptions_cannot_be_dropped_as_section_metadata(heading):
    source = heading + "\n1. 已知变量$x=2$，求其值。"
    plan = prepare(source)
    assert not plan["eligible"]
    assert "unowned_section_statement" in plan["fallback_reasons"]
    assert plan["source"] == source
    with pytest.raises(SourceMetadataContractError):
        build_source_metadata_messages(plan, CURRICULUM)


@pytest.mark.parametrize(("heading", "correct_type", "wrong_type"), [
    ("一、单项选择题", "single_choice", "multi_choice"),
    ("一、多项选择题", "multi_choice", "single_choice"),
    ("二、填空题", "fill_in_blank", "detailed_answer"),
    ("三、解答题", "detailed_answer", "fill_in_blank"),
])
def test_explicit_original_section_type_cannot_be_changed_by_model(heading, correct_type, wrong_type):
    source = heading + "\n1. 已知变量$x=2$，求其值。"
    plan = prepare(source)
    assert plan["eligible"]
    reply = metadata(plan, question_type=correct_type)
    questions = apply_source_metadata_response(reply, plan, CURRICULUM)
    assert questions[0]["question_type"] == correct_type
    reply["items"][0]["question_type"] = wrong_type
    with pytest.raises(SourceMetadataContractError, match="分节"):
        apply_source_metadata_response(reply, plan, CURRICULUM)


def test_unknown_generic_choice_section_does_not_guess_single_or_multi():
    plan = prepare("一、选择题\n1. 已知变量$x=2$，求其值。")
    assert plan["eligible"]
    assert plan["questions"][0]["explicit_question_type"] is None


def test_original_answer_full_context_is_sent_for_classification_without_model_copy_output():
    source = "三、解答题\n1. 已知函数$f(x)=x^2$，求最小值。\n【答案】用导数$f'(x)=2x$判单调。……3分"
    plan = prepare(source)
    assert plan["eligible"], plan["fallback_reasons"]
    items = json.loads(build_source_metadata_messages(plan, CURRICULUM)[1]["content"])["items"]
    assert items[0]["original_answer_context"] == "用导数$f'(x)=2x$判单调。……3分"
    assert items[0]["content"] == plan["questions"][0]["content"]


@pytest.mark.parametrize("answer", [
    "原始示例代码 `[EXTRACTED_ORIGINAL]`。",
    r"原始示例代码 \texttt{[EXTRACTED_ORIGINAL]}。",
    r"原始数学文本 $\text{[EXTRACTED_ORIGINAL]}$。",
    "[EXTRACTED_ORIGINAL][EXTRACTED_ORIGINAL]都是原文数据。",
])
def test_original_source_marker_literals_are_never_deleted_by_protocol_prefix_cleanup(answer):
    plan = prepare("1. 比较原始示例代码。\n答案：" + answer)
    assert plan["eligible"], plan["fallback_reasons"]
    assert plan["questions"][0]["answer_markdown"] == answer
    reply = apply_source_metadata_response(metadata(plan), plan, CURRICULUM)
    assert reply[0]["answer_markdown"] == "[EXTRACTED_ORIGINAL]" + answer
    items = json.loads(build_source_metadata_messages(plan, CURRICULUM)[1]["content"])["items"]
    assert items[0]["original_answer_context"] == answer


def test_textbox_paragraphs_concatenated_by_native_extractor_cannot_become_one_certified_question(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    from mathbank.docx_helper import extract_docx_markdown
    xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml"><w:body>
    <w:p><w:r><w:t>1. 正常第一题。</w:t></w:r></w:p>
    <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
    <w:p><w:r><w:t>2. 文本框第二题文字</w:t></w:r></w:p>
    <w:p><w:r><w:t>3. 文本框第三题文字。</w:t></w:r></w:p>
    </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
    </w:body></w:document>'''
    memory = BytesIO()
    with ZipFile(memory, "w") as archive:
        archive.writestr("word/document.xml", xml)
    result = extract_docx_markdown(memory.getvalue(), output_dir=tmp_path / "assets")
    assert result["success"]
    source = result["markdown"]
    assert "2. 文本框第二题文字" in source and "3. 文本框第三题文字" in source
    plan = prepare_word_source_metadata(source, result["diagnostics"])
    # When the extractor preserves boundaries, all three can be counted. If
    # they are concatenated, metadata must abstain rather than drop question 3.
    if "\n3." in source:
        assert len(plan["questions"]) == 3
    else:
        assert not plan["eligible"]
        assert "visible_heading_census_mismatch" in plan["fallback_reasons"]


def test_stale_or_json_forged_plan_is_not_an_internal_certificate():
    plan = prepare("1. 求$x$。")
    assert plan["eligible"]
    changed = dict(plan)
    changed["source"] = "1. 求$y$。"
    with pytest.raises(SourceMetadataContractError):
        build_source_metadata_messages(changed, CURRICULUM)
    forged = {key: value for key, value in plan.items() if not key.startswith("_")}
    with pytest.raises(SourceMetadataContractError):
        build_source_metadata_messages(forged, CURRICULUM)
    changed = dict(plan)
    changed["questions"] = deepcopy(plan["questions"])
    changed["questions"][0]["content"] = "changed"
    with pytest.raises(SourceMetadataContractError):
        build_source_metadata_messages(changed, CURRICULUM)
    diagnostics = source_metadata_diagnostics(plan)
    assert "source" not in diagnostics and "questions" not in diagnostics
    assert "求" not in json.dumps(diagnostics, ensure_ascii=False)
