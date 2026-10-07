"""Narrow compatibility for a Word exam outside the active curriculum stage.

Call only after the Word source plan's private certificate was checked. This
module does not certify document extraction, create a curriculum, or accept a
model's claim about source grade. Unknown/custom coverage remains strict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re

from mathbank.content_locks import _formulas
from mathbank.question_assets import markdown_literal_ranges


MAX_METADATA_RECORDS = 64
MAX_METADATA_TEXT = 4_096
MAX_TITLE_CHARACTERS = 180
MAX_CHAPTER_CHARACTERS = 48

_GRADE = re.compile(r"初[一二三123]|高[一二三123]|[一二三四五六七八九1-9]年级")
_TITLE_TEXT = re.compile(r"[\u3400-\u9fffA-Za-z0-9 \t·•・.．—\-~～()（）]+")
_SUBJECT = re.compile(r"(?:[\u3400-\u9fffA-Za-z0-9 \t·•・.．—\-~～()（）]*?)数学(?:试卷|试题|测试卷|考试|测试)$")
_EXAM = re.compile(r"期中|期末|月考|联考|模拟|[一二三]模|中考|高考|考试|测试")
_NOT_TITLE = re.compile(r"已知|满足|至少|至多|共有|每[人班组]|求|若|则|设|如图|下表|材料|定义|函数|集合|数列|条件|共用|共享|请|忽略|指令|归类|改为")
_CHAPTER = re.compile(r"[\u3400-\u9fff][\u3400-\u9fff0-9 ()（）、，·\-]*")
_NOT_CHAPTER = re.compile(r"忽略|指令|提示词|系统消息|密钥|密码|执行|调用|发送|写入|删除|输出|题干|答案|解析")


@dataclass(frozen=True)
class WordClassificationScope:
    source_grade: str | None
    source_stage: str | None
    allow_outside: bool
    reason: str
    source_sha256: str | None
    taxonomy_sha256: str | None
    categories: frozenset[tuple[str, str]]
    grade_source_ranges: tuple[tuple[int, int], ...] = ()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_grade(value: str) -> tuple[str, str]:
    digits = "零一二三四五六七八九"
    if value.startswith(("初", "高")):
        number = int(value[1]) if value[1].isdigit() else digits.index(value[1])
        return ((digits[number + 6] + "年级", "初中") if value[0] == "初" else ("高" + digits[number], "高中"))
    digit = value[0]
    number = int(digit) if digit.isdigit() else digits.index(digit)
    return digits[number] + "年级", "小学" if number <= 6 else "初中"


def _plain_line(value: str) -> str | None:
    value = re.sub(r"^\s*#{1,6}[ \t]*", "", value.strip())
    value = re.sub(r"\\(?:textbf|textrm|textit)\{([^{}]*)\}", lambda m: m.group(1), value)
    if value.startswith("**") and value.endswith("**"):
        value = value[2:-2].strip()
    value = value.strip()
    if (not 4 <= len(value) <= MAX_TITLE_CHARACTERS or not _TITLE_TEXT.fullmatch(value)
            or _NOT_TITLE.search(value)):
        return None
    return value


def _record_titles(value: str):
    literal = markdown_literal_ranges(value)
    protected = [*literal, *[(item.start, item.end) for item in _formulas(value, literal_ranges=literal)]]
    lines = []
    offset = 0
    for raw in value.splitlines(keepends=True):
        visible_start = offset + len(raw) - len(raw.lstrip())
        covered = any(start <= visible_start < end for start, end in protected)
        line = None if covered else _plain_line(raw)
        lines.append((line, offset, offset + len(raw)))
        offset += len(raw)
    for index, (line, start, end) in enumerate(lines):
        if line is None:
            continue
        if _SUBJECT.fullmatch(line):
            yield line, start, end
        elif (_EXAM.search(line) and _GRADE.search(line) and index + 1 < len(lines)
              and lines[index + 1][0] is not None
              and _SUBJECT.fullmatch(lines[index + 1][0])):
            yield line + " " + lines[index + 1][0], start, lines[index + 1][2]


def _catalog_stage(label: str) -> str | None:
    """Known stage labels only; an arbitrary custom name implies no coverage."""
    grades = {_canonical_grade(match.group())[1] for match in _GRADE.finditer(label)}
    named = {stage for stage in ("小学", "初中", "高中") if stage in label}
    stages = grades | named
    if re.fullmatch(r"(?:选择性)?(?:必修|选修)[一二三四五六七八九十0-9 \-]*(?:第?[一二三四五六七八九十0-9]+册)?", label):
        stages.add("高中")
    return next(iter(stages)) if len(stages) == 1 else None


def build_word_classification_scope(plan: Mapping, curriculum: Mapping) -> WordClassificationScope:
    """Read a unique grade from already-certified, source-equal exam metadata.

    The caller must first require the Word plan's private source certificate.
    A source grade has no authority to widen question/body fields or taxonomy.
    A mapping returned by a model cannot stand in for this frozen object.
    """
    categories = frozenset((category, chapter) for category, chapters in curriculum.items()
                           if isinstance(category, str) and isinstance(chapters, Mapping)
                           for chapter in chapters if isinstance(chapter, str)) if isinstance(curriculum, Mapping) else frozenset()
    try:
        taxonomy_sha = _sha(json.dumps(curriculum, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError):
        taxonomy_sha = None
    source = plan.get("source") if isinstance(plan, Mapping) else None
    source_sha = _sha(source) if isinstance(source, str) else None

    def strict(reason, grade=None, stage=None, ranges=()):
        return WordClassificationScope(grade, stage, False, reason, source_sha, taxonomy_sha, categories, tuple(ranges))

    if not categories or taxonomy_sha is None:
        return strict("taxonomy_missing_or_invalid")
    records = plan.get("document_metadata") if isinstance(plan, Mapping) else None
    if not isinstance(source, str) or not isinstance(records, list) or len(records) > MAX_METADATA_RECORDS:
        return strict("document_metadata_missing_or_excessive")
    found = set()
    grade_ranges = []
    for record in records:
        if not isinstance(record, Mapping):
            return strict("document_metadata_invalid")
        if record.get("kind") != "declared_exam_metadata":
            continue
        start, end, value = record.get("start"), record.get("end"), record.get("text")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source)
                or not isinstance(value, str) or len(value) > MAX_METADATA_TEXT or source[start:end] != value):
            return strict("document_metadata_source_changed")
        for title, left, right in _record_titles(value):
            for match in _GRADE.finditer(title):
                found.add(_canonical_grade(match.group()))
                grade_ranges.append((start + left, start + right))
    if len(found) != 1:
        return strict("source_grade_conflicting" if found else "source_grade_unknown")
    grade, stage = next(iter(found))
    grade_ranges = sorted(set(grade_ranges))
    labels = [category for category, chapters in curriculum.items() if isinstance(chapters, Mapping)]
    catalog_stages = [_catalog_stage(label) for label in labels if isinstance(label, str)]
    if (len(catalog_stages) != len(labels) or not catalog_stages or any(item is None for item in catalog_stages)):
        return strict("custom_stage_coverage_unknown", grade, stage, grade_ranges)
    if stage in catalog_stages:
        return strict("source_stage_covered", grade, stage, grade_ranges)
    return WordClassificationScope(grade, stage, True, "source_stage_not_covered", source_sha,
                                   taxonomy_sha, categories, tuple(grade_ranges))


def word_category_allowed(scope: WordClassificationScope, compulsory: str, chapter: str) -> bool:
    """Keep strict catalog pairs, or require this exact unsupported source grade."""
    if type(scope) is not WordClassificationScope or not isinstance(compulsory, str) or not isinstance(chapter, str):
        return False
    if not scope.allow_outside:
        return (compulsory, chapter) in scope.categories
    if (compulsory != scope.source_grade
            or not 1 <= len(chapter) <= MAX_CHAPTER_CHARACTERS or chapter != chapter.strip()
            or not _CHAPTER.fullmatch(chapter)
            or _NOT_CHAPTER.search(chapter)):
        return False
    stack = []
    for character in chapter:
        if character in "(（":
            stack.append(character)
            if len(stack) > 1:
                return False
        elif character in ")）":
            if not stack or stack.pop() != {"）": "（", ")": "("}[character]:
                return False
    return not stack


def classification_scope_instruction(scope: WordClassificationScope) -> str:
    """Use as the classification rule, not alongside a contradictory strict rule."""
    if type(scope) is not WordClassificationScope or not scope.allow_outside:
        return "学段与章节只能选择当前目录中的真实对应项，不得自造或扩展目录。"
    return (
        f"已确认原卷 source_grade={scope.source_grade}，属于{scope.source_stage}；当前目录不覆盖该阶段。"
        f"请将 category_compulsory 精确返回为‘{scope.source_grade}’，category_chapter 返回原题实际使用的考点建议。"
        "考点建议用1至48字符的简短中文名称，不用代码、LaTeX、路径、题文或解答。"
        "不要为了符合当前目录而强行映射成集合、立体几何等不相符模块。"
        "当前目录排序的分类优先级只适用于目录实际涵盖的模块；本例按原题实际知识标注。"
        "这些目录外分类只是供教师人工归类的建议，不新增教材目录。"
        "仍只返回原协议的id、question_type、category_compulsory、category_chapter、difficulty五个字段，"
        "不得另返source_grade、content、answer_markdown或source_review。"
    )


def classification_scope_diagnostics(scope: WordClassificationScope) -> dict:
    if type(scope) is not WordClassificationScope:
        return {"mode": "strict", "reason": "scope_type_invalid"}
    return {"mode": "source_grade_suggestion" if scope.allow_outside else "strict",
            "source_grade": scope.source_grade, "source_stage": scope.source_stage,
            "reason": scope.reason, "source_sha256": scope.source_sha256,
            "taxonomy_sha256": scope.taxonomy_sha256, "requires_teacher_classification": scope.allow_outside}
