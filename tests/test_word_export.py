import io
import shutil
import zipfile

import pytest
from lxml import etree
from PIL import Image

from main import LOCAL_TOKEN
from mathbank import word_export_helper
from mathbank.word_export_helper import MathToken, build_word_document, tokenize_mixed_content


def _document_xml(docx_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as archive:
        return archive.read("word/document.xml")


def _package_part(docx_bytes: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as archive:
        return archive.read(name)


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
        assert archive.namelist() == ["高一数学月考.docx", "高一数学月考_含答案与解析.docx"]
        assert archive.read("高一数学月考.docx") == main_bytes
        assert archive.read("高一数学月考_含答案与解析.docx") == ans_bytes
