"""Reference contracts on disposable API data, including target deletion."""
import copy

import pytest

from mathbank.database import Question


@pytest.fixture(autouse=True)
def authenticate(client):
    from main import LOCAL_TOKEN
    client.headers.update({"X-Local-Token": LOCAL_TOKEN})


def create(client, text, source="", answer="", review=""):
    response = client.post("/api/questions", data={
        "content": text, "question_type": "detailed_answer", "difficulty": "normal",
        "source": source, "answer_markdown": answer, "review": review,
    })
    assert response.status_code == 200, response.text
    return response.json()["question"]


def update(client, q, **changes):
    values = {name: q.get(name, "") for name in (
        "content", "question_type", "difficulty", "source", "answer_markdown", "review",
    )}
    values.update(changes)
    response = client.put(f'/api/questions/{q["id"]}', data=values)
    assert response.status_code == 200, response.text
    return response.json()["question"]


def resolved(q, field="answer_markdown"):
    return [item for item in q["question_references"]
            if item["field"] == field and item["status"] == "resolved"]


def test_reference_keeps_original_text_and_follows_current_sequence(client, db_session):
    prefix = create(client, "前序题")
    target = create(client, "所引用的例题", "引用回归教材 | 例 2.6")
    original = "参考例2.6的做法，得到结论。"
    source = create(client, "引用来源题", "引用回归教材 | 例 2.7", original)
    ref = resolved(source)[0]
    assert ref["target_question_id"] == target["id"]
    assert ref["seq_num"] == target["seq_num"] == 2
    assert ref["text"] == original[ref["start"]:ref["end"]] == "例2.6"
    assert client.delete(f'/api/questions/{prefix["id"]}').status_code == 200
    current = client.get(f'/api/questions/{source["id"]}').json()
    assert resolved(current)[0]["seq_num"] == 1
    assert current["answer_markdown"] == original
    assert db_session.get(Question, source["id"]).answer_markdown == original


def test_target_deletion_and_reused_highest_id_never_rebind(client, db_session):
    source = create(client, "先创建引用题", "引用删除回归 | 例 1.1", "参考例1.2。")
    target = create(client, "最后创建目标", "引用删除回归 | 例 1.2")
    # Saving also establishes any reference that was unresolved before the target existed.
    source = update(client, source)
    assert resolved(source)[0]["target_question_id"] == target["id"]
    assert client.delete(f'/api/questions/{target["id"]}').status_code == 200
    deleted = client.get(f'/api/questions/{source["id"]}').json()["question_references"][0]
    assert deleted["status"] == "target_deleted" and deleted["target_question_id"] is None
    replacement = create(client, "后来新建的不同题", "引用删除回归 | 例 1.2")
    assert replacement["id"] == target["id"]  # Real SQLite INTEGER PRIMARY KEY reuse.
    source = update(client, source, answer_markdown="新增说明。参考例 1.2。")
    assert not resolved(source)
    assert source["question_references"][0]["status"] == "target_deleted"


def test_target_source_edit_preserves_binding_and_source_text_edit_updates_anchors(client, db_session):
    target = create(client, "原目标", "引用编辑回归 | 例 3.1")
    source = create(client, "来源", "引用编辑回归 | 例 3.2", "参照例3.1。")
    target = update(client, target, source="用户改写的来源说明")
    assert resolved(client.get(f'/api/questions/{source["id"]}').json())[0]["target_question_id"] == target["id"]
    source = update(client, source, answer_markdown="😀前置文字。参照例3.1。")
    ref = resolved(source)[0]
    assert ref["start"] == source["answer_markdown"].index("例3.1")
    source = update(client, source, answer_markdown="此处不再引用其他例题。")
    assert source["question_references"] == []


def test_binding_follows_formatting_and_moving_citation_between_fields(client, db_session):
    target = create(client, "引用搬移目标", "搬移教材 | 例 3.1")
    source = create(client, "引用搬移来源", "搬移教材 | 例 3.2", "参照例3.1。")
    update(client, target, source="后续更正的来源说明")
    source = update(client, source, answer_markdown="", review="现在参照例 3.1。")
    assert resolved(source, "review")[0]["target_question_id"] == target["id"]
    assert source["question_references"][0]["field"] == "review"


def test_deleting_one_ambiguous_target_resolves_pending_reference(client, db_session):
    first = create(client, "同书号第一候选", "歧义教材 | 例 6.1")
    second = create(client, "同书号第二候选", "歧义教材 | 例 6.1")
    source = create(client, "等待歧义消除", "歧义教材 | 例 6.2", "参考例6.1。")
    assert source["question_references"][0]["status"] == "unresolved"
    assert source["question_references"][0]["reason"] == "multiple_source_targets"
    assert client.delete(f'/api/questions/{second["id"]}').status_code == 200
    current = client.get(f'/api/questions/{source["id"]}').json()
    assert resolved(current)[0]["target_question_id"] == first["id"]


def test_historical_alias_cannot_establish_new_binding_after_target_source_changed(client, db_session):
    target = create(client, "历史来源目标", "历史教材 | 例 2.6")
    old = create(client, "已建立引用", "历史教材 | 例 2.7", "参考例2.6。")
    # An old JSON exchange may update the text while omitting the relation arrays.
    db_session.get(Question, target["id"]).source = "另一教材 | 例 9.9"
    db_session.commit()
    new = create(client, "后来建立的新引用", "历史教材 | 例 2.8", "参考例2.6。")
    assert not resolved(new)
    assert new["question_references"][0]["reason"] == "target_not_found"
    assert resolved(client.get(f'/api/questions/{old["id"]}').json())[0]["target_question_id"] == target["id"]


def test_source_label_limits_fail_before_durable_question_commit(client, db_session):
    count = db_session.query(Question).count()
    response = client.post("/api/questions", data={
        "content": "不能让超长来源标签破坏下次启动", "question_type": "detailed_answer",
        "difficulty": "normal", "source": "某书 | 例" + " " * 200 + "2.6",
    })
    assert response.status_code == 400
    assert db_session.query(Question).count() == count


def test_explicit_other_book_name_overrides_parent_source_namespace(client, db_session):
    first = create(client, "甲书例题", "甲书 | 例 1.2")
    second = create(client, "乙书例题", "乙书 | 例 1.2")
    source = create(client, "引用明确限定另一书", "甲书 | 例 2.1", "参照《乙书》的例1.2。")
    assert resolved(source)[0]["book_key"] == "乙书"
    assert resolved(source)[0]["target_question_id"] == second["id"] != first["id"]


def test_historical_identity_ambiguity_survives_repeated_formatting_and_export(client, db_session):
    from mathbank.database import QuestionReference
    from mathbank.question_references import replace_exchange_reference_data
    a = create(client, "历史身份甲", "历史身份教材 | 例 1.2")
    source = create(client, "历史身份来源", "历史身份教材 | 例 2.1", "参考例1.2，再看例1.2。")
    b = create(client, "历史身份乙", "历史身份教材 | 例 1.2")
    # Existing external archives may contain two established historical bindings.
    rows = db_session.query(QuestionReference).filter_by(source_question_id=source["id"]).order_by(QuestionReference.start).all()
    rows[1].target_question_id = b["id"]
    db_session.commit()
    source = update(client, source, answer_markdown="参考例 1.2，再看例 1.2。")
    assert all(row["reason"] == "multiple_previous_reference_targets" for row in source["question_references"])
    update(client, b, source="其他来源")
    source = update(client, source, answer_markdown="参考例题1.2，再看例题1.2。")
    assert not resolved(source)
    assert all(row["reason"] == "multiple_previous_reference_targets" for row in source["question_references"])
    replace_exchange_reference_data(db_session, db_session.get(Question, source["id"]), {
        "question_references": source["question_references"],
    })
    db_session.commit()
    imported = client.get(f'/api/questions/{source["id"]}').json()
    assert not resolved(imported)
    assert all(row["reason"] == "multiple_previous_reference_targets" for row in imported["question_references"])


def test_book_namespace_protected_literals_and_list_paper_payloads(client, db_session):
    a = create(client, "甲书目标题", "甲书 | 例 2.6")
    b = create(client, "乙书目标题", "乙书 | 例 2.6")
    source = create(client, "引用展示题", "乙书 | 例 2.7",
                    "参考例2.6。`例2.6`。$\\text{例2.6}$。https://example.test/例2.6",
                    "借鉴例 2.6。")
    assert len(resolved(source)) == 1
    assert resolved(source)[0]["target_question_id"] == b["id"] != a["id"]
    assert resolved(source, "review")[0]["target_question_id"] == b["id"]
    listed = client.get("/api/questions", params={"page": 1}).json()["items"]
    assert next(item for item in listed if item["id"] == source["id"])["question_references"]
    paper = client.get("/api/paper/questions", params={"ids": str(source["id"])}).json()["data"][0]
    assert resolved(paper)[0]["target_question_id"] == b["id"]


def test_export_resolves_only_a_copy_and_uses_library_number(client, db_session):
    from main import _prepare_paper_export_questions
    create(client, "题号前置")
    target = create(client, "导出目标", "引用导出回归 | 例 4.1")
    source = create(client, "导出来源", "引用导出回归 | 例 4.2", "采用例4.1。")
    snapshot = copy.deepcopy(source)
    result = _prepare_paper_export_questions([{"id": source["id"], "score": 8}], db_session)
    assert "题库" in result[0]["question"]["answer_markdown"]
    assert str(target["seq_num"]) in result[0]["question"]["answer_markdown"]
    assert source == snapshot
    assert client.get(f'/api/questions/{source["id"]}').json()["answer_markdown"] == "采用例4.1。"


def test_word_export_contains_resolved_number_in_editable_text(client, db_session):
    import io
    import zipfile
    from lxml import etree
    from main import _prepare_paper_export_questions
    from mathbank.word_export_helper import build_word_document
    create(client, "Word 导出目标", "Word 引用教材 | 例 5.1")
    source = create(client, "Word 导出来源", "Word 引用教材 | 例 5.2", "参考例5.1的方法。")
    items = _prepare_paper_export_questions([{"id": source["id"]}], db_session)
    data, diagnostics = build_word_document("引用导出验证", "", "exam_19", items, include_answers=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    texts = "".join(root.xpath("//w:t/text()", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))
    assert "题库第 1 题" in texts and "原例5.1" in texts
    assert client.get(f'/api/questions/{source["id"]}').json()["answer_markdown"] == "参考例5.1的方法。"
