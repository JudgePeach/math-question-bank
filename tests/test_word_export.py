import io
import shutil
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image

from main import LOCAL_TOKEN
from mathbank import word_export_helper
from mathbank.word_export_helper import MathBoundaryIssue, MathToken, build_word_document, tokenize_mixed_content


def _document_xml(docx_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as archive:
        return archive.read("word/document.xml")


def _package_part(docx_bytes: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as archive:
        return archive.read(name)


def _drawing_extents_inches(docx_bytes: bytes) -> list[tuple[float, float]]:
    root = etree.fromstring(_document_xml(docx_bytes))
    namespaces = {
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    }
    emu_per_inch = float(word_export_helper.Inches(1))
    return [
        (
            int(extent.get("cx")) / emu_per_inch,
            int(extent.get("cy")) / emu_per_inch,
        )
        for extent in root.xpath(
            ".//wp:anchor/wp:extent | .//wp:inline/wp:extent",
            namespaces=namespaces,
        )
    ]


def _sample_questions():
    return [
        {
            "question": {
                "id": 1,
                "question_type": "single_choice",
                "content": (
                    r"已知集合 $A=\{x\mid -1<x<2\}$，$B=\{-2,-1,0,1,2\}$，则 $A\cap B=$" "\n"
                    r"\begin{choices}" "\n"
                    r"\item $\{-2,-1,0\}$" "\n"
                    r"\item $\{-1,0,1\}$" "\n"
                    r"\item $\{0,1,2\}$" "\n"
                    r"\item $\{0,1\}$" "\n"
                    r"\end{choices}"
                ),
                "answer_markdown": r"答案为 $\{-1,0,1\}$。",
                "image_paths": [],
            },
            "score": 5,
        },
        {
            "question": {
                "id": 2,
                "question_type": "fill_in_blank",
                "content": r"若 $f(x)=x^2$，则 $f'(2)=\fillin$。",
                "answer_markdown": r"$4$",
                "image_paths": [],
            },
            "score": 5,
        },
    ]


def test_tokenize_mixed_content_keeps_inline_and_display_math():
    tokens = tokenize_mixed_content(r"已知 $A\cap B$，且 \[x^2+y^2=1\]。")
    math_tokens = [item for item in tokens if isinstance(item, MathToken)]
    assert [item.latex for item in math_tokens] == [r"A\cap B", r"x^2+y^2=1"]
    assert [item.display for item in math_tokens] == [False, True]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_export_preserves_comparison_symbols_and_aliases():
    data, diagnostics = build_word_document("关系符号", "", "quiz", [{
        "question": {
            "question_type": "detailed_answer",
            "content": r"比较 $a\lt b$、$a\gt b$、$A\ltimes B$ 和 $a\gtrsim b$。",
        },
        "score": 5,
    }])
    root = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS}
    assert [
        "".join(equation.xpath(".//m:t/text()", namespaces=ns))
        for equation in root.xpath(".//m:oMath", namespaces=ns)
    ] == ["a<b", "a>b", "A⋉B", "a≳b"]
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0


def test_word_reports_incomplete_alignments_but_keeps_literal_examples():
    alignment = r"\begin{align*}x&=1\\&=2\end{align*}"
    text = (
        f"{alignment}。用法：`{alignment}`\n\n"
        f"~~~latex\n\n{alignment}\n~~~\n\n"
        f"<code>{alignment}</code>\n"
        rf"转义：\{alignment}。残缺：\begin{{align*}}x&=1"
    )
    tokens = tokenize_mixed_content(text)
    assert [token for token in tokens if isinstance(token, MathToken)] == [MathToken(alignment, True)]
    issues = [token for token in tokens if isinstance(token, MathBoundaryIssue)]
    assert [issue.source for issue in issues] == [r"\begin{align*}x&=1"]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_exports_currency_standalone_equations_and_nested_cases():
    answer = (
        r"费用 $5，另有 $x^2+1$ 个单位。单价 \$10，折扣 \textdollar{}2.50。"
        r"数值 $5$ 和乘积 $2 xy+3$。" "\n\n"
        r"\begin{equation}u^2=4\end{equation}" "\n"
        r"\begin{gather*}a=1\\b=2\end{gather*}" "\n"
        r"\begin{alignat*}{2}c&=3&\quad d&=4\\c&=5&\quad d&=6\end{alignat*}" "\n"
        r"\begin {align*}p&=7\\q&=8\end {align*}" "\n"
        r"分段函数 \begin{cases}1,&x>0\\\begin{cases}2,&x<0\\3,&x=0\end{cases},&x\le0\end{cases}。"
    )
    data, diagnostics = build_word_document("公式边界回归", "", "quiz", [{
        "question": {"question_type": "detailed_answer", "content": "样题。", "answer_markdown": answer},
        "score": 5,
    }], include_answers=True)
    xml = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS, "w": word_export_helper.WORD_NS}
    formulas = xml.xpath(".//m:oMath", namespaces=ns)
    text = ["".join(formula.xpath(".//m:t/text()", namespaces=ns)) for formula in formulas]
    assert text[:5] == ["x2+1", "5", "2xy+3", "u2=4", "a=1b=2"]
    assert "".join(text[5].split()) == "c=3d=4c=5d=6"
    assert text[6:] == ["p=7q=8", "1,x>02,x<03,x=0,x≤0"]
    assert formulas[-1].xpath(".//m:d//m:d", namespaces=ns), "Nested cases must remain a nested equation"
    plain = "".join(xml.xpath(".//w:t/text()", namespaces=ns))
    assert "费用 $5，另有 " in plain
    assert "单价 $10，折扣 $2.50" in plain
    assert r"\begin" not in plain and r"\end" not in plain
    assert diagnostics["native_formulas"] == 8
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0


@pytest.mark.parametrize("as_single_docx", [True, False])
@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_export_reports_damaged_boundaries_and_failed_conversions(client, db_session, as_single_docx):
    from mathbank.database import Question

    question = Question(
        content="样题。",
        question_type="detailed_answer",
        answer_markdown=(
            r"残缺的 \(x^2=4" "\n\n"
            r"残缺的 \begin{gather*}x=1" "\n\n"
            r"未知命令 $x+\mathbankUndefinedCommand{2}$。紧随的公式 $y=3$。"
        ),
    )
    db_session.add(question)
    db_session.commit()
    title = "公式失败核验"
    response = client.post("/api/paper/export/word", json={
        "title": title,
        "paper_type": "quiz",
        "questions": [{"id": question.id, "score": 5}],
        "as_single_docx": as_single_docx,
        "include_answers": True,
    }, headers={"X-Local-Token": LOCAL_TOKEN})
    assert response.status_code == 200
    assert response.headers["X-Word-Native-Formulas"] == "1"
    assert response.headers["X-Word-Fallback-Formulas"] == "0"
    assert response.headers["X-Word-Failed-Formulas"] == "3"
    if as_single_docx:
        data = response.content
    else:
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            data = bundle.read(f"{title}_含答案与解析.docx")
    xml = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS, "w": word_export_helper.WORD_NS}
    red = xml.xpath(".//w:r[w:rPr/w:color[@w:val='C00000']]/w:t/text()", namespaces=ns)
    assert len(red) == 3
    assert "缺少闭合定界符" in red[0] and r"\(x^2=4" in red[0]
    assert "公式环境 gather* 未正确闭合" in red[1] and r"\begin{gather*}x=1" in red[1]
    assert red[2] == r"[公式待核对：x+\mathbankUndefinedCommand{2}]"
    assert "".join(xml.xpath(".//m:oMath//m:t/text()", namespaces=ns)) == "y=3"


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_exports_numbered_displays_as_editable_formulas_with_visible_tags():
    source = (
        r"前文 \[\frac{x^2}{2}=4\tag{A}\] 后文。" "\n\n"
        r"$$y^2=9\tag*{补充}$$" "\n\n"
        r"\[\begin{array}{l}u+v=4\tag{6.9.7}\\v=3\\\end{array}\]"
    )
    data, diagnostics = build_word_document("编号公式", "", "quiz", [{
        "question": {"question_type": "detailed_answer", "content": "样题。", "answer_markdown": source},
        "score": 5,
    }], include_answers=True)
    root = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS, "w": word_export_helper.WORD_NS}
    paragraphs = root.xpath(".//w:p[m:oMath]", namespaces=ns)
    assert [
        ("".join(p.xpath("./m:oMath//m:t/text()", namespaces=ns)),
         "".join(p.xpath("./w:r/w:t/text()", namespaces=ns)))
        for p in paragraphs
    ] == [("x22=4", "(A)"), ("y2=9", "补充"), ("u+v=4v=3", "(6.9.7)")]
    assert paragraphs[0].xpath("./m:oMath/m:f/m:num/m:sSup", namespaces=ns)
    for paragraph in paragraphs:
        assert paragraph.xpath("./w:pPr/w:tabs/w:tab/@w:val", namespaces=ns) == ["center", "right"]
    assert "前文(A)后文。" in "".join(root.xpath(".//w:t/text()", namespaces=ns))
    assert not root.xpath(".//w:drawing", namespaces=ns)
    assert diagnostics["native_formulas"] == 3
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_keeps_multiline_tags_on_their_authored_rows():
    source = (
        r"\begin{align*}"
        r"a&=\begin{pmatrix}1&2\\3&4\end{pmatrix}\tag {B}\\"
        r"b&=5\notag\\"
        r"c&=6\tag*{C}"
        r"\end{align*}"
    )
    data, diagnostics = build_word_document("多行编号", "", "quiz", [{
        "question": {"question_type": "detailed_answer", "content": source},
        "score": 5,
    }])
    root = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS, "w": word_export_helper.WORD_NS}
    matrix = root.xpath(".//w:p/m:oMath/m:m", namespaces=ns)[0]
    rows = matrix.xpath("./m:mr", namespaces=ns)
    assert len(rows) == 3
    assert ["".join(row.xpath("./m:e[last()]//m:t/text()", namespaces=ns)).strip() for row in rows] == [
        "(B)", "", "C",
    ]
    assert ["".join(row.xpath("./m:e[2]//m:t/text()", namespaces=ns)) for row in rows] == [
        "=1234", "=5", "=6",
    ]
    assert matrix.xpath("./m:mPr/m:mcs//m:mcJc/@m:val", namespaces=ns) == ["right", "left", "right"]
    assert diagnostics["native_formulas"] == 1
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0


@pytest.mark.skipif(shutil.which("xelatex") is None, reason="XeLaTeX is not installed")
def test_word_uses_fallback_for_tags_with_tex_formatting():
    data, diagnostics = build_word_document("带格式编号", "", "quiz", [{
        "question": {"question_type": "detailed_answer", "content": r"\[x^2=4\tag{\textbf{A}}\]"},
        "score": 5,
    }])
    root = etree.fromstring(_document_xml(data))
    assert root.xpath(".//w:drawing", namespaces={"w": word_export_helper.WORD_NS})
    assert diagnostics["fallback_formulas"] == 1
    assert diagnostics["native_formulas"] == diagnostics["failed_formulas"] == 0


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_answer_exports_bare_alignments_as_editable_rows():
    answer = (
        r"【法一】设 $t=x$，则" "\n"
        r"\begin{align*}" "\n"
        r"Q(t)&=-8t^2+8t+25\\" "\n\n"
        r"&=-8\left(t-\dfrac{1}{2}\right)^2+27\\" "\n"
        r"&\le 27" "\n"
        r"\end{align*}" "\n"
        r"当且仅当 $t=\frac{1}{2}$ 时取等。" "\n\n"
        r"【法二】由 \begin{align}2t&=1\\t&=\dfrac{1}{2}\end{align} 得同一结果。"
    )
    question = {
        "question_type": "detailed_answer",
        "content": "求最大值。",
        "answer_markdown": answer,
    }
    data, diagnostics = build_word_document(
        "答案公式回归", "", "quiz", [{"question": question, "score": 5}], include_answers=True,
    )
    xml = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS, "w": word_export_helper.WORD_NS}
    arrays = xml.xpath(".//w:p/m:oMath/m:m", namespaces=ns)
    rows = [
        [
            ["".join(cell.xpath(".//m:t/text()", namespaces=ns)).strip()
             for cell in row.xpath("./m:e", namespaces=ns)]
            for row in array.xpath("./m:mr", namespaces=ns)
        ]
        for array in arrays
    ]
    assert [len(array_rows) for array_rows in rows] == [3, 2]
    assert rows[0][0][0] == "Q(t)"
    assert rows[0][0][1].startswith("=−8")
    assert rows[0][1][1].endswith("+27")
    assert rows[0][2] == ["", "≤27"]
    assert rows[1][0] == ["2t", "=1"]
    assert len(xml.xpath(".//m:m//m:f", namespaces=ns)) == 2
    for array in arrays:
        assert array.xpath("./m:mPr//m:mcJc/@m:val", namespaces=ns) == ["right", "left"]
        assert "&" not in array.xpath(".//m:t/text()", namespaces=ns)
        assert not array.xpath("../../w:r/w:t", namespaces=ns)
    plain = "".join(xml.xpath(".//w:t/text()", namespaces=ns))
    assert r"\begin{align" not in plain
    assert "当且仅当" in plain and "时取等" in plain and "得同一结果" in plain
    assert diagnostics["native_formulas"] == 4
    assert diagnostics["failed_formulas"] == diagnostics["fallback_formulas"] == 0
    assert question["answer_markdown"] == answer


def test_word_fillin_uses_longer_native_underlined_blank(monkeypatch):
    monkeypatch.setattr(
        word_export_helper.PandocOmmlConverter,
        "convert_many",
        lambda self, formulas: {},
    )
    data, _ = build_word_document(
        "填空线长度测试",
        "",
        "quiz",
        [
            {
                "question": {
                    "id": 1,
                    "question_type": "fill_in_blank",
                    "content": r"若 $x=1$，则结果为 \fillin。",
                    "answer_markdown": "1",
                    "image_paths": [],
                },
                "score": 5,
            }
        ],
    )
    document = etree.fromstring(_document_xml(data))
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    underlined_text = document.xpath(
        ".//w:r[w:rPr/w:u]/w:t/text()",
        namespaces=namespaces,
    )

    assert "\u00a0" * word_export_helper.WORD_FILLIN_BLANK_SPACES in underlined_text
    assert word_export_helper.WORD_FILLIN_BLANK_SPACES == 18


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_export_uses_native_omml_when_pandoc_is_available():
    data, diagnostics = build_word_document(
        "可编辑 Word 测试卷",
        "OMML 公式验证",
        "exam",
        _sample_questions(),
        include_answers=True,
    )
    xml = _document_xml(data)
    root = etree.fromstring(xml)
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    assert root.xpath("count(.//m:oMath)", namespaces=namespaces) >= 8
    math_text = "".join(root.xpath(".//m:oMath//m:t/text()", namespaces=namespaces))
    assert "{0,1,2}" in math_text
    assert "{0,1}" in math_text
    assert root.xpath(
        ".//m:oMath//m:r[m:t='0']/m:rPr/m:sty/@m:val",
        namespaces=namespaces,
    )
    assert set(root.xpath(
        ".//m:oMath//m:r[m:t='0']/m:rPr/m:sty/@m:val",
        namespaces=namespaces,
    )) == {"p"}
    assert set(root.xpath(
        ".//m:oMath//m:r[m:t='x']/m:rPr/m:sty/@m:val",
        namespaces=namespaces,
    )) == {"i"}
    assert set(root.xpath(
        ".//m:oMath//m:r[m:t='x']/w:rPr/w:rFonts/@w:ascii",
        namespaces=namespaces,
    )) == {"Times New Roman"}
    assert root.xpath(
        ".//m:oMath//m:r[m:t='x']/w:rPr/w:i",
        namespaces=namespaces,
    )
    assert root.xpath(
        ".//m:oMath//m:r[m:t='x']/m:rPr/m:nor",
        namespaces=namespaces,
    )
    assert not root.xpath(
        ".//m:oMath//m:r[m:t='0']/w:rPr/w:rFonts",
        namespaces=namespaces,
    )
    assert not root.xpath(
        ".//m:oMath//m:r[m:t='0']/m:rPr/m:nor",
        namespaces=namespaces,
    )
    italic_variable_runs = root.xpath(
        ".//m:oMath//m:r[m:rPr/m:sty[@m:val='i'] and not(m:rPr/m:scr)]",
        namespaces=namespaces,
    )
    italic_variable_text = {"".join(run.xpath("./m:t/text()", namespaces=namespaces)) for run in italic_variable_runs}
    assert {"A", "B", "f", "x"}.issubset(italic_variable_text)
    for run in italic_variable_runs:
        assert run.xpath("./w:rPr/w:rFonts/@w:ascii", namespaces=namespaces) == ["Times New Roman"]
        assert run.xpath("./m:rPr/m:nor", namespaces=namespaces)
    assert diagnostics["native_formulas"] >= 8
    assert diagnostics["failed_formulas"] == 0
    assert "公式待核对" not in xml.decode("utf-8")


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_word_export_roundtrips_the_imported_mathtype_interval():
    from mathbank.docx_helper import extract_docx_markdown
    from mathbank.mtef_helper import decode_mtef_formula

    payload = bytes.fromhex((Path(__file__).parent / "fixtures/mtef/tabbed_interval.hex").read_text())
    formula = decode_mtef_formula(payload).latex
    data, diagnostics = build_word_document("公式导出核验", "", "exam", [{
        "question": {"id": 8, "question_type": "detailed_answer",
                     "content": f"条件为 ${formula}$。", "answer_markdown": f"原条件为 ${formula}$。"},
        "score": 5,
    }], include_answers=True)
    root = etree.fromstring(_document_xml(data))
    ns = {"m": word_export_helper.MATH_NS, "w": word_export_helper.WORD_NS}
    formulas = root.xpath('.//m:oMath', namespaces=ns)
    assert len(formulas) == 2
    for math in formulas:
        fractions = math.xpath('.//m:f', namespaces=ns)
        assert [(
            ''.join(f.xpath('./m:num//m:t/text()', namespaces=ns)),
            ''.join(f.xpath('./m:den//m:t/text()', namespaces=ns)),
        ) for f in fractions] == [('1', '2'), ('3', '4')]
        assert math.xpath('./m:r/m:t/text()', namespaces=ns) == ['≤', 'm', '<']
    assert not root.xpath('.//w:object', namespaces=ns)
    assert diagnostics['native_formulas'] == 2
    assert diagnostics['fallback_formulas'] == diagnostics['failed_formulas'] == 0
    readback = extract_docx_markdown(data)
    assert readback['diagnostics']['omml_converted'] == 2
    assert r"\dfrac{1}{2}" in readback['markdown'] or r"\frac{1}{2}" in readback['markdown']
    assert 'APG_' not in readback['markdown']


def test_word_export_keeps_each_image_option_in_its_labeled_cell(tmp_path):
    names = ['z.png', 'b.png', 'x.png', 'a.png']
    colors = ['red', 'green', 'blue', 'black']
    for name, color in zip(names, colors):
        Image.new('RGB', (400, 300), color).save(tmp_path / name)
    data, diagnostics = build_word_document('图片选项', '', 'exam', [{
        'question': {'id': 6, 'question_type': 'single_choice',
                     'content': '选择正确的图象。\\begin{choices}' + ''.join(
                         f'\\item ![](/static/uploads/{name})' for name in names
                     ) + '\\end{choices}',
                     'image_paths': [f'/static/uploads/{name}' for name in names[::-1]]},
        'score': 5,
    }], uploads_dir=tmp_path)
    assert diagnostics['missing_images'] == 0, diagnostics
    root = etree.fromstring(_document_xml(data))
    ns = {'w': word_export_helper.WORD_NS, 'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    table = root.xpath('.//w:tbl', namespaces=ns)[0]
    cells = table.xpath('./w:tr/w:tc', namespaces=ns)
    assert len(table.xpath('./w:tr', namespaces=ns)) == 1
    assert [''.join(c.xpath('.//w:t/text()', namespaces=ns)) for c in cells] == ['A. ', 'B. ', 'C. ', 'D. ']
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rels = etree.fromstring(archive.read('word/_rels/document.xml.rels'))
        targets = {r.get('Id'): r.get('Target') for r in rels}
        for cell, color in zip(cells, colors):
            label_cells = cell.xpath('./w:tbl/w:tr/w:tc[1]', namespaces=ns)
            assert len(label_cells) == 1
            assert label_cells[0].xpath('./w:tcPr/w:vAlign/@w:val', namespaces=ns) == ['center']
            assert not label_cells[0].xpath('.//w:drawing', namespaces=ns)
            assert len(cell.xpath('./w:tbl/w:tr/w:tc[2]//w:drawing', namespaces=ns)) == 1
            embedded = cell.xpath('.//a:blip/@r:embed', namespaces=ns)
            assert len(embedded) == 1
            with Image.open(io.BytesIO(archive.read('word/' + targets[embedded[0]]))) as image:
                assert image.getpixel((0, 0)) == Image.new('RGB', (1, 1), color).getpixel((0, 0))
    assert len(root.xpath('.//w:drawing', namespaces=ns)) == 4
    assert all(width <= 1.5625 and height <= 2.36 for width, height in _drawing_extents_inches(data))


def test_word_export_contains_choice_grid_and_marks_exam_19_answer_card_omission(monkeypatch):
    sample_omml = (
        b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        b"<m:r><m:t>x</m:t></m:r></m:oMath>"
    )
    monkeypatch.setattr(
        word_export_helper.PandocOmmlConverter,
        "convert_many",
        lambda self, formulas: {formula: sample_omml for formula in formulas},
    )
    data, diagnostics = build_word_document(
        "19题试卷",
        "",
        "exam_19",
        _sample_questions(),
    )
    xml = _document_xml(data).decode("utf-8")
    assert diagnostics["answer_card_omitted"] is True
    assert "不包含答题卡" in "".join(diagnostics["warnings"])
    assert xml.count("<w:tbl") >= 2
    assert "A." in xml and "D." in xml


def test_word_export_preserves_complex_table_and_inline_image_positions(tmp_path):
    image_names = ("fig12.png", "fig3.png", "fig4.png", "fig234.png")
    image_sizes = ((307, 153), (130, 149), (217, 161), (400, 169))
    for name, size in zip(image_names, image_sizes):
        Image.new("RGB", size, "white").save(tmp_path / name, format="PNG")

    content = (
        "【课本回顾】图示如下。\n\n"
        "![图1和图2](/static/uploads/fig12.png)\n\n"
        "【知识探究】比较下面两种思路。\n\n"
        r"\begin{tabular}{|c|c|c|}" "\n"
        r"\hline & 思路一 & 思路二 \\" "\n"
        r"\hline 第一步 & 连接中点并利用相似三角形证明数量关系 & 作平行线并利用全等三角形证明数量关系 \\" "\n"
        r"\hline 第二步 & 利用相似三角形的性质及中位线的性质完成推导 & 利用全等三角形及相似三角形的性质完成推导 \\" "\n"
        "\\hline 第三步 &\n\n"
        "![图3](/static/uploads/fig3.png)\n\n"
        "&\n\n"
        "![图4](/static/uploads/fig4.png)\n\n"
        r"\\ \hline\end{tabular}" "\n\n"
        "【问题解决】继续研究。\n\n"
        "![图II至图IV](/static/uploads/fig234.png)"
    )
    data, diagnostics = build_word_document(
        "复杂表格导出",
        "",
        "exam",
        [{
            "question": {
                "id": 53,
                "question_type": "detailed_answer",
                "content": content,
                "image_paths": [f"/static/uploads/{name}" for name in image_names],
                "figure_align": "right",
            },
            "score": 12,
        }],
        uploads_dir=tmp_path,
    )

    document = etree.fromstring(_document_xml(data))
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    visible_text = "".join(document.xpath(".//w:t/text()", namespaces=namespaces))
    tables = document.xpath(".//w:tbl", namespaces=namespaces)

    assert r"\begin{tabular}" not in visible_text
    assert r"\end{tabular}" not in visible_text
    assert len(tables) == 1
    table = tables[0]
    rows = table.xpath("./w:tr", namespaces=namespaces)
    assert len(rows) == 4
    assert len(rows[-1].xpath("./w:tc[2]//w:drawing", namespaces=namespaces)) == 1
    assert len(rows[-1].xpath("./w:tc[3]//w:drawing", namespaces=namespaces)) == 1
    assert len(document.xpath(".//w:drawing", namespaces=namespaces)) == 4
    assert table.xpath("./w:tblPr/w:tblW/@w:w", namespaces=namespaces) == ["9000"]
    assert table.xpath("./w:tblGrid/w:gridCol/@w:w", namespaces=namespaces) == [
        "1080", "3960", "3960"
    ]
    assert table.xpath("./w:tr[1]/w:trPr/w:tblHeader/@w:val", namespaces=namespaces) == ["true"]
    assert len(table.xpath("./w:tr/w:trPr/w:cantSplit", namespaces=namespaces)) == 4
    assert len(table.xpath("./w:tr/w:tc/w:tcPr/w:vAlign[@w:val='center']", namespaces=namespaces)) == 12
    assert diagnostics["missing_images"] == 0


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
@pytest.mark.parametrize("field", ["content", "answer_markdown"])
def test_word_export_keeps_matrix_entries_in_their_table_cell(field):
    table = (
        r"\begin{tabular}{|c|c|}" "\n"
        r"\hline 项目 & 内容 \\[2pt]" "\n"
        r"\hline 矩阵 & $\begin{pmatrix}1&2\\3&4\end{pmatrix}$ \\" "\n"
        r"\hline 核对 & $x=2$ \\ \hline" "\n"
        r"\end{tabular}"
    )
    question = {"question_type": "detailed_answer", "content": "核对各项。", field: table}
    data, diagnostics = build_word_document(
        "表格内公式", "", "quiz", [{"question": question, "score": 5}],
        include_answers=field == "answer_markdown",
    )
    root = etree.fromstring(_document_xml(data))
    ns = {"w": word_export_helper.WORD_NS, "m": word_export_helper.MATH_NS}
    rows = root.xpath(".//w:tbl/w:tr", namespaces=ns)
    assert len(rows) == 3
    assert [len(row.xpath("./w:tc", namespaces=ns)) for row in rows] == [2, 2, 2]
    matrix_rows = rows[1].xpath("./w:tc[2]//m:oMath//m:m/m:mr", namespaces=ns)
    assert [
        ["".join(cell.xpath(".//m:t/text()", namespaces=ns)) for cell in row.xpath("./m:e", namespaces=ns)]
        for row in matrix_rows
    ] == [["1", "2"], ["3", "4"]]
    assert "".join(rows[2].xpath("./w:tc[2]//m:oMath//m:t/text()", namespaces=ns)) == "x=2"
    plain = "".join(root.xpath(".//w:t/text()", namespaces=ns))
    assert r"\begin{pmatrix}" not in plain and "[2pt]" not in plain
    assert diagnostics["native_formulas"] == 2
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0
    assert not diagnostics["warnings"]


@pytest.mark.parametrize(
    ("figure_align", "figure_size", "expected_width"),
    [
        ("center", None, 2.35),
        ("bottom_right", "small", 1.97),
        ("center", "medium", 3.15),
        ("bottom_right", "large", 4.33),
        ("right", "large", 1.55),
    ],
)
def test_word_export_maps_detached_figure_size_and_preserves_aspect_ratio(
    tmp_path,
    figure_align,
    figure_size,
    expected_width,
):
    image_path = tmp_path / "sized-figure.png"
    Image.new("RGB", (600, 300), "white").save(image_path, format="PNG")
    question = {
        "id": 57,
        "question_type": "detailed_answer",
        "content": "尺寸测试\n\n![插图](/static/uploads/sized-figure.png)",
        "image_paths": ["/static/uploads/sized-figure.png"],
        "figure_align": figure_align,
    }
    if figure_size is not None:
        question["figure_size"] = figure_size

    data, diagnostics = build_word_document(
        "Word 插图尺寸",
        "",
        "exam",
        [{"question": question, "score": 12}],
        uploads_dir=tmp_path,
    )

    extents = _drawing_extents_inches(data)
    assert len(extents) == 1
    width, height = extents[0]
    assert width == pytest.approx(expected_width, abs=0.01)
    assert width / height == pytest.approx(2.0, abs=0.01)
    assert diagnostics["missing_images"] == 0


@pytest.mark.parametrize(
    ("figure_size", "expected_width"),
    [("small", 1.97), ("large", 4.33)],
)
def test_word_export_bottom_left_uses_left_alignment_and_requested_size(
    tmp_path,
    figure_size,
    expected_width,
):
    image_path = tmp_path / "bottom-left.png"
    Image.new("RGB", (600, 300), "white").save(image_path, format="PNG")

    data, diagnostics = build_word_document(
        "Word 下方居左插图",
        "",
        "exam",
        [{
            "question": {
                "id": 61,
                "question_type": "detailed_answer",
                "content": "居左尺寸测试\n\n![插图](/static/uploads/bottom-left.png)",
                "image_paths": ["/static/uploads/bottom-left.png"],
                "figure_align": "bottom_left",
                "figure_size": figure_size,
            },
            "score": 12,
        }],
        uploads_dir=tmp_path,
    )

    width, height = _drawing_extents_inches(data)[0]
    assert width == pytest.approx(expected_width, abs=0.01)
    assert width / height == pytest.approx(2.0, abs=0.01)

    root = etree.fromstring(_document_xml(data))
    alignments = root.xpath(
        ".//w:p[.//w:drawing]/w:pPr/w:jc/@w:val",
        namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"},
    )
    assert alignments == ["left"]
    assert diagnostics["missing_images"] == 0


@pytest.mark.parametrize(
    ("figure_align", "figure_size", "expected_width"),
    [
        ("center", "auto", 1.85),
        ("right", "auto", 1.85),
        ("right", "large", 4.33),
    ],
)
def test_word_export_sizes_multi_figures_after_right_to_center_fallback(
    tmp_path,
    figure_align,
    figure_size,
    expected_width,
):
    image_names = ("multi-a.png", "multi-b.png")
    for name in image_names:
        Image.new("RGB", (400, 200), "white").save(tmp_path / name, format="PNG")
    content = "多图测试\n\n" + "\n\n".join(
        f"![插图](/static/uploads/{name})" for name in image_names
    )

    data, diagnostics = build_word_document(
        "Word 多图自动尺寸",
        "",
        "exam",
        [{
            "question": {
                "id": 58,
                "question_type": "detailed_answer",
                "content": content,
                "image_paths": [f"/static/uploads/{name}" for name in image_names],
                "figure_align": figure_align,
                "figure_size": figure_size,
            },
            "score": 12,
        }],
        uploads_dir=tmp_path,
    )

    extents = _drawing_extents_inches(data)
    assert len(extents) == 2
    assert [width for width, _height in extents] == pytest.approx(
        [expected_width, expected_width], abs=0.01
    )
    assert diagnostics["missing_images"] == 0


@pytest.mark.parametrize("figure_align", ["right", "bottom_right"])
def test_word_export_caps_tall_detached_figures(tmp_path, figure_align):
    image_path = tmp_path / "tall-figure.png"
    Image.new("RGB", (200, 1200), "white").save(image_path, format="PNG")

    data, diagnostics = build_word_document(
        "Word 竖图边界",
        "",
        "exam",
        [{
            "question": {
                "id": 59,
                "question_type": "detailed_answer",
                "content": "竖图测试\n\n![插图](/static/uploads/tall-figure.png)",
                "image_paths": ["/static/uploads/tall-figure.png"],
                "figure_align": figure_align,
                "figure_size": "large",
            },
            "score": 12,
        }],
        uploads_dir=tmp_path,
    )

    width, height = _drawing_extents_inches(data)[0]
    assert height == pytest.approx(3.15, abs=0.01)
    assert width / height == pytest.approx(1 / 6, abs=0.01)
    assert diagnostics["missing_images"] == 0


def test_word_lower_figure_uses_part_of_the_configured_solution_space(tmp_path):
    image_path = tmp_path / "wide-large.png"
    Image.new("RGB", (600, 300), "white").save(image_path, format="PNG")

    data, diagnostics = build_word_document(
        "Word 大图留白",
        "",
        "exam",
        [{
            "question": {
                "id": 60,
                "question_type": "detailed_answer",
                "content": "大图留白测试\n\n![插图](/static/uploads/wide-large.png)",
                "image_paths": ["/static/uploads/wide-large.png"],
                "figure_align": "bottom_right",
                "figure_size": "large",
            },
            "score": 12,
            "solution_space": "7.0",
        }],
        uploads_dir=tmp_path,
    )

    root = etree.fromstring(_document_xml(data))
    values = root.xpath(
        ".//w:body/w:p[last()]/w:pPr/w:spacing[@w:lineRule='exact']/@w:line",
        namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"},
    )
    assert values
    space_after_cm = int(values[0]) / 1440 * 2.54
    assert space_after_cm == pytest.approx(1.5, abs=0.05)
    assert diagnostics["missing_images"] == 0


def test_word_figure_size_is_capped_by_current_page_content_width():
    builder = word_export_helper.WordExamBuilder(
        {}, word_export_helper.WordExportDiagnostics()
    )
    section = builder.doc.sections[0]
    section.page_width = word_export_helper.Inches(4)
    section.left_margin = word_export_helper.Inches(1.25)
    section.right_margin = word_export_helper.Inches(1.25)
    section.page_height = word_export_helper.Inches(4)
    section.top_margin = word_export_helper.Inches(1.25)
    section.bottom_margin = word_export_helper.Inches(1.25)

    assert builder._detached_figure_width(
        {"figure_size": "large"}, align="center", image_count=1
    ) == pytest.approx(1.5)
    assert builder._detached_figure_width(
        {"figure_size": "large"}, align="right", image_count=1
    ) == pytest.approx(1.5)
    assert builder._detached_figure_height(
        {"figure_size": "large"}
    ) == pytest.approx(1.5)


def test_word_export_converts_multicolumn_and_multirow_to_native_merges(
    tmp_path,
    monkeypatch,
):
    sample_omml = (
        b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        b"<m:r><m:t>AP=2PD</m:t></m:r></m:oMath>"
    )
    monkeypatch.setattr(
        word_export_helper.PandocOmmlConverter,
        "convert_many",
        lambda self, formulas: {formula: sample_omml for formula in formulas},
    )
    image_path = tmp_path / "merged-cell.png"
    Image.new("RGB", (240, 140), "white").save(image_path, format="PNG")
    content = (
        r"\begin{tabular}{|c|c|c|}" "\n"
        r"\hline \multicolumn{3}{|c|}{综合探究 $AP=2PD$} \\" "\n"
        r"\hline \multirow{2}{*}{步骤} & 第一种完整证明思路 & 第二种完整证明思路 \\" "\n"
        r"\cline{2-3} & ![示意图](/static/uploads/merged-cell.png) & 完成证明 \\" "\n"
        r"\hline\end{tabular}"
    )
    data, diagnostics = build_word_document(
        "合并单元格导出",
        "",
        "exam",
        [{
            "question": {
                "id": 54,
                "question_type": "detailed_answer",
                "content": content,
                "image_paths": ["/static/uploads/merged-cell.png"],
            },
            "score": 12,
        }],
        uploads_dir=tmp_path,
    )

    document = etree.fromstring(_document_xml(data))
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    visible_text = "".join(document.xpath(".//w:t/text()", namespaces=namespaces))
    table = document.xpath(".//w:tbl", namespaces=namespaces)[0]

    assert r"\multicolumn" not in visible_text
    assert r"\multirow" not in visible_text
    assert r"\cline" not in visible_text
    assert "综合探究" in visible_text
    rows = table.xpath("./w:tr", namespaces=namespaces)
    assert len(rows[0].xpath("./w:tc", namespaces=namespaces)) == 1
    assert rows[0].xpath("./w:tc/w:tcPr/w:gridSpan/@w:val", namespaces=namespaces) == ["3"]
    assert rows[0].xpath("./w:tc/w:tcPr/w:tcW/@w:w", namespaces=namespaces) == ["9000"]
    assert rows[1].xpath("./w:tc[1]/w:tcPr/w:vMerge/@w:val", namespaces=namespaces) == ["restart"]
    assert len(rows[2].xpath("./w:tc[1]/w:tcPr/w:vMerge[not(@w:val)]", namespaces=namespaces)) == 1
    assert rows[2].xpath("./w:tc[1]//w:t/text()", namespaces=namespaces) == []
    assert len(rows[2].xpath("./w:tc[2]//w:drawing", namespaces=namespaces)) == 1
    assert table.xpath("count(.//m:oMath)", namespaces=namespaces) == 1
    assert table.xpath("./w:tblGrid/w:gridCol/@w:w", namespaces=namespaces) == [
        "1080", "3960", "3960"
    ]
    assert diagnostics["native_formulas"] == 1
    assert diagnostics["failed_formulas"] == 0
    assert diagnostics["missing_images"] == 0


def test_word_export_bounds_invalid_table_spans():
    content = (
        r"\begin{tabular}{|c|c|c|}" "\n"
        r"\multicolumn{999999}{c}{越界标题} \\" "\n"
        r"\multirow{-2}{*}{负跨度} & A & B \\" "\n"
        r"\end{tabular}"
    )

    data, diagnostics = build_word_document(
        "异常合并范围",
        "",
        "exam",
        [{
            "question": {
                "id": 55,
                "question_type": "detailed_answer",
                "content": content,
                "image_paths": [],
            },
            "score": 12,
        }],
    )

    document = etree.fromstring(_document_xml(data))
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    table = document.xpath(".//w:tbl", namespaces=namespaces)[0]
    assert len(table.xpath("./w:tblGrid/w:gridCol", namespaces=namespaces)) == 3
    assert table.xpath("./w:tr[1]/w:tc/w:tcPr/w:gridSpan/@w:val", namespaces=namespaces) == ["3"]
    assert not table.xpath(".//w:vMerge", namespaces=namespaces)
    assert any("合并范围超界" in warning for warning in diagnostics["warnings"])


def test_word_export_uses_exam_typography_and_one_inch_margins(monkeypatch):
    sample_omml = (
        b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        b"<m:r><m:t>x</m:t></m:r></m:oMath>"
    )
    monkeypatch.setattr(
        word_export_helper.PandocOmmlConverter,
        "convert_many",
        lambda self, formulas: {formula: sample_omml for formula in formulas},
    )
    data, _ = build_word_document("字体测试卷", "副标题", "exam", _sample_questions())
    styles = etree.fromstring(_package_part(data, "word/styles.xml"))
    settings = etree.fromstring(_package_part(data, "word/settings.xml"))
    document = etree.fromstring(_document_xml(data))
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    normal_fonts = styles.xpath(
        ".//w:style[@w:styleId='Normal']/w:rPr/w:rFonts",
        namespaces=namespaces,
    )[0]
    assert normal_fonts.get(f"{{{namespaces['w']}}}eastAsia") == "SimSun"
    assert normal_fonts.get(f"{{{namespaces['w']}}}ascii") == "Times New Roman"
    assert styles.xpath(
        ".//w:style[@w:styleId='Normal']/w:rPr/w:sz/@w:val",
        namespaces=namespaces,
    ) == ["21"]
    assert settings.xpath(
        ".//m:mathPr/m:mathFont/@m:val",
        namespaces=namespaces,
    ) == [word_export_helper.MATH_FONT]
    assert settings.xpath(
        ".//w:themeFontLang/@w:eastAsia",
        namespaces=namespaces,
    ) == ["zh-CN"]

    math_run_fonts = document.xpath(
        ".//m:oMath//m:r/w:rPr/w:rFonts/@w:ascii",
        namespaces=namespaces,
    )
    math_run_sizes = document.xpath(
        ".//m:oMath//m:r/w:rPr/w:sz/@w:val",
        namespaces=namespaces,
    )
    # Only Latin/Greek variables receive Times New Roman Italic. Numerals and
    # symbols must never receive direct fonts because WPS for Mac can hide them
    # until the equation is rebuilt through "Convert to Professional".
    assert math_run_fonts and set(math_run_fonts) == {"Times New Roman"}
    assert not document.xpath(
        ".//m:oMath//m:r[m:t='0']/w:rPr/w:rFonts",
        namespaces=namespaces,
    )
    assert math_run_sizes == []

    visible_text = "".join(document.xpath(".//w:t/text()", namespaces=namespaces))
    assert "共 5 分。 在每小题给出的四个选项中" in visible_text

    margins = document.xpath(".//w:sectPr/w:pgMar", namespaces=namespaces)[0]
    for side in ("top", "right", "bottom", "left"):
        assert margins.get(f"{{{namespaces['w']}}}{side}") == "1440"


def test_word_export_api_returns_zip_bundle(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    create_res = client.post(
        "/api/questions",
        data={
            "content": (
                r"已知 $f(x)=x^2$，则 $f(2)=$" "\n"
                r"\begin{choices}" "\n"
                r"\item $2$" "\n"
                r"\item $4$" "\n"
                r"\item $6$" "\n"
                r"\item $8$" "\n"
                r"\end{choices}"
            ),
            "question_type": "single_choice",
            "category_compulsory": "必修第一册",
            "category_chapter": "函数",
            "category_knowledge": "函数值",
            "difficulty": "easy",
            "source": "Word 导出接口测试",
            "answer_markdown": r"【答案】$4$。\n\n【解析】由 $f(x)=x^2$ 得 $f(2)=2^2=4$。",
            "review": "",
            "tags": "",
            "related_question_id": "",
            "image_paths": "[]",
        },
        headers=headers,
    )
    assert create_res.status_code == 200
    question_id = create_res.json()["question"]["id"]
    response = client.post(
        "/api/paper/export/word",
        json={
            "title": "接口测试卷",
            "subtitle": "",
            "paper_type": "exam",
            "questions": [{"id": question_id, "score": 5}],
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(response.content), "r") as archive:
        names = archive.namelist()
        assert "接口测试卷.docx" in names
        assert "接口测试卷_含答案与解析.docx" in names

        # Verify main docx has questions but no answer appendix
        main_xml = archive.read("接口测试卷.docx")
        main_root = etree.fromstring(_document_xml(main_xml))
        main_text = "".join(main_root.xpath(".//w:t/text()", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))
        assert "接口测试卷" in main_text
        assert "参考答案与解析" not in main_text

        # Verify answers docx has questions AND the answer appendix
        ans_xml = archive.read("接口测试卷_含答案与解析.docx")
        ans_root = etree.fromstring(_document_xml(ans_xml))
        ans_text = "".join(ans_root.xpath(".//w:t/text()", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))
        assert "接口测试卷" in ans_text
        assert "参考答案与解析" in ans_text

    # Native OMML requires optional Pandoc.  This API test verifies that every
    # formula is accounted for regardless of native/fallback/failed outcome;
    # the focused Pandoc test above verifies native conversion when available.
    formula_diagnostics = sum(
        int(response.headers[name])
        for name in (
            "X-Word-Native-Formulas",
            "X-Word-Fallback-Formulas",
            "X-Word-Failed-Formulas",
        )
    )
    assert formula_diagnostics >= 1


def test_word_export_api_supports_single_docx(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    create_res = client.post(
        "/api/questions",
        data={
            "content": r"已知集合 $A=\{1,2\}$，求 $A$ 的子集个数。",
            "question_type": "fill_in_blank",
            "category_compulsory": "必修第一册",
            "category_chapter": "集合",
            "category_knowledge": "子集",
            "difficulty": "easy",
            "source": "单Docx测试",
            "answer_markdown": r"$4$ 个。",
            "review": "",
            "tags": "",
            "related_question_id": "",
            "image_paths": "[]",
        },
        headers=headers,
    )
    assert create_res.status_code == 200
    question_id = create_res.json()["question"]["id"]
    response = client.post(
        "/api/paper/export/word",
        json={
            "title": "单题试卷",
            "paper_type": "exam",
            "as_single_docx": True,
            "include_answers": True,
            "questions": [{"id": question_id, "score": 5}],
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content[:2] == b"PK"
    xml = _document_xml(response.content).decode("utf-8")
    assert "参考答案与解析" in xml



def test_word_export_api_rejects_empty_paper(client):
    response = client.post(
        "/api/paper/export/word",
        json={"title": "空试卷", "paper_type": "exam", "questions": []},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 400
    assert "卷面为空" in response.json()["message"]


@pytest.mark.parametrize("single_docx, include_answers", [(False, False), (True, False), (True, True)])
def test_word_export_api_keeps_answer_images_out_of_questions(
    client, db_session, tmp_path, monkeypatch, single_docx, include_answers,
):
    import main
    from mathbank.database import Question

    Image.new("RGB", (120, 80), "red").save(tmp_path / "answer-only.png")
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
    question = Question(
        content="完成证明。", question_type="detailed_answer",
        answer_markdown="![](/static/uploads/answer-only.png)",
    )
    question.image_paths = ["/static/uploads/answer-only.png"]
    db_session.add(question)
    db_session.commit()
    response = client.post("/api/paper/export/word", json={
        "title": "图片答案接口回归", "paper_type": "exam",
        "as_single_docx": single_docx, "include_answers": include_answers,
        "questions": [{"id": question.id, "score": 12}],
    }, headers={"X-Local-Token": LOCAL_TOKEN})
    assert response.status_code == 200
    assert response.headers["X-Word-Missing-Images"] == "0"
    if single_docx:
        documents = [(response.content, include_answers)]
    else:
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            documents = [(package.read("图片答案接口回归.docx"), False),
                         (package.read("图片答案接口回归_含答案与解析.docx"), True)]
    ns = {"w": word_export_helper.WORD_NS,
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    for data, has_answers in documents:
        root = etree.fromstring(_document_xml(data))
        in_answers = False
        drawings = []
        for block in root.find("w:body", ns):
            if "参考答案与解析" in "".join(block.xpath(".//w:t/text()", namespaces=ns)):
                in_answers = True
            for drawing in block.xpath(".//a:blip", namespaces=ns):
                assert in_answers, "Answer image appeared before the answer appendix"
                drawings.append(drawing)
        assert len(drawings) == int(has_answers)
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            media = [name for name in package.namelist() if name.startswith("word/media/")]
            assert len(media) == int(has_answers)


def test_pandoc_runtime_status_api_reports_existing_install(client, monkeypatch):
    monkeypatch.setattr(
        "main.pandoc_status",
        lambda: {
            "status": "ready",
            "available": True,
            "source": "system",
            "version": "pandoc 3.test",
            "path": "/test/pandoc",
        },
    )
    monkeypatch.setattr(
        "main.PANDOC_INSTALL_MANAGER.snapshot",
        lambda: {"status": "idle", "task_id": None},
    )

    response = client.get("/api/runtime/pandoc/status")

    assert response.status_code == 200
    assert response.json()["pandoc"]["source"] == "system"
    assert response.json()["pandoc"]["available"] is True


def test_pandoc_runtime_install_api_requires_token_and_returns_task(client, monkeypatch):
    state = {
        "task_id": "pandoc-task",
        "status": "queued",
        "progress": 0,
        "message": "queued",
    }
    monkeypatch.setattr("main.PANDOC_INSTALL_MANAGER.ensure", lambda: state)

    forbidden = client.post("/api/runtime/pandoc/install")
    accepted = client.post(
        "/api/runtime/pandoc/install",
        headers={"X-Local-Token": LOCAL_TOKEN},
    )

    assert forbidden.status_code == 403
    assert accepted.status_code == 202
    assert accepted.json()["pandoc"]["task_id"] == "pandoc-task"


def test_word_export_title_block_matches_exam_layout():
    data, _ = build_word_document(
        "2026高中数学期末考试",
        "",
        "exam",
        _sample_questions(),
        show_secret=True,
        show_notice=True,
    )
    document = etree.fromstring(_document_xml(data))
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    paragraphs = document.xpath(".//w:body/w:p", namespaces=namespaces)
    p_texts = ["".join(p.xpath(".//w:t/text()", namespaces=namespaces)) for p in paragraphs]

    # Secret mark is the first paragraph and left-aligned (no 'right' justification)
    assert "绝密★启用前" in p_texts[0]
    secret_jc = paragraphs[0].xpath("./w:pPr/w:jc/@w:val", namespaces=namespaces)
    assert secret_jc == ["left"] or not secret_jc

    # Title, Subject, Meta info
    assert "2026高中数学期末考试" in p_texts[1]
    assert "数  学" in p_texts[2]
    assert "本试卷共 2 题。全卷满分 10 分。考试用时 120 分钟。" in "".join(p_texts)

    # Notices
    assert "注意事项：" in "".join(p_texts)
    assert "1. 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。" in "".join(p_texts)
    assert "2. 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑" in "".join(p_texts)
    assert "3. 考试结束后，将本试卷和答题卡一并交回。" in "".join(p_texts)


def test_word_export_supports_toggling_secret_and_notice():
    data, _ = build_word_document(
        "小练测试卷",
        "",
        "quiz",
        _sample_questions(),
        show_secret=False,
        show_notice=False,
    )
    document = etree.fromstring(_document_xml(data))
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    all_text = "".join(document.xpath(".//w:t/text()", namespaces=namespaces))
    assert "绝密" not in all_text
    assert "注意事项" not in all_text
    assert "数  学" not in all_text
    assert "考试用时" not in all_text


def test_word_document_with_answers_and_omml(monkeypatch):
    sample_omml = (
        b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        b"<m:r><m:t>4</m:t></m:r></m:oMath>"
    )
    monkeypatch.setattr(
        word_export_helper.PandocOmmlConverter,
        "convert_many",
        lambda self, formulas: {formula: sample_omml for formula in formulas},
    )
    data, diagnostics = build_word_document(
        "期末测试卷",
        "含解析版",
        "exam",
        _sample_questions(),
        include_answers=True,
    )
    document = etree.fromstring(_document_xml(data))
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    all_text = "".join(document.xpath(".//w:t/text()", namespaces=namespaces))
    assert "期末测试卷 参考答案与解析" in all_text
    assert "答案为" in all_text

    # Verify answers section has proper OMML equations
    math_elements = document.xpath(".//m:oMath", namespaces=namespaces)
    assert len(math_elements) >= 2


def test_create_word_bundle_zip():
    main_bytes = b"fake_main_docx"
    ans_bytes = b"fake_ans_docx"
    zip_bytes = word_export_helper.create_word_bundle_zip("高一数学月考", main_bytes, ans_bytes)
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
        assert archive.namelist() == ["高一数学月考.docx", "高一数学月考_含答案与解析.docx", "content_sources.json", "CONTENT_SOURCES.txt"]
        assert archive.read("高一数学月考.docx") == main_bytes
        assert archive.read("高一数学月考_含答案与解析.docx") == ans_bytes
