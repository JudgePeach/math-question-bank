"""Portable sharing uses isolated SQLite files and temporary raster assets only."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import zipfile

from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from mathbank import database
from mathbank.database import (
    KnowledgePoint, KnowledgePointCurriculum, Question, QuestionCurriculum, QuestionFingerprint,
    QuestionKnowledgePoint, QuestionReference, QuestionSourceLabel,
)
from mathbank import library_transfer as transfer
from mathbank.question_references import (
    reference_payloads, sync_question_references, sync_question_source_labels,
)


@contextmanager
def isolated_db(path):
    engine = create_engine(f"sqlite:///{path}")
    @event.listens_for(engine, "connect")
    def sqlite_settings(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    database.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=13")
    try:
        with sessionmaker(bind=engine)() as session:
            yield session
    finally:
        engine.dispose()


def image_bytes(color, fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (32, 20), color).save(output, format=fmt)
    return output.getvalue()


@pytest.fixture
def share(tmp_path):
    uploads = tmp_path / "source_uploads"
    uploads.mkdir()
    for name, color in (("figure.png", "red"), ("review.png", "blue"), ("hidden.jpg", "yellow")):
        (uploads / name).write_bytes(image_bytes(color, "JPEG" if name.endswith("jpg") else "PNG"))
    zip_path = tmp_path / "share.zip"
    with isolated_db(tmp_path / "source.db") as db:
        platform = KnowledgePoint(code="1015", name="平台原名", original_name="平台来源原名", source="zhixue")
        platform.aliases = ["旧名称"]
        local = KnowledgePoint(code="local:test", name="本地点", original_name="本地点", source="local")
        local.aliases = []
        db.add_all([platform, local])
        first = Question(id=1, content='<!-- MathBank source attribution:\n新高掌\n-->\n\n先看图 ![图](/static/uploads/figure.png) 然后见例1.2。\n`![字面](/static/uploads/figure.png)`',
            source="新高掌 | 例1.1", answer_markdown="见《赵礼显》例1.3。", review="点评 ![图](/static/uploads/review.png)",
            association_group_id="shared-group", difficulty="challenge", tags="复习,图像", tikz_code=r"\begin{tikzpicture}\end{tikzpicture}",
            figure_align="center", figure_size="large", figure_align_custom=True,
            usage_count=9, created_at=datetime(2025, 8, 1, 12, 30))
        first.image_paths = ["/static/uploads/figure.png", "/static/uploads/review.png", "/static/uploads/hidden.jpg"]
        first.tikz_reference_image_path = "/static/uploads/hidden.jpg"
        first.content_tikz_assets = [{"id": "one", "image_path": "/static/uploads/figure.png", "reference_image_path": "/static/uploads/hidden.jpg", "tikz_code": r"\begin{tikzpicture}\end{tikzpicture}", "instruction": "保留图形"}]
        first.image_layouts = {"figure.png": {"align": "left", "size": "medium"}}
        first.curriculum_mappings = [
            QuestionCurriculum(version_code="A", compulsory="必修一", chapter="自定义章", knowledge="历史小节"),
            QuestionCurriculum(version_code="B", compulsory="B册", chapter="B章", knowledge="B小节"),
        ]
        first.curriculum_mappings[0].sub_knowledge = [{"compulsory": "旧册", "chapter": "旧章", "knowledge": "旧关系证据"}]
        first.category_compulsory, first.category_chapter, first.category_knowledge = "必修一", "自定义章", "历史小节"
        first.knowledge_points = [QuestionKnowledgePoint(point=platform, role="main", status="confirmed", position=3, scope='{"text":"证据"}'), QuestionKnowledgePoint(point=local, role="auxiliary", status="suggested", position=7, reason='"人工复核"')]
        second = Question(id=2, content="目标题 $x^2$", source="新高掌 | 例1.2", association_group_id="shared-group")
        third = Question(id=3, content="包外目标题", source="赵礼显 | 例1.3")
        db.add_all([first, second, third])
        db.flush()
        for question in (first, second, third):
            sync_question_source_labels(db, question)
        sync_question_references(db, first)
        db.commit()
        assert transfer.export_library_zip(db, zip_path, uploads_dir=uploads, source_keywords=["新高掌"]) == {"question_count": 2, "image_count": 3}
    return zip_path, uploads


def rewrite_zip(path, *, mutate_manifest=None, mutate_records=None, extra=None, omit=None):
    with zipfile.ZipFile(path) as archive:
        values = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(values["manifest.json"])
    if mutate_records:
        records = json.loads(values["questions.json"])
        mutate_records(records)
        values["questions.json"] = json.dumps(records, ensure_ascii=False).encode()
        item = next(item for item in manifest["files"] if item["name"] == "questions.json")
        item.update(size=len(values["questions.json"]), sha256=hashlib.sha256(values["questions.json"]).hexdigest())
    if mutate_manifest:
        mutate_manifest(manifest)
    values["manifest.json"] = json.dumps(manifest).encode()
    if omit:
        values.pop(omit)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in values.items():
            archive.writestr(name, value)
        if extra:
            archive.writestr(*extra)


def test_share_contains_one_hashed_scoped_source_catalog_not_per_question(share):
    from mathbank.content_sources import content_source_export_files
    package, _ = share
    expected = content_source_export_files()
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("questions.json"))
        assert manifest["format_version"] == 1 and manifest["content_sources_included"] is True
        for name, data in expected.items():
            assert archive.namelist().count(name) == 1 and archive.read(name) == data
            entry = next(x for x in manifest["files"] if x["name"] == name)
            assert entry == {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        assert all("content_sources" not in row for row in records)
    assert transfer.inspect_library_zip(package) == {"question_count": 2, "image_count": 3, "format_version": 1}


def test_old_share_without_source_documents_still_validates_and_imports(share, tmp_path):
    package, _ = share
    with zipfile.ZipFile(package) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    for name in ("content_sources.json", "CONTENT_SOURCES.txt"):
        entries.pop(name)
    manifest = json.loads(entries["manifest.json"])
    manifest.pop("content_sources_included")
    manifest["files"] = [x for x in manifest["files"] if x["name"] not in {"content_sources.json", "CONTENT_SOURCES.txt"}]
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    assert transfer.inspect_library_zip(package)["question_count"] == 2
    with isolated_db(tmp_path / "old-share.db") as db:
        assert transfer.import_library_zip(db, package, uploads_dir=tmp_path / "uploads")["imported_count"] == 2


@pytest.mark.parametrize("kind", ("missing", "hash", "invalid_json", "invalid_text", "flag", "unlisted"))
def test_share_rejects_invalid_source_document_members_before_import(share, tmp_path, kind):
    package, _ = share
    with zipfile.ZipFile(package) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    if kind == "missing":
        entries.pop("CONTENT_SOURCES.txt")
        manifest["files"] = [x for x in manifest["files"] if x["name"] != "CONTENT_SOURCES.txt"]
    elif kind == "hash":
        next(x for x in manifest["files"] if x["name"] == "content_sources.json")["sha256"] = "0" * 64
    elif kind in {"invalid_json", "invalid_text"}:
        name, data = ("content_sources.json", b"[]") if kind == "invalid_json" else ("CONTENT_SOURCES.txt", b"\xff")
        entries[name] = data
        next(x for x in manifest["files"] if x["name"] == name).update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    elif kind == "flag":
        manifest["content_sources_included"] = False
    else:
        manifest["files"] = [x for x in manifest["files"] if x["name"] != "CONTENT_SOURCES.txt"]
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with isolated_db(tmp_path / "invalid-share.db") as db:
        with pytest.raises(ValueError):
            transfer.import_library_zip(db, package, uploads_dir=tmp_path / "target_uploads")
        assert db.query(Question).count() == 0


def test_roundtrip_preserves_content_and_relations_without_touching_existing(share, tmp_path):
    package, original_uploads = share
    target_uploads = tmp_path / "target_uploads"
    target_uploads.mkdir()
    (target_uploads / "figure.png").write_bytes(b"existing bytes stay intact")
    with isolated_db(tmp_path / "target.db") as db:
        existing = Question(id=50, content="原有题", source="新高掌 | 例1.2", association_group_id="shared-group", usage_count=6)
        platform = KnowledgePoint(code="1015", name="接收端已改名", original_name="平台来源原名", source="zhixue")
        platform.aliases = []
        local = KnowledgePoint(code="local:test", name="本机另一个点", original_name="本机另一个点", source="local")
        local.aliases = []
        db.add_all([existing, platform, local])
        db.commit()
        sync_question_source_labels(db, existing)
        db.commit()
        result = transfer.import_library_zip(db, package, uploads_dir=target_uploads, active_version="B")
        assert result["imported_count"] == 2 and result["image_count"] == 3
        first, second = (db.get(Question, result["id_map"][number]) for number in (1, 2))
        assert first.id > 50 and second.id > first.id
        assert first.content.startswith("<!-- MathBank source attribution:\n新高掌\n-->\n\n")
        assert '`![字面](/static/uploads/figure.png)`' in first.content
        assert first.category_compulsory == "B册" and first.category_knowledge == "B小节"
        assert first.curriculum_mappings[0].sub_knowledge == [{"compulsory": "旧册", "chapter": "旧章", "knowledge": "旧关系证据"}]
        assert first.figure_align == "center" and first.figure_size == "large" and first.figure_align_custom
        assert first.created_at == datetime(2025, 8, 1, 12, 30) and first.usage_count == 0
        assert first.tikz_code == r"\begin{tikzpicture}\end{tikzpicture}"
        assert len(first.image_paths) == 3
        assert all(path.startswith("/static/uploads/shared_") for path in first.image_paths)
        assert first.content_tikz_assets[0]["reference_image_path"] == first.tikz_reference_image_path
        assert first.image_layouts == {Path(first.image_paths[0]).name: {"align": "left", "size": "medium"}}
        for original_name, rewritten in zip(("figure.png", "review.png", "hidden.jpg"), first.image_paths):
            assert (target_uploads / Path(rewritten).name).read_bytes() == (original_uploads / original_name).read_bytes()
        assert first.association_group_id == second.association_group_id != "shared-group"
        assert existing.content == "原有题" and existing.usage_count == 6 and existing.association_group_id == "shared-group"
        assert (target_uploads / "figure.png").read_bytes() == b"existing bytes stay intact"
        assert db.get(KnowledgePoint, "1015").name == "接收端已改名"
        assert db.get(KnowledgePoint, "local:test").name == "本机另一个点"
        assert len(first.knowledge_points) == 2
        assert first.knowledge_points[0].point_code == "1015"
        assert first.knowledge_points[1].point_code.startswith("local:") and first.knowledge_points[1].point_code != "local:test"
        assert first.knowledge_points[1].status == "suggested" and first.knowledge_points[1].reason == '"人工复核"'
        refs = reference_payloads(db, [first])[first.id]
        internal = next(ref for ref in refs if ref["field"] == "content")
        external = next(ref for ref in refs if ref["field"] == "answer_markdown")
        assert internal["status"] == "resolved" and internal["target_question_id"] == second.id
        assert first.content[internal["start"]:internal["end"]] == internal["text"] == "例1.2"
        assert internal["field_sha256"] == hashlib.sha256(first.content.encode()).hexdigest()
        assert external["resolution_state"] == "unresolved" and external["target_question_id"] is None
        assert external["reason"] == "target_outside_share"
        assert db.query(QuestionFingerprint).count() == 2
        assert db.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []
        from mathbank.db_migrations import _validate_knowledge_point_schema, _validate_question_reference_schema
        _validate_knowledge_point_schema(db.connection())
        _validate_question_reference_schema(db.connection())


@pytest.mark.parametrize("kind", [
    "source_book", "source_label", "reference_book", "reference_label", "reference_text",
    "reference_prefix", "reference_suffix", "reference_long_book", "reference_long_label",
    "leaf_without_section", "parent_without_section", "chapter_with_section",
])
def test_share_preflight_rejects_startup_invalid_relations_before_database_or_asset_writes(share, tmp_path, kind):
    package, _ = share
    def damage(records):
        record = records[0]
        if kind.startswith("source_"):
            label = record["source_labels"][0]
            label["book_key" if kind == "source_book" else "label_key"] = " 书名 " if kind == "source_book" else "例1"
        elif kind.startswith("reference_"):
            ref = record["question_references"][0]
            changes = {
                "reference_book": {"book_key": " 书名 "}, "reference_label": {"label_key": "例1"},
                "reference_text": {"text": ref["text"] + "多"}, "reference_prefix": {"prefix": "前" * 25},
                "reference_suffix": {"suffix": "后" * 25}, "reference_long_book": {"book_key": "书" * 201},
                "reference_long_label": {"label_key": "例" + "1." * 50 + "1"},
            }
            ref.update(changes[kind])
        else:
            path = {"version_code": "A", "compulsory": "必修一", "chapter": "历史章",
                    "knowledge": "有小节" if kind == "chapter_with_section" else "", "reference_section": "保留参考",
                    "level": {"leaf_without_section": "leaf", "parent_without_section": "parent", "chapter_with_section": "chapter"}[kind]}
            record["knowledge_points"][0]["curriculum_paths"] = [path]
    rewrite_zip(package, mutate_records=damage)
    with pytest.raises(ValueError):
        transfer.inspect_library_zip(package)
    uploads = tmp_path / "target_uploads"
    uploads.mkdir()
    with isolated_db(tmp_path / "target.db") as db:
        writes = []
        def record_writes(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                writes.append(statement)
        event.listen(db.bind, "before_cursor_execute", record_writes)
        try:
            with pytest.raises(ValueError):
                transfer.import_library_zip(db, package, uploads_dir=uploads)
            assert writes == [] and not db.in_transaction()
            assert list(uploads.iterdir()) == []
        finally:
            event.remove(db.bind, "before_cursor_execute", record_writes)


@pytest.mark.parametrize("receiving_version", ["A", "B", "N"])
@pytest.mark.parametrize("source_mirror", [True, False])
def test_historical_null_textbook_locations_roundtrip_without_rewriting_source(tmp_path, receiving_version, source_mirror):
    from mathbank.db_migrations import _validate_knowledge_point_schema, _validate_question_reference_schema
    package, uploads = tmp_path / "nullable.zip", tmp_path / "source_uploads"
    uploads.mkdir()
    locations = {"A": (None, "历史章", None), "B": ("历史册", None, "历史节")}
    with isolated_db(tmp_path / "source.db") as db:
        question = Question(content="保留原题 $x^2$", category_compulsory="", category_chapter="历史章", category_knowledge="")
        question.curriculum_mappings = [QuestionCurriculum(version_code=version, compulsory="", chapter="", knowledge="")
                                        for version in locations if source_mirror or version != "A"]
        db.add(question)
        db.commit()
        # These NULLs are valid persisted legacy evidence; the ORM normally
        # supplies empty strings when a new row has no location yet.
        connection = db.connection()
        connection.exec_driver_sql("UPDATE questions SET category_compulsory=NULL, category_knowledge=NULL")
        for version, location in locations.items():
            connection.exec_driver_sql("UPDATE question_curriculums SET compulsory=?,chapter=?,knowledge=? WHERE version_code=?",
                                       (*location, version))
        db.commit()
        db.expire_all()
        before = (db.connection().exec_driver_sql("SELECT category_compulsory,category_chapter,category_knowledge FROM questions").all(),
                  db.connection().exec_driver_sql("SELECT version_code,compulsory,chapter,knowledge FROM question_curriculums ORDER BY version_code").all())
        transfer.export_library_zip(db, package, uploads_dir=uploads, active_version="A")
        assert (db.connection().exec_driver_sql("SELECT category_compulsory,category_chapter,category_knowledge FROM questions").all(),
                db.connection().exec_driver_sql("SELECT version_code,compulsory,chapter,knowledge FROM question_curriculums ORDER BY version_code").all()) == before
    assert transfer.inspect_library_zip(package)["question_count"] == 1
    with zipfile.ZipFile(package) as archive:
        record = json.loads(archive.read("questions.json"))[0]
    assert tuple(record["category_" + field] for field in ("compulsory", "chapter", "knowledge")) == locations["A"]
    with isolated_db(tmp_path / "target.db") as db:
        result = transfer.import_library_zip(db, package, uploads_dir=tmp_path / "target_uploads", active_version=receiving_version)
        imported = db.get(Question, next(iter(result["id_map"].values())))
        mirrors = {row.version_code: (row.compulsory, row.chapter, row.knowledge) for row in imported.curriculum_mappings}
        assert imported.content == "保留原题 $x^2$"
        assert mirrors["A"] == locations["A"] and mirrors["B"] == locations["B"]
        expected = locations.get(receiving_version, ("", "", ""))
        assert (imported.category_compulsory, imported.category_chapter, imported.category_knowledge) == expected
        _validate_knowledge_point_schema(db.connection())
        _validate_question_reference_schema(db.connection())
        assert db.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_repeated_import_always_appends_and_never_cross_links(share, tmp_path):
    package, _ = share
    uploads = tmp_path / "target_uploads"
    with isolated_db(tmp_path / "target.db") as db:
        first = transfer.import_library_zip(db, package, uploads_dir=uploads)
        second = transfer.import_library_zip(db, package, uploads_dir=uploads)
        assert db.query(Question).count() == 4 and len(list(uploads.iterdir())) == 6
        assert set(first["id_map"].values()).isdisjoint(second["id_map"].values())
        assert db.get(Question, first["id_map"][1]).association_group_id != db.get(Question, second["id_map"][1]).association_group_id
        refs = db.query(QuestionReference).filter_by(field="content").all()
        assert {(row.source_question_id, row.target_question_id) for row in refs} == {(result["id_map"][1], result["id_map"][2]) for result in (first, second)}


@pytest.mark.parametrize("source_version", ("A", "B", "S", "H", "N", "X"))
def test_builtin_versions_roundtrip_preserves_identity_and_historical_mirrors(tmp_path, source_version):
    versions = ("A", "B", "S", "H", "N", "X")
    target_version = versions[(versions.index(source_version) + 1) % len(versions)]
    uploads = tmp_path / "source_uploads"
    uploads.mkdir()
    package = tmp_path / "six-versions.zip"
    # Stored custom labels are evidence. They need not occur in today's preset.
    paths = {
        version: {"compulsory": f"{version}历史册", "chapter": f"{version}历史章", "knowledge": f"{version}历史小节"}
        for version in versions
    }
    with isolated_db(tmp_path / "source.db") as db:
        point = KnowledgePoint(code="1015", name="保留名称", original_name="平台原名", source="zhixue", aliases=["保留别名"])
        point.curriculum_paths = [KnowledgePointCurriculum(
            version_code=version, **path, reference_section=f"{version}来源末节", level="leaf",
        ) for version, path in paths.items()]
        question = Question(content="六版原题 $x+1$。", answer_markdown="原解析。", review="原评析。", tags="原标签",
            **{f"category_{field}": value for field, value in paths[source_version].items()})
        for version, path in paths.items():
            mapping = QuestionCurriculum(version_code=version, **path)
            mapping.sub_knowledge = [dict(path, knowledge=f"{version}旧关系证据")]
            question.curriculum_mappings.append(mapping)
        question.knowledge_points = [QuestionKnowledgePoint(point=point, role="auxiliary", status="suggested", position=0,
            evidence='"原文依据"', reason='"已存复核理由"')]
        db.add(question)
        db.commit()
        expected_mirrors = {row.version_code: {key: value for key, value in row.to_dict().items()
            if key not in {"id", "question_id"}} for row in question.curriculum_mappings}
        expected_point = point.to_dict()
        assert transfer.export_library_zip(db, package, uploads_dir=uploads, active_version=source_version) == {
            "question_count": 1, "image_count": 0,
        }
    assert transfer.inspect_library_zip(package) == {"question_count": 1, "image_count": 0, "format_version": 1}
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("questions.json"))
    assert manifest["active_version_code"] == records[0]["active_version_code"] == source_version
    with isolated_db(tmp_path / "target.db") as db:
        existing = Question(content="原有题目", category_compulsory="原有册", category_chapter="原有章", category_knowledge="原有小节")
        db.add(existing)
        db.commit()
        result = transfer.import_library_zip(db, package, uploads_dir=tmp_path / "target_uploads", active_version=target_version)
        imported = db.get(Question, next(iter(result["id_map"].values())))
        assert (imported.content, imported.answer_markdown, imported.review, imported.tags) == (
            "六版原题 $x+1$。", "原解析。", "原评析。", "原标签",
        )
        assert {field: getattr(imported, f"category_{field}") for field in paths[target_version]} == paths[target_version]
        assert {row.version_code: {key: value for key, value in row.to_dict().items()
            if key not in {"id", "question_id"}} for row in imported.curriculum_mappings} == expected_mirrors
        assert imported.knowledge_points[0].point.to_dict() == expected_point
        assert (imported.knowledge_points[0].role, imported.knowledge_points[0].status,
                imported.knowledge_points[0].evidence, imported.knowledge_points[0].reason) == (
            "auxiliary", "suggested", '"原文依据"', '"已存复核理由"',
        )
        assert (existing.content, existing.category_compulsory, existing.category_chapter, existing.category_knowledge) == (
            "原有题目", "原有册", "原有章", "原有小节",
        )
        assert db.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_unknown_active_version_rejected_before_installing_rows(share, tmp_path):
    package, uploads = share
    with isolated_db(tmp_path / "unknown-version.db") as db:
        db.add(Question(content="原有题"))
        db.commit()
        with pytest.raises(ValueError, match="教材版本无效"):
            transfer.export_library_zip(db, tmp_path / "invalid.zip", uploads_dir=uploads, active_version="UNKNOWN")
        with pytest.raises(ValueError, match="教材版本无效"):
            transfer.import_library_zip(db, package, uploads_dir=tmp_path / "target_uploads", active_version="UNKNOWN")
        assert db.query(Question).count() == 1 and db.query(KnowledgePoint).count() == 0
    rewrite_zip(package, mutate_manifest=lambda manifest: manifest.update(active_version_code="UNKNOWN"),
                mutate_records=lambda records: [record.update(active_version_code="UNKNOWN") for record in records])
    with pytest.raises(ValueError, match="教材版本无效"):
        transfer.inspect_library_zip(package)


@pytest.mark.parametrize("extra", [("../escape.txt", b"bad"), ("extra.txt", b"extra"), ("/absolute.txt", b"bad"), ("assets\\escape.png", b"bad"), ("MANIFEST.JSON", b"duplicate")])
def test_unlisted_or_unsafe_members_rejected(share, extra):
    package, _ = share
    rewrite_zip(package, extra=extra)
    with pytest.raises(ValueError):
        transfer.inspect_library_zip(package)


def test_zip_symlink_rejected(share):
    package, _ = share
    member = zipfile.ZipInfo("assets/link.png")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr(member, "../../secret")
    with pytest.raises(ValueError, match="不安全"):
        transfer.inspect_library_zip(package)


@pytest.mark.parametrize("kind", ["hash", "format", "missing", "record"])
def test_corrupt_packages_rejected_before_db_mutation(share, tmp_path, kind):
    package, _ = share
    if kind == "hash":
        rewrite_zip(package, mutate_manifest=lambda manifest: manifest["files"][0].update(sha256="0" * 64))
    elif kind == "format":
        rewrite_zip(package, mutate_manifest=lambda manifest: manifest.update(format="some-other-app"))
    elif kind == "missing":
        rewrite_zip(package, omit="assets/000001.png")
    else:
        rewrite_zip(package, mutate_records=lambda records: records[0].update(image_paths=["/static/uploads/../../secret.png"]))
    uploads = tmp_path / "import_uploads"
    with isolated_db(tmp_path / "receiving.db") as db:
        db.add(Question(content="不动的题"))
        db.commit()
        with pytest.raises(ValueError):
            transfer.import_library_zip(db, package, uploads_dir=uploads)
        assert db.query(Question).count() == 1 and list(uploads.iterdir()) == []
        assert db.query(KnowledgePoint).count() == 0


def test_failed_commit_rolls_back_rows_catalog_and_installed_files(share, tmp_path, monkeypatch):
    package, _ = share
    uploads = tmp_path / "receiving_uploads"
    uploads.mkdir()
    sentinel = uploads / "existing.png"
    sentinel.write_bytes(image_bytes("green"))
    verified = []
    original_readback = transfer._import_rows_absent
    def readback(path, ids):
        ids = tuple(ids)
        absent = original_readback(path, ids)
        verified.append((path, len(ids), absent))
        return absent
    monkeypatch.setattr(transfer, "_import_rows_absent", readback)
    with isolated_db(tmp_path / "receiving.db") as db:
        original = Question(id=20, content="已有题", source="已有来源")
        db.add(original)
        db.commit()
        def fail_commit():
            raise RuntimeError("injected commit failure")
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            transfer.import_library_zip(db, package, uploads_dir=uploads)
        assert [(q.id, q.content, q.source) for q in db.query(Question)] == [(20, "已有题", "已有来源")]
        assert db.query(KnowledgePoint).count() == db.query(QuestionReference).count() == db.query(QuestionSourceLabel).count() == db.query(QuestionFingerprint).count() == 0
        assert list(uploads.iterdir()) == [sentinel]
        assert verified == [(tmp_path / "receiving.db", 2, True)]


@pytest.mark.parametrize("failure_mode", ["commit_wrapper", "after_commit_hook"])
def test_exception_after_real_commit_preserves_all_rows_relations_and_images(share, tmp_path, monkeypatch, failure_mode):
    package, source_uploads = share
    uploads = tmp_path / "committed_uploads"
    uploads.mkdir()
    sentinel = uploads / "existing.png"
    sentinel.write_bytes(image_bytes("green"))
    database_path = tmp_path / "committed.db"
    with isolated_db(database_path) as db:
        db.add(Question(id=20, content="原有题"))
        db.commit()
        if failure_mode == "commit_wrapper":
            real_commit = db.commit
            def commit_then_raise():
                real_commit()
                raise OSError("controlled exception after real commit")
            monkeypatch.setattr(db, "commit", commit_then_raise)
        else:
            def after_commit(_session):
                raise OSError("controlled after_commit callback failure")
            event.listen(db, "after_commit", after_commit)
        with pytest.raises(transfer.LibraryImportCommitUncertain, match="核实") as failure:
            transfer.import_library_zip(db, package, uploads_dir=uploads)
        assert failure.value.commit_outcome_unknown is True
        assert isinstance(failure.value.__cause__, OSError)
        # Read the on-disk commit independently: the importing Session may
        # still be in SQLAlchemy's committed state after its hook raised.
        with sqlite3.connect(database_path) as readback:
            assert readback.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 3
            assert readback.execute("SELECT COUNT(*) FROM question_fingerprints").fetchone()[0] == 2
            assert readback.execute("SELECT COUNT(*) FROM question_curriculums").fetchone()[0] == 3
            assert readback.execute("SELECT COUNT(*) FROM question_knowledge_points").fetchone()[0] == 2
            assert readback.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert readback.execute("PRAGMA foreign_key_check").fetchall() == []
            references = [path for row in readback.execute("SELECT image_paths FROM questions WHERE id > 20")
                          for path in json.loads(row[0])]
        assert len(list(uploads.glob("shared_*"))) == 3
        assert all((uploads / Path(reference).name).is_file() for reference in references)
        expected_images = {hashlib.sha256(path.read_bytes()).hexdigest() for path in source_uploads.iterdir()}
        assert {hashlib.sha256(path.read_bytes()).hexdigest() for path in uploads.glob("shared_*")} == expected_images
        assert sentinel.read_bytes() == image_bytes("green")


def test_commit_failure_with_unavailable_independent_readback_keeps_images(share, tmp_path, monkeypatch):
    package, _ = share
    uploads, database_path = tmp_path / "unknown_uploads", tmp_path / "unknown.db"
    real_connect = sqlite3.connect
    attempted = []
    def unavailable_readback(*args, **kwargs):
        if kwargs.get("uri"):
            attempted.append(args[0])
            raise sqlite3.OperationalError("controlled readback unavailable")
        return real_connect(*args, **kwargs)
    with isolated_db(database_path) as db:
        def fail_commit():
            raise OSError("controlled error before SQLite commit")
        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(transfer.sqlite3, "connect", unavailable_readback)
        with pytest.raises(transfer.LibraryImportCommitUncertain):
            transfer.import_library_zip(db, package, uploads_dir=uploads)
        assert attempted == [database_path.as_uri() + "?mode=ro"]
        assert db.query(Question).count() == 0
        # Unreferenced retained files are safer than deleting images when a
        # separately committed result cannot be disproved.
        assert len(list(uploads.glob("shared_*"))) == 3


@pytest.mark.parametrize("stage", ["writing", "committing"])
def test_error_before_commit_call_rolls_back_and_removes_only_new_images(share, tmp_path, stage):
    package, _ = share
    uploads = tmp_path / "before_commit_uploads"
    uploads.mkdir()
    sentinel = uploads / "existing.png"
    sentinel.write_bytes(image_bytes("green"))
    def fail_before_commit(current_stage, **_progress):
        if current_stage == stage:
            raise OSError("controlled error before commit call")
    with isolated_db(tmp_path / "before_commit.db") as db:
        with pytest.raises(OSError, match="before commit call"):
            transfer.import_library_zip(db, package, uploads_dir=uploads, progress=fail_before_commit)
        assert db.query(Question).count() == db.query(KnowledgePoint).count() == 0
        assert list(uploads.iterdir()) == [sentinel]


def test_empty_export_and_literal_source_filter(tmp_path):
    with isolated_db(tmp_path / "filter.db") as db:
        output = tmp_path / "share.zip"
        with pytest.raises(ValueError, match="没有题目"):
            transfer.export_library_zip(db, output, uploads_dir=tmp_path)
        assert not output.exists()
        db.rollback()
        db.add_all([Question(content="A", source="卷%_甲"), Question(content="B", source="卷abcd甲")])
        db.commit()
        result = transfer.export_library_zip(db, output, uploads_dir=tmp_path, source_keywords=["%_"])
        assert result["question_count"] == 1
        with zipfile.ZipFile(output) as archive:
            assert json.loads(archive.read("questions.json"))[0]["content"] == "A"


def test_untrusted_raster_bytes_rejected_even_with_matching_hash(share):
    package, _ = share
    with zipfile.ZipFile(package) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    name = "assets/000001.png"
    entries[name] = b"<svg onload='alert(1)'/>"
    manifest = json.loads(entries["manifest.json"])
    item = next(item for item in manifest["files"] if item["name"] == name)
    item.update(size=len(entries[name]), sha256=hashlib.sha256(entries[name]).hexdigest())
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(package, "w") as archive:
        for member, data in entries.items():
            archive.writestr(member, data)
    with pytest.raises(ValueError, match="栅格图片"):
        transfer.inspect_library_zip(package)


def test_export_question_rows_and_mirrors_share_one_real_snapshot(tmp_path):
    path = tmp_path / "snapshot.db"
    with isolated_db(path) as db:
        db.connection().exec_driver_sql("PRAGMA journal_mode=WAL")
        question = Question(content="快照题", source="快照来源", category_compulsory="必修", category_chapter="章节", category_knowledge="旧小节")
        question.curriculum_mappings = [QuestionCurriculum(version_code="A", compulsory="必修", chapter="章节", knowledge="旧小节")]
        db.add(question)
        db.commit()
        changed = []
        def concurrent_update(_connection, _cursor, statement, _parameters, _context, _many):
            if "from question_curriculums" not in statement.lower() or changed:
                return
            changed.append(True)
            with isolated_db(path) as writer:
                writer.query(Question).update({Question.category_knowledge: "新小节"})
                writer.query(QuestionCurriculum).update({QuestionCurriculum.knowledge: "新小节"})
                writer.commit()
        event.listen(db.bind, "before_cursor_execute", concurrent_update)
        try:
            output = tmp_path / "snapshot.zip"
            transfer.export_library_zip(db, output, uploads_dir=tmp_path)
            assert changed
            with zipfile.ZipFile(output) as archive:
                record = json.loads(archive.read("questions.json"))[0]
            assert record["category_knowledge"] == record["curriculum_mappings"][0]["knowledge"] == "旧小节"
            db.rollback()
            assert db.query(Question).populate_existing().one().category_knowledge == "新小节"
        finally:
            event.remove(db.bind, "before_cursor_execute", concurrent_update)


@pytest.fixture
def selectable_library(tmp_path):
    with isolated_db(tmp_path / "selection.db") as db:
        first_point = KnowledgePoint(code="101", name="函数定义", original_name="函数定义", source="zhixue")
        second_point = KnowledgePoint(code="102", name="集合运算", original_name="集合运算", source="zhixue")
        db.add_all([first_point, second_point])
        questions = []
        for qid, source, chapter, knowledge in (
            (2, "新高掌 | 第一册", "函数", ""),
            (5, "新高掌 | 第二册", "函数", "函数概念"),
            (9, "赵礼显 | 复习", "集合", "集合运算"),
            (12, "高考 | 2024", "函数", "函数概念"),
        ):
            question = Question(id=qid, content=f"题干{qid} $x^2$", source=source,
                category_compulsory="必修一", category_chapter=chapter, category_knowledge=knowledge,
                answer_markdown=f"隐藏答案{qid}", review=f"隐藏评析{qid}", tags=f"标签{qid}",
                tikz_code="不应在候选响应中载入的源码")
            question.curriculum_mappings = [QuestionCurriculum(version_code="A", compulsory="必修一", chapter=chapter, knowledge=knowledge)]
            questions.append(question)
        # Historical paths and the second point's recommendation do not place
        # the question in a different chapter or replace an empty section.
        questions[0].curriculum_mappings[0].sub_knowledge = [
            {"compulsory": "必修一", "chapter": "集合", "knowledge": "集合运算"}]
        questions[0].knowledge_points = [
            QuestionKnowledgePoint(point=first_point, role="main", status="confirmed", position=0, evidence='"私人证据"'),
            QuestionKnowledgePoint(point=second_point, role="auxiliary", status="confirmed", position=1),
        ]
        questions[1].knowledge_points = [
            QuestionKnowledgePoint(point=first_point, role="main", status="suggested", position=0)]
        questions[2].knowledge_points = [
            QuestionKnowledgePoint(point=first_point, role="main", status="confirmed", position=0),
            QuestionKnowledgePoint(point=second_point, role="main", status="confirmed", position=1),
        ]
        db.add_all(questions)
        db.commit()
        yield db


def test_selection_filters_and_preview_share_exact_ids(selectable_library):
    db = selectable_library
    filters = {"source_keywords": ["新高掌", "赵礼显"], "compulsory": "必修一", "chapter": "函数",
               "knowledge_point_codes": ["101"]}
    assert transfer.select_library_question_ids(db, filters=filters) == [2, 5]
    preview = transfer.preview_library_questions(db, filters=filters)
    assert preview["total"] == 2 and [item["id"] for item in preview["items"]] == [2, 5]
    assert preview["items"][1]["knowledge_points"] == [{"code": "101", "name": "函数定义"}]
    assert preview["items"][0]["category_knowledge"] == ""
    # The old relation must not match a current section or chapter.
    assert transfer.select_library_question_ids(db, filters={**filters, "knowledge": "集合运算"}) == []
    assert transfer.select_library_question_ids(db, filters={**filters, "chapter": "集合"}) == [9]
    assert transfer.select_library_question_ids(db, filters={"knowledge_point_codes": ["101", "102"], "knowledge_point_mode": "all"}) == [9]
    assert transfer.select_library_question_ids(db, filters={"knowledge_point_codes": ["102"]}) == [9]
    assert transfer.select_library_question_ids(db, filters={"knowledge": "函数概念"}) == [5, 12]


@pytest.mark.parametrize("search, expected", [
    ("5", [5]), ("隐藏答案9", [9]), ("隐藏评析12", [12]), ("标签2", [2]),
    ("2024", [12]), ("%", []), ("_", []), ("#1", []),
])
def test_candidate_search_is_literal_and_never_guesses_a_sequence(selectable_library, search, expected):
    assert transfer.select_library_question_ids(selectable_library, filters={"search": search}) == expected


def test_preview_is_paginated_and_omits_answer_evidence_and_tikz(selectable_library):
    result = transfer.preview_library_questions(selectable_library, page=2, page_size=1)
    assert (result["total"], result["total_pages"], result["page"]) == (4, 4, 2)
    item = result["items"][0]
    assert item["id"] == 5 and item["seq_num"] == 2 and item["content"] == "题干5 $x^2$"
    assert set(item) == {"id", "seq_num", "content", "source", "question_type", "category_compulsory",
                         "category_chapter", "category_knowledge", "knowledge_points"}
    first = transfer.preview_library_questions(selectable_library, page_size=1)["items"][0]
    assert first["knowledge_points"] == [{"code": "101", "name": "函数定义"}]
    assert not any(secret in json.dumps(result, ensure_ascii=False) for secret in ("隐藏答案", "隐藏评析", "私人证据", "源码"))
    assert transfer.preview_library_questions(selectable_library, page=5, page_size=1)["items"] == []
    with pytest.raises(ValueError, match="最多 20"):
        transfer.preview_library_questions(selectable_library, page_size=21)


def test_selected_preview_preserves_order_and_reports_missing_ids(selectable_library):
    result = transfer.preview_library_questions(selectable_library, question_ids=[12, 100, 2, 99], page_size=1)
    assert [item["id"] for item in result["items"]] == [12]
    assert result["items"][0]["seq_num"] == 4
    assert result["missing_ids"] == [100, 99] and result["total"] == 2
    assert transfer.preview_library_questions(selectable_library, question_ids=[])["total"] == 0
    with pytest.raises(ValueError, match="不能同时"):
        transfer.preview_library_questions(selectable_library, question_ids=[2], filters={})


def test_export_only_checked_ids_in_client_order(selectable_library, tmp_path):
    output = tmp_path / "selected.zip"
    result = transfer.export_library_zip(selectable_library, output, uploads_dir=tmp_path, question_ids=[12, 2])
    assert result == {"question_count": 2, "image_count": 0}
    with zipfile.ZipFile(output) as archive:
        records = json.loads(archive.read("questions.json"))
    assert [record["id"] for record in records] == [12, 2]
    assert records[0]["answer_markdown"] == "隐藏答案12"


@pytest.mark.parametrize("ids", [[], [2, 2], [True], [0], [-1], [2**63], [2, "5"], [2, {}], "2,5"])
def test_export_rejects_invalid_checked_ids(selectable_library, tmp_path, ids):
    with pytest.raises(ValueError, match="正整数 ID"):
        transfer.export_library_zip(selectable_library, tmp_path / "bad.zip", uploads_dir=tmp_path, question_ids=ids)
    assert not (tmp_path / "bad.zip").exists()


def test_missing_checked_id_rejects_export_without_replacing_output(selectable_library, tmp_path):
    output = tmp_path / "selected.zip"
    output.write_bytes(b"previous successful download")
    with pytest.raises(ValueError, match="已不存在"):
        transfer.export_library_zip(selectable_library, output, uploads_dir=tmp_path, question_ids=[2, 999, 12])
    assert output.read_bytes() == b"previous successful download"
    with pytest.raises(ValueError, match="不能同时"):
        transfer.export_library_zip(selectable_library, output, uploads_dir=tmp_path,
                                    question_ids=[2], source_keywords=["新高掌"])


def test_preview_counts_rows_and_sequence_numbers_share_one_snapshot(tmp_path):
    path = tmp_path / "preview_snapshot.db"
    with isolated_db(path) as db:
        db.connection().exec_driver_sql("PRAGMA journal_mode=WAL")
        db.add_all([Question(id=2, content="将被删除的题"), Question(id=9, content="原第二题")])
        db.commit()
        changed = []
        def delete_after_count(_connection, _cursor, statement, _parameters, _context, _many):
            if not statement.lstrip().lower().startswith("select count(") or changed:
                return
            changed.append(True)
            with isolated_db(path) as writer:
                writer.query(Question).filter_by(id=2).delete()
                writer.commit()
        # Run the concurrent deletion after COUNT has read the snapshot.
        event.listen(db.bind, "after_cursor_execute", delete_after_count)
        try:
            result = transfer.preview_library_questions(db, page_size=1, page=2)
            assert changed and result["total"] == 2
            assert result["items"][0]["id"] == 9 and result["items"][0]["seq_num"] == 2
            db.rollback()
            assert transfer.preview_library_questions(db)["items"][0]["seq_num"] == 1
        finally:
            event.remove(db.bind, "after_cursor_execute", delete_after_count)


def test_excessive_compression_ratio_rejected(share):
    package, _ = share
    rewrite_zip(package, extra=("bomb.txt", b"0" * (2 * 1024 * 1024)))
    with pytest.raises(ValueError, match="压缩比例"):
        transfer.inspect_library_zip(package)


@pytest.mark.parametrize("mutation", [
    lambda records: records[0].update(figure_align=[]),
    lambda records: records[0].update(image_paths=[{}]),
    lambda records: records[0]["knowledge_points"][0].update(role=[]),
    lambda records: records[0]["question_references"][0].update(field=[]),
    lambda records: records[0]["content_tikz_assets"][0].update(image_path=[]),
])
def test_wrong_nested_types_rejected_as_user_input_errors(share, mutation):
    package, _ = share
    rewrite_zip(package, mutate_records=mutation)
    with pytest.raises(ValueError):
        transfer.inspect_library_zip(package)


@pytest.mark.parametrize("has_source_mirror", [True, False])
@pytest.mark.parametrize("receiving_version", ("B", "N", "X"))
def test_missing_receiving_textbook_mirror_is_empty_without_losing_source_location(share, tmp_path, has_source_mirror, receiving_version):
    package, _ = share
    def remove_b_mirrors(records):
        for record in records:
            record["curriculum_mappings"] = ([mapping for mapping in record["curriculum_mappings"] if mapping["version_code"] == "A"]
                                             if has_source_mirror else [])
    rewrite_zip(package, mutate_records=remove_b_mirrors)
    with isolated_db(tmp_path / "receiving.db") as db:
        result = transfer.import_library_zip(db, package, uploads_dir=tmp_path / "uploads", active_version=receiving_version)
        first = db.get(Question, result["id_map"][1])
        assert (first.category_compulsory, first.category_chapter, first.category_knowledge) == ("", "", "")
        mirrors = {row.version_code: row for row in first.curriculum_mappings}
        assert mirrors["A"].knowledge == "历史小节" and mirrors["A"].chapter == "自定义章"
        assert mirrors["A"].sub_knowledge == [{"compulsory": "旧册", "chapter": "旧章", "knowledge": "旧关系证据"}]
        assert (mirrors[receiving_version].compulsory, mirrors[receiving_version].chapter, mirrors[receiving_version].knowledge) == ("", "", "")


def test_staging_cleanup_failure_after_commit_keeps_rows_and_images(share, tmp_path, monkeypatch):
    package, _ = share
    uploads = tmp_path / "target_uploads"
    original_factory = transfer.tempfile.TemporaryDirectory
    attempts = []
    def fail_cleanup_factory(*args, **kwargs):
        staging = original_factory(*args, **kwargs)
        real_cleanup = staging.cleanup
        def cleanup():
            real_cleanup()
            attempts.append(True)
            raise PermissionError("simulated post-commit cleanup failure")
        staging.cleanup = cleanup
        return staging
    monkeypatch.setattr(transfer.tempfile, "TemporaryDirectory", fail_cleanup_factory)
    with isolated_db(tmp_path / "target.db") as db:
        result = transfer.import_library_zip(db, package, uploads_dir=uploads)
        assert attempts and result["imported_count"] == db.query(Question).count() == 2
        first = db.get(Question, result["id_map"][1])
        assert len(list(uploads.iterdir())) == 3
        assert all((uploads / Path(path).name).is_file() for path in first.image_paths)


@pytest.mark.parametrize("mutation", [
    lambda records: records[0].update(future_field="must not be silently lost"),
    lambda records: records[0]["knowledge_points"][0].update(future_metadata="unknown"),
    lambda records: records[0]["knowledge_points"][0].update(source="unrecognized-platform"),
    lambda records: records[0]["knowledge_points"][0].update(code="local:pretending-to-be-zhixue"),
])
def test_unknown_fields_or_catalogue_identities_are_rejected(share, mutation):
    package, _ = share
    rewrite_zip(package, mutate_records=mutation)
    with pytest.raises(ValueError):
        transfer.inspect_library_zip(package)


def test_large_chinese_knowledge_evidence_remains_exportable_and_roundtrips(share, tmp_path):
    package, _ = share
    evidence = "证" * 12000  # 12,002 JSON characters, 36,002 UTF-8 bytes.
    rewrite_zip(package, mutate_records=lambda records: records[0]["knowledge_points"][0].update(evidence=evidence))
    assert transfer.inspect_library_zip(package)["question_count"] == 2
    with isolated_db(tmp_path / "evidence.db") as db:
        uploads = tmp_path / "evidence_uploads"
        result = transfer.import_library_zip(db, package, uploads_dir=uploads)
        first = db.get(Question, result["id_map"][1])
        assert json.loads(first.knowledge_points[0].evidence) == evidence
        output = tmp_path / "evidence_export.zip"
        transfer.export_library_zip(db, output, uploads_dir=uploads)
        with zipfile.ZipFile(output) as archive:
            records = json.loads(archive.read("questions.json"))
        assert records[0]["knowledge_points"][0]["evidence"] == evidence


def test_knowledge_metadata_uses_existing_json_character_limit(share):
    package, _ = share
    # Default JSON separators contribute to the editor's existing limit.
    evidence = {"x": "证" * 32760}
    assert len(json.dumps(evidence, ensure_ascii=False)) > 32768
    rewrite_zip(package, mutate_records=lambda records: records[0]["knowledge_points"][0].update(evidence=evidence))
    with pytest.raises(ValueError, match="证据过长"):
        transfer.inspect_library_zip(package)


def test_export_streams_question_json_and_cleans_temporary_files(share, tmp_path, monkeypatch):
    package, _ = share
    with isolated_db(tmp_path / "streaming.db") as db:
        uploads = tmp_path / "streaming_uploads"
        transfer.import_library_zip(db, package, uploads_dir=uploads)
        original_json = transfer._json
        def reject_materialized_question_array(value):
            if isinstance(value, list):
                raise AssertionError("the complete question array must be streamed")
            return original_json(value)
        monkeypatch.setattr(transfer, "_json", reject_materialized_question_array)
        output = tmp_path / "streamed.zip"
        transfer.export_library_zip(db, output, uploads_dir=uploads)
        assert transfer.inspect_library_zip(output)["question_count"] == 2
        assert not list(tmp_path.glob(".mathbank-share-*"))
        # The current full library exceeds the old 64 MiB limit.
        assert transfer.MAX_JSON_BYTES >= 256 * 1024 * 1024


def test_streaming_json_limit_failure_preserves_previous_export(share, tmp_path, monkeypatch):
    package, _ = share
    with isolated_db(tmp_path / "limited.db") as db:
        uploads = tmp_path / "limited_uploads"
        transfer.import_library_zip(db, package, uploads_dir=uploads)
        output = tmp_path / "previous.zip"
        previous = b"previous good export"
        output.write_bytes(previous)
        monkeypatch.setattr(transfer, "MAX_JSON_BYTES", 100)
        with pytest.raises(ValueError, match="上限"):
            transfer.export_library_zip(db, output, uploads_dir=uploads)
        assert output.read_bytes() == previous
        assert not list(tmp_path.glob(".mathbank-share-*"))


def test_export_fsync_uses_writable_handles_for_windows(tmp_path, monkeypatch):
    real_fsync, synced = os.fsync, []
    def windows_flush(descriptor):
        os.write(descriptor, b"")
        synced.append(descriptor)
        real_fsync(descriptor)
    monkeypatch.setattr("mathbank.asset_security.os.fsync", windows_flush)
    output = tmp_path / "中文 空格分享.zip"
    with isolated_db(tmp_path / "source.db") as db:
        db.add(Question(content="隔离题目 $x^2$"))
        db.commit()
        transfer.export_library_zip(db, output, uploads_dir=tmp_path)
    assert len(synced) >= 2
    assert transfer.inspect_library_zip(output)["question_count"] == 1


def test_prepared_import_validates_images_once_and_preserves_original_bytes(share, tmp_path, monkeypatch):
    package, source_uploads = share
    real_validate = transfer.validate_raster_image
    validated, progress = [], []
    def validate(data):
        validated.append(hashlib.sha256(data).hexdigest())
        return real_validate(data)
    monkeypatch.setattr(transfer, "validate_raster_image", validate)
    workspace, uploads = tmp_path / "private", tmp_path / "destination"
    def report(stage, **state):
        progress.append((stage, state))
    with transfer.prepare_library_import(package, workspace_dir=workspace, progress=report) as prepared:
        summary = prepared.summary
        summary["question_count"] = 999
        assert prepared.summary == {"question_count": 2, "image_count": 3, "format_version": 1}
        with isolated_db(tmp_path / "prepared.db") as db:
            result = transfer.import_library_zip(db, package, uploads_dir=uploads, prepared=prepared, progress=report)
            assert result["imported_count"] == db.query(Question).count() == 2
            assert db.query(QuestionFingerprint).count() == 2
    assert len(validated) == 3
    assert sorted(path.read_bytes() for path in uploads.iterdir()) == sorted(path.read_bytes() for path in source_uploads.iterdir())
    assert list(workspace.iterdir()) == []
    assert list(dict.fromkeys(stage for stage, _ in progress)) == [
        "validating", "installing", "writing", "fingerprinting", "committing"]
    assert all(0 <= state["current"] <= state["total"] for _, state in progress)


def test_prepared_import_rejects_forgery_reuse_and_expired_context(share, tmp_path):
    package, _ = share
    with pytest.raises(TypeError):
        transfer.PreparedLibraryImport()
    with isolated_db(tmp_path / "capability.db") as db:
        uploads = tmp_path / "destination"
        for invalid in ({"records": []}, object.__new__(transfer.PreparedLibraryImport)):
            with pytest.raises(ValueError, match="预检"):
                transfer.import_library_zip(db, package, uploads_dir=uploads, prepared=invalid)
        with transfer.prepare_library_import(package, workspace_dir=tmp_path / "private") as prepared:
            transfer.import_library_zip(db, package, uploads_dir=uploads, prepared=prepared)
            with pytest.raises(ValueError, match="已使用"):
                transfer.import_library_zip(db, package, uploads_dir=uploads, prepared=prepared)
        with pytest.raises(ValueError, match="预检"):
            transfer.import_library_zip(db, package, uploads_dir=uploads, prepared=prepared)
        with pytest.raises(ValueError, match="失效"):
            _ = prepared.summary
        assert db.query(Question).count() == 2


@pytest.mark.parametrize("changed", ["archive", "image", "different_path"])
def test_prepared_import_binds_archive_and_staged_image_bytes(share, tmp_path, changed):
    package, _ = share
    workspace, uploads = tmp_path / "private", tmp_path / "destination"
    with transfer.prepare_library_import(package, workspace_dir=workspace) as prepared:
        target = package
        if changed == "archive":
            package.write_bytes(package.read_bytes() + b"changed")
        elif changed == "image":
            image = next(next(workspace.iterdir()).iterdir())
            image.write_bytes(image_bytes("black"))
        else:
            target = tmp_path / "same-bytes.zip"
            target.write_bytes(package.read_bytes())
        with isolated_db(tmp_path / "changed.db") as db:
            with pytest.raises(ValueError, match="变化"):
                transfer.import_library_zip(db, target, uploads_dir=uploads, prepared=prepared)
            assert db.query(Question).count() == db.query(QuestionFingerprint).count() == 0
            assert not uploads.exists() or not list(uploads.iterdir())
    assert not list(workspace.iterdir())


def test_progress_failure_before_commit_rolls_back_rows_and_files(share, tmp_path):
    package, _ = share
    uploads = tmp_path / "destination"
    def fail(stage, **_):
        if stage == "committing":
            raise RuntimeError("progress cancelled the operation")
    with isolated_db(tmp_path / "cancelled.db") as db:
        with pytest.raises(RuntimeError, match="cancelled"):
            transfer.import_library_zip(db, package, uploads_dir=uploads, progress=fail)
        assert db.query(Question).count() == db.query(KnowledgePoint).count() == db.query(QuestionFingerprint).count() == 0
        assert list(uploads.iterdir()) == []


def test_export_progress_and_selected_mirrors_are_bounded(selectable_library, tmp_path):
    statements, phases = [], []
    def collect(_connection, _cursor, statement, parameters, _context, _many):
        if "from question_curriculums" in statement.lower():
            statements.append((statement, parameters))
    event.listen(selectable_library.bind, "before_cursor_execute", collect)
    try:
        transfer.export_library_zip(selectable_library, tmp_path / "selected.zip", uploads_dir=tmp_path,
            question_ids=[2], progress=lambda stage, **_: phases.append(stage))
    finally:
        event.remove(selectable_library.bind, "before_cursor_execute", collect)
    assert statements and all("question_curriculums.question_id IN" in statement for statement, _ in statements)
    assert all(set(parameters) == {2} for _, parameters in statements)
    assert list(dict.fromkeys(phases)) == ["reading", "packaging", "verifying"]


def test_question_array_is_decoded_incrementally_with_utf8_and_exact_syntax(tmp_path, monkeypatch):
    records = [{"id": 1, "content": "证" * 50000 + '\\"数组[]对象{}'}, {"id": 2, "content": "尾题"}]
    raw = json.dumps(records, ensure_ascii=False).encode("utf-8")
    archive_path = tmp_path / "large-json.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("questions.json", raw)
    with zipfile.ZipFile(archive_path) as archive:
        real_open = archive.open
        def bounded_open(*args, **kwargs):
            stream = real_open(*args, **kwargs)
            real_read = stream.read
            def bounded_read(size=-1):
                assert 0 < size <= 64 * 1024
                return real_read(size)
            stream.read = bounded_read
            return stream
        monkeypatch.setattr(archive, "open", bounded_open)
        assert transfer._read_question_records(archive, hashlib.sha256(raw).hexdigest()) == records


@pytest.mark.parametrize("raw", [
    b'[{"id":1,"id":2}]', b'[{"id":1,"nested":{"a":1,"a":2}}]',
    b'[{"id":NaN}]', b'[{"id":Infinity}]', b'[{"id":1},]', b'[{"id":1}',
    b'[{"id":1}] trailing', b'{"id":1}', b'[1]', b'[{"content":"\xff"}]',
])
def test_incremental_question_json_rejects_ambiguous_or_malformed_data(tmp_path, raw):
    archive_path = tmp_path / "bad-json.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("questions.json", raw)
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError):
            transfer._read_question_records(archive, hashlib.sha256(raw).hexdigest())


def test_image_layout_rewrites_reuse_package_mapping_and_skip_empty_layouts():
    from mathbank.question_assets import rewrite_image_layout_paths
    class NoRepeatedMapping(dict):
        def items(self):
            raise AssertionError("do not rebuild the package mapping per question")
    replacements = NoRepeatedMapping({"/static/uploads/figure.png": "/static/uploads/shared.png"})
    assert rewrite_image_layout_paths({}, replacements) == {}
    assert rewrite_image_layout_paths({"figure.png": {"align": "left", "size": "small"}}, replacements,
        image_keys={"figure.png": "shared.png"}) == {"shared.png": {"align": "left", "size": "small"}}
