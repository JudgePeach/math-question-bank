"""Sharing API contracts; run only in a disposable source-only checkout.

Importing ``main`` initializes local storage. As with the image lifecycle API
tests, the containing source checkout must not contain a real database,
uploads, retained files or credentials. Requests below additionally use their
own file SQLite database and independent connections, including real backups.
"""

import io
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mathbank import database, sync_helper
from mathbank.backup import verify_full_backup
from mathbank.database import (
    Base, KnowledgePoint, Paper, Question, QuestionCurriculum,
    QuestionKnowledgePoint, get_db,
)
from mathbank.db_migrations import LATEST_SCHEMA_VERSION


def _png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def transfer_api(tmp_path, monkeypatch):
    import main

    database_path = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    backups = tmp_path / "data_backup"
    generated = tmp_path / ".system_generated"
    uploads.mkdir()
    backups.mkdir()
    (backups / "custom_metadata.json").write_text('{"version_code":"A"}', encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=not-for-sharing\n", encoding="utf-8")
    (uploads / "figure.png").write_bytes(_png_bytes())
    # An unreferenced upload must not become part of a sharing package.
    (uploads / "unreferenced.png").write_bytes(_png_bytes())
    file_engine = create_engine(
        f"sqlite:///{database_path}", connect_args={"check_same_thread": False}
    )
    assert database.configure_sqlite_wal(file_engine) == "wal"
    Base.metadata.create_all(file_engine)
    with file_engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=file_engine)
    for module in (database, main):
        monkeypatch.setattr(module, "engine", file_engine)
    for module in (database, sync_helper):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    monkeypatch.setattr(main, "DATA_BACKUP_DIR", backups)
    monkeypatch.setattr(main, "SYSTEM_GENERATED_DIR", generated)
    monkeypatch.setattr(main, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(main, "UPLOAD_DIR_REL", "static/uploads")
    monkeypatch.setattr(main, "get_active_version_code", lambda: "A")
    monkeypatch.setattr(sync_helper, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(sync_helper, "JSON_BACKUP_PATH", str(backups / "questions.json"))
    monkeypatch.setattr(sync_helper, "MD_BACKUP_PATH", str(backups / "questions.md"))

    def independent_session():
        with sessions() as session:
            yield session

    previous_override = main.app.dependency_overrides.get(get_db)
    main.app.dependency_overrides[get_db] = independent_session
    # Avoid lifespan maintenance, which is unrelated to these endpoint tests.
    client = TestClient(main.app)
    headers = {"X-Local-Token": main.LOCAL_TOKEN}
    try:
        yield SimpleNamespace(
            client=client, headers=headers, sessions=sessions, uploads=uploads,
            backups=backups, generated=generated, main=main,
        )
    finally:
        client.close()
        if previous_override is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = previous_override
        file_engine.dispose()


def _seed(api, source="新高掌 | 测试例题", *, with_image=True):
    reference = "/static/uploads/figure.png"
    content = "保留原题 $x^2+1$。"
    if with_image:
        content += f"\n![示意图]({reference})\n图后原文。"
    with api.sessions() as session:
        question = Question(
            content=content, question_type="detailed_answer", difficulty="normal",
            source=source, answer_markdown="原解析 $x=1$。", review="原评析。",
            category_compulsory="必修第一册", category_chapter="第一章 集合与常用逻辑用语",
            category_knowledge="历史小节值", tags="标签甲,标签乙", figure_align="center",
            figure_size="medium", figure_align_custom=True,
        )
        question.image_paths = [reference] if with_image else []
        if with_image:
            question.image_layouts = {reference: {"align": "left", "size": "small"}}
        question.curriculum_mappings.append(QuestionCurriculum(
            version_code="A", compulsory=question.category_compulsory,
            chapter=question.category_chapter, knowledge=question.category_knowledge,
        ))
        session.add(question)
        session.commit()
        return question.id


def _export(api, keywords=None):
    response = api.client.post(
        "/api/library-transfer/export", json={"source_keywords": keywords or []},
        headers=api.headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["cache-control"] == "no-store"
    return response.content


def _import(api, data, filename="MathBank.zip"):
    return api.client.post(
        "/api/library-transfer/import", headers=api.headers,
        files={"file": (filename, data, "application/zip")},
    )


def _question_count(api):
    with api.sessions() as session:
        return session.query(Question).count()


def _assert_no_backup_or_transfer_residue(api):
    assert not list(api.backups.rglob("*.zip"))
    assert not api.generated.exists() or not list(api.generated.rglob("library.zip"))
    transfer_root = api.generated / "library_transfer"
    assert not transfer_root.exists() or not list(transfer_root.iterdir())


def test_transfer_endpoints_require_local_token(transfer_api):
    api = transfer_api
    export = api.client.post("/api/library-transfer/export", json={"source_keywords": []})
    imported = api.client.post(
        "/api/library-transfer/import", files={"file": ("library.zip", b"bad", "application/zip")}
    )
    preview = api.client.post("/api/library-transfer/preview", json={"filters": {}})
    selected = api.client.post("/api/library-transfer/select-ids", json={"filters": {}})
    assert export.status_code == imported.status_code == preview.status_code == selected.status_code == 403
    assert _question_count(api) == 0
    _assert_no_backup_or_transfer_residue(api)


def _seed_selection_questions(api):
    """Small catalog with one counterexample for each independent filter."""
    ids = [_seed(api, with_image=False) for _ in range(8)]
    with api.sessions() as session:
        session.add_all([
            KnowledgePoint(code="local:share-a", name="集合运算", original_name="集合运算", source="local"),
            KnowledgePoint(code="local:share-b", name="命题判断", original_name="命题判断", source="local"),
        ])
        session.flush()
        for qid in ids:
            session.get(Question, qid).content = "共同题干 $A\\cap B$。"
        session.get(Question, ids[3]).category_chapter = "第二章 一元二次函数、方程和不等式"
        session.get(Question, ids[4]).source = "赵礼显 | 测试例题"
        session.get(Question, ids[5]).category_knowledge = "另一个小节"
        session.get(Question, ids[6]).content = "无关键词的另一道题。"
        session.get(Question, ids[7]).category_compulsory = "选择性必修第一册"
        for index, qid in enumerate(ids):
            session.add(QuestionKnowledgePoint(
                question_id=qid, point_code="local:share-a", role="main",
                status="suggested" if index == 2 else "confirmed", position=0,
                evidence=json.dumps({"private": "不得随轻量预览返回的标注依据"}, ensure_ascii=False),
            ))
        for index, role in ((0, "main"), (1, "auxiliary")):
            session.add(QuestionKnowledgePoint(
                question_id=ids[index], point_code="local:share-b", role=role,
                status="confirmed", position=1,
            ))
        session.commit()
    return ids


def _selection_filters(**overrides):
    return {
        "search": "共同题干", "source_keywords": ["新高掌"],
        "compulsory": "必修第一册", "chapter": "第一章 集合与常用逻辑用语",
        "knowledge": "历史小节值", "knowledge_point_codes": ["local:share-a"],
        "knowledge_point_mode": "any", **overrides,
    }


def test_export_preview_filters_are_combined_and_pagination_matches_select_ids(transfer_api):
    api = transfer_api
    ids = _seed_selection_questions(api)
    filters = _selection_filters()
    selected = api.client.post(
        "/api/library-transfer/select-ids", json={"filters": filters}, headers=api.headers,
    )
    assert selected.status_code == 200, selected.text
    selection = selected.json()
    assert selection["total"] == 3
    assert set(selection["ids"]) == set(ids[:3])
    page_ids = []
    for page in (1, 2, 3):
        response = api.client.post(
            "/api/library-transfer/preview",
            json={"filters": filters, "page": page, "page_size": 1}, headers=api.headers,
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total"] == result["total_pages"] == 3
        assert result["page"] == page and result["missing_ids"] == []
        assert len(result["items"]) == 1
        item = result["items"][0]
        page_ids.append(item["id"])
        assert item["seq_num"] == ids.index(item["id"]) + 1
        assert item["content"] == "共同题干 $A\\cap B$。"
        assert item["source"].startswith("新高掌")
        assert item["category_knowledge"] == "历史小节值"
        assert {"answer_markdown", "review", "tikz_code", "curriculum_mappings"}.isdisjoint(item)
        assert {point["code"] for point in item["knowledge_points"]} >= {"local:share-a"}
        assert all(point["name"] for point in item["knowledge_points"])
        assert "不得随轻量预览返回的标注依据" not in response.text
        assert all({"evidence", "scope", "reason", "origin", "evidence_source"}.isdisjoint(point)
                   for point in item["knowledge_points"])
    assert page_ids == selection["ids"]
    assert _question_count(api) == 8
    _assert_no_backup_or_transfer_residue(api)


@pytest.mark.parametrize("mode,expected_indexes", [("any", {0, 1, 2}), ("all", {0})])
def test_export_selection_knowledge_points_use_all_main_any_or_all(transfer_api, mode, expected_indexes):
    api = transfer_api
    ids = _seed_selection_questions(api)
    response = api.client.post(
        "/api/library-transfer/select-ids",
        json={"filters": _selection_filters(
            knowledge_point_codes=["local:share-a", "local:share-b"], knowledge_point_mode=mode,
        )}, headers=api.headers,
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["ids"]) == {ids[index] for index in expected_indexes}


def test_selected_preview_keeps_requested_order_and_reports_missing_ids(transfer_api):
    api = transfer_api
    ids = [_seed(api, f"来源 {index}", with_image=False) for index in range(3)]
    missing_id = max(ids) + 100
    requested = [ids[2], missing_id, ids[0]]
    response = api.client.post(
        "/api/library-transfer/preview", json={"question_ids": requested, "page": 1, "page_size": 10},
        headers=api.headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["id"] for item in result["items"]] == [ids[2], ids[0]]
    assert result["missing_ids"] == [missing_id]
    assert result["total"] == 2
    assert [item["seq_num"] for item in result["items"]] == [3, 1]


def test_selection_search_finds_source_and_empty_selected_view_never_means_all(transfer_api):
    api = transfer_api
    qid = _seed(api, "赵礼显 | 第一章", with_image=False)
    found = api.client.post(
        "/api/library-transfer/select-ids", json={"filters": {"search": "赵礼显"}}, headers=api.headers,
    )
    assert found.status_code == 200, found.text
    assert found.json()["ids"] == [qid]
    preview = api.client.post(
        "/api/library-transfer/preview", json={"question_ids": [], "page": 1, "page_size": 10},
        headers=api.headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["items"] == []
    assert preview.json()["total"] == 0
    assert preview.json()["missing_ids"] == []


def test_export_uses_exact_selected_id_order_without_filter_reselection(transfer_api):
    api = transfer_api
    ids = [_seed(api, f"不同来源 {index}", with_image=False) for index in range(3)]
    requested = [ids[2], ids[0]]
    response = api.client.post(
        "/api/library-transfer/export", json={"question_ids": requested}, headers=api.headers,
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        records = json.loads(package.read("questions.json"))
        assert [record["id"] for record in records] == requested
        assert json.loads(package.read("manifest.json"))["question_count"] == 2
    assert _question_count(api) == 3
    _assert_no_backup_or_transfer_residue(api)


@pytest.mark.parametrize("invalid_selection", [
    "empty", "duplicate", "missing", "zero", "negative", "string", "float", "bool", "not_list", "null",
])
def test_export_rejects_invalid_or_stale_explicit_selection_without_partial_zip(transfer_api, invalid_selection):
    api = transfer_api
    qid = _seed(api, with_image=False)
    selections = {
        "empty": [], "duplicate": [qid, qid], "missing": [qid, qid + 100],
        "zero": [qid, 0], "negative": [qid, -1], "string": [str(qid)],
        "float": [float(qid)], "bool": [True], "not_list": str(qid), "null": None,
    }
    response = api.client.post(
        "/api/library-transfer/export", json={"question_ids": selections[invalid_selection]}, headers=api.headers,
    )
    assert response.status_code == 400, response.text
    assert not response.headers["content-type"].startswith("application/zip")
    assert _question_count(api) == 1
    _assert_no_backup_or_transfer_residue(api)


@pytest.mark.parametrize("keywords,selected", [([], {0, 1, 2}), (["新高掌", "赵礼显"], {0, 1}), (["%_"], {2})])
def test_export_filters_literal_sources_and_includes_only_referenced_assets(transfer_api, keywords, selected):
    api = transfer_api
    ids = [
        _seed(api, "新高掌 | 第一章"),
        _seed(api, "赵礼显 | 第一章", with_image=False),
        _seed(api, "高考%_字面来源", with_image=False),
    ]
    # A LIKE-wildcard interpretation of %_ would incorrectly select this row.
    excluded_id = _seed(api, "高考XY字面来源", with_image=False)
    if not keywords:
        selected = {*selected, 3}
        ids.append(excluded_id)
    with api.sessions() as session:
        session.add(Paper(title="私人组卷不得分享"))
        session.commit()
    archive = _export(api, keywords)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        manifest = json.loads(package.read("manifest.json"))
        records = json.loads(package.read("questions.json"))
        assert manifest["format"] == "mathbank-library-transfer"
        assert manifest["question_count"] == len(selected)
        assert {record["id"] for record in records} == {ids[index] for index in selected}
        names = set(package.namelist())
        expected_images = 1 if 0 in selected else 0
        assert manifest["image_count"] == expected_images
        assert len([name for name in names if name.startswith("assets/")]) == expected_images
        assert names == {"manifest.json", *(entry["name"] for entry in manifest["files"])}
        assert not any(".env" in name or "paper" in name or "unreferenced" in name for name in names)
        assert b"not-for-sharing" not in package.read("questions.json")
        assert b"papers" not in package.read("questions.json")
    _assert_no_backup_or_transfer_residue(api)


def test_import_appends_new_ids_repeatedly_preserves_content_and_verifiable_backup(transfer_api):
    api = transfer_api
    original_id = _seed(api)
    archive = _export(api)
    source_bytes = (api.uploads / "figure.png").read_bytes()
    original_reference = "/static/uploads/figure.png"
    imported_references = set()
    for previous_count in (1, 2):
        response = _import(api, archive)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "success"
        assert result["imported_count"] == result["image_count"] == 1
        backup_path = Path(result["backup_path"])
        assert backup_path.parent == api.backups / "snapshots"
        manifest = verify_full_backup(backup_path)
        assert manifest["database"]["row_counts"]["questions"] == previous_count
        assert manifest["metadata_included"] is True
        assert manifest["upload_file_count"] == previous_count
        with api.sessions() as session:
            assert session.query(Question).count() == previous_count + 1
            original = session.get(Question, original_id)
            newest = session.query(Question).order_by(Question.id.desc()).first()
            assert newest.id != original_id
            assert newest.image_paths[0] != original_reference
            assert newest.image_paths[0] not in imported_references
            imported_references.add(newest.image_paths[0])
            assert newest.content == original.content.replace(original_reference, newest.image_paths[0])
            for field in ("answer_markdown", "review", "source", "tags", "question_type", "difficulty",
                          "category_compulsory", "category_chapter", "category_knowledge",
                          "figure_align", "figure_size", "figure_align_custom"):
                assert getattr(newest, field) == getattr(original, field)
            assert newest.image_layouts == {Path(newest.image_paths[0]).name: {"align": "left", "size": "small"}}
            assert newest.curriculum_mappings[0].knowledge == original.category_knowledge
            assert original.image_paths == [original_reference]
            assert (api.uploads / Path(newest.image_paths[0]).name).read_bytes() == source_bytes
        assert (api.uploads / "figure.png").read_bytes() == source_bytes
    assert len(list((api.backups / "snapshots").glob("*.zip"))) == 2
    assert not list((api.generated / "library_transfer").iterdir())


def _malformed_package(archive, kind):
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    if kind == "traversal":
        files["../../outside.png"] = _png_bytes()
    elif kind == "checksum":
        manifest = json.loads(files["manifest.json"])
        manifest["files"][0]["sha256"] = "0" * 64
        files["manifest.json"] = json.dumps(manifest).encode()
    elif kind == "wrong_format":
        manifest = json.loads(files["manifest.json"])
        manifest["format"] = "unrelated-zip-file"
        files["manifest.json"] = json.dumps(manifest).encode()
    elif kind in {"source_label", "reference_span", "point_path"}:
        records = json.loads(files["questions.json"])
        record = records[0]
        if kind == "source_label":
            record["source_labels"] = [{"book_key": "测试书", "label_key": "例1", "original_label": "例1"}]
        elif kind == "reference_span":
            record["question_references"] = [{"field": "content", "start": 0, "end": 1, "text": "两个字",
                "label_key": "例1.1", "book_key": "测试书", "field_sha256": hashlib.sha256(record["content"].encode()).hexdigest(),
                "prefix": "", "suffix": "", "resolution_state": "unresolved", "reason": ""}]
        else:
            record["knowledge_points"] = [{"code": "local:invalid-share-path", "name": "测试知识点", "original_name": "测试知识点",
                "source": "local", "aliases": [], "role": "main", "status": "confirmed", "curriculum_paths": [{
                    "version_code": "A", "compulsory": "测试册", "chapter": "测试章", "knowledge": "", "reference_section": "", "level": "leaf"}]}]
        files["questions.json"] = json.dumps(records, ensure_ascii=False).encode()
        manifest = json.loads(files["manifest.json"])
        entry = next(item for item in manifest["files"] if item["name"] == "questions.json")
        entry.update(size=len(files["questions.json"]), sha256=hashlib.sha256(files["questions.json"]).hexdigest())
        files["manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as destination:
        for name, data in files.items():
            destination.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["not_zip", "wrong_extension", "traversal", "checksum", "wrong_format", "source_label", "reference_span", "point_path"])
def test_invalid_packages_fail_before_backup_or_destination_writes(transfer_api, kind):
    api = transfer_api
    _seed(api)
    archive = _export(api)
    before = {path.name: path.read_bytes() for path in api.uploads.iterdir()}
    filename = "library.json" if kind == "wrong_extension" else "library.zip"
    data = b"this is not a ZIP archive" if kind == "not_zip" else archive
    if kind not in {"not_zip", "wrong_extension"}:
        data = _malformed_package(archive, kind)
    response = _import(api, data, filename)
    assert response.status_code == 400, response.text
    assert _question_count(api) == 1
    assert {path.name: path.read_bytes() for path in api.uploads.iterdir()} == before
    _assert_no_backup_or_transfer_residue(api)


def test_oversized_upload_is_rejected_before_backup(transfer_api, monkeypatch):
    api = transfer_api
    monkeypatch.setattr(api.main, "MAX_LIBRARY_ARCHIVE_BYTES", 32)
    response = _import(api, b"x" * 33)
    assert response.status_code == 413, response.text
    assert _question_count(api) == 0
    _assert_no_backup_or_transfer_residue(api)


def test_backup_failure_prevents_import_and_image_installation(transfer_api, monkeypatch):
    api = transfer_api
    _seed(api)
    archive = _export(api)
    before = {path.name: path.read_bytes() for path in api.uploads.iterdir()}

    def unavailable_backup(**kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(api.main, "create_full_backup", unavailable_backup)
    response = _import(api, archive)
    assert response.status_code == 500, response.text
    assert "备份失败" in response.json()["detail"]
    assert _question_count(api) == 1
    assert {path.name: path.read_bytes() for path in api.uploads.iterdir()} == before
    _assert_no_backup_or_transfer_residue(api)
