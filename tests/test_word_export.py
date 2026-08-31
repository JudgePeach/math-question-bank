import io
import shutil
import zipfile

import pytest
from lxml import etree

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
