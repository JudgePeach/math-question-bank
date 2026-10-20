"""Curriculum preset loading and metadata defaults.

The textbook trees live in JSON resources so the backend and browser use
one authoritative copy instead of maintaining independent Python and
JavaScript constants.
"""

from copy import deepcopy
from functools import lru_cache
import json

from mathbank.paths import CURRICULUMS_DIR


CURRICULUM_NAMES = {
    "A": "人教A版",
    "B": "人教B版",
    "S": "苏教版",
    "H": "沪教版",
    "N": "北师大版",
    "X": "湘教版",
}

DEFAULT_QUESTION_TYPES = [
    {"value": "single_choice", "label": "单选题"},
    {"value": "multi_choice", "label": "多选题"},
    {"value": "fill_in_blank", "label": "填空题"},
    {"value": "detailed_answer", "label": "解答题"},
]

DEFAULT_DIFFICULTIES = [
    {
        "value": "easy_error",
        "label": "易错题",
        "color": "text-green-600 bg-green-50 border-green-200",
    },
    {
        "value": "normal",
        "label": "常规题",
        "color": "text-blue-600 bg-blue-50 border-blue-200",
    },
    {
        "value": "challenge",
        "label": "挑战题",
        "color": "text-red-600 bg-red-50 border-red-200",
    },
    {
        "value": "qiangji",
        "label": "强基题",
        "color": "text-purple-600 bg-purple-50 border-purple-200",
    },
]


def normalize_version_code(version: str) -> str:
    """Validate and normalize a curriculum version code."""

    code = str(version or "A").strip().upper()
    if code not in CURRICULUM_NAMES:
        raise ValueError(f"不支持的教材大纲版本: {version}")
    return code


@lru_cache(maxsize=len(CURRICULUM_NAMES))
def _load_curriculum_cached(version: str) -> dict:
    code = normalize_version_code(version)
    path = CURRICULUMS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"教材大纲资源格式错误: {path}")
    return data


def load_curriculum(version: str = "A") -> dict:
    """Return an isolated copy of one curriculum tree."""

    return deepcopy(_load_curriculum_cached(normalize_version_code(version)))


def build_default_metadata(version: str = "A") -> dict:
    """Build the editable metadata payload used by settings and first boot."""

    code = normalize_version_code(version)
    return {
        "curriculum_version": code,
        "question_types": deepcopy(DEFAULT_QUESTION_TYPES),
        "difficulties": deepcopy(DEFAULT_DIFFICULTIES),
        "curriculum": load_curriculum(code),
    }


def get_curriculum_preset(version: str = "A") -> dict:
    """Return the API representation of a curriculum preset."""

    code = normalize_version_code(version)
    return {
        "version": code,
        "name": CURRICULUM_NAMES[code],
        "metadata": build_default_metadata(code),
    }


def build_curriculum_routing_index(paths, source_version, target_version, target_curriculum, *, target_ancestors=None):
    """Index verified shared point paths once for a user-requested switch.

    This is a directory crosswalk, not a question classifier.  Only exact
    source paths and real target paths participate; no topic-name inference
    or chapter-number comparison is performed.  ``target_ancestors`` contains
    source-verified ancestry for this target version only, indexed by
    (book, chapter, leaf reference section); absent evidence stays ambiguous.
    """
    order = {(book, chapter): position
             for position, (book, chapter) in enumerate(
                 (book, chapter) for book, chapters in target_curriculum.items()
                 if isinstance(chapters, dict) for chapter in chapters)}
    ancestor_titles = {}
    for (book, chapter, _reference), lineage in (target_ancestors or {}).items():
        ancestor_titles.setdefault((book, chapter), set()).update("".join(title.split()) for title in lineage)
    parent_sections = {(book, chapter, section)
                       for (book, chapter) in order for section in target_curriculum[book][chapter]
                       if "".join(section.split()) in ancestor_titles.get((book, chapter), set())}
    target_by_code, source_by_code, target_records = {}, {}, {}
    for path in paths:
        code = path.get("point_code")
        if not code:
            continue
        location = tuple(path.get(field, "") or "" for field in ("compulsory", "chapter", "knowledge"))
        if path.get("version_code") == target_version:
            book, chapter, section = location
            if ((book, chapter) not in order
                    or section and section not in target_curriculum[book][chapter]):
                continue
            target_records.setdefault(code, []).append({**path, "location": location})
        if path.get("version_code") == source_version:
            source_by_code.setdefault(code, set()).add(location)
    for code, records in target_records.items():
        leaves = [record for record in records if record.get("level", "leaf") == "leaf"
                  and record["location"][2] and record["location"] not in parent_sections]
        for record in records:
            location = record["location"]
            if record.get("level", "leaf") == "leaf" and location[2] and location not in parent_sections:
                target_by_code.setdefault(code, set()).add(location)
                continue
            # Parent/whole-chapter recommendations do not establish a terminal
            # section.  Remove one only when an actual leaf proves it redundant.
            redundant = any(_is_proven_path_ancestor(record, leaf, target_ancestors or {}) for leaf in leaves)
            if not redundant:
                target_by_code.setdefault(code, set()).add((*location[:2], ""))
    by_source = {}
    for code, source_paths in source_by_code.items():
        targets = target_by_code.get(code, set())
        for book, chapter, section in source_paths:
            by_source.setdefault((book, chapter, section), set()).update(targets)
            by_source.setdefault((book, chapter, ""), set()).update(targets)
    return {"by_code": target_by_code, "by_source": by_source,
            "order": order, "curriculum": target_curriculum, "parent_sections": parent_sections}


def _is_proven_path_ancestor(parent, leaf, ancestors):
    if parent["location"][:2] != leaf["location"][:2]:
        return False
    # Some presets intentionally expose one local section for several source
    # sub-sections.  Equal validated local paths already prove equivalence at
    # the precision this preset can store; no number-based inference is needed.
    if parent["location"] == leaf["location"]:
        return True
    if not parent["location"][2] or parent.get("level") == "chapter":
        return True
    book, chapter, section = leaf["location"]
    reference = leaf.get("reference_section", section)
    lineage = ancestors.get((book, chapter, reference), leaf.get("ancestors", ()))
    return parent.get("reference_section", parent["location"][2]) in lineage


def _single_chapter_location(locations):
    chapters = {(book, chapter) for book, chapter, _ in locations}
    if len(chapters) != 1:
        return None
    book, chapter = next(iter(chapters))
    sections = {section for _, _, section in locations}
    return book, chapter, next(iter(sections)) if len(sections) == 1 else ""


def route_curriculum_location(compulsory, chapter, knowledge, routing_index, *, point_codes=()):
    """Use confirmed main points, then an unambiguous directory crosswalk.

    Different points that each identify a chapter form a combined question;
    the later chapter follows actual target-directory order.  One point with
    alternative uses across chapters is ambiguous and cannot prove that all
    of those chapters were used by this question.
    """
    by_code, by_source = routing_index["by_code"], routing_index["by_source"]
    point_locations = [by_code[code] for code in dict.fromkeys(point_codes) if by_code.get(code)]
    reliable = [location for locations in point_locations
                if (location := _single_chapter_location(locations)) is not None]
    if reliable:
        target_chapter = max((location[:2] for location in reliable), key=routing_index["order"].__getitem__)
        # An ambiguous main point might require a later chapter than the
        # already-proven parts.  Do not silently discard that uncertainty.
        if any(_single_chapter_location(locations) is None
               and any(routing_index["order"][location[:2]] > routing_index["order"][target_chapter]
                       for location in locations) for locations in point_locations):
            return "", "", ""
        candidates = {location for locations in point_locations for location in locations
                      if location[:2] == target_chapter}
        return _single_chapter_location(candidates)

    source_location = (compulsory or "", chapter or "", knowledge or "")
    has_exact_section = bool(source_location[2]) and source_location in by_source
    chapter_fallback = not has_exact_section
    candidates = (by_source[source_location] if has_exact_section
                  else by_source.get((*source_location[:2], ""), set()))
    if has_exact_section and not candidates:
        # This source section is known but has no verified target mapping.
        # A neighbouring section must not stand in for its missing evidence.
        return "", "", ""
    source_match = _single_chapter_location(candidates)
    if source_match is not None:
        # Source context can disambiguate a multi-purpose point, but cannot
        # select a target chapter absent from that point's actual paths.
        if point_locations:
            contextual = {location for locations in point_locations for location in locations
                          if location[:2] == source_match[:2]}
            if contextual:
                return _single_chapter_location(contextual)
            return "", "", ""
        # An entire source chapter does not establish that this particular
        # question belongs to the one target section represented in the data.
        return (*source_match[:2], "") if chapter_fallback else source_match
    if point_locations:
        return "", "", ""

    # Preserve a complete literal path when directories share one.  Matching
    # only a generic section label is not sufficient evidence.
    tree = routing_index["curriculum"]
    book, title, section = source_location
    def terminal_or_empty(book, chapter, section):
        return section if section in tree[book][chapter] and (book, chapter, section) not in routing_index["parent_sections"] else ""
    if book in tree and title in tree[book]:
        return book, title, terminal_or_empty(book, title, section)
    exact_chapters = [(book, title, terminal_or_empty(book, title, section))
                      for book, chapters in tree.items() if isinstance(chapters, dict)
                      for name in chapters if name == title]
    return exact_chapters[0] if len(exact_chapters) == 1 else ("", "", "")
