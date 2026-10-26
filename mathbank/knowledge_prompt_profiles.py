"""Versioned safe fallback for models that did not generalize after tuning.

Templates contain instructions only. Catalogue identities, names and curriculum
data are supplied by callers; inserted values are never recursively expanded.
"""
from __future__ import annotations

import json
import re

from .paths import KNOWLEDGE_PROMPT_PROFILES_DIR
from .knowledge_annotation_guidance import build_verified_boundary_notes


CONSERVATIVE_PROFILE = "conservative"
TARGET_FIRST_PROFILE = "target_first"
_CATALOG_SLOT = "<MB_KNOWLEDGE_CATALOG_JSON>"
_CURRICULUM_SLOT = "<MB_CURRICULUM_TEXT>"
_KNOWLEDGE_SLOT = "<MB_KNOWLEDGE_PROMPT>"
_BOUNDARIES_SLOT = "<MB_VERIFIED_BOUNDARY_NOTES>"
_KNOWLEDGE_TEMPLATE = (KNOWLEDGE_PROMPT_PROFILES_DIR / "conservative_knowledge.v1.txt").read_text(encoding="utf-8")
_CLASSIFICATION_TEMPLATE = (KNOWLEDGE_PROMPT_PROFILES_DIR / "conservative_classification.v1.txt").read_text(encoding="utf-8")
if (_KNOWLEDGE_TEMPLATE.count(_CATALOG_SLOT) != 1
        or _KNOWLEDGE_TEMPLATE.count(_BOUNDARIES_SLOT) != 1
        or _CLASSIFICATION_TEMPLATE.count(_CURRICULUM_SLOT) != 1
        or _CLASSIFICATION_TEMPLATE.count(_KNOWLEDGE_SLOT) != 1):
    raise ValueError("知识点保守提示模板结构无效。")
_CLASSIFICATION_SLOTS = re.compile("|".join(re.escape(value) for value in (_CURRICULUM_SLOT, _KNOWLEDGE_SLOT)))
_KNOWLEDGE_SLOTS = re.compile("|".join(re.escape(value) for value in (_CATALOG_SLOT, _BOUNDARIES_SLOT)))


def build_conservative_knowledge_prompt(catalog):
    """Preserve the pre-tuning task and alias protocol as an exact fallback."""
    compact = []
    for point in catalog:
        names = list(dict.fromkeys(value for value in
            [point.get("name"), point.get("original_name"), *point.get("aliases", [])]
            if isinstance(value, str) and value))
        compact.append([point["code"], *names])
    values = {_CATALOG_SLOT: json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
              _BOUNDARIES_SLOT: build_verified_boundary_notes(catalog)}
    return _KNOWLEDGE_SLOTS.sub(lambda match: values[match[0]], _KNOWLEDGE_TEMPLATE)


def build_conservative_classification_prompt(curriculum_text, knowledge_prompt):
    values = {_CURRICULUM_SLOT: curriculum_text, _KNOWLEDGE_SLOT: knowledge_prompt}
    # A marker appearing in teacher-supplied data remains literal data.
    return _CLASSIFICATION_SLOTS.sub(lambda match: values[match[0]], _CLASSIFICATION_TEMPLATE)
