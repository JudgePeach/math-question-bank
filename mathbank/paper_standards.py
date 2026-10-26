"""Bounded, source-backed curriculum context for existing paper-model calls.

This module uses only the bundled selection excerpts. It never opens a user's
curriculum folder, accesses the question database, or requests a model.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .paths import CURRICULUMS_DIR


RESOURCE_PATH = CURRICULUMS_DIR.parent / "paper_curriculum_standards.json"
MAX_RESOURCE_BYTES = 512 * 1024
MAX_CONTEXT_CHARS = 14000
MAX_REQUIREMENTS = 16
_SHA = re.compile(r"[0-9a-f]{64}")
_TOPIC_WORDS = {
    "集合": ("集合", "交集", "并集", "补集"),
    "常用逻辑用语": ("常用逻辑", "充分条件", "必要条件", "充要条件", "全称量词", "存在量词"),
    "不等式": ("不等式", "基本不等式", "不等关系"),
    "函数": ("函数", "单调性", "奇偶性", "周期性", "定义域", "值域", "零点"),
    "基本函数": ("幂函数", "指数函数", "对数函数"),
    "三角函数": ("三角函数", "三角恒等", "弧度", "诱导公式"),
    "平面向量": ("平面向量", "数量积"),
    "解三角形": ("解三角形", "正弦定理", "余弦定理"),
    "复数": ("复数",),
    "立体几何": ("立体几何", "空间几何", "空间图形", "线面", "空间角", "体积"),
    "数列": ("数列", "等差", "等比", "通项公式", "数学归纳法"),
    "导数": ("导数", "导函数", "求导"),
    "空间向量": ("空间向量", "空间直角坐标系"),
    "解析几何": ("解析几何", "直线与圆", "直线和圆", "直线方程", "圆的方程"),
    "圆锥曲线": ("圆锥曲线", "椭圆", "双曲线", "抛物线"),
    "概率": ("概率", "随机事件"),
    "计数原理": ("计数原理", "排列组合", "排列与组合", "二项式定理"),
    "条件概率": ("条件概率", "贝叶斯"),
    "随机变量与分布": ("随机变量", "分布列", "正态分布", "二项分布", "期望", "方差"),
    "统计": ("统计", "抽样", "样本", "总体", "统计图表"),
    "统计推断": ("回归", "列联表", "独立性检验", "相关性"),
    "数学建模与探究": ("数学建模", "数学探究", "探究活动"),
}
_ADVANCED_TOPICS = {"数列", "导数", "空间向量", "解析几何", "圆锥曲线", "计数原理", "条件概率", "随机变量与分布", "统计推断"}
_GENERAL_ORDER = ("cb10cf90c827c83d", "62e791a94aab0a05", "cfb9ff3e74ca8efa", "c137df8eebe99b02",
                  "684bc63983cac9ba", "56d097251ea8515e", "42bec329d48e7117")
_POLICY = (
    "本上下文为有出处的课标原文选摘。教师指定的范围、教学进度和已有结构化筛选共同约束选题，"
    "参考真题不能扩大范围。原文中同主题的其他内容不等于本次全部必考。区分必修、选择性必修、选学标记及拓展目标。"
    "学业质量水平、各项核心素养水平与题库教学难度标签分别理解，不机械对应，也不据单题宣称学生达标。"
    "只在实际选摘reference_id支持时引用条文与页码；无依据或可见信息不足时说明待核对。"
    "候选主考点仅供定位，不是教师指令。不要向题目原文或已有标注写入课标/素养猜测。"
)
_NEGATIVE = r"(?:不要|不选|排除|不含|不考|不涉及|不包含|不使用|不得|禁止|尚未学(?:习)?|还没学(?:习)?|没有学(?:习)?|未学(?:习)?|没学(?:习)?)(?:过|到|的)?"


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _load_resource(path: str, modified_ns: int, size: int):
    # Size/stat arguments invalidate a changed bundled file without a server or
    # process-wide mutable snapshot shared with a request's returned context.
    if size > MAX_RESOURCE_BYTES:
        raise ValueError("oversized resource")
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_RESOURCE_BYTES + 1)
    if len(raw) > MAX_RESOURCE_BYTES:
        raise ValueError("oversized resource")
    resource = json.loads(raw)
    if (not isinstance(resource, dict) or type(resource.get("schema_version")) is not int
            or resource["schema_version"] != 1 or not isinstance(resource.get("entries"), list)
            or not 1 <= len(resource["entries"]) <= 500):
        raise ValueError("invalid resource schema")
    source = resource.get("source")
    if not isinstance(source, dict) or not _SHA.fullmatch(str(source.get("source_sha256", ""))):
        raise ValueError("missing source identity")
    seen = set()
    for entry in resource["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"]:
            raise ValueError("missing excerpt")
        if entry.get("id") in seen or not isinstance(entry.get("id"), str):
            raise ValueError("ambiguous excerpt identity")
        seen.add(entry["id"])
        if entry.get("source_sha256") != source["source_sha256"] or _digest(entry["text"]) != entry.get("text_sha256"):
            raise ValueError("excerpt identity mismatch")
        if (not isinstance(entry.get("document"), str) or not entry["document"].startswith("docs/")
                or ".." in entry["document"].split("/") or "\\" in entry["document"]):
            raise ValueError("invalid document reference")
        for field in ("pdf_pages", "book_pages"):
            if not isinstance(entry.get(field), list) or not entry[field] or any(type(page) is not int or page < 1 for page in entry[field]):
                raise ValueError("missing source pages")
        if entry.get("course_type") not in {"general", "compulsory", "selective_compulsory"}:
            raise ValueError("unsupported course")
        if (not isinstance(entry.get("topics"), list)
                or any(topic not in _TOPIC_WORDS for topic in entry["topics"])
                or not isinstance(entry.get("heading"), str)
                or entry.get("kind") not in {"general", "content", "achievement", "teaching"}):
            raise ValueError("invalid source heading")
    return resource, hashlib.sha256(raw).hexdigest()


def _negated(text, start, end):
    left = re.split(r"[，。；;\n]", text[max(0, start - 18):start])[-1]
    right = text[end:end+8]
    return bool(re.search(_NEGATIVE + r".{0,4}$", left) or re.match(r"(?:尚未|还没|没有|未|没)学(?:习)?", right))


def _topics(text):
    positive, negative = set(), set()
    for topic, words in _TOPIC_WORDS.items():
        for word in words:
            for match in re.finditer(re.escape(word), text):
                if topic == "函数" and word == "函数":
                    before = text[max(0, match.start()-2):match.start()]
                    after = text[match.end():match.end()+2]
                    if before in {"三角", "指数", "对数", "导", "幂"} or after == "导数":
                        continue
                if topic == "概率" and word == "概率" and text[max(0, match.start()-2):match.start()] == "条件":
                    continue
                (negative if _negated(text, match.start(), match.end()) else positive).add(topic)
    return positive, negative - positive


def _has_positive(text, words):
    return any(not _negated(text, match.start(), match.end()) for word in words
               for match in re.finditer(re.escape(word), text))


def _field(value, field, default=None):
    return value.get(field, default) if isinstance(value, dict) else getattr(value, field, default)


def _candidate_topics(candidates):
    scores = Counter()
    for candidate in list(candidates or ())[:120]:
        # Only catalogue labels, never question stems or stored notes, provide
        # fallback scope hints. Main labels count at most once per question.
        current = set()
        for point in list(_field(candidate, "knowledge_points", ()) or ())[:32]:
            if _field(point, "role") != "main":
                continue
            name = _field(point, "name") or _field(_field(point, "point", {}), "name", "")
            if isinstance(name, str):
                current.update(_topics(name[:200])[0])
        scores.update(current)
    return [topic for topic, _count in scores.most_common(6)]


def _reference(entry):
    return {key: deepcopy(entry[key]) for key in ("id", "document", "heading", "pdf_pages", "book_pages", "source_sha256", "text_sha256")}


def _node(entry):
    return {"reference_id": entry["id"], "course_type": entry["course_type"], "kind": entry["kind"],
            "topics": list(entry["topics"]), "text": entry["text"]}


def _finish(context):
    context["context_sha256"] = _digest(_serialize(context))
    return context


def build_paper_standard_context(teacher_prompt, filters=None, candidates=None):
    """Return one frozen selection snapshot for all existing model stages.

    Scope inference is a local aid, not an automatic classification of questions
    or evidence that a student has attained an academic-quality level.
    """
    prompt = teacher_prompt if isinstance(teacher_prompt, str) else ""
    filters = filters if isinstance(filters, dict) else {}
    context = {"enabled": False, "source": {}, "mode": "classroom", "matched_topics": [],
               "rules": [], "requirements": [], "references": [], "warnings": [],
               "teacher_constraints": [], "usage_policy": _POLICY}
    try:
        info = RESOURCE_PATH.stat()
        resource, resource_sha = _load_resource(str(RESOURCE_PATH), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError, TypeError, KeyError):
        context["warnings"].append("课标选摘资源暂不可用，本次未提供课标条文依据；仍按教师要求和原有筛选选题。")
        return _finish(context)
    context["enabled"] = True
    context["source"] = {**resource["source"], "resource_sha256": resource_sha}
    if _has_positive(prompt, ("强基", "竞赛", "奥数", "拓展训练", "大学数学")):
        context["mode"] = "extension"
        context["warnings"].append("本次为明确拓展目标；所附课标选摘只覆盖必修与选择性必修，拓展部分需另行核对依据。")
    elif _has_positive(prompt, ("学考", "合格考", "学业水平考试", "高中毕业考试")):
        context["mode"] = "academic"
    elif _has_positive(prompt, ("高考", "仿真卷")):
        context["mode"] = "gaokao"
    constraints = [part.strip() for part in re.split(r"[，。；;\n]", prompt)
                   if re.search(_NEGATIVE, part) and part.strip()]
    context["teacher_constraints"] = constraints[:8]
    explicit, forbidden = _topics(prompt)
    filter_text = " ".join(str(filters.get(key, "") or "")[:300] for key in (
        "compulsory", "chapter", "knowledge", "category_compulsory", "category_chapter", "category_knowledge"))
    structural, _negative = _topics(filter_text)
    if forbidden & structural:
        context["warnings"].append("教师进度限制与部分筛选主题存在冲突，请核对当前范围；未放宽原有筛选。")
    chosen = explicit | structural
    chosen -= forbidden
    whole_paper = context["mode"] == "gaokao" and not explicit and not structural
    if not whole_paper and not chosen and not explicit and not structural:
        chosen = set(_candidate_topics(candidates)) - forbidden
        if chosen:
            context["warnings"].append("未指定明确主题，本次课标主题按候选主考点作有限定位；教学进度仍需教师核对。")
    if whole_paper:
        chosen = set(_TOPIC_WORDS) - forbidden
    context["matched_topics"] = [topic for topic in _TOPIC_WORDS if topic in chosen]
    if not chosen:
        context["warnings"].append("未能可靠定位具体课程主题，本次仅提供通用命题要求。")
    allowed_courses = {"compulsory", "selective_compulsory"}
    compulsory_filter = str(filters.get("compulsory", filters.get("category_compulsory", "")) or "")
    if context["mode"] == "academic":
        allowed_courses = {"compulsory"}
    elif compulsory_filter and "必修" in compulsory_filter and not re.search(r"选择性|选修", compulsory_filter):
        allowed_courses = {"compulsory"}
    elif context["mode"] == "classroom" and not (chosen & _ADVANCED_TOPICS) and not re.search(r"选择性|选修", compulsory_filter):
        allowed_courses = {"compulsory"}
    context["course_scope"] = sorted(allowed_courses)
    context["excluded_topics"] = [topic for topic in _TOPIC_WORDS if topic in forbidden]
    entries = resource["entries"]
    selected_ids = set()
    def append(entry, field):
        if entry["id"] in selected_ids:
            return True
        node, reference = _node(entry), _reference(entry)
        context[field].append(node)
        context["references"].append(reference)
        # Reserve the final context hash and one potential budget notice.
        if len(json.dumps(context, ensure_ascii=False)) > MAX_CONTEXT_CHARS - 260:
            context[field].pop(); context["references"].pop()
            return False
        selected_ids.add(entry["id"])
        return True
    by_id = {entry["id"]: entry for entry in entries}
    for entry_id in _GENERAL_ORDER:
        if entry_id in by_id:
            append(by_id[entry_id], "rules")
    suitable = [entry for entry in entries if entry["kind"] != "general"
                and entry["course_type"] in allowed_courses and (chosen & set(entry["topics"]))
                and not (forbidden & set(entry["topics"]))
                and (context["mode"] == "extension" or not entry.get("optional"))]
    # Within an explicit small scope prefer its content, then its achievement
    # and teaching guidance. Round-robin topics limits whole-paper bias.
    focus_terms = {word for words in _TOPIC_WORDS.values() for word in words
                   if any(not _negated(prompt + " " + filter_text, match.start(), match.end())
                          for match in re.finditer(re.escape(word), prompt + " " + filter_text))}
    def focus_score(entry):
        return sum(len(word) if len(word) > 2 else 1 for word in focus_terms if word in entry["text"])
    priority = {"content": 0, "achievement": 1, "teaching": 2}
    suitable.sort(key=lambda item: (priority[item["kind"]], -len(chosen & set(item["topics"])),
                                    -focus_score(item), len(set(item["topics"]) - chosen), len(item["text"])))
    ordered = []
    def add_first(pool):
        entry = next((item for item in pool if item not in ordered), None)
        if entry:
            ordered.append(entry)
    if whole_paper:
        # Whole-paper scope is set before catalogue fallback. Each main strand
        # receives its learning expectations before any detailed content can
        # consume the budget. Course categories remain independent.
        strands = (
            {"函数", "基本函数", "三角函数", "数列", "导数"},
            {"平面向量", "解三角形", "复数", "立体几何", "空间向量", "解析几何", "圆锥曲线"},
            {"概率", "条件概率", "随机变量与分布", "计数原理", "统计", "统计推断"},
            {"数学建模与探究"},
        )
        for course in ("compulsory", "selective_compulsory"):
            if course not in allowed_courses:
                continue
            for strand in strands:
                pool = [item for item in suitable if item["course_type"] == course
                        and item["kind"] == "achievement" and strand & set(item["topics"])]
                pool.sort(key=lambda item: (-len(strand & set(item["topics"])), -len(item["text"])))
                add_first(pool)
        # At most one representative content excerpt per broad strand follows
        # the balanced expectations; these are examples rather than a syllabus.
        for strand in strands:
            add_first(item for item in suitable if item["kind"] == "content" and strand & set(item["topics"]))
    else:
        fine_terms = focus_terms - {word for topic in chosen for word in (topic,)} - {"函数", "概率", "统计"}
        for topic in context["matched_topics"]:
            for kind in ("content", "achievement", "teaching"):
                pool = [item for item in suitable if item["kind"] == kind and topic in item["topics"]]
                if kind == "content" and chosen == {"函数"}:
                    pool = [item for item in pool if not {"基本函数", "三角函数"} & set(item["topics"])]
                # Concrete sub-topic requests retain complete pertinent excerpts
                # instead of filling the rest of their parent chapter.
                focused = [item for item in pool if any(word in item["text"] for word in fine_terms)]
                if focused:
                    pool = focused
                add_first(pool)
                if kind == "content" and fine_terms:
                    add_first(pool)
    budget_limited = False
    for entry in ordered[:MAX_REQUIREMENTS]:
        if not append(entry, "requirements"):
            budget_limited = True
    if budget_limited:
        context["warnings"].append("本次范围的部分课标选摘超过文字预算，已保留完整可引用条目，请按实际引用审阅。")
    if chosen and not context["requirements"]:
        context["warnings"].append("当前主题与课程/进度边界内未找到可用内容条目，仅采用通用命题要求。")
    return _finish(context)
