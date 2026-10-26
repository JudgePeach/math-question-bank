"""Read-only, bounded question access shared by the Agent CLI and HTTP API.

This module deliberately does not import ``database`` or application startup.
Each reader owns one real SQLite read transaction; it never initializes a
schema, seeds catalogues, restores retained assets, or changes stored content.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
import sqlite3

from mathbank.curriculums import (
    CURRICULUM_NAMES, DEFAULT_DIFFICULTIES, DEFAULT_QUESTION_TYPES,
    normalize_version_code,
)
from mathbank.paths import DATABASE_FILE, DATA_BACKUP_DIR, PROJECT_ROOT, UPLOADS_DIR
from mathbank.question_assets import embedded_question_assets
from mathbank.source_comments import strip_source_attribution_comment
from mathbank.sub_knowledge import detect_curriculum_version


PROFILES = ("candidate", "teaching", "evidence")
MAX_BATCH_SIZE = 100
_AUDIT_FIELDS = ("scope", "evidence_source", "evidence", "reason", "origin")


class AgentReadError(RuntimeError):
    """A safe diagnostic which contains no local database or credential path."""


def _json(value, default):
    if value is None or value == "":
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _text(value, name, maximum=500):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} 必须是不超过 {maximum} 字符的文本")
    return value.strip()


def _integer(value, name, minimum=1, maximum=2**63 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 至 {maximum} 之间")
    return value


def _ids(values):
    if not isinstance(values, (list, tuple)) or not values or len(values) > MAX_BATCH_SIZE:
        raise ValueError(f"题目 ID 必须是 1 至 {MAX_BATCH_SIZE} 项的数组")
    result = []
    for value in values:
        value = _integer(value, "题目 ID")
        if value in result:
            raise ValueError("题目 ID 不能重复")
        result.append(value)
    return result


def _like(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AgentReader:
    """Use as a context manager. Paths are injected by trusted local callers."""

    def __init__(self, db_path=DATABASE_FILE, *, uploads_dir=UPLOADS_DIR,
                 metadata_path=None, active_version_code=None, include_local_paths=False):
        self.db_path = Path(db_path).resolve()
        self.uploads_dir = Path(uploads_dir).resolve()
        self.uploads_url_prefix = "static/test_uploads" if self.uploads_dir.name == "test_uploads" else "static/uploads"
        self.metadata_path = Path(metadata_path) if metadata_path is not None else (
            DATA_BACKUP_DIR / "custom_metadata.json" if self.db_path == DATABASE_FILE.resolve()
            else self.db_path.parent / "data_backup" / "custom_metadata.json"
        )
        self._active_version = active_version_code
        self.include_local_paths = bool(include_local_paths)
        self.connection = None
        self._sequences = None
        self._code_aliases = None

    def __enter__(self):
        if self.connection is not None:
            raise AgentReadError("读取会话已经打开")
        if not self.db_path.is_file():
            raise FileNotFoundError("题库数据库不存在；只读查询不会创建数据库")
        try:
            self.connection = sqlite3.connect(
                self.db_path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None,
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("BEGIN")
            self.tables = {row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "questions" not in self.tables:
                raise AgentReadError("当前文件不是可读取的题库：缺少 questions 表")
            self.columns = {row[1] for row in self.connection.execute("PRAGMA table_info(questions)")}
            if not {"id", "content"}.issubset(self.columns):
                raise AgentReadError("题库结构不兼容；只读查询不会迁移数据库")
            try:
                self.metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
                if not isinstance(self.metadata, dict):
                    self.metadata = {}
            except (OSError, ValueError):
                self.metadata = {}
            self.active_version_code = normalize_version_code(
                self._active_version or detect_curriculum_version(self.metadata)
            )
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        if self.connection is not None:
            try:
                self.connection.rollback()
            finally:
                self.connection.close()
                self.connection = None

    def _execute(self, statement, params=()):
        if self.connection is None:
            raise AgentReadError("请在 with AgentReader(...) 读取会话中执行查询")
        return self.connection.execute(statement, params)

    def _version(self, version_code):
        return normalize_version_code(version_code or self.active_version_code)

    def _sequence_map(self):
        if self._sequences is None:
            order = "COALESCE(display_order,id),id" if "display_order" in self.columns else "id"
            self._sequences = {row[0]: index for index, row in enumerate(
                self._execute(f"SELECT id FROM questions ORDER BY {order}"), 1
            )}
        return self._sequences

    def _has(self, *tables):
        return all(table in self.tables for table in tables)

    def _enum(self, field, metadata_key, defaults):
        labels = {item["value"]: item.get("label", item["value"]) for item in defaults}
        configured = self.metadata.get(metadata_key, [])
        if isinstance(configured, list):
            labels.update({item["value"]: item.get("label", item["value"])
                           for item in configured if isinstance(item, dict)
                           and isinstance(item.get("value"), str)
                           and isinstance(item.get("label", item["value"]), str)})
        counts = {row[0]: row[1] for row in self._execute(
            f"SELECT {field},COUNT(*) FROM questions GROUP BY {field}"
        )} if field in self.columns else {}
        return [{"value": value, "label": labels.get(value, value), "count": counts.get(value, 0)}
                for value in dict.fromkeys([*labels, *counts]) if value is not None]

    def capabilities(self):
        return {
            "read_only": True, "schema_version": self._execute("PRAGMA user_version").fetchone()[0],
            "total_questions": self._execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "active_version_code": self.active_version_code,
            "curriculum_versions": [{"value": code, "label": name} for code, name in CURRICULUM_NAMES.items()],
            "question_types": self._enum("question_type", "question_types", DEFAULT_QUESTION_TYPES),
            "difficulties": self._enum("difficulty", "difficulties", DEFAULT_DIFFICULTIES),
            "profiles": list(PROFILES), "default_page_size": 20, "max_page_size": 100,
            "max_batch_size": MAX_BATCH_SIZE,
            "knowledge_points_available": self._has("knowledge_points", "question_knowledge_points"),
            "curriculum_mappings_available": self._has("question_curriculums"),
            "query_is_literal": True,
            "identity_note": "id 是当前题库内的题目身份；seq_num 是随增删和整理顺序变化的展示序号",
            "quality_note": "has_answer 仅表示有答案；知识点标注状态不证明题文或答案已核验",
            "content_sources_note": "逐题 source 保留具体出处；公共资料来源声明见 mathbank/resources/content_sources.json",
        }

    def _point_codes(self, codes):
        if codes is None:
            return []
        if isinstance(codes, str) or not isinstance(codes, (list, tuple)) or len(codes) > 64:
            raise ValueError("知识点编码必须是不超过64项的数组")
        normalized = []
        for value in codes:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("知识点编码必须是非空文本")
            normalized.append(self._canonical_point_code(_text(value, "知识点编码", 100)))
        return list(dict.fromkeys(normalized))

    def _canonical_point_code(self, code):
        """Reuse the catalogue's shipped identity aliases without seeding it."""
        if self._code_aliases is None:
            path = PROJECT_ROOT / "mathbank" / "resources" / "knowledge_points.json"
            try:
                resource = json.loads(path.read_text(encoding="utf-8"))
                aliases = resource.get("source_code_aliases", {})
                self._code_aliases = aliases if isinstance(aliases, dict) else {}
            except (OSError, ValueError, AttributeError):
                self._code_aliases = {}
        return self._code_aliases.get(code, code)

    def _knowledge_sql(self, query, version_code, compulsory, chapter, knowledge):
        if not self._has("knowledge_points"):
            raise AgentReadError("当前题库没有独立知识点目录；请使用正文或教材分类检索")
        clauses, params = [], []
        if query:
            if query.startswith("#"):
                clauses.append("k.code=?")
                params.append(self._canonical_point_code(query[1:].strip()))
            else:
                clauses.append("(" + " OR ".join(f"k.{field} LIKE ? ESCAPE '\\'"
                               for field in ("code", "name", "original_name", "aliases")) + ")")
                params.extend([_like(query)] * 4)
        paths = {"compulsory": compulsory, "chapter": chapter, "knowledge": knowledge}
        paths = {key: _text(value, key, 100) for key, value in paths.items() if value}
        if paths:
            if not self._has("knowledge_point_curriculums"):
                raise AgentReadError("当前题库没有知识点教材关联")
            path_sql = " AND ".join(f"p.{key}=?" for key in paths)
            clauses.append("EXISTS(SELECT 1 FROM knowledge_point_curriculums p WHERE "
                           "p.point_code=k.code AND p.version_code=? AND " + path_sql + ")")
            params.extend([version_code, *paths.values()])
        return " AND ".join(clauses) or "1=1", params

    def knowledge_points(self, query="", version_code=None, compulsory="", chapter="", knowledge="",
                         limit=20, offset=0):
        query = _text(query, "知识点搜索", 200)
        limit = _integer(limit, "目录返回条数", 1, 100)
        offset = _integer(offset, "目录偏移", 0)
        version = self._version(version_code)
        where, params = self._knowledge_sql(query, version, compulsory, chapter, knowledge)
        total = self._execute("SELECT COUNT(*) FROM knowledge_points k WHERE " + where, params).fetchone()[0]
        rows = self._execute("SELECT k.* FROM knowledge_points k WHERE " + where +
                             " ORDER BY k.name,k.code LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["aliases"] = _json(item.get("aliases"), [])
            item["curriculum_paths"] = self._point_paths(item["code"])
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None,
                "truncated": offset + len(items) < total, "version_code": version}

    def _point_paths(self, code):
        if not self._has("knowledge_point_curriculums"):
            return []
        fields = "version_code,compulsory,chapter,knowledge,reference_section,level"
        return [dict(row) for row in self._execute(
            f"SELECT {fields} FROM knowledge_point_curriculums WHERE point_code=? ORDER BY id", (code,)
        )]

    def association_group(self, question_id):
        question_id = _integer(question_id, "关联题目 ID")
        if "association_group_id" not in self.columns:
            return None
        row = self._execute("SELECT association_group_id FROM questions WHERE id=?", (question_id,)).fetchone()
        return str(row[0] or "").strip() or None if row else None

    def search(self, query="", source="", compulsory="", chapter="", knowledge="",
               knowledge_point_codes=None, knowledge_query="", knowledge_point_mode="any",
               knowledge_point_role="main", knowledge_point_status="all", qtype="", difficulty="",
               page=1, page_size=20, related_to=None, profile="candidate", include_answers=False,
               sort="desc", version_code=None):
        profile = self._profile(profile)
        page = _integer(page, "页码")
        page_size = _integer(page_size, "每页条数", 1, 100)
        version = self._version(version_code)
        query, source = _text(query, "关键词"), _text(source, "来源")
        knowledge_query = _text(knowledge_query, "知识点名称", 200)
        if knowledge_point_mode not in {"any", "all"}:
            raise ValueError("知识点匹配方式必须是 any 或 all")
        if knowledge_point_role not in {"main", "auxiliary", "all"}:
            raise ValueError("知识点角色必须是 main、auxiliary 或 all")
        if knowledge_point_status not in {"all", "confirmed", "suggested"}:
            raise ValueError("知识点状态必须是 all、confirmed 或 suggested")
        clauses, params, resolved_points = [], [], []
        if query:
            fields = [field for field in ("category_compulsory", "category_chapter", "category_knowledge",
                      "content", "source", "tags", "answer_markdown", "review") if field in self.columns]
            clauses.append("(" + " OR ".join(f"q.{field} LIKE ? ESCAPE '\\'" for field in fields) + ")")
            params.extend([_like(query)] * len(fields))
        if source:
            clauses.append("q.source LIKE ? ESCAPE '\\'")
            params.append(_like(source))
        for field, value in (("question_type", qtype), ("difficulty", difficulty)):
            if value:
                clauses.append(f"q.{field}=?")
                params.append(_text(value, field, 100))
        path = {key: _text(value, key, 100) for key, value in
                (("compulsory", compulsory), ("chapter", chapter), ("knowledge", knowledge)) if value}
        if path:
            if version == self.active_version_code:
                clauses.extend(f"q.category_{field}=?" for field in path)
                params.extend(path.values())
            elif self._has("question_curriculums"):
                clauses.append("EXISTS(SELECT 1 FROM question_curriculums c WHERE c.question_id=q.id "
                               "AND c.version_code=? AND " + " AND ".join(f"c.{field}=?" for field in path) + ")")
                params.extend([version, *path.values()])
            else:
                raise AgentReadError("当前题库没有指定教材版本的定位；不会切换全局教材设置")
        codes = self._point_codes(knowledge_point_codes)
        if codes or knowledge_query:
            if not self._has("knowledge_points", "question_knowledge_points"):
                raise AgentReadError("当前题库没有独立知识点标注")
            if knowledge_query:
                point_where, point_params = self._knowledge_sql(knowledge_query, version, "", "", "")
                resolved_points = [dict(row) for row in self._execute(
                    "SELECT k.code,k.name FROM knowledge_points k WHERE " + point_where + " ORDER BY k.name,k.code",
                    point_params,
                )]
                if len(resolved_points) > 64:
                    raise ValueError("知识点名称匹配超过64项，请先查询目录并选择明确编码")
                if not resolved_points:
                    clauses.append("0=1")
            explicit = list(codes)
            resolved_codes = [point["code"] for point in resolved_points]
            code_groups = []
            if explicit:
                code_groups.extend([[code] for code in explicit] if knowledge_point_mode == "all" else [explicit])
            if resolved_codes:
                # A broad name is a set of alternatives, even in explicit all-mode.
                code_groups.append(resolved_codes)
            for group in code_groups:
                placeholders = ",".join("?" for _ in group)
                restriction = f"r.point_code IN ({placeholders})"
                group_params = list(group)
                if knowledge_point_role != "all":
                    restriction += " AND r.role=?"
                    group_params.append(knowledge_point_role)
                if knowledge_point_status != "all":
                    restriction += " AND r.status=?"
                    group_params.append(knowledge_point_status)
                clauses.append("EXISTS(SELECT 1 FROM question_knowledge_points r WHERE r.question_id=q.id AND " + restriction + ")")
                params.extend(group_params)
        related_status = None
        if related_to is not None:
            related_to = _integer(related_to, "关联题目 ID")
            exists = self._execute("SELECT 1 FROM questions WHERE id=?", (related_to,)).fetchone()
            group = self.association_group(related_to)
            related_status = "not_found" if not exists else ("no_group" if not group else "found")
            if group:
                clauses.append("q.association_group_id=? AND q.id!=?")
                params.extend([group, related_to])
            else:
                clauses.append("0=1")
        where = " AND ".join(clauses) or "1=1"
        total = self._execute("SELECT COUNT(*) FROM questions q WHERE " + where, params).fetchone()[0]
        order = self._order(sort)
        rows = self._execute("SELECT q.* FROM questions q WHERE " + where + " ORDER BY " + order +
                             " LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
        items = [self._project(dict(row), profile, include_answers, version) for row in rows]
        matched_codes = set(codes) | {point["code"] for point in resolved_points}
        for item, row in zip(items, rows):
            reasons = []
            if query:
                reasons.extend({"field": field, "query": query} for field in
                               ("category_compulsory", "category_chapter", "category_knowledge", "content",
                                "source", "tags", "answer_markdown", "review")
                               if field in row.keys() and query.casefold() in str(row[field] or "").casefold())
            if source:
                reasons.append({"field": "source", "query": source})
            if matched_codes:
                reasons.extend({"field": "knowledge_points", "code": point["code"], "name": point["name"]}
                               for point in item["knowledge_points"] if point["code"] in matched_codes)
            item["match_reasons"] = reasons
        result = {"items": items, "total": total, "page": page, "page_size": page_size,
                  "total_pages": max(1, (total + page_size - 1) // page_size),
                  "next_page": page + 1 if page * page_size < total else None,
                  "truncated": page * page_size < total, "profile": profile,
                  "version_code": version, "active_version_code": self.active_version_code,
                  "resolved_knowledge_points": resolved_points}
        if related_to is not None:
            result.update(related_to=related_to, related_status=related_status)
        return result

    def _order(self, sort):
        if sort not in {"asc", "desc", "id_asc", "id_desc"}:
            raise ValueError("排序必须是 asc、desc、id_asc 或 id_desc")
        direction = "DESC" if sort.endswith("desc") else "ASC"
        if sort.startswith("id_"):
            return "q.id " + direction
        custom = "display_order" in self.columns and self._execute(
            "SELECT 1 FROM questions WHERE display_order IS NOT NULL LIMIT 1"
        ).fetchone()
        if custom:
            return f"COALESCE(q.display_order,q.id) {direction},q.id {direction}"
        if "created_at" in self.columns:
            return f"q.created_at {direction},q.id {direction}"
        return "q.id " + direction

    def _profile(self, profile):
        if profile not in PROFILES:
            raise ValueError("读取层次必须是 candidate、teaching 或 evidence")
        return profile

    def get_questions(self, ids, profile="teaching", include_answers=True, version_code=None):
        ids, profile = _ids(ids), self._profile(profile)
        version = self._version(version_code)
        rows = {row["id"]: dict(row) for row in self._execute(
            "SELECT * FROM questions WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids
        )}
        return {"items": [self._project(rows[qid], profile, include_answers, version) for qid in ids if qid in rows],
                "requested_ids": ids, "missing_ids": [qid for qid in ids if qid not in rows],
                "profile": profile, "version_code": version, "active_version_code": self.active_version_code}

    def _points(self, qid, evidence=False):
        if not self._has("question_knowledge_points", "knowledge_points"):
            return []
        audit = "," + ",".join("r." + field for field in _AUDIT_FIELDS) if evidence else ""
        rows = self._execute("SELECT k.code,k.name,r.role,r.status" + audit +
                             " FROM question_knowledge_points r JOIN knowledge_points k ON k.code=r.point_code "
                             "WHERE r.question_id=? ORDER BY r.position,r.id", (qid,))
        result = []
        for row in rows:
            point = dict(row)
            if evidence:
                for field in _AUDIT_FIELDS:
                    point[field] = _json(point[field], point[field])
            result.append(point)
        return result

    def _mappings(self, qid):
        if not self._has("question_curriculums"):
            return []
        result = []
        for row in self._execute("SELECT * FROM question_curriculums WHERE question_id=? ORDER BY version_code", (qid,)):
            mapping = dict(row)
            if self._has("question_sub_knowledge"):
                mapping["sub_knowledge"] = [dict(value) for value in self._execute(
                    "SELECT compulsory,chapter,knowledge FROM question_sub_knowledge WHERE curriculum_id=? ORDER BY position,id",
                    (mapping["id"],),
                )]
            else:
                mapping["sub_knowledge"] = []
            result.append(mapping)
        return result

    def _references(self, row):
        if not self._has("question_references"):
            return []
        sequences, result = self._sequence_map(), []
        for value in self._execute("SELECT * FROM question_references WHERE source_question_id=? ORDER BY field,start,id", (row["id"],)):
            item = dict(value)
            text = str(row.get(item["field"]) or "")
            current = _hash(text) == item["field_sha256"] and text[item["start"]:item["end"]] == item["text"]
            if not current:
                status, reason = "stale", "source_text_changed"
            elif item["resolution_state"] == "resolved":
                status = "resolved" if item["target_question_id"] in sequences else "target_deleted"
                reason = "" if status == "resolved" else "target_deleted"
            else:
                status, reason = "unresolved", item.get("reason", "")
            item.update(status=status, reason=reason,
                        seq_num=sequences.get(item["target_question_id"]) if status == "resolved" else None)
            result.append(item)
        return result

    def _project(self, row, profile, include_answers, version):
        content = str(row.get("content") or "")
        asset_roles = self._asset_roles(row)
        item = {key: row.get(key, "") for key in (
            "id", "question_type", "category_compulsory", "category_chapter", "category_knowledge",
            "difficulty", "source", "association_group_id", "tags", "created_at",
        )}
        item.update(
            seq_num=self._sequence_map().get(row["id"]),
            content_sha256=_hash(content),
            revision_sha256=_hash(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
            has_answer=bool(str(row.get("answer_markdown") or "").strip()),
            has_review=bool(str(row.get("review") or "").strip()),
            image_count=len(asset_roles), has_images=bool(asset_roles),
            knowledge_points=self._points(row["id"], profile == "evidence"),
            version_code=version, active_version_code=self.active_version_code,
            quality={"content_verification": "unknown", "answer_verification": "unknown"},
        )
        mappings = self._mappings(row["id"])
        if version != self.active_version_code:
            mapping = next((value for value in mappings if value["version_code"] == version), None)
            for key in ("compulsory", "chapter", "knowledge"):
                item["category_" + key] = mapping.get(key) if mapping else None
            item["curriculum_mapping_found"] = mapping is not None
        if profile == "candidate":
            preview = " ".join(strip_source_attribution_comment(content).split())
            item["content_preview"] = preview[:320] + ("…" if len(preview) > 320 else "")
            item["content_truncated"] = len(preview) > 320
            return item
        assets = self._assets(row)
        item.update(content=content, review=str(row.get("review") or ""),
                    curriculum_mappings=mappings, question_references=self._references(row),
                    assets=assets, image_paths=[asset["url"] for asset in assets
                                               if asset["url"] and "reference" not in asset["roles"]])
        for key in ("figure_align", "figure_align_custom", "figure_size", "usage_count"):
            item[key] = row.get(key)
        item["image_layouts"] = _json(row.get("image_layouts"), {})
        if include_answers:
            item["answer_markdown"] = str(row.get("answer_markdown") or "")
        if profile == "evidence":
            for key in ("tikz_code", "tikz_reference_image_path"):
                item[key] = row.get(key) or ""
            for key in ("content_tikz_assets", "answer_tikz_assets"):
                item[key] = _json(row.get(key), [])
            item["source_labels"] = [dict(value) for value in self._execute(
                "SELECT book_key,label_key,original_label FROM question_source_labels WHERE question_id=? ORDER BY id", (row["id"],)
            )] if self._has("question_source_labels") else []
        return item

    def _asset_roles(self, row):
        result = {}

        def add(path, role):
            if isinstance(path, str) and path.strip():
                path = path.strip()
                # The same URL with/without a leading slash is one asset.
                key = "/" + path.lstrip("/") if path.lstrip("/").startswith("static/") else path
                roles = result.setdefault(key, [])
                if role not in roles:
                    roles.append(role)

        for field, role in (("content", "content"), ("answer_markdown", "answer"), ("review", "review")):
            for path in embedded_question_assets(str(row.get(field) or "")):
                add(path, role)
        for field, role in (("content_tikz_assets", "content"), ("answer_tikz_assets", "answer")):
            values = _json(row.get(field), [])
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict):
                    add(value.get("image_path"), role)
                    add(value.get("reference_image_path"), "reference")
        add(row.get("tikz_reference_image_path"), "reference")
        paths = _json(row.get("image_paths"), [])
        for path in paths if isinstance(paths, list) else []:
            key = "/" + path.lstrip("/") if isinstance(path, str) and path.lstrip("/").startswith("static/") else path
            if isinstance(key, str) and key not in result:
                add(key, "content")  # Ordinary legacy attachment without a prose anchor.
        return result

    def _assets(self, row):
        from mathbank.asset_security import AssetSecurityError, resolve_upload_asset
        from PIL import Image, UnidentifiedImageError

        result = []
        for reference, roles in self._asset_roles(row).items():
            item = {"url": None, "roles": roles, "exists": False, "status": "invalid_reference",
                    "width": None, "height": None, "media_type": None, "size_bytes": None}
            try:
                # require_file=False is essential: the normal true branch may
                # restore quarantined assets, which is forbidden in a reader.
                path = resolve_upload_asset(reference, uploads_dir=self.uploads_dir,
                                            url_prefix=self.uploads_url_prefix, require_file=False)
                item["url"] = "/" + self.uploads_url_prefix + "/" + path.relative_to(self.uploads_dir).as_posix()
                if self.include_local_paths:
                    item["local_path"] = str(path)
                item["exists"] = path.is_file()
                item["status"] = "missing" if not item["exists"] else "available"
                if item["exists"]:
                    item["size_bytes"] = path.stat().st_size
                    item["media_type"] = mimetypes.guess_type(path.name)[0]
                    try:
                        with Image.open(path) as image:
                            item["width"], item["height"] = image.size
                            item["media_type"] = Image.MIME.get(image.format, item["media_type"])
                    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                        item["status"] = "invalid_image"
            except AssetSecurityError:
                pass
            except OSError:
                item["status"] = "unreadable"
            result.append(item)
        return result

    def assets(self, ids):
        ids = _ids(ids)
        rows = {row["id"]: dict(row) for row in self._execute(
            "SELECT * FROM questions WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids
        )}
        return {"items": [{"id": qid, "assets": self._assets(rows[qid])} for qid in ids if qid in rows],
                "requested_ids": ids, "missing_ids": [qid for qid in ids if qid not in rows]}
