"""PDF/OCR source forms reproduced from captured local import diagnostics.

Fixtures are small authored equivalents. An optional captured-task replay uses
local user files without committing the exam or calling any external model.
"""

import copy
import json
import os
from pathlib import Path
import re

import pytest

from mathbank.content_locks import _source_parts, lock_visible_math, reconcile_visible_math


@pytest.mark.parametrize('heading', ['题目 1：', '题目1:', '题目1.', '题目 1．', '**题目 1：**', '### 题目 1：', '题 1：', '第1题：',
                                     '题目 **1**：', '题目\t** 1 ** ： ', '题目 __1__ :', r'题目 \textbf{1}：', '## 题目 1：'])
def test_explicit_question_labels_with_colons_keep_local_answers_and_comments(heading):
    source = heading + '已知 $x=2$，求值。\n答案：2\n评析：保留本题评论。\n\n题目 2：求 $y$。\n答案：$y=3$'
    _, locks = lock_visible_math(source, 'colon-heading')
    questions = [{'content': '已知 $x=2$，求值。', 'answer_markdown': '2\n评析：保留本题评论。'},
                 {'content': '求 $y$。', 'answer_markdown': '$y=3$'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == report['math_locks_missing'] == 0
    assert [(p.number, p.field) for p in _source_parts(source, locks)] == [
        (1, 'content'), (1, 'answer_markdown'), (2, 'content'), (2, 'answer_markdown')]
    assert report['math_locks_restored'] == 3
    assert not report['unmatched_source']


@pytest.mark.parametrize('text', ['1:2', '12: 30', '**1**:2', '__1__： 2', r'\textbf{1}:2', '1.5', '题目1.5', '题目1000：', '讨论题目2：不是行首题号'])
def test_question_colon_recognition_does_not_split_ratios_decimals_or_inline_labels(text):
    from mathbank.content_locks import _NUMBER
    assert _NUMBER.match(text) is None


def test_numbered_labels_inside_math_code_and_comments_are_not_new_questions():
    source = '题目1：比较如下文字。\n```text\n题目2：示例编号\n```\n<!--\n题目3：注释\n-->\n$$\n题目4：x=2\n$$\n题目5：真实下一题。'
    _, locks = lock_visible_math(source, 'literal-headings')
    parts = [p for p in _source_parts(source, locks) if p.field == 'content']
    assert [p.number for p in parts] == [1, 5]


def test_unclosed_code_block_does_not_create_numbered_question_sources():
    source = '题目1：以下为文字示例。\n```text\n题目2：待补充\n题目3：求值。'
    _, locks = lock_visible_math(source, 'unclosed-code')
    assert [p.number for p in _source_parts(source, locks) if p.field == 'content'] == [1]


@pytest.mark.parametrize('label', ['待补充', '（待补充）', '【待补充】', '待补充。'])
def test_explicit_whole_question_placeholder_is_reported_separately(label):
    source = '题目1：计算 $x$。\n答案：1\n题目2：' + label + '\n题目3：求 $y$。\n答案：2'
    _, locks = lock_visible_math(source, 'placeholder')
    questions = [{'content': '计算 $x$。', 'answer_markdown': '1'}, {'content': '求 $y$。', 'answer_markdown': '2'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == report['math_locks_missing'] == 0
    assert not report['unmatched_source']
    assert len(report['ignored_source_notes']) == 1
    assert '原题 2' in report['ignored_source_notes'][0]
    assert '待补充' in report['ignored_source_notes'][0]
    placeholder = next(p for p in _source_parts(source, locks) if p.field == 'placeholder')
    assert source[placeholder.source_start:placeholder.source_end] == placeholder.text


@pytest.mark.parametrize('body', ['待补充函数 $f(x)$ 的定义。', '待补充\n答案：$x=2$', '待补充\n评析：这是一个问题。', '待补充\n![](/static/uploads/test.png)', '待补充条件后求值。'])
def test_placeholder_label_cannot_hide_formula_image_answer_or_real_body(body):
    source = '题目1：求 $y$。\n题目2：' + body
    _, locks = lock_visible_math(source, 'real-placeholder-text')
    questions = [{'content': '求 $y$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['unmatched_source']
    assert report['ignored_source_notes'] == []


def test_leading_question_type_and_source_are_compared_against_exact_metadata():
    source = '题目1：（多选题）已知 $x=2$，求值。\n答案：A\n评析：$x>0$。\n题目2：(2025·武汉二中高一月考) 求 $y$。\n答案：2'
    _, locks = lock_visible_math(source, 'metadata-heading')
    questions = [{'content': '已知 $x=2$，求值。', 'answer_markdown': 'A\n评析：$x>0$。', 'question_type': 'multi_choice'},
                 {'content': '求 $y$。', 'answer_markdown': '2', 'source': '2025·武汉二中高一月考'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == report['math_locks_missing'] == 0
    assert report['math_locks_restored'] == 3
    for match in report['source_matches']:
        assert source[match['source_start']:match['source_end']] == match['source_excerpt']
    assert '（多选题）' in report['source_matches'][0]['source_excerpt']
    assert '2025·武汉二中' in report['source_matches'][1]['source_excerpt']


@pytest.mark.parametrize('retain_type,retain_source', [(True, True), (True, False), (False, True), (False, False)])
def test_verified_metadata_may_remain_in_stem_or_move_to_fields(retain_type, retain_source):
    image = '![](/static/uploads/metadata.png)'
    source = '题目1：（多选题）(2025·武汉二中高一月考) 已知 $x=2$，看图' + image + '求值。'
    _, locks = lock_visible_math(source, 'retained-metadata')
    prefix = ('（多选题）' if retain_type else '') + ('(2025·武汉二中高一月考)' if retain_source else '')
    content = prefix + ' 已知 $x=2$，看图' + image + '求值。'
    questions = [{'content': content, 'question_type': 'multi_choice', 'source': '2025·武汉二中高一月考'}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == report['math_locks_missing'] == 0
    assert questions[0]['content'] == content
    assert report['source_matches'][0]['source_excerpt'] == source
    assert report['source_matches'][0]['source_start'] == 0
    assert report['source_matches'][0]['source_end'] == len(source)


@pytest.mark.parametrize('change', ['formula', 'image', 'prefix_order'])
def test_retained_metadata_does_not_hide_math_image_or_prefix_changes(change):
    image = '![](/static/uploads/metadata.png)'
    source = '题目1：（多选题）(2025·武汉二中高一月考) 已知 $x=2$，看图' + image + '求值。'
    content = source.removeprefix('题目1：')
    if change == 'formula':
        content = content.replace('$x=2$', '$x=3$')
    elif change == 'image':
        content = content.replace(image, '') + image
    else:
        content = content.replace('（多选题）(2025·武汉二中高一月考)', '(2025·武汉二中高一月考)（多选题）')
    _, locks = lock_visible_math(source, 'retained-but-changed')
    report = reconcile_visible_math([{'content': content, 'question_type': 'multi_choice',
                                     'source': '2025·武汉二中高一月考'}], locks, source)
    assert report['source_review_count'] == 1


def test_retained_mathematical_constraint_does_not_make_later_brackets_metadata():
    source = '题目1：(x=2)(2025·武汉二中高一月考) 求 $x$。'
    _, locks = lock_visible_math(source, 'constraint-prefix')
    report = reconcile_visible_math([{'content': '(x=2) 求 $x$。', 'source': '2025·武汉二中高一月考'}], locks, source)
    assert report['source_review_count'] == 1


@pytest.mark.parametrize(('label', 'metadata'), [
    ('多选题', {'question_type': 'single_choice'}), ('多选题', {}),
    ('2025·武汉二中高一月考', {'source': '2025·武汉三中高一月考'}),
    ('x=2', {'source': 'x=2'}),
    ('2025个元素', {'source': '2025个元素'}),
    ('2025年高考每人100分', {'source': '2025年高考每人100分'}),
    ('2025年已知每人100分月考', {'source': '2025年已知每人100分月考'}),
    ('2025维空间测试', {'source': '2025维空间测试'}),
    ('2025元测试', {'source': '2025元测试'}),
    ('2025次测试', {'source': '2025次测试'}),
    ('2025个测试', {'source': '2025个测试'}),
    ('2025·空间测试', {'source': '2025·空间测试'}),
    ('2025·高一100维空间测试', {'source': '2025·高一100维空间测试'}),
    ('2025·高一100元月考', {'source': '2025·高一100元月考'}),
])
def test_metadata_prefix_never_hides_conditions_or_mismatched_fields(label, metadata):
    source = '题目1：（' + label + '）求 $x$。'
    _, locks = lock_visible_math(source, 'wrong-metadata')
    questions = [{'content': '求 $x$。', **metadata}]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == 1


def test_new_question_heading_does_not_hide_changed_math_or_removed_comment():
    source = '题目1：求 $x+1$。\n答案：2\n评析：由 $x=1$ 得到。\n题目2：求 $y$。'
    _, locks = lock_visible_math(source, 'heading-change')
    questions = [{'content': '求 $x-1$。', 'answer_markdown': '2'}, {'content': '求 $y$。'}]
    report = reconcile_visible_math(questions, locks, source)
    assert questions[0]['source_review']['required']
    assert report['math_locks_missing'] >= 2


@pytest.mark.parametrize('returned', ['已知 $28$ 人，求值。', '已知28人，求值。'])
def test_type_metadata_does_not_break_numeric_delimiter_matching(returned):
    source = '题目1：（填空题）已知28人，求值。'
    _, locks = lock_visible_math(source, 'metadata-number')
    questions = [{'content': returned, 'question_type': 'fill_in_blank'}]
    assert reconcile_visible_math(questions, locks, source)['source_review_count'] == 0


@pytest.mark.skipif(not os.environ.get('MATHBANK_PDF_CHALLENGE_TASK'), reason='local challenge PDF cache is opt-in')
def test_challenge_pdf_cached_four_questions_match_original_numbers_and_comments():
    from mathbank.pdf_inspector_helper import merge_pdf_page_texts
    task = json.loads(Path(os.environ['MATHBANK_PDF_CHALLENGE_TASK']).read_text())
    source = re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->', '', merge_pdf_page_texts(
        [page['markdown'] for page in task['pdf_source_pages']]))
    _, locks = lock_visible_math(source, 'challenge-real')
    questions = copy.deepcopy(task['data'])
    for question in questions:
        question.pop('source_review', None)
    before = [(q['content'], q['answer_markdown']) for q in questions]
    report = reconcile_visible_math(questions, locks, source)
    assert report['source_review_count'] == report['math_locks_missing'] == 0
    assert report['math_locks_restored'] == report['math_locks_created'] == 30
    assert report['unmatched_source'] == []
    assert '原题 3' in report['ignored_source_notes'][0]
    assert [m['source_number'] for m in report['source_matches'] if m['field'] == 'content'] == [1, 2, 4, 5]
    assert [m['source_number'] for m in report['source_matches'] if m['field'] == 'answer_markdown'] == [1, 2, 4, 5]
    assert [(q['content'], q['answer_markdown']) for q in questions] == before
    assert all('评析：' in questions[i]['answer_markdown'] for i in (1, 2, 3))
    for match in report['source_matches']:
        assert source[match['source_start']:match['source_end']] == match['source_excerpt']


@pytest.mark.parametrize("number", [
    "1.", "1．", "题 1.", "题1．", "**1.**", "**1**.", "__1.__",
    r"\textbf{1.}", r"\textbf{1}.", r"\textbf{题 1.}",
    "### **1.**", "第 1 题：", "【题 1.】", "【1．】", r"\textbf{【题 1.】}",
])
def test_pdf_number_wrappers_identify_the_same_source_without_changing_math(number):
    source = number + " 已知 $x=2$，求值。\n\n题 2. 求 $a+b$ 的值。"
    _, locks = lock_visible_math(source, "number")
    questions = [{"content": "已知 $x=2$，求值。"}, {"content": "求 $a+b$ 的值。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0
    assert report["math_locks_missing"] == 0
    assert [item["source_number"] for item in report["source_matches"]] == [1, 2]
    for item in report["source_matches"]:
        assert source[item["source_start"]:item["source_end"]] == item["source_excerpt"]


@pytest.mark.parametrize("label", ["【星空解析】", "【解析】", "【答案】", "【分析】", "【详解】"])
def test_inline_branded_explanation_has_its_own_formula_slots(label):
    source = f"题 1. 计算 $1+1$。\n{label}$1+1=2$，故答案为：$2$。"
    _, locks = lock_visible_math(source, "answer")
    questions = [{"content": "计算 $1+1$。", "answer_markdown": "[EXTRACTED_ORIGINAL]2\n\n$1+1=2$，故答案为：$2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert questions[0]["answer_markdown"].startswith("[EXTRACTED_ORIGINAL]2\n")
    assert [match["field"] for match in report["source_matches"]] == ["content", "answer_markdown"]
    assert label not in report["source_matches"][0]["source_excerpt"]


def test_branded_label_inside_a_math_text_macro_is_not_an_answer_boundary():
    content = r"已知 $\text{【星空解析】}$ 是标签，说明其含义。"
    source = "题 1. " + content
    _, locks = lock_visible_math(source, "math-label")
    questions = [{"content": content}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert not report["unmatched_source"]


@pytest.mark.parametrize("prefix", ["D", "0", "19"])
def test_redundant_plain_answer_requires_the_same_explicit_original_result(prefix):
    ending = "故选：$D$。" if prefix == "D" else f"故答案为：${prefix}$。"
    explanation = "$x=2$，" + ending
    source = "**1.** 已知 $x=2$，求值。\n【星空解析】" + explanation
    _, locks = lock_visible_math(source, "summary")
    questions = [{"content": "已知 $x=2$，求值。", "answer_markdown": prefix + "\n\n" + explanation}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0
    questions[0]["answer_markdown"] = "3\n\n" + explanation
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1


@pytest.mark.parametrize("answer", [
    "C\n\n$x=2$，故选：$D$。",  # conflicting new summary
    "D",  # explanation dropped
    "D\n\n$x=3$，故选：$D$。",  # altered formula despite correct summary
    "D\n\n故选：$D$。",  # missing formula despite correct summary
])
def test_real_answer_changes_and_losses_remain_reviewable(answer):
    source = "题 1. 已知 $x=2$，求值。\n【星空解析】$x=2$，故选：$D$。"
    _, locks = lock_visible_math(source, "changes")
    questions = [{"content": "已知 $x=2$，求值。", "answer_markdown": answer}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert questions[0]["answer_markdown"] == answer


def test_answer_origin_marker_can_be_on_its_own_line():
    source = "题 1. 计算 $1+1$。\n【星空解析】$1+1=2$，故答案为：$2$。"
    _, locks = lock_visible_math(source, "marker")
    value = "\n[EXTRACTED_ORIGINAL]\n2\n\n$1+1=2$，故答案为：$2$。"
    questions = [{"content": "计算 $1+1$。", "answer_markdown": value}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0
    assert questions[0]["answer_markdown"] == value


def test_cross_page_footer_does_not_corrupt_continuing_explanation():
    source = "题 1. 计算 $1+1$。\n【星空解析】先算\n\n### 第 1 页/共 2 页\n\n$1+1=2$，故答案为：$2$。"
    _, locks = lock_visible_math(source, "footer")
    questions = [{"content": "计算 $1+1$。", "answer_markdown": "2\n\n先算 $1+1=2$，故答案为：$2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0


def test_decimal_at_line_start_does_not_become_a_question_number():
    source = "题 1. 数据如下：\n1.5 和 $x=2$。\n\n题 2. 求 $y$。"
    _, locks = lock_visible_math(source, "decimal")
    parts = [part for part in _source_parts(source, locks) if part.field == "content"]
    assert len(parts) == 2
    assert "1.5" in parts[0].text


def test_bold_section_heading_is_not_added_to_preceding_question():
    source = "**一、单选题**\n**1.** 计算 $1+1$。\n\n二**、**填空题\n题 2. 求 $x$。"
    _, locks = lock_visible_math(source, "headings")
    questions = [{"content": "计算 $1+1$。"}, {"content": "求 $x$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0


def test_number_wrapper_does_not_relax_changed_formula_or_option_image_position():
    image = "![](/static/uploads/tmp/pdf_figure.png)"
    source = "题 1. 已知 $x=2$。\nA. " + image + " B. $1$\n\n题 2. 计算 $1+1$。"
    _, locks = lock_visible_math(source, "image")
    questions = [{"content": "题 1. 已知 $x=3$。\\begin{choices}\\item $1$\\item " + image + "\\end{choices}"},
                 {"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert any("插图" in reason for reason in questions[0]["source_review"]["reasons"])


@pytest.mark.parametrize("blank", [r"$(\quad)$", r"$()$", r"\((\qquad)\)", r"$\left(\quad\right)$"])
def test_empty_answer_bracket_before_choices_is_presentation_only(blank):
    options = r"\begin{choices}\item $1$\item $2$\end{choices}"
    source = "题 1. 计算 $1+1$，则 " + blank + "\n" + options
    locked, locks = lock_visible_math(source, "choice-blank")
    assert blank in locked
    assert len(locks) == 3  # expression and two options; no empty-bracket lock
    questions = [{"content": "计算 $1+1$，则 （ ）\n" + options}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0


@pytest.mark.parametrize("expression", ["$(x)$", "$[0,1)$", r"$(\quad x)$", "$()$"])
def test_actual_parentheses_or_blank_without_choices_still_need_formula_verification(expression):
    suffix = "，解释该记号。" if expression == "$()$" else r"\begin{choices}\item $1$\item $2$\end{choices}"
    source = "题 1. 给定 " + expression + suffix
    _, locks = lock_visible_math(source, "real-parentheses")
    assert locks[0].original == expression
    questions = [{"content": "题 1. 给定 （ ）" + suffix}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert report["math_locks_missing"] >= 1


def test_same_number_different_source_answers_remains_ambiguous():
    source = "题 1. 已知 $x=2$。\n【答案】$2$\n\n参考答案：\n1. $3$"
    _, locks = lock_visible_math(source, "conflict")
    questions = [{"content": "已知 $x=2$。", "answer_markdown": "$2$"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert any("唯一定位对应答案" in reason for reason in questions[0]["source_review"]["reasons"])


def test_repeated_number_with_different_formulas_is_not_collapsed_into_one_question():
    source = "题 1. 计算 $x+1$。\n\n题 1. 计算 $x+2$。"
    _, locks = lock_visible_math(source, "repeated-number")
    questions = [{"content": "计算 $x+1$。"}, {"content": "计算 $x+2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 2
    assert report["math_locks_restored"] == 0
    assert report["source_matches"] == []


def test_bracketed_question_headings_remain_separate_from_subquestions_and_answers():
    source = ("四、解答题：\n\n【题 15.】已知集合 $A$。\n(1) 求 $A$。\n(2) 求 $B$。"
              "\n【星空解析】(1) 得到 $A=\\{1\\}$。\n(2) 得到 $B=\\{2\\}$。"
              "\n\n【题 16.】已知函数 $f(x)=x$。\n(1) 求 $f(1)$。")
    _, locks = lock_visible_math(source, "bracketed")
    parts = _source_parts(source, locks)
    questions = [
        {"content": "已知集合 $A$。\n(1) 求 $A$。\n(2) 求 $B$。",
         "answer_markdown": "[EXTRACTED_ORIGINAL](1) 得到 $A=\\{1\\}$。\n(2) 得到 $B=\\{2\\}$。"},
        {"content": "已知函数 $f(x)=x$。\n(1) 求 $f(1)$。"},
    ]
    report = reconcile_visible_math(questions, locks, source)
    assert [part.number for part in parts if part.field == "content"] == [15, 16]
    assert report["source_review_count"] == report["math_locks_missing"] == 0


def test_wrong_source_id_in_answer_is_not_excused_by_correct_answer_summary():
    source = (r"【题 14.】设参数 $a,b$，求 $a+2b=\fillin$。" + "\n"
              + r"【星空解析】记 $f(x)=(ax+2)(x^2+2b)$，故答案为：$-3$。")
    _, locks = lock_visible_math(source, "wrong-answer-id")
    questions = [{
        "content": r"设参数 $a,b$，求 $a+2b=\fillin$。",
        "answer_markdown": f"[EXTRACTED_ORIGINAL]-3\n\n记 [[{locks[1].lock_id}]]，故答案为：$-3$。",
    }]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert any("其他位置" in reason for reason in questions[0]["source_review"]["reasons"])
    assert r"f(x)=(ax+2)(x^2+2b)" in questions[0]["source_review"]["source_excerpt"]
    assert r"a+2b=\fillin" in questions[0]["answer_markdown"]


def test_duplicate_results_do_not_advertise_unique_source_matches():
    source = "题 1. 已知 $x=2$。"
    _, locks = lock_visible_math(source, "duplicate")
    questions = [{"content": "已知 $x=2$。"}, {"content": "已知 $x=2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 2
    assert report["source_matches"] == []


def test_question_and_separate_answer_pages_keep_distinct_exact_source_ranges():
    content = "题 1. 已知 $x=2$，求值。"
    answer = r"$2$。见图 ![](/static/uploads/tmp/answer_figure.png)"
    source = content + "\n\n第1页/共2页\n\n参考答案：\n\n1. " + answer
    _, locks = lock_visible_math(source, "two-pages")
    questions = [{"content": "已知 $x=2$，求值。", "answer_markdown": "[EXTRACTED_ORIGINAL]" + answer}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0
    matches = report["source_matches"]
    assert [(match["question_index"], match["source_number"], match["field"]) for match in matches] == [
        (0, 1, "content"), (0, 1, "answer_markdown"),
    ]
    assert "answer_figure.png" not in matches[0]["source_excerpt"]
    assert "answer_figure.png" in matches[1]["source_excerpt"]
    assert matches[0]["source_end"] <= matches[1]["source_start"]
    for match in matches:
        assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"]


def _captured_q14_fillin_texts():
    # Exact discrepancy from task 87585a97: the source has JSON-decoded U+000C,
    # while the splitter returns the intended layout macro. All math is equal.
    stem = r"将抛物线 $y=x^2-2x+3$ 向下平移 $k$ 个单位后与坐标轴仅有两个交点，则 $k=$"
    return "14. " + stem + "\x0cillin.\n\n", stem + r"\fillin."


def test_q14_decoded_form_feed_fillin_is_only_a_layout_difference():
    source, content = _captured_q14_fillin_texts()
    _, locks = lock_visible_math(source, "q14-fillin")
    questions = [{"content": content, "answer_markdown": ""}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["math_locks_restored"] == 3
    assert questions[0]["content"] == content
    match = report["source_matches"][0]
    assert match["source_excerpt"] == source  # Historical evidence is untouched.
    assert source[match["source_start"]:match["source_end"]] == source


@pytest.mark.parametrize("before,after", [
    ("x^2-2x+3", "x^2-2x+4"), ("x^2-2x+3", "x^2+2x+3"),
    ("$k$", "$t$"), ("$k=$", "$k<=$"),
    ("两个交点", "三个交点"), (r"\fillin", "illin"),
])
def test_q14_fillin_equivalence_does_not_excuse_real_changes(before, after):
    source, content = _captured_q14_fillin_texts()
    changed = content.replace(before, after)
    _, locks = lock_visible_math(source, "q14-changed")
    questions = [{"content": changed, "answer_markdown": ""}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert questions[0]["content"] == changed


def test_corrupt_fillin_prefix_cannot_hide_a_different_word():
    source, content = _captured_q14_fillin_texts()
    source = source.replace("\x0cillin", "\x0cillinfoo")
    _, locks = lock_visible_math(source, "q14-prefix")
    questions = [{"content": content}]
    assert reconcile_visible_math(questions, locks, source)["source_review_count"] == 1


def test_answer_source_shared_by_two_results_is_not_advertised_as_unique():
    source = "题 1. 已知 $x=2$。\n\n题 1. 计算 $1+1$。\n\n参考答案：\n\n1. $2$。"
    _, locks = lock_visible_math(source, "shared-answer")
    questions = [{"content": "已知 $x=2$。", "answer_markdown": "$2$。"},
                 {"content": "计算 $1+1$。", "answer_markdown": "$2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert len(report["source_matches"]) == 2
    assert all(match["field"] == "content" for match in report["source_matches"])


@pytest.mark.parametrize("score", ["(13分)", "（15分）", "( 17 分 )", "（ 13 分 ）"])
def test_numbered_question_score_is_comparison_metadata_only(score):
    from mathbank.content_locks import _comparison_layout
    content = r"已知 $x=2$，求值。"
    source = "15. " + score + "\n\n" + content
    _, locks = lock_visible_math(source, "score-header")
    questions = [{"content": content}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert len(_comparison_layout(source)) == len(source)
    match = report["source_matches"][0]
    assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"] == source
    assert questions[0]["content"] == content


@pytest.mark.parametrize("footer", [
    "高三数学 第1页(共4页)", "高三数学 第 1 页（共 4 页）", "高 三 数 学 第 1 页 ( 共 4 页 )",
    "第1页(共4页)", "第1页/共4页", "**高三数学 第1页(共4页)**",
    "高一数学试题 第1页(共4页)", "初二数学试卷 第 1 页／共 4 页", "数学试卷 第1页（共4页）",
])
def test_independent_exam_footer_is_not_part_of_question_content(footer):
    source = "4. 已知 $x=2$。\n\n" + footer + "\n\n5. 计算 $y=3$。"
    _, locks = lock_visible_math(source, "exam-footer")
    questions = [{"content": "已知 $x=2$。"}, {"content": "计算 $y=3$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert footer in report["source_matches"][0]["source_excerpt"]
    for match in report["source_matches"]:
        assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"]


@pytest.mark.parametrize("subject", ["高三数学", "高三数试", "初二数学试卷", "数学试题", "数学", "數學"])
def test_footer_subjects_are_explicit_not_arbitrary_ocr_replacements(subject):
    from mathbank.content_locks import _page_footer_ranges
    value = subject + r" $\cdot$ 第2页（共4页）"
    assert bool(_page_footer_ranges(value)) == (subject != "數學")


@pytest.mark.parametrize("separator", ["·", "•", "⋅", r"$\cdot$", r"$ \bullet $", r"$$\cdot$$",
                                       r"\(\cdot\)", r"\[\cdot\]", r"\cdot"])
def test_footer_separator_is_excluded_consistently_from_locks_and_formula_comparison(separator):
    from mathbank.content_locks import _formulas, _comparison_layout
    content = r"计算 $2\cdot3$，并求 $x+1$。"
    footer = "高三数试 " + separator + " 第2页（共4页）"
    source = "6. " + content + "\n\n" + footer
    locked, locks = lock_visible_math(source, "footer-separator")
    assert [lock.original for lock in locks] == [r"$2\cdot3$", "$x+1$"]
    assert footer in locked and _formulas(footer) == []
    assert len(_comparison_layout(source)) == len(source)
    questions = [{"content": content}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["math_locks_restored"] == 2
    assert report["source_matches"][0]["source_excerpt"] == source
    assert report["ignored_source_notes"] == ["已按页脚格式排除 1 处页面标记。"]


@pytest.mark.parametrize("line", [
    r"高三数学 $a\cdot b$ 第1页（共4页）",
    r"高三数学 $\cdot+1$ 第1页（共4页）",
    r"计算 高三数学 $\cdot$ 第1页（共4页）",
    r"高三数学 $\cdot$ 第1页（共4页）并说明理由",
    r"高三数值 $\cdot$ 第1页（共4页）",
    r"符号 $\cdot$ 表示乘法。",
    r"高三数学 $\cdotx$ 第1页（共4页）",
])
def test_real_multiplication_unknown_subjects_and_body_text_cannot_be_footer_metadata(line):
    from mathbank.content_locks import _page_footer_ranges, _formulas
    assert _page_footer_ranges(line) == []
    _, locks = lock_visible_math(line, "not-footer")
    assert len(locks) == len(_formulas(line)) == 1


@pytest.mark.parametrize("opening,closing", [(r"\[", r"\]"), ("```", "```"),
                                             (r"\begin{verbatim}", r"\end{verbatim}")])
def test_footer_looking_line_inside_math_or_literal_example_is_not_ignored(opening, closing):
    from mathbank.content_locks import _page_footer_ranges, _comparison_layout
    value = opening + "\n" + r"高三数学 $\cdot$ 第1页（共4页）" + "\n" + closing
    assert _page_footer_ranges(value) == []
    assert _comparison_layout(value) == value
    _, locks = lock_visible_math(value, "quoted-footer")
    assert locks


def test_excluding_footer_separator_cannot_hide_changed_real_multiplication():
    source = r"6. 已知 $a\cdot b=2$，求值。" + "\n" + r"高三数学 $\cdot$ 第1页（共4页）"
    _, locks = lock_visible_math(source, "real-product")
    questions = [{"content": "$ab=2$，求值。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 1


def test_footer_separator_before_an_image_does_not_change_its_proven_position():
    image = "![图](/static/uploads/tmp/hangzhou_figure.png)"
    content = "已知 $x=2$。\n" + image + "\n继续求 $x+1$。"
    source = "14. 已知 $x=2$。\n" + r"高三数学 $\cdot$ 第1页（共4页）" + "\n" + image + "\n继续求 $x+1$。"
    _, locks = lock_visible_math(source, "footer-image")
    questions = [{"content": content}]
    assert reconcile_visible_math(questions, locks, source)["source_review_count"] == 0
    questions = [{"content": "已知 $x=2$。\n继续求 $x+1$。\n" + image}]
    assert reconcile_visible_math(questions, locks, source)["source_review_count"] == 1


@pytest.mark.parametrize("original,returned", [
    ("经过(13分)后测量，结果为 $x=2$。", "经过后测量，结果为 $x=2$。"),
    ("(13分钟)后测量，结果为 $x=2$。", "后测量，结果为 $x=2$。"),
    ("经过13分钟后测量，结果为 $x=2$。", "经过15分钟后测量，结果为 $x=2$。"),
    ("高三数学 第1页(共4页)的图形如下，求 $x$。", "图形如下，求 $x$。"),
    ("已知 $x=2$。\n未知内容待补全", "已知 $x=2$。"),
    ("已知 $x=2$。\n任意前缀 高一数学 第1页(共4页)", "已知 $x=2$。"),
    ("已知 $x=2$。\n高三语文 第1页(共4页)", "已知 $x=2$。"),
])
def test_real_conditions_and_unknown_paragraphs_are_not_score_or_footer_metadata(original, returned):
    source = "15. " + original
    _, locks = lock_visible_math(source, "real-condition")
    questions = [{"content": returned}]
    assert reconcile_visible_math(questions, locks, source)["source_review_count"] == 1


def test_unnumbered_parenthesized_points_cannot_be_dropped_as_metadata():
    source = "(13分)后测量，结果为 $x=2$。"
    _, locks = lock_visible_math(source, "no-number-score")
    questions = [{"content": "后测量，结果为 $x=2$。"}]
    assert reconcile_visible_math(questions, locks, source)["source_review_count"] == 1


@pytest.mark.parametrize("move_image", [False, True])
def test_score_and_footer_normalization_also_preserve_precise_image_positions(move_image):
    image = "![图](/static/uploads/tmp/source_figure.png)"
    stem = "已知 $x=2$。\n" + image + "\n(1) 求 $x+1$。"
    source = "17. (15分)\n" + stem + "\n高三数学 第3页(共4页)\n"
    _, locks = lock_visible_math(source, "score-image")
    returned = stem if not move_image else "已知 $x=2$。\n(1) 求 $x+1$。\n" + image
    questions = [{"content": returned}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == int(move_image)
    if move_image:
        assert any("插图" in reason for reason in questions[0]["source_review"]["reasons"])


@pytest.mark.parametrize("change", ["x=3", "x<2", "x=-2"])
def test_score_metadata_cannot_certify_changed_formula(change):
    source = "16. (15分)\n已知 $x=2$。\n高三数学 第1页(共4页)"
    _, locks = lock_visible_math(source, "score-formula")
    questions = [{"content": "已知 $" + change + "$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert report["math_locks_missing"] == 1


@pytest.mark.parametrize("number,needs_review", [("28", 0), ("29", 1)])
def test_score_and_footer_do_not_distort_numeric_delimiter_comparison(number, needs_review):
    source = "15. (13分)\n共有28组，计算 $x+1$。\n高三数学 第3页(共4页)"
    _, locks = lock_visible_math(source, "score-numeric")
    questions = [{"content": "共有 $" + number + "$ 组，计算 $x+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == needs_review
    if not needs_review:
        assert report["math_locks_missing"] == 0


def _exam_notices():
    return ("注意事项：\n\n"
            "1. 选择题的作答: 每小题选出答案后, 用 2B 铅笔把答题卡上对应题目的答案标号涂黑.\n\n"
            "2. 非选择题的作答: 用黑色签字笔直接答在答题卡上对应的答题区域内.\n\n")


def test_bounded_exam_notices_do_not_create_two_extra_questions():
    source = _exam_notices() + "一、单项选择题\n\n1. 计算加法 $1+1$。\n\n2. 求和 $2+2$。"
    _, locks = lock_visible_math(source, "notice-boundary")
    questions = [{"content": "计算加法 $1+1$。"}, {"content": "求和 $2+2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["unmatched_source"] == []
    assert len([part for part in _source_parts(source, locks) if part.field == "content"]) == 2
    for match in report["source_matches"]:
        assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"]


@pytest.mark.parametrize("heading", ["注意事项：", "考生须知：", "答题须知"])
def test_bounded_administrative_notices_do_not_become_math_questions(heading):
    notices = (
        "1. 本试卷分试题卷和答题卷两部分。满分 150 分，考试时间 120 分钟。\n\n"
        "2. 请用黑色字迹的钢笔或签字笔在答题卡指定的区域（黑色边框）内作答，超出答题区域的作答无效！\n\n"
        "3. 考试结束，只需上交答题卡。\n\n"
    )
    source = heading + "\n\n" + notices + "一、选择题：\n\n1. 计算 $1+1$。"
    _, locks = lock_visible_math(source, "exam-admin")
    questions = [{"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["unmatched_source"] == []
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["ignored_source_notes"] == ["已忽略卷首 3 条考试说明。"]
    assert not any("考试说明" in warning for warning in report["warnings"])
    assert len([part for part in _source_parts(source, locks) if part.field == "content"]) == 1
    for match in report["source_matches"]:
        assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"]


@pytest.mark.parametrize("unknown", [
    "本试卷满分150分，考试时间120分钟，求每分钟应完成的分值。",
    "考试结束，只需上交答题卡，求上交顺序的概率。",
    "请用黑色字迹的签字笔在答题卡指定区域内作答。已知 $x=2$，求值。",
    "本试卷满分 $100+50$ 分，考试时间120分钟。",
    "这里有一段尚未判断含义的普通正文。",
])
def test_admin_keywords_do_not_hide_real_math_or_unknown_notice_items(unknown):
    source = "考生须知：\n1. " + unknown + "\n\n一、选择题\n1. 计算 $1+1$。"
    _, locks = lock_visible_math(source, "unknown-admin")
    questions = [{"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert any(unknown in item["source_excerpt"] for item in report["unmatched_source"])


def test_exam_administration_without_a_bounded_notice_header_remains_source_evidence():
    source = "1. 本试卷满分150分，考试时间120分钟。\n2. 计算 $1+1$。"
    _, locks = lock_visible_math(source, "no-admin-boundary")
    questions = [{"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert any("满分150" in item["source_excerpt"] for item in report["unmatched_source"])
    assert report["ignored_source_notes"] == []


@pytest.mark.parametrize("score,time", [("$150$", "120"), ("$150$", r"\(120\)"),
                                      (r"\[150\]", "$$120$$"), ("$ 150 $", "$120$")])
def test_pure_integer_math_in_administrative_template_is_not_a_question_formula(score, time):
    from mathbank.content_locks import _formulas, _preamble_instruction_ranges
    instruction = f"1. 本试卷满分 {score} 分，考试时间 {time} 分钟。\n\n"
    source = "考生须知：\n\n" + instruction + "一、选择题\n\n1. 计算 $x+1$。\n2. 求值 $y=2$。"
    locked, locks = lock_visible_math(source, "numeric-administration")
    assert [lock.original for lock in locks] == ["$x+1$", "$y=2$"]
    assert [formula.formula for formula in _formulas(source)] == ["$x+1$", "$y=2$"]
    assert instruction in locked
    ranges = _preamble_instruction_ranges(source)
    assert len(ranges) == 1 and source[ranges[0][0]:ranges[0][1]] == instruction
    questions = [{"content": "计算 $x+1$。"}, {"content": "求值 $y=2$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["math_locks_created"] == report["math_locks_restored"] == 2
    assert report["unmatched_source"] == []
    assert report["ignored_source_notes"] == ["已忽略卷首 1 条考试说明。"]
    for match in report["source_matches"]:
        assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"]


@pytest.mark.parametrize("score,suffix", [
    ("$100+50$", ""), ("$N$", ""), ("$0$", ""), ("$-150$", ""), ("$150.0$", ""),
    ("$150$", "求每分钟应完成的分值。"), ("$150$", "另有未知正文需要核对。"),
    ("$150$", "已知 $x=2$，求值。"),
])
def test_nonliteral_or_extended_admin_items_keep_their_real_formula_locks(score, suffix):
    from mathbank.content_locks import _preamble_instruction_ranges
    statement = f"本试卷满分 {score} 分，考试时间120分钟。" + suffix
    source = "考生须知：\n1. " + statement + "\n\n一、选择题\n1. 计算 $1+1$。"
    _, locks = lock_visible_math(source, "not-pure-administration")
    assert _preamble_instruction_ranges(source) == []
    assert score in [lock.original for lock in locks]
    questions = [{"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert any(statement in item["source_excerpt"] for item in report["unmatched_source"])
    assert report["math_locks_missing"] >= 1
    assert report["ignored_source_notes"] == []


def test_pure_integer_admin_line_without_both_document_boundaries_keeps_locks():
    source = "1. 本试卷满分 $150$ 分，考试时间 $120$ 分钟。"
    _, locks = lock_visible_math(source, "unbounded-admin")
    assert [lock.original for lock in locks] == ["$150$", "$120$"]


def test_administrative_integer_projection_is_independent_of_formula_collector(monkeypatch):
    from mathbank import content_locks
    monkeypatch.setattr(content_locks, "_formulas", lambda *a, **k: pytest.fail("metadata matcher must not recurse into formula collection"))
    source = "考生须知：\n1. 本试卷满分 $150$ 分，考试时间 \\(120\\) 分钟。\n一、选择题\n1. 求值。"
    assert content_locks._preamble_instruction_ranges(source)


def test_ignored_source_notes_do_not_clear_a_real_formula_change():
    source = ("考生须知：\n1. 考试结束，只需上交答题卡。\n一、选择题\n"
              r"1. 已知 $x=2$，求值。" + "\n" + r"高三数试 $\cdot$ 第1页（共4页）")
    _, locks = lock_visible_math(source, "notes-with-real-change")
    questions = [{"content": "已知 $x=3$，求值。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 1
    assert report["ignored_source_notes"] == ["已忽略卷首 1 条考试说明。", "已按页脚格式排除 1 处页面标记。"]


@pytest.mark.parametrize("prefix", ["", "注意事项：\n\n"])
def test_plain_numbered_math_without_a_question_section_is_never_removed(prefix):
    source = prefix + "1. 计算 $1+1$。\n\n2. 计算 $2+2$。"
    _, locks = lock_visible_math(source, "plain-questions")
    questions = [{"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert len([part for part in _source_parts(source, locks) if part.field == "content"]) == 2
    assert any("2+2" in item["source_excerpt"] for item in report["unmatched_source"])


def test_unknown_numbered_paragraph_in_notice_range_remains_reviewable():
    source = (_exam_notices() + "3. 某校共有28个队伍参加比赛，求所需场次。\n\n"
              "一、单项选择题\n\n1. 计算 $1+1$。")
    _, locks = lock_visible_math(source, "unknown-notice")
    questions = [{"content": "计算 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert len(report["unmatched_source"]) == 1
    assert "28个队伍" in report["unmatched_source"][0]["source_excerpt"]


def test_notice_paragraph_with_real_formula_remains_source_evidence():
    source = ("注意事项：\n1. 选择题的作答: 答题卡上的示例为 $x=2$。\n\n"
              "一、单项选择题\n1. 计算加法 $1+1$。")
    _, locks = lock_visible_math(source, "notice-formula")
    questions = [{"content": "计算加法 $1+1$。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert any("x=2" in item["source_excerpt"] for item in report["unmatched_source"])
    assert report["math_locks_missing"] == 1


def test_notice_heading_inside_an_existing_section_cannot_hide_questions():
    source = "一、单项选择题\n\n" + _exam_notices() + "二、填空题\n3. 计算 $1+1$。"
    _, locks = lock_visible_math(source, "notice-in-body")
    parts = [part for part in _source_parts(source, locks) if part.field == "content"]
    assert [part.number for part in parts] == [1, 2, 3]


@pytest.mark.skipif(not os.getenv("MATHBANK_PDF_SOURCE_TASK"), reason="optional local captured PDF import task")
def test_captured_task_replay_uses_exact_page_evidence_without_any_model():
    from mathbank.pdf_figures import clear_resolved_figure_placeholders
    from mathbank.pdf_layout import apply_figure_anchors

    task = json.loads(Path(os.environ["MATHBANK_PDF_SOURCE_TASK"]).read_text())
    pages = {
        issue["page_index"]: re.split(r"<!-- MATHBANK_PDF_PAGE:\d+ -->\n?", issue["source_excerpt"], maxsplit=1)[-1].split("\n\n![未归位配图]")[0]
        for issue in task["diagnostics"]["unmatched_source"] if "page_index" in issue
    }
    chunks = []
    for page in task["pdf_layout"]["pages"]:
        assert page["page_index"] in pages, "Captured task must contain source evidence for every selected page"
        figures = [figure for figure in page["figures"] if figure.get("attached")]
        anchored = apply_figure_anchors(pages[page["page_index"]], figures)
        chunks.append(clear_resolved_figure_placeholders(anchored["markdown"], figures))
    source = "\n\n".join(chunks)
    _, locks = lock_visible_math(source, "captured")
    questions = copy.deepcopy(task["data"])
    for question in questions:
        question.pop("source_review", None)
    report = reconcile_visible_math(questions, locks, source)
    assert len([part for part in _source_parts(source, locks) if part.field == "content"]) == 18
    assert report["source_review_count"] < 18
    assert all("source_review" not in questions[index] for index in (0, 1, 2, 3, 4, 9, 10, 11, 12))
    # Unreliable native extraction / changed formulas still need review.
    assert "source_review" in questions[6]
    for match in report["source_matches"]:
        assert source[match["source_start"]:match["source_end"]] == match["source_excerpt"]


@pytest.mark.skipif(not os.getenv("MATHBANK_PDF_SPLIT_CAPTURE"), reason="optional local captured PDF splitter input/output")
def test_full_xuejun_split_capture_keeps_only_actual_model_formula_error():
    from mathbank.content_locks import ContentLock

    capture = Path(os.environ["MATHBANK_PDF_SPLIT_CAPTURE"])
    locked = (capture / "split-input.md").read_text()
    tags = re.compile(r'<mathbank-math id="([^"]+)">(.*?)</mathbank-math>', re.DOTALL)
    locks = [ContentLock(match[1], match[2]) for match in tags.finditer(locked)]
    source = tags.sub(lambda match: match[2], locked)
    questions = json.loads((capture / "split-output.json").read_text())
    report = reconcile_visible_math(questions, locks, source)
    assert len(questions) == len([match for match in report["source_matches"] if match["field"] == "content"]) == 18
    assert [index + 1 for index, question in enumerate(questions) if question.get("source_review", {}).get("required")] == [14]
    assert not report["unmatched_source"]
    assert any("其他位置" in reason for reason in questions[13]["source_review"]["reasons"])


@pytest.mark.parametrize(("plain", "wrapped"), [
    ("28", "$28$"), ("28", r"\(28\)"), ("28", "$$28$$"),
    ("-3", "$-3$"), ("+3", "$+3$"), ("1.5", "$1.5$"),
    ("-1.5%", r"$-1.5\%$"), ("20%", r"$20\%$"),
    ("-3", "-$3$"), ("20%", "$20$%"), ("20%", r"$20$\%"),
])
def test_numeric_delimiters_are_local_presentation_in_both_directions(plain, wrapped):
    for original, returned in [(plain, wrapped), (wrapped, plain)]:
        source = f"13. 记录的数据为 {original}，请按原数值填写。"
        _, locks = lock_visible_math(source, "numeric-style")
        questions = [{"content": f"记录的数据为 {returned}，请按原数值填写。"}]
        report = reconcile_visible_math(questions, locks, source)
        assert report["source_review_count"] == 0, report
        assert report["math_locks_created"] == len(locks)
        assert report["math_locks_missing"] == 0
        assert report["math_locks_restored"] == report["math_locks_by_content"] == len(locks)
        assert report["math_locks_by_id"] == 0
        expected = original if locks else returned
        assert questions[0]["content"] == f"记录的数据为 {expected}，请按原数值填写。"
        assert report["source_matches"][0]["source_excerpt"] == source


@pytest.mark.parametrize(("original", "returned"), [
    ("28", "$29$"), ("-3", "$3$"), ("+3", "$3$"),
    ("1.5", "$15$"), ("1.5", "$1.50$"), ("20%", "$20$"),
    ("20", r"$20\%$"), ("28", "$14+14$"),
    ("28", "$2$ $8$"), ("28", "$2$8"), ("28", "2$8$"),
    ("28", "$2$$8$"), ("2 8", "$28$"), ("1.5", "$1$.5"),
    ("−28", "$28$"), ("3⋅5", "$3$ $5$"), ("3·5", "$3$ $5$"),
    ("2_8", "$2$ $8$"), ("2^8", "$2$ $8$"), ("3′5", "$3$ $5$"),
    ("–28", "$28$"), ("3*5", "$3$ $5$"), ("3'5", "$3$ $5$"),
])
def test_numeric_comparison_never_certifies_changed_values_signs_or_grouping(original, returned):
    source = f"13. 记录的数据为 {original}，请按原数值填写。"
    _, locks = lock_visible_math(source, "numeric-changed")
    questions = [{"content": f"记录的数据为 {returned}，请按原数值填写。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert questions[0]["content"] == f"记录的数据为 {returned}，请按原数值填写。"


def test_numbers_must_stay_in_their_own_prose_positions():
    source = "13. 甲组有28人，乙组有29人，请比较。"
    _, locks = lock_visible_math(source, "numeric-swap")
    questions = [{"content": "甲组有$29$人，乙组有$28$人，请比较。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1


def test_numeric_delimiter_changes_do_not_shift_other_math_slots_or_replacement_offsets():
    source = r"13. 有 $28$ 场比赛，已知 $x=2$ 和 $\dfrac{x}{2}$，求值。"
    _, locks = lock_visible_math(source, "mixed-numeric")
    questions = [{"content": r"有 28 场比赛，已知 $x=2$ 和 $\frac{ x }{ 2 }$，求值。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["math_locks_restored"] == report["math_locks_by_content"] == 3
    assert questions[0]["content"] == source[4:]


def test_new_numeric_math_does_not_invent_source_locks_or_mask_changed_formula():
    source = "13. 有28场比赛，已知 $x=2$，求值。"
    _, locks = lock_visible_math(source, "new-numeric")
    questions = [{"content": f"有$28$场比赛，已知 [[{locks[0].lock_id}]]，求值。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 0
    assert report["math_locks_created"] == report["math_locks_restored"] == report["math_locks_by_id"] == 1
    questions = [{"content": "有$28$场比赛，已知 $x=3$，求值。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 1
    assert "$x=3$" in questions[0]["content"]


def test_same_numeric_value_with_foreign_id_still_requires_review():
    source = "1. 甲组共有28人。\n\n2. 乙组共有$28$人。"
    _, locks = lock_visible_math(source, "foreign-numeric")
    questions = [{"content": f"甲组共有[[{locks[0].lock_id}]]人。"}, {"content": "乙组共有28人。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == 1
    assert "其他来源" in questions[0]["source_review"]["reasons"][0]
    assert "source_review" not in questions[1]
    assert report["math_locks_restored"] == report["math_locks_by_content"] == 1


def test_numeric_projection_does_not_hide_duplicate_ids():
    source = "1. 甲组有28人，乙组有$29$人，丙组有$29$人。"
    _, locks = lock_visible_math(source, "duplicate-numeric")
    questions = [{"content": f"甲组有$28$人，乙组有[[{locks[0].lock_id}]]人，丙组有[[{locks[0].lock_id}]]人。"}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_duplicated"] == 1
    assert any("重复" in reason for reason in questions[0]["source_review"]["reasons"])
    assert "MBM_" not in questions[0]["content"]


@pytest.mark.parametrize(("value", "image_before_number", "expected_review"), [
    ("28", False, False), ("29", False, True), ("28", True, True),
])
def test_numeric_delimiter_equivalence_preserves_real_image_position_checks(value, image_before_number, expected_review):
    image = "![](/static/uploads/tmp/numeric_figure.png)"
    source = f"13. 共有28场比赛，{image}请根据图示说明。"
    _, locks = lock_visible_math(source, "numeric-image")
    body = f"共有{image}${value}$场比赛，请根据图示说明。" if image_before_number else f"共有${value}$场比赛，{image}请根据图示说明。"
    questions = [{"content": body}]
    report = reconcile_visible_math(questions, locks, source)
    assert bool(report["source_review_count"]) == expected_review
    if image_before_number:
        assert any("插图" in reason for reason in questions[0]["source_review"]["reasons"])


def test_numeric_source_lock_without_provenance_is_not_certified():
    source = "13. 有$28$场比赛，请计数。"
    questions = [{"content": "有28场比赛，请计数。"}]
    report = reconcile_visible_math(questions, [], source)
    assert report["source_review_count"] == 1
    assert report["math_locks_restored"] == 0


@pytest.mark.parametrize("punctuation", ["。", "."])
def test_numeric_answer_loses_only_delimiters_without_losing_source_provenance(punctuation):
    source = "13. 求值。\n【答案】$28$" + punctuation
    _, locks = lock_visible_math(source, "numeric-answer")
    questions = [{"content": "求值。", "answer_markdown": "28" + punctuation}]
    report = reconcile_visible_math(questions, locks, source)
    assert report["source_review_count"] == report["math_locks_missing"] == 0
    assert report["math_locks_created"] == report["math_locks_by_content"] == 1
    assert questions[0]["answer_markdown"] == "$28$" + punctuation


@pytest.mark.skipif(not os.getenv("MATHBANK_NUMERIC_SOURCE_TASK"), reason="optional captured Q13 numeric-delimiter task")
def test_captured_question_13_only_adds_numeric_math_delimiters():
    task = json.loads(Path(os.environ["MATHBANK_NUMERIC_SOURCE_TASK"]).read_text())
    question = copy.deepcopy(task["data"][12])
    source = question.pop("source_review")["source_excerpt"]
    assert "28" in source and "$28$" in question["content"]
    _, locks = lock_visible_math(source, "captured-q13")
    assert not locks
    report = reconcile_visible_math([question], locks, source)
    assert report["source_review_count"] == report["math_locks_created"] == report["math_locks_missing"] == 0
    assert not report["unmatched_source"]
    assert question["content"] == task["data"][12]["content"]
