
import pymupdf as fitz

from mathbank.pdf_fidelity import clean_empty_fraction_wrappers, enforce_source_fields, protect_choice_grids
from mathbank.pdf_inspector_helper import _has_math_formula_loss
from mathbank.pdf_raster_guard import _clean_local_graph_frame


def test_nested_empty_fraction_unwraps_only_complete_operand():
    original = r'$\tan(\alpha+\beta)=\dfrac{\frac{2+3}{1-2\cdot3}}{}=-1$'
    expected = r'$\tan(\alpha+\beta)=\frac{2+3}{1-2\cdot3}=-1$'
    assert clean_empty_fraction_wrappers(original) == (expected, 1, 0)
    invalid = r'$\frac{x+1}{}$'
    assert clean_empty_fraction_wrappers(invalid) == (invalid, 0, 1)
    fenced = '~~~latex\n' + original + '\n~~~'
    assert clean_empty_fraction_wrappers(fenced) == (fenced, 0, 0)


def test_corrupt_option_table_with_floating_labels_uses_visual_route():
    document = '|1. A. B. C.|一、选择 下列关于|函数|\n|---|---|---|\n|D.|||\n||内容|'
    assert _has_math_formula_loss(document)
    assert not _has_math_formula_loss(document.replace('|D.|||', '|D.|22||'))


def test_original_inline_answer_and_its_distinct_solution_are_preserved():
    original = '4. 已知函数对其定义域内任意的实数满足以下条件。\n\n【答案】 1\n\n【解析】 分段结论不等于答案栏。\n\n【标注】解析法'
    edited = {'content': '已知函数对其定义域内任意的实数满足以下条件。', 'answer_markdown': '【答案】 分段结论'}
    assert enforce_source_fields([edited], original, {'pages': []})['answer_blocks_restored'] == 1
    assert edited['answer_markdown'] == '【答案】 1\n\n【解析】 分段结论不等于答案栏。'
    unrelated = {'content': '设两条直线相交，计算所夹的锐角。', 'answer_markdown': '保留旧值'}
    enforce_source_fields([unrelated], original, {'pages': []})
    assert unrelated['answer_markdown'] == '保留旧值'
    assert enforce_source_fields([edited, dict(edited)], original, {'pages': []})['answer_blocks_restored'] == 0


def test_stroked_table_stays_in_its_actual_option_if_splitter_omits_it():
    stem = '下列关于变量之间的关系符合函数定义的是'
    source = '1. ' + stem + '。\n' + r'\begin{choices}' + '\n' + r'\item 1 \item 2 \item 3 \item 错版表格' + '\n' + r'\end{choices}'
    layout = {'pages': [{'page_index': 0, 'figures': [], 'candidates': [1]}], 'page_texts': [source]}
    with fitz.open() as document:
        page = document.new_page(width=595, height=800)
        page.insert_text((30, 80), '1.', fontsize=12)
        page.insert_text((47, 80), stem, fontname='china-s', fontsize=12)
        page.insert_text((48, 180), 'D.', fontsize=12)
        for row in range(2):
            for col in range(3):
                x, y = 60 + 60 * col, 170 + 23 * row
                page.draw_rect((x, y, x+60, y+23), width=0.5)
        protect_choice_grids(layout, document, crop=lambda page, box, id: '/static/uploads/tmp/x.png',
                             check_cancelled=lambda: None)
    assert '![选项表格](/static/uploads/tmp/x.png)' in layout['page_texts'][0]
    assert layout['pages'][0]['figures'][0]['option'] == 'D'
    cards = [{'content': stem + '\nA.   1\nB.    2\nC.    3\nD.    错版表格'}]
    assert enforce_source_fields(cards, layout['page_texts'][0], layout)['choice_tables_restored'] == 1
    assert 'D.     ![选项表格]' in cards[0]['content'] or (
        cards[0]['content'].splitlines()[-1].startswith('D.') and '/static/uploads/tmp/x.png' in cards[0]['content'])
    assert 'C.    3' in cards[0]['content']


def test_unique_scanned_graph_reframes_only_with_no_neighbor_content_loss():
    graph = {'bbox': (390, 195, 620, 274), 'labels': 5, 'core_bbox': (393, 200, 590, 272)}
    original = (370, 205, 600, 310)
    evidence = {'figures': [graph], 'large_components': 1,
                'text_rows': [(100, 288, 823, 302)], 'significant_components': [graph['core_bbox']]}
    assert _clean_local_graph_frame(evidence, original, allow_relocation=False) == graph
    evidence['significant_components'].append((450, 285, 500, 310))
    assert _clean_local_graph_frame(evidence, original, allow_relocation=False) is None


def test_number_only_page_requires_ink_free_body_outside_owned_footer():
    from mathbank.pdf_blank_pages import is_confirmed_number_only_page

    with fitz.open() as document:
        page = document.new_page(width=595, height=800)
        page.draw_rect((0, 0, 595, 25), color=None, fill=(.91, .95, .99))
        page.insert_text((294, 780), "1", fontsize=12)
        assert is_confirmed_number_only_page(page)
        page.draw_line((25, 460), (65, 460), color=(.95, .95, .95))
        assert not is_confirmed_number_only_page(page)


def test_right_axis_unit_keeps_adjacent_glyph_but_not_prose_below():
    from mathbank.pdf_raster_guard import _right_label_continuation

    core = (360, 260, 542, 354)
    near = [(551, 316, 567, 331)]
    continuation = (568, 317, 582, 331)
    unrelated = (568, 370, 589, 390)
    assert _right_label_continuation(near, [*near, continuation, unrelated], core, 14) == [
        *near, continuation,
    ]
