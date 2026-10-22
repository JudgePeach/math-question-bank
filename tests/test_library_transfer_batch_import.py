"""Bounded import work must keep the sharing transaction fully atomic."""
import hashlib
import json
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from mathbank import library_transfer as transfer
from mathbank import question_duplicate_service as fingerprints
from mathbank.database import Base, KnowledgePoint, Question, QuestionFingerprint, QuestionKnowledgePoint
from mathbank.knowledge_points import METADATA_FIELDS


def test_streamed_question_json_matches_v1_bytes_and_exact_size_cap(tmp_path, monkeypatch):
    records = [{"id": 1, "content": "中文😀 $x^2$", "nested": {"n": None, "values": [1, 1.25, True]}},
               {"id": 2, "content": "换行\n与反斜杠\\", "evidence": {"text": "逐字保留"}}]
    expected = json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    path = tmp_path / "questions.json"
    monkeypatch.setattr(transfer, "MAX_JSON_BYTES", len(expected))
    entry = transfer._write_questions_json(iter(records), path)
    assert path.read_bytes() == expected
    assert entry == {"name": "questions.json", "size": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}
    monkeypatch.setattr(transfer, "MAX_JSON_BYTES", len(expected) - 1)
    with pytest.raises(ValueError, match="512MB"):
        transfer._write_questions_json(iter(records), path)


@pytest.mark.parametrize("fail_index", [None, 130])
def test_large_import_flushes_groups_and_rolls_back_late_batch_failure(tmp_path, monkeypatch, fail_index):
    source_engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    Base.metadata.create_all(source_engine)
    archive = tmp_path / "sharing.zip"
    with Session(source_engine) as source:
        source.add_all(Question(content=f"第{index}题 $x={index}$") for index in range(257))
        source.commit()
        transfer.export_library_zip(source, archive, uploads_dir=tmp_path)
    source_engine.dispose()
    target_engine = create_engine(f"sqlite:///{tmp_path / 'destination.db'}")
    Base.metadata.create_all(target_engine)
    flushed, persistent_questions = [], []
    def count_flush(session, *_):
        flushed.append(len(session.new))
    def count_persistent(session, *_):
        persistent_questions.append(sum(isinstance(row, Question) for row in session.identity_map.values()))
    real_fingerprint, computed = fingerprints.fingerprint_for_question, []
    def compute(question, **kwargs):
        computed.append(question.id)
        if len(computed) == fail_index:
            raise RuntimeError("controlled late fingerprint failure")
        return real_fingerprint(question, **kwargs)
    monkeypatch.setattr(fingerprints, "fingerprint_for_question", compute)
    with Session(target_engine) as target:
        event.listen(target, "before_flush", count_flush)
        event.listen(target, "after_flush_postexec", count_persistent)
        if fail_index is None:
            result = transfer.import_library_zip(target, archive, uploads_dir=tmp_path / "uploads")
            assert result["imported_count"] == 257
            assert len(set(result["id_map"].values())) == 257
        else:
            with pytest.raises(RuntimeError, match="late fingerprint"):
                transfer.import_library_zip(target, archive, uploads_dir=tmp_path / "uploads")
    # Use a separate connection/session to check actual persisted outcomes.
    with Session(target_engine) as readback:
        expected = 257 if fail_index is None else 0
        assert readback.query(Question).count() == expected
        assert readback.query(QuestionFingerprint).count() == expected
        if expected:
            assert {row.status for row in readback.query(QuestionFingerprint)} == {"ready"}
            assert len(flushed) < 12
    target_engine.dispose()
    assert persistent_questions and max(persistent_questions) <= 128


def test_consumed_private_point_trees_release_without_losing_persisted_evidence(tmp_path):
    source_engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    Base.metadata.create_all(source_engine)
    archive = tmp_path / "sharing.zip"
    with Session(source_engine) as source:
        point = KnowledgePoint(code="local:memory_perf", name="独立点", original_name="独立点", source="local")
        source.add(point)
        for index in range(257):
            q = Question(content=f"第{index}题 $x={index}$")
            values = {"scope": {"question": index}, "evidence_source": {"field": "content", "ordinal": index},
                      "evidence": ["唯一证据", index], "reason": f"独立说明{index}",
                      "origin": {"unique": index, "nested": [None, {"文本": "原样保留"}]}}
            q.knowledge_points = [QuestionKnowledgePoint(point=point, role="main", status="confirmed", position=0,
                **{field: json.dumps(values[field], ensure_ascii=False) for field in METADATA_FIELDS})]
            source.add(q)
        source.commit()
        transfer.export_library_zip(source, archive, uploads_dir=tmp_path)
    source_engine.dispose()
    original_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    target_engine = create_engine(f"sqlite:///{tmp_path / 'destination.db'}")
    Base.metadata.create_all(target_engine)
    with transfer.prepare_library_import(archive, workspace_dir=tmp_path) as prepared:
        package = transfer._PREPARED_IMPORTS[prepared].package
        expected = {record["id"]: {field: deepcopy(record["knowledge_points"][0][field]) for field in METADATA_FIELDS}
                    for record in package["records"]}
        definitions = deepcopy(package["definitions"])
        with Session(target_engine) as target:
            result = transfer.import_library_zip(target, archive, uploads_dir=tmp_path / "uploads", prepared=prepared)
        assert all("knowledge_points" not in record for record in package["records"])
        assert package["definitions"] == definitions
        with Session(target_engine) as readback:
            for source_id, fields in expected.items():
                point = readback.query(QuestionKnowledgePoint).filter_by(question_id=result["id_map"][source_id]).one()
                assert point.point_code == "local:memory_perf" and point.role == "main" and point.status == "confirmed"
                assert {field: json.loads(getattr(point, field)) for field in METADATA_FIELDS} == fields
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == original_hash
    target_engine.dispose()
