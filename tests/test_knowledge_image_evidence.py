"""Private image_request manifests and unverified OCR evidence, with synthetic images only."""
from copy import deepcopy
import io
import json

from PIL import Image
import pytest

from mathbank import ai_classification as classification
from mathbank.ai_providers import resolve_text_provider
from mathbank.ai_knowledge_points import annotate_ai_knowledge_points
from mathbank.knowledge_image_evidence import IMAGE_REASON_NOTICE


CATALOG = [{"code": "local:method", "name": "实际方法"}]
FORMULA = r"$x^2-1=(x-1)(x+1)$"
TRANSCRIPT = "【解析】" + FORMULA + r"，故方程的根为$-1,1$。"


def png(path, color="white"):
    image = Image.new("RGB", (20, 20), color)
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())


@pytest.fixture
def image_request(tmp_path):
    png(tmp_path / "answer.png")
    question = {"content": "求方程的根。", "answer_markdown": "![原解](/static/uploads/answer.png)", "review_markdown": ""}
    provider = resolve_text_provider("SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash", {})
    message, context, _ = classification.build_classification_user_message(question, provider, uploads_dir=tmp_path)
    entry = context["image_manifest"][0]
    proof = {"image_id": entry["image_id"], "sha256": entry["sha256"],
             "transcription": TRANSCRIPT, "step_quote": FORMULA}
    point = {"code": "local:method", "role": "auxiliary", "evidence_source": "answer_markdown",
             "evidence": question["answer_markdown"], "target_evidence": "求方程的根", "reason": "原解实际进行了变形。",
             "image_evidence": proof}
    return question, point, context, tmp_path, provider


def adopt(image_request, *, question=None, point=None, manifest=None):
    original, raw, context, _, _ = image_request
    q = deepcopy(original if question is None else question)
    q["knowledge_points"] = [deepcopy(raw if point is None else point)]
    return annotate_ai_knowledge_points(q, CATALOG,
        image_sources=context["image_manifest"] if manifest is None else manifest)


def test_image_steps_keep_existing_persistent_fields_and_disclose_ocr_basis(image_request):
    q = adopt(image_request)
    accepted = q["knowledge_points"][0]
    assert accepted["evidence_source"] == "answer_markdown"
    assert accepted["evidence"] == image_request[0]["answer_markdown"]
    assert accepted["reason"].startswith(IMAGE_REASON_NOTICE) and FORMULA in accepted["reason"]
    assert "image_evidence" not in accepted and len(accepted["reason"]) <= 1000
    assert q["knowledge_point_diagnostics"]["solution_context"]["has_reasoning"] is False
    proof = q["knowledge_point_diagnostics"]["image_evidence"][0]
    assert proof["basis"] == "model_image_transcription_not_independently_verified"
    assert proof["transcription_sha256"] and proof["field"] == "answer_markdown"


def test_private_manifest_and_diagnostics_survive_second_local_validation(image_request):
    q = adopt(image_request)
    before = deepcopy(q)
    annotate_ai_knowledge_points(q, [])
    assert q == before
    serialized = json.dumps(q, ensure_ascii=False)
    assert "_bindings" not in serialized and str(image_request[3]) not in serialized


def test_plain_json_manifest_cannot_inherit_trust(image_request):
    manifest = json.loads(json.dumps(image_request[2]["image_manifest"]))
    assert adopt(image_request, manifest=manifest)["knowledge_points"] == []


def test_cross_question_manifest_replay_is_refused(image_request):
    q = {**image_request[0], "content": "另一道题，求方程的根。"}
    assert adopt(image_request, question=q)["knowledge_points"] == []


@pytest.mark.parametrize("mutation", ["replace", "delete", "symlink"])
def test_image_file_must_still_be_the_original_sent_file_at_adoption(image_request, mutation):
    path = image_request[3] / "answer.png"
    if mutation == "replace": png(path, "black")
    elif mutation == "delete": path.unlink()
    else:
        other = image_request[3] / "other.png"; png(other)
        path.unlink(); path.symlink_to(other)
    assert adopt(image_request)["knowledge_points"] == []


@pytest.mark.parametrize("change", [
    {"image_id": "foreign"}, {"sha256": "0" * 64}, {"step_quote": "模型补写的步骤"},
    {"has_reasoning": True}, {"transcription": TRANSCRIPT + "x" * 8000},
    {"transcription": "【答案】$x=-1,1$", "step_quote": "$x=-1,1$"},
    {"transcription": "【答案】" + FORMULA, "step_quote": FORMULA},
    {"transcription": "【答案】解得$x=5$。", "step_quote": "解得$x=5$"},
    {"transcription": "可得$x=5$。", "step_quote": "可得$x=5$"},
    {"transcription": "若改问因式分解，则" + TRANSCRIPT, "step_quote": FORMULA},
    {"transcription": "假设采用因式分解，" + TRANSCRIPT, "step_quote": FORMULA},
])
def test_unknown_images_guessed_or_result_only_transcripts_do_not_license_auxiliary(image_request, change):
    point = deepcopy(image_request[1]); point["image_evidence"].update(change)
    assert adopt(image_request, point=point)["knowledge_points"] == []


def test_identical_pixels_in_different_fields_do_not_transfer_image_ownership(image_request):
    q, point, _, root, provider = image_request
    png(root / "review.png")
    q = {**q, "review_markdown": "![评析](/static/uploads/review.png)"}
    _, context, _ = classification.build_classification_user_message(q, provider, uploads_dir=root)
    first, second = context["image_manifest"]
    assert first["sha256"] == second["sha256"]
    point = {**point, "evidence_source": "review_markdown", "evidence": q["review_markdown"]}
    assert adopt(image_request, question=q, point=point, manifest=context["image_manifest"])["knowledge_points"] == []


def test_stem_image_cannot_claim_to_be_an_auxiliary_solution(image_request):
    q, point, _, root, provider = image_request
    q = {**q, "content": "求方程的根。" + q["answer_markdown"], "answer_markdown": ""}
    _, context, _ = classification.build_classification_user_message(q, provider, uploads_dir=root)
    point = {**point, "evidence_source": "content", "evidence": image_request[0]["answer_markdown"]}
    assert adopt(image_request, question=q, point=point, manifest=context["image_manifest"])["knowledge_points"] == []


@pytest.mark.parametrize("frame", ["若改问因式分解，则看下列解法：", "这是未采用的解法：", "可替代的方法如下："])
def test_an_image_in_a_hypothetical_source_frame_is_not_an_actual_solution(image_request, frame):
    q, point, _, root, provider = image_request
    q = {**q, "answer_markdown": frame + q["answer_markdown"]}
    _, context, _ = classification.build_classification_user_message(q, provider, uploads_dir=root)
    assert adopt(image_request, question=q, manifest=context["image_manifest"])["knowledge_points"] == []


def test_bad_early_reference_does_not_occupy_an_attachment_slot(image_request, monkeypatch):
    q, _, _, root, provider = image_request
    png(root / "extra.png")
    q = {**q, "content": "![](/static/uploads/missing.png) " + q["answer_markdown"] + " ![](/static/uploads/extra.png)",
         "answer_markdown": ""}
    monkeypatch.setattr(classification, "MAX_CLASSIFY_IMAGES", 1)
    _, context, warnings = classification.build_classification_user_message(q, provider, uploads_dir=root)
    assert [entry["reason"] for entry in context["image_inputs"]] == ["missing_file", None, "image_limit"]
    assert context["visible_image_count"] == 1 and len(warnings) == 3


@pytest.mark.parametrize("kind", ["input_too_large", "output_too_large", "invalid_image", "invalid_path", "model_no_images", "unreadable_file"])
def test_each_unsent_image_has_a_safe_reason_and_friendly_message(image_request, monkeypatch, kind):
    q, _, _, root, provider = image_request
    if kind == "input_too_large": monkeypatch.setattr(classification, "MAX_CLASSIFY_IMAGE_BYTES", 4)
    elif kind == "invalid_image": (root / "answer.png").write_bytes(b"not an image")
    elif kind == "invalid_path": q = {**q, "answer_markdown": "![](https://private.example/secret.png)"}
    elif kind in {"output_too_large", "unreadable_file"}:
        from mathbank.asset_security import _ImageOutputTooLargeError
        def fail(*args, **kwargs):
            raise (_ImageOutputTooLargeError("private conversion detail") if kind == "output_too_large"
                   else PermissionError("private file detail"))
        monkeypatch.setattr(classification, "normalize_raster_image" if kind == "output_too_large" else "read_stream_limited", fail)
    else: provider = resolve_text_provider("BAILIAN/custom-text", {})
    _, context, _ = classification.build_classification_user_message(q, provider, uploads_dir=root)
    detail = context["image_inputs"][0]
    assert detail["reason"] == kind and detail["message"] and not detail["sent"]
    assert str(root) not in json.dumps(context) and "private.example" not in json.dumps(context)
    assert "private conversion" not in json.dumps(context) and "private file" not in json.dumps(context)


@pytest.mark.parametrize("evidence,source,field", [
    ({"quote": "旧JSON元数据"}, "旧JSON元数据", "answer_markdown"),
    ("使用定义", "使用定义", "answer_markdown"),
    ("/static/uploads/answer.png", "/static/uploads/answer.png", "answer_markdown"),
    ("![另一张图](/static/uploads/answer.png)", "其他原解", "answer_markdown"),
    ("![字面](/static/uploads/answer.png)", "`![字面](/static/uploads/answer.png)`", "answer_markdown"),
    ("![原图](/static/uploads/answer.png)", "![原图](/static/uploads/answer.png)", "legacy_source"),
])
def test_sharing_does_not_rewrite_legacy_or_ungrounded_metadata(evidence, source, field):
    from mathbank.library_transfer import _import_image_evidence
    frozen = deepcopy(evidence)
    result = _import_image_evidence({"evidence_source": field, "evidence": evidence},
        {"answer_markdown": source}, {"answer_markdown": source.replace("answer.png", "shared.png")},
        {"/static/uploads/answer.png": "/static/uploads/shared.png"})
    assert result == frozen and evidence == frozen


def test_image_metadata_survives_normal_save_and_sharing_roundtrip(image_request, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from mathbank.database import Base, KnowledgePoint, Question
    from mathbank.knowledge_points import normalize_question_points, replace_question_points
    from mathbank.library_transfer import export_library_zip, import_library_zip, inspect_library_zip

    q = adopt(image_request)
    engines = [create_engine("sqlite:///:memory:") for _ in range(2)]
    try:
        for engine in engines:
            Base.metadata.create_all(engine)
        archive = tmp_path / "synthetic-image-evidence.zip"
        with Session(engines[0]) as db:
            kp = KnowledgePoint(code="local:method", name="实际方法", original_name="实际方法", source="local")
            kp.aliases = []; db.add(kp); db.flush()
            row = Question(content=q["content"], answer_markdown=q["answer_markdown"], question_type="detailed_answer")
            row.image_paths = ["/static/uploads/answer.png"]
            replace_question_points(row, normalize_question_points(q["knowledge_points"], db), db)
            db.add(row); db.commit()
            saved = row.to_dict()["knowledge_points"][0]
            assert saved["evidence_source"] == "answer_markdown" and saved["evidence"] in row.answer_markdown
            assert saved["reason"].startswith(IMAGE_REASON_NOTICE) and "image_evidence" not in saved
            export_library_zip(db, archive, uploads_dir=image_request[3])
        assert inspect_library_zip(archive)["question_count"] == 1
        with Session(engines[1]) as db:
            result = import_library_zip(db, archive, uploads_dir=tmp_path / "received")
            assert result["imported_count"] == 1
            received = db.query(Question).one()
            label = received.to_dict()["knowledge_points"][0]
            for key in ("code", "role", "status", "evidence_source", "reason", "origin"):
                assert label[key] == saved[key]
            assert label["evidence"] in received.answer_markdown
            assert "image_evidence" not in label and FORMULA in label["reason"]
    finally:
        for engine in engines:
            engine.dispose()
