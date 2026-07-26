import os
import json
import pytest
from main import LOCAL_TOKEN

def test_paper_api_flow(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    # 1. Create a question
    q_data = {
        "content": "已知函数 $f(x) = x^2 + 2x + 1$，求 $f(1)$ 的值。\n\\begin{choices}\n\\item 1\n\\item 2\n\\item 4\n\\item 8\n\\end{choices}",
        "question_type": "single_choice",
        "category_compulsory": "必修第一册",
        "category_chapter": "第一章 集合与常用逻辑用语",
        "category_knowledge": "集合的概念",
        "difficulty": "easy",
        "source": "2026月考",
        "answer_markdown": "解：$f(1) = 1^2 + 2(1) + 1 = 4$。故选 C。",
        "review": "易错点：计算细节",
        "tags": "函数,计算",
        "related_question_id": "",
        "image_paths": "[]"
    }
    create_res = client.post("/api/questions", data=q_data, headers=headers)
    assert create_res.status_code == 200
    q_id = create_res.json()["question"]["id"]
    
    # 2. Batch fetch paper questions
    fetch_res = client.get(f"/api/paper/questions?ids={q_id}")
    assert fetch_res.status_code == 200
    assert len(fetch_res.json()["data"]) == 1
    assert fetch_res.json()["data"][0]["id"] == q_id
    
    # 3. Save Paper & verify usage_count increment
    paper_payload = {
        "title": "测试高中数学单元测试卷",
        "subtitle": "满分150分",
        "paper_type": "exam",
        "questions": [{"id": q_id, "score": 5, "order": 1}]
    }
    save_res = client.post("/api/paper/save", json=paper_payload, headers=headers)
    print("SAVE_RES:", save_res.json())
    assert save_res.status_code == 200
    assert save_res.json()["status"] == "success"
    
    # Check question usage count
    get_q_res = client.get(f"/api/questions/{q_id}")
    assert get_q_res.status_code == 200
    assert get_q_res.json()["usage_count"] == 1

    # 4. Export LaTeX TEX Zip
    tex_res = client.post("/api/paper/export/tex", json=paper_payload, headers=headers)
    assert tex_res.status_code == 200
    assert tex_res.headers.get("content-type") == "application/zip"

    # 4b. Export Full Bundle Zip (TeX + PDF)
    bundle_res = client.post("/api/paper/export/bundle", json=paper_payload, headers=headers)
    assert bundle_res.status_code == 200
    assert bundle_res.headers.get("content-type") == "application/zip"

    # 5. AI Paper Selection
    ai_res = client.post("/api/paper/ai-select", json={"prompt": "函数", "limit": 5}, headers=headers)
    assert ai_res.status_code == 200
    assert ai_res.json()["status"] == "success"
    assert len(ai_res.json()["data"]) >= 1

    # 6. Saved Papers List & Detail & Delete
    list_res = client.get("/api/papers")
    assert list_res.status_code == 200
    papers_list = list_res.json()["data"]
    assert len(papers_list) >= 1
    paper_id = papers_list[0]["id"]

    detail_res = client.get(f"/api/papers/{paper_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["data"]["title"] == "测试高中数学单元测试卷"
    assert len(detail_res.json()["data"]["questions"]) == 1

    del_res = client.delete(f"/api/papers/{paper_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

def test_exam_19_and_answer_sheet_generation(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    from paper_helper import build_answer_sheet_latex
    
    questions_data = [
        {
            "question": {
                "id": 101,
                "question_type": "detailed_answer",
                "content": "求证：$\\sin^2 x + \\cos^2 x = 1$。\n\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}"
            },
            "score": 13
        }
    ]
    
    sheet_tex = build_answer_sheet_latex("2026年模拟考试试卷", "", questions_data)
    assert "\\documentclass" in sheet_tex
    assert "2026年模拟考试试卷" in sheet_tex
    assert "15.（13分）" in sheet_tex
    assert "\\begin{tikzpicture}" in sheet_tex
    
    # Test export endpoints for exam_19
    paper_payload = {
        "title": "2026年高考模拟试卷",
        "subtitle": "数学",
        "paper_type": "exam_19",
        "questions": []
    }
    
    export_res = client.post("/api/paper/export/tex", json=paper_payload, headers=headers)
    assert export_res.status_code == 200
    assert export_res.headers.get("content-type") == "application/zip"

    pdf_sheet_payload = {
        "title": "2026年高考模拟试卷",
        "subtitle": "数学",
        "paper_type": "exam_19",
        "target": "sheet",
        "questions": []
    }
    sheet_pdf_res = client.post("/api/paper/export/pdf", json=pdf_sheet_payload, headers=headers)
    assert sheet_pdf_res.status_code == 200
    assert sheet_pdf_res.headers.get("content-type") == "application/pdf"

def test_solution_space_latex_generation():
    from paper_helper import build_latex_document
    questions_data_65 = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "6.5"
    }]
    questions_data_zero = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "0.0"
    }]
    
    # 1. Non-exam_19 paper mode -> should inject \vspace*{6.5cm}
    tex = build_latex_document("单元测试", "试卷", "exam", questions_data_65, include_answers=False)
    assert r"\vspace*{6.5cm}" in tex
    
    # 2. exam_19 paper mode with 3.0cm solution space -> should inject \vspace*{3.0cm}
    questions_data_3 = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "3.0"
    }]
    tex_19_three = build_latex_document("高考模拟", "试卷", "exam_19", questions_data_3, include_answers=False)
    assert r"\vspace*{3.0cm}" in tex_19_three

    # 3. exam_19 paper mode with 0.0cm solution space -> should not inject \vspace
    tex_19_zero = build_latex_document("高考模拟", "试卷", "exam_19", questions_data_zero, include_answers=False)
    assert r"\vspace*" not in tex_19_zero

