"""One catalog of content credits, separate from per-question exam sources.

The catalog describes the scopes of imported collections. It does not infer
copyright or licenses from a question ID, exam name, or the software license.
Export snapshots accompany an artifact once, never each question's content.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from mathbank.paths import PROJECT_ROOT


CONTENT_SOURCES_PATH = PROJECT_ROOT / "mathbank" / "resources" / "content_sources.json"
CONTENT_SOURCES_JSON_NAME = "content_sources.json"
CONTENT_SOURCES_NOTICE_NAME = "CONTENT_SOURCES.txt"
MAX_CATALOG_BYTES = 256 * 1024


def validate_content_sources(catalog: object) -> dict:
    """Validate a small plain-data catalog; return an independent JSON copy."""
    if (not isinstance(catalog, dict) or type(catalog.get("schema_version")) is not int
            or catalog.get("schema_version") != 1
            or set(catalog) != {"schema_version", "title", "scope_notice", "sources"}):
        raise ValueError("资料来源总表版本无效")

    def text(value, *, optional=False):
        if optional and value == "":
            return
        if not isinstance(value, str) or not value.strip() or len(value) > 10000:
            raise ValueError("资料来源总表文字无效")

    def url(value):
        text(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("资料来源链接必须为不含凭据的 HTTPS 链接")

    def license_data(value):
        if not isinstance(value, dict) or set(value) != {"name", "url"}:
            raise ValueError("资料来源许可无效")
        text(value.get("name"))
        url(value.get("url"))

    text(catalog.get("title"))
    text(catalog.get("scope_notice"))
    entries = catalog.get("sources")
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError("资料来源条目无效")
    ids = set()
    for entry in entries:
        if (not isinstance(entry, dict)
                or set(entry) - {"id", "name", "authors", "url", "revision", "scope", "license", "upstream", "changes"}):
            raise ValueError("资料来源条目必须为对象")
        for field in ("id", "name", "scope", "changes"):
            text(entry.get(field))
        key = entry["id"]
        if key in ids:
            raise ValueError("资料来源编码重复")
        ids.add(key)
        authors = entry.get("authors")
        if not isinstance(authors, list) or not authors or len(authors) > 100:
            raise ValueError("资料来源作者无效")
        for author in authors:
            text(author)
        url(entry.get("url"))
        text(entry.get("revision", ""), optional=True)
        license_data(entry.get("license"))
        upstream = entry.get("upstream", [])
        if not isinstance(upstream, list) or len(upstream) > 100:
            raise ValueError("上游资料来源无效")
        for item in upstream:
            if not isinstance(item, dict) or set(item) != {"name", "url", "license"}:
                raise ValueError("上游资料来源必须为对象")
            text(item.get("name"))
            url(item.get("url"))
            license_data(item.get("license"))
    encoded = json.dumps(catalog, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_CATALOG_BYTES:
        raise ValueError("资料来源总表过大")
    return json.loads(encoded)


def load_content_sources(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else CONTENT_SOURCES_PATH
    raw = path.read_bytes()
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("资料来源总表过大")
    return validate_content_sources(json.loads(raw.decode("utf-8")))


def content_sources_json_bytes(catalog: dict | None = None) -> bytes:
    catalog = load_content_sources() if catalog is None else validate_content_sources(catalog)
    return (json.dumps(catalog, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def content_sources_notice(catalog: dict | None = None) -> str:
    catalog = load_content_sources() if catalog is None else validate_content_sources(catalog)
    lines = [catalog["title"], "", catalog["scope_notice"]]
    for entry in catalog["sources"]:
        lines.extend(["", entry["name"], "作者：" + "、".join(entry["authors"]),
                      "来源：" + entry["url"], "适用资料：" + entry["scope"],
                      "许可：" + entry["license"]["name"] + " " + entry["license"]["url"]])
        if entry.get("revision"):
            lines.append("导入依据版本：" + entry["revision"])
        for upstream in entry.get("upstream", []):
            lines.append("上游来源：" + upstream["name"] + " " + upstream["url"])
            lines.append("上游许可：" + upstream["license"]["name"] + " " + upstream["license"]["url"])
        lines.append("本地整理与修改：" + entry["changes"])
    return "\n".join(lines) + "\n"


def content_source_export_files(catalog: dict | None = None) -> dict[str, bytes]:
    """Take one catalog snapshot for both files in the same artifact."""
    catalog = load_content_sources() if catalog is None else validate_content_sources(catalog)
    return {
        CONTENT_SOURCES_JSON_NAME: content_sources_json_bytes(catalog),
        CONTENT_SOURCES_NOTICE_NAME: content_sources_notice(catalog).encode("utf-8"),
    }
