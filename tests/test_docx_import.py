# tests/test_docx_import.py
# -*- coding: utf-8 -*-

import io
import zipfile
import xml.etree.ElementTree as ET
from fastapi.testclient import TestClient
from main import app, LOCAL_TOKEN
from mathbank.omml_helper import omml_to_latex
from mathbank.docx_helper import extract_docx_markdown

from mathbank.mtef_helper import mtef_to_latex

client = TestClient(app)


def test_mtef_to_latex_decoder():
    """测试 MTEF 纯 Python 解码器将 MathType 二进制转换为 LaTeX"""
    # 模拟一个 MTEF v5 数据包: Header (5, 1, 0, 7, b'DSMT7\0', opts=1)
    mtef_bytes = (
        b"\x05\x01\x00\x07"
        b"DSMT7\x00\x01"
        b"Equation Native z= 0=2+i1-i"
    )
    latex = mtef_to_latex(mtef_bytes)
    assert "z = \\dfrac{2+i}{1-i}" in latex or "\\dfrac" in latex


def test_omml_to_latex_conversions():
    """测试 OMML 常见数学结构转换至 LaTeX 语法"""
    # 1. 分数
    frac_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:f><m:num><m:r><m:t>a+b</m:t></m:r></m:num><m:den><m:r><m:t>c+d</m:t></m:r></m:den></m:f></m:oMath>"""
    assert "\\dfrac{a+b}{c+d}" in omml_to_latex(frac_xml)

    # 2. 根号
    rad_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:rad><m:deg/><m:e><m:r><m:t>x^2+1</m:t></m:r></m:e></m:rad></m:oMath>"""
    assert "\\sqrt{x^2+1}" in omml_to_latex(rad_xml)

    # 3. 矢量箭头
    acc_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:acc><m:accPr><m:chr m:val="→"/></m:accPr><m:e><m:r><m:t>AB</m:t></m:r></m:e></m:acc></m:oMath>"""
    assert "\\vec{AB}" in omml_to_latex(acc_xml)

    # 4. 方程组
    cases_xml = """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:eqArr><m:e><m:r><m:t>x+y=3</m:t></m:r></m:e><m:e><m:r><m:t>x-y=1</m:t></m:r></m:e></m:eqArr></m:oMath>"""
    assert "\\begin{cases}" in omml_to_latex(cases_xml)
    assert "x+y=3 \\\\ x-y=1" in omml_to_latex(cases_xml)


def _create_mock_docx(paragraphs, tables=None) -> bytes:
    """在内存中生成一个用于测试的标准 .docx 字节流"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. [Content_Types].xml
        ct = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
        z.writestr("[Content_Types].xml", ct)

        # 2. word/document.xml
        p_xmls = []
        for p in paragraphs:
            p_xmls.append(f"""<w:p><w:r><w:t>{p}</w:t></w:r></w:p>""")

        tbl_xmls = []
        if tables:
            for tbl in tables:
                rows_xml = []
                for row in tbl:
                    cells_xml = "".join([f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row])
                    rows_xml.append(f"<w:tr>{cells_xml}</w:tr>")
                tbl_xmls.append(f"<w:tbl>{''.join(rows_xml)}</w:tbl>")

        doc_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    {"".join(p_xmls)}
    {"".join(tbl_xmls)}
  </w:body>
</w:document>"""
        z.writestr("word/document.xml", doc_xml)
        z.writestr("word/_rels/document.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""")

    return buf.getvalue()


def test_docx_extract_markdown_with_tables():
    """测试 docx 提取段落与表格转换为 Markdown 和 LaTeX tabular"""
    docx_bytes = _create_mock_docx(
        paragraphs=["高中数学模拟试卷", "1. 已知集合 $A = {1, 2}$，求子集个数。"],
        tables=[
            [["类别", "数量"], ["甲", "10"], ["乙", "20"]]
        ]
    )

    res = extract_docx_markdown(docx_bytes)
    assert res["success"] is True
    assert "高中数学模拟试卷" in res["markdown"]
    assert "\\begin{tabular}" in res["markdown"]
    assert "类别 & 数量 \\\\" in res["markdown"]


def test_docx_upload_task_api_validation():
    """测试 docx-task 接口对格式与任务排队生命周期的校验"""
    headers = {"X-Local-Token": LOCAL_TOKEN}

    # 1. 拒绝非 .docx 后缀
    bad_res = client.post(
        "/api/upload/docx-task",
        headers=headers,
        files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert bad_res.status_code == 400
    assert "必须为 .docx" in bad_res.json()["message"]

    # 2. 正常接收 .docx 并返回 task_id
    docx_bytes = _create_mock_docx(["2026年浙江名校联考数学试题"])
    ok_res = client.post(
        "/api/upload/docx-task",
        headers=headers,
        files={"file": ("2026_exam.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    )
    assert ok_res.status_code == 200
    task_id = ok_res.json()["task_id"]
    assert task_id is not None

    # 3. 轮询状态接口
    status_res = client.get(f"/api/tasks/{task_id}/status")
    assert status_res.status_code == 200
    assert "status" in status_res.json()
