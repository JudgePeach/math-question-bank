import os
import json
import pytest
from main import LOCAL_TOKEN
from database import Question

def test_api_forbidden_without_token(client):
    # Any POST/PUT/DELETE request without X-Local-Token must return 403 Forbidden
    response = client.post("/api/settings/save", data={"deepseek_key": "test_key"})
    assert response.status_code == 403
    assert response.json()["status"] == "error"
    assert "Forbidden" in response.json()["message"]


def test_api_settings_get(client):
    # GET settings should always be allowed (does not require token)
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "prefer_engine" in data
    assert "prefer_solve_model" in data


def test_api_questions_crud(client):
    # 1. Get initial empty question list
    response = client.get("/api/questions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 0

    # 2. Create a new question with valid X-Local-Token
    headers = {"X-Local-Token": LOCAL_TOKEN}
    payload = {
        "content": "测试API题目干 $a^2+b^2=c^2$",
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "第一章",
        "category_knowledge": "勾股定理",
        "difficulty": "medium",
        "source": "单元测试",
        "answer_markdown": "答案解析内容",
        "review": "评述内容",
        "related_question_id": "",
        "image_paths": "[]"
    }
    
    response = client.post("/api/questions", data=payload, headers=headers)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"
    created_q = res_data["question"]
    assert created_q["id"] is not None
    assert created_q["content"] == payload["content"]
    assert created_q["question_type"] == "single_choice"
    assert created_q["category_compulsory"] == "必修一"
    
    question_id = created_q["id"]

    # 3. Read the specific question (single question API)
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    fetched_q = response.json()
    assert fetched_q["id"] == question_id
    assert fetched_q["answer_markdown"] == "答案解析内容"
    assert fetched_q["review"] == "评述内容"

    # 4. Filter list of questions
    response = client.get("/api/questions?compulsory=必修一&difficulty=medium")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == question_id

    # Filter with mismatching criteria
    response = client.get("/api/questions?compulsory=必修一&difficulty=hard")
    assert response.status_code == 200
    assert len(response.json()) == 0

    # 5. Update the question
    update_payload = payload.copy()
    update_payload["content"] = "更新后的API题目干"
    update_payload["difficulty"] = "challenge"
    
    response = client.put(f"/api/questions/{question_id}", data=update_payload, headers=headers)
    assert response.status_code == 200
    res_data_update = response.json()
    assert res_data_update["status"] == "success"
    updated_q = res_data_update["question"]
    assert updated_q["id"] == question_id
    assert updated_q["content"] == "更新后的API题目干"
    assert updated_q["difficulty"] == "challenge"

    # 6. Delete the question
    response = client.delete(f"/api/questions/{question_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # 7. Check list is empty again
    response = client.get("/api/questions")
    assert len(response.json()) == 0


def test_api_categories(client):
    # GET categories should return category options
    response = client.get("/api/categories")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_api_stats(client):
    # GET stats should return correct question counts
    response = client.get("/api/stats")
    assert response.status_code == 200
    stats = response.json()
    assert stats["status"] == "success"
    assert "total_count" in stats
    assert "easy_error_count" in stats
    assert "challenge_count" in stats
    assert "qiangji_count" in stats
    assert stats["total_count"] == 0


def test_api_search_by_review(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    payload = {
        "content": "这是一道特殊的代数题",
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "第一章",
        "category_knowledge": "勾股定理",
        "difficulty": "medium",
        "source": "单元测试",
        "answer_markdown": "答案解析内容",
        "review": "这是名师特别推荐的精品评析",
        "tags": "高一,期中,真题",
        "related_question_id": "",
        "image_paths": "[]"
    }
    # Create question
    response = client.post("/api/questions", data=payload, headers=headers)
    assert response.status_code == 200
    q_id = response.json()["question"]["id"]

    try:
        # Search for something in content
        response = client.get("/api/questions?q=特殊的代数")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["id"] == q_id

        # Search for something in review
        response = client.get("/api/questions?q=精品评析")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["id"] == q_id

        # Search for something in tags
        response = client.get("/api/questions?q=期中")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["id"] == q_id
        assert response.json()[0]["tags"] == "高一,期中,真题"

        # Search for non-existent text
        response = client.get("/api/questions?q=不存在的关键字")
        assert response.status_code == 200
        assert len(response.json()) == 0
    finally:
        # Clean up
        client.delete(f"/api/questions/{q_id}", headers=headers)


def test_api_metadata_config(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    # 1. GET metadata
    response = client.get("/api/config/metadata")
    assert response.status_code == 200
    data = response.json()
    assert "question_types" in data
    assert "difficulties" in data
    assert "curriculum" in data

    # 保存原始配置以便还原
    original_config = data

    try:
        # 2. POST custom config (Forbidden without token)
        test_payload = {
            "question_types": [{"value": "test_type", "label": "测试题型"}],
            "difficulties": [{"value": "test_diff", "label": "测试难度", "color": "color-test"}],
            "curriculum": {"测试学段": {"测试章节": ["测试小节"]}}
        }
        response = client.post("/api/config/metadata", json=test_payload)
        assert response.status_code == 403

        # 3. POST custom config (Success with token)
        response = client.post("/api/config/metadata", json=test_payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # 4. Verify config updated
        response = client.get("/api/config/metadata")
        assert response.status_code == 200
        new_data = response.json()
        assert new_data["question_types"][0]["value"] == "test_type"
        assert new_data["curriculum"]["测试学段"]["测试章节"] == ["测试小节"]
    finally:
        # 5. Restore original config
        client.post("/api/config/metadata", json=original_config, headers=headers)


def test_curriculum_preset_api(client):
    for version in ("A", "B", "S", "H"):
        response = client.get(f"/api/config/curriculum-presets/{version}")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == version
        assert data["name"]
        assert data["metadata"]["curriculum"]

    response = client.get("/api/config/curriculum-presets/unknown")
    assert response.status_code == 404


def test_pdf_task_and_crop(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    # 1. Test POST /api/upload/pdf-task with invalid format
    response = client.post(
        "/api/upload/pdf-task",
        files={"file": ("test.txt", b"some plain text", "text/plain")},
        data={"generate_answers": "false"},
        headers=headers
    )
    assert response.status_code == 400
    assert "必须为 .pdf 格式" in response.json()["message"]

    # 2. Test status route for non-existent task
    response = client.get("/api/tasks/non-existent-task-id/status")
    assert response.status_code == 404

    # 3. Test clear-temp-crops endpoint
    payload = {"paths": ["/static/uploads/tmp/pdf_crop_test_nonexistent.png"]}
    response = client.post("/api/ai/clear-temp-crops", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_ai_solve_with_ocr(client):
    from unittest.mock import patch, MagicMock
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake_key"}):
        with patch("mathbank.ai_http.robust_request_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": "\\textbf{【参考答案】}：2\n\\textbf{【详细解析】}：求导结果正确\n\\textbf{【核心知识点】}：导数"
                        }
                    }
                ]
            }
            mock_post.return_value = mock_resp

            payload = {
                "content": "已知 $f(x) = x^2$，求 $f'(1)$",
                "question_type": "detailed_answer",
                "ocr_result": "OCR识别的草稿：求导得到2x，带入1得到2",
                "custom_prompt": "请简化解答步骤",
                "thinking": "disabled",
                "model": "DEEPSEEK/deepseek-chat"
            }
            
            response = client.post("/api/ai/solve", data=payload, headers=headers)
            assert response.status_code == 200
            res_data = response.json()
            assert res_data["status"] == "success"
            assert "2" in res_data["solution"]
            
            # Verify request payload included OCR context and custom prompt
            args, kwargs = mock_post.call_args
            sent_data = kwargs["json"]
            user_msg = sent_data["messages"][1]["content"]
            assert "已有的 OCR 识别解析/草稿内容如下" in user_msg
            assert "OCR识别的草稿" in user_msg
            assert "请简化解答步骤" in user_msg
            assert "已知 $f(x) = x^2$" in user_msg


def test_parse_paper_flows_use_shared_provider_resolution(client):
    from unittest.mock import MagicMock, patch
    from main import parse_paper_text_internal

    headers = {"X-Local-Token": LOCAL_TOKEN}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "questions": [
                                {
                                    "content": "求 $1+1$ 的值。",
                                    "answer_markdown": "",
                                    "referenced_images": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }

    provider_env = {
        "PREFER_PARSE_MODEL": "BAILIAN/qwen3.7-max",
        "ALI_BAILIAN_API_KEY": "fake-bailian-key",
        "ALI_BAILIAN_API_BASE": "https://bailian.example/v1/",
    }
    with patch.dict(os.environ, provider_env):
        with patch("mathbank.ai_http.robust_request_post", return_value=mock_resp) as mock_post:
            response = client.post(
                "/api/ai/parse-paper",
                data={
                    "latex_content": "求 $1+1$ 的值。",
                    "paper_title": "测试试卷",
                    "image_mapping_json": "{}",
                    "generate_answers": "false",
                },
                headers=headers,
            )
            internal_questions = parse_paper_text_internal(
                "求 $1+1$ 的值。", generate_answers_bool=False
            )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert internal_questions[0]["content"] == "求 $1+1$ 的值。"
    assert mock_post.call_count == 2
    for call in mock_post.call_args_list:
        args, kwargs = call
        assert args[0] == "https://bailian.example/v1/chat/completions"
        assert kwargs["json"]["model"] == "qwen-max"
        assert kwargs["timeout"] == 180

def test_figure_align_api(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    payload = {
        "content": "插图排版测试题目 $x+y$",
        "question_type": "single_choice",
        "category_compulsory": "必修一",
        "category_chapter": "集合",
        "category_knowledge": "集合的含义",
        "difficulty": "easy",
        "source": "单元测试",
        "answer_markdown": "答案",
        "review": "",
        "tikz_code": "",
        "figure_align": "right",
        "tags": "",
        "related_question_id": "",
        "image_paths": "[]"
    }

    response = client.post("/api/questions", data=payload, headers=headers)
    assert response.status_code == 200
    q_id = response.json()["question"]["id"]
    assert response.json()["question"]["figure_align"] == "right"

    # Update figure align to 'center' via dedicated endpoint
    res_center = client.post(f"/api/questions/{q_id}/figure_align", data={"figure_align": "center"}, headers=headers)
    assert res_center.status_code == 200
    assert res_center.json()["figure_align"] == "center"

    # Query back
    res_get = client.get(f"/api/questions/{q_id}")
    assert res_get.status_code == 200
    assert res_get.json()["figure_align"] == "center"

