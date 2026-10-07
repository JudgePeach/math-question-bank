"""Physical Word text-box paragraphs must remain separate source blocks."""
from io import BytesIO
from zipfile import ZipFile

from mathbank.docx_helper import extract_docx_markdown
from mathbank.content_locks import lock_visible_math, _source_parts


def docx(texts):
    paragraphs = "".join('<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>' for text in texts)
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body><w:p><w:r><w:pict><w:txbxContent>' + paragraphs +
           '</w:txbxContent></w:pict></w:r></w:p></w:body></w:document>')
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return buffer.getvalue()


def test_two_textbox_questions_have_distinct_original_source_ranges(tmp_path):
    result = extract_docx_markdown(docx(['1. 已知 x=2', '2. 计算 y=3']), output_dir=tmp_path)
    assert result['success']
    assert result['markdown'] == '1. 已知 x=2\n\n2. 计算 y=3'
    _, locks = lock_visible_math(result['markdown'], 'textbox')
    questions = [p for p in _source_parts(result['markdown'], locks) if p.field == 'content']
    assert [p.number for p in questions] == [1, 2]


def test_adjacent_numeric_textbox_paragraphs_do_not_become_one_value(tmp_path):
    result = extract_docx_markdown(docx(['1. 条件的值是2', '3是另一个条件']), output_dir=tmp_path)
    assert result['success']
    assert '值是23' not in result['markdown']
    assert '值是2\n\n3是' in result['markdown']
