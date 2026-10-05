"""Mixed image placement: anchors, persistence, and both real export formats."""
import json
import os
import shutil
import sqlite3
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from docx import Document
from sqlalchemy import create_engine

from mathbank.image_layout import split_image_anchors, normalize_image_layouts
from mathbank.paper_helper import build_latex_document, compile_tex_to_pdf
from mathbank.word_export_helper import build_word_document
from mathbank.database import Base, Question
from mathbank import db_migrations

A = '![](/static/uploads/a.png)'
B = '![](/static/uploads/b.png)'
SOURCE = f'前文111\n\n{A}\n\n后文222\n\n{B}'


def question(content=SOURCE, **overrides):
    return dict(id=101, content=content, question_type='detailed_answer',
                image_paths=['/static/uploads/a.png', '/static/uploads/b.png'],
                figure_align='bottom_right', figure_align_custom=True, figure_size='medium',
                image_layouts={'a.png': {'align': 'left', 'size': 'small'}}, **overrides)


@pytest.mark.parametrize('source,tail', [(SOURCE, B), (f'{A}\n后文', ''),
    (f'前文\n{A}\n{B}', f'{A}\n{B}'),
    (rf'\begin{{tabular}}{{c}}{A}\end{{tabular}}'+ '\n'+B, B),
    (rf'\begin{{tabular}}{{c}}{A}', ''),
    (f'题干{A}'+r'\begin{choices}\item 1\end{choices}', '')])
def test_tail_cluster_is_independent_of_earlier_anchors(source, tail):
    assert split_image_anchors(source)[1] == tail


def test_layout_normalization_prunes_removed_images_and_rejects_css():
    assert normalize_image_layouts({'/static/uploads/a.png': {'align': 'left', 'size': 'small'},
                                   'removed.png': {'align': 'right', 'size': 'large'}}, SOURCE) == {
        'a.png': {'align': 'left', 'size': 'small'}}
    with pytest.raises(ValueError):
        normalize_image_layouts({'a.png': {'align': 'left; color:red', 'size': 'small'}}, SOURCE)


def test_v9_migration_backs_up_before_adding_individual_layouts(tmp_path, monkeypatch):
    path = tmp_path/'old.db'
    engine = create_engine(f'sqlite:///{path}')
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql('ALTER TABLE questions DROP COLUMN image_layouts')
            conn.exec_driver_sql('PRAGMA user_version=9')
            conn.exec_driver_sql("INSERT INTO questions (content) VALUES ('旧题')")
        monkeypatch.setattr(db_migrations, 'SCHEMA_SNAPSHOT_DIR', tmp_path/'snapshots')
        result = db_migrations.migrate_database(engine)
        assert result['added_image_layouts'] == 1
        with sqlite3.connect(result['backup']) as conn:
            assert 'image_layouts' not in {r[1] for r in conn.execute('PRAGMA table_info(questions)')}
            assert conn.execute('PRAGMA user_version').fetchone()[0] == 9
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT content, image_layouts FROM questions').one() == ('旧题', '{}')
    finally:
        engine.dispose()


@pytest.mark.parametrize('paper_type', ['exam', 'quiz', 'exam_19'])
def test_mixed_latex_preserves_order_and_independent_size(paper_type):
    tex = build_latex_document('图片测试', '', paper_type, [{'question': question(), 'score': 5}],
                              show_secret=False, show_notice=False)
    assert tex.index('前文111') < tex.index('{a.png}') < tex.index('后文222') < tex.index('{b.png}')
    assert r'\begin{flushleft}' in tex
    assert 'max width=5.0cm,max height=4.2cm' in tex
    assert 'max width=8.0cm' in tex
    assert tex.count('{a.png}') == tex.count('{b.png}') == 1


def _four_image_options():
    return r'选择正确图象。\begin{choices}' + ''.join(
        rf'\item ![](/static/uploads/option_{label}.png)' for label in 'ABCD'
    ) + r'\end{choices}'


@pytest.mark.parametrize('paper_type', ['exam', 'quiz', 'exam_19'])
def test_latex_four_image_options_are_compact_and_ordered(paper_type):
    q = dict(id=6, question_type='single_choice', content=_four_image_options(), image_paths=[])
    tex = build_latex_document('四图选项', '', paper_type, [{'question': q, 'score': 5}],
                               show_secret=False, show_notice=False)
    assert r'\begin{choices}[columns=4,label-pos=left]' in tex
    assert tex.count('max width=3.0cm,max height=3.0cm') == 4
    positions = [tex.index('{option_' + label + '.png}') for label in 'ABCD']
    assert positions == sorted(positions)


@pytest.mark.parametrize('content', [
    _four_image_options().replace(r'\item ![]', r'\item 图像说明 ![]', 1),
    _four_image_options().replace(r'\begin{choices}', r'\begin{choices}[columns=4,label-pos=left]'),
    _four_image_options().replace(r'\item ![](/static/uploads/option_D.png)', r'\item $1$'),
])
def test_compact_image_options_do_not_override_mixed_or_authored_layouts(content):
    from mathbank.paper_helper import clean_content_for_latex
    tex = clean_content_for_latex(content, q_type='single_choice', preserve_image_positions=True)
    assert 'max width=3.0cm,max height=3.0cm' not in tex


@pytest.mark.skipif(os.environ.get('MATHBANK_TEST_TIKZ_NATIVE') != '1', reason='opt-in XeLaTeX')
@pytest.mark.parametrize('paper_type', ['exam', 'quiz', 'exam_19'])
def test_native_pdf_four_image_options_share_one_row(tmp_path, paper_type):
    import pymupdf as fitz
    paths = []
    for label, color in zip('ABCD', ['red', 'green', 'blue', 'black']):
        path = tmp_path / f'option_{label}.png'
        Image.new('RGB', (400, 300), color).save(path)
        paths.append(str(path))
    q = dict(id=6, question_type='single_choice', content=_four_image_options(), image_paths=[])
    tex = build_latex_document('四图选项', '', paper_type, [{'question': q, 'score': 5}],
                               show_secret=False, show_notice=False)
    pdf, diagnostics = compile_tex_to_pdf(tex, paths)
    assert pdf, diagnostics
    with fitz.open(stream=pdf, filetype='pdf') as document:
        images = [image for page in document for image in page.get_image_info()]
        assert len(images) == 4
        boxes = [image['bbox'] for image in images]
        assert max(box[1] for box in boxes) - min(box[1] for box in boxes) < 1
        assert [box[0] for box in boxes] == sorted(box[0] for box in boxes)
        assert all(box[2] - box[0] <= 3 * 72 / 2.54 + 1 for box in boxes)


def test_mixed_word_keeps_positions_and_individual_alignment(tmp_path):
    for name in ('a.png','b.png'):
        Image.new('RGB',(600,300),'white').save(tmp_path/name)
    data, diagnostics = build_word_document('图片测试', '', 'exam', [{'question':question(), 'score':5}],
                                           uploads_dir=tmp_path, show_secret=False, show_notice=False)
    doc = Document(BytesIO(data))
    entries = []
    for p in doc.paragraphs:
        blips = p._p.xpath('.//a:blip')
        if blips:
            entries.append(('image', p.alignment))
        elif '111' in p.text or '222' in p.text:
            entries.append(('text', p.text))
    assert [kind for kind,_ in entries] == ['text','image','text','image']
    assert int(entries[1][1]) == 0  # left
    assert int(entries[3][1]) == 2  # right
    assert len(doc.inline_shapes) == 2
    assert doc.inline_shapes[0].width < doc.inline_shapes[1].width
    assert diagnostics['missing_images'] == 0


def test_api_saves_image_layout_and_rejects_invalid_update(client, tmp_path, monkeypatch):
    import main
    from mathbank.asset_lifecycle import register_asset_store
    from main import LOCAL_TOKEN
    headers={'X-Local-Token':LOCAL_TOKEN}
    # Persistence now validates visible image references independently of the
    # client list. Use real isolated fixtures instead of nonexistent URLs.
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    for name in ('a.png', 'b.png'):
        Image.new('RGB', (4, 4), 'white').save(uploads / name)
    monkeypatch.setattr(main, 'UPLOAD_DIR', str(uploads))
    monkeypatch.setattr(main, 'TMP_UPLOAD_DIR', str(uploads / 'tmp'))
    monkeypatch.setattr(main, 'UPLOAD_DIR_REL', 'static/uploads')
    register_asset_store(uploads, tmp_path / 'retained')
    payload={'content':SOURCE,'question_type':'detailed_answer','difficulty':'medium',
             'image_layouts': json.dumps({'a.png':{'align':'right','size':'large'}})}
    response=client.post('/api/questions',data=payload,headers=headers)
    assert response.status_code == 200, response.text
    item=response.json()['question']
    qid=item['id']
    assert item['image_layouts']['a.png']['size']=='large'
    assert client.get(f'/api/questions/{qid}').json()['image_layouts'] == item['image_layouts']
    bad={**payload,'image_layouts':json.dumps({'a.png':{'align':'floating','size':'large'}})}
    response=client.put(f'/api/questions/{qid}',data=bad,headers=headers)
    assert response.status_code==400
    assert client.get(f'/api/questions/{qid}').json()['image_layouts'] == item['image_layouts']
    legacy={k:v for k,v in payload.items() if k!='image_layouts'}
    response=client.put(f'/api/questions/{qid}',data=legacy,headers=headers)
    assert response.json()['question']['image_layouts'] == item['image_layouts']
    response=client.put(f'/api/questions/{qid}',data={**legacy,'content':'已移除全部插图'},headers=headers)
    assert response.json()['question']['image_layouts'] == {}


def test_browser_helpers_enable_both_mixed_images():
    root=Path(__file__).resolve().parents[1]
    api=(root/'static/js/api.js').read_text()
    shared=api[api.index('window.ImageLayoutTools = {'):api.index('const FigureLayoutState = {')]
    ocr=(root/'static/js/ocr.js').read_text()
    helpers=ocr[ocr.index('function currentEditorFigureLayout()'):ocr.index('window.applyEditorFigureLayoutPreview =')]
    script='global.window={};\n'+shared+'''\n
const EDITOR_FIGURE_SIZE_LABELS={auto:'自动',small:'小',medium:'中',large:'大'};
const EDITOR_FIGURE_ALIGN_LABELS={bottom_right:'下方居右'};
window.FigureLayoutState={snapshot(){return {figure_align:'bottom_right',figure_size:'medium'};}};
global.document={getElementById(){return null;}};
const images=['a.png','b.png'].map(src=>({dataset:{},style:{},parentElement:{style:{}},
 classList:{add(){},remove(){}},setAttribute(){},addEventListener(){}}));
const container={style:{},querySelectorAll(){return images;},appendChild(){}};
'''+helpers+f'\napplyEditorFigureLayoutPreview(container, {json.dumps(SOURCE)});'+'''
if(images[0].dataset.editorImageKey!=='a.png') throw Error('missing individual control');
if(images[1].dataset.editorImageKey!=='') throw Error('missing trailing group control');
if(images[1].style.maxHeight!=='240px') throw Error('trailing group size was ignored');
'''
    result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('anchored_content', [
    '![](/static/uploads/a_1.png)',
    r'\begin{tabular}{cc}![](/static/uploads/a_1.png) & $t$\end{tabular}',
    r'\begin{choices}\item ![](/static/uploads/a_1.png)\item $1$\end{choices}',
])
def test_math_repair_preserves_individual_image_layout_through_editor_and_paper(anchored_content):
    """Exercise the merged formula/image pipeline, including the resolved parameter conflict."""
    from lxml import html

    root = Path(__file__).resolve().parents[1]
    api = (root / 'static/js/api.js').read_text()
    shared = api[api.index('window.ImageLayoutTools = {'):api.index('const FigureLayoutState = {')]
    editor = (root / 'static/js/editor.js').read_text()
    editor_start = editor.index('function transformFillinMacro(clean)')
    editor_end_marker = 'window.parseMarkdownWithMath = parseMarkdownWithMath;'
    editor_end = editor.index(editor_end_marker, editor_start) + len(editor_end_marker)
    paper = (root / 'static/js/paper.js').read_text()
    paper_start = paper.index('function shouldPreserveInlinePaperImages(raw)')
    paper_end = paper.index('// Init on DOMContentLoaded', paper_start)
    source = (r'向量 \mathbf{a}。前文111' + '\n\n' + anchored_content
              + '\n\n后文222 $x^2 +\ny^2 = 1$。\n\n![](/static/uploads/b_2.png)')
    layouts = {'a_1.png': {'align': 'left', 'size': 'small'}}
    script = '''
global.window = { MathBankSafe: {
    safeImageUrl: value => value,
    escapeAttribute: value => value,
    sanitizeRichHtml: value => value
} };
''' + shared + editor[editor_start:editor_end] + paper[paper_start:paper_end] + f'''
const source = {json.dumps(source)};
const layouts = {json.dumps(layouts)};
console.log(JSON.stringify({{
    editor: window.parseMarkdownWithMath(source, layouts),
    paper: formatQuestionContentHtml(source, 101, 'bottom_right', false, false, 'medium', layouts),
    embedded: formatQuestionContentHtml(source, 101, 'bottom_right', true, false, 'medium', layouts)
}}));
'''
    node = shutil.which('node')
    assert node, 'Node.js is required for the frontend executable regression'
    result = subprocess.run([node, '-e', script], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    for name in ('editor', 'paper'):
        markup = rendered[name]
        tree = html.fragment_fromstring(markup, create_parent='div')
        images = tree.xpath('.//img')
        assert [image.get('src') for image in images] == [
            '/static/uploads/a_1.png', '/static/uploads/b_2.png'
        ], name
        assert 'mb-inline-image-size-small' in images[0].get('class', ''), name
        assert 'mb-inline-image-align-left' in images[0].getparent().get('class', ''), name
        assert markup.index('前文111') < markup.index('a_1.png') < markup.index('后文222') < markup.index('b_2.png'), name
        assert r'$\mathbf{a}$' in markup, name
        assert '$x^2 +\ny^2 = 1$' in markup, name
        assert '@@MATH_PLACEHOLDER_' not in markup, name
        if 'tabular' in anchored_content:
            assert len(tree.xpath('.//td//img')) == 1, name
        if 'choices' in anchored_content:
            assert len(tree.xpath('.//*[contains(@class,"choices-content")]//img')) == 1, name

    embedded = rendered['embedded']
    assert 'a_1.png' in embedded['stemHtml'] and 'b_2.png' not in embedded['stemHtml']
    assert 'b_2.png' in embedded['imgHtml'] and 'a_1.png' not in embedded['imgHtml']
    assert 'mb-inline-image-size-small' in embedded['stemHtml']
    assert 'data-figure-size="medium"' in embedded['imgHtml']


@pytest.mark.skipif(os.environ.get('MATHBANK_TEST_TIKZ_NATIVE')!='1', reason='opt-in XeLaTeX')
def test_native_mixed_tikz_and_table_layout(tmp_path):
    import pymupdf as fitz
    code=r'\begin{tikzpicture}\draw (0,0) rectangle (2,1);\end{tikzpicture}'
    source=rf'前文111\begin{{tabular}}{{|c|c|c|}}\hline 图 & {A} & 说明\\\hline\end{{tabular}}后文222'+'\n\n'+B
    q=question(source,content_tikz_assets=[{'id':'a','image_path':'/static/uploads/a.png','tikz_code':code},
                                       {'id':'b','image_path':'/static/uploads/b.png','tikz_code':code}])
    tex=build_latex_document('混合插图验证', '', 'exam', [{'question':q,'score':5}],show_secret=False,show_notice=False)
    assert tex.count(code)==2
    pdf,diagnostic=compile_tex_to_pdf(tex)
    assert pdf,diagnostic
    with fitz.open(stream=pdf,filetype='pdf') as doc:
        assert sum(len(p.get_images()) for p in doc)==0
        assert sum(len(p.get_drawings()) for p in doc)>0
