"""Large sharing packages retain one verified catalog tree per point code."""
import hashlib
import json
import zipfile

import pytest

from mathbank import library_transfer as transfer


def _records(count=3):
    point = {
        "code": "local:shared", "name": "重复知识点", "original_name": "原始名称",
        "source": "local", "aliases": ["旧名"], "curriculum_paths": [{
            "version_code": "A", "compulsory": "必修一", "chapter": "第一章",
            "knowledge": f"第{index}节", "reference_section": "原始目录", "level": "leaf",
        } for index in range(64)], "role": "main", "status": "confirmed", "position": 0,
        "evidence": {"text": "每题自己的依据"},
    }
    # A JSON round trip creates distinct catalog trees, as a ZIP decoder does.
    return json.loads(json.dumps([
        {"id": index + 1, "content": f"第{index + 1}题", "knowledge_points": [point],
         "active_version_code": "A"} for index in range(count)
    ], ensure_ascii=False))


def _archive(tmp_path, records):
    data = json.dumps(records, ensure_ascii=False).encode("utf-8")
    path = tmp_path / "share.zip"
    manifest = {
        "format": transfer.FORMAT, "format_version": 1, "active_version_code": "A",
        "question_count": len(records), "image_count": 0,
        "files": [{"name": "questions.json", "size": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()}],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("questions.json", data)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
    return path, hashlib.sha256(data).hexdigest()


def test_streamed_records_share_only_the_verified_catalog_before_accumulation(tmp_path, monkeypatch):
    records = _records()
    records[1]["knowledge_points"][0].update(role="auxiliary", status="suggested", position=7,
                                           evidence={"text": "第二题的独立依据"})
    path, _ = _archive(tmp_path, records)
    validated = []
    original = transfer._RecordValidator.validate

    def observe(self, record):
        point = record["knowledge_points"][0]
        if validated:
            assert point["curriculum_paths"] is not validated[0]["curriculum_paths"]
        original(self, record)
        if validated:
            assert point["curriculum_paths"] is validated[0]["curriculum_paths"]
        validated.append(point)

    def no_full_array_pass(_records):
        raise AssertionError("the package must validate each decoded record immediately")

    monkeypatch.setattr(transfer._RecordValidator, "validate", observe)
    monkeypatch.setattr(transfer, "_validate_records", no_full_array_pass)
    package = transfer._read_package(path)
    assert len(validated) == 3
    points = [record["knowledge_points"][0] for record in package["records"]]
    assert points[0] is not points[1]
    assert points[0]["aliases"] is points[1]["aliases"]
    assert points[0]["curriculum_paths"] is package["definitions"]["local:shared"]["curriculum_paths"]
    assert points[1]["role"] == "auxiliary" and points[1]["status"] == "suggested" and points[1]["position"] == 7
    assert points[0]["evidence"] is not points[1]["evidence"]
    points[0]["evidence"]["text"] = "只修改第一题"
    assert points[1]["evidence"] == {"text": "第二题的独立依据"}


@pytest.mark.parametrize("damage", [
    lambda point: point.update(unknown=True),
    lambda point: point.update(position=True),
    lambda point: point.update(aliases=[False]),
    lambda point: point["curriculum_paths"][0].update(unknown="extra"),
    lambda point: point["curriculum_paths"][0].update(reference_section=True),
    lambda point: point["curriculum_paths"].append(point["curriculum_paths"][0].copy()),
    lambda point: point.update(name="定义冲突"),
    lambda point: point.update(evidence="据" * 32768),
])
def test_repeated_catalog_never_bypasses_per_record_strict_validation(tmp_path, damage):
    records = _records(2)
    damage(records[1]["knowledge_points"][0])
    path, _ = _archive(tmp_path, records)
    with pytest.raises(ValueError):
        transfer._read_package(path)


def test_streamed_record_validation_still_requires_the_final_json_hash(tmp_path):
    path, digest = _archive(tmp_path, _records())
    validator = transfer._RecordValidator()
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ValueError, match="校验失败"):
            transfer._read_question_records(archive, "0" * 64, validate_record=validator.validate)
    assert len(validator.ids) == 3 and digest != "0" * 64


def test_catalog_sharing_is_scoped_to_one_validation_run():
    left, right = _records(), _records()
    _, left_definitions = transfer._validate_records(left)
    _, right_definitions = transfer._validate_records(right)
    assert left_definitions == right_definitions
    assert left_definitions["local:shared"]["curriculum_paths"] is not right_definitions["local:shared"]["curriculum_paths"]
    assert left[0]["knowledge_points"][0]["curriculum_paths"] is left[1]["knowledge_points"][0]["curriculum_paths"]
