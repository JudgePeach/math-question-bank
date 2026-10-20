"""Independent, version-specific fine-grained curriculum labels.

These values never replace the legacy single knowledge string or custom tags.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mathbank.curriculums import normalize_version_code


MAX_SUB_KNOWLEDGE_ITEMS = 64
MAX_SUB_KNOWLEDGE_LENGTH = 100
MAX_SUB_KNOWLEDGE_JSON_LENGTH = 32_768


def normalize_sub_knowledge(value) -> list[dict[str, str]]:
    """Validate full paths; stable deduplication never loses chapter context."""

    if isinstance(value, str):
        if len(value) > MAX_SUB_KNOWLEDGE_JSON_LENGTH:
            raise ValueError("细分知识点数据过长")
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("细分知识点必须是 JSON 路径对象数组") from exc
    if not isinstance(value, list):
        raise ValueError("细分知识点必须是路径对象数组")
    if len(value) > MAX_SUB_KNOWLEDGE_ITEMS:
        raise ValueError(f"细分知识点不能超过 {MAX_SUB_KNOWLEDGE_ITEMS} 项")
    labels: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"compulsory", "chapter", "knowledge"}:
            raise ValueError("细分知识点须包含 compulsory、chapter、knowledge 完整路径")
        path: dict[str, str] = {}
        for field in ("compulsory", "chapter", "knowledge"):
            if not isinstance(item[field], str):
                raise ValueError("细分知识点路径的每个字段必须是字符串")
            label = item[field].strip()
            if not label or len(label) > MAX_SUB_KNOWLEDGE_LENGTH:
                raise ValueError(f"细分知识点路径须为 1 至 {MAX_SUB_KNOWLEDGE_LENGTH} 个字符")
            if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in label):
                raise ValueError("细分知识点不能含控制字符")
            path[field] = label
        key = tuple(path[field] for field in ("compulsory", "chapter", "knowledge"))
        if key not in seen:
            seen.add(key)
            labels.append(path)
    return labels


def dump_sub_knowledge(value) -> str:
    return json.dumps(normalize_sub_knowledge(value), ensure_ascii=False, separators=(",", ":"))


def read_sub_knowledge(value) -> list[dict[str, str]]:
    """Safely expose old/new ORM rows, including not-yet-flushed defaults."""

    try:
        return normalize_sub_knowledge(value if value is not None else [])
    except ValueError:
        return []


def detect_curriculum_version(metadata, *, strict: bool = False) -> str:
    """Prefer preset identity; retain chapter detection for legacy metadata."""

    if isinstance(metadata, dict) and "curriculum_version" in metadata:
        version = metadata["curriculum_version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("curriculum_version 必须是有效的教材版本编码")
        return normalize_version_code(version)

    curriculum = metadata.get("curriculum", {}) if isinstance(metadata, dict) else {}
    chapters = " ".join(str(chapter) for book in curriculum.values()
                        if isinstance(book, dict) for chapter in book) if isinstance(curriculum, dict) else ""
    if "第一章" in chapters:
        return "B"
    if any(marker in chapters for marker in (
        "第 1 章 集合与逻辑", "数学建模活动案例", "第 2 章 等式与不等式", "第 3 章 幂、指数与对数",
    )):
        return "H"
    if "第1章" in chapters:
        return "S"
    if strict and not re.search(r"(?:^|\s)\d+\.\s*\S", chapters):
        raise ValueError("无法从对应元数据明确识别教材版本")
    return "A"


def resolve_active_version(metadata_path=None, *, strict: bool = False) -> str:
    if metadata_path is None:
        from mathbank.paths import DATA_BACKUP_DIR
        metadata_path = DATA_BACKUP_DIR / "custom_metadata.json"
    try:
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        if strict:
            raise ValueError("对应数据库的教材元数据缺失、无效或无法识别") from exc
        return "A"
    # An explicit but invalid identity must never silently select another
    # version, including for ordinary exports that use non-strict resolution.
    return detect_curriculum_version(metadata, strict=strict)
