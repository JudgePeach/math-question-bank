import pytest
from main import LOCAL_TOKEN
from database import Question, QuestionCurriculum, init_db

def test_curriculum_coexistence_and_migration(client, db_session):
    # Initialize the test DB index and initial migrations
    init_db()

    # Reset metadata cache to A-version default to ensure test independence from local configuration
    import main
    main.METADATA_CACHE = {
        "question_types": [],
        "difficulties": [],
        "curriculum": {
            "必修一": {
                "1. 集合与常用逻辑用语": ["1.1"]
            }
        }
    }

    headers = {"X-Local-Token": LOCAL_TOKEN}

    # 1. Create a question under Renjiao A (active by default)
    payload = {
        "content": "测试向量题干 $a+b$",
        "question_type": "single_choice",
        "category_compulsory": "必修二",
        "category_chapter": "6. 平面向量及其应用",
        "category_knowledge": "平面向量的数量积",
        "difficulty": "medium",
        "source": "单元测试",
        "answer_markdown": "答案解析",
        "review": "评述",
        "tikz_code": "",
        "tags": "",
        "related_question_id": "",
        "image_paths": "[]"
    }
    
    response = client.post("/api/questions", data=payload, headers=headers)
    assert response.status_code == 200
    question_id = response.json()["question"]["id"]

    # Verify that it exists in QuestionCurriculum for version 'A'
    mapping_a = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="A"
    ).first()
    assert mapping_a is not None
    assert mapping_a.compulsory == "必修二"
    assert mapping_a.chapter == "6. 平面向量及其应用"
    assert mapping_a.knowledge == "平面向量的数量积"

    # 2. Test changing metadata config version to B (which automatically triggers migration from A to B)
    # Save a metadata payload configured with curriculumB (Renjiao B)
    from main import METADATA_CACHE
    new_metadata_payload = {
        "question_types": METADATA_CACHE["question_types"],
        "difficulties": METADATA_CACHE["difficulties"],
        "curriculum": {
            "必修一": {
                "第一章 集合与常用逻辑用语": ["1.1 集合"]
            },
            "必修二": {
                "第六章 平面向量初步": ["6.1"]
            },
            "必修三": {
                "第八章 向量的数量积与三角恒等变换": ["8.1"]
            }
        }
    }

    response = client.post("/api/config/metadata", json=new_metadata_payload, headers=headers)
    assert response.status_code == 200

    # Verify that a 'B' version mapping was automatically created and aligned during metadata save
    mapping_b = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b is not None
    assert mapping_b.compulsory == "必修三"
    assert mapping_b.chapter == "第八章 向量的数量积与三角恒等变换"
    assert mapping_b.knowledge == "" # Leaf knowledge is reset to empty for B

    # Query the question and verify its main category fields are now updated to B-version!
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    fetched_q = response.json()
    assert fetched_q["category_compulsory"] == "必修三"
    assert fetched_q["category_chapter"] == "第八章 向量的数量积与三角恒等变换"
    assert fetched_q["category_knowledge"] == ""

    # 3. Test updating the question category under B-version
    update_payload = payload.copy()
    update_payload["content"] = "更新向量题干"
    update_payload["category_compulsory"] = "必修二"
    update_payload["category_chapter"] = "第六章 平面向量初步"
    update_payload["category_knowledge"] = "6.1"

    response = client.put(f"/api/questions/{question_id}", data=update_payload, headers=headers)
    assert response.status_code == 200

    # Verify B-version mapping is updated
    mapping_b_updated = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="B"
    ).first()
    assert mapping_b_updated.compulsory == "必修二"
    assert mapping_b_updated.chapter == "第六章 平面向量初步"
    assert mapping_b_updated.knowledge == "6.1"

    # Verify original A-version mapping is still preserved and untouched!
    mapping_a_preserved = db_session.query(QuestionCurriculum).filter_by(
        question_id=question_id, version_code="A"
    ).first()
    assert mapping_a_preserved.compulsory == "必修二"
    assert mapping_a_preserved.chapter == "6. 平面向量及其应用"
    assert mapping_a_preserved.knowledge == "平面向量的数量积"

    # 4. Switch metadata back to A-version
    revert_metadata_payload = {
        "question_types": METADATA_CACHE["question_types"],
        "difficulties": METADATA_CACHE["difficulties"],
        "curriculum": {
            "必修一": {
                "1. 集合与常用逻辑用语": ["1.1"]
            },
            "必修二": {
                "6. 平面向量及其应用": ["6.1"]
            }
        }
    }
    response = client.post("/api/config/metadata", json=revert_metadata_payload, headers=headers)
    assert response.status_code == 200

    # Query the question and verify its main category fields are reverted back to A-version!
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200
    reverted_q = response.json()
    assert reverted_q["category_compulsory"] == "必修二"
    assert reverted_q["category_chapter"] == "6. 平面向量及其应用"
    assert reverted_q["category_knowledge"] == "平面向量的数量积"
