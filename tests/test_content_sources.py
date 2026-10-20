"""One data-source catalog serves whole artifacts, never question text."""

from copy import deepcopy
import json

import pytest

from mathbank import content_sources as sources


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
