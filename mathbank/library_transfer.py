"""Portable, append-only library sharing; no database IDs or asset names are reused.

The caller creates a verified full backup before importing. Import owns its
commit/rollback and never resolves references against pre-existing questions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile
import zlib

from sqlalchemy import func, null, or_
from sqlalchemy.orm import selectinload

from mathbank import __version__
from mathbank.content_sources import (
    CONTENT_SOURCES_JSON_NAME, CONTENT_SOURCES_NOTICE_NAME,
    content_source_export_files, validate_content_sources,
)
from mathbank.asset_security import (
    MAX_NORMALIZED_IMAGE_BYTES, SAFE_IMAGE_EXTENSIONS, _reference_parts, normalize_raster_image,
    resolve_upload_asset,
)
from mathbank.database import (
    KnowledgePoint, KnowledgePointCurriculum, Question, QuestionCurriculum,
    QuestionKnowledgePoint, QuestionReference, QuestionSourceLabel,
)
from mathbank.curriculums import CURRICULUM_NAMES
from mathbank.image_layout import normalize_image_layouts
from mathbank.knowledge_points import METADATA_FIELDS, apply_knowledge_point_filters, is_zxxk_catalog_code
from mathbank.question_assets import (
    embedded_question_assets, image_reference_spans, rewrite_image_layout_paths,
    rewrite_question_asset_paths, rewrite_structured_asset_paths,
    structured_question_assets,
)
from mathbank.question_references import normalize_book_key, normalize_label_key, text_sha256
from mathbank.sub_knowledge import normalize_sub_knowledge
from mathbank.sync_helper import serialize_exchange_questions

FORMAT = "mathbank-library-transfer"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_QUESTIONS = 50_000
MAX_MEMBERS = 50_004
MAX_CONTENT_SOURCE_BYTES = 1024 * 1024
_CONTENT_SOURCE_MEMBERS = frozenset({CONTENT_SOURCES_JSON_NAME, CONTENT_SOURCES_NOTICE_NAME})
UPLOAD_PREFIX = "/static/uploads"
_TEXT_FIELDS = {
    "content": 2_000_000, "question_type": 50, "category_compulsory": 100,
    "category_chapter": 100, "category_knowledge": 100, "difficulty": 50,
    "source": 2000, "answer_markdown": 2_000_000, "review": 2_000_000,
    "association_group_id": 100, "tikz_code": 2_000_000, "tags": 100_000,
}
_NULLABLE_LOCATION_FIELDS = frozenset({
    "category_compulsory", "category_chapter", "category_knowledge",
})
_RECORD_FIELDS = set(_TEXT_FIELDS) | {"id", "sub_knowledge", "knowledge_points", "has_answer", "image_paths",
    "tikz_reference_image_path", "content_tikz_assets", "answer_tikz_assets", "figure_align",
    "figure_align_custom", "figure_size", "image_layouts", "usage_count", "created_at",
    "active_version_code", "curriculum_mappings", "source_labels", "question_references"}
_POINT_FIELDS = {"code", "name", "original_name", "source", "aliases", "curriculum_paths",
                 "role", "status", "position", *METADATA_FIELDS}
_REFERENCE_FIELDS = ("field", "start", "end", "text", "label_key", "book_key",
                     "field_sha256", "prefix", "suffix", "resolution_state", "reason")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _write_questions_json(records, path):
    """Encode in bounded chunks instead of materializing the whole JSON twice."""
    digest, size = hashlib.sha256(), 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    with Path(path).open("wb") as output:
        chunks, characters = [], 0
        for chunk in encoder.iterencode(records):
            chunks.append(chunk)
            characters += len(chunk)
            if characters < 64 * 1024:
                continue
            data = "".join(chunks).encode("utf-8")
            size += len(data)
            if size > MAX_JSON_BYTES:
                raise ValueError("导出题目数据超过 512MB 上限，请按来源分批导出")
            output.write(data)
            digest.update(data)
            chunks, characters = [], 0
        if chunks:
            data = "".join(chunks).encode("utf-8")
            size += len(data)
            if size > MAX_JSON_BYTES:
                raise ValueError("导出题目数据超过 512MB 上限，请按来源分批导出")
            output.write(data)
            digest.update(data)
        output.flush()
        os.fsync(output.fileno())
    return {"name": "questions.json", "size": size, "sha256": digest.hexdigest()}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("分享包 JSON 含重复字段")
        result[key] = value
    return result


def _decode(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON 数值无效")))
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("分享包 JSON 数据无效") from exc


def _text(value, field, maximum, *, empty=True, nullable=False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not empty and not value.strip()):
        raise ValueError(f"分享包 {field} 格式无效")
    return value


def _canonical_path(value):
    # Reuse the asset security component parser without touching the filesystem.
    parts = _reference_parts(value, UPLOAD_PREFIX)
    if PurePosixPath(*parts).suffix.lower() not in SAFE_IMAGE_EXTENSIONS:
        raise ValueError("分享包图片类型不受支持")
    clean = value.strip().lstrip("/")
    if not clean.startswith("static/uploads/"):
        raise ValueError("分享包图片必须使用题库上传路径")
    return "/" + clean


def _asset_references(record):
    paths = list(record.get("image_paths", []))
    paths.extend(embedded_question_assets(*(record.get(field, "") for field in ("content", "answer_markdown", "review"))))
    paths.extend(structured_question_assets(record.get("content_tikz_assets", []),
                 record.get("answer_tikz_assets", []), legacy_reference=record.get("tikz_reference_image_path", "")))
    return list(dict.fromkeys(paths))


def _validate_point(point):
    if not isinstance(point, dict):
        raise ValueError("分享包知识点记录无效")
    if set(point) - _POINT_FIELDS:
        raise ValueError("分享包知识点含未知字段")
    for key, maximum in (("code", 100), ("name", 200), ("original_name", 200), ("source", 50)):
        _text(point.get(key), key, maximum, empty=False)
    if (point["source"] not in ("zhixue", "local", "zxxk")
            or (point["source"] == "zhixue" and not re.fullmatch(r"[0-9]+", point["code"]))
            or (point["source"] == "local" and not re.fullmatch(r"local:[A-Za-z0-9_-]+", point["code"]))
            or (point["source"] == "zxxk" and not is_zxxk_catalog_code(point["code"]))):
        raise ValueError("分享包知识点来源或编码无效")
    aliases = point.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 32:
        raise ValueError("分享包知识点别名无效")
    for alias in aliases:
        _text(alias, "alias", 200, empty=False)
    paths = point.get("curriculum_paths", [])
    if not isinstance(paths, list) or len(paths) > 64:
        raise ValueError("分享包知识点教材路径无效")
    seen = set()
    for path in paths:
        if not isinstance(path, dict) or set(path) != {"version_code", "compulsory", "chapter", "knowledge", "reference_section", "level"}:
            raise ValueError("分享包知识点教材路径字段无效")
        for field in ("version_code", "compulsory", "chapter", "knowledge", "reference_section"):
            _text(path[field], field, 200)
        # The startup schema validator requires chapter paths to have no
        # section, and leaf/parent paths to retain a nonempty section.
        if (path["level"] not in ("leaf", "parent", "chapter")
                or (path["level"] == "chapter") != (not path["knowledge"])
                or tuple(path[field] for field in ("version_code", "compulsory", "chapter", "knowledge", "reference_section", "level")) in seen):
            raise ValueError("分享包知识点教材路径重复或层级无效")
        seen.add(tuple(path[field] for field in ("version_code", "compulsory", "chapter", "knowledge", "reference_section", "level")))
    if point.get("role") not in ("main", "auxiliary") or point.get("status") not in ("confirmed", "suggested"):
        raise ValueError("分享包知识点角色或状态无效")
    if type(point.get("position", 0)) is not int or point.get("position", 0) < 0:
        raise ValueError("分享包知识点顺序无效")
    for field in METADATA_FIELDS:
        # Match normalize_question_points: the editor limit counts JSON
        # characters (with its default separators), rather than UTF-8 bytes.
        if len(json.dumps(point.get(field), ensure_ascii=False, allow_nan=False)) > 32768:
            raise ValueError("分享包知识点证据过长")


def _point_definition(point):
    return {key: point.get(key, [] if key in {"aliases", "curriculum_paths"} else "")
            for key in ("name", "original_name", "source", "aliases", "curriculum_paths")}


def _validate_records(records):
    if not isinstance(records, list) or not records or len(records) > MAX_QUESTIONS:
        raise ValueError("分享包没有题目，或题目数量超过上限")
    ids, assets, definitions = set(), set(), {}
    for record in records:
        if not isinstance(record, dict) or type(record.get("id")) is not int or record["id"] < 1 or record["id"] in ids:
            raise ValueError("分享包题目 ID 无效或重复")
        if set(record) - _RECORD_FIELDS:
            raise ValueError("分享包题目含未知字段，请使用兼容版本的题库")
        ids.add(record["id"])
        normalize_sub_knowledge(record.get("sub_knowledge", []))
        for field, maximum in _TEXT_FIELDS.items():
            _text(record.get(field, ""), field, maximum, empty=field != "content",
                  nullable=field in _NULLABLE_LOCATION_FIELDS)
        if record.get("figure_align", "right") not in ("right", "center", "bottom_left", "bottom_right") or record.get("figure_size", "auto") not in ("auto", "small", "medium", "large"):
            raise ValueError("分享包插图排版无效")
        if type(record.get("figure_align_custom", False)) is not bool:
            raise ValueError("分享包插图位置标记无效")
        if type(record.get("usage_count", 0)) is not int or record.get("usage_count", 0) < 0:
            raise ValueError("分享包使用次数无效")
        if record.get("created_at") is not None:
            _parse_created_at(record["created_at"])
        if not isinstance(record.get("image_paths", []), list) or len(record.get("image_paths", [])) > 200:
            raise ValueError("分享包图片列表无效")
        for path in record.get("image_paths", []):
            _canonical_path(path)
        _text(record.get("tikz_reference_image_path", ""), "tikz_reference_image_path", 1000)
        for field in ("content_tikz_assets", "answer_tikz_assets"):
            items = record.get(field, [])
            if not isinstance(items, list) or len(items) > 200:
                raise ValueError("分享包 TikZ 列表无效")
            for item in items:
                if not isinstance(item, dict) or set(item) - {"id", "image_path", "reference_image_path", "tikz_code", "instruction"}:
                    raise ValueError("分享包 TikZ 数据无效或含未知字段")
                for key in ("image_path", "reference_image_path"):
                    if key in item:
                        _text(item[key], key, 1000)
                        if item[key]:
                            _canonical_path(item[key])
                for key in ("id", "tikz_code", "instruction"):
                    if key in item:
                        _text(item[key], key, 2_000_000)
        for path in _asset_references(record):
            assets.add(_canonical_path(path))
        normalize_image_layouts(record.get("image_layouts", {}), record["content"])
        mappings = record.get("curriculum_mappings", [])
        if not isinstance(mappings, list) or len(mappings) > 32:
            raise ValueError("分享包教材映射无效")
        versions = set()
        for mapping in mappings:
            if not isinstance(mapping, dict) or set(mapping) - {"version_code", "compulsory", "chapter", "knowledge", "sub_knowledge"}:
                raise ValueError("分享包教材映射无效或含未知字段")
            version = _text(mapping.get("version_code"), "version_code", 50, empty=False)
            if version in versions:
                raise ValueError("分享包教材版本重复")
            versions.add(version)
            for field in ("compulsory", "chapter", "knowledge"):
                _text(mapping.get(field, ""), field, 100, nullable=True)
            normalize_sub_knowledge(mapping.get("sub_knowledge", []))
        points = record.get("knowledge_points", [])
        if not isinstance(points, list) or len(points) > 64:
            raise ValueError("分享包题目知识点无效")
        codes = set()
        for point in points:
            _validate_point(point)
            code, definition = point["code"], _point_definition(point)
            if code in codes or (code in definitions and definition != definitions[code]):
                raise ValueError("分享包知识点重复或定义冲突")
            codes.add(code)
            definitions[code] = definition
        labels = record.get("source_labels", [])
        if not isinstance(labels, list) or len(labels) > 512:
            raise ValueError("分享包来源标签无效")
        seen = set()
        for label in labels:
            if not isinstance(label, dict) or set(label) != {"book_key", "label_key", "original_label"}:
                raise ValueError("分享包来源标签字段无效")
            for field, maximum in (("book_key", 200), ("label_key", 100), ("original_label", 200)):
                _text(label[field], field, maximum, empty=False)
            if (normalize_book_key(label["book_key"]) != label["book_key"]
                    or normalize_label_key(label["label_key"]) != label["label_key"]):
                raise ValueError("分享包来源标签未使用规范书名或题号")
            key = (label["book_key"], label["label_key"])
            if key in seen:
                raise ValueError("分享包来源标签重复")
            seen.add(key)
        references = record.get("question_references", [])
        if not isinstance(references, list) or len(references) > 1536:
            raise ValueError("分享包交叉引用无效")
        seen = set()
        for ref in references:
            if (not isinstance(ref, dict) or set(ref) - {*_REFERENCE_FIELDS, "target_question_id", "status", "candidate_label_keys", "is_range"}
                    or ref.get("field") not in ("content", "answer_markdown", "review")):
                raise ValueError("分享包交叉引用字段无效")
            if type(ref.get("start")) is not int or type(ref.get("end")) is not int or not 0 <= ref["start"] < ref["end"] <= 2_000_000:
                raise ValueError("分享包交叉引用跨度无效")
            for field, maximum in (("text", 4000), ("label_key", 100), ("book_key", 200),
                                   ("field_sha256", 64), ("prefix", 24), ("suffix", 24), ("reason", 4000)):
                _text(ref.get(field, ""), field, maximum)
            if len(ref.get("text", "")) != ref["end"] - ref["start"]:
                raise ValueError("分享包交叉引用文字与跨度不一致")
            if (normalize_book_key(ref.get("book_key", "")) != ref.get("book_key", "")
                    or (ref.get("label_key") and normalize_label_key(ref["label_key"]) != ref["label_key"])):
                raise ValueError("分享包交叉引用未使用规范书名或题号")
            if not re.fullmatch(r"[0-9a-f]{64}", ref.get("field_sha256", "")) or ref.get("resolution_state") not in ("resolved", "unresolved", "ambiguous"):
                raise ValueError("分享包交叉引用状态或哈希无效")
            target = ref.get("target_question_id")
            if target is not None and (type(target) is not int or target < 1 or ref["resolution_state"] != "resolved"):
                raise ValueError("分享包交叉引用目标无效")
            key = (ref["field"], ref["start"], ref["end"], ref.get("label_key", ""))
            if key in seen:
                raise ValueError("分享包交叉引用重复")
            seen.add(key)
    return assets, definitions


def _parse_created_at(value):
    try:
        parsed = datetime.fromisoformat(_text(value, "created_at", 50).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError as exc:
        raise ValueError("分享包创建时间无效") from exc


def _member(info):
    name = info.filename
    mode = info.external_attr >> 16
    if (not name or "\\" in name or "\x00" in name or name.startswith("/")
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or PurePosixPath(name).as_posix() != name or info.is_dir()
            or info.orig_filename != name or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
            or info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
        raise ValueError("分享包包含不安全的 ZIP 成员")
    if info.file_size < 0 or info.compress_size < 0 or info.file_size > MAX_TOTAL_BYTES:
        raise ValueError("分享包成员超过安全大小")
    if info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 300:
        raise ValueError("分享包压缩比例超过安全上限")


def _read_member(archive, name, maximum):
    info = archive.getinfo(name)
    if info.file_size > maximum:
        raise ValueError("分享包成员超过安全大小")
    with archive.open(info) as stream:
        data = stream.read(maximum + 1)
        if len(data) > maximum or len(data) != info.file_size:
            raise ValueError("分享包成员实际大小无效")
        return data


def _read_package(archive_path, *, stage_dir=None):
    if Path(archive_path).stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("分享包 ZIP 超过 512MB 上限")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(infos) > MAX_MEMBERS or len(set(name.casefold() for name in names)) != len(names):
                raise ValueError("分享包成员重复或数量过多")
            for info in infos:
                _member(info)
            if "manifest.json" not in names or sum(item.file_size for item in infos) > MAX_TOTAL_BYTES:
                raise ValueError("分享包缺少清单或解压体积超过 1GB")
            manifest = _decode(_read_member(archive, "manifest.json", MAX_MANIFEST_BYTES))
            if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
                    or type(manifest.get("format_version")) is not int or manifest["format_version"] != FORMAT_VERSION):
                raise ValueError("只支持本题库导出的 ZIP 分享包（格式版本 1）")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) > MAX_MEMBERS - 1:
                raise ValueError("分享包清单格式无效")
            listed, asset_paths = {}, set()
            for item in files:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise ValueError("分享包清单文件无效")
                name = item["name"]
                if name in listed or name == "manifest.json" or name not in names:
                    raise ValueError("分享包清单重复或缺少文件")
                if (type(item.get("size")) is not int or item["size"] != archive.getinfo(name).file_size
                        or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                    raise ValueError("分享包清单大小或哈希无效")
                if name == "questions.json" or name in _CONTENT_SOURCE_MEMBERS:
                    if set(item) != {"name", "size", "sha256"}:
                        raise ValueError("分享包题目文件清单无效")
                else:
                    if set(item) != {"name", "size", "sha256", "path"} or not re.fullmatch(r"assets/[0-9]{6}\.(?:png|jpg|jpeg|gif|webp)", name):
                        raise ValueError("分享包图片成员无效")
                    canonical = _canonical_path(item["path"])
                    if canonical != item["path"] or canonical in asset_paths:
                        raise ValueError("分享包图片路径重复或无效")
                    asset_paths.add(canonical)
                listed[name] = item
            if set(names) != {"manifest.json", *listed} or "questions.json" not in listed:
                raise ValueError("分享包包含未列入清单的文件")
            source_names = _CONTENT_SOURCE_MEMBERS.intersection(listed)
            if source_names and source_names != _CONTENT_SOURCE_MEMBERS:
                raise ValueError("分享包资料来源声明文件不完整")
            if "content_sources_included" in manifest and (
                type(manifest["content_sources_included"]) is not bool
                or manifest["content_sources_included"] != bool(source_names)
            ):
                raise ValueError("分享包资料来源声明与文件不一致")
            # These are untrusted historical documentation, not a catalog to
            # install or a license inferred for individual question records.
            for name in sorted(source_names):
                data = _read_member(archive, name, MAX_CONTENT_SOURCE_BYTES)
                if hashlib.sha256(data).hexdigest() != listed[name]["sha256"]:
                    raise ValueError("分享包资料来源声明校验失败")
                if name == CONTENT_SOURCES_JSON_NAME:
                    validate_content_sources(_decode(data))
                else:
                    try:
                        if not data.decode("utf-8").strip():
                            raise ValueError("分享包资料来源说明为空")
                    except UnicodeError as exc:
                        raise ValueError("分享包资料来源说明编码无效") from exc
            raw = _read_member(archive, "questions.json", MAX_JSON_BYTES)
            if hashlib.sha256(raw).hexdigest() != listed["questions.json"]["sha256"]:
                raise ValueError("分享包题目数据校验失败")
            source_version = _text(manifest.get("active_version_code"), "active_version_code", 50, empty=False)
            if source_version not in CURRICULUM_NAMES:
                raise ValueError("分享包教材版本无效")
            records = _decode(raw)
            referenced, definitions = _validate_records(records)
            if any(record.get("active_version_code") != source_version for record in records):
                raise ValueError("分享包题目教材版本与清单不一致")
            if referenced != asset_paths:
                raise ValueError("分享包图片缺失或含未引用图片")
            if (type(manifest.get("question_count")) is not int or manifest["question_count"] != len(records)
                    or type(manifest.get("image_count")) is not int or manifest["image_count"] != len(asset_paths)):
                raise ValueError("分享包清单数量不一致")
            assets = []
            for index, (name, item) in enumerate(listed.items()):
                if name == "questions.json" or name in _CONTENT_SOURCE_MEMBERS:
                    continue
                raw = _read_member(archive, name, MAX_NORMALIZED_IMAGE_BYTES)
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("分享包图片校验失败")
                normalize_raster_image(raw)
                extension = PurePosixPath(name).suffix.lower()
                asset = dict(item, extension=extension)
                if stage_dir is not None:
                    staged = Path(stage_dir) / f"{index:06d}{extension}"
                    staged.write_bytes(raw)
                    asset["staged"] = staged
                assets.append(asset)
            return {"records": records, "assets": assets, "definitions": definitions,
                    "question_count": len(records), "image_count": len(assets), "format_version": FORMAT_VERSION, "active_version_code": source_version}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, KeyError, RuntimeError, NotImplementedError, RecursionError, zlib.error) as exc:
        raise ValueError("ZIP 分享包损坏或格式不受支持") from exc


def inspect_library_zip(archive_path: Path) -> dict:
    """Fully validate before the caller creates a backup or starts a write."""
    package = _read_package(archive_path)
    return {key: package[key] for key in ("question_count", "image_count", "format_version")}


def _library_read_snapshot(db, active_version):
    if active_version not in CURRICULUM_NAMES:
        raise ValueError("导出使用的教材版本无效")
    if db.new or db.dirty or db.deleted:
        raise ValueError("请先保存当前数据库修改，再选择或导出分享题目")
    connection = db.connection()
    raw = connection.connection.dbapi_connection
    if isinstance(raw, sqlite3.Connection) and not raw.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _source_keywords(values):
    if not isinstance(values, list) or len(values) > 50:
        raise ValueError("来源筛选最多支持 50 个关键词")
    return list(dict.fromkeys(_text(value, "来源关键词", 200, empty=False).strip() for value in values))


def _question_ids(values, *, allow_empty=False):
    if (not isinstance(values, list) or len(values) > MAX_QUESTIONS
            or (not values and not allow_empty)
            or any(type(value) is not int or not 1 <= value <= 2**63 - 1 for value in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"勾选题目须为不重复的正整数 ID 列表，最多 {MAX_QUESTIONS} 道")
    return values


def _library_filtered_query(db, filters):
    """One filter definition for the displayed candidates and select-all IDs."""
    if filters is None:
        filters = {}
    allowed = {"search", "source_keywords", "compulsory", "chapter", "knowledge",
               "knowledge_point_codes", "knowledge_point_mode"}
    if not isinstance(filters, dict) or set(filters) - allowed:
        raise ValueError("分享题目筛选字段无效")
    query = db.query(Question.id)
    search = _text(filters.get("search", ""), "搜索文字", 200).strip()
    if search:
        # All searches are literal text, including numbers; an ID/sequence must
        # never silently broaden the result of a source/content search.
        query = query.filter(or_(*(field.contains(search, autoescape=True) for field in (
            Question.content, Question.source, Question.answer_markdown, Question.review, Question.tags))))
    keywords = _source_keywords(filters.get("source_keywords", []))
    if keywords:
        query = query.filter(or_(*(Question.source.contains(value, autoescape=True) for value in keywords)))
    # The main fields are the active textbook mirror, just as in the library.
    # The historical multi-path relation and point recommendations are evidence,
    # never alternate OR paths into a chapter/section filter.
    for field in ("compulsory", "chapter", "knowledge"):
        value = _text(filters.get(field, ""), field, 100)
        if value:
            query = query.filter(getattr(Question, f"category_{field}") == value)
    return apply_knowledge_point_filters(
        query, filters.get("knowledge_point_codes", []),
        match_mode=filters.get("knowledge_point_mode", "any"),
        role_scope="main", status_scope="all",
    )


def _existing_question_ids(db, question_ids):
    existing = set()
    # Keep compatibility with SQLite builds whose parameter limit is 999.
    for start in range(0, len(question_ids), 500):
        existing.update(qid for qid, in db.query(Question.id).filter(Question.id.in_(question_ids[start:start + 500])))
    return existing


def select_library_question_ids(db, *, filters=None, active_version="A") -> list[int]:
    """Resolve an explicit stable-ID selection, without loading question bodies."""
    _library_read_snapshot(db, active_version)
    rows = _library_filtered_query(db, filters).order_by(Question.id).limit(MAX_QUESTIONS + 1).all()
    if len(rows) > MAX_QUESTIONS:
        raise ValueError(f"单次分享最多 {MAX_QUESTIONS} 道题，请缩小筛选范围")
    return [qid for qid, in rows]


def preview_library_questions(db, *, filters=None, question_ids=None, page=1,
                              page_size=10, active_version="A") -> dict:
    """Bounded, read-only previews; never serialize answers, evidence or TikZ."""
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 20:
        raise ValueError("预览页码须为正整数，每页最多 20 道题")
    if question_ids is not None and filters is not None:
        raise ValueError("勾选题目预览不能同时使用筛选条件")
    if question_ids is not None:
        _question_ids(question_ids, allow_empty=True)
    _library_read_snapshot(db, active_version)
    missing = []
    if question_ids is not None:
        existing = _existing_question_ids(db, question_ids)
        missing = [qid for qid in question_ids if qid not in existing]
        ordered = [qid for qid in question_ids if qid in existing]
        total = len(ordered)
        page_ids = ordered[(page - 1) * page_size:page * page_size]
    else:
        query = _library_filtered_query(db, filters)
        total = query.count()
        page_ids = [qid for qid, in query.order_by(Question.id).offset((page - 1) * page_size).limit(page_size)]
    result = {"items": [], "total": total, "page": page, "page_size": page_size,
              "total_pages": max(1, (total + page_size - 1) // page_size), "missing_ids": missing}
    if not page_ids:
        return result
    ranked = db.query(Question.id.label("qid"), func.row_number().over(order_by=Question.id).label("seq_num")).subquery()
    rows = db.query(Question.id, ranked.c.seq_num, Question.content, Question.source, Question.question_type,
                    Question.category_compulsory, Question.category_chapter, Question.category_knowledge).join(
                        ranked, ranked.c.qid == Question.id).filter(Question.id.in_(page_ids)).all()
    points = {}
    for qid, code, name in db.query(QuestionKnowledgePoint.question_id, KnowledgePoint.code, KnowledgePoint.name).join(
            KnowledgePoint, KnowledgePoint.code == QuestionKnowledgePoint.point_code).filter(
                QuestionKnowledgePoint.question_id.in_(page_ids), QuestionKnowledgePoint.role == "main").order_by(
                    QuestionKnowledgePoint.question_id, QuestionKnowledgePoint.position, KnowledgePoint.code):
        points.setdefault(qid, []).append({"code": code, "name": name})
    by_id = {row.id: {"id": row.id, "seq_num": int(row.seq_num), "content": row.content,
                     "source": row.source, "question_type": row.question_type,
                     "category_compulsory": row.category_compulsory, "category_chapter": row.category_chapter,
                     "category_knowledge": row.category_knowledge, "knowledge_points": points.get(row.id, [])}
             for row in rows}
    result["items"] = [by_id[qid] for qid in page_ids]
    return result


def export_library_zip(db, output_path: Path, *, uploads_dir: Path,
                       source_keywords: list[str] | None = None, question_ids: list[int] | None = None,
                       active_version: str = "A") -> dict:
    """Write one atomic, self-contained sharing ZIP from a real SQLite snapshot."""
    if source_keywords is not None:
        source_keywords = _source_keywords(source_keywords)
    if question_ids is not None:
        _question_ids(question_ids)
        if source_keywords:
            raise ValueError("勾选题目不能同时使用来源筛选")
    _library_read_snapshot(db, active_version)
    query = db.query(Question).options(selectinload(Question.knowledge_points)).populate_existing()
    if question_ids is not None:
        existing = _existing_question_ids(db, question_ids)
        missing = [qid for qid in question_ids if qid not in existing]
        if missing:
            raise ValueError(f"勾选的题目已不存在（ID：{'、'.join(str(qid) for qid in missing[:20])}），请刷新后重新选择")
        questions_by_id = {}
        for start in range(0, len(question_ids), 500):
            questions_by_id.update((question.id, question) for question in query.filter(
                Question.id.in_(question_ids[start:start + 500])).all())
        questions = [questions_by_id[qid] for qid in question_ids]
    else:
        if source_keywords:
            query = query.filter(or_(*(Question.source.contains(value, autoescape=True) for value in source_keywords)))
        questions = query.order_by(Question.id).all()
    records, _ = serialize_exchange_questions(db, questions, active_version)
    for question, record in zip(questions, records):
        # to_dict deliberately hides AI references and review-only images in UI.
        record["image_paths"] = list(question.image_paths)
    asset_paths, _ = _validate_records(records)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".mathbank-share-", suffix=".zip", dir=output_path.parent)
    os.close(fd)
    json_name = None
    try:
        json_fd, json_name = tempfile.mkstemp(prefix=".mathbank-share-", suffix=".json", dir=output_path.parent)
        os.close(json_fd)
        json_entry = _write_questions_json(records, json_name)
        files = [json_entry]
        total = json_entry["size"]
        source_files = content_source_export_files()
        if set(source_files) != _CONTENT_SOURCE_MEMBERS:
            raise ValueError("资料来源声明文件不完整")
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(json_name, "questions.json")
            for name, data in source_files.items():
                if not isinstance(data, bytes) or len(data) > MAX_CONTENT_SOURCE_BYTES:
                    raise ValueError("资料来源声明文件大小无效")
                total += len(data)
                archive.writestr(name, data)
                files.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            for index, reference in enumerate(sorted(asset_paths), 1):
                path = resolve_upload_asset(reference, uploads_dir=uploads_dir, url_prefix=UPLOAD_PREFIX, require_file=False)
                if not path.is_file() or path.stat().st_size > MAX_NORMALIZED_IMAGE_BYTES:
                    raise ValueError(f"导出图片不存在或超过安全大小：{reference}")
                data = path.read_bytes()
                normalize_raster_image(data)
                name = f"assets/{index:06d}{path.suffix.lower()}"
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("导出图片总大小超过 1GB，请按来源分批导出")
                archive.writestr(name, data)
                files.append({"name": name, "path": reference, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            manifest = {"format": FORMAT, "format_version": FORMAT_VERSION,
                        "exported_by": __version__, "active_version_code": active_version,
                        "content_sources_included": True,
                        "question_count": len(records), "image_count": len(asset_paths), "files": files}
            manifest_raw = _json(manifest)
            if len(manifest_raw) > MAX_MANIFEST_BYTES or len(files) + 1 > MAX_MEMBERS:
                raise ValueError("分享包图片数量超过上限，请按来源分批导出")
            archive.writestr("manifest.json", manifest_raw)
        # All records and image bytes were already validated above. Reopening
        # only the directory and checking CRC keeps the final acceptance check
        # streaming; a second full json.loads would double the large library.
        if Path(temp_name).stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("分享包 ZIP 超过 512MB 上限，请按来源分批导出")
        with zipfile.ZipFile(temp_name) as completed:
            infos = completed.infolist()
            for info in infos:
                _member(info)
            if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES:
                raise ValueError("分享包解压体积超过 1GB，请按来源分批导出")
            if completed.testzip() is not None:
                raise ValueError("导出 ZIP 完整性校验失败")
        with open(temp_name, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_name, output_path)
        return {"question_count": len(records), "image_count": len(asset_paths)}
    finally:
        for temporary in (temp_name, json_name):
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass


def _reference_row(record, reference, replacements, id_map):
    row = {key: reference.get(key, "") for key in _REFERENCE_FIELDS}
    source = record[row["field"]]
    rewritten = rewrite_question_asset_paths(source, replacements)
    start, end = row["start"], row["end"]
    for left, right, path in image_reference_spans(source):
        if path not in replacements:
            continue
        delta = len(replacements[path]) - (right - left)
        if right <= reference["start"]:
            start += delta
        if right <= reference["end"]:
            end += delta
    row.update(start=start, end=end)
    if row["field_sha256"] == text_sha256(source):
        row.update(field_sha256=text_sha256(rewritten), prefix=rewritten[max(0, start - 24):start], suffix=rewritten[end:end + 24])
    old_target = reference.get("target_question_id")
    row["target_question_id"] = id_map.get(old_target)
    if old_target is not None and old_target not in id_map:
        row.update(resolution_state="unresolved", reason="target_outside_share")
    return row


def _import_points(db, definitions):
    mapping = {}
    for code, definition in definitions.items():
        original_code = code
        existing = db.get(KnowledgePoint, code)
        if existing is not None:
            if (existing.source == "local" or definition["source"] == "local" or code.startswith("local:")) and _point_definition(existing.to_dict()) != definition:
                code = "local:" + uuid.uuid4().hex
            else:
                mapping[code] = existing.code
                continue
        point = KnowledgePoint(code=code, **{field: definition[field] for field in ("name", "original_name", "source")})
        point.aliases = definition["aliases"]
        point.curriculum_paths = [KnowledgePointCurriculum(**path) for path in definition["curriculum_paths"]]
        db.add(point)
        mapping[original_code] = code
    db.flush()
    return mapping


def import_library_zip(db, archive_path: Path, *, uploads_dir: Path, active_version: str = "A") -> dict:
    """Append every question, allocating independent IDs/groups/files atomically.

    The supplied Session must have no pending changes. All newly installed files
    are removed on transaction failure; existing files are never overwritten.
    """
    if active_version not in CURRICULUM_NAMES:
        raise ValueError("导入使用的教材版本无效")
    if db.new or db.dirty or db.deleted:
        raise ValueError("请先保存当前数据库修改，再导入分享包")
    uploads_dir = Path(uploads_dir)
    if uploads_dir.is_symlink():
        raise ValueError("上传目录不能是符号链接")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    installed, staging, committed = [], None, False
    try:
        staging = tempfile.TemporaryDirectory(prefix=".mathbank-share-import-", dir=uploads_dir)
        stage = staging.name
        package = _read_package(archive_path, stage_dir=Path(stage))
        replacements = {}
        for asset in package["assets"]:
            # Every import uses independent names, including repeated imports
            # of exactly the same package. Exclusive creation closes races.
            destination = uploads_dir / ("shared_" + uuid.uuid4().hex + asset["extension"])
            with destination.open("xb") as target:
                installed.append(destination)
                with asset["staged"].open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            rewritten = UPLOAD_PREFIX + "/" + destination.name
            replacements[asset["path"]] = rewritten
            replacements[asset["path"].lstrip("/")] = rewritten
        for record in package["records"]:
            for reference in _asset_references(record):
                replacements[reference] = replacements[_canonical_path(reference)]
        connection = db.connection()
        raw = connection.connection.dbapi_connection
        if isinstance(raw, sqlite3.Connection) and not raw.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        points = _import_points(db, package["definitions"])
        groups, id_map, imported = {}, {}, []
        for record in package["records"]:
            values = {field: record.get(field, "") for field in _TEXT_FIELDS}
            for field in ("content", "answer_markdown", "review"):
                values[field] = rewrite_question_asset_paths(values[field], replacements)
            old_group = values["association_group_id"]
            values["association_group_id"] = groups.setdefault(old_group, uuid.uuid4().hex) if old_group else ""
            # ORM client defaults replace Python None on INSERT. Explicit SQL
            # NULL preserves historical textbook locations in both mirrors.
            question = Question(**{field: null() if field in _NULLABLE_LOCATION_FIELDS and value is None else value
                                   for field, value in values.items()})
            question.image_paths = [replacements[_canonical_path(path)] for path in record.get("image_paths", [])]
            question.tikz_reference_image_path = replacements.get(record.get("tikz_reference_image_path", ""), "")
            question.content_tikz_assets = rewrite_structured_asset_paths(record.get("content_tikz_assets", []), replacements)
            question.answer_tikz_assets = rewrite_structured_asset_paths(record.get("answer_tikz_assets", []), replacements)
            question.image_layouts = rewrite_image_layout_paths(record.get("image_layouts", {}), replacements)
            question.figure_align = record.get("figure_align", "right")
            question.figure_size = record.get("figure_size", "auto")
            question.figure_align_custom = record.get("figure_align_custom", False)
            question.usage_count = 0
            if record.get("created_at"):
                question.created_at = _parse_created_at(record["created_at"])
            mappings = record.get("curriculum_mappings", [])
            for mapping in mappings:
                row = QuestionCurriculum(**{field: null() if mapping.get(field, "") is None else mapping.get(field, "")
                                            for field in ("version_code", "compulsory", "chapter", "knowledge")})
                row.sub_knowledge = mapping.get("sub_knowledge", [])
                question.curriculum_mappings.append(row)
            # Source location belongs only to the exporting textbook. A
            # missing receiving mirror remains explicitly unclassified.
            source_version = package["active_version_code"]
            if not any(row.version_code == source_version for row in question.curriculum_mappings):
                source_mapping = QuestionCurriculum(version_code=source_version, **{
                    field: null() if values["category_" + field] is None else values["category_" + field]
                    for field in ("compulsory", "chapter", "knowledge")})
                source_mapping.sub_knowledge = record.get("sub_knowledge", [])
                question.curriculum_mappings.append(source_mapping)
            active = next((row for row in question.curriculum_mappings if row.version_code == active_version), None)
            if active is None:
                active = QuestionCurriculum(version_code=active_version, compulsory="", chapter="", knowledge="")
                question.curriculum_mappings.append(active)
            question.category_compulsory, question.category_chapter, question.category_knowledge = active.compulsory, active.chapter, active.knowledge
            for position, item in enumerate(record.get("knowledge_points", [])):
                row = QuestionKnowledgePoint(point_code=points[item["code"]], role=item["role"], status=item["status"], position=item.get("position", position))
                for field in METADATA_FIELDS:
                    setattr(row, field, json.dumps(item.get(field), ensure_ascii=False, allow_nan=False))
                question.knowledge_points.append(row)
            db.add(question)
            db.flush()
            id_map[record["id"]] = question.id
            imported.append((question, record))
        for question, record in imported:
            for label in record.get("source_labels", []):
                db.add(QuestionSourceLabel(question_id=question.id, **label))
            for reference in record.get("question_references", []):
                db.add(QuestionReference(source_question_id=question.id, **_reference_row(record, reference, replacements, id_map)))
        # Maintaining the search index does not perform duplicate screening.
        from mathbank.question_duplicate_service import fingerprint_for_question, upsert_question_fingerprint
        for question, _ in imported:
            fingerprint = fingerprint_for_question(question, uploads_dir=uploads_dir, url_prefix=UPLOAD_PREFIX)
            upsert_question_fingerprint(db, question, fingerprint)
        result = {"imported_count": len(imported), "image_count": len(package["assets"]), "id_map": id_map}
        db.commit()
        committed = True
        return result
    except BaseException:
        if not committed:
            db.rollback()
            for path in installed:
                path.unlink(missing_ok=True)
        raise
    finally:
        if staging is not None:
            try:
                staging.cleanup()
            except Exception:
                # Temporary cleanup cannot reverse a committed database write.
                # A leftover private staging directory is safe to remove later.
                pass
