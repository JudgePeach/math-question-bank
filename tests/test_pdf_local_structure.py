"""Offline integrity checks; no model calls, database or production main import."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from mathbank.pdf_local_structure import (
    PdfLocalStructureError, prepare_pdf_local_structure,
    build_pdf_local_structure_messages, adopt_pdf_local_structure_response,
)

TREE = {"必修一": {"3. 函数的概念与性质": ["3.1 函数的概念"]}}


def prepare(*texts, numbers=None):
    numbers = numbers or list(range(1, len(texts) + 1))
    source = "\n\n".join(texts)
    pages = [{"page_number": n, "markdown": text, "revision": 1} for n, text in zip(numbers, texts)]
    plan = prepare_pdf_local_structure(source, pages, source_document_sha256="a" * 64)
    assert plan["ready"], plan["fallback_reasons"]
    return plan


def select(plan, needle):
    return next(b["id"] for b in plan["blocks"] if needle in b["text"])


def row(number, stem, answer=(), cells=()):
    return {"id": f"q{number}", "source_number": number, "stem": list(stem), "answer": list(answer),
            "answer_cells": list(cells), "question_type": "detailed_answer", "difficulty": "normal",
            "category_compulsory": "必修一", "category_chapter": "函数的概念与性质", "category_knowledge": ""}


def reply(plan, rows, roles=()):
    return {"input_sha256": plan["input_sha256"], "questions": rows, "roles": list(roles)}


def test_exact_slices_atomic_structures_stable_identity_and_source_changes():
    text = ('1. 求值。\n$$a+\nb$$\n\\begin{choices}\n\\item A\n\\item B\n\\item C\n\\item D\n\\end{choices}\n'
            '```python\nx = "$broken"\n```\n![](/static/uploads/a.png)\n'
            '\\begin{tabular}{cc}\n甲 & $x$ \\\\\n乙 & $y$ \\\\\n\\end{tabular}')
    plan = prepare(text)
    assert "".join(b["text"] for b in plan["blocks"]) == text
    for fragment in ('$$a+\nb$$', '\\begin{choices}', '```python', '\\begin{tabular}'):
        block = next(b for b in plan["blocks"] if fragment in b["text"])
        if fragment == '\\begin{choices}':
            assert '\\end{choices}' in block["text"]
        elif fragment == '```python':
            assert block["text"].count('```') == 2
        elif fragment == '\\begin{tabular}':
            assert '\\end{tabular}' in block["text"]
    assert prepare(text)["input_sha256"] == plan["input_sha256"]
    changed = prepare(text.replace('求值', '计算'))
    assert changed["input_sha256"] != plan["input_sha256"]
    mutated = deepcopy(plan)
    mutated["blocks"][0]["text"] += "注入"
    with pytest.raises(PdfLocalStructureError):
        build_pdf_local_structure_messages(mutated, TREE)


def test_repeated_equal_text_has_distinct_occurrence_ids_and_page_revision_binding():
    plan = prepare('1. $x$\n同文\n同文', '2. $x$\n同文')
    same = [b for b in plan["blocks"] if b["text"].strip() == '同文']
    assert len(same) == 3 and len({b["id"] for b in same}) == 3
    pages = [{"page_number": 1, "markdown": '1. $x$\n同文\n同文', "revision": 2},
             {"page_number": 2, "markdown": '2. $x$\n同文', "revision": 1}]
    assert prepare_pdf_local_structure(plan["source"], pages)["input_sha256"] != plan["input_sha256"]


def test_cross_page_exact_assembly_answer_and_images_are_locally_owned():
    plan = prepare('1.（本小题满分13分）已知 $x=1$。\n', '继续求 $x+x$。\n![](/static/uploads/q.png)\n【答案】$2$。')
    answer = select(plan, '【答案】')
    stem = [b["id"] for b in plan["blocks"] if b["id"] != answer]
    result = adopt_pdf_local_structure_response(reply(plan, [row(1, stem, [answer])]), plan, TREE)
    assert not result["failed_groups"] and not result["unassigned"]
    question = result["questions"][0]
    assert question["content"].startswith('（本小题满分13分）')
    assert '继续求 $x+x$。' in question["content"]
    assert question["answer_markdown"] == '[EXTRACTED_ORIGINAL]$2$。'
    assert '/static/uploads/q.png' in question["referenced_images"]
    assert question["source_review"]["required"] is False
    assert question["source_review"]["status"] == "not_verified"
    assert question["pdf_local_source"]["original_verified"] is False
    assert question["pdf_local_source"]["native_reliable"] is False
    for match in result["source_matches"]:
        b = next(b for b in plan["blocks"] if b["id"] == match["block_id"])
        assert plan["source"][match["source_start"]:match["source_end"]] == b["text"]


def test_noncontiguous_selected_physical_pages_cannot_imply_complete_question():
    plan = prepare('1. 题干', '续文', numbers=[1, 3])
    result = adopt_pdf_local_structure_response(reply(plan, [row(1, [b['id'] for b in plan['blocks']])]), plan, TREE)
    assert result["failed_groups"] == {'q1': 'question_crosses_unselected_page_gap'}


@pytest.mark.parametrize('mutation', ['content', 'image_path', 'answer_markdown', 'reorder', 'duplicate', 'unknown', 'stale'])
def test_model_cannot_supply_text_assets_reorder_duplicate_or_stale_sources(mutation):
    plan = prepare('1. 正文\n下一行')
    data = reply(plan, [row(1, [b['id'] for b in plan['blocks']])])
    if mutation in {'content', 'image_path', 'answer_markdown'}:
        data['questions'][0][mutation] = '模型生成内容'
    elif mutation == 'reorder':
        data['questions'][0]['stem'].reverse()
    elif mutation == 'duplicate':
        data['questions'][0]['stem'] *= 2
    elif mutation == 'unknown':
        data['questions'][0]['stem'].append('p99b1')
    elif mutation == 'stale':
        data['input_sha256'] = '0' * 64
        with pytest.raises(PdfLocalStructureError):
            adopt_pdf_local_structure_response(data, plan, TREE)
        return
    result = adopt_pdf_local_structure_response(data, plan, TREE)
    assert not result['questions'] and result['failed_groups']


def test_one_bad_group_keeps_good_group_and_retry_does_not_send_good_body():
    plan = prepare('1. 甲题\n【答案】甲答\n2. 乙题\n【答案】乙答')
    rows = [row(1, [select(plan, '1. 甲')], [select(plan, '甲答')]),
            row(2, [select(plan, '2. 乙')], [select(plan, '乙答')])]
    rows[1]['difficulty'] = 'wrong'
    result = adopt_pdf_local_structure_response(reply(plan, rows), plan, TREE)
    assert result['question_ids'] == ['q1'] and result['retry_question_ids'] == ['q2']
    messages = build_pdf_local_structure_messages(plan, TREE, retry_question_ids=['q2'], retry_scopes=result['retry_scopes'])
    data = json.loads(messages[1]['content'])
    assert '乙题' in messages[1]['content'] and '甲题' not in messages[1]['content']
    assert data['retry_question_ids'] == ['q2']
    with pytest.raises(PdfLocalStructureError):
        build_pdf_local_structure_messages(plan, TREE, retry_question_ids=['q2'])


def test_unknown_blocks_preserved_and_incomplete_json_not_salvaged():
    plan = prepare('1. 正文\n未知原文不得丢弃')
    data = reply(plan, [row(1, [select(plan, '1. 正文')])])
    result = adopt_pdf_local_structure_response(data, plan, TREE)
    assert result['unassigned'] == [select(plan, '未知原文')]
    assert result['partial']
    with pytest.raises(PdfLocalStructureError):
        adopt_pdf_local_structure_response(json.dumps(data)[:-1], plan, TREE)


def test_answer_table_exact_cells_crosschecks_and_appendix_remains_separate():
    text = ('1. 甲题\n2. 乙题\n参考答案\n'
            '\\begin{tabular}{|l|c|c|}\n\\hline\n题号 & 1 & 2 \\\\\n\\hline\n答案 & D & A \\\\\n\\hline\n\\end{tabular}\n'
            '1. 【答案】D\n2. 【答案】B\n多维细目表\n完整附表')
    plan = prepare(text)
    assert [c['source_number'] for c in plan['answer_cells']] == [1, 2]
    for cell in plan['answer_cells']:
        assert plan['source'][cell['start']:cell['end']] == cell['value']
    heads = [b['id'] for b in plan['blocks'] if b['text'].startswith('1.')]
    second = [b['id'] for b in plan['blocks'] if b['text'].startswith('2.')]
    rows = [row(1, heads[:1], heads[1:], [plan['answer_cells'][0]['id']]),
            row(2, second[:1], second[1:], [plan['answer_cells'][1]['id']])]
    used = set(heads + second)
    role_blocks = [b['id'] for b in plan['blocks'] if b['id'] not in used and b['text'].strip()]
    result = adopt_pdf_local_structure_response(reply(plan, rows, [{'role':'appendix','blocks':role_blocks}]), plan, TREE)
    assert result['question_ids'] == ['q1']
    assert result['failed_groups']['q2'] == 'answer_table_conflict_or_unproved'
    assert '完整附表' not in result['questions'][0]['answer_markdown']


def test_duplicate_question_ids_and_overlapping_ownership_reject_all_affected_groups():
    plan = prepare('1. 甲\n2. 乙')
    rows = [row(1, [select(plan, '1. 甲')]), row(1, [select(plan, '1. 甲')])]
    result = adopt_pdf_local_structure_response(reply(plan, rows), plan, TREE)
    assert not result['questions']


def test_model_cannot_hide_a_numbered_question_under_appendix_role():
    plan = prepare('1. 甲题\n2. 乙题')
    result = adopt_pdf_local_structure_response(reply(plan,[row(1,[select(plan,'1. 甲')])],
        [{'role':'appendix','blocks':[select(plan,'2. 乙')]}]),plan,TREE)
    assert result['unassigned'] == [select(plan,'2. 乙')]
    assert result['partial']


@pytest.mark.parametrize('field', ['category_compulsory','category_chapter','category_knowledge'])
@pytest.mark.parametrize('bad_value', [None, ['invalid'], {'unexpected':'object'}])
def test_classification_type_errors_preserve_question_and_do_not_create_paid_retry_group(field,bad_value):
    plan=prepare('1. 原题 $x=1$。')
    value=row(1,[plan['blocks'][0]['id']]);value[field]=deepcopy(bad_value)
    result=adopt_pdf_local_structure_response(reply(plan,[value]),plan,TREE)
    assert not result['failed_groups'] and not result['retry_question_ids']
    question=result['questions'][0]
    assert question['content']=='原题 $x=1$。'
    assert question[field]==''
    assert question['classification_review']['required'] is True
    assert question['classification_review']['original'][field]==bad_value


@pytest.mark.parametrize('body,required', [
    ('普通公式 $x=1$。',False),
    ('此处[公式待核对]。',True),
    ('图形[插图待补: 图1]。',True),
    ('代码例子`[公式待核对]`。',False),
])
def test_only_specific_source_risks_request_verification(body,required):
    plan=prepare('1. '+body)
    result=adopt_pdf_local_structure_response(reply(plan,[row(1,[b['id'] for b in plan['blocks']])]),plan,TREE)
    question=result['questions'][0]
    assert question['source_review']['required'] is required
    assert question['source_review'].get('verified_by') is None
    assert question['pdf_local_source']['original_verified'] is False
    assert question['pdf_local_source']['native_reliable'] is False


def test_answer_cannot_swallow_the_next_questions_original_answer():
    plan = prepare('1. 甲题\n2. 乙题\n1. 【答案】甲\n2. 【答案】乙')
    heads=[b['id'] for b in plan['blocks']]
    result=adopt_pdf_local_structure_response(reply(plan,[row(1,heads[:1],heads[2:])]),plan,TREE)
    assert result['failed_groups']['q1'] == 'answer_contains_another_numbered_heading'


def test_unbalanced_math_or_changed_page_requires_manual_instead_of_whole_recopy():
    bad = prepare_pdf_local_structure('1. $x', [{'page_number':1,'markdown':'1. $x'}])
    assert not bad['ready'] and 'unmatched_math_delimiter' in bad['fallback_reasons']
    bad = prepare_pdf_local_structure('1. 已修改', [{'page_number':1,'markdown':'1. 原文'}])
    assert not bad['ready'] and bad['fallback_reasons'] == ['assembled_source_does_not_match_pages']


def test_balanced_nested_tabular_is_one_atomic_object_and_does_not_block_questions():
    table = (r'\begin{tabular}{cc}题号 & \begin{tabular}{c}数学\\抽象\end{tabular} \\'
             r'1 & \begin{tabular}{c}数据\\分析\end{tabular}\end{tabular}')
    plan = prepare('1. 求值。\n'+table+'\n2. 正常后题。')
    containing = [block for block in plan['blocks'] if r'\begin{tabular}' in block['text']]
    assert len(containing) == 1 and containing[0]['text'] == table+'\n'
    assert not containing[0]['blocked'] and not plan['blocked_scopes']
    result = adopt_pdf_local_structure_response(reply(plan, [row(1,[plan['blocks'][0]['id'],containing[0]['id']])]),plan,TREE)
    assert len(result['questions']) == 1 and table in result['questions'][0]['content']


def test_empty_options_quarantine_only_affected_question_without_inventing_values():
    damaged = '2. 损坏题。\n\\begin{choices}\n\\item A\n\\item \\quad\n\\item C\n\\item D\n\\end{choices}\n'
    plan = prepare('1. 完整前题。\n'+damaged+'3. 完整后题。')
    assert plan['source'] == '1. 完整前题。\n'+damaged+'3. 完整后题。'
    assert plan['blocked_scopes'][0]['source_numbers'] == [2]
    sent = json.loads(build_pdf_local_structure_messages(plan,TREE)[1]['content'])
    assert all('损坏题' not in block['text'] for block in sent['blocks'])
    first, third = select(plan,'1. 完整前题'), select(plan,'3. 完整后题')
    blocked = next(block['id'] for block in plan['blocks'] if block['blocked'])
    result = adopt_pdf_local_structure_response(reply(plan,[row(1,[first]),row(2,[blocked]),row(3,[third])]),plan,TREE)
    assert result['question_ids'] == ['q1','q3']
    assert result['failed_groups']['q2'] == 'question_has_blocked_source'
    assert 'q2' not in result['retry_question_ids']
    assert result['partial'] and result['diagnostics']['blocked_scope_count'] == 1


def test_unclosed_choice_continuation_is_preserved_as_blocked_and_not_attached_to_next_question():
    plan = prepare('1. 损坏题。\n\\begin{choices}\n\\item A\n',
                   '上页尚未结束的选项内容\n2. 完整新题。')
    assert plan['blocked_scopes'][0]['page_numbers'] == [1,2]
    assert plan['blocked_scopes'][0]['source_numbers'] == [1]
    sent = json.loads(build_pdf_local_structure_messages(plan,TREE)[1]['content'])
    assert all('上页尚未结束' not in block['text'] for block in sent['blocks'])
    second = select(plan,'2. 完整新题')
    result = adopt_pdf_local_structure_response(reply(plan,[row(2,[second])]),plan,TREE)
    assert result['question_ids'] == ['q2'] and result['questions'][0]['content'] == '完整新题。'
    assert '上页尚未结束的选项内容' in plan['source']


def test_complete_choices_can_cross_pages_without_quarantining_tail_continuation():
    plan = prepare('1. 完整跨页题。\n\\begin{choices}\n\\item A\n\\item ',
                   'B\n\\item C\n\\item D\n\\end{choices}\n2. 后题。')
    assert not plan['blocked_scopes']
    choice = next(block for block in plan['blocks'] if r'\begin{choices}' in block['text'])
    assert choice['page_numbers'] == [1,2] and r'\end{choices}' in choice['text']
    first = select(plan,'1. 完整跨页题')
    result = adopt_pdf_local_structure_response(reply(plan,[row(1,[first,choice['id']])]),plan,TREE)
    assert result['question_ids'] == ['q1'] and re.search(r'\\item\s+B', result['questions'][0]['content'])


def test_blocked_refs_cannot_be_hidden_in_roles_or_bridged_by_a_question_range():
    plan = prepare('1. 前题。\n2. 坏题。\\begin{choices}\\item \\end{choices}\n3. 后题。')
    one, three = select(plan,'1. 前题'), select(plan,'3. 后题')
    blocked = [block['id'] for block in plan['blocks'] if block['blocked']]
    result = adopt_pdf_local_structure_response(reply(plan,[row(1,[one,three]),row(3,[three])],
        [{'role':'appendix','blocks':blocked}]),plan,TREE)
    assert result['question_ids'] == ['q3']
    assert result['failed_groups']['q1'] == 'question_crosses_blocked_source_scope'
    assert not result['retry_question_ids']
    with pytest.raises(PdfLocalStructureError,match='retry_scope'):
        build_pdf_local_structure_messages(plan,TREE,retry_question_ids=['q2'],retry_scopes={'q2':blocked})


def test_blocked_scope_and_page_issue_changes_invalidate_the_plan_binding():
    plan = prepare('1. 坏题。\\begin{choices}\\item \\end{choices}\n2. 好题。')
    for field in ('blocked_scopes','page_issues'):
        changed = deepcopy(plan); changed[field] = []
        with pytest.raises(PdfLocalStructureError):
            build_pdf_local_structure_messages(changed,TREE)


def test_actual_d1_ocr_bad_first_page_does_not_block_sound_questions_and_nested_appendix():
    path = Path(__file__).resolve().parents[1] / '.system_generated/document_tasks/d1b3aadf-be85-4a04-bba7-416d910843d7.json'
    if not path.exists():
        pytest.skip('Private diagnostic source is intentionally not distributed')
    original = path.read_bytes()
    task = json.loads(original); pages = task['pdf_source_pages']
    from mathbank.pdf_inspector_helper import merge_pdf_page_texts
    from mathbank.pdf_local_structure import _number, _known_notice
    source = re.sub(r'<!-- MATHBANK_PDF_PAGE:\d+ -->','',merge_pdf_page_texts([page['markdown'] for page in pages]))
    plan = prepare_pdf_local_structure(source,pages,source_document_sha256=task['document_input']['sha256'])
    assert plan['ready'] and plan['schema'].endswith('.v2')
    assert ''.join(block['text'] for block in plan['blocks']) == source
    assert {issue['page_number'] for issue in plan['page_issues'] if issue['blocked']} == {1}
    assert {n for scope in plan['blocked_scopes'] for n in scope['source_numbers']} == set(range(1,7))
    table = next(block for block in plan['blocks'] if block['page_numbers'] == [9] and r'\begin{tabular}' in block['text'])
    assert table['text'].count(r'\begin{tabular}') == 7 and not table['blocked']
    message = json.loads(build_pdf_local_structure_messages(plan,TREE)[1]['content'])
    assert not any('一些盆栽' in block['text'] for block in message['blocks'])
    blocks = plan['blocks']; positions = {block['id']:index for index,block in enumerate(blocks)}
    stems = [(int(_number(block['text'])),index) for index,block in enumerate(blocks)
             if not block['blocked'] and 2 <= block['page_numbers'][0] <= 4 and _number(block['text']) in range(7,20)]
    answers = [(int(_number(block['text'])),index) for index,block in enumerate(blocks)
               if not block['blocked'] and 5 <= block['page_numbers'][0] <= 8 and _number(block['text']) in range(7,20)]
    assert [number for number,_ in stems] == [number for number,_ in answers] == list(range(7,20))
    def content_blocks(left,right):
        return [block['id'] for block in blocks[left:right] if not block['blocked'] and block['text'].strip()
                and not re.search(r'数\s*学\s*(?:参考答案)?\s*第\s*\d+\s*页',block['text'])
                and not re.match(r'\s*(?:\*\*)?[一二三四五六七八九十]+[、.．]',block['text'])]
    rows = []
    for index,(number,left) in enumerate(stems):
        right = stems[index+1][1] if index+1<len(stems) else next(i for i,b in enumerate(blocks) if b['page_numbers'][0]>=5)
        answer_left=answers[index][1]
        answer_right=answers[index+1][1] if index+1<len(answers) else next(i for i,b in enumerate(blocks) if b['page_numbers'][0]>=9)
        rows.append(row(number,content_blocks(left,right),content_blocks(answer_left,answer_right)))
    used={value for item in rows for field in ('stem','answer') for value in item[field]}
    roles=[{'role':'notice' if _known_notice(block['text']) else 'unknown','blocks':[block['id']]}
           for block in blocks if block['text'].strip() and not block['blocked'] and block['id'] not in used]
    result=adopt_pdf_local_structure_response(reply(plan,rows,roles),plan,TREE)
    assert result['question_ids'] == [f'q{number}' for number in range(7,20)], result['failed_groups']
    assert len(result['questions']) == 13 and all(q['pdf_local_source']['original_verified'] is False for q in result['questions'])
    for match in result['source_matches']:
        assert not blocks[positions[match['block_id']]]['blocked']
        assert source[match['source_start']:match['source_end']] == blocks[positions[match['block_id']]]['text']
    assert path.read_bytes() == original


def test_real_retained_nine_page_ocr_replay_is_exact_without_model_or_database():
    root = Path(__file__).resolve().parents[1] / '.system_generated/pdf-classification-20261018'
    if not (root / 'responses/03/response.json').exists():
        pytest.skip('Local diagnostic OCR evidence is intentionally not distributed')
    # Physical-page mapping was independently verified from captured image hashes.
    call_ids = [3, 4, 2, 1, 10, 6, 7, 8, 9]
    texts = [json.loads(json.loads((root / f'responses/{n:02}/response.json').read_text())['choices'][0]['message']['content'])['markdown']
             for n in call_ids]
    plan = prepare(*texts)
    assert ''.join(b['text'] for b in plan['blocks']) == '\n\n'.join(texts)
    assert len(plan['pages']) == 9
    # Replay a boundary-only reply, keeping all source roles and source answers.
    question_heads, answer_heads = [], []
    first_section = plan['source'].index('一、选择题')
    for b in plan['blocks']:
        match = re.match(r'\s*(\d{1,2})[.．、]', b['text'])
        if not match:
            continue
        number = int(match[1])
        if 1 <= number <= 19:
            if b['start'] > first_section and b['page_numbers'][0] <= 4:
                question_heads.append((number,b))
            elif b['page_numbers'][0] >= 5:
                answer_heads.append((number,b))
    assert [n for n,b in question_heads] == list(range(1,20))
    assert [n for n,b in answer_heads] == list(range(1,20))
    roles = []; rows = []; used = set()
    appendix_start = next(b['start'] for b in plan['blocks'] if b['page_numbers'][0] == 9)
    for index, (number, head) in enumerate(question_heads):
        stem_end = question_heads[index+1][1]['start'] if index < 18 else next(b['start'] for b in plan['blocks'] if b['page_numbers'][0] == 5)
        ahead = answer_heads[index][1]
        answer_end = answer_heads[index+1][1]['start'] if index < 18 else appendix_start
        stem = [b['id'] for b in plan['blocks'] if head['start'] <= b['start'] < stem_end]
        answer = [b['id'] for b in plan['blocks'] if ahead['start'] <= b['start'] < answer_end]
        # Known section headings and footers are retained roles, not question text.
        excluded = {b['id'] for b in plan['blocks'] if re.match(r'\s*(?:[一二三四]、|数\s*学\s*(?:参考答案)?\s*第)',b['text'])}
        stem = [v for v in stem if v not in excluded]
        answer = [v for v in answer if v not in excluded]
        used.update(stem+answer)
        rows.append(row(number,stem,answer))
    remaining=[b['id'] for b in plan['blocks'] if b['id'] not in used and b['text'].strip()]
    notices=[b['id'] for b in plan['blocks'] if b['id'] in remaining and b['start'] < first_section
             and re.match(r'\s*[123][.]',b['text'])]
    roles.append({'role':'notice','blocks':notices})
    roles.append({'role':'shared_context','blocks':[v for v in remaining if v not in notices]})
    result = adopt_pdf_local_structure_response(reply(plan,rows,roles),plan,TREE)
    assert result['question_ids'] == [f'q{i}' for i in range(1,20)], result['failed_groups']
    assert not result['unassigned']
    assert len(result['questions']) == 19
    assert '多维细目表' not in result['questions'][-1]['answer_markdown']
    assert all(q['pdf_local_source']['native_reliable'] is False for q in result['questions'])
