"""Agent reads use temporary SQLite files and never initialize the live app."""

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from PIL import Image

from mathbank.agent_reads import AgentReader


SCHEMA = """
CREATE TABLE questions (
 id INTEGER PRIMARY KEY,display_order INTEGER,content TEXT NOT NULL,
 question_type TEXT DEFAULT 'detailed_answer',category_compulsory TEXT DEFAULT '',
 category_chapter TEXT DEFAULT '',category_knowledge TEXT DEFAULT '',difficulty TEXT DEFAULT 'normal',
 source TEXT DEFAULT '',answer_markdown TEXT DEFAULT '',review TEXT DEFAULT '',
 association_group_id TEXT DEFAULT '',image_paths TEXT DEFAULT '[]',
 tikz_code TEXT DEFAULT '',tikz_reference_image_path TEXT DEFAULT '',
 content_tikz_assets TEXT DEFAULT '[]',answer_tikz_assets TEXT DEFAULT '[]',
 figure_align TEXT DEFAULT 'right',figure_align_custom INTEGER DEFAULT 0,figure_size TEXT DEFAULT 'auto',
 image_layouts TEXT DEFAULT '{}',tags TEXT DEFAULT '',usage_count INTEGER DEFAULT 0,created_at TEXT DEFAULT '2026-01-01'
);
CREATE TABLE knowledge_points(code TEXT PRIMARY KEY,name TEXT,original_name TEXT,source TEXT,aliases TEXT);
CREATE TABLE question_knowledge_points(id INTEGER PRIMARY KEY,question_id INTEGER,point_code TEXT,
 role TEXT,status TEXT,position INTEGER DEFAULT 0,scope TEXT,evidence_source TEXT,evidence TEXT,reason TEXT,origin TEXT);
CREATE TABLE question_curriculums(id INTEGER PRIMARY KEY,question_id INTEGER,version_code TEXT,
 compulsory TEXT,chapter TEXT,knowledge TEXT);
CREATE TABLE knowledge_point_curriculums(id INTEGER PRIMARY KEY,point_code TEXT,version_code TEXT,
 compulsory TEXT,chapter TEXT,knowledge TEXT,reference_section TEXT DEFAULT '',level TEXT DEFAULT 'leaf');
CREATE TABLE question_source_labels(id INTEGER PRIMARY KEY,question_id INTEGER,book_key TEXT,label_key TEXT,original_label TEXT);
CREATE TABLE question_references(id INTEGER PRIMARY KEY,source_question_id INTEGER,target_question_id INTEGER,
 field TEXT,start INTEGER,end INTEGER,text TEXT,label_key TEXT,book_key TEXT,field_sha256 TEXT,
 prefix TEXT,suffix TEXT,resolution_state TEXT,reason TEXT);
"""


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "题库 #1.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO questions(id,content,source,answer_markdown,review,association_group_id) VALUES(?,?,?,?,?,?)", [
        (3, "证明 x_y 是函数", "讲义A", "完整答案3", "必须读取的原书错印说明", "group"),
        (9, "证明 xay 是函数", "讲义B", "完整答案9", "", "group"),
        (12, "100%概率", "题集", "", "", ""),
    ])
    db.executemany("INSERT INTO knowledge_points VALUES(?,?,?,?,?)", [
        ("p1", "判断单调性", "判断或证明函数的单调性", "local", '["函数递增"]'),
        ("p2", "解不等式", "利用单调性解不等式", "local", "[]"),
    ])
    audit = json.dumps({"trace": "x" * 100_000})
    for qid, code, role, status in ((3, "p1", "main", "confirmed"), (3, "p2", "main", "confirmed"),
                                    (9, "p1", "auxiliary", "suggested"), (12, "p2", "main", "confirmed")):
        db.execute("INSERT INTO question_knowledge_points(question_id,point_code,role,status,scope,evidence_source,evidence,reason,origin) VALUES(?,?,?,?,?,?,?,?,?)",
                   (qid, code, role, status, "null", '"原题"', '"逐字证据"', '"标注依据"', audit))
    db.execute("INSERT INTO question_curriculums VALUES(1,3,'B','B册','B章','B节')")
    db.execute("INSERT INTO knowledge_point_curriculums(point_code,version_code,compulsory,chapter,knowledge) VALUES('p1','B','B册','B章','B节')")
    db.commit()
    db.close()
    uploads = tmp_path / "static" / "uploads"
    uploads.mkdir(parents=True)
    return path, uploads


def reader(library, **kwargs):
    return AgentReader(library[0], uploads_dir=library[1], **kwargs)


def test_module_import_does_not_load_database_or_create_persistent_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = "import sys;sys.path.insert(0,sys.argv[1]);import mathbank.agent_reads;assert 'mathbank.database' not in sys.modules"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError), AgentReader(missing):
        pass
    assert not missing.exists()


def test_read_only_and_real_snapshot(library):
    db = sqlite3.connect(library[0])
    db.execute("PRAGMA journal_mode=WAL")
    with reader(library) as value:
        assert value.capabilities()["total_questions"] == 3
        with pytest.raises(sqlite3.OperationalError):
            value.connection.execute("UPDATE questions SET content='bad'")
        db.execute("INSERT INTO questions(id,content) VALUES(99,'并发新增')")
        db.commit()
        assert value.capabilities()["total_questions"] == 3
        assert value.get_questions([99])["missing_ids"] == [99]
    with reader(library) as value:
        assert value.capabilities()["total_questions"] == 4
        assert value.get_questions([3])["items"][0]["content"] == "证明 x_y 是函数"
    db.close()


def test_literal_search_and_pagination(library):
    before = hashlib.sha256(library[0].read_bytes()).hexdigest()
    with reader(library) as value:
        assert [q["id"] for q in value.search(query="x_y")["items"]] == [3]
        assert [q["id"] for q in value.search(query="100%")["items"]] == [12]
        page = value.search(page_size=1, sort="id_asc")
        assert page["total"] == 3 and len(page["items"]) == 1
        assert page["next_page"] == 2 and page["truncated"]
        assert page["items"][0]["id"] == 3
        assert value.search(page=20, page_size=1)["items"] == []
        assert value.search(source="讲义A")["total"] == 1
    assert hashlib.sha256(library[0].read_bytes()).hexdigest() == before


def test_knowledge_name_original_alias_and_any_all_roles(library):
    with reader(library) as value:
        for query in ("判断单调性", "判断或证明", "函数递增", "#p1"):
            points = value.knowledge_points(query=query)
            assert points["total"] == 1 and points["items"][0]["code"] == "p1"
        results = value.search(knowledge_query="函数递增")
        assert [item["id"] for item in results["items"]] == [3]
        assert results["resolved_knowledge_points"] == [{"code": "p1", "name": "判断单调性"}]
        assert results["items"][0]["match_reasons"][0]["code"] == "p1"
        assert value.search(knowledge_point_codes=["p1"], knowledge_point_role="all")["total"] == 2
        assert value.search(knowledge_point_codes=["p1"], knowledge_point_role="all", knowledge_point_status="confirmed")["total"] == 1
        assert value.search(knowledge_point_codes=["p1", "p2"])["total"] == 2
        assert value.search(knowledge_point_codes=["p1", "p2"], knowledge_point_mode="all")["total"] == 1
        assert value.search(knowledge_query="没有这个知识点")["total"] == 0


def test_shipped_source_code_aliases_resolve_to_the_existing_identity(library):
    db = sqlite3.connect(library[0])
    db.execute("UPDATE knowledge_points SET code='1228' WHERE code='p1'")
    db.execute("UPDATE question_knowledge_points SET point_code='1228' WHERE point_code='p1'")
    db.commit()
    db.close()
    with reader(library) as value:
        assert value.knowledge_points(query="#1229")["items"][0]["code"] == "1228"
        page = value.search(knowledge_point_codes=["1229"])
        assert [item["id"] for item in page["items"]] == [3]
        assert page["items"][0]["match_reasons"][0]["code"] == "1228"
        assert value.search(knowledge_query="#1229")["resolved_knowledge_points"][0]["code"] == "1228"


@pytest.mark.parametrize("code", ["", " ", "\t\n", None, False, 0, 1136])
def test_invalid_explicit_point_code_never_broadens_the_query(library, code):
    with reader(library) as value:
        with pytest.raises(ValueError, match="知识点编码必须是非空文本"):
            value.search(knowledge_point_codes=[code])
        with pytest.raises(ValueError, match="知识点编码必须是非空文本"):
            value.search(knowledge_point_codes=["p1", code])
        assert value.search(knowledge_point_codes=None)["total"] == 3


def test_explicit_curriculum_read_does_not_switch_active_version(library):
    with reader(library, active_version_code="A") as value:
        assert value.search(compulsory="B册")["total"] == 0
        result = value.search(compulsory="B册", version_code="B", profile="teaching")
        assert result["total"] == 1 and result["active_version_code"] == "A"
        assert result["items"][0]["category_compulsory"] == "B册"
        assert value.get_questions([9], version_code="B")["items"][0]["curriculum_mapping_found"] is False
        assert value.knowledge_points(version_code="B", compulsory="B册")["total"] == 1


def test_profiles_preserve_selected_question_and_hide_large_evidence(library):
    with reader(library) as value:
        statements = []
        value.connection.set_trace_callback(statements.append)
        candidate = value.get_questions([3], profile="candidate")["items"][0]
        assert "content" not in candidate and "answer_markdown" not in candidate and "review" not in candidate
        assert candidate["has_review"] and candidate["has_answer"]
        assert candidate["quality"]["answer_verification"] == "unknown"
        assert not any("r.origin" in query for query in statements)
        teaching = value.get_questions([3], include_answers=False)["items"][0]
        assert teaching["content"] == "证明 x_y 是函数"
        assert teaching["review"] == "必须读取的原书错印说明"
        assert "answer_markdown" not in teaching
        assert "origin" not in teaching["knowledge_points"][0]
        full = value.get_questions([3], profile="evidence")["items"][0]
        assert full["answer_markdown"] == "完整答案3"
        assert full["knowledge_points"][0]["origin"] == {"trace": "x" * 100_000}
        assert len(json.dumps(teaching)) < 10_000 < len(json.dumps(full))
        assert teaching["revision_sha256"] == full["revision_sha256"]


def test_batch_missing_validation_and_related_status(library):
    with reader(library) as value:
        result = value.get_questions([9, 999, 3])
        assert [item["id"] for item in result["items"]] == [9, 3]
        assert result["requested_ids"] == [9, 999, 3] and result["missing_ids"] == [999]
        for ids in ([], [True], [0], ["3"], [3, 3], list(range(1, 102))):
            with pytest.raises(ValueError):
                value.get_questions(ids)
        assert value.association_group(3) == "group"
        assert value.search(related_to=3)["items"][0]["id"] == 9
        assert value.search(related_to=12)["related_status"] == "no_group"
        assert value.search(related_to=999)["related_status"] == "not_found"


def test_observed_enums_and_display_identity(library):
    db = sqlite3.connect(library[0])
    db.execute("UPDATE questions SET difficulty='custom',question_type='custom_type',display_order=1 WHERE id=12")
    db.execute("UPDATE questions SET display_order=2 WHERE id=3")
    db.commit()
    db.close()
    with reader(library) as value:
        capabilities = value.capabilities()
        assert any(item["value"] == "custom" and item["count"] == 1 for item in capabilities["difficulties"])
        assert any(item["value"] == "custom_type" for item in capabilities["question_types"])
        items = value.search(sort="asc")["items"]
        assert [item["id"] for item in items] == [12, 3, 9]
        assert [item["seq_num"] for item in items] == [1, 2, 3]
        assert value.search(difficulty="custom")["total"] == 1


def test_assets_roles_missing_reference_and_no_restoration(library, monkeypatch):
    from mathbank import asset_lifecycle
    monkeypatch.setattr(asset_lifecycle, "restore_asset", lambda *a, **k: pytest.fail("read must never restore images"))
    for name in ("stem.png", "answer.png", "review.png", "reference.png", "legacy.png"):
        Image.new("RGB", (37, 19), "white").save(library[1] / name)
    db = sqlite3.connect(library[0])
    db.execute("UPDATE questions SET content=?,answer_markdown=?,review=?,image_paths=?,tikz_reference_image_path=? WHERE id=3", (
        "![题图](/static/uploads/stem.png) `![不是图](/static/uploads/code.png)`",
        "![解答图](/static/uploads/answer.png)", "![评析图](/static/uploads/review.png)",
        json.dumps(["/static/uploads/legacy.png", "/static/uploads/missing.png"]), "/static/uploads/reference.png",
    ))
    db.commit()
    db.close()
    with reader(library) as value:
        assets = value.assets([3])["items"][0]["assets"]
        by_name = {item["url"].rsplit("/", 1)[-1]: item for item in assets}
        assert "code.png" not in by_name
        for name, role in (("stem.png", "content"), ("answer.png", "answer"), ("review.png", "review"),
                           ("reference.png", "reference"), ("legacy.png", "content")):
            assert by_name[name]["roles"] == [role]
            assert by_name[name]["exists"] and by_name[name]["status"] == "available"
            assert (by_name[name]["width"], by_name[name]["height"]) == (37, 19)
            assert "local_path" not in by_name[name]
        assert by_name["missing.png"]["status"] == "missing"
        assert not (library[1] / "missing.png").exists()
    with reader(library, include_local_paths=True) as value:
        assert value.assets([3])["items"][0]["assets"][0]["local_path"] == str(library[1] / "stem.png")


def test_assets_reject_external_traversal_and_symlinks(library, tmp_path):
    outside = tmp_path / "outside.png"
    Image.new("RGB", (3, 3)).save(outside)
    try:
        (library[1] / "link.png").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    db = sqlite3.connect(library[0])
    db.execute("UPDATE questions SET image_paths=? WHERE id=3", (json.dumps([
        str(outside), "/static/uploads/../outside.png", "https://example.com/x.png", "/static/uploads/link.png",
    ]),))
    db.commit()
    db.close()
    with reader(library) as value:
        assets = value.assets([3])["items"][0]["assets"]
        assert all(item["status"] == "invalid_reference" and item["url"] is None for item in assets)
        assert str(outside) not in json.dumps(assets)


def test_reference_status_is_checked_against_same_snapshot(library):
    text = "前文例1.1后文"
    start, end = 2, 6
    db = sqlite3.connect(library[0])
    db.execute("UPDATE questions SET review=? WHERE id=3", (text,))
    db.execute("INSERT INTO question_references VALUES(1,3,9,'review',?,?,'例1.1','1.1','书',?,'前文','后文','resolved','')",
               (start, end, hashlib.sha256(text.encode()).hexdigest()))
    db.commit()
    db.close()
    with reader(library) as value:
        reference = value.get_questions([3])["items"][0]["question_references"][0]
        assert reference["status"] == "resolved" and reference["seq_num"] == 2
    db = sqlite3.connect(library[0])
    db.execute("UPDATE questions SET review='题文已改' WHERE id=3")
    db.commit()
    db.close()
    with reader(library) as value:
        assert value.get_questions([3])["items"][0]["question_references"][0]["status"] == "stale"


@pytest.mark.parametrize("kwargs", [{"page_size": 101}, {"page": 0}, {"profile": "bad"},
                                     {"knowledge_point_codes": "p1"}, {"knowledge_point_mode": "bad"}])
def test_search_rejects_invalid_contract(library, kwargs):
    with reader(library) as value, pytest.raises(ValueError):
        value.search(**kwargs)
