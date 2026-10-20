"""Local exact-range drafting: no models, no database, no reconstructed math."""
from copy import deepcopy
import hashlib

import pytest
import requests

from mathbank.pdf_local_draft import RULES_VERSION, prepare_pdf_local_draft


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session,"request",lambda *a,**kw:pytest.fail("Drafting is local"))


def draft(chunks):
    pages=[{"page_number":i+1,"markdown":f"<!-- MATHBANK_PDF_PAGE:{i+1} -->\n"+text,"origin":"joint_vision"}
           for i,text in enumerate(chunks)]
    source="\n\n".join("\n"+text for text in chunks)
    return source,pages,prepare_pdf_local_draft(source,pages,source_document_sha256="a"*64)


def copy_ranges(source,ranges):
    return "".join(source[a:b] for a,b in ranges)


def test_explicit_stems_original_answers_and_appendix_are_locally_separated():
    source,pages,plan=draft(["2027届高三数学阶段练习\n注意事项\n1. 写姓名。\n一、选择题：只有一项符合要求。\n"
        "1. 求$1+1$。\n\\begin{choices}\\item 1\\item 2\\end{choices}\n"
        "二、解答题\n2. 证明$x^2\\ge0$。\n数 学 第1页 共1页",
        "## 数学参考答案及多维细目表\n1. 【答案】B\n【解析】$1+1=2$。\n2. 解：由平方非负即得。\n数学 参考答案 第1页 共2页",
        "**多维细目表**\n\\begin{tabular}{c}1. 假题号\\\\\\end{tabular}"])
    assert plan["ready"] and not plan["partial"]
    assert [q["source_number"] for q in plan["questions"]]==[1,2]
    assert [q["question_type"] for q in plan["questions"]]==["single_choice","detailed_answer"]
    assert "【答案】B" in plan["questions"][0]["answer_markdown"]
    assert "多维细目表" not in plan["questions"][1]["answer_markdown"]
    assert any(row["role"]=="appendix" for row in plan["unassigned"])
    assert plan["source"]==source and plan["input_sha256"]==hashlib.sha256(source.encode()).hexdigest()
    for item,q in zip(plan["items"],plan["questions"]):
        assert item["question"]==q
        assert copy_ranges(source,item["stem_ranges"])==q["content"]
        assert copy_ranges(source,item["answer_ranges"])==q["answer_markdown"]
        assert item["content"]==q["content"] and item["answer_markdown"]==q["answer_markdown"]
        for receipt in item["removals"]:assert receipt["text"]==source[receipt["start"]:receipt["end"]]


def test_detailed_original_answer_is_preferred_over_summary_line_without_duplication():
    source,pages,plan=draft(["一、单项选择题\n1. 甲条件。\n2. 乙条件。",
        "# 参考答案与解析\n1\\. A    2\\. B\n选择题简析\n1\\. 甲题原解析。\n2\\. 乙题原解析。"])
    assert not plan["partial"] and len(plan["questions"])==2
    assert plan["questions"][0]["answer_markdown"]=="甲题原解析。"
    assert plan["questions"][1]["answer_markdown"]=="乙题原解析。"


def test_explicit_summary_table_cells_can_supply_original_answers_without_position_guess():
    source,pages,plan=draft(["一、单项选择题\n1. 甲条件。\n2. 乙条件。",
        "# 参考答案\n\\begin{tabular}{|c|c|c|}\n\\hline\n题号 & 2 & 1 \\\\\n答案 & B & A \\\\\n\\hline\n\\end{tabular}"])
    assert [q["answer_markdown"] for q in plan["questions"]]==["A","B"]
    assert not plan["partial"]
    assert all(copy_ranges(source,it["answer_ranges"])==it["answer_markdown"] for it in plan["items"])


def test_cross_page_stem_and_answer_continuations_are_copied_exactly():
    source,pages,plan=draft(["四、解答题\n1. 题干前半$A$，\n数学（模拟卷） 第1页 共4页",
        "续题干后半$B$。\n2. 第二道完整题。\n数学（模拟卷） 第2页 共4页",
        "# 参考答案\n1. 原解前半$x+1$，\n数学 参考答案 第1页 共2页",
        "续原解后半$x+2$。\n2. 第二题原答案。\n数学 参考答案 第2页 共2页"])
    first=plan["questions"][0]
    assert "题干前半$A$，" in first["content"] and "续题干后半$B$。" in first["content"]
    assert "原解前半$x+1$，" in first["answer_markdown"] and "续原解后半$x+2$。" in first["answer_markdown"]
    assert "第1页" not in first["content"]+first["answer_markdown"]
    assert any(r["reason"]=="page_footer" for r in plan["items"][0]["removals"])


def test_known_inline_question_heads_outside_atomic_structure_are_char_ranges():
    source,pages,plan=draft(["一、解答题\n1. 第一题$x=1$。 2. 第二题$x=2$。\n# 参考答案\n1. 一答。 2. 二答。"])
    assert [q["source_number"] for q in plan["questions"]]==[1,2]
    assert plan["questions"][0]["content"]=="第一题$x=1$。"
    assert plan["questions"][1]["answer_markdown"]=="二答。"


def test_choices_math_code_and_nested_table_numbers_never_become_cards():
    source,pages,plan=draft(["一、单项选择题\n1. 原题$x=1$。\n"
        "\\begin{choices}\\item A\n8. 选项文字\n\\end{choices}\n"
        "\\begin{tabular}{c}9. 外表\n\\begin{tabular}{c}10. 内表\\end{tabular}\\end{tabular}\n"
        "```text\n11. 代码\n```\n$$\n12. 数学\n$$\n# 参考答案\n1. 原答。"])
    assert len(plan["questions"])==1 and "10. 内表" in plan["questions"][0]["content"]
    assert not plan["partial"]


def test_duplicate_answers_remain_local_manual_ranges_without_paying_or_losing_stem():
    source,pages,plan=draft(["一、解答题\n1. 唯一原题。\n# 参考答案\n1. 原答甲。\n1. 原答乙。"])
    assert len(plan["questions"])==1 and plan["partial"]
    assert plan["questions"][0]["answer_markdown"]==""
    assert plan["items"][0]["manual_reasons"]==["original_answer_ambiguous"]
    assert len([r for r in plan["unassigned"] if r["role"]=="manual"])==2


def test_original_answer_for_unknown_question_remains_an_explicit_manual_range():
    source,_,plan=draft(["一、解答题\n1. 唯一原题。\n# 参考答案\n1. 一答。\n2. 没有对应题干的原答案。"])
    assert plan["ready"] and plan["partial"]
    assert plan["questions"][0]["answer_markdown"]=="一答。"
    orphan=[row for row in plan["unassigned"] if row["reason"]=="original_answer_without_question"]
    assert len(orphan)==1 and orphan[0]["source_number"]==2
    assert orphan[0]["text"]==source[slice(*orphan[0]["range"])]
    assert "没有对应题干的原答案" in orphan[0]["text"]


def test_no_original_answers_is_a_legal_local_question_draft():
    _,_,plan=draft(["一、解答题\n1. 原题独立完整。"])
    assert plan["ready"] and not plan["partial"] and plan["questions"][0]["answer_markdown"]==""


def test_source_review_marker_is_retained_without_marking_structure_incomplete():
    _,_,plan=draft(["一、解答题\n1. 原题$x=1$。\n# 参考答案\n1. $x=1$[公式待核对]。"])
    assert not plan["partial"] and "[公式待核对]" in plan["questions"][0]["answer_markdown"]
    assert plan["questions"][0]["source_review"]["required"] and not plan["items"][0]["manual_reasons"]


def test_stable_original_document_ids_do_not_change_after_another_card_edit():
    source,pages,first=draft(["一、解答题\n1. 第一题。\n2. 第二题。\n# 参考答案\n1. 一答。\n2. 二答。"])
    source2=source.replace("第一题。","第一题的新人工校对正文。")
    pages2=deepcopy(pages);pages2[0]["markdown"]=pages2[0]["markdown"].replace("第一题。","第一题的新人工校对正文。")
    second=prepare_pdf_local_draft(source2,pages2,source_document_sha256="a"*64)
    assert [i["id"] for i in first["items"]]==[i["id"] for i in second["items"]]
    assert first["questions"][1]==second["questions"][1]


def test_shared_mathematical_material_is_kept_in_every_affected_card():
    _,_,plan=draft(["一、解答题\n下列各题共用参数$t=5$。\n1. 第一题。\n2. 第二题。\n# 参考答案\n1. 一答。\n2. 二答。"])
    assert all("共用参数$t=5$" in q["content"] for q in plan["questions"])
    assert all(q["shared_context"] for q in plan["questions"])


def test_following_section_shared_material_is_not_appended_to_previous_question():
    _,_,plan=draft(["一、解答题\n1. 第一部分题目。\n二、解答题\n下列各题共用参数$t=5$。\n2. 第二部分题目。\n# 参考答案\n1. 一答。\n2. 二答。"])
    assert "共用参数" not in plan["questions"][0]["content"]
    assert "共用参数$t=5$" in plan["questions"][1]["content"]


def test_unbound_page_source_is_explicit_manual_not_silent_zero_success():
    source,pages,plan=draft(["一、解答题\n1. 原题。"]);pages[0]["markdown"]+="changed"
    result=prepare_pdf_local_draft(source,pages)
    assert not result["ready"] and result["partial"] and result["unassigned"][0]["role"]=="manual"
    assert result["source"]==source


def test_unclosed_atomic_source_is_retained_for_local_review():
    source,_,plan=draft(["一、解答题\n1. 原题\n\\begin{choices}\n\\item A\n2. 不可靠后文"])
    assert plan["ready"] and plan["partial"] and "2. 不可靠后文" in plan["source"]
    assert "unclosed_environment" in plan["manual_reasons"]
    blocked=[row for row in plan["unassigned"] if row["reason"]=="unclosed_environment"]
    assert len(blocked)==1 and blocked[0]["role"]=="manual"
    assert blocked[0]["text"]==source[slice(*blocked[0]["range"])]


def assert_source_receipts(plan):
    source=plan["source"]
    assert plan["rules_version"]==RULES_VERSION
    for item,q in zip(plan["items"],plan["questions"]):
        assert copy_ranges(source,item["stem_ranges"])==q["content"]
        assert copy_ranges(source,item["answer_ranges"])==q["answer_markdown"]
        assert item["question"]==q
        for receipt in item["removals"]:
            assert receipt["text"]==source[receipt["start"]:receipt["end"]]
    for row in plan["page_furniture"]:
        assert row["text"]==source[row["start"]:row["end"]]


@pytest.mark.parametrize("footer",[
    "数   学   参 考 答 案   第 3 页 共 5 页",
    "数 学 参 考 答 案 \n第3页 \n\n\n, \n\n\n\n\n5页",
    "数学参考答案\n第3页\n共5页",
    "数学\n参考答案\n第3页\n/\n共5页",
    "数学（模拟卷Ⅱ）参考答案\n第3页\n共5页",
])
def test_physical_page_footer_is_proved_before_answer_heading_scan(footer):
    source,_,plan=draft(["四、解答题\n1. 原题一。\n2. 原题二。",
        "# 数学参考答案及多维细目表\n1. 一答。\n2. 原解前半。\n"+footer,
        "原解后半。"])
    assert plan["ready"] and not plan["partial"]
    assert [q["source_number"] for q in plan["questions"]]==[1,2]
    assert plan["questions"][0]["answer_markdown"]=="一答。"
    assert "原解前半。" in plan["questions"][1]["answer_markdown"]
    assert "原解后半。" in plan["questions"][1]["answer_markdown"]
    assert "第3页" not in plan["questions"][1]["answer_markdown"]
    assert "数 学 参 考 答 案" not in plan["questions"][1]["answer_markdown"]
    assert len(plan["page_furniture"])==1 and plan["page_furniture"][0]["page_number"]==2
    assert "page_footer" in {r["reason"] for r in plan["items"][1]["removals"]}
    assert plan["source"]==source and footer in source
    assert_source_receipts(plan)


def test_first_standalone_answer_heading_at_page_break_is_a_legal_title():
    _,_,plan=draft(["一、解答题\n1. 唯一原题。", "数学参考答案\n第1页 共2页", "1. 唯一原解。"])
    assert plan["ready"] and not plan["partial"] and plan["questions"][0]["answer_markdown"]=="唯一原解。"
    assert all("数学参考答案" not in row["text"] for row in plan["page_furniture"])
    assert_source_receipts(plan)


@pytest.mark.parametrize("tail",[
    "数学参考答案", "数学参考答案\n第3页", "数学参考答案\n第3页\n5页",
    "数学参考答案\n第8页\n共5页", "数学参考答案\n第3页\n,\n5页\n这仍是正文。",
])
def test_unproved_terminal_answer_heading_is_preserved_as_ambiguity(tail):
    source,_,plan=draft(["一、解答题\n1. 唯一原题。", "# 参考答案\n1. 第一版原解。\n"+tail])
    assert plan["ready"] and plan["partial"] and len(plan["questions"])==1
    assert plan["questions"][0]["content"]=="唯一原题。"
    assert plan["questions"][0]["answer_markdown"]==""
    assert plan["manual_reasons"]==["answer_section_ambiguous"]
    assert plan["source"]==source and tail in plan["unassigned"][1]["text"]
    assert plan["page_furniture"]==[]
    assert_source_receipts(plan)


def test_true_two_answer_sections_keep_unique_stems_without_choosing_an_answer():
    source,_,plan=draft(["一、解答题\n1. 第一原题。\n2. 第二原题。",
        "# 参考答案\n1. 甲版一答。\n2. 甲版二答。\n数学 参考答案 第1页 共2页",
        "# 数学参考答案\n1. 乙版一答。\n2. 乙版二答。\n数学 参考答案 第2页 共2页"])
    assert len(plan["questions"])==2 and plan["ready"] and plan["partial"]
    assert [q["content"] for q in plan["questions"]]==["第一原题。","第二原题。"]
    assert all(q["answer_markdown"]=="" for q in plan["questions"])
    assert plan["manual_reasons"]==["answer_section_ambiguous"]
    conflicts=[row for row in plan["unassigned"] if row["role"]=="manual"]
    assert len(conflicts)==1 and "甲版一答。" in conflicts[0]["text"] and "乙版一答。" in conflicts[0]["text"]
    assert conflicts[0]["text"]==source[slice(*conflicts[0]["range"])]
    assert plan["page_issues"]==[{"page_number":n,"reason_codes":["answer_section_ambiguous"]} for n in [2,3]]
    assert all(q["source_review"]["required"] for q in plan["questions"])
    assert_source_receipts(plan)


def test_answer_words_in_prose_are_copied_without_becoming_section_boundaries():
    _,_,plan=draft(["一、解答题\n1. 本题要求说明参考答案中结论的理由。\n数学参考答案不是本文标题。",
        "# 数学参考答案\n1. 不使用参考答案，直接证明。"])
    assert not plan["partial"] and len(plan["questions"])==1
    assert "数学参考答案不是本文标题。" in plan["questions"][0]["content"]
    assert "不使用参考答案" in plan["questions"][0]["answer_markdown"]
    assert_source_receipts(plan)


@pytest.mark.parametrize("opening,closing",[("```text","```"),("$$","$$"),
    (r"\begin{align}",r"\end{align}"),(r"\begin{tabular}{c}",r"\end{tabular}")])
def test_footer_looking_code_math_and_environment_source_is_not_removed(opening,closing):
    _,_,plan=draft(["一、解答题\n1. 保留字面片段。\n"+opening+"\n数学参考答案\n第3页共5页\n"+closing,
        "# 参考答案\n1. 唯一原解。"])
    assert len(plan["questions"])==1 and not plan["partial"]
    assert "数学参考答案\n第3页共5页" in plan["questions"][0]["content"]
    assert plan["page_furniture"]==[]
    assert_source_receipts(plan)


def test_cross_page_atomic_environment_does_not_lend_its_footer_text_to_heading_rules():
    _,_,plan=draft(["一、单项选择题\n1. 保留原选项。\n\\begin{choices}\n\\item A\n数学参考答案\n第3页共5页",
        "\\item B\n\\end{choices}\n# 参考答案\n1. 原解。"])
    assert len(plan["questions"])==1 and not plan["partial"]
    assert "数学参考答案\n第3页共5页" in plan["questions"][0]["content"]
    assert plan["page_furniture"]==[]
    assert_source_receipts(plan)


def test_page_counter_inside_body_or_too_far_from_tail_is_preserved():
    _,_,plan=draft(["一、解答题\n1. 第一步。\n数学 第1页 共4页\n后续是实际题干。",
        "# 参考答案\n1. 一答。"])
    assert not plan["partial"]
    assert "数学 第1页 共4页" in plan["questions"][0]["content"]
    assert plan["page_furniture"]==[]
    assert_source_receipts(plan)


@pytest.mark.parametrize("declaration",["一、填空题：本题共 2 小题。","一、填空题"])
def test_stray_dollar_cannot_hide_a_proved_next_question_or_original_answers(declaration):
    source,_,plan=draft([declaration+"\n1. 第一题令 $a=$ \\underline{\\hspace{2em}}$\n\n"
        "2. 第二题保留 $x+1$。\n# 参考答案\n1. 第一份原答案。\n2. 第二份原答案。"])
    assert plan["ready"] and not plan["partial"]
    assert [q["source_number"] for q in plan["questions"]]==[1,2]
    assert plan["questions"][0]["content"]=="第一题令 $a=$ \\underline{\\hspace{2em}}$"
    assert plan["questions"][1]["content"]=="第二题保留 $x+1$。"
    assert [q["answer_markdown"] for q in plan["questions"]]==["第一份原答案。","第二份原答案。"]
    assert plan["questions"][0]["source_review"]["reasons"]==["source_math_delimiter_requires_review"]
    assert not plan["questions"][0]["source_review"]["original_verified"]
    assert plan["page_issues"]==[{"page_number":1,"reason_codes":["source_math_delimiter_requires_review"]}]
    assert plan["structure_census"]["recovered_boundaries"]
    assert plan["structure_census"]["final_numbers"]==[1,2]
    assert plan["source"]==source
    assert_source_receipts(plan)


def test_unproved_missing_question_census_is_partial_and_keeps_exact_manual_source():
    source,_,plan=draft(["一、填空题：本题共3小题。\n1. 第一题 $a=$ \\underline{\\hspace{2em}}$\n\n"
        "3. 第三题 $b=1$。\n# 参考答案\n1. 一答。\n3. 三答。"])
    assert plan["partial"] and "source_structure_census_requires_review" in plan["manual_reasons"]
    manual=[row for row in plan["unassigned"] if row["reason"]=="source_structure_census_requires_review"]
    assert manual and all(row["text"]==source[slice(*row["range"])] for row in manual)
    assert any("第三题" in row["text"] for row in manual)
    assert not plan["structure_census"]["independent_boundaries_proved"]
    assert not plan["structure_census"]["recovered_boundaries"]
    assert any("source_structure_census_requires_review" in row["reason_codes"] for row in plan["page_issues"])
    assert_source_receipts(plan)


def test_unproved_cross_paragraph_inline_shell_never_reports_complete_success():
    _,_,plan=draft(["一、解答题\n1. 原题一留下 $\n\n2. 第二题 $x+1$。"])
    assert plan["partial"] and "source_structure_census_requires_review" in plan["manual_reasons"]
    assert not plan["structure_census"]["independent_boundaries_proved"]
    assert_source_receipts(plan)


@pytest.mark.parametrize("opening,closing",[("$","$"),("$$","$$"),(r"\(",r"\)"),(r"\[",r"\]")])
def test_legal_multiline_math_cannot_supply_question_heads_or_document_titles(opening,closing):
    block=opening+"\n2. 这是公式内的数字行\n\n二、填空题：本题共5小题。\n数学参考答案\n"+closing
    _,_,plan=draft(["一、解答题：本题共2小题。\n1. 保留下面的公式块。\n"+block+"\n2. 真正第二题。",
        "# 参考答案\n1. 一答。\n2. 二答。"])
    assert not plan["partial"] and len(plan["questions"])==2
    assert block in plan["questions"][0]["content"]
    assert not plan["structure_census"]["recovered_boundaries"]
    assert plan["questions"][0]["source_review"]["reasons"]==[]
    assert_source_receipts(plan)


def test_legal_inline_multiline_dollar_with_numbered_math_line_is_kept():
    formula="$x+\n2. y\n+z$"
    _,_,plan=draft(["一、解答题：本题共2小题。\n1. 已知 "+formula+"，求值。\n2. 真正第二题。",
        "# 参考答案\n1. 一答。\n2. 二答。"])
    assert not plan["partial"] and len(plan["questions"])==2
    assert formula in plan["questions"][0]["content"]
    assert not plan["structure_census"]["recovered_boundaries"]
    assert_source_receipts(plan)


def test_legal_inline_multiline_dollar_with_title_words_is_kept():
    formula="$x+\n数学参考答案\n+z$"
    _,_,plan=draft(["一、解答题：本题共2小题。\n1. 已知 "+formula+"，求值。\n2. 真正第二题。",
        "# 参考答案\n1. 一答。\n2. 二答。"])
    assert not plan["partial"] and len(plan["questions"])==2
    assert formula in plan["questions"][0]["content"]
    assert not plan["structure_census"]["recovered_boundaries"]
    assert_source_receipts(plan)


def test_structural_census_ignores_code_and_cross_page_atomic_numbers():
    _,_,plan=draft(["一、解答题：本题共2小题。\n1. 原题。\n```text\n99. 代码\n数学参考答案\n```\n"
        "\\begin{choices}\n\\item A\n2. 跨页环境内的数字行",
        "三、填空题：本题共5小题。\n数学参考答案\n\\item B\n\\end{choices}\n2. 真正第二题。",
        "# 参考答案\n1. 一答。\n2. 二答。"])
    assert not plan["partial"] and len(plan["questions"])==2
    assert "99. 代码" in plan["questions"][0]["content"]
    assert "2. 跨页环境内的数字行" in plan["questions"][0]["content"]
    assert plan["structure_census"]["independent_numbers"]==[1,2]
    assert not plan["structure_census"]["recovered_boundaries"]
    assert_source_receipts(plan)
