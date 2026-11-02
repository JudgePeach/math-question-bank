"""One data-source catalog serves whole artifacts, never question text."""

from copy import deepcopy
from io import BytesIO
import json
import zipfile
from xml.etree import ElementTree

import pytest

from mathbank import content_sources as sources
from mathbank import paths


@pytest.fixture(autouse=True)
def absent_local_catalog(tmp_path, monkeypatch):
    """Tests never read the current installation's private provenance."""
    monkeypatch.setattr(sources, "LOCAL_CONTENT_SOURCES_PATH", tmp_path / "content_sources.local.json")


def _example_catalog(*identifiers, title="示例公共来源", scope_notice="仅适用于示例资料"):
    return {
        "schema_version": 1, "title": title, "scope_notice": scope_notice,
        "sources": [{
            "id": identifier, "name": "示例机构资料 " + identifier,
            "authors": ["示例教研机构"], "url": "https://example.test/" + identifier,
            "revision": "示例本地批次", "scope": "仅覆盖虚构示例资料",
            "license": {"name": "示例许可", "url": "https://example.test/license"},
            "upstream": [{"name": "示例上游", "url": "https://example.test/upstream",
                          "license": {"name": "示例上游许可", "url": "https://example.test/upstream-license"}}],
            "changes": "示例本地整理记录",
        } for identifier in identifiers],
    }


def _write_catalog(path, catalog):
    raw = json.dumps(catalog, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


@pytest.fixture
def catalog_files(tmp_path, monkeypatch):
    public_path = tmp_path / "public.json"
    monkeypatch.setattr(sources, "CONTENT_SOURCES_PATH", public_path)
    return public_path, sources.LOCAL_CONTENT_SOURCES_PATH


def test_private_catalog_path_is_anchored_in_generated_storage():
    assert paths.LOCAL_CONTENT_SOURCES_PATH == paths.SYSTEM_GENERATED_DIR / "content_sources.local.json"


def test_missing_local_catalog_returns_public_without_creating_any_file(catalog_files):
    public_path, local_path = catalog_files
    public = _example_catalog("public-example")
    original = _write_catalog(public_path, public)
    assert sources.load_content_sources() == public
    assert public_path.read_bytes() == original
    assert not local_path.exists()


def test_default_catalog_merges_local_entries_without_overwriting_public_metadata(catalog_files):
    public_path, local_path = catalog_files
    public = _example_catalog("public-example")
    local = _example_catalog("local-example", title="示例私有标题", scope_notice="示例私有说明")
    before = deepcopy((public, local))
    raw_public = _write_catalog(public_path, public)
    raw_local = _write_catalog(local_path, local)
    catalog = sources.load_content_sources()
    assert catalog == {**public, "sources": public["sources"] + local["sources"]}
    assert (public, local) == before
    assert public_path.read_bytes() == raw_public and local_path.read_bytes() == raw_local
    catalog["sources"][-1]["authors"].append("临时修改")
    assert sources.load_content_sources()["sources"][-1] == local["sources"][0]


def test_explicit_path_reads_only_that_catalog_even_when_default_files_are_invalid(catalog_files, tmp_path):
    public_path, local_path = catalog_files
    public_path.write_text("invalid public JSON", encoding="utf-8")
    local_path.write_text("invalid private JSON", encoding="utf-8")
    selected = tmp_path / "selected.json"
    catalog = _example_catalog("explicit-example")
    _write_catalog(selected, catalog)
    assert sources.load_content_sources(selected) == catalog
    assert json.loads(sources.content_source_export_files(catalog)["content_sources.json"]) == catalog


def test_default_catalog_rejects_public_local_id_collision(catalog_files):
    public_path, local_path = catalog_files
    public = _example_catalog("same-example")
    local = _example_catalog("same-example")
    local["sources"][0]["name"] = "不允许覆盖公共记录"
    _write_catalog(public_path, public)
    _write_catalog(local_path, local)
    with pytest.raises(ValueError, match="编码重复"):
        sources.load_content_sources()


@pytest.mark.parametrize("change", [
    lambda c: c.update(schema_version=2),
    lambda c: c.update(sources="invalid"),
    lambda c: c["sources"].append(deepcopy(c["sources"][0])),
    lambda c: c["sources"][0].update(authors=[]),
    lambda c: c["sources"][0]["license"].update(url="http://example.test/license"),
])
def test_invalid_private_catalog_fails_instead_of_dropping_credits(catalog_files, change):
    public_path, local_path = catalog_files
    _write_catalog(public_path, _example_catalog("public-example"))
    local = _example_catalog("local-example")
    change(local)
    _write_catalog(local_path, local)
    with pytest.raises(ValueError):
        sources.content_source_export_files()


@pytest.mark.parametrize("raw", [b"not JSON", b'"not a catalog"', b'\xff'])
def test_unreadable_private_catalog_is_not_treated_as_absent(catalog_files, raw):
    public_path, local_path = catalog_files
    _write_catalog(public_path, _example_catalog("public-example"))
    local_path.write_bytes(raw)
    with pytest.raises((ValueError, UnicodeError)):
        sources.load_content_sources()


def test_private_catalog_io_errors_are_not_treated_as_absence(catalog_files):
    public_path, local_path = catalog_files
    _write_catalog(public_path, _example_catalog("public-example"))
    local_path.mkdir()
    with pytest.raises(OSError):
        sources.load_content_sources()


def test_merged_catalog_enforces_combined_entry_limit(catalog_files):
    public_path, local_path = catalog_files
    _write_catalog(public_path, _example_catalog(*(f"public-{index}" for index in range(100))))
    _write_catalog(local_path, _example_catalog("local-example"))
    assert len(sources.load_content_sources(public_path)["sources"]) == 100
    assert len(sources.load_content_sources(local_path)["sources"]) == 1
    with pytest.raises(ValueError, match="条目无效"):
        sources.load_content_sources()


def test_merged_catalog_enforces_combined_byte_limit(catalog_files, monkeypatch):
    public_path, local_path = catalog_files
    public = _write_catalog(public_path, _example_catalog("public-example"))
    local = _write_catalog(local_path, _example_catalog("local-example"))
    monkeypatch.setattr(sources, "MAX_CATALOG_BYTES", max(len(public), len(local)))
    assert sources.load_content_sources(public_path) and sources.load_content_sources(local_path)
    with pytest.raises(ValueError, match="总表过大"):
        sources.load_content_sources()


def test_private_catalog_enforces_its_own_raw_byte_limit(catalog_files, monkeypatch):
    public_path, local_path = catalog_files
    public = _write_catalog(public_path, _example_catalog("public-example"))
    local = _example_catalog("local-example")
    local["sources"][0]["changes"] = "x" * 2000
    raw = _write_catalog(local_path, local)
    assert len(raw) > len(public)
    monkeypatch.setattr(sources, "MAX_CATALOG_BYTES", len(raw) - 1)
    with pytest.raises(ValueError, match="总表过大"):
        sources.load_content_sources()


def test_merged_export_snapshot_preserves_private_credits_and_input_files(catalog_files):
    public_path, local_path = catalog_files
    public = _example_catalog("public-example")
    local = _example_catalog("local-example", title="示例私有标题")
    before = deepcopy((public, local))
    raw_public = _write_catalog(public_path, public)
    raw_local = _write_catalog(local_path, local)
    files = sources.content_source_export_files()
    exported = json.loads(files["content_sources.json"])
    assert exported == {**public, "sources": public["sources"] + local["sources"]}
    notice = files["CONTENT_SOURCES.txt"].decode("utf-8")
    assert "示例私有标题" not in notice
    for value in (local["sources"][0]["name"], "示例本地批次", "示例本地整理记录", "示例教研机构", "示例上游许可"):
        assert value in notice
    assert (public, local) == before
    assert public_path.read_bytes() == raw_public and local_path.read_bytes() == raw_local


def test_local_credits_reach_docx_metadata_without_entering_question_body(catalog_files):
    from mathbank.word_export_helper import build_word_document

    public_path, local_path = catalog_files
    _write_catalog(public_path, _example_catalog("public-example"))
    local = _example_catalog("local-example")
    _write_catalog(local_path, local)
    questions = [{"question": {"id": 1, "content": "虚构题目正文。", "question_type": "detailed_answer"}}]
    original = deepcopy(questions)
    data, diagnostics = build_word_document("示例试卷", "", "exam", questions)
    assert not diagnostics["failed_formulas"]
    with zipfile.ZipFile(BytesIO(data)) as package:
        metadata = ElementTree.fromstring(package.read("docProps/custom.xml"))
        own = [item for item in metadata if item.get("name") == "MathBankContentSources"]
        assert len(own) == 1 and own[0][0].text == sources.content_sources_notice()
        assert local["sources"][0]["name"] in own[0][0].text
        assert local["sources"][0]["name"] not in package.read("word/document.xml").decode("utf-8")
    assert questions == original


def test_bundled_catalog_keeps_two_distinct_upstream_licenses_and_explicit_scope():
    catalog = sources.load_content_sources()
    entry = catalog["sources"][0]
    assert entry["id"] == "gaokao-math-problems-compilation"
    assert entry["license"]["name"] == "CC BY-SA 4.0"
    assert entry["upstream"][0]["license"]["name"] == "CC BY 4.0"
    assert "不表示全部题目或题库软件采用同一许可" in catalog["scope_notice"]
    assert "第三方内容" in entry["scope"]


def test_catalog_uses_project_path_independently_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sources.load_content_sources()["sources"]


def test_both_artifact_files_use_one_snapshot_without_mutating_the_catalog(monkeypatch):
    catalog = sources.load_content_sources()
    original = deepcopy(catalog)
    calls = []

    def snapshot():
        calls.append(True)
        return catalog

    monkeypatch.setattr(sources, "load_content_sources", snapshot)
    files = sources.content_source_export_files()
    assert len(calls) == 1
    assert set(files) == {"content_sources.json", "CONTENT_SOURCES.txt"}
    assert json.loads(files["content_sources.json"]) == catalog == original
    notice = files["CONTENT_SOURCES.txt"].decode("utf-8")
    assert notice.count(catalog["sources"][0]["url"]) == 1
    assert notice.count(catalog["sources"][0]["upstream"][0]["url"]) == 1
    assert "本地整理与修改" in notice


@pytest.mark.parametrize("change", [
    lambda c: c.update(schema_version=True),
    lambda c: c.update(api_key="not-a-catalog-field"),
    lambda c: c["sources"].append(deepcopy(c["sources"][0])),
    lambda c: c["sources"][0].update(authors="作者"),
    lambda c: c["sources"][0].update(url="javascript:alert(1)"),
    lambda c: c["sources"][0].update(url="https://name:secret@example.test/source"),
    lambda c: c["sources"][0]["license"].update(api_key="not-a-license-field"),
    lambda c: c["sources"][0].update(changes=""),
])
def test_invalid_or_unexpected_catalog_data_fails_closed(change):
    catalog = sources.load_content_sources()
    change(catalog)
    with pytest.raises(ValueError):
        sources.content_source_export_files(catalog)


def test_missing_or_invalid_source_file_is_reported_instead_of_silently_dropping_credits(tmp_path):
    with pytest.raises(FileNotFoundError):
        sources.load_content_sources(tmp_path / "absent.json")
    path = tmp_path / "bad.json"
    path.write_bytes(b'"not a catalog"')
    with pytest.raises(ValueError):
        sources.load_content_sources(path)
